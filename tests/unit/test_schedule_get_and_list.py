"""schedule_job get and list: a schedule can be inspected, copied and recreated without losing its instructions, and a long
list is never cut mid-entry (Scheduler items 34 and 33). Synthetic schedules only."""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from agent_friday.services import builtin_overrides as bo
from agent_friday.services import schedule_tools, scheduler

PROMPT = "Summarise today’s news — café, naïve, 日本語, \U0001f600.\n\nLine two:\n\t- indented\r\nLast line."


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler, "SCHEDULES_FILE", tmp_path / "schedules.json")
    monkeypatch.setattr(scheduler, "RUNS_FILE", tmp_path / "runs.jsonl")
    monkeypatch.setattr(bo, "_file", lambda: tmp_path / "overrides.json")
    monkeypatch.setattr(scheduler, "BUILTIN_TASKS", {
        "news_morning": {"fn": lambda: None, "label": "Morning news briefing", "default_trigger": "daily",
                         "default_spec": {"hour": 7, "minute": 0}, "notify": "on_complete", "weekday_only": None,
                         "default_enabled": True},
        "front_page_evening": {"fn": lambda: None, "label": "Evening front page", "default_trigger": "daily",
                               "default_spec": {"hour": 18, "minute": 0}, "notify": "on_complete", "weekday_only": None,
                               "default_enabled": True}})
    monkeypatch.setattr(schedule_tools, "_caller_may_schedule", lambda: True)
    monkeypatch.setattr(schedule_tools, "_caller_is_owner_chat", lambda: True)
    scheduler._seed_and_reconcile()
    return tmp_path


def _call(**inp):
    return json.loads(schedule_tools.schedule_job(inp))


def _make_user_schedules(n):
    return [scheduler.register_schedule({"name": "Owner job %02d" % i, "trigger": "daily",
                                         "spec": {"hour": 8, "minute": i % 60},
                                         "task": {"kind": "agent_prompt", "prompt": "Do the owner thing %d" % i},
                                         "enabled": True})["id"] for i in range(n)]


# ── get ────────────────────────────────────────────────────────────────────────────

def test_get_returns_a_prompt_with_unicode_and_newlines_byte_for_byte(store):
    created = _call(action="create", prompt=PROMPT, name="Round trip", trigger="daily", spec={"hour": 9, "minute": 30})
    got = _call(action="get", schedule_id=created["schedule_id"])
    assert got["prompt"].encode("utf-8") == PROMPT.encode("utf-8")
    assert got["kind"] == "agent_prompt" and got["workflow"] is None
    assert got["trigger"] == "daily" and got["spec"] == {"hour": 9, "minute": 30}


def test_get_carries_everything_needed_to_recreate_the_schedule(store):
    created = _call(action="create", prompt="Check the sample", name="Pinned one", trigger="interval",
                    spec={"every_minutes": 45}, model="sample-model:1b", missed="skip")
    got = _call(action="get", schedule_id=created["schedule_id"])
    assert got["spec"] == {"every_minutes": 45} and got["missed_policy"] == "skip" and got["missed_policy_is_own"] is True
    assert got["model"] and got["model_pinned"] is True and got["notify"] and got["timeout_seconds"]
    assert got["builtin"] is False and "versions" not in got


def test_get_names_a_workflow_by_its_slug_not_its_body(store, monkeypatch):
    sid = scheduler.register_schedule({"name": "Flow", "trigger": "daily", "spec": {"hour": 7, "minute": 0},
                                       "task": {"kind": "workflow", "ref": "weekly-review"}, "enabled": True})["id"]
    got = _call(action="get", schedule_id=sid)
    assert got["workflow"] == "weekly-review" and got["prompt"] is None and got["kind"] == "workflow"


def test_get_of_a_built_in_carries_the_version_info_of_the_owners_edits(store):
    assert _call(action="get", schedule_id="sch_news_morning")["versions"]["latest"] == 0
    _call(action="update", schedule_id="sch_news_morning", trigger="daily", spec={"hour": 5, "minute": 15})
    got = _call(action="get", schedule_id="sch_news_morning")
    assert got["versions"]["latest"] == 1 and got["versions"]["has_owner_edits"] is True
    assert got["versions"]["recent"][0]["kind"] == "edit"
    assert got["versions"]["shipped_default"]["spec"] == {"hour": 7, "minute": 0}


def test_get_of_an_unknown_schedule_says_so(store):
    with pytest.raises(ValueError, match="no schedule"):
        schedule_tools.schedule_job({"action": "get", "schedule_id": "sch_missing"})


# ── list ───────────────────────────────────────────────────────────────────────────

