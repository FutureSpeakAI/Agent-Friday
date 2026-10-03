"""The File access panel's routes, end to end through Flask.

A grant added through POST /api/privacy/file-grants survives a restart and is
listed with its type and a plain status; removing it takes it off the list; an
old permission set aside after a key change is offered again only as a new
approval card.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

OLD_KEY = "retired-session-secret-for-this-test"   # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    from agent_friday.services import approvals
    from agent_friday.services import file_grant_requests as fgr
    from agent_friday.services import file_grants as fg
    ledger = tmp_path / "grants-ledger" / "file_grants.jsonl"
    monkeypatch.setattr(fg, "_ledger_path", lambda: ledger)
    monkeypatch.setattr(fg, "_SIGNING_KEY_CACHE", {})
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(approvals, "_HOOKS", {})
    monkeypatch.setitem(fgr._HOOKED, "done", False)
    monkeypatch.delenv("FRIDAY_SECRET_KEY", raising=False)
    fg._invalidate_cache()
    yield
    fg._invalidate_cache()


def _restart():
    from agent_friday.services import file_grants as fg
    fg._SIGNING_KEY_CACHE.clear()
    fg._invalidate_cache()


def test_the_notice_routes_are_registered(client):
    rules = {str(r.rule) for r in client.application.url_map.iter_rules()}
    assert "/api/privacy/file-grants/notices/<notice_id>/regrant" in rules
    assert "/api/privacy/file-grants/notices/<notice_id>/dismiss" in rules


def test_a_grant_added_through_the_route_persists_and_is_listed(client, tmp_path):
    p = tmp_path / "cv.txt"
    p.write_text("Senior AI leadership experience.\n\nPivot analysis.", encoding="utf-8")
    folder = tmp_path / "notes"
    folder.mkdir()

    made = client.post("/api/privacy/file-grants", json={"path": str(p), "scope": "file"})
    assert made.status_code == 200
    gid = made.get_json()["grant"]["id"]
    made_f = client.post("/api/privacy/file-grants",
                         json={"path": str(folder), "scope": "folder", "expiry_days": 7})
    assert made_f.status_code == 200
    fid = made_f.get_json()["grant"]["id"]

    _restart()
    listed = client.get("/api/privacy/file-grants").get_json()
    rows = {r["id"]: r for r in listed["rows"]}
    assert rows[gid]["status"] == "valid" and rows[gid]["type"] == "file"
    assert rows[gid]["path"] == str(p.resolve())
    assert rows[fid]["status"] == "valid" and rows[fid]["type"] == "folder"
    assert rows[fid]["expires_ts"] and listed["notices"] == []

    assert client.post(f"/api/privacy/file-grants/{gid}/revoke").status_code == 200
    rows = {r["id"] for r in client.get("/api/privacy/file-grants").get_json()["rows"]}
    assert gid not in rows and fid in rows


def test_an_old_permission_is_offered_again_only_as_an_approval_card(client, tmp_path, monkeypatch):
    from agent_friday.services import approvals
    from agent_friday.services import file_grants as fg
    monkeypatch.setenv("FRIDAY_SECRET_KEY", OLD_KEY)
    p = tmp_path / "cv.txt"
    p.write_text("Senior AI leadership experience.", encoding="utf-8")
    fg._secret_bytes()
    ev = {"event": "grant_file", "id": "old-aug-grant", "type": "file",
          "path": str(p.resolve()), "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
          "created_ts": _dt.datetime(2026, 8, 25, 12).timestamp(),
          "never_send_override": False, "ack_never_send_matches": [],
          "findings_summary": ""}
    ledger = fg._ledger_path()
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(json.dumps({"event": ev, "hmac": fg._hmac_hex(ev, OLD_KEY.encode())},
                                 sort_keys=True, separators=(",", ":")) + "\n",
                      encoding="utf-8")
    fg._invalidate_cache()

    listed = client.get("/api/privacy/file-grants").get_json()
    assert listed["status"]["suspended"] is False
    assert [r["status"] for r in listed["rows"]] == ["quarantined"]
    (notice,) = listed["notices"]
    assert notice["message"] == ("An old permission from Aug 25 was signed with a "
                                 "retired key; re-grant it?")

    out = client.post(f"/api/privacy/file-grants/notices/{notice['id']}/regrant")
    assert out.status_code == 200
    card = approvals.get_approval(out.get_json()["approval_id"])
    assert card["status"] == "pending" and card["kind"] == "file_grant_request"
    assert [it["path"] for it in card["payload"]["items"]] == [str(p.resolve())]
    assert fg.list_grants() == [], "a re-grant must wait for the card"


def test_the_reattest_route_refuses_a_line_no_known_key_signed(client, tmp_path):
    from agent_friday.services import file_grants as fg
    fg._secret_bytes()
    ev = {"event": "grant_file", "id": "tampered", "type": "file",
          "path": str(tmp_path / "cv.txt"), "sha256": "0" * 64, "created_ts": 1.0}
    ledger = fg._ledger_path()
    ledger.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"event": ev, "hmac": fg._hmac_hex(ev, b"a key nobody has")},
                      sort_keys=True, separators=(",", ":"))
    ledger.write_text(line + "\n", encoding="utf-8")
    fg._invalidate_cache()
    sha = hashlib.sha256(line.encode("utf-8")).hexdigest()

    resp = client.post("/api/privacy/file-grants/reattest",
                       json={"line_sha256": sha, "confirmed_by": "owner"})

    assert resp.status_code == 400
    text = ledger.read_text(encoding="utf-8")
    assert "reattested_from" not in text and line in text
    assert fg.status()["suspended"] is True
