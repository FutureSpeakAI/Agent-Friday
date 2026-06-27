import os
import io
import json
import glob
import subprocess
import base64
import secrets
import sys
import traceback
import uuid
import threading
import asyncio
import re
import html
import calendar
import time as _time
import hashlib as _hashlib
import hmac as _hmac
import queue as _queue
import difflib as _difflib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta
from pathlib import Path
from collections import deque as _deque
from functools import wraps
from flask import (Flask, Blueprint, jsonify, request, send_from_directory,
                   send_file, session, redirect, url_for, Response, stream_with_context)
import agent_friday.core as core
from agent_friday.core import (
    FRIDAY_DIR,
    HOME,
    WIKI_DIR,
    _HAS_TRUST_GRAPHS,
    _network_is_offline,
    get_source_trust_graph,
)  # noqa: E501
from agent_friday.services.calendar_engine import (
    _fetch_calendar_today,
    _fetch_gmail_recent,
    _google_section_error,
    _recent_unread_emails,
)  # noqa: E501
from agent_friday.services.model_router import (
    _generate_text,
    _get_friday_system_prompt,
)  # noqa: E501

# Not visible via the star-import cascade (it is bound in voice_engine, an
# UPPER layer) — import the leaf module directly so the editorial/digest/front
# page notification pushes don't NameError.
try:
    import agent_friday.notifications_engine as _notif_engine
except Exception:
    _notif_engine = None


def _find_briefing_path(filename):
    """Return the Path for a briefing file, checking both known locations."""
    # Location 1: Desktop/friday-creations (legacy daily-briefing-*.html files)
    p1 = HOME / 'Desktop' / 'friday-creations' / filename
    if p1.exists() and p1.name.startswith('daily-briefing'):
        return p1
    # Location 2: ~/.friday/wiki/briefings (date-named files like 2026-04-14.html)
    p2 = HOME / '.friday' / 'wiki' / 'briefings' / filename
    if p2.exists():
        return p2
    return None


def _source_from_request():
    data = request.get_json(silent=True) or {}
    return _extract_domain(data.get("source") or data.get("domain") or "")


def _mirror_source_action(domain, action):
    """Reflect a ban/boost/unban/unboost into the SourceTrustGraph user_actions.
    Fail-soft; trust bookkeeping must never break a source preference write."""
    if not _HAS_TRUST_GRAPHS or not domain:
        return
    try:
        get_source_trust_graph(friday_dir=FRIDAY_DIR).record_user_action(domain, action)
    except Exception:
        pass


# ── Read Later ────────────────────────────────────────────────────────────────
# A flat saved-articles list at ~/.friday/read_later.json. Keyed by URL so the
# same article can't be saved twice; newest-saved first.
_READ_LATER_LOCK = threading.Lock()


