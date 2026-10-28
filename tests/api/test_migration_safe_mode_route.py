"""After a failed upgrade, safe mode is left only with a press on the Doctor
page, on this PC (Stephen's ruling 16, north star §28.8-1)."""
from __future__ import annotations

import importlib.util


def _press():
    from agent_friday.core import _current_api_token
    return {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost", "X-Friday-Token": _current_api_token()}


def test_leaving_safe_mode_after_a_failed_upgrade_takes_the_press(client, monkeypatch):
    assert importlib.util.find_spec("agent_friday.services.migrations") is not None, "there is no migration runner"
    from agent_friday.services import action_ledger, safe_mode
    assert hasattr(safe_mode, "marker_reason"), "safe mode does not know why it was chosen"
    safe_mode.set_next_start(True, reason="migration_failed")
    try:
        r = client.post("/api/safe-mode", json={"next_start": False})
        assert r.status_code == 403 and safe_mode.marker_path().exists()
        r = client.post("/api/safe-mode", json={"next_start": False}, headers=_press(),
                        environ_base={"REMOTE_ADDR": "203.0.113.7"})
        assert r.status_code in (401, 403) and safe_mode.marker_path().exists()
        got = []
        monkeypatch.setattr(action_ledger, "try_append", lambda kind, rec, **k: got.append(rec) or {})
        r = client.post("/api/safe-mode", json={"next_start": False}, headers=_press())
        assert r.status_code == 200 and not safe_mode.marker_path().exists()
        assert got and "after a failed upgrade" in got[0]["reason"]
    finally:
        safe_mode.set_next_start(False)
        safe_mode.reset_for_tests()          # decided once per process: never from this test's marker
