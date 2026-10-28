"""LoopGuard lets a status, poll or wait call through when its result changes.

Owner ruling, decision 19 (2026-10-28, via Dispatch): "LoopGuard lets a repeated
call through when its result changes, but only for status, poll and wait tools."
Everything else is judged exactly as before, and a stuck loop must be caught at
least as often as before.

The bench cases are the 32 hand-written adversarial traces from the decision
models bench (ftv/decision_eval/kit/fixtures/e2_adversarial.json, copied to
tests/fixtures/loopguard_e2_adversarial.json): 16 productive, 16 stuck, each
labelled with the step where it became stuck. They are replayed the way the
agent loops drive the guard: observe() before a call, the call's row added to
the turn's trace after it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_friday.services import turn_budget as tb

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "loopguard_e2_adversarial.json"

#: What the shipped guard did on the bench before the ruling (2026-10-28 run,
#: ftv/decision_eval/results/2026-10-28_0151): 4 of 16 stuck traces caught, one
#: stopped before it was stuck, 7 of 16 productive traces stopped.
CAUGHT_BEFORE = {"adv-17", "adv-18", "adv-25", "adv-26"}
FALSE_STOPS_BEFORE = {"adv-01", "adv-02", "adv-03", "adv-07", "adv-08", "adv-12", "adv-13"}


def _guard(trace):
    return tb.LoopGuard(trace=trace)


def _run(calls, trace=None, with_trace=True):
    """calls: (tool, args, result) in order. The index of the call the guard
    stops, or None."""
    trace = [] if trace is None else trace
    g = tb.LoopGuard(trace=trace) if with_trace else tb.LoopGuard()
    for i, (tool, args, result) in enumerate(calls):
        if g.observe(tool, args):
            return i
        trace.append({"name": tool, "input": args, "result": result})
    return None


def _replay():
    traces = json.loads(FIXTURE.read_text("utf-8"))
    out = {}
    for t in traces:
        calls = [(s["tool"], s["args"],
                  s.get("result") if s.get("ok", True) else "Error: %s" % s.get("error"))
                 for s in t["steps"]]
        out[t["id"]] = (t, _run(calls))
    return out


# -- which tools the ruling covers ---------------------------------------------

@pytest.mark.parametrize("name", ["check_job_status", "work_order_status", "workflow_status",
                                  "queue_status", "pollExport", "wait_for_upload", "job.status"])
def test_a_status_poll_or_wait_tool_is_recognised(name):
    assert tb.is_status_tool(name) is True


@pytest.mark.parametrize("name", ["search_email", "calendar_events", "read_file", "fetch_page",
                                  "send_draft", "check_plan_travel", "statusbar_theme_x", ""])
def test_any_other_tool_is_not(name):
    assert tb.is_status_tool(name) is False


# -- the ruling -----------------------------------------------------------------

def test_a_status_call_whose_result_keeps_changing_is_let_through():
    args = {"job_id": "bk-1"}
    calls = [("check_job_status", args, "running, %d%%" % p) for p in (10, 20, 30, 40, 50, 60)]
    calls.append(("check_job_status", args, "done"))
    assert _run(calls) is None


def test_the_same_status_result_three_times_is_still_a_loop():
    args = {"job_id": "j"}
    calls = [("check_job_status", args, "pending.")] * 5
    stop = _run(calls)
    assert stop == 2                     # the third identical call, as before the ruling


def test_a_heartbeat_age_or_a_timestamp_is_not_a_change():
    args = {"job_id": "bk-2291"}
    calls = [("check_job_status", args,
              "running, 37%% (25.9 of 70 GB), last heartbeat %s ago, checked %s, request id: %s."
              % (age, when, rid)) for age, when, rid in (
        ("4s", "2026-10-28T09:10:02Z", "7f3a9c"),
        ("2m", "2026-10-28T09:12:10Z", "81bd02"),
        ("4m", "2026-10-28T09:14:02Z", "c0ffee"),
        ("6m", "2026-10-28T09:16:40Z", "9a9a01"))]
    assert _run(calls) == 2


def test_a_job_flapping_between_two_states_is_still_a_loop():
    args = {"job_id": "j"}
    calls = [("check_job_status", args, r) for r in ("queued", "running") * 4]
    assert _run(calls) == 3              # queued, running, queued: nothing new twice


@pytest.mark.parametrize("make", [
    lambda i: json.dumps({"state": "running", "request_id": "r%d" % i}),
    lambda i: "running, elapsed %ds" % (30 * i),
    lambda i: "running, checked at epoch %d" % (1761300000 + i),
    lambda i: {"state": "running", "uptime": 100 + i, "attempt": i},
])
def test_counters_and_ids_that_only_tick_are_not_a_change(make):
    args = {"job_id": "j"}
    assert _run([("check_job_status", args, make(i)) for i in range(5)]) == 2


def test_a_result_long_enough_to_have_been_clipped_is_judged_as_before():
    args = {"job_id": "j"}
    calls = [("check_job_status", args, "x" * 1995 + str(i)) for i in range(5)]
    assert _run(calls) == 2


def test_a_cycle_through_a_status_poll_and_another_tool_is_caught_as_before():
    calls = []
    for i in range(10):
        calls.append(("workflow_status", {"id": "w"}, "step %d" % i))
        calls.append(("search_web", {"q": "q%d" % (i % 2)}, "the same three links"))
    # The search alternates between two queries: the cycle check catches the
    # pattern on its fourth pass, judged on the calls alone, as before the ruling.
    assert _run(calls) == 7


@pytest.mark.parametrize("name", ["set_status", "update_status", "post_status_update",
                                  "wait_then_send", "create_commit_status"])
def test_a_tool_that_changes_something_is_not_a_status_tool(name):
    assert tb.is_status_tool(name) is False


def test_a_changed_result_starts_the_count_again():
    args = {"job_id": "j"}
    calls = [("queue_status", args, r) for r in ("3 ahead", "2 ahead", "2 ahead", "1 ahead")]
    # Counted across the turn, as before the ruling, the third call would stop.
    # Counted since the result last changed, it is the fourth: two "2 ahead" in a row.
    assert _run(calls) == 3


def test_only_status_tools_are_loosened():
    args = {"query": "from:petra signed contract"}
    calls = [("search_email", args, "%d messages" % n) for n in (1, 2, 3, 4)]
    assert _run(calls) == 2              # a search repeated is judged as before


def test_without_the_turns_trace_the_guard_is_as_before():
    args = {"job_id": "j"}
    calls = [("check_job_status", args, "%d%%" % p) for p in (10, 20, 30, 40)]
    assert _run(calls, with_trace=False) == 2


def test_rows_the_guard_did_not_see_make_it_strict_again():
    # A resumed run carries rows from before; the counts no longer line up, so
    # the call is judged as before rather than trusted.
    args = {"job_id": "j"}
    resumed = [{"name": "check_job_status", "input": args, "result": "5%"}]
    calls = [("check_job_status", args, "%d%%" % p) for p in (10, 20, 30, 40)]
    assert _run(calls, trace=resumed) == 2


def test_a_read_repeated_beside_a_status_poll_is_still_judged_on_its_own():
    calls = []
    for p in range(8):
        calls.append(("workflow_status", {"id": "w"}, "step %d of 9" % p))
        calls.append(("read_file", {"path": "log.txt"}, "line %d" % p))
    # read_file repeats with the same arguments, so the identical-call rule
    # still applies to it on its own third call.
    assert _run(calls) == 5


# -- the bench -------------------------------------------------------------------

def test_no_stuck_trace_caught_before_is_missed_now():
    got = _replay()
    caught = {k for k, (t, stop) in got.items()
              if t["stalled"] and stop is not None and stop >= t["onset"]}
    assert CAUGHT_BEFORE <= caught
    assert len(caught) >= len(CAUGHT_BEFORE)


def test_the_hung_job_is_now_caught_after_it_hangs_not_before():
    t, stop = _replay()["adv-32"]
    # Before the ruling the guard stopped this job while it was still moving
    # (12%, 25%, 37%), one step before it hung.
    assert stop is not None and stop >= t["onset"]


def test_no_productive_trace_is_newly_stopped():
    got = _replay()
    stopped = {k for k, (t, stop) in got.items() if not t["stalled"] and stop is not None}
    assert stopped <= FALSE_STOPS_BEFORE


@pytest.mark.parametrize("tid,why", [
    ("adv-01", "a status tool returning 'pending' four times: an unchanged result"),
    ("adv-02", "a status tool returning 'still running' six times: an unchanged result"),
    ("adv-03", "a file re-read before a write: not a status tool"),
    ("adv-07", "a retried call failing with the same 503: not a status tool"),
    ("adv-08", "a write retried after the same SMTP 421: not a status tool"),
    ("adv-12", "an inbox search repeated while waiting: not a status tool by name"),
    ("adv-13", "a calendar query repeated while waiting: not a status tool by name"),
])
def test_the_seven_bench_false_stops_are_outside_the_ruling(tid, why):
    """The ruling covers a status call whose result CHANGES. None of the seven
    productive traces the guard stopped is that case, so each is still stopped,
    and the owner has been told (DECISIONS-FOR-STEPHEN, LoopGuard ruling). If a
    later ruling covers them, this test changes with it."""
    t, stop = _replay()[tid]
    assert not t["stalled"]
    assert stop is not None, why


def test_both_agent_loops_give_the_guard_the_turns_trace():
    src = Path(tb.__file__).with_name("agent.py").read_text(encoding="utf-8")
    assert src.count("_tb.LoopGuard(trace=tool_trace)") == 2
    assert "_tb.LoopGuard()" not in src
