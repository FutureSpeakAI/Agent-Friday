"""Dismissal sticks to the job, and the tray groups its cards.

A dismissal is keyed to the job a card is about, kept in the notifications
store beside the cards, and honoured by the tray, the list and voice. A later
card for a dismissed job stays dismissed unless it is louder (a failure after
progress). Cards group by job, then kind, then source; memory proposals are one
grouped card kept or skipped item by item; clearing a group never clears a
pending approval.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import pytest

from agent_friday import notifications_engine as ne
from agent_friday.services import notification_policy as pol

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clean(friday_dir, monkeypatch):
    if ne.NOTIF_FILE.exists():
        ne.NOTIF_FILE.unlink()
    monkeypatch.setattr(pol, "_LAST_TURN", [0.0])          # nobody mid-conversation
    monkeypatch.setattr(ne, "_log_only", lambda entry: None)
    getattr(ne, "_OFF_RECORD_LEDGER", {}).clear()
    yield
    if ne.NOTIF_FILE.exists():
        ne.NOTIF_FILE.unlink()


def _cards():
    return ne.list_notifications(limit=500)


def _restart():
    """What a restart leaves: the store on disk and nothing in memory. The
    dismissed cards themselves are gone too (the queue is capped and
    clear_dismissed drops them); only the store's dismissal record remains."""
    ne._OFF_RECORD_ITEMS.clear()
    ne.clear_dismissed()


def _fail(sid="sch_afternoon_briefing", body="no seat"):
    return ne.push(title="Afternoon Briefing failed", body=body, source="scheduler",
                   kind="scheduled_failure", priority="high",
                   dedupe_key="sched-fail:" + sid, resolve_key="sched-fail:" + sid)


# ── dismissal that sticks ────────────────────────────────────────────────────

def test_a_dismissed_jobs_later_same_tier_card_stays_dismissed_after_a_restart():
    first = _fail()
    assert ne.dismiss(first["id"]) is True
    _restart()
    stored = json.loads(ne.NOTIF_FILE.read_text(encoding="utf-8"))
    assert "sched:sch_afternoon_briefing" in stored["dismissed_jobs"], stored
    again = _fail(body="still no seat")
    assert again.get("suppressed") is True, again
    assert _cards() == [], "a dismissed job's card came back after a restart"
    assert ne.unread_count() == 0
    from agent_friday.services import notification_tools as nt
    assert nt.handle({"action": "summary"}) == "Your notifications are clear."


def test_a_higher_tier_card_for_a_dismissed_job_resurfaces():
    run = "sch_news_morning:2026-10-02"
    started = ne.run_card(run, "started", title="Front Page", source="scheduler")
    ne.dismiss(started["id"])
    _restart()
    ne.run_card(run, "done", title="Front Page", body="ready", source="scheduler")
    assert _cards() == [], "progress on a dismissed run came back"
    ne.run_card(run, "failed", title="Front Page failed", body="no seat", source="scheduler")
    cards = _cards()
    assert len(cards) == 1 and cards[0]["tier"] == pol.NEEDS_YOU, cards
    assert ne.unread_count() == 1


def test_dismissing_one_card_closes_its_jobs_quieter_cards():
    ne.push(title="Afternoon Briefing ran", source="scheduler", kind="scheduled_task",
            priority="low", dedupe_key="sched-ok:sch_afternoon_briefing")
    fail = _fail()
    ne.dismiss(fail["id"])
    assert _cards() == [], "the job's other card stayed after the job was dismissed"


def test_a_dismissed_failure_is_news_again_once_the_job_has_recovered():
    ne.dismiss(_fail()["id"])
    assert _fail().get("suppressed") is True
    ne.resolve("sched-fail:sch_afternoon_briefing")       # the next run succeeded
    ok = ne.push(title="Afternoon Briefing ran", source="scheduler", kind="scheduled_task",
                 priority="low", dedupe_key="sched-ok:sch_afternoon_briefing")
    assert ok.get("suppressed") is True, "a routine success of a dismissed job came back"
    assert len(_cards()) == 0
    _fail(body="failed again tomorrow")
    assert [c["kind"] for c in _cards()] == ["scheduled_failure"]


def test_clear_all_dismissals_stick_too():
    _fail()
    assert ne.dismiss_all() == 1
    _restart()
    assert _fail().get("suppressed") is True and _cards() == []


def test_an_off_record_card_writes_nothing_of_its_content_to_the_store(monkeypatch):
    """Off the record, the store keeps a stub with no title, body, actions,
    target, meta, or any key built from them (grouping, dedupe, resolve, job),
    and a dismissal is remembered in memory only."""
    from agent_friday.services import off_record
    monkeypatch.setattr(off_record, "skip", lambda *a, **k: True)
    # Letters only, so folding digits and lower-casing cannot disguise it.
    word = "".join(chr(97 + int(c, 16)) for c in uuid.uuid4().hex)
    card = ne.push(title="Lunch with " + word, body="about " + word, source="mail",
                   kind="info", priority="low", actions=[{"label": word}],
                   target={"url": "https://example.test/" + word}, meta={"note": word})
    ne.push(title="Lunch with " + word, body="again", source="mail", kind="info",
            priority="low", resolve_key="done:" + word)
    ne.push(title="Approval needed: " + word, source="approvals", kind="approval_pending")
    assert ne.dismiss(card["id"]) is True
    raw = ne.NOTIF_FILE.read_bytes()
    assert word.encode("utf-8") not in raw, "off-the-record text reached the store"
    assert json.loads(raw.decode("utf-8"))["dismissed_jobs"] == {}, (
        "an off-the-record dismissal was written to the store")
    # In this process the words and the dismissal still work.
    assert ne.push(title="Lunch with " + word, source="mail", kind="info",
                   priority="low").get("suppressed") is True
    assert any(word in c["title"] for c in _cards() if c["kind"] == "approval_pending")
    assert word.encode("utf-8") not in ne.NOTIF_FILE.read_bytes()


