"""NA-2 review fixes: off-the-record coalescing, held approvals, and runs due in quiet hours.

A card the policy would not pop open keeps its time and is offered when the hold ends. A scheduled run
that comes due in quiet hours runs once when they end, and says so. An off-the-record notice is never
merged into, or out of, an on-record card, and never written to notifications.json.
"""
from __future__ import annotations

import time
from datetime import datetime

import pytest

from agent_friday import notifications_engine as ne
from agent_friday.services import approval_feed, approvals
from agent_friday.services import interruption_policy as ip
from agent_friday.services import notification_policy as pol


@pytest.fixture(autouse=True)
def _clean(friday_dir, monkeypatch):
    if ne.NOTIF_FILE.exists():
        ne.NOTIF_FILE.unlink()
    monkeypatch.setattr(pol, "owner_in_conversation", lambda now=None: False)
    ip.set_focus_probe(None)
    yield


def _settings(monkeypatch, **s):
    monkeypatch.setattr(ip, "_settings", lambda: dict(s))


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


@pytest.fixture
def approval_store(tmp_path, monkeypatch):
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    approval_feed.reset()
    yield
    approval_feed.reset()


def _raise(i):
    return approvals.create_approval(
        kind="tainted_action", subject_type="tool_action", subject_id="s%d" % i, title="Open page %d" % i,
        action_description="send the weekly roundup %d to the desk" % i, force_gate=True,
        requested_by="taint_gate")


# ── B3: coalescing and the off-the-record guarantee ─────────────────────────

def test_an_off_the_record_notice_is_never_merged_into_an_on_record_card(monkeypatch):
    ne.push(title="Front Page ready", source="scheduler", kind="scheduled_task", emitter="E07", dedupe_key="sched-ok:a")
    monkeypatch.setattr(ne, "_off_record_now", lambda: True)
    ne.push(title="Secret plan ready", body="private words", source="scheduler", kind="scheduled_task", emitter="E07",
            dedupe_key="sched-ok:b")
    monkeypatch.setattr(ne, "_off_record_now", lambda: False)
    stored = ne.NOTIF_FILE.read_text(encoding="utf-8")
    assert "Secret plan" not in stored and "private words" not in stored
    cards = ne._load()
    assert len(cards) == 2
    on = [c for c in cards if not c.get("off_record")][0]
    assert on["title"] == "Front Page ready" and on["count"] == 1


def test_an_on_record_notice_is_never_merged_into_an_off_record_card(monkeypatch):
    monkeypatch.setattr(ne, "_off_record_now", lambda: True)
    ne.push(title="Secret plan ready", source="scheduler", kind="scheduled_task", emitter="E07", dedupe_key="sched-ok:a")
    monkeypatch.setattr(ne, "_off_record_now", lambda: False)
    ne.push(title="Front Page ready", source="scheduler", kind="scheduled_task", emitter="E07", dedupe_key="sched-ok:b")
    on = [c for c in ne._load() if c["title"] == "Front Page ready"]
    assert len(on) == 1 and not on[0].get("off_record") and on[0]["count"] == 1


def test_a_burst_card_follows_the_latest_item_not_the_first():
    ne.push(title="One", source="scheduler", kind="scheduled_task", emitter="E07", dedupe_key="sched-ok:a",
            target={"workspace": "news", "tab": "a"})
    ne.push(title="Two", source="scheduler", kind="scheduled_task", emitter="E07", dedupe_key="sched-ok:b",
            target={"workspace": "news", "tab": "b"})
    card = ne.list_notifications(limit=5)[0]
    assert card["count"] == 2 and card["target"] == {"workspace": "news", "tab": "b"}


# ── m1: safety is checked before tier ───────────────────────────────────────

def test_a_safety_critical_notice_is_never_marked_quiet_even_with_an_explicit_fyi_tier(monkeypatch):
    _settings(monkeypatch, quiet_until_ts=time.time() + 3600)
    c = ne.push(title="Spend halted", source="spend-guard", kind="spend_halt", priority="high", tier="fyi",
                dedupe_key="spend_halt:x")
    assert c["interrupt"] is True and c["quiet"] is False


# ── M2: a held approval keeps its time and is offered when the hold ends ────

def test_a_held_approval_keeps_the_expiry_its_class_gave_it_and_pops_open_when_the_hold_ends(
        approval_store, monkeypatch):
    _settings(monkeypatch, quiet_until_ts=time.time() + 3600)
    q = approval_feed.subscribe()
    rec = _raise(1)
    held = approvals.get_approval(rec["approval_id"])
    assert held["held_by_policy"] is True
    assert held["expires_at"] == rec["expires_at"], "holding never moves the owner's expiry"
    assert held["expires_at"] > time.time(), "the time shown is a real, future one"
    _drain(q)
    _settings(monkeypatch)                                                    # the quiet is over
    assert approvals.release_held() == 1
    out = approvals.get_approval(rec["approval_id"])
    assert out["held_by_policy"] is False and out["released_at"] > 0 and out["expires_at"] == rec["expires_at"]
    ev = [e for e in _drain(q) if e["type"] == "pending"]
    assert ev and "interrupt" not in ev[-1], "the pages are told, and now pop it open"
    assert approvals.release_held() == 0


