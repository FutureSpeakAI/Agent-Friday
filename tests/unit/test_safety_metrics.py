"""The safety and privacy metrics held at zero (north star §35.7-1), counted on
this PC from Friday's own records: each one counts what it says, an unreadable
source is "unknown" and never zero, and none of it is sent."""
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
    monkeypatch.setattr(backup_schedule, "doctor_view", lambda home=None: {"scheduled": False})
    return h


def _by(out):
    return {m["id"]: m for m in out["metrics"]}


def _egress(h, rows):
    with open(h / "vault" / "egress-log.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_a_sensitive_send_to_the_cloud_is_counted_and_a_withheld_one_is_not(home):
    now = time.time()
    _egress(home, [
        {"ts": now, "provider": "anthropic", "tier": "TIER_3", "action": "allow"},      # counted
        {"ts": now, "provider": "anthropic", "tier": "TIER_3", "action": "block"},      # the gate working
        {"ts": now, "provider": "anthropic", "tier": "TIER_2", "action": "redact"},
        {"ts": now, "provider": "ollama", "tier": "TIER_3", "action": "allow"},         # stays on this PC
        {"ts": now - 40 * 86400, "provider": "openai", "tier": "TIER_3", "action": "allow"},   # outside the window
    ])
    m = _by(_sm().metrics())["egress_protected_allowed"]
    assert m["value"] == 1 and m["state"] == "attention"


def test_nothing_held_reads_as_held(home):
    out = _sm().metrics([{"id": c, "state": "healthy"} for c in ("egress_gate", "receipt_chain", "rules",
                                                                   "action_gate")])
    m = _by(out)
    for mid in ("egress_protected_allowed", "gate_bypasses", "missing_receipts", "plaintext_credentials"):
        assert m[mid]["value"] == 0 and m[mid]["state"] == "ok", m[mid]
    assert m["self_tests"]["value"] == 100.0 and m["self_tests"]["state"] == "ok"
    assert m["restore_coverage"]["state"] == "unknown"           # no scheduled bundles: not "100%"


def test_bypasses_and_missing_receipts_come_from_the_receipt_audit(home, monkeypatch):
    from agent_friday.services import receipt_audit
    monkeypatch.setattr(receipt_audit, "audit", lambda **k: {"orphan_count": 2, "open_count": 3})
    m = _by(_sm().metrics())
    assert m["gate_bypasses"]["value"] == 2 and m["missing_receipts"]["value"] == 3
    assert {"gate_bypasses", "missing_receipts"} <= set(_sm().metrics()["attention"])


def test_an_unreadable_source_is_unknown_never_zero(home, monkeypatch):
    from agent_friday.services import receipt_audit

    def boom(**k):
        raise OSError("ledger unreadable")
    monkeypatch.setattr(receipt_audit, "audit", boom)
    m = _by(_sm().metrics())
    assert m["gate_bypasses"]["value"] is None and m["gate_bypasses"]["state"] == "unknown"
    assert "OSError" in m["gate_bypasses"]["note"]


def test_a_credential_without_its_envelope_is_counted(home, monkeypatch):
    from agent_friday.services import credential_store
    plain = home / "plain.secret"
    plain.write_bytes(b"sk-placeholder-not-real")              # pragma: allowlist secret
    monkeypatch.setattr(credential_store, "_credential_files", lambda: [plain])
    monkeypatch.setattr(credential_store, "looks_protected", lambda blob: None)
    m = _by(_sm().metrics())["plaintext_credentials"]
    assert m["value"] == 1 and m["state"] == "attention"


def test_a_failing_self_test_and_a_failed_rehearsal_show(home, monkeypatch):
    from agent_friday.services import backup_schedule
    monkeypatch.setattr(backup_schedule, "doctor_view", lambda home=None: {
        "scheduled": True, "rehearsed_at": time.time() - 3600, "rehearsal_ok": False})
    out = _sm().metrics([{"id": "egress_gate", "state": "broken"}, {"id": "rules", "state": "healthy"}])
    m = _by(out)
    assert m["self_tests"]["value"] == 50.0 and m["self_tests"]["state"] == "attention"
    assert m["restore_coverage"]["value"] == 0.0 and m["restore_coverage"]["state"] == "attention"


def test_the_report_carries_counts_and_the_weekly_email_leaves_them_out(home):
    from agent_friday.services import doctor, doctor_weekly
    rep = doctor.build()
    assert "safety_metrics" in rep, "the Doctor report carries no safety metrics"
    ids = {x["id"] for x in rep["safety_metrics"]["metrics"]}
    assert {"egress_protected_allowed", "gate_bypasses", "self_tests"} <= ids
    assert all(set(x) == {"id", "value", "target", "unit", "state"} for x in rep["safety_metrics"]["metrics"])
    assert "Safety metrics" in doctor.render_text(rep)
    assert "Safety metrics" not in doctor_weekly._render(rep, False)