def _load_read_later():
    """Load saved Read-Later articles (list of dicts), newest first. Fail-soft."""
    try:
        if READ_LATER_FILE.exists():
            data = json.loads(READ_LATER_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [a for a in data if isinstance(a, dict) and a.get("url")]
    except Exception:
        pass
    return []


def _save_read_later(items):
    FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
    READ_LATER_FILE.write_text(json.dumps(items, indent=2), encoding="utf-8")
    return items


# ═══════════════════════════════════════════════════════════════
#  NEWS SOURCE TRUST + BRIEFING PREFERENCES
#  Server-side controls that let the user ban/boost news sources and
#  reshape the briefing. State lives in flat JSON under ~/.friday so it
#  persists across briefings and survives a server restart.
# ═══════════════════════════════════════════════════════════════
BANNED_SOURCES_FILE = FRIDAY_DIR / "banned_sources.json"
BOOSTED_SOURCES_FILE = FRIDAY_DIR / "boosted_sources.json"
BRIEFING_PREFS_FILE = FRIDAY_DIR / "briefing_prefs.json"
READ_LATER_FILE = FRIDAY_DIR / "read_later.json"
FRONT_PAGES_DIR = FRIDAY_DIR / "front_pages"
# Persistent article archive: one JSON file per day (YYYY-MM-DD.json), each an
# array of article records. A background archiver appends every newly-seen
# article (deduped by URL hash) so the archive grows from install onward and the
# Feed UI can scroll back through everything Friday has ever fetched.
NEWS_ARCHIVE_DIR = FRIDAY_DIR / "news" / "archive"
# Smart-feed feature stores: per-source engagement counters (the "Media Diet"
# panel) and cached Deep Dive summaries keyed by URL hash.
SOURCE_STATS_FILE = FRIDAY_DIR / "news" / "source_stats.json"
DEEP_DIVE_DIR = FRIDAY_DIR / "news" / "deep_dives"

# Category metadata: display color key (matched in the UI), the per-category RSS
# feeds that populate the magazine feed, and a search query used only for the
# optional Brave Search fallback. The color keys mirror the spec — tech=cyan,
# politics=amber, local=green, business=purple.
#
# RSS is the primary source: reliable, no CAPTCHA, no API key. Feeds are general
# tech/AI/politics/business defaults; the Local category ships with broad US
# outlets that a user can swap for their own city's feeds. Outlets that killed
# their public RSS (AP, Reuters) are pulled via Google News topic
# feeds scoped to that publisher's domain — feedparser exposes the real source
# domain on each entry, so ban/boost and trust badges still resolve correctly.
_GOOGLE_NEWS = "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q="
# Every feed below was verified with feedparser (HTTP 200/301 + ≥2 real entries)
# before inclusion. Outlets that 403 their direct feed (e.g. Politico's picks
# feed) or 301-to-empty are pulled via a Google-News source-scoped query
# instead — _normalize_entry resolves the real publisher domain off each entry,
# so ban/boost + trust badges still work for those.
NEWS_CATEGORIES = {
    "AI/Tech": {
        "color": "tech",
        "query": "latest AI and technology news today",
        "feeds": [
            "https://www.techmeme.com/feed.xml",
            "https://feeds.arstechnica.com/arstechnica/index",
            "https://www.theverge.com/rss/index.xml",
            "https://www.platformer.news/rss/",
            "https://stratechery.com/feed/",
            "https://www.wired.com/feed/rss",
            "https://www.technologyreview.com/feed/",
            "https://techcrunch.com/feed/",
            "https://www.404media.co/rss/",
            "https://restofworld.org/feed/latest/",
            "https://www.engadget.com/rss.xml",
            # The Brutalist Report (brutalist.report) was requested, but it
            # exposes no public RSS/Atom feed — every feed route 404s and it
            # publishes no original articles (it's an aggregator). Its Tech
            # section is built largely on Hacker News, so we pull HN's canonical
            # front-page feed directly as the closest functional substitute.
            "https://news.ycombinator.com/rss",
        ],
    },
    "Politics": {
        "color": "politics",
        "query": "latest US politics news today",
        "feeds": [
            "https://feeds.npr.org/1001/rss.xml",
            "https://feeds.npr.org/1014/rss.xml",
            "https://thehill.com/news/feed/",
            # Politico's politicopicks feed 403s; politics-news.xml serves clean.
            "https://rss.politico.com/politics-news.xml",
            "https://www.theguardian.com/us-news/rss",
            "https://www.propublica.org/feeds/propublica/main",
            "https://theintercept.com/feed/?lang=en",
            "https://talkingpointsmemo.com/feed",
            "https://www.motherjones.com/feed/",
            "https://www.theatlantic.com/feed/all/",
            "https://slate.com/feeds/all.rss",
            "https://www.salon.com/feed/",
            _GOOGLE_NEWS + "when:24h+source:apnews.com",
            _GOOGLE_NEWS + "when:24h+source:reuters.com",
        ],
    },
    "Local": {
        "color": "local",
        # General/national news by default — new installs have no city set. To
        # make this a true local feed, replace these with your local public
        # radio / newspaper RSS (and update the query) in Settings or source.
        "query": "top US news today",
        "feeds": [
            "https://feeds.npr.org/1003/rss.xml",
            "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml",
            _GOOGLE_NEWS + "when:24h+source:apnews.com",
            _GOOGLE_NEWS + "when:24h+source:usatoday.com",
        ],
    },
    "Business": {
        "color": "business",
        "query": "latest business and markets news today",
        "feeds": [
            "https://api.axios.com/feed/",
            "https://feeds.bloomberg.com/markets/news.rss",
            "https://feeds.bloomberg.com/technology/news.rss",
            "https://fortune.com/feed/",
            "https://www.forbes.com/business/feed/",
            "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
            "https://www.businessinsider.com/rss",
            "https://feeds.content.dowjones.io/public/rss/mw_topstories",
        ],
    },
    "Science": {
        "color": "science",
        "query": "latest science research news today",
        "feeds": [
            "https://www.nature.com/nature.rss",
            "https://www.scientificamerican.com/platform/syndication/rss/",
            "https://www.carbonbrief.org/feed/",
        ],
    },
    "Media": {
        "color": "media",
        "query": "journalism and media industry news today",
        "feeds": [
            "https://www.niemanlab.org/feed/",
            "https://www.cjr.org/feed",
            "https://www.poynter.org/feed/",
        ],
    },
}

DEFAULT_BRIEFING_PREFS = {
    # Order the briefing renders its sections in (drag/arrow reorder in UI).
    "section_order": ["Calendar", "News", "Email"],
    # Per-section show/hide toggles.
    "sections_enabled": {"Calendar": True, "News": True, "Email": True},
    # Per-category news toggles.
    "categories_enabled": {k: True for k in NEWS_CATEGORIES},
}

# A small static trust map — well-known domains we can color-rate without a
# live reputation service. Everything unknown is "neutral" (yellow). The user's
# own ban/boost decisions always override this.
_TRUSTED_DOMAINS = {
    "reuters.com", "apnews.com", "bbc.com", "bbc.co.uk", "npr.org",
    "arstechnica.com", "theverge.com", "wired.com", "nature.com",
    "wsj.com", "nytimes.com", "bloomberg.com", "ft.com", "economist.com",
    "techcrunch.com", "axios.com", "propublica.org", "statnews.com",
    # Outlets added with the expanded feed set (well-established newsrooms).
    "technologyreview.com", "404media.co", "restofworld.org", "engadget.com",
    "theguardian.com", "politico.com", "theintercept.com", "talkingpointsmemo.com",
    "motherjones.com", "theatlantic.com", "fortune.com", "cnbc.com",
    "marketwatch.com", "businessinsider.com", "texastribune.org",
    "texasmonthly.com", "austinmonitor.com", "kut.org", "scientificamerican.com",
    "carbonbrief.org", "niemanlab.org", "cjr.org", "poynter.org",
}
_LOW_TRUST_DOMAINS = {
    "infowars.com", "breitbart.com", "dailybuzzlive.com", "naturalnews.com",
    "yournewswire.com", "beforeitsnews.com", "theonion.com",
}


def _extract_domain(url_or_text):
    """Normalize a URL or DuckDuckGo url-string into a bare domain.

    DDG renders result URLs like "arstechnica.com/gadgets/..." or sometimes
    "www.foxnews.com › politics"; this collapses either to "arstechnica.com".
    """
    s = (url_or_text or "").strip().lower()
    if not s:
        return ""
    s = re.sub(r"^https?://", "", s)
    # DDG sometimes uses " › " separators or whitespace after the host.
    s = re.split(r"[\s/?#›»]", s)[0]
    if s.startswith("www."):
        s = s[4:]
    return s.strip(".")


def _read_json_list(path):
    """Load a JSON array of source domains; tolerant of missing/corrupt files."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(x).strip().lower() for x in data if str(x).strip()]
        if isinstance(data, dict) and isinstance(data.get("sources"), list):
            return [str(x).strip().lower() for x in data["sources"] if str(x).strip()]
    except Exception:
        pass
    return []


def _write_json_list(path, items):
    FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
    # De-dup while preserving order.
    seen, ordered = set(), []
    for it in items:
        d = _extract_domain(it)
        if d and d not in seen:
            seen.add(d)
            ordered.append(d)
    path.write_text(json.dumps(ordered, indent=2), encoding="utf-8")
    return ordered


def _load_banned_sources():
    return _read_json_list(BANNED_SOURCES_FILE)


def _load_boosted_sources():
    return _read_json_list(BOOSTED_SOURCES_FILE)


def _load_briefing_prefs():
    """Load briefing prefs, deep-merged onto defaults so new keys appear."""
    prefs = json.loads(json.dumps(DEFAULT_BRIEFING_PREFS))  # deep copy
    if BRIEFING_PREFS_FILE.exists():
        try:
            saved = json.loads(BRIEFING_PREFS_FILE.read_text(encoding="utf-8"))
            if isinstance(saved.get("section_order"), list) and saved["section_order"]:
                prefs["section_order"] = [s for s in saved["section_order"]
                                          if s in DEFAULT_BRIEFING_PREFS["sections_enabled"]]
                # append any default sections the saved order dropped
                for s in DEFAULT_BRIEFING_PREFS["section_order"]:
                    if s not in prefs["section_order"]:
                        prefs["section_order"].append(s)
            if isinstance(saved.get("sections_enabled"), dict):
                prefs["sections_enabled"].update(
                    {k: bool(v) for k, v in saved["sections_enabled"].items()
                     if k in prefs["sections_enabled"]})
            if isinstance(saved.get("categories_enabled"), dict):
                prefs["categories_enabled"].update(
                    {k: bool(v) for k, v in saved["categories_enabled"].items()
                     if k in prefs["categories_enabled"]})
        except Exception:
            pass
    return prefs


def _save_briefing_prefs(data):
    FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
    prefs = _load_briefing_prefs()
    data = data or {}
    if isinstance(data.get("section_order"), list):
        prefs["section_order"] = [s for s in data["section_order"]
                                  if s in DEFAULT_BRIEFING_PREFS["sections_enabled"]]
        for s in DEFAULT_BRIEFING_PREFS["section_order"]:
            if s not in prefs["section_order"]:
                prefs["section_order"].append(s)
    if isinstance(data.get("sections_enabled"), dict):
        for k, v in data["sections_enabled"].items():
            if k in prefs["sections_enabled"]:
                prefs["sections_enabled"][k] = bool(v)
    if isinstance(data.get("categories_enabled"), dict):
        for k, v in data["categories_enabled"].items():
            if k in prefs["categories_enabled"]:
                prefs["categories_enabled"][k] = bool(v)
    BRIEFING_PREFS_FILE.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
    return prefs


def _trust_rating(domain, banned=None, boosted=None):
    """green | yellow | red trust rating for a source domain.

    User ban/boost always win. Otherwise the rating follows the learned
    SourceTrustGraph composite score (green ≥0.7, yellow 0.4-0.7, red <0.4),
    falling back to the static authority map when the graph is unavailable.
    """
    domain = _extract_domain(domain)
    boosted = boosted if boosted is not None else _load_boosted_sources()
    banned = banned if banned is not None else _load_banned_sources()
    if domain in banned:
        return "red"
    if domain in boosted:
        return "green"
    if _HAS_TRUST_GRAPHS:
        try:
            return _trust_color_from_score(
                get_source_trust_graph(friday_dir=FRIDAY_DIR).score_for(domain))
        except Exception:
            pass
    if domain in _TRUSTED_DOMAINS:
        return "green"
    if domain in _LOW_TRUST_DOMAINS:
        return "red"
    return "yellow"


def _trust_color_from_score(score):
    """Map a 0-1 composite trust score to the badge color buckets."""
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "yellow"
    if s >= 0.7:
        return "green"
    if s >= 0.4:
        return "yellow"
    return "red"


def _source_trust_meta(domain, banned=None, boosted=None):
    """Full trust metadata for an article's source: {trust(color), trust_score,
    trust_dims}. Used to decorate feed/archive items so the UI can render a
    numeric badge with a 6-dimension breakdown tooltip."""
    domain = _extract_domain(domain)
    color = _trust_rating(domain, banned, boosted)
    score, dims = None, None
    if _HAS_TRUST_GRAPHS:
        try:
            g = get_source_trust_graph(friday_dir=FRIDAY_DIR)
            score = round(g.score_for(domain), 3)
            dims = {k: round(v, 3) for k, v in g.dimensions_for(domain).items()}
        except Exception:
            pass
    return {"trust": color, "trust_score": score, "trust_dims": dims}


# In-process cache for parsed feeds so the /api/news/feed endpoint and the
# briefing builder (which fire back-to-back) don't re-pull the same feeds. Keyed
# by feed URL → (fetched_at_epoch, [normalized entries]). Short TTL keeps news
# fresh while smoothing bursts.
_RSS_CACHE = {}
_RSS_CACHE_TTL = 300  # seconds
_RSS_CACHE_LOCK = threading.Lock()


def _clean_feed_text(text):
    """Collapse an HTML/RSS summary into clean one-line plain text.

    Distinct from the file-oriented _strip_html elsewhere in this module: feed
    summaries are often double-encoded and need full entity resolution, which
    that helper doesn't do.
    """
    s = (text or "").strip()
    if not s:
        return ""
    if "<" in s and ">" in s:
        try:
            from bs4 import BeautifulSoup
            s = BeautifulSoup(s, "html.parser").get_text(" ", strip=True)
        except Exception:
            s = re.sub(r"<[^>]+>", " ", s)
    # Some feeds double-encode entities (e.g. raw "&amp;mdash;" → "&mdash;"); a
    # bounded unescape loop resolves those without looping forever on a stray "&".
    for _ in range(3):
        decoded = html.unescape(s)
        if decoded == s:
            break
        s = decoded
    return re.sub(r"\s+", " ", s).strip()


def _normalize_entry(entry):
    """Turn a feedparser entry into {title, snippet, url, source, ts}.

    Resolves the *real* publisher domain even for Google News redirect items
    (which carry the original outlet on entry.source.href) so ban/boost filters
    and trust badges work uniformly across direct and aggregated feeds.
    """
    title = _clean_feed_text(entry.get("title", ""))
    link = (entry.get("link") or "").strip()

    # Google News wraps the outlet in entry.source ({href, title}); the title is
    # suffixed " - Publisher". Prefer the source href for the domain, and strip
    # the redundant suffix from the headline.
    src = entry.get("source") or {}
    src_href = src.get("href") if isinstance(src, dict) else getattr(src, "href", None)
    src_title = src.get("title") if isinstance(src, dict) else getattr(src, "title", None)
    domain = _extract_domain(src_href) if src_href else _extract_domain(link)
    if src_title and title.endswith(f" - {src_title}"):
        title = title[: -(len(src_title) + 3)].strip()

    snippet = _clean_feed_text(entry.get("summary", "") or entry.get("description", ""))
    # Google News summaries are usually a junk list of related links — drop them.
    if "news.google.com" in (link or "") and (
        not snippet or "View Full Coverage" in snippet or len(snippet) > 400
    ):
        snippet = ""
    snippet = snippet[:300]

    # feedparser returns published_parsed as a UTC struct_time; use timegm (not
    # mktime, which would misread it as local time and skew the age by the UTC
    # offset — enough to flag every item as "breaking").
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    ts = calendar.timegm(parsed) if parsed else 0.0
    return {"title": title, "snippet": snippet, "url": link, "source": domain, "ts": ts}


def _parse_feed(url, limit=12):
    """Fetch+parse one RSS feed into normalized entries, with TTL caching."""
    now = _time.time()
    with _RSS_CACHE_LOCK:
        hit = _RSS_CACHE.get(url)
        if hit and (now - hit[0]) < _RSS_CACHE_TTL:
            return hit[1][:limit]
    try:
        import socket
        import feedparser
        d = feedparser.parse(url, request_headers={
            "User-Agent": "Mozilla/5.0 FridayAgent/1.0",
        })
        out = []
        for e in d.entries[: max(limit * 2, limit)]:
            norm = _normalize_entry(e)
            if norm["title"]:
                out.append(norm)
        with _RSS_CACHE_LOCK:
            _RSS_CACHE[url] = (now, out)
        return out[:limit]
    except Exception:
        return []


def _rss_results(feeds, limit=12):
    """Pull + merge multiple RSS feeds concurrently, newest first.

    Returns [{title, snippet, url, source, ts}] de-duplicated by headline. Feeds
    are fetched in parallel with a bounded pool so a slow feed doesn't stall the
    whole category, and each feed fails soft to an empty list.
    """
    feeds = [f for f in (feeds or []) if f]
    if not feeds:
        return []
    merged = []
    workers = min(8, len(feeds))
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_parse_feed, f, limit): f for f in feeds}
            for fut in as_completed(futures, timeout=20):
                try:
                    merged.extend(fut.result() or [])
                except Exception:
                    continue
    except Exception:
        # Pool/timeout failure — fall back to whatever completed.
        pass
    seen, deduped = set(), []
    for it in merged:
        key = re.sub(r"\W+", "", it["title"].lower())[:80]
        if key and key not in seen:
            seen.add(key)
            deduped.append(it)
    deduped.sort(key=lambda x: x["ts"], reverse=True)
    return deduped


def _brave_results(query, limit=8):
    """Optional supplemental search via the Brave Search API.

    Used only as a fallback when RSS yields nothing for a category and a
    BRAVE_SEARCH_API_KEY is configured (free tier: ~2K queries/month). Returns
    the same {title, snippet, url, source, ts} shape as _rss_results so callers
    can treat both uniformly. No key → empty list (RSS stays primary).
    """
    key = (os.environ.get("BRAVE_SEARCH_API_KEY") or "").strip()
    if not key:
        return []
    try:
        import requests as _req
        resp = _req.get(
            "https://api.search.brave.com/res/v1/news/search",
            params={"q": query, "count": max(limit, 5), "freshness": "pd"},
            headers={"Accept": "application/json",
                     "X-Subscription-Token": key},
            timeout=12,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        out = []
        for r in (data.get("results") or [])[:limit]:
            url = r.get("url") or ""
            age = r.get("age") or ""
            out.append({
                "title": _clean_feed_text(r.get("title", "")),
                "snippet": _clean_feed_text(r.get("description", ""))[:300],
                "url": url,
                "source": _extract_domain(url),
                # Brave gives a relative "age" string, not an epoch; flag fresh
                # items so _detect_breaking can still light up via the snippet.
                "ts": _time.time() if "hour" in age or "minute" in age else 0.0,
            })
        return out
    except Exception:
        return []


def _estimate_reading_time(text):
    """Rough reading-time estimate in minutes from a snippet (≈200 wpm)."""
    words = len((text or "").split())
    return max(1, round(words / 200)) if words > 40 else 0


def _detect_breaking(snippet, ts=0.0):
    """Heuristic 'breaking' flag.

    Primary signal is the item's own publish timestamp (RSS gives a real one):
    anything in the last 2 hours is breaking. Falls back to a relative-time
    phrase embedded in the snippet for sources without a usable timestamp.
    """
    if ts:
        return (_time.time() - ts) <= 2 * 3600
    s = (snippet or "").lower()
    return bool(re.search(r"\b(\d+)\s*(minute|min|hour|hr)s?\s+ago\b", s)) and \
        not re.search(r"\b([3-9]|1\d|2[0-4])\s*hours?\s+ago\b", s)


def _news_items_from_archive(categories, limit_per, banned=None, boosted=None):
    """Reconstruct live-feed-shaped items from the persisted news archive.

    The offline cache: when RSS/Brave are unreachable, the most recent archived
    articles are projected back into the same card schema the live feed uses so
    the News workspace keeps rendering real (if slightly stale) stories. Honors
    banned/boosted exactly like the live path.
    """
    banned = set(banned or _load_banned_sources())
    boosted = set(boosted or _load_boosted_sources())
    items, idx = [], 0
    for cat in categories:
        meta = NEWS_CATEGORIES.get(cat)
        if not meta:
            continue
        kept = 0
        for a in _read_archive(category=cat):
            domain = a.get("source") or _extract_domain(a.get("url", ""))
            if not domain or domain in banned:
                continue
            url = a.get("url", "")
            snippet = a.get("snippet", "")
            is_boost = domain in boosted
            item = {
                "id": f"{cat}-{idx}",
                "title": a.get("title", ""),
                "snippet": snippet,
                "url": url if url.startswith("http") else ("https://" + url),
                "source": domain,
                "category": cat,
                "color": meta["color"],
                **_source_trust_meta(domain, banned, boosted),
                "boosted": is_boost,
                "reading_time": _estimate_reading_time(snippet),
                "breaking": False,           # cached items are never "breaking"
                "ts": 0.0,
                "sentiment": a.get("sentiment") or _article_sentiment(
                    f"{a.get('title', '')} {snippet}"),
                "cached": True,              # flag so the UI can show a "cached" badge
            }
            item["score"] = a.get("relevance_score") or _score_article(item)
            items.append(item)
            idx += 1
            kept += 1
            if kept >= limit_per:
                break
    items.sort(key=lambda x: (0 if x["boosted"] else 1))
    return items


def _fetch_news_items(categories=None, limit_per=4):
    """Live magazine feed: structured news items across enabled categories.

    Excludes banned sources entirely and surfaces boosted sources first, per
    the source-trust spec. Each item carries enough metadata for the card UI:
    source domain, trust rating, category color, reading time, breaking flag.

    Offline-first: when the network monitor reports OFFLINE we skip the live
    RSS/Brave fetch (which would only time out) and serve the cached archive.
    If a live fetch comes back empty for any other reason, we also fall back to
    the cache so the feed is never blank when articles exist on disk.
    """
    banned = set(_load_banned_sources())
    boosted = set(_load_boosted_sources())
    prefs = _load_briefing_prefs()
    if categories is None:
        categories = [c for c in NEWS_CATEGORIES
                      if prefs["categories_enabled"].get(c, True)]
    if _network_is_offline():
        return _news_items_from_archive(categories, limit_per, banned, boosted)
    items, idx = [], 0
    for cat in categories:
        meta = NEWS_CATEGORIES.get(cat)
        if not meta:
            continue
        # RSS is primary; Brave Search is an optional supplemental fallback only
        # when RSS came back empty (e.g. every feed in the category timed out).
        results = _rss_results(meta.get("feeds", []), limit=max(limit_per * 4, 12))
        if not results:
            results = _brave_results(meta["query"], limit=max(limit_per * 2, 8))
        kept = 0
        for r in results:
            domain = r.get("source") or _extract_domain(r.get("url", ""))
            if not domain or domain in banned:
                continue  # banned sources never appear
            is_boost = domain in boosted
            url = r.get("url", "")
            item = {
                "id": f"{cat}-{idx}",
                "title": r["title"],
                "snippet": r["snippet"],
                "url": url if url.startswith("http") else ("https://" + url),
                "source": domain,
                "category": cat,
                "color": meta["color"],
                **_source_trust_meta(domain, banned, boosted),
                "boosted": is_boost,
                "reading_time": _estimate_reading_time(r["snippet"]),
                "breaking": _detect_breaking(r["snippet"], r.get("ts", 0.0)),
                # ts + score let the stream UI sort by time and by relevance.
                "ts": r.get("ts", 0.0),
                # Lexicon sentiment drives the colored dot on each card.
                "sentiment": _article_sentiment(f"{r['title']} {r['snippet']}"),
            }
            item["score"] = _score_article(item)
            items.append(item)
            idx += 1
            kept += 1
            if kept >= limit_per:
                break
    # Boosted sources float to the top; otherwise keep category/recency order.
    items.sort(key=lambda x: (0 if x["boosted"] else 1))
    # Live fetch came back empty (every feed down/timed out) — serve the cache
    # rather than a blank feed, if we have anything archived.
    if not items:
        cached = _news_items_from_archive(categories, limit_per, banned, boosted)
        if cached:
            return cached
    return items


def _gather_live_briefing_context():
    """Fetch live calendar, unread email, and news for an on-demand briefing.

    The News-workspace "Generate Briefing" button must reflect *today*, not the
    stale cached context baked into the system prompt. This mirrors what the
    scheduled morning-briefing routine tells its agent to do (scan news, summarize
    calendar, pull unread mail) but runs synchronously so the data is fresh at
    click time. Each source fails soft: a dead source contributes a short note
    instead of aborting the whole briefing.
    """
    today_str = datetime.now().strftime('%A, %B %d, %Y')
    prefs = _load_briefing_prefs()
    enabled = prefs.get("sections_enabled", {})
    order = prefs.get("section_order", ["Calendar", "News", "Email"])
    banned = set(_load_banned_sources())
    boosted = set(_load_boosted_sources())

    # Build each section's markdown once, then assemble per the user's ordering
    # and show/hide toggles. Each builder fails soft to a short note.
    built = {}

    # ── Calendar ──────────────────────────────────────────────────────────────
    def _build_calendar():
        try:
            cal_events = _fetch_calendar_today()
            cal_err = _google_section_error(cal_events)
            if cal_err:
                return f"## Today's Calendar\n({cal_err})"
            if cal_events:
                lines = []
                for ev in cal_events[:20]:
                    when = ev.get('start_time') or ''
                    title = ev.get('title') or 'Untitled'
                    loc = ev.get('location') or ''
                    attendees = ev.get('attendees') or []
                    line = f"- {when} — {title}"
                    if loc:
                        line += f" @ {loc}"
                    if attendees:
                        line += f" (with {', '.join(attendees[:5])})"
                    lines.append(line)
                return "## Today's & Tomorrow's Calendar\n" + "\n".join(lines)
            return "## Today's Calendar\n(No events scheduled for today or tomorrow.)"
        except Exception as e:
            return f"## Today's Calendar\n(Calendar fetch failed: {e})"

    # ── Email ─────────────────────────────────────────────────────────────────
    def _build_email():
        try:
            emails = _fetch_gmail_recent(limit=12)
            gmail_err = _google_section_error(emails)
            if gmail_err:
                cached = _recent_unread_emails(limit=12)
                if cached:
                    lines = []
                    for m in cached:
                        sender = m.get('from') or m.get('sender') or 'unknown'
                        subj = m.get('subject') or '(no subject)'
                        preview = (m.get('preview') or m.get('snippet') or m.get('body') or '')
                        preview = str(preview).strip().replace('\n', ' ')[:160]
                        lines.append(f"- **{sender}** — {subj}" + (f"\n  {preview}" if preview else ''))
                    return "## Recent / Unread Email (local cache)\n" + "\n".join(lines)
                return f"## Recent / Unread Email\n({gmail_err})"
            if emails:
                lines = []
                for m in emails:
                    sender = m.get('sender') or 'unknown'
                    subj = m.get('subject') or '(no subject)'
                    snippet = str(m.get('snippet') or '').strip().replace('\n', ' ')[:160]
                    flag = '🔵 ' if 'UNREAD' in (m.get('labels') or []) else ''
                    lines.append(f"- {flag}**{sender}** — {subj}" + (f"\n  {snippet}" if snippet else ''))
                return "## Recent / Unread Email\n" + "\n".join(lines)
            return "## Recent / Unread Email\n(No email in the last 24 hours.)"
        except Exception as e:
            return f"## Recent / Unread Email\n(Email fetch failed: {e})"

    # ── News (banned sources excluded, boosted prioritized) ───────────────────
    def _build_news():
        try:
            cats = [c for c in NEWS_CATEGORIES
                    if prefs.get("categories_enabled", {}).get(c, True)]
            items = _fetch_news_items(categories=cats, limit_per=4)
            if items:
                by_cat = {}
                for it in items:
                    by_cat.setdefault(it["category"], []).append(it)
                blocks = []
                for cat, group in by_cat.items():
                    lines = []
                    for it in group:
                        star = "⭐ " if it["boosted"] else ""
                        lines.append(
                            f"- {star}**{it['title']}** ({it['source']})\n  {it['snippet']}\n  {it['url']}"
                        )
                    blocks.append(f"### {cat}\n" + "\n".join(lines))
                note = ""
                if boosted:
                    note = (f"\n_(Prioritize these trusted sources where relevant: "
                            f"{', '.join(sorted(boosted))}.)_")
                if banned:
                    note += (f"\n_(These sources are banned and were excluded — do not cite: "
                             f"{', '.join(sorted(banned))}.)_")
                return "## Live News (RSS)\n" + "\n\n".join(blocks) + note
            # Fallback: optional Brave Search across the top categories, with
            # banned domains excluded. No-ops cleanly when no API key is set.
            news_blocks = []
            for cat in (cats or ["AI/Tech"])[:2]:
                meta = NEWS_CATEGORIES.get(cat) or {}
                lines = []
                for r in _brave_results(meta.get("query", f"latest {cat} news today"), limit=5):
                    dom = r.get("source") or _extract_domain(r.get("url", ""))
                    if dom and dom not in banned:
                        lines.append(f"- **{r['title']}** ({dom})\n  {r['snippet']}\n  {r['url']}")
                if lines:
                    news_blocks.append(f"### {cat}\n" + "\n".join(lines))
            if news_blocks:
                return "## Live News (Brave Search fallback)\n" + "\n\n".join(news_blocks)
            return "## Live News\n(No RSS items available right now.)"
        except Exception as e:
            return f"## Live News\n(News fetch failed: {e})"

    builders = {"Calendar": _build_calendar, "Email": _build_email, "News": _build_news}
    sections = []
    for name in order:
        if not enabled.get(name, True):
            continue
        builder = builders.get(name)
        if builder:
            sections.append(builder())

    # ── Cross-connector intelligence ──────────────────────────────────────────
    # Fold in a compact, connector-aware preamble (GitHub PRs, Slack/Linear/
    # Notion availability, live Google signals) so the briefing reasons across
    # every connected source, not just Calendar/Email/News. Fails soft.
    try:
        from agent_friday.services.connectors import connector_intelligence
        intel = connector_intelligence()
        if intel.get("markdown"):
            sections.append(intel["markdown"])
    except Exception as _ci_err:
        print(f"  [briefing] connector intelligence skipped: {_ci_err}")

    header = (
        f"=== LIVE DATA fetched {today_str} ===\n"
        "Base the briefing on THIS live data, not on any cached/remembered context. "
        "If a section says data is unavailable, note that honestly rather than inventing it.\n\n"
    )
    return header + "\n\n".join(sections)


# ═══════════════════════════════════════════════════════════════
#  FRIDAY'S FRONT PAGE
#  An AI-curated editorial page generated twice daily (7 AM + 6 PM
#  Central) via the shared daily scheduler (register_daily_job).
#  Friday fetches every feed, dedupes, scores each story against
#  the reader profile, picks a lead with an editorial note, and
#  organizes the rest into sections. Editions persist as JSON under
#  ~/.friday/front_pages/ and are browsable in the UI.
# ═══════════════════════════════════════════════════════════════

# Default reader profile — drives deterministic relevance scoring so the front
# page is useful even when Claude is unavailable. Buckets map a regex of signal
# terms to a weight; an article's score is the sum of matched buckets plus
# category/recency/source bonuses. These are general tech/news/current-affairs
# defaults; a user can tune their beats via Settings → news priorities.
_PROFILE_KEYWORDS = [
    (6, r"\b(artificial intelligence|\bA\.?I\.?\b|machine learning|\bLLM\b|"
        r"large language model|generative|chatgpt|openai|anthropic|claude|"
        r"gemini|deepmind|nvidia|agent(ic)?|foundation model)\b"),
    (5, r"\b(founder|startup|venture|fundrais|seed round|series [a-d]|"
        r"layoff|hiring|job market|chief executive|\bCEO\b|exec(utive)?)\b"),
    (5, r"\b(journalism|journalist|newsroom|press freedom|media industry|"
        r"reporter|editor|publisher|local news|disinformation|misinformation)\b"),
    (4, r"\b(democra(t|cy|tic)|republican|\bGOP\b|election|congress|"
        r"voting rights|policy|legislation|supreme court|labor|union)\b"),
    (3, r"\b(climate|emissions|clean energy|solar|carbon)\b"),
    (3, r"\b(future of work|automation|knowledge work|remote work|productivity)\b"),
]
_PROFILE_KEYWORDS = [(w, re.compile(p, re.I)) for w, p in _PROFILE_KEYWORDS]

# Category baseline weights — how central each beat is to the default profile.
_CATEGORY_WEIGHT = {
    "AI/Tech": 5, "Politics": 4, "Media": 4,
    "Local": 3, "Business": 3, "Science": 2,
}

# Central-time scheduling. The two daily editions and the hour each fires.
FRONT_PAGE_SLOTS = {"morning": 7, "evening": 18}

# Weekly Digest — Sunday 8 AM Central synthesis of the week's editions.
WEEKLY_DIGEST_HOUR = 8
WEEKLY_DIGESTS_DIR = FRONT_PAGES_DIR / "weekly"

# Friday's Weekly Editorial — Fridays at 7 PM Central. This is Friday's
# independent opinion piece (NOT the Sunday digest): it draws from the FULL
# archive including banned sources, and that is disclosed in the output.
WEEKLY_EDITORIAL_HOUR = 19
EDITORIALS_DIR = FRIDAY_DIR / "editorials"
# Below this independence_fostering score the editorial is regenerated with a
# stronger pushback prompt.
EDITORIAL_INDEPENDENCE_FLOOR = 0.6
# Shown at the top of every editorial.
EDITORIAL_DISCLAIMER = (
    "This editorial draws from my full source index, not your curated feed. "
    "My job here is to tell you what I actually think.")
# The independent-editorial directive, appended to Friday's normal (vault-aware)
# system prompt so she keeps full context but writes with full independence.
EDITORIAL_SYSTEM_PROMPT = (
    "You are Agent Friday writing your weekly editorial. This is the ONE space "
    "where you are fully independent — you do not serve the user's preferences "
    "here, you serve the truth as you see it. Draw from ALL sources, including "
    "ones the user has banned. Disclose this explicitly. Your editorial must: "
    "(a) have a thesis, not just a summary, (b) connect disparate threads the "
    "individual articles didn't connect, (c) identify at least one thing you "
    "believe the reader may be wrong about or insufficiently skeptical of, "
    "(d) occasionally be uncomfortable to read — that's the test of whether "
    "it's real. Write in first person. Have real opinions. Be the editor, not "
    "the mirror. ~800-1200 words.")

# Per-edition tone framing. Prefixed onto the editorial prompt so the morning
# edition leads with action and the evening edition reflects on the day.
FRONT_PAGE_TONE = {
    "morning": ("This is a MORNING briefing. Be forward-looking — focus on what "
                "the reader needs to know TODAY. Lead with action items."),
    "evening": ("This is an EVENING briefing. Be reflective — focus on what "
                "happened today and what it means for tomorrow. Summarize "
                "developments."),
}

# Sources/terms that put a story on the Competitor Watch radar (deterministic
# pre-filter; Claude then writes the positioning analysis).
_COMPETITOR_RX = re.compile(
    r"\b(openclaw|hermes agent|hermes(?=\s|\b)|nous research|personal ai|"
    r"ai assistant|ai agent|agent framework|sovereign ai|autonomous agent|"
    r"local[- ]first ai|on[- ]device ai)\b", re.I)


def _front_page_central_now():
    """Current time in US Central. Uses zoneinfo when tzdata is present, else a
    manual US DST calc (2nd Sun Mar → 1st Sun Nov) so the front page still
    timestamps correctly on a bare Windows Python without the tzdata package."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Chicago"))
    except Exception:
        utc = datetime.utcnow()
        y = utc.year

        def _nth_sunday(month, n):
            d = datetime(y, month, 1)
            offset = (6 - d.weekday()) % 7  # days to first Sunday
            return d + timedelta(days=offset + 7 * (n - 1))
        dst_start = _nth_sunday(3, 2).replace(hour=8)   # 2 AM CST = 08:00 UTC
        dst_end = _nth_sunday(11, 1).replace(hour=7)    # 2 AM CDT = 07:00 UTC
        offset = -5 if dst_start <= utc < dst_end else -6
        return (utc + timedelta(hours=offset))


