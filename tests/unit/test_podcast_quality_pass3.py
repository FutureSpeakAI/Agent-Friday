"""The third pass on the Briefing episode, from a close read of a real one:
home is not a destination, facts stay with their story, no editorial frame on
violence, no narration of the writer's process, no forced relevance, stories
weighted by news value, publisher links, and clean text encoding.

Synthetic stories and a synthetic home city (Springfield) only.
"""
from __future__ import annotations

import base64
import json

import pytest

from agent_friday.services import news_links as nl
from agent_friday.services import podcast_engine as pe
from agent_friday.services import podcast_news
from agent_friday.services import podcast_quality as q

from podcast_briefing_fixture import docs, good_lines

HOME = "Springfield"


@pytest.fixture
def ds():
    return docs()


def _codes(problems):
    return [p["code"] for p in problems]


def _check(lines, ds, home=HOME):
    return q.script_problems(lines, ds, n_chapters=3, news=True, personal=True, solo=True, home=home)


def _sid(ds, start):
    return next(d["sid"] for d in ds if d["title"].startswith(start))


# 1 ── home is not "where you are going" ─────────────────────────────────────

def test_a_story_in_the_home_city_needs_no_practical_line(ds):
    lines = [ln for ln in good_lines(ds) if not ln["text"].startswith(("Your 5:30", "Tonight's"))]
    assert "safety_no_practical_line" not in _codes(_check(lines, ds))


def test_home_is_never_called_a_place_you_are_going(ds):
    lines = good_lines(ds)
    lines[9] = dict(lines[9], text="That happened in Springfield, where you are going today.")
    assert "home_as_destination" in _codes(_check(lines, ds))


def test_a_specific_tie_to_today_s_venue_still_needs_its_practical_line(ds):
    near = [dict(d, text=d["text"] + " The bar is on Harbor St.") if d["title"].startswith("Two injured")
            else d for d in ds]
    lines = [ln for ln in good_lines(near) if not ln["text"].startswith(("Your 5:30", "Tonight's"))]
    assert "safety_no_practical_line" in _codes(_check(lines, near))


# 2 ── facts stay with their story ───────────────────────────────────────────

def test_a_fact_from_another_story_in_a_sentence_is_named(ds):
    lines = good_lines(ds)
    lines[8] = dict(lines[8], text=lines[8]["text"] + " The companies signed it in Washington.")
    probs = [p for p in _check(lines, ds) if p["code"] == "crossed_facts"]
    assert probs and "voluntary pledge" in probs[0]["message"]


def test_facts_from_the_story_cited_pass(ds):
    assert "crossed_facts" not in _codes(_check(good_lines(ds), ds))


# 3 ── no editorial frame on violence; a read elsewhere is grounded ──────────

def test_no_opinion_on_a_violence_or_crime_story(ds):
    lines = good_lines(ds)
    lines[9] = dict(lines[9], text="My read is that this shows a pattern of state force.")
    assert "opinion_on_violence" in _codes(_check(lines, ds))


def test_a_read_must_rest_on_the_cited_facts(ds):
    lines = good_lines(ds)
    capex = _sid(ds, "AI data-center")
    lines[5] = dict(lines[5], text="My read is that the moon landing was staged.", cites=[capex])
    assert "ungrounded_read" in _codes(_check(lines, ds))
    lines[5] = dict(lines[5], text="My read: spending of $400B next year outruns any pledge.", cites=[capex])
    assert "ungrounded_read" not in _codes(_check(lines, ds))


# 4 ── no narration of the writer's process ──────────────────────────────────

def test_process_narration_is_cut_before_speech():
    kept, _ = pe.clean_lines([{"speaker": "a", "cites": ["S1"], "text":
                               "The shooting is under investigation. I did not check the exact "
                               "time, so I am not inventing one. Police have not named a motive."}], {"S1"})
    assert kept[0]["text"] == "The shooting is under investigation. Police have not named a motive."


def test_process_narration_that_survives_is_named(ds):
    lines = good_lines(ds)
    lines[8] = dict(lines[8], text=lines[8]["text"] + " I won't guess at a name, so I'm leaving it out.")
    assert "meta_narration" in _codes(_check(lines, ds))


# 5 ── no forced relevance ───────────────────────────────────────────────────

def test_a_story_with_no_personal_tie_needs_none(ds):
    lines = [ln for ln in good_lines(ds) if not ln["text"].startswith("For you,")]
    assert "no_lede" not in _codes(_check(lines, ds))


def test_the_writer_is_not_asked_to_invent_a_tie():
    p = pe._system_prompt({"show": "The Briefing", "hosts": pe.DEFAULTS["hosts"], "format": "solo",
                           "attached": {"routine": "briefing"}, "home": HOME})
    assert "Then one line on why it matters" not in p
    assert "no tie is needed" in p and "lives in Springfield" in p and "here in Springfield" in p


# 6 ── story weight ──────────────────────────────────────────────────────────

