"""Mark a run local-only, and refuse any cloud call inside it.

The product rule: daily creation, briefings, the news front page and the
heartbeat default to the local reasoning model, so that routine scheduled work
costs nothing. And they must NOT silently fall back to a cloud model -- if the
local model is down or busy they wait, retry inside their window, then SKIP with
a visible reason. Cloud only if a job is explicitly opted in.

`scheduler._run_task` honours `local_only: true` on an **agent_prompt** task: it
resolves the local seat and raises `SkippedRun` rather than going to the cloud,
so routine checks of email and calendar are not billed. Heartbeat runs under
that rule serve on `arbiter-local/bonsai2:27b` at $0.00.

Two gaps this closes.

1. `local_only` was only read on the agent_prompt path. Every BUILTIN schedule
   (daily creation, the briefings, the news front page) ignored it completely,
   because `_run_task` just calls `meta["fn"]()` and the job picks its own model
   internally.

2. Even on the agent_prompt path, pinning a model at spawn time is not the same
   as forbidding cloud for the whole run. `_generate_agent`'s own fallback ladder
   can retry a failed leg on a DIFFERENT provider (`agent.py` journals
   `ladder_fallback`), so one failed local leg could still reach Anthropic. A
   heartbeat run that does so costs around $0.42 -- almost all of it ~112,000
   CACHE-WRITE tokens, i.e. the price of shipping the full system prompt, not of
   the hundred or so tokens it actually says.

So the flag becomes a property of the RUN, not of one dispatch decision, and the
refusal lands at the cloud transports themselves -- the last place before the
money is spent, and the one place no new fallback path can route around.
"""

from __future__ import annotations

import logging
import threading

_log = logging.getLogger("friday.local_only")

_state = threading.local()


class CloudRefused(RuntimeError):
    """A local-only run tried to reach a paid provider.

    Raised rather than silently downgraded: the caller's retry/skip logic needs
    to know the difference between "the local seat is busy" and "this run is not
    allowed to leave the machine".
    """


def is_active() -> bool:
    return bool(getattr(_state, "active", False))


def routing_mode() -> str:
    """The routing mode a direct (non-router) cloud call must honour.

    The user's model_routing.mode (the offline overlay is folded in by
    `_load_settings`), or "local_only" while a local-only run is active.
    `smart` reads as `local_preferred`. A blank mode is unset and behaves as
    the factory default, `cloud_only` (one reading, `cloud_intent.routing_mode`,
    which this delegates to). An unreadable setting fails CLOSED to
    "local_only": the cloud is reached only on an affirmative read.
    """
    if is_active():
        return "local_only"
    try:
        from agent_friday.services import cloud_intent as _ci
        return _ci.normalize_mode(_ci.routing_mode())
    except Exception:
        return "local_only"


def label() -> str:
    return str(getattr(_state, "label", "") or "this job")


class local_only:
    """Context manager marking everything inside as local-only.

    Thread-local, and re-entrant. Scheduled jobs each run on their own thread, so
    one job being local-only never constrains an interactive turn on another.
    """

    def __init__(self, job_label: str = ""):
        self.job_label = job_label or "this job"
        self._prev = None
        self._prev_label = None

    def __enter__(self):
        self._prev = getattr(_state, "active", False)
        self._prev_label = getattr(_state, "label", "")
        # The stricter mark wins: a local-only scope inside a cloud pin is
        # local-only for its duration, so the pin is lifted until it exits.
        self._prev_pin = (getattr(_state, "pin", ""), getattr(_state, "pin_label", ""))
        _state.pin, _state.pin_label = "", ""
        _state.active = True
        _state.label = self.job_label
        return self

    def __exit__(self, *exc):
        _state.active = self._prev
        _state.label = self._prev_label
        _state.pin, _state.pin_label = self._prev_pin
        return False


def provider_name_of(provider) -> str:
    """The provider's NAME, whether it arrived as a string or a descriptor dict.

    `_call_openai` takes `provider` as EITHER a registry name or a full
    descriptor dict (the multi-provider path). `str(provider or "openai")` would
    stringify the whole dict -- so `{'name': 'arbiter-local', 'classification':
    'local', ...}` matches no local name and the guard refuses Friday's OWN LOCAL
    SEAT: "Daily creation is local-only, so it will not call {'name':
    'arbiter-local', ...}". A guard that blocks the thing it is
    supposed to permit is worse than no guard.
    """
    if isinstance(provider, dict):
        return str(provider.get("name") or provider.get("id") or "").strip()
    return str(provider or "").strip()


# Names the daemon's own inventory marks as remote (`remote_host` /
# `remote_model` on a /api/tags row), e.g. `ollama cp x:cloud myglm:latest`.
# Replaced wholesale by each successful inventory read; never fetched here.
_REMOTE_ALIASES: frozenset = frozenset()
_ALIASES_FILLED = False
_ALIAS_RETRY_AT = 0.0


