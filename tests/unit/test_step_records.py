"""Step records (north star §30.9, NS-30.9-2; BD-12 slice G8).

A step record holds kind, state, input and output refs (ids only, never
content: keyed fingerprints), a distinct_progress_key and timing. The key is
the same for a step that made the same progress again and new for one that
made new progress. Across a goal's attempts only the attempt-level key can
stop the run (§17.10): steps inside an attempt are reported, never a reason
to stop.

Every fixture is synthetic; the tool calls go through the real journal hook
(agent._orb_tool_trace) with tool execution faked, and the milestone executor
and its check are fakes, so nothing reaches a model. The governance key is
derived from a fixed phrase at run time, never written out.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

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
GOV = hashlib.sha256(b"synthetic step-records fixture").digest()
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    from agent_friday.services import dissent_gate as dg
    from agent_friday.services import artifact_manifest as am
    from agent_friday.governance import proof_of_integrity as poi
    monkeypatch.setattr(poi, "get_governance_key", lambda: GOV)
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

def test_the_same_call_with_the_same_outcome_gives_the_same_key_and_other_arguments_do_not():
    _call("read_file", {"path": "notes-a.md"}, "line one\nline two")
    _call("read_file", {"path": "notes-a.md"}, "line one\nline two", duration_ms=900)
    _call("read_file", {"path": "./notes-a.md"}, "line one\nline two")
    a, b, c = tj.steps(TID)
    assert a["id"] != b["id"], "two steps, two records"
    assert a["distinct_progress_key"] == b["distinct_progress_key"]
    # The arguments' fingerprint is part of the key: asked differently is another step.
    assert c["distinct_progress_key"] != a["distinct_progress_key"]


def test_three_different_actions_with_the_same_short_answer_are_three_keys():
    for q in ("synthetic query one", "synthetic query two", "synthetic query three"):
        _call("search_web", {"query": q}, "No results")
    keys = [s["distinct_progress_key"] for s in tj.steps(TID)]
    assert len(set(keys)) == 3, "different searches that each found nothing are still different steps"
    assert goal_state.stalled_steps(tj.steps(TID)) is None


def test_fingerprints_are_keyed_and_absent_without_a_key(monkeypatch):
    from agent_friday.governance import proof_of_integrity as poi
    plain = hashlib.sha256(sr.normalize_outcome("line one").encode()).hexdigest()[:16]
    ref = sr.result_ref("line one")
    assert ref and ref != "result:" + plain, "a plain hash of the value is never stored"

    def no_key():
        raise RuntimeError("synthetic: no governance key")
    monkeypatch.setattr(poi, "get_governance_key", no_key)
    assert sr.fingerprint("line one") is None and sr.result_ref("line one") is None
    _call("search_web", {"query": "q"}, "line one")
    s = tj.steps(TID)[0]
    assert s["kind"] == "tool_call" and s["distinct_progress_key"], "the step is still recorded"
    assert not any(r.startswith(("args:", "result:")) for r in s["input_refs"] + s["output_refs"])


def test_a_huge_result_is_fingerprinted_from_its_head_and_its_length():
    n = sr.RESULT_FP_MAX_CHARS
    capped = sr.capped_outcome("a" * (n * 4))
    assert len(capped) < n + 64 and str(n * 4) in capped
    assert sr.result_ref("a" * (n + 10)) != sr.result_ref("a" * (n + 20)), "the length still counts"
    assert sr.result_ref("a" * n + "x" * 10) == sr.result_ref("a" * n + "y" * 10), \
        "past the first 256 KB only the length is read"


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
    assert any(r.startswith("artifact:hmac-") for r in a["output_refs"])
    assert a["distinct_progress_key"] != b["distinct_progress_key"], "new file content is new progress"
    assert b["distinct_progress_key"] == c["distinct_progress_key"], "the same file again is not"
    assert "alpha" not in json.dumps(_raw_steps()) and "bravo" not in json.dumps(_raw_steps())
    sha = hashlib.sha256(b"bravo-draft2").hexdigest()
    assert sha[:16] not in json.dumps(_raw_steps()), "the file's plain hash is not stored either"


def test_artifact_refs_read_nothing_off_the_record(tmp_path, monkeypatch):
    from agent_friday.services import artifact_manifest as am
    from agent_friday.services import off_record
    out = tmp_path / "private.md"
    out.write_text("synthetic private draft", encoding="utf-8")
    monkeypatch.setattr(off_record, "active", lambda *a, **k: True)

    def never(*a, **k):
        raise AssertionError("a file was looked at off the record")
    monkeypatch.setattr(am, "written_paths", never)
    monkeypatch.setattr(am, "file_sha256", never)
    assert sr.artifact_refs("write_file", {"path": str(out)}, f"Wrote 23 chars to {out}", task_id=TID) == []


def test_the_manifest_reuses_the_hash_the_step_took(tmp_path, monkeypatch):
    import agent_friday.paths as paths
    from agent_friday.governance import action_gate as gate
    from agent_friday.services import artifact_manifest as am
    from agent_friday.services import off_record
    monkeypatch.setattr(paths, "friday_home", lambda: tmp_path)
    monkeypatch.setattr(gate, "friday_home", lambda: tmp_path)
    monkeypatch.setattr(off_record, "active", lambda *a, **k: False)
    out = tmp_path / "shared.md"
    out.write_text("synthetic shared draft", encoding="utf-8")
    args, result = {"path": str(out)}, f"Wrote 22 chars to {out}"
    taken = {}
    refs = sr.artifact_refs("write_file", args, result, task_id=TID, hashes=taken)
    want = hashlib.sha256(b"synthetic shared draft").hexdigest()
    assert len(refs) == 1 and refs[0].startswith("artifact:hmac-")
    assert taken == {str(out.resolve()): want}

    def hashed_again(*a, **k):
        raise AssertionError("the same file was hashed twice for one call")
    monkeypatch.setattr(am, "file_sha256", hashed_again)
    lines = am.record_call("write_file", args, result, task_id=TID, turn_id=None,
                           started_at=time.time() - 5, known_sha256=taken,
                           where={"manifest": tmp_path / "manifest.jsonl", "key": am._key(), "gov_key": GOV})
    assert len(lines) == 1 and lines[0]["sha256"] == want


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
    assert sr.progress_key("tool_call", "check_inbox", "done", a["output_refs"],
                           a["input_refs"]) == a["distinct_progress_key"]


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


def _as_task(task_id):
    """The attempt's task exists, as a real dispatch makes it (a state record)."""
    tj.write_state(task_id, {"task_id": task_id, "name": "synthetic attempt", "status": "running",
                             "created": time.time()})


