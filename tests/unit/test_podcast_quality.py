"""The script-quality gate (services/podcast_quality.py), on a synthetic day
that reproduces a real briefing episode's defects (podcast_briefing_fixture)."""
from __future__ import annotations

import pytest

from agent_friday.services import podcast_quality as q

from podcast_briefing_fixture import bad_lines, docs, good_lines


@pytest.fixture
def ds():
    return docs()


def _codes(problems):
    return [p["code"] for p in problems]


def _check(lines, ds):
    return q.script_problems(lines, ds, n_chapters=3, news=True, personal=True, solo=True)


def test_the_good_script_passes_every_check(ds):
    assert _check(good_lines(ds), ds) == []


def test_each_story_first_mentioned_without_a_lede_is_named(ds):
    probs = [p for p in _check(bad_lines(ds), ds) if p["code"] == "no_lede"]
    titles = " ".join(p["message"] for p in probs)
    for t in ("voluntary pledge", "$400B", "Rowan Hale", "Springfield bar"):
        assert t in titles
    assert all("the outlet, named aloud" in p["message"] for p in probs)


def test_a_lede_needs_the_outlet_said_aloud(ds):
    lines = good_lines(ds)
    lines[2] = dict(lines[2], text=lines[2]["text"].replace("Example Wire reports that ", ""))
    msgs = [p["message"] for p in _check(lines, ds) if p["code"] == "no_lede"]
    assert len(msgs) == 1 and "the outlet, named aloud (Examplewire" in msgs[0]


def test_a_safety_story_filed_as_background_fails(ds):
    codes = _codes(_check(bad_lines(ds), ds))
    assert "safety_dismissed" in codes and "safety_unattributed" in codes
    assert "safety_no_practical_line" in codes      # the meetup is in the same town


def test_before_your_interviews_is_checked_against_the_calendar(ds):
    probs = [p for p in _check(bad_lines(ds), ds) if p["code"] == "time_order"]
    assert len(probs) == 1 and "5:30 PM" in probs[0]["message"] and "9:00 AM" in probs[0]["message"]


def test_a_clock_time_the_calendar_does_not_have_fails(ds):
    lines = good_lines(ds)
    lines[5] = dict(lines[5], text=lines[5]["text"].replace("1:00 PM", "2:30 PM"))
    assert "time_not_in_calendar" in _codes(_check(lines, ds))


def test_after_is_checked_too(ds):
    lines = good_lines(ds)
    lines[10] = dict(lines[10], text="Your Northwind Labs interview comes after the Juniper "
                                     "Robotics one, so save your best example.")
    assert "time_order" in _codes(_check(lines, ds))


def test_the_scripts_own_words_are_said_at_most_twice(ds):
    msgs = [p["message"] for p in _check(bad_lines(ds), ds) if p["code"] == "repeats_word"]
    joined = " ".join(msgs)
    assert "\"linchpin\" is said 3 times" in joined and "\"landscape\" is said 4 times" in joined


def test_a_close_that_rereads_the_opening_fails(ds):
    probs = [p for p in _check(bad_lines(ds), ds) if p["code"] == "close_restates"]
    assert probs and "line 1" in probs[0]["message"]


def test_an_outline_heading_read_aloud_fails(ds):
    assert "heading_read_aloud" in _codes(_check(bad_lines(ds), ds))


def test_flat_fragments_are_counted(ds):
    msgs = [p["message"] for p in _check(bad_lines(ds), ds) if p["code"] == "flat_fragments"]
    assert msgs and "It's context." in msgs[0]


def test_the_link_claim_needs_a_link_for_every_story_heard(ds):
    lines = good_lines(ds)
    assert q.link_claim_ok(lines, q.stories(ds))
    unlinked = [dict(d, url="") if d["title"].startswith("Rowan Hale") else d for d in ds]
    assert not q.link_claim_ok(lines, q.stories(unlinked))
    assert "false_link_claim" in _codes(_check(lines, unlinked))


def test_low_density_in_a_solo_newscast(ds):
    thin = [ln for ln in good_lines(ds) if ln.get("signature") or ln["chapter"] == 0]
    thin = thin + [dict(thin[1], text="That interview matters most, and here is the long "
                                      "version of why it matters so much to your whole day, "
                                      "said slowly, at length, several times over. " * 12)]
    assert "low_density" in _codes(q.script_problems(thin, ds, n_chapters=3, news=True,
                                                     personal=True, solo=True))


def test_outlets_are_said_the_way_people_say_them():
    assert q.spoken_outlet({"outlet": "theguardian.com"}) == "The Guardian"
    assert q.spoken_outlet({"outlet": "kxan.com"}) == "KXAN"
    assert "ap" in q.outlet_aliases({"outlet": "apnews.com"})
    assert "the local ledger" in q.outlet_aliases(
        {"outlet": "news.google.com", "title": "Council votes - The Local Ledger"})
