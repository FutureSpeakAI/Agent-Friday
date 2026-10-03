"""The Chat Hub's tools are on demand (docs/design/active/chat-hub.md; the
program rule: keep the catalogue ceilings, make tools on demand).

They live in the workspace-tools pool, not the always-on catalogue. A chat in
the hub (bound to a codebase, or filed in a project) gets them in its turn's
catalogue, with codebase_edit resident; the loader finds any of them from any
chat, by name or by a query (services/tool_catalogue.expand searches the
workspace pools too), so the ways into the hub need no rent.
"""
from __future__ import annotations

import pytest

from agent_friday.services import agent as ag
from agent_friday.services import codebases as cb
from agent_friday.services import conversations as convs
from agent_friday.services import projects
from agent_friday.services import tool_catalogue as tc


@pytest.fixture(autouse=True)
def _roots(monkeypatch, tmp_path):
    monkeypatch.setattr(projects, "_root", lambda: tmp_path / "projects")
    monkeypatch.setattr(convs, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(cb, "_root", lambda: tmp_path / "codebases")
    yield


def _names(tools):
    return {t["name"] for t in tools}


def test_the_hubs_tools_are_out_of_the_always_on_catalogue():
    always = _names(ag.CLAUDE_TOOLS)
    hub = _names(ag.WORKSPACE_TOOLS["hub"])
    assert set(ag.HUB_TOOL_NAMES) == hub
    assert not (hub & always), "a hub tool is still paid for on every turn"
    for n in ag.HUB_TOOL_NAMES:
        assert n in ag.CLAUDE_TOOL_HANDLERS, n
    # the loader's own text is a constant: it never lists tools
    assert tc.loader_spec(ag.CLAUDE_TOOLS)["description"] == tc.LOADER_DESCRIPTION


def test_a_hub_chat_gets_them_and_a_plain_chat_does_not():
    plain = convs.create("Plain")
    bound = convs.create("Build it")
    cb.create("Rent tracker", template="static", conversation_id=bound["id"])
    p = projects.create("Parks desk")
    filed = convs.create("Field notes"); convs.patch(filed["id"], project=p["id"])
    assert _names(ag.tools_for_workspace(None)) == _names(ag.CLAUDE_TOOLS)
    assert "codebase_edit" not in _names(ag.tools_for_workspace("chat", conversation_id=plain["id"]))
    assert set(ag.HUB_TOOL_NAMES) <= _names(ag.tools_for_workspace("chat", conversation_id=bound["id"]))
    assert set(ag.HUB_TOOL_NAMES) <= _names(ag.tools_for_workspace("news", conversation_id=filed["id"]))
    assert len(ag.tools_for_workspace("chat", conversation_id=bound["id"])) == len(ag.CLAUDE_TOOLS) + len(ag.HUB_TOOL_NAMES)


def test_in_a_hub_chat_codebase_edit_is_resident_and_the_rest_wait_for_the_loader():
    bound = convs.create("Build it")
    cb.create("Rent tracker", template="static", conversation_id=bound["id"])
    opening = {t["name"] for t in tc.opening_set(ag.tools_for_workspace("chat", conversation_id=bound["id"]))}
    assert "codebase_edit" in opening and tc.LOADER_NAME in opening
    assert "codebase_run" not in opening and "build_mode" not in opening
    plain_opening = {t["name"] for t in tc.opening_set(ag.tools_for_workspace(None))}
    assert "codebase_edit" not in plain_opening, "a plain chat pays nothing for the hub"


def test_any_chat_can_load_a_hub_tool_by_name_or_by_query():
    new, msg = tc.expand(ag.CLAUDE_TOOLS, ["codebase_run", "open_project"], [])
    assert sorted(t["name"] for t in new) == ["codebase_run", "open_project"] and "Loaded" in msg
    for query, name in (("build mode", "build_mode"), ("open project", "open_project"),
                        ("improve workspace", "improve_workspace")):
        found, _ = tc.expand(ag.CLAUDE_TOOLS, [], [], query=query)
        assert name in [t["name"] for t in found], (query, [t["name"] for t in found])