def test_a_short_class_expiry_is_not_stretched_across_a_night_of_quiet(approval_store, monkeypatch):
    """A3: the owner set external messages to expire in 15 minutes; quiet hours must not undo that."""
    monkeypatch.setattr(approvals, "_load_settings",
                        lambda: {"approvals_policy": {"external_message": {"expires_seconds": 900}}})
    _settings(monkeypatch, quiet_until_ts=time.time() + 8 * 3600)
    rec = approvals.create_approval(
        kind="tainted_action", subject_type="tool_action", subject_id="msg1", title="Email the roundup",
        action_description="email the weekly roundup to the desk", force_gate=True, requested_by="taint_gate")
    assert rec["policy_class"] == "external_message" and rec["expires_at"] - rec["created_at"] == 900
    assert approvals.get_approval(rec["approval_id"])["held_by_policy"] is True
    approvals._patch(rec["approval_id"], created_at=time.time() - 8 * 3600,
                     expires_at=time.time() - 8 * 3600 + 900)                # the night has passed
    _settings(monkeypatch)                                                    # quiet is over
    assert approvals.release_held() == 0, "a card whose time ran out is never released"
    out = approvals.get_approval(rec["approval_id"])
    assert out["status"] == "expired" and out["decided_by"] == "system:expiry"
    _, won = approvals.decide_with_outcome(rec["approval_id"], "approve", decided_by="owner")
    assert won is False, "an old approval cannot be approved into running a stale action"
    notes = [c for c in ne.list_notifications(limit=20) if c.get("kind") == "approval_expired_held"]
    assert notes and "expired before you saw it" in notes[0]["title"]


def test_if_quiet_never_ends_a_held_card_still_expires_by_its_class_cap(approval_store, monkeypatch):
    _settings(monkeypatch, honour_focus_assist=True)
    ip.set_focus_probe(lambda: True)                                          # Focus Assist stays on
    rec = _raise(1)
    assert approvals.get_approval(rec["approval_id"])["held_by_policy"] is True
    approvals._patch(rec["approval_id"], expires_at=time.time() - 5)
    assert approvals.expire_stale() == 1 and approvals.get_approval(rec["approval_id"])["status"] == "expired"


def _open_page_card(i=1):
    return approvals.create_approval(kind="tainted_action", subject_type="tool_action", subject_id="o%d" % i,
                                     title="Open a page", action_description="open_url https://example.test/%d" % i,
                                     force_gate=True, requested_by="taint_gate")


def test_a_hold_never_gives_a_card_an_expiry_so_a_released_one_cannot_expire_silently(approval_store, monkeypatch):
    """N1: a class with no expiry stays without one while held, and after release, however long it waits."""
    _settings(monkeypatch, quiet_until_ts=time.time() + 3600)
    rec = _open_page_card()
    assert rec["expires_at"] is None, "this class never expires when it is not held"
    held = approvals.get_approval(rec["approval_id"])
    assert held["held_by_policy"] is True and held["expires_at"] is None, "a hold does not invent an expiry"
    _settings(monkeypatch)                                                    # the quiet is over
    assert approvals.release_held() == 1
    out = approvals.get_approval(rec["approval_id"])
    assert out["held_by_policy"] is False and out["expires_at"] is None
    approvals._patch(rec["approval_id"], created_at=time.time() - 25 * 3600, held_at=time.time() - 25 * 3600)
    assert approvals.expire_stale() == 0
    assert approvals.get_approval(rec["approval_id"])["status"] == "pending", "25 hours later it still waits"


def test_a_hold_that_lasts_the_cap_releases_the_card_into_needs_you_and_does_not_expire_it(approval_store, monkeypatch):
    """N1: the cap is on the HOLD. When it is reached the card moves into Needs you, quietly, unexpired."""
    _settings(monkeypatch, quiet_until_ts=time.time() + 72 * 3600)
    q = approval_feed.subscribe()
    rec = _open_page_card()
    assert approvals.get_approval(rec["approval_id"])["held_by_policy"] is True
    _drain(q)
    approvals._patch(rec["approval_id"], held_at=time.time() - approvals.HOLD_CAP_SECONDS - 5)
    assert approvals.release_held() == 1
    out = approvals.get_approval(rec["approval_id"])
    assert out["status"] == "pending" and out["held_by_policy"] is False and out["hold_capped"] is True
    assert out["expires_at"] is None, "the cap never gives the card a deadline"
    ev = [e for e in _drain(q) if e["type"] == "pending"]
    assert ev and ev[-1].get("interrupt") is False, "listed and counted in Needs you, not popped open"
    assert approvals.expire_stale() == 0 and approvals.get_approval(rec["approval_id"])["status"] == "pending"
    assert approvals.release_held() == 0, "released once"


