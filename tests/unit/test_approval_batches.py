"""NA-2: approvals of one kind and source form one batch card; the approvals stay per action.

The invariant under test is the gate: a batch is a view and a loop. Approving the batch approves exactly
the listed actions, each through the same decide() as a single card; anything that has changed since the
card was drawn is refused and decides nothing; the batch shows every item in full; taint cards (NA-4),
money, connector actions, click-only approvals and cards with no trusted source never batch; a decline is
always allowed.
"""
from __future__ import annotations

import pytest

from agent_friday.services import approval_batches as ab
from agent_friday.services import approvals
from tests.desk_press_support import pressed      # "the click" is a verified press on this PC (services/desk_press)

OK = frozenset({"internal", "outward"})


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch, friday_dir):
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    yield


def _card(i, kind="media_diet_rule", by="media_diet", payload=None, **extra):
    return approvals.create_approval(
        kind=kind, subject_type="rule", subject_id="%s-%s-%d" % (kind, by, i),
        title="Change rule %d" % i, action_description="add the rule 'limit %d'" % i,
        description="Friday would add this rule.", force_gate=True,
        payload=payload or {}, requested_by=by, **extra)


def _ids(*cards):
    return [c["approval_id"] for c in cards]


def _status(aid):
    return approvals.get_approval(aid)["status"]


def _allow(monkeypatch, *kinds):
    """Put a kind on the batch list for a test that is about the other checks, not the list."""
    monkeypatch.setattr(ab, "BATCHABLE_KINDS", dict(ab.BATCHABLE_KINDS, **{k: OK for k in kinds}))


# ── what may batch ───────────────────────────────────────────────────────────

def test_cards_of_one_batchable_kind_and_service_are_one_batch_that_lists_each_action():
    cards = [_card(i) for i in range(3)]
    plan = ab.pending_plan()
    assert plan["singles"] == [] and len(plan["batches"]) == 1
    b = plan["batches"][0]
    assert b["count"] == 3 and b["kind"] == "media_diet_rule"
    assert sorted(b["approval_ids"]) == sorted(_ids(*cards))
    assert "3 media diet rule approvals" in b["sentence"] and b["batch_id"].startswith("batch_")


def test_taint_cards_do_not_batch_until_their_own_review(monkeypatch):
    """The addendum puts taint batching under NA-4 (safety review + harsh critic before enable)."""
    for i in range(3):
        _card(i, kind="tainted_action", by="taint_gate",
              payload={"tool": "open_url", "conversation_id": "chat-1", "task_id": "t1"})
    plan = ab.pending_plan()
    assert plan["batches"] == [] and len(plan["singles"]) == 3
    assert "tainted_action" not in ab.BATCHABLE_KINDS and "governed_action" not in ab.BATCHABLE_KINDS
    assert "tool_confirm" not in ab.BATCHABLE_KINDS


def test_a_batch_shows_every_item_in_full_as_a_single_card_would():
    """B1: the owner never says yes to less than one card would have shown."""
    cards = [_card(i, cost_estimate_mψ=0) for i in range(2)]
    b = ab.pending_plan()["batches"][0]
    for item, card in zip(b["items"], sorted(cards, key=lambda c: c["created_at"])):
        stored = approvals.get_approval(card["approval_id"])
        for field in ("approval_id", "title", "action_description", "description", "provenance", "dissent",
                      "policy_class", "kind", "expires_at", "payload", "status"):
            assert field in item, field
            assert item[field] == stored[field], field
        assert len(item["action_description"]) == len(stored["action_description"]), "nothing is cut"


def test_a_long_action_is_not_cut_in_the_batch_view():
    long_text = "add the rule " + "x" * 900
    for i in range(2):
        approvals.create_approval(kind="media_diet_rule", subject_type="rule", subject_id="long%d" % i,
                                  title="Rule", action_description=long_text, force_gate=True,
                                  requested_by="media_diet")
    assert all(it["action_description"] == long_text[:1000] for it in ab.pending_plan()["batches"][0]["items"])


def test_a_different_service_or_a_lone_card_stays_a_single_card():
    _card(1, by="media_diet"); _card(2, by="media_diet"); _card(3, by="other_service")
    plan = ab.pending_plan()
    assert [b["count"] for b in plan["batches"]] == [2]
    assert [r["title"] for r in plan["singles"]] == ["Change rule 3"]


