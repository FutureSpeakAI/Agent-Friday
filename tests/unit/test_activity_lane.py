"""NA-3: the four places, read for the panel and for voice (services/activity_lane).

Needs you holds only blocking decisions. Activity lists every task with its model, status, trace link,
queue position and Stop only where the job honours it. Updates is a pulled digest. Receipts are
reached from an item ("why?"). Voice reaches all of it through tools that already exist.
"""
from __future__ import annotations

import time

import pytest

from agent_friday import notifications_engine as ne
from agent_friday.services import activity_lane as lane
from agent_friday.services import approvals
from agent_friday.services import notification_policy as pol

NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def _clean(friday_dir, tmp_path, monkeypatch):
    if ne.NOTIF_FILE.exists():
        ne.NOTIF_FILE.unlink()
    monkeypatch.setattr(pol, "owner_in_conversation", lambda now=None: False)
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    yield


def _task(tid, status="running", **kw):
    base = {"task_id": tid, "id": tid, "name": "Task " + tid, "status": status, "created": NOW - 100,
            "started": NOW - 90 if status != "queued" else None, "ended": None, "model": "gemma-test",
            "trace_id": "tr_" + tid, "queue_position": 0}
    base.update(kw)
    return base


def _world(monkeypatch, tasks, jobs=()):
    from agent_friday.services import agent
    monkeypatch.setattr(agent, "_task_snapshot", lambda task_id=None: [dict(t) for t in tasks])
    monkeypatch.setattr(agent, "local_queue_snapshot", lambda: {"jobs": list(jobs), "running": [],
                                                                 "queued": [], "count": len(jobs)})
    from agent_friday import core
    monkeypatch.setattr(core, "PROCESSES", {})


# ── Activity ────────────────────────────────────────────────────────────────

def test_every_task_shows_its_model_status_trace_and_queue_position(monkeypatch):
    _world(monkeypatch, [
        _task("a"),
        _task("b", "queued", model="", trace_id="", created=NOW - 50),
        _task("c", "queued", model="", trace_id="", created=NOW - 40),
    ])
    rows = {r["id"]: r for r in lane.activity(NOW)["tasks"]}
    assert rows["a"]["model"] == "gemma-test" and rows["a"]["trace_id"] == "tr_a"
    assert rows["a"]["status"] == "running" and rows["a"]["queue_position"] == 0
    assert (rows["b"]["queue_position"], rows["c"]["queue_position"]) == (1, 2)
    assert [r["id"] for r in lane.activity(NOW)["tasks"]] == ["a", "b", "c"], "running first, then in line order"
    out = lane.activity(NOW)
    assert (out["running"], out["queued"], out["count"]) == (1, 2, 3)


def test_stop_is_offered_only_where_the_job_says_it_honours_it(monkeypatch):
    """M6: a record that does not say `stoppable` is not stoppable, and Stop never deletes."""
    _world(monkeypatch, [
        _task("plain"),                                                       # no `stoppable` key at all
        _task("honours", stoppable=True, job_kind="library.index", visible_job=True),
        _task("inline", job_kind="news.briefing", job_label="Writing your briefing",
              visible_job=True, inline=True, stoppable=False),
        _task("waiting", "queued", stoppable=True)])
    rows = {r["id"]: r for r in lane.activity(NOW)["tasks"]}
    assert rows["plain"]["stoppable"] is False and rows["plain"]["stop"] is None
    assert rows["inline"]["stoppable"] is False and rows["inline"]["stop"] is None
    assert rows["inline"]["label"] == "Writing your briefing" and rows["inline"]["kind"] == "news.briefing"
    for tid in ("honours", "waiting"):
        assert rows[tid]["stoppable"] is True
        assert rows[tid]["stop"] == {"method": "POST", "url": "/api/tasks/%s/stop-after-step" % tid}
    assert all((r["stop"] or {}).get("method") != "DELETE" for r in rows.values())


def test_a_truthy_value_that_is_not_true_is_not_stoppable(monkeypatch):
    _world(monkeypatch, [_task("odd", stoppable="yes"), _task("one", stoppable=1)])
    assert [r["stoppable"] for r in lane.activity(NOW)["tasks"]] == [False, False]


def test_a_finished_task_is_a_receipt_after_half_an_hour_and_has_no_stop(monkeypatch):
    _world(monkeypatch, [_task("fresh", "complete", ended=NOW - 60), _task("old", "complete", ended=NOW - 4000),
                         _task("gone", "complete", ended=NOW - 10, dismissed=True)])
    rows = {r["id"]: r for r in lane.activity(NOW)["tasks"]}
    assert set(rows) == {"fresh"} and rows["fresh"]["stoppable"] is False and rows["fresh"]["stop"] is None


