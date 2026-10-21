"""The one choke point between a request and a cloud model.

Routing modes (Beta 1.1, routing-modes addendum 2.1 / 2.4):

* ``cloud_only``      -- the cloud is the answer; the intent ``cloud_mode`` is implicit.
                         Laya never answers in the cloud's place (services/laya_templates).
* ``cloud_preferred`` -- a cloud mode like ``cloud_only`` (the intent ``cloud_mode`` is
                         implicit), with one difference: when the cloud cannot be
                         reached the local brain may answer, and the reply says so in
                         the fixed words of ``laya_templates.OUTAGE_LOCAL`` (``outage_local``).
                         Routine background work follows the local rows of
                         ``background_route`` (it is not the consent ``cloud_only``
                         is). Not offered in the UI
                         until the lead turns it on (``UI_HIDDEN_MODES``).
* ``local_preferred`` -- local is the default for every model call. The cloud is
  called only with a recorded INTENT.
* ``local_only``      -- the same, stricter: only an explicit request the owner
  confirmed for ONE turn, or a job the owner pinned to a cloud model.
* ``smart``           -- the old duplicate of local_preferred; treated as one.

The intents:

``user_explicit``     the owner picked a cloud model for this chat or turn, or asked
                      for a cloud answer ("Send to Claude", a per-chat cloud seat).
``laya_escalation``   Laya judged the turn beyond local ability and ASKED; the owner
                      said yes on the card or out loud.
``remembered_choice`` the owner ticked "Always send this kind of task to Claude" on an
                      earlier escalation (opt-in, visible in Settings, revocable).
``scheduled_cloud``   a background job the owner pinned to one cloud model.
``cloud_mode``        implicit: the routing mode IS cloud_only.

Every cloud transport calls ``require(provider, purpose)`` before it builds a
payload. It returns a receipt-shaped token or raises ``NoCloudIntent``. A local
provider needs no intent (``require`` returns a ``local`` token without touching
the mode), so a transport that serves both legs can call it unconditionally.

The intent is carried in a ContextVar, set by the code that HAS the owner's
word: the chat route for a cloud-bound chat or a "send it to Claude" choice, the
card handler for an approved escalation, the scheduler for a pinned job. It is
never inferred from a fallback leg, a retry or a setting the owner did not
touch, which is the point: a failed local call raises ``LocalUnavailable`` and
becomes an OFFER, not a cloud call.

``tests/unit/test_every_cloud_transport_requires_intent.py`` walks ``src/`` and
fails by name on any transport that does not call ``require``.

Routine background jobs (``background_route``)
-----------------------------------------------

The routine jobs (News front pages and briefings, the weekly digest and
editorial, podcast scripts, the heartbeat, daily creation) ship local-only to
save money (``scheduler.LOCAL_ONLY_BY_DEFAULT``). Where they run is answered
HERE, once, and the scheduler, the News engine, the podcast writer and the
local-only guard all ask ``background_route(job)``:

=========================  ==========  ===================  ===================  ========
mode                       job (**)    a local seat serves  scheduled_cloud      route
                                                            .allow (*)
=========================  ==========  ===================  ===================  ========
cloud_only, owner chose it released    either               either               cloud
any other                  any         yes                  either               local
any other                  any         no                   yes                  cloud
any other                  any         no                   no                   skip
=========================  ==========  ===================  ===================  ========

"Any other" is every other case: a local mode, ``cloud_preferred``, a
``cloud_only`` nobody chose (below), and a job ``cloud_only`` does not release.

(*) ``scheduled_cloud.allow`` is the owner's explicit opt-in, and it covers
only the jobs it was asked about (``scheduled_cloud.JOBS``: daily creation and
the heartbeat). For every other routine job it reads as "no".

(**) The jobs a chosen ``cloud_only`` releases (``CLOUD_ONLY_RELEASES``): the
News routines (front pages, briefings, the weekly digest and editorial),
podcast scripts and the heartbeat. Daily creation is not among them: it was
not named in the owner's decision and stays on its explicit opt-in.

Owner decision (2026-10-15): choosing ``cloud_only`` IS consent for news,
podcasts, briefings and the heartbeat to use the cloud. Those jobs then run on
the cloud, pinned to the one cloud model ``scheduled_cloud`` names for them
(``heartbeat_model`` or ``job_model``), so an unattended run can never escalate
to a dearer model; with no model set there the job is skipped and says so.
``scheduled_cloud.routine_follows_cloud_mode: false`` keeps them local even
under ``cloud_only`` (the old behaviour).

The consent is the CHOOSING. ``cloud_only`` is also the factory default that
every fresh install writes into settings.json, so the stored mode alone proves
nothing. ``model_routing.mode_chosen`` is the evidence: core._save_settings
writes it only on an owner routing change (the Settings mode control, the
setup chat / setup completion) and strips it from every other write; the
defaults never carry it. The consent row needs ``mode_chosen`` to name the
stored mode (``mode_was_chosen``). A blank mode, a factory ``cloud_only``, a
mode moved by anything but the owner, and an unreadable (degraded) settings
read are all NOT chosen, and keep the other rows.

What is NOT routine stays local in every mode: a ``local_only`` scope opened
for privacy (compaction, meeting capture, memory review, citations, a private
podcast episode) and a local-only schedule the owner wrote themselves.

Saved jobs the owner pinned to a model (``pinned_job_route``)
-------------------------------------------------------------

A saved job (a workflow or a scheduled prompt) the owner pinned to one model
runs on exactly that model, in every mode: the pin is the owner's choice and
its consent. A pinned model that cannot answer (a local seat not serving, a
cloud service with no key) HOLDS the run with the reason; it is never moved
to another model. An unpinned saved job follows the ordinary routing.
"""

