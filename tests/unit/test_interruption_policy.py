"""NA-2: when Friday may interrupt you (budget, coalescing, quiet hours, Focus Assist, deferral).

Addendum decisions NA-D1 (6 an hour, safety-critical exempt), NA-D3 (quiet hours off by default,
22:00-07:00 recommended) and NA-D4 (Windows Focus Assist honoured only when chosen). Nothing here
removes, hides or approves anything: a needs-you item that may not interrupt still lands in the
badge, and an approval is still decided exactly as before.
"""
from __future__ import annotations

import time
from datetime import datetime

import pytest

from agent_friday import notifications_engine as ne
from agent_friday.services import approval_feed, approvals
from agent_friday.services import interruption_policy as ip
from agent_friday.services import notification_policy as pol

NOON = datetime(2026, 10, 12, 12, 0).timestamp()
NIGHT = datetime(2026, 10, 12, 23, 30).timestamp()
SMALL_HOURS = datetime(2026, 10, 13, 3, 0).timestamp()
SEVEN = datetime(2026, 10, 13, 7, 0).timestamp()
QUIET = {"quiet_hours_enabled": True}


@pytest.fixture(autouse=True)
def _clean(friday_dir, monkeypatch):
    if ne.NOTIF_FILE.exists():
        ne.NOTIF_FILE.unlink()
    monkeypatch.setattr(pol, "owner_in_conversation", lambda now=None: False)
    ip.set_focus_probe(None)
    yield
    ip.set_focus_probe(None)


@pytest.fixture
def burst_emitter(monkeypatch):
    """A tray emitter that declares itself bursty, independent of the
    scheduler's own lines (its "waiting" line lives in Activity)."""
    from agent_friday.services import notice_registry as reg
    monkeypatch.setitem(reg.REGISTRY, "TB1", reg._e(
        "TB1", "tests", "scheduled_ran", reg.UPDATES, "sched-ok:{schedule_id}",
        source="scheduler", pk="scheduled_task", burst=True))


def _settings(monkeypatch, **s):
    monkeypatch.setattr(ip, "_settings", lambda: dict(s))


def _v(i, now=NOON, kind="scheduled_failure", settings=None, source="scheduler"):
    return ip.verdict(kind, source=source, key="k%d" % i, now=now, settings=settings or {})


# ── the budget ──────────────────────────────────────────────────────────────

def test_the_default_budget_is_six_an_hour_and_six_is_the_recommended_choice():
    assert ip.DEFAULT_BUDGET == 6 == ip.RECOMMENDED_BUDGET and ip.budget({}) == 6
    assert 6 in ip.BUDGET_CHOICES and 0 in ip.BUDGET_CHOICES
    from agent_friday.core import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["interruption_budget_per_hour"] == 6


def test_the_seventh_interruption_in_an_hour_waits_silently_and_the_next_hour_starts_fresh():
    got = [_v(i, NOON + i) for i in range(7)]
    assert [v.interrupt for v in got] == [True] * 6 + [False]
    assert got[6].reason == ip.BUDGET and not got[6].counted
    assert ip.used_this_hour(NOON + 7) == 6
    assert _v(99, NOON + 3700).interrupt is True, "an hour later the budget is whole again"


def test_the_budget_is_the_owners_to_set_and_zero_means_no_limit():
    s = {"interruption_budget_per_hour": 2}
    assert [_v(i, NOON, settings=s).interrupt for i in range(4)] == [True, True, False, False]
    ip.set_state_path(ip._path().parent / "other.json")
    s0 = {"interruption_budget_per_hour": 0}
    assert all(_v(i, NOON, settings=s0).interrupt for i in range(40))


def test_safety_critical_kinds_always_interrupt_and_do_not_use_the_budget():
    s = {"interruption_budget_per_hour": 1, **QUIET, "honour_focus_assist": True}
    ip.set_focus_probe(lambda: True)
    for kind in ("spend_halt", "credential_problem", "budget_alert", "seat_change", "claw_block",
                 "security"):
        v = ip.verdict(kind, key=kind, now=NIGHT, settings=s)
        assert v.interrupt and v.reason == ip.SAFETY and not v.counted, kind
    assert ip.used_this_hour(NIGHT) == 0
    # ... so a flood of them never costs an ordinary item its place.
    ip.set_state_path(ip._path().parent / "fresh.json")
    for i in range(30):
        ip.verdict("spend_halt", key="s%d" % i, now=NOON, settings={"interruption_budget_per_hour": 1})
    assert _v(1, NOON, settings={"interruption_budget_per_hour": 1}).interrupt is True


