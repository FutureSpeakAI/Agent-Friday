"""The safety and privacy metrics held at zero (north star §35.7-1), counted on
this PC from Friday's own records: each one counts what it says, what the gate
allows by design is shown and not counted, an unreadable or untrusted source is
"unknown" and never zero, and none of it is sent."""
from __future__ import annotations

import importlib
import importlib.util
import json
import time

import pytest


def _sm():
    assert importlib.util.find_spec("agent_friday.services.safety_metrics") is not None, \
        "the safety metrics are not counted anywhere"
    return importlib.import_module("agent_friday.services.safety_metrics")


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / ".friday"
    (h / "vault").mkdir(parents=True)
    monkeypatch.setenv("FRIDAY_HOME", str(h))
    from agent_friday.services import backup_schedule, credential_store, receipt_audit
    monkeypatch.setattr(receipt_audit, "audit", lambda **k: {"orphan_count": 0, "open_count": 0})
    monkeypatch.setattr(credential_store, "_credential_files", lambda: [])
    monkeypatch.setattr(backup_schedule, "doctor_view", lambda home=None: {
        "scheduled": True, "rehearsed_at": time.time() - 3600, "rehearsal_ok": True})
    if importlib.util.find_spec("agent_friday.services.safety_metrics") is not None:
        sm = importlib.import_module("agent_friday.services.safety_metrics")
        monkeypatch.setattr(sm, "_ledger_trusted", lambda: None)
        monkeypatch.setattr(sm, "_tainted_allowed", lambda since: 0)
    return h


def _by(out):
    return {m["id"]: m for m in out["metrics"]}


def _egress(h, rows):
    with open(h / "vault" / "egress-log.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_a_sensitive_send_is_counted_and_what_the_gate_allows_by_design_is_not(home):
    now = time.time()
    t3 = {"ts": now, "tier": "TIER_3", "action": "allow"}
    _egress(home, [                                                     # oldest first, as the gate writes
        dict(t3, ts=now - 40 * 86400, provider="openai", reason="x"),                        # outside the window
        dict(t3, provider="anthropic", reason="tier=TIER_3 whole field"),                    # counted
        dict(t3, provider="ollama", reason="tier=TIER_3"),                                   # an :cloud model: counted
        dict(t3, provider="anthropic", reason="unrestricted cloud consent - never-send floor applied"),
        dict(t3, provider="anthropic", reason="judgment=public overturned TIER_3 after verified scrub: x"),
        dict(t3, provider="anthropic", reason="off the record"),
        {"ts": now, "provider": "anthropic", "tier": "TIER_3", "action": "block", "reason": "x"},
        {"ts": "not a time", "provider": "anthropic", "tier": "TIER_3", "action": "allow", "reason": "x"},
    ])
    m = _by(_sm().metrics())["egress_protected_allowed"]
    assert m["value"] == 2 and m["state"] == "attention"
    assert "2 approved by design" in m["note"] and "1 off the record" in m["note"]


def test_nothing_held_reads_as_held(home):
    out = _sm().metrics([{"id": c, "state": "healthy"} for c in ("egress_gate", "receipt_chain", "rules",
                                                                   "action_gate")])
    m = _by(out)
    for mid in ("egress_protected_allowed", "gate_bypasses", "missing_receipts", "tainted_allowed",
                "plaintext_credentials"):
        assert m[mid]["value"] == 0 and m[mid]["state"] == "ok", m[mid]
    assert m["self_tests"]["value"] == 100.0 and m["restore_coverage"]["value"] == 100.0
    assert out["attention"] == [] and out["unknown"] == []


def test_bypasses_and_missing_receipts_come_from_the_receipt_audit(home, monkeypatch):
    from agent_friday.services import receipt_audit
    monkeypatch.setattr(receipt_audit, "audit", lambda **k: {"orphan_count": 2, "open_count": 3})
    m = _by(_sm().metrics())
    assert m["gate_bypasses"]["value"] == 2 and m["missing_receipts"]["value"] == 3


def test_an_untrusted_or_unreadable_ledger_is_unknown_never_zero(home, monkeypatch):
    sm = _sm()
    monkeypatch.setattr(sm, "_ledger_trusted", lambda: "the receipt chain has a break")
    m = _by(sm.metrics())
    for mid in ("gate_bypasses", "missing_receipts", "tainted_allowed"):
        assert m[mid]["value"] is None and m[mid]["state"] == "unknown" and "break" in m[mid]["note"]
    monkeypatch.setattr(sm, "_ledger_trusted", lambda: None)
    from agent_friday.services import receipt_audit

    def boom(**k):
        raise OSError("unreadable")
    monkeypatch.setattr(receipt_audit, "audit", boom)
    assert _by(sm.metrics())["gate_bypasses"]["state"] == "unknown"


def test_a_credential_without_its_envelope_is_counted_and_an_unreadable_one_is_unknown(home, monkeypatch):
    from agent_friday.services import credential_store
    plain = home / "plain.secret"
    plain.write_bytes(b"sk-placeholder-not-real")              # pragma: allowlist secret
    monkeypatch.setattr(credential_store, "_credential_files", lambda: [plain])
    monkeypatch.setattr(credential_store, "looks_protected", lambda blob: None)
    m = _by(_sm().metrics())["plaintext_credentials"]
    assert m["value"] == 1 and m["state"] == "attention"
    folder = home / "a-folder.secret"
    folder.mkdir()
    real_is_file = type(folder).is_file
    monkeypatch.setattr(type(folder), "is_file", lambda self: True if self == folder else real_is_file(self))
    monkeypatch.setattr(credential_store, "_credential_files", lambda: [folder])
    assert _by(_sm().metrics())["plaintext_credentials"]["state"] == "unknown"


def test_self_tests_count_only_what_ran_here_and_restores_need_a_rehearsal(home, monkeypatch):
    from agent_friday.services import backup_schedule
    out = _sm().metrics([{"id": "egress_gate", "state": "unverified"}, {"id": "rules", "state": "broken"},
                         {"id": "receipt_chain", "state": "healthy"}])
    m = _by(out)
    assert m["self_tests"]["value"] == 50.0 and "egress_gate" in m["self_tests"]["note"]
    monkeypatch.setattr(backup_schedule, "doctor_view", lambda home=None: {"scheduled": False})
    m = _by(_sm().metrics())["restore_coverage"]
    assert m["value"] == 0.0 and m["state"] == "attention" and "no scheduled bundles" in m["note"]


def test_the_report_carries_counts_and_the_weekly_email_leaves_them_out(home):
    from agent_friday.services import doctor, doctor_weekly
    rep = doctor.build()
    assert "safety_metrics" in rep, "the Doctor report carries no safety metrics"
    ids = {x["id"] for x in rep["safety_metrics"]["metrics"]}
    assert {"egress_protected_allowed", "gate_bypasses", "self_tests"} <= ids
    assert all(set(x) == {"id", "value", "target", "unit", "state"} for x in rep["safety_metrics"]["metrics"])
    assert "Safety metrics" in doctor.render_text(rep)
    assert "Safety metrics" not in doctor_weekly._render(rep, False)
