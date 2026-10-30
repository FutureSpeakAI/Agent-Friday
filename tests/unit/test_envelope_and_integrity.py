"""A8: the action envelope completed, idempotent execute, one integrity view.

  * NS-18.1-1/-2, NS-18.7-1: every decision receipt's envelope names its
    origin (chat, voice, task, schedule, channel, card, owner_route, route), its risk
    class (the gate's own classification) and a provenance summary (counts of
    owner, content, sender and model values; never a value);
  * NS-36.3-5: an outward action whose request (idempotency key) already ran
    is refused as already done (outcome denied, detail duplicate_key), not run
    again; a new card is a new key, so the owner can still re-do it;
  * NS-29.8-1: one view of the record (services/ledger_integrity) for the
    Doctor, the Ledger page and GET /api/ledger/integrity: signature health,
    chain breaks and gaps, unsigned lines, open/unconfirmed/duplicate outward
    actions and the policy version id. Counts only.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from agent_friday.governance import action_gate as G
from agent_friday.services import action_ledger as L
from agent_friday.services import approvals

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(G, "verify_claws", lambda *a, **k: (True, "intact"))
    monkeypatch.setattr(G, "outward_hold", lambda: None)
    monkeypatch.setattr(G, "_send_grant", lambda *a, **k: None)
    from agent_friday.services import idempotency_index, outward_audit
    idempotency_index.reset()
    outward_audit.reset_cache()
    yield
    idempotency_index.reset()
    outward_audit.reset_cache()


def _classify_as(monkeypatch, klass):
    monkeypatch.setattr(G, "classify", lambda tool, args, ctx=None: (klass, "test rule"))


def _allow_outward(monkeypatch):
    _classify_as(monkeypatch, G.OUTWARD)
    monkeypatch.setattr(G, "_decide", lambda tool, klass, why, ctx, *a, **k: G.Verdict("allow", klass, "test"))


def _decisions(tool):
    return [e["rec"] for e in L.entries(["decision"], newest_first=False)
            if e["rec"].get("tool") == tool]


def _outcomes(action_id):
    return [e["rec"] for e in L.entries(["outcome"], newest_first=False)
            if e["rec"].get("action_id") == action_id]


# ── 1. the envelope: origin, risk class, provenance ─────────────────────────

def test_the_envelope_has_the_new_fields_and_old_ones_stay():
    assert G.ENVELOPE_FIELDS[:9] == ("action_id", "principal", "tool", "args_hash", "idempotency_key",
                                     "verification_plan", "policy_version", "claws_state", "created_at")
    assert set(G.ENVELOPE_FIELDS) >= {"origin", "risk_class", "provenance_summary"}
    assert set(G.ORIGINS) == {"chat", "voice", "task", "schedule", "channel", "card", "owner_route",
                              "route", "other"}


@pytest.mark.parametrize("ctx, origin", [
    ({"session_id": "s1"}, "chat"),
    ({"session_id": "s1", "is_voice": True}, "voice"),
    ({"session_id": "s1", "surface": "voice"}, "voice"),
    ({"is_background_task": True, "task_id": "t-env"}, "task"),
    ({"is_background_task": True, "scheduled": True, "schedule_id": "job-1"}, "schedule"),
    ({"origin": "channel"}, "channel"),
    ({"origin": "phone", "authenticated": False}, "channel"),
    ({"approved_card": "ap-env-1"}, "card"),
    ({}, "other"),
])
def test_a_decision_receipt_names_its_origin_and_risk_class(monkeypatch, ctx, origin):
    _classify_as(monkeypatch, G.INTERNAL)
    G.authorize("probe_env", {"x": origin}, ctx,
                provenance={"user": 2, "own": 1, "content": 1, "sender": 0, "model": 3})
    rec = _decisions("probe_env")[-1]
    env = rec["envelope"]
    assert env["origin"] == origin
    assert env["risk_class"] == G.INTERNAL == rec["class"]
    assert env["provenance_summary"] == {"owner": 3, "content": 1, "sender": 0, "model": 3}
    assert G.verify_receipt(rec), "the new fields are signed with the receipt"


def test_without_a_provenance_check_the_summary_is_none_and_never_a_value(monkeypatch):
    _classify_as(monkeypatch, G.OUTWARD)
    G.authorize("probe_env_out", {"to": "carol@example.com"}, {"is_background_task": True})
    env = _decisions("probe_env_out")[-1]["envelope"]
    assert env["provenance_summary"] is None and env["risk_class"] == G.OUTWARD
    assert "carol@example.com" not in json.dumps(env)
    assert G.provenance_summary({"user": 1, "weird": 5, "content": -2}) == \
        {"owner": 1, "content": 0, "sender": 0, "model": 0}


def test_an_executor_outside_a_tool_call_names_its_origin():
    G.record_external("probe:env_card", surface="approval_card", approval_id="ap-env-2",
                      target="x", once=True)
    G.record_external("probe:env_chan", surface="channel")
    card = _decisions("probe:env_card")[-1]["envelope"]
    chan = _decisions("probe:env_chan")[-1]["envelope"]
    assert card["origin"] == "card" and card["risk_class"] == G.OUTWARD
    assert chan["origin"] == "channel" and chan["risk_class"] == G.OUTWARD


def test_owner_route_only_for_a_request_verified_as_the_owner():
    """owner_route only when check_auth passed the request as the owner (the
    user principal, signed in); any other request to a route is route."""
    from flask import g, session
    from agent_friday import core
    assert G.derive_origin(None, surface="receipts_undo") == "other"
    with core.app.test_request_context("/api/x", method="POST"):
        assert G.derive_origin(None, surface="receipts_undo") == "route", "not verified"
        g.friday_principal = "user"
        assert G.derive_origin(None, surface="receipts_undo") == "route", "not signed in"
        session["authenticated"] = True
        assert G.derive_origin(None, surface="receipts_undo") == "owner_route"
        g.friday_principal = "observer"
        assert G.derive_origin(None, surface="receipts_undo") == "route", "the observer is not the owner"
        assert G.derive_origin({"session_id": "s"}) == "chat"      # a chat request stays chat


def test_the_provenance_check_counts_origins_without_values():
    from agent_friday.services import taint
    key = "a8-provenance"
    taint.reset(key)
    taint.note_user_message(key, "please email dana@example.com about the lunch plan")
    d = taint.evaluate(key, "send_email", {"to": "dana@example.com", "subject": "Lunch",
                                           "body": "See you at noon."})
    assert d.origins.get("user", 0) >= 1
    assert all(isinstance(v, int) for v in d.origins.values())
    taint.reset(key)


def test_the_tool_call_path_hands_the_provenance_to_the_gate(monkeypatch):
    import agent_friday.services.agent as agent
    from agent_friday.services import taint
    agent._PENDING_CONFIRMATIONS.clear()
    taint.reset()
    monkeypatch.setattr(G, "_decide",
                        lambda tool, klass, why, ctx, *a, **k: G.Verdict("allow", klass, "test"))
    tool = "send_email" if "send_email" in agent.CLAUDE_TOOL_HANDLERS else next(
        n for n in sorted(agent.CLAUDE_TOOL_HANDLERS) if G.classify(n, {})[0] == G.OUTWARD)
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, tool, lambda inp: "sent")
    ctx = {"is_background_task": True, "task_id": "t-a8-prov"}
    agent._hooks.reset_rate_limiter()
    agent._call_verifier.reset(ctx)
    agent._execute_tool(tool, _args_for(agent, tool), session_ctx=ctx)
    rec = _decisions(tool)[-1]
    env = rec["envelope"]
    assert isinstance(env["provenance_summary"], dict)
    assert set(env["provenance_summary"]) == set(G.PROVENANCE_KEYS)
    assert all(isinstance(n, int) for n in env["provenance_summary"].values())
    assert env["origin"] == "task" and env["risk_class"] == rec["class"]
    taint.reset()


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


# ── 2. idempotent execute ───────────────────────────────────────────────────

def test_the_same_card_carried_out_twice_is_refused_as_already_done():
    from agent_friday.services import outward_audit
    first = G.record_external("probe:a8_send", surface="approval_card", approval_id="ap-a8-1",
                              target="x", once=True)
    assert G.close_external(first, True)
    with pytest.raises(G.AlreadyDone) as hit:
        G.record_external("probe:a8_send", surface="approval_card", approval_id="ap-a8-1",
                          target="x", once=True)
    assert isinstance(hit.value, G.Held), "every executor that handles Held stops before acting"
    assert hit.value.action_id == first["action_id"]
    assert "already done" in str(hit.value)
    refused = _decisions("probe:a8_send")[-1]
    assert refused["decision"] == "deny" and refused["outcome"] == G.DENIED
    assert refused["detail"] == "duplicate_key" and refused["already_done_action"] == first["action_id"]
    key = refused["envelope"]["idempotency_key"]
    ran = [d for d in _decisions("probe:a8_send") if (d.get("envelope") or {}).get("idempotency_key") == key
           and any(o["outcome"] == G.EXECUTED for o in _outcomes(d["action_id"]))]
    assert len(ran) == 1, "the ledger shows the action executed once"
    # a new card is a new key: the owner re-does it with a new card
    again = G.record_external("probe:a8_send", surface="approval_card", approval_id="ap-a8-2",
                              target="x", once=True)
    assert G.close_external(again, True)
    r = outward_audit.audit(now=time.time() + 2 * outward_audit.GRACE_S, tasks=[])
    assert r["counts"]["duplicate_key"] == 0 and r["counts"]["open"] == 0, r


def test_an_unconfirmed_or_failed_run_is_not_already_done():
    for word in (G.UNCONFIRMED, G.FAILED):
        aid = "ap-a8-%s" % word
        ref = G.record_external("probe:a8_retry", surface="approval_card", approval_id=aid,
                                target="y", once=True)
        G.close_external(ref, word != G.FAILED, outcome=word)
        retry = G.record_external("probe:a8_retry", surface="approval_card", approval_id=aid,
                                  target="y", once=True)
        assert retry["action_id"] != ref["action_id"]


def test_a_tool_call_whose_request_already_ran_is_refused(monkeypatch):
    _allow_outward(monkeypatch)
    ctx = {"is_background_task": True, "turn_id": "turn-a8-1"}
    v1 = G.authorize("create_task", {"to": "a"}, ctx, via="tool_call")
    assert v1.action == "allow"
    G.record_outcome("create_task", G.LAST_RECEIPT.get(), "ok")
    v2 = G.authorize("create_task", {"to": "a"}, ctx, via="tool_call")
    assert v2.action == "deny" and v2.outcome == G.DENIED
    assert v2.reason == G.ALREADY_DONE_REASON and v2.detail.get("duplicate_key") is True
    dec = _decisions("create_task")[-1]
    assert dec["detail"] == "duplicate_key" and dec["outcome"] == G.DENIED
    assert dec["envelope"]["verification_plan"]["outcome"] == "none"
    # another request runs: other arguments, another turn
    assert G.authorize("create_task", {"to": "b"}, ctx).action == "allow"
    assert G.authorize("create_task", {"to": "a"}, dict(ctx, turn_id="turn-a8-2")).action == "allow"


def test_a_key_scoped_by_a_session_or_schedule_never_refuses(monkeypatch):
    _allow_outward(monkeypatch)
    for ctx in ({"session_id": "s-a8"}, {"schedule_id": "job-a8", "scheduled": True}):
        assert G.authorize("hold_slots", {"to": "a"}, ctx).action == "allow"
        G.record_outcome("hold_slots", G.LAST_RECEIPT.get(), "ok")
        assert G.authorize("hold_slots", {"to": "a"}, ctx).action == "allow", ctx


def test_only_an_allow_is_refused_and_a_failure_can_be_retried(monkeypatch):
    _allow_outward(monkeypatch)
    ctx = {"is_background_task": True, "task_id": "t-a8-retry"}
    G.authorize("complete_task", {"to": "a"}, ctx)
    G.record_outcome("complete_task", G.LAST_RECEIPT.get(), "error")
    assert G.authorize("complete_task", {"to": "a"}, ctx).action == "allow"
    # an internal action is never refused by the index
    _classify_as(monkeypatch, G.INTERNAL)
    G.authorize("probe_a8_int", {"x": 1}, ctx)
    G.record_outcome("probe_a8_int", G.LAST_RECEIPT.get(), "ok")
    assert G.authorize("probe_a8_int", {"x": 1}, ctx).action == "allow"


def test_the_index_looks_back_thirty_days_and_reads_only_signed_lines():
    from agent_friday.services import idempotency_index as ix
    ref = G.record_external("probe:a8_window", surface="approval_card", approval_id="ap-a8-w",
                            target="z", once=True)
    G.close_external(ref, True)
    key = _decisions("probe:a8_window")[-1]["envelope"]["idempotency_key"]
    assert ix.done_action(key) == ref["action_id"]
    assert ix.done_action("idk_" + "0" * 24) is None and ix.done_action(None) is None
    assert ix.stats()["keys_done"] >= 1
    # a forged line names nothing
    from tests.ledger_support import forge
    forge("decision", {"tool": "probe:a8_forged", "class": "outward", "decision": "allow",
                       "action_id": "act_" + "f" * 20,
                       "envelope": {"action_id": "act_" + "f" * 20, "idempotency_key": "idk_forged"}})
    forge("outcome", {"tool": "probe:a8_forged", "action_id": "act_" + "f" * 20, "outcome": "executed"})
    ix.reset()
    assert ix.done_action("idk_forged") is None
    assert ix.done_action(key, now=time.time() + ix.WINDOW_S + 86400) is None


def _card_done(approval_id, target="t"):
    ref = G.record_external("probe:a8_ix", surface="approval_card", approval_id=approval_id,
                            target=target, once=True)
    G.close_external(ref, True)
    key = next(d for d in _decisions("probe:a8_ix") if d["action_id"] == ref["action_id"])
    return ref["action_id"], key["envelope"]["idempotency_key"]


def test_trailing_unsigned_lines_are_read_once_not_on_every_lookup(monkeypatch):
    from agent_friday.services import idempotency_index as ix
    aid, key = _card_done("ap-a8-ix-tail")
    ix.reset()
    assert ix.done_action(key) == aid                    # builds the index
    with open(L.live_path(), "a", encoding="utf-8") as f:
        for i in range(3):
            f.write(json.dumps({"v": 1, "seq": 10 ** 9 + i, "t": time.time(), "kind": "activity",
                                "rec": {}}) + "\n")
    reads = []
    real = L._reverse_lines
    monkeypatch.setattr(L, "_reverse_lines", lambda p: (reads.append(p), real(p))[1])
    for _ in range(3):
        assert ix.done_action(key) == aid
    assert reads == [], "no read from the newest end once the index is built"


def test_the_index_scan_is_capped_and_what_is_older_is_unknown(monkeypatch):
    from agent_friday.services import idempotency_index as ix
    aid, key = _card_done("ap-a8-ix-cap")
    ix.reset()
    monkeypatch.setattr(ix, "MAX_SCAN_LINES", 1)
    assert ix.done_action(key) is None, "beyond the cap: unknown, refuses nothing"
    assert ix.stats()["truncated"] is True
    monkeypatch.setattr(ix, "MAX_SCAN_LINES", 200_000)
    ix.reset()
    assert ix.done_action(key) == aid and ix.stats()["truncated"] is False


def test_a_ledger_replaced_in_place_rebuilds_the_index(monkeypatch, tmp_path):
    """The same ledger folder, another ledger in it (a restore): the index is
    read again, so a request done only in the old ledger refuses nothing."""
    import os
    import shutil
    from agent_friday.services import idempotency_index as ix
    here = L.ledger_dir()
    aid, key = _card_done("ap-a8-ix-old")
    assert ix.done_action(key) == aid
    old_seq = ix.stats()["seq"]
    # another ledger, written elsewhere, with more records than the old one
    other = tmp_path / "other-ledger"
    other.mkdir()
    monkeypatch.setenv("FRIDAY_LEDGER_DIR", str(other))
    L.reset_caches()
    for i in range(old_seq + 5):
        L.append("activity", {"n": i})
    new_aid = "act_a8_ix_new_1"
    L.append("decision", {"tool": "text_by_phone", "class": G.OUTWARD, "decision": "allow",
                          "action_id": new_aid, G.IDEMPOTENCY_SCOPE_FIELD: "client",
                          "envelope": {"action_id": new_aid, "idempotency_key": "idk_a8_ix_new"}},
             durable=True)
    L.append("outcome", {"tool": "text_by_phone", "action_id": new_aid, "status": "ok",
                         "outcome": G.EXECUTED, "terminal": True})
    # put it in place of the old one, under the old folder
    for p in list(here.iterdir()):
        shutil.rmtree(p) if p.is_dir() else os.remove(p)
    for p in other.iterdir():
        (shutil.copytree if p.is_dir() else shutil.copy2)(p, here / p.name)
    monkeypatch.setenv("FRIDAY_LEDGER_DIR", str(here))
    L.reset_caches()
    assert ix.done_action("idk_a8_ix_new") == new_aid
    assert ix.done_action(key) is None, "the old ledger's request is not in this one"


def test_the_index_is_warmed_once_at_boot():
    from agent_friday.services import idempotency_index as ix
    aid, key = _card_done("ap-a8-ix-warm")
    ix.reset()
    ix.warm()
    assert ix.stats()["seq"] > 0 and ix.stats()["keys_done"] >= 1
    src = (ROOT / "src" / "agent_friday" / "server.py").read_text(encoding="utf-8")
    assert "idempotency_index" in src and "warm_in_background()" in src


def test_a_refused_repeat_does_not_spend_a_grant_use(monkeypatch, tmp_path):
    """The duplicate check runs before a grant use is spent: the grant's
    count is the same after a refused repeat as before it."""
    monkeypatch.setattr(G, "_grants_file", lambda: tmp_path / "grants.json")
    _classify_as(monkeypatch, G.OUTWARD)
    g = G.create_grant(tools=["update_task"], scope="t-a8-grant", expires_in_seconds=600,
                       max_uses=3)
    ctx = {"is_background_task": True, "task_id": "t-a8-grant"}

    def uses_left():
        return next(x for x in G.list_grants() if x["grant_id"] == g["grant_id"])["uses_left"]

    v1 = G.authorize("update_task", {"to": "a"}, ctx, via="tool_call")
    assert v1.action == "allow" and (v1.grant or {}).get("grant_id") == g["grant_id"]
    G.record_outcome("update_task", G.LAST_RECEIPT.get(), "ok")
    assert uses_left() == 2
    v2 = G.authorize("update_task", {"to": "a"}, ctx, via="tool_call")
    assert v2.action == "deny" and v2.reason == G.ALREADY_DONE_REASON
    assert v2.detail.get("duplicate_key") is True and v2.grant is None
    assert uses_left() == 2, "a refused repeat spends no grant use"
    dec = _decisions("update_task")[-1]
    assert dec["detail"] == "duplicate_key" and dec["outcome"] == G.DENIED and dec["grant"] is None
    assert dec["envelope"]["verification_plan"]["outcome"] == "none"
    # another request under the same grant still runs and spends one
    assert G.authorize("update_task", {"to": "b"}, ctx).action == "allow"
    assert uses_left() == 1


def test_the_audit_ignores_keys_scoped_by_a_session_or_schedule(monkeypatch):
    """The outward audit's duplicate_key uses the gate's own scope rule: a
    message a session or a schedule rightly sends again is not a duplicate."""
    from agent_friday.services import outward_audit
    _allow_outward(monkeypatch)
    for ctx, scope in (({"session_id": "s-a8-audit"}, "session_id"),
                       ({"schedule_id": "job-a8-audit", "scheduled": True}, "schedule_id")):
        for _ in range(2):
            assert G.authorize("delete_task", {"to": "a"}, ctx).action == "allow"
            G.record_outcome("delete_task", G.LAST_RECEIPT.get(), "ok")
            assert _decisions("delete_task")[-1][G.IDEMPOTENCY_SCOPE_FIELD] == scope
    keys = {d["envelope"]["idempotency_key"] for d in _decisions("delete_task")}
    assert len(keys) == 2, "each scope ran its one request twice"
    r = outward_audit.audit(now=time.time() + 2 * outward_audit.GRACE_S, tasks=[])
    assert r["counts"]["duplicate_key"] == 0, r
    assert not G.recorded_key_names_one_request({G.IDEMPOTENCY_SCOPE_FIELD: "session_id"})
    assert not G.recorded_key_names_one_request({G.IDEMPOTENCY_SCOPE_FIELD: None})
    assert G.recorded_key_names_one_request({"tool": "text_by_phone", G.IDEMPOTENCY_SCOPE_FIELD: "task_id"})
    assert not G.recorded_key_names_one_request({"tool": "codebase_run", G.IDEMPOTENCY_SCOPE_FIELD: "task_id"})
    assert G.recorded_key_names_one_request({"tool": "codebase_run", G.IDEMPOTENCY_SCOPE_FIELD: "client"}), \
        "a card's own key names one card, whatever the tool"
    assert G.recorded_key_names_one_request({}), "a decision from before the field counts"


def test_the_audit_still_counts_a_one_request_key_run_twice():
    from agent_friday.services import outward_audit
    for scope, key, n in (("task_id", "idk_a8_task_twice", 1), ("schedule_id", "idk_a8_sched_twice", 0)):
        for i in range(2):
            aid = "act_a8_%s_%d" % (scope, i)
            L.append("decision", {"tool": "text_by_phone", "class": G.OUTWARD, "decision": "allow",
                                  "action_id": aid, G.IDEMPOTENCY_SCOPE_FIELD: scope,
                                  "envelope": {"action_id": aid, "idempotency_key": key}},
                     durable=True)
            L.append("outcome", {"tool": "text_by_phone", "action_id": aid, "status": "ok",
                                 "outcome": G.EXECUTED, "terminal": True})
        r = outward_audit.audit(now=time.time() + 2 * outward_audit.GRACE_S, tasks=[])
        assert sum(1 for d in r["duplicate_key"] if d["idempotency_key"] == key) == n, (scope, r)


@pytest.mark.parametrize("tool", ["codebase_run", "codebase_agent", "browser_click", "browser_type",
                                  "organize_files", "undo_action"])
def test_a_self_gated_tool_repeated_in_one_task_is_not_refused(monkeypatch, tool):
    """A codebase command, a browser step or an organize/undo is rightly run
    again with the same arguments in one task: only a one-shot send or write
    (ONE_SHOT_ACTIONS) is refused as already done, and the audit agrees."""
    from agent_friday.services import outward_audit
    _allow_outward(monkeypatch)
    assert tool in G.SELF_GATED and not G.is_one_shot(tool)
    ctx = {"is_background_task": True, "task_id": "t-a8-self-%s" % tool}
    for _ in range(2):
        v = G.authorize(tool, {"command": "pytest -q"}, ctx, via="tool_call")
        assert v.action == "allow", v.reason
        G.record_outcome(tool, G.LAST_RECEIPT.get(), "ok")
    r = outward_audit.audit(now=time.time() + 2 * outward_audit.GRACE_S, tasks=[])
    assert r["counts"]["duplicate_key"] == 0, r


@pytest.mark.parametrize("tool", ["text_by_phone", "book_slot", "create_calendar_event",
                                  "mcp_slack_post_message"])
def test_a_one_shot_send_repeated_in_one_task_is_refused(monkeypatch, tool):
    _allow_outward(monkeypatch)
    assert G.is_one_shot(tool)
    ctx = {"is_background_task": True, "task_id": "t-a8-once-%s" % tool}
    assert G.authorize(tool, {"to": "a"}, ctx, via="tool_call").action == "allow"
    G.record_outcome(tool, G.LAST_RECEIPT.get(), "ok")
    v = G.authorize(tool, {"to": "a"}, ctx, via="tool_call")
    assert v.action == "deny" and v.reason == G.ALREADY_DONE_REASON


@pytest.mark.parametrize("status, word", [("waiting", G.WAITING), ("refused", G.DENIED),
                                          ("denied", G.DENIED), ("error", G.FAILED),
                                          ("failed", G.FAILED)])
def test_a_handler_dict_that_did_not_run_is_not_executed_and_a_retry_runs(monkeypatch, status, word):
    """A handler's {"status": "waiting"} (or refused, denied, error, failed)
    from an outward tool that is not one-shot is recorded as that outcome,
    never executed, and a retry in the same task runs. (A one-shot tool's own
    dict answer is never downgraded: see the test below.)"""
    import agent_friday.services.agent as agent
    from agent_friday.services import taint
    agent._PENDING_CONFIRMATIONS.clear()
    taint.reset()
    monkeypatch.setattr(G, "_decide",
                        lambda tool, klass, why, ctx, *a, **k: G.Verdict("allow", klass, "test"))
    tool = "save_google_contact"
    monkeypatch.setattr(G, "ONE_SHOT_ACTIONS", G.ONE_SHOT_ACTIONS - {tool})
    assert not G.is_one_shot(tool)
    calls = []

    def handler(inp):
        calls.append(inp)
        return {"status": status, "say": "not yet"}
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, tool, handler)
    ctx = {"is_background_task": True, "task_id": "t-a8-dict-%s" % status}
    args = _args_for(agent, tool)
    for _ in range(2):
        agent._hooks.reset_rate_limiter()
        agent._call_verifier.reset(ctx)
        agent._execute_tool(tool, dict(args), session_ctx=ctx)
    assert len(calls) == 2, "the retry ran: the first was not recorded as done"
    decs = [d for d in _decisions(tool) if (d.get("envelope") or {}).get("origin") == "task"]
    assert decs[-1]["decision"] == "allow"
    words = [o["outcome"] for d in decs[-2:] for o in _outcomes(d["action_id"])]
    assert words == [word, word], words
    assert G.EXECUTED not in words
    taint.reset()


@pytest.mark.parametrize("status", ["queued", "waiting", "failed"])
def test_a_one_shot_tools_own_dict_answer_is_not_downgraded(monkeypatch, status):
    """A one-shot send whose handler answers {"status": "queued"} (or any other
    status) has gone: it is recorded as executed, and the same request asked
    again in the task is refused as already done, never sent twice."""
    import agent_friday.services.agent as agent
    from agent_friday.services import taint
    agent._PENDING_CONFIRMATIONS.clear()
    taint.reset()
    monkeypatch.setattr(G, "_decide",
                        lambda tool, klass, why, ctx, *a, **k: G.Verdict("allow", klass, "test"))
    tool = "save_google_contact"
    assert G.is_one_shot(tool)
    assert agent._dict_outcome(tool, {"status": status}) is None
    assert agent._dict_outcome("probe_not_one_shot", {"status": "queued"}) is None, \
        "queued is not a waiting status"
    calls = []

    def handler(inp):
        calls.append(inp)
        return {"status": status}
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, tool, handler)
    ctx = {"is_background_task": True, "task_id": "t-a8-oneshot-dict-%s" % status}
    args = _args_for(agent, tool)
    for _ in range(2):
        agent._hooks.reset_rate_limiter()
        agent._call_verifier.reset(ctx)
        agent._execute_tool(tool, dict(args), session_ctx=ctx)
    assert len(calls) == 1, "the repeat was refused as already done"
    decs = [d for d in _decisions(tool) if (d.get("envelope") or {}).get("origin") == "task"]
    words = [o["outcome"] for o in _outcomes(decs[0]["action_id"])]
    assert words and G.WAITING not in words and G.FAILED not in words, words
    assert decs[-1]["decision"] == "deny" and decs[-1]["detail"] == "duplicate_key"
    taint.reset()


def test_a_browser_connector_tool_repeated_in_one_task_is_not_refused(monkeypatch):
    """A browser or UI-automation connector tool (a click, a keypress) is not
    one-shot: the same click twice in one task runs twice."""
    _allow_outward(monkeypatch)
    for t in ("mcp_playwright_browser_click", "mcp_playwright_browser_type",
              "mcp_puppeteer_navigate", "mcp_playwright_browser_file_upload",
              "mcp_github_list_issues"):
        assert not G.is_one_shot(t), t
    tool = "mcp_playwright_browser_click"
    ctx = {"is_background_task": True, "task_id": "t-a8-browser-click"}
    for _ in range(2):
        v = G.authorize(tool, {"ref": "e12"}, ctx, via="tool_call")
        assert v.action == "allow", v.reason
        G.record_outcome(tool, G.LAST_RECEIPT.get(), "ok")


def test_a_connector_send_repeated_in_one_task_is_refused(monkeypatch):
    _allow_outward(monkeypatch)
    for t in ("mcp_slack_send_message", "mcp_slack_sendMessage", "mcp_gmail_reply",
              "mcp_calendar_create_event", "mcp_x_publish_post"):
        assert G.is_one_shot(t), t
    tool = "mcp_slack_send_message"
    ctx = {"is_background_task": True, "task_id": "t-a8-slack-send"}
    assert G.authorize(tool, {"to": "a"}, ctx, via="tool_call").action == "allow"
    G.record_outcome(tool, G.LAST_RECEIPT.get(), "ok")
    v = G.authorize(tool, {"to": "a"}, ctx, via="tool_call")
    assert v.action == "deny" and v.reason == G.ALREADY_DONE_REASON


# ── 5. one integrity view ───────────────────────────────────────────────────

def _clean_run():
    ref = G.record_external("probe:a8_integrity", surface="test")
    G.close_external(ref, True)
    return ref


def test_the_integrity_view_of_a_clean_record():
    from agent_friday.services import doctor, ledger_integrity, policy_version
    _clean_run()
    v = ledger_integrity.integrity()
    assert v["ok"], v
    assert v["signatures"]["ok"] and v["signatures"]["bad"] == 0
    assert v["chain"]["breaks"] == v["chain"]["gaps"] == v["chain"]["open_problems"] == 0
    assert v["unsigned"]["lines"] == 0
    assert v["outward"]["open"] == v["outward"]["duplicate_key"] == v["outward"]["duplicate"] == 0
    assert v["policy_version"] == policy_version.current_id()
    assert "probe:a8_integrity" not in json.dumps(v), "counts only"
    state, code = doctor._c_record_integrity()
    assert state == "healthy", code
    assert code.startswith("sig_bad:0,breaks:0,gaps:0,unsigned:0,open:0") and ",pv:" in code
    assert ("record_integrity", "receipt chains", doctor._c_record_integrity) in doctor.CHECKS


def test_the_integrity_view_counts_a_request_run_twice():
    from agent_friday.services import doctor, ledger_integrity
    for aid in ("act_a8_int_k1", "act_a8_int_k2"):
        L.append("decision", {"tool": "probe_send", "class": G.OUTWARD, "decision": "allow",
                              "action_id": aid, "envelope": {"action_id": aid,
                                                             "idempotency_key": "idk_a8_same"}},
                 durable=True)
        L.append("outcome", {"tool": "probe_send", "action_id": aid, "status": "ok",
                             "outcome": G.EXECUTED, "terminal": True})
    v = ledger_integrity.integrity()
    assert v["outward"]["duplicate_key"] == 1 and v["ok"] is False
    state, code = doctor._c_record_integrity()
    assert state == "unverified" and "duplicate_key:1" in code


def test_a_changed_line_and_an_unsigned_line_are_counted():
    from agent_friday.services import doctor, ledger_integrity
    from tests.ledger_support import tamper
    _clean_run()
    tamper("decision", lambda r: r.get("tool") == "probe:a8_integrity", lambda r: r.update(tool="x"))
    v = ledger_integrity.integrity()
    assert v["signatures"]["bad"] >= 1 and v["ok"] is False
    assert doctor._c_record_integrity()[0] == "broken"
    with open(L.live_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps({"v": 1, "seq": 10 ** 9, "t": time.time(), "kind": "activity",
                            "rec": {}}) + "\n")
    L.reset_caches()
    assert ledger_integrity.integrity()["unsigned"]["lines"] == 1


def test_the_integrity_route_is_owner_only_and_reads_nothing_it_writes():
    from agent_friday import core
    from agent_friday.routes import ledger as ledger_routes
    from agent_friday.services import observer_access
    _clean_run()
    before = L.summary().get("records")
    route = ledger_routes.api_ledger_integrity
    assert hasattr(route, "__wrapped__"), "login_required, as /api/ledger/audit"
    assert not any("/api/ledger/integrity".startswith(p) for p in observer_access.READ_ONLY_PREFIXES)
    with core.app.test_request_context("/api/ledger/integrity?days=7"):
        resp = route.__wrapped__()
    d = resp.get_json()
    assert d["status"] == "ok" and d["integrity"]["window_days"] == 7.0
    assert {"signatures", "chain", "unsigned", "outward", "policy_version"} <= set(d["integrity"])
    assert L.summary().get("records") == before
    assert any(r.rule == "/api/ledger/integrity" for r in core.app.url_map.iter_rules())
    html = (ROOT / "static" / "ledger.html").read_text(encoding="utf-8")
    assert "/api/ledger/integrity" in html and "/api/ledger/audit" in html
