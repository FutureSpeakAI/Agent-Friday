"""The task journal's linked events (NS-29.4-1, NS-29.4-2; slice G10).

The journal records approval, verification, artifact, blocker, paused,
resumed, stopped and receipt events, each written at the hook where the thing
happens, with ids and codes only. Events that relate to a gate decision carry
its action id (and the ledger receipt), the tool_call event names the action
id of its own decision, and `ledger_links` checks the journal's action ids
against the action ledger (served by /api/tasks/<id>/journal as `links`).
Off the record nothing is written; an observer gets the ids that are random
or sequence numbers, never the artifact reference; every line is encrypted at
rest.

Every fixture is synthetic. Tool handlers, the approval executor's run and
the goal grader are fakes, so nothing reaches a model or a service.
"""
from __future__ import annotations

import hashlib
import json
import time

import pytest
from flask import Flask, g

from agent_friday.governance import action_gate as G
from agent_friday.services import action_ledger as L
from agent_friday.services import agent as ag
from agent_friday.services import approvals
from agent_friday.services import artifact_manifest as am
from agent_friday.services import goal_state
from agent_friday.services import goals
from agent_friday.services import task_journal as tj

TID = "g10-events-task-0001"
WORDS = "SYNTHETIC-G10-CONTENT-MARKER"
PAST = "2020-01-01T00:00:00Z"


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    monkeypatch.setattr(tj, "settings", lambda: {"retention_days": 0, "capture_reasoning": True,
                                                 "encrypt_at_rest": False})
    tj.reset_for_tests()
    tj.note_action("", None)            # no gate decision carried over from another test
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: kw)
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(approvals, "_HOOKS", {})
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(G, "verify_claws", lambda *a, **k: (True, "intact"))
    monkeypatch.setattr(G, "outward_hold", lambda: None)
    monkeypatch.setattr(G, "_send_grant", lambda *a, **k: None)
    monkeypatch.setattr(am, "submit", lambda *a, **k: False)
    monkeypatch.setattr(ag._rtrace, "tool_finished", lambda *a, **k: None)
    monkeypatch.setattr(goals, "GOALS_DIR", tmp_path / "goals")
    monkeypatch.setattr(goals, "REVIEWS_DIR", tmp_path / "goals" / "reviews")
    monkeypatch.setattr(goals, "RECEIPTS_LOG", tmp_path / "governance" / "goal_receipts.jsonl")
    tj.append(TID, "created", name="synthetic task")
    yield
    goals.set_executor(None)


# ── helpers ──────────────────────────────────────────────────────────────

def _events(kind, tid=TID):
    return tj.read(tid, kind=kind)


def _raw(tid=TID) -> str:
    return (tj.task_dir(tid) / "journal.jsonl").read_text(encoding="utf-8")


def _new_action_id():
    from agent_friday.services import policy_version as pv
    return pv.new_action_id()


def _outward_tool():
    return "send_email" if "send_email" in ag.CLAUDE_TOOL_HANDLERS else next(
        n for n in sorted(ag.CLAUDE_TOOL_HANDLERS) if G.classify(n, {})[0] == G.OUTWARD)


def _args_for(name):
    schema = ag._schema_for_tool(name) or {}
    props = schema.get("properties") or {}
    out = {}
    for k in schema.get("required") or []:
        p = props.get(k) or {}
        if p.get("enum"):
            out[k] = p["enum"][0]
            continue
        t = p.get("type")
        t = t[0] if isinstance(t, list) else t
        out[k] = {"string": "x", "number": 1, "integer": 1, "boolean": False,
                  "array": [], "object": {}}.get(t, "x")
    return out


def _fresh_call_state(ctx):
    from agent_friday.services import taint
    ag._PENDING_CONFIRMATIONS.clear()
    taint.reset()
    ag._hooks.reset_rate_limiter()
    ag._call_verifier.reset(ctx)


def _decisions(tool):
    return [e for e in L.entries(["decision"], newest_first=False) if e["rec"].get("tool") == tool]


def _outcomes(action_id):
    return [e for e in L.entries(["outcome"], newest_first=False)
            if e["rec"].get("action_id") == action_id]


