"""A24 (owner ruling 2026-10-22): completing, creating and editing Google Tasks ask first.

Before: complete_task, create_task and update_task were internal at the checkpoint, so Friday
changed the owner's Google Tasks without asking, in chat or in a run nobody watched. Now they are
outward. In a background task, routine or workflow run, the first change raises one card that
covers the run (the batch-grant model, services/run_grants). Approving it mints a grant scoped to
the run, so the run's later task changes go through, and each one writes its own receipt naming
that grant. Deleting a task still asks each time. Synthetic ids only.
"""
from __future__ import annotations

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
    assert _rg().card_extras("delete_task", {"run_id": "wfr_x"}) is None
    assert _rg().card_extras("create_task", {}) is None, "no run, no run grant"


def _card(aid, decided_by, scope):
    return {"approval_id": aid, "status": "approved", "decided_by": decided_by,
            "payload": {"tool": "create_task", "input": {},
                        "run_grant": _rg().card_extras("create_task", {"run_id": scope})}}


def test_the_owners_approval_grants_the_rest_of_the_run_and_each_use_is_counted():
    scope = "wfr_test_a24_2"
    g = _rg().mint_on_approval(_card("appr_a24_2", "owner:ui", scope))
    assert g and g["scope"] == scope and set(g["tools"]) == set(_rg().RUN_GRANTABLE)
    assert _rg().mint_on_approval(_card("appr_a24_2", "owner:ui", scope))["grant_id"] == g["grant_id"], \
        "one card, one grant"
    used = ag._use_grant("update_task", {"run_id": scope})
    assert used and used["grant_id"] == g["grant_id"] and used["uses_left"] == g["uses_left"] - 1
    assert ag._use_grant("delete_task", {"run_id": scope}) is None, "the grant does not cover deleting"
    assert ag._use_grant("update_task", {"run_id": "wfr_another_run"}) is None, "another run is not covered"
    ag.revoke_grant(g["grant_id"])


@pytest.mark.parametrize("decided_by", ["friday", "system:expiry", "grant:abc", ""],
                         ids=["friday", "expiry", "grant", "nobody"])
def test_nobody_but_the_owner_mints_a_run_grant(decided_by):
    assert _rg().mint_on_approval(_card("appr_a24_3", decided_by, "wfr_test_a24_3")) is None


def test_a_card_cannot_smuggle_in_other_tools():
    rec = _card("appr_a24_4", "owner", "wfr_test_a24_4")
    rec["payload"]["run_grant"]["tools"] = ["create_task", "send_email"]
    assert _rg().mint_on_approval(rec) is None
