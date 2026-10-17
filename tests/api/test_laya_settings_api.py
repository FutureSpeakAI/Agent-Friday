"""Settings > Laya API (routes/laya_settings): local owner only; letting her act more needs a click."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from flask import Flask, g

from agent_friday.services import laya_router as lr
from agent_friday.services import laya_settings as ls

READS = {"query_calendar", "check_email", "get_briefing", "search_web", "search_news"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_friday.core.FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(lr, "log_path", lambda: tmp_path / "runtime" / "laya2" / "router.jsonl")
    monkeypatch.setattr("agent_friday.services.laya.mutation_class", lambda t: "read" if t in READS else "write")
    monkeypatch.setattr(ls, "_settings", lambda: {})
    monkeypatch.setattr("agent_friday.core._is_local_request", lambda: True)
    screen = [False]
    monkeypatch.setattr("agent_friday.services.screen_click.screen_session", lambda request: screen[0])
    ls._cache.update(checked=0.0, mtime=None, path=None, data=None)
    from agent_friday.routes import laya_settings as route
    app = Flask("t")
    principal = ["user"]

    @app.before_request
    def _who():
        g.friday_principal = principal[0]
    app.register_blueprint(route.laya_settings_bp)
    c = app.test_client()
    c.principal, c.screen, c.monkeypatch = principal, screen, monkeypatch
    return c


def test_the_tab_is_served_to_the_local_owner_only(client):
    r = client.get("/api/laya/settings")
    assert r.status_code == 200
    d = r.get_json()
    assert d["what_runs"]["series"] == "Series One"
    caps = {m["id"]: m for m in d["modes"]}
    assert caps["approval_check"]["never_act"] and caps["approval_check"]["mode"] != "act"
    assert {row["tool"] for row in d["bars"]["rows"]} == set(lr.TOOLS)
    client.principal[0] = "observer"
    assert client.get("/api/laya/settings").status_code == 403
    assert client.post("/api/laya/settings/replay", json={}).status_code == 403
    client.principal[0] = "user"
    client.monkeypatch.setattr("agent_friday.core._is_local_request", lambda: False)
    assert client.get("/api/laya/settings").status_code == 403


def test_lowering_a_bar_needs_a_click_and_raising_one_does_not(client):
    r = client.post("/api/laya/settings/bars", json={"bars": {"query_calendar": 0.45}})
    assert r.status_code == 403 and "click" in r.get_json()["message"]
    assert ls.load_bars()["bars"] == {}
    assert client.post("/api/laya/settings/bars", json={"bars": {"query_calendar": 0.70}}).status_code == 200
    assert ls.load_bars()["bars"] == {"query_calendar": 0.70}
    client.screen[0] = True
    assert client.post("/api/laya/settings/bars", json={"bars": {"query_calendar": 0.45}}).status_code == 200
    assert json.loads(ls.bars_history_path().read_text(encoding="utf-8").splitlines()[-1])["actor"] == "owner:ui"


def test_turning_the_bars_to_act_needs_a_click_and_back_to_shadow_does_not(client):
    assert client.post("/api/laya/settings/bars", json={"mode": "act"}).status_code == 403
    client.screen[0] = True
    assert client.post("/api/laya/settings/bars", json={"mode": "act"}).status_code == 200
    client.screen[0] = False
    assert client.post("/api/laya/settings/bars", json={"mode": "shadow"}).status_code == 200
    assert ls.load_bars()["mode"] == "shadow"


def test_back_to_shadow_needs_a_click_while_a_stricter_bar_is_in_force(client):
    """In shadow every action falls back to the shipped score, so turning act off takes a stricter bar out of force."""
    client.screen[0] = True
    assert client.post("/api/laya/settings/bars", json={"bars": {"check_email": 0.80}, "mode": "act"}).status_code == 200
    client.screen[0] = False
    assert client.post("/api/laya/settings/bars", json={"mode": "shadow"}).status_code == 403
    assert ls.load_bars()["mode"] == "act"
    client.screen[0] = True
    assert client.post("/api/laya/settings/bars", json={"mode": "shadow"}).status_code == 200


def test_a_click_still_cannot_lower_an_action_that_changes_something(client):
    client.screen[0] = True
    r = client.post("/api/laya/settings/bars", json={"bars": {"set_chat_tray": 0.40}})
    assert r.status_code == 400 and "only be raised" in r.get_json()["message"]
    assert client.post("/api/laya/settings/bars", json={"bars": {"search_web": 0.40}}).status_code == 400
    assert client.post("/api/laya/settings/bars", json={"mode": "suggest"}).status_code == 400
    assert client.post("/api/laya/settings/bars", json={"bars": ["x"]}).status_code == 400


def test_the_replay_route_reports_a_locked_vault_and_returns_no_rows(client):
    client.monkeypatch.setattr("agent_friday.services.laya_personal_store.load_rows", lambda profile: None)
    r = client.post("/api/laya/settings/replay", json={})
    assert r.status_code == 409 and "locked" in r.get_json()["message"]
    client.monkeypatch.setattr("agent_friday.services.laya_personal_store.load_rows",
                               lambda profile: [{"id": "lr_1", "at": 1, "kind": "correction", "weight": "gold",
                                                 "text": "pull up my week", "label": "query_calendar"}])
    client.monkeypatch.setattr(ls, "_route_one", lambda text, profile: SimpleNamespace(
        decision="tool", tool="query_calendar", layer="laya2", confidence=0.9, candidates=[]))
    r = client.post("/api/laya/settings/replay", json={})
    d = r.get_json()
    assert r.status_code == 200 and d["plain"]["right"] == 1 and "cases" not in d
    assert "pull up my week" not in r.get_data(as_text=True)
    assert client.get("/api/laya/settings").get_json()["report"]["replay"]["plain"]["of"] == 1
