"""A24 (owner ruling 2026-10-22): completing, creating and editing Google Tasks ask first.

Before: complete_task, create_task and update_task were internal at the checkpoint, so Friday
changed the owner's Google Tasks without asking, in chat or in a run nobody watched. Now they are
outward. In a background task, routine or workflow run, the first change raises one card that
covers the run (the batch-grant model, services/run_grants). Approving it mints a grant scoped to
the run, so the run's later task changes go through, and each one writes its own receipt naming
that grant. Deleting a task still asks each time. A change that already has its own card is never
done twice. Synthetic ids only.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent_friday.governance import action_gate as ag


@pytest.mark.parametrize("tool", ["complete_task", "create_task", "update_task"])
def test_changing_google_tasks_asks_first(tool):
    assert ag.classify(tool, None)[0] == ag.OUTWARD


def _rg():
    from agent_friday.services import run_grants
    return run_grants


def test_the_card_in_a_run_offers_to_cover_the_run():
    ex = _rg().card_extras("create_task", {"run_id": "wfr_test_a24_1", "task_id": "t1"})
    assert ex["scope"] == "wfr_test_a24_1", "a workflow run is the unit"
    assert set(ex["tools"]) == {"complete_task", "create_task", "update_task"}
    assert "delete_task" not in ex["tools"], "deleting still asks each time"
    words = _rg().card_words(ex)
    assert "this run only" in words and "own receipt" in words
    assert "something Friday read" in words, "the card does not promise what a flagged change won't get"
    assert _rg().card_extras("delete_task", {"run_id": "wfr_x"}) is None
    assert _rg().card_extras("create_task", {}) is None, "no run, no run grant"


def _card(aid, decided_by, scope, source="desktop_press"):
    return {"approval_id": aid, "status": "approved", "decided_by": decided_by,
            "decided_source": source,
            "payload": {"tool": "create_task", "input": {},
                        "run_grant": _rg().card_extras("create_task", {"run_id": scope})}}


def test_the_owners_approval_grants_the_rest_of_the_run_and_each_use_is_counted():
    scope = "wfr_test_a24_2"
    g = _rg().mint_on_approval(_card("appr_a24_2", "owner:ui", scope))
    assert g and g["scope"] == scope and set(g["tools"]) == set(_rg().RUN_GRANTABLE)
    assert _rg().mint_on_approval(_card("appr_a24_2", "owner:ui", scope))["grant_id"] == g["grant_id"], \
        "one card, one grant"
    assert _rg().mint_on_approval(_card("appr_a24_2b", "owner:chat", scope, "typed"))["grant_id"] \
        == g["grant_id"], "one live run grant per run"
    used = ag._use_grant("update_task", {"run_id": scope})
    assert used and used["grant_id"] == g["grant_id"] and used["uses_left"] == g["uses_left"] - 1
    assert ag._use_grant("delete_task", {"run_id": scope}) is None, "the grant does not cover deleting"
    assert ag._use_grant("update_task", {"run_id": "wfr_another_run"}) is None, "another run is not covered"
    ag.revoke_grant(g["grant_id"])


@pytest.mark.parametrize("decided_by,source", [
    ("friday", "friday"), ("system:expiry", "system"), ("grant:abc", "standing_grant"), ("", "in_process"),
    ("owner", "page_without_desk_key"), ("owner", "page_not_on_this_pc"), ("owner", "local_program"),
    ("owner", "in_process"), ("owner:ui", None)],
    ids=["friday", "expiry", "grant", "nobody", "no-desk-key", "other-device", "script", "in-process",
         "no-source"])
def test_nobody_but_the_owner_mints_a_run_grant(decided_by, source):
    assert _rg().mint_on_approval(_card("appr_a24_3", decided_by, "wfr_test_a24_3", source)) is None


def test_a_card_cannot_smuggle_in_other_tools():
    rec = _card("appr_a24_4", "owner", "wfr_test_a24_4", "typed")
    rec["payload"]["run_grant"]["tools"] = ["create_task", "send_email"]
    assert _rg().mint_on_approval(rec) is None


# ── Through the checkpoint ──────────────────────────────────────────────────

@pytest.fixture
def gate(monkeypatch):
    monkeypatch.setattr(ag, "verify_claws", lambda: (True, "ok"))
    yield ag


def _last_receipt():
    lines = ag.receipts_path().read_text(encoding="utf-8").strip().splitlines()
    return json.loads(lines[-1])


def test_a_background_run_asks_then_its_grant_lets_the_next_change_through_with_a_receipt(gate):
    scope = "t_test_a24_bg"
    ctx = {"is_background_task": True, "task_id": scope}
    assert gate.authorize("create_task", {"title": "x"}, ctx).action == "card"
    g = _rg().mint_on_approval(_card("appr_a24_bg", "owner:ui", scope))
    try:
        v = gate.authorize("update_task", {"task_id": "k1", "title": "y"}, ctx)
        assert v.action == "allow" and (v.grant or {}).get("grant_id") == g["grant_id"]
        r = _last_receipt()
        assert r["tool"] == "update_task" and r["decision"] == "allow" and r["grant"] == g["grant_id"]
        assert gate.authorize("delete_task", {"task_id": "k1"}, ctx).action == "card"
        assert gate.authorize("update_task", {"task_id": "k1"}, ctx, tainted=True).action == "card", \
            "a change with details from outside content still gets its own card"
    finally:
        ag.revoke_grant(g["grant_id"])


def test_in_chat_each_change_is_asked_even_when_a_grant_exists(gate):
    scope = "t_test_a24_chat"
    g = _rg().mint_on_approval(_card("appr_a24_chat", "owner:ui", scope))
    try:
        v = gate.authorize("complete_task", {"task_id": "k2"}, {"session_id": "s_a24", "task_id": scope})
        assert v.action == "confirm" and not v.grant
    finally:
        ag.revoke_grant(g["grant_id"])


# ── Never twice: the run's own card and its grant ───────────────────────────

@pytest.fixture
def cards(tmp_path, monkeypatch):
    from agent_friday.services import approvals as ap, dissent_gate as dg
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(ap, "_fire_hook", lambda rec: None)   # no executor in these tests
    monkeypatch.setattr(ag, "verify_claws", lambda: (True, "ok"))
    return ap


def _call(scope, tool="create_task", inp=None):
    sc = {"is_background_task": True, "task_id": scope}
    return SimpleNamespace(tool_name=tool, input=inp or {"title": "Buy stamps"}, session_ctx=sc, meta={})


def _raise_card(ap, ctx):
    from agent_friday.services import agent, taint
    key = taint.ledger_key(ctx.session_ctx)
    subject = "taint:%s:%s" % (key, taint.fingerprint(ctx.tool_name, ctx.input))
    rec = ap.create_approval(
        kind="tainted_action", subject_type="tool_action", subject_id=subject,
        title="Create task", action_description="create_task", description="test",
        force_gate=True, payload=agent._run_grant_payload(
            {"tool": ctx.tool_name, "input": ctx.input}, ctx.tool_name, ctx.session_ctx),
        action_class="outward", requested_by="taint_gate")
    return key, rec


def test_a_card_already_carried_out_is_not_done_again_by_the_grant(cards):
    from agent_friday.services import agent
    ctx = _call("t_test_a24_done")
    key, rec = _raise_card(cards, ctx)
    cards.decide_with_outcome(rec["approval_id"], "approve", decided_by="owner:chat")
    assert cards.claim_for_execution(rec["approval_id"])            # the executor ran it
    cards.mark_used(rec["approval_id"], "approval_executor")
    g = _rg().mint_on_approval(cards.get_approval(rec["approval_id"]))
    try:
        verdict, waiting = agent._run_card_prior(ctx, key)
        assert verdict is not None and verdict.action == "deny" and "[ALREADY DONE]" in verdict.reason
        assert waiting is None
        assert [x for x in ag.list_grants() if x["grant_id"] == g["grant_id"]][0]["uses_left"] == \
            g["uses_left"], "no grant use is spent on a change that is not made"
    finally:
        ag.revoke_grant(g["grant_id"])


def test_an_approved_card_not_yet_run_is_claimed_once_by_the_run(cards):
    from agent_friday.services import agent
    ctx = _call("t_test_a24_claim")
    key, rec = _raise_card(cards, ctx)
    cards.decide_with_outcome(rec["approval_id"], "approve", decided_by="owner:chat")
    verdict, _ = agent._run_card_prior(ctx, key)
    try:
        assert verdict is not None and verdict.action == "allow"
        assert ctx.meta["owner_decided"] == rec["approval_id"]
        r = _last_receipt()
        assert r["tool"] == "create_task" and r["approval"] == rec["approval_id"], \
            "the run carrying out its card still gets the checkpoint's signed receipt"
        assert cards.get_approval(rec["approval_id"])["consumed"]
        assert not cards.claim_for_execution(rec["approval_id"]), "the executor cannot run it a second time"
        again, _ = agent._run_card_prior(_call("t_test_a24_claim"), key)
        assert again.action == "deny" and "[ALREADY DONE]" in again.reason
    finally:
        for x in ag.list_grants():
            if x.get("scope") == "t_test_a24_claim":
                ag.revoke_grant(x["grant_id"])


def test_a_waiting_card_is_withdrawn_when_the_grant_carries_out_the_change(cards):
    from agent_friday.services import agent
    ctx = _call("t_test_a24_wait")
    key, rec = _raise_card(cards, ctx)
    verdict, waiting = agent._run_card_prior(ctx, key)
    assert verdict is None and [w["approval_id"] for w in waiting] == [rec["approval_id"]]
    assert agent._run_card_supersede(ctx, waiting, {"grant_id": "grant_test"}) is None
    after = cards.get_approval(rec["approval_id"])
    assert after["status"] == "denied" and after["decided_by"] == "friday:superseded"
    out, _ = cards.decide_with_outcome(rec["approval_id"], "approve", decided_by="owner:chat")
    assert out["status"] == "denied", "approving the withdrawn card later cannot do it a second time"
    again, _ = agent._run_card_prior(_call("t_test_a24_wait"), key)
    assert again.action == "deny" and "[ALREADY DONE]" in again.reason


def test_the_owners_no_to_one_change_is_not_overridden_by_the_runs_grant(cards):
    from agent_friday.services import agent
    a, b = _call("t_test_a24_no"), _call("t_test_a24_no", inp={"title": "Cancel the trip"})
    _, card_a = _raise_card(cards, a)
    key, card_b = _raise_card(cards, b)
    cards.decide_with_outcome(card_a["approval_id"], "approve", decided_by="owner:chat")
    g = _rg().mint_on_approval(cards.get_approval(card_a["approval_id"]))
    cards.decide_with_outcome(card_b["approval_id"], "deny", decided_by="owner:chat")
    try:
        verdict, _ = agent._run_card_prior(b, key)
        assert verdict is not None and verdict.action == "deny" and "[DECLINED]" in verdict.reason
        assert [x for x in ag.list_grants() if x["grant_id"] == g["grant_id"]][0]["uses_left"] == g["uses_left"]
    finally:
        ag.revoke_grant(g["grant_id"])


def test_a_change_re_carded_after_its_first_card_expired_is_still_done_once(cards):
    from agent_friday.services import agent
    ctx = _call("t_test_a24_exp")
    key, first = _raise_card(cards, ctx)
    cards._patch(first["approval_id"], status="expired")
    second = cards.create_approval(
        kind="tainted_action", subject_type="tool_action", subject_id=first["subject_id"] + ":a1b2c3",
        title="Create task", action_description="create_task", description="test", force_gate=True,
        payload=dict(first["payload"]), action_class="outward", requested_by="taint_gate")
    cards.decide_with_outcome(second["approval_id"], "approve", decided_by="owner:chat")
    assert cards.claim_for_execution(second["approval_id"])          # the executor ran it
    cards.mark_used(second["approval_id"], "approval_executor")
    verdict, _ = agent._run_card_prior(_call("t_test_a24_exp"), key)
    assert verdict is not None and "[ALREADY DONE]" in verdict.reason


def _re_raise(ap, first, tag):
    return ap.create_approval(
        kind="tainted_action", subject_type="tool_action", subject_id=first["subject_id"] + ":" + tag,
        title="Create task", action_description="create_task", description="test", force_gate=True,
        payload=dict(first["payload"]), action_class="outward", requested_by="taint_gate")


def test_every_waiting_card_for_the_change_is_withdrawn_and_any_no_stands(cards):
    from agent_friday.services import agent
    ctx = _call("t_test_a24_many")
    key, first = _raise_card(cards, ctx)
    cards._patch(first["approval_id"], status="expired")
    y2, y3 = _re_raise(cards, first, "aaaaaa"), _re_raise(cards, first, "bbbbbb")
    verdict, waiting = agent._run_card_prior(ctx, key)
    assert verdict is None and {w["approval_id"] for w in waiting} == {y2["approval_id"], y3["approval_id"]}
    assert agent._run_card_supersede(ctx, waiting, {"grant_id": "grant_test"}) is None
    for y in (y2, y3):
        assert cards.get_approval(y["approval_id"])["decided_by"] == "friday:superseded"

    other = _call("t_test_a24_many2")
    key2, f2 = _raise_card(cards, other)
    cards._patch(f2["approval_id"], status="expired")
    z2, z3 = _re_raise(cards, f2, "cccccc"), _re_raise(cards, f2, "dddddd")
    cards.decide_with_outcome(z2["approval_id"], "deny", decided_by="owner:chat")
    verdict, _ = agent._run_card_prior(other, key2)
    assert verdict is not None and "[DECLINED]" in verdict.reason, "a no on any card for it stands"


def test_a_card_held_at_the_checkpoint_is_not_reported_as_done(cards):
    from agent_friday.services import agent
    ctx = _call("t_test_a24_held")
    key, rec = _raise_card(cards, ctx)
    cards.decide_with_outcome(rec["approval_id"], "approve", decided_by="owner:chat")
    assert cards.claim_for_execution(rec["approval_id"])
    cards.mark_used(rec["approval_id"], "approval_executor", detail={"ok": False, "error": "held"})
    verdict, _ = agent._run_card_prior(ctx, key)
    assert verdict.action == "deny" and "[NOT RUN]" in verdict.reason


def test_if_the_owner_approved_the_waiting_card_first_it_still_runs_once(cards):
    from agent_friday.services import agent
    ctx = _call("t_test_a24_race")
    key, rec = _raise_card(cards, ctx)
    _, waiting = agent._run_card_prior(ctx, key)
    cards.decide_with_outcome(rec["approval_id"], "approve", decided_by="owner:chat")   # same instant
    try:
        verdict = agent._run_card_supersede(ctx, waiting, {"grant_id": "grant_test"})
        assert verdict is not None and verdict.action == "allow"
        assert not cards.claim_for_execution(rec["approval_id"])
    finally:
        for x in ag.list_grants():
            if x.get("scope") == "t_test_a24_race":
                ag.revoke_grant(x["grant_id"])
