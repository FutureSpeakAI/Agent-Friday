"""By voice: "add my Obsidian vault" opens the folder window and speaks what to offer.

The tool adds nothing. The folders the owner picks come back as one Library
card that is decided on screen; a spoken yes never adds a folder. No test
opens a real dialog.
"""
from __future__ import annotations

import pytest

from agent_friday.services import desktop_bus, knowledge_folders as kf
from agent_friday.services.library import grants, principal as pr, tools

_ROWS = [
    {"kind": "documents", "label": "Your Documents folder", "name": "Documents", "size_label": "1.2 GB",
     "file_count": 3400, "approximate": False, "recommended": True, "added": False},
    {"kind": "obsidian", "label": "Obsidian vault", "name": "Journal Vault", "size_label": "40 MB",
     "file_count": 800, "approximate": False, "recommended": False, "added": False},
]


@pytest.fixture
def wired(monkeypatch):
    sent, picks, wrote = [], [], []
    monkeypatch.setattr(pr, "current", lambda: "owner")
    monkeypatch.setattr(kf, "suggestions", lambda principal=None, **k: [dict(r) for r in _ROWS])
    monkeypatch.setattr(kf, "start_voice_pick", lambda principal: picks.append(principal) or {"started": True})
    monkeypatch.setattr(desktop_bus, "send", lambda actions, **kw: sent.append(actions) or {"delivered": True})
    monkeypatch.setattr(grants, "add_scope", lambda *a, **k: wrote.append(a))
    monkeypatch.setattr(tools, "_loop_is_local", lambda: False)
    return {"sent": sent, "picks": picks, "wrote": wrote}


def test_it_opens_the_folder_window_and_speaks_the_suggestions(wired):
    out = tools.add_knowledge({})
    assert out.startswith("KNOWLEDGE_OK:") and wired["picks"] == ["owner"]
    assert "1.2 GB in 3,400 files" in out and "Obsidian vault" in out
    assert "folder window is open on their screen" in out


def test_it_adds_nothing_and_says_a_spoken_yes_does_not(wired):
    out = tools.add_knowledge({})
    assert wired["wrote"] == []
    assert "nothing is added until they pick folders and approve the card" in out
    assert "a spoken yes does not add anything" in out
    assert "do not offer the cloud choice for a vault or a private folder" in out


def test_a_cloud_voice_model_hears_no_folder_names_a_local_one_does(wired, monkeypatch):
    assert "Journal Vault" not in tools.add_knowledge({})
    monkeypatch.setattr(tools, "_loop_is_local", lambda: True)
    assert "Journal Vault" in tools.add_knowledge({})


def test_it_moves_the_screen_to_the_panel(wired):
    tools.add_knowledge({})
    assert wired["sent"] == [[{"type": "navigate", "workspace": "settings", "tab": "privacy",
                               "section": "Your knowledge"}]]


def test_a_window_already_open_is_said(wired, monkeypatch):
    monkeypatch.setattr(kf, "start_voice_pick", lambda principal: {"started": False, "reason": "already open"})
    assert "already open on their screen" in tools.add_knowledge({})


def test_no_screen_connected_still_opens_the_window_and_speaks(wired, monkeypatch):
    def boom(actions, **kw):
        raise RuntimeError("no desktop")
    monkeypatch.setattr(desktop_bus, "send", boom)
    assert tools.add_knowledge({}).startswith("KNOWLEDGE_OK:")


def test_an_account_without_a_library_gets_a_plain_refusal(monkeypatch):
    monkeypatch.setattr(pr, "current", lambda: None)
    assert tools.add_knowledge({}) == "The Library is not available to this account."


def test_the_tool_is_registered_like_the_other_library_tools():
    from agent_friday.governance import action_gate
    from agent_friday.services import agent, tool_catalogue
    assert tools.RINGS["add_knowledge"] == 0 and "add_knowledge" in tools.HANDLERS
    assert "add_knowledge" in action_gate.INTERNAL_TOOLS
    assert "add_knowledge" in agent.ON_DEMAND_TOOLS
    assert "add_knowledge" not in {t["name"] for t in agent.CLAUDE_TOOLS}      # never in the always-on catalogue
    assert "add_knowledge" in {t["name"] for t in agent.WORKSPACE_TOOLS["on_demand"]}
    assert tool_catalogue.groups_of("add_knowledge") == ["files", "workspace"]
    assert agent.CLAUDE_TOOL_HANDLERS["add_knowledge"] is tools.add_knowledge