def aliases_filled() -> bool:
    """True once a daemon inventory read has filled the alias set."""
    return _ALIASES_FILLED


def note_remote_aliases(rows) -> None:
    """Record which daemon /api/tags rows are relayed to a remote host.

    Called by the code that already reads the inventory, so classifying a
    model never costs a network call."""
    global _REMOTE_ALIASES, _ALIASES_FILLED
    names = set()
    for r in rows or ():
        if isinstance(r, dict) and (_names_a_remote(r.get("remote_host"))
                                    or _names_a_remote(r.get("remote_model"))):
            n = str(r.get("name") or r.get("model") or "").strip().lower()
            if n:
                names.add(n)
    _REMOTE_ALIASES = frozenset(names)
    _ALIASES_FILLED = True


def _names_a_remote(value) -> bool:
    """A `remote_host` / `remote_model` field that actually names somewhere.

    A model the daemon runs here has neither key, or an empty one; a blank or
    whitespace string is the same absence. Any other present value (a host, a
    model, or a shape this code does not know) counts as remote, so an
    unexpected inventory format fails toward cloud, never toward local."""
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return bool(value)


def _reset_aliases_for_tests(state=None) -> tuple:
    """Put the alias set back to ``state`` (from a previous call) or to the
    state a fresh process starts in; returns the state it replaced. The set is
    process-wide, so one test's inventory must not decide the next one's."""
    global _REMOTE_ALIASES, _ALIASES_FILLED, _ALIAS_RETRY_AT
    before = (_REMOTE_ALIASES, _ALIASES_FILLED, _ALIAS_RETRY_AT)
    _REMOTE_ALIASES, _ALIASES_FILLED, _ALIAS_RETRY_AT = (
        state if state is not None else (frozenset(), False, 0.0))
    return before


def ollama_daemon_is_local(base_url=None) -> bool:
    """Is the Ollama daemon a relay transport posts to on this machine or the
    local network? ``base_url`` is the address the transport will actually
    use; None means the configured ``model_routing.ollama_url``. Decided by the
    same host rule as provider descriptors (`is_private_host`): loopback,
    RFC1918, link-local and *.local names are local; a public address, or a
    name that cannot be resolved to only private addresses, is not. Any
    fault answers False, so an unverifiable daemon is treated as cloud."""
    try:
        url = str(base_url or "").strip()
        if not url:
            from agent_friday.services.local_call import ollama_url
            url = ollama_url()
        from agent_friday.routing.provider_descriptors import is_private_host
        return bool(url) and bool(is_private_host(url))
    except Exception:
        return False


def is_cloud_model_tag(model: str) -> bool:
    """An Ollama model the daemon relays to ollama.com rather than running:
    tag `cloud` or ending in `-cloud` (`glm-4.6:cloud`, `gpt-oss:120b-cloud`),
    or an untagged name ending in `-cloud`; or an alias the daemon's last
    inventory read marked as remote."""
    m = str(model or "").strip().lower()
    if not m:
        return False
    if m in _REMOTE_ALIASES:
        return True
    tag = m.rsplit(":", 1)[1] if ":" in m else m
    return tag == "cloud" or tag.endswith("-cloud")


def reader_model_is_cloud(model: str) -> bool:
    """True when a READER's model is one the daemon relays to ollama.com, by its tag or by an alias.

    The alias set (names the daemon's inventory marks remote_host/remote_model, e.g.
    `ollama cp x:cloud myglm:latest`) is filled by the code that reads the inventory. A meeting's
    words and titles go only to a reader that is local, so this decision must not be taken on a set
    that was never filled: when it has not been, and the model is not a seat of this PC's own, the
    daemon inventory is read once now (at most every 30 seconds while the read keeps failing).
    A daemon that cannot be read cannot be relaying an alias either, so a failed read leaves the
    answer to the tag check, as local_call does outside local-only mode.
    """
    m = str(model or "").strip()
    if not m:
        return False
    if is_cloud_model_tag(m):
        return True
    global _ALIAS_RETRY_AT
    if _ALIASES_FILLED:
        return False
    try:
        import time
        from agent_friday.services import local_call
        if local_call.seat_endpoint(m):
            return False
        if time.monotonic() < _ALIAS_RETRY_AT:
            return False
        local_call._daemon_tags()
        if not _ALIASES_FILLED:
            _ALIAS_RETRY_AT = time.monotonic() + 30.0
    except Exception:
        return False
    return is_cloud_model_tag(m)


