"""The Chat Hub's tools are on demand (docs/design/active/chat-hub.md; the
program rule: keep the catalogue ceilings, make tools on demand).

They live in the workspace-tools pool, not the always-on catalogue. A chat in
the hub (bound to a codebase, or filed in a project) gets them in its turn's
catalogue, as index lines, with codebase_edit resident; any other chat can
still load one by name. The ways into the hub stay always-on.
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


def test_the_hubs_tools_are_out_of_the_always_on_catalogue_and_named_by_the_loader():
    always = _names(ag.CLAUDE_TOOLS)
    hub = _names(ag.WORKSPACE_TOOLS["hub"])
    assert set(ag.HUB_TOOL_NAMES) == hub
    assert not (hub & always), "a hub tool is still paid for on every turn"
    for n in ag.HUB_TOOL_NAMES:
        assert n in ag.CLAUDE_TOOL_HANDLERS, n
    # a plain chat's loader names the ways into the hub on one short line, so
    # artifact_put, improve_workspace and open_project are a load_tools away;
    # the rest of the pool is in a hub chat's own index, not a plain chat's
    loader = str(tc.loader_spec(ag.CLAUDE_TOOLS))
    for entry in ag.HUB_ENTRY_NAMES:
        assert entry in loader, entry + " is not named by the loader"
    assert "build_mode" not in loader and "codebase_run" not in loader


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


def test_in_a_hub_chat_codebase_edit_is_resident_and_the_rest_are_index_lines():
    bound = convs.create("Build it")
    cb.create("Rent tracker", template="static", conversation_id=bound["id"])
    turn = ag.tools_for_workspace("chat", conversation_id=bound["id"])
    opening = tc.opening_set(turn)
    full = {t["name"] for t in opening if (t.get("input_schema") or {}).get("properties") is not None and t["name"] != tc.LOADER_NAME}
    assert "codebase_edit" in full
    assert "codebase_run" not in full and "build_mode" not in full
    loader = next(t for t in opening if t["name"] == tc.LOADER_NAME)
    assert "codebase_run" in str(loader) and "build_mode" in str(loader), "the index names them"
    # a plain chat's opening set carries none of them, not even as index lines
    plain_opening = tc.opening_set(ag.tools_for_workspace(None))
    assert "codebase_edit" not in {t["name"] for t in plain_opening}
    assert "codebase_run" not in str(next(t for t in plain_opening if t["name"] == tc.LOADER_NAME))


def test_any_chat_can_still_load_a_hub_tool_by_name():
    new, msg = tc.expand(ag.CLAUDE_TOOLS, ["codebase_run"], [])
    assert [t["name"] for t in new] == ["codebase_run"] and "Loaded" in msg
