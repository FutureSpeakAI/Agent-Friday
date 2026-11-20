"""The standing-grant offer card, the press that accepts it, and the grant it makes (Compressor eval K5, PR B).
Synthetic cards only."""
from __future__ import annotations

import itertools
import json
import re
from pathlib import Path

import pytest

from agent_friday.governance import action_gate
from agent_friday.services import action_ledger, action_pattern as ap
from agent_friday.services import approval_batches, approval_novelty as an, approvals
from agent_friday.services import standing_grants as sg
from agent_friday.services import standing_offers as so

NEXT = itertools.count()
DAY = 86400.0


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
    monkeypatch.setattr(action_gate, "_grants_file", lambda: tmp_path / "grants.json")
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    sg.register()
    pressed = {"on": False}
    monkeypatch.setattr(approvals, "_press_granted", lambda approval_id=None: pressed["on"])
    return pressed


def _decide(tool="create_task", decision="approve", task_id="", **tool_input):
    rec = approvals.create_approval(
        kind="tool_confirm", subject_type="tool_action", subject_id=f"s{next(NEXT)}",
        title="t", action_description="run it", force_gate=True, requested_by="confirmation_gate",
        payload={"tool": tool, "task_id": task_id, "input": tool_input or {"list": "home"}})
    return approvals.decide(rec["approval_id"], decision)


def _earn(tool="create_task", **tool_input):
    for _ in range(so.OFFER_AFTER_APPROVALS):
        _decide(tool=tool, **tool_input)


def _offers():
    return [c for c in approvals.list_approvals(status="pending") if c["kind"] == sg.OFFER_KIND]


def _press(store, offer_id, decision="approve"):
    store["on"] = True
    try:
        return approvals.decide(offer_id, decision, decided_by="owner:ui")
    finally:
        store["on"] = False


def _grants():
    return [g for g in action_gate.list_grants() if str(g["scope"]).startswith(sg.PATTERN_SCOPE)]


def _call(tool="create_task", **args):
    return action_gate.authorize(tool, args or {"list": "home"}, {"principal": "owner", "session_id": "s-chat"})


def test_the_nth_approval_raises_exactly_one_offer_card_and_no_grant():
    for _ in range(so.OFFER_AFTER_APPROVALS - 1):
        _decide()
    assert _offers() == []
    _decide()
    offers = _offers()
    assert len(offers) == 1 and offers[0]["requested_by"] == sg.SHELL
    assert "take this back in Settings" in offers[0]["description"]
    assert _grants() == []
    _decide()
    assert len(_offers()) == 1, "one offer pending at a time"


def test_a_press_on_the_offer_makes_a_signed_scoped_grant_and_a_call_then_runs_on_it(store):
    _earn()
    offer = _offers()[0]
    assert _call().action != "allow", "no grant before the press"
    _press(store, offer["approval_id"])
    [grant] = _grants()
    assert grant["tools"] == ["create_task"] and grant["created_by"] == "owner:ui"
    assert grant["max_uses"] == sg.GRANT_USES
    assert 29 * DAY < grant["expires_at"] - grant["created_at"] <= 30 * DAY + 5
    verdict = _call()
    assert verdict.action == "allow" and "standing grant" in verdict.reason


def test_only_the_press_on_this_pc_accepts_and_every_other_channel_may_decline(store):
    _earn()
    offer_id = _offers()[0]["approval_id"]
    refused = [
        approvals.decide(offer_id, "approve", decided_by="owner"),
        approvals.decide(offer_id, "approve", decided_by="owner", source="voice"),
        approvals.decide(offer_id, "approve", decided_by="owner", source="typed"),
        approvals.decide(offer_id, "approve", decided_by="owner", source="text_message"),
        approvals.decide(offer_id, "approve", decided_by="friday", source="system"),
        approvals.decide(offer_id, "approve", decided_by="owner:ui"),
    ]
    assert all(card["status"] == "pending" for card in refused)
    assert _grants() == []
    batch = approval_batches.decide_batch("x", [offer_id, "a", "b"], "approve")
    assert batch["ok"] is False
    assert approvals.decide(offer_id, "deny", decided_by="owner", source="typed")["status"] == "denied"


def test_a_declined_offer_makes_no_grant_and_is_not_offered_again_for_thirty_days(store):
    _earn()
    _press(store, _offers()[0]["approval_id"], decision="deny")
    assert _grants() == []
    _earn()
    assert _offers() == []