from __future__ import annotations

import contextvars
import json
import logging
import threading
import time
from dataclasses import dataclass, field

_log = logging.getLogger("friday.cloud_intent")

#: Intents an owner (or a job the owner pinned) can carry.
EXPLICIT_INTENTS = ("user_explicit", "laya_escalation", "remembered_choice",
                    "scheduled_cloud")
#: Every intent a receipt can name. ``cloud_mode`` is implicit and never set by hand.
INTENTS = EXPLICIT_INTENTS + ("cloud_mode",)

#: Every routing mode. The one list the other mode tuples in the code are
#: checked against (tests/unit/test_mode_lists_agree.py).
MODES = ("local_only", "local_preferred", "cloud_preferred", "cloud_only")
#: The modes in which the cloud is the default answer.
CLOUD_MODES = ("cloud_preferred", "cloud_only")
#: Modes that exist in the code but are not shown in any picker yet.
UI_HIDDEN_MODES = ("cloud_preferred",)
#: Old spellings that read as another mode.
MODE_ALIASES = {"smart": "local_preferred"}
#: The factory default, and what a blank mode behaves as. A blank mode is
#: reported as UNSET (``mode_is_unset``) so setup can ask.
DEFAULT_MODE = "cloud_only"

#: Modes in which the cloud needs an intent. ``smart`` is the old duplicate of
#: local_preferred (decision RM-D1 migrates it); it behaves as one here.
LOCAL_MODES = frozenset({"local_preferred", "local_only", "smart"})

#: What local_only accepts: an explicit request the owner confirmed for this one
#: turn (the confirm names the mode), or a job pinned to a cloud model. It never
#: escalates on its own and a remembered choice does not reach it.
_LOCAL_ONLY_OK = frozenset({"user_explicit", "scheduled_cloud"})


class NoCloudIntent(RuntimeError):
    """A cloud transport was called in a local mode with no recorded intent."""

    def __init__(self, provider="", purpose="", model="", mode=""):
        self.provider = str(provider or "")
        self.purpose = str(purpose or "")
        self.model = str(model or "")
        self.mode = str(mode or "")
        super().__init__(
            "Friday is in %s mode, so nothing goes to %s%s (%s) unless you ask "
            "for it. Nothing was sent." % (
                (self.mode or "a local").replace("_", " "),
                self.provider or "a cloud model",
                (" / " + self.model) if self.model else "",
                self.purpose or "model call"))


class LocalUnavailable(RuntimeError):
    """A local mode could not get an answer from a local model.

    Raised INSTEAD of falling over to the cloud. Callers turn it into an offer
    (an interactive turn: "try again locally / a shorter version / send it to
    Claude", nothing preselected) or a scheduler retry (a background job). It is
    a RuntimeError so code that only knows "the model call failed" still stops.
    """

    def __init__(self, reason="", *, purpose="", model="", mode="", cause=None):
        self.reason = str(reason or "no local model answered")
        self.purpose = str(purpose or "")
        self.model = str(model or "")
        self.mode = str(mode or "")
        self.cause = cause
        super().__init__(
            "The local model could not answer (%s). Nothing was sent to a cloud "
            "model." % self.reason)