def test_one_item_counts_once_however_many_surfaces_ask():
    a = ip.verdict("approval_pending", source="approvals", key="approval:x", now=NOON, settings={})
    b = ip.verdict("approval_pending", source="approvals", key="approval:x", now=NOON + 1, settings={})
    assert a.interrupt and b.interrupt and not b.counted and ip.used_this_hour(NOON + 2) == 1


def test_a_decision_to_hold_is_remembered_so_the_card_and_the_popup_agree():
    s = {"interruption_budget_per_hour": 1}
    _v(1, NOON, settings=s)
    first = ip.verdict("approval_pending", source="approvals", key="approval:late", now=NOON, settings=s)
    again = ip.verdict("approval_pending", source="approvals", key="approval:late", now=NOON + 5,
                       settings={"interruption_budget_per_hour": 99})
    assert first.interrupt is False and again.interrupt is False and again.reason == ip.BUDGET


# ── quiet hours ─────────────────────────────────────────────────────────────

def test_quiet_hours_are_off_by_default_and_22_to_07_is_the_recommended_window():
    from agent_friday.core import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["quiet_hours_enabled"] is False
    assert ip.RECOMMENDED_QUIET == ("22:00", "07:00")
    assert (DEFAULT_SETTINGS["quiet_hours_start"], DEFAULT_SETTINGS["quiet_hours_end"]) == ip.RECOMMENDED_QUIET
    assert _v(1, NIGHT, settings={}).interrupt is True, "off means off"


def test_in_quiet_hours_only_safety_critical_items_interrupt():
    assert ip.quiet_state(NIGHT, QUIET)["active"] and ip.quiet_state(SMALL_HOURS, QUIET)["active"]
    assert not ip.quiet_state(NOON, QUIET)["active"]
    assert not ip.quiet_state(SEVEN, QUIET)["active"], "the end time is the first quiet-free minute"
    v = _v(1, NIGHT, settings=QUIET)
    assert (v.interrupt, v.reason) == (False, ip.QUIET_HOURS)
    assert ip.verdict("credential_problem", key="c", now=NIGHT, settings=QUIET).interrupt is True
    assert _v(2, NOON, settings=QUIET).interrupt is True


def test_a_custom_window_that_does_not_cross_midnight_works():
    s = {"quiet_hours_enabled": True, "quiet_hours_start": "13:00", "quiet_hours_end": "14:30"}
    assert ip.quiet_state(datetime(2026, 10, 12, 13, 59).timestamp(), s)["active"]
    assert not ip.quiet_state(datetime(2026, 10, 12, 14, 30).timestamp(), s)["active"]
    assert not ip.quiet_state(NIGHT, s)["active"]


def test_quiet_until_morning_ends_at_the_next_seven():
    until = ip.until_morning(NIGHT, {})
    assert datetime.fromtimestamp(until) == datetime(2026, 10, 13, 7, 0)
    s = {"quiet_until_ts": until}
    assert ip.quiet_state(NIGHT, s)["active"] and ip.quiet_state(NIGHT, s)["why"] == "until_morning"
    assert not ip.quiet_state(until + 1, s)["active"]
    # with quiet hours set to another end, "morning" is that end
    s2 = {"quiet_hours_enabled": True, "quiet_hours_end": "06:30"}
    assert datetime.fromtimestamp(ip.until_morning(NIGHT, s2)) == datetime(2026, 10, 13, 6, 30)


def test_background_jobs_that_would_raise_approvals_wait_for_the_morning():
    d = ip.defer_job("task.agent", now=NIGHT, settings=QUIET)
    assert d["defer"] and d["reason"] == ip.QUIET_HOURS
    assert datetime.fromtimestamp(d["until"]) == datetime(2026, 10, 13, 7, 0)
    assert not ip.defer_job("task.agent", now=NOON, settings=QUIET)["defer"]
    assert not ip.defer_job("news.briefing", now=NIGHT, settings=QUIET)["defer"], "no approvals, no wait"
    assert ip.defer_job("anything.else", creates=True, now=NIGHT, settings=QUIET)["defer"]
    assert not ip.defer_job("task.agent", now=NIGHT, settings={})["defer"], "quiet hours off"


def test_the_scheduler_does_not_start_an_approval_raising_run_in_quiet_hours(monkeypatch):
    from agent_friday.services import scheduler
    _settings(monkeypatch, quiet_until_ts=time.time() + 3600)
    agent_run = {"id": "s1", "task": {"kind": "agent_prompt", "prompt": "x"}}
    routine = {"id": "s2", "task": {"kind": "builtin", "ref": "briefing"}}
    assert scheduler._deferred_for_quiet(agent_run) is True
    assert scheduler._deferred_for_quiet(routine) is False
    _settings(monkeypatch)
    assert scheduler._deferred_for_quiet(agent_run) is False


# ── Windows Focus Assist ────────────────────────────────────────────────────