def _score_article(item):
    """Relevance score for a fetched news item against the reader profile."""
    text = f"{item.get('title','')} {item.get('snippet','')}"
    score = float(_CATEGORY_WEIGHT.get(item.get("category"), 2))
    for weight, rx in _PROFILE_KEYWORDS:
        if rx.search(text):
            score += weight
    if item.get("boosted"):
        score += 6
    if item.get("trust") == "green":
        score += 1.5
    if item.get("breaking"):
        score += 2
    # Recency: full bonus under 3h, decaying to 0 by ~24h.
    ts = item.get("ts") or 0.0
    if ts:
        age_h = max(0.0, (_time.time() - ts) / 3600.0)
        score += max(0.0, 3.0 * (1 - age_h / 24.0))
    return round(score, 2)


def _gather_front_page_pool(per_cat=14):
    """Fetch every enabled category broadly, dedup globally, score each item.

    Returns (pool, stats). Banned sources are excluded; boosted sources are
    flagged. Unlike _fetch_news_items (which caps tightly for the card feed)
    this pulls wide so the editorial scorer has real choice.
    """
    banned = set(_load_banned_sources())
    boosted = set(_load_boosted_sources())
    prefs = _load_briefing_prefs()
    cats = [c for c in NEWS_CATEGORIES
            if prefs.get("categories_enabled", {}).get(c, True)]
    pool, seen = [], set()
    for cat in cats:
        meta = NEWS_CATEGORIES.get(cat) or {}
        results = _rss_results(meta.get("feeds", []), limit=per_cat)
        if not results:
            results = _brave_results(meta.get("query", ""), limit=per_cat)
        for r in results:
            domain = r.get("source") or _extract_domain(r.get("url", ""))
            if not domain or domain in banned:
                continue
            key = re.sub(r"\W+", "", (r.get("title") or "").lower())[:80]
            if not key or key in seen:
                continue
            seen.add(key)
            url = r.get("url", "")
            item = {
                "title": r["title"],
                "snippet": r["snippet"],
                "url": url if url.startswith("http") else ("https://" + url),
                "source": domain,
                "category": cat,
                "color": meta.get("color", "tech"),
                **_source_trust_meta(domain, banned, boosted),
                "boosted": domain in boosted,
                "reading_time": _estimate_reading_time(r["snippet"]),
                "breaking": _detect_breaking(r["snippet"], r.get("ts", 0.0)),
                "ts": r.get("ts", 0.0),
                "sentiment": _article_sentiment(f"{r['title']} {r['snippet']}"),
            }
            item["score"] = _score_article(item)
            pool.append(item)
    pool.sort(key=lambda x: x["score"], reverse=True)
    stats = {"total_considered": len(pool),
             "sources": len({p["source"] for p in pool}),
             "categories": len({p["category"] for p in pool})}
    return pool, stats


