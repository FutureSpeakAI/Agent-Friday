"""Lifecycle hooks for the internal tool loop (Part B of Self-Sufficient Friday).

Every native and MCP tool call funnels through ``_execute_tool``
(``services/agent.py``) — the one choke point through which all tool execution
passes. This module turns the hard-coded gate sequence that used to live there
(confirmation → governance → sandbox → log → PII scrub) into an ordered,
registrable chain of **PreToolUse** and **PostToolUse** hooks, and exposes a
registration API so new governance / observability behaviours — and
user/skill-supplied hooks — can attach without editing tool code.

Design: this module is *pure mechanism* (the registry + dispatch + a generic
token-bucket rate limiter). The built-in hook *functions* that depend on
governance/confirmation/PII live where those dependencies live
(``services/agent.py`` registers them at import time). That keeps this a leaf
module with no upward imports and avoids the import cycle.

Guarantees
----------
* **Pre hooks** return a :class:`PreVerdict` — ``ALLOW`` / ``MODIFY(new_input)`` /
  ``DENY(reason)``. A DENY short-circuits the chain; a MODIFY rewrites the input
  the remaining hooks (and the handler) see.
* **Post hooks** receive ``(ctx, result)`` and return a (possibly transformed)
  result; they may also fire-and-forget side effects (logging, follow-ups).
* **Exception isolation:** a hook that throws is logged and treated as ALLOW
  (pre) / passthrough (post) — a buggy hook can never brick tool execution.
  *Exception:* hooks marked ``critical=True`` (governance, vault) fail **closed**
  (a throw becomes a DENY) so a crash can't open a security gate.
* **Ordering:** lower ``priority`` runs first. Built-ins occupy 0–99; user/skill
  hooks default to ``priority=100`` so they always run *after* the built-in
  critical gates and can only *tighten* governance, never loosen a built-in DENY
  (the chain already short-circuited).
* **Settings toggles:** non-critical built-ins honour
  ``settings.tool_hooks.<name>.enabled`` (default on). Critical hooks ignore the
  toggle — they cannot be disabled from the UI.
"""

import threading
import time as _time
from dataclasses import dataclass, field

import agent_friday.core as core


# ── Verdicts ────────────────────────────────────────────────────────────────
class PreVerdict:
    """Result of a PreToolUse hook: allow / modify-input / deny."""

    __slots__ = ("action", "new_input", "reason", "hook")

    def __init__(self, action, *, new_input=None, reason=None, hook=None):
        self.action = action            # "allow" | "modify" | "deny"
        self.new_input = new_input
        self.reason = reason
        self.hook = hook

    def __repr__(self):
        return f"PreVerdict({self.action!r}, reason={self.reason!r})"


ALLOW = PreVerdict("allow")


def MODIFY(new_input):
    """Rewrite the tool input the remaining hooks + handler will see."""
    return PreVerdict("modify", new_input=new_input)


def DENY(reason):
    """Veto the tool call. ``reason`` is surfaced to the model as the result."""
    return PreVerdict("deny", reason=str(reason))


@dataclass
class HookContext:
    """Carried through a single tool execution, shared pre → handler → post.

    ``meta`` is scratch space hooks use to pass state across phases (e.g. the
    audit hook stamps a start time in pre and reads it in post).
    """

    tool_name: str
    input: dict
    session_ctx: dict | None = None
    pii_lookup: dict | None = None
    meta: dict = field(default_factory=dict)

    @property
    def workspace(self):
        return (self.session_ctx or {}).get("workspace") or ""

    @property
    def run_id(self):
        sc = self.session_ctx or {}
        return sc.get("run_id") or sc.get("schedule_run_id")

    @property
    def schedule_id(self):
        return (self.session_ctx or {}).get("schedule_id")


# ── Registry ─────────────────────────────────────────────────────────────────
@dataclass
class _Hook:
    fn: object
    name: str
    phase: str                  # "pre" | "post"
    priority: int = 100
    tools: set | None = None    # None → global; else scoped to these tool names
    critical: bool = False      # fail-closed on exception; ignores disable toggle
    source: str = "builtin"     # "builtin" | "skill" | "user"


_PRE_HOOKS: list[_Hook] = []
_POST_HOOKS: list[_Hook] = []
_LOCK = threading.Lock()


def _insert_sorted(bucket, hook):
    with _LOCK:
        # Remove any prior registration of the same name (idempotent re-register).
        bucket[:] = [h for h in bucket if h.name != hook.name]
        bucket.append(hook)
        bucket.sort(key=lambda h: (h.priority, h.name))


def register_pre_hook(fn, *, name, priority=100, tools=None, critical=False,
                      source="builtin"):
    """Register a PreToolUse hook.

    ``fn(ctx: HookContext) -> PreVerdict | None``. Returning ``None`` is treated
    as ALLOW. ``tools=None`` → global; pass a set to scope to specific tools.
    """
    _insert_sorted(_PRE_HOOKS, _Hook(
        fn=fn, name=name, phase="pre", priority=priority,
        tools=set(tools) if tools else None, critical=critical, source=source))


