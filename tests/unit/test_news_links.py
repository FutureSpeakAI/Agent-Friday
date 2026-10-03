"""News links are attached by code from the fetched stories' URLs; the model
cites by id and never types a link (services/news_links.py). Synthetic
stories only."""
from __future__ import annotations

import json

import pytest

from agent_friday.services import news_links as nl

ITEMS = [
    {"title": "Council passes the transit budget", "source": "examplewire.com",
     "url": "https://examplewire.com/2031/transit", "snippet": "The vote was 7-2."},
    {"title": "Storm closes the river road", "source": "exampleledger.com",
     "url": "https://exampleledger.com/storm", "snippet": "Crews expect it to reopen Friday."},
    {"title": "A story the feed gave no address for", "source": "examplepost.com",
     "url": "", "snippet": "No link."},
]


@pytest.fixture
def stories():
    return nl.number(ITEMS)


def test_the_model_sees_ids_and_never_a_url(stories):
    block = nl.prompt_lines(stories)
    assert "[N1] Council passes the transit budget (examplewire.com): The vote was 7-2." in block
    assert "http" not in block


def test_ids_become_links_from_the_fetched_urls(stories):
    out = nl.attach_links("The council passed the budget. [N1] The river road is shut. [N2]", stories)
    assert "(Examplewire)" not in out
    assert "[Examplewire](https://examplewire.com/2031/transit)" in out
    assert "[Exampleledger](https://exampleledger.com/storm)" in out
    assert "## Sources\n- [Council passes the transit budget](https://examplewire.com/2031/transit)" in out


def test_links_the_model_typed_are_removed_and_ids_it_invented_are_dropped(stories):
    out = nl.attach_links("See [your inbox](https://mail.example.com) and https://jobs.example.com/x. "
                          "Also [N9]. The vote passed. [N1]", stories)
    assert "mail.example.com" not in out and "jobs.example.com" not in out
    assert "your inbox" in out and "N9" not in out
    assert "https://examplewire.com/2031/transit" in out


def test_several_ids_in_one_citation(stories):
    out = nl.attach_links("Two outlets agree. [N1, N2]", stories)
    assert "([Examplewire](https://examplewire.com/2031/transit), [Exampleledger](https://exampleledger.com/storm))" in out


def test_a_briefing_whose_stories_carry_no_working_link_fails(stories):
    """The rule: every story a routine uses is linked. A briefing that cites no
    story, or cites one the feed gave no address for, fails."""
    assert nl.link_problems("The vote passed. [N1] The road is shut. [N2]", stories) == []
    assert nl.link_problems("The vote passed and the road is shut.", stories) == [
        "no story is cited by id, so none can be linked"]
    probs = nl.link_problems("Something happened. [N3]", stories)
    assert probs and "no working link" in probs[0]


def test_a_spoken_briefing_gets_the_stories_without_ids_or_the_citation_rule(stories):
    ctx = "## Live News (RSS)\n" + nl.CITE_RULE + "\n\n### Local\n" + nl.prompt_lines(stories)
    spoken = nl.for_speech(ctx)
    assert "[N1]" not in spoken and nl.CITE_RULE not in spoken
    assert "- Council passes the transit budget (examplewire.com)" in spoken


# ── the Weekly Digest and the Editorial ────────────────────────────────────

@pytest.fixture
def engine(tmp_path, monkeypatch):
    from agent_friday.services import news_engine as ne
    monkeypatch.setattr(ne, "WEEKLY_DIGESTS_DIR", tmp_path / "weekly")
    monkeypatch.setattr(ne, "EDITORIALS_DIR", tmp_path / "editorials")
    monkeypatch.setattr(ne, "_get_friday_system_prompt", lambda **k: "")
    monkeypatch.setattr(ne, "_predict_route_provider", lambda **k: "local")
    monkeypatch.setattr(ne, "_gated_vault_control", lambda: None)
    monkeypatch.setattr(ne, "_gather_weekly_editions", lambda days=7: [
        {"date": "2031-03-10", "lead": dict(ITEMS[0]), "sections": [{"articles": [dict(ITEMS[1])]}]}])
    monkeypatch.setattr(ne, "_gather_editorial_pool", lambda days=7: [dict(i) for i in ITEMS[:2]])
    monkeypatch.setattr(ne, "_editorial_independence_score", lambda text: 0.9)
    monkeypatch.setattr(ne, "_load_banned_sources", lambda: [])
    return ne