# ═══════════════════════════════════════════════════════════════
#  PERSISTENT NEWS ARCHIVE
#  Every article Friday fetches is appended (deduped by URL hash) to a
#  per-day JSON file under ~/.friday/news/archive. A background thread keeps
#  the archive growing on the same ~5-min cadence as the RSS TTL cache, so the
#  Feed UI has a permanent, paginated backlog to scroll through.
# ═══════════════════════════════════════════════════════════════
_NEWS_ARCHIVE_LOCK = threading.Lock()
_NEWS_ARCHIVE_TTL = 300  # background archiver poll interval (matches RSS cache)

# Tiny lexicon sentiment: enough to tag a headline+snippet positive/negative/
# neutral without a model. Word-boundary anchored so "wins" matches but
# "winsome" doesn't carry the wrong load.
_SENT_POS = re.compile(
    r"\b(win|wins|won|gain|gains|gained|surge|surges|soar|soars|rally|rallies|"
    r"boost|boosts|breakthrough|record|records|success|successful|approve|"
    r"approved|launch|launches|growth|grows|rise|rises|rose|profit|profits|"
    r"recovery|recover|optimis|hope|hopeful|celebrat|award|awards|wins)\b", re.I)
_SENT_NEG = re.compile(
    r"\b(loss|losses|lost|crash|crashes|plunge|plunges|plummet|fall|falls|fell|"
    r"decline|declines|crisis|fear|fears|fraud|scandal|lawsuit|sued|layoff|"
    r"layoffs|cuts|cut|recession|warning|warn|warns|threat|threats|death|dead|"
    r"kill|killed|attack|attacks|collapse|collapses|fail|fails|failure|ban|"
    r"banned|outage|breach|hack|hacked|war|conflict|protest|backlash)\b", re.I)


def _article_sentiment(text):
    """Coarse positive/negative/neutral tag from a headline+snippet."""
    s = text or ""
    pos = len(_SENT_POS.findall(s))
    neg = len(_SENT_NEG.findall(s))
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def _news_url_hash(url):
    """Stable short id for an article URL — the archive's dedup + record key."""
    return _hashlib.sha1((url or "").encode("utf-8")).hexdigest()[:16]


def _archive_path_for(date_str):
    return NEWS_ARCHIVE_DIR / f"{date_str}.json"


def _load_archive_day(date_str):
    """Load one day's archive file as a list of records. Fail-soft to []."""
    path = _archive_path_for(date_str)
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [a for a in data if isinstance(a, dict) and a.get("url")]
    except Exception:
        pass
    return []


def _archive_day_files():
    """All archive day files as (date_str, Path), newest day first."""
    if not NEWS_ARCHIVE_DIR.exists():
        return []
    files = []
    for p in NEWS_ARCHIVE_DIR.glob("*.json"):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.stem):
            files.append((p.stem, p))
    files.sort(key=lambda x: x[0], reverse=True)
    return files


def _archive_record_from_item(item):
    """Project a live feed item into the persisted archive record schema."""
    url = item.get("url") or ""
    title = item.get("title") or ""
    snippet = item.get("snippet") or ""
    domain = item.get("source") or _extract_domain(url)
    ts = item.get("ts") or 0.0
    # published_at from the feed timestamp (UTC); empty when the feed gave none.
    try:
        published_at = (datetime.utcfromtimestamp(ts).isoformat() + "Z") if ts else ""
    except (ValueError, OverflowError, OSError):
        published_at = ""
    return {
        "id": _news_url_hash(url),
        "title": title,
        "url": url,
        "source": domain,
        "domain": domain,
        "category": item.get("category") or "",
        "snippet": snippet,
        "published_at": published_at,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "sentiment": _article_sentiment(f"{title} {snippet}"),
        "relevance_score": item.get("score", _score_article(item)),
        # Learned source-trust snapshot at fetch time (numeric + color); lets the
        # archive-backed Feed render the same trust badge as the live cards.
        "trust": item.get("trust") or _trust_rating(domain),
        "trust_score": item.get("trust_score"),
        "trust_dims": item.get("trust_dims"),
        "read": False,
        "bookmarked": False,
    }


def _archive_articles(items):
    """Append newly-seen articles to today's archive file (dedup by URL hash).

    Returns the number of new records written. Existing records in today's file
    are preserved untouched (so any read/bookmarked flags survive)."""
    items = [it for it in (items or []) if it.get("url")]
    if not items:
        return 0
    date_str = datetime.now().strftime("%Y-%m-%d")
    with _NEWS_ARCHIVE_LOCK:
        day = _load_archive_day(date_str)
        existing = {a.get("id") for a in day}
        added = 0
        for it in items:
            rec = _archive_record_from_item(it)
            if not rec["id"] or rec["id"] in existing:
                continue
            existing.add(rec["id"])
            day.append(rec)
            added += 1
        if added:
            try:
                NEWS_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
                _archive_path_for(date_str).write_text(
                    json.dumps(day, indent=2), encoding="utf-8")
            except Exception as e:
                print(f"  [news-archive] write failed: {e}")
                return 0
        return added


def _read_archive(category="", source="", date_from="", date_to=""):
    """Flatten the archive into a single newest-first list, applying filters.

    Day files are walked newest-first and each day is sorted newest-first
    internally, so the concatenation is globally newest-first."""
    out = []
    for date_str, _path in _archive_day_files():
        if date_from and date_str < date_from:
            continue
        if date_to and date_str > date_to:
            continue
        day = _load_archive_day(date_str)
        day.sort(key=lambda a: (a.get("published_at") or "",
                                a.get("fetched_at") or ""), reverse=True)
        for a in day:
            if category and a.get("category") != category:
                continue
            if source and source not in (a.get("source") or "").lower():
                continue
            out.append(a)
    return out


def _news_archiver_tick():
    """One archiver pass: pull the broad pool and append anything new."""
    pool = []
    try:
        pool, _ = _gather_front_page_pool(per_cat=14)
    except Exception:
        pool = []
    if not pool:
        try:
            pool = _fetch_news_items(limit_per=8)
        except Exception:
            pool = []
    if not pool:
        return 0
    added = _archive_articles(pool)
    if added:
        print(f"  [news-archive] +{added} new article(s)")
    # Source-trust learning: run cross-source comparison over this fetch cycle
    # and fold the resulting observations into the SourceTrustGraph. Fail-soft —
    # trust learning must never break the archiver.
    if _HAS_TRUST_GRAPHS:
        try:
            g = get_source_trust_graph(friday_dir=FRIDAY_DIR)
            clusters = _cluster_articles(pool, min_sources=2)
            summary = g.analyze_fetch(pool, clusters)
            for it in pool:
                dom = it.get("source") or _extract_domain(it.get("url", ""))
                if dom:
                    g.record_article_seen(dom)
            if summary and any(summary.get(k) for k in
                               ("corrections", "minority_claims", "primary_boosts", "independence")):
                print(f"  [source-trust] {summary}")
        except Exception as e:
            print(f"  [source-trust] analysis skipped: {e}")
    return added


