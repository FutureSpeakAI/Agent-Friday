"""The brutalist.report scrape: parsing, classification, and archiving.

brutalist.report publishes no RSS — every feed route 404s — so this source is
a scrape of rendered HTML rather than a parsed feed. That makes it the most
fragile source in the pipeline: a layout change breaks it silently, and a
scraper that returns nothing looks exactly like a quiet news day.

These tests run entirely offline. The page shape is pinned as a fixture so a
parser regression fails here rather than in a twice-daily scheduled job whose
only symptom is an archive that stops growing.
"""

import types

import pytest
import requests

from agent_friday.services import news_engine as ne


def _serve(monkeypatch, html):
    """Patch requests.get on the MODULE.

    _fetch_brutalist_links does `import requests` inside the function body, so
    there is no news_engine.requests attribute to patch — an earlier version of
    this file patched one, silently did nothing, and the "offline" tests went to
    the live site.
    """
    def _fake(url, *a, **kw):
        return types.SimpleNamespace(
            text=html, content=html.encode(), status_code=200,
            raise_for_status=lambda: None)
    monkeypatch.setattr(requests, "get", _fake)


# The real page is <h3>source</h3> followed by a SIBLING CONTAINER of links,
# not bare <a> siblings: the parser walks siblings and calls find_all("a") on
# each, so links must be nested inside an element. Getting this wrong makes
# every parse test pass vacuously against an empty result.
PAGE = """
<html><body>
  <h3>Hacker News</h3>
  <ul>
    <li><a href="https://example-tech.com/gpu-benchmarks">New GPU benchmarks leak</a></li>
    <li><a href="https://example-tech.com/compiler">A much faster compiler</a></li>
  </ul>
  <h3>The Verge</h3>
  <ul>
    <li><a href="https://example-news.com/senate-vote">Senate votes on the bill</a></li>
  </ul>
  <h3>Denver Post</h3>
  <ul>
    <li><a href="https://example-local.com/denver-council">Denver city council meets</a></li>
  </ul>
</body></html>
"""


@pytest.fixture
def page(monkeypatch):
    """Serve the fixture page in place of a live fetch."""
    _serve(monkeypatch, PAGE)
    return PAGE


# ── parsing ──────────────────────────────────────────────────────────────────

def test_links_are_extracted_with_their_source_domain(page):
    links = ne._fetch_brutalist_links(limit=50)
    assert links, "the parser returned nothing from a well-formed page"
    titles = {l["title"] for l in links}
    assert "New GPU benchmarks leak" in titles
    assert "Senate votes on the bill" in titles
    # the domain is what ban/boost and trust scoring key on, so it must be the
    # ARTICLE's host, never the aggregator's
    assert all("brutalist" not in (l["source"] or "") for l in links)
    assert any(l["source"] == "example-tech.com" for l in links)


def test_the_limit_is_honoured(page):
    assert len(ne._fetch_brutalist_links(limit=2)) <= 2


def test_a_page_with_no_links_yields_nothing_rather_than_raising(monkeypatch):
    """A layout change must degrade to "no new articles", not to a traceback
    inside a scheduled job nobody is watching."""
    _serve(monkeypatch, "<html><body></body></html>")
    assert ne._fetch_brutalist_links(limit=10) == []


def test_a_fetch_failure_is_swallowed(monkeypatch):
    def _boom(url, *a, **kw):
        raise OSError("network down")
    monkeypatch.setattr(requests, "get", _boom)
    assert ne._fetch_brutalist_links(limit=10) == []


# ── classification ───────────────────────────────────────────────────────────

def test_headlines_land_in_a_known_beat():
    beats = set(ne.NEWS_BEATS) if hasattr(ne, "NEWS_BEATS") else None
    for headline in ("New GPU benchmarks leak",
                     "Senate votes on the bill",
                     "Denver city council meets"):
        beat = ne._classify_brutalist_headline(headline)
        assert isinstance(beat, str) and beat
        if beats:
            assert beat in beats, f"{headline!r} -> {beat!r}, not a known beat"


