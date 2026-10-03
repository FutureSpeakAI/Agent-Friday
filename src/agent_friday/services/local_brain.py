"""The local brain seat, as the local model may know it about itself.

Two readers share one answer here:

  * the system prompt, which tells a local seat in one line what it is: the
    model on this PC serving the brain seat, its working window, what it
    cannot do, and that the conversation stays on this machine
    (`self_knowledge_line`);
  * the read-only `local_model_status` tool, which answers "which model is
    answering, how much can it hold, is it busy" from the same facts
    (`status`).

Both read the residency Arbiter's plan in this process. Neither makes a
network call, so building a prompt never waits on a seat. Nothing here raises:
a fact that cannot be established is left out rather than guessed.
"""
from __future__ import annotations

import contextlib
import threading

#: The plan's names for the seat that answers chat turns, canonical first.
BRAIN_ROLES = ("interactive_brain", "brain")

# Rounds the local tool loop is sending to a local seat right now. A round is
# counted from the moment the request leaves until the reply is back, so the
# number is the seat's real generation load, not the number of open turns.
_GEN_LOCK = threading.Lock()
_GENERATING = [0]


@contextlib.contextmanager
def generating():
    """Mark one round in flight to the local seat for its duration."""
    with _GEN_LOCK:
        _GENERATING[0] += 1
    try:
        yield
    finally:
        with _GEN_LOCK:
            _GENERATING[0] = max(0, _GENERATING[0] - 1)


def rounds_in_flight() -> int:
    with _GEN_LOCK:
        return _GENERATING[0]


def _arbiter():
    try:
        from agent_friday.services.residency_arbiter import get_arbiter
        return get_arbiter()
    except Exception:
        return None


def brain_seat() -> dict | None:
    """The brain seat from the Arbiter's plan, or None when there is none.

    `{"seat", "model", "context", "status", "device", "backend"}`; a field the
    plan does not carry is None.
    """
    arb = _arbiter()
    if arb is None:
        return None
    try:
        seats = (getattr(arb, "plan", None) or {}).get("seats") or {}
    except Exception:
        return None
    for role in BRAIN_ROLES:
        s = seats.get(role)
        if isinstance(s, dict) and s.get("model_id"):
            ctx = s.get("num_ctx")
            try:
                ctx = int(ctx) if ctx else None
            except (TypeError, ValueError):
                ctx = None
            return {"seat": role, "model": str(s.get("model_id")),
                    "context": ctx, "status": s.get("status"),
                    "device": s.get("device"), "backend": s.get("backend")}
    return None


def status() -> dict:
    """What `local_model_status` reports. Read-only; never raises."""
    seat = brain_seat()
    arb = _arbiter()
    state, lease = None, None
    if arb is not None:
        try:
            state = getattr(arb, "state", None)
            lease = dict(arb.lease) if getattr(arb, "lease", None) else None
        except Exception:
            state, lease = None, None
    inflight = rounds_in_flight()
    reasons = []
    if lease:
        reasons.append("the GPU is lent to a %s" % str(lease.get("kind") or "job")
                       .replace("_", " "))
    if state and state not in ("DEFAULT", "LEASED"):
        reasons.append("the seats are %s" % str(state).lower().replace("_", " "))
    if inflight:
        reasons.append("%d request%s generating" % (inflight, "" if inflight == 1 else "s"))
    out = {
        "serving": bool(seat),
        "seat": (seat or {}).get("seat"),
        "model": (seat or {}).get("model"),
        "context_tokens": (seat or {}).get("context"),
        "seat_status": (seat or {}).get("status"),
        "device": (seat or {}).get("device"),
        "busy": bool(reasons),
        "busy_because": "; ".join(reasons),
        "rounds_in_flight": inflight,
        "runs_on": "this PC",
    }
    if not seat:
        out["note"] = ("No local brain seat is in the residency plan in this "
                       "process, so which model is serving cannot be confirmed.")
    return out


def _is_local(provider) -> bool:
    try:
        from agent_friday.services.egress_gate import is_local_provider
        return bool(is_local_provider(str(provider or "")))
    except Exception:
        return False


def self_knowledge_line(provider) -> str:
    """One line for a local seat's system prompt; "" for any other provider.

    The line names the serving model and its window when the plan has them,
    and otherwise says only what is certain: it is the local model on this PC.
    """
    if not _is_local(provider):
        return ""
    seat = brain_seat()
    who = "the local model %s" % seat["model"] if seat else "the local model"
    window = ""
    if seat and seat.get("context"):
        window = " with a %s-token working window" % format(seat["context"], ",")
    return (
        "You are %s, running on this PC as Friday's brain seat%s. This "
        "conversation is private: it stays on this machine and no cloud model "
        "sees it. You are slower than the cloud models and hold less at once, so "
        "keep tool calls few and their arguments exact. You know only this prompt "
        "and what your tools return, and a tool such as web search sends its "
        "arguments off this PC. local_model_status reports your own seat, window "
        "and load." % (who, window))
