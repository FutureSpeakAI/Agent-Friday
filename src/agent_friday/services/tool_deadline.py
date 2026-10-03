"""A time budget a tool call carries, for tools that can stop early.

A local seat waits for every tool it calls, and on a 27B model the wait is on
top of tens of seconds of reasoning per round. So a tool called by a local seat
runs under a 3-second budget (`LOCAL_TOOL_BUDGET_S`). A tool that scans (files,
wiki pages) checks the budget as it goes and, when it runs out, returns what it
has found so far marked partial, with a note that says so, rather than making
the seat wait for a complete answer.

The budget is cooperative: it is a deadline a tool reads, never a timer that
interrupts one. A tool that does not read it runs exactly as before, so an
action is never cut off half-done and an approval card is never timed out.
"""
from __future__ import annotations

import contextlib
import contextvars
import time

#: Seconds a tool called by a local seat has before it returns what it has.
LOCAL_TOOL_BUDGET_S = 3.0

_DEADLINE: contextvars.ContextVar = contextvars.ContextVar(
    "friday_tool_deadline", default=None)


@contextlib.contextmanager
def budget(seconds: float):
    """Run the enclosed tool call under a deadline `seconds` from now."""
    token = _DEADLINE.set(time.monotonic() + float(seconds))
    try:
        yield
    finally:
        _DEADLINE.reset(token)


def deadline(own_budget_s: float) -> float:
    """The monotonic deadline a tool should stop at: its own budget, or the
    call's budget when that comes sooner."""
    own = time.monotonic() + float(own_budget_s)
    d = _DEADLINE.get()
    return min(own, d) if d is not None else own


def active() -> bool:
    """Whether the current call runs under a budget."""
    return _DEADLINE.get() is not None


def expired() -> bool:
    """True once the current call's budget has run out; False with none."""
    d = _DEADLINE.get()
    return d is not None and time.monotonic() >= d
