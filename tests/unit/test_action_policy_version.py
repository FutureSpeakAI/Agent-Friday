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
import threading
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
        "policy_version", "claws_state", "created_at", "origin", "risk_class", "provenance_summary"}
    assert env["origin"] == "task" and env["risk_class"] == rec["class"]
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


# ── review fixes ───────────────────────────────────────────────────────────

def test_a_broken_policy_version_never_holds_a_receipt(home, monkeypatch):
    from agent_friday.governance import action_gate as ag
    from agent_friday.services import policy_version as pv

    def boom(*a, **k):
        raise RuntimeError("policy version broken")
    monkeypatch.setattr(pv, "current", boom)
    out = ag._receipt({"tool": "probe_broken", "class": "outward", "decision": "allow"})
    rec = out["rec"]
    assert re.fullmatch(r"act_[0-9a-f]{20}", rec["action_id"])
    assert rec["policy_version"] == ag.PV_UNAVAILABLE == "pv_unavailable"
    assert rec["envelope"]["action_id"] == rec["action_id"]
    assert set(rec["envelope"]) == set(ag.ENVELOPE_FIELDS)
    assert rec["envelope"]["claws_state"]["status"] == "unavailable"
    assert "pv" not in out, "a version that is not an id is not stamped"
    assert L.entries([pv.KIND]) == [], "nothing to record"
    assert ag.verify_receipt(ag.receipts()[-1])
    # an action id the entry already names is kept
    kept = ag._receipt({"tool": "probe_broken", "class": "outward", "decision": "allow",
                        "action_id": "act_" + "1" * 20})["rec"]
    assert kept["action_id"] == "act_" + "1" * 20
    # and the gate is not held by it
    v = ag.authorize("read_file", {"path": "x.txt"}, {"is_background_task": True})
    assert "signed receipt could not be written" not in (v.reason or "")
    assert L.entries(["decision"])[0]["rec"]["policy_version"] == "pv_unavailable"


def test_one_policy_version_read_per_receipt(home, monkeypatch):
    from agent_friday.governance import action_gate as ag
    from agent_friday.services import policy_version as pv
    real, calls = pv.current, []

    def counted():
        calls.append(1)
        return real()
    monkeypatch.setattr(pv, "current", counted)
    out = ag._receipt({"tool": "probe_once", "class": "outward", "decision": "allow"})
    assert len(calls) == 1
    assert out["rec"]["policy_version"] == out["pv"] == pv.history()[0]["id"]
    calls.clear()
    tok = ag._DECISION_PV.set(real())                     # authorize passes its read through
    try:
        ag._receipt({"tool": "probe_once", "class": "outward", "decision": "allow"})
    finally:
        ag._DECISION_PV.reset(tok)
    assert calls == []