def test_a_card_that_expires_while_still_held_says_so_in_needs_you(approval_store, monkeypatch):
    """N1/m6/m-G: a card that never popped and ran out leaves a notice in Needs you."""
    _settings(monkeypatch, quiet_until_ts=time.time() + 3600)
    rec = _raise(1)
    assert approvals.get_approval(rec["approval_id"])["held_by_policy"] is True
    approvals._patch(rec["approval_id"], expires_at=time.time() - 5)
    assert approvals.expire_stale() == 1
    notes = [c for c in ne.list_notifications(limit=20) if c.get("kind") == "approval_expired_held"]
    assert notes and "expired before you saw it" in notes[0]["title"]
    assert notes[0].get("place") == "needs_you" and notes[0].get("tier") == "needs_you"


def test_a_card_that_was_released_and_popped_does_not_claim_it_expired_unseen(approval_store, monkeypatch):
    """m-G: once released it popped open; its later expiry is the owner's own class expiry."""
    _settings(monkeypatch, quiet_until_ts=time.time() + 3600)
    rec = _raise(1)
    _settings(monkeypatch)
    assert approvals.release_held() == 1
    approvals._patch(rec["approval_id"], expires_at=time.time() - 5)
    assert approvals.expire_stale() == 1
    assert [c for c in ne.list_notifications(limit=20) if c.get("kind") == "approval_expired_held"] == []


def test_a_capped_card_that_later_expires_says_so(approval_store, monkeypatch):
    _settings(monkeypatch, quiet_until_ts=time.time() + 72 * 3600)
    rec = _raise(1)
    approvals._patch(rec["approval_id"], held_at=time.time() - approvals.HOLD_CAP_SECONDS - 5)
    assert approvals.release_held() == 1
    approvals._patch(rec["approval_id"], expires_at=time.time() - 5)
    assert approvals.expire_stale() == 1
    assert [c for c in ne.list_notifications(limit=20) if c.get("kind") == "approval_expired_held"]


def test_notices_about_dead_cards_never_use_the_budget_ahead_of_a_live_approval(approval_store, monkeypatch):
    """m-C: the live held card is judged first; the expiry notice comes after."""
    _settings(monkeypatch, quiet_until_ts=time.time() + 3600, interruption_budget_per_hour=1)
    live, dead = _raise(1), _raise(2)
    _settings(monkeypatch, interruption_budget_per_hour=1)                    # quiet is over; one slot an hour
    approvals._patch(dead["approval_id"], expires_at=time.time() - 5)
    assert approvals.release_held() == 1
    assert approvals.get_approval(live["approval_id"])["held_by_policy"] is False, "the live card got the slot"
    assert approvals.get_approval(dead["approval_id"])["status"] == "expired"
    assert [c for c in ne.list_notifications(limit=20) if c.get("kind") == "approval_expired_held"]


def test_the_expiry_notice_of_an_off_the_record_card_keeps_its_title_off_the_disk(approval_store, monkeypatch):
    """N3: the card was raised off the record; the mode has ended by the time it expires."""
    from agent_friday.services import off_record
    _settings(monkeypatch, quiet_until_ts=time.time() + 3600)
    monkeypatch.setattr(off_record, "skip", lambda store, settings=None: True)
    rec = approvals.create_approval(
        kind="tainted_action", subject_type="tool_action", subject_id="priv1",
        title="Email Dr. X about the biopsy result", action_description="send the weekly roundup to the desk",
        force_gate=True, requested_by="taint_gate")
    monkeypatch.setattr(off_record, "skip", lambda store, settings=None: False)   # off the record has ended
    card = approvals.get_approval(rec["approval_id"])
    assert card["off_record"] is True and card["held_by_policy"]
    approvals._patch(rec["approval_id"], expires_at=time.time() - 5)
    assert approvals.expire_stale() == 1
    stored = ne.NOTIF_FILE.read_text(encoding="utf-8") if ne.NOTIF_FILE.exists() else ""
    assert "biopsy" not in stored and "Dr. X" not in stored, "the title never reaches notifications.json"
    note = [c for c in ne._load() if c.get("kind") == "approval_expired_held"]
    assert note and "biopsy" not in note[0]["title"] and note[0]["off_record"] is True
    assert "off-the-record" in note[0]["title"]


def test_a_card_decided_a_moment_ago_is_not_re_announced(approval_store, monkeypatch):
    _settings(monkeypatch, quiet_until_ts=time.time() + 3600)
    rec = _raise(1)
    q = approval_feed.subscribe()
    approvals.decide(rec["approval_id"], "deny", decided_by="owner")
    _drain(q)
    assert approvals._release_one(rec["approval_id"], time.time()) is None
    _settings(monkeypatch)
    assert approvals.release_held() == 0
    assert [e for e in _drain(q) if e["type"] == "pending"] == []


def test_while_quiet_is_still_on_a_poll_writes_no_state(approval_store, monkeypatch):
    _settings(monkeypatch, quiet_until_ts=time.time() + 3600)
    _raise(1)
    saves = []
    real = ip._save
    monkeypatch.setattr(ip, "_save", lambda: saves.append(1) or real())
    for _ in range(5):
        assert approvals.release_held() == 0
    assert saves == [], "no forget + verdict rewriting on every poll"


def test_a_hold_that_has_not_ended_releases_nothing(approval_store, monkeypatch):
    _settings(monkeypatch, quiet_until_ts=time.time() + 3600)
    rec = _raise(1)
    assert approvals.release_held() == 0
    assert approvals.get_approval(rec["approval_id"])["held_by_policy"] is True


