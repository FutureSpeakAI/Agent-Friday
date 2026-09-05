"""Tests for the 2026-09-04 fix: `_parse_feed`/`_rss_results` had no real
timeout, which blocked a pool worker (and the archiver thread waiting on the
pool) forever against a feed server that accepts a connection and never
finishes sending. See KNOWN_ISSUES.md for the incident this was found
investigating (a stack-overflow crash of the whole server process — this
fix does not claim to be *the* cause, only an independent, unambiguous bug
found while investigating it).

Uses a real, local, deliberately-silent TCP listener rather than mocking
`urllib`/`feedparser` -- the whole point is proving the SOCKET actually
gives up in bounded time, which a mock can't demonstrate.
"""
from __future__ import annotations

import socket
import threading
import time

import pytest

from agent_friday.services import news_engine as ne


@pytest.fixture
def hanging_server():
    """A TCP listener that accepts a connection and then sends nothing,
    ever -- exactly the failure mode `urllib.request.urlopen` has no
    default timeout for (a "connects fine, never finishes responding"
    server, as opposed to a refused/unreachable connection, which fails
    fast on its own)."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    stop = threading.Event()

    def _accept_and_stall():
        srv.settimeout(1.0)
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            # Accept the connection, read the request, respond with nothing.
            # The client is left waiting on a read with no data and no close.
            try:
                conn.settimeout(2.0)
                conn.recv(4096)
            except Exception:
                pass
            # Deliberately never conn.send() or conn.close() here; closing
            # happens only when the fixture tears down, below.
            while not stop.is_set():
                time.sleep(0.1)
            try:
                conn.close()
            except Exception:
                pass

    t = threading.Thread(target=_accept_and_stall, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}/feed.xml"
    finally:
        stop.set()
        srv.close()
        t.join(timeout=3)


class TestParseFeedTimeout:
    def test_a_feed_that_never_responds_still_returns_within_the_configured_timeout(
            self, hanging_server):
        """GREEN: today's code. A short, explicit timeout is what makes this
        return at all rather than hang indefinitely."""
        start = time.monotonic()
        result = ne._parse_feed(hanging_server, timeout=1.5)
        elapsed = time.monotonic() - start
        assert result == [], "a feed that sent nothing should fail soft to []"
        assert elapsed < 5.0, (
            f"took {elapsed:.1f}s against a 1.5s timeout -- the fetch is not "
            f"actually bounded")

    def test_default_timeout_is_finite_and_applied(self, monkeypatch, hanging_server):
        """The production default (`_RSS_FETCH_TIMEOUT_S`) is what's used
        when a caller doesn't override it -- proven by shrinking it rather
        than waiting out the real 8s value."""
        monkeypatch.setattr(ne, "_RSS_FETCH_TIMEOUT_S", 1.0)
        start = time.monotonic()
        result = ne._parse_feed(hanging_server)
        elapsed = time.monotonic() - start
        assert result == []
        assert elapsed < 4.0, f"took {elapsed:.1f}s against a 1.0s default timeout"

    def test_red_without_a_timeout_the_same_fetch_would_hang(self, hanging_server):
        """RED, reproduced deliberately: calling urlopen the OLD way (no
        timeout at all) against the same hanging server does NOT return
        quickly -- proving the fixture genuinely exercises the failure mode,
        not just a fast-failing connection. Bounded to a few seconds here
        only so the suite doesn't actually hang; the assertion is that it's
        SLOWER than the fixed path's bound, not that it hangs forever."""
        import urllib.request
        req = urllib.request.Request(hanging_server)
        start = time.monotonic()
        with pytest.raises(TimeoutError):
            # A generous but finite outer timeout so this test itself
            # terminates -- the old code path had NO timeout at all, so if
            # this returns before the outer bound, it did so only because
            # something else (not the fetch) gave up.
            urllib.request.urlopen(req, timeout=3.0)
        elapsed = time.monotonic() - start
        assert elapsed >= 2.5, (
            "the hanging server responded too fast to be exercising the "
            "no-data-ever-sent case this test is supposed to reproduce")


class TestRssResultsBoundedShutdown:
    def test_one_wedged_feed_does_not_block_the_whole_pool_past_as_completed(
            self, hanging_server, monkeypatch):
        """GREEN: today's code. Before the fix, `with ThreadPoolExecutor(...)
        as pool:` called shutdown(wait=True) unconditionally on exit, so a
        worker stuck fetching the hanging feed blocked _rss_results (and
        anything waiting on it, like the news archiver) long after
        as_completed's own 20s timeout gave up. A working, fast feed is
        included alongside the hanging one so success is proven by content
        arriving, not merely by the call returning."""
        monkeypatch.setattr(ne, "_RSS_FETCH_TIMEOUT_S", 1.0)

        def _fast_parse_feed(url, limit=12, timeout=None):
            if url == "https://fast.example/feed":
                return [{"title": "Real headline", "snippet": "", "url": url,
                         "source": "fast.example", "ts": time.time()}]
            return ne._parse_feed(url, limit=limit, timeout=timeout)

        monkeypatch.setattr(ne, "_parse_feed", _fast_parse_feed)

        start = time.monotonic()
        out = ne._rss_results([hanging_server, "https://fast.example/feed"])
        elapsed = time.monotonic() - start

        assert elapsed < 5.0, (
            f"_rss_results took {elapsed:.1f}s -- the wedged feed's worker "
            f"blocked the pool shutdown past its own fetch timeout")
        assert any(it["title"] == "Real headline" for it in out), (
            "the working feed's results were lost, not just delayed")