class _Work:
    """Each attempt reads the same files (the same calls, coming back the
    same); its result's words vary as asked."""

    def __init__(self, result, text=None, ok=True):
        self.calls = 0
        self.result = result
        self.text = text or (lambda attempt: f"synthetic draft {attempt}")
        self.ok = ok
        self.task_ids = []

    def __call__(self, goal, milestone, *, critique_hint=""):
        self.calls += 1
        tid = milestone["_task_id"]
        self.task_ids.append(tid)
        _as_task(tid)
        for i in range(3):
            tj.tool_call(name="read_file", args={"path": f"synthetic-{i}.md"}, result=self.result(self.calls, i),
                         ok=True, duration_ms=5, task_id=tid)
        if not self.ok:
            return {"ok": False, "text": "", "cost_mψ": 10, "error": self.text(self.calls)}
        return {"ok": True, "text": self.text(self.calls), "cost_mψ": 10}


def _goal(retries=None):
    g_ = goals.create_goal(title="Synthetic goal", status="active", success_criteria="a synthetic draft exists",
                           milestones=[{"name": "Draft", "due": PAST}])
    if retries is not None:
        goals.update_goal(g_["goal_id"], {"max_repair_retries": retries})
        assert goals.get_goal(g_["goal_id"])["max_repair_retries"] == retries
    return g_["goal_id"], g_["milestones"][0]["milestone_id"]


def test_a_retry_that_rereads_the_same_files_but_produces_something_new_is_not_stopped(monkeypatch):
    _verdicts(monkeypatch)
    work = _Work(lambda attempt, i: f"the same synthetic file {i}")
    goals.set_executor(work)
    gid, mid = _goal()
    out = goals.run_milestone(gid, mid)
    # Every attempt re-read exactly the same files with the same answers;
    # each produced a different result, so each attempt made progress.
    assert out["status"] != "no_progress", out
    assert work.calls == goals.DEFAULT_MAX_REPAIR_RETRIES + 1
    tool_keys = [s["distinct_progress_key"] for t in work.task_ids for s in tj.steps(t) if s["kind"] == "tool_call"]
    assert len(set(tool_keys)) == 3 < len(tool_keys), "the tool steps repeated (and stay reported)"
    assert goal_state.stalled_steps([s for t in work.task_ids for s in tj.steps(t)])
    m = goals.get_goal(gid)["milestones"][0]
    assert (m.get("blocker") or {}).get("category") != "no_progress"


