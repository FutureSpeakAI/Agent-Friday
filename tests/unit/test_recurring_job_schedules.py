"""Saved jobs run on a real recurring timetable, on the model they are pinned to.

Field report: saved jobs could be pinned to a model but only started by hand,
and Friday said "a weekly email is scheduled" when nothing backed it. What this
file holds:

* a schedule fires at its computed time, on a fake clock, once per slot;
* next-run maths for daily / weekly / monthly, including the week wrap, the
  31st in a short month, a leap February, the last day of the month, a year
  wrap, and local time kept across a daylight-saving change;
* the timetable lives in schedules.json, so a restart finds it, and a slot
  missed while Friday was closed runs once at the next start (or, if the owner
  chose skip, is recorded as missed and not run);
* a run whose pinned model is unavailable is HELD with its reason and never
  sent to another model; an available pin runs on exactly that model;
* the schedule list says each job's model, status and next run;
* a "scheduled" claim in a reply needs a receipt from a tool that wrote a
  timetable (`schedule_job` create), not a read and not nothing;
* the `schedule_job` tool answers with the schedule id and next run, and a
  schedule that will send things later is outward (asks first).
Synthetic data only.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from agent_friday.services import scheduler as s

CT = ZoneInfo("America/Chicago")
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clean_store(friday_dir, monkeypatch):
    for f in (s.SCHEDULES_FILE, s.RUNS_FILE):
        if f.exists():
            f.unlink()
    s._RUNNING.clear()
    # The tick's other chores are not under test here.
    for name in ("_reclaim_expired_lease", "_away_drain_tick", "_meeting_retention_tick"):
        monkeypatch.setattr(s, name, lambda: None)
    monkeypatch.setattr(s, "_deferred_for_quiet", lambda rec, now=None: False)
    yield
    s._RUNNING.clear()


def _ts(*a):
    return datetime(*a, tzinfo=CT).timestamp()


def _local(ts):
    return datetime.fromtimestamp(ts, CT)


def _user(trigger, spec, *, created, **extra):
    rec = {"name": "Synthetic job", "trigger": trigger, "spec": spec,
           "task": {"kind": "agent_prompt", "prompt": "Summarise the synthetic notes."},
           "created": created}
    rec.update(extra)
    return s.register_schedule(rec)


# ── next run: daily / weekly / monthly edges, DST-safe local time ────────────

def _next(rec, now):
    rec = dict(rec, created=now.timestamp() - 1)     # made just now: no earlier slot is owed
    ts = s._next_run_ts(rec, now)
    assert ts is not None, "no next run was computed"
    return _local(ts)


def test_weekly_next_run_wraps_from_sunday_evening_to_monday():
    rec = {"enabled": True, "trigger": "weekly", "source": "user", "created": 0,
           "spec": {"weekdays": [0], "hour": 9, "minute": 0}}
    nxt = _next(rec, datetime(2026, 10, 18, 20, 0, tzinfo=CT))          # a Sunday
    assert (nxt.date().isoformat(), nxt.hour, nxt.minute) == ("2026-10-19", 9, 0)


@pytest.mark.parametrize("day,now,want", [
    (31, datetime(2026, 2, 1, 12, 0, tzinfo=CT), "2026-02-28"),   # Feb has no 31st: its last day
    (31, datetime(2028, 2, 1, 12, 0, tzinfo=CT), "2028-02-29"),   # leap year
    (-1, datetime(2026, 4, 2, 12, 0, tzinfo=CT), "2026-04-30"),   # last day of the month
    (15, datetime(2026, 12, 20, 12, 0, tzinfo=CT), "2027-01-15"),  # past this month's: next year
    (31, datetime(2026, 1, 31, 7, 0, tzinfo=CT), "2026-01-31"),   # later today
])
def test_monthly_next_run_edges(day, now, want):
    rec = {"enabled": True, "trigger": "monthly", "source": "user", "created": 0,
           "spec": {"day": day, "hour": 8, "minute": 30}}
    nxt = _next(rec, now)
    assert (nxt.date().isoformat(), nxt.hour, nxt.minute) == (want, 8, 30)


def test_monthly_keeps_its_local_time_across_the_autumn_clock_change():
    # US clocks go back on 2026-11-01: 9:00 on Nov 1st must still be 9:00 local.
    rec = {"enabled": True, "trigger": "monthly", "source": "user", "created": 0,
           "spec": {"day": 1, "hour": 9, "minute": 0}}
    nxt = _next(rec, datetime(2026, 10, 15, 12, 0, tzinfo=CT))
    assert (nxt.month, nxt.day, nxt.hour, nxt.minute) == (11, 1, 9, 0)
    assert nxt.utcoffset() == timedelta(hours=-6), "9:00 on Nov 1 is CST, not CDT"


def test_a_time_inside_the_spring_forward_gap_runs_just_after_it_and_weekly_keeps_its_hour():
    daily = {"enabled": True, "trigger": "daily", "source": "user", "created": 0,
             "spec": {"hour": 2, "minute": 30}}
    nxt = _next(daily, datetime(2026, 3, 7, 12, 0, tzinfo=CT))           # clocks go forward on Mar 8
    assert (nxt.month, nxt.day, nxt.hour, nxt.minute) == (3, 8, 3, 30)
    weekly = {"enabled": True, "trigger": "weekly", "source": "user", "created": 0,
              "spec": {"weekdays": [0], "hour": 9, "minute": 0}}
    nxt = _next(weekly, datetime(2026, 3, 7, 12, 0, tzinfo=CT))
    assert (nxt.month, nxt.day, nxt.hour) == (3, 9, 9)


# ── it fires at the computed time, on a fake clock ───────────────────────────

class _Clock:
    def __init__(self, monkeypatch):
        self.now = None
        self.fired = []
        monkeypatch.setattr(s, "_now_central", lambda: self.now)
        monkeypatch.setattr(s, "dispatch", self._dispatch)

    def _dispatch(self, rec, **kw):
        # What dispatch records as a run starts, without starting a thread.
        self.fired.append((rec["id"], self.now, dict(rec)))
        s._patch_record(rec["id"], **s._run_mark(rec, self.now, self.now.timestamp()))
        return "run_x"

    def tick_at(self, *a):
        self.now = datetime(*a, tzinfo=CT)
        s._tick()


def test_a_monthly_schedule_fires_at_its_computed_time_and_once_per_slot(monkeypatch):
    clock = _Clock(monkeypatch)
    rec = _user("monthly", {"day": 15, "hour": 7, "minute": 0}, created=_ts(2026, 10, 1, 9, 0))
    clock.now = datetime(2026, 10, 1, 9, 0, tzinfo=CT)
    assert _local(s.list_schedules()[0]["next_run"]) == datetime(2026, 10, 15, 7, 0, tzinfo=CT)
    clock.tick_at(2026, 10, 15, 6, 59)
    assert clock.fired == [], "it fired before its time"
    clock.tick_at(2026, 10, 15, 7, 0)
    assert [f[0] for f in clock.fired] == [rec["id"]]
    clock.tick_at(2026, 10, 15, 7, 1)
    clock.tick_at(2026, 10, 15, 12, 0)
    assert len(clock.fired) == 1, "it fired twice for one slot"
    clock.now = datetime(2026, 10, 15, 12, 0, tzinfo=CT)
    assert _local(s.list_schedules()[0]["next_run"]) == datetime(2026, 11, 15, 7, 0, tzinfo=CT)
    clock.tick_at(2026, 11, 15, 7, 0)
    assert len(clock.fired) == 2


def test_the_timetable_is_on_disk_and_a_slot_missed_while_closed_runs_once_at_the_next_start(monkeypatch):
    clock = _Clock(monkeypatch)
    rec = _user("daily", {"hour": 9, "minute": 0}, created=_ts(2026, 10, 1, 8, 0),
                last_run_ts=_ts(2026, 10, 10, 9, 0))
    # A restart reads the store from disk: the schedule and its choices are there.
    stored = json.loads(s.SCHEDULES_FILE.read_text(encoding="utf-8"))
    assert [(r["id"], r["trigger"], r["missed"]) for r in stored] == [(rec["id"], "daily", "run_once")]
    s._RUNNING.clear()
    # Friday was closed from the 10th until 08:00 on the 13th: two slots passed.
    clock.tick_at(2026, 10, 13, 8, 0)
    assert len(clock.fired) == 1, "a missed slot did not run at the next start"
    assert clock.fired[0][2].get("_missed_slot") == _ts(2026, 10, 12, 9, 0)
    clock.tick_at(2026, 10, 13, 8, 1)
    assert len(clock.fired) == 1, "the missed slots ran more than once"
    clock.tick_at(2026, 10, 13, 9, 0)
    assert len(clock.fired) == 2, "today's own slot still runs"


def test_a_job_set_to_skip_records_the_missed_slot_and_waits_for_the_next(monkeypatch):
    clock = _Clock(monkeypatch)
    rec = _user("daily", {"hour": 9, "minute": 0}, created=_ts(2026, 10, 1, 8, 0),
                last_run_ts=_ts(2026, 10, 10, 9, 0), missed="skip")
    clock.tick_at(2026, 10, 13, 8, 0)
    assert clock.fired == []
    hist = s.run_history(rec["id"], limit=5)
    assert hist and hist[0]["status"] == "missed" and "skip" in hist[0]["summary"]
    row = s.list_schedules()[0]
    assert row["missed_text"].endswith("it is skipped until its next time.")
    assert _local(row["next_run"]) == datetime(2026, 10, 13, 9, 0, tzinfo=CT)
    clock.tick_at(2026, 10, 13, 9, 0)
    assert len(clock.fired) == 1


def test_a_changed_timetable_does_not_count_older_slots_as_missed(monkeypatch):
    clock = _Clock(monkeypatch)
    rec = _user("daily", {"hour": 9, "minute": 0}, created=_ts(2026, 10, 1, 8, 0),
                last_run_ts=_ts(2026, 10, 10, 9, 0))
    s.update_schedule(rec["id"], {"spec": {"hour": 6, "minute": 0}})
    # The edit happens now (real time); every slot before it is not "missed".
    clock.now = datetime.fromtimestamp(time.time() + 60, CT)
    s._tick()
    assert clock.fired == []


# ── the pinned model: held when unavailable, never moved ─────────────────────

def _wait_for_run(sid):
    for _ in range(100):
        hist = s.run_history(sid, limit=1)
        if hist:
            return hist[0]
        time.sleep(0.05)
    raise AssertionError("the run never finished")


@pytest.fixture
def spawned(monkeypatch):
    from agent_friday.services import agent
    from agent_friday.services import local_only_guard
    calls = []

    def fake_spawn(name, prompt, **kw):
        calls.append(dict(kw, pinned=local_only_guard.pinned_model()))
        return "task_synthetic"
    monkeypatch.setattr(agent, "_spawn_task", fake_spawn)
    monkeypatch.setattr(agent, "_task_snapshot", lambda tid: {"status": "complete", "result": "done"})
    return calls


def _cloud_service(monkeypatch, up):
    from agent_friday.services import provider_registry, visible_jobs
    monkeypatch.setattr(provider_registry.ProviderRegistry, "is_provider_available",
                        lambda self, name: up)
    monkeypatch.setattr(visible_jobs, "is_local_model", lambda m, provider=None: False)


def test_a_run_whose_pinned_cloud_model_is_unavailable_is_held_with_the_reason(monkeypatch, spawned):
    _cloud_service(monkeypatch, up=False)
    rec = _user("weekly", {"weekdays": [0], "hour": 7, "minute": 0}, created=0)
    s.update_schedule(rec["id"], {"task": dict(rec["task"], model="claude-synthetic-1")})
    s.dispatch(s.get_schedule(rec["id"]), manual=True)
    run = _wait_for_run(rec["id"])
    assert spawned == [], "the job ran although its pinned model is unavailable"
    assert run["status"] == "held"
    assert "claude-synthetic-1" in run["summary"] and "not moved to another model" in run["summary"]
    row = s.list_schedules()[0]
    assert (row["status"], row["model"], row["model_pinned"]) == ("held", "claude-synthetic-1", True)
    assert "not moved" in row["status_reason"]


def test_a_run_whose_pinned_local_model_is_not_serving_is_held(monkeypatch, spawned):
    from agent_friday.services import local_call, visible_jobs
    monkeypatch.setattr(visible_jobs, "is_local_model", lambda m, provider=None: True)
    monkeypatch.setattr(local_call, "describe_dispatch", lambda m, daemon_tags=None: {"route": "unreachable"})
    rec = _user("daily", {"hour": 7, "minute": 0}, created=0,
                task={"kind": "agent_prompt", "prompt": "Tidy the synthetic list.", "model": "synthetic-local:7b"})
    s.dispatch(s.get_schedule(rec["id"]), manual=True)
    run = _wait_for_run(rec["id"])
    assert spawned == [] and run["status"] == "held"
    assert "synthetic-local:7b" in run["summary"] and "not serving" in run["summary"]


def test_an_available_pinned_cloud_model_runs_the_job_on_exactly_that_model(monkeypatch, spawned):
    _cloud_service(monkeypatch, up=True)
    rec = _user("daily", {"hour": 7, "minute": 0}, created=0,
                task={"kind": "agent_prompt", "prompt": "Tidy the synthetic list.", "model": "claude-synthetic-1"})
    s.dispatch(s.get_schedule(rec["id"]), manual=True)
    run = _wait_for_run(rec["id"])
    assert run["status"] == "complete"
    assert len(spawned) == 1
    assert spawned[0]["model"] == "claude-synthetic-1"
    assert spawned[0]["pinned"] == "claude-synthetic-1", "the run was not pinned to its model"


def test_one_run_at_a_time_per_job(monkeypatch, spawned):
    _cloud_service(monkeypatch, up=True)
    rec = _user("daily", {"hour": 7, "minute": 0}, created=0)
    s._RUNNING.add(rec["id"])
    assert s.dispatch(s.get_schedule(rec["id"]), manual=True) is None
    assert s.list_schedules()[0]["status"] == "running"


def test_the_list_says_paused_and_failed_with_the_reason():
    a = _user("daily", {"hour": 7, "minute": 0}, created=0)
    b = _user("daily", {"hour": 8, "minute": 0}, created=0)
    s.update_schedule(a["id"], {"enabled": False})
    s._patch_record(b["id"], last_status="failed", last_summary="RuntimeError: synthetic failure")
    rows = {r["id"]: r for r in s.list_schedules()}
    assert rows[a["id"]]["status"] == "paused"
    assert (rows[b["id"]]["status"], rows[b["id"]]["status_reason"]) == ("failed", "RuntimeError: synthetic failure")
    assert rows[b["id"]]["model"], "every row names the model it runs on"


# ── monthly in plain language and in the Workflows sentence ──────────────────

def test_monthly_is_read_from_plain_language_and_described():
    from agent_friday.services import workflow_overview as wo
    w, rest, note = wo.parse_when("on the 1st of every month at 7am, send me the invoice summary",
                                  now=datetime(2026, 10, 15, 9, 0))
    assert w == {"trigger": "monthly", "spec": {"day": 1, "hour": 7, "minute": 0}}
    assert note == ""
    assert wo.describe_when(w) == "On the 1st of every month at 7 AM"
    assert wo.describe_when({"trigger": "monthly", "spec": {"day": -1, "hour": 18, "minute": 0}}) \
        == "On the last day of every month at 6 PM"


# ── honesty: "scheduled" needs a receipt that a timetable was written ────────

def _claims(reply, trace):
    from agent_friday.services.completion_receipts import find_unreceipted_completion_claims
    return find_unreceipted_completion_claims(reply, trace)


CREATED = {"name": "schedule_job", "input": {"action": "create"},
           "result": json.dumps({"created": True, "schedule_id": "sch_synthetic", "next_run": 1792000000})}


def test_a_passive_scheduled_claim_with_no_receipt_is_flagged_and_a_create_receipt_satisfies_it():
    reply = "Done. Your weekly email is scheduled for Mondays at 7am."
    assert _claims(reply, []), "an unbacked 'is scheduled' passed"
    assert _claims(reply, [CREATED]) == []


def test_a_read_is_not_a_receipt_for_scheduled():
    reply = "I've scheduled the weekly report for Friday."
    for read in ({"name": "find_calendar_events", "input": {}, "result": "[]"},
                 {"name": "schedule_job", "input": {"action": "list"}, "result": json.dumps({"schedules": []})}):
        assert _claims(reply, [read]), "a read satisfied a scheduled claim: %s" % read["name"]
    failed = dict(CREATED, result="Tool error (schedule_job): the schedule was not saved")
    assert _claims(reply, [failed])
    assert _claims(reply, [CREATED]) == []


def test_an_honest_no_is_not_a_claim():
    assert _claims("Nothing is scheduled yet; want me to set it up?", []) == []
    assert _claims("It isn't set to run on its own.", []) == []


# ── the schedule_job tool ────────────────────────────────────────────────────

def test_create_answers_with_the_schedule_id_and_next_run_read_back_from_the_store():
    from agent_friday.services import schedule_tools as st
    out = json.loads(st.schedule_job({
        "action": "create", "prompt": "Summarise the synthetic notes.", "name": "Notes digest",
        "trigger": "monthly", "spec": {"day": 1, "hour": 7, "minute": 0}, "missed": "skip"}))
    assert out["created"] is True and out["schedule_id"].startswith("sch_")
    assert out["next_run"] and s.get_schedule(out["schedule_id"])["trigger"] == "monthly"
    assert out["missed"].endswith("skipped until its next time.")
    # The scheduler keeps wall-clock time in its own zone (scheduler._now_central),
    # whatever zone the machine running the test is in.
    from zoneinfo import ZoneInfo
    nxt = datetime.fromtimestamp(out["next_run"], ZoneInfo("America/Chicago"))
    assert (nxt.day, nxt.hour, nxt.minute) == (1, 7, 0)
    listed = json.loads(st.schedule_job({"action": "list"}))
    assert [r["schedule_id"] for r in listed["schedules"]] == [out["schedule_id"]]
    paused = json.loads(st.schedule_job({"action": "pause", "schedule_id": out["schedule_id"]}))
    assert paused["status"] == "paused" and paused["next_run"] is None
    resumed = json.loads(st.schedule_job({"action": "resume", "schedule_id": out["schedule_id"]}))
    assert resumed["status"] == "scheduled" and resumed["next_run"]
    gone = json.loads(st.schedule_job({"action": "delete", "schedule_id": out["schedule_id"]}))
    assert gone == {"deleted": True, "schedule_id": out["schedule_id"], "still_exists": False}


def test_creating_a_schedule_that_will_send_later_asks_first_and_one_that_does_not_is_internal():
    from agent_friday.governance import action_gate as ag
    sends = {"action": "create", "trigger": "weekly", "spec": {"weekdays": [0], "hour": 7},
             "prompt": "Send a weekly email to the synthetic team with the status summary."}
    quiet = dict(sends, prompt="Summarise the synthetic notes into my wiki.")
    k, why = ag.classify("schedule_job", sends)
    assert k == ag.OUTWARD and "send email" in why
    assert ag.classify("schedule_job", quiet)[0] == ag.INTERNAL
    assert ag.classify("schedule_job", {"action": "list"})[0] == ag.INTERNAL
    v = ag.authorize("schedule_job", sends, {"session_id": "s1"})
    assert v.action == "confirm", "an outward schedule in chat is asked about first"


def test_a_background_job_cannot_create_schedules(monkeypatch):
    from agent_friday.services import agent, schedule_tools as st
    monkeypatch.setattr(agent, "_workflow_caller_context", lambda: {"is_background_task": True})
    with pytest.raises(ValueError, match="cannot set up or re-arm"):
        st.schedule_job({"action": "create", "prompt": "x", "trigger": "daily", "spec": {"hour": 7}})
    assert s.list_schedules() == []


def test_the_tool_is_registered_with_a_ring_in_chat_and_voice():
    from agent_friday.services import agent, voice_engine
    assert agent.TOOL_RINGS["schedule_job"] == 1
    assert "schedule_job" in agent.CLAUDE_TOOL_HANDLERS
    # Voice schedules through workflow_action's `when` (the spoken contract's
    # budget has no room for a second scheduler); it must offer monthly too.
    assert "schedule_job" not in voice_engine._VOICE_SHARED_TOOLS
    assert "workflow_action" in voice_engine._VOICE_SHARED_TOOLS
    wf = next(t for t in voice_engine.build_voice_tool_contract()["tools"]
              if t["function"]["name"] == "workflow_action")["function"]["parameters"]
    when = wf["properties"]["when"]["properties"]
    assert "monthly" in when["trigger"]["enum"] and "day" in when["spec"]["properties"]


# ── the Workflows card shows model, status and next run (node harness) ───────

def _extract(src, start):
    i = src.index(start)
    # A function's body starts after its parameter list (which may destructure `{ w }`).
    depth, j = 0, src.index("{", src.index(")", i) if start.startswith("function") else i)
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                end = k + 1
                return src[i:end + 1 if src[end:end + 1] == ";" else end]
    raise AssertionError(start)


@pytest.mark.skipif(not shutil.which("node"), reason="node is not on PATH")
def test_the_workflow_card_renders_schedule_status_model_and_missed_rule_under_node():
    src = (REPO / "index.html").read_text(encoding="utf-8")
    parts = [_extract(src, n) for n in (
        "const WF_SCHED_STATUS", "function wfClock(", "function wfJoin(", "function wfDescribe(",
        "function wfWhenToUi(", "function wfUiToWhen(", "function wfNext(", "function WfScheduleFacts(",
        "function wfRoutineLine(")]
    script = "\n".join([
        "const WF_DAY_NAMES = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];",
        "const React = {createElement: (t, p, ...c) => ({t, p: p || {}, c: c.flat().filter(x => x !== null && x !== false && x !== undefined)})};",
        *parts,
        "const text = n => typeof n === 'string' ? n : (n.c || []).map(text).join('');",
        "const find = (n, id) => typeof n === 'string' ? null : (n.p['data-testid'] === id ? n : (n.c || []).map(x => find(x, id)).find(Boolean) || null);",
        "const held = WfScheduleFacts({w: {when: {trigger: 'monthly', spec: {day: 1, hour: 7}}, schedule_status: 'held', status_reason: 'its pinned model is not available', model: 'claude-synthetic-1', model_pinned: true, missed_text: 'If a run is missed while Friday is closed, it runs once when Friday starts.'}});",
        "const ui = wfWhenToUi({trigger: 'monthly', spec: {day: -1, hour: 18, minute: 5}});",
        "console.log(JSON.stringify({",
        "  status: text(find(held, 'wf-schedule-status')), model: text(find(held, 'wf-model')),",
        "  reason: text(find(held, 'wf-schedule-reason')), missed: text(find(held, 'wf-missed')),",
        "  describe: wfDescribe({trigger: 'monthly', spec: {day: 31, hour: 9, minute: 0}}),",
        "  roundtrip: wfUiToWhen(ui), repeat: ui.repeat,",
        "  routine: wfRoutineLine({when_text: 'Every day at 7 AM', schedule_status: 'failed', status_reason: 'synthetic', enabled: true, model: 'local'}),",
        "  unpinned: text(WfScheduleFacts({w: {model: 'm1', model_pinned: false}}))}));",
    ])
    cp = subprocess.run([shutil.which("node"), "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert cp.returncode == 0, cp.stderr[-2000:]
    got = json.loads(cp.stdout)
    assert got["status"] == "Schedule: Held"
    assert got["model"] == "Model: claude-synthetic-1"
    assert got["reason"] == "its pinned model is not available"
    assert got["missed"].startswith("If a run is missed")
    assert got["describe"] == "On the 31st of every month (or the month's last day) at 9 AM"
    assert got["repeat"] == "monthly"
    assert got["roundtrip"] == {"trigger": "monthly", "spec": {"day": -1, "hour": 18, "minute": 5}}
    assert got["routine"] == "Every day at 7 AM \xb7 Failed: synthetic \xb7 Model: local"
    assert got["unpinned"] == "Model (not pinned): m1"


def test_both_pages_put_the_schedule_facts_on_the_workflow_card_and_offer_monthly():
    for rel in ("index.html", "ui_parts/app.html"):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert "function WfScheduleFacts(" in text, rel
        card = text[text.index("function WfCard("):text.index("function WorkflowsWS(")]
        assert re.search(r"WfScheduleFacts", card), rel
        assert "Every month" in text and "Day of the month" in text, rel
        assert "wfRoutineLine(r)" in text, rel


@pytest.mark.parametrize("text, claim", [
    ("Your weekly email is scheduled.", True),
    ("Done: the digest is now scheduled.", True),
    ("It's scheduled to go out every Monday.", True),
    ("The report is set up for every Friday at 7.", True),
    ("Your meeting is scheduled for 3pm.", False),
    ("The dentist is scheduled for Tuesday at 10.", False),
    ("Nothing is scheduled yet.", False),
])
def test_a_calendar_fact_is_not_a_scheduling_claim(text, claim):
    """Reading 'your meeting is scheduled for 3pm' off the calendar claims
    nothing; a recurring job said to be scheduled with no receipt does."""
    from agent_friday.services.completion_receipts import find_unreceipted_completion_claims
    assert bool(find_unreceipted_completion_claims(text, [])) is claim
