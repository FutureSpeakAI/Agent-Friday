"""BD-16: POST /api/server/prepare-for-exit gets the server ready to be stopped: add-ons and
browser sessions stopped, Laya's queue flushed, a clean exit recorded. Synthetic stand-ins."""
from __future__ import annotations

import sys


def test_getting_ready_to_stop_records_a_clean_exit_and_stops_addons(client, tmp_path, monkeypatch):
    from agent_friday.services import task_journal as tj
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", str(tmp_path / "tasks"))
    stopped = []

    class _Mgr:
        def stop_all(self):
            stopped.append("addons")
    agent = sys.modules.get("agent_friday.services.agent")
    if agent is None:
        import agent_friday.services.agent as agent
    monkeypatch.setattr(agent, "_MCP_MANAGER", _Mgr(), raising=False)
    import agent_friday.services.browser_session as bs
    monkeypatch.setattr(bs, "stop_all_owned_sessions", lambda: stopped.append("browser"))
    r = client.post("/api/server/prepare-for-exit", json={"reason": "tray_quit"})
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    d = r.get_json()
    assert d["steps"]["marked"] is True and d["steps"]["addons_stopped"] is True
    assert sorted(stopped) == ["addons", "browser"] and d["steps"]["browser_sessions_stopped"] is True
    assert (tj.tasks_dir() / tj.EXIT_MARKER).exists()