def test_a_local_headline_is_classified_local(monkeypatch):
    monkeypatch.setattr(ne, "_local_area", lambda: "Springfield, Illinois")
    assert ne._classify_brutalist_headline("Springfield city council meets") == "Local"


def test_with_no_area_set_no_headline_is_local_and_the_beat_is_empty(monkeypatch):
    monkeypatch.setattr(ne, "_local_area", lambda: "")
    assert ne._classify_brutalist_headline("Springfield city council meets") != "Local"
    meta = ne.category_meta("Local")
    assert meta["feeds"] == [] and meta["query"] == ""


def test_the_local_beat_follows_the_owners_area(monkeypatch):
    monkeypatch.setattr(ne, "_local_area", lambda: "Portland, Oregon")
    meta = ne.category_meta("Local")
    assert meta["query"] == "Portland, Oregon local news today"
    assert len(meta["feeds"]) == 1 and "Portland" in meta["feeds"][0]
    assert ne.category_meta("Business") is ne.NEWS_CATEGORIES["Business"]


def test_classification_never_raises_on_odd_input():
    for bad in ("", None, "   ", "x" * 500):
        assert isinstance(ne._classify_brutalist_headline(bad), str)


# ── the scheduled tick ───────────────────────────────────────────────────────

def test_the_tick_archives_what_it_scraped(page, monkeypatch):
    archived = {}
    def _capture(pool):
        archived["pool"] = pool
        return len(pool)
    monkeypatch.setattr(ne, "_archive_articles", _capture)
    monkeypatch.setattr(ne, "_load_banned_sources", lambda: [])
    monkeypatch.setattr(ne, "_load_boosted_sources", lambda: [])
    added = ne._brutalist_scraper_tick()
    assert added > 0
    pool = archived["pool"]
    # every item must carry what the Feed and the trust badges read
    for item in pool:
        for field in ("title", "source", "score", "reading_time", "sentiment"):
            assert field in item, f"archived item missing {field!r}"


def test_a_banned_source_is_dropped_before_archiving(page, monkeypatch):
    """The scrape must respect the same ban list as every other source; a new
    ingest path that ignores it would quietly reintroduce what was banned."""
    seen = {}
    def _capture(pool):
        seen["pool"] = pool
        return len(pool)
    monkeypatch.setattr(ne, "_archive_articles", _capture)
    monkeypatch.setattr(ne, "_load_banned_sources", lambda: ["example-tech.com"])
    monkeypatch.setattr(ne, "_load_boosted_sources", lambda: [])
    ne._brutalist_scraper_tick()
    assert all("example-tech.com" not in (i["source"] or "")
               for i in seen["pool"]), "a banned source survived the scrape path"


def test_an_empty_scrape_archives_nothing(monkeypatch):
    monkeypatch.setattr(ne, "_fetch_brutalist_links", lambda limit=200: [])
    monkeypatch.setattr(ne, "_archive_articles",
                        lambda pool: pytest.fail("archived an empty scrape"))
    monkeypatch.setattr(ne, "_load_banned_sources", lambda: [])
    monkeypatch.setattr(ne, "_load_boosted_sources", lambda: [])
    assert ne._brutalist_scraper_tick() == 0


# ── the scheduler wiring ─────────────────────────────────────────────────────

def test_both_daily_scrapes_are_registered():
    """The tick is only useful if something calls it. Two registrations, at
    the morning and evening slots."""
    import inspect
    from agent_friday.services import scheduler as sch
    src = inspect.getsource(sch)
    assert "brutalist_morning" in src and "brutalist_evening" in src
    assert "_brutalist_scraper_tick" in src


def test_the_function_the_scheduler_imports_exists():
    """The scheduler imports this by name at registration time; a rename that
    missed one side would fail only at boot, inside a try/except that prints
    and moves on."""
    assert callable(getattr(ne, "_brutalist_scraper_tick", None))