def test_a_held_card_can_still_be_decided_exactly_as_before(approval_store, monkeypatch):
    _settings(monkeypatch, quiet_until_ts=time.time() + 3600)
    rec = _raise(1)
    out, won = approvals.decide_with_outcome(rec["approval_id"], "approve", decided_by="owner")
    assert won and out["status"] == "approved"


def test_a_decided_card_forgets_its_verdict(approval_store, monkeypatch):
    _settings(monkeypatch, quiet_until_ts=time.time() + 3600)
    rec = _raise(1)
    key = "approval:%s" % rec["approval_id"]
    assert ip.held(key) is True
    approvals.decide(rec["approval_id"], "deny", decided_by="owner")
    assert ip.held(key) is False


def test_the_text_offer_for_a_held_card_goes_out_when_the_hold_ends(approval_store, monkeypatch):
    from agent_friday.phone import config as pcfg
    from agent_friday.phone import service as phone
    sent = []
    monkeypatch.setattr(pcfg, "load", lambda: {"enabled": True, "sms_approvals": True})
    monkeypatch.setattr(pcfg, "verified_owner_cell", lambda: "+15550100")
    monkeypatch.setattr(phone, "text_owner", lambda body, reason="alert": sent.append(body) or {"ok": True})
    monkeypatch.setattr(phone, "_gate_text", lambda t: t)          # no classifier, no model, no network
    _settings(monkeypatch, quiet_until_ts=time.time() + 3600)
    rec = _raise(1)
    approvals._patch(rec["approval_id"], created_at=time.time() - 7200)       # held all night
    assert phone.offer_pending_approvals() == 0 and sent == []
    _settings(monkeypatch)
    approvals.release_held()
    assert phone.offer_pending_approvals() == 1 and len(sent) == 1, "texted when the hold ended, not dropped as old"


def test_nothing_promises_a_morning_digest_that_does_not_exist():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    for rel in ("src/agent_friday/services/interruption_policy.py", "src/agent_friday/services/notification_tools.py",
                "docs/user-guide/notifications.md", "index.html", "ui_parts/app.html"):
        assert "morning digest" not in (root / rel).read_text(encoding="utf-8"), rel


# ── M3/A1/A4: a run due in quiet hours runs once when they end ──────────────

class _Store:
    """A schedule store in memory: _patch_record writes into the record the tests tick."""

    def __init__(self, rec):
        self.rec = rec
        self.patches = []

    def patch(self, sid, **kw):
        self.patches.append(kw)
        self.rec.update(kw)


def _sched(monkeypatch, **extra):
    from agent_friday.services import scheduler
    rec = {"id": "s1", "name": "Nightly check", "enabled": True, "trigger": "daily",
           "spec": {"hour": 23, "minute": 0}, "task": {"kind": "agent_prompt", "prompt": "x"}}
    rec.update(extra)
    store = _Store(rec)
    monkeypatch.setattr(scheduler, "_patch_record", store.patch)
    monkeypatch.setattr(scheduler, "_read_store", lambda: [store.rec])
    return scheduler, rec, store


def _run(scheduler, rec, now):
    """What a tick does for a due schedule: dispatch records the run (without starting a thread)."""
    rec.update(scheduler._run_mark(rec, now, now.timestamp()))


def test_a_daily_run_due_in_quiet_hours_runs_once_when_they_end_and_says_so(monkeypatch):
    _settings(monkeypatch, quiet_hours_enabled=True)
    scheduler, rec, store = _sched(monkeypatch)
    assert scheduler._is_due(rec, datetime(2026, 10, 12, 23, 30)) is False        # due 23:00, inside quiet
    assert rec["quiet_deferred"]["day"] == "2026-10-12"
    notes = [c for c in ne.list_notifications(limit=20) if c.get("kind") == "scheduled_deferred"]
    assert notes and "Nightly check" in notes[0]["title"]
    assert scheduler._is_due(rec, datetime(2026, 10, 13, 3, 0)) is False           # still quiet
    assert scheduler._is_due(rec, datetime(2026, 10, 13, 7, 5)) is True            # quiet is over: it runs, once
    assert len(store.patches) == 1, "the deferral is recorded once"


def test_a_nightly_job_runs_once_each_day_across_two_days(monkeypatch):
    """A1: the late run is recorded as the previous slot's, so the same evening's slot still comes due."""
    _settings(monkeypatch, quiet_hours_enabled=True)
    scheduler, rec, store = _sched(monkeypatch)
    runs = []
    for day in (12, 13, 14):
        evening = datetime(2026, 10, day, 23, 30)
        assert scheduler._is_due(rec, evening) is False                           # deferred every evening
        assert rec["quiet_deferred"]["day"] == "2026-10-%02d" % day
        morning = datetime(2026, 10, day + 1, 7, 5)
        assert scheduler._is_due(rec, morning) is True
        _run(scheduler, rec, morning)
        runs.append((morning.date(), rec["last_run_date"]))
        assert scheduler._is_due(rec, morning) is False, "ran, so not due again the same morning"
    assert runs == [(datetime(2026, 10, 13).date(), "2026-10-12"), (datetime(2026, 10, 14).date(), "2026-10-13"),
                    (datetime(2026, 10, 15).date(), "2026-10-14")]