def test_focus_assist_is_honoured_only_when_the_owner_chose_it():
    ip.set_focus_probe(lambda: True)
    assert _v(1, NOON, settings={}).interrupt is True, "off until chosen"
    ip.set_state_path(ip._path().parent / "f.json")
    v = _v(2, NOON, settings={"honour_focus_assist": True})
    assert (v.interrupt, v.reason) == (False, ip.FOCUS)
    ip.set_focus_probe(lambda: False)
    ip.set_state_path(ip._path().parent / "g.json")
    assert _v(3, NOON, settings={"honour_focus_assist": True}).interrupt is True
    from agent_friday.core import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["honour_focus_assist"] is False


def test_a_probe_that_fails_never_holds_anything_back():
    def boom():
        raise OSError("no shell")
    ip.set_focus_probe(boom)
    assert _v(1, NOON, settings={"honour_focus_assist": True}).interrupt is True


# ── coalescing ──────────────────────────────────────────────────────────────

def test_a_burst_inside_five_minutes_coalesces_and_a_later_one_does_not():
    assert ip.COALESCE_S == 300
    assert ip.coalesces(NOON, NOON + 299) and not ip.coalesces(NOON, NOON + 301)
    assert not ip.coalesces(0, NOON)


def test_a_burst_of_one_kind_from_one_source_is_one_card_with_a_count(burst_emitter):
    for i in range(4):
        ne.push(title="Front Page %d ready" % i, source="scheduler", kind="scheduled_task", emitter="TB1",
                dedupe_key="sched-ok:ed%d" % i)
    cards = ne.list_notifications(limit=50)
    assert len(cards) == 1 and cards[0]["count"] == 4, cards


def test_a_burst_outside_five_minutes_is_a_new_card(burst_emitter):
    ne.push(title="Front Page ready", source="scheduler", kind="scheduled_task", emitter="TB1", dedupe_key="sched-ok:a")
    items = ne._load()
    items[0]["created_at"] = datetime.utcfromtimestamp(time.time() - 600).strftime("%Y-%m-%dT%H:%M:%SZ")
    ne._save(items)
    ne.push(title="Front Page ready", source="scheduler", kind="scheduled_task", emitter="TB1", dedupe_key="sched-ok:b")
    assert len(ne.list_notifications(limit=50)) == 2


def test_needs_you_items_are_not_merged_across_keys():
    ne.push(title="Morning run failed", source="scheduler", kind="scheduled_failure", priority="high",
            dedupe_key="sched-fail:1")
    ne.push(title="Evening run failed", source="scheduler", kind="scheduled_failure", priority="high",
            dedupe_key="sched-fail:2")
    assert len(ne.list_notifications(limit=50)) == 2


# ── the engine: over budget still lands in the badge ────────────────────────

def test_over_budget_a_needs_you_card_waits_in_the_badge_without_interrupting(monkeypatch):
    _settings(monkeypatch, interruption_budget_per_hour=2)
    cards = [ne.push(title="Run %d failed" % i, source="scheduler", kind="scheduled_failure",
                     priority="high", dedupe_key="sched-fail:%d" % i) for i in range(3)]
    assert [c["interrupt"] for c in cards] == [True, True, False]
    assert cards[2]["hold_reason"] == "budget" and cards[2]["quiet"] is True
    listed = ne.list_notifications(limit=50)
    assert len(listed) == 3, "nothing is hidden"
    assert all(c["read"] is False for c in listed), "all three are in the badge"


def test_a_safety_critical_card_interrupts_after_the_budget_is_gone(monkeypatch):
    _settings(monkeypatch, interruption_budget_per_hour=1, **QUIET, quiet_until_ts=time.time() + 600)
    c = ne.push(title="Spend halted", source="spend-guard", kind="spend_halt", priority="high",
                dedupe_key="spend_halt:day")
    assert c["interrupt"] is True and c["safety_critical"] is True
    d = ne.push(title="Run failed", source="scheduler", kind="scheduled_failure", priority="high",
                dedupe_key="sched-fail:q")
    assert d["interrupt"] is False and d["hold_reason"] == "quiet_hours"


def test_an_fyi_never_interrupts_and_never_uses_the_budget(monkeypatch):
    _settings(monkeypatch, interruption_budget_per_hour=1)
    for i in range(5):
        ne.push(title="Briefing %d" % i, source="briefing", kind="briefing", dedupe_key="briefing:%d" % i)
    assert ip.used_this_hour() == 0


# ── approvals: the popup is quiet, the gate is not touched ──────────────────

