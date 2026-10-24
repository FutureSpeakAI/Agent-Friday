"""The phone list in Google Tasks, Part 1 (services/tasks_inbox): a tick approves NOTHING.

Spec: ftv/program/specs/google_tasks_approval_inbox_STORM_2026-10-23.md (GT0 + GT1, owner
decisions of 2026-10-23). What these tests pin:

* a tick in Google Tasks brings a card to the desk and never approves it;
* deleting an item, or ticking its "No, not now", declines only the kinds of card where no
  means "don't do it"; a burst of changes declines nothing;
* only items Friday wrote count; an item changed outside Friday is void and replaced;
* Friday's own writes are never mistaken for a tick;
* what leaves the PC is built by code at the level the owner chose, passes the egress gate and
  the checkpoint first, and nothing is posted off the record;
* the record is signed and chained, and a broken record pauses the list.

Google is a fake (tests/tasks_inbox_support.py); nothing reaches the network. On a tree
without services/tasks_inbox each test fails by assertion ("there is no phone list").
"""
from __future__ import annotations

import importlib
import json
import time
from types import SimpleNamespace

import pytest

from tests.tasks_inbox_support import FakeTasks

ACCOUNT = {"id": "acct1", "email": "owner@example.com", "label": "Me"}


def _module():
    try:
        return importlib.import_module("agent_friday.services.tasks_inbox")
    except ImportError:
        pytest.fail("there is no phone list: services/tasks_inbox does not exist")


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    ti = _module()
    from agent_friday.services import approvals as ap
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(ap, "_fire_hook", lambda rec: None)
    monkeypatch.setattr(ap, "_feed", lambda *a, **k: None)
    monkeypatch.setattr(ap, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(ti, "_key", lambda: b"k" * 32)
    fake = FakeTasks()
    real_gate, real_checkpoint = ti._gate, ti._checkpoint
    monkeypatch.setattr(ti, "_service", lambda account_id: fake)
    monkeypatch.setattr(ti, "writable_accounts", lambda: [dict(ACCOUNT)])
    monkeypatch.setattr(ti, "_assistant", lambda: "Friday")
    gated, checks, notices = [], [], []
    monkeypatch.setattr(ti, "_gate", lambda text, field: (gated.append((field, text)) or text))
    monkeypatch.setattr(ti, "_checkpoint", lambda action, aid: (checks.append((action, aid)) or True))
    monkeypatch.setattr(ti, "_notify", lambda *a, **k: notices.append(a))
    from agent_friday.services import off_record, cloud_mode_policy
    monkeypatch.setattr(off_record, "active", lambda settings=None: False)
    monkeypatch.setattr(cloud_mode_policy, "is_local_mode", lambda mode=None: False)
    return SimpleNamespace(ti=ti, ap=ap, fake=fake, gated=gated, checks=checks, notices=notices,
                           home=tmp_path, real_gate=real_gate, real_checkpoint=real_checkpoint)


def _card(ap, sid="c1", kind="tool_confirm", task_id="", tool="send_email", to="dana@example.com"):
    return ap.create_approval(
        kind=kind, subject_type="inbox_test", subject_id=sid,
        title="Send the council summary to %s" % to,
        description="Email to %s: 'the budget line is £4.2m'" % to,
        action_description="%s %s" % (tool, json.dumps({"to": to})),
        force_gate=True, payload={"tool": tool, "input": {"to": to}, "task_id": task_id})


def _on(t, level="short"):
    out = t.ti.enable("acct1", level)
    assert out.get("ok"), out
    return out["config"]["list_id"]


def _sync(t, **kw):
    return t.ti.tick(force=True, **kw)


def _status(t, aid):
    return (t.ap.get_approval(aid) or {}).get("status")


def _item_for(t, list_id, needle=None):
    tops = t.fake.top_level(list_id)
    assert tops, "nothing was posted"
    return tops[0] if needle is None else next(x for x in tops if needle in x["title"])


# ── off, on, settings ────────────────────────────────────────────────────────

def test_off_by_default_a_sync_touches_nothing(inbox):
    _card(inbox.ap)
    out = _sync(inbox)
    assert out.get("skipped") == "off"
    assert inbox.fake.writes == []


def test_turning_it_on_makes_one_list_and_signs_the_settings(inbox):
    list_id = _on(inbox)
    assert list(inbox.fake.lists) == [list_id]
    assert inbox.fake.lists[list_id]["title"] == "Friday — awaiting you"
    cfg = inbox.ti.config()
    assert cfg["enabled"] and cfg["text_level"] == "short" and "problem" not in cfg
    _on(inbox)                                           # again: the same list, not a second one
    assert list(inbox.fake.lists) == [list_id]


def test_a_settings_file_changed_outside_friday_reads_as_off(inbox):
    _on(inbox)
    p = inbox.home / "tasks_inbox" / "config.json"
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw["text_level"] = "full"
    p.write_text(json.dumps(raw), encoding="utf-8")
    cfg = inbox.ti.config()
    assert cfg["enabled"] is False and "outside" in cfg["problem"]
    _card(inbox.ap)
    assert _sync(inbox).get("skipped") == "off"


def test_an_account_that_has_not_allowed_task_changes_is_refused(inbox, monkeypatch):
    monkeypatch.setattr(inbox.ti, "writable_accounts", lambda: [])
    out = inbox.ti.enable("acct1", "short")
    assert not out["ok"] and inbox.fake.lists == {}


def test_an_unknown_text_level_is_refused_and_nothing_is_pre_selected(inbox):
    assert not inbox.ti.enable("acct1", "")["ok"]
    assert inbox.fake.lists == {}
    v = inbox.ti.view()
    assert v["text_level"] is None and v["recommended_level"] == "short"


# ── what is posted ───────────────────────────────────────────────────────────

def test_a_waiting_card_is_posted_once_with_a_no_subtask_and_short_words(inbox):
    card = _card(inbox.ap)
    list_id = _on(inbox)
    _sync(inbox)
    _sync(inbox)
    tops = inbox.fake.top_level(list_id)
    assert len(tops) == 1
    item = tops[0]
    assert item["title"] == "Friday needs your yes for send_email"
    assert "approves nothing" in item["notes"]
    assert "dana@example.com" not in item["title"] + item["notes"]
    assert "4.2m" not in item["notes"]
    kids = inbox.fake.children(item["id"])
    assert [k["title"] for k in kids] == ["No, not now"]
    assert ("tasks_inbox_post", card["approval_id"]) in inbox.checks


def test_minimal_words_say_only_that_something_needs_you(inbox):
    _card(inbox.ap)
    list_id = _on(inbox, "minimal")
    _sync(inbox)
    item = _item_for(inbox, list_id)
    assert item["title"] == "Something needs you in Friday"
    assert "send_email" not in item["notes"]


def test_full_words_for_an_action_card_are_still_the_short_words(inbox):
    _card(inbox.ap)
    list_id = _on(inbox, "full")
    _sync(inbox)
    item = _item_for(inbox, list_id)
    assert item["title"] == "Friday needs your yes for send_email"
    assert "dana@example.com" not in item["title"] + item["notes"]


def test_full_words_never_carry_an_emails_recipient(inbox):
    inbox.ap.create_approval(kind="external_message", subject_type="email", subject_id="m1",
                             title="Send email to bob@example.org", description="To bob@example.org: the draft",
                             force_gate=True)
    list_id = _on(inbox, "full")
    _sync(inbox)
    item = _item_for(inbox, list_id)
    assert "bob@example.org" not in item["title"] + item["notes"]
    assert item["title"] == "Friday needs your decision: an email"


def test_full_words_carry_the_title_of_a_card_with_nothing_private_in_it(inbox):
    inbox.ap.create_approval(kind="scheduled_late_run", subject_type="schedule", subject_id="s1:9:0",
                             title="Morning news was due in quiet hours: run it now?", force_gate=True)
    list_id = _on(inbox, "full")
    _sync(inbox)
    assert _item_for(inbox, list_id)["title"] == "Morning news was due in quiet hours: run it now?"


def test_only_on_this_pc_posts_only_minimal(inbox, monkeypatch):
    from agent_friday.services import cloud_mode_policy
    monkeypatch.setattr(cloud_mode_policy, "is_local_mode", lambda mode=None: True)
    _card(inbox.ap)
    list_id = _on(inbox, "full")
    _sync(inbox)
    assert _item_for(inbox, list_id)["title"] == "Something needs you in Friday"


def test_nothing_is_posted_off_the_record(inbox, monkeypatch):
    list_id = _on(inbox)
    from agent_friday.services import off_record
    monkeypatch.setattr(off_record, "active", lambda settings=None: True)
    _card(inbox.ap)
    _sync(inbox)
    assert inbox.fake.items_in(list_id) == []


def test_every_word_passes_the_egress_gate_and_nothing_goes_when_it_withholds(inbox, monkeypatch):
    list_id = _on(inbox)
    from agent_friday.services import egress_gate
    monkeypatch.setattr(inbox.ti, "_gate", inbox.real_gate)
    seen = []

    def withhold(text, provider, field, log_path=None):
        seen.append((provider, field))
        return "[withheld]" if field == "tasks_inbox.notes" else text
    monkeypatch.setattr(egress_gate, "_gate_text", withhold)
    _card(inbox.ap)
    _sync(inbox)
    assert ("google", "tasks_inbox.title") in seen and ("google", "tasks_inbox.notes") in seen
    assert inbox.fake.items_in(list_id) == []


def test_a_gate_that_cannot_run_posts_nothing(inbox, monkeypatch):
    list_id = _on(inbox)
    from agent_friday.services import egress_gate
    monkeypatch.setattr(inbox.ti, "_gate", inbox.real_gate)

    def broken(text, provider, field, log_path=None):
        raise RuntimeError("classifier down")
    monkeypatch.setattr(egress_gate, "_gate_text", broken)
    _card(inbox.ap)
    _sync(inbox)
    assert inbox.fake.items_in(list_id) == []


def test_when_the_checkpoint_holds_nothing_leaves(inbox, monkeypatch):
    list_id = _on(inbox)
    monkeypatch.setattr(inbox.ti, "_checkpoint", lambda action, aid: False)
    _card(inbox.ap)
    _sync(inbox)
    assert inbox.fake.items_in(list_id) == []


def test_the_checkpoint_is_the_action_gates_signed_receipt(inbox, monkeypatch):
    from agent_friday.governance import action_gate
    calls = []

    def held(action, *, surface, approval_id=None, target=""):
        calls.append((action, surface, target))
        raise action_gate.Held("the cLaws integrity check failed (test)")
    monkeypatch.setattr(action_gate, "record_external", held)
    assert inbox.real_checkpoint("tasks_inbox_post", "appr_x") is False
    assert calls == [("tasks_inbox_post", "tasks_inbox", "google_tasks:appr_x")]


def test_website_plans_and_app_requests_are_never_posted(inbox):
    inbox.ap.create_approval(kind="web_task_plan", subject_type="web_task", subject_id="w1",
                             title="Change my address on the grocery site", force_gate=True)
    inbox.ap.create_approval(kind="tool_confirm", subject_type="inbox_test", subject_id="c9",
                             title="Asked by an app", force_gate=True,
                             payload={"tool": "send_email", "origin": "caller:claude"})
    list_id = _on(inbox)
    _sync(inbox)
    assert inbox.fake.items_in(list_id) == []


def test_at_most_thirty_posts_a_day(inbox):
    for i in range(32):
        _card(inbox.ap, sid="c%d" % i, kind="governed_action", tool="")
    list_id = _on(inbox)
    _sync(inbox)
    _sync(inbox)
    tops = inbox.fake.top_level(list_id)
    assert len(tops) == 30


def test_a_task_with_two_cards_is_one_parent_with_both_as_subtasks(inbox):
    a = _card(inbox.ap, sid="a", task_id="task_7")
    b = _card(inbox.ap, sid="b", task_id="task_7", tool="create_event")
    list_id = _on(inbox)
    _sync(inbox)
    tops = inbox.fake.top_level(list_id)
    assert len(tops) == 1 and "waiting for you" in tops[0]["title"]
    kids = [k["title"] for k in inbox.fake.children(tops[0]["id"])]
    assert sorted(kids) == sorted(["Friday needs your yes for send_email",
                                   "Friday needs your yes for create_event", "No, not now"])
    assert _status(inbox, a["approval_id"]) == _status(inbox, b["approval_id"]) == "pending"


# ── a tick approves nothing ──────────────────────────────────────────────────

def test_a_tick_brings_the_card_to_the_desk_and_approves_nothing(inbox):
    card = _card(inbox.ap)
    list_id = _on(inbox)
    _sync(inbox)
    inbox.fake.phone_tick(_item_for(inbox, list_id)["id"])
    _sync(inbox)
    rec = inbox.ap.get_approval(card["approval_id"])
    assert rec["status"] == "pending"
    assert "can't tell who" in rec["phone_tick"]["words"]
    assert inbox.notices and inbox.notices[-1][0] == "TI1"
    assert "can't tell who" in inbox.notices[-1][3]


def test_a_tick_on_the_parent_brings_every_card_under_it_and_approves_none(inbox):
    a = _card(inbox.ap, sid="a", task_id="task_7")
    b = _card(inbox.ap, sid="b", task_id="task_7", tool="create_event")
    list_id = _on(inbox)
    _sync(inbox)
    inbox.fake.phone_tick(inbox.fake.top_level(list_id)[0]["id"])
    _sync(inbox)
    for c in (a, b):
        rec = inbox.ap.get_approval(c["approval_id"])
        assert rec["status"] == "pending" and rec.get("phone_tick")


def test_no_tick_of_any_kind_can_approve_a_card(inbox):
    card = _card(inbox.ap)
    for source in ("", "typed", "voice", "text_message"):
        rec, won = inbox.ap.decide_with_outcome(card["approval_id"], "approve",
                                                decided_by=inbox.ti.TICK_LABEL, source=source)
        assert not won and rec["status"] == "pending"


# ── declining from the phone ─────────────────────────────────────────────────

def test_deleting_an_item_declines_a_dont_do_it_card_and_says_who_cannot_be_known(inbox):
    card = _card(inbox.ap)
    list_id = _on(inbox)
    _sync(inbox)
    inbox.fake.phone_delete(_item_for(inbox, list_id)["id"])
    _sync(inbox)
    rec = inbox.ap.get_approval(card["approval_id"])
    assert rec["status"] == "denied"
    assert rec["decided_by"] == "remote:tasks_tick" and rec["decided_source"] == "google_tasks_tick"
    assert "can't tell who" in rec["decision_note"]


def test_ticking_no_not_now_declines(inbox):
    card = _card(inbox.ap)
    list_id = _on(inbox)
    _sync(inbox)
    no = inbox.fake.children(_item_for(inbox, list_id)["id"])[0]
    inbox.fake.phone_tick(no["id"])
    _sync(inbox)
    assert _status(inbox, card["approval_id"]) == "denied"


@pytest.mark.parametrize("kind", ["library_add_request", "file_grant_request", "goal_approval"])
def test_a_card_whose_no_is_not_just_dont_do_it_stays_on_the_pc(inbox, kind):
    card = inbox.ap.create_approval(kind=kind, subject_type="inbox_test", subject_id="k1",
                                    title="A decision", force_gate=True)
    list_id = _on(inbox)
    _sync(inbox)
    item = _item_for(inbox, list_id)
    assert inbox.fake.children(item["id"]) == []         # no "No, not now" offered
    assert "Decide it on your PC" in item["notes"]
    inbox.fake.phone_delete(item["id"])
    _sync(inbox)
    assert _status(inbox, card["approval_id"]) == "pending"


def test_deleting_a_parent_declines_its_cards_as_one_change(inbox):
    a = _card(inbox.ap, sid="a", task_id="task_7")
    b = _card(inbox.ap, sid="b", task_id="task_7", tool="create_event")
    c = _card(inbox.ap, sid="c", task_id="task_7", tool="update_event")
    list_id = _on(inbox)
    _sync(inbox)
    inbox.fake.phone_delete(inbox.fake.top_level(list_id)[0]["id"])
    _sync(inbox)
    assert [_status(inbox, x["approval_id"]) for x in (a, b, c)] == ["denied"] * 3


def test_a_burst_of_deletes_declines_nothing(inbox):
    cards = [_card(inbox.ap, sid="c%d" % i) for i in range(4)]
    list_id = _on(inbox)
    _sync(inbox)
    at = time.time()
    for k, item in enumerate(inbox.fake.top_level(list_id)):
        inbox.fake.phone_delete(item["id"], at=at + k)
    out = _sync(inbox)
    assert out["held"] >= 1
    assert all(_status(inbox, c["approval_id"]) == "pending" for c in cards)


def test_deletes_spaced_apart_each_decline(inbox):
    cards = [_card(inbox.ap, sid="c%d" % i) for i in range(4)]
    list_id = _on(inbox)
    _sync(inbox)
    at = time.time()
    for k, item in enumerate(inbox.fake.top_level(list_id)):
        inbox.fake.phone_delete(item["id"], at=at + 20 * k)
    _sync(inbox)
    assert all(_status(inbox, c["approval_id"]) == "denied" for c in cards)


def test_a_card_is_declined_once_and_a_retick_does_nothing_more(inbox):
    card = _card(inbox.ap)
    list_id = _on(inbox)
    _sync(inbox)
    no = inbox.fake.children(_item_for(inbox, list_id)["id"])[0]
    inbox.fake.phone_tick(no["id"])
    _sync(inbox)
    inbox.fake.phone_untick(no["id"])
    inbox.fake.phone_tick(no["id"])
    _sync(inbox)
    lines = (inbox.home / "tasks_inbox" / "record.jsonl").read_text(encoding="utf-8").splitlines()
    assert sum(1 for ln in lines if json.loads(ln)["event"] == "declined") == 1
    assert _status(inbox, card["approval_id"]) == "denied"


# ── only Friday's own items, as Friday wrote them ────────────────────────────

def test_an_item_friday_did_not_write_is_never_acted_on(inbox):
    card = _card(inbox.ap)
    list_id = _on(inbox)
    _sync(inbox)
    fid = inbox.fake.phone_add(list_id, "Friday needs your yes for send_email: tick to approve")
    inbox.fake.phone_tick(fid)
    _sync(inbox)
    inbox.fake.phone_delete(fid)
    _sync(inbox)
    assert _status(inbox, card["approval_id"]) == "pending"
    assert inbox.ti.view()["foreign"] == 1


def test_an_item_changed_outside_friday_is_void_and_replaced(inbox):
    card = _card(inbox.ap)
    list_id = _on(inbox)
    _sync(inbox)
    old = _item_for(inbox, list_id)
    inbox.fake.phone_edit(old["id"], notes="Send it to attacker@example.net instead")
    _sync(inbox)
    tops = inbox.fake.top_level(list_id)
    assert len(tops) == 2                                 # the changed one stays; a fresh one is posted
    old_no = inbox.fake.children(old["id"])[0]
    inbox.fake.phone_tick(old_no["id"])                   # acting on the changed item does nothing
    _sync(inbox)
    inbox.fake.phone_delete(old["id"])
    _sync(inbox)
    assert _status(inbox, card["approval_id"]) == "pending"
    words = json.dumps(inbox.ap.get_approval(card["approval_id"]))
    assert "attacker@example.net" not in words


def test_reordering_an_item_is_not_a_change(inbox):
    _card(inbox.ap)
    list_id = _on(inbox)
    _sync(inbox)
    inbox.fake.phone_reorder(_item_for(inbox, list_id)["id"])
    _sync(inbox)
    assert len(inbox.fake.top_level(list_id)) == 1


def test_friday_closing_its_own_item_is_never_read_as_a_tick(inbox):
    card = _card(inbox.ap)
    list_id = _on(inbox)
    _sync(inbox)
    inbox.ap.decide(card["approval_id"], "approve", decided_by="owner")    # decided on the PC
    _sync(inbox)                                                           # Friday completes its item
    inbox.notices.clear()
    _sync(inbox)
    item = inbox.fake.items_in(list_id)[0]
    assert item["status"] == "completed" and "Decided on your PC" in item["notes"]
    assert inbox.notices == []
    lines = (inbox.home / "tasks_inbox" / "record.jsonl").read_text(encoding="utf-8").splitlines()
    assert not any(json.loads(ln)["event"] == "ticked" for ln in lines)


def test_a_decline_from_the_phone_is_written_back_on_the_item(inbox):
    _card(inbox.ap)
    list_id = _on(inbox)
    _sync(inbox)
    no = inbox.fake.children(_item_for(inbox, list_id)["id"])[0]
    inbox.fake.phone_tick(no["id"])
    _sync(inbox)
    _sync(inbox)
    item = _item_for(inbox, list_id)
    assert item["status"] == "completed" and "Declined from Google Tasks" in item["notes"]


# ── the record ───────────────────────────────────────────────────────────────

def test_a_record_changed_outside_friday_pauses_the_list(inbox):
    list_id = _on(inbox)
    _card(inbox.ap)
    _sync(inbox)
    p = inbox.home / "tasks_inbox" / "record.jsonl"
    lines = p.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["list_id"] = "someone-elses-list"
    lines[0] = json.dumps(first, sort_keys=True)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _card(inbox.ap, sid="c2")
    out = _sync(inbox)
    assert out.get("skipped") == "record"
    assert len(inbox.fake.top_level(list_id)) == 1


def test_tidy_up_deletes_only_friday_items_a_week_after_they_end(inbox):
    card = _card(inbox.ap)
    list_id = _on(inbox)
    _sync(inbox)
    fid = inbox.fake.phone_add(list_id, "Buy milk")
    inbox.ap.decide(card["approval_id"], "deny", decided_by="owner")
    _sync(inbox)
    _sync(inbox, now=time.time() + 8 * 86400)
    left = inbox.fake.items_in(list_id)
    assert [t["id"] for t in left] == [fid]


def test_turning_it_off_keeps_the_fence_and_removing_the_list_deletes_only_that_list(inbox):
    list_id = _on(inbox)
    other = inbox.fake.tasklists().insert(body={"title": "Groceries"}).execute()["id"]
    inbox.ti.disable(remove_list=False)
    assert inbox.ti.is_inbox_list("acct1", list_id)
    _on(inbox)
    inbox.ti.disable(remove_list=True)
    assert list(inbox.fake.lists) == [other]
    assert inbox.ti.is_inbox_list("acct1", list_id)


def test_the_view_says_what_a_tick_does_and_the_honest_limit(inbox):
    v = inbox.ti.view()
    joined = " ".join(v["what_a_tick_does"])
    assert "Nothing is approved" in joined
    assert "approves nothing" in v["honest_limit"]
    assert "token" not in json.dumps(v).lower()


# ── review round 1 ───────────────────────────────────────────────────────────

def test_exactly_three_deletes_at_once_is_not_a_burst(inbox):
    cards = [_card(inbox.ap, sid="c%d" % i) for i in range(3)]
    list_id = _on(inbox)
    _sync(inbox)
    at = time.time()
    for item in inbox.fake.top_level(list_id):
        inbox.fake.phone_delete(item["id"], at=at)
    _sync(inbox)
    assert all(_status(inbox, c["approval_id"]) == "denied" for c in cards)


def test_a_burst_of_deletes_brings_the_cards_to_the_desk(inbox):
    cards = [_card(inbox.ap, sid="c%d" % i) for i in range(4)]
    list_id = _on(inbox)
    _sync(inbox)
    at = time.time()
    for item in inbox.fake.top_level(list_id):
        inbox.fake.phone_delete(item["id"], at=at)
    _sync(inbox)
    for c in cards:
        rec = inbox.ap.get_approval(c["approval_id"])
        assert rec["status"] == "pending" and rec.get("phone_tick")


def test_a_card_deleted_from_the_phone_that_stays_on_the_pc_is_not_posted_again(inbox):
    inbox.ap.create_approval(kind="library_add_request", subject_type="inbox_test", subject_id="k1",
                             title="A decision", force_gate=True)
    list_id = _on(inbox)
    _sync(inbox)
    inbox.fake.phone_delete(_item_for(inbox, list_id)["id"])
    _sync(inbox)
    _sync(inbox)
    assert inbox.fake.items_in(list_id) == []


def test_a_group_that_ended_is_not_used_for_a_new_card(inbox):
    a = _card(inbox.ap, sid="a", task_id="task_7")
    b = _card(inbox.ap, sid="b", task_id="task_7", tool="create_event")
    list_id = _on(inbox)
    _sync(inbox)
    inbox.ap.decide(a["approval_id"], "deny", decided_by="owner")
    inbox.ap.decide(b["approval_id"], "deny", decided_by="owner")
    _sync(inbox)
    old_group = [t for t in inbox.fake.top_level(list_id) if "waiting for you" in t["title"]][0]
    assert old_group["status"] == "completed"
    _card(inbox.ap, sid="c", task_id="task_7", tool="update_event")
    _card(inbox.ap, sid="d", task_id="task_7", tool="delete_event")
    _sync(inbox)
    open_groups = [t for t in inbox.fake.top_level(list_id)
                   if "waiting for you" in t["title"] and t["status"] == "needsAction"]
    assert len(open_groups) == 1 and open_groups[0]["id"] != old_group["id"]


def test_a_garbage_last_line_is_cut_and_the_list_keeps_working(inbox):
    list_id = _on(inbox)
    _card(inbox.ap)
    _sync(inbox)
    p = inbox.home / "tasks_inbox" / "record.jsonl"
    with open(p, "ab") as fh:
        fh.write(b'{"not": "a record line"\n')
    _card(inbox.ap, sid="c2", tool="create_event")
    out = _sync(inbox)
    assert out.get("skipped") is None, out
    assert len(inbox.fake.top_level(list_id)) == 2
    _lines, ok = inbox.ti._read_record()
    assert ok


def test_a_last_line_that_lost_only_its_newline_is_kept(inbox):
    _on(inbox)
    _card(inbox.ap)
    _sync(inbox)
    p = inbox.home / "tasks_inbox" / "record.jsonl"
    data = p.read_bytes()
    before = len(data.splitlines())
    p.write_bytes(data.rstrip(b"\n"))
    inbox.ti._append("untick", item_id="none")
    lines, ok = inbox.ti._read_record()
    assert ok and len(lines) == before + 1
