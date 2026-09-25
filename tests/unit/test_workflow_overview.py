"""The Workflows screen: plain words in, a plain summary out.

What a draft says about timing must be exactly what the scheduler will do,
so the timetable is read by a parser these tests pin, not by a model. What it
says about asking first must never be *less* than the checkpoint will ask:
it is a prediction shown to the owner, and the checkpoint stays the gate.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from agent_friday.services import agent as ag
from agent_friday.services import scheduler as sch
from agent_friday.services import workflow_overview as wo

# 2026-09-25 is a Friday.
NOW = datetime(2026, 9, 25, 10, 0)


def when(text):
    return wo.parse_when(text, now=NOW)[0]


# ── reading a timetable ─────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("Every weekday at 7:30am, check the council agendas",
     {"trigger": "weekly", "spec": {"weekdays": [0, 1, 2, 3, 4], "hour": 7, "minute": 30}}),
    ("each morning summarize my inbox",
     {"trigger": "daily", "spec": {"hour": 8, "minute": 0}}),
    ("every day at 6 pm send me the top stories",
     {"trigger": "daily", "spec": {"hour": 18, "minute": 0}}),
    ("daily at noon, look for new court filings",
     {"trigger": "daily", "spec": {"hour": 12, "minute": 0}}),
    ("Every Monday and Thursday at 9, list upcoming deadlines",
     {"trigger": "weekly", "spec": {"weekdays": [0, 3], "hour": 9, "minute": 0}}),
    ("On Tuesdays in the evening, back up my notes",
     {"trigger": "weekly", "spec": {"weekdays": [1], "hour": 18, "minute": 0}}),
    ("weekends at 10am, pick three longreads for me",
     {"trigger": "weekly", "spec": {"weekdays": [5, 6], "hour": 10, "minute": 0}}),
    ("every 2 hours check the wire for breaking news",
     {"trigger": "interval", "spec": {"every_minutes": 120}}),
    ("hourly, watch the police scanner feed",
     {"trigger": "interval", "spec": {"every_minutes": 60}}),
    ("every week, tidy my research folder",
     {"trigger": "weekly", "spec": {"weekdays": [0], "hour": 9, "minute": 0}}),
    ("Monday through Friday 8:15 brief me",
     {"trigger": "weekly", "spec": {"weekdays": [0, 1, 2, 3, 4], "hour": 8, "minute": 15}}),
])
def test_recurring_timetables(text, expected):
    assert when(text) == expected


def test_tomorrow_is_a_single_run_at_that_time():
    w = when("tomorrow at 3pm, remind me to call the source back")
    assert w["trigger"] == "once"
    assert datetime.fromtimestamp(w["spec"]["at"]) == datetime(2026, 9, 26, 15, 0)


def test_a_single_named_day_is_the_next_one_not_every_week():
    w = when("on Monday at 9am, pull last week's page views")
    assert w["trigger"] == "once"
    assert datetime.fromtimestamp(w["spec"]["at"]) == datetime(2026, 9, 28, 9, 0)


def test_no_timing_means_run_by_hand():
    assert when("Collect reader tips from my email and sort them by beat") is None


def test_a_part_of_the_day_inside_the_task_is_not_a_timetable():
    # "morning" here is what to summarize, not when.
    assert when("Summarize the morning news for me") is None


def test_a_bare_time_repeats_daily():
    assert when("at 7am check the weather") == {
        "trigger": "daily", "spec": {"hour": 7, "minute": 0}}


def test_monthly_is_refused_out_loud_not_guessed():
    w, _, note = wo.parse_when("every month, total my freelance invoices", now=NOW)
    assert w is None
    assert "monthly" in note


def test_the_timing_words_are_taken_out_of_the_task():
    _, rest, _ = wo.parse_when(
        "Every weekday at 7:30am, check the council agendas and tell me what changed", now=NOW)
    assert rest == "Check the council agendas and tell me what changed"


def test_words_that_merely_start_like_and_are_left_alone():
    _, rest, _ = wo.parse_when("daily: Andrew's column, proofread it", now=NOW)
    assert rest.startswith("Andrew's column")


# ── describing a timetable ──────────────────────────────────────────────────

@pytest.mark.parametrize("w,text", [
    (None, "Only when you run it"),
    ({"trigger": "daily", "spec": {"hour": 7, "minute": 30}}, "Every day at 7:30 AM"),
    ({"trigger": "daily", "spec": {"hour": 12, "minute": 0}}, "Every day at noon"),
    ({"trigger": "weekly", "spec": {"weekdays": [0, 1, 2, 3, 4], "hour": 18, "minute": 0}},
     "Every weekday at 6 PM"),
    ({"trigger": "weekly", "spec": {"weekdays": [0, 2, 4], "hour": 9, "minute": 0}},
     "Every Monday, Wednesday and Friday at 9 AM"),
    ({"trigger": "weekly", "spec": {"weekday": 6, "hour": 9, "minute": 0}}, "Every Sunday at 9 AM"),
    ({"trigger": "interval", "spec": {"every_minutes": 60}}, "Every hour"),
    ({"trigger": "interval", "spec": {"every_minutes": 180}}, "Every 3 hours"),
    ({"trigger": "interval", "spec": {"every_minutes": 45}}, "Every 45 minutes"),
    ({"trigger": "idle_daily", "spec": {}}, "Once a day, while you're away from the computer"),
])
def test_describe_when(w, text):
    assert wo.describe_when(w) == text


def test_describe_a_single_run():
    at = datetime(2026, 9, 26, 15, 0).timestamp()
    assert wo.describe_when({"trigger": "once", "spec": {"at": at}}) == \
        "Once, on Saturday, September 26 at 3 PM"


def test_every_parsed_timetable_describes_back_to_what_was_asked():
    assert wo.describe_when(when("every weekday at 7:30am check agendas")) == \
        "Every weekday at 7:30 AM"


# ── asking first ────────────────────────────────────────────────────────────

def test_outward_words_are_named_in_plain_language():
    assert wo.asks_first(["Draft a reply and email it to my editor"]) == ["send email"]
    assert "post or publish online" in wo.asks_first(["Post the summary to Bluesky"])
    assert "spend money" in wo.asks_first(["Buy two tickets"])
    assert "change your calendar or invite people" in wo.asks_first(["Invite Sam to a call"])


def test_reading_and_summarizing_asks_nothing():
    assert wo.asks_first(["Read the council agenda and summarize what changed"]) == []


@pytest.mark.parametrize("text", [
    "Summarize my email from this morning",
    "Read the Washington Post front page and list the city stories",
    "Check my calendar and tell me what's next",
    "Sort the tips in order of urgency",
    "Remove duplicates from the list of tips",
    "Search my inbox for reader tips",
    # Wording a live model wrote for a workflow that only drafts a memo.
    "Search the inbox for emails that are reader tips (submissions, story leads, "
    "or suggestions from readers) and collect them",
    "Summarize the email the reporter sent this morning",
])
def test_topics_are_not_mistaken_for_actions(text):
    assert wo.asks_first([text]) == []


@pytest.mark.parametrize("text,label", [
    ("check the agendas and email me what changed", "send email"),
    ("Forward the best tip emails to my editor", "send email"),
    ("Reply to each reader who sent a tip", "send email"),
    ("Email it to my editor", "send email"),
    # A live model's wording for the last step of a legislature digest.
    ("Compose an email containing the ranked list of bills with their summaries "
     "and send it to the editor.", "send email"),
    ("Share it on LinkedIn", "post or publish online"),
    ("Text her the address", "send text messages"),
    ("Add the hearing to my calendar", "change your calendar or invite people"),
    ("Book a flight to Denver", "spend money"),
    ("Delete last month's drafts", "delete or move files"),
])
def test_acts_are_named(text, label):
    assert label in wo.asks_first([text])


# ── drafting ────────────────────────────────────────────────────────────────

def _model(reply):
    calls = []

    def gen(messages, **kw):
        calls.append((messages, kw))
        return reply
    gen.calls = calls
    return gen


def test_a_model_draft_supplies_name_summary_and_steps_but_not_the_timing():
    gen = _model(json.dumps({
        "name": "Council agenda watch",
        "summary": "Checks the council site and tells you what changed.",
        "steps": [{"name": "Read agendas", "prompt": "Open the council agendas page."},
                  {"name": "Report changes", "prompt": "Tell me what is new since last time."}]}))
    d = wo.draft_from_text("Every weekday at 7:30am, check the council agendas and "
                           "tell me what changed", generate=gen, now=NOW)
    assert d["source"] == "model"
    assert d["name"] == "Council agenda watch"
    assert [s["name"] for s in d["steps"]] == ["Read agendas", "Report changes"]
    assert d["when_text"] == "Every weekday at 7:30 AM"
    # The model is given the task with the timing taken out.
    assert "7:30" not in gen.calls[0][0][0]["content"]


def test_without_a_usable_model_the_request_becomes_one_step():
    for reply in ("I'm in demo mode.", "{not json", json.dumps({"name": "x", "steps": []})):
        d = wo.draft_from_text("daily at 6pm, list tomorrow's city meetings",
                               generate=_model(reply), now=NOW)
        assert d["source"] == "simple"
        assert d["steps"] == [{"name": "Do the task",
                               "prompt": "List tomorrow's city meetings"}]
        assert d["when"] == {"trigger": "daily", "spec": {"hour": 18, "minute": 0}}


def test_a_model_that_raises_falls_back_rather_than_failing():
    def boom(*a, **k):
        raise RuntimeError("no provider")
    assert wo.draft_from_text("check my mail", generate=boom)["source"] == "simple"


def test_asks_first_covers_steps_the_model_wrote():
    gen = _model(json.dumps({"name": "Tip reply", "summary": "",
                             "steps": [{"name": "Reply", "prompt": "Email each tipster back."}]}))
    assert wo.draft_from_text("handle the tips", generate=gen)["asks_first"] == ["send email"]


def test_an_empty_request_is_refused():
    with pytest.raises(ValueError):
        wo.draft_from_text("   ")


# ── saving, listing, removing ───────────────────────────────────────────────

@pytest.fixture
def stores(friday_dir, monkeypatch, tmp_path):
    monkeypatch.setattr(ag, "WORKFLOWS_DIR", tmp_path / "workflows")
    for f in (sch.SCHEDULES_FILE, sch.RUNS_FILE):
        if f.exists():
            f.unlink()
    sch._RUNNING.clear()
    yield


STEPS = [{"name": "Gather", "prompt": "Search my inbox for reader tips."},
         {"name": "Memo", "prompt": "Write a one-page memo of the best tips."}]
WEEKDAYS_730 = {"trigger": "weekly", "spec": {"weekdays": [0, 1, 2, 3, 4], "hour": 7, "minute": 30}}


def _item(name):
    return next(w for w in wo.overview()["workflows"] if w["name"] == name)


def test_a_scheduled_workflow_is_one_item_with_its_timing(stores):
    out = wo.save({"name": "Tip roundup", "steps": STEPS, "when": WEEKDAYS_730})
    assert out["slug"] == "tip-roundup" and out["schedule_id"]
    rec = sch.get_schedule(out["schedule_id"])
    assert rec["task"] == {"kind": "workflow", "ref": "tip-roundup"}
    assert rec["timeout_seconds"] >= wo.WORKFLOW_TIMEOUT_S
    items = [w for w in wo.overview()["workflows"]]
    assert len(items) == 1                       # not a chain plus a schedule
    it = items[0]
    assert it["when_text"] == "Every weekday at 7:30 AM"
    assert [s["name"] for s in it["steps"]] == ["Gather", "Memo"]
    assert it["next_run"] and it["last_run"] is None


def test_a_workflow_with_no_timing_has_no_schedule(stores):
    out = wo.save({"name": "Tip roundup", "steps": STEPS, "when": None})
    assert out["schedule_id"] is None
    assert _item("Tip roundup")["when_text"] == "Only when you run it"
    assert not [r for r in sch.list_schedules()]


def test_taking_the_timing_off_removes_the_schedule(stores):
    out = wo.save({"name": "Tip roundup", "steps": STEPS, "when": WEEKDAYS_730})
    wo.save({"name": "Tip roundup", "steps": STEPS, "when": None,
             "slug": out["slug"], "schedule_id": out["schedule_id"]})
    assert sch.get_schedule(out["schedule_id"]) is None


def test_renaming_moves_the_workflow_and_keeps_one_schedule(stores):
    out = wo.save({"name": "Tip roundup", "steps": STEPS, "when": WEEKDAYS_730})
    again = wo.save({"name": "Reader tips", "steps": STEPS, "when": WEEKDAYS_730,
                     "slug": out["slug"], "schedule_id": out["schedule_id"]})
    assert again["schedule_id"] == out["schedule_id"]
    assert ag.load_workflow_chain("tip-roundup") is None
    assert sch.get_schedule(out["schedule_id"])["task"]["ref"] == "reader-tips"
    assert [w["name"] for w in wo.overview()["workflows"]] == ["Reader tips"]


def test_a_name_already_in_use_is_refused(stores):
    wo.save({"name": "Tip roundup", "steps": STEPS})
    with pytest.raises(ValueError, match="already a workflow"):
        wo.save({"name": "Tip Roundup", "steps": STEPS})


def test_a_one_step_schedule_from_the_old_screen_shows_and_can_be_upgraded(stores):
    rec = sch.register_schedule({"name": "Agenda check", "trigger": "daily",
                                 "spec": {"hour": 7, "minute": 30},
                                 "task": {"kind": "agent_prompt", "prompt": "Check agendas."}})
    it = _item("Agenda check")
    assert it["slug"] is None and it["schedule_id"] == rec["id"]
    assert it["steps"] == [{"name": "Do the task", "prompt": "Check agendas."}]
    wo.save({"name": "Agenda check", "schedule_id": rec["id"],
             "steps": it["steps"] + [{"name": "Tell me", "prompt": "Summarize changes."}],
             "when": it["when"]})
    assert sch.get_schedule(rec["id"])["task"] == {"kind": "workflow", "ref": "agenda-check"}
    assert len(wo.overview()["workflows"]) == 1


def test_built_in_routines_are_listed_apart_and_cannot_be_edited(stores):
    sch._upsert(sch._normalize_record({"id": "sch_news", "name": "Morning news briefing",
                                       "trigger": "daily", "spec": {"hour": 7, "minute": 0},
                                       "task": {"kind": "builtin", "ref": "news"}},
                                      source="builtin"))
    ov = wo.overview()
    assert ov["workflows"] == []
    assert ov["routines"][0]["name"] == "Morning news briefing"
    assert ov["routines"][0]["when_text"] == "Every day at 7 AM"
    with pytest.raises(ValueError, match="built-in"):
        wo.save({"name": "x", "steps": STEPS, "schedule_id": "sch_news",
                 "when": {"trigger": "daily", "spec": {}}})
    with pytest.raises(ValueError, match="built-in"):
        wo.delete(schedule_id="sch_news")


def test_last_run_reads_the_schedule_history(stores):
    out = wo.save({"name": "Tip roundup", "steps": STEPS, "when": WEEKDAYS_730})
    sch._patch_record(out["schedule_id"], last_run_ts=1_790_000_000.0,
                      last_status="failed", last_summary="step 2 (Memo) failed: no tips")
    last = _item("Tip roundup")["last_run"]
    assert last["status"] == "failed" and "no tips" in last["summary"]


def test_last_run_of_a_chain_names_the_step_that_stopped_it(stores, monkeypatch):
    wo.save({"name": "Tip roundup", "steps": STEPS})
    monkeypatch.setattr(ag, "chain_run_status", lambda slug: {
        "state": "failed", "steps": [
            {"index": 0, "name": "Gather", "status": "completed", "started": 100, "ended": 200},
            {"index": 1, "name": "Memo", "status": "failed", "started": 200, "ended": 300,
             "reason": "the model timed out", "result_tail": ""}]})
    last = _item("Tip roundup")["last_run"]
    assert last["status"] == "failed" and last["at"] == 300
    assert last["summary"] == "Step 2 (Memo): the model timed out"


def test_removing_takes_the_schedule_with_it(stores):
    out = wo.save({"name": "Tip roundup", "steps": STEPS, "when": WEEKDAYS_730})
    assert wo.delete(slug=out["slug"], schedule_id=out["schedule_id"])
    assert wo.overview()["workflows"] == [] and sch.list_schedules() == []


def test_a_single_run_in_the_past_is_refused(stores):
    with pytest.raises(ValueError, match="already passed"):
        wo.save({"name": "Late", "steps": STEPS,
                 "when": {"trigger": "once", "spec": {"at": 1_000_000}}})


def test_empty_names_and_steps_are_refused(stores):
    with pytest.raises(ValueError, match="name"):
        wo.save({"name": " ", "steps": STEPS})
    with pytest.raises(ValueError, match="step"):
        wo.save({"name": "x", "steps": [{"name": "a", "prompt": " "}]})
