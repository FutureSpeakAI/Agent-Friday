"""The workflow proposal — Friday proposes, Stephen disposes.

Stephen, 2026-08-15: "The user decides when a task is heavy." These pin that
Friday's judgement only ever raises the question, that the three executions he
named are the menu, that the vault constrains the MENU rather than being
applied silently afterwards, and that "choose for me" says what it chose.
"""
from __future__ import annotations

import time

import pytest

from agent_friday.services import work_queue as wq
from agent_friday.services import workflow_plan as wp


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(wq, "queue_path",
                        lambda: tmp_path / "wq" / "queue.json")
    monkeypatch.setattr(wp, "proposals_dir", lambda: tmp_path / "props")
    wq.mark_active()
    yield


def _tasks(n=2, vault=False, cls="heavy"):
    return [{"title": "step %d" % i, "detail": "do a thing " * 40,
             "cls": cls, "touches_vault": vault and i == 0}
            for i in range(n)]


# ── the menu ─────────────────────────────────────────────────────────────────

def test_the_three_options_are_the_ones_stephen_named():
    p = wp.build("refactor", _tasks())
    assert p["options"] == ["when_away", "now_local", "now_cloud"]


def test_vault_work_removes_the_cloud_option_and_says_why():
    """The router forces vault turns local regardless of configured mode, so
    offering a cloud run would be a lie in the interface."""
    p = wp.build("read my notes", _tasks(vault=True))
    assert "now_cloud" not in p["options"]
    blocked = [b for b in p["blocked"] if b["option"] == "now_cloud"][0]
    assert "never leaves this machine" in blocked["reason"]
    assert "step 0" in blocked["reason"], "name the task, not just the rule"


def test_choosing_a_blocked_option_is_refused_with_its_reason():
    p = wp.build("read my notes", _tasks(vault=True))
    out = wp.decide(p["id"], "now_cloud")
    assert out["ok"] is False and out["blocked"] is True
    assert "vault-tier" in out["error"]


def test_a_per_task_cloud_choice_on_vault_work_is_refused_not_downgraded():
    """He asked for something specific and is owed either it or a reason."""
    p = wp.build("mixed", _tasks(vault=True))
    vault_task = [t for t in p["tasks"] if t["touches_vault"]][0]
    out = wp.decide(p["id"], "now_local",
                    per_task={vault_task["id"]: "now_cloud"})
    assert out["ok"] is True
    assert out["refused"] and out["refused"][0]["task"] == vault_task["title"]
    queued = [wq.get(i) for i in out["enqueued"]]
    assert all(i["disposition"] != "now_cloud" for i in queued)


# ── choose for me ────────────────────────────────────────────────────────────

def test_choose_for_me_records_that_friday_chose_and_why():
    """"Friday chose" is not something Stephen can disagree with. "Friday chose
    local because two steps read your vault" is."""
    p = wp.build("thing", _tasks(vault=True))
    out = wp.decide(p["id"], choose_for_me=True)
    d = out["proposal"]["decision"]
    assert d["chosen_by"] == "friday"
    assert d["why"] and "vault" in d["why"]


def test_choose_for_me_keeps_vault_work_local():
    p = wp.build("thing", _tasks(vault=True))
    assert p["recommendation"]["execution"] == "now_local"


def test_choose_for_me_parks_long_work_when_the_machine_is_quiet():
    wq.mark_active(time.time() - (wq.AWAY_AFTER_S + 60))
    p = wp.build("big job", _tasks(n=6))
    assert p["recommendation"]["execution"] == "when_away"
    assert "away" in p["recommendation"]["why"]


def test_choose_for_me_prefers_local_for_short_work():
    p = wp.build("quick", [{"title": "t", "detail": "hi", "cls": "reflex"}])
    assert p["recommendation"]["execution"] == "now_local"


def test_a_recommendation_always_carries_its_reasoning():
    for tasks in (_tasks(1), _tasks(6), _tasks(2, vault=True)):
        assert wp.build("x", tasks)["recommendation"]["why"]


# ── the decision reaches the queue ───────────────────────────────────────────

def test_deciding_enqueues_every_task_with_the_chosen_disposition():
    p = wp.build("job", _tasks(3))
    out = wp.decide(p["id"], "when_away")
    assert len(out["enqueued"]) == 3
    assert all(wq.get(i)["disposition"] == "when_away" for i in out["enqueued"])
    assert all(wq.get(i)["workflow_id"] == p["id"] for i in out["enqueued"])


def test_a_proposal_is_decided_once():
    p = wp.build("job", _tasks(1))
    assert wp.decide(p["id"], "now_local")["ok"] is True
    assert wp.decide(p["id"], "now_local")["ok"] is False


def test_a_pending_proposal_is_listed_until_it_is_decided():
    p = wp.build("job", _tasks(1))
    assert [x["id"] for x in wp.listing("pending")] == [p["id"]]
    wp.decide(p["id"], "now_local")
    assert wp.listing("pending") == []


# ── estimates ────────────────────────────────────────────────────────────────

def test_a_heavy_estimate_includes_the_wake_cost():
    """53.5 s of the estimate is the model turning up. Hiding that would make
    "run it now, locally" look far cheaper than it is."""
    e = wp.estimate_task("x" * 2000, "heavy")
    assert e["est_s_local"] > wp.HEAVY_WAKE_S


def test_heaviness_only_ever_raises_the_question():
    assert wp.looks_heavy("refactor every call site") is True
    assert wp.looks_heavy("what time is it") is False
    # And nothing in this module acts on it — build() takes the classes it is
    # given. The signal reaches Stephen, not the scheduler.
