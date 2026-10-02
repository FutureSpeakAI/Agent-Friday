"""`artifact_put`: the one tool a model uses to make something for the panel.

It is INTERNAL for the gate (it writes only to Friday's own artifact store),
resident in the tool set only while the panel is enabled, and it reports into
the conversation that is asking.
"""
from __future__ import annotations

import pytest

from agent_friday.governance import action_gate
from agent_friday.services import agent as ag
from agent_friday.services import artifacts as art
from agent_friday.services import tool_catalogue as TC


@pytest.fixture(autouse=True)
def _store(monkeypatch, tmp_path):
    monkeypatch.setattr(art, "_root", lambda: tmp_path / "artifacts")
    yield


def _schema():
    hits = [t for t in ag.CLAUDE_TOOLS if t.get("name") == "artifact_put"]
    assert len(hits) == 1, "artifact_put must be declared exactly once"
    return hits[0]


def test_the_tool_is_declared_with_the_spec_arguments():
    s = _schema()
    props = s["input_schema"]["properties"]
    for p in ("kind", "title", "content", "artifact_id", "conversation_id", "meta"):
        assert p in props, p
    assert set(s["input_schema"]["required"]) == {"kind", "title", "content"}
    assert set(props["kind"]["enum"]) == set(art.KINDS)


def test_the_tool_has_a_handler_and_is_internal_for_the_gate():
    assert "artifact_put" in ag.CLAUDE_TOOL_HANDLERS
    klass, _why = action_gate.classify("artifact_put", {"kind": "markdown", "title": "t", "content": "c"})
    assert klass == action_gate.INTERNAL


def test_the_handler_writes_into_the_asking_conversation():
    tok = ag._CURRENT_CONVERSATION.set("conv-asking")
    try:
        out = ag.CLAUDE_TOOL_HANDLERS["artifact_put"](
            {"kind": "markdown", "title": "Draft", "content": "# hi"})
    finally:
        ag._CURRENT_CONVERSATION.reset(tok)
    assert out["artifact_id"]
    assert out["version"] == 1
    assert art.get("conv-asking", out["artifact_id"])["content"] == "# hi"


def test_the_handler_updates_when_given_an_artifact_id():
    first = art.put("conv-x", kind="markdown", title="Draft", content="v1")
    out = ag.CLAUDE_TOOL_HANDLERS["artifact_put"](
        {"conversation_id": "conv-x", "artifact_id": first["id"],
         "kind": "markdown", "title": "Draft", "content": "v2"})
    assert out["version"] == 2
    assert art.get("conv-x", first["id"])["content"] == "v2"


def test_the_handler_refuses_a_bad_kind_out_loud():
    out = ag.CLAUDE_TOOL_HANDLERS["artifact_put"](
        {"conversation_id": "conv-x", "kind": "slides", "title": "t", "content": "c"})
    assert isinstance(out, str) and "kind" in out.lower()
    assert art.list_for("conv-x") == []


def test_the_handler_needs_a_conversation():
    tok = ag._CURRENT_CONVERSATION.set(None)
    try:
        out = ag.CLAUDE_TOOL_HANDLERS["artifact_put"](
            {"kind": "markdown", "title": "t", "content": "c"})
    finally:
        ag._CURRENT_CONVERSATION.reset(tok)
    assert isinstance(out, str) and "conversation" in out.lower()


def _tools():
    return [{"name": n, "description": "x. y", "input_schema": {"type": "object", "properties": {}}}
            for n in ("search_web", "artifact_put", "tool_a")]


def test_artifact_put_loads_through_the_index_and_is_never_resident():
    """The opening set pays nothing for the panel's tool: its index line names
    it and it loads like every other tool (tests/unit/test_latency_budget.py).
    The always-resident tools stay resident whatever the panel setting."""
    on = {n["name"] for n in TC.resident(_tools(), settings={"artifact_panel_enabled": True})}
    off = {n["name"] for n in TC.resident(_tools(), settings={"artifact_panel_enabled": False})}
    assert "artifact_put" not in on and "artifact_put" not in off
    assert "search_web" in on and "search_web" in off
    assert "artifact_put" in {t["name"] for t in TC.opening_set(_tools())} or any(
        "artifact_put" in (t.get("description") or "") for t in TC.opening_set(_tools())), "the index must name it"


def test_the_panel_is_enabled_by_default():
    from agent_friday.core import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["artifact_panel_enabled"] is True
