"""Home source adapters read real local shapes without refreshing or starting work."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from agent_friday.services import desktop_card_sources as sources


NOW = datetime(2026, 6, 15, 12).timestamp()


@pytest.fixture
def local_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "FRIDAY_DIR", tmp_path)
    return tmp_path


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def event(key="meeting", **extra):
    return {"id": key, "title": "Synthetic meeting", "start_time": datetime.fromtimestamp(NOW + 60).isoformat(),
            "end_time": datetime.fromtimestamp(NOW + 3600).isoformat(), **extra}


def test_calendar_reads_local_and_existing_cache_without_contacting_provider(local_sources, monkeypatch):
    from agent_friday.services import swr_cache
    path = local_sources / "calendar" / "local_events.json"
    write(path, [event(), event("private", off_record=True)])
    before = path.read_bytes()
    calls = []
    def peek(key):
        calls.append(key)
        return [event("remote-cached", description="Never copied into Home", attendees=["synthetic@example.test"])], NOW - 400
    monkeypatch.setattr(swr_cache, "peek", peek)
    monkeypatch.setattr(swr_cache, "get", lambda *_a, **_k: pytest.fail("Home must not start a cache refresh"))
    records, _stamp, detail = sources._calendar(NOW)
    assert [r["id"] for r in records] == ["local:meeting", "cached:remote-cached"]
    assert records[1]["state"] == "stale" and "never contacts" in detail
    assert "Never copied" not in json.dumps(records) and "synthetic@example" not in json.dumps(records)
    day = datetime.fromtimestamp(NOW).replace(hour=0, minute=0, second=0, microsecond=0)
    assert calls == ["calendar.range:" + day.isoformat() + "/" + (day + timedelta(days=1)).isoformat()]
    assert path.read_bytes() == before


@pytest.mark.parametrize("value", [{}, [event(start_time="broken")], [event(id=None)], ["invalid row"]])
def test_invalid_calendar_is_not_reported_as_no_events(local_sources, monkeypatch, value):
    from agent_friday.services import swr_cache
    monkeypatch.setattr(swr_cache, "peek", lambda _key: None)
    write(local_sources / "calendar" / "local_events.json", value)
    monkeypatch.setattr(sources, "_READERS", {**{kind: lambda now: ([], None, "Empty fixture") for kind in sources.KINDS}, "calendar": sources._calendar})
    assert sources.snapshot(NOW)["calendar"]["status"] == "unavailable"


def test_projects_read_only_metadata_without_migrating_legacy_or_opening_content(local_sources):
    root = local_sources / "projects"
    write(root / "one" / "project.json", {"id": "one", "name": "Synthetic project", "updated_at": NOW - 10,
        "files": [{"path": "private-file.txt"}], "codebases": ["repo-a"], "instructions": "Private instructions never copied"})
    write(root / "archived" / "project.json", {"id": "archived", "archived": True})
    write(root / "legacy" / "bible.json", {"title": "Old project"})
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    records, stamp, detail = sources._projects(NOW)
    assert len(records) == 1 and records[0]["id"] == "one" and stamp == NOW - 10
    assert "1 saved files" in records[0]["body"] and "1 connected codebases" in records[0]["body"]
    assert "Private instructions" not in json.dumps(records) and "private-file.txt" not in json.dumps(records)
    assert "no migration" in detail
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_project_catalog_and_single_file_are_bounded(local_sources, monkeypatch):
    monkeypatch.setattr(sources, "MAX_RECORDS", 1)
    for key in ("one", "two"):
        write(local_sources / "projects" / key / "project.json", {"id": key})
    with pytest.raises(ValueError, match="bounded snapshot"):
        sources._projects(NOW)
    path = local_sources / "large.json"
    path.write_bytes(b" " * (sources.MAX_SOURCE_BYTES + 1))
    with pytest.raises(ValueError, match="bounded snapshot"):
        sources._json(path, default=[])


def test_task_cards_use_registry_metadata_not_prompts_results_or_journal_reads(monkeypatch):
    from agent_friday.services import agent, task_journal
    monkeypatch.setattr(agent, "_task_snapshot", lambda: [
        {"task_id": "one", "name": "Synthetic task", "created": NOW - 30, "status": "running",
         "last_seen": NOW - task_journal.STALLED_AFTER_S - 1, "progress": 35,
         "prompt": "PRIVATE PROMPT", "result": "PRIVATE RESULT", "log": ["PRIVATE LOG"]},
        {"task_id": "hidden", "name": "PRIVATE TASK", "status": "running", "off_record": True}])
    monkeypatch.setattr(task_journal, "liveness", lambda *_a, **_k: pytest.fail("Home must not scan task journals"))
    records, stamp, detail = sources._tasks(NOW)
    assert len(records) == 1 and records[0]["state"] == "stalled"
    assert "35%" in records[0]["body"] and "Reported status" in records[0]["body"]
    assert stamp == NOW - task_journal.STALLED_AFTER_S - 1
    assert "PRIVATE" not in json.dumps(records) and "not an independent heartbeat" in detail


def test_schedule_snapshot_uses_real_next_run_logic_at_supplied_clock_without_execution(local_sources, monkeypatch):
    from agent_friday.services import scheduler
    path = local_sources / "schedules.json"
    monkeypatch.setattr(scheduler, "SCHEDULES_FILE", path)
    monkeypatch.setattr(scheduler, "_now_central", lambda: datetime(2035, 1, 1, tzinfo=timezone.utc))
    write(path, [{"id": "review", "name": "Review notes", "enabled": True, "trigger": "interval",
        "spec": {"every_minutes": 10}, "task": {"prompt": "PRIVATE TASK"}, "last_summary": "PRIVATE SUMMARY"},
        {"id": "off", "name": "Disabled", "enabled": False, "trigger": "once", "spec": {"at": NOW + 60}}])
    before = path.read_bytes()
    records, _stamp, _detail = sources._schedules(NOW)
    assert records[0]["next_run"] == NOW + 600 and records[1]["next_run"] is None
    assert "PRIVATE" not in json.dumps(records) and path.read_bytes() == before


def test_routines_use_actual_handler_mapping_and_do_not_claim_template_monitoring(local_sources, monkeypatch):
    from agent_friday.services import misc_engine
    from agent_friday.routes import workflows
    path = local_sources / "routine_status.json"
    monkeypatch.setattr(misc_engine, "ROUTINE_STATUS_FILE", path)
    monkeypatch.setattr(misc_engine, "ROUTINE_REGISTRY", [{"id": "ready", "label": "Ready routine"}, {"id": "template", "label": "Template"}])
    monkeypatch.setattr(workflows, "ROUTINE_TASKS", {"ready": "real-handler-reference"})
    write(path, {"ready": {"last_status": "launched", "last_run": "2026-06-15T10:00:00"}})
    before = path.read_bytes()
    records, _stamp, _detail = sources._routines(NOW)
    assert records[0]["available"] and records[0]["state"] == "launched"
    assert not records[1]["available"] and records[1]["state"] == "unimplemented"
    assert "no executable handler" in records[1]["body"] and path.read_bytes() == before


def test_activity_is_bounded_metadata_only_and_rolls_its_automatic_identity_each_day(local_sources, monkeypatch):
    from agent_friday.services import activity_ledger
    path = local_sources / "activity.jsonl"
    monkeypatch.setattr(activity_ledger, "LEDGER_FILE", path)
    path.write_text("\n".join(json.dumps(row) for row in [
        {"ts": NOW - 10, "kind": "tool_call", "description": "PRIVATE DESCRIPTION"},
        {"ts": NOW - 20, "kind": "model_invocation", "prompt": "PRIVATE PROMPT"},
        {"ts": NOW - 30, "kind": "subagent_spawn"},
        {"ts": NOW - 5, "kind": "tool_call", "off_record": True},
        {"ts": NOW - 86400, "kind": "tool_call"}]) + "\n", encoding="utf-8")
    before = path.read_bytes()
    records, stamp, detail = sources._activity(NOW)
    assert len(records) == 1 and stamp == NOW - 10
    assert "1 tool calls" in records[0]["body"] and "1 model calls" in records[0]["body"]
    assert "PRIVATE" not in json.dumps(records) and "partial on busy days" in detail
    assert path.read_bytes() == before
    monkeypatch.setattr(sources, "_READERS", {kind: (lambda now, k=kind: (records if k == "activity" else [], stamp, detail)) for kind in sources.KINDS})
    observation = sources.snapshot(NOW)
    first = sources.automatic(observation, NOW)[0]
    assert first["id"] == "auto-activity-" + datetime.fromtimestamp(NOW).date().isoformat()
    assert sources.automatic(observation, NOW + 86400)[0]["id"] != first["id"]


def test_partial_or_corrupt_activity_never_claims_an_empty_success(local_sources, monkeypatch):
    from agent_friday.services import activity_ledger
    path = local_sources / "activity.jsonl"
    path.write_text('{"kind":', encoding="utf-8")
    monkeypatch.setattr(activity_ledger, "LEDGER_FILE", path)
    with pytest.raises(ValueError):
        sources._activity(NOW)


def test_source_failures_are_independent_and_automatic_destinations_are_real(monkeypatch):
    def broken(_now):
        raise OSError("private path should not escape")
    def record(kind):
        row = sources._record(kind + "-one", "Synthetic " + kind, "Local metadata", updated=NOW,
            state="running" if kind == "task" else "failed", enabled=True, available=True, starts_at=NOW, expires=NOW + 60)
        return lambda _now: ([row], NOW, "Local only")
    monkeypatch.setattr(sources, "_READERS", {kind: broken if kind == "calendar" else record(kind) for kind in sources.KINDS})
    observation = sources.snapshot(NOW)
    assert observation["calendar"]["status"] == "unavailable" and observation["calendar"]["options"] == []
    assert "private path" not in json.dumps(observation)
    assert observation["project"]["view"] == "projects" and observation["task"]["view"] == "activity"
    cards = sources.automatic(observation, NOW)
    assert cards and all(card["source"]["checked_at"] == NOW for card in cards)
    for card in cards:
        action = card["actions"][0]
        if card["type"] in {"task", "activity", "project"}:
            assert action["view"] in {"projects", "activity"} and "workspace" not in action
        else:
            assert action["workspace"] in {"calendar", "settings", "workflows"}


def test_same_source_uses_same_id_across_updates_and_failed_reads(monkeypatch):
    rows = [sources._record("task-a", "First", "Reported running", state="running", updated=NOW)]
    monkeypatch.setattr(sources, "_READERS", {kind: (lambda now, k=kind: (rows if k == "task" else [], NOW, "Local only")) for kind in sources.KINDS})
    first = sources.automatic(sources.snapshot(NOW), NOW)[0]
    rows[0].update(title="Second", updated_at=NOW + 60)
    second = sources.automatic(sources.snapshot(NOW + 60), NOW + 60)[0]
    assert first["id"] == second["id"] and first["title"] != second["title"]
