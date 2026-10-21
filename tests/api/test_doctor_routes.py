"""The Doctor report: versions, checks, errors, drift and performance, with no
content and no personal data, kept on this PC and shared only by hand (A3).
Safe mode is chosen and left from the Doctor page with the owner's press.

Red on the old code by assertion: /api/doctor/report, /api/safe-mode and the
/doctor page did not exist (404).
"""
from __future__ import annotations

import json
import os

import pytest

from agent_friday.paths import friday_home

# Personal details planted where a careless report would pick them up.
NAME = "Wilhelmina Okonkwo-Fairweather"
EMAIL = "wilhelmina.private@example.test"
PRIVATE_FACT = "allergic-to-quinces-7731"
SERVER = "wilhelmina-private-notes"
PATH_BIT = "Taxes 2026 Wilhelmina"


def _plant():
    home = friday_home()
    home.mkdir(parents=True, exist_ok=True)
    settings_p = home / "settings.json"
    old = settings_p.read_bytes() if settings_p.exists() else None
    d = json.loads(old) if old else {}
    d.update({"user_name": NAME, "wiki_mirror_dir": str(home.parent / PATH_BIT),
              "owner_email": EMAIL, "a_key_nobody_ships": PRIVATE_FACT})
    settings_p.write_text(json.dumps(d), encoding="utf-8")
    (home / "core_memory").mkdir(exist_ok=True)
    (home / "core_memory" / "ME.md").write_text(f"- {NAME} is {PRIVATE_FACT}\n", encoding="utf-8")
    (home / "conversations").mkdir(exist_ok=True)
    (home / "conversations" / "c1.json").write_text(json.dumps({"text": PRIVATE_FACT}), encoding="utf-8")
    (home / "mcp_servers.json").write_text(json.dumps({"servers": {SERVER: {
        "command": str(home.parent / PATH_BIT / "server.exe"), "env": {"TOKEN": PRIVATE_FACT}}}}),
        encoding="utf-8")
    with open(home / "friday.log", "a", encoding="utf-8") as f:
        # friday.log's own format, a line naming private things, an INFO line
        # with "ERROR" in its words, and an extension's own exception class
        f.write(f"2026-10-21T09:00:00 ERROR    agent_friday.services.probe \u2014 ValueError: could not "
                f"read {home.parent / PATH_BIT} for {EMAIL} ({PRIVATE_FACT})\n")
        f.write(f"2026-10-21T09:00:01 INFO     agent_friday.chat \u2014 ERROR wilhelmina said hi\n")
        f.write(f"2026-10-21T09:00:02 ERROR    wilhelmina_plugin \u2014 WilhelminaVaultError: nope\n")
    return settings_p, old


def _restore(settings_p, old):
    if old is None:
        settings_p.unlink(missing_ok=True)
    else:
        settings_p.write_bytes(old)


def test_the_report_has_the_five_parts_and_nothing_personal(client):
    settings_p, old = _plant()
    try:
        r = client.post("/api/doctor/report", json={})
        assert r.status_code == 200, r.get_data(as_text=True)
        d = r.get_json()
        rep, text = d["report"], d["text"]
        for part in ("versions", "checks", "errors", "drift", "performance"):
            assert part in rep, part
        assert rep["checks"] and all(c["state"] in (
            "healthy", "degraded", "unavailable_by_configuration", "unavailable_by_hardware",
            "misconfigured", "blocked_by_policy", "broken", "repairing", "unverified")
            for c in rep["checks"])
        assert any(e["signature"] == "ValueError" for e in rep["errors"]["exception_types"])
        assert "user_name" in rep["drift"]["settings_changed_from_default"] or \
            rep["drift"].get("settings_unknown_key_count", 0) >= 1
        blob = json.dumps(rep) + text
        assert any(e["signature"] == "other_exception" for e in rep["errors"]["exception_types"])
        assert any(e["signature"] == "other_logger" for e in rep["errors"]["loggers"])
        for bit in (NAME, "Wilhelmina", "wilhelmina", EMAIL, PRIVATE_FACT, SERVER, PATH_BIT, "quinces",
                    str(friday_home()), os.path.expanduser("~")):
            assert bit not in blob, bit
    finally:
        _restore(settings_p, old)