def test_stories_are_weighted_by_news_value_and_capped():
    items = ([{"title": "New wireless earbuds reviewed", "source": "gadgets.example", "url": "https://g.example/1",
               "snippet": "Hands-on review of the headphones.", "category": "AI/Tech"}] * 1
             + [{"title": "Tech startup %d raises money" % i, "source": "t.example", "url": "https://t.example/%d" % i,
                 "snippet": "A funding round.", "category": "AI/Tech"} for i in range(8)]
             + [{"title": "Two injured in a Springfield shooting", "source": "local.example",
                 "url": "https://local.example/s", "snippet": "Police said two people were hurt.",
                 "category": "Local"},
                {"title": "Inflation eased in August", "source": "biz.example", "url": "https://biz.example/i",
                 "snippet": "Core prices rose 3.0%.", "category": "Business"},
                {"title": "Senate passes the budget bill", "source": "pol.example", "url": "https://pol.example/b",
                 "snippet": "The vote was 52-48.", "category": "Politics"}])
    docs_ = podcast_news.briefing_docs({"news": nl.number(items), "calendar": []}, "# B\n", "2031-03-12")
    titles = [d["title"] for d in docs_ if d.get("role") == "story"]
    assert len(titles) == podcast_news.MAX_BRIEFING_STORIES == 8
    assert titles[:3] == ["Two injured in a Springfield shooting", "Senate passes the budget bill",
                          "Inflation eased in August"]
    assert "New wireless earbuds reviewed" not in titles


# 7 ── publisher links, not news.google.com redirects ─────────────────────────

def test_an_old_style_google_news_link_decodes_to_the_publisher():
    inner = b"\x08\x13\x22\x21https://example.org/2031/real-story\xd2\x01\x00"
    gurl = "https://news.google.com/rss/articles/" + base64.urlsafe_b64encode(inner).decode().rstrip("=") + "?oc=5"
    assert nl.resolve_url(gurl, fetch=lambda *a, **k: pytest.fail("no network needed")) == \
        "https://example.org/2031/real-story"


def test_a_new_style_google_news_link_is_resolved_over_the_network():
    gurl = "https://news.google.com/rss/articles/CBMiX0FVX3lxTE9uZXdzdHlsZWlk?oc=5"
    page = '<c-wiz><div jscontroller="x" data-n-a-id="AU_yqLOnewstyleid" data-n-a-ts="1790000000" data-n-a-sg="SIG123"></div></c-wiz>'
    calls = []

    def fetch(url, data=None):
        calls.append((url, data))
        if data is None:
            return page
        return ')]}\'\n\n[["wrb.fr","Fbv4je","[\\"garturlres\\",\\"https://example.org/real\\",1]",null,null,null,"generic"]]'
    assert nl.resolve_url(gurl, fetch=fetch) == "https://example.org/real"
    assert "SIG123" in calls[1][1] and "AU_yqLOnewstyleid" in calls[1][1]


def test_an_unresolvable_google_link_is_kept_rather_than_lost():
    gurl = "https://news.google.com/rss/articles/CBMiX0FVX3lxTE9uZXdzdHlsZWlk"

    def fetch(url, data=None):
        raise OSError("offline")
    assert nl.resolve_url(gurl, fetch=fetch) == gurl


def test_a_publisher_link_is_left_alone():
    assert nl.resolve_url("https://example.org/a", fetch=lambda *a, **k: pytest.fail("no")) == "https://example.org/a"


# 8 ── text encoding ─────────────────────────────────────────────────────────

def test_the_transcript_file_is_utf8_with_a_mark_windows_readers_honour():
    ep = {"title": "Your “Wednesday” — briefing", "show": "The Briefing", "duration_s": 61,
          "format": "solo", "hosts": pe.DEFAULTS["hosts"], "chapters": [{"title": "The day"}],
          "lines": [{"speaker": "a", "chapter": 0, "start": 0, "text": "It’s here — today.", "cites": ["S1"]}],
          "sources": [{"id": "S1", "title": "A “quoted” story", "outlet": "Example", "role": "story",
                       "url": "https://example.org/a"}],
          "check": {"wer": 0.02}, "script_check": {"ok": True, "problems": []}}
    raw = pe.transcript_bytes(ep)
    assert raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    assert "It’s here — today." in text and "Example: A “quoted” story — https://example.org/a" in text


def test_a_day_from_another_story_in_a_sentence_about_this_one_is_named(ds):
    """The real case: "the hearing is set for Wednesday" said of a different incident."""
    lines = good_lines(ds)
    lines[8] = dict(lines[8], text=lines[8]["text"] + " The Springfield bar reopens March 12.")
    lines[4] = dict(lines[4], text=lines[4]["text"].replace("next year", "by March 12 next year"))
    near = [dict(d, text=d["text"] + " The report is due March 12.") if d["title"].startswith("AI data")
            else d for d in ds]
    probs = [p for p in _check(lines, near) if p["code"] == "crossed_facts"]
    assert probs and "March" in probs[0]["message"]