def test_list_stays_short_and_does_not_carry_the_prompt(store):
    _call(action="create", prompt=PROMPT, name="Has a prompt", trigger="daily", spec={"hour": 9, "minute": 30})
    out = _call(action="list")
    assert all("prompt" not in row and "task" not in row for row in out["schedules"])
    assert any(row["name"] == "Has a prompt" for row in out["schedules"])


def test_list_with_builtin_false_returns_only_the_owners_schedules_and_true_only_the_built_ins(store):
    _make_user_schedules(3)
    mine = _call(action="list", builtin=False)
    assert mine["count"] == 3 and all(r["builtin"] is False for r in mine["schedules"])
    own = _call(action="list", builtin=True)
    assert own["count"] == 2 and {r["schedule_id"] for r in own["schedules"]} == {"sch_news_morning", "sch_front_page_evening"}


def test_list_filters_by_name(store):
    _make_user_schedules(4)
    assert _call(action="list", name_contains="job 02")["count"] == 1
    assert _call(action="list", name_contains="MORNING")["schedules"][0]["schedule_id"] == "sch_news_morning"
    assert _call(action="list", name_contains="no such name")["schedules"] == []


def test_a_37_schedule_list_of_the_owners_schedules_fits_without_being_cut(store):
    ids = _make_user_schedules(37)
    text = schedule_tools.schedule_job({"action": "list", "builtin": False})
    out = json.loads(text)                                           # whole JSON: nothing cut mid-entry
    assert out["count"] == 37 and out["returned"] == 37 and "omitted" not in out
    assert [r["schedule_id"] for r in out["schedules"]] == ids and len(text) <= schedule_tools.MAX_LIST_CHARS


def test_a_list_too_long_says_how_many_it_left_out_and_how_to_page_and_never_ends_mid_entry(store, monkeypatch):
    _make_user_schedules(30)
    monkeypatch.setattr(schedule_tools, "MAX_LIST_CHARS", 2500)
    first = _call(action="list", builtin=False)
    assert 0 < first["returned"] < 30 and first["omitted"] == 30 - first["returned"]
    assert first["next_offset"] == first["returned"] and "offset=%d" % first["next_offset"] in first["note"]
    seen, page = [r["schedule_id"] for r in first["schedules"]], first
    while page.get("omitted"):
        page = _call(action="list", builtin=False, offset=page["next_offset"])
        seen += [r["schedule_id"] for r in page["schedules"]]
    assert len(seen) == 30 and len(set(seen)) == 30                    # every schedule, once, in order


def test_a_single_row_longer_than_the_budget_is_still_returned_whole(store, monkeypatch):
    _make_user_schedules(2)
    monkeypatch.setattr(schedule_tools, "MAX_LIST_CHARS", 10)
    out = _call(action="list", builtin=False)
    assert out["returned"] == 1 and out["omitted"] == 1 and out["schedules"][0]["schedule_id"]


# ── item 33: update works on built-in and user schedules and persists across a restart ───────────────────

def _next_run_clock(sid):
    rec = next(r for r in scheduler.list_schedules() if r["id"] == sid)
    return datetime.fromtimestamp(rec["next_run"], tz=scheduler._now_central().tzinfo)


def test_changing_the_morning_news_to_0515_survives_a_restart_of_the_scheduler(store):
    assert (_next_run_clock("sch_news_morning").hour, _next_run_clock("sch_news_morning").minute) == (7, 0)
    out = _call(action="update", schedule_id="sch_news_morning", trigger="daily", spec={"hour": 5, "minute": 15})
    assert out["updated"] is True
    scheduler._seed_and_reconcile()                       # the restart: seed, migrate, reapply, read the store again
    clock = _next_run_clock("sch_news_morning")
    assert (clock.hour, clock.minute) == (5, 15)
    assert _next_run_clock("sch_front_page_evening").hour == 18          # the other built-in is untouched


def test_update_works_on_the_owners_own_schedule_too_and_a_built_in_still_cannot_be_deleted(store):
    [sid] = _make_user_schedules(1)
    _call(action="update", schedule_id=sid, spec={"hour": 6, "minute": 5})
    assert scheduler.get_schedule(sid)["spec"] == {"hour": 6, "minute": 5}
    with pytest.raises(ValueError, match="built-in routines can be paused, not deleted"):
        schedule_tools.schedule_job({"action": "delete", "schedule_id": "sch_news_morning"})
    assert scheduler.get_schedule("sch_news_morning") is not None


def test_update_with_nothing_to_change_or_an_impossible_time_is_refused(store):
    with pytest.raises(ValueError, match="Say what to change"):
        schedule_tools.schedule_job({"action": "update", "schedule_id": "sch_news_morning"})
    with pytest.raises(Exception):
        schedule_tools.schedule_job({"action": "update", "schedule_id": "sch_news_morning", "trigger": "daily",
                                     "spec": {"hour": 25, "minute": 0}})