# ── grouping ─────────────────────────────────────────────────────────────────

def _memory_records(n):
    """Pending approvals that would save something into Friday's memory. The
    kind has no decision hook, so deciding one here executes nothing."""
    from agent_friday.services import approvals
    recs = []
    for i in range(n):
        recs.append(approvals.create_approval(
            kind="test_memory_proposal", subject_type="tool_action",
            subject_id="test-memory-proposal:%d:%s" % (i, uuid.uuid4().hex),
            title=pol.MEMORY_TITLE, action_description="learn_skill {'pattern': 'p%d'}" % i,
            force_gate=True, payload={"tool": "learn_skill", "input": {"pattern": "p%d" % i}},
            requested_by="test"))
    return recs


def test_memory_proposals_arrive_as_one_grouped_card_with_keep_or_skip_per_item():
    from agent_friday.services import approvals
    recs = _memory_records(5)
    assert all(r["status"] == "pending" for r in recs), recs
    ne.push(title="Daily creation ran", source="scheduler", kind="scheduled_task")
    groups = ne.groups()
    memory = [g for g in groups if g["memory"]]
    assert len(memory) == 1, groups
    card = memory[0]
    assert card["key"] == pol.MEMORY_GROUP and card["tier"] == pol.NEEDS_YOU
    assert len(card["items"]) == 5 and card["approvals"] == 5
    ids = {i["meta"]["approval_id"] for i in card["items"]}
    assert ids == {r["approval_id"] for r in recs}, "each item must be its own approval"
    # Keep one, skip one: each decides only its own proposal.
    approvals.decide(recs[0]["approval_id"], "approve")
    approvals.decide(recs[1]["approval_id"], "deny")
    card = [g for g in ne.groups() if g["memory"]][0]
    left = {i["meta"]["approval_id"] for i in card["items"]}
    assert left == {r["approval_id"] for r in recs[2:]}, left
    assert approvals.get_approval(recs[2]["approval_id"])["status"] == "pending"


def test_clear_group_never_clears_a_pending_approval():
    _memory_records(3)
    assert ne.dismiss_group(pol.MEMORY_GROUP) == 0
    assert len([g for g in ne.groups() if g["memory"]][0]["items"]) == 3
    # An approval inside a job's group stays when the group is cleared.
    _fail()
    ne.push(title="Approval needed: retry the briefing on a cloud model", source="approvals",
            kind="approval_pending", dedupe_key="appr:retry",
            meta={"group": "job:sched:sch_afternoon_briefing"})
    assert ne.dismiss_group("job:sched:sch_afternoon_briefing") == 1
    kinds = sorted(c["kind"] for c in _cards())
    assert kinds.count("approval_pending") == 4 and "scheduled_failure" not in kinds, kinds


def test_cards_group_by_job_then_kind_and_source():
    _fail()
    ne.push(title="Afternoon Briefing is waiting for the local seat", source="scheduler",
            kind="scheduled_task", priority="low", dedupe_key="sched-wait:sch_afternoon_briefing")
    ne.push(title="Back online soon", source="mail", kind="info", priority="low")
    ne.push(title="Mail synced 3 threads", source="mail", kind="info", priority="low")
    keys = {g["key"]: len(g["items"]) for g in ne.groups()}
    assert keys.get("job:sched:sch_afternoon_briefing") == 2, keys
    assert keys.get("kind:info|mail") == 2, keys


def test_voice_reads_and_clears_a_group():
    from agent_friday.services import notification_tools as nt
    _memory_records(2)
    _fail()
    said = nt.handle({"action": "read_group", "group": "memory"})
    assert "memory proposal" in said and "keep or skip" in said, said
    said = nt.handle({"action": "clear_group", "group": "memory"})
    assert "Cleared 0" in said and "2 approvals" in said, said
    said = nt.handle({"action": "clear_group", "group": "job:sched:sch_afternoon_briefing"})
    assert "Cleared 1" in said, said
    assert [c["kind"] for c in _cards()] == ["approval_pending", "approval_pending"]


@pytest.mark.parametrize("ui", ["index.html", "ui_parts/app.html"])
def test_both_ui_files_have_the_grouping_ui(ui):
    text = (REPO / ui).read_text(encoding="utf-8")
    flat = re.sub(r"\s+", "", text)
    assert "notif-group" in text and "notifGroupCards(notifs)" in flat, f"{ui}: no group cards"
    assert "'memory_proposals'" in text, f"{ui}: memory proposals are not one group"
    assert "decideMemoryItem(n,true)" in flat and "decideMemoryItem(n,false)" in flat, (
        f"{ui}: no keep/skip per memory proposal")
    assert '"Keep"' in text and '"Skip"' in text, f"{ui}: keep/skip buttons missing"
    assert "/api/notifications/clear-group" in text and '"Clear group"' in text, (
        f"{ui}: no Clear group")
    assert "n.group!==g.key||n.kind==='approval_pending'" in flat, (
        f"{ui}: Clear group drops pending approvals from the panel")
    assert "filter(n=>!notifGroupedIds(notifs).has(n.id))" in flat, (
        f"{ui}: grouped cards are also shown one by one")
