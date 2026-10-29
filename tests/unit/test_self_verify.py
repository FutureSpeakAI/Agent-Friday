"""Friday re-checks her own safeguards at start and every few hours (north
star §26.20-1): the receipt chain, the signed rules, the egress gate, the
action gate, the credential store, the vault's settings and the authority
switch. A problem is said through the data-integrity path, each sweep is in
the action ledger, and what is not covered yet is named."""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _sv():
    assert importlib.util.find_spec("agent_friday.services.self_verify") is not None, \
        "Friday never re-checks her own safeguards"
    return importlib.import_module("agent_friday.services.self_verify")


@pytest.fixture
def healthy(monkeypatch):
    sv = _sv()
    fns = {cid: (lambda: ("healthy", "ok")) for cid, _f, _s in sv.CHECKS}
    monkeypatch.setattr(sv, "CHECKS", tuple((cid, fns[cid], said) for cid, _f, said in sv.CHECKS))
    from agent_friday.services import action_ledger
    got = []
    monkeypatch.setattr(action_ledger, "try_append", lambda kind, rec, **k: got.append((kind, rec)) or {})
    monkeypatch.setattr(sv, "_LAST", {})
    monkeypatch.setattr(sv, "_key_present", lambda: True)
    return sv, got


def test_health_never_runs_a_sweep_and_a_sweep_never_mints_a_key(healthy, monkeypatch):
    sv, got = healthy
    ran = []
    monkeypatch.setattr(sv, "_sweep", lambda record: ran.append(record) or {})
    assert sv.probe() == [] and ran == []                    # /api/health reads the last sweep only
    monkeypatch.undo()
    sv2 = _sv()
    monkeypatch.setattr(sv2, "_key_present", lambda: False)
    calls = []
    monkeypatch.setattr(sv2, "CHECKS", tuple((cid, (lambda c=cid: calls.append(c) or ("healthy", "ok")), said)
                                              for cid, _f, said in sv2.CHECKS))
    from agent_friday.services import action_ledger
    recorded = []
    monkeypatch.setattr(action_ledger, "try_append", lambda *a, **k: recorded.append(a) or {})
    sv2.sweep()
    assert "authority" not in calls and recorded == []        # nothing that reaches the ledger's key


def test_a_locked_key_store_is_not_a_fault(monkeypatch):
    sv = _sv()
    from agent_friday.services import health_check
    monkeypatch.setattr(health_check, "check_credential_store", lambda: (False, "KeystoreLocked: waiting"))
    assert sv._credential_store() == ("unverified", "keystore_locked")
    monkeypatch.setattr(health_check, "check_credential_store", lambda: (False, "OSError: disk"))
    assert sv._credential_store()[0] == "broken"


def test_the_sweep_covers_each_safeguard_and_names_what_it_does_not():
    sv = _sv()
    ids = {cid for cid, _f, _s in sv.CHECKS}
    assert ids >= {"receipt_chain", "rules", "egress_gate", "action_gate", "credential_store", "authority"}
    assert set(sv.NOT_COVERED) == {"update_signatures", "connector_domains", "sandbox", "principal_invariants"}


def test_a_clean_sweep_is_recorded_and_says_nothing(healthy):
    sv, got = healthy
    out = sv.sweep()
    assert out["failed"] == [] and out["checked"] == len(sv.CHECKS)
    assert got == [("self_check", {"checked": len(sv.CHECKS), "failed": [], "codes": []})]
    assert sv.probe() == []


def test_a_broken_safeguard_or_a_check_that_cannot_run_is_said(healthy, monkeypatch):
    sv, got = healthy

    def boom():
        raise OSError("disk")
    checks = list(sv.CHECKS)
    checks[0] = ("receipt_chain", lambda: ("broken", "breaks_to_review"), checks[0][2])
    checks[1] = ("rules", lambda: ("blocked_by_policy", "rules_signature_mismatch"), checks[1][2])
    checks[2] = ("egress_gate", boom, checks[2][2])
    checks[3] = ("action_gate", lambda: ("unverified", "self_test_not_run_in_this_process"), checks[3][2])
    monkeypatch.setattr(sv, "CHECKS", tuple(checks))
    out = sv.sweep()
    assert [f["id"] for f in out["failed"]] == ["receipt_chain", "rules", "egress_gate"]   # not merely unverified
    assert got[-1][1]["failed"] == ["receipt_chain", "rules", "egress_gate"]
    probs = sv.probe()
    assert {p["store"] for p in probs} == {"safeguard:receipt_chain", "safeguard:rules", "safeguard:egress_gate"}
    assert all(p["message"] and "disk" not in p["message"] for p in probs)    # Friday's words, not the error's


def test_a_fixed_problem_that_comes_back_raises_a_new_card(monkeypatch):
    from agent_friday.services import integrity_report as ir
    cards, state = [], {"bad": True}
    monkeypatch.setattr(ir, "_card", lambda store, detail: cards.append(store))
    monkeypatch.setattr(ir, "_PROBES", {"s": lambda: [{"store": "s", "state": "x", "message": "m"}] if state["bad"] else []})
    monkeypatch.setattr(ir, "_ANNOUNCED", set())
    ir.announce()
    ir.announce()
    state["bad"] = False
    ir.announce()
    state["bad"] = True
    ir.announce()
    assert cards == ["s", "s"]


def test_the_server_starts_the_sweep_and_activity_names_it():
    _sv()
    src = (ROOT / "src" / "agent_friday" / "server.py").read_text(encoding="utf-8")
    assert "self_verify" in src and "start_periodic()" in src
    led = (ROOT / "src" / "agent_friday" / "routes" / "ledger.py").read_text(encoding="utf-8")
    assert '"self_check":' in led


def test_start_periodic_waits_then_sweeps(monkeypatch):
    sv = _sv()
    import threading
    done, slept = threading.Event(), []
    monkeypatch.setattr(sv, "sweep", lambda record=True: done.set() or {"failed": []})

    def sleep(s):
        slept.append(s)
        if len(slept) > 1:
            raise SystemExit   # stop the loop after one sweep
    from agent_friday.services import integrity_report as ir
    monkeypatch.setattr(ir, "announce", lambda: [])
    registered = []
    monkeypatch.setattr(ir, "register", lambda store, fn: registered.append(store))   # no probe left behind
    sv.start_periodic(interval_s=99, first_delay_s=7, sleep=sleep)
    assert done.wait(10) and slept[0] == 7 and registered == ["safeguards"]
