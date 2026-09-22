"""Red-first ground truth for Defect C: external task watchdog.

Contract under test: ``agent_friday.services.task_watchdog``.

The dispatcher's timeout checks are cooperative (they only run between agent
rounds), so a wedged inference call can block a worker thread forever while
the task record still says ``running``. Incident of record: a step with an
1800s budget ran 8,869 seconds with zero tool calls, pinning the GPU, and no
retry ever fired. The watchdog must assess task records from the OUTSIDE —
pure functions over dicts, no cooperation from the blocked thread required.

Required API (all pure, importable without side effects):

    assess(task: dict, *, now: float) -> str
        Returns one of the verdicts: "ok" | "overdue" | "stuck" | "kill".

    GRACE_MULTIPLIER: float   # hard cap on budget drift, must be <= 1.5
    STALL_SECONDS: float      # no-tool-call stall threshold, default 300

Task-record fields used (all present in ~/.friday forensics records):
    started_at: float (epoch seconds)
    timeout_seconds: float
    tool_call_count: int
    last_tool_call_at: float | None (epoch seconds)
    stop_requested: bool
    status: str ("running" | "completed" | "failed" | ...)
"""

import pytest

from agent_friday.services import task_watchdog
from agent_friday.services.task_watchdog import assess


def _task(
    *,
    started_at=1000.0,
    timeout_seconds=1800.0,
    tool_call_count=0,
    last_tool_call_at=None,
    stop_requested=False,
    status="running",
):
    return {
        "started_at": started_at,
        "timeout_seconds": timeout_seconds,
        "tool_call_count": tool_call_count,
        "last_tool_call_at": last_tool_call_at,
        "stop_requested": stop_requested,
        "status": status,
    }


# ---------------------------------------------------------------- verdicts


def test_healthy_running_task_is_ok():
    """A task inside budget with recent tool activity is healthy."""
    task = _task(tool_call_count=5, last_tool_call_at=1550.0)
    assert assess(task, now=1600.0) == "ok"


def test_task_past_budget_is_overdue():
    """Elapsed past timeout_seconds but under the grace cap: overdue."""
    task = _task(tool_call_count=20, last_tool_call_at=2790.0)
    # elapsed = 1900s against an 1800s budget -> past budget, inside grace
    assert assess(task, now=2900.0) == "overdue"


def test_task_past_grace_cap_is_kill():
    """Elapsed beyond GRACE_MULTIPLIER * budget must rule kill."""
    task = _task(tool_call_count=20, last_tool_call_at=3690.0)
    # elapsed = 2800s > 1.5 * 1800s (2700s)
    assert assess(task, now=3800.0) == "kill"


def test_running_with_zero_tool_calls_past_stall_is_stuck():
    """The wedge signature: running, no tool calls, past stall threshold."""
    task = _task(tool_call_count=0, last_tool_call_at=None)
    # 400s elapsed, zero tool calls, stall threshold 300s
    assert assess(task, now=1400.0) == "stuck"


def test_running_with_zero_tool_calls_within_stall_window_is_ok():
    """A fresh task that simply hasn't called a tool yet is not stuck."""
    task = _task(tool_call_count=0, last_tool_call_at=None)
    # only 100s elapsed
    assert assess(task, now=1100.0) == "ok"


def test_stalled_mid_run_tool_silence_is_stuck():
    """Tool calls happened, then went silent past the stall threshold."""
    task = _task(tool_call_count=12, last_tool_call_at=1200.0)
    # 500s of tool silence, still inside overall budget
    assert assess(task, now=1700.0) == "stuck"


def test_stop_requested_is_kill_regardless_of_elapsed():
    """stop_requested must mean something: instant kill verdict."""
    task = _task(tool_call_count=3, last_tool_call_at=1050.0, stop_requested=True)
    # only 100s elapsed, healthy activity — stop still wins
    assert assess(task, now=1100.0) == "kill"


def test_completed_task_is_ok_never_flagged():
    """Terminal tasks are not the watchdog's business."""
    task = _task(status="completed", tool_call_count=9, last_tool_call_at=1500.0)
    # elapsed wildly past budget: irrelevant once terminal
    assert assess(task, now=99999.0) == "ok"


# ---------------------------------------------------- incident regression


def test_incident_8869s_zero_tool_calls_rules_kill():
    """Regression: the 2026-09-21 wedge. 8,869s elapsed against an 1800s
    budget with zero tool calls pinned the GPU until the OS choked, and the
    dispatcher ruled nothing. The watchdog must rule kill."""
    task = _task(
        started_at=0.0,
        timeout_seconds=1800.0,
        tool_call_count=0,
        last_tool_call_at=None,
    )
    assert assess(task, now=8869.0) == "kill"


# ------------------------------------------------------------- constants


def test_grace_multiplier_capped():
    """Budget drift of 4.9x was observed in production. The cap must make
    that structurally impossible to bless."""
    assert task_watchdog.GRACE_MULTIPLIER <= 1.5


def test_stall_seconds_default():
    """Stall threshold: 300s with no tool calls is the stuck signature."""
    assert task_watchdog.STALL_SECONDS == pytest.approx(300.0)