def test_attempts_that_repeat_an_earlier_attempt_key_stop_the_run(monkeypatch):
    _verdicts(monkeypatch)
    # The same work failing the same way every time: the same attempt key.
    # The failure says nothing about its cause ("execution failed"): one
    # that does is stopped on its first repeat by the same-failure check
    # (goals._run_milestone_locked, §17.10) before the attempt keys count.
    work = _Work(lambda attempt, i: f"the same synthetic file {i}",
                 text=lambda attempt: "execution failed", ok=False)
    goals.set_executor(work)
    gid, mid = _goal(retries=6)
    out = goals.run_milestone(gid, mid)
    limit = goal_state.NO_NEW_PROGRESS_LIMIT
    assert out["status"] == "no_progress", out
    assert work.calls == limit + 1, "the first attempt, then LIMIT attempts that each repeated its key"
    runs = [s for t in work.task_ids for s in tj.steps(t) if s["kind"] == "milestone_run"]
    assert len(runs) == work.calls and len({s["distinct_progress_key"] for s in runs}) == 1
    m = goals.get_goal(gid)["milestones"][0]
    assert m["status"] == "blocked" and m["blocker"]["category"] == "no_progress"
    assert "came back exactly like an earlier one" in m["blocker"]["text"]


def test_stalled_attempts_counts_only_attempt_steps():
    def step(kind, key, refs=("result:aaaa",)):
        return {"kind": kind, "distinct_progress_key": key, "output_refs": list(refs)}
    tools = [step("tool_call", "dpk_same") for _ in range(10)]
    assert goal_state.stalled_attempts(tools) is None, "tool steps never stop a run"
    limit = goal_state.NO_NEW_PROGRESS_LIMIT
    same = [step("milestone_run", "dpk_attempt") for _ in range(limit + 1)]
    assert goal_state.stalled_attempts(same)
    assert goal_state.stalled_attempts(same[:limit]) is None
    varied = [step("milestone_run", f"dpk_attempt_{i}") for i in range(limit + 3)]
    assert goal_state.stalled_attempts(varied) is None
    unkeyed = [step("milestone_run", "dpk_attempt", refs=("task:t1",)) for _ in range(limit + 3)]
    assert goal_state.stalled_attempts(unkeyed) is None, "no fingerprinted outcome: neither counts nor resets"


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
        seen.append(milestone["_task_id"]) or _as_task(milestone["_task_id"]) or
        {"ok": False, "text": "", "cost_mψ": 0, "error": "synthetic failure"}))
    gid, mid = _goal()
    goals.run_milestone(gid, mid)
    assert seen
    runs = [s for s in tj.steps(seen[0]) if s["kind"] == "milestone_run"]
    assert len(runs) == 1 and runs[0]["state"] == "failed" and runs[0]["name"] == mid
    assert f"milestone:{mid}" in runs[0]["input_refs"]
    assert "synthetic failure" not in json.dumps(_raw_steps(seen[0]))


def test_an_attempt_whose_task_does_not_exist_is_kept_on_the_run_record_not_in_a_new_journal():
    seen = []
    goals.set_executor(lambda goal, milestone, critique_hint="": (
        seen.append(milestone["_task_id"]) or
        {"ok": False, "text": "", "cost_mψ": 0, "error": "synthetic failure"}))
    gid, mid = _goal()
    goals.run_milestone(gid, mid)
    assert seen
    for t in seen:
        assert tj.read(t) == [] and not (tj.tasks_dir() / t).exists(), "a step never creates a task's record"
    m = goals.get_goal(gid)["milestones"][0]
    kept = (m.get("run") or m.get("last_run") or {}).get("attempt_steps") or []
    assert [s["kind"] for s in kept] == ["milestone_run"] * len(seen)
    assert {s["task_id"] for s in kept} == set(seen)
    assert "synthetic failure" not in json.dumps(kept)


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


