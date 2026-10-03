"""The local brain seat, as the local model may know it about itself.

The system prompt tells a local seat in one line what it is: the model on this
PC serving the brain seat, its working window, what it cannot do, and that the
conversation stays on this machine (`self_knowledge_line`).

The facts come from the residency Arbiter's plan in this process. Nothing here
makes a network call, so building a prompt never waits on a seat, and nothing
raises: a fact that cannot be established is left out rather than guessed.
"""
from __future__ import annotations

#: The plan's names for the seat that answers chat turns, canonical first.
BRAIN_ROLES = ("interactive_brain", "brain")


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
        "arguments off this PC." % (who, window))