def _card(subject, tid=TID, tool="create_calendar_event"):
    return approvals.create_approval(
        kind="tainted_action", subject_type="tool_action", subject_id=subject,
        title="Synthetic card", action_description="%s %s" % (tool, json.dumps({"title": WORDS})),
        description=WORDS, force_gate=True,
        payload={"tool": tool, "input": {"title": WORDS}, "task_id": tid},
        requested_by="taint_gate")


def _running_task(tid):
    now = time.time()
    with ag.TASKS_LOCK:
        ag.TASKS[tid] = {"id": tid, "name": "synthetic task", "status": "running",
                         "created": now, "started": now, "phase": "working"}


def _goal():
    g_ = goals.create_goal(title="Synthetic goal", status="active", success_criteria="a synthetic draft exists",
                           milestones=[{"name": "Draft", "due": PAST}])
    return g_["goal_id"], g_["milestones"][0]["milestone_id"]


def _client(who):
    from agent_friday.routes.tasks import tasks_bp
    app = Flask("g10-journal-route")

    @app.before_request
    def _who():
        g.friday_principal = who[0]
    app.register_blueprint(tasks_bp)
    return app.test_client()


# ── approval: raised and decided at the approvals hook ───────────────────

def test_a_card_raised_for_a_task_tool_call_is_journaled_with_its_action_id(monkeypatch):
    tool = _outward_tool()
    monkeypatch.setattr(G, "_decide",
                        lambda tool_, klass, why, ctx, *a, **k: G.Verdict("card", klass, "test"))
    ctx = {"is_background_task": True}
    _fresh_call_state(ctx)
    tj.push_task(TID)
    try:
        out = ag._execute_tool(tool, _args_for(tool), session_ctx=ctx)
    finally:
        tj.pop_task()
    assert "APPROVAL CARD RAISED" in str(out), out
    raised = [e for e in _events("approval") if e.get("phase") == "raised"]
    assert len(raised) == 1, _events("approval")
    card = approvals.get_approval(raised[0]["approval_id"])
    assert card and card["status"] == "pending"
    assert raised[0]["action_id"] == _decisions(tool)[-1]["rec"]["action_id"], \
        "the card's event names the gate decision that raised it"
    assert raised[0]["card_kind"] == "tainted_action"


def test_a_decided_card_is_journaled_approved_denied_or_expired_by_ids_only():
    denied = _card("taint:g10:deny")
    expired = _card("taint:g10:expire")
    approvals.decide(denied["approval_id"], "deny", decided_by="owner")
    approvals.expire_matching(lambda r: r.get("approval_id") == expired["approval_id"], "system:test")
    evs = _events("approval")
    by_id = {}
    for e in evs:
        by_id.setdefault(e["approval_id"], []).append(e)
    assert [e["phase"] for e in by_id[denied["approval_id"]]] == ["raised", "decided"]
    assert by_id[denied["approval_id"]][-1]["outcome"] == "denied"
    assert by_id[denied["approval_id"]][-1]["by"] == "owner"
    assert by_id[expired["approval_id"]][-1]["outcome"] == "expired"
    assert by_id[expired["approval_id"]][-1]["by"] == "system"
    raw = _raw()
    assert WORDS not in raw and "Synthetic card" not in raw, "ids only, never the card's words"


def test_an_approved_card_run_by_the_executor_journals_its_decision_and_receipt(monkeypatch):
    from agent_friday.services import approval_executor as ex
    calls = []

    def fake_run(name, args, **kw):
        calls.append(kw.get("session_ctx") or {})
        return "created: ok"
    monkeypatch.setattr(ex, "_run_tool", fake_run)
    monkeypatch.setattr(approvals, "_HOOKS", {ex.KIND: [ex._on_decision]})
    rec = _card("taint:g10:approve")
    approvals.decide(rec["approval_id"], "approve", decided_by="owner")
    assert len(calls) == 1
    decided = [e for e in _events("approval") if e.get("phase") == "decided"]
    assert decided and decided[-1]["outcome"] == "approved" and decided[-1]["by"] == "owner"
    ref = calls[0]["approved_card_decision"]
    receipts = [e for e in _events("receipt") if e.get("action_id") == ref["action_id"]]
    assert len(receipts) == 1, "the executor's outcome lands in the task that raised the card"
    out = _outcomes(ref["action_id"])[-1]
    assert receipts[0]["receipt_id"] == "lgr_%d" % out["seq"]
    assert receipts[0]["approval_id"] == rec["approval_id"]
    assert receipts[0]["outcome"] == G.EXECUTED


