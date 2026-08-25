"""WO-17 HTTP surface — routes/control.py's file-grants endpoints.

Authenticated (login_required), UI-chrome-driven, and never reachable by a
model: this file only asserts the wiring works end to end through Flask,
not the grant semantics themselves (covered by tests/unit/test_file_grants.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest


@pytest.fixture(autouse=True)
def _isolated_ledger(monkeypatch, friday_dir):
    from agent_friday.services import file_grants as fg
    ledger = friday_dir / "privacy" / "file_grants.jsonl"
    monkeypatch.setattr(fg, "_ledger_path", lambda: ledger)
    fg._invalidate_cache()
    yield
    fg._invalidate_cache()


def test_endpoints_are_registered(client):
    rules = {str(r.rule) for r in client.application.url_map.iter_rules()}
    for path in ("/api/privacy/file-grants", "/api/privacy/file-grants/scan",
                 "/api/privacy/deny-marks",
                 "/api/privacy/file-grants/<grant_id>/revoke"):
        assert path in rules, f"{path} not registered"


def test_create_scan_list_and_revoke_round_trip(client, tmp_path):
    p = tmp_path / "cv.txt"
    p.write_text("Senior AI leadership experience.\n\nSanofi pivot analysis.",
                 encoding="utf-8")

    scan = client.get(f"/api/privacy/file-grants/scan?path={p}")
    assert scan.status_code == 200
    assert scan.get_json()["extractable"] is True

    created = client.post("/api/privacy/file-grants", json={"path": str(p), "scope": "file"})
    assert created.status_code == 200
    grant = created.get_json()["grant"]
    assert grant["type"] == "file"

    listed = client.get("/api/privacy/file-grants")
    assert listed.status_code == 200
    assert any(g["id"] == grant["id"] for g in listed.get_json()["grants"])

    revoked = client.post(f"/api/privacy/file-grants/{grant['id']}/revoke")
    assert revoked.status_code == 200

    listed_again = client.get("/api/privacy/file-grants")
    assert not any(g["id"] == grant["id"] for g in listed_again.get_json()["grants"])


def test_scope_grant_without_expiry_is_rejected(client, tmp_path):
    folder = tmp_path / "docs"
    folder.mkdir()
    resp = client.post("/api/privacy/file-grants",
                       json={"path": str(folder), "scope": "folder"})
    assert resp.status_code == 400


def test_scope_grant_over_30_days_is_rejected(client, tmp_path):
    folder = tmp_path / "docs"
    folder.mkdir()
    resp = client.post("/api/privacy/file-grants",
                       json={"path": str(folder), "scope": "folder", "expiry_days": 45})
    assert resp.status_code == 400


def test_deny_mark_round_trip(client, tmp_path):
    p = tmp_path / "private.txt"
    p.write_text("secret", encoding="utf-8")

    created = client.post("/api/privacy/deny-marks", json={"path": str(p), "scope": "file"})
    assert created.status_code == 200
    deny = created.get_json()["deny"]

    listed = client.get("/api/privacy/file-grants")
    assert any(d["id"] == deny["id"] for d in listed.get_json()["denies"])


def test_missing_file_grant_returns_404(client, tmp_path):
    resp = client.post("/api/privacy/file-grants",
                       json={"path": str(tmp_path / "nope.txt"), "scope": "file"})
    assert resp.status_code == 404


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
