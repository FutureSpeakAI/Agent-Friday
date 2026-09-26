"""Latency budgets for Gemini Live voice: a slow upstream must not become silence.

On 2026-09-25 a spoken question waited 40 s before Friday answered: the Live
bridge ran check_email (9 s) and then search_news (31 s) one after the other,
while the Gemini loop sat blocked on them. search_news re-fetched forty RSS
feeds category by category, and every item re-parsed a 5 MB trust file.

Each test here puts a slow upstream in place (sleeps, not network) and holds a
wall-clock budget that the old code blew by tens of seconds.
"""
import asyncio
import json
import time

import pytest

from agent_friday.services import news_engine as ne


@pytest.fixture(autouse=True)
def _fresh_news_cache():
    for name in ("_NEWS_CACHE", "_NEWS_INFLIGHT"):
        getattr(ne, name, {}).clear()
    yield
    for name in ("_NEWS_CACHE", "_NEWS_INFLIGHT"):
        getattr(ne, name, {}).clear()


def _items(n=3, tag="live"):
    return [{"title": "%s story %d" % (tag, i), "snippet": "s", "url": "https://x.test/%d" % i,
             "source": "x.test", "category": "tech"} for i in range(n)]


# ── the news feed ────────────────────────────────────────────────────────────

def test_categories_are_fetched_in_parallel(monkeypatch):
    """Six categories at 0.5 s each: 3 s in sequence, ~0.5 s in parallel."""
    monkeypatch.setattr(ne, "_network_is_offline", lambda: False)
    monkeypatch.setattr(ne, "_rss_results", lambda feeds, limit=12: (time.sleep(0.5), [
        {"title": "t%s" % feeds, "snippet": "s", "url": "https://x.test/a", "source": "x.test",
         "ts": 1.0}])[1])
    monkeypatch.setattr(ne, "_source_trust_meta", lambda d, b, bo: {})
    monkeypatch.setattr(ne, "_register_news_provenance", lambda items: items)
    cats = [c for c in ne.NEWS_CATEGORIES if ne.category_meta(c)]
    assert len(cats) >= 4
    t = time.time()
    items = ne._fetch_news_items(categories=cats, limit_per=2)
    took = time.time() - t
    assert took < 1.5, "categories fetched one after another: %.1fs" % took
    assert [i["category"] for i in items] == [c for c in cats]      # order kept


def test_a_slow_feed_never_holds_a_spoken_turn(monkeypatch):
    """No cache and a 30 s live fetch: the answer comes from the archive
    within the wait budget, and the fetch still lands in the cache."""
    monkeypatch.setattr(ne, "_fetch_news_items",
                        lambda **kw: (time.sleep(3), _items(tag="live"))[1])
    monkeypatch.setattr(ne, "_news_items_from_archive", lambda *a, **k: _items(tag="archived"))
    monkeypatch.setattr(ne, "_register_news_provenance", lambda items: items)
    t = time.time()
    got = ne.news_items_fast(limit_per=8, wait_s=0.5)
    assert time.time() - t < 1.5
    assert got[0]["title"].startswith("archived")
    time.sleep(3.2)
    t = time.time()
    got = ne.news_items_fast(limit_per=8, wait_s=0.5)
    assert time.time() - t < 0.05 and got[0]["title"].startswith("live")


def test_a_stale_feed_answers_now_and_refreshes_behind(monkeypatch):
    calls = []
    monkeypatch.setattr(ne, "_fetch_news_items",
                        lambda **kw: (calls.append(1), time.sleep(1), _items(tag="new"))[2])
    ne._NEWS_CACHE[8] = (time.time() - ne.NEWS_FRESH_S - 1, _items(tag="old"))
    t = time.time()
    got = ne.news_items_fast(limit_per=8)
    assert time.time() - t < 0.05 and got[0]["title"].startswith("old")
    time.sleep(1.3)
    assert calls and ne.news_items_fast(limit_per=8)[0]["title"].startswith("new")


def test_the_search_news_tool_uses_the_fast_feed(monkeypatch):
    from agent_friday.services import agent
    monkeypatch.setattr(ne, "_fetch_news_items",
                        lambda **kw: (time.sleep(30), [])[1])      # the old stall
    if hasattr(ne, "_NEWS_CACHE"):
        ne._NEWS_CACHE[8] = (time.time(), _items(tag="cached"))
    t = time.time()
    out = json.loads(agent._tool_search_news({}) or "{}")
    assert time.time() - t < 0.5
    assert out["hits"][0]["title"].startswith("cached")


