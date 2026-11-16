"""When a standing grant may be offered, and for what (Compressor eval K5, PR A). Synthetic cards only."""
from __future__ import annotations

import itertools

import pytest

from agent_friday.governance import action_gate
from agent_friday.services import action_pattern as ap
from agent_friday.services import approval_novelty as an
from agent_friday.services import approvals, standing_offers as so

DAY = 86400.0
NEXT = itertools.count()


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(approvals, "_HOOKS", {})
    monkeypatch.setattr(an, "_DIR", tmp_path)
    monkeypatch.setattr(an, "COUNTS_FILE", tmp_path / "counts.json")
    monkeypatch.setattr(an, "LABELS_FILE", tmp_path / "labels.jsonl")
    monkeypatch.setattr(so, "STATE_FILE", tmp_path / "offers.json")
    monkeypatch.setattr(approvals, "_offer_standing_grant", lambda card: None)
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")


def _decide(tool="create_task", decision="approve", task_id="", **tool_input):
    rec = approvals.create_approval(
        kind="tool_confirm", subject_type="tool_action", subject_id=f"s{next(NEXT)}",
        title="t", action_description="run it", force_gate=True, requested_by="confirmation_gate",
        payload={"tool": tool, "task_id": task_id, "input": tool_input or {"list": "home"}})
    return approvals.decide(rec["approval_id"], decision)


def _approve_n(n, **kw):
    last = None
    for _ in range(n):
        last = _decide(**kw)
    return last


def test_n_minus_one_approvals_raise_no_offer_and_the_nth_raises_one():
    for _ in range(so.OFFER_AFTER_APPROVALS - 1):
        assert so.offer_due(_decide()) is None
    assert so.offer_due(_decide()) is not None


def test_a_thousand_approvals_without_a_press_change_no_card():
    _approve_n(1000)
    rec = approvals.create_approval(
        kind="tool_confirm", subject_type="tool_action", subject_id="next", title="t",
        action_description="run it", force_gate=True,
        payload={"tool": "create_task", "input": {"list": "home"}})
    assert rec["status"] == "pending" and rec["gated"] is True


def test_a_denial_resets_the_count_and_holds_offers_for_thirty_days():
    _approve_n(so.OFFER_AFTER_APPROVALS - 1)
    _decide(decision="deny")
    last = _approve_n(so.OFFER_AFTER_APPROVALS)
    assert so.offer_due(last) is None
    assert so.offer_due(last, now=last["decided_at"] + 31 * DAY) is None, "outside the window the count is empty"


def test_a_declined_offer_waits_thirty_days_and_never_again_holds_for_good():
    last = _approve_n(so.OFFER_AFTER_APPROVALS)
    fingerprint = so.offer_due(last).fingerprint()
    so.record_offer_raised(fingerprint, now=last["decided_at"])
    so.record_offer_closed(fingerprint, declined=True, now=last["decided_at"])
    again = _decide()
    assert so.offer_due(again, now=again["decided_at"] + 2 * DAY) is None
    so.record_offer_closed(fingerprint, declined=False, never_again=True)
    assert so.offer_due(_decide(), now=again["decided_at"] + 40 * DAY) is None


def test_one_offer_pending_and_one_a_day():
    last = _approve_n(so.OFFER_AFTER_APPROVALS)
    first = so.offer_due(last).fingerprint()
    so.record_offer_raised(first, now=last["decided_at"])
    assert so.offer_due(_approve_n(so.OFFER_AFTER_APPROVALS, tool="update_task", id="x")) is None, "pending"
    so.record_offer_closed(first, declined=False)
    other = _approve_n(so.OFFER_AFTER_APPROVALS, tool="complete_task", id="y")
    assert so.offer_due(other, now=other["decided_at"] + 60) is None, "one new offer a day"
    assert so.offer_due(other, now=other["decided_at"] + 2 * DAY) is not None