@dataclass(frozen=True)
class Intent:
    """A recorded reason the cloud may be called."""
    intent: str
    source: str = ""
    confirmed: bool = False
    detail: dict = field(default_factory=dict)
    at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class CloudToken:
    """What ``require`` returns, and what a receipt carries."""
    intent: str            # one of INTENTS, or "local" when the leg is on-device
    provider: str
    purpose: str
    model: str = ""
    mode: str = ""
    source: str = ""
    at: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return {"intent": self.intent, "provider": self.provider,
                "purpose": self.purpose, "model": self.model, "mode": self.mode,
                "source": self.source, "at": self.at}


_CURRENT: contextvars.ContextVar = contextvars.ContextVar(
    "friday_cloud_intent", default=None)

#: True while a turn the owner started (typed or spoken) is running on this thread.
#: A background job or an unattended inbound message never has it.
_OWNER_TURN: contextvars.ContextVar = contextvars.ContextVar(
    "friday_owner_turn", default=False)

_RECENT: list = []
_RECENT_LOCK = threading.Lock()
_RECENT_MAX = 200
_RECEIPT_FILE_MAX = 4 * 1024 * 1024


# ── carrying an intent ───────────────────────────────────────────────────────

def set_intent(intent: str, *, source: str = "", confirmed: bool = False,
               **detail):
    """Record an intent for the current context; returns a token for ``reset_intent``."""
    if intent not in EXPLICIT_INTENTS:
        raise ValueError("cloud intent %r is not one the owner can give "
                         "(%s)" % (intent, ", ".join(EXPLICIT_INTENTS)))
    return _CURRENT.set(Intent(intent, str(source or ""), bool(confirmed),
                               dict(detail)))


def reset_intent(token) -> None:
    try:
        _CURRENT.reset(token)
    except (ValueError, LookupError):
        _CURRENT.set(None)


def current() -> Intent | None:
    return _CURRENT.get()


def clear() -> None:
    _CURRENT.set(None)


class intent_scope:
    """``with intent_scope("user_explicit", source="chat_seat"): ...``

    Re-entrant. The intent is gone when the block exits.
    """

    def __init__(self, intent: str, *, source: str = "", confirmed: bool = False,
                 **detail):
        self._args = (intent, source, confirmed, detail)
        self._token = None

    def __enter__(self):
        intent, source, confirmed, detail = self._args
        self._token = set_intent(intent, source=source, confirmed=confirmed,
                                 **detail)
        return current()

    def __exit__(self, *exc):
        reset_intent(self._token)
        return False


class preserve_scope:
    """Restores the caller's intent on exit, whatever the block set inside it.

    The dispatch functions set an intent a route implied (a configured per-task
    cloud override); this keeps that from outliving the call."""

    def __enter__(self):
        self._saved = _CURRENT.get()
        return self

    def __exit__(self, *exc):
        _CURRENT.set(self._saved)
        return False


class turn_scope:
    """Opens a turn with NO intent and drops whatever the turn set when it ends.

    A request handler runs on a pooled thread; without this an intent set for one
    turn could still be there for the next one the thread serves.
    """

    def __enter__(self):
        self._token = _CURRENT.set(None)
        self._owner = _OWNER_TURN.set(True)
        return self

    def __exit__(self, *exc):
        reset_intent(self._token)
        _OWNER_TURN.reset(self._owner)
        return False


class owner_turn:
    """Marks a turn the owner started (a spoken turn; chat routes use turn_scope)."""

    def __enter__(self):
        self._t = _OWNER_TURN.set(True)
        return self

    def __exit__(self, *exc):
        _OWNER_TURN.reset(self._t)
        return False


