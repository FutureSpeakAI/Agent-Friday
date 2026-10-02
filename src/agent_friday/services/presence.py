"""Presence frames: what Friday is doing, for the holographic lattice to show.

docs/design/active/avatar-visual-genome.md §13. Every processing state the
lattice shows (a twist per tool call, a wave layer per round, a vent when a
call really leaves for the cloud...) is driven by one of these frames, sent
from the place in Friday's loop where the thing actually happens. Nothing
here runs on a timer, and nothing emits a frame "to look busy": a state whose
event does not exist yet simply has no emitter.

A frame is a state from a fixed list, a phase, and counts and opaque ids.
It never carries the user's words, a tool's name or arguments, a model's
output, a file path or a URL: every field is checked against a shape, and a
field that does not match is dropped rather than sent. So a frame is safe to
send while off the record, and it is never written to disk.

Frames ride the approvals stream every open page already shares
(services/approval_feed.py, /api/approvals/events, relayed across tabs by
fridayApprovalFeed in index.html), as {"type": "presence", ...}. They are
lossy: a page that falls behind loses presence frames, never approval cards.

Every frame says whose state it is, as `agent`. The label comes from the
context the code runs in (`acting_as`), never from the caller: Friday's own
turn runs under `FRIDAY`, a helper or a scheduled run under its own opaque
id (`helper_id`), which can never equal `FRIDAY`. Code that runs under no
agent sends no label, never Friday's; the avatar treats only
`agent == "friday"` as her own state. A new thread starts with no agent, so
a helper's thread never inherits Friday's label; work that is hers and is
handed to another thread carries it with `contextvars.copy_context`.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import logging
import re
import threading
import time

_log = logging.getLogger("friday.presence")

#: The states the lattice knows (§13.3). `listening` is set in the page from
#: the microphone and has no server frame; `approval` rides the card events.
STATES = frozenset({
    "retrieval", "round", "route", "egress", "tool", "reflex", "blocked",
    "verify", "progress", "memory_saved", "handoff", "error", "background",
    "subagent",
})
PHASES = frozenset({"start", "end", "step", "sent", "once"})
ROUTES = frozenset({"local", "cloud"})

#: Opaque ids only: a trace id, a call number, a hash. No spaces, slashes or
#: colons, so no path, sentence or URL fits.
_ID = re.compile(r"^[A-Za-z0-9_\-.]{1,64}$")
#: The orchestrator's own label. Only Friday's own turn runs under it.
FRIDAY = "friday"
_AGENT: contextvars.ContextVar = contextvars.ContextVar("friday_presence_agent",
                                                       default=None)
#: Counts are small non-negative integers.
_MAX_COUNT = 1_000_000

_LOCK = threading.Lock()
_OPEN_TOOLS: dict = {}          # (turn, name) -> [ref, ...] in call order
_SEQ = [0]
_LAST_PROGRESS: dict = {}       # ref -> last percent sent


def reset() -> None:
    """Forget open calls and progress (tests)."""
    with _LOCK:
        _OPEN_TOOLS.clear()
        _LAST_PROGRESS.clear()
        _SEQ[0] = 0


def _count(v):
    return (isinstance(v, int) and not isinstance(v, bool)
            and 0 <= v <= _MAX_COUNT)


def _frame(state, phase, fields) -> dict:
    f = {"type": "presence", "state": state, "phase": phase, "at": time.time()}
    for key in ("turn", "ref", "agent"):
        v = fields.get(key)
        if isinstance(v, str) and _ID.match(v):
            f[key] = v
    for key in ("n", "of"):
        if _count(fields.get(key)):
            f[key] = fields[key]
    if fields.get("route") in ROUTES:
        f["route"] = fields["route"]
    if isinstance(fields.get("ok"), bool):
        f["ok"] = fields["ok"]
    return f


def emit(state: str, phase: str, **fields) -> bool:
    """Send one presence frame to every open page. Returns whether it was
    sent. Never raises: a presence frame must never break the work it shows."""
    try:
        if state not in STATES or phase not in PHASES:
            return False
        if "agent" not in fields:
            fields["agent"] = _AGENT.get()
        from agent_friday.services import approval_feed
        approval_feed.publish(_frame(state, phase, fields), lossy=True)
        return True
    except Exception as e:
        _log.debug("presence frame not sent: %s", e)
        return False


@contextlib.contextmanager
def acting_as(agent):
    """Run a block as `agent`: every frame sent inside it, on this context,
    carries that label. A value that is not an opaque id runs the block
    under no agent."""
    token = _AGENT.set(agent if isinstance(agent, str) and _ID.match(agent) else None)
    try:
        yield
    finally:
        _AGENT.reset(token)


def current_agent():
    """The agent the calling code runs as, or None."""
    return _AGENT.get()


def helper_id(value) -> str:
    """A helper's own label: opaque, and never `FRIDAY`."""
    return "helper-" + opaque(value)


def opaque(value) -> str:
    """A stable id for something whose own name must not leave (a process
    id, a schedule id)."""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def current_turn():
    """The trace the caller runs under, as the frame's turn id, if any."""
    try:
        from agent_friday.services import reasoning_trace as _rt
        tid = _rt.current()
        return tid if isinstance(tid, str) and _ID.match(tid) else None
    except Exception:
        return None


# ── tool calls: one twist per call, paired start to end ──────────────────────

def tool_started(name, *, turn=None) -> None:
    turn = turn if turn is not None else current_turn()
    with _LOCK:
        _SEQ[0] += 1
        ref = "c%d" % _SEQ[0]
        _OPEN_TOOLS.setdefault((turn, str(name)), []).append(ref)
    emit("tool", "start", turn=turn, ref=ref)


def tool_finished(name, *, ok=True, turn=None) -> None:
    turn = turn if turn is not None else current_turn()
    with _LOCK:
        opened = _OPEN_TOOLS.get((turn, str(name))) or []
        ref = opened.pop(0) if opened else None
        if not opened:
            _OPEN_TOOLS.pop((turn, str(name)), None)
        if ref is None:
            _SEQ[0] += 1
            ref = "c%d" % _SEQ[0]
    emit("tool", "end", turn=turn, ref=ref, ok=bool(ok))


# ── true progress: sent only when the fraction moves a whole percent ─────────

def progress(process_id, fraction) -> None:
    try:
        pct = int(max(0.0, min(1.0, float(fraction))) * 100)
    except (TypeError, ValueError):
        return
    ref = opaque(process_id)
    with _LOCK:
        if _LAST_PROGRESS.get(ref) == pct:
            return
        _LAST_PROGRESS[ref] = pct
    emit("progress", "step", ref=ref, n=pct, of=100)


def progress_done(process_id) -> None:
    with _LOCK:
        _LAST_PROGRESS.pop(opaque(process_id), None)
