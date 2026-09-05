"""2026-09-04: `_task_worker` / `_spawn_task` gained an optional `tools`
override so a scheduled task that knows its own job is narrow (see
scheduler.py's `sch_heartbeat`) doesn't have to pay CLAUDE_TOOLS' full
~13k-token registry on every call. The failure this guards against is silent:
a schedule record naming tools that don't exist (a typo, or a tool later
renamed) should degrade to "fewer tools available", not crash the run — and a
schedule with NO `tools` field must see exactly what every task saw before
this existed, the full registry.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import agent_friday.services.agent as agent_mod

CALENDAR_TOOL_NAMES = {"query_calendar", "find_calendar_events", "search_email"}


def _run_task_worker_capturing_tools(monkeypatch, *, tools):
    captured = {}

    def _fake_generate_agent(messages, system=None, tools=None, **kw):
        captured['tools'] = tools
        return "done", []

    monkeypatch.setattr(agent_mod, "_generate_agent", _fake_generate_agent)

    task_id = "test-task-tools-narrowing"
    with agent_mod.TASKS_LOCK:
        agent_mod.TASKS[task_id] = {}
    try:
        agent_mod._task_worker(task_id, "Test Task", "hello", tools=tools)
    finally:
        with agent_mod.TASKS_LOCK:
            agent_mod.TASKS.pop(task_id, None)
    return captured.get('tools')


class TestTaskToolsNarrowing:
    def test_no_tools_field_keeps_full_registry(self, monkeypatch):
        """The default (no `tools` on the schedule record) must be a no-op —
        _generate_agent gets tools=None and falls back to CLAUDE_TOOLS itself,
        exactly as every task worked before this override existed."""
        got = _run_task_worker_capturing_tools(monkeypatch, tools=None)
        assert got is None

    def test_named_tools_are_filtered_down_from_the_full_registry(self, monkeypatch):
        got = _run_task_worker_capturing_tools(
            monkeypatch, tools=["query_calendar", "search_email"])
        assert got is not None
        names = {t["name"] for t in got}
        assert names == {"query_calendar", "search_email"}
        # Confirm these came from the REAL registry (real schemas), not stubs.
        assert all("input_schema" in t for t in got)

    def test_unknown_tool_names_are_dropped_not_fatal(self, monkeypatch):
        """A stale/renamed name in a schedule record must not crash the run —
        it degrades to whatever subset DID match."""
        got = _run_task_worker_capturing_tools(
            monkeypatch, tools=["query_calendar", "this_tool_does_not_exist"])
        assert got is not None
        assert {t["name"] for t in got} == {"query_calendar"}

    def test_all_names_unrecognized_falls_back_to_full_registry(self, monkeypatch):
        """An empty match set is indistinguishable from 'no override' at the
        _generate_agent boundary — None, not an empty list that would leave
        the task with ZERO tools."""
        got = _run_task_worker_capturing_tools(
            monkeypatch, tools=["nonexistent_tool_a", "nonexistent_tool_b"])
        assert got is None

    def test_heartbeat_schedule_tools_all_resolve_in_the_real_registry(self):
        """Pins the actual sch_heartbeat tool list against CLAUDE_TOOLS so a
        future rename of query_calendar/find_calendar_events/search_email
        silently empties the heartbeat's tool set (see the previous test)
        instead of being caught here."""
        from agent_friday.services.scheduler import _DEFAULT_AGENT_SCHEDULES
        hb = next(r for r in _DEFAULT_AGENT_SCHEDULES if r["id"] == "sch_heartbeat")
        names = set(hb["task"]["tools"])
        assert names, "sch_heartbeat should declare a narrowed tool list"
        registry_names = {t["name"] for t in agent_mod.CLAUDE_TOOLS}
        assert names <= registry_names, (
            f"sch_heartbeat names tools not in CLAUDE_TOOLS: {names - registry_names}")
