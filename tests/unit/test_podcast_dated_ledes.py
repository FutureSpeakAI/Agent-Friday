"""A lede that names its outlet but not when says the day the outlet published
the story, from the story's own brief, so a draft that leaves the day out is
not refused for it. It dates the report, never the event, and a lede that
already says when is left alone. Synthetic stories."""
from __future__ import annotations

from datetime import datetime

import pytest

from agent_friday.services import podcast_engine as pe
from agent_friday.services import podcast_news
from agent_friday.services import podcast_quality as q
from agent_friday.services import podcast_sources
from agent_friday.services import podcast_weather as weather

SUNDAY = datetime(2031, 3, 9, 18, 0).timestamp()


def edition():
    return {
        "id": "2031-03-10-morning", "headline": "Ballots and buses",
        "sections": [{"articles": [
            {"title": "Voters are asking chatbots how to fill in their ballots",
             "source": "npr.org", "url": "https://www.npr.org/ballot-chatbots",
             "snippet": "Voters in Riverton are asking chatbots how to fill in their ballots for the "
                        "council election, election officials said.", "ts": SUNDAY},
            {"title": "Council approves night buses for Riverton",
             "source": "thetribune.example", "url": "https://thetribune.example/night-buses",
             "snippet": "The Riverton council approved night buses on three routes, "
                        "officials said.", "ts": SUNDAY},
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


def L(chapter, text, cites):
    return {"speaker": "a", "chapter": chapter, "text": text, "cites": list(cites)}


def no_lede(lines, ds):
    return [p for p in q.script_problems(lines, ds, n_chapters=2, news=True, personal=True, solo=True,
                                         home="Riverton") if p["code"] == "no_lede"]


def draft(ds):
    # The check reads a lede with the line after it, so the undated lede comes last.
    return [L(0, "The Tribune reports today that the Riverton council approved night buses on "
                 "three routes.", [sid(ds, "Council approves")]),
            L(1, "NPR reports that voters in Riverton are asking chatbots how to fill in their "
                 "ballots for the council election.", [sid(ds, "Voters are")])]


def test_a_lede_without_its_day_blocks_before_the_editor(ds):
    probs = no_lede(draft(ds), ds)
    assert [p["sid"] for p in probs] == [sid(ds, "Voters are")] and "when" in probs[0]["message"]


def test_the_editor_says_the_day_the_outlet_reported_it(ds):
    lines, _cut = pe.edit_script(draft(ds), ds, 2)
    said = " ".join(ln["text"] for ln in lines)
    assert "NPR reported on Sunday that voters in Riverton" in said
    assert not no_lede(lines, ds)


def test_a_lede_that_already_says_when_is_left_alone(ds):
    lines, _cut = pe.edit_script(draft(ds), ds, 2)
    said = " ".join(ln["text"] for ln in lines)
    assert "The Tribune reports today that the Riverton council" in said
    assert "Tribune reported on" not in said


def test_without_a_published_day_nothing_is_invented(ds):
    for d in ds:
        d["text"] = "\n".join(t for t in (d.get("text") or "").split("\n") if not t.startswith("Published:"))
    lines, _cut = pe.edit_script(draft(ds), ds, 2)
    assert not any("reported on" in ln["text"] for ln in lines)
