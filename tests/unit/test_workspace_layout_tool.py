"""set_workspace_layout: fullscreen with the chat tray, by voice and in chat.

Per docs/reference/voice-tool-contract.md: declared for the live session with
its arguments, routed through _governed to its handler, internal (the owner's
own screen, ring 1), and told how to speak about what happened. The choice is
remembered per workspace (settings.workspace_layouts); LAYOUT_OK only when a
page that shows the workspace says it applied it.
"""
from __future__ import annotations

import threading

import pytest

from agent_friday.services import agent
from agent_friday.services import desktop_bus
from agent_friday.services import voice_engine as ve


@pytest.fixture(autouse=True)
def _pages():
    desktop_bus.reset()
    yield
    desktop_bus.reset()


def _layouts():
    from agent_friday.core import _load_settings
    return dict((_load_settings() or {}).get("workspace_layouts") or {})


# ── the contract ─────────────────────────────────────────────────────────────

def test_it_is_a_voice_tool_with_its_arguments():
    spec = next(t for t in ve._VOICE_LIVE_TOOLS if t[0] == "set_workspace_layout")
    assert spec[3] == ["fullscreen_chat"]
    assert spec[2]["fullscreen_chat"][0] == "boolean" and "workspace" in spec[2]
    assert ve._voice_tool_names().count("set_workspace_layout") == 1
    assert "LAYOUT_OK" in spec[1] and "LAYOUT_SAVED" in spec[1] and "not that it changed" in spec[1]


def test_it_is_a_chat_tool_on_the_owners_own_screen():
    from agent_friday.governance import action_gate
    from agent_friday.services import taint
    assert agent.CLAUDE_TOOL_HANDLERS["set_workspace_layout"] is agent._tool_set_workspace_layout
    assert "set_workspace_layout" in {t["name"] for t in agent.CLAUDE_TOOLS}
    assert agent.TOOL_RINGS["set_workspace_layout"] == 1
    assert "set_workspace_layout" in action_gate.INTERNAL_TOOLS
    assert taint.TOOL_ROLES["set_workspace_layout"] == {}


def test_voice_routes_it_to_its_handler(monkeypatch):
    called = []
    monkeypatch.setattr(agent, "_tool_set_workspace_layout", lambda inp: called.append(inp) or "ok")
    monkeypatch.setattr(agent, "_execute_tool", lambda t, a, handler=None, session_ctx=None: handler(a))
    out = ve._voice_tool_run("set_workspace_layout", {"fullscreen_chat": True}, lambda *a, **k: None,
                             {"conversation_id": "c1"})
    assert out == "ok" and called == [{"fullscreen_chat": True}]


# ── what it does ─────────────────────────────────────────────────────────────

def test_the_choice_is_remembered_per_workspace():
    out = agent._tool_set_workspace_layout({"workspace": "news", "fullscreen_chat": True})
    assert out.startswith("LAYOUT_SAVED:news"), out
    assert "no Friday page" in out
    assert _layouts() == {"news": "fullscreen_chat"}
    agent._tool_set_workspace_layout({"workspace": "calendar", "fullscreen_chat": True})
    agent._tool_set_workspace_layout({"workspace": "news", "fullscreen_chat": False})
    assert _layouts() == {"calendar": "fullscreen_chat"}


def test_a_spoken_name_is_resolved_and_a_wrong_one_is_refused():
    assert agent._tool_set_workspace_layout({"workspace": "my calendar", "fullscreen_chat": True}
                                            ).startswith("LAYOUT_SAVED:calendar")
    assert agent._tool_set_workspace_layout({"workspace": "the moon", "fullscreen_chat": True}
                                            ).startswith("LAYOUT_FAIL")
    assert "panel" in agent._tool_set_workspace_layout({"workspace": "settings", "fullscreen_chat": True})


def test_this_means_the_workspace_on_the_page_with_the_focus():
    desktop_bus.report_state("desk", {"kind": "desktop", "focused": False,
                                      "focused_window": {"workspace": "news"}})
    desktop_bus.report_state("tab", {"kind": "tab", "focused": True,
                                     "focused_window": {"workspace": "calendar"}})
    assert desktop_bus.focused_workspace() == "calendar"
    out = agent._tool_set_workspace_layout({"fullscreen_chat": True})
    assert out.startswith("LAYOUT_SAVED:calendar"), out
    desktop_bus.reset()
    assert agent._tool_set_workspace_layout({"fullscreen_chat": True}).startswith("LAYOUT_FAIL")


def test_ok_only_when_the_page_that_shows_it_says_it_applied_it(monkeypatch):
    monkeypatch.setattr(agent, "LAYOUT_ACK_S", 5.0)
    q = desktop_bus.subscribe("page-1", "chat")
    out = {}

    def call():
        out["text"] = agent._tool_set_workspace_layout({"workspace": "news", "fullscreen_chat": True})

    t = threading.Thread(target=call)
    t.start()
    msg = q.get(timeout=5)
    assert msg["type"] == "layout" and msg["workspace"] == "news" and msg["fullscreen_chat"] is True
    assert desktop_bus.ack(msg["id"], {"applied": True, "workspace": "news", "page": "desktop"})
    t.join(10)
    assert out["text"].startswith("LAYOUT_OK:news"), out


def test_a_page_that_does_not_answer_leaves_it_saved_not_ok(monkeypatch):
    monkeypatch.setattr(agent, "LAYOUT_ACK_S", 0.3)
    desktop_bus.subscribe("page-2", "chat")
    out = agent._tool_set_workspace_layout({"workspace": "news", "fullscreen_chat": True})
    assert out.startswith("LAYOUT_SAVED:news") and "no Friday page" not in out, out