def owner_request(source: str):
    """Decorator for a tool handler that does what the owner asked for in the
    cloud (generate an image, a video, a piece of music, look at a picture).

    Inside an owner-started turn the call runs under a `user_explicit` intent, so
    local_preferred works as the owner expects. The intent is NOT confirmed, so
    local_only still refuses until the per-turn confirm. A handler reached from a
    background job (no owner turn) gets no intent, and a local mode holds it back.
    """
    import functools

    def _wrap(fn):
        @functools.wraps(fn)
        def _inner(*args, **kwargs):
            if _OWNER_TURN.get() and current() is None:
                with intent_scope("user_explicit", source=source):
                    return fn(*args, **kwargs)
            return fn(*args, **kwargs)
        return _inner
    return _wrap


def per_turn(fn):
    """Decorator form of ``turn_scope`` for a route handler."""
    import functools

    @functools.wraps(fn)
    def _wrapped(*args, **kwargs):
        with turn_scope():
            return fn(*args, **kwargs)
    return _wrapped


def carry(fn):
    """Wrap ``fn`` so a worker thread runs under THIS context's intent.

    ContextVars do not follow a ``threading.Thread``; the code that hands work to
    a thread on the owner's behalf wraps the target with this.
    """
    ctx = contextvars.copy_context()

    def _run(*args, **kwargs):
        return ctx.run(fn, *args, **kwargs)
    return _run


# ── the mode ─────────────────────────────────────────────────────────────────

def routing_mode() -> str:
    """The routing mode a transport must honour right now.

    THE single reader: ``local_only_guard.routing_mode`` and
    ``cloud_mode_policy.current_mode`` delegate here.

    ``local_only`` while a local-only run is active; otherwise
    ``model_routing.mode`` (the offline overlay is folded in by
    ``_load_settings``). A blank or missing setting behaves as the factory
    default, ``cloud_only``, and ``mode_is_unset`` reports that it is unset.
    An unreadable setting fails CLOSED to ``local_only``.
    """
    try:
        from agent_friday.services import local_only_guard as _guard
        if _guard.is_active():
            return "local_only"
    except Exception:
        return "local_only"
    try:
        # Blank after stripping (whitespace only) is unset too: the factory default.
        return str(_stored_mode() or "").strip().lower() or DEFAULT_MODE
    except Exception:
        return "local_only"


def _stored_mode():
    from agent_friday import core
    return ((core._load_settings() or {}).get("model_routing") or {}).get("mode")


#: The record of the owner's own routing choice (``core._save_settings`` writes
#: it on an owner routing change only). Not in DEFAULT_SETTINGS.
MODE_CHOSEN_KEY = "mode_chosen"


def _chosen_mode():
    """The mode the owner's own routing change recorded, or "". A degraded
    (unreadable) settings read has no evidence of a choice: ""."""
    from agent_friday import core
    raw = core._load_settings_raw() or {}
    if core._is_degraded_settings(raw):
        return ""
    mr = raw.get("model_routing")
    return str((mr or {}).get(MODE_CHOSEN_KEY) or "") if isinstance(mr, dict) else ""


def mode_was_chosen(mode=None) -> bool:
    """True when the owner chose ``mode`` (default: the stored mode) through an
    owner routing change. A blank mode is never chosen; any fault is "no"."""
    try:
        stored = _stored_mode() if mode is None else mode
        if not str(stored or "").strip():
            return False
        chosen = str(_chosen_mode() or "").strip()
        return bool(chosen) and normalize_mode(chosen) == normalize_mode(stored)
    except Exception:
        return False


def mode_is_unset() -> bool:
    """True when the owner has not chosen a mode (a blank or missing setting).

    The behaviour is still the factory default (``cloud_only``, for
    compatibility); this only reports that nobody chose it, so setup can ask.
    An unreadable setting is not "unset": it fails closed in ``routing_mode``.
    """
    try:
        return not str(_stored_mode() or "").strip()
    except Exception:
        return False


def normalize_mode(mode) -> str:
    """A mode as the rest of the code should read it: old spellings mapped
    (``smart`` -> ``local_preferred``), a blank one as the factory default."""
    m = str(mode or "").strip().lower() or DEFAULT_MODE
    return MODE_ALIASES.get(m, m)


def is_cloud_mode(mode: str | None = None) -> bool:
    return normalize_mode(mode if mode is not None else routing_mode()) in CLOUD_MODES


