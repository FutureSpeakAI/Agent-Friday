"""Does every trust score show 0.5? Checked end to end, writer to screen.

An earlier evaluation suspected every trust score showed 0.5. Findings:

* NOT a key mismatch: the archiver writes observations under the article's
  bare domain and every reader (score_for, the feed metadata, the
  leaderboard and detail routes) reads the same key; scores move off the
  prior as evidence lands. test_writer_and_readers_agree proves it and passes
  on main 2f88c152a as well.
* TRUE for the Feed: the Feed is archive-backed, and an archived article
  carries the trust snapshot from when it was first archived. The archiver
  archives a tick's articles before that tick's analysis runs, so an outlet's
  first articles were frozen at its starting prior (0.50 for any outlet not
  on a list) and never moved. Red on main 2f88c152a:
  test_the_feed_shows_the_live_score_not_the_first_fetch_snapshot.
"""
from __future__ import annotations

import pytest

from agent_friday import source_trust_graph as stg

OUTLET = "unknown-outlet.test"


@pytest.fixture
def graph(tmp_path, monkeypatch):
    from agent_friday.routes import news as rn
    from agent_friday.services import news_engine as ne
    g = stg.SourceTrustGraph(friday_dir=tmp_path)
    for mod in (ne, rn):
        monkeypatch.setattr(mod, "_HAS_TRUST_GRAPHS", True, raising=False)
        monkeypatch.setattr(mod, "get_source_trust_graph", lambda friday_dir=None: g, raising=False)
    monkeypatch.setattr(ne, "_load_banned_sources", lambda: [])
    monkeypatch.setattr(ne, "_load_boosted_sources", lambda: [])
    monkeypatch.setattr(rn, "_load_banned_sources", lambda: [])
    return g


def _pool(n=6):
    return [{"source": OUTLET, "title": "Court ruling %d published" % i,
             "snippet": "According to the court filing, officials said the ruling stands.",
             "url": "https://%s/story/%d" % (OUTLET, i)} for i in range(n)] + \
           [{"source": "other-outlet.test", "title": "Court ruling %d published" % i,
             "snippet": "According to the court filing, officials said the ruling stands.",
             "url": "https://other-outlet.test/story/%d" % i} for i in range(n)]


def _run_archiver(monkeypatch, pool):
    from agent_friday.services import news_engine as ne
    monkeypatch.setattr(ne, "_gather_front_page_pool", lambda per_cat=14: (pool, None))
    monkeypatch.setattr(ne, "_archive_articles", lambda items: 0)
    ne._news_archiver_tick()


def test_writer_and_readers_agree(graph, client, monkeypatch):
    from agent_friday.services import news_engine as ne
    _run_archiver(monkeypatch, _pool())
    live = graph.score_for(OUTLET)
    assert abs(live - 0.5) > 0.05, "evidence did not move the score off the prior"
    meta = ne._source_trust_meta(OUTLET, banned=set(), boosted=set())
    assert meta["trust_score"] == pytest.approx(live, abs=1e-3)
    board = client.get("/api/source-trust/leaderboard?limit=50").get_json()["leaderboard"]
    row = next(r for r in board if r["domain"] == OUTLET)
    assert row["trust_score"] == pytest.approx(live, abs=1e-3)
    one = client.get("/api/source-trust/" + OUTLET).get_json()
    assert one["source"]["trust_score"] == pytest.approx(live, abs=1e-3)


def test_the_feed_shows_the_live_score_not_the_first_fetch_snapshot(graph, client, monkeypatch):
    from agent_friday.routes import news as rn
    # The article as the archiver stored it on first sight: the outlet's prior.
    snapshot = {"id": "a1", "title": "Court ruling 0 published", "url": "https://%s/story/0" % OUTLET,
                "source": OUTLET, "domain": OUTLET, "category": "Politics",
                "published_at": "", "fetched_at": "2026-10-01T08:00:00",
                "trust": "yellow", "trust_score": 0.5,
                "trust_dims": {d: 0.5 for d in stg.DIMENSIONS}}
    monkeypatch.setattr(rn, "_read_archive", lambda **kw: [dict(snapshot)])
    _run_archiver(monkeypatch, _pool())
    live = round(graph.score_for(OUTLET), 3)
    assert abs(live - 0.5) > 0.05
    items = client.get("/api/news/archive?limit=10").get_json()["items"]
    assert items[0]["trust_score"] == pytest.approx(live, abs=1e-3), items[0]["trust_score"]
    assert items[0]["trust_dims"]["prediction_accuracy"] is None