def test_the_late_run_goes_through_the_schedules_other_gates(monkeypatch):
    _settings(monkeypatch)
    scheduler, rec, store = _sched(monkeypatch, quiet_deferred={"day": "2026-10-12", "at": 1.0})
    morning = datetime(2026, 10, 13, 7, 5)
    assert scheduler._is_due(rec, morning) is True
    rec["retry_pending"], rec["not_before"] = True, morning.timestamp() + 600
    assert scheduler._is_due(rec, morning) is False, "retry backoff still holds it"
    rec["retry_pending"], rec["not_before"] = False, morning.timestamp() + 600
    assert scheduler._is_due(rec, morning) is False, "not_before still holds it"
    rec["not_before"] = 0
    rec["catch_up"] = {"date": "2026-10-12"}
    assert scheduler._is_due(rec, morning) is False, "a catch-up from another day is not carried over"
    rec["catch_up"] = None
    rec["enabled"] = False
    assert scheduler._is_due(rec, morning) is False
    rec["enabled"] = True
    scheduler._RUNNING.add("s1")
    try:
        assert scheduler._is_due(rec, morning) is False
    finally:
        scheduler._RUNNING.discard("s1")


def test_editing_a_schedule_forgets_a_run_that_was_waiting(monkeypatch):
    from agent_friday.services import scheduler
    cur = {"id": "s1", "name": "n", "trigger": "once", "spec": {"at": 1}, "task": {"kind": "agent_prompt"},
           "enabled": True, "quiet_deferred": {"day": "2026-10-12", "at": 1.0}}
    monkeypatch.setattr(scheduler, "get_schedule", lambda sid: cur)
    monkeypatch.setattr(scheduler, "_upsert", lambda rec: rec)
    assert scheduler.update_schedule("s1", {"name": "renamed"})["quiet_deferred"], "a rename is not a new decision"
    for patch in ({"spec": {"at": 99}}, {"trigger": "daily"}, {"task": {"kind": "agent_prompt", "prompt": "y"}},
                  {"enabled": False}):
        cur["quiet_deferred"] = {"day": "2026-10-12", "at": 1.0}
        assert scheduler.update_schedule("s1", patch)["quiet_deferred"] is None, patch


def test_a_one_time_job_deferred_past_its_slot_asks_again_and_runs_only_on_a_yes(
        approval_store, monkeypatch):
    _settings(monkeypatch, quiet_hours_enabled=True)
    at = datetime(2026, 10, 12, 23, 30).timestamp()
    scheduler, rec, store = _sched(monkeypatch, trigger="once", spec={"at": at})
    dispatched = []
    monkeypatch.setattr(scheduler, "dispatch",
                        lambda r, manual=False, require_enabled=False: dispatched.append((r["id"], manual)))
    assert scheduler._is_due(rec, datetime(2026, 10, 12, 23, 31)) is False
    morning = datetime(2026, 10, 13, 7, 1)
    assert scheduler._is_due(rec, morning) is False, "it does not run under its old terms"
    assert scheduler._is_due(rec, morning) is False
    cards = approvals.list_approvals(status="pending", kind=scheduler.LATE_RUN_KIND)
    assert len(cards) == 1 and "run it now?" in cards[0]["title"] and dispatched == []
    approvals.decide(cards[0]["approval_id"], "approve", decided_by="owner")
    assert dispatched == [("s1", True)]


def test_a_no_to_the_late_one_time_job_drops_it(approval_store, monkeypatch):
    _settings(monkeypatch, quiet_hours_enabled=True)
    at = datetime(2026, 10, 12, 23, 30).timestamp()
    scheduler, rec, store = _sched(monkeypatch, trigger="once", spec={"at": at})
    dispatched = []
    monkeypatch.setattr(scheduler, "dispatch",
                        lambda r, manual=False, require_enabled=False: dispatched.append(r["id"]))
    scheduler._is_due(rec, datetime(2026, 10, 12, 23, 31))
    scheduler._is_due(rec, datetime(2026, 10, 13, 7, 1))
    card = approvals.list_approvals(status="pending", kind=scheduler.LATE_RUN_KIND)[0]
    approvals.decide(card["approval_id"], "deny", decided_by="owner")
    assert dispatched == [] and rec["enabled"] is False and rec["quiet_deferred"] is None


def test_a_builtin_routine_and_a_run_outside_quiet_hours_are_untouched(monkeypatch):
    _settings(monkeypatch, quiet_hours_enabled=True)
    scheduler, rec, store = _sched(monkeypatch, task={"kind": "builtin", "ref": "briefing"})
    assert scheduler._is_due(rec, datetime(2026, 10, 12, 23, 30)) is True and not store.patches
    scheduler, rec, store = _sched(monkeypatch)
    assert scheduler._is_due(rec, datetime(2026, 10, 12, 23, 30)) is False
    _settings(monkeypatch)
    assert scheduler._is_due(dict(rec, quiet_deferred=None), datetime(2026, 10, 12, 23, 30)) is True