def outage_local_allowed(mode: str | None = None) -> bool:
    """May the local brain answer when the cloud cannot be reached?

    Only ``cloud_preferred`` allows it, and only with the announcement
    ``outage_notice`` provides. ``cloud_only`` never does (an outage there is
    said plainly with a retry offer); the local modes are local already.
    """
    return normalize_mode(mode if mode is not None else routing_mode()) == "cloud_preferred"


def local_voice_label(mode, default: str, tier: str = "cpu") -> str:
    """How the voice panel names the local engine. In a cloud mode (cloud_only,
    cloud_preferred) a local voice is the owner's own engine choice, and the
    label says so, still naming the GPU tier when that is what runs; in a local
    mode (or ``default`` for anything else) it keeps ``default``."""
    if normalize_mode(mode) not in CLOUD_MODES:
        return default
    return "Local GPU voice (your choice)" if tier == "gpu" else "Local voice (your choice)"


def outage_notice(provider: str):
    """The fixed words a ``cloud_preferred`` outage answer must open with."""
    from agent_friday.services import laya_templates
    return laya_templates.outage_notice(provider)


def is_local_mode(mode: str | None = None) -> bool:
    m = (mode if mode is not None else routing_mode())
    return str(m or "").strip().lower() in LOCAL_MODES


def allows(mode: str, intent: Intent | None) -> bool:
    """Does ``intent`` open the cloud in ``mode``?"""
    if intent is None:
        return False
    mode = str(mode or "").lower()
    if mode == "local_only":
        if intent.intent not in _LOCAL_ONLY_OK:
            return False
        return intent.intent == "scheduled_cloud" or intent.confirmed
    return intent.intent in EXPLICIT_INTENTS


# ── routine background jobs ──────────────────────────────────────────────────

#: Routine jobs by the name the code passes. The scheduler's ids for the
#: built-in routines are routine too (``is_routine_job``).
ROUTINE_JOBS = frozenset({"news", "podcast", "briefing", "heartbeat",
                          "daily_creation"})

#: The routine jobs a CHOSEN ``cloud_only`` releases onto the cloud (owner
#: decision 2026-10-15: "news, podcasts, briefings and the heartbeat"): the
#: News routines by name and by scheduler id, podcast scripts, the heartbeat.
#: Daily creation is not named and keeps its explicit opt-in.
CLOUD_ONLY_RELEASES = frozenset({
    "news", "briefing", "podcast", "heartbeat",
    "sch_news_morning", "sch_front_page_evening", "sch_afternoon_briefing",
    "sch_weekly_digest", "sch_weekly_editorial", "sch_heartbeat",
})


@dataclass(frozen=True)
class BackgroundRoute:
    """Where a routine background job runs, and why (for the log and the UI)."""
    route: str             # "local" | "cloud" | "skip"
    reason: str
    mode: str
    model: str = ""        # the cloud model the run is pinned to (route == "cloud")
    local_model: str = ""  # the serving local seat (route == "local"), when known


def is_routine_job(job) -> bool:
    j = str(job or "").strip()
    if j in ROUTINE_JOBS:
        return True
    try:
        from agent_friday.services.scheduler import LOCAL_ONLY_BY_DEFAULT
        return j in LOCAL_ONLY_BY_DEFAULT
    except Exception:
        return False


def _background_mode(mode=None) -> str:
    """The OWNER's mode, not the guard-shadowed one: a local-only scope makes
    ``routing_mode`` say ``local_only`` while it is open, which is the scope's
    effect, not the owner's choice. Unreadable fails closed to ``local_only``."""
    if mode is not None:
        return normalize_mode(mode)
    try:
        return normalize_mode(_stored_mode())
    except Exception:
        return "local_only"


