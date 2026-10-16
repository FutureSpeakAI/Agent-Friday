"""The owner's Boost/Ban is a real, visible input to an outlet's trust score.

Red on main 2f88c152a: record_user_action stored banned/boosted in
user_actions and nothing read them; score_for, the feed metadata and the Trust
view showed the same number before and after a Ban.
"""
from __future__ import annotations

import pytest

from agent_friday import source_trust_graph as stg
from agent_friday.source_trust_graph import SourceTrustGraph


BAN_CEILING = 0.15   # stg.OWNER_BAN_CEILING
BOOST_FLOOR = 0.70   # stg.OWNER_BOOST_FLOOR


@pytest.fixture
def graph(tmp_path):
    return SourceTrustGraph(friday_dir=tmp_path)


def test_ban_pins_the_score_low_and_unban_reverses_it(graph):
    graph.record_article_seen("reuters.com")
    before = graph.score_for("reuters.com")
    dims_before = graph.dimensions_for("reuters.com")
    assert before >= 0.7
    graph.record_user_action("reuters.com", "ban")
    assert graph.score_for("reuters.com") <= BAN_CEILING
    # The preference is its own line, not hidden inside a dimension.
    assert graph.dimensions_for("reuters.com") == dims_before
    assert graph.leaderboard()[0]["owner_preference"] == "banned"
    assert stg.OWNER_BAN_CEILING == BAN_CEILING
    ex = graph.explain("reuters.com")
    assert ex["owner_preference"]["state"] == "banned"
    assert ex["owner_preference"]["set_by"] == "owner"
    assert ex["evidence_score"] == pytest.approx(before, abs=1e-3)
    graph.record_user_action("reuters.com", "unban")
    assert graph.score_for("reuters.com") == pytest.approx(before, abs=1e-4)
    assert graph.explain("reuters.com")["owner_preference"] is None


def test_boost_raises_the_score_and_unboost_reverses_it(graph):
    graph.record_article_seen("unknown-outlet.test")
    before = graph.score_for("unknown-outlet.test")
    graph.record_user_action("unknown-outlet.test", "boost")
    after = graph.score_for("unknown-outlet.test")
    assert after > before and after >= BOOST_FLOOR
    assert graph.leaderboard()[0]["owner_preference"] == "boosted"
    graph.record_user_action("unknown-outlet.test", "unboost")
    assert graph.score_for("unknown-outlet.test") == pytest.approx(before, abs=1e-4)


def test_ban_after_boost_then_unban_leaves_no_boost_behind(graph):
    graph.record_user_action("outlet.test", "boost")
    graph.record_user_action("outlet.test", "ban")
    graph.record_user_action("outlet.test", "unban")
    assert graph.score_for("outlet.test") == pytest.approx(0.5, abs=1e-4)
    assert graph.leaderboard()[0]["owner_preference"] is None


def test_the_feed_metadata_shows_the_owner_preference(graph, monkeypatch):
    from agent_friday.services import news_engine as ne
    monkeypatch.setattr(ne, "_HAS_TRUST_GRAPHS", True)
    monkeypatch.setattr(ne, "get_source_trust_graph", lambda friday_dir=None: graph)
    ne._mirror_source_action("reuters.com", "ban")
    meta = ne._source_trust_meta("reuters.com", banned={"reuters.com"}, boosted=set())
    assert meta["trust_score"] <= BAN_CEILING
    assert meta["trust_owner"] == "banned"


def test_the_archiver_applies_the_owner_lists_even_if_the_mirror_was_missed(graph, monkeypatch):
    """Bans made before the mirror existed, or a mirror write that failed,
    still reach the score: the archiver hands the lists to the analysis."""
    from agent_friday.services import news_engine as ne
    pool = [{"source": "reuters.com", "title": "Story", "url": "https://reuters.com/s"},
            {"source": "small.test", "title": "Story", "url": "https://small.test/s"}]
    monkeypatch.setattr(ne, "_gather_front_page_pool", lambda per_cat=14: (pool, None))
    monkeypatch.setattr(ne, "_archive_articles", lambda items: 0)
    monkeypatch.setattr(ne, "_cluster_articles", lambda items, min_sources=2: [])
    monkeypatch.setattr(ne, "_HAS_TRUST_GRAPHS", True)
    monkeypatch.setattr(ne, "get_source_trust_graph", lambda friday_dir=None: graph)
    monkeypatch.setattr(ne, "_load_banned_sources", lambda: ["reuters.com"])
    monkeypatch.setattr(ne, "_load_boosted_sources", lambda: ["small.test"])
    ne._news_archiver_tick()
    assert graph.score_for("reuters.com") <= BAN_CEILING
    assert graph.score_for("small.test") >= BOOST_FLOOR
    # Lifting them in the lists lifts them in the score on the next pass.
    monkeypatch.setattr(ne, "_load_banned_sources", lambda: [])
    monkeypatch.setattr(ne, "_load_boosted_sources", lambda: [])
    ne._news_archiver_tick()
    assert graph.score_for("reuters.com") >= 0.7
    assert graph.score_for("small.test") < BOOST_FLOOR
