"""services/prompt_freeze: one capture per conversation and block."""
from __future__ import annotations

import pytest

from agent_friday.services import prompt_freeze as pf


@pytest.fixture(autouse=True)
def _fresh():
    pf.reset()
    yield
    pf.reset()


def _counter():
    calls = {"n": 0}

    def build():
        calls["n"] += 1
        return f"render {calls['n']}"
    return calls, build


def test_a_scope_keeps_its_first_capture():
    calls, build = _counter()
    assert pf.frozen("c1", "user_model", build) == "render 1"
    assert pf.frozen("c1", "user_model", build) == "render 1"
    assert calls["n"] == 1


def test_scopes_and_keys_are_separate():
    _, build = _counter()
    assert pf.frozen("c1", "a", build) == "render 1"
    assert pf.frozen("c2", "a", build) == "render 2"
    assert pf.frozen("c1", "b", build) == "render 3"


def test_no_scope_renders_live():
    calls, build = _counter()
    assert pf.frozen(None, "a", build) == "render 1"
    assert pf.frozen("", "a", build) == "render 2"


def test_an_owner_change_drops_every_capture():
    _, build = _counter()
    pf.frozen("c1", "a", build)
    pf.owner_changed("test")
    assert pf.frozen("c1", "a", build) == "render 2"


def test_an_owner_change_during_a_render_is_not_captured():
    calls = {"n": 0}

    def build():
        calls["n"] += 1
        if calls["n"] == 1:
            pf.owner_changed("edited while rendering")
        return f"render {calls['n']}"
    assert pf.frozen("c1", "a", build) == "render 1"
    assert pf.frozen("c1", "a", build) == "render 2", "a render overtaken by an owner edit was kept"


def test_an_idle_conversation_starts_fresh(monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(pf, "_clock", lambda: now["t"])
    _, build = _counter()
    pf.frozen("c1", "a", build)
    now["t"] += pf.IDLE_SECONDS - 1
    assert pf.frozen("c1", "a", build) == "render 1"
    now["t"] += pf.IDLE_SECONDS + 1
    assert pf.frozen("c1", "a", build) == "render 2"


def test_a_failing_render_is_empty_and_not_captured():
    calls = {"n": 0}

    def build():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("store locked")
        return "ok"
    assert pf.frozen("c1", "a", build) == ""
    assert pf.frozen("c1", "a", build) == "ok"


def test_the_number_of_conversations_held_is_bounded(monkeypatch):
    monkeypatch.setattr(pf, "MAX_SCOPES", 3)
    _, build = _counter()
    for i in range(5):
        pf.frozen(f"c{i}", "a", build)
    assert len(pf._SNAPSHOTS) == 3
    assert "c0" not in pf._SNAPSHOTS and "c4" in pf._SNAPSHOTS


def test_forget_drops_one_conversation():
    _, build = _counter()
    pf.frozen("c1", "a", build)
    pf.frozen("c2", "a", build)
    pf.forget("c1")
    assert "c1" not in pf._SNAPSHOTS and "c2" in pf._SNAPSHOTS