def refuse_if_active(provider, model: str = "") -> None:
    """Raise when a cloud call is attempted inside a local-only run.

    Called from the cloud transports. `provider` may be a name or a descriptor
    dict. An unknown name is treated as cloud -- fail-closed, since the cost of
    being wrong the other way is a bill nobody chose.
    """
    if not is_active():
        return
    name = provider_name_of(provider)
    if is_cloud_model_tag(model):
        # Served through the local daemon, run in someone else's datacenter:
        # the provider name says local and the model says otherwise.
        name = "%s (a cloud-relayed model)" % (name or "ollama")
        _refuse(name, model)
    try:
        from agent_friday.services.seat_policy import is_local_provider_name
        if is_local_provider_name(name):
            return
        # A descriptor can also declare itself local outright.
        if isinstance(provider, dict) and                 str(provider.get("classification") or "").lower() == "local":
            return
    except Exception:
        pass
    _refuse(name or provider, model)


def _refuse(provider, model: str = "") -> None:
    msg = ("%s is local-only, so it will not call %s%s. It waits for the local "
           "seat and skips with a reason rather than spending money nobody "
           "chose." % (label(), provider or "a cloud provider",
                       (" (%s)" % model) if model else ""))
    _log.warning("refused a cloud call inside a local-only run: %s", msg)
    raise CloudRefused(msg)


# ── Scheduled jobs the owner allowed onto ONE cloud model ────────────────────
#
# The other half of the same rule. When no local model is serving, a local-only
# job is skipped; the owner can instead allow those jobs to run on a cloud
# model they chose (settings `scheduled_cloud`, services/scheduled_cloud.py).
# "Allowed onto the cloud" means that model, not whatever the router or a
# fallback leg reaches for: an unattended job that silently escalates to a
# frontier model is how an hourly heartbeat becomes a bill of hundreds of
# dollars. So the chosen model is a property of the RUN, pinned here, and the
# cloud transports apply it at the last point before the money is spent.

class cloud_pinned:
    """Context manager: every cloud call inside uses `model`, or is refused.

    Thread-local and re-entrant, like `local_only`. Entering a pin clears an
    enclosing local-only mark for its duration, because the owner allowed this
    run onto the cloud.
    """

    def __init__(self, model: str, job_label: str = ""):
        self.model = str(model or "").strip()
        self.job_label = job_label or "this job"
        self._prev = None

    def __enter__(self):
        self._prev = (getattr(_state, "pin", ""), getattr(_state, "pin_label", ""),
                      getattr(_state, "active", False))
        _state.pin = self.model
        _state.pin_label = self.job_label
        _state.active = False
        return self

    def __exit__(self, *exc):
        _state.pin, _state.pin_label, _state.active = self._prev
        return False


def pinned_model() -> str:
    """The cloud model this thread's run is pinned to, or ""."""
    return str(getattr(_state, "pin", "") or "")


def pin_snapshot() -> dict | None:
    """The active pin as a plain dict, to carry into a worker thread."""
    m = pinned_model()
    if not m:
        return None
    return {"model": m, "label": str(getattr(_state, "pin_label", "") or "")}


def local_only_snapshot() -> dict | None:
    """This thread's local-only run as a plain dict, to carry into a worker
    thread and onto disk, or None. A cloud pin also marks the thread active
    (it refuses every OTHER cloud call) and is carried by `pin_snapshot`, so
    it is not reported here."""
    if not is_active() or pinned_model():
        return None
    return {"label": label()}


def _gateway_id(model: str) -> str:
    """`claude-haiku-4-5-20251001` -> `anthropic/claude-haiku-4.5`.

    The OpenRouter spelling of an Anthropic id: vendor prefix, no date suffix,
    and a dot between version digits. `cost_meter._canonical_gateway_id`
    reverses it, so the call still meters at the Anthropic row.
    """
    import re as _re
    base = _re.sub(r"-\d{8}$", "", model)
    return "anthropic/" + _re.sub(r"(\d)-(\d)", r"\1.\2", base)


def apply_pin(provider, model: str | None = None) -> str | None:
    """The model a transport should send, given the run's pin.

    No pin: `model` unchanged. A local provider: unchanged (free, and a local
    seat is never what the pin protects against). Anthropic: the pinned Claude
    id. OpenRouter: the pinned id in its gateway spelling. Any other cloud
    provider cannot serve the chosen model, so the call is refused rather than
    sent to a model nobody chose.
    """
    pin = pinned_model()
    if not pin:
        return model
    name = provider_name_of(provider).lower()
    try:
        from agent_friday.services.seat_policy import is_local_provider_name
        if name and is_local_provider_name(name):
            return model
    except Exception:
        pass
    if isinstance(provider, dict) and \
            str(provider.get("classification") or "").lower() == "local":
        return model
    is_claude = pin.startswith("claude")
    if name in ("anthropic", "cloud", "claude", ""):
        if is_claude:
            return pin
    elif name == "openrouter":
        return _gateway_id(pin) if is_claude else pin
    label_ = str(getattr(_state, "pin_label", "") or "this job")
    msg = ("%s may run only on %s, which %s does not serve. It was not sent "
           "to a different model." % (label_, pin, name or "this provider"))
    _log.warning("refused a cloud call outside the pinned model: %s", msg)
    raise CloudRefused(msg)