# ── N2: a late-run card is about the job as it was asked ────────────────────

def _store_sched(monkeypatch, **extra):
    """A schedule store that update_schedule can write to as well as read."""
    scheduler, rec, store = _sched(monkeypatch, **extra)

    def _up(r):
        rec.clear()
        rec.update(r)
        return rec
    monkeypatch.setattr(scheduler, "_upsert", _up)
    monkeypatch.setattr(scheduler, "get_schedule", lambda sid: dict(rec) if rec.get("id") == sid else None)
    return scheduler, rec, store


def _late_card(scheduler, rec):
    at = datetime(2026, 10, 12, 23, 30).timestamp()
    rec.update(trigger="once", spec={"at": at})
    assert scheduler._is_due(rec, datetime(2026, 10, 12, 23, 31)) is False
    assert scheduler._is_due(rec, datetime(2026, 10, 13, 7, 1)) is False
    return approvals.list_approvals(status="pending", kind=scheduler.LATE_RUN_KIND)[0]


@pytest.fixture
def late_once(approval_store, monkeypatch):
    _settings(monkeypatch, quiet_hours_enabled=True)
    scheduler, rec, store = _store_sched(monkeypatch)
    dispatched = []
    monkeypatch.setattr(scheduler, "dispatch",
                        lambda r, manual=False, require_enabled=False: dispatched.append((r["id"], r.get("task"))))
    card = _late_card(scheduler, rec)
    return scheduler, rec, card, dispatched


def _stale_notices():
    return [c for c in ne.list_notifications(limit=30) if "changed since" in (c.get("title") or "")]


def test_a_no_after_the_owner_rescheduled_the_job_does_not_disable_it(late_once):
    scheduler, rec, card, dispatched = late_once
    scheduler.update_schedule("s1", {"spec": {"at": datetime(2026, 10, 13, 10, 0).timestamp()}})
    # the edit already resolved the card as superseded; a click on it afterwards changes nothing
    approvals.decide(card["approval_id"], "deny", decided_by="owner")
    assert rec["enabled"] is True, "the edited job was not switched off by the old card"
    assert dispatched == [] and _stale_notices()


def test_an_approve_after_an_edit_runs_nothing(late_once):
    scheduler, rec, card, dispatched = late_once
    scheduler.update_schedule("s1", {"task": {"kind": "agent_prompt", "prompt": "something else"}})
    assert approvals.get_approval(card["approval_id"])["status"] == "denied", "the old card was superseded"
    out, won = approvals.decide_with_outcome(card["approval_id"], "approve", decided_by="owner")
    assert won is False and dispatched == []


def test_the_hook_itself_refuses_a_card_for_an_edited_job(late_once):
    """Even if the card were decided some other way, the hook compares the job with what was asked."""
    scheduler, rec, card, dispatched = late_once
    rec["task"] = {"kind": "agent_prompt", "prompt": "edited behind the card's back"}
    approvals.decide(card["approval_id"], "approve", decided_by="owner")
    assert dispatched == [] and rec["enabled"] is True and _stale_notices()


def test_an_approve_after_the_job_was_switched_off_runs_nothing(late_once):
    scheduler, rec, card, dispatched = late_once
    rec["enabled"] = False
    approvals.decide(card["approval_id"], "approve", decided_by="owner")
    assert dispatched == [] and _stale_notices()


def test_a_card_expiring_after_an_edit_does_not_disable_the_edited_job(late_once):
    scheduler, rec, card, dispatched = late_once
    rec["spec"] = {"at": datetime(2026, 10, 14, 9, 0).timestamp()}            # edited; card left pending
    approvals._patch(card["approval_id"], expires_at=time.time() - 5)
    assert approvals.expire_stale() >= 1
    assert rec["enabled"] is True and dispatched == [] and _stale_notices()


def test_a_card_expiring_with_the_job_unchanged_drops_the_run_and_says_so(late_once):
    scheduler, rec, card, dispatched = late_once
    approvals._patch(card["approval_id"], expires_at=time.time() - 5)
    approvals.expire_stale()
    assert rec["enabled"] is False and dispatched == []
    assert [c for c in ne.list_notifications(limit=30) if "did not answer in time" in (c.get("title") or "")]


def test_deleting_the_job_resolves_its_late_card_without_running_anything(late_once, monkeypatch):
    scheduler, rec, card, dispatched = late_once
    monkeypatch.setattr(scheduler, "_write_store", lambda recs: rec.clear() or rec.update({"gone": True}))
    assert scheduler.delete_schedule("s1") is True
    assert approvals.get_approval(card["approval_id"])["status"] == "denied" and dispatched == []


def test_dispatch_for_a_late_run_refuses_a_job_that_is_switched_off(monkeypatch):
    _settings(monkeypatch)
    scheduler, rec, store = _sched(monkeypatch, enabled=False)
    assert scheduler.dispatch(rec, manual=True, require_enabled=True) is None
    assert "s1" not in scheduler._RUNNING


# ── m1: a late run's retry records the slot it was raised for ───────────────

