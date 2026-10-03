"""The notification tray: one card per thing, three tiers, approvals untouchable.

Owner-approved design (2026-10-02): one card per run updated in place; repeats
collapse with a count; NEEDS_YOU / FYI / LOG_ONLY; a failure card resolves when
the next run succeeds; mute a kind (reversible) and clear all; quiet while the
owner is mid-conversation; approvals are never demoted, muted or held away.
"""
from __future__ import annotations

import pytest

from agent_friday import notifications_engine as ne
from agent_friday.services import notification_policy as pol


@pytest.fixture(autouse=True)
def _clean(friday_dir, monkeypatch):
    if ne.NOTIF_FILE.exists():
        ne.NOTIF_FILE.unlink()
    monkeypatch.setattr(pol, "_LAST_TURN", [0.0])          # nobody mid-conversation
    logged = []
    monkeypatch.setattr(ne, "_log_only", lambda entry: logged.append(entry))
    yield logged


def _cards():
    return ne.list_notifications(limit=500)


def test_repeats_collapse_into_one_card_with_a_count():
    for _ in range(3):
        ne.push(title="Afternoon Briefing failed", body="no seat", source="scheduler",
                kind="scheduled_failure", priority="high")
    cards = _cards()
    assert len(cards) == 1 and cards[0]["count"] == 3, cards


def test_a_flood_of_fifty_identical_failures_is_one_card():
    for i in range(50):
        ne.push(title="Front Page failed", body="attempt %d" % i, source="scheduler",
                kind="scheduled_failure", priority="high")
    cards = _cards()
    assert len(cards) == 1 and cards[0]["count"] == 50
    assert cards[0]["body"] == "attempt 49", "the card shows the latest occurrence"


def test_a_run_keeps_one_card_updated_in_place():
    ne.run_card("sch_news_morning:2026-10-02", "started", title="Front Page", source="scheduler")
    ne.run_card("sch_news_morning:2026-10-02", "done", title="Front Page", body="ready",
                source="scheduler")
    cards = _cards()
    assert len(cards) == 1 and (cards[0]["meta"] or {}).get("state") == "done", cards


def test_a_failure_resolves_itself_when_the_next_run_succeeds():
    ne.push(title="Afternoon Briefing failed", source="scheduler", kind="scheduled_failure",
            priority="high", resolve_key="sched:sch_afternoon_briefing")
    assert len(_cards()) == 1
    assert ne.resolve("sched:sch_afternoon_briefing") == 1
    assert _cards() == []


def test_tiers_route_housekeeping_to_the_log_and_fyi_away_from_the_badge(_clean):
    ne.push(title="Back online", source="network", kind="info", priority="low")
    ne.push(title="Retired: The Friday Edition", source="scheduler", kind="schedule_retired")
    assert _cards() == [] and len(_clean) == 2, "log-only items reached the tray"
    ne.push(title="Daily creation ran", source="scheduler", kind="scheduled_task", priority="low")
    fyi = _cards()[0]
    assert fyi["tier"] == pol.FYI and fyi["read"] is True
    assert ne.unread_count() == 0, "an FYI item bumped the badge"
    ne.push(title="Approval needed: write a file", source="approvals", kind="approval_pending",
            dedupe_key="appr:1")
    assert ne.unread_count() == 1


def test_different_approvals_are_never_collapsed():
    for i in range(3):
        ne.push(title="Approval needed: Save something into Friday's memory",
                source="approvals", kind="approval_pending", dedupe_key="appr:%d" % i)
    assert len([c for c in _cards() if c["kind"] == "approval_pending"]) == 3


def test_an_approval_is_never_muted_or_demoted(monkeypatch, _clean):
    monkeypatch.setattr(pol, "muted_kinds", lambda: {"approval_pending|approvals",
                                                    "scheduled_task|scheduler"})
    ne.push(title="Approval needed: send email", source="approvals", kind="approval_pending",
            dedupe_key="appr:9", tier=pol.LOG_ONLY)
    ne.push(title="Daily creation ran", source="scheduler", kind="scheduled_task")
    cards = _cards()
    assert [c["kind"] for c in cards] == ["approval_pending"], cards
    assert cards[0]["tier"] == pol.NEEDS_YOU
    assert len(_clean) == 1, "the muted kind should go to the activity log"