def test_an_observer_reading_the_journal_gets_step_events_without_their_refs():
    _call("search_web", {"query": "synthetic"}, "synthetic results")
    who = ["user"]
    c = _client(who)
    own = [e for e in c.get(f"/api/tasks/{TID}/journal").get_json()["events"] if e["kind"] == "step"]
    assert own and own[0]["output_refs"], "the owner's journal keeps them"
    who[0] = "observer"
    r = c.get(f"/api/tasks/{TID}/journal")
    assert r.status_code == 200, r.get_data(as_text=True)
    seen = [e for e in r.get_json()["events"] if e["kind"] == "step"]
    assert seen, "the step itself is still shown"
    assert all("input_refs" not in e and "output_refs" not in e for e in seen)
    assert "result:" not in r.get_data(as_text=True) and "args:" not in r.get_data(as_text=True)


# ── a failed ref never costs the tool_call record ────────────────────────

def test_the_tool_call_is_journaled_even_when_its_refs_cannot_be_made(monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("synthetic: refs failed")
    monkeypatch.setattr(sr, "artifact_refs", broken)
    monkeypatch.setattr(sr, "fp_key", broken)
    _call("write_file", {"path": "synthetic.md"}, "Wrote 3 chars to synthetic.md")
    calls = tj.read(TID, kind="tool_call")
    assert len(calls) == 1 and calls[0]["name"] == "write_file"


# ── a subagent is one step, opened and closed ─────────────────────────────

def test_a_subagent_step_opens_and_closes_with_the_same_step_id():
    def runner(task_id):
        return {"status": "complete", "result": "synthetic child result"}
    child = ag._spawn_task("child", "synthetic delegated work", runner=runner, parent_task_id=TID)
    assert child
    try:
        with ag.TASKS_LOCK:
            th = ag.TASK_THREADS.get(child)
        if th is not None:
            th.join(timeout=20)
            assert not th.is_alive(), "the child's worker did not finish"
        end = time.time() + 10
        while time.time() < end and len([e for e in _raw_steps() if e["step_kind"] == "subagent"]) < 2:
            time.sleep(0.05)
        raw = [e for e in _raw_steps() if e["step_kind"] == "subagent"]
        assert len(raw) == 2, raw
        opened, closed = raw
        assert opened["step_id"] == closed["step_id"]
        assert opened["state"] == "running" and closed["state"] != "running" and closed["ended_at"]
        assert f"task:{child}" in opened["output_refs"] and f"task:{child}" in closed["output_refs"]
        shown = [s for s in tj.steps(TID) if s["kind"] == "subagent"]
        assert len(shown) == 1 and shown[0]["id"] == opened["step_id"] and shown[0]["state"] == closed["state"]
        assert "synthetic delegated work" not in json.dumps(raw) and "synthetic child result" not in json.dumps(raw)
    finally:
        with ag.TASKS_LOCK:
            ag.TASKS.pop(child, None)
            ag.TASK_THREADS.pop(child, None)


# ── at rest: step lines are encrypted like every journal line ─────────────

def test_step_lines_are_encrypted_at_rest(monkeypatch):
    from agent_friday.services import credential_store as cs
    from agent_friday.privacy import vault_crypto as vc
    monkeypatch.setattr(tj, "settings", lambda: {"retention_days": 0, "capture_reasoning": True,
                                                 "encrypt_at_rest": True})
    derived = vc.derive_key("synthetic-passphrase-for-steps", b"0123456789abcdef")
    monkeypatch.setattr(cs, "_vault_key", lambda: derived)
    _call("search_web", {"query": "synthetic"}, "synthetic results")
    tj.step("subagent", "running", name="researcher", task_id=TID, output_refs=["task:synthetic-child"])
    raw = (tj.task_dir(TID) / "journal.jsonl").read_bytes()
    lines = [ln for ln in raw.decode("utf-8").splitlines() if ln.strip()]
    assert lines and all(ln.startswith(tj._LINE_PREFIX) for ln in lines), "every line is protected"
    for needle in (b"stp_", b"dpk_", b"step_kind", b"search_web", b"synthetic-child", b"researcher"):
        assert needle not in raw, needle
    kinds = [s["kind"] for s in tj.steps(TID)]
    assert kinds == ["tool_call", "subagent"], "and fully legible through the module"


# ── the timeline keeps 400 rows it can draw ───────────────────────────────

def test_the_timeline_drops_hidden_step_rows_before_keeping_the_last_400():
    for rel in ("ui_parts/app.html", "index.html"):
        src = (REPO / rel).read_text(encoding="utf-8")
        i = src.find("/journal?since=")
        assert i > 0, rel
        window = src[i:i + 1200]
        f, s = window.find("step_kind"), window.find("slice(-400)")
        assert 0 <= f < s, f"{rel}: hidden tool_call step rows must be filtered before slice(-400)"
