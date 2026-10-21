"""Stop everything over HTTP: the button, typed words in chat, and Resume.

Stopping is open to the owner's page and needs no approval. While stopped no chat
turn runs and no voice reply is spoken. Resume is accepted only as a press on
Friday's own page (same-origin browser request carrying the page token); a plain
request, a script without the page's token, or a typed "resume" never resumes.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def gs(tmp_path, monkeypatch):
    from agent_friday.services import global_stop, agent, local_image
    monkeypatch.setattr(global_stop, "_path", lambda: tmp_path / "global_stop.json")
    monkeypatch.setattr(agent, "_HAS_PYAUTOGUI", False, raising=False)
    monkeypatch.setattr(local_image, "interrupt_comfy", lambda timeout=10: False)
    had_perm, had_kill = agent._CC_PERMISSION.is_set(), agent._CC_KILL.is_set()
    global_stop._reset_for_tests()
    yield global_stop
    global_stop._reset_for_tests()
    (agent._CC_PERMISSION.set if had_perm else agent._CC_PERMISSION.clear)()
    (agent._CC_KILL.set if had_kill else agent._CC_KILL.clear)()


def _page():
    import agent_friday.core as core
    return {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost",
            "X-Friday-Token": core._current_api_token()}


def test_the_button_stops_everything(client, gs):
    r = client.post("/api/stop-everything", json={"source": "button"})
    assert r.status_code == 200, r.data
    assert r.get_json()["stopped"] is True
    assert client.get("/api/stop-everything").get_json()["stopped"] is True


def test_no_chat_turn_runs_while_stopped(client, gs, monkeypatch):
    from agent_friday.services import agent
    called = []
    monkeypatch.setattr(agent, "_generate_agent", lambda *a, **k: called.append(1) or "hi")
    client.post("/api/stop-everything", json={})
    for path in ("/api/chat", "/api/chat/send"):
        r = client.post(path, json={"message": "what's the weather"})
        assert r.status_code == 423, (path, r.status_code)
        assert r.get_json().get("everything_stopped") is True
    assert not called


def test_typing_stop_everything_stops_without_a_model(client, gs, monkeypatch):
    from agent_friday.services import agent, model_router
    def boom(*a, **k):
        raise AssertionError("a model was asked")
    monkeypatch.setattr(agent, "_generate_agent", boom)
    monkeypatch.setattr(model_router, "_generate_text", boom)
    r = client.post("/api/chat", json={"message": "Stop everything!"})
    assert r.status_code == 200
    assert r.get_json().get("everything_stopped") is True
    assert gs.active()


def test_no_voice_reply_is_spoken_while_stopped(client, gs):
    client.post("/api/stop-everything", json={})
    r = client.post("/api/voice/tts", json={"text": "hello"})
    assert r.status_code == 423


def test_resume_is_refused_without_a_press_on_the_page(client, gs):
    client.post("/api/stop-everything", json={})
    for headers in ({}, {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost"},
                    {"X-Friday-Token": _page()["X-Friday-Token"]}):
        r = client.post("/api/stop-everything/resume", json={}, headers=headers)
        assert r.status_code == 403, headers
        assert r.get_json()["code"] == "resume_needs_press"
    assert gs.active()
    # Words in chat never resume either.
    r = client.post("/api/chat", json={"message": "resume everything"})
    assert r.status_code == 423 and gs.active()


def test_a_press_on_the_page_resumes(client, gs):
    client.post("/api/stop-everything", json={})
    r = client.post("/api/stop-everything/resume", json={}, headers=_page())
    assert r.status_code == 200, r.data
    assert r.get_json()["stopped"] is False
    assert not gs.active()


def test_stop_is_post_only(client, gs):
    assert client.get("/api/stop-everything").get_json()["stopped"] is False
    assert client.put("/api/stop-everything").status_code in (404, 405)
    assert not gs.active()


def test_a_press_through_a_tunnel_or_forward_does_not_resume(client, gs):
    """The page reached through a tunnel carries the same token; only this PC's own page resumes."""
    client.post("/api/stop-everything", json={})
    for extra in ({"X-Forwarded-For": "203.0.113.9"}, {"CF-Connecting-IP": "203.0.113.9"}):
        h = dict(_page(), **extra)
        r = client.post("/api/stop-everything/resume", json={}, headers=h)
        assert r.status_code == 403, extra
    assert gs.active()


def test_a_press_from_another_machine_on_the_lan_does_not_resume(client, gs):
    client.post("/api/stop-everything", json={})
    r = client.post("/api/stop-everything/resume", json={}, headers=_page(),
                    environ_base={"REMOTE_ADDR": "192.168.1.20"})
    assert r.status_code != 200
    assert gs.active()


def test_file_analysis_waits_while_stopped(client, gs):
    import io
    client.post("/api/stop-everything", json={})
    r = client.post("/api/analyze", data={"file": (io.BytesIO(b"hello"), "a.txt")},
                    content_type="multipart/form-data")
    assert r.status_code == 423