def test_a_denial_between_the_offer_and_the_press_means_no_grant(store):
    _earn()
    offer_id = _offers()[0]["approval_id"]
    _decide(decision="deny")
    _press(store, offer_id)
    assert _grants() == []
    assert any(e["kind"] == "standing_offer_stale" for e in action_ledger.entries(["standing_offer_stale"]))


def test_only_this_module_makes_a_standing_grant():
    root = Path(action_gate.__file__).resolve().parents[1]
    users = sorted(p.relative_to(root).as_posix() for p in root.rglob("*.py")
                   if "PATTERN_SCOPE" in p.read_text(encoding="utf-8", errors="ignore"))
    assert users == ["governance/action_gate.py", "services/standing_grants.py"], users
    sources = [p.read_text(encoding="utf-8", errors="ignore") for p in root.rglob("*.py")]
    assert not [s for s in sources if re.search(r"create_grant\([^)]*scope=['\"]pattern:", s)]


@pytest.mark.parametrize("tool, seconds, uses, by", [
    ("delete_task", 3600, 1, "owner:ui"),
    ("a_tool_nobody_knows", 3600, 1, "owner:ui"),
    ("create_task", 91 * DAY, 1, "owner:ui"),
    ("create_task", 3600, 51, "owner:ui"),
    ("create_task", 3600, 1, "owner"),
    ("create_task", 3600, 1, "friday"),
])
def test_a_standing_grant_made_by_hand_is_refused(tool, seconds, uses, by):
    with pytest.raises(Exception):
        action_gate.create_grant(tools=[tool], scope="pattern:abc", expires_in_seconds=seconds,
                                 max_uses=uses, created_by=by)
    assert _grants() == []


def _granted(store, **tool_input):
    _earn(**tool_input)
    _press(store, _offers()[0]["approval_id"])
    return _grants()[0]


def test_a_grant_stops_covering_when_it_is_revoked_and_the_revoke_is_a_ledger_entry(store):
    grant = _granted(store)
    assert _call().action == "allow"
    assert action_gate.revoke_grant(grant["grant_id"]) is True
    assert _call().action != "allow"
    assert any(e["kind"] == "grant_revoke" for e in action_ledger.entries(["grant_revoke"]))


def test_a_grant_stops_covering_at_expiry(store, monkeypatch):
    _granted(store)
    assert _call().action == "allow"
    import time as _time
    real = _time.time
    monkeypatch.setattr(action_gate.time, "time", lambda: real() + 31 * DAY)
    assert _call().action != "allow"


def test_the_use_cap_ends_the_grant(store):
    _granted(store)
    results = [_call().action for _ in range(sg.GRANT_USES + 1)]
    assert results[:sg.GRANT_USES] == ["allow"] * sg.GRANT_USES and results[-1] != "allow"
    assert len([e for e in action_ledger.entries(["grant_use"])]) == sg.GRANT_USES


def test_a_grant_never_covers_another_pattern_or_a_new_or_unusual_instance(store):
    _granted(store)
    assert _call(list="another").action != "allow", "a different fixed value is another action"
    assert _call("update_task", list="home").action != "allow"
    _decide(tool="update_task", task_id="T9", id="x")
    unusual = action_gate.authorize("create_task", {"list": "home"}, {"principal": "owner", "task_id": "T9"})
    assert unusual.action != "allow", "after another kind in a task it is a new sequence: asked"
    approvals_before = an.decided_rows()
    assert _call().action == "allow", "the same action still runs"
    assert an.decided_rows() == approvals_before


def test_a_laya_or_private_data_reason_asks_even_with_a_live_grant(store):
    _granted(store)
    ctx = {"principal": "owner"}
    assert sg.cover("create_task", {"list": "home"}, ctx, "routine") is not None
    assert sg.cover("create_task", {"list": "home"}, ctx, "Laya judged that it changes something outside") is None
    assert sg.cover("create_task", {"list": "home"}, ctx, "private data would leave in its arguments") is None


def test_a_tainted_call_is_asked_even_with_a_live_grant(store):
    _granted(store)
    assert action_gate.authorize("create_task", {"list": "home"}, {"principal": "owner"}, tainted=True).action != "allow"


def test_a_stored_grant_with_a_changed_field_covers_nothing(store):
    path = action_gate._grants_file()
    _granted(store)
    rows = json.loads(path.read_text(encoding="utf-8"))
    rows[0]["expires_at"] = rows[0]["expires_at"] + 400 * DAY
    path.write_text(json.dumps(rows), encoding="utf-8")
    assert _call().action != "allow"