def test_the_archive_stops_reading_once_it_has_enough(monkeypatch):
    days = [("2026-09-%02d" % d, None) for d in range(25, 0, -1)]
    loaded = []
    monkeypatch.setattr(ne, "_archive_day_files", lambda: days)
    monkeypatch.setattr(ne, "_load_archive_day", lambda ds: (loaded.append(ds), [
        {"title": "%s-%d" % (ds, i), "category": "tech", "source": "x.test",
         "url": "https://x.test", "published_at": ds} for i in range(10)])[1])
    got = list(ne._iter_archive(category="tech"))[:0]
    assert got == []
    loaded.clear()
    it = ne._iter_archive(category="tech")
    first = [next(it) for _ in range(8)]
    assert first[0]["title"].startswith("2026-09-25") and loaded == ["2026-09-25"]
    assert ne._read_archive(category="tech")[:1] == first[:1]      # same order as before


# ── the trust file ───────────────────────────────────────────────────────────

def test_trust_lookups_parse_the_file_once_until_it_changes(tmp_path, monkeypatch):
    from agent_friday import source_trust_graph as stg
    g = stg.SourceTrustGraph(friday_dir=tmp_path)
    g.record_article_seen("reuters.com")
    stg._READ_CACHE.clear()
    # An unknown domain's seed consults the owner's Local beat, which is read
    # from settings.json; that is a different file with its own cache, so it
    # is held constant here and only trust-file parses are counted.
    monkeypatch.setattr(stg, "local_beat_sources", lambda: ())
    parses = []
    real = stg.json.loads
    monkeypatch.setattr(stg.json, "loads",
                        lambda s, *a, **k: (parses.append(1), real(s, *a, **k))[1])
    for _ in range(200):
        g.score_for("reuters.com")
        g.dimensions_for("example.org")
    assert len(parses) == 1, "the trust file was parsed %d times" % len(parses)
    assert g.get("apnews.com") is None
    time.sleep(0.05)                       # a distinct mtime on coarse clocks
    g.record_article_seen("apnews.com")    # a write
    assert g.get("apnews.com") is not None, "a write was not seen by the next read"
    rec = g.get("reuters.com")
    rec["trust_score"] = -1                # a caller scribbling on its copy
    assert g.score_for("reuters.com") != -1


# ── the Live bridge ──────────────────────────────────────────────────────────

def test_tool_calls_in_one_message_run_together():
    from agent_friday.routes.voice import _run_calls_concurrently

    async def one(secs):
        await asyncio.sleep(secs)
        return secs

    t = time.time()
    out = asyncio.run(_run_calls_concurrently([0.6, 0.6, 0.3], one))
    took = time.time() - t
    assert out == [0.6, 0.6, 0.3]
    assert took < 1.0, "tool calls ran one after another: %.1fs" % took


def test_a_crashing_call_does_not_lose_the_others():
    from agent_friday.routes.voice import _run_calls_concurrently

    async def one(x):
        if x == "boom":
            raise RuntimeError("tool exploded")
        return x

    assert asyncio.run(_run_calls_concurrently(["a", "boom", None, "b"], one)) == ["a", "b"]


def test_the_gemini_loop_does_not_wait_on_a_tool():
    """The writer must hand tool calls to a task, not await them inline:
    inline, nothing from Gemini (transcripts, interruptions, GoAway) was read
    until the slowest tool finished."""
    import inspect
    from agent_friday.routes import voice
    src = inspect.getsource(voice)
    body = src[src.index("async def writer(sess, sdone):"):src.index("async def no_audio_watchdog")]
    assert "await _run_tool_calls(sess, _tc)" not in body       # the old inline await
    assert "asyncio.create_task(_tool_job())" in body


def test_a_session_warms_its_reads_on_open(monkeypatch):
    from agent_friday.services import voice_engine as ve
    slow = []
    monkeypatch.setattr(ve, "_fetch_calendar_today", lambda: (slow.append("cal"), time.sleep(0.4), [])[2])
    monkeypatch.setattr(ve, "_collect_messages", lambda limit=25: (slow.append("mail"), time.sleep(0.4), ([], "empty"))[2])
    monkeypatch.setattr(ne, "warm_news_cache", lambda limit_per=8: None)
    ve._VOICE_READS.clear()
    t = time.time()
    ve.warm_voice_reads()
    assert time.time() - t < 0.1                                  # never blocks the open
    time.sleep(1.0)
    t = time.time()
    ve._tool_query_calendar({})
    ve._tool_check_email({})
    assert time.time() - t < 0.1 and sorted(slow) == ["cal", "mail"]
    ve._VOICE_READS.clear()
