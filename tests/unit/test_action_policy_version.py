"""Every action, grant, approval, model route and receipt names the governing
policy version, and every receipt names its action (BD-11a).

NS-30.19-1: "Every action, grant, approval, model route, receipt references
governing policy version." NS-36.3-1: one action envelope (action id,
principal, idempotency key, verification plan, policy version, cLaws state).
The burn-down's exit test: a receipt without an action id cannot be written.

Recording only: nothing here changes what an action may do.
"""
from __future__ import annotations

import json
import re
import sys
import uuid

import pytest

from agent_friday.governance import proof_of_integrity as poi
from agent_friday.services import action_ledger as L

MARK = "pv-marker-" + uuid.uuid4().hex[:8]
HEX64 = re.compile(r"[0-9a-f]{64}")


def _settings(home, **over):
    s = {"theme": "dark", "outward_reads": "observe", "email_cloud_release": MARK,
         "model_routing": {"vault_local_only": True, "vault_cloud_fallback": "redact"},
         "approvals_policy": {"outward": {"gated": True, "expires_seconds": 86400}}}
    s.update(over)
    (home / "settings.json").write_text(json.dumps(s), encoding="utf-8")


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / "governance").mkdir(parents=True)
    monkeypatch.setenv("FRIDAY_HOME", str(h))
    monkeypatch.setattr(poi, "_GOV_KEY_FILE", tmp_path / "gk")
    monkeypatch.setitem(sys.modules, "keyring", None)
    _settings(h)
    from agent_friday.services import policy_version as pv
    pv.reset_for_tests()
    L.reset_caches()
    yield h
    pv.reset_for_tests()


def test_the_version_is_stable_for_the_same_inputs_and_holds_no_content(home):
    from agent_friday.services import policy_version as pv
    a, b = pv.current(), pv.current()
    assert a == b and pv.is_id(a["id"])
    assert set(a["parts"]) == set(pv.PARTS)
    assert all(HEX64.fullmatch(h) for h in a["parts"].values())
    pv.reset_for_tests()                                  # a fresh process gives the same
    assert pv.current() == a
    assert MARK not in json.dumps(a), "hashes only, never a setting's value"
    _settings(home, theme="light")                        # not a governing input
    assert pv.current_id() == a["id"]


def test_a_governing_change_is_a_new_version_recorded_once(home):
    from agent_friday.services import policy_version as pv
    first = pv.current()
    assert pv.ensure_recorded() is True
    assert pv.ensure_recorded() is False                  # once
    assert [h["id"] for h in pv.history()] == [first["id"]]
    assert pv.history()[0]["parts"] == first["parts"]

    _settings(home, outward_reads="card")                 # what runs without asking
    second = pv.current()
    assert second["id"] != first["id"]
    assert [p for p in pv.PARTS if second["parts"][p] != first["parts"][p]] == ["autonomy"]
    assert pv.ensure_recorded() is True
    assert pv.ensure_recorded() is False
    assert [h["id"] for h in pv.history()] == [second["id"], first["id"]]
    assert len(L.entries([pv.KIND])) == 2

    _settings(home, outward_reads="card", email_cloud_release="never")   # what may leave
    third = pv.current()
    assert [p for p in pv.PARTS if third["parts"][p] != second["parts"][p]] == ["privacy"]


def test_a_receipt_without_an_action_id_cannot_be_written(home):
    with pytest.raises(L.LedgerError):
        L.append("decision", {"tool": "send_email", "class": "outward", "decision": "allow"})
    with pytest.raises(L.LedgerError):
        L.append("outcome", {"tool": "send_email", "status": "ok"})
    with pytest.raises(L.LedgerError):
        L.append("decision", {"tool": "send_email", "decision": "allow", "action_id": "  "})
    assert L.try_append("outcome", {"tool": "send_email", "status": "ok"}) is None
    assert L.entries(["decision", "outcome"]) == []
    ok = L.append("decision", {"tool": "send_email", "decision": "allow", "action_id": "act_test_1"})
    assert ok["rec"]["action_id"] == "act_test_1"
    other = L.append("activity", {"kind": "tool_call", "tool": "x"})   # not a receipt
    assert other["kind"] == "activity"


def test_every_ledger_entry_carries_the_version_it_was_written_under(home):
    from agent_friday.services import policy_version as pv
    e = L.append("activity", {"kind": "tool_call", "tool": "x"})
    assert e["pv"] == pv.current_id()
    assert L.verify()["ok"], "the stamp is part of the signed line"