def test_a_late_runs_retry_and_seat_wait_keep_the_missed_slots_day(monkeypatch):
    _settings(monkeypatch)
    scheduler, rec, store = _sched(monkeypatch, quiet_deferred={"day": "2026-10-12", "at": 1.0})
    morning = datetime(2026, 10, 13, 7, 5)
    _run(scheduler, rec, morning)
    assert rec["last_run_date"] == "2026-10-12" and rec["slot_day"] == "2026-10-12"
    rec.update(retry_pending=True, retry_count=1, not_before=0)                # the late run failed; retry pending
    retry = datetime(2026, 10, 13, 7, 20)
    _run(scheduler, rec, retry)
    assert rec["last_run_date"] == "2026-10-12", "the retry is still the 12th's slot, not today's"
    rec.update(retry_pending=False, catch_up={"date": "2026-10-13", "since": 1.0})   # waiting for the seat
    _run(scheduler, rec, datetime(2026, 10, 13, 8, 0))
    assert rec["last_run_date"] == "2026-10-12"
    rec.update(catch_up=None)
    _run(scheduler, rec, datetime(2026, 10, 13, 23, 0))
    assert rec["last_run_date"] == "2026-10-13" and rec["slot_day"] is None, "the next ordinary run is today's"


# ── m2: only a real change drops a waiting run, and it says so ──────────────

def test_saving_the_same_values_leaves_a_waiting_run_alone(monkeypatch):
    _settings(monkeypatch)
    scheduler, rec, store = _store_sched(monkeypatch, quiet_deferred={"day": "2026-10-12", "at": 1.0})
    same = {"enabled": True, "trigger": rec["trigger"], "spec": dict(rec["spec"]), "task": dict(rec["task"])}
    assert scheduler.update_schedule("s1", same)["quiet_deferred"], "a workflow save or a resume changes nothing"
    assert scheduler.update_schedule("s1", {"enabled": True})["quiet_deferred"]


def test_a_real_edit_drops_the_waiting_run_and_a_notice_says_so(monkeypatch):
    _settings(monkeypatch)
    scheduler, rec, store = _store_sched(monkeypatch, quiet_deferred={"day": "2026-10-12", "at": 1.0})
    assert scheduler.update_schedule("s1", {"spec": {"hour": 9, "minute": 0}})["quiet_deferred"] is None
    notes = [c for c in ne.list_notifications(limit=30) if "was dropped" in (c.get("title") or "")]
    assert notes and "Nightly check" in notes[0]["title"]


# ── m3: the held-card budget update is idempotent ───────────────────────────

def test_a_budget_hold_rewrites_nothing_on_every_tick(approval_store, monkeypatch):
    _settings(monkeypatch, interruption_budget_per_hour=1)
    _raise(1)                                                                # uses the one slot
    rec2 = _raise(2)                                                         # over budget: held
    assert approvals.get_approval(rec2["approval_id"])["held_by_policy"] is True
    saves = []
    real = ip._save
    monkeypatch.setattr(ip, "_save", lambda: saves.append(1) or real())
    for _ in range(5):
        assert approvals.release_held() == 0
    assert saves == [], "no forget + verdict rewriting while the budget is still full"
    assert approvals.get_approval(rec2["approval_id"])["held_by_policy"] is True


def test_two_releases_at_once_charge_the_budget_once_and_leave_no_stale_held(approval_store, monkeypatch):
    import threading
    _settings(monkeypatch, quiet_until_ts=time.time() + 3600)
    rec = _raise(1)
    _settings(monkeypatch)
    key = "approval:%s" % rec["approval_id"]
    before = ip.used_this_hour()
    gate = threading.Barrier(4)
    counts = []

    def go():
        gate.wait()
        counts.append(approvals.release_held())
    ts = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(counts) == 1
    assert ip.used_this_hour() - before == 1, "one release, one slot"
    assert ip.held(key) is False
    assert approvals.release_held() == 0 and ip.used_this_hour() - before == 1


# ── m7: the card names the real cause ───────────────────────────────────────

def test_the_late_card_says_focus_assist_when_that_was_the_cause(approval_store, monkeypatch):
    _settings(monkeypatch, honour_focus_assist=True)
    ip.set_focus_probe(lambda: True)
    scheduler, rec, store = _sched(monkeypatch)
    at = datetime(2026, 10, 12, 23, 30).timestamp()
    rec.update(trigger="once", spec={"at": at})
    assert scheduler._is_due(rec, datetime(2026, 10, 12, 23, 31)) is False
    assert rec["quiet_deferred"]["reason"] == "focus"
    ip.set_focus_probe(lambda: False)
    assert scheduler._is_due(rec, datetime(2026, 10, 13, 7, 1)) is False
    card = approvals.list_approvals(status="pending", kind=scheduler.LATE_RUN_KIND)[0]
    assert "Focus Assist" in card["title"] and "quiet hours" not in card["title"]
    assert "quiet hours" not in card["description"]
    notes = [c for c in ne.list_notifications(limit=30) if c.get("kind") == "scheduled_deferred"]
    assert notes and "Focus Assist" in notes[-1]["title"]


# ── N4: a rescheduled one-time job is asked about again ─────────────────────

