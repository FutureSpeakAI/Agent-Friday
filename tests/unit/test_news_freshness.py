"""The Front Page is today's news. Synthetic stories in the shape of a real
edition that carried a 42-day-old feature, continuing items with nothing new,
a lead that had already run, pages that are not articles, and one shooting
told three times by three outlets.

- a story is eligible only inside the edition window (36 h by default, a
  per-routine setting); older only with a material new development, dated
- "continuing" needs a real update; an empty one is dropped, never shown
- the lead is new since the last edition, or a dated update
- forecast pages, headline indexes, app promos and truncated posts are not stories
- one event from several outlets is one story listing every outlet
"""
from __future__ import annotations

import json
import time

import pytest

from agent_friday.services import news_seen as ns

NOW = time.mktime((2031, 3, 12, 9, 15, 0, 0, 0, -1))
H = 3600.0


def item(title, source, age_h, snippet="", url=None, **k):
    return dict({"title": title, "source": source, "url": url or "https://%s/%d" % (source, abs(hash(title)) % 10**8),
                 "snippet": snippet or title + ".", "ts": NOW - age_h * H, "category": "Local"}, **k)


#: Titles of pages that are not articles, as real feeds served them.
NOT_ARTICLES = ["Your latest forecast", "Your latest headlines", "Citizen: Keeping you safe & informed",
                "Yet More …", "Yet More ...", ""]


@pytest.mark.parametrize("title", NOT_ARTICLES)
def test_a_page_that_is_not_an_article_is_not_a_story(title):
    assert not ns.is_article(item(title, "examplelocal.com", 2))


@pytest.mark.parametrize("title", [
    "Man shot by an agent has surgery to remove a bullet, his attorney says",
    "Your guide to the city's new transit fares",
    "Yet more delays for the river road reopening, crews say",
    "Council passes the transit budget, 7-2"])
def test_a_real_headline_is_a_story(title):
    assert ns.is_article(item(title, "examplewire.com", 2))


def test_a_homepage_link_is_not_a_story():
    assert not ns.is_article(item("Example Local News", "examplelocal.com", 2, url="https://examplelocal.com/"))


def test_freshness_follows_the_routines_window(monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda: {"news_edition_window_hours": {"front_page": 12}})
    assert ns.window_hours("front_page") == 12
    monkeypatch.setattr(core, "_load_settings", lambda: {})
    assert ns.window_hours("front_page") == 36 and ns.window_hours("briefing") == 36


def test_one_event_from_three_outlets_is_one_story_listing_all():
    pool = [item("Man shot by agent in Riverton undergoes surgery, his attorney says", "examplelocal.com", 4),
            item("Man shot by agent in Riverton has surgery to remove bullet lodged in his back", "exampleglobe.com", 2),
            item("Delivery driver shot by agent has surgery to remove bullet", "examplespectrum.com", 1),
            item("Council passes the transit budget, 7-2", "examplewire.com", 3)]
    merged = ns.merge_events(pool)
    assert len(merged) == 2
    shooting = next(m for m in merged if "shot" in m["title"])
    assert len(shooting["also"]) == 2
    assert {shooting["source"]} | {a["source"] for a in shooting["also"]} == {
        "examplelocal.com", "exampleglobe.com", "examplespectrum.com"}


def _past():
    return [{"id": "2031-03-11-evening", "generated_at": "2031-03-11T19:00:00",
             "lead": item("Is my chatbot poisoned by disinformation?", "examplereview.org", 28),
             "sections": [{"articles": [
                 item("The newsroom that built an AI copy of its editor in chief", "examplepost.com", 1022),
                 item("Google tests its plan for data centers in orbit", "examplescience.com", 23),
                 item("Appeals court upholds a landmark AI copyright ruling", "examplelab.org", 41)]}]}]


