"""NS-33.6-3: the release gate checks the outward-action record.

A representative set of outward actions runs through the stubbed providers
(the fakes of the BD-11a and R2 tests: a channel adapter, Twilio, the approval
executor, the tasks inbox, a calendar write route, a domain change and a site
operation, a tool call, a refused action and an approved card carried out
again), and services/outward_audit shows nothing open and no request run
twice. scripts/release_checks.py runs the same audit on a ledger and exits
non-zero on open > 0 or duplicate_key > 0.

Synthetic data only; nothing reaches a network.
"""
from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

import pytest

from agent_friday.governance import action_gate as G
from agent_friday.services import action_ledger as L
from agent_friday.services import approvals
from agent_friday.services import outward_audit
from tests.unit.phone_fakes import fake_twilio, phone_home, verify_owner  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]


def _release_checks():
    spec = importlib.util.spec_from_file_location("release_checks", ROOT / "scripts" / "release_checks.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(G, "verify_claws", lambda *a, **k: (True, "intact"))
    monkeypatch.setattr(G, "outward_hold", lambda: None)
    monkeypatch.setattr(G, "_send_grant", lambda *a, **k: None)
    from agent_friday.services import idempotency_index
    idempotency_index.reset()
    outward_audit.reset_cache()
    yield
    idempotency_index.reset()
    outward_audit.reset_cache()


def _settled_audit():
    return outward_audit.audit(now=time.time() + 2 * outward_audit.GRACE_S, tasks=[])


def _fake_channel():
    from agent_friday.services.channels import base

    class Fake(base.ChannelAdapter):
        name = "relchan"

        def _send_raw(self, chat_id, text):
            return {"ok": True}

        def _poll_once(self):
            pass
    a = Fake.__new__(Fake)
    a.name = "relchan"
    a._last_error = None
    return a


def _representative_run(monkeypatch):
    """Each outward path once, through its stub, as the product carries it out."""
    import json as _json
    from agent_friday import core
    from agent_friday.phone import service
    from agent_friday.routes import calendar as cal
    from agent_friday.services import approval_executor as ex
    from agent_friday.services import domain_operations as dom
    from agent_friday.services import sites_operations as so
    from agent_friday.services import tasks_inbox as ti
    from agent_friday.services.channels import base

    # a channel reply
    monkeypatch.setattr(base, "_gate_channel_text", lambda text, ch: text)
    assert _fake_channel().send("123456789", "hello")["ok"]
    # a text to the owner (stub Twilio)
    verify_owner(monkeypatch)
    monkeypatch.setattr(service, "_gate_text", lambda t: t)
    service.text_owner("release check")
    # an approved card carried out by the executor, then the same card again
    monkeypatch.setattr(ex, "_run_tool", lambda name, args, **kw: "created: ok")
    monkeypatch.setattr(approvals, "_HOOKS", {ex.KIND: [ex._on_decision]})
    rec = approvals.create_approval(
        kind="tainted_action", subject_type="tool_action", subject_id="taint:release:probe",
        title="Create calendar event",
        action_description="create_calendar_event %s" % _json.dumps({"title": "Review"}),
        description="test", force_gate=True,
        payload={"tool": "create_calendar_event", "input": {"title": "Review"}},
        requested_by="taint_gate")
    approvals.decide(rec["approval_id"], "approve", decided_by="owner")
    with pytest.raises(G.AlreadyDone):
        G.record_external("create_calendar_event", surface="approval_card",
                          approval_id=rec["approval_id"], target="Review", once=True)
    # the tasks inbox (the provider refused it)
    assert ti._checkpoint("tasks_inbox_post", None) is True
    ti._settle(False, "provider_refused")
    # a calendar write from the owner's page
    with core.app.test_request_context("/api/calendar/events", method="POST"):
        assert cal._held_by_the_gate("calendar:create_event", "acct|primary Review", "Nothing was added") is None
        cal._close_gate(True, "created", verified=True)
    # a domain change and a site operation, closed from their reconcile
    dom._close_decision(G.record_external(dom.KIND, surface="approval_card", approval_id="ap-rel-dom"),
                        "provider_verified")
    so._close_decision(G.record_external(so.KIND, surface="approval_card", approval_id="ap-rel-site"),
                       {"status": "built"})
    # a tool call that ran, the same request asked again, and a refused one
    monkeypatch.setattr(G, "classify", lambda tool, args, ctx=None: (G.OUTWARD, "test rule"))
    monkeypatch.setattr(G, "_decide", lambda tool, klass, why, ctx, *a, **k: G.Verdict("allow", klass, "test"))
    ctx = {"is_background_task": True, "turn_id": "turn-release"}
    assert G.authorize("save_google_contact", {"to": "a"}, ctx).action == "allow"
    G.record_outcome("save_google_contact", G.LAST_RECEIPT.get(), "ok")
    assert G.authorize("save_google_contact", {"to": "a"}, ctx).action == "deny"
    monkeypatch.setattr(G, "_decide", lambda tool, klass, why, ctx, *a, **k: G.Verdict(
        "deny", klass, "not allowed: test"))
    assert G.authorize("probe_release_forbidden", {"to": "b"}, ctx).action == "deny"


def test_a_representative_run_leaves_nothing_open_and_nothing_run_twice(
        monkeypatch, phone_home, fake_twilio):  # noqa: F811
    _representative_run(monkeypatch)
    r = _settled_audit()
    c = r["counts"]
    assert c["decisions"] >= 9, r
    assert c["open"] == 0 and c["duplicate_key"] == 0, r
    assert c["duplicate"] == 0, r
    code, report = _release_checks().run(settled=True)
    assert code == 0, report
    assert report["ok"] and report["counts"]["open"] == 0 and report["counts"]["duplicate_key"] == 0


def _decision(aid, key=None):
    return L.append("decision", {"tool": "probe_send", "class": G.OUTWARD, "decision": "allow",
                                 "action_id": aid, "args_hash": "x",
                                 "envelope": {"action_id": aid, "idempotency_key": key}}, durable=True)


def _outcome(aid, word=G.EXECUTED):
    return L.append("outcome", {"tool": "probe_send", "action_id": aid, "status": "ok", "outcome": word,
                                "terminal": word in G.TERMINAL_OUTCOMES})


def test_the_release_check_refuses_an_open_action():
    rc = _release_checks()
    _decision("act_rel_open_1")
    code, report = rc.run(settled=True)
    assert code == 1 and report["reasons"] == ["open: 1"]
    assert report["open"][0]["action_id"] == "act_rel_open_1"
    code, _ = rc.run(settled=False)                 # still inside its grace period
    assert code == 0


def test_the_release_check_refuses_a_request_run_twice(capsys):
    rc = _release_checks()
    for aid in ("act_rel_key_1", "act_rel_key_2"):
        _decision(aid, key="idk_rel_same")
        _outcome(aid)
    code, report = rc.run(settled=True)
    assert code == 1 and report["reasons"] == ["duplicate_key: 1"]
    assert rc.main(["--settled"]) == 1
    assert "REFUSED, duplicate_key: 1" in capsys.readouterr().out
    assert rc.main(["--settled", "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["counts"]["duplicate_key"] == 1


def test_the_verdict_is_a_pure_function():
    rc = _release_checks()
    assert rc.verdict({"open": 0, "duplicate_key": 0, "duplicate": 3}) == (True, [])
    assert rc.verdict({"open": 0, "duplicate_key": 0, "duplicate": 3}, strict=True) == (False, ["duplicate: 3"])
    assert rc.verdict({"open": 2, "duplicate_key": 1}) == (False, ["open: 2", "duplicate_key: 1"])
    assert rc.verdict(None)[0] is False
    assert rc.main(["--days", "0"]) == 3


def test_a_ledger_that_cannot_be_checked_refuses(monkeypatch):
    rc = _release_checks()

    def no_key():
        raise L.LedgerError("the governance key is not available")
    monkeypatch.setattr(L, "_key", no_key)
    code, report = rc.run(settled=True)
    assert code == 3 and "cannot be checked" in report["error"]


def test_with_no_governance_key_it_refuses_and_creates_none(tmp_path, monkeypatch):
    """No key in the keychain or the key file: exit 3, and no key is minted
    (get_governance_key is never called, nothing is stored anywhere)."""
    import sys
    import types
    from agent_friday.governance import proof_of_integrity as poi
    rc = _release_checks()
    monkeypatch.delenv("FRIDAY_TRIAL", raising=False)
    key_file = tmp_path / "vault" / ".governance-key"
    monkeypatch.setattr(poi, "_GOV_KEY_FILE", key_file)
    stored = []
    kr = types.ModuleType("keyring")
    kr.get_password = lambda service, account: None
    kr.set_password = lambda service, account, value: stored.append(service)
    monkeypatch.setitem(sys.modules, "keyring", kr)
    minted = []

    def spy():
        minted.append(1)
        raise AssertionError("the release check must not ask for (or mint) the key")
    monkeypatch.setattr(poi, "get_governance_key", spy)
    code, report = rc.run(settled=True)
    assert code == 3 and "no governance key" in report["error"]
    assert minted == [] and stored == [] and not key_file.exists()


def test_a_ledger_signed_with_another_key_refuses(monkeypatch):
    """Records signed with a key this machine does not hold are not read by
    the audit, so the check would pass empty: it refuses instead (exit 3)."""
    import hashlib
    from agent_friday.services import doctor
    rc = _release_checks()
    _decision("act_rel_foreign_1")
    _outcome("act_rel_foreign_1")
    monkeypatch.setattr(doctor, "_governance_key_present", lambda: (True, "file"))
    other = hashlib.sha256(b"a different machine").digest()
    monkeypatch.setattr(L, "_key", lambda: other)
    L.reset_caches()
    code, report = rc.run(settled=True)
    assert code == 3 and "another key" in report["error"], report
