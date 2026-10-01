"""Learning never changes how Friday behaves on its own (NS-10.6-2, NS-13.7-3).

Amendment A2: a change to Friday's personality or behaviour (tone, verbosity,
proactivity, vocabulary, rituals) arrives as a proposal the owner accepts,
edits, rejects or defers. Nothing about who Friday is changes silently. The
visual avatar evolution is the one exception and is not covered here.

Two paths learn behaviour automatically:

* ``learning_loop`` mines heuristics that are injected into every system
  prompt. A heuristic that clears the bar raises an approval card carrying a
  diff, its evidence and a preview of the prompt block; only the owner's
  decision adds it to, or removes it from, the prompt.
* ``user_model.observe_message`` adapts tone, verbosity and expertise from
  how the owner writes. That adaptation is ephemeral: it lives in process
  memory for the running session and is never written to the durable store.
"""
from __future__ import annotations

import sqlite3

import pytest

from agent_friday.services import approvals
from agent_friday.services import learning_loop as ll
from agent_friday.services import user_model as um


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(ll, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(ll, "DB_PATH", tmp_path / "learning.db")
    monkeypatch.setattr(um, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(um, "DB_PATH", tmp_path / "user_model.db")
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    um.forget()
    yield
    um.forget()


# ── learning_loop ────────────────────────────────────────────────────────────

def _ready_skill(task="code", approach="test_first"):
    for i in range(5):
        ll.observe(task, f"prompt {i}", approach=approach, success=True,
                   satisfaction=0.9)
    sid = ll.mine_candidates()[0]["skill_id"]
    for _ in range(4):
        ll.record_trial(sid, True, 0.9)
    return sid


def _cards():
    return [c for c in approvals.list_approvals()
            if c.get("kind") == ll.PROPOSAL_KIND]


def _status(sid):
    conn = ll._connect()
    row = conn.execute("SELECT status FROM skills WHERE skill_id=?", (sid,)).fetchone()
    conn.close()
    return row[0]


def test_promotion_raises_a_proposal_and_changes_no_prompt():
    sid = _ready_skill()
    changes = ll.promote(threshold=0.5, min_trials=3)
    assert all(c["to"] != "active" for c in changes)
    assert ll.active_skills() == []
    assert ll.render_heuristics_prompt() == ""
    cards = _cards()
    assert len(cards) == 1
    card = cards[0]
    assert card["status"] == "pending"
    p = card["payload"]
    assert p["skill_id"] == sid and p["change"] == "add"
    assert p["diff"].startswith("+ ")
    assert p["evidence"]["trials"] == 4
    assert p["pattern"] in p["preview"]


def test_weekly_epoch_activates_nothing():
    for i in range(6):
        ll.observe("code", f"prompt {i}", approach="tdd", success=True,
                   satisfaction=0.9)
    out = ll.run_epoch()
    assert out["ok"] is True
    assert ll.active_skills() == []
    assert len(_cards()) == 1


def test_promotion_is_idempotent_while_the_card_is_pending():
    _ready_skill()
    ll.promote(threshold=0.5, min_trials=3)
    ll.promote(threshold=0.5, min_trials=3)
    assert len(_cards()) == 1


def test_owner_accept_adds_the_heuristic():
    sid = _ready_skill()
    ll.promote(threshold=0.5, min_trials=3)
    res = ll.decide_proposal(sid, "accept")
    assert res["ok"] is True
    assert sid in [s["skill_id"] for s in ll.active_skills()]
    assert "test first" in ll.render_heuristics_prompt()


def test_approving_the_card_directly_adds_the_heuristic():
    sid = _ready_skill()
    ll.promote(threshold=0.5, min_trials=3)
    approvals.decide(_cards()[0]["approval_id"], "approve")
    assert sid in [s["skill_id"] for s in ll.active_skills()]


def test_owner_edit_adds_the_owners_wording():
    sid = _ready_skill()
    ll.promote(threshold=0.5, min_trials=3)
    res = ll.decide_proposal(sid, "edit", pattern="Write the failing test first.")
    assert res["ok"] is True
    assert ll.render_heuristics_prompt() == "• Write the failing test first."


def test_owner_reject_keeps_it_out_and_it_is_not_asked_again():
    sid = _ready_skill()
    ll.promote(threshold=0.5, min_trials=3)
    assert ll.decide_proposal(sid, "reject")["ok"] is True
    assert ll.active_skills() == []
    assert _status(sid) == "rejected"
    ll.promote(threshold=0.5, min_trials=3)
    assert len(_cards()) == 1


def test_owner_defer_leaves_the_proposal_pending():
    sid = _ready_skill()
    ll.promote(threshold=0.5, min_trials=3)
    assert ll.decide_proposal(sid, "defer")["ok"] is True
    assert ll.active_skills() == []
    assert _cards()[0]["status"] == "pending"
    assert [p["skill_id"] for p in ll.pending_proposals()] == [sid]


def test_decay_proposes_removal_instead_of_retiring():
    sid = _ready_skill()
    ll.promote(threshold=0.5, min_trials=3)
    ll.decide_proposal(sid, "accept")
    for _ in range(20):
        ll.record_trial(sid, success=False, satisfaction=0.05)
    changes = ll.promote(threshold=0.5, retire=0.4)
    assert all(c["to"] != "retired" for c in changes)
    assert sid in [s["skill_id"] for s in ll.active_skills()]
    removal = [c for c in _cards() if c["payload"]["change"] == "remove"]
    assert len(removal) == 1 and removal[0]["status"] == "pending"
    assert removal[0]["payload"]["diff"].startswith("- ")
    ll.decide_proposal(sid, "accept")
    assert ll.active_skills() == []
    assert _status(sid) == "retired"


def test_unknown_decision_is_refused():
    sid = _ready_skill()
    ll.promote(threshold=0.5, min_trials=3)
    assert ll.decide_proposal(sid, "maybe")["ok"] is False
    assert ll.decide_proposal("no-such-skill", "accept")["ok"] is False


# ── user_model ───────────────────────────────────────────────────────────────

def _durable_trait_keys():
    conn = sqlite3.connect(str(um.DB_PATH))
    rows = conn.execute("SELECT key FROM traits").fetchall()
    conn.close()
    return {r[0] for r in rows}


def test_observed_style_is_not_written_to_the_durable_store():
    for _ in range(8):
        um.observe_message(
            "lol yeah gonna refactor the async function and deploy, thanks!",
            role="user")
    keys = _durable_trait_keys()
    assert not any(k.startswith(("comm.", "expertise.")) for k in keys), keys


def test_observed_style_adapts_the_running_session_only():
    for _ in range(8):
        um.observe_message("lol yeah gonna grab coffee, thanks!", role="user")
    assert um.get_trait("comm.formality") < 0.5
    assert "casual" in um.render_user_model_prompt()
    um.reset_session_adaptation()
    assert um.get_trait("comm.formality") is None
    assert um.render_user_model_prompt() == ""


def test_an_owner_set_trait_is_durable():
    um.set_trait("comm.formality", 0.9)
    um.reset_session_adaptation()
    assert um.get_trait("comm.formality") == 0.9
    assert "comm.formality" in _durable_trait_keys()