def test_mute_is_reversible_through_settings():
    """Through the settings file, as the Settings list and the voice tool do."""
    ne.mute("scheduled_task", "scheduler")
    assert pol.is_muted("scheduled_task", "scheduler")
    ne.unmute("scheduled_task", "scheduler")
    assert not pol.is_muted("scheduled_task", "scheduler")


def test_quiet_mid_conversation_holds_cards_but_approvals_still_arrive():
    pol.note_owner_turn()
    ne.push(title="Daily creation ran", source="scheduler", kind="scheduled_task")
    ne.push(title="Approval needed: send email", source="approvals", kind="approval_pending",
            dedupe_key="appr:2", proactive_chat=True)
    cards = _cards()
    assert [c["kind"] for c in cards] == ["approval_pending"], "an FYI card interrupted"
    assert cards[0]["quiet"] is True and ne.pending_chat_injections() == [], (
        "an approval interrupted mid-sentence")
    pol._LAST_TURN[0] = 0.0                                 # the conversation is over
    kinds = sorted(c["kind"] for c in _cards())
    assert kinds == ["approval_pending", "scheduled_task"], "held cards were not delivered"


def test_clear_all_keeps_pending_approvals():
    ne.push(title="Daily creation ran", source="scheduler", kind="scheduled_task")
    ne.push(title="Front Page failed", source="scheduler", kind="scheduled_failure", priority="high")
    ne.push(title="Approval needed: send email", source="approvals", kind="approval_pending",
            dedupe_key="appr:3")
    assert ne.dismiss_all() == 2
    assert [c["kind"] for c in _cards()] == ["approval_pending"]


# ── wiring: the scheduler, the voice/chat tool ────────────────────────────────

def _sched(monkeypatch):
    from agent_friday.services import scheduler as s
    from agent_friday.services import voice_engine as ve
    monkeypatch.setattr(ve, "_notif_engine", ne, raising=False)
    return s


def test_a_job_failing_three_times_is_one_card_and_its_next_success_resolves_it(monkeypatch):
    s = _sched(monkeypatch)
    rec = {"id": "sch_afternoon_briefing", "name": "Afternoon briefing"}
    for _ in range(3):
        s._notify_run(rec, "failed", "No model provider could generate text")
    fails = [c for c in _cards() if c["kind"] == "scheduled_failure"]
    assert len(fails) == 1 and fails[0]["count"] == 3, fails
    s._notify_run(rec, "complete", "ok")
    kinds = [c["kind"] for c in _cards()]
    assert "scheduled_failure" not in kinds, "the failure did not resolve on success"
    assert kinds == ["scheduled_task"]


def test_a_retired_schedule_goes_to_the_activity_log_only(monkeypatch, _clean):
    s = _sched(monkeypatch)
    s._notify_run({"id": "sch_edition_daily", "name": "The Friday Edition"}, "retired",
                  "retired: its built-in task no longer exists")
    assert _cards() == [] and len(_clean) == 1


def test_the_voice_and_chat_tool_summarises_clears_and_mutes():
    from agent_friday.services import notification_tools as nt
    ne.push(title="Daily creation ran", source="scheduler", kind="scheduled_task")
    ne.push(title="Approval needed: send email", source="approvals", kind="approval_pending",
            dedupe_key="appr:7")
    said = nt.handle({"action": "summary"})
    assert "need you" in said and "send email" in said
    said = nt.handle({"action": "mute"})
    assert "Muted 1 kind" in said and pol.is_muted("scheduled_task", "scheduler")
    assert not pol.is_muted("approval_pending", "approvals")
    said = nt.handle({"action": "clear"})
    assert "approval" in said and [c["kind"] for c in _cards()] == ["approval_pending"]


def test_the_tool_is_shared_into_voice():
    from agent_friday.services import voice_engine as ve
    assert "notifications" in ve._VOICE_SHARED_TOOLS