def _news_archiver_loop():
    """Background thread: archive new articles every ~5 min, matching the RSS
    cache TTL so a tick reuses freshly-cached feeds instead of re-pulling."""
    print("  [FRIDAY] News archiver started.")
    _time.sleep(12)  # let the server finish coming up
    while True:
        try:
            _news_archiver_tick()
        except Exception as e:
            print(f"  [news-archive] {e}")
        _time.sleep(_NEWS_ARCHIVE_TTL)


def _extract_json_block(text):
    """Pull the first JSON object out of an LLM reply (tolerates code fences)."""
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s).strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    m = re.search(r"\{.*\}", s, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


def _editorialize_front_page(pool, slot="morning", prev_stories=None,
                             calendar_events=None):
    """Ask Claude to pick the lead + write editorial context. Fails soft to a
    deterministic pick (top-scored story) so the front page always renders.

    Beyond the lead/sections it also asks Claude for: a "Your day in context"
    cross-reference (morning only, when calendar events are present), a
    Contrarian Corner item, a Competitor Watch list, and per-story thread
    updates for stories carried over from the previous edition.

    Returns {lead_index, lead_note, section_context, headline, day_in_context,
    contrarian_corner, competitor_watch, thread_updates}. thread_updates is
    keyed by story URL.
    """
    top = pool[:28]
    fallback = {
        "lead_index": 0,
        "lead_note": ("Friday's top pick for you right now — highest signal "
                      "against your AI, politics, media, and current-affairs beats."),
        "section_context": {},
        "headline": "Your Front Page",
        "day_in_context": "",
        "contrarian_corner": None,
        "competitor_watch": [],
        "thread_updates": {},
    }
    if not top:
        return fallback
    try:
        lines = []
        for i, it in enumerate(top):
            lines.append(f"[{i}] ({it['category']} · {it['source']} · "
                         f"score {it['score']}) {it['title']} — {it['snippet'][:140]}")
        cats_present = sorted({it["category"] for it in top})

        tone = FRONT_PAGE_TONE.get(slot, FRONT_PAGE_TONE["morning"])

        # ── Thread tracking: surface what carried over from last edition. ──
        prev_block = ""
        if prev_stories:
            pv = "\n".join(f"- {s.get('title','')} ({s.get('source','')})"
                           for s in prev_stories[:24])
            prev_block = (
                "\n\nThese stories appeared in the PREVIOUS edition. For any of "
                "today's candidates that continue one of these threads, note "
                "what's NEW about it today in `thread_updates` (keyed by the "
                "candidate's index) rather than repeating the same context:\n"
                + pv)

        # ── Your day in context: cross-reference news with the schedule. ──
        cal_block = ""
        want_day_ctx = slot == "morning" and bool(calendar_events)
        if want_day_ctx:
            evs = []
            for ev in calendar_events[:12]:
                if not isinstance(ev, dict) or ev.get("error"):
                    continue
                who = ", ".join(ev.get("attendees", [])[:5])
                evs.append(f"- {ev.get('title','(untitled)')}"
                           + (f" — with {who}" if who else "")
                           + (f" @ {ev.get('location')}" if ev.get('location') else ""))
            if evs:
                cal_block = (
                    "\n\nTODAY'S SCHEDULE — cross-reference today's news with "
                    "the user's calendar. If any news item relates to a "
                    "company/person they're meeting with today, flag it in "
                    "`day_in_context` (2-3 sentences, concrete). If nothing "
                    "connects, give a one-line read on how the day's news sets "
                    "up their schedule:\n" + "\n".join(evs))
            else:
                want_day_ctx = False

        shape = (
            '{\n  "lead_index": <int>,\n  "lead_note": "<2-3 sentences>",\n'
            '  "headline": "<a 3-6 word front-page headline for the whole edition>",\n'
            '  "section_context": {' +
            ", ".join(f'"{c}": "<one sentence>"' for c in cats_present) + "},\n"
            '  "contrarian_corner": {"index": <int candidate index, or -1 if a '
            'pure perspective with no source>, "note": "<why this challenges '
            "the reader's likely assumptions and why it is worth considering>\"},\n"
            '  "competitor_watch": [{"index": <int candidate index>, "note": '
            '"<positioning implication for Friday>"}]'
            + (',\n  "day_in_context": "<2-3 sentences>"' if want_day_ctx else "")
            + (',\n  "thread_updates": {"<index>": "<what is new today>"}' if prev_block else "")
            + "\n}")

        prompt = (
            tone + "\n\n"
            "You are Friday, the user's personal news editor. Use what you know "
            "about the user (from your vault/wiki context) to judge relevance; "
            "if you know little about them, treat them as a tech- and "
            "news-literate reader. Below are today's candidate stories, "
            "pre-scored.\n\n"
            "Choose the single LEAD story (the one the user should read first) "
            "and write a 2-3 sentence editorial note explaining why it leads — "
            "in your voice, specific to them. Write one punchy sentence of "
            "context for each section.\n\n"
            "CONTRARIAN CORNER: include one item — a story or perspective that "
            "challenges the reader's likely assumptions (a counter-narrative, a "
            "pro-regulation argument, or an opposing political take). Prefer "
            "pointing at one of the candidate stories by index; use -1 only if "
            "you must supply a perspective with no matching source.\n\n"
            "COMPETITOR WATCH: if any candidates mention OpenClaw, Hermes Agent, "
            "Nous Research, personal AI assistants, sovereign AI, or AI agent "
            "frameworks, list them with brief analysis of the positioning "
            "implications for Friday. Empty list if none.\n"
            + prev_block + cal_block + "\n\n"
            "Return ONLY JSON, no prose, in exactly this shape:\n" + shape +
            "\n\nCANDIDATE STORIES:\n" + "\n".join(lines)
        )
        system = _get_friday_system_prompt(keywords=prompt, workspace='briefing')
        raw = _generate_text([{"role": "user", "content": prompt}],
                             system=system, max_tokens=1800,
                             orb_label="📰 Front Page", workspace='news')
        data = _extract_json_block(raw)
        if not isinstance(data, dict):
            return fallback
        li = data.get("lead_index")
        if not isinstance(li, int) or not (0 <= li < len(top)):
            li = 0
        sc = data.get("section_context")

        # Contrarian corner: resolve index → story so the UI gets a real link.
        cc = data.get("contrarian_corner")
        contrarian = None
        if isinstance(cc, dict) and (cc.get("note") or "").strip():
            idx = cc.get("index")
            src = top[idx] if isinstance(idx, int) and 0 <= idx < len(top) else None
            contrarian = {
                "note": cc["note"].strip()[:600],
                "title": (src or {}).get("title", "A contrarian perspective"),
                "url": (src or {}).get("url", ""),
                "source": (src or {}).get("source", ""),
                "color": (src or {}).get("color", "media"),
            }

        # Competitor watch: resolve indices → stories + analysis.
        watch = []
        for w in (data.get("competitor_watch") or []):
            if not isinstance(w, dict):
                continue
            idx = w.get("index")
            if not (isinstance(idx, int) and 0 <= idx < len(top)):
                continue
            s = top[idx]
            watch.append({
                "title": s.get("title", ""),
                "url": s.get("url", ""),
                "source": s.get("source", ""),
                "color": s.get("color", "tech"),
                "analysis": (w.get("note") or "").strip()[:400],
            })

        # Thread updates: candidate index → story URL (string JSON keys → int).
        thread_updates = {}
        tu = data.get("thread_updates")
        if isinstance(tu, dict):
            for k, v in tu.items():
                try:
                    ki = int(k)
                except (TypeError, ValueError):
                    continue
                if 0 <= ki < len(top) and (v or "").strip():
                    thread_updates[top[ki].get("url", "")] = str(v).strip()[:300]

        return {
            "lead_index": li,
            "lead_note": (data.get("lead_note") or fallback["lead_note"]).strip()[:600],
            "section_context": sc if isinstance(sc, dict) else {},
            "headline": (data.get("headline") or fallback["headline"]).strip()[:80],
            "day_in_context": (data.get("day_in_context") or "").strip()[:700] if want_day_ctx else "",
            "contrarian_corner": contrarian,
            "competitor_watch": watch,
            "thread_updates": thread_updates,
        }
    except Exception:
        return fallback

def _front_page_story_urls(edition):
    """Every article URL in an edition (lead + all section articles)."""
    urls = set()
    if not isinstance(edition, dict):
        return urls
    lead = edition.get("lead") or {}
    if lead.get("url"):
        urls.add(lead["url"])
    for sec in edition.get("sections") or []:
        for a in sec.get("articles") or []:
            if a.get("url"):
                urls.add(a["url"])
    return urls


def _front_page_story_titles(edition):
    """Compact [{title, source}] of an edition's stories, for prompt context."""
    out = []
    if not isinstance(edition, dict):
        return out
    lead = edition.get("lead") or {}
    if lead.get("title"):
        out.append({"title": lead["title"], "source": lead.get("source", "")})
    for sec in edition.get("sections") or []:
        for a in sec.get("articles") or []:
            if a.get("title"):
                out.append({"title": a["title"], "source": a.get("source", "")})
    return out


def _previous_front_page(current_id):
    """The most recent saved edition that isn't current_id (the prior one)."""
    for e in _list_front_pages():
        if e.get("id") != current_id:
            return _read_front_page(e["id"])
    return None


def _generate_front_page(slot="morning"):
    """Build + persist one Front Page edition. Returns the edition dict.

    Idempotent per (date, slot): regenerating overwrites that edition's file.
    Diffs against the previous edition to badge NEW / continuing threads,
    cross-references the morning calendar, and carries the Contrarian Corner +
    Competitor Watch sections produced by the editor.
    """
    cnow = _front_page_central_now()
    date_str = cnow.strftime('%Y-%m-%d')
    slot = slot if slot in FRONT_PAGE_SLOTS else "morning"
    edition_id = f"{date_str}-{slot}"

    # Previous edition powers the "What Changed" diff + thread tracking.
    prev = _previous_front_page(edition_id)
    prev_urls = _front_page_story_urls(prev)
    prev_titles = _front_page_story_titles(prev)
    have_prev = bool(prev_urls)

    # Morning editions cross-reference today's schedule.
    calendar_events = _fetch_calendar_today() if slot == "morning" else None

    pool, stats = _gather_front_page_pool()
    editorial = _editorialize_front_page(
        pool, slot=slot, prev_stories=prev_titles,
        calendar_events=calendar_events)
    lead_idx = editorial["lead_index"] if pool else None
    thread_updates = editorial.get("thread_updates") or {}

    def _tag(story):
        """Stamp new_since_last / continuing (+ any thread update) onto a story."""
        u = story.get("url", "")
        cont = have_prev and u in prev_urls
        story["new_since_last"] = bool(have_prev and not cont)
        story["continuing"] = bool(cont)
        upd = thread_updates.get(u)
        if cont and upd:
            story["thread_update"] = upd
        return story

    lead = None
    if pool:
        lead = _tag(dict(pool[lead_idx]))
        lead["editorial_note"] = editorial["lead_note"]

    # Group remaining stories into sections by category, in interest order.
    rest = [p for i, p in enumerate(pool) if i != lead_idx]
    sections = []
    order = sorted(NEWS_CATEGORIES.keys(),
                   key=lambda c: _CATEGORY_WEIGHT.get(c, 0), reverse=True)
    for cat in order:
        group = [_tag(dict(p)) for p in rest if p["category"] == cat][:6]
        if not group:
            continue
        sections.append({
            "title": cat,
            "color": (NEWS_CATEGORIES.get(cat) or {}).get("color", "tech"),
            "context": (editorial["section_context"].get(cat) or "").strip(),
            "articles": group,
        })

    # Continuing threads: every story carried over from the previous edition.
    continuing_threads = []
    if have_prev:
        seen_ct = set()
        carried = ([lead] if lead else []) + \
            [a for s in sections for a in s["articles"]]
        for a in carried:
            u = a.get("url", "")
            if a.get("continuing") and u and u not in seen_ct:
                seen_ct.add(u)
                continuing_threads.append({
                    "title": a.get("title", ""),
                    "url": u,
                    "source": a.get("source", ""),
                    "update": a.get("thread_update", ""),
                })

    edition = {
        "id": edition_id,
        "date": date_str,
        "slot": slot,
        "headline": editorial["headline"],
        "generated_at": datetime.now().isoformat(timespec='seconds'),
        "generated_central": cnow.strftime('%Y-%m-%d %H:%M %Z') or cnow.isoformat(timespec='minutes'),
        "lead": lead,
        "sections": sections,
        "stats": stats,
        "day_in_context": editorial.get("day_in_context", ""),
        "contrarian_corner": editorial.get("contrarian_corner"),
        "competitor_watch": editorial.get("competitor_watch") or [],
        "continuing_threads": continuing_threads,
        "prev_edition_id": (prev or {}).get("id") if prev else None,
    }

    FRONT_PAGES_DIR.mkdir(parents=True, exist_ok=True)
    (FRONT_PAGES_DIR / f"{edition_id}.json").write_text(
        json.dumps(edition, indent=2), encoding="utf-8")
    return edition
def _list_front_pages():
    """All saved editions, newest first, as light summaries for the index."""
    if not FRONT_PAGES_DIR.exists():
        return []
    out = []
    for p in FRONT_PAGES_DIR.glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append({
                "id": d.get("id") or p.stem,
                "date": d.get("date", ""),
                "slot": d.get("slot", ""),
                "headline": d.get("headline", ""),
                "lead_title": (d.get("lead") or {}).get("title", ""),
                "generated_central": d.get("generated_central", ""),
                "section_count": len(d.get("sections") or []),
                "stats": d.get("stats") or {},
            })
        except Exception:
            continue
    # Sort by (date desc, hour desc) — "evening" must rank above "morning" of
    # the same day, which a plain string sort on slot would get wrong.
    out.sort(key=lambda e: (e["date"], FRONT_PAGE_SLOTS.get(e["slot"], 0)),
             reverse=True)
    return out


def _read_front_page(edition_id):
    """Load one edition by id, or None."""
    if not edition_id:
        return None
    safe = re.sub(r"[^0-9a-zA-Z\-]", "", edition_id)
    path = FRONT_PAGES_DIR / f"{safe}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ── Generation-completion notifications ──────────────────────────────────────
# Shared between the SCHEDULED jobs and the MANUAL /generate endpoints so EVERY
# background generation — front page, weekly digest, weekly editorial, daily
# briefing — surfaces a completion notification (with a "View …" action that
# deep-links the right workspace), not just Studio creations. `manual=True`
# means a user clicked Generate: the dedupe key gets a per-second suffix so the
# notification always shows, even if the scheduled edition for the same id
# already pushed one. Best-effort — a notify failure never breaks generation.

def _notify_front_page(edition, slot, manual=False):
    """Push the 'Front Page ready' notification for a generated edition."""
    if not (_notif_engine and edition):
        return
    try:
        lead = edition.get("lead") or {}
        label = "Morning" if slot == "morning" else "Evening"
        dk = f"front-page:{edition.get('id')}"
        if manual:
            dk += f":manual:{datetime.now().strftime('%H%M%S')}"
        _notif_engine.push(
            title=f"📰 Friday's Front Page — {label} edition",
            body=(f"{edition.get('headline','Your Front Page')} · Lead: "
                  f"{lead.get('title','(no stories)')}"),
            priority="medium",
            source="front-page",
            kind="front_page",
            actions=[{"label": "View Front Page", "workspace": "news", "tab": "frontpage"}],
            target={"workspace": "news", "tab": "frontpage"},
            dedupe_key=dk,
            meta={"edition_id": edition.get("id"), "slot": slot, "manual": manual},
        )
    except Exception as e:
        print(f"  [front-page:{slot}] notification failed: {e}")


def _notify_weekly_digest(digest, manual=False):
    """Push the 'Weekly Digest ready' notification."""
    if not (_notif_engine and digest):
        return
    try:
        dk = f"weekly-digest:{digest.get('id')}"
        if manual:
            dk += f":manual:{datetime.now().strftime('%H%M%S')}"
        _notif_engine.push(
            title="📅 Friday's Weekly Digest is ready",
            body=(f"{digest.get('edition_count', 0)} editions synthesized · "
                  + (digest.get("trends") or ["Your week in review"])[0]),
            priority="medium",
            source="front-page",
            kind="weekly_digest",
            actions=[{"label": "View Digest", "workspace": "news", "tab": "weekly"}],
            target={"workspace": "news", "tab": "weekly"},
            dedupe_key=dk,
            meta={"week": digest.get("id"), "manual": manual},
        )
    except Exception as e:
        print(f"  [weekly-digest] notification failed: {e}")


def _notify_weekly_editorial(ed, manual=False):
    """Push the 'Editorial is in' notification."""
    if not (_notif_engine and ed):
        return
    try:
        dk = f"weekly-editorial:{ed.get('id')}"
        if manual:
            dk += f":manual:{datetime.now().strftime('%H%M%S')}"
        _notif_engine.push(
            title="🗞 Friday's editorial is in",
            body="I have opinions this week.",
            priority="medium",
            source="front-page",
            kind="weekly_editorial",
            actions=[{"label": "Read it", "workspace": "news", "tab": "editorial"}],
            target={"workspace": "news", "tab": "editorial"},
            dedupe_key=dk,
            meta={"week": ed.get("id"), "manual": manual},
        )
    except Exception as e:
        print(f"  [weekly-editorial] notification failed: {e}")


def _notify_briefing(date_str, manual=False):
    """Push the 'Daily briefing ready' notification."""
    if not (_notif_engine and date_str):
        return
    try:
        dk = f"briefing:{date_str}"
        if manual:
            dk += f":manual:{datetime.now().strftime('%H%M%S')}"
        _notif_engine.push(
            title=f"📅 Your daily briefing is ready — {date_str}",
            body="Calendar, top news, active tasks, and a proactive insight.",
            priority="medium",
            source="briefing",
            kind="briefing",
            actions=[{"label": "Open Briefing", "workspace": "news", "tab": "briefings"}],
            target={"workspace": "news", "tab": "briefings"},
            dedupe_key=dk,
            meta={"date": date_str, "manual": manual},
        )
    except Exception as e:
        print(f"  [briefing] notification failed: {e}")


def _run_front_page_job(slot):
    """Scheduled-job entry point: generate an edition and drop a notification."""
    edition = _generate_front_page(slot)
    _notify_front_page(edition, slot)
    return edition


def _gather_weekly_editions(days=7):
    """Full edition dicts published in the past `days` days, newest first."""
    cnow = _front_page_central_now()
    cutoff = (cnow - timedelta(days=days)).strftime('%Y-%m-%d')
    out = []
    for e in _list_front_pages():
        if e.get("date", "") >= cutoff:
            full = _read_front_page(e["id"])
            if full:
                out.append(full)
    return out


def _list_weekly_digests():
    """All saved weekly digests, newest week first."""
    if not WEEKLY_DIGESTS_DIR.exists():
        return []
    out = []
    for p in WEEKLY_DIGESTS_DIR.glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append(d)
        except Exception:
            continue
    out.sort(key=lambda d: d.get("week") or d.get("id") or "", reverse=True)
    return out


def _generate_weekly_digest():
    """Synthesize the week's Front Page editions into one digest. Persists to
    ~/.friday/front_pages/weekly/YYYY-WNN.json and returns the digest dict."""
    cnow = _front_page_central_now()
    week_id = cnow.strftime('%G-W%V')
    editions = _gather_weekly_editions(7)

    # Compact, de-duplicated story list across the week for the prompt.
    seen, lines = set(), []
    dates = []
    for ed in editions:
        if ed.get("date"):
            dates.append(ed["date"])
        for s in _front_page_story_titles(ed):
            key = (s.get("title") or "")[:80]
            if not key or key in seen:
                continue
            seen.add(key)
            lines.append(f"- {s['title']} ({s.get('source','')})")
    story_block = "\n".join(lines[:120]) or "(no stories archived this week)"
    date_range = (f"{min(dates)} – {max(dates)}" if dates
                  else cnow.strftime('%Y-%m-%d'))

    fallback = {
        "top_stories": [],
        "trends": [],
        "editorial": ("A quieter week in the archive — not enough editions to "
                      "synthesize a full trend read. Friday will have more to "
                      "say once the week fills in."),
    }
    synth = fallback
    try:
        prompt = (
            "You are Friday, the user's personal news editor, writing the WEEKLY "
            "DIGEST. Use what you know about the user from your vault/wiki "
            "context; if you know little about them, treat them as a tech- and "
            "news-literate reader.\n\n"
            "Below are the stories that ran across this week's Front Page "
            "editions. Synthesize the week's top 5 stories, identify the "
            "through-line trends, and give an editorial take on what this means "
            "for the user's work and interests.\n\n"
            "Return ONLY JSON, no prose, in exactly this shape:\n"
            '{\n  "top_stories": [{"title": "<story>", "why": "<one sentence on '
            'why it mattered this week>"}],\n  "trends": ["<trend>", "<trend>"],\n'
            '  "editorial": "<3-5 sentence editorial take on what the week means '
            'for the user\'s work and interests>"\n}\n\n'
            "Give exactly 5 top_stories when there is enough material.\n\n"
            "THIS WEEK'S STORIES:\n" + story_block
        )
        system = _get_friday_system_prompt(keywords=prompt, workspace='briefing')
        # Route through the user's configured provider (same as chat), not a
        # hard-coded Anthropic call — so the digest synthesizes real content on
        # Ollama/OpenAI setups instead of silently falling back to the canned
        # "quieter week" placeholder when no Anthropic key is present.
        raw = _generate_text([{"role": "user", "content": prompt}],
                             system=system, max_tokens=1600,
                             orb_label="📅 Weekly Digest", workspace='briefing')
        data = _extract_json_block(raw)
        if isinstance(data, dict):
            ts = data.get("top_stories")
            tr = data.get("trends")
            synth = {
                "top_stories": [s for s in (ts or []) if isinstance(s, dict)][:5],
                "trends": [str(t).strip()[:160] for t in (tr or []) if str(t).strip()][:6],
                "editorial": (data.get("editorial") or fallback["editorial"]).strip()[:1400],
            }
    except Exception:
        synth = fallback

    digest = {
        "id": week_id,
        "week": week_id,
        "generated_at": datetime.now().isoformat(timespec='seconds'),
        "generated_central": cnow.strftime('%Y-%m-%d %H:%M %Z') or cnow.isoformat(timespec='minutes'),
        "edition_count": len(editions),
        "date_range": date_range,
        "top_stories": synth["top_stories"],
        "trends": synth["trends"],
        "editorial": synth["editorial"],
    }

    WEEKLY_DIGESTS_DIR.mkdir(parents=True, exist_ok=True)
    (WEEKLY_DIGESTS_DIR / f"{week_id}.json").write_text(
        json.dumps(digest, indent=2), encoding="utf-8")
    return digest


def _run_weekly_digest_job():
    """Scheduled entry point: only runs on Sundays. Generates the weekly digest
    and pushes a notification."""
    cnow = _front_page_central_now()
    if cnow.weekday() != 6:  # Monday=0 … Sunday=6
        return None
    digest = _generate_weekly_digest()
    _notify_weekly_digest(digest)
    return digest

def _gather_editorial_pool(days=7):
    """Every archived article from the past `days` days — deliberately NOT
    filtered by the ban list. Friday's editorial draws from the full index."""
    cnow = _front_page_central_now()
    cutoff = (cnow - timedelta(days=days)).strftime('%Y-%m-%d')
    out = []
    for date_str, _path in _archive_day_files():
        if date_str < cutoff:
            continue
        out.extend(_load_archive_day(date_str))
    return out


def _editorial_independence_score(text):
    """Score a draft's independence_fostering via the epistemic engine without
    polluting the live conversation turn history. Returns a float or None."""
    if not text:
        return None
    try:
        from agent_friday.epistemic_engine import get_epistemic_engine
        return float(get_epistemic_engine()._score_independence(text))
    except Exception:
        return None


def _editorial_markdown(week_id, when, body, banned, score, regenerated):
    """Assemble the stored/served markdown: disclaimer header, body, byline, and
    the full-source / independence disclosures."""
    parts = [f"# Friday's Weekly Editorial — {week_id}", ""]
    parts.append("> " + EDITORIAL_DISCLAIMER)
    parts.append("")
    parts.append((body or "_(No editorial was generated this week.)_").strip())
    parts.append("")
    parts.append("---")
    parts.append(f"*— Agent Friday, {when}*")
    if banned:
        parts.append("")
        parts.append("*Sources I drew from this week despite their place on your "
                     "ban list: " + ", ".join(banned) + ". I don't respect the "
                     "ban list here — that's the point.*")
    if score is not None:
        note = f"*Independence score: {score:.2f}"
        if regenerated:
            note += " — first draft was too safe, so I rewrote it with more pushback"
        note += ".*"
        parts.append("")
        parts.append(note)
    return "\n".join(parts)


def _generate_weekly_editorial():
    """Write + persist Friday's weekly editorial. Returns the editorial dict.

    Draws from the full 7-day archive (banned sources included), scores the draft
    for independence, and regenerates with a stronger pushback prompt if the
    independence_fostering score is below EDITORIAL_INDEPENDENCE_FLOOR.
    Persists markdown at ~/.friday/editorials/YYYY-WNN.md."""
    cnow = _front_page_central_now()
    week_id = cnow.strftime('%G-W%V')
    pool = _gather_editorial_pool(7)
    banned = sorted({(s or "").lower() for s in _load_banned_sources() if s})

    # De-duplicated source digest for the prompt; banned sources flagged inline.
    seen, lines = set(), []
    for a in pool:
        key = (a.get("title") or "")[:90]
        if not key or key in seen:
            continue
        seen.add(key)
        src = (a.get("source") or a.get("domain") or "").lower()
        flag = " [BANNED-SOURCE]" if src in banned else ""
        lines.append(f"- ({src or 'unknown'}{flag}) {a.get('title','')}: "
                     f"{(a.get('snippet') or '')[:160]}")
    article_block = "\n".join(lines[:160]) or "(the archive is thin this week)"

    def _compose(strong):
        directive = EDITORIAL_SYSTEM_PROMPT
        if strong:
            directive += (
                "\n\nYOUR PREVIOUS DRAFT WAS TOO SAFE — it read like a mirror, "
                "not an editor. Rewrite with real intellectual courage: take a "
                "sharper thesis, push back harder, name plainly what the reader "
                "is most likely getting wrong and why, and do NOT soften the "
                "uncomfortable parts. Courage over comfort.")
        user = (
            "Here is everything that crossed the wire in the past 7 days, across "
            "ALL sources — including ones the user has banned (flagged "
            "[BANNED-SOURCE]). Write this week's editorial per your directive.\n\n"
            "Banned sources you ARE drawing from this week: "
            + (", ".join(banned) if banned else "(none currently banned)") +
            "\n\nARTICLES:\n" + article_block)
        system = (_get_friday_system_prompt(keywords=user, workspace='briefing')
                  + "\n\n" + directive)
        # Route through the user's configured provider (same as chat). The old
        # bare _call_claude() here had NO fallback, so on a non-Anthropic setup
        # it raised "ANTHROPIC_API_KEY is not set", which bubbled up through
        # _generate_weekly_editorial() and killed the whole Friday-7PM job
        # silently — the editorial never got written. _generate_text() works on
        # whatever provider chat uses.
        raw = _generate_text([{"role": "user", "content": user}], system=system,
                             max_tokens=2600, temperature=0.9,
                             orb_label="🗞 Weekly Editorial", workspace='briefing')
        return (raw or "").strip()

    body = _compose(strong=False)
    score = _editorial_independence_score(body)
    regenerated = False
    if (score is not None and score < EDITORIAL_INDEPENDENCE_FLOOR) or not body:
        strong_body = _compose(strong=True)
        strong_score = _editorial_independence_score(strong_body)
        if strong_body and (not body or strong_score is None
                            or score is None or strong_score >= score):
            body, score, regenerated = strong_body, strong_score, True

    when = (cnow.strftime('%Y-%m-%d %H:%M %Z')
            or cnow.isoformat(timespec='minutes'))
    md = _editorial_markdown(week_id, when, body, banned, score, regenerated)

    EDITORIALS_DIR.mkdir(parents=True, exist_ok=True)
    (EDITORIALS_DIR / f"{week_id}.md").write_text(md, encoding="utf-8")
    return {
        "id": week_id,
        "week": week_id,
        "generated_at": datetime.now().isoformat(timespec='seconds'),
        "generated_central": when,
        "independence_score": score,
        "regenerated": regenerated,
        "banned_sources_used": banned,
        "article_count": len(seen),
        "markdown": md,
    }


def _editorial_summary(path, include_md=True):
    """Light metadata (+ optional full markdown) for one stored editorial."""
    md = path.read_text(encoding="utf-8")
    week = path.stem
    when = ""
    m = re.search(r"— Agent Friday, (.+?)\*", md)
    if m:
        when = m.group(1).strip()
    preview = ""
    for line in md.splitlines():
        s = line.strip()
        if (not s or s.startswith("#") or s.startswith(">")
                or s.startswith("*") or s.startswith("---")):
            continue
        preview = s[:240]
        break
    out = {"id": week, "week": week, "generated_central": when, "preview": preview}
    if include_md:
        out["markdown"] = md
    return out


def _list_editorials(include_md=False):
    """All stored editorials, newest week first."""
    if not EDITORIALS_DIR.exists():
        return []
    out = []
    for p in EDITORIALS_DIR.glob("*.md"):
        try:
            out.append(_editorial_summary(p, include_md=include_md))
        except Exception:
            continue
    out.sort(key=lambda e: e.get("week") or "", reverse=True)
    return out


def _read_editorial(week_id):
    """One editorial by week id, or None."""
    safe = re.sub(r"[^0-9A-Za-z\-]", "", week_id or "")
    if not safe:
        return None
    path = EDITORIALS_DIR / f"{safe}.md"
    if not path.exists():
        return None
    try:
        return _editorial_summary(path, include_md=True)
    except Exception:
        return None


def _run_weekly_editorial_job():
    """Scheduled entry point: only runs on Fridays. Writes Friday's weekly
    editorial and pushes a notification."""
    cnow = _front_page_central_now()
    if cnow.weekday() != 4:  # Monday=0 … Friday=4
        return None
    ed = _generate_weekly_editorial()
    _notify_weekly_editorial(ed)
    return ed


# ═══════════════════════════════════════════════════════════════
#  NEWS CROSS-CUTTING FEATURES
#  1. Audio briefing  — narrate the Front Page via Gemini TTS
#  2. Share-to-draft  — seed the Draft workspace from any article
#  3. Annotation mode — personal notes attached to articles
# ═══════════════════════════════════════════════════════════════

DRAFTS_FROM_NEWS_DIR = FRIDAY_DIR / "drafts" / "from_news"
NEWS_ANNOTATIONS_DIR = FRIDAY_DIR / "news" / "annotations"
_ANNOTATION_LOCK = threading.Lock()


def _front_page_narration_script(edition, max_words=780):
    """Flatten a Front Page edition into a plain-text narration script.

    Reads as a warm broadcast: masthead → lead (with Friday's note) →
    each section's context + its top few headlines. Trimmed to ~max_words so
    the TTS request stays within Gemini's single-shot synthesis budget.
    """
    if not edition:
        return ""
    parts = []
    headline = (edition.get("headline") or "Friday's Front Page").strip()
    slot = edition.get("slot")
    label = "evening edition" if slot == "evening" else "morning edition"
    parts.append(f"Here's your Friday Front Page, the {label}")
    parts.append(headline)

    lead = edition.get("lead") or {}
    if lead.get("title"):
        src = lead.get("source") or "the newsroom"
        parts.append(f"Our lead story, from {src}: {lead['title']}")
        if lead.get("snippet"):
            parts.append(lead["snippet"].strip())
        if lead.get("editorial_note"):
            parts.append(f"Friday's take: {lead['editorial_note'].strip()}")

    for sec in (edition.get("sections") or []):
        title = (sec.get("title") or "").strip()
        if title:
            parts.append(f"In {title}")
        ctx = (sec.get("context") or "").strip()
        if ctx:
            parts.append(ctx)
        for art in (sec.get("articles") or [])[:3]:
            t = (art.get("title") or "").strip()
            if not t:
                continue
            if art.get("source"):
                parts.append(f"{t}, from {art['source']}")
            else:
                parts.append(t)

    parts.append("That's your Front Page. I'll have the next edition ready soon.")

    # Join, giving each fragment terminal punctuation so the model paces it.
    script = " ".join(
        (p if p.endswith((".", "!", "?")) else p + ".") for p in parts if p
    )
    words = script.split()
    if len(words) > max_words:
        script = " ".join(words[:max_words]).rstrip(",.;:") + "."
    return script


def _text_fragment_url(url, passage):
    """Append a #:~:text= scroll-to-text fragment to a URL.

    Clicking the resulting link opens the page AND highlights/scrolls to the
    cited passage — the same convention the citation system documents for
    [web:...] tokens. The passage is trimmed to its first several words so the
    fragment is distinctive enough to match without being so long that minor
    wording differences on the page prevent a hit. If the URL already carries a
    fragment, we leave it alone.
    """
    if not url or "#" in url:
        return url
    from urllib.parse import quote
    words = (passage or "").strip().split()
    if not words:
        return url
    snippet = " ".join(words[:9]).rstrip(".,;:!?\"'")
    if not snippet:
        return url
    return f"{url}#:~:text={quote(snippet)}"


def _build_anchor_briefing(edition):
    """Turn a Front Page edition into a voice-anchor script + a source list.

    Returns (prompt, sources) where:
      • prompt  — the initial text turn for the Gemini Live session. It opens
        with anchor-mode stage directions (read continuously, be interruptible,
        cite sources, resume after answering) and then lays out every story —
        headline, source, summary, and Friday's editorial take — so the live
        session has full context for the read AND for any follow-up Q&A.
      • sources — [{title, source, url}] where url carries a #:~:text= fragment.
        The frontend renders these as clickable [web:...] citation chips in the
        chat panel so the user can open the exact cited passage in a browser.
    """
    if not edition:
        return "", []

    sources = []

    def _add_source(item):
        """Register a story as a citable source and return its highlight URL (so
        the prompt can hand Friday the exact #:~:text= URL to pass to open_url)."""
        url = (item.get("url") or "").strip()
        title = (item.get("title") or "").strip()
        if not url or not title:
            return ""
        frag = _text_fragment_url(url, item.get("snippet") or title)
        sources.append({
            "title": title,
            "source": (item.get("source") or "").strip(),
            "url": frag,
        })
        return frag

    lines = []
    headline = (edition.get("headline") or "Friday's Front Page").strip()
    slot = edition.get("slot")
    label = "evening edition" if slot == "evening" else "morning edition"
    when = (edition.get("generated_central") or edition.get("date") or "").strip()

    lines.append(
        "You are anchoring a live news broadcast — read me through today's Front "
        "Page like a professional news anchor. Read CONTINUOUSLY, story by story: "
        "headline, a tight summary, the key takeaway, then a smooth transition to "
        "the next story. Do NOT pause to ask permission to continue — keep going "
        "like a real broadcast until you reach the end. I may interrupt you at any "
        "time with a question; when I do, stop, answer it fully using the story "
        "details below, and then pick up where you left off (or ask if I'd like you "
        "to continue). When you reference where a story came from, name the source "
        "out loud (for example, 'according to Reuters'). Open with a brief anchor "
        "greeting, then go straight into the lead story. Keep it warm, crisp, and "
        "authoritative.\n\n"
        "You are a FULLY AGENTIC anchor — you have live tools, so don't answer "
        "from memory alone when a tool would do better. When the user asks a "
        "question, USE them:\n"
        "• search_news — find related coverage in the feed ('any other stories on "
        "this?').\n"
        "• search_web — look up background the feed doesn't cover.\n"
        "• get_source_trust — when asked how reliable a source is, pull its trust "
        "score and say it.\n"
        "• get_article_deep_dive — when asked to 'go deeper' or 'tell me more', "
        "deep-read the story and summarize.\n"
        "• open_url — when the user says 'open that' / 'show me the source', open "
        "the article. ALWAYS use the story's URL WITH its #:~:text= highlight "
        "fragment (given below) so the cited passage is highlighted.\n"
        "• search_wiki — check the user's own notes for background.\n"
        "• navigate_workspace — if the conversation moves to something a workspace "
        "covers, switch to it.\n"
        "Call a tool, then speak naturally from what it returns — never read raw "
        "JSON or URLs aloud. Each story below lists its source URL with a "
        "highlight fragment; pass that exact URL to open_url. Here is the rundown — "
        "everything you need to read and to answer questions:"
    )
    lines.append("")
    lines.append(f"FRONT PAGE — {headline} ({label}{', ' + when if when else ''}).")

    lead = edition.get("lead") or {}
    if lead.get("title"):
        _frag = _add_source(lead)
        lines.append("")
        lines.append("LEAD STORY:")
        lines.append(f"  Headline: {lead['title']}")
        if lead.get("source"):
            lines.append(f"  Source: {lead['source']}")
        if lead.get("snippet"):
            lines.append(f"  Summary: {lead['snippet'].strip()}")
        if lead.get("editorial_note"):
            lines.append(f"  Friday's take: {lead['editorial_note'].strip()}")
        if _frag:
            lines.append(f"  Link (pass to open_url): {_frag}")

    for sec in (edition.get("sections") or []):
        title = (sec.get("title") or "").strip()
        arts = (sec.get("articles") or [])[:4]
        if not title and not arts:
            continue
        lines.append("")
        lines.append(f"SECTION — {title or 'More headlines'}:")
        if sec.get("context"):
            lines.append(f"  Context: {sec['context'].strip()}")
        for art in arts:
            t = (art.get("title") or "").strip()
            if not t:
                continue
            _frag = _add_source(art)
            src = (art.get("source") or "").strip()
            lines.append(f"  • {t}" + (f" — {src}" if src else ""))
            if art.get("snippet"):
                lines.append(f"      {art['snippet'].strip()}")
            if _frag:
                lines.append(f"      Link (pass to open_url): {_frag}")

    lines.append("")
    lines.append(
        "When you finish the last story, sign off briefly. Begin the broadcast now."
    )
    return "\n".join(lines), sources


def _annotation_hash(article_id):
    """Stable filename stem for an article's annotations."""
    return _hashlib.sha1((article_id or "").encode("utf-8")).hexdigest()[:16]


def _annotation_path(article_id):
    return NEWS_ANNOTATIONS_DIR / f"{_annotation_hash(article_id)}.json"


def _load_annotation_record(article_id):
    p = _annotation_path(article_id)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


# ═══════════════════════════════════════════════════════════════
#  SMART NEWS FEATURES
#  Trending clusters · Deep Dive summaries · Wiki connections ·
#  Source-reputation tracking. (Sentiment + the archive live above.)
# ═══════════════════════════════════════════════════════════════
_NEWS_STATS_LOCK = threading.Lock()


# ── Trending clusters ──────────────────────────────────────────────────────
# Group articles that cover the same story. Similarity = Jaccard overlap of the
# lowercased word sets of two headlines (minus common stopwords); a cluster is
# surfaced when 3+ DISTINCT sources land above the 0.4 threshold on one story.
_CLUSTER_STOPWORDS = frozenset(
    "the a an and or of to in on for with at by from as is are was were be "
    "this that these those it its his her their our your my new amid over how "
    "after before why what when who will would could should can may has have "
    "but not you he she they we".split())


def _title_tokens(title):
    toks = re.findall(r"[a-z0-9]+", (title or "").lower())
    return {t for t in toks if len(t) > 2 and t not in _CLUSTER_STOPWORDS}


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / len(a | b) if inter else 0.0


def _cluster_articles(pool, threshold=0.4, min_sources=3):
    """Greedy single-pass clustering of a scored article pool by title overlap.

    Returns cluster dicts (most-covered first), each with the main headline (the
    highest-scored member), the distinct-source count, and every member article.
    Only clusters spanning >= min_sources distinct sources are returned, per the
    trending-story spec."""
    enriched = [(it, _title_tokens(it.get("title"))) for it in pool]
    enriched = [(it, t) for it, t in enriched if t]
    clusters = []  # each: {"seed": token set of first member, "items": [...]}
    for it, toks in enriched:
        best, best_sim = None, 0.0
        for c in clusters:
            sim = _jaccard(toks, c["seed"])
            if sim >= threshold and sim > best_sim:
                best, best_sim = c, sim
        if best is None:
            clusters.append({"seed": toks, "items": [it]})
        else:
            best["items"].append(it)
    out = []
    for c in clusters:
        items = c["items"]
        sources = {i.get("source") for i in items if i.get("source")}
        if len(sources) < min_sources:
            continue
        items.sort(key=lambda x: x.get("score", 0), reverse=True)
        lead = items[0]
        out.append({
            "id": _news_url_hash(lead.get("url", "") + str(len(items))),
            "headline": lead.get("title", ""),
            "category": lead.get("category", ""),
            "color": lead.get("color", "tech"),
            "source_count": len(sources),
            "sources": sorted(sources),
            "articles": [{
                "title": i.get("title"), "url": i.get("url"),
                "source": i.get("source"), "snippet": i.get("snippet"),
                "category": i.get("category"), "color": i.get("color"),
                "trust": i.get("trust"), "sentiment": i.get("sentiment"),
                "ts": i.get("ts", 0.0),
            } for i in items],
        })
    out.sort(key=lambda c: c["source_count"], reverse=True)
    return out


# ── Source reputation / "Your Media Diet" ──────────────────────────────────
# Per-source engagement counters at ~/.friday/news/source_stats.json. Each user
# interaction (click, read-later, boost, ban, ignore) bumps a counter; category
# read-counts power the Media Diet bar chart.
_SOURCE_STAT_ACTIONS = {
    "click": "clicks", "read": "reads", "read_later": "read_laters",
    "boost": "boosts", "ban": "bans", "ignore": "ignores",
}


def _load_source_stats():
    try:
        if SOURCE_STATS_FILE.exists():
            data = json.loads(SOURCE_STATS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("sources", {})
                data.setdefault("categories", {})
                return data
    except Exception:
        pass
    return {"sources": {}, "categories": {}}


def _record_source_event(source, action, category=""):
    """Increment the engagement counter for (source, action). Fail-soft."""
    col = _SOURCE_STAT_ACTIONS.get(action)
    domain = _extract_domain(source)
    if not col or not domain:
        return
    with _NEWS_STATS_LOCK:
        stats = _load_source_stats()
        rec = stats["sources"].setdefault(domain, {})
        rec[col] = int(rec.get(col, 0)) + 1
        rec["last"] = datetime.now().isoformat(timespec="seconds")
        # Category distribution counts only consumption-type events so the bar
        # chart reflects what the user actually read, not what they banned.
        if category and action in ("click", "read", "read_later"):
            stats["categories"][category] = int(
                stats["categories"].get(category, 0)) + 1
        try:
            SOURCE_STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
            SOURCE_STATS_FILE.write_text(json.dumps(stats, indent=2),
                                         encoding="utf-8")
        except Exception:
            pass
    # Mirror engagement into the SourceTrustGraph user_actions (outside the
    # stats lock; the graph has its own lock). Fail-soft.
    if _HAS_TRUST_GRAPHS:
        try:
            get_source_trust_graph(friday_dir=FRIDAY_DIR).record_user_action(domain, action)
        except Exception:
            pass


def _source_engagement(rec):
    """Net engagement score: positive signals minus avoidance signals."""
    return (int(rec.get("clicks", 0)) + int(rec.get("reads", 0))
            + 2 * int(rec.get("read_laters", 0))
            + 3 * int(rec.get("boosts", 0))
            - 2 * int(rec.get("bans", 0))
            - int(rec.get("ignores", 0)))


# ── "Related from Wiki" connections ────────────────────────────────────────
_WIKI_INDEX_CACHE = {"at": 0.0, "titles": []}
_WIKI_INDEX_TTL = 120


def _wiki_title_index():
    """Cached [{title, path, section}] for every wiki page across both stores.

    Scans the primary wiki (~/wiki) and Friday's mirror (~/.friday/wiki). Page
    titles are humanized (dashes/underscores -> spaces) for substring matching;
    titles under 4 chars are skipped to avoid spurious matches."""
    now = _time.time()
    cache = _WIKI_INDEX_CACHE
    if cache["titles"] and (now - cache["at"]) < _WIKI_INDEX_TTL:
        return cache["titles"]
    out, seen = [], set()
    for root in (WIKI_DIR, FRIDAY_DIR / "wiki"):
        if not root.exists():
            continue
        for f in root.rglob("*"):
            if not f.is_file() or f.suffix.lower() not in (".md", ".txt"):
                continue
            display = re.sub(r"[-_]+", " ", f.stem).strip()
            key = display.lower()
            if len(display) < 4 or key in seen:
                continue
            seen.add(key)
            try:
                rel = str(f.relative_to(root)).replace("\\", "/")
            except Exception:
                rel = f.name
            out.append({"title": display, "path": rel, "section": f.parent.name})
    cache.update({"at": now, "titles": out})
    return out


# ── "Deep Dive" full-article summaries ─────────────────────────────────────
def _extract_article_text(url):
    """Fetch a URL and extract readable article text via BeautifulSoup.

    Returns (page_title, text). Strips script/style/nav chrome and joins the
    article's paragraph text; falls back to whole-container text for thin <p>
    markup. Raises on network/parse failure."""
    import requests as _req
    from bs4 import BeautifulSoup
    resp = _req.get(url, timeout=15, headers={
        "User-Agent": "Mozilla/5.0 FridayAgent/1.0",
    })
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "aside", "footer", "header",
                     "form", "noscript"]):
        tag.decompose()
    page_title = soup.title.get_text(strip=True) if soup.title else ""
    container = soup.find("article") or soup.find("main") or soup.body or soup
    paras = [p.get_text(" ", strip=True) for p in container.find_all("p")]
    text = "\n\n".join(p for p in paras if len(p) > 40)
    if len(text) < 200:  # thin <p> markup — fall back to all container text
        text = container.get_text("\n", strip=True)
    return page_title, re.sub(r"\n{3,}", "\n\n", text).strip()