# ── tool_call carries its action id; receipt on outcome close ────────────

def test_the_tool_call_names_its_action_id_and_the_outcome_is_a_receipt(monkeypatch):
    tool = _outward_tool()
    monkeypatch.setattr(G, "_decide",
                        lambda tool_, klass, why, ctx, *a, **k: G.Verdict("allow", klass, "test"))
    monkeypatch.setitem(ag.CLAUDE_TOOL_HANDLERS, tool, lambda inp: "sent")
    ctx = {"is_background_task": True}
    _fresh_call_state(ctx)
    args = _args_for(tool)
    tj.push_task(TID)
    try:
        result = ag._execute_tool(tool, args, session_ctx=ctx)
        ag._orb_tool_trace(None, tool, args, result, 5)
    finally:
        tj.pop_task()
    dec = _decisions(tool)[-1]
    aid = dec["rec"]["action_id"]
    call = _events("tool_call")[-1]
    assert call["action_id"] == aid, "the tool_call event names the gate decision's action id"
    out = _outcomes(aid)[-1]
    rcpt = [e for e in _events("receipt") if e.get("action_id") == aid]
    assert len(rcpt) == 1
    assert rcpt[0]["receipt_id"] == "lgr_%d" % out["seq"]
    assert rcpt[0]["decision_receipt_id"] == "lgr_%d" % dec["seq"]
    assert rcpt[0]["outcome"] == out["rec"]["outcome"] and rcpt[0]["terminal"] is True
    assert set(rcpt[0]) <= {"seq", "ts", "task_id", "kind", "action_id", "receipt_id",
                            "decision_receipt_id", "outcome", "terminal", "approval_id"}


def test_a_note_for_one_call_never_names_another():
    tj.note_action("search_web", _new_action_id())
    tj.push_task(TID)
    try:
        tj.tool_call(name="read_file", args={}, result="ok", ok=True)
        tj.tool_call(name="search_web", args={}, result="ok", ok=True)
    finally:
        tj.pop_task()
    calls = _events("tool_call")
    assert "action_id" not in calls[0]
    assert "action_id" not in calls[1], "a note is taken once, by the next call, or dropped"


# ── verification: a goal check ran ───────────────────────────────────────

def test_a_goal_check_is_journaled_passed_failed_or_unknown(monkeypatch):
    monkeypatch.setattr(goals.qa_gates, "evaluate_text",
                        lambda content, intent, **kw: {"status": "ok", "passed": True, "score": 1.0,
                                                        "threshold": 0.7, "critique": WORDS,
                                                        "suggestions": ""})
    gid, mid = _goal()
    goal = goals.get_goal(gid)
    m = goal["milestones"][0]
    since = time.time() - 60
    v_failed = goals._verify(goal, dict(m, evidence=[{"kind": "file", "what": "synthetic-proof.md"}]),
                             WORDS, "criteria", task_id=TID, since=since)
    goals._verify(goal, dict(m, evidence=[]), WORDS, "criteria", task_id=TID, since=since)
    goals._verify(goal, dict(m, evidence=[{"kind": "owner_confirms", "what": "the owner likes it"}]),
                  WORDS, "criteria", task_id=TID, since=since)
    evs = _events("verification")
    assert len(evs) == 3
    assert {e["check_id"] for e in evs} == {f"goal:{gid}:{mid}"}
    assert v_failed["passed"] is False
    assert evs[0]["result"] == ("failed" if v_failed.get("status") == "ok" else "unknown")
    assert evs[1]["result"] == "passed"
    assert evs[2]["result"] == "unknown", "only the owner can judge it"
    assert WORDS not in _raw() and "synthetic-proof" not in _raw()


# ── artifact: a creating tool made a file ────────────────────────────────

