"""The Doctor page's "After a restore" lists what this PC still needs, and
Resume answers 409 with that list until it is in place (north star §27.6-2)."""
from __future__ import annotations

import importlib.util
import json
import time

from agent_friday.paths import friday_home


def _press():
    from agent_friday.core import _current_api_token
    return {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost", "X-Friday-Token": _current_api_token()}


def test_resume_waits_for_what_is_missing_and_says_what(client, monkeypatch):
    assert importlib.util.find_spec("agent_friday.services.restore_reestablish") is not None, \
        "nothing checks what a restored PC still needs"
    from agent_friday.services import restore_reestablish
    left = [{"code": "channel_signin", "what": "telegram", "said": "Telegram is switched on; reconnect it."}]
    monkeypatch.setattr(restore_reestablish, "blockers", lambda home=None: left)
    hold = friday_home() / "restore" / "hold.json"
    hold.parent.mkdir(parents=True, exist_ok=True)
    hold.write_text(json.dumps({"bundle_id": "fb-test", "restored_at": time.time()}), encoding="utf-8")
    try:
        st = client.get("/api/restore/state").get_json()
        assert st["held"] is True and st["blockers"] == left
        r = client.post("/api/restore/resume", json={}, headers=_press())
        assert r.status_code == 409 and r.get_json()["code"] == "not_reestablished"
        assert r.get_json()["blockers"] == left and hold.exists()
        monkeypatch.setattr(restore_reestablish, "blockers", lambda home=None: [])
        r = client.post("/api/restore/resume", json={}, headers=_press())
        assert r.status_code == 200 and r.get_json()["resumed"] is True
    finally:
        hold.unlink(missing_ok=True)