def test_the_local_queue_rows_join_the_list_once_with_cancel_and_run_now(monkeypatch):
    jobs = [{"id": "t1", "label": "Same as the task", "kind": "task", "state": "running"},
            {"id": "q9", "label": "Wiki notes", "kind": "wiki", "state": "queued", "why": "waiting for idle",
             "deferrable": True, "run_now": False}]
    _world(monkeypatch, [_task("t1")], jobs)
    rows = lane.activity(NOW)["tasks"]
    assert [r["id"] for r in rows].count("t1") == 1
    q = next(r for r in rows if r["id"] == "q9")
    assert q["source"] == "local_queue" and q["queue_position"] == 1 and q["run_now"] is True
    assert q["stop"] == {"method": "POST", "url": "/api/local-queue/q9/cancel"} and q["why"] == "waiting for idle"


def test_activity_with_nothing_running_is_empty_not_an_error(monkeypatch):
    _world(monkeypatch, [])
    assert lane.activity(NOW) == {"tasks": [], "running": 0, "queued": 0, "count": 0}
    assert lane.spoken_running() == "Nothing is running and nothing is waiting."


def test_what_is_running_is_said_for_every_task_not_only_the_local_queue(monkeypatch):
    _world(monkeypatch, [_task("a", job_label="Writing your briefing", job_kind="news.briefing"),
                         _task("b", "queued", name="Research the thing", created=NOW - 5)])
    said = lane.spoken_running()
    assert "One running: Writing your briefing" in said and "One waiting, in order: Research the thing" in said


# ── Needs you ───────────────────────────────────────────────────────────────

def _pending(i, conv="c1", kind="media_diet_rule"):
    """A pending approval of a kind that may batch; `conv` names the service, so another one stays single."""
    return approvals.create_approval(
        kind=kind, subject_type="rule", subject_id="%s%d%s" % (kind, i, conv), title="Change rule %d" % i,
        action_description="add the rule 'limit %d'" % i, force_gate=True, payload={}, requested_by="svc-" + conv)


def test_needs_you_holds_only_blocking_decisions(monkeypatch):
    for i in range(3):
        _pending(i)
    _pending(7, conv="c2")
    ne.push(title="Morning run failed", source="scheduler", kind="scheduled_failure", priority="high",
            dedupe_key="sched-fail:a", actions=[{"type": "navigate", "label": "Open schedules"}])
    ne.push(title="Front Page ready", source="front-page", kind="front_page", dedupe_key="front-page:e1")
    n = lane.needs_you()
    assert n["approvals"] == 4 and len(n["batches"]) == 1 and n["batches"][0]["count"] == 3
    assert [s["title"] for s in n["singles"]] == ["Change rule 7"]
    assert [o["title"] for o in n["others"]] == ["Morning run failed"], "an FYI is not in Needs you"
    assert n["others"][0]["actions"][0]["label"] == "Open schedules", "an error card offers its fix"
    assert n["count"] == 5 and n["summary"] == "4 approvals and 1 other thing waiting"


def test_needs_you_reads_nothing_when_nothing_blocks(monkeypatch):
    n = lane.needs_you()
    assert n["count"] == 0 and n["summary"] == "" and lane.spoken_needs_you() == "Nothing needs you right now."


def test_what_needs_me_says_the_count_then_each_item_briefly(monkeypatch):
    for i in range(2):
        _pending(i)
    ne.push(title="Morning run failed", source="scheduler", kind="scheduled_failure", priority="high",
            dedupe_key="sched-fail:a")
    said = lane.spoken_needs_you()
    assert said.startswith("Three things need you:")
    assert "two actions from one source" in said and "Morning run failed" in said


# ── Updates ─────────────────────────────────────────────────────────────────

def test_updates_are_a_digest_grouped_by_kind_and_never_include_decisions():
    ne.push(title="Briefing ready", source="briefing", kind="briefing", dedupe_key="briefing:d1")
    ne.push(title="Podcast ready", source="podcasts", kind="info", dedupe_key="podcast:e1")
    ne.push(title="Front Page ready", source="front-page", kind="front_page", dedupe_key="front-page:e1")
    ne.push(title="Run failed", source="scheduler", kind="scheduled_failure", priority="high",
            dedupe_key="sched-fail:z")
    u = lane.updates()
    kinds = {g["kind"] for g in u["groups"]}
    assert {"briefing", "front_page"} <= kinds and "scheduled_failure" not in kinds
    assert u["count"] >= 3
    today = lane.updates(today_only=True)
    assert today["count"] == u["count"], "all of those arrived today"


