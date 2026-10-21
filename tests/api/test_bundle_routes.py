"""Backup and restore from the Doctor page: making a bundle and resuming
outward actions after a restore take the owner's press on this PC.

Red on the old code by assertion: the routes did not exist (404).
"""
from __future__ import annotations

import json
import time

from agent_friday.paths import friday_home


def _press():
    from agent_friday.core import _current_api_token
    return {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost",
            "X-Friday-Token": _current_api_token()}


def test_resume_after_a_restore_takes_the_press(client):
    hold = friday_home() / "restore" / "hold.json"
    hold.parent.mkdir(parents=True, exist_ok=True)
    hold.write_text(json.dumps({"bundle_id": "fb-test", "restored_at": time.time()}), encoding="utf-8")
    try:
        st = client.get("/api/restore/state").get_json()
        assert st["held"] is True and "fb-test" in st["why"]
        assert client.post("/api/restore/resume", json={}).status_code == 403
        assert client.post("/api/restore/resume", json={}, headers=_press(),
                           environ_base={"REMOTE_ADDR": "203.0.113.7"}).status_code in (401, 403)
        r = client.post("/api/restore/resume", json={}, headers=_press())
        assert r.status_code == 200 and r.get_json()["resumed"] is True
        assert client.get("/api/restore/state").get_json()["held"] is False
    finally:
        hold.unlink(missing_ok=True)


def test_making_a_bundle_takes_the_press_and_a_long_passphrase(client):
    assert client.post("/api/bundle/create", json={"passphrase": "x" * 20}).status_code == 403
    r = client.post("/api/bundle/create", json={"passphrase": "short"}, headers=_press())
    assert r.status_code == 400
    r = client.post("/api/bundle/create", json={"passphrase": "x" * 20, "categories": ["nope"]},
                    headers=_press())
    assert r.status_code == 400
    assert client.get("/api/bundle/status").status_code == 200
