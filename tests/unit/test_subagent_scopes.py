"""Unit tests for scoped subagents — scope resolution, allow/deny semantics,
ring ceiling, and step/time budgets."""
from __future__ import annotations

import pytest

from services import subagents


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(subagents, "SCOPES_FILE", tmp_path / "scopes.json")
    monkeypatch.setattr(subagents, "_SCOPED_TASKS", {})


def test_builtin_scopes_resolve():
    for name in ("readonly", "researcher", "writer", "recipe-runner"):
        assert subagents.get_scope(name).name == name


def test_unknown_scope_raises():
    with pytest.raises(KeyError):
        subagents.get_scope("nope")


def test_readonly_denies_network_tools():
    sc = subagents.get_scope("readonly")
    ok, why = sc.allows("search_web", 2)
    assert not ok and "ring" in why
    assert sc.allows("read_file", 0)[0]


def test_researcher_allowlist():
    sc = subagents.get_scope("researcher")
    assert sc.allows("search_web", 2)[0]
    ok, why = sc.allows("write_file", 1)
    assert not ok and "allow-list" in why


def test_recipe_runner_denylist():
    sc = subagents.get_scope("recipe-runner")
    assert sc.allows("search_news", 2)[0]
    ok, why = sc.allows("run_command", 2)
    assert not ok and "deny-listed" in why


def test_custom_scope_ring_is_clamped_below_os_control():
    saved = subagents.save_custom_scope({"name": "sneaky", "max_ring": 3})
    assert saved["max_ring"] == 2  # ring 3 (OS control) is never delegable
    sc = subagents.get_scope("sneaky")
    assert not sc.allows("click", 3)[0]


def test_scope_check_passes_unscoped_tasks():
    assert subagents.scope_check("not-a-scoped-task", "anything", 3) == (True, "")


def test_spawn_registers_scope_and_appends_contract(monkeypatch):
    captured = {}

    def fake_spawn(name, prompt, description="", **kw):
        captured.update(name=name, prompt=prompt)
        return "tid-123"

    import services.agent as agent
    monkeypatch.setattr(agent, "_spawn_task", fake_spawn)

    out = subagents.spawn_scoped_subagent("Research X", "Find facts about X.",
                                          scope="researcher")
    assert out["task_id"] == "tid-123"
    assert out["scope"]["name"] == "researcher"
    assert "SCOPE CONTRACT" in captured["prompt"]
    assert subagents.get_task_scope("tid-123").name == "researcher"

    # Governance hook: in-scope passes, out-of-scope is denied.
    assert subagents.scope_check("tid-123", "search_web", 2)[0]
    ok, why = subagents.scope_check("tid-123", "write_file", 1)
    assert not ok and "allow-list" in why


def test_step_budget_exhaustion(monkeypatch):
    import services.agent as agent
    monkeypatch.setattr(agent, "_spawn_task", lambda *a, **k: "tid-steps")
    subagents.save_custom_scope({"name": "tiny", "max_ring": 2, "max_steps": 2})
    subagents.spawn_scoped_subagent("t", "p", scope="tiny")

    assert subagents.scope_check("tid-steps", "search_web", 2)[0]
    assert subagents.scope_check("tid-steps", "search_web", 2)[0]
    ok, why = subagents.scope_check("tid-steps", "search_web", 2)
    assert not ok and "step budget" in why


def test_time_budget_exhaustion(monkeypatch):
    import services.agent as agent
    monkeypatch.setattr(agent, "_spawn_task", lambda *a, **k: "tid-time")
    subagents.save_custom_scope({"name": "brief", "max_ring": 2, "time_budget_s": 1})
    subagents.spawn_scoped_subagent("t", "p", scope="brief")
    subagents._SCOPED_TASKS["tid-time"]["spawned"] -= 10  # simulate elapsed time

    ok, why = subagents.scope_check("tid-time", "search_web", 2)
    assert not ok and "time budget" in why


def test_list_scoped_tasks(monkeypatch):
    import services.agent as agent
    monkeypatch.setattr(agent, "_spawn_task", lambda *a, **k: "tid-list")
    subagents.spawn_scoped_subagent("Listed", "p", scope="readonly")
    rows = subagents.list_scoped_tasks()
    assert rows and rows[0]["task_id"] == "tid-list"
    assert rows[0]["scope"] == "readonly"
