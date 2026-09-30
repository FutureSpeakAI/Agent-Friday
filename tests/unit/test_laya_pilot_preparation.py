"""Advisory preparation cannot remove tools or bypass private-context gating."""
from types import SimpleNamespace

import pytest

from agent_friday.services import model_router as mr, tool_catalogue as tc


def _pilot(family, arm="assisted"):
    counts = {}
    return SimpleNamespace(
        arm=arm, plan=lambda: family,
        increment=lambda key, amount=1: counts.update({key: counts.get(key, 0) + amount}),
        counts=counts,
    )


def _tools():
    names = list(tc.ALWAYS_RESIDENT) + ["query_calendar", "search_email", "list_tasks",
                                      "find_calendar_events", "send_email", "delete_task"]
    return [{"name": name, "description": "Example tool", "input_schema": {}}
            for name in names]


def test_apps_preloads_read_schemas_and_retains_every_loader_entry():
    tools = _tools()
    pilot = _pilot("apps")
    opening = tc.opening_set(tools, pilot=pilot)
    names = [t["name"] for t in opening]
    assert set(tc.ALWAYS_RESIDENT).issubset(names)
    assert {"query_calendar", "search_email", "list_tasks"}.issubset(names)
    assert "send_email" not in names and "delete_task" not in names
    assert len(names) == len(set(names))
    for tool in tools:
        assert tool["name"] in opening[-1]["description"]
    assert pilot.counts["added_tools"] >= 3


@pytest.mark.parametrize("family,arm", [(None, "assisted"), ("web", "assisted"),
                                      ("apps", "control"), ("invalid", "assisted")])
def test_other_plans_keep_the_original_opening(family, arm):
    assert tc.opening_set(_tools(), pilot=_pilot(family, arm)) == tc.opening_set(_tools())


def test_optional_schema_budget_does_not_drop_baseline_or_loader():
    tools = _tools()
    for tool in tools:
        if tool["name"] not in tc.ALWAYS_RESIDENT:
            tool["description"] = "x" * 10000
    assert tc.opening_set(tools, pilot=_pilot("apps")) == tc.opening_set(tools)


@pytest.mark.parametrize("needs", [[], ["wiki"]])
def test_knowledge_plan_prioritizes_existing_context_without_extra_retrieval(monkeypatch, needs):
    from agent_friday.services.knowledge_graph import integration
    seen = []
    monkeypatch.setattr(integration, "knowledge_context_block", lambda message, max_items=3:
                        seen.append((message, max_items)) or ["Private synthetic graph pointer"])
    monkeypatch.setattr(mr, "_load_smart_context", lambda *a, **k: "")
    monkeypatch.setattr(mr, "_detect_context_needs", lambda *a, **k: needs)
    wiki = []
    monkeypatch.setattr(mr, "_get_wiki_context", lambda *a, **k: wiki.append(True) or [])
    mr._build_context_prompt("hello there", provider="cloud")
    normal_wiki_reads = list(wiki)
    wiki.clear()
    pilot = _pilot("knowledge")
    prompt, _ = mr._build_context_prompt("hello there", provider="cloud", pilot=pilot)
    assert seen == [] and wiki == normal_wiki_reads
    assert len(wiki) == int("wiki" in needs)
    assert "search_wiki" in prompt and "PERSONAL KNOWLEDGE" in prompt
    assert "Private synthetic graph pointer" not in prompt
    assert pilot.counts["context_blocks"] == 1


def test_control_and_not_ready_do_not_add_graph_retrieval(monkeypatch):
    from agent_friday.services.knowledge_graph import integration
    monkeypatch.setattr(integration, "knowledge_context_block",
                        lambda *a, **k: pytest.fail("pilot graph lookup ran"))
    for pilot in [_pilot("knowledge", "control"), _pilot(None)]:
        mr._build_context_prompt("hello there", pilot=pilot)


def test_selected_pilot_warms_at_startup_even_with_approval_laya_off(monkeypatch):
    from agent_friday import server
    from agent_friday.services import decisions, laya_backend
    calls = []
    monkeypatch.setattr(server, "_TESTING", False)
    monkeypatch.setattr(server, "_load_settings", lambda: {"laya_pilot_enabled": True})
    monkeypatch.setattr(decisions, "active_backend", lambda: "keyword")
    monkeypatch.setattr(decisions, "shadow_backend", lambda: None)
    monkeypatch.setattr(laya_backend, "start_warming", lambda: calls.append("warm"))
    server._register_decision_backends()
    assert calls == ["warm"]
