"""Revoke all action authority from the Doctor page (§18.12).

Red on the old code by assertion: the routes did not exist (404).
"""
from __future__ import annotations


def _press():
    from agent_friday.core import _current_api_token
    return {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost",
            "X-Friday-Token": _current_api_token(),
            "X-Friday-Desk": __import__("agent_friday.services.desk_press", fromlist=["x"])._KEY}


def test_revoke_needs_the_owners_page_and_restore_needs_this_pc(client):
    from agent_friday.services import authority
    authority._SEEN.clear()
    assert client.get("/api/authority").get_json()["revoked"] is False
    assert client.post("/api/authority/revoke-all", json={}).status_code == 403   # no page token
    r = client.post("/api/authority/revoke-all", json={}, headers=_press())
    assert r.status_code == 200, r.get_data(as_text=True)
    assert client.get("/api/authority").get_json()["revoked"] is True
    r = client.post("/api/authority/restore", json={}, headers=_press(),
                    environ_base={"REMOTE_ADDR": "203.0.113.7"})
    assert r.status_code in (401, 403)
    assert client.post("/api/authority/restore", json={}).status_code == 403
    r = client.post("/api/authority/restore", json={}, headers=_press())
    assert r.status_code == 200 and r.get_json()["restored"] is True
    assert client.get("/api/authority").get_json()["revoked"] is False


def test_the_doctor_page_has_the_control():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[2] / "static" / "doctor.html").read_text(encoding="utf-8")
    assert 'id="auth-revoke"' in html and "/api/authority/revoke-all" in html and 'id="auth-restore"' in html