def register_post_hook(fn, *, name, priority=100, tools=None, critical=False,
                       source="builtin"):
    """Register a PostToolUse hook.

    ``fn(ctx: HookContext, result: str) -> str``. The return value becomes the
    new result. Exceptions are swallowed (the prior result passes through).
    """
    _insert_sorted(_POST_HOOKS, _Hook(
        fn=fn, name=name, phase="post", priority=priority,
        tools=set(tools) if tools else None, critical=critical, source=source))


def unregister_hook(name):
    """Remove a hook by name from both chains (used by tests / hot-reload)."""
    with _LOCK:
        _PRE_HOOKS[:] = [h for h in _PRE_HOOKS if h.name != name]
        _POST_HOOKS[:] = [h for h in _POST_HOOKS if h.name != name]


def _hook_settings():
    try:
        return (core._load_settings() or {}).get("tool_hooks") or {}
    except Exception:
        return {}


def hook_enabled(name, critical=False):
    """A hook runs when it's critical, or its settings toggle isn't off.

    Default is on — an absent entry means enabled.
    """
    if critical:
        return True
    cfg = _hook_settings().get(name)
    if isinstance(cfg, dict):
        return cfg.get("enabled", True) is not False
    if isinstance(cfg, bool):
        return cfg
    return True


def _applies(hook, tool_name):
    return hook.tools is None or tool_name in hook.tools


# ── Dispatch ─────────────────────────────────────────────────────────────────
def run_pre_hooks(ctx: HookContext) -> PreVerdict:
    """Run the PreToolUse chain. Returns the first DENY verdict, or ALLOW.

    Applies any MODIFY verdicts to ``ctx.input`` in place as it goes.
    """
    for hook in list(_PRE_HOOKS):
        if not _applies(hook, ctx.tool_name):
            continue
        if not hook_enabled(hook.name, hook.critical):
            continue
        try:
            verdict = hook.fn(ctx)
        except Exception as e:  # noqa: BLE001
            if hook.critical:
                # Fail closed — a crash in a security gate must not open it.
                return PreVerdict("deny",
                                  reason=f"{hook.name} errored (fail-closed): {e}",
                                  hook=hook.name)
            _log_hook_error("pre", hook.name, e)
            continue
        if verdict is None or verdict.action == "allow":
            continue
        if verdict.action == "modify":
            if isinstance(verdict.new_input, dict):
                ctx.input = verdict.new_input
            continue
        if verdict.action == "deny":
            verdict.hook = verdict.hook or hook.name
            return verdict
    return ALLOW


def run_post_hooks(ctx: HookContext, result: str) -> str:
    """Run the PostToolUse chain, threading ``result`` through each hook."""
    for hook in list(_POST_HOOKS):
        if not _applies(hook, ctx.tool_name):
            continue
        if not hook_enabled(hook.name, hook.critical):
            continue
        try:
            out = hook.fn(ctx, result)
            if out is not None:
                result = out
        except Exception as e:  # noqa: BLE001
            _log_hook_error("post", hook.name, e)
            continue
    return result


def _log_hook_error(phase, name, err):
    try:
        print(f"  [tool-hook:{phase}:{name}] error (ignored): {err}")
    except Exception:
        pass


# ── Introspection (for the Settings → Active Hooks list) ─────────────────────
def list_hooks():
    """Return all registered hooks for the read-only Settings UI."""
    rows = []
    for hook in list(_PRE_HOOKS) + list(_POST_HOOKS):
        rows.append({
            "name": hook.name,
            "phase": hook.phase,
            "priority": hook.priority,
            "scope": "global" if hook.tools is None else sorted(hook.tools),
            "critical": hook.critical,
            "source": hook.source,
            "enabled": hook_enabled(hook.name, hook.critical),
        })
    rows.sort(key=lambda r: (r["phase"], r["priority"], r["name"]))
    return rows


# ── Built-in: generic token-bucket rate limiter ──────────────────────────────
# Caps tool-call frequency per (tool-family, ring) so a runaway agent loop can't
# hammer a network API or burn spend. Lives here (pure mechanism) and is
# registered as a pre-hook by services/agent.py, which supplies the ring lookup.
class _TokenBucket:
    __slots__ = ("capacity", "tokens", "refill_per_sec", "last")

    def __init__(self, capacity, refill_per_sec):
        self.capacity = float(capacity)
        self.tokens = float(capacity)
        self.refill_per_sec = float(refill_per_sec)
        self.last = _time.time()

    def take(self):
        now = _time.time()
        self.tokens = min(self.capacity,
                          self.tokens + (now - self.last) * self.refill_per_sec)
        self.last = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


_BUCKETS: dict = {}
_BUCKET_LOCK = threading.Lock()


def rate_limit_check(key, per_minute):
    """Return True if a call keyed by ``key`` is allowed under ``per_minute``.

    ``per_minute <= 0`` disables limiting for that key (always allowed).
    """
    try:
        per_minute = int(per_minute)
    except Exception:
        per_minute = 0
    if per_minute <= 0:
        return True
    with _BUCKET_LOCK:
        bucket = _BUCKETS.get(key)
        if bucket is None or abs(bucket.capacity - per_minute) > 0.5:
            bucket = _TokenBucket(per_minute, per_minute / 60.0)
            _BUCKETS[key] = bucket
        return bucket.take()


def reset_rate_limiter():
    """Clear all buckets (tests)."""
    with _BUCKET_LOCK:
        _BUCKETS.clear()