def background_route(job, *, local_serving=None, mode=None) -> BackgroundRoute:
    """Where routine background job ``job`` runs right now. The truth table is
    in this module's docstring; this function is the only place it is written.

    ``job`` is a scheduler id (``sch_heartbeat``) or a routine name
    (``"news"``, ``"podcast"``). ``local_serving`` is the serving local seat:
    a bool, a model name, a callable returning either, or None to ask the
    scheduler (``_resolve_local_seat``). It is asked only when the answer
    depends on it, so the consent row never probes the seat.

    ``mode`` given explicitly is the caller's own question ("what would this
    mode do?") and is taken as chosen; None reads the owner's stored mode and
    needs the ``mode_chosen`` record for the consent row.

    A cloud answer always names a non-empty model to pin the run to; with no
    model set for the job the answer is skip, and it says so.
    """
    job = str(job or "").strip()
    m = _background_mode(mode)
    try:
        from agent_friday.services import scheduled_cloud as _sc
        cfg = _sc.settings()
        covered = job in {sid for sid, _ in _sc.JOBS}
        model = str(_sc.model_for(job, cfg) or "").strip()
    except Exception:
        cfg, covered, model = {}, False, ""
    unchosen = ""
    if (m == "cloud_only" and job in CLOUD_ONLY_RELEASES
            and cfg.get("routine_follows_cloud_mode", True)):
        # Consent is the CHOOSING of cloud_only, not the factory value.
        chosen = True if mode is not None else mode_was_chosen()
        if not chosen:
            unchosen = (" (the starting setting, not one you chose in Settings "
                        "or setup, so it is not consent)")
        else:
            if not model:
                return BackgroundRoute(
                    "skip", "the routing mode is cloud only, but no cloud model is "
                    "set for this job (Settings > Spending)", m)
            return BackgroundRoute(
                "cloud", "the routing mode is cloud only, and choosing it is consent "
                "for News, podcasts, briefings and the heartbeat to use the cloud",
                m, model)
    serving = local_serving
    if serving is None:
        try:
            from agent_friday.services.scheduler import _resolve_local_seat
            serving = _resolve_local_seat
        except Exception:
            serving = False
    if callable(serving):
        try:
            serving = serving()
        except Exception:
            serving = False
    if serving:
        return BackgroundRoute("local", "a local model is serving", m,
                               local_model=serving if isinstance(serving, str) else "")
    if covered and cfg.get("allow"):
        if not model:
            return BackgroundRoute(
                "skip", "no local model is serving, and no cloud model is set for "
                "this job (Settings > Spending)", m)
        return BackgroundRoute(
            "cloud", "no local model is serving and you allowed this job onto %s "
            "(Settings > Spending)" % model, m, model)
    return BackgroundRoute(
        "skip", "no local model is serving, the routing mode is %s%s, and this job "
        "is not allowed onto a cloud model" % (m.replace("_", " "), unchosen), m)


def _cloud_model_ready(model: str):
    """(ready, why) for a cloud model a saved job is pinned to: Friday knows
    which service serves it and that service has its key."""
    try:
        import agent_friday.core as core
        provider = core._provider_for_model(model)
    except Exception:
        provider = None
    if not provider:
        return False, "Friday does not know which service serves %s" % model
    try:
        from agent_friday.services.provider_registry import get_provider_registry
        if get_provider_registry().is_provider_available(provider):
            return True, ""
    except Exception as e:
        return False, "the %s service could not be checked (%s)" % (provider, e)
    return False, "the %s service is not set up (no key, or switched off)" % provider


def pinned_job_route(model, *, local_serving=None, cloud_ready=None) -> BackgroundRoute:
    """Where a saved job the OWNER pinned to ``model`` runs right now.

    The second half of the one decision (``background_route`` is the first,
    for Friday's own routines). The pin is the owner's choice of model, made
    when the job was saved, and it is the consent: a cloud pin runs on that
    cloud model in every routing mode (``scheduled_cloud`` intent, the same as
    a routine allowed onto the cloud), a local pin runs on that local seat in
    every mode. The routing mode does not move a pinned job, and nothing else
    does either: when the pinned model cannot answer, the run is ``held`` with
    the reason, never sent to another model.

    ``local_serving(model)`` and ``cloud_ready(model)`` are for tests; by
    default the seat is asked what it is serving (``local_call.describe_dispatch``)
    and the provider registry whether the cloud service has its key.
    """
    model = str(model or "").strip()
    m = _background_mode()
    if not model:
        return BackgroundRoute("held", "it has no pinned model", m)
    from agent_friday.services.local_only_guard import is_cloud_model_tag
    local = None
    if not is_cloud_model_tag(model):
        try:
            from agent_friday.services.visible_jobs import is_local_model
            local = is_local_model(model)
        except Exception:
            local = None
    if local:
        if local_serving is None:
            def local_serving(name):
                from agent_friday.services.local_call import describe_dispatch
                return (describe_dispatch(name) or {}).get("route") in ("seat", "daemon")
        try:
            up = bool(local_serving(model))
        except Exception:
            up = False
        if up:
            return BackgroundRoute("local", "pinned to the local model %s, which is serving" % model,
                                   m, local_model=model)
        return BackgroundRoute(
            "held", "its pinned local model %s is not serving on this computer. It was not "
            "moved to another model" % model, m)
    ready, why = (cloud_ready(model) if cloud_ready is not None else _cloud_model_ready(model))
    if ready:
        return BackgroundRoute("cloud", "pinned by you to the cloud model %s" % model, m, model)
    return BackgroundRoute(
        "held", "its pinned model %s is not available: %s. It was not moved to another "
        "model" % (model, why or "unknown reason"), m)