def test_a_card_only_a_click_may_approve_never_batches_even_if_its_kind_were_listed(monkeypatch):
    """T1: exercise the SCREEN_ONLY branch itself."""
    assert "file_grant_request" in approvals.SCREEN_ONLY_KINDS
    _allow(monkeypatch, "file_grant_request")
    for i in range(3):
        approvals.create_approval(kind="file_grant_request", subject_type="file", subject_id="f%d" % i,
                                  title="Let Friday read file %d" % i, force_gate=True, requested_by="svc")
    assert ab.pending_plan()["batches"] == []
    monkeypatch.setattr(approvals, "SCREEN_ONLY_KINDS", {})        # take the check away and it batches
    assert len(ab.pending_plan()["batches"]) == 1


@pytest.mark.parametrize("cls", ["spend", "irreversible", "external_message", "law1_block"])
def test_money_irreversible_and_outgoing_message_classes_never_batch(cls):
    cards = [_card(i) for i in range(3)]
    for c in cards:
        approvals._patch(c["approval_id"], policy_class=cls)
    assert ab.pending_plan()["batches"] == []


def test_a_class_off_the_kinds_allowlist_never_batches():
    cards = [_card(i) for i in range(2)]
    for c in cards:
        approvals._patch(c["approval_id"], policy_class="surprise")
    assert ab.pending_plan()["batches"] == []


def test_a_payment_named_connector_tool_never_batches_whatever_its_label_says(monkeypatch):
    """M5: the label scan misses 'mcp_stripe_create_payment'; the connector prefix does not."""
    _allow(monkeypatch, "tool_confirm")
    for i in range(2):
        _card(i, kind="tool_confirm", by="confirmation_gate",
              payload={"tool": "mcp_stripe_create_payment", "conversation_id": "c1", "task_id": "t1"})
    assert ab.pending_plan()["batches"] == []
    for i in range(2, 4):
        _card(i, kind="tool_confirm", by="confirmation_gate",
              payload={"tool": "read_notes", "conversation_id": "c1", "task_id": "t1"})
    assert len(ab.pending_plan()["batches"]) == 1                  # the control: the same shape batches


def test_an_approval_that_costs_money_is_never_batched():
    _card(1, cost_estimate_mψ=5); _card(2, cost_estimate_mψ=5)
    plan = ab.pending_plan()
    assert plan["batches"] == [] and len(plan["singles"]) == 2


# ── who asked: trusted, per conversation and per task ───────────────────────

def test_two_tasks_in_one_conversation_never_share_a_batch(monkeypatch):
    _allow(monkeypatch, "tool_confirm")
    for i, task in enumerate(("t1", "t1", "t2", "t2")):
        _card(i, kind="tool_confirm", by="confirmation_gate",
              payload={"tool": "read_notes", "conversation_id": "c1", "task_id": task})
    plan = ab.pending_plan()
    assert sorted(b["count"] for b in plan["batches"]) == [2, 2]
    assert not {tuple(sorted(b["approval_ids"])) for b in plan["batches"]} & {()}


def test_background_cards_with_no_conversation_and_no_task_are_single(monkeypatch):
    _allow(monkeypatch, "tool_confirm")
    for i in range(3):
        _card(i, kind="tool_confirm", by="confirmation_gate", payload={"tool": "read_notes", "conversation_id": ""})
    plan = ab.pending_plan()
    assert plan["batches"] == [] and len(plan["singles"]) == 3, "requested_by is not a shared bucket"


def test_a_governed_actions_own_detail_cannot_steer_the_grouping(monkeypatch):
    """M4: the key comes from fields the server wrote (kind, requested_by), never from the payload."""
    _allow(monkeypatch, "governed_action")
    for i in range(3):          # three services, one payload claiming the same task: not one batch
        _card(i, kind="governed_action", by="svc%d" % i,
              payload={"task_id": "same", "conversation_id": "same", "tool": "read_notes"})
    assert ab.pending_plan()["batches"] == []
    for i in range(3, 6):       # one service, payloads claiming different tasks: still one batch
        _card(i, kind="governed_action", by="item_actions",
              payload={"task_id": "t%d" % i, "conversation_id": "c%d" % i, "tool": "read_notes"})
    assert [b["count"] for b in ab.pending_plan()["batches"]] == [3]


def test_the_session_payload_carries_the_task_id_the_server_wrote():
    from agent_friday.services import agent
    p = agent._approval_tool_payload("read_notes", {}, {"conversation_id": "c1", "task_id": "t9"})
    assert (p["conversation_id"], p["task_id"]) == ("c1", "t9")


