"""The ``after`` trigger: run job B when job A finishes successfully, no later than a deadline; if A is late or failed, B runs
anyway and says its data is stale, with the age (services/schedule_after; Scheduler item 32). Synthetic jobs only."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta

import pytest

from agent_friday.services import schedule_after as sa
from agent_friday.services import schedule_tools, scheduler, workflow_operations, workflow_overview
from agent_friday.user_errors import UserFacingValueError

TZ = scheduler._now_central().tzinfo
SPEC = {"job": "sch_news", "deadline_hour": 6, "deadline_minute": 0}


def _at(hour, minute=0, day=22):
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ)


def _a(**kw):
    return dict({"id": "sch_news", "name": "Morning news"}, **kw)


# ── the decision ───────────────────────────────────────────────────────────────────

def test_b_waits_for_a_while_a_has_not_run_and_the_deadline_is_ahead():
    assert sa.evaluate(SPEC, _a(last_success_ts=_at(5, 0, 21).timestamp()), _at(5, 30)) is None


def test_b_waits_while_a_is_running_or_has_a_retry_pending_before_the_deadline():
    base = dict(last_run_ts=_at(5, 10).timestamp(), last_status="failed")
    assert sa.evaluate(SPEC, _a(in_flight=True, **base), _at(5, 40)) is None
    assert sa.evaluate(SPEC, _a(retry_pending=True, **base), _at(5, 40)) is None


def test_b_runs_on_fresh_data_the_moment_a_has_succeeded_today():
    verdict = sa.evaluate(SPEC, _a(last_success_ts=_at(5, 16).timestamp(), last_status="complete"), _at(5, 20))
    assert verdict == {"stale": None}


def test_b_runs_at_the_deadline_when_a_is_late_and_says_how_stale_the_data_is():
    yesterday = _at(5, 15, 21).timestamp()
    verdict = sa.evaluate(SPEC, _a(last_success_ts=yesterday), _at(6, 0))
    stale = verdict["stale"]
    assert stale["why"] == "it had not run by the deadline" and stale["age_s"] == _at(6, 0).timestamp() - yesterday
    assert "24 hours 45 minutes" in sa.stale_words(stale)


def test_b_does_not_run_a_minute_before_the_deadline():
    assert sa.evaluate(SPEC, _a(last_success_ts=_at(5, 0, 21).timestamp()), _at(5, 59)) is None


def test_b_runs_at_once_and_says_stale_when_a_failed_today():
    up = _a(last_success_ts=_at(5, 15, 21).timestamp(), last_run_ts=_at(5, 16).timestamp(), last_status="failed")
    stale = sa.evaluate(SPEC, up, _at(5, 30))["stale"]
    assert stale["why"] == "it failed today" and stale["age_s"] == _at(5, 30).timestamp() - _at(5, 15, 21).timestamp()
    words = sa.stale_words(stale)
    assert words.startswith("Stale data:") and "Morning news" in words and "ago" in words
    prompt = sa.stale_prompt(stale)
    assert "stale" in prompt and "how old" in prompt and "old" in prompt


def test_a_job_that_never_succeeded_is_reported_as_having_no_success_on_record():
    stale = sa.evaluate(SPEC, _a(), _at(6, 30))["stale"]
    assert stale["age_s"] is None and "no successful run on record" in sa.stale_words(stale)


def test_a_job_it_follows_that_no_longer_exists_does_not_hold_it_past_the_deadline():
    assert sa.evaluate(SPEC, None, _at(5, 0)) is None
    assert "no longer exists" in sa.evaluate(SPEC, None, _at(6, 1))["stale"]["why"]


# ── refusing what cannot work ──────────────────────────────────────────────────────

RECORDS = [{"id": "sch_a", "trigger": "daily", "spec": {}}, {"id": "sch_b", "trigger": "after", "spec": {"job": "sch_a"}},
           {"id": "sch_c", "trigger": "after", "spec": {"job": "sch_b"}}]


def test_a_job_cannot_run_after_itself():
    with pytest.raises(UserFacingValueError, match="after itself"):
        sa.validate("sch_a", {"job": "sch_a"}, RECORDS)


@pytest.mark.parametrize("own,target", [("sch_a", "sch_b"), ("sch_a", "sch_c")])
def test_a_loop_is_refused_directly_or_through_others(own, target):
    with pytest.raises(UserFacingValueError, match="loop"):
        sa.validate(own, {"job": target}, RECORDS)


def test_an_unknown_or_unnamed_job_is_refused_and_a_fine_chain_is_accepted():
    with pytest.raises(UserFacingValueError, match="no schedule"):
        sa.validate("sch_x", {"job": "sch_nope"}, RECORDS)
    with pytest.raises(UserFacingValueError, match="which job"):
        sa.validate("sch_x", {}, RECORDS)
    sa.validate("sch_d", {"job": "sch_c"}, RECORDS + [{"id": "sch_d", "trigger": "daily"}])      # a chain a <- b <- c <- d
    sa.validate(None, {"job": "sch_a"}, RECORDS)                                               # a schedule not made yet


def test_the_timing_check_needs_the_job_and_a_deadline():
    good = workflow_operations.validate_timing({"trigger": "after", "spec": dict(SPEC)})
    assert good == {"trigger": "after", "spec": {"job": "sch_news", "deadline_hour": 6, "deadline_minute": 0}}
    for bad in ({"deadline_hour": 6}, {"job": "sch_news"}, {"job": "sch_news", "deadline_hour": 24},
                {"job": "", "deadline_hour": 6}):
        with pytest.raises(UserFacingValueError):
            workflow_operations.validate_timing({"trigger": "after", "spec": bad})


# ── through the scheduler ──────────────────────────────────────────────────────────

@pytest.fixture
def jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler, "SCHEDULES_FILE", tmp_path / "schedules.json")
    monkeypatch.setattr(scheduler, "RUNS_FILE", tmp_path / "runs.jsonl")
    from agent_friday.services import builtin_overrides
    monkeypatch.setattr(builtin_overrides, "_file", lambda: tmp_path / "overrides.json")
    monkeypatch.setattr(scheduler, "_RUNNING", set())
    news = scheduler.register_schedule({"id": "sch_news", "name": "Morning news", "trigger": "daily",
                                        "spec": {"hour": 5, "minute": 15}, "task": {"kind": "builtin", "ref": "news"},
                                        "enabled": True})
    digest = scheduler.register_schedule({"id": "sch_digest", "name": "Digest", "trigger": "after", "spec": dict(SPEC),
                                          "task": {"kind": "builtin", "ref": "digest"}, "enabled": True})
    return news, digest


def _due(sid, when):
    rec = scheduler.get_schedule(sid)
    return scheduler._is_due_on_schedule(rec, when), rec


def test_through_the_scheduler_b_waits_for_a_then_runs_when_a_finishes(jobs):
    due, _ = _due("sch_digest", _at(5, 30))
    assert due is False                                                       # A has not run
    scheduler._patch_record("sch_news", last_status="complete", last_run_ts=_at(5, 16).timestamp(),
                            last_success_ts=_at(5, 16).timestamp())
    due, rec = _due("sch_digest", _at(5, 30))
    assert due is True and rec["_stale"] is None                              # A finished: fresh


def test_through_the_scheduler_b_runs_at_the_deadline_when_a_is_late(jobs):
    scheduler._patch_record("sch_news", last_success_ts=_at(5, 15, 21).timestamp(), last_status="complete",
                            last_run_ts=_at(5, 15, 21).timestamp())
    assert _due("sch_digest", _at(5, 59))[0] is False
    due, rec = _due("sch_digest", _at(6, 0))
    assert due is True and rec["_stale"]["why"] == "it had not run by the deadline"


def test_b_does_not_run_twice_in_a_day(jobs):
    scheduler._patch_record("sch_digest", last_run_date="2026-11-22")
    scheduler._patch_record("sch_news", last_success_ts=_at(5, 16).timestamp())
    assert _due("sch_digest", _at(6, 30))[0] is False


def test_the_runs_output_says_stale_with_the_age_when_a_failed(jobs, monkeypatch):
    seen = {}
    monkeypatch.setattr(scheduler, "BUILTIN_TASKS", {"digest": {"fn": lambda: seen.setdefault("ran", True) and {"ok": True},
                                                                "label": "Digest", "default_trigger": "daily",
                                                                "default_spec": {}, "notify": "silent",
                                                                "weekday_only": None, "default_enabled": True}})

    class Sync(threading.Thread):
        def start(self):
            self.run()

    monkeypatch.setattr(scheduler.threading, "Thread", Sync)
    scheduler._patch_record("sch_news", last_success_ts=_at(5, 15, 21).timestamp(), last_status="failed",
                            last_run_ts=_at(5, 16).timestamp())
    due, rec = _due("sch_digest", _at(5, 30))
    assert due is True and rec["_stale"]["why"] == "it failed today"
    scheduler.dispatch(rec, manual=True)
    summary = scheduler.get_schedule("sch_digest")["last_summary"]
    assert seen.get("ran") and summary.startswith("Stale data:") and "Morning news" in summary
    assert "24 hours 15 minutes ago" in summary


def test_a_dependency_loop_or_self_dependency_is_refused_when_a_schedule_is_made_or_changed(jobs):
    with pytest.raises(UserFacingValueError, match="after itself"):
        scheduler.update_schedule("sch_digest", {"spec": {"job": "sch_digest", "deadline_hour": 6, "deadline_minute": 0}})
    with pytest.raises(UserFacingValueError, match="loop"):
        scheduler.update_schedule("sch_news", {"trigger": "after", "spec": {"job": "sch_digest", "deadline_hour": 5,
                                                                              "deadline_minute": 0}})
    assert scheduler.get_schedule("sch_news")["trigger"] == "daily"          # the refused change was not saved
    scheduler.register_schedule({"id": "sch_third", "name": "Third", "trigger": "after", "spec": dict(SPEC, job="sch_digest"),
                                 "task": {"kind": "builtin", "ref": "x"}})                       # news <- digest <- third
    with pytest.raises(UserFacingValueError, match="loop"):
        scheduler.update_schedule("sch_news", {"trigger": "after", "spec": dict(SPEC, job="sch_third")})


def test_the_list_shows_the_latest_start_as_its_next_run_and_says_what_it_waits_for(jobs):
    rows = {r["id"]: r for r in scheduler.list_schedules()}
    clock = datetime.fromtimestamp(rows["sch_digest"]["next_run"], tz=TZ)
    assert (clock.hour, clock.minute) == (6, 0)
    text = workflow_overview.describe_when({"trigger": "after", "spec": dict(SPEC)})
    assert "After sch_news finishes" in text and "no later than 6 AM" in text and "stale" in text


# ── the tool and the voice ─────────────────────────────────────────────────────────

def test_the_tool_schema_offers_the_trigger_and_its_fields():
    assert "after" in schedule_tools.TRIGGERS
    props = schedule_tools.TOOL_SCHEMAS[0]["input_schema"]["properties"]["spec"]["properties"]
    assert {"job", "deadline_hour", "deadline_minute"} <= set(props)


def test_the_model_can_create_an_after_job_through_schedule_job_and_a_loop_is_refused(jobs, monkeypatch):
    import json
    monkeypatch.setattr(schedule_tools, "_caller_may_schedule", lambda: True)
    out = json.loads(schedule_tools.schedule_job({
        "action": "create", "prompt": "Write the digest", "name": "Digest two", "trigger": "after",
        "spec": {"job": "sch_news", "deadline_hour": 6, "deadline_minute": 0}}))
    assert out["created"] is True and "After sch_news" in out["when"]
    with pytest.raises(Exception, match="loop|after itself"):
        schedule_tools.schedule_job({"action": "update", "schedule_id": "sch_news", "trigger": "after",
                                     "spec": {"job": out["schedule_id"], "deadline_hour": 5}})


def test_voice_borrows_the_same_tool_so_the_trigger_is_there_by_voice_too():
    """Chat and voice draw the tool from one place: agent registers schedule_tools.TOOL_SCHEMAS (and its handler) once."""
    import agent_friday.services.agent as agent
    assert agent._SCHEDULE_TOOLS is schedule_tools.TOOL_SCHEMAS and agent.CLAUDE_TOOL_HANDLERS["schedule_job"] is schedule_tools.schedule_job
    declared = {t["name"]: t for t in agent.WORKSPACE_TOOLS["on_demand"] if isinstance(t, dict) and "name" in t}
    schema = declared["schedule_job"]["input_schema"]["properties"]
    assert "after" in schema["trigger"]["enum"] and "deadline_hour" in schema["spec"]["properties"]