def opens_cloud(mode: str | None = None) -> bool:
    """True when this context may call the cloud in ``mode``: it carries an intent
    that opens that mode, or the run is a job the owner pinned to a cloud model."""
    m = mode if mode is not None else routing_mode()
    return bool(_pin()) or allows(m, current())


# ── the choke point ──────────────────────────────────────────────────────────

#: The name the Ollama relay transports pass (`require("ollama", purpose, model,
#: base_url=...)`): the daemon a transport posts to. It is not a registered
#: provider (the built-in descriptor is `ollama-local`), so the seat-name rule
#: alone would call every daemon model a cloud call and refuse a plain local
#: model in a local mode.
_OLLAMA_RELAY = "ollama"


def _relay_descriptor():
    """A descriptor registered under the relay's own name, in any letter case,
    or None. A registry that cannot be read has none: the relay is then judged
    by its daemon address and its model alone, as the egress gate judges it."""
    try:
        from agent_friday.services.provider_registry import get_provider_registry
        reg = get_provider_registry()
        hit = reg.get_provider(_OLLAMA_RELAY)
        if hit is not None:
            return hit
        for p in reg.list_providers() or ():
            if isinstance(p, dict) and str(p.get("name") or "").strip().lower() == _OLLAMA_RELAY:
                return p
    except Exception:
        return None
    return None


def _relay_is_local(model: str, base_url) -> bool:
    """The Ollama relay stays on this machine only when ALL hold: the model is
    not cloud-tagged; the daemon address is loopback or private; a descriptor
    registered as "ollama" (any case), if there is one, is local; and the
    daemon's inventory rules out a cloud alias (`local_call.relay_model_status`:
    an inventory never read and unreadable now is unknown, which is not local
    unless the model is one this PC serves from its own seat)."""
    from agent_friday.services.local_only_guard import (
        is_cloud_model_tag, ollama_daemon_is_local)
    from agent_friday.services.seat_policy import is_local_descriptor
    if is_cloud_model_tag(model):
        return False
    if not ollama_daemon_is_local(base_url):
        return False
    descriptor = _relay_descriptor()
    if descriptor is not None and not is_local_descriptor(descriptor):
        return False
    from agent_friday.services.local_call import relay_model_status
    return relay_model_status(model) == "local"


def _is_local(provider, model: str, base_url=None) -> bool:
    try:
        from agent_friday.services.seat_policy import (
            is_local_descriptor, is_local_seat)
        from agent_friday.services.local_only_guard import is_cloud_model_tag
        if isinstance(provider, dict):
            return (not is_cloud_model_tag(model)) and is_local_descriptor(provider)
        if str(provider or "").strip().lower() == _OLLAMA_RELAY:
            return _relay_is_local(model, base_url)
        return is_local_seat(provider, model)
    except Exception:
        return False


def _pin() -> str:
    try:
        from agent_friday.services.local_only_guard import pinned_model
        return pinned_model()
    except Exception:
        return ""