# ── deciding ─────────────────────────────────────────────────────────────────

def test_approving_the_batch_approves_exactly_the_listed_actions_each_on_its_own_card():
    cards = [_card(i) for i in range(3)]
    other = _card(9, by="other_service")
    b = ab.pending_plan()["batches"][0]
    with pressed(): out = ab.decide_batch(b["batch_id"], b["approval_ids"], "approve", decided_by="owner:ui")
    assert out["ok"] and out["decided"] == 3 and out["refused"] == 0
    assert {_status(a) for a in _ids(*cards)} == {"approved"}
    assert _status(other["approval_id"]) == "pending", "a card outside the batch is untouched"
    for a in _ids(*cards):
        rec = approvals.get_approval(a)
        assert rec["decided_by"] == "owner:ui" and rec["consumed"] is False, "one decision, one action"


def test_a_batch_that_changed_since_it_was_drawn_decides_nothing():
    cards = [_card(i) for i in range(3)]
    b = ab.pending_plan()["batches"][0]
    _card(4)                                                 # a fourth action arrives
    out = ab.decide_batch(b["batch_id"], b["approval_ids"], "approve")
    assert out["ok"] is False and out["error"] == "changed"
    assert {_status(a) for a in _ids(*cards)} == {"pending"}


def test_a_card_decided_elsewhere_makes_the_old_batch_stale():
    cards = [_card(i) for i in range(3)]
    b = ab.pending_plan()["batches"][0]
    approvals.decide(cards[0]["approval_id"], "deny", decided_by="owner")
    out = ab.decide_batch(b["batch_id"], b["approval_ids"], "approve")
    assert out["ok"] is False and out["error"] == "changed"
    assert _status(cards[0]["approval_id"]) == "denied", "the earlier decision stands"
    assert {_status(a) for a in _ids(cards[1], cards[2])} == {"pending"}


@pytest.mark.parametrize("tamper", ["extra", "fewer", "other_batch_id", "single", "dupes", "unknown"])
def test_a_request_that_does_not_name_exactly_the_batch_is_refused(tamper):
    cards = [_card(i) for i in range(3)]
    stranger = _card(7, by="other_service")
    b = ab.pending_plan()["batches"][0]
    ids, bid = list(b["approval_ids"]), b["batch_id"]
    if tamper == "extra":
        ids.append(stranger["approval_id"])
    elif tamper == "fewer":
        ids = ids[:2]
    elif tamper == "other_batch_id":
        bid = ab.batch_id_for(["x", "y"])
    elif tamper == "single":
        ids = ids[:1]
    elif tamper == "dupes":
        ids = [ids[0], ids[0], ids[1], ids[2]]
    else:
        ids = ["appr_nope", "appr_none"]
    assert ab.decide_batch(bid, ids, "approve")["ok"] is False
    assert {_status(a) for a in _ids(*cards, stranger)} == {"pending"}, "nothing was decided"


def test_declining_a_batch_declines_exactly_the_listed_actions():
    cards = [_card(i) for i in range(2)]
    b = ab.pending_plan()["batches"][0]
    out = ab.decide_batch(b["batch_id"], b["approval_ids"], "deny", note="not these")
    assert out["ok"] and {_status(a) for a in _ids(*cards)} == {"denied"}


def test_a_decision_word_other_than_approve_or_deny_is_refused():
    [_card(i) for i in range(2)]
    b = ab.pending_plan()["batches"][0]
    assert ab.decide_batch(b["batch_id"], b["approval_ids"], "maybe")["ok"] is False


def test_each_card_is_decided_through_the_normal_gate_with_the_callers_decided_by(monkeypatch):
    [_card(i) for i in range(2)]
    seen = []
    real = approvals.decide_with_outcome

    def spy(aid, decision, *, decided_by="owner", note=""):
        seen.append((aid, decision, decided_by))
        return real(aid, decision, decided_by=decided_by, note=note)

    monkeypatch.setattr(approvals, "decide_with_outcome", spy)
    b = ab.pending_plan()["batches"][0]
    ab.decide_batch(b["batch_id"], b["approval_ids"], "approve", decided_by="voice")
    assert sorted(a for a, _, _ in seen) == sorted(b["approval_ids"])
    assert {d for _, _, d in seen} == {"voice"} and {x for _, x, _ in seen} == {"approve"}
