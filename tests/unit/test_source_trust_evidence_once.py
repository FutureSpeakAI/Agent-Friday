"""Each (article, check) is scored once, however the feed dresses it up.

Field log: one outlet had six headlines each logged five or more times on one
day, and with the 400-observation cap the repeats pushed older evidence out.
Red on main 2f88c152a: the article key was the raw URL, so a link that came
back with a different utm_* tag or #fragment was a new article; a headline with
no URL was keyed on its exact lower-cased text, so a punctuation change was a
new article; and the key was per outcome, so an article first scored
"attribution absent" was scored again as "attribution present" when its snippet
grew. Duplicates already on disk were never cleaned up.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from agent_friday import source_trust_graph as stg
from agent_friday.source_trust_graph import SourceTrustGraph

ATTRIBUTED = "Officials said the vote was moved, according to a statement."


@pytest.fixture
def graph(tmp_path):
    return SourceTrustGraph(friday_dir=tmp_path)


def _obs(graph, domain, typ=None):
    rec = graph.get(domain) or {}
    return [o for o in rec.get("observations") or [] if typ is None or o.get("type") == typ]


def test_tracking_params_and_fragments_do_not_make_a_new_article(graph):
    base = "https://www.outlet.test/news/vote-moved"
    variants = [base, base + "?utm_source=newsletter&utm_medium=email",
                base + "#comments", base + "/?fbclid=abc123",
                "http://outlet.test/news/vote-moved?utm_campaign=x#top"]
    for url in variants:
        graph.analyze_fetch([{"source": "outlet.test", "title": "Vote moved",
                              "snippet": ATTRIBUTED, "url": url}], [])
    assert len(_obs(graph, "outlet.test", "attribution_present")) == 1


def test_a_real_query_parameter_still_names_a_different_article(graph):
    for url in ("https://outlet.test/story?id=1", "https://outlet.test/story?id=2"):
        graph.analyze_fetch([{"source": "outlet.test", "title": "Story",
                              "snippet": ATTRIBUTED, "url": url}], [])
    assert len(_obs(graph, "outlet.test", "attribution_present")) == 2


def test_without_a_url_the_outlet_and_normalised_headline_are_the_key(graph):
    for title in ("Council approves the budget", "Council approves the budget!",
                  "  council   APPROVES the budget  ", "Council approves the budget."):
        graph.analyze_fetch([{"source": "outlet.test", "title": title,
                              "snippet": ATTRIBUTED, "url": ""}], [])
    assert len(_obs(graph, "outlet.test", "attribution_present")) == 1
    # The same headline from another outlet is that outlet's own article.
    graph.analyze_fetch([{"source": "other.test", "title": "Council approves the budget",
                          "snippet": ATTRIBUTED, "url": ""}], [])
    assert len(_obs(graph, "other.test", "attribution_present")) == 1


def test_a_check_counts_once_even_when_its_outcome_changes(graph):
    url = "https://outlet.test/a/1"
    long_bare = "x " * 100  # a long snippet that names no source
    graph.analyze_fetch([{"source": "outlet.test", "title": "Budget news",
                          "snippet": long_bare, "url": url}], [])
    graph.analyze_fetch([{"source": "outlet.test", "title": "Budget news",
                          "snippet": ATTRIBUTED, "url": url}], [])
    attribution = [o for o in _obs(graph, "outlet.test")
                   if o["dimension"] == "source_attribution"]
    assert len(attribution) == 1, [o["type"] for o in attribution]


def test_the_field_case_six_headlines_five_passes_is_six_observations(graph):
    for p in range(5):
        pool = [{"source": "outlet.test", "title": "Headline %d" % i, "snippet": ATTRIBUTED,
                 "url": "https://outlet.test/h/%d?utm_source=feed%d" % (i, p)}
                for i in range(6)]
        summary = graph.analyze_fetch(pool, [])
        if p:
            assert summary["attribution"] == 0
    assert len(_obs(graph, "outlet.test", "attribution_present")) == 6


def test_a_key_counted_before_the_upgrade_is_still_honoured(graph, tmp_path):
    url = "https://outlet.test/old/story?utm_source=x"
    legacy = hashlib.sha1(url.encode()).hexdigest()[:16]
    graph.observe("outlet.test", "seed", "correction_behavior", 0.5)
    data = json.loads(graph.path.read_text(encoding="utf-8"))
    data["sources"]["outlet.test"]["evidence_keys"] = [legacy + ":attribution_present"]
    graph.path.write_text(json.dumps(data), encoding="utf-8")
    graph.analyze_fetch([{"source": "outlet.test", "title": "Old story",
                          "snippet": ATTRIBUTED, "url": url}], [])
    assert _obs(graph, "outlet.test", "attribution_present") == []


def _old_style_file(graph):
    """A graph as the field wrote it: one headline observed on every pass."""
    obs = []
    for day in ("2026-10-01", "2026-10-02", "2026-10-03"):
        for i in range(6):
            obs.append({"date": day, "type": "attribution_present",
                        "dimension": "source_attribution", "signal": 0.9,
                        "detail": "Headline %d" % i, "counter_sources": [],
                        "signed_by": "local"})
    obs.append({"date": "2026-10-03", "type": "user_observation", "dimension": "factual_accuracy",
                "signal": 0.2, "detail": "Headline 0", "counter_sources": [], "signed_by": "user"})
    obs.append({"date": "2026-10-03", "type": "claim_verified", "dimension": "factual_accuracy",
                "signal": 0.9, "detail": "Headline 0", "counter_sources": [], "signed_by": "peer-key"})
    rec = {"domain": "outlet.test", "name": "outlet.test", "scores": {}, "observations": obs,
           "trust_score": 0.5, "article_count": 18, "first_seen": "2026-10-01",
           "last_updated": "2026-10-03", "user_actions": {}}
    graph.friday_dir.mkdir(parents=True, exist_ok=True)
    graph.path.write_text(json.dumps({"sources": {"outlet.test": rec},
                                      "meta": {"version": "1.0"}}), encoding="utf-8")


def test_existing_duplicate_evidence_is_cleaned_once_keeping_the_earliest(graph):
    _old_style_file(graph)
    obs = _obs(graph, "outlet.test")
    attr = [o for o in obs if o["type"] == "attribution_present"]
    assert len(attr) == 6
    assert {o["date"] for o in attr} == {"2026-10-01"}, "the earliest of each was not kept"
    # Owner and peer evidence is never touched by the cleanup.
    assert any(o["signed_by"] == "user" for o in obs)
    assert any(o["signed_by"] == "peer-key" for o in obs)
    # The next write persists the cleanup and stamps it so it runs once.
    graph.record_articles_seen(["outlet.test"])
    data = json.loads(graph.path.read_text(encoding="utf-8"))
    assert len(data["sources"]["outlet.test"]["observations"]) == 8
    stamp = data["meta"]["evidence_dedupe"]
    assert stamp["removed"] == 12 and stamp["version"] >= 1


def test_the_url_normaliser():
    n = stg.normalize_article_url
    assert n("https://www.Outlet.test/a/b/?utm_source=x&id=3#frag") == "outlet.test/a/b?id=3"
    assert n("http://outlet.test/a/b?b=2&a=1") == n("https://outlet.test/a/b/?a=1&b=2")
    assert n("") == "" and n("mailto:someone@outlet.test") == ""