def test_the_edition_shape_that_went_out_does_not_come_back():
    """The regression: the 42-day-old feature, continuing items with nothing
    new and a lead that ran before do not appear; fresh news does, the
    non-articles do not, and the shooting is one story."""
    pool = [
        item("Is my chatbot poisoned by disinformation?", "examplereview.org", 28),          # ran, nothing new
        item("The newsroom that built an AI copy of its editor in chief", "examplepost.com", 1022),  # 42 days
        item("The case for a robot tax to redistribute wealth", "exampleworld.org", 340),    # 14 days, never ran
        item("Google tests its plan for data centers in orbit", "examplescience.com", 19),   # ran, nothing new
        item("Appeals court upholds a landmark AI copyright ruling", "examplelab.org", 41),   # ran, nothing new
        item("Your latest forecast", "examplelocal.com", 4),
        item("Citizen: Keeping you safe & informed", "examplecitizen.com", 3),
        item("Yet More …", "examplememo.com", 1),
        item("Man shot by agent in Riverton undergoes surgery, his attorney says", "examplelocal.com", 4),
        item("Man shot by agent in Riverton has surgery to remove bullet lodged in his back", "exampleglobe.com", 2),
        item("The nation added 29,000 jobs in September", "examplepublic.org", 1),
    ]
    kept, held = ns.edition_pool(pool, _past(), now=NOW, window_h=36)
    titles = [k["title"] for k in kept]
    assert len(titles) == 2 and "The nation added 29,000 jobs in September" in titles
    shooting = next(k for k in kept if "shot" in k["title"])
    assert len(shooting["also"]) == 1                       # two outlets, one story
    reasons = {h["title"]: h["why"] for h in held}
    assert reasons["The newsroom that built an AI copy of its editor in chief"] == "older than the edition window"
    assert reasons["The case for a robot tax to redistribute wealth"] == "older than the edition window"
    assert reasons["Is my chatbot poisoned by disinformation?"] == "ran before, nothing new"
    assert reasons["Your latest forecast"] == "not an article"


def test_an_old_story_comes_back_only_with_a_dated_development():
    past = _past()
    developed = item("Appeals court upholds a landmark AI copyright ruling", "examplelab.org", 2,
                     snippet="Appeals court upholds a landmark AI copyright ruling. On Thursday the losing "
                             "side, Ledgerline, said it would appeal to the Supreme Court.")
    kept, _ = ns.edition_pool([developed], past, now=NOW, window_h=36)
    assert len(kept) == 1 and kept[0]["update"] is True
    assert "Ledgerline" in kept[0]["update_note"] and "Mar 12" in kept[0]["update_note"]


def test_the_front_page_never_shows_an_empty_continuing_item_or_a_stale_lead(tmp_path, monkeypatch):
    from agent_friday.services import news_engine as ne
    monkeypatch.setattr(ne, "FRONT_PAGES_DIR", tmp_path)
    for ed in _past():
        (tmp_path / (ed["id"] + ".json")).write_text(json.dumps(dict(ed, date="2031-03-11", slot="evening")),
                                                     encoding="utf-8")
    real_now = time.time()
    fresh = [dict(i, ts=real_now - 2 * H) for i in (
        item("Council passes the transit budget, 7-2", "examplewire.com", 0),
        item("Storm closes the river road", "exampleledger.com", 0))]
    ran = dict(_past()[0]["lead"], ts=real_now - 2 * H)                  # fresh-dated, but it ran, nothing new
    monkeypatch.setattr(ne, "_gather_front_page_pool", lambda per_cat=14: ([ran] + fresh, {}))
    monkeypatch.setattr(ne, "_fetch_calendar_today", lambda: [])
    monkeypatch.setattr(ne, "_editorialize_front_page", lambda pool, **k: {
        "lead_index": 0, "lead_note": "", "headline": "H", "section_context": {},
        "thread_updates": {p["url"]: "Same story as last edition" for p in pool}})
    ed = ne._generate_front_page.__wrapped__("morning")
    shown = [ed["lead"]] + [a for s in ed["sections"] for a in s["articles"]]
    assert all(not a.get("continuing") or a.get("update") for a in shown)
    assert ed["lead"]["title"] in ("Council passes the transit budget, 7-2", "Storm closes the river road")
    assert not any(a["title"] == ran["title"] for a in shown)


def test_the_briefing_uses_only_current_articles():
    items = [item("Council passes the transit budget, 7-2", "examplewire.com", 3),
             item("The case for a robot tax to redistribute wealth", "exampleworld.org", 340),
             item("Your latest headlines", "examplelocal.com", 2)]
    assert [i["title"] for i in ns.current(items, "briefing", now=NOW)] == ["Council passes the transit budget, 7-2"]