def test_concurrent_first_decisions_record_the_version_once(home):
    from agent_friday.governance import action_gate as ag
    from agent_friday.services import policy_version as pv
    n = 8
    ag._governance_key()                                  # the keys exist before the race
    L._key()
    gate = threading.Barrier(n)
    errors = []

    def one(i):
        try:
            gate.wait(5)
            ag._receipt({"tool": "probe_race_%d" % i, "class": "outward", "decision": "allow"})
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    ts = [threading.Thread(target=one, args=(i,)) for i in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    assert errors == []
    assert len(L.entries([pv.KIND])) == 1
    assert len(L.entries(["decision"])) == n
    # a fresh process (memory cleared) finds it in the live file, no second entry
    pv._RECORDED.clear()
    assert pv.ensure_recorded() is False
    assert len(L.entries([pv.KIND])) == 1


def test_the_claws_status_is_cached_by_the_pin_file(home, monkeypatch):
    from agent_friday.governance import action_gate as ag
    from agent_friday.services import policy_version as pv
    assert ag.verify_claws()[0]
    key = ag._governance_key()
    real, calls = pv._claws_status, []

    def counted(k):
        calls.append(1)
        return real(k)
    monkeypatch.setattr(pv, "_claws_status", counted)
    assert pv.claws_state(key)["status"] == "intact"
    assert pv.claws_state(key)["status"] == "intact"
    assert len(calls) == 1
    pin = home / "governance" / "claws.pin.json"
    pin.write_text(pin.read_text(encoding="utf-8") + "\n", encoding="utf-8")   # size changes
    pv.claws_state(key)
    assert len(calls) == 2
    pin.unlink()
    assert pv.claws_state(key)["status"] == "unpinned"


def test_the_ledger_stamps_only_a_well_formed_version(home):
    from agent_friday.services import policy_version as pv
    bad = L.append("activity", {"kind": "tool_call", "policy_version": "not a version"})
    assert bad["pv"] == pv.current_id()
    good_id = "pv_" + "0" * 16
    good = L.append("activity", {"kind": "tool_call", "policy_version": good_id})
    assert good["pv"] == good_id
    assert L.verify()["ok"]


def test_a_shipped_default_change_is_a_new_version(home, monkeypatch):
    from agent_friday import core
    from agent_friday.services import policy_version as pv
    before = pv.current()
    monkeypatch.setitem(core.DEFAULT_SETTINGS, "outward_reads", "default-" + MARK)
    pv.reset_for_tests()
    after = pv.current()
    assert [p for p in pv.PARTS if after["parts"][p] != before["parts"][p]] == ["constitution"]
    assert MARK not in json.dumps(after)


@pytest.mark.parametrize("name", ["web_sites.json", "mcp_hosts.json"])
def test_the_owner_allow_lists_are_grants_policy(home, name):
    from agent_friday.services import policy_version as pv
    before = pv.current()
    (home / "governance" / name).write_text(
        json.dumps({"example.com": {"level": "may_read", "made_by": "owner:ui", "sig": "x"}}),
        encoding="utf-8")
    after = pv.current()
    assert [p for p in pv.PARTS if after["parts"][p] != before["parts"][p]] == ["grants_policy"]


def test_unreadable_or_unparsable_inputs_give_a_distinct_stable_id(home):
    from agent_friday.services import policy_version as pv
    absent = pv.current_id()
    grants = home / "governance" / "grants.json"
    grants.write_text("{not json", encoding="utf-8")
    unparsable = pv.current_id()
    pv.reset_for_tests()
    assert pv.current_id() == unparsable, "stable"
    grants.unlink()
    grants.mkdir()                                        # cannot be read as a file
    unreadable = pv.current_id()
    pv.reset_for_tests()
    assert pv.current_id() == unreadable, "stable"
    assert len({absent, unparsable, unreadable}) == 3
    assert all(pv.is_id(x) for x in (absent, unparsable, unreadable))
    (home / "settings.json").write_text("[[[", encoding="utf-8")   # never raises
    assert pv.is_id(pv.current_id())


def test_an_off_record_envelope_holds_no_argument_hash_or_key(home, monkeypatch):
    from agent_friday.governance import action_gate as ag
    from agent_friday.services import off_record
    env = ag.build_envelope("send_email", args_hash="ab" * 32, ctx={"turn_id": "t-off"},
                            klass=ag.OUTWARD, decision="card").to_dict()
    assert env["args_hash"] and env["idempotency_key"]
    monkeypatch.setattr(off_record, "active", lambda *a, **k: True)
    rec = ag._receipt({"tool": "send_email", "class": "outward", "decision": "card",
                       "args_hash": "ab" * 32, "envelope": env,
                       "action_id": env["action_id"]})["rec"]
    assert rec["off_record"] is True and "args_hash" not in rec
    assert rec["envelope"]["args_hash"] is None and rec["envelope"]["idempotency_key"] is None
    assert rec["envelope"]["action_id"] == rec["action_id"] == env["action_id"]
    assert "ab" * 32 not in json.dumps(rec)


def _legacy_append(kind, rec):
    """A line as written before policy versions and action ids: no `pv`, and
    a receipt with no action id (straight through the writer, as then)."""
    key = L._key()
    with L._TLOCK:
        L._check_dir()
        L._ensure_migrated()
        return L._guarded_append(key, [(kind, rec)], False)


def test_a_ledger_written_before_policy_versions_still_verifies(home):
    from agent_friday.governance import action_gate as ag
    old = [_legacy_append("decision", {"tool": "send_email", "decision": "allow"}),
           _legacy_append("outcome", {"tool": "send_email", "status": "ok"}),
           _legacy_append("activity", {"kind": "tool_call", "tool": "x"})]
    assert all("pv" not in e for e in old)
    assert L.verify()["ok"], "old lines alone"
    ag._receipt({"tool": "probe_new", "class": "outward", "decision": "allow"})
    L.append("activity", {"kind": "tool_call", "tool": "y"})
    rows = L.entries(newest_first=False)
    assert [("pv" in e) for e in rows] == [False, False, False, True, True, True]
    assert L.verify()["ok"], "old and new lines together"