def test_decision_and_outcome_receipts_carry_the_envelope(home):
    from agent_friday.governance import action_gate as ag
    from agent_friday.services import policy_version as pv
    assert ag.verify_claws()[0]                           # pinned in this home
    ag.authorize("create_calendar_event", {"title": "lunch with " + MARK},
                 {"is_background_task": True, "turn_id": "turn-pv"}, via="tool_call")
    rec = ag.receipts()[-1]
    assert rec["tool"] == "create_calendar_event"
    assert pv.valid_action_id(rec["action_id"]) and pv.is_id(rec["policy_version"])
    env = rec["envelope"]
    assert set(env) == set(ag.ENVELOPE_FIELDS) == {
        "action_id", "principal", "tool", "args_hash", "idempotency_key", "verification_plan",
        "policy_version", "claws_state", "created_at"}
    assert env["action_id"] == rec["action_id"] and env["policy_version"] == rec["policy_version"]
    assert env["principal"] == L.PRINCIPAL and env["tool"] == "create_calendar_event"
    assert env["args_hash"] == rec["args_hash"]
    assert str(env["idempotency_key"]).startswith("idk_")
    assert env["claws_state"]["status"] == "intact" and HEX64.fullmatch(env["claws_state"]["hash"])
    assert "outcome_receipt" == env["verification_plan"]["outcome"] or rec["decision"] == "deny"
    assert MARK not in json.dumps(rec), "ids, labels and hashes only"
    assert ag.verify_receipt(rec), "the envelope is signed with the receipt"
    # the ledger line names the same version, and that version is on record
    line = L.entries(["decision"])[0]
    assert line["pv"] == rec["policy_version"]
    assert pv.history()[0]["id"] == rec["policy_version"]
    # the outcome names the decision's action
    last = ag.LAST_RECEIPT.get()
    assert last["action_id"] == rec["action_id"]
    ag.record_outcome("create_calendar_event", last, "ok")
    out = L.entries(["outcome"])[0]
    assert out["rec"]["action_id"] == rec["action_id"] and "action_id_origin" not in out["rec"]
    assert out["rec"]["policy_version"] == rec["policy_version"] == out["pv"]
    # an outcome with no decision to name gets an action of its own, marked so
    ag.record_outcome("create_calendar_event", None, "ok", approval="ap-pv")
    lone = L.entries(["outcome"])[0]["rec"]
    assert pv.valid_action_id(lone["action_id"]) and lone["action_id_origin"] == "outcome"


def test_every_direct_decision_receipt_gets_an_envelope(home):
    from agent_friday.governance import action_gate as ag
    from agent_friday.services import policy_version as pv
    out = ag._receipt({"tool": "probe_direct", "class": "outward", "decision": "allow"})
    assert pv.valid_action_id(out["rec"]["action_id"])
    assert set(out["rec"]["envelope"]) == set(ag.ENVELOPE_FIELDS)
    assert out["pv"] == out["rec"]["policy_version"]


def test_the_same_request_has_one_idempotency_key_and_each_action_its_own_id(home):
    from agent_friday.governance import action_gate as ag
    ctx = {"turn_id": "t-1"}
    a = ag.build_envelope("send_email", args_hash="ab" * 32, ctx=ctx, klass=ag.OUTWARD,
                          decision="card")
    b = ag.build_envelope("send_email", args_hash="ab" * 32, ctx=ctx, klass=ag.OUTWARD,
                          decision="card")
    c = ag.build_envelope("send_email", args_hash="cd" * 32, ctx=ctx, klass=ag.OUTWARD,
                          decision="card")
    assert a.idempotency_key == b.idempotency_key != c.idempotency_key
    assert a.action_id != b.action_id
    assert set(a.to_dict()) == set(ag.ENVELOPE_FIELDS)
    supplied = ag.build_envelope("send_email", ctx={"idempotency_key": "client-key-12345"})
    assert supplied.idempotency_key.startswith("idk_") and "client-key" not in supplied.idempotency_key


def test_an_approval_card_names_the_version_it_was_raised_and_decided_under(home):
    from agent_friday.services import approvals
    from agent_friday.services import policy_version as pv
    card = approvals.create_approval(kind="send_email", subject_type="test_policy_version",
                                     subject_id=uuid.uuid4().hex, title="Email the team",
                                     action_description="send an email", force_gate=True)
    assert pv.is_id(card["policy_version"])
    done = approvals.decide(card["approval_id"], "approve")
    assert done["status"] == "approved" and pv.is_id(done["decided_policy_version"])
    assert approvals.get_approval(card["approval_id"])["decided_policy_version"] == done[
        "decided_policy_version"]


def test_a_grant_names_the_version_it_was_issued_and_used_under(home):
    from agent_friday.governance import action_gate as ag
    from agent_friday.services import policy_version as pv
    g = ag.create_grant(tools=["send_email"], scope="job-pv", expires_in_seconds=600, max_uses=2)
    assert pv.is_id(g["policy_version"])
    issued = pv.current_id()                              # a new grant is itself a change
    assert issued != g["policy_version"]
    used = ag.consume_grant("send_email", "job-pv")
    assert used and pv.is_id(used["used_policy_version"])
    assert pv.current_id() == issued, "spending a use is not a new policy"


def test_a_pre_approved_send_log_row_names_the_version(home):
    from agent_friday.governance import send_grants as sg
    from agent_friday.services import policy_version as pv
    sg._log("probe", grant_id="sgr_probe", by="owner")
    row = L.entries(["send_grant"])[0]
    assert pv.is_id(row["rec"]["policy_version"]) and row["pv"] == row["rec"]["policy_version"]


def test_a_model_route_names_the_version(home):
    from agent_friday.routing.model_router import ModelRouter
    from agent_friday.services import policy_version as pv
    r = ModelRouter()._finalize({"provider": "local", "model": "llama3"})
    assert r["policy_version"] == pv.current_id()


def test_the_version_is_recorded_before_the_first_receipt_that_names_it(home):
    from agent_friday.governance import action_gate as ag
    from agent_friday.services import policy_version as pv
    ag._receipt({"tool": "probe_one", "class": "outward", "decision": "allow"})
    ag._receipt({"tool": "probe_two", "class": "outward", "decision": "allow"})
    kinds = [e["kind"] for e in L.entries(newest_first=False)]
    assert kinds == [pv.KIND, "decision", "decision"]