def require(provider, purpose: str, model: str = "", *, base_url=None) -> CloudToken:
    """Every cloud transport calls this FIRST. Returns a token or raises.

    ``provider`` is a provider name or a descriptor dict (as the transports take
    it). A provider that runs on this machine returns an intent-free ``local``
    token. ``base_url`` is the address an Ollama relay transport (provider
    ``"ollama"``) will post to; None means the configured daemon address.
    Otherwise:

    * a run the owner pinned to a cloud model carries ``scheduled_cloud``;
    * ``cloud_only`` and ``cloud_preferred`` carry the implicit ``cloud_mode``;
    * a local mode needs the context's intent to open the cloud in that mode, or
      raises ``NoCloudIntent``.
    """
    from agent_friday.services.local_only_guard import provider_name_of
    name = provider_name_of(provider) or "cloud"
    model = str(model or "")
    purpose = str(purpose or "model call")
    if _is_local(provider, model, base_url):
        return CloudToken("local", name, purpose, model, "", "")
    # Safe mode (services/safe_mode.py): no cloud at all, whatever the mode or
    # the turn's intent.
    from agent_friday.services import safe_mode as _sm
    if not _sm.allows("cloud"):
        _log.warning("refused a cloud call in safe mode: %s -> %s (%s)", purpose, name, model)
        raise NoCloudIntent(name, purpose, model, "safe_mode")
    mode = routing_mode()
    cur = current()
    if _pin():
        tok = CloudToken("scheduled_cloud", name, purpose, model, mode, "pinned_job")
    elif mode in LOCAL_MODES:
        if not allows(mode, cur):
            _log.warning("refused a cloud call with no intent: %s -> %s (%s) in %s",
                         purpose, name, model, mode)
            raise NoCloudIntent(name, purpose, model, mode)
        tok = CloudToken(cur.intent, name, purpose, model, mode, cur.source)
    else:
        # cloud_only / cloud_preferred: the mode is the intent. A more specific intent, when the
        # route has one, is the better receipt.
        tok = (CloudToken(cur.intent, name, purpose, model, mode, cur.source)
               if cur is not None else
               CloudToken("cloud_mode", name, purpose, model, mode, "mode"))
    _record(tok)
    return tok


def _record(tok: CloudToken) -> None:
    """Receipt: the thread's turn attribution, an in-memory ring, an append-only
    file. Never raises; a receipt failure must not cost the owner the answer."""
    with _RECENT_LOCK:
        _RECENT.append(tok)
        del _RECENT[:-_RECENT_MAX]
    try:
        from agent_friday.services import attribution as _attr
        _attr.note_cloud_intent(tok.as_dict())
    except Exception:
        pass
    try:
        from agent_friday import paths
        p = paths.runtime_dir() / "cloud_intents.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Append-only: a full file is closed under a dated name and a new one
            # starts. Nothing is ever overwritten or erased.
            if p.stat().st_size > _RECEIPT_FILE_MAX:
                stamp = int(time.time())
                for n in range(1000):
                    closed = p.with_name("cloud_intents-%d-%d.jsonl" % (stamp, n))
                    if not closed.exists():
                        p.rename(closed)
                        break
        except OSError:
            pass
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(tok.as_dict()) + "\n")
    except Exception:
        pass


def record_owner_engine(provider, purpose: str, model: str = "",
                        source: str = "voice_engine") -> CloudToken:
    """Receipt for a cloud engine the owner picked by name (Settings > Voice).

    These are not gated by `require`: the owner's engine setting is the choice,
    and local_only refuses them in code elsewhere. The receipt keeps the record of
    what left complete."""
    from agent_friday.services.local_only_guard import provider_name_of
    tok = CloudToken("user_explicit", provider_name_of(provider) or "cloud",
                     str(purpose or "voice"), str(model or ""), routing_mode(), source)
    _record(tok)
    return tok


def require_owner_engine(provider, purpose: str, model: str = "",
                         source: str = "voice_engine") -> CloudToken:
    """The checkpoint for a cloud engine the owner picked by name (Gemini Live).

    The owner's engine choice is a recorded ``user_explicit`` intent, so this
    goes through ``require`` like every other cloud transport and leaves the
    same receipt. Outcomes by mode:

    * ``cloud_only`` / ``cloud_preferred``: allowed (the mode is the intent; the
      more specific ``user_explicit`` is the receipt).
    * ``local_preferred``: allowed by the recorded intent. Same result as before.
    * ``local_only``: refused (``NoCloudIntent``). The intent is not CONFIRMED
      for a turn, and a voice engine setting is not that confirm.
    """
    with intent_scope("user_explicit", source=source, confirmed=False):
        return require(provider, purpose, model)


def recent(n: int = 50) -> list:
    with _RECENT_LOCK:
        return [t.as_dict() for t in _RECENT[-n:]]


def forget_recent() -> None:
    with _RECENT_LOCK:
        _RECENT.clear()