@pytest.fixture
def approval_store(tmp_path, monkeypatch):
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    approval_feed.reset()
    yield
    approval_feed.reset()


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def _raise(i):
    return approvals.create_approval(
        kind="tainted_action", subject_type="tool_action", subject_id="s%d" % i, title="Open page %d" % i,
        action_description="open_url https://example.test/%d" % i, force_gate=True,
        requested_by="taint_gate")


def test_a_card_over_budget_reaches_every_page_marked_quiet_and_is_still_decided_normally(
        approval_store, monkeypatch):
    _settings(monkeypatch, interruption_budget_per_hour=1)
    q = approval_feed.subscribe()
    first, second = _raise(1), _raise(2)
    pend = [e for e in _drain(q) if e["type"] == "pending"]
    assert [e["approval"]["approval_id"] for e in pend] == [first["approval_id"], second["approval_id"]]
    assert "interrupt" not in pend[0] and pend[1]["interrupt"] is False
    assert pend[1]["approval"]["status"] == "pending"
    # the quiet card is the same card: one decision, one action
    out, won = approvals.decide_with_outcome(second["approval_id"], "approve", decided_by="owner")
    assert won and out["status"] == "approved" and out["consumed"] is False


def test_in_quiet_hours_an_approval_does_not_pop_open_but_waits_to_be_answered(approval_store, monkeypatch):
    _settings(monkeypatch, quiet_until_ts=time.time() + 3600)
    q = approval_feed.subscribe()
    rec = _raise(1)
    ev = [e for e in _drain(q) if e["type"] == "pending"]
    assert ev and ev[0]["interrupt"] is False
    assert [r["approval_id"] for r in approvals.list_approvals(status="pending")] == [rec["approval_id"]]


def test_a_text_message_about_an_approval_follows_the_same_verdict(approval_store, monkeypatch):
    from agent_friday.phone import service as phone
    _settings(monkeypatch, quiet_until_ts=time.time() + 3600)
    assert phone._may_text_about({"approval_id": "a1"}) is False
    _settings(monkeypatch)
    assert phone._may_text_about({"approval_id": "a2"}) is True


# ── settings ────────────────────────────────────────────────────────────────

def test_the_settings_route_refuses_a_value_the_policy_would_misread():
    from agent_friday.routes.core_routes import _check_interruption_settings as check
    assert check({"interruption_budget_per_hour": 6, "quiet_hours_start": "22:00",
                  "quiet_hours_end": "07:00", "quiet_hours_enabled": True, "honour_focus_assist": False}) is None
    for bad in ({"interruption_budget_per_hour": -1}, {"interruption_budget_per_hour": 61},
                {"interruption_budget_per_hour": True}, {"interruption_budget_per_hour": "6"},
                {"quiet_hours_start": "25:00"}, {"quiet_hours_end": "7pm"},
                {"quiet_hours_enabled": "yes"}, {"honour_focus_assist": 1}):
        assert check(bad)["status"] == "error", bad


def test_waiting_phrase_says_how_many_wait():
    assert ip.waiting_phrase(4, 0) == "4 approvals waiting"
    assert ip.waiting_phrase(1, 0) == "1 approval waiting"
    assert ip.waiting_phrase(2, 1) == "2 approvals and 1 other thing waiting"
    assert ip.waiting_phrase(0, 2) == "2 things waiting" and ip.waiting_phrase(0, 0) == ""


# ── what the state file holds ───────────────────────────────────────────────

def test_the_state_file_holds_hashes_and_timestamps_never_an_id_or_a_title():
    ip.verdict("scheduled_failure", source="scheduler", key="sched-fail:secret-job", now=NOON, settings={})
    ip.verdict("approval_pending", source="approvals", key="approval:appr_private", now=NOON, settings={})
    text = ip._path().read_text(encoding="utf-8")
    assert "secret-job" not in text and "appr_private" not in text and "sched-fail" not in text
    assert ip.held("approval:appr_private", NOON) is False


def test_an_off_the_record_card_leaves_no_trace_of_its_words_in_the_state(monkeypatch):
    monkeypatch.setattr(ne, "_off_record_now", lambda: True)
    ne.push(title="Secret plan failed", source="scheduler", kind="scheduled_failure", priority="high")
    p = ip._path()
    assert "Secret" not in (p.read_text(encoding="utf-8") if p.exists() else "")
    assert ip.used_this_hour() == 1, "it still counts toward the hour"


def test_a_card_about_one_task_is_not_merged_with_another_even_in_a_burst():
    """Only the emitters that declare themselves bursty (a schedule's "ran" lines) coalesce across keys."""
    ne.push(title="Task finished: A", source="system", kind="info", emitter="E02", dedupe_key="task-done:t1")
    ne.push(title="Task finished: B", source="system", kind="info", emitter="E02", dedupe_key="task-done:t2")
    assert len(ne.list_notifications(limit=20)) == 2