def test_a_file_made_by_a_creating_tool_is_journaled_by_reference(tmp_path, monkeypatch):
    from agent_friday.services import off_record
    monkeypatch.setattr(off_record, "active", lambda *a, **k: False)
    monkeypatch.setattr(G, "classify", lambda tool, args, ctx=None: (G.INTERNAL, "test rule"))
    made = tmp_path / "g10-made-file.md"
    made.write_text(WORDS, encoding="utf-8")
    args = {"path": str(made), "content": WORDS}
    result = "Wrote %d chars to %s" % (len(WORDS), made)
    G.authorize("write_file", args, {"task_id": TID, "is_background_task": True})
    aid = _decisions("write_file")[-1]["rec"]["action_id"]
    lines = am.record_call("write_file", args, result, task_id=TID, turn_id=None,
                           started_at=time.time() - 5,
                           where={"manifest": tmp_path / "manifest.jsonl", "key": am._key(),
                                  "gov_key": G._governance_key()})
    assert len(lines) == 1
    evs = _events("artifact")
    assert len(evs) == 1
    assert evs[0]["artifact_id"] == am.artifact_ref(lines[0])
    assert evs[0]["tool"] == "write_file" and evs[0]["action_id"] == aid
    raw = _raw()
    assert str(made) not in raw and made.name not in raw and WORDS not in raw, "no path, no content"


# ── blocker: a typed blocker stopped the attempt ─────────────────────────

def test_a_typed_blocker_is_journaled_in_the_attempt_task():
    gid, mid = _goal()
    goals._note_run(gid, mid, task_id=TID, started_at=time.time())
    goals._block(gid, mid, goal_state.blocker("run_failed", WORDS, type="task_failed"), note=WORDS)
    evs = _events("blocker")
    assert len(evs) == 1
    assert evs[0]["code"] == "task_failed" and evs[0]["category"] == "run_failed"
    assert WORDS not in _raw()


# ── paused / resumed / stopped at the task's state transitions ───────────

def test_pause_resume_and_stop_are_journaled_with_who_and_why():
    _running_task(TID)
    ag._task_set(TID, user_paused=True)
    ag._task_set(TID, user_paused=False)
    ag._task_set(TID, status="cancelled", ended=time.time(),
                 result="[Cancelled] Stopped by the user; the record is kept.")
    kinds = [e["kind"] for e in tj.read(TID) if e["kind"] in tj.LIFECYCLE_KINDS]
    assert kinds == ["paused", "resumed", "stopped"]
    p, r, s = (_events(k)[-1] for k in ("paused", "resumed", "stopped"))
    assert (p["who"], p["reason_code"]) == ("owner", "owner_pause")
    assert (r["who"], r["reason_code"]) == ("owner", "owner_resume")
    assert (s["who"], s["reason_code"]) == ("owner", "owner_cancel")


def test_the_spending_cap_pause_and_a_timeout_are_friday_s():
    _running_task(TID)
    ag._task_set(TID, status="failed", ended=time.time(),
                 result="%s synthetic cap reached" % ag.SPEND_CAP_PAUSE_MARK)
    p = _events("paused")[-1]
    assert (p["who"], p["reason_code"]) == ("system", "spend_cap")
    other = "g10-events-task-0002"
    tj.append(other, "created", name="synthetic task")
    _running_task(other)
    ag._task_set(other, status="timeout", ended=time.time(), result="[Timeout]")
    s = _events("stopped", other)[-1]
    assert (s["who"], s["reason_code"]) == ("system", "timeout")


# ── the journal -> ledger cross-check ────────────────────────────────────

def test_links_count_the_journal_action_ids_found_and_missing_in_the_ledger(monkeypatch):
    tool = _outward_tool()
    monkeypatch.setattr(G, "_decide",
                        lambda tool_, klass, why, ctx, *a, **k: G.Verdict("allow", klass, "test"))
    monkeypatch.setitem(ag.CLAUDE_TOOL_HANDLERS, tool, lambda inp: "sent")
    ctx = {"is_background_task": True}
    _fresh_call_state(ctx)
    args = _args_for(tool)
    tj.push_task(TID)
    try:
        result = ag._execute_tool(tool, args, session_ctx=ctx)
        ag._orb_tool_trace(None, tool, args, result, 5)
    finally:
        tj.pop_task()
    assert tj.ledger_links(TID) == {"checked": 1, "found": 1, "missing": 0}
    # an action id the ledger never recorded
    tj.receipt({"seq": 99999, "rec": {"action_id": _new_action_id(), "outcome": "executed"}},
               task_id=TID)
    assert tj.ledger_links(TID) == {"checked": 2, "found": 1, "missing": 1}
    body = _client(["user"]).get(f"/api/tasks/{TID}/journal").get_json()
    assert body["links"] == {"checked": 2, "found": 1, "missing": 1}


