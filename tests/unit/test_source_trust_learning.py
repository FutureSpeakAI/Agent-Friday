"""Source trust learns from evidence, not from repetition or from agreement.

Red on main 7c49be86 by reading of the code and confirmed in the red run:
  * the archiver hands the whole pool to analyze_fetch every five minutes and
    nothing deduped, so the same headline was observed again every tick;
  * a 1-of-8 sentiment dissent cost the dissenter factual_accuracy (0.25)
    unless it quoted a primary document;
  * any headline containing "Correction" raised correction_behavior (0.95),
    with no prior article and no cap.
"""
from __future__ import annotations

import pytest

from agent_friday.source_trust_graph import SourceTrustGraph


def _art(source, title="", snippet="", sentiment=None, url="", category=""):
    return {"source": source, "title": title, "snippet": snippet,
            "sentiment": sentiment, "url": url, "category": category}


@pytest.fixture
def graph(tmp_path):
    return SourceTrustGraph(friday_dir=tmp_path)


def _obs(graph, domain):
    rec = graph.get(domain) or {}
    return list(rec.get("observations") or [])


class TestDedupe:
    def test_the_same_pool_twice_adds_nothing_the_second_time(self, graph):
        pool = [_art("a.test", title="Court filing shows the merger terms",
                     snippet="According to the filing at https://example.test/doc the terms changed.",
                     url="https://a.test/merger"),
                _art("b.test", title="Why the merger matters", url="https://b.test/opinion/merger",
                     category="opinion")]
        first = graph.analyze_fetch(pool, [])
        n1 = len(_obs(graph, "a.test")) + len(_obs(graph, "b.test"))
        assert n1 >= 2 and first["attribution"] == 1 and first["opinion"] == 1
        second = graph.analyze_fetch(pool, [])
        n2 = len(_obs(graph, "a.test")) + len(_obs(graph, "b.test"))
        assert n2 == n1, "the same articles were observed again"
        assert second["attribution"] == 0 and second["opinion"] == 0

    def test_a_different_article_still_counts(self, graph):
        graph.analyze_fetch([_art("a.test", title="One", snippet="per https://x.test/a", url="https://a.test/1")], [])
        graph.analyze_fetch([_art("a.test", title="Two", snippet="per https://x.test/b", url="https://a.test/2")], [])
        assert len([o for o in _obs(graph, "a.test") if o["type"] == "attribution_present"]) == 2

    def test_the_cluster_boost_is_once_per_article(self, graph):
        arts = [_art("a.test", snippet="court filing shows the terms", url="https://a.test/x"),
                _art("b.test", snippet="court filing shows the terms", url="https://b.test/x")]
        cluster = {"articles": arts, "source_count": 2, "headline": "Court filing"}
        graph.analyze_fetch([], [cluster])
        graph.analyze_fetch([], [cluster])
        assert len([o for o in _obs(graph, "a.test") if o["type"] == "primary_corroborated"]) == 1


class TestNoPackPenalty:
    @pytest.mark.parametrize("snippet", ["Officials said the plan is bad.",
                                         "A court filing shows the plan is bad."])
    def test_a_lone_dissent_in_a_large_cluster_changes_nothing(self, graph, snippet):
        arts = [_art(f"s{i}.test", title="Plan announced", sentiment="positive",
                     url=f"https://s{i}.test/plan") for i in range(7)]
        arts.append(_art("lonely.test", title="Plan announced", sentiment="negative",
                         snippet=snippet, url="https://lonely.test/plan"))
        cluster = {"articles": arts, "source_count": 8, "headline": "Plan announced"}
        before = dict((graph.get("lonely.test") or {}).get("scores") or {})
        summary = graph.analyze_fetch(arts, [cluster])
        rec = graph.get("lonely.test") or {}
        types = {o["type"] for o in rec.get("observations") or []}
        assert "minority_claim" not in types and "narrative_break" not in types
        assert summary.get("minority_claims", 0) == 0 and summary.get("independence", 0) == 0
        after = rec.get("scores") or {}
        for dim in ("factual_accuracy", "narrative_independence"):
            assert after.get(dim) == before.get(dim, after.get(dim))


class TestCorrections:
    def test_ten_corrections_with_no_prior_article_count_for_nothing(self, graph):
        pool = [_art("a.test", title=f"Correction: earlier article {i} had errors",
                     url=f"https://a.test/corr{i}") for i in range(10)]
        summary = graph.analyze_fetch(pool, [])
        assert summary["corrections"] == 0
        assert not [o for o in _obs(graph, "a.test") if o["type"] == "correction_issued"]

    def test_a_correction_of_an_article_the_source_ran_counts(self, graph):
        graph.analyze_fetch([_art("a.test", title="Council approves the riverside housing plan",
                                  url="https://a.test/riverside")], [])
        summary = graph.analyze_fetch([_art("a.test", title="Correction: council riverside housing plan vote count",
                                            url="https://a.test/riverside-correction")], [])
        assert summary["corrections"] == 1
        assert len([o for o in _obs(graph, "a.test") if o["type"] == "correction_issued"]) == 1

    def test_at_most_two_corrections_a_week_count(self, graph):
        for i in range(4):
            graph.analyze_fetch([_art("a.test", title=f"Story {i}: harbour wall repairs budget approved",
                                      url=f"https://a.test/story{i}")], [])
        for i in range(4):
            graph.analyze_fetch([_art("a.test", title=f"Correction: story {i} harbour wall repairs budget",
                                      url=f"https://a.test/corr{i}")], [])
        assert len([o for o in _obs(graph, "a.test") if o["type"] == "correction_issued"]) == 2