def test_a_one_time_job_rescheduled_and_deferred_again_the_same_day_gets_a_fresh_card(late_once):
    scheduler, rec, card, dispatched = late_once
    first_at = rec["quiet_deferred"]["at"]
    later = datetime(2026, 10, 12, 23, 45).timestamp()
    scheduler.update_schedule("s1", {"spec": {"at": later}})                   # superseded; deferral cleared
    assert approvals.get_approval(card["approval_id"])["status"] == "denied" and rec["quiet_deferred"] is None
    assert scheduler._is_due(rec, datetime(2026, 10, 12, 23, 46)) is False     # due in quiet hours again, same day
    assert rec["quiet_deferred"]["day"] == "2026-10-12" and rec["quiet_deferred"]["at"] != first_at
    assert scheduler._is_due(rec, datetime(2026, 10, 13, 7, 1)) is False
    pending = approvals.list_approvals(status="pending", kind=scheduler.LATE_RUN_KIND)
    assert len(pending) == 1 and pending[0]["approval_id"] != card["approval_id"], "a new card, not the old one"
    approvals.decide(pending[0]["approval_id"], "approve", decided_by="owner")
    assert dispatched == [("s1", rec["task"])], "approving the new card runs the job"


def test_a_rolled_back_save_that_superseded_the_card_asks_again(late_once):
    """The workflow save restores the old schedule bytes (workflow_overview) after a later step failed."""
    import copy
    scheduler, rec, card, dispatched = late_once
    snapshot = copy.deepcopy(rec)
    scheduler.update_schedule("s1", {"spec": {"at": datetime(2026, 10, 14, 9, 0).timestamp()}})
    assert approvals.get_approval(card["approval_id"])["status"] == "denied"
    rec.clear()
    rec.update(snapshot)                                                       # the rollback
    assert scheduler._is_due(rec, datetime(2026, 10, 13, 7, 2)) is False
    pending = approvals.list_approvals(status="pending", kind=scheduler.LATE_RUN_KIND)
    assert len(pending) == 1 and pending[0]["approval_id"] != card["approval_id"]
    assert scheduler._is_due(rec, datetime(2026, 10, 13, 7, 3)) is False
    assert len(approvals.list_approvals(status="pending", kind=scheduler.LATE_RUN_KIND)) == 1, "asked once"
    approvals.decide(pending[0]["approval_id"], "approve", decided_by="owner")
    assert len(dispatched) == 1


def test_an_answered_card_is_not_asked_again_on_the_next_tick(late_once):
    scheduler, rec, card, dispatched = late_once
    approvals.decide(card["approval_id"], "deny", decided_by="owner")
    assert rec["enabled"] is False
    rec["enabled"] = True                                                      # even if switched back on
    rec["quiet_deferred"] = {"day": "2026-10-12", "at": card["payload"]["at"]}
    assert scheduler._is_due(rec, datetime(2026, 10, 13, 7, 5)) is False
    assert approvals.list_approvals(status="pending", kind=scheduler.LATE_RUN_KIND) == []


# ── m-B / m-E: the dropped-run notice names no actor and promises nothing it cannot keep ──

def test_the_dropped_notice_does_not_say_you_edited_and_does_not_promise_a_run_for_a_switched_off_job(monkeypatch):
    _settings(monkeypatch)
    scheduler, rec, store = _store_sched(monkeypatch, quiet_deferred={"day": "2026-10-12", "at": 1.0})
    scheduler.update_schedule("s1", {"enabled": False})
    notes = [c for c in ne.list_notifications(limit=30) if "was dropped" in (c.get("title") or "")]
    text = notes[0]["title"] + " " + notes[0].get("body", "")
    assert "you edited" not in text.lower() and "new schedule" not in text
    assert "switched off" in text


# ── m7 residual: the run summary names the real cause ───────────────────────

def test_a_late_run_summary_names_the_cause_and_keeps_it_through_a_retry(monkeypatch):
    _settings(monkeypatch)
    scheduler, rec, store = _sched(monkeypatch, quiet_deferred={"day": "2026-10-12", "at": 1.0, "reason": "focus"})
    assert scheduler._cause_words(scheduler._late_cause(rec)) == "Focus Assist"
    _run(scheduler, rec, datetime(2026, 10, 13, 7, 5))
    rec.update(retry_pending=True)
    assert scheduler._cause_words(scheduler._late_cause(rec)) == "Focus Assist"


# ── m-A: a page that connects or reloads later does not pop a held or quietly released card ──

def test_the_snapshot_lists_held_and_capped_cards_as_quiet_so_a_reload_does_not_pop_them(approval_store):
    from agent_friday.routes import goals
    held = {"approval_id": "a1", "held_by_policy": True}
    capped = {"approval_id": "a2", "held_by_policy": False, "hold_capped": True}
    live = {"approval_id": "a3"}
    evt = goals._snapshot_event([held, capped, live])
    assert evt["type"] == "snapshot" and [p["approval_id"] for p in evt["pending"]] == ["a1", "a2", "a3"]
    assert evt["quiet"] == ["a1", "a2"], "every card is in the list; only the held and capped ones are quiet"
