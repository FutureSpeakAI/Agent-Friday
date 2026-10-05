"""A lede says what happened in the headline's words or, under a figurative
headline, in the facts of the outlet's own summary; it owes who or where only
when the reporting names someone or somewhere. A vague lede still blocks, and
a story that names its people still needs them said. Synthetic stories."""
from __future__ import annotations

from datetime import datetime

import pytest

from agent_friday.services import podcast_news
from agent_friday.services import podcast_quality as q
from agent_friday.services import podcast_sources
from agent_friday.services import podcast_weather as weather

MONDAY = datetime(2031, 3, 10, 9, 0).timestamp()


def edition():
    return {
        "id": "2031-03-10-morning", "headline": "Chips and councils",
        "sections": [{"articles": [
            {"title": "The data boom is making the cheapest phones disappear",
             "source": "techwire.example", "url": "https://techwire.example/cheap-phones",
             "snippet": "Data centers are driving demand for memory chips, pushing phone makers to raise "
                        "prices and abandon some of their most affordable models.", "ts": MONDAY},
            {"title": "Council approves night buses",
             "source": "thetribune.example", "url": "https://thetribune.example/night-buses",
             "snippet": "Mayor Dana Ortiz said the council approved night buses on three routes.",
             "ts": MONDAY},
        ]}],
    }


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(core, "_load_settings", lambda: {"news_local_area": "Riverton"})
    monkeypatch.setattr(weather, "current", lambda city, **k: None)
    return tmp_path


@pytest.fixture
def ds():
    return podcast_sources.number(podcast_news._front_page_docs(edition()))


def sid(ds, start):
    return next(d["sid"] for d in ds if d["title"].startswith(start))


def L(text, cites):
    return {"speaker": "a", "chapter": 0, "text": text, "cites": list(cites)}


def lede(ds, story, text):
    probs = [p for p in q.lede_problems([L(text, [sid(ds, story)])], q.stories(ds))]
    return probs[0]["message"] if probs else ""


def test_a_faithful_lede_under_a_figurative_headline_that_names_no_one_passes(ds):
    assert lede(ds, "The data boom",
                "Techwire reported on Monday that data centers are driving demand for memory chips, "
                "pushing phone makers to raise prices and abandon their most affordable models.") == ""


def test_a_vague_lede_still_says_nothing_happened(ds):
    msg = lede(ds, "The data boom", "Techwire has a story on Monday about phones and what they cost "
                                    "people who are shopping for one this season.")
    assert "what happened" in msg


def test_a_story_that_names_its_people_still_owes_them(ds):
    msg = lede(ds, "Council approves", "The Tribune reported on Monday that night buses were approved "
                                       "on three routes across the city.")
    assert "who or where" in msg
    assert lede(ds, "Council approves", "The Tribune reported on Monday that Mayor Dana Ortiz said the "
                                        "council approved night buses on three routes.") == ""
