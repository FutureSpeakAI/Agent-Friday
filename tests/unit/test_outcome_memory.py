"""Outcome memory (north star §4.7, NS-4.7-1; BD-12 slice G9).

Friday remembers how finished work turned out as a structured record: the
decision, evidence ids, attempts, the actual outcome (checked or not), what is
unresolved, the owner's corrections as events, when to look again and when it
may be stale. Written when a milestone ends, when a goal ends and when an
approved outward action inside a task closes checked or failed; never off the
record. Recall shows it as memory "as of" a date with a stale flag, never as the
current state. The owner sees, corrects, resolves and forgets it; forgetting
leaves a deletion receipt.

Synthetic fixtures only: the milestone executor and its check are fakes, the
governance key is derived from a fixed phrase at run time, nothing reaches a
model.
"""
from __future__ import annotations

import hashlib
import json
import os
import time

import pytest
from flask import Flask, g

from agent_friday.services import goals
from agent_friday.services import outcome_memory as om

DAY = 86400
GOV = hashlib.sha256(b"synthetic outcome-memory fixture").digest()
OUTPUT = "SYNTHETIC-EXECUTOR-OUTPUT-MARKER"
CRITIQUE = "SYNTHETIC-CRITIQUE-MARKER"


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    from agent_friday.governance import proof_of_integrity as poi
    from agent_friday.services import dissent_gate as dg
    from agent_friday.services import off_record
    monkeypatch.setattr(poi, "get_governance_key", lambda: GOV)
    monkeypatch.setattr(goals, "GOALS_DIR", tmp_path / "goals")
    monkeypatch.setattr(goals, "REVIEWS_DIR", tmp_path / "goals" / "reviews")
    monkeypatch.setattr(goals, "RECEIPTS_LOG", tmp_path / "governance" / "goal_receipts.jsonl")
    monkeypatch.setattr(goals.approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(om, "DIR_OVERRIDE", tmp_path / "outcomes")
    monkeypatch.setattr(off_record, "active", lambda *a, **k: False)
    yield
    goals.set_executor(None)


def _executor(goal, milestone, *, critique_hint=""):
    return {"ok": True, "text": OUTPUT + " " + str(time.time()), "cost_mψ": 1}


def _verdicts(passed):
    n = {"i": 0}

    def grade(content, intent, **kw):
        n["i"] += 1
        return {"status": "ok", "passed": passed, "score": 0.9 if passed else 0.3, "threshold": 0.7,
                "critique": "" if passed else "%s %d" % (CRITIQUE, n["i"]),
                "suggestions": "" if passed else "be specific"}
    return grade


def _goal(**kw):
    args = dict(title="Synthetic newsletter", description="synthetic", status="active",
                success_criteria="a synthetic draft exists",
                milestones=[{"name": "Draft the synthetic issue"}])
    args.update(kw)
    return goals.create_goal(**args)


def _run(g_):
    return goals.run_milestone(g_["goal_id"], g_["milestones"][0]["milestone_id"])


def _ms_ref(g_):
    return "milestone:%s/%s" % (g_["goal_id"], g_["milestones"][0]["milestone_id"])


def _gap_days(a, b):
    return round((om._ts(a) - om._ts(b)) / DAY)


# ── written when work ends ─────────────────────────────────────────────────

def test_a_milestone_that_passes_its_check_and_its_goal_write_records(monkeypatch):
    goals.set_executor(_executor)
    monkeypatch.setattr(goals.qa_gates, "evaluate_text", _verdicts(True))
    g_ = _goal()
    assert _run(g_)["status"] == "done"
    rec = om.for_subject(_ms_ref(g_))
    assert rec is not None, "no outcome record for the finished milestone"
    assert rec["kind"] == "milestone"
    assert rec["outcome"]["result"] == "succeeded" and rec["outcome"]["verified"] is True
    receipt = goals.get_goal(g_["goal_id"])["receipts"][0]
    assert receipt["receipt_id"] in rec["evidence_refs"]
    assert rec["attempts"]["count"] >= 1
    assert "Draft the synthetic issue" in rec["decision"]["summary"] and rec["decision"]["ref"]
    assert rec["unresolved"] == [] and rec["corrections"] == []
    assert rec["recorded_status"] == "open" and rec["stale"] is False
    assert _gap_days(rec["revisit_at"], rec["outcome"]["at"]) == om.REVISIT_DAYS["succeeded"]
    assert _gap_days(rec["stale_after"], rec["outcome"]["at"]) == om.STALE_DAYS["milestone"]
    for field in ("id", "subject", "decision", "evidence_refs", "attempts", "outcome", "unresolved",
                  "corrections", "revisit_at", "stale_after", "created_at", "updated_at", "status"):
        assert field in rec, field
    # The goal met with its one step: the goal's own record.
    goal_now = goals.get_goal(g_["goal_id"])
    assert goal_now["status"] in ("met_verified", "met_unverified")
    grec = om.for_subject("goal:" + g_["goal_id"])
    assert grec is not None and grec["kind"] == "goal"
    assert grec["outcome"]["result"] == "succeeded"
    assert grec["outcome"]["verified"] is (goal_now["status"] == "met_verified")
    assert receipt["receipt_id"] in grec["evidence_refs"]
    # Summaries and ids only: none of the work's own words.
    stored = json.dumps(om.export())
    assert OUTPUT not in stored


def test_a_milestone_that_fails_its_check_records_a_checked_failure_with_what_is_open(monkeypatch):
    goals.set_executor(_executor)
    monkeypatch.setattr(goals.qa_gates, "evaluate_text", _verdicts(False))
    g_ = _goal()
    assert _run(g_)["status"] == "escalated"
    rec = om.for_subject(_ms_ref(g_))
    assert rec is not None, "no outcome record for the failed milestone"
    assert rec["outcome"]["result"] == "failed" and rec["outcome"]["verified"] is True
    assert any("Draft the synthetic issue" in u for u in rec["unresolved"])
    assert _gap_days(rec["revisit_at"], rec["outcome"]["at"]) == om.REVISIT_DAYS["failed"]
    escalated = [r for r in goals.get_goal(g_["goal_id"])["receipts"]
                 if r["kind"] == "verification_failed_escalated"]
    assert escalated and escalated[0]["receipt_id"] in rec["evidence_refs"]
    assert om.for_subject("goal:" + g_["goal_id"]) is None, "the goal has not ended"
    stored = json.dumps(om.export())
    assert OUTPUT not in stored and CRITIQUE not in stored


def test_ending_a_goal_with_a_step_left_records_it_unresolved():
    g_ = _goal(milestones=[{"name": "Book the synthetic venue"}])
    goals.transition_goal(g_["goal_id"], "abandoned", reason="End this goal")
    rec = om.for_subject("goal:" + g_["goal_id"])
    assert rec is not None
    assert rec["outcome"] == dict(rec["outcome"], result="unknown", verified=False)
    assert rec["unresolved"] and "Book the synthetic venue" in rec["unresolved"][0]
    assert "ended by you" in rec["decision"]["summary"]
    assert _gap_days(rec["revisit_at"], rec["outcome"]["at"]) == om.UNRESOLVED_REVISIT_DAYS


def test_off_the_record_nothing_is_written(monkeypatch, tmp_path):
    from agent_friday.services import off_record
    monkeypatch.setattr(off_record, "active", lambda *a, **k: True)
    goals.set_executor(_executor)
    monkeypatch.setattr(goals.qa_gates, "evaluate_text", _verdicts(True))
    g_ = _goal()
    _run(g_)
    goals.transition_goal(_goal()["goal_id"], "abandoned")
    assert om.count() == 0
    assert om.record(kind="action", subject={"action_id": "act_synthetic"}, decision_ref="act_synthetic",
                     decision_summary="synthetic", outcome="failed", verified=False) is None
    assert not (tmp_path / "outcomes").exists()


def test_an_outward_action_closed_in_a_task_is_remembered_and_none_without_a_task():
    from agent_friday.governance import action_gate as ag
    from agent_friday.services import policy_version as pv
    from agent_friday.services import task_journal as tj
    aid = pv.new_action_id()
    tj.push_task("synthetic-task-0001")
    try:
        ag.record_outcome("send_email", {"class": ag.OUTWARD, "action_id": aid, "decision": "allow"},
                          "ok", outcome=ag.COMPLETED_VERIFIED)
    finally:
        tj.pop_task()
    rec = om.for_subject("action:" + aid)
    assert rec is not None
    assert rec["outcome"]["result"] == "succeeded" and rec["outcome"]["verified"] is True
    assert rec["subject"]["task_id"] == "synthetic-task-0001" and aid in rec["evidence_refs"]
    other = pv.new_action_id()
    ag.record_outcome("send_email", {"class": ag.OUTWARD, "action_id": other, "decision": "allow"},
                      "error")
    assert om.for_subject("action:" + other) is None, "no task attached: nothing remembered"


# ── revisit and staleness ───────────────────────────────────────────────────

def _rec(outcome="failed", summary="Step “Synthetic step” of goal “Synthetic trip”", ident=None, now=None, **kw):
    return om.record(kind="action", subject={"action_id": ident or ("act_" + os.urandom(4).hex())},
                     decision_ref="synthetic", decision_summary=summary, outcome=outcome,
                     verified=False, now=now, **kw)


def test_due_revisits_and_stale_are_computed_not_rewritten(tmp_path):
    t0 = time.time() - 400 * DAY
    failed = _rec("failed", now=t0)
    ok = _rec("succeeded", now=t0)
    due = {r["id"] for r in om.due_revisits(now=t0 + 31 * DAY)}
    assert failed["id"] in due and ok["id"] not in due
    due = {r["id"] for r in om.due_revisits(now=t0 + 91 * DAY)}
    assert {failed["id"], ok["id"]} <= due
    store = tmp_path / "outcomes" / "records.bin"
    before = store.read_bytes()
    v = om.get(failed["id"], now=t0 + (om.STALE_DAYS["action"] + 1) * DAY)
    assert v["stale"] is True and v["status"] == "stale" and v["recorded_status"] == "open"
    assert om.get(failed["id"], now=t0 + DAY)["stale"] is False
    om.records(now=t0 + 999 * DAY)
    assert store.read_bytes() == before, "reading must not rewrite the store"
    assert b"Synthetic trip" not in before, "the store is encrypted at rest"


def test_home_shows_one_quiet_item_when_revisits_are_due():
    from agent_friday.services import desktop_card_sources as dcs
    _rec("failed", now=time.time() - 40 * DAY)
    _rec("failed", now=time.time() - 50 * DAY)
    rows, _stamp, _detail = dcs._goals(time.time())
    items = [r for r in rows if r["id"] == "outcomes:revisit"]
    assert len(items) == 1 and items[0]["state"] == "revisit"
    assert "2 past outcomes" in items[0]["body"] and "Synthetic trip" not in items[0]["body"]


# ── recall ──────────────────────────────────────────────────────────────────

def test_recall_says_as_of_and_stale_and_never_presents_it_as_current():
    t0 = time.time() - 400 * DAY
    old = _rec("failed", summary="Step “Book flights” of goal “Synthetic Lisbon trip”", now=t0)
    fresh = _rec("succeeded", summary="Step “Pack bags” of goal “Synthetic Lisbon trip”")
    block = om.context_block("how did the synthetic lisbon trip go?")
    assert om.RECALL_HEADER in block and "not the current state" in block
    old_line = next(l for l in block.splitlines() if old["id"] in l)
    fresh_line = next(l for l in block.splitlines() if fresh["id"] in l)
    assert "as of " + old["outcome"]["at"][:10] in old_line and "STALE" in old_line
    assert "STALE" not in fresh_line and "as of " in fresh_line
    assert "open" not in block.lower(), "no stored status is offered as how things stand"
    assert om.context_block("something unrelated entirely") == ""


# ── the owner corrects and resolves ─────────────────────────────────────────

def test_a_correction_is_an_event_and_changes_the_effective_outcome():
    r = _rec("failed")
    out = om.correct(r["id"], outcome="succeeded")
    assert out["ok"]
    v = om.get(r["id"])
    assert v["outcome"]["result"] == "failed", "the recorded outcome is kept"
    assert len(v["corrections"]) == 1
    ev = v["corrections"][0]
    assert ev["by"] == "owner" and ev["field"] == "outcome" and ev["before"] == "failed" and ev["after"] == "succeeded"
    assert v["effective"] == dict(v["effective"], result="succeeded", verified=False, source="owner")
    om.correct(r["id"], unresolved=["synthetic follow-up"])
    v = om.get(r["id"])
    assert len(v["corrections"]) == 2 and v["unresolved"] == ["synthetic follow-up"]
    assert om.correct(r["id"], outcome="done-ish")["ok"] is False


def test_resolve_takes_it_off_the_revisit_list_without_deleting():
    r = _rec("failed", now=time.time() - 40 * DAY)
    assert r["id"] in {x["id"] for x in om.due_revisits()}
    assert om.resolve(r["id"])["ok"]
    v = om.get(r["id"])
    assert v["recorded_status"] == "resolved" and v["corrections"][-1]["after"] == "resolved"
    assert r["id"] not in {x["id"] for x in om.due_revisits()}
    assert om.count() == 1


# ── routes: owner only ──────────────────────────────────────────────────────

def _client(who):
    from agent_friday.routes.goals import goals_bp
    app = Flask("outcomes-route")
    app.secret_key = os.urandom(16)

    @app.before_request
    def _who():
        g.friday_principal = who[0]
    app.register_blueprint(goals_bp)
    return app.test_client()


def test_routes_serve_the_owner_and_refuse_an_observer():
    r = _rec("failed")
    who = ["user"]
    c = _client(who)
    got = c.get("/api/outcomes")
    assert got.status_code == 200, got.get_data(as_text=True)
    assert [o["id"] for o in got.get_json()["outcomes"]] == [r["id"]]
    assert c.post("/api/outcomes/%s/correct" % r["id"], json={"outcome": "partial"}).status_code == 200
    assert om.get(r["id"])["effective"]["result"] == "partial"
    assert c.post("/api/outcomes/%s/resolve" % r["id"], json={}).status_code == 200
    assert c.post("/api/outcomes/out_nosuchrecord/resolve", json={}).status_code == 404
    who[0] = "observer"
    assert c.get("/api/outcomes").status_code == 403
    assert c.get("/api/outcomes?export=1").status_code == 403
    assert c.post("/api/outcomes/%s/correct" % r["id"], json={"outcome": "failed"}).status_code == 403
    assert c.post("/api/outcomes/%s/resolve" % r["id"], json={}).status_code == 403
    assert c.delete("/api/outcomes/%s" % r["id"]).status_code == 403
    assert c.post("/api/outcomes/forget", json={"goal_id": "goal_x"}).status_code == 403
    assert om.get(r["id"])["effective"]["result"] == "partial"


# ── forget ──────────────────────────────────────────────────────────────────

def test_forgetting_a_goal_removes_its_records_with_a_deletion_receipt(monkeypatch):
    from agent_friday.services import action_ledger
    goals.set_executor(_executor)
    monkeypatch.setattr(goals.qa_gates, "evaluate_text", _verdicts(True))
    g_ = _goal()
    _run(g_)
    keep = _rec("failed")
    mine = {r["id"] for r in om.records() if (r.get("subject") or {}).get("goal_id") == g_["goal_id"]}
    assert len(mine) >= 2
    receipt = om.forget_subject("goal", g_["goal_id"])
    assert receipt["ok"] and receipt["removed"] == len(mine) and set(receipt["ids"]) == mine
    assert receipt["receipt_id"] and receipt["scope"] == "goal:" + g_["goal_id"]
    assert [r["id"] for r in om.records()] == [keep["id"]]
    logged = action_ledger.entries(kinds=["outcome_memory_forget"])
    assert logged and receipt["receipt_id"] in json.dumps(logged)
    assert "Synthetic newsletter" not in json.dumps(logged), "the receipt keeps ids, not summaries"
    who = ["user"]
    c = _client(who)
    out = c.delete("/api/outcomes/%s" % keep["id"]).get_json()
    assert out["removed"] == 1 and om.count() == 0


def test_forgetting_a_source_task_removes_the_records_it_fed():
    fed = _rec("failed", attempt_refs=["synthetic-task-0042"])
    other = _rec("failed", attempt_refs=["synthetic-task-0043"])
    receipt = om.forget_subject("task", "synthetic-task-0042")
    assert receipt["removed"] == 1 and receipt["ids"] == [fed["id"]]
    assert [r["id"] for r in om.records()] == [other["id"]]
    assert om.forget_subject("task", "")["ok"] is False


def test_the_store_is_in_the_privacy_map_and_forget_reaches_it():
    from agent_friday.services import forget_registry as fr
    from agent_friday.services import privacy_map as pm
    stores = {s["key"]: s for s in pm._stores()}
    assert "outcomes" in stores and stores["outcomes"]["paths"] == ["outcomes"]
    kinds = {c["kind"] for c in stores["outcomes"]["controls"]}
    assert {"delete", "export"} <= kinds
    assert "outcome_memory" in fr.registered()
    _rec("failed", summary="Step “Call Pat Example” of goal “Synthetic party”")
    _rec("failed", summary="Step “Order cake” of goal “Synthetic party”")
    assert fr.find_all({"pat example"}, set())["outcome_memory"] == 1
    assert fr.purge_all({"pat example"}, set())["outcome_memory"] == 1
    assert om.count() == 1 and "Pat Example" not in json.dumps(om.export())
