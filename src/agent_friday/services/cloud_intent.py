"""The one choke point between a request and a cloud model.

Routing modes (Beta 1.1, routing-modes addendum 2.1 / 2.4):

* ``cloud_only``      -- the cloud is the answer; the intent ``cloud_mode`` is implicit.
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

    ``local_only`` while a local-only run is active; otherwise
    ``model_routing.mode`` (the offline overlay is folded in by
    ``_load_settings``). A missing setting is the factory default,
    ``cloud_only``. An unreadable setting fails CLOSED to ``local_only``.
    """
    try:
        from agent_friday.services import local_only_guard as _guard
        if _guard.is_active():
            return "local_only"
    except Exception:
        return "local_only"
    try:
        from agent_friday import core
        mode = ((core._load_settings() or {}).get("model_routing") or {}).get("mode")
        return str(mode or "cloud_only").strip().lower()
    except Exception:
        return "local_only"


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


def opens_cloud(mode: str | None = None) -> bool:
    """True when this context may call the cloud in ``mode``: it carries an intent
    that opens that mode, or the run is a job the owner pinned to a cloud model."""
    m = mode if mode is not None else routing_mode()
    return bool(_pin()) or allows(m, current())


# ── the choke point ──────────────────────────────────────────────────────────

def _is_local(provider, model: str) -> bool:
    try:
        from agent_friday.services.seat_policy import (
            is_local_descriptor, is_local_seat)
        if isinstance(provider, dict):
            from agent_friday.services.local_only_guard import is_cloud_model_tag
            return (not is_cloud_model_tag(model)) and is_local_descriptor(provider)
        return is_local_seat(provider, model)
    except Exception:
        return False


def _pin() -> str:
    try:
        from agent_friday.services.local_only_guard import pinned_model
        return pinned_model()
    except Exception:
        return ""


def require(provider, purpose: str, model: str = "") -> CloudToken:
    """Every cloud transport calls this FIRST. Returns a token or raises.

    ``provider`` is a provider name or a descriptor dict (as the transports take
    it). A provider that runs on this machine returns an intent-free ``local``
    token. Otherwise:

    * a run the owner pinned to a cloud model carries ``scheduled_cloud``;
    * ``cloud_only`` carries the implicit ``cloud_mode``;
    * a local mode needs the context's intent to open the cloud in that mode, or
      raises ``NoCloudIntent``.
    """
    from agent_friday.services.local_only_guard import provider_name_of
    name = provider_name_of(provider) or "cloud"
    model = str(model or "")
    purpose = str(purpose or "model call")
    if _is_local(provider, model):
        return CloudToken("local", name, purpose, model, "", "")
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
        # cloud_only: the mode is the intent. A more specific intent, when the
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


def recent(n: int = 50) -> list:
    with _RECENT_LOCK:
        return [t.as_dict() for t in _RECENT[-n:]]


def forget_recent() -> None:
    with _RECENT_LOCK:
        _RECENT.clear()