def test_making_a_report_sends_nothing(client, monkeypatch):
    import socket
    def no_net(*a, **k):
        raise AssertionError("the Doctor reached for the network")
    real_connect = socket.socket.connect

    def guarded(self, addr, *a, **k):
        host = addr[0] if isinstance(addr, tuple) else addr
        if str(host) not in ("127.0.0.1", "::1", "localhost"):
            no_net()
        return real_connect(self, addr, *a, **k)
    monkeypatch.setattr(socket.socket, "connect", guarded)
    monkeypatch.setattr(socket, "getaddrinfo", no_net)
    r = client.post("/api/doctor/report", json={})
    assert r.status_code == 200, r.get_data(as_text=True)


def test_the_report_is_kept_and_saved_by_hand_as_a_file(client):
    rep = client.post("/api/doctor/report", json={}).get_json()["report"]
    hist = client.get("/api/doctor/reports").get_json()["reports"]
    assert any(h["id"] == rep["id"] for h in hist)
    for kind in ("txt", "json"):
        f = client.get(f"/api/doctor/reports/{rep['id']}.{kind}")
        assert f.status_code == 200
        assert "attachment" in f.headers.get("Content-Disposition", "")
    assert client.get("/api/doctor/reports/..%2f..%2fsettings.json").status_code == 404
    assert client.get("/api/doctor/reports/doctor-x.txt").status_code == 404


def test_the_report_changes_nothing_else_in_the_data_folder(client):
    home = friday_home()
    def snap():
        out = {}
        for p in home.rglob("*"):
            if p.is_file() and "doctor" not in p.relative_to(home).parts:
                try:
                    st = p.stat()
                    out[str(p)] = (st.st_size, st.st_mtime_ns)
                except OSError:
                    pass
        return out
    before = snap()
    assert client.post("/api/doctor/report", json={}).status_code == 200
    after = snap()
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    assert not changed, changed


def test_the_doctor_page_is_served_locally(client):
    r = client.get("/doctor")
    assert r.status_code == 200 and b"Doctor" in r.data
    assert b"never sends it anywhere" in r.data


def test_safe_mode_for_the_next_start_needs_the_owners_press(client, monkeypatch):
    from agent_friday.core import _current_api_token
    st = client.get("/api/safe-mode")
    assert st.status_code == 200 and st.get_json()["active"] is False
    r = client.post("/api/safe-mode", json={"next_start": True})
    assert r.status_code == 403
    press = {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost",
             "X-Friday-Token": _current_api_token()}
    marker = friday_home() / "safe_mode" / "ON"
    try:
        r = client.post("/api/safe-mode", json={"next_start": True}, headers=press)
        assert r.status_code == 200, r.get_data(as_text=True)
        d = r.get_json()
        assert d["next_start_safe"] is True and d["restart_needed"] is True
        assert marker.exists()
        r = client.post("/api/safe-mode", json={"next_start": False}, headers=press)
        assert r.status_code == 200 and r.get_json()["next_start_safe"] is False
        assert not marker.exists()
    finally:
        marker.unlink(missing_ok=True)


def test_the_observer_cannot_read_the_doctor(client):
    from agent_friday.services import observer_access as obs
    tok = client.post("/api/tasks/observer-token").get_json()["token"]
    for path in ("/api/doctor/reports", "/api/safe-mode", "/doctor"):
        assert client.get(path, headers={obs.HEADER: tok}).status_code == 403, path
    assert client.post("/api/doctor/report", headers={obs.HEADER: tok}, json={}).status_code == 403


def test_nothing_here_answers_a_page_reached_from_another_machine(client):
    from agent_friday.core import _current_api_token
    remote = {"REMOTE_ADDR": "203.0.113.7"}
    press = {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost",
             "X-Friday-Token": _current_api_token()}
    r = client.post("/api/safe-mode", json={"next_start": False}, headers=press, environ_base=remote)
    assert r.status_code in (401, 403)
    r = client.post("/api/ledger/acknowledge", json={"reviewed": []}, headers=press, environ_base=remote)
    assert r.status_code in (401, 403)
    r = client.get("/doctor", environ_base=remote)
    assert r.status_code in (302, 401, 403)
    assert _current_api_token().encode() not in r.data
