"""Settings controls that did nothing, each wired or removed, each pinned.

The audit behind this file traced every Settings control from its handler to
the server code that reads what it writes. These are the ones that failed:

* Voice engine "ElevenLabs": the server refused the value, so it could never
  be saved.
* Economy "Per-task cap": stored and echoed, never compared.
* Publishing "Staging host": echoed, never read; Instagram read its own store.
* Substack "Enable manual handoff": posted an empty body, saved nothing.
* "Artifact panel beside the chat": nothing read it.
* "Load fonts from Google Fonts": inert, because the font files ship in the
  tree; the row is gone.
* The MCP servers list read a field the route does not return.
* The chat prompt never received the name set in General.
* The gear menu's "Log conversations" was a second control for a setting that
  has one home in Privacy & Data.
"""
from __future__ import annotations

import os
import pathlib
import re
import uuid

import pytest

ROOT = pathlib.Path(os.environ.get("SETTINGS_TEST_ROOT") or pathlib.Path(__file__).resolve().parents[2])
INDEX = ROOT / "index.html"
APP = ROOT / "ui_parts" / "app.html"
UI = pytest.mark.parametrize("path", [INDEX, APP], ids=["index.html", "app.html"])


def _t(p):
    return pathlib.Path(p).read_text(encoding="utf-8")


# server ------------------------------------------------------------------

@pytest.mark.parametrize("value", ["elevenlabs", "cloud:elevenlabs", "ElevenLabs"])
def test_a_registered_cloud_voice_provider_can_be_saved_as_the_voice_engine(value):
    from agent_friday.routes.core_routes import _check_voice_enums
    assert _check_voice_enums({"voice_engine": value}) is None


def test_an_unknown_voice_engine_is_still_refused():
    from agent_friday.routes.core_routes import _check_voice_enums
    assert _check_voice_enums({"voice_engine": "nonesuch"})["status"] == "error"
    assert _check_voice_enums({"voice_engine": "cloud:nonesuch"})["status"] == "error"
    assert _check_voice_enums({"voice_engine": 7})["status"] == "error"


def test_the_per_task_cap_limits_one_reservation():
    from agent_friday.services import budget_enforcer as be
    ws = "dead-ctl-%s" % uuid.uuid4().hex[:8]
    be.set_policy(ws, monthly_cap_mψ=1_000_000, per_task_cap_mψ=5_000)
    assert be.reserve_budget(ws, 5_001) is False
    assert be.monthly_spend(ws) == 0
    assert be.reserve_budget(ws, 5_000) is True


def test_instagram_falls_back_to_the_staging_host_saved_in_publishing_channels(monkeypatch):
    from agent_friday import core
    from agent_friday.services.platforms import instagram
    cls = next(v for v in vars(instagram).values()
               if isinstance(v, type) and v.__module__ == instagram.__name__
               and hasattr(v, "_staging_base_url"))
    a = object.__new__(cls)
    a._config = {}
    monkeypatch.setattr(core, "_load_settings",
                        lambda: {"content": {"staging_base_url": " https://stage.example/x "}})
    assert a._staging_base_url() == "https://stage.example/x"
    a._config = {"staging_base_url": "https://own.example"}
    assert a._staging_base_url() == "https://own.example"


def test_artifact_put_refuses_while_the_panel_is_off(monkeypatch):
    from agent_friday import core
    from agent_friday.services import agent
    monkeypatch.setattr(core, "_load_settings", lambda: {"artifact_panel_enabled": False})
    out = agent._tool_artifact_put({"conversation_id": "c1", "kind": "html", "title": "t", "content": "x"})
    assert isinstance(out, str) and "turned off" in out and "Nothing was stored" in out


def test_the_chat_prompt_carries_the_name_set_in_general():
    from agent_friday.services import context_injection as ci
    assert any("Juniper" in l for l in ci._preferences_block({"agent_name": "Juniper"}))
    assert not any("named you" in l for l in ci._preferences_block({"agent_name": "AGENT FRIDAY"}))


# UI ----------------------------------------------------------------------

@UI
def test_the_fonts_row_is_gone_because_the_setting_is_inert(path):
    src = _t(path)
    assert "Load fonts from Google Fonts" not in src
    assert not re.search(r"web_fonts_from_google\s*:", src)


@UI
def test_publishing_channels_show_the_saved_staging_host(path):
    assert re.search(r"staging_base_url\)\s*setStaging\(", _t(path))


@UI
def test_substack_manual_handoff_saves_the_publication_address(path):
    src = _t(path)
    assert "Publication address" in src
    assert re.search(r"publication_url", src)


@UI
def test_the_mcp_list_reads_the_field_the_route_returns(path):
    src = _t(path)
    i = src.index("apiFetch('/api/mcp/servers')")
    assert "d.config" in src[i:i + 400]


@UI
def test_the_artifact_shell_honours_the_panel_switch(path):
    src = _t(path)
    assert "panelOff" in src and "artifact_panel_enabled" in src


@UI
def test_the_gear_menu_links_to_conversation_logging_instead_of_duplicating_it(path):
    src = _t(path)
    assert not re.search(r"saveAgentSettings\(\{\s*context_logging_enabled", src)


@UI
def test_phone_pointer_names_the_section_it_lives_in(path):
    src = _t(path)
    assert "The phone is off. Settings \\u2192 Phone." not in src
