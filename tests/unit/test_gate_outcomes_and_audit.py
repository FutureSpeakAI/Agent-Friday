"""BD-11a: one outcome vocabulary on every verdict and receipt (NS-31.5-2), and
the terminal-receipt invariant over the action ledger (NS-36.3-12): every
outward action has its decision and exactly one terminal receipt, no request
runs twice, no task is marked done before its outward actions are.

  * each outcome word comes out of a representative call: executed,
    approval_required, denied, forbidden, waiting, failed, completed_verified;
  * services/outward_audit finds an open action, a duplicate terminal receipt,
    the same idempotency key executed twice and a task marked done too early on
    crafted ledgers, and nothing on a clean run;
  * the executors outside a tool call (record_external) now close their
    decision with a terminal outcome.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from agent_friday.governance import action_gate as G
from agent_friday.services import action_ledger as L
from agent_friday.services import approvals
from tests.unit.phone_fakes import fake_twilio, phone_home, verify_owner  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(G, "verify_claws", lambda *a, **k: (True, "intact"))
    monkeypatch.setattr(G, "outward_hold", lambda: None)
    monkeypatch.setattr(G, "_send_grant", lambda *a, **k: None)
    yield


def _classify_as(monkeypatch, klass):
    monkeypatch.setattr(G, "classify", lambda tool, args, ctx=None: (klass, "test rule"))


def _decisions(tool):
    return [e["rec"] for e in L.entries(["decision"], newest_first=False)
            if e["rec"].get("tool") == tool]


def _outcomes(action_id):
    return [e["rec"] for e in L.entries(["outcome"], newest_first=False)
            if e["rec"].get("action_id") == action_id]


def _outcomes_for_tool(tool):
    return [e["rec"] for e in L.entries(["outcome"], newest_first=False)
            if e["rec"].get("tool") == tool]


# ── the vocabulary ───────────────────────────────────────────────────────────

def test_one_vocabulary_and_the_old_statuses_map_into_it():
    assert G.OUTCOMES == ("executed", "approval_required", "denied", "forbidden", "waiting",
                          "failed", "completed_verified")
    assert G.TERMINAL_OUTCOMES == {"executed", "failed", "completed_verified", "denied", "forbidden"}
    assert [G.outcome_of_status(s) for s in ("ok", "error", "refused", "held")] == \
        ["executed", "failed", "denied", "forbidden"]
    assert G.outcome_of_status("waiting_approval") == "waiting"
    assert G.outcome_of_status("completed_unverified") == "executed"
    assert G.outcome_of_status("failed") == "failed"
    assert G.outcome_of_status("something new") == "failed"        # never executed by default
    assert G.TASK_STATE_OUTCOME["completed_verified"] == "completed_verified"


# ── each outcome from a representative call ─────────────────────────────────

def test_an_internal_action_is_executed(monkeypatch):
    _classify_as(monkeypatch, G.INTERNAL)
    v = G.authorize("probe_internal", {"x": 1}, {"is_background_task": True})
    assert v.action == "allow" and v.outcome == G.EXECUTED
    assert _decisions("probe_internal")[-1]["outcome"] == G.EXECUTED


def test_an_outward_action_that_needs_the_owner_is_approval_required(monkeypatch):
    _classify_as(monkeypatch, G.OUTWARD)
    v = G.authorize("probe_send", {"to": "a"}, {"session_id": "s1"})
    assert v.action == "confirm" and v.outcome == G.APPROVAL_REQUIRED
    v = G.authorize("probe_send", {"to": "b"}, {"is_background_task": True})
    assert v.action == "card" and v.outcome == G.APPROVAL_REQUIRED
    assert [d["outcome"] for d in _decisions("probe_send")] == [G.APPROVAL_REQUIRED] * 2


def test_a_rule_or_safe_mode_is_forbidden(monkeypatch):
    _classify_as(monkeypatch, "forbidden")
    v = G.authorize("probe_send", {}, {"session_id": "s1"})
    assert v.action == "deny" and v.outcome == G.FORBIDDEN
    _classify_as(monkeypatch, G.OUTWARD)
    monkeypatch.setattr(G, "outward_hold", lambda: "Friday is in safe mode")
    v = G.authorize("probe_send", {"to": "a"}, {"session_id": "s1"})
    assert v.action == "deny" and v.outcome == G.FORBIDDEN
    assert [d["outcome"] for d in _decisions("probe_send")] == [G.FORBIDDEN, G.FORBIDDEN]


def test_a_card_waits_and_the_owners_no_is_denied(monkeypatch):
    monkeypatch.setattr(approvals, "_HOOKS", {})
    detail = {"what": "probe"}
    first = G.authorize_external("probe:external", detail, requested_by="test")
    assert first.action == "card" and first.outcome == G.APPROVAL_REQUIRED
    again = G.authorize_external("probe:external", detail, requested_by="test")
    assert again.action == "card" and again.outcome == G.WAITING          # its card is open
    card = approvals.find_for_subject("external_action", G.external_subject("probe:external", detail),
                                      "governed_action")
    approvals.decide(card["approval_id"], "deny", decided_by="owner")
    no = G.authorize_external("probe:external", detail, requested_by="test")
    assert no.action == "deny" and no.outcome == G.DENIED
    recs = _decisions("probe:external")
    assert [r["outcome"] for r in recs] == [G.APPROVAL_REQUIRED, G.WAITING, G.DENIED]
    assert all(r.get("approval") == card["approval_id"] for r in recs)


def _args_for(agent, name):
    schema = agent._schema_for_tool(name) or {}
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


def test_a_handler_error_is_failed_and_a_handlers_own_card_is_not_executed(monkeypatch):
    import agent_friday.services.agent as agent
    from agent_friday.services import taint
    agent._PENDING_CONFIRMATIONS.clear()
    taint.reset()
    monkeypatch.setattr(G, "_decide",
                        lambda tool, klass, why, ctx, *a, **k: G.Verdict("allow", klass, "test"))
    tool = "send_email" if "send_email" in agent.CLAUDE_TOOL_HANDLERS else next(
        n for n in sorted(agent.CLAUDE_TOOL_HANDLERS) if G.classify(n, {})[0] == G.OUTWARD)
    ctx = {"is_background_task": True}

    def run(handler):
        monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, tool, handler)
        agent._hooks.reset_rate_limiter()
        agent._call_verifier.reset(ctx)
        agent._execute_tool(tool, _args_for(agent, tool), session_ctx=ctx)
        return _outcomes_for_tool(tool)[-1]

    def boom(inp):
        raise RuntimeError("the service said no")
    out = run(boom)
    assert out["status"] == "error" and out["outcome"] == G.FAILED and out["terminal"] is True
    out = run(lambda inp: "[APPROVAL CARD RAISED] the send waits for the owner's card")
    assert out["outcome"] == G.APPROVAL_REQUIRED and out["terminal"] is False
    out = run(lambda inp: "sent")
    assert out["status"] == "ok" and out["outcome"] == G.EXECUTED and out["terminal"] is True
    taint.reset()


def test_a_confirmed_completion_is_completed_verified_once():
    ref = G.record_external("probe:send", surface="test", approval_id="ap-verified")
    assert G.close_external(ref, True, verified=True, detail="sent")
    assert not G.close_external(ref, True)                                 # once per decision
    outs = _outcomes(ref["action_id"])
    assert len(outs) == 1 and outs[0]["outcome"] == G.COMPLETED_VERIFIED and outs[0]["terminal"]
    assert outs[0]["decision_seq"] == ref["seq"] and outs[0]["decision_hash"] == ref["hash"]
    bad = G.record_external("probe:send", surface="test")
    G.close_external(bad, False, verified=True)                            # verified only if it ran
    assert [o["outcome"] for o in _outcomes(bad["action_id"])] == [G.FAILED]
    assert not G.close_external(None, True) and not G.close_external({"ok": True}, True)
    from agent_friday.services import receipt_audit
    assert receipt_audit.audit()["orphan_count"] == 0


# ── the audit ────────────────────────────────────────────────────────────────

def _decision(aid, *, decision="allow", tool="probe_send", task=None, key=None, outcome=None,
              approval=None):
    rec = {"tool": tool, "class": G.OUTWARD, "decision": decision, "action_id": aid,
           "args_hash": "x", "envelope": {"action_id": aid, "idempotency_key": key}}
    for k, v in (("task_id", task), ("outcome", outcome), ("approval", approval)):
        if v is not None:
            rec[k] = v
    return L.append("decision", rec, durable=True)


def _outcome(aid, word, status="ok", tool="probe_send"):
    return L.append("outcome", {"tool": tool, "action_id": aid, "status": status, "outcome": word,
                                "terminal": word in G.TERMINAL_OUTCOMES})


def test_a_clean_run_has_nothing_to_report(monkeypatch):
    from agent_friday.services import outward_audit
    ref = G.record_external("probe:send", surface="test")
    G.close_external(ref, True)
    _classify_as(monkeypatch, G.OUTWARD)
    monkeypatch.setattr(G, "outward_hold", lambda: "Friday is in safe mode")
    G.authorize("probe_send", {"to": "a"}, {"session_id": "s1"})        # a deny is its own receipt
    r = outward_audit.audit(tasks=[])
    c = r["counts"]
    assert r["ok"], r
    assert c["decisions"] == 2 and c["closed"] == 2
    assert c["open"] == c["duplicate"] == c["duplicate_key"] == c["false_done"] == 0
    r = outward_audit.audit(now=time.time() + 2 * outward_audit.GRACE_S, tasks=[])
    assert r["ok"] and r["counts"]["open"] == 0


def test_an_action_with_no_terminal_receipt_is_open_after_the_grace_period():
    from agent_friday.services import outward_audit
    _decision("act_audit_open_1")
    r = outward_audit.audit(tasks=[])
    assert r["counts"]["in_flight"] == 1 and r["counts"]["open"] == 0
    r = outward_audit.audit(now=time.time() + 2 * outward_audit.GRACE_S, tasks=[])
    assert r["ok"] is False and r["counts"]["open"] == 1
    assert r["open"][0]["action_id"] == "act_audit_open_1"


def test_an_action_waiting_on_the_owner_is_not_open():
    from agent_friday.services import outward_audit
    _decision("act_audit_wait_1", decision="card", outcome=G.APPROVAL_REQUIRED)
    _decision("act_audit_wait_2")
    _outcome("act_audit_wait_2", G.APPROVAL_REQUIRED, status="held")
    r = outward_audit.audit(now=time.time() + 2 * outward_audit.GRACE_S, tasks=[])
    assert r["counts"]["waiting"] == 2 and r["counts"]["open"] == 0 and r["ok"]


def test_two_terminal_receipts_for_one_action_are_a_duplicate():
    from agent_friday.services import outward_audit
    _decision("act_audit_dup_1")
    _outcome("act_audit_dup_1", G.EXECUTED)
    _outcome("act_audit_dup_1", G.FAILED, status="error")
    r = outward_audit.audit(tasks=[])
    assert r["ok"] is False and r["counts"]["duplicate"] == 1
    assert r["duplicate"][0]["action_id"] == "act_audit_dup_1" and r["duplicate"][0]["terminal"] == 2
    from agent_friday.services import doctor
    state, code = doctor._c_outward_terminal()
    assert state == "unverified" and "duplicate:1" in code


def test_the_same_request_executed_twice_is_flagged_by_its_idempotency_key():
    from agent_friday.services import outward_audit
    for aid in ("act_audit_key_1", "act_audit_key_2"):
        _decision(aid, key="idk_same_request")
        _outcome(aid, G.EXECUTED)
    _decision("act_audit_key_3", key="idk_other_request")
    _outcome("act_audit_key_3", G.EXECUTED)
    r = outward_audit.audit(tasks=[])
    assert r["counts"]["duplicate_key"] == 1 and r["ok"] is False
    hit = r["duplicate_key"][0]
    assert hit["idempotency_key"] == "idk_same_request" and hit["count"] == 2


def test_a_task_marked_done_whose_outward_action_failed_is_a_false_done():
    from agent_friday.services import outward_audit
    _decision("act_audit_fd_1", task="t-false-done")
    _outcome("act_audit_fd_1", G.FAILED, status="error")
    _decision("act_audit_fd_2", task="t-really-done")
    _outcome("act_audit_fd_2", G.COMPLETED_VERIFIED)
    _decision("act_audit_fd_3", task="t-honest")
    _outcome("act_audit_fd_3", G.FAILED, status="error")
    tasks = [{"task_id": "t-false-done", "status": "complete"},
             {"task_id": "t-really-done", "status": "complete"},
             {"task_id": "t-honest", "status": "completed_unverified"}]
    r = outward_audit.audit(tasks=tasks)
    assert r["counts"]["false_done"] == 1 and r["ok"] is False
    fd = r["false_done"][0]
    assert fd["task_id"] == "t-false-done" and fd["actions"] == ["act_audit_fd_1"]
    assert fd["states"] == [G.FAILED]


def test_the_audit_route_reads_and_writes_nothing():
    from agent_friday import core
    from agent_friday.routes import ledger as ledger_routes
    _decision("act_audit_route_1")
    _outcome("act_audit_route_1", G.EXECUTED)
    before = L.summary().get("records")
    view = getattr(ledger_routes.api_ledger_audit, "__wrapped__", ledger_routes.api_ledger_audit)
    with core.app.test_request_context("/api/ledger/audit?days=7"):
        resp = view()
    d = resp.get_json()
    assert d["status"] == "ok" and d["audit"]["counts"]["closed"] >= 1
    assert d["audit"]["window_days"] == 7.0
    assert L.summary().get("records") == before
    html = (ROOT / "static" / "ledger.html").read_text(encoding="utf-8")
    assert "/api/ledger/audit" in html


# ── the paths outside a tool call now close their decision ──────────────────

def test_a_channel_send_closes_its_decision():
    from agent_friday.services.channels import base

    class Fake(base.ChannelAdapter):
        name = "fakechan"

        def _send_raw(self, chat_id, text):
            return {"ok": True}

        def _poll_once(self):
            pass
    a = Fake.__new__(Fake)
    a.name = "fakechan"
    a._last_error = None
    import agent_friday.services.channels.base as b
    real = b._gate_channel_text
    b._gate_channel_text = lambda text, ch: text
    try:
        assert a.send("123456789", "hello")["ok"]
    finally:
        b._gate_channel_text = real
    dec = _decisions("channel:fakechan:send")[-1]
    outs = _outcomes(dec["action_id"])
    assert [o["outcome"] for o in outs] == [G.EXECUTED]


def test_a_text_to_the_owner_closes_its_decision(phone_home, fake_twilio, monkeypatch):  # noqa: F811
    from agent_friday.phone import service
    verify_owner(monkeypatch)
    monkeypatch.setattr(service, "_gate_text", lambda t: t)
    service.text_owner("hi")
    dec = _decisions("phone:sms")[-1]
    outs = _outcomes(dec["action_id"])
    assert len(outs) == 1 and outs[0]["outcome"] in (G.EXECUTED, G.COMPLETED_VERIFIED)


def test_the_approval_executor_closes_its_decision(monkeypatch):
    import json as _json
    from agent_friday.services import approval_executor as ex
    calls = []

    def fake_exec(name, args, **kw):
        calls.append(kw.get("session_ctx") or {})
        return "created: ok"
    monkeypatch.setattr(ex, "_run_tool", fake_exec)
    monkeypatch.setattr(approvals, "_HOOKS", {ex.KIND: [ex._on_decision]})
    rec = approvals.create_approval(
        kind="tainted_action", subject_type="tool_action", subject_id="taint:test:probe",
        title="Create calendar event",
        action_description="create_calendar_event %s" % _json.dumps({"title": "Dinner"}),
        description="test", force_gate=True,
        payload={"tool": "create_calendar_event", "input": {"title": "Dinner"}},
        requested_by="taint_gate")
    approvals.decide(rec["approval_id"], "approve", decided_by="owner")
    assert len(calls) == 1
    ref = calls[0].get("approved_card_decision")
    assert isinstance(ref, dict) and ref.get("approval") == rec["approval_id"]
    outs = _outcomes(ref["action_id"])
    assert [o["outcome"] for o in outs] == [G.EXECUTED]


def test_the_tasks_inbox_closes_its_decision():
    from agent_friday.services import tasks_inbox as ti
    assert ti._checkpoint("tasks_inbox_post", None) is True
    ti._settle(False, "provider_refused")
    dec = _decisions("tasks_inbox_post")[-1]
    assert [o["outcome"] for o in _outcomes(dec["action_id"])] == [G.FAILED]


def test_a_request_settles_what_its_executor_left_open():
    from flask import Flask
    app = Flask("outcome-settle-test")
    with app.test_request_context("/x"):
        ref = G.record_external("probe:request", surface="test")
        assert G.settle_request_externals(True) == 1
        assert G.settle_request_externals(True) == 0
    assert [o["outcome"] for o in _outcomes(ref["action_id"])] == [G.EXECUTED]
    from agent_friday import core
    assert core._settle_external_actions in core.app.after_request_funcs[None]


def test_every_fixed_executor_closes_what_it_records():
    """The executors outside a tool call write a terminal outcome for the
    decision record_external returns."""
    src = ROOT / "src" / "agent_friday"
    for rel in ("services/gmail_send.py", "services/career_ops.py", "services/media_diet.py",
                "services/pdf_signing.py", "services/publish_web.py",
                "services/skill_import_review.py", "services/channels/base.py",
                "services/approval_executor.py", "phone/service.py", "routes/actions.py"):
        text = (src / rel).read_text(encoding="utf-8")
        assert "record_external(" in text and ("close_external(" in text or "_close(" in text), rel