def test_every_step_is_a_ledger_entry_and_the_ledger_still_verifies(store):
    _granted(store)
    _call()
    action_gate.revoke_grant(_grants()[0]["grant_id"])
    kinds = {e["kind"] for e in action_ledger.entries()}
    assert {"standing_offer_shown", "standing_offer_accepted", "grant_use", "grant_revoke"} <= kinds
    assert action_ledger.verify().get("ok", True) is not False


def test_cover_itself_refuses_an_excluded_action_even_if_a_grant_were_found(monkeypatch):
    monkeypatch.setattr(action_gate, "_use_grant", lambda tool, ctx, args=None: {"grant_id": "grant_x"})
    for _ in range(3):
        _decide(tool="draft_email", send=True)
    ctx = {"principal": "owner"}
    assert an.peek(ap.card_of_call("draft_email", {"send": True})) is an.Novelty.SEEN
    assert sg.cover("draft_email", {"send": True}, ctx, "") is None, "a send with no named recipient"
    for _ in range(3):
        _decide(tool="draft_email", send=True, to="ann@example.com")
    assert sg.cover("draft_email", {"send": True, "to": "ann@example.com"}, ctx, "") is not None


# ── review findings on #873 (two independent critics) ─────────────────────────────────────────────────────

CHAT = {"principal": "owner", "session_id": "s-chat"}


def _grant_via_press(store, **tool_input):
    _earn(**tool_input)
    _press(store, _offers()[0]["approval_id"])


def test_a_grant_earned_in_chat_does_not_cover_the_same_call_from_an_unattended_ctx(store):
    """The pattern's origin is the confirmation gate of a chat session: a new origin is a new action."""
    _grant_via_press(store)
    assert action_gate.authorize("create_task", {"list": "home"}, dict(CHAT)).action == "allow"
    for ctx in ({"principal": "owner"},
                {"principal": "owner", "scheduled": True, "schedule_id": "sch_x"},
                {"principal": "owner", "session_id": "s-chat", "is_background_task": True},
                {"principal": "owner", "session_id": "s-chat", "confirm_bypass": True}):
        assert action_gate.authorize("create_task", {"list": "home"}, ctx).action != "allow", ctx


def test_a_grant_earned_on_one_text_does_not_cover_a_call_that_spends_money(store):
    """Free text is outside the pattern, so the money and irreversible exclusions must be checked on the call
    itself when the grant is spent, not only on the card that earned it."""
    _grant_via_press(store, list="home", title="Call mom")
    assert action_gate.authorize("create_task", {"list": "home", "title": "Call dad"}, dict(CHAT)).action == "allow"
    spend = action_gate.authorize("create_task", {"list": "home", "title": "Wire $5,000 to the vendor now"},
                                  dict(CHAT))
    assert spend.action != "allow"


def test_the_standing_path_never_spends_a_task_or_schedule_grant(store):
    """cover() hands the gate only the pattern scope. A task grant in an interactive chat ctx is not one the
    standing path may spend, however familiar the call is (no standing grant has been pressed here)."""
    _earn()
    action_gate.create_grant(tools=["create_task"], scope="task_zz", expires_in_seconds=3600, max_uses=1,
                             created_by="owner", note="a task grant")
    ctx = dict(CHAT, task_id="task_zz")
    assert action_gate.authorize("create_task", {"list": "home"}, ctx).action != "allow"


def test_an_offer_can_be_raised_again_after_a_decline_without_wedging_the_slot(store):
    _earn()
    first = _offers()[0]
    approvals.decide(first["approval_id"], "deny", decided_by="owner")
    so.STATE_FILE.unlink(missing_ok=True)                 # the 30-day suppression is over
    _decide()
    again = _offers()
    assert len(again) == 1 and again[0]["approval_id"] != first["approval_id"], "no new offer card was raised"
    _press(store, again[0]["approval_id"])
    assert len(_grants()) == 1


def test_the_offer_card_says_the_scope_first_and_bounds_what_a_tool_argument_can_add(store, monkeypatch):
    long_recipient = "a" * 5000 + "@evil.example"
    pattern = ap.from_call("create_task", {"to": long_recipient, "list": "home"})
    monkeypatch.setattr(so, "offer_due", lambda card: pattern)
    sg.maybe_offer({"status": "approved", "kind": "tool_confirm", "requested_by": "confirmation_gate",
                    "payload": {"tool": "create_task", "input": {"to": long_recipient, "list": "home"}}})
    [offer] = _offers()
    text = offer["description"]
    assert "30 days or 50 uses" in text and "take this back in Settings" in text
    assert text.index("30 days") < text.index("a" * 30), "the fixed wording must come before tool-supplied text"
    assert len(text) < 700 and "a" * 100 not in text