def test_only_a_seen_instance_is_offered():
    _approve_n(so.OFFER_AFTER_APPROVALS - 1)
    _decide(tool="update_task", task_id="T1", id="x")
    after_another = _decide(task_id="T1")
    assert so.approval_novelty.decided_rows()[-1]["label"] == "new_sequence"
    assert so.offer_due(after_another) is None
    assert so.offer_due(_decide()) is not None


def test_an_excluded_action_is_never_offered_however_often_it_is_approved():
    for tool in ("delete_task", "a_tool_nobody_knows"):
        assert so.offer_due(_approve_n(so.OFFER_AFTER_APPROVALS + 3, tool=tool, id="x")) is None, tool


def _pattern(tool="send_email", **tool_input):
    return ap.from_card({"kind": "tool_confirm", "payload": {"tool": tool, "input": tool_input}})


def test_a_recipient_outside_the_pattern_is_another_pattern():
    base = {"to": "ann@example.com", "subject": "report"}
    assert _pattern(**base).fingerprint() == _pattern(**base).fingerprint()
    assert _pattern(**{**base, "to": "bob@example.com"}).fingerprint() != _pattern(**base).fingerprint()
    lookalike = "аnn@example.com"
    assert _pattern(**{**base, "to": lookalike}).fingerprint() != _pattern(**base).fingerprint()
    assert _pattern(**{**base, "cc": "bob@example.com"}).fingerprint() != _pattern(**base).fingerprint()


def test_free_text_is_not_part_of_a_pattern_but_a_fixed_value_is():
    a = _pattern("create_task", title="buy milk, then call the school")
    b = _pattern("create_task", title="a different sentence entirely")
    assert a.fingerprint() == b.fingerprint()
    assert _pattern("create_task", list="home").fingerprint() != _pattern("create_task", list="work").fingerprint()
    assert _pattern("create_task", done=True).fingerprint() != _pattern("create_task", done=False).fingerprint()


def _tool_card(tool, tool_input=None, **extra):
    return {"kind": "tool_confirm", "policy_class": "outward",
            "payload": {"tool": tool, "input": tool_input or {}}, **extra}


def test_every_known_tool_crossed_with_every_exclusion():
    """Q11: each exclusion's trigger excludes every tool the gate knows; an unknown tool is refused."""
    tools = sorted(ap.known_tools())
    assert len(tools) > 20
    triggers = {
        ap.GrantExclusion.SCREEN_ONLY: lambda t: dict(_tool_card(t), kind="web_task_plan"),
        ap.GrantExclusion.SEARCH_CARD: lambda t: {"kind": "tool_confirm", "payload": {
            "tool": t, "input": {}, "public_query": {"q": 1}}},
        ap.GrantExclusion.LAYA_CARD: lambda t: {"kind": "tool_confirm", "payload": {
            "tool": t, "input": {}, "origin": "laya_safe_write"}},
        ap.GrantExclusion.MONEY: lambda t: _tool_card(t, policy_class="spend"),
        ap.GrantExclusion.CREDENTIAL: lambda t: _tool_card(t, {"password": "x"}),
    }
    for tool in tools:
        for want, make in triggers.items():
            assert ap.exclusion_of(make(tool)) is not None, (tool, want)
        if action_gate.narrow_grant_only(tool):
            assert ap.exclusion_of(_tool_card(tool)) is ap.GrantExclusion.NO_UNDO, tool
    assert ap.exclusion_of(_tool_card("a_tool_nobody_knows")) is ap.GrantExclusion.UNKNOWN_TOOL
    assert ap.exclusion_of({"kind": "file_grant_request", "payload": {}}) is ap.GrantExclusion.NOT_A_TOOL_CARD
    assert ap.exclusion_of(_tool_card("pair_device")) is ap.GrantExclusion.DEVICE_RIGHT


def test_a_send_with_no_named_recipient_is_never_offered_and_a_named_one_may_be():
    assert ap.exclusion_of(_tool_card("draft_email", {"send": True})) is ap.GrantExclusion.UNNAMED_RECIPIENT
    assert ap.exclusion_of(_tool_card("draft_email", {"send": True, "to": "ann@example.com"})) is None
