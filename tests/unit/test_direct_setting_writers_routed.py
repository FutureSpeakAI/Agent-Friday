"""Audit B6: the tools that change a setting no longer write it on the model's word.

Seven tools wrote a setting at once, with no yes/no step (switch_model, set_workspace_layout, show_my_day with
a mode, big_mode, hologram_window set and reset, call_mode set_mode, podcast_format). Each now says what it would
change, raises ONE card and writes nothing until the owner approves. What is not a setting stays as it was:
status reads, showing the start screen's cluster, calibrating, and standing back for a call now.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.core import _load_settings
from agent_friday.governance import action_gate as ag
from agent_friday.services import agent, approvals as ap, desktop_bus
from agent_friday.services import setting_proposals as sp


@pytest.fixture
def world(tmp_path, monkeypatch, friday_dir):
    from agent_friday.services import dissent_gate as dg
    desktop_bus.reset()
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(ag, "verify_claws", lambda: (True, "ok"))
    monkeypatch.setattr(sp, "_store", lambda: tmp_path / "setting_changes.json")
    yield
    desktop_bus.reset()


def _pending():
    return [r for r in ap.list_approvals(kind="governed_action") if (r.get("payload") or {}).get("handler") == sp.HANDLER
            and r.get("status") == "pending"]


def _call(tool, **args):
    return agent.CLAUDE_TOOL_HANDLERS[tool](args)


def test_big_mode_is_held_for_a_yes(world):
    before = _load_settings().get("big_mode")
    out = _call("big_mode", mode="on" if before != "on" else "off")
    assert out.startswith("SETTING_NEEDS_YES"), out
    assert _load_settings().get("big_mode") == before
    assert len(_pending()) == 1


def test_call_mode_set_mode_is_held(world):
    before = _load_settings().get("call_mode")
    out = _call("call_mode", action="set_mode", mode="off" if before != "off" else "ask")
    assert out.startswith("SETTING_NEEDS_YES"), out
    assert _load_settings().get("call_mode") == before and len(_pending()) == 1


def test_show_my_day_with_a_mode_is_held_but_showing_it_is_not(world):
    before = _load_settings().get("landing_mode")
    out = _call("show_my_day", mode="never" if before != "never" else "always")
    assert out.startswith("SETTING_NEEDS_YES"), out
    assert _load_settings().get("landing_mode") == before and len(_pending()) == 1
    shown = _call("show_my_day")
    assert not shown.startswith("SETTING_"), shown
    assert len(_pending()) == 1, "showing the cluster raised no card"


def test_hologram_set_and_reset_are_held_but_status_is_not(world):
    before = dict(_load_settings().get("tracking") or {})
    out = _call("hologram_window", action="set", depth_strength=2.0)
    assert out.startswith("SETTING_NEEDS_YES") and "depth" in out.lower(), out
    out2 = _call("hologram_window", action="reset")
    assert out2.startswith("SETTING_NEEDS_YES") or out2.startswith("SETTING_UNCHANGED"), out2
    assert dict(_load_settings().get("tracking") or {}) == before
    assert not _call("hologram_window", action="status").startswith("SETTING_")


def test_podcast_format_is_held(world):
    import agent_friday.services.podcast_engine as pe
    cur = pe.format_for("")
    new = "duo" if cur != "duo" else "solo"
    out = json.loads(_call("podcast_format", routine="any", format=new))
    assert out["status"] == "needs_yes" and "SETTING_NEEDS_YES" in out["message"], out
    assert pe.format_for("") == cur and len(_pending()) == 1


def test_workspace_layout_is_held(world):
    out = _call("set_workspace_layout", workspace="calendar", fullscreen_chat=True)
    assert out.startswith("SETTING_NEEDS_YES"), out
    assert "calendar" not in (_load_settings().get("workspace_layouts") or {})
    assert len(_pending()) == 1


def test_switch_model_is_held(world, monkeypatch):
    from agent_friday.services import model_catalog
    monkeypatch.setattr(model_catalog, "build_catalog", lambda *a, **k: {"models": [
        {"id": "local-small", "label": "Small local", "local": True, "provider": "ollama-local"},
        {"id": "cloud-big", "label": "Big cloud", "local": False, "provider": "anthropic"}]})
    cur = (((_load_settings().get("capability_routing") or {}).get("reasoning")) or {}).get("model")
    out = _call("switch_model", model="cloud-big" if cur != "cloud-big" else "local-small")
    assert out.startswith("SETTING_NEEDS_YES"), out
    assert (((_load_settings().get("capability_routing") or {}).get("reasoning")) or {}).get("model") == cur
    assert len(_pending()) == 1
    if cur != "cloud-big":
        assert "cost money" in out


def test_calling_a_tool_twice_does_not_stack_a_second_card(world):
    before = _load_settings().get("big_mode")
    new = "on" if before != "on" else "off"
    _call("big_mode", mode=new)
    _call("big_mode", mode=new)
    assert len(_pending()) == 1


def test_a_status_read_or_a_no_op_never_raises_a_card(world):
    assert not _call("big_mode", mode="status").startswith("SETTING_")
    assert not _call("call_mode", action="status").startswith("SETTING_")
    cur = _load_settings().get("big_mode") or "auto"
    assert _call("big_mode", mode=cur).startswith("SETTING_UNCHANGED")
    assert _pending() == []


def test_every_setting_writer_is_listed_so_a_new_one_cannot_be_forgotten():
    """The tool table, the keys it writes and its label move together."""
    assert set(sp.TOOL_OF.values()) == set(sp.KEYS_OF) == set(sp.LABELS)