# ── off the record: nothing is written ───────────────────────────────────

def test_off_the_record_no_linked_event_is_written(monkeypatch):
    from agent_friday.services import off_record
    monkeypatch.setattr(off_record, "skip", lambda *a, **k: True)
    tid = "g10-offrecord-0001"          # its own id: off-record holds for a task's whole life
    _running_task(tid)
    ag._task_set(tid, user_paused=True)
    ag._task_set(tid, status="cancelled", ended=time.time(), result="[Cancelled]")
    _card("taint:g10:offrecord", tid=tid)
    assert tj.verification(tid, "goal:g:m", "passed") is None
    assert tj.blocker(tid, "run_failed") is None
    assert tj.artifact(tid, "art_synthetic") is None
    assert tj.receipt({"seq": 3, "rec": {"action_id": _new_action_id(), "outcome": "executed"}},
                      task_id=tid) is None
    assert tj.lifecycle(tid, "stopped", who="owner", reason_code="owner_cancel") is None
    assert not (tj.tasks_dir() / tid).exists()
    assert tj.ledger_links(tid) == {"checked": 0, "found": 0, "missing": 0}


# ── an observer: ids that are random stay, the artifact reference goes ───

def test_an_observer_gets_action_and_receipt_ids_but_no_artifact_reference():
    aid = _new_action_id()
    ref = am.artifact_ref({"hmac": hashlib.sha256(b"synthetic manifest line").hexdigest()})
    tj.artifact(TID, ref, tool="write_file", qa_ok=True, action_id=aid)
    tj.receipt({"seq": 7, "rec": {"action_id": aid, "decision_seq": 6, "outcome": "executed",
                                  "terminal": True}}, task_id=TID)
    who = ["user"]
    c = _client(who)
    own = c.get(f"/api/tasks/{TID}/journal").get_json()["events"]
    assert [e for e in own if e["kind"] == "artifact"][0]["artifact_id"] == ref
    who[0] = "observer"
    r = c.get(f"/api/tasks/{TID}/journal")
    assert r.status_code == 200, r.get_data(as_text=True)
    seen = r.get_json()["events"]
    art = [e for e in seen if e["kind"] == "artifact"]
    rcpt = [e for e in seen if e["kind"] == "receipt"]
    assert art and "artifact_id" not in art[0], "a keyed reference is the owner's only"
    assert art[0]["action_id"] == aid
    assert rcpt and rcpt[0]["action_id"] == aid and rcpt[0]["receipt_id"] == "lgr_7"
    assert ref not in r.get_data(as_text=True)


# ── at rest: every linked line is encrypted ──────────────────────────────

def test_linked_events_are_encrypted_at_rest(monkeypatch):
    from agent_friday.privacy import vault_crypto as vc
    from agent_friday.services import credential_store as cs
    monkeypatch.setattr(tj, "settings", lambda: {"retention_days": 0, "capture_reasoning": True,
                                                 "encrypt_at_rest": True})
    derived = vc.derive_key("synthetic phrase for the g10 journal", b"synthetic-salt-for-g10")
    monkeypatch.setattr(cs, "_vault_key", lambda: derived)
    tid = "g10-encrypted-0001"
    tj.append(tid, "created", name="synthetic task")
    aid = _new_action_id()
    tj.lifecycle(tid, "paused", who="owner", reason_code="owner_pause")
    tj.verification(tid, "goal:synthetic:check", "passed")
    tj.blocker(tid, "run_failed")
    tj.receipt({"seq": 11, "rec": {"action_id": aid, "outcome": "executed"}}, task_id=tid)
    raw = (tj.task_dir(tid) / "journal.jsonl").read_bytes()
    lines = [ln for ln in raw.decode("utf-8").splitlines() if ln.strip()]
    assert len(lines) == 5 and all(ln.startswith(tj._LINE_PREFIX) for ln in lines), "every line is protected"
    for needle in (aid.encode(), b"lgr_11", b"owner_pause", b"run_failed", b"goal:synthetic"):
        assert needle not in raw, needle
    kinds = [e["kind"] for e in tj.read(tid)]
    assert kinds == ["created", "paused", "verification", "blocker", "receipt"], "and legible through the module"
