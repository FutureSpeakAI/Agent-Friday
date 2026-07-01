"""Unit tests for services/learning_loop.py — local closed-loop learning."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_friday.services import learning_loop as ll  # noqa: E402


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(ll, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(ll, "DB_PATH", tmp_path / "learning.db")
    yield


def _seed_successful(task="code", approach="test_first", n=5, success=True):
    for i in range(n):
        ll.observe(task, f"prompt {i}", approach=approach, success=success,
                   satisfaction=0.9 if success else 0.1)


def test_observe_records():
    r = ll.observe("code", "do a thing", approach="tdd", success=True)
    assert r["ok"] is True
    assert ll.state()["observations"] == 1


def test_mine_candidates_creates_skill():
    _seed_successful(n=5)
    created = ll.mine_candidates(min_success=0.7, min_samples=3)
    assert len(created) == 1
    assert created[0]["samples"] == 5
    assert ll.state()["counts"]["candidate"] == 1


def test_mine_skips_low_success():
    _seed_successful(n=5, success=False)
    created = ll.mine_candidates(min_success=0.7, min_samples=3)
    assert created == []


def test_mine_skips_too_few_samples():
    _seed_successful(n=2)
    created = ll.mine_candidates(min_success=0.7, min_samples=3)
    assert created == []


def test_mine_is_idempotent():
    _seed_successful(n=5)
    ll.mine_candidates()
    again = ll.mine_candidates()
    assert again == []  # pattern already exists


def test_wilson_lower_bound_monotonic():
    lo = ll._wilson_lower_bound(1, 2)
    hi = ll._wilson_lower_bound(10, 10)
    assert 0.0 <= lo < hi <= 1.0


def test_record_trial_updates_score():
    _seed_successful(n=5)
    sid = ll.mine_candidates()[0]["skill_id"]
    for _ in range(5):
        ll.record_trial(sid, success=True, satisfaction=0.9)
    assert ll.score_skill(sid) > 0.5


def test_promote_candidate_to_active():
    _seed_successful(n=5)
    sid = ll.mine_candidates()[0]["skill_id"]
    for _ in range(4):
        ll.record_trial(sid, True, 0.9)
    changes = ll.promote(threshold=0.5, min_trials=3)
    to_states = {c["to"] for c in changes}
    assert "active" in to_states
    assert any(s["skill_id"] == sid for s in ll.active_skills())


def test_active_skills_respects_max():
    # create several skills across task types, all active, and confirm the
    # active_skills() query never returns more than max_active_skills.
    for t in ("a", "b", "c"):
        for i in range(4):
            ll.observe(t, f"p{i}", approach="x", success=True, satisfaction=0.95)
    ll.mine_candidates()
    # force-activate all
    conn = ll._connect()
    conn.execute("UPDATE skills SET status='active', score=0.9")
    conn.commit()
    conn.close()
    got = ll.active_skills()
    assert len(got) <= ll._max_active()


def test_render_heuristics_prompt():
    _seed_successful(n=5)
    sid = ll.mine_candidates()[0]["skill_id"]
    conn = ll._connect()
    conn.execute("UPDATE skills SET status='active', score=0.9 WHERE skill_id=?", (sid,))
    conn.commit()
    conn.close()
    prompt = ll.render_heuristics_prompt()
    assert prompt.startswith("•")
    assert "code" in prompt


def test_run_epoch_end_to_end():
    _seed_successful(n=6)
    out = ll.run_epoch()
    assert out["ok"] is True
    assert out["mined"] >= 1


def test_retire_low_scoring_active():
    _seed_successful(n=5)
    sid = ll.mine_candidates()[0]["skill_id"]
    conn = ll._connect()
    conn.execute("UPDATE skills SET status='active' WHERE skill_id=?", (sid,))
    conn.commit()
    conn.close()
    for _ in range(6):
        ll.record_trial(sid, success=False, satisfaction=0.05)
    changes = ll.promote()
    assert any(c["to"] == "retired" for c in changes)


def test_mine_requires_distinct_prompts():
    # Anti-flood: 5 observations that all share ONE prompt have only 1 distinct
    # prompt_hash (< min_distinct) and must NOT mint a promotable skill.
    for _ in range(5):
        ll.observe("code", "same prompt", approach="tdd", success=True, satisfaction=0.9)
    assert ll.mine_candidates() == []


def test_mine_dedup_pattern_unique(tmp_path):
    # UNIQUE index on pattern: even a direct second mine can't duplicate.
    _seed_successful(n=5)
    first = ll.mine_candidates()
    assert len(first) == 1
    # a raw INSERT of the same pattern is rejected by the unique index
    conn = ll._connect()
    import sqlite3
    pat = first[0]["pattern"]
    dup_ok = True
    try:
        conn.execute(
            "INSERT INTO skills(skill_id,name,task_type,created_ts,pattern,status,"
            "score,trials,wins,source_obs_json) VALUES('x','x','code',0,?, 'candidate',0,0,0,'[]')",
            (pat,))
        conn.commit()
    except sqlite3.IntegrityError:
        dup_ok = False
    conn.close()
    assert dup_ok is False
