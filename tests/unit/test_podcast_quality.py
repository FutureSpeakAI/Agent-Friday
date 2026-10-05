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


# ── from the first real regeneration ────────────────────────────────────────

def test_the_digest_s_summary_and_analysis_are_not_sources():
    """They discuss news the listener has not heard introduced, in the digest's
    own phrases; only tasks and the insight are context."""
    titles = [d["title"] for d in docs() if d.get("role") == "digest"]
    assert titles == ["Friday's written briefing: Active Tasks & Commitments",
                      "Friday's written briefing: Proactive Insight"]


def test_the_digest_s_words_still_count_as_the_script_s_own(ds):
    digest = dict(ds[-1], text=ds[-1]["text"] + " The day turns on a linchpin.")
    lines = good_lines(ds)
    for i in (1, 4, 6):
        lines[i] = dict(lines[i], text=lines[i]["text"] + " That is the linchpin.")
    msgs = [p["message"] for p in q.repetition_problems(lines, ds[:-1] + [digest], 3)]
    assert any("linchpin" in m for m in msgs)


@pytest.mark.parametrize("when", ["in August", "last month", "this quarter"])
def test_a_month_or_period_is_a_when(when):
    assert q._WHEN_RE.search("Core inflation eased %s, the report says." % when)


def test_a_name_that_opens_the_headline_is_who():
    s = q.stories([{"sid": "S1", "kind": "news", "title": "Robinhood unveils weekend trading hours",
                    "text": "The broker said the hours start next month.", "outlet": "cnbc.com"}])[0]
    assert "robinhood" in s["entities"]


def test_a_city_shared_with_an_event_is_not_a_tie_that_needs_a_practical_line(ds):
    """Sharing a city (the listener's own, often) is not a specific tie."""
    evs = [dict(d, title="Makers meetup Springfield", location="Springfield") if d.get("kind") == "event"
           and d["start"].endswith("17:30:00-05:00") else d for d in ds]
    lines = [ln for ln in good_lines(ds) if not ln["text"].startswith(("Your 5:30", "Tonight's"))]
    assert "safety_no_practical_line" not in _codes(q.safety_problems(lines, q.stories(evs), q.events(evs)))


def test_the_episode_never_points_at_the_written_digest(ds):
    lines = good_lines(ds)
    lines[1] = dict(lines[1], text="My written briefing flags this interview as the day's key moment.")
    assert "digest_referenced" in _codes(_check(lines, ds))


# ── from the second real regeneration ───────────────────────────────────────

def test_saying_a_story_is_not_background_is_not_dismissing_it(ds):
    lines = good_lines(ds)
    lines[9] = dict(lines[9], text=lines[9]["text"] + " It is not background noise.")
    assert "safety_dismissed" not in _codes(_check(lines, ds))


def test_the_week_is_plain_news_vocabulary(ds):
    lines = good_lines(ds)
    for i in (2, 4, 6):
        lines[i] = dict(lines[i], text=lines[i]["text"] + " It has been a busy week.")
    assert not [p for p in _check(lines, ds) if p["code"] == "repeats_word" and "week" in p["message"]]


def test_what_she_did_not_check_is_said_once_not_as_a_refrain(ds):
    lines = good_lines(ds)
    lines[4] = dict(lines[4], text=lines[4]["text"] + " I did not check the bank's method.")
    lines[8] = dict(lines[8], text=lines[8]["text"] + " I did not verify the bar's name.")
    assert "refrain" in _codes(_check(lines, ds))


def test_a_generic_word_shared_with_an_event_s_name_is_not_a_practical_line(ds):
    """The story names the venue's street, so a practical line is owed; one
    common word shared with the event's name ("local") is not that line."""
    near = [dict(d, text=d["text"] + " The bar is on Harbor St.") if d["title"].startswith("Two injured")
            else dict(d, title="Makers: Local Models Night") if d.get("kind") == "event"
            and d["start"].endswith("17:30:00-05:00") else d for d in ds]
    lines = [ln for ln in good_lines(ds) if not ln["text"].startswith(("Your 5:30", "Tonight's"))]
    lines.insert(9, {"speaker": "a", "chapter": 1, "cites": [], "text": "It is the local story to know."})
    assert "safety_no_practical_line" in _codes(q.safety_problems(lines, q.stories(near), q.events(near)))


def test_a_postal_address_is_not_read_out_whole(ds):
    lines = good_lines(ds)
    lines[9] = dict(lines[9], text="Your 5:30 PM meetup is at 12 Harbor St, Springfield, IL 62701, "
                                   "USA, so leave a little early.")
    assert "reads_address" in _codes(_check(lines, ds))


def test_outlets_on_a_shared_host_are_named_by_their_own_name():
    """abcnews.go.com is ABC News, and "go" is never an alias for it."""
    s = {"outlet": "abcnews.go.com", "title": "x"}
    assert q.spoken_outlet(s) == "ABC News"
    assert "go" not in q.outlet_aliases(s)
    assert q.said_outlet("ABC News reports that the storm passed.", q.outlet_aliases(s))
    assert not q.said_outlet("You should go early.", q.outlet_aliases(s))


def test_a_place_is_a_whole_word_never_the_front_of_a_longer_one():
    # "An AI couldn't beat humans at StarCraft" rejected an episode for a lede
    # without "where (Star)".
    from agent_friday.services import podcast_quality as q
    assert q._places("An AI couldn't beat humans at StarCraft, so it cheated") == set()
    assert q._places("A rally in Ohio, then a stop at McAllen's airport") == {"ohio"}
    assert q._places("Flooding in Texas and near Austin") == {"texas", "austin"}
