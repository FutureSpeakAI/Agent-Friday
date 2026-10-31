"""Folder grants widen where the file tools reach, so only a press on Friday's own page
on this PC adds one; removing one is open to the page (BD-26)."""
from __future__ import annotations

from tests.desk_press_support import desk_headers as _page      # a press on this PC's screen (services/desk_press)
from tests.desk_press_support import page_headers


def test_a_grant_needs_a_press_on_this_pc(client, tmp_path):
    body = {"path": str(tmp_path), "ops": ["read"], "days": 7}
    for headers, addr in (({}, "127.0.0.1"), (dict(_page(), **{"X-Forwarded-For": "203.0.113.9"}), "127.0.0.1"),
                          (_page(), "192.168.1.20"), (page_headers(), "127.0.0.1")):
        r = client.post("/api/files/folder-grants", json=body, headers=headers, environ_base={"REMOTE_ADDR": addr})
        assert r.status_code in (401, 403), (headers, addr, r.status_code)
    assert client.get("/api/files/folder-grants").get_json()["grants"] == []


def test_a_press_adds_a_grant_and_any_page_request_removes_it(client, tmp_path):
    r = client.post("/api/files/folder-grants", json={"path": str(tmp_path), "ops": ["read", "write"], "days": 7},
                    headers=_page())
    assert r.status_code == 200, r.data
    gid = r.get_json()["grant"]["id"]
    assert [g["id"] for g in client.get("/api/files/folder-grants").get_json()["grants"]] == [gid]
    assert client.delete(f"/api/files/folder-grants/{gid}").get_json()["revoked"] is True
    assert client.get("/api/files/folder-grants").get_json()["grants"] == []


def test_a_bad_grant_says_why_without_internals(client):
    r = client.post("/api/files/folder-grants", json={"path": "\\\\server\\share", "ops": ["read"]}, headers=_page())
    assert r.status_code == 400
    assert "network" in r.get_json()["error"] and "Traceback" not in r.get_json()["error"]
