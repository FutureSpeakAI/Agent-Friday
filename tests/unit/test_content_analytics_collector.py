"""Unit tests for services/analytics_collector.py — the §8 collector.

Hermetic: content store on tmp, mock platform adapter as the metrics source,
economy.earn stubbed. Covers the stateless decaying poll plan (§8.1), snapshot
storage + budget slide, engagement→ψ threshold minting (idempotent under
re-poll storms, daily-capped — §8.7), the weekly best-times/attribute-lift
job feeding the §6.4 learned layer, and the get_insights envelope.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_friday.services import analytics_collector as ac      # noqa: E402
from agent_friday.services import content_pipeline as cpl        # noqa: E402
from agent_friday.services import platforms as preg              # noqa: E402
from agent_friday.services.platforms import base as pbase        # noqa: E402

PAST = "2026-07-01T09:00:00Z"


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(cpl, "DB_PATH", tmp_path / "content_pipeline.db")
    monkeypatch.setattr(cpl, "CONTENT_DIR", tmp_path / "content")
    monkeypatch.setattr(cpl, "PUBLISH_LOG", tmp_path / "content" / "publish_log.jsonl")
    monkeypatch.setattr(pbase, "PLATFORMS_DIR", tmp_path / "platforms")
    monkeypatch.setattr(pbase, "BUDGET_PATH", tmp_path / "platforms" / "rate_budget.json")
    monkeypatch.setattr(preg, "CONFIG_PATH", tmp_path / "platforms.json")
    preg._reset_for_tests()
    earned = []
    try:
        from agent_friday.services import economy
        monkeypatch.setattr(economy, "earn",
                            lambda agent, amount, reason: earned.append((amount, reason)))
    except Exception:
        pass
    yield {"earned": earned}
    preg._reset_for_tests()


@pytest.fixture
def earned(_env):
    return _env["earned"]


def _confirmed_post(platform="mock", pid="mock-77", published_at=None):
    """A PUBLISHED post whose single target is CONFIRMED with `pid`."""
    res = cpl.create_post(title="T", body="Body.", platforms=[platform],
                          schedule=cpl.new_schedule_config(publish_at=PAST))
    post = cpl.schedule_post(res["post"]["id"])["post"]
    t = cpl.claim_due_targets(now="2026-07-04T12:00:00Z")["targets"][0]
    cpl.set_target_status(t["id"], "CONFIRMED",
                          post_url=f"mock://post/{pid}", platform_post_id=pid)
    if published_at:
        with cpl._connect() as con:
            con.execute("UPDATE posts SET published_at=? WHERE id=?",
                        (published_at, post["id"]))
    return cpl.get_post(post["id"])["post"]


def _mock_adapter(pid="mock-77", metrics=None):
    a = preg.get_adapter("mock")
    a.reset()
    a.published.append({"platform_post_id": pid})
    if metrics:
        a.set_metrics(pid, metrics)
    return a


# ── poll plan (§8.1) ──────────────────────────────────────────────────────────

def test_poll_due_follows_decaying_plan():
    now = datetime.now(timezone.utc)
    post = _confirmed_post(published_at=_iso(now - timedelta(hours=2)))
    tid = post["targets"][0]["id"]
    assert len(ac.poll_due()) == 1                 # +1h step due (0 snaps)
    cpl.insert_engagement_snapshot(tid, {"likes": 1})
    assert ac.poll_due() == []                     # +6h step not due yet
    cpl.insert_engagement_snapshot(tid, {"likes": 2})
    # +24h step: due only when we pretend a day has passed
    assert len(ac.poll_due(now=_iso(now + timedelta(days=2)))) == 1


def test_poll_plan_exhausts_after_six_snapshots():
    now = datetime.now(timezone.utc)
    post = _confirmed_post(published_at=_iso(now - timedelta(days=90)))
    tid = post["targets"][0]["id"]
    for i in range(len(ac.POLL_PLAN_S)):
        cpl.insert_engagement_snapshot(tid, {"likes": i})
    assert ac.poll_due() == []                     # plan done — polls stop


def test_collect_stores_snapshot_from_adapter():
    now = datetime.now(timezone.utc)
    _mock_adapter(metrics={"likes": 7, "impressions": 120})
    post = _confirmed_post(published_at=_iso(now - timedelta(hours=2)))
    res = ac.collect()
    assert res["ok"] and res["polled"] == 1
    snaps = cpl.get_snapshots(post_id=post["id"])["snapshots"]
    assert len(snaps) == 1 and snaps[0]["metrics"]["likes"] == 7
    # rollup refreshed on the post
    assert cpl.get_post(post["id"])["post"]["analytics"]["likes"] == 7


def test_collect_slides_when_budget_tight():
    now = datetime.now(timezone.utc)
    adapter = _mock_adapter(metrics={"likes": 1})
    adapter.configure({"daily_post_limit": 1})
    adapter.consume_budget(1)
    post = _confirmed_post(published_at=_iso(now - timedelta(hours=2)))
    res = ac.collect()
    assert res["ok"] and res["slid"] == 1 and res["polled"] == 0
    assert cpl.get_snapshots(post_id=post["id"])["snapshots"] == []


# ── engagement → ψ (§8.7) ─────────────────────────────────────────────────────

def test_threshold_psi_minted_once(earned):
    now = datetime.now(timezone.utc)
    _mock_adapter(metrics={"likes": 25, "shares": 6})
    post = _confirmed_post(published_at=_iso(now - timedelta(hours=2)))
    assert ac.collect()["polled"] == 1
    # likes 10+20, shares 5 → three PSI_LIKE mints
    assert len(earned) == 3
    # re-poll storm (manual refreshes) never double-mints
    tid = post["targets"][0]["id"]
    for _ in range(5):
        ac._award_engagement_psi({"id": tid, "platform": "mock"},
                                 {"likes": 25, "shares": 6})
    assert len(earned) == 3
    # growth mints only the newly crossed threshold
    ac._award_engagement_psi({"id": tid, "platform": "mock"},
                             {"likes": 32, "shares": 6})
    assert len(earned) == 4


def test_daily_cap_bounds_minting(monkeypatch, earned):
    from agent_friday.services.economy import PSI_LIKE
    monkeypatch.setattr(ac, "_psi_daily_cap_mpsi", lambda: PSI_LIKE * 2)
    now = datetime.now(timezone.utc)
    _mock_adapter(metrics={"likes": 100})          # 10 thresholds crossed
    _confirmed_post(published_at=_iso(now - timedelta(hours=2)))
    ac.collect()
    assert len(earned) == 2                        # capped at 2ψ-equivalents


# ── weekly insights (§8.4) → the §6.4 learned layer ──────────────────────────

def test_weekly_insights_builds_best_times_and_cards(tmp_path):
    now = datetime.now(timezone.utc)
    for i in range(3):
        post = _confirmed_post(pid=f"mock-{i}",
                               published_at=_iso(now - timedelta(days=1)))
        tid = post["targets"][0]["id"]
        cpl.insert_engagement_snapshot(
            tid, {"likes": 10 + i, "impressions": 100})
    res = ac.weekly_insights()
    assert res["ok"], res
    rows = cpl.get_best_times(platform="mock")["best_times"]
    assert rows and rows[0]["samples"] == 3
    assert 0 < rows[0]["score"] <= 1
    got = ac.get_insights()
    assert got["ok"] and got["generated_at"]
    assert isinstance(got["insights"], list)


def test_get_insights_empty_deck():
    res = ac.get_insights()
    assert res == {"ok": True, "insights": [], "generated_at": None}


def test_start_registers_builtins():
    res = ac.start()
    assert res["ok"], res
    assert set(res["registered"]) == {"content_analytics", "content_insights"}
