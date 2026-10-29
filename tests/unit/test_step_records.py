"""Step records (north star §30.9, NS-30.9-2; BD-12 slice G8).

A step record holds kind, state, input and output refs (ids only, never
content), a distinct_progress_key and timing. The key is the same for a step
that made the same progress again and new for one that made new progress,
and the goal engine's no-progress breaker (§17.10) stops a run whose steps
keep finding nothing new.

Every fixture is synthetic; the tool calls go through the real journal hook
(agent._orb_tool_trace) with tool execution faked, and the milestone executor
and its check are fakes, so nothing reaches a model.
"""
from __future__ import annotations

import json
import time

import pytest
from flask import Flask, g

from agent_friday.services import agent as ag
from agent_friday.services import goal_state
from agent_friday.services import goals
from agent_friday.services import step_records as sr
from agent_friday.services import task_journal as tj

TID = "steps-task-0001"
MARK = "SYNTHETIC-STEP-CONTENT-MARKER"
PAST = "2020-01-01T00:00:00Z"


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    from agent_friday.services import dissent_gate as dg
    from agent_friday.services import artifact_manifest as am
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    monkeypatch.setattr(tj, "settings", lambda: {"retention_days": 0, "capture_reasoning": True,
                                                 "encrypt_at_rest": False})
    tj.reset_for_tests()
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: kw)
    # The artifact manifest's background recorder is not under test here.
    monkeypatch.setattr(am, "submit", lambda *a, **k: False)
    monkeypatch.setattr(ag._rtrace, "tool_finished", lambda *a, **k: None)
    monkeypatch.setattr(goals, "GOALS_DIR", tmp_path / "goals")
    monkeypatch.setattr(goals, "REVIEWS_DIR", tmp_path / "goals" / "reviews")
    monkeypatch.setattr(goals, "RECEIPTS_LOG", tmp_path / "governance" / "goal_receipts.jsonl")
    monkeypatch.setattr(goals.approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    yield
    goals.set_executor(None)


def _call(name, args, result, *, duration_ms=40, tid=TID):
    """One executed tool call through the single hook every call passes."""
    tj.push_task(tid)
    try:
        ag._orb_tool_trace(None, name, args, result, duration_ms)
    finally:
        tj.pop_task()


def _raw_steps(tid=TID):
    return tj.read(tid, kind="step")


# ── a tool call creates a step record ─────────────────────────────────────

def test_a_tool_call_creates_a_step_record_with_every_field_and_no_content():
    _call("search_web", {"query": MARK + " about the lunch plan"}, MARK + ": three results found")
    steps = tj.steps(TID)
    assert len(steps) == 1, "one tool call is one step"
    s = steps[0]
    assert set(s) >= {"id", "task_id", "kind", "state", "input_refs", "output_refs",
                      "distinct_progress_key", "started_at", "ended_at", "duration_ms"}
    assert s["task_id"] == TID and s["id"].startswith("stp_")
    assert s["kind"] == "tool_call" and s["state"] == "done" and s["name"] == "search_web"
    assert s["distinct_progress_key"].startswith("dpk_")
    assert s["ended_at"] >= s["started_at"] and s["duration_ms"] == 40
    assert any(r.startswith("args:") for r in s["input_refs"])
    assert any(r.startswith("result:") for r in s["output_refs"])
    assert all(sr.valid_ref(r) for r in s["input_refs"] + s["output_refs"])
    # The tool_call event carries the (tier-safe) summary; the step carries none of it.
    raw = _raw_steps()
    assert len(raw) == 1 and MARK not in json.dumps(raw[0])
    assert "lunch" not in json.dumps(raw[0]) and "three results" not in json.dumps(raw[0])


def test_a_ref_that_is_not_an_id_is_never_stored():
    rec = tj.step("tool_call", "done", name="search_web", task_id=TID,
                  output_refs=["receipt:rcpt_one", "result:" + MARK + " with spaces in it",
                               "message:" + "x" * 300, "free text " + MARK])
    assert rec["output_refs"] == ["receipt:rcpt_one"]
    assert MARK not in json.dumps(_raw_steps())


def test_a_failed_call_is_a_failed_step():
    _call("search_web", {"query": "q"}, "Tool error (search_web): the service did not answer")
    assert tj.steps(TID)[0]["state"] == "failed"


# ── distinct_progress_key ─────────────────────────────────────────────────

def test_the_same_tool_with_the_same_outcome_gives_the_same_key():
    _call("read_file", {"path": "notes-a.md"}, "line one\nline two")
    _call("read_file", {"path": "./notes-a.md"}, "line one\nline two", duration_ms=900)
    a, b = tj.steps(TID)
    assert a["id"] != b["id"], "two steps, two records"
    assert a["distinct_progress_key"] == b["distinct_progress_key"]


def test_a_different_outcome_gives_a_new_key():
    _call("read_file", {"path": "notes-a.md"}, "line one")
    _call("read_file", {"path": "notes-a.md"}, "line one\nline two added since")
    a, b = tj.steps(TID)
    assert a["distinct_progress_key"] != b["distinct_progress_key"]


def test_a_new_artifact_gives_a_new_key(tmp_path):
    out = tmp_path / "draft.md"
    out.write_text("alpha-draft1", encoding="utf-8")
    result = f"Wrote 12 chars to {out}"
    _call("write_file", {"path": str(out), "content": "alpha-draft1"}, result)
    out.write_text("bravo-draft2", encoding="utf-8")     # the same length: the same result text
    _call("write_file", {"path": str(out), "content": "bravo-draft2"}, result)
    out.write_text("bravo-draft2", encoding="utf-8")
    _call("write_file", {"path": str(out), "content": "bravo-draft2"}, result)
    a, b, c = tj.steps(TID)
    assert any(r.startswith("artifact:sha256-") for r in a["output_refs"])
    assert a["distinct_progress_key"] != b["distinct_progress_key"], "new file content is new progress"
    assert b["distinct_progress_key"] == c["distinct_progress_key"], "the same file again is not"
    assert "alpha" not in json.dumps(_raw_steps()) and "bravo" not in json.dumps(_raw_steps())


def test_a_new_receipt_gives_a_new_key_and_a_new_action_id_does_not():
    def one(**refs):
        return tj.step("tool_call", "done", name="send_email", task_id=TID, **refs)
    a = one(output_refs=["result:abc123", "receipt:rcpt_first"], input_refs=["action:act_one"])
    b = one(output_refs=["result:abc123", "receipt:rcpt_second"], input_refs=["action:act_one"])
    c = one(output_refs=["result:abc123", "receipt:rcpt_second", "action:act_three"],
            input_refs=["action:act_two"])
    assert a["distinct_progress_key"] != b["distinct_progress_key"]
    assert b["distinct_progress_key"] == c["distinct_progress_key"], \
        "an action id changes on every repeat and is not an outcome"


def test_timestamps_do_not_affect_the_key():
    _call("check_inbox", {}, "Checked at 2026-10-29T10:41:07Z: 3 unread, newest 10:39", duration_ms=10)
    time.sleep(0.01)
    _call("check_inbox", {}, "Checked at 2026-10-29T10:41:59Z: 3 unread, newest 10:40", duration_ms=700)
    a, b = tj.steps(TID)
    assert a["started_at"] != b["started_at"] and a["duration_ms"] != b["duration_ms"]
    assert a["distinct_progress_key"] == b["distinct_progress_key"]
    # Recomputed from the same fields at another time: the same key.
    assert sr.progress_key("tool_call", "check_inbox", "done", a["output_refs"]) == a["distinct_progress_key"]


# ── no progress (§17.10) on the keys ──────────────────────────────────────

def test_repeats_with_no_new_key_are_no_progress_and_varying_keys_are_not():
    for _ in range(goal_state.NO_NEW_PROGRESS_LIMIT + 1):
        _call("search_web", {"query": "same"}, "no results")
    assert goal_state.stalled_steps(tj.steps(TID)), "repeating the same call is no progress"
    other = "steps-task-0002"
    for i in range(goal_state.NO_NEW_PROGRESS_LIMIT + 3):
        _call("search_web", {"query": "same"}, f"result page {i}", tid=other)
    assert goal_state.stalled_steps(tj.steps(other)) is None


def test_a_cycle_that_comes_round_again_is_no_progress():
    for _ in range(3):
        _call("read_file", {"path": "a.md"}, "A")
        _call("read_file", {"path": "b.md"}, "B")
    assert goal_state.stalled_steps(tj.steps(TID))


def _verdicts(monkeypatch):
    n = {"i": 0}

    def fake(content, intent, **kw):
        n["i"] += 1
        return {"status": "ok", "passed": False, "score": 0.3, "threshold": 0.7,
                "critique": f"synthetic critique {n['i']}", "suggestions": ""}
    monkeypatch.setattr(goals.qa_gates, "evaluate_text", fake)


class _Work:
    """Each attempt calls the same tool three times; its result and its words vary as asked."""

    def __init__(self, result):
        self.calls = 0
        self.result = result
        self.task_ids = []

    def __call__(self, goal, milestone, *, critique_hint=""):
        self.calls += 1
        self.task_ids.append(milestone["_task_id"])
        for i in range(3):
            tj.tool_call(name="search_web", args={"q": "synthetic"}, result=self.result(self.calls, i),
                         ok=True, duration_ms=5, task_id=milestone["_task_id"])
        return {"ok": True, "text": f"synthetic draft {self.calls}", "cost_mψ": 10}


def _goal():
    g_ = goals.create_goal(title="Synthetic goal", status="active", success_criteria="a synthetic draft exists",
                           milestones=[{"name": "Draft", "due": PAST}])
    return g_["goal_id"], g_["milestones"][0]["milestone_id"]


def test_a_milestone_whose_steps_find_nothing_new_stops_with_the_no_progress_blocker(monkeypatch):
    _verdicts(monkeypatch)
    work = _Work(lambda attempt, i: "the same synthetic results")
    goals.set_executor(work)
    gid, mid = _goal()
    out = goals.run_milestone(gid, mid)
    # The words and the critiques differ every time (the attempt-level check
    # cannot see it); the steps show the same calls coming back the same.
    assert out["status"] == "no_progress", out
    assert work.calls == 2, "a third identical attempt was dispatched"
    m = goals.get_goal(gid)["milestones"][0]
    assert m["status"] == "blocked" and m["blocker"]["category"] == "no_progress"
    assert "found nothing new" in m["blocker"]["text"]


def test_a_milestone_whose_steps_keep_finding_new_things_is_not_no_progress(monkeypatch):
    _verdicts(monkeypatch)
    work = _Work(lambda attempt, i: f"synthetic results {attempt}-{i}")
    goals.set_executor(work)
    gid, mid = _goal()
    out = goals.run_milestone(gid, mid)
    assert out["status"] != "no_progress", out
    assert work.calls == goals.DEFAULT_MAX_REPAIR_RETRIES + 1
    # Every attempt's steps were recorded, each with a key no earlier step had.
    keys = [s["distinct_progress_key"] for t in work.task_ids for s in tj.steps(t)]
    assert len(keys) == work.calls * 4, "three tool steps and the attempt itself, per attempt"
    assert len(set(keys)) == len(keys)


def test_each_attempt_is_a_milestone_run_step_in_its_task():
    seen = []
    goals.set_executor(lambda goal, milestone, critique_hint="": (
        seen.append(milestone["_task_id"]) or
        {"ok": False, "text": "", "cost_mψ": 0, "error": "synthetic failure"}))
    gid, mid = _goal()
    goals.run_milestone(gid, mid)
    assert seen
    runs = [s for s in tj.steps(seen[0]) if s["kind"] == "milestone_run"]
    assert len(runs) == 1 and runs[0]["state"] == "failed" and runs[0]["name"] == mid
    assert f"milestone:{mid}" in runs[0]["input_refs"]
    assert "synthetic failure" not in json.dumps(_raw_steps(seen[0]))


# ── off the record ────────────────────────────────────────────────────────

def test_off_the_record_no_step_is_written(monkeypatch):
    from agent_friday.services import off_record
    monkeypatch.setattr(off_record, "skip", lambda *a, **k: True)
    tid = "steps-offrecord-0001"          # its own id: off-record holds for a task's whole life
    _call("search_web", {"query": MARK}, MARK, tid=tid)
    assert tj.steps(tid) == [] and not (tj.tasks_dir() / tid).exists()


# ── the route: read-only, owner only ──────────────────────────────────────

def _client(who):
    from agent_friday.routes.tasks import tasks_bp
    app = Flask("steps-route")

    @app.before_request
    def _who():
        g.friday_principal = who[0]
    app.register_blueprint(tasks_bp)
    return app.test_client()


def test_the_route_serves_steps_to_the_owner_only():
    for _ in range(3):
        _call("search_web", {"query": MARK}, MARK + " no results")
    who = ["user"]
    c = _client(who)
    r = c.get(f"/api/tasks/{TID}/steps")
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["count"] == 3 and len(body["steps"]) == 3
    assert body["steps"][0]["kind"] == "tool_call" and body["steps"][0]["distinct_progress_key"]
    assert body["progress"]["limit"] == goal_state.NO_NEW_PROGRESS_LIMIT
    assert MARK not in r.get_data(as_text=True)
    assert c.get(f"/api/tasks/{TID}/steps?limit=1").get_json()["truncated"] is True
    assert c.get("/api/tasks/no-such-task/steps").status_code == 404
    who[0] = "observer"
    r = c.get(f"/api/tasks/{TID}/steps")
    assert r.status_code == 403 and "steps" not in (r.get_json() or {})
    assert c.post(f"/api/tasks/{TID}/steps").status_code == 405, "read-only"
