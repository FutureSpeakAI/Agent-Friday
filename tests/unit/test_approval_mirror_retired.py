"""Approval cards are not mirrored to Google Tasks, and nothing read back from there becomes a card or a notice.

The waiting list in Google Tasks (services/tasks_inbox) posted one item per waiting card to the owner's Google
account and, when it saw a tick on one, raised a "Brought to your desk from Google Tasks" notice. That notice was filed
as "for your information" although it stands for an approval, and it never closed when its card was decided, so a
decided card kept a live-looking Review notice. Approvals live in Friday's tray only, reached by phone, tablet and
widget through the gateway. A machine whose settings still say "on" posts nothing, reads nothing, and clears what the
old mirror left behind. Synthetic data only; Google is never reached.
"""
from __future__ import annotations

import pytest

from agent_friday import notifications_engine as ne
from agent_friday.services import approvals as ap
from agent_friday.services import notification_policy as pol
from agent_friday.services import tasks_inbox as ti


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(ap, "_fire_hook", lambda rec: None)
    # Pages are not told (no SSE), but approvals._feed itself runs: it is what closes a decided card's notices.
    from agent_friday.services import approval_feed
    monkeypatch.setattr(approval_feed, "publish", lambda *a, **k: None)
    monkeypatch.setattr(ap, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(ti, "_key", lambda: b"k" * 32)
    monkeypatch.setattr(ti, "_assistant", lambda: "Friday")
    monkeypatch.setattr(ne, "_notify_toast", lambda *a, **k: None, raising=False)
    yield


@pytest.fixture
def google(monkeypatch):
    """Every attempt to reach Google through the mirror."""
    reached = []
    monkeypatch.setattr(ti, "_service", lambda account_id: reached.append(account_id))
    return reached


def _card(sid="c1"):
    return ap.create_approval(
        kind="tool_confirm", subject_type="mirror_test", subject_id=sid, title="Synthetic action",
        description="Synthetic", action_description="update_core_memory {}", force_gate=True,
        payload={"tool": "update_core_memory", "input": {}, "task_id": ""})


def _already_on():
    """The settings an earlier build wrote: on, for one account, with a list."""
    ti._write_config({"enabled": True, "account_id": "acct1", "list_id": "list1", "list_title": "Friday - awaiting you",
                      "text_level": "short", "enabled_at": 1.0, "instance": "i1"})
    assert ti.config().get("enabled"), "the old settings were not read back"


def _legacy_notice(approval_id):
    """A notice as an older build stored it. Nothing can push one any more (its emitter is retired), so it is written
    into the store the way that build left it, not through push (whose routing for an unknown kind follows policy)."""
    import uuid
    with ne._LOCK:
        items = ne._load()
        items.append({"id": uuid.uuid4().hex[:12], "title": "Brought to your desk from Google Tasks: Friday needs your "
                      "approval for an add-on", "body": "A tick was seen in Google Tasks at 23:33.",
                      "priority": "high", "source": "tasks_inbox", "kind": "tasks_inbox_tick", "emitter": "TI1",
                      "tier": "needs_you", "place": "needs_you", "dedupe_key": "tasks-inbox-tick:%s" % approval_id,
                      "dismissed": False, "read": False, "created_at": ne._now_iso()})
        ne._save(items)


def _live(kind="tasks_inbox_tick"):
    return [n for n in ne.list_notifications(limit=200) if n.get("kind") == kind and not n.get("dismissed")]


def test_a_machine_that_had_the_mirror_on_posts_nothing_and_reads_nothing(google):
    _already_on()
    _card()
    out = ti.tick(force=True)
    assert out.get("skipped") == "retired", out
    assert google == [], "the mirror reached Google"


def test_the_mirror_cannot_be_turned_on_again(google):
    out = ti.enable("acct1", "short")
    assert out.get("ok") is False and "no longer" in out.get("message", "").lower(), out
    assert not ti.config().get("enabled")
    assert google == []


def test_a_tick_that_was_seen_is_never_turned_into_a_notice_a_card_or_a_decision(google):
    _already_on()
    card = _card()
    ti.tick(force=True)
    assert _live() == [] and _live("tasks_inbox_change") == []
    assert ap.get_approval(card["approval_id"])["status"] == "pending"
    assert [c["approval_id"] for c in ap.list_approvals(status="pending")] == [card["approval_id"]]


def test_notices_the_old_mirror_left_behind_are_cleared(google):
    _already_on()
    card = _card()
    _legacy_notice(card["approval_id"])
    assert _live(), "the legacy notice was not made"
    ti.tick(force=True)
    assert _live() == [], "a notice from the old mirror is still showing"


def test_deciding_a_card_closes_every_notice_that_names_it():
    card = _card()
    _legacy_notice(card["approval_id"])
    ap.decide_with_outcome(card["approval_id"], "deny", decided_by="owner:ui", source="desktop_press")
    assert _live() == [], "a decided card kept a live Review notice"


def test_an_approval_is_never_for_your_information():
    assert pol.tier_for("approval_pending", "approvals", "low", tier="fyi") == pol.NEEDS_YOU
    from agent_friday.services import notice_registry as nr
    # The old mirror's notices (an approval brought "to your desk", filed as FYI) are gone from the registry.
    assert [e.id for e in nr.REGISTRY.values() if e.source == "tasks_inbox"] == []
    wrong = [e for e in nr.REGISTRY.values() if e.kind == "approval_pending"
             and pol.tier_for(e.kind, e.source, "normal") != pol.NEEDS_YOU]
    assert wrong == [], "an approval card's own notice is filed as for your information: %s" % [e.id for e in wrong]


def test_no_part_of_the_mirror_still_posts_an_item_or_a_notice():
    assert not hasattr(ti, "_bring_to_desk") and not hasattr(ti, "_post_one") and not hasattr(ti, "_post_group")
    assert set(ti.RETIRED_NOTICE_KINDS) == {"tasks_inbox_tick", "tasks_inbox_change"}


def test_the_settings_section_says_it_is_retired_and_offers_only_turning_it_off():
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    for f in ("index.html", "ui_parts/app.html"):
        text = (repo / f).read_text(encoding="utf-8")
        at = text.index("function TasksInboxPanel(")
        panel = text[at:text.index("\nfunction ", at + 10)]
        early = panel[:panel.index("const levelButtons")]
        assert "if (d.retired)" in early, f + ": the section still offers to turn the mirror on"
        assert "no longer mirrors approvals" in early and "/api/tasks-inbox/disable" in early, f
