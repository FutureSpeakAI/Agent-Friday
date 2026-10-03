"""What leads and what is left out, by news value. Synthetic stories in the
shape of a real edition: local hard news ranked under a hospital
advertorial and a fall-events list; an outlet's own conference posts
filling a section; a first-person column shown as news; a bond-market story
filed under Politics.

- hard news > analysis > service > promo; local hard news tops Local and
  is a lead candidate
- sponsored, advertorial and an outlet's own event promotion are not stories;
  one outlet at most twice in a section
- opinion is labelled as opinion
- a markets story is Business, whatever feed it came from
"""
from __future__ import annotations

import json
import time

import pytest

from agent_friday.services import news_seen as ns

H = 3600.0


def item(title, source, age_h=2, category="Local", url=None, snippet=""):
    now = time.time()
    return {"title": title, "source": source, "category": category, "ts": now - age_h * H,
            "url": url or "https://%s/%d" % (source, abs(hash(title)) % 10**8), "snippet": snippet or title + "."}


SHOOTING = item("Terrorism motive probed in mass shooting at Riverton bar: FBI", "examplenews.com", 7.4)
ADVERTORIAL = item("Riverton Health discusses how primary care providers can partner in your care",
                   "examplecommunity.com", 1.6)
FALL_EVENTS = item("Gear up for the holiday season with this list of local fall events across the region",
                   "examplecommunity.com", 5.4)
THINGS_TO_DO = item("Weekend Check: family-friendly things to do around Riverton", "examplelocal.com", 7.1)
COUNCIL = item("City council delays the vote on the police budget after a heated hearing", "examplelocal.com", 3)


def test_news_value_orders_hard_over_analysis_over_service_over_promo():
    assert ns.news_value(SHOOTING) == "hard" and ns.news_value(COUNCIL) == "hard"
    assert ns.news_value(FALL_EVENTS) == "service" and ns.news_value(THINGS_TO_DO) == "service"
    assert ns.news_value(item("What it takes to make a local newsroom work", "examplemedia.org")) == "analysis"
    assert ns.news_value(ADVERTORIAL) == "promo"
    assert ns.VALUE_ORDER.index("hard") < ns.VALUE_ORDER.index("analysis") \
        < ns.VALUE_ORDER.index("service") < ns.VALUE_ORDER.index("promo")


def test_local_hard_news_tops_local():
    ranked = ns.rank([FALL_EVENTS, THINGS_TO_DO, SHOOTING, COUNCIL])
    assert [r["title"] for r in ranked[:2]] == [SHOOTING["title"], COUNCIL["title"]]


def test_promo_and_sponsored_content_are_not_stories():
    for t, src in (("TechCo Summit 2031: a founder on the rise of the sales engineer", "techco.com"),
                   ("The founder's guide to TechCo Summit 2031: everything you need to know", "techco.com"),
                   ("Sponsored: five ways to save on your energy bill", "examplewire.com"),
                   ("Partner content: how one bank is rethinking lending", "examplebiz.com")):
        assert not ns.is_article(item(t, src)), t
    assert not ns.is_article(ADVERTORIAL)
    # Another outlet covering the conference is news.
    assert ns.is_article(item("At TechCo Summit 2031, founders argue over AI pricing", "examplewire.com"))


def test_one_outlet_at_most_twice_in_a_section():
    many = [item("Story %d about the region's water supply" % i, "exampleone.com", i) for i in range(5)]
    other = item("Another outlet's story on the water supply vote", "exampletwo.com", 2)
    capped = ns.cap_per_outlet(many + [other], per_outlet=2)
    assert sum(1 for c in capped if c["source"] == "exampleone.com") == 2 and other in capped


def test_opinion_is_labelled_as_opinion():
    col = item("Forget the old syndrome: fatigue is real, and I've got it", "examplehill.com", 1)
    assert ns.is_opinion(col)
    assert ns.is_opinion(item("Why the council is wrong about fares", "examplewire.com",
                              url="https://examplewire.com/opinion/fares"))
    assert not ns.is_opinion(COUNCIL)


def test_a_markets_story_is_business_whatever_its_feed():
    bonds = item("Swings in the bond market shake stock markets worldwide", "examplewire.com", 2, category="Politics")
    assert ns.section_for(bonds) == "Business"
    assert ns.section_for(COUNCIL) == "Local"


def test_the_edition_puts_hard_news_first_and_labels_opinion(tmp_path, monkeypatch):
    """Regression on the edition's shape: the shooting leads Local; the
    advertorial and the conference posts are gone; the column is labelled;
    the bond story is in Business."""
    from agent_friday.services import news_engine as ne
    monkeypatch.setattr(ne, "FRONT_PAGES_DIR", tmp_path)
    col = item("Forget the old syndrome: fatigue is real, and I've got it", "examplehill.com", 1, category="Politics")
    bonds = item("Swings in the bond market shake stock markets worldwide", "examplewire.com", 2, category="Politics")
    promo = item("TechCo Summit 2031: a founder on the rise of the sales engineer", "techco.com", 1, category="AI/Tech")
    pool = [FALL_EVENTS, ADVERTORIAL, THINGS_TO_DO, SHOOTING, COUNCIL, col, bonds, promo]
    monkeypatch.setattr(ne, "_gather_front_page_pool", lambda per_cat=14: ([dict(p) for p in pool], {}))
    monkeypatch.setattr(ne, "_fetch_calendar_today", lambda: [])
    monkeypatch.setattr(ne, "_editorialize_front_page", lambda pool, **k: {
        "lead_index": 0, "lead_note": "", "headline": "H", "section_context": {}, "thread_updates": {}})
    ed = ne._generate_front_page.__wrapped__("morning")
    shown = [ed["lead"]] + [a for s in ed["sections"] for a in s["articles"]]
    titles = [a["title"] for a in shown]
    assert ADVERTORIAL["title"] not in titles and promo["title"] not in titles
    assert ed["lead"]["title"] == SHOOTING["title"]          # hard news is the lead candidate
    local = next(s for s in ed["sections"] if s["title"] == "Local")
    assert local["articles"][0]["title"] == COUNCIL["title"]
    business = next(s for s in ed["sections"] if s["title"] == "Business")
    assert bonds["title"] in [a["title"] for a in business["articles"]]
    assert next(a for a in shown if a["title"] == col["title"])["opinion"] is True



@pytest.mark.parametrize("title", [
    "The nation added only 29,000 jobs in September as the job market lacks spark",
    "A justice's recusal from a key climate case may come too late",
    "Riverton's car crime has plunged. How much credit do the cameras deserve?",
    "The economy slides toward recession as interest rates hold"])
def test_the_economy_courts_and_crime_are_hard_news(title):
    assert ns.news_value(item(title, "examplewire.com")) == "hard"
