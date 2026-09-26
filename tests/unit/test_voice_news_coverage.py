"""A spoken news rundown moves forward instead of repeating itself.

Asked again for the news, the voice session was handed the same five stories
from the feed's first category every time, and had no briefing to read. The
feed is now spread across categories, stories Friday has already told are left
out, and when nothing new is left the tool says so. The daily briefing is a
voice tool of its own.
"""
import json

import pytest

import agent_friday.services.agent as agent
import agent_friday.services.voice_engine as ve


def _item(title, cat, score=1.0, breaking=False):
    return {"title": title, "snippet": title + " snippet", "url": "https://example.test/" +
            title.replace(" ", "-"), "source": "Example", "category": cat,
            "score": score, "breaking": breaking}


FEED = (
    [_item("Chipmaker unveils faster processor line", "tech", 5),
     _item("Browser vendor patches zero day flaw", "tech", 4),
     _item("Startup raises funding for robot kitchens", "tech", 3),
     _item("Phone maker delays foldable launch", "tech", 2),
     _item("Cloud outage disrupts streaming services", "tech", 1),
     _item("Parliament passes housing reform bill", "world", 5),
     _item("Earthquake strikes coastal region overnight", "world", 6, breaking=True),
     _item("Central bank holds interest rates steady", "business", 5),
     _item("Retail sales climb during holiday quarter", "business", 3)]
)


@pytest.fixture
def feed(monkeypatch):
    import agent_friday.services.news_engine as ne
    monkeypatch.setattr(ne, "news_items_fast", lambda limit_per=8: list(FEED))
    # Governance is covered elsewhere; here the handler runs as it would once allowed.
    monkeypatch.setattr(agent, "_execute_tool",
                        lambda name, a, handler=None, session_ctx=None, **k: handler(a))


def _titles(res):
    return [h["title"] for h in json.loads(res)["hits"]]


def test_the_top_stories_span_the_sections(feed):
    titles = _titles(agent._tool_search_news({"limit": 5}))
    cats = {next(i["category"] for i in FEED if i["title"] == t) for t in titles}
    assert cats == {"tech", "world", "business"}
    # Breaking news leads its section.
    assert "Earthquake strikes coastal region overnight" in titles[:3]


def test_a_second_request_in_the_same_call_brings_new_stories(feed):
    session = {}
    first = _titles(ve._voice_tool_run("search_news", {"limit": 3}, lambda o: None, session=session))
    # Friday reads the first two aloud; the third is offered but never spoken.
    session["spoken"] = ["Here is the news. " + first[0] + ". Also, " + first[1] + "."]
    second = _titles(ve._voice_tool_run("search_news", {"limit": 3}, lambda o: None, session=session))
    assert not set(first[:2]) & set(second), "told stories were repeated"
    # Fresh stories come before the one that was offered but not told.
    assert second[-1] == first[2] or first[2] not in second


def test_when_every_story_is_told_the_tool_says_so(feed):
    session = {"news_offered": [i["title"] for i in FEED],
               "spoken": [". ".join(i["title"] for i in FEED)]}
    res = json.loads(ve._voice_tool_run("search_news", {"limit": 5}, lambda o: None, session=session))
    assert res["hits"] == [] and res["out_of_stories"] is True
    assert "get_briefing" in res["note"]


def test_title_told_needs_the_story_not_one_shared_word():
    assert ve._title_told("Central bank holds interest rates steady",
                          "the central bank held rates, holds interest steady")
    assert not ve._title_told("Central bank holds interest rates steady",
                              "I went to the bank today")


def test_the_briefing_is_a_voice_tool(tmp_path, monkeypatch, feed):
    assert "get_briefing" in [t[0] for t in ve._VOICE_LIVE_TOOLS]
    bdir = tmp_path / "wiki" / "briefings"
    bdir.mkdir(parents=True)
    (bdir / "2026-09-25.md").write_text("# Briefing\nEarthquake strikes coastal region.", encoding="utf-8")
    idx = bdir / "_index.md"
    idx.write_text("# Index of briefings", encoding="utf-8")
    import os
    os.utime(idx, (4_000_000_000, 4_000_000_000))       # the index is the newest file
    monkeypatch.setattr(agent, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(agent, "CREATIONS_DIR", tmp_path / "none")
    out = ve._voice_tool_run("get_briefing", {}, lambda o: None)
    assert "Earthquake strikes" in out and "Index of briefings" not in out


def test_a_long_briefing_is_trimmed_for_speech(tmp_path, monkeypatch, feed):
    bdir = tmp_path / "wiki" / "briefings"
    bdir.mkdir(parents=True)
    (bdir / "2026-09-25.md").write_text("story. " * 5000, encoding="utf-8")
    monkeypatch.setattr(agent, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(agent, "CREATIONS_DIR", tmp_path / "none")
    out = ve._voice_tool_run("get_briefing", {}, lambda o: None)
    assert len(out) < ve.VOICE_BRIEFING_CHARS + 200