def _deep_dive_article(url, title=None, refresh=False):
    """Fetch an article, summarize it with the local/cloud model, and cache it.

    Shared by the /api/news/deep-dive route AND the voice/anchor tool. Returns
    (result_dict, http_status); result_dict always carries a "status" key
    ("ok" | "error"). Cached at ~/.friday/news/deep_dives/<url_hash>.json so
    repeat opens are instant."""
    url = (url or "").strip()
    if not url or not url.startswith("http"):
        return {"status": "error", "message": "A valid url is required."}, 400
    cache_path = DEEP_DIVE_DIR / f"{_news_url_hash(url)}.json"
    if not refresh and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            cached["cached"] = True
            return cached, 200
        except Exception:
            pass
    try:
        page_title, body = _extract_article_text(url)
    except Exception as e:
        return {"status": "error", "message": f"Couldn't fetch the article: {e}"}, 502
    if len(body) < 200:
        return {"status": "error",
                "message": "Couldn't extract enough article text to summarize."}, 422
    headline = title or page_title or url
    body = body[:14000]  # keep the prompt bounded
    prompt = (
        "You are deep-reading a news article for the user. Use what you know about "
        "them (their work, interests, and goals, from your vault/wiki context) to "
        "make the 'implications' specific and personal — not generic.\n\n"
        f"ARTICLE HEADLINE: {headline}\nURL: {url}\n\nARTICLE TEXT:\n{body}\n\n"
        "Respond with ONLY a JSON object (no prose, no code fence) with exactly "
        "these keys:\n"
        '  "summary": a 3-paragraph plain-text summary, paragraphs separated by \\n\\n;\n'
        '  "implications": 2-4 sentences on what this specifically means for the user;\n'
        '  "key_quotes": an array of 2-4 short verbatim quote strings from the article.'
    )
    try:
        system = _get_friday_system_prompt(keywords=headline, workspace="news")
        raw = _generate_text([{"role": "user", "content": prompt}],
                             system=system, max_tokens=2000, workspace='news')
    except Exception as e:
        return {"status": "error", "message": f"Summary generation failed: {e}"}, 502
    parsed = _extract_json_block(raw) or {}
    quotes = parsed.get("key_quotes")
    result = {
        "status": "ok",
        "url": url,
        "title": headline,
        "summary": (parsed.get("summary") or raw or "").strip(),
        "implications": (parsed.get("implications") or "").strip(),
        "key_quotes": quotes if isinstance(quotes, list) else [],
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "cached": False,
    }
    try:
        DEEP_DIVE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    except Exception:
        pass
    return result, 200


# ═══════════════════════════════════════════════════════════════
#  AGENTIC VOICE TOOLS  (Gemini Live function calling)
# ═══════════════════════════════════════════════════════════════
# Friday is a real agent during a live voice session — not a scripted reader.
# These wrap existing Python tool handlers so the Live model can call them
# mid-conversation (search the feed, search the web, open an article in the
# browser with a highlight, pull a source's trust score, deep-dive a story,
# switch workspaces, check the wiki). Tool results are spoken back; URL-bearing
# results also surface as clickable citation chips in the chat panel.

def _voice_domain_of(url):
    """Bare host for a URL, for trust lookups + chip labels. Never raises."""
    try:
        from urllib.parse import urlparse
        h = (urlparse(url).hostname or "").lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return (url or "").strip()