def test_read_me_todays_updates_names_what_arrived():
    ne.push(title="Briefing ready", source="briefing", kind="briefing", dedupe_key="briefing:d1")
    assert "Briefing ready" in lane.spoken_updates()
    ne.NOTIF_FILE.unlink()
    assert lane.spoken_updates() == "No new updates today."


# ── Receipts and traces: reached from the item ("why?") ─────────────────────

def test_why_a_card_waited_in_the_badge_is_said_in_plain_words(monkeypatch):
    from agent_friday.services import interruption_policy as ip
    monkeypatch.setattr(ip, "_settings", lambda: {"interruption_budget_per_hour": 1})
    ne.push(title="One", source="scheduler", kind="scheduled_failure", priority="high", dedupe_key="sched-fail:1")
    held = ne.push(title="Two", source="scheduler", kind="scheduled_failure", priority="high",
                   dedupe_key="sched-fail:2")
    w = lane.why(held["id"])
    assert w["ok"] and w["place"] == "needs_you" and w["hold_reason"] == "budget"
    assert "hourly limit" in w["sentence"] and w["emitter"] == "E04"


def test_why_a_safety_critical_card_always_reached_you():
    c = ne.push(title="Spend halted", source="spend-guard", kind="spend_halt", priority="high",
                dedupe_key="spend_halt:d")
    assert "always reaches you" in lane.why(c["id"])["sentence"]


def test_why_an_update_never_popped_up_and_a_missing_item_is_not_found():
    c = ne.push(title="Briefing ready", source="briefing", kind="briefing", dedupe_key="briefing:d1")
    assert "never pops up" in lane.why(c["id"])["sentence"]
    assert lane.why("nope") == {"ok": False, "error": "not found"}


def test_why_follows_a_task_card_to_its_trace(monkeypatch):
    from agent_friday.services import reasoning_trace as rt
    monkeypatch.setattr(rt, "for_task", lambda tid: "tr_for_" + tid)
    c = ne.push(title="Task done", source="system", kind="info", dedupe_key="task-done:t9",
                meta={"task_id": "t9"})
    assert lane.why(c["id"])["trace_id"] == "tr_for_t9"


# ── Voice: quiet until morning, by a tool that already exists ────────────────
# "quiet until morning" is a router rule onto the notifications tool, judged internal by action only for
# turning quiet on and only on the owner's own words (tests/unit/test_notifications_gate.py). No tool was
# added to the voice contract, and the contract still fits.

def test_no_voice_contract_tool_was_added_and_the_contract_still_fits():
    from agent_friday.services import notification_tools as nt
    from agent_friday.services import voice_engine as ve
    c = ve.build_voice_tool_contract()
    assert c["fits"] and c["tokens"] <= ve.VOICE_CONTRACT_MAX_TOKENS - 300
    assert [t["name"] for t in nt.TOOLS] == ["notifications"]
    assert nt.TOOLS[0]["input_schema"]["properties"]["action"]["enum"][-1] == "quiet"


def test_quiet_until_morning_by_the_tool_turns_it_on_and_says_when(monkeypatch):
    from agent_friday import core
    from agent_friday.services import notification_tools as nt
    saved = {}
    monkeypatch.setattr(core, "_save_settings", lambda d, **k: saved.update(d))
    said = nt.handle({"action": "quiet"})
    assert saved["quiet_until_ts"] > time.time() and said.startswith("Quiet until ")
    assert "safety-critical" in said
    assert nt.handle({"action": "quiet", "group": "off"}).startswith("Quiet is off.")
    assert saved["quiet_until_ts"] == 0




# ── M1: what a cloud model is told about the tasks ───────────────────────────

def _private_world(monkeypatch):
    _world(monkeypatch, [
        _task("a", job_label="Writing your briefing", job_kind="news.briefing"),
        _task("p", name="Draft the reply about the biopsy", off_record=True),
        _task("l", name="Summarise the divorce papers", local_only=True),
        _task("w", "queued", name="Research the lawsuit", created=NOW - 5)])


def test_a_local_model_hears_task_names_but_a_private_task_is_never_named(monkeypatch):
    _private_world(monkeypatch)
    said = lane.spoken_running(names=True)
    assert "Writing your briefing" in said and "Research the lawsuit" in said
    assert "biopsy" not in said and "divorce" not in said
    assert said.count("a private task") == 2


def test_a_cloud_model_hears_counts_only(monkeypatch):
    _private_world(monkeypatch)
    said = lane.spoken_running(names=False)
    assert said == "Three running and one waiting."
    for word in ("briefing", "biopsy", "divorce", "lawsuit", "private"):
        assert word not in said