def test_the_digest_picks_stories_by_id_and_code_attaches_title_and_link(engine, monkeypatch):
    prompts = []

    def model(messages, **k):
        prompts.append(messages[0]["content"])
        return json.dumps({"top_stories": [
            {"id": "W2", "title": "A title the model made up", "why": "The road matters."},
            {"id": "W9", "why": "An id that does not exist."}],
            "trends": ["Weather"], "editorial": "A quiet week."})
    monkeypatch.setattr(engine, "_generate_text", model)
    dg = engine._generate_weekly_digest()
    assert "[W1] Council passes the transit budget (examplewire.com)" in prompts[0]
    assert "http" not in prompts[0]
    assert dg["top_stories"] == [{"id": "W2", "title": "Storm closes the river road",
                                  "source": "exampleledger.com", "url": "https://exampleledger.com/storm",
                                  "why": "The road matters."}]


def test_the_editorial_cites_by_id_and_is_linked_by_code(engine, monkeypatch):
    prompts = []

    def model(messages, **k):
        prompts.append(messages[0]["content"])
        return ("The budget vote says more than it seems. [E1] See also "
                "[a report](https://made-up.example.com/x).")
    monkeypatch.setattr(engine, "_generate_text", model)
    out = engine._generate_weekly_editorial()
    assert "[E1] (examplewire.com) Council passes the transit budget" in prompts[0]
    assert "http" not in prompts[0].split("ARTICLES:")[1]
    assert "[Examplewire](https://examplewire.com/2031/transit)" in out["markdown"]
    assert "made-up.example.com" not in out["markdown"]
    side = json.loads((engine.EDITORIALS_DIR / (out["week"] + ".sources.json")).read_text(encoding="utf-8"))
    assert [s["id"] for s in side["news"]] == ["E1"] and side["link_check"] == []


def test_episode_source_chips_resolve_from_the_same_story_ids(engine, tmp_path, monkeypatch):
    from agent_friday.services import podcast_news
    monkeypatch.setattr(engine, "_generate_text", lambda messages, **k: "The vote matters. [E1]")
    out = engine._generate_weekly_editorial()
    monkeypatch.setattr(podcast_news, "run_path", lambda routine, rid: engine.EDITORIALS_DIR / (rid + ".md"))
    docs = podcast_news.run_documents("editorial", out["week"])
    story = [d for d in docs if d.get("role") == "story"]
    assert [(d["story_id"], d["url"]) for d in story] == [("E1", "https://examplewire.com/2031/transit")]
    assert not any("Sources" == d.get("heading") for d in docs)


def test_a_briefing_episode_s_stories_carry_the_briefing_s_ids(stories):
    from agent_friday.services import podcast_news
    docs = podcast_news.briefing_docs({"news": stories, "calendar": []}, "# B\n## Tasks\n- one\n", "2031-03-12")
    assert [(d["story_id"], d["url"]) for d in docs if d.get("role") == "story"][:2] == [
        ("N1", "https://examplewire.com/2031/transit"), ("N2", "https://exampleledger.com/storm")]


def test_the_written_briefing_links_to_the_publisher_not_google(monkeypatch):
    from agent_friday.services import news_engine as ne
    stories = nl.number([{"title": "Council passes the budget", "source": "news.google.com",
                          "url": "https://news.google.com/rss/articles/CBMiabc", "snippet": "x"}])
    monkeypatch.setattr(nl, "resolve_url", lambda url, fetch=None: "https://publisher.example/budget"
                        if "news.google.com" in url else url)
    monkeypatch.setitem(ne._LAST_BRIEFING_SOURCES, "news", stories)
    out = ne._finish_briefing("The council passed the budget. [N1]")
    assert "https://publisher.example/budget" in out and "news.google.com" not in out
