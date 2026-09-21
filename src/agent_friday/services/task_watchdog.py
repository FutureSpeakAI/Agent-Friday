"""External task watchdog for Defect C (the dispatcher's cooperative timeouts).

The dispatcher's timeout checks only run *between* agent rounds, so a wedged
inference call can pin a worker thread — and the GPU — forever while the task
record still reads ``running``. Incident of record: a step with an 1800s budget
ran 8,869 seconds with zero tool calls and no retry ever fired.

This module rules from the OUTSIDE: pure functions over task-record dicts, with
no cooperation required from the (possibly blocked) thread.

Required API (all pure, importable without side effects):

    assess(task: dict, *, now: float) -> str
        Returns exactly one of: "ok" | "overdue" | "stuck" | "kill".

    GRACE_MULTIPLIER: float   # hard cap on budget drift; must be <= 1.5
    STALL_SECONDS: float      # no-tool-call stall threshold; 300.0
"""

# Hard cap on how far a task may drift past its budget before we force-kill.
# Production observed 4.9x drift; the cap makes that structurally un-blessable.
GRACE_MULTIPLIER: float = 1.5

# Time (in seconds) with no tool call that is the "stuck" signature.
STALL_SECONDS: float = 300.0

# Statuses the watchdog does not touch. Once a task is terminal, its record is
# not a wedge — flagging it would be false signal.
TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed", "cancelled"})


def assess(task: dict, *, now: float) -> str:
    """Assess a single task record and return a watchdog verdict.

    Args:
        task: A task-record dict (the shape found in ``~/.friday`` forensics).
            Recognised fields (all present in real records):
                started_at (float, epoch s), timeout_seconds (float),
                tool_call_count (int), last_tool_call_at (float | None),
                stop_requested (bool), status (str).
        now: The assessment clock, in epoch seconds. Keyword-only so callers
            can inject a deterministic value (as every test does).

    Returns:
        "ok"      — healthy, or terminal (exempt).
        "overdue" — past ``timeout_seconds`` but within the grace cap.
        "stuck"   — no tool call for >= STALL_SECONDS (the wedge signature).
        "kill"    — stop requested, or elapsed past budget * GRACE_MULTIPLIER.
    """
    status = str(task.get("status", "running")).lower()

    # Terminal tasks are never the watchdog's business.
    if status in TERMINAL_STATUSES:
        return "ok"

    # stop_requested means something: an instant kill, regardless of elapsed.
    if bool(task.get("stop_requested", False)):
        return "kill"

    started_at = float(task.get("started_at", 0.0))
    budget = float(task.get("timeout_seconds", 0.0))
    elapsed = now - started_at

    # Past the grace cap on the budget -> kill. (The incident: 8869s vs 1800s.)
    if elapsed > budget * GRACE_MULTIPLIER:
        return "kill"

    # Stuck: the thread has been silent on tool calls for a stall window.
    # Silence is measured from the last tool call. When there have been no
    # tool calls at all, the silence clock runs from started_at — a fresh
    # task that simply hasn't called a tool yet is not stuck until the stall
    # window has elapsed since start.
    last = task.get("last_tool_call_at")
    if last is None:
        silence = now - started_at
    else:
        silence = now - float(last)
    if silence >= STALL_SECONDS:
        return "stuck"

    # Past budget but still inside the grace cap -> overdue.
    if elapsed > budget:
        return "overdue"

    return "ok"
