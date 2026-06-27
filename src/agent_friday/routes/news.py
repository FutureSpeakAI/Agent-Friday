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
    _HAS_INTEGRITY,
    _HAS_TRUST_GRAPHS,
    _offline_queue_add,
    _offline_should_queue,
    federation,
    get_integrity_engine,
    get_source_trust_graph,
)  # noqa: E501
from agent_friday.services.agent import (
    _get_governance_key,
)  # noqa: E501
from agent_friday.services.calendar_engine import (
    _google_credentials,
)  # noqa: E501
from agent_friday.services.model_router import (
    _generate_text,
    _get_friday_system_prompt,
)  # noqa: E501
from agent_friday.services.news_engine import (
    BANNED_SOURCES_FILE,
    BOOSTED_SOURCES_FILE,
    DRAFTS_FROM_NEWS_DIR,
    FRONT_PAGE_SLOTS,
    NEWS_ANNOTATIONS_DIR,
    NEWS_CATEGORIES,
    _ANNOTATION_LOCK,
    _READ_LATER_LOCK,
    _annotation_hash,
    _annotation_path,
    _archive_day_files,
    _build_anchor_briefing,
    _cluster_articles,
    _deep_dive_article,
    _extract_domain,
    _fetch_news_items,
    _find_briefing_path,
    _front_page_central_now,
    _front_page_narration_script,
    _gather_front_page_pool,
    _gather_live_briefing_context,
    _generate_front_page,
    _generate_weekly_digest,
    _generate_weekly_editorial,
    _list_editorials,
    _list_front_pages,
    _list_weekly_digests,
    _load_annotation_record,
    _load_archive_day,
    _load_banned_sources,
    _load_boosted_sources,
    _load_briefing_prefs,
    _load_read_later,
    _load_source_stats,
    _mirror_source_action,
    _notify_briefing,
    _notify_front_page,
    _notify_weekly_digest,
    _notify_weekly_editorial,
    _read_archive,
    _read_editorial,
    _read_front_page,
    _record_source_event,
    _save_briefing_prefs,
    _save_read_later,
    _source_engagement,
    _source_from_request,
    _wiki_title_index,
    _write_json_list,
)  # noqa: E501
from agent_friday.services.voice_engine import (
    _synthesize_tts_wav,
)  # noqa: E501

news_bp = Blueprint('news', __name__)


@news_bp.route('/api/briefings')
def list_briefings():
    """List all daily briefing files from both known locations (never delete these)."""
    briefings_by_date = {}

    # Location 1: Desktop/friday-creations — filenames like daily-briefing-2026-04-14.html
    creations = HOME / 'Desktop' / 'friday-creations'
    if creations.exists():
        for f in creations.iterdir():
            if f.name.startswith('daily-briefing') and f.suffix in ('.html', '.md'):
                date_part = f.name.replace('daily-briefing-', '').replace('.html', '').replace('.md', '')
                entry = briefings_by_date.setdefault(date_part, {'date': date_part, 'name': f.stem})
                entry[f.suffix.lstrip('.')] = f.name
                entry['size'] = f.stat().st_size

    # Location 2: ~/.friday/wiki/briefings — filenames like 2026-04-14.html or .md
    wiki_briefings = HOME / '.friday' / 'wiki' / 'briefings'
    if wiki_briefings.exists():
        for f in wiki_briefings.iterdir():
            if f.suffix in ('.html', '.md') and len(f.stem) == 10 and f.stem[4] == '-' and f.stem[7] == '-':
                date_part = f.stem  # e.g. "2026-04-14"
                entry = briefings_by_date.setdefault(date_part, {'date': date_part, 'name': f.stem})
                entry[f.suffix.lstrip('.')] = f.name
                entry.setdefault('size', f.stat().st_size)

    briefings = sorted(briefings_by_date.values(), key=lambda b: b['date'], reverse=True)
    return jsonify({'status': 'ok', 'briefings': briefings, 'total': len(briefings)})

@news_bp.route('/briefing/<filename>')
def serve_briefing(filename):
    """Serve a briefing HTML file directly for browser viewing."""
    path = _find_briefing_path(filename)
    if path:
        return send_from_directory(str(path.parent), filename)
    return 'Not found', 404

@news_bp.route('/api/briefing/status')
def briefing_status():
    """Report which data connectors are live for the News workspace.

    Drives the colored status indicators (✅ / ⚠️ / ❌). Static segment so it
    ranks above the /api/briefing/<filename> rule in Werkzeug's matcher.
    """
    google_connected = _google_credentials() is not None
    try:
        import feedparser  # noqa: F401
        from bs4 import BeautifulSoup  # noqa: F401
        news_ok = True
    except Exception:
        news_ok = False
    brave_on = bool((os.environ.get("BRAVE_SEARCH_API_KEY") or "").strip())
    connectors = [
        {
            "key": "gmail", "label": "Gmail", "icon": "📧",
            "status": "connected" if google_connected else "disconnected",
            "detail": "Live read-only" if google_connected else "Not linked — using local cache if present",
        },
        {
            "key": "calendar", "label": "Calendar", "icon": "📅",
            "status": "connected" if google_connected else "disconnected",
            "detail": "Live read-only" if google_connected else "Not linked",
        },
        {
            "key": "news", "label": "News (RSS)", "icon": "📰",
            "status": "connected" if news_ok else "disconnected",
            "detail": (
                ("RSS feeds active" + (" + Brave fallback" if brave_on else ""))
                if news_ok else "feedparser/bs4 unavailable"
            ),
        },
    ]
    return jsonify({
        "status": "ok",
        "connectors": connectors,
        "google_connected": google_connected,
    })


@news_bp.route('/api/briefing/preferences', methods=['GET', 'POST'])
def briefing_preferences():
    """Get or update briefing layout prefs (section order, toggles, categories)."""
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        prefs = _save_briefing_prefs(data)
        return jsonify({"status": "ok", "preferences": prefs})
    return jsonify({
        "status": "ok",
        "preferences": _load_briefing_prefs(),
        "categories": [
            {"name": k, "color": v["color"]} for k, v in NEWS_CATEGORIES.items()
        ],
    })


@news_bp.route('/api/sources/preferences')
def sources_preferences():
    """Return the banned + boosted source lists for the Manage Sources panel."""
    return jsonify({
        "status": "ok",
        "banned": _load_banned_sources(),
        "boosted": _load_boosted_sources(),
    })


@news_bp.route('/api/sources/ban', methods=['POST', 'DELETE'])
def sources_ban():
    """Add (POST) or remove (DELETE) a source from the server-side blacklist."""
    src = _source_from_request()
    if not src:
        return jsonify({"status": "error", "message": "A 'source' domain is required."}), 400
    banned = _load_banned_sources()
    if request.method == 'POST':
        if src not in banned:
            banned.append(src)
        # Banning a source un-boosts it — the two lists are mutually exclusive.
        boosted = [b for b in _load_boosted_sources() if b != src]
        _write_json_list(BOOSTED_SOURCES_FILE, boosted)
        banned = _write_json_list(BANNED_SOURCES_FILE, banned)
        _record_source_event(src, 'ban')
    else:  # DELETE — un-ban
        banned = _write_json_list(BANNED_SOURCES_FILE, [b for b in banned if b != src])
        _mirror_source_action(src, 'unban')
    return jsonify({"status": "ok", "source": src,
                    "banned": banned, "boosted": _load_boosted_sources()})


@news_bp.route('/api/sources/boost', methods=['POST', 'DELETE'])
def sources_boost():
    """Add (POST) or remove (DELETE) a source from the boosted/priority list."""
    src = _source_from_request()
    if not src:
        return jsonify({"status": "error", "message": "A 'source' domain is required."}), 400
    boosted = _load_boosted_sources()
    if request.method == 'POST':
        if src not in boosted:
            boosted.append(src)
        # Boosting a source un-bans it.
        banned = [b for b in _load_banned_sources() if b != src]
        _write_json_list(BANNED_SOURCES_FILE, banned)
        boosted = _write_json_list(BOOSTED_SOURCES_FILE, boosted)
        _record_source_event(src, 'boost')
    else:  # DELETE — un-boost
        boosted = _write_json_list(BOOSTED_SOURCES_FILE, [b for b in boosted if b != src])
        _mirror_source_action(src, 'unboost')
    return jsonify({"status": "ok", "source": src,
                    "banned": _load_banned_sources(), "boosted": boosted})


@news_bp.route('/api/news/feed')
def news_feed():
    """Live magazine feed for the News workspace cards.

    Honors category toggles and excludes banned sources; boosted sources sort
    first. ?categories=AI/Tech,Politics overrides the saved toggles.
    """
    raw = (request.args.get('categories') or '').strip()
    cats = None
    if raw:
        cats = [c.strip() for c in raw.split(',') if c.strip() in NEWS_CATEGORIES]
    try:
        limit_per = max(1, min(8, int(request.args.get('limit_per', 4))))
    except (TypeError, ValueError):
        limit_per = 4
    items = _fetch_news_items(categories=cats, limit_per=limit_per)
    return jsonify({
        "status": "ok",
        "items": items,
        "total": len(items),
        "banned": _load_banned_sources(),
        "boosted": _load_boosted_sources(),
        "generated_at": datetime.now().isoformat(timespec='seconds'),
    })


@news_bp.route('/api/news/read-later', methods=['GET', 'POST', 'DELETE'])
def news_read_later():
    """Saved-for-later articles store.

    GET    → {items: [...]} newest-saved first
    POST   → save an article ({url, title, source, snippet, category, ...})
    DELETE → remove by ?url= (or JSON {url}); ?clear=1 empties the list
    """
    if request.method == 'GET':
        return jsonify({"status": "ok", "items": _load_read_later()})

    with _READ_LATER_LOCK:
        items = _load_read_later()
        if request.method == 'POST':
            data = request.get_json(silent=True) or {}
            url = (data.get("url") or "").strip()
            if not url:
                return jsonify({"status": "error", "message": "url required"}), 400
            # De-dup by URL — re-saving just refreshes the stored fields.
            items = [a for a in items if a.get("url") != url]
            entry = {
                "url": url,
                "title": (data.get("title") or url)[:300],
                "source": _extract_domain(data.get("source") or url),
                "snippet": (data.get("snippet") or "")[:400],
                "category": data.get("category") or "",
                "color": data.get("color") or "",
                "saved_at": datetime.now().isoformat(timespec='seconds'),
            }
            items.insert(0, entry)
            _save_read_later(items)
            return jsonify({"status": "ok", "item": entry, "items": items})

        # DELETE
        if request.args.get("clear"):
            _save_read_later([])
            return jsonify({"status": "ok", "items": []})
        data = request.get_json(silent=True) or {}
        url = (request.args.get("url") or data.get("url") or "").strip()
        if not url:
            return jsonify({"status": "error", "message": "url required"}), 400
        items = [a for a in items if a.get("url") != url]
        _save_read_later(items)
        return jsonify({"status": "ok", "items": items})


@news_bp.route('/api/briefing/<filename>')
def get_briefing(filename):
    """Serve a briefing file content."""
    path = _find_briefing_path(filename)
    if path:
        return jsonify({'status': 'ok', 'content': path.read_text(encoding='utf-8'), 'filename': filename, 'is_html': path.suffix == '.html'})
    return jsonify({'status': 'not_found'}), 404


@news_bp.route('/api/briefing/generate', methods=['POST'])
def generate_briefing():
    """Generate a fresh daily briefing on demand via Claude and persist it.

    Replaces the old behavior of spawning a Claude Code terminal (which failed
    with "not found"). Pulls LIVE data first — today's calendar, recent unread
    email, and a fresh news search for the user's interests — then synthesizes
    the briefing from that live data plus Friday's vault/wiki context. Saves it
    as markdown in the archive and returns the markdown so the News panel can
    render it inline with the branded markdown viewer.
    """
    try:
        # Pull live sources BEFORE writing the briefing so it never runs on stale
        # cached context. This mirrors the scheduled morning-briefing routine.
        live_context = _gather_live_briefing_context()

        prompt = (
            "Generate a crisp daily briefing using the LIVE DATA below plus what "
            "you know about me (career pipeline, active tasks, co-parenting "
            "context). Cover, in order:\n"
            "1. Today's calendar events — most important first\n"
            "2. Top news relevant to me\n"
            "3. Active tasks and commitments needing attention\n"
            "4. One proactive insight or recommendation\n\n"
            "Format as clean markdown with a level-1 heading, section subheadings, "
            "and tight bullet points. Lead with the most urgent item. Be specific — "
            "use real names, dates, and details from the live data and my context, "
            "not placeholders.\n\n"
            f"{live_context}"
        )
        # ALL generation calls must carry Friday's vault/wiki context.
        system = _get_friday_system_prompt(keywords=prompt, workspace='briefing')
        # Route through the user's configured provider (Ollama / OpenAI / cloud)
        # like the chat path does — NOT a hard-coded Anthropic call — so the
        # briefing works on whatever provider chat already works on, instead of
        # failing with "ANTHROPIC_API_KEY is not set" on a non-Anthropic setup.
        content = _generate_text(
            [{"role": "user", "content": prompt}],
            system=system,
            temperature=0.4,
            orb_label="📅 Daily Briefing",
            workspace='briefing',
        )
        if not content or not content.strip():
            return jsonify({"status": "error", "message": "Empty briefing generated"}), 502

        date_str = datetime.now().strftime('%Y-%m-%d')
        briefings_dir = FRIDAY_DIR / "wiki" / "briefings"
        briefings_dir.mkdir(parents=True, exist_ok=True)
        out_path = briefings_dir / f"{date_str}.md"
        out_path.write_text(content, encoding='utf-8')

        _notify_briefing(date_str, manual=True)

        return jsonify({
            "status": "ok",
            "date": date_str,
            "filename": out_path.name,
            "content": content,
            "is_html": False,
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@news_bp.route('/api/news/front-page/weekly/latest')
def front_page_weekly_latest():
    """The most recent weekly digest (or null), plus the list of past weeks."""
    digests = _list_weekly_digests()
    if not digests:
        return jsonify({"status": "ok", "digest": None, "digests": []})
    return jsonify({"status": "ok", "digest": digests[0], "digests": digests})


@news_bp.route('/api/news/front-page/weekly/generate', methods=['POST'])
def front_page_weekly_generate():
    """Generate (or regenerate) this week's digest on demand."""
    if _offline_should_queue():
        entry = _offline_queue_add("weekly_digest", {}, dedupe_key="weekly_digest")
        return jsonify({"status": "queued", "entry": entry,
                        "message": "You're offline — the weekly digest is queued."}), 202
    try:
        digest = _generate_weekly_digest()
        _notify_weekly_digest(digest, manual=True)
        return jsonify({"status": "ok", "digest": digest})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@news_bp.route('/api/news/editorial/latest')
def editorial_latest():
    """The most recent editorial (or null), plus the list of past weeks."""
    items = _list_editorials(include_md=False)
    if not items:
        return jsonify({"status": "ok", "editorial": None, "editorials": []})
    latest = _read_editorial(items[0]["id"])
    return jsonify({"status": "ok", "editorial": latest, "editorials": items})


@news_bp.route('/api/news/editorials')
def editorials_list():
    """Index of all past editorials, newest first."""
    return jsonify({"status": "ok",
                    "editorials": _list_editorials(include_md=False)})


@news_bp.route('/api/news/editorial/generate', methods=['POST'])
def editorial_generate():
    """Write (or rewrite) this week's editorial on demand."""
    if _offline_should_queue():
        entry = _offline_queue_add("weekly_editorial", {}, dedupe_key="weekly_editorial")
        return jsonify({"status": "queued", "entry": entry,
                        "message": "You're offline — the editorial is queued."}), 202
    try:
        ed = _generate_weekly_editorial()
        _notify_weekly_editorial(ed, manual=True)
        return jsonify({"status": "ok", "editorial": ed})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@news_bp.route('/api/news/editorial/<week_id>')
def editorial_get(week_id):
    """A specific editorial by week id (YYYY-WNN)."""
    ed = _read_editorial(week_id)
    if not ed:
        return jsonify({"status": "not_found"}), 404
    return jsonify({"status": "ok", "editorial": ed})

@news_bp.route('/api/news/front-page/generate', methods=['POST'])
def front_page_generate():
    """Generate (or regenerate) a Front Page edition on demand."""
    data = request.get_json(silent=True) or {}
    slot = data.get("slot")
    if slot not in FRONT_PAGE_SLOTS:
        # Auto-pick the slot from the current Central hour.
        slot = "evening" if _front_page_central_now().hour >= 12 else "morning"
    if _offline_should_queue():
        entry = _offline_queue_add("front_page", {"slot": slot},
                                   dedupe_key=f"front_page:{slot}")
        return jsonify({"status": "queued", "entry": entry,
                        "message": "You're offline — I'll build the Front Page the "
                                   "moment you're back online."}), 202
    try:
        edition = _generate_front_page(slot)
        _notify_front_page(edition, slot, manual=True)
        return jsonify({"status": "ok", "edition": edition})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@news_bp.route('/api/news/front-page/latest')
def front_page_latest():
    """The most recent edition (or null if none generated yet)."""
    listing = _list_front_pages()
    if not listing:
        return jsonify({"status": "ok", "edition": None, "editions": []})
    latest = _read_front_page(listing[0]["id"])
    return jsonify({"status": "ok", "edition": latest, "editions": listing})


@news_bp.route('/api/news/front-pages')
def front_pages_list():
    """Index of all saved editions, newest first."""
    return jsonify({"status": "ok", "editions": _list_front_pages()})


@news_bp.route('/api/news/front-page/<edition_id>')
def front_page_get(edition_id):
    """A specific edition by id (YYYY-MM-DD-{morning|evening})."""
    edition = _read_front_page(edition_id)
    if not edition:
        return jsonify({"status": "not_found"}), 404
    return jsonify({"status": "ok", "edition": edition})


@news_bp.route('/api/news/front-page/audio', methods=['POST'])
def front_page_audio():
    """Synthesize an audio briefing of the latest (or a specified) Front Page.

    Body (all optional): {edition_id, voice}. Returns a 24kHz mono WAV stream
    that the browser plays through the News audio-player bar.
    """
    data = request.get_json(silent=True) or {}
    edition_id = data.get("edition_id")
    if edition_id:
        edition = _read_front_page(edition_id)
    else:
        listing = _list_front_pages()
        edition = _read_front_page(listing[0]["id"]) if listing else None
    if not edition:
        return jsonify({"status": "error",
                        "message": "No Front Page yet — generate one first."}), 404
    script = _front_page_narration_script(edition)
    if not script:
        return jsonify({"status": "error",
                        "message": "This Front Page has no content to read."}), 400
    try:
        buf = _synthesize_tts_wav(script, voice=data.get("voice"), style="briefing")
        return send_file(buf, mimetype='audio/wav')
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@news_bp.route('/api/news/anchor-briefing', methods=['POST'])
def news_anchor_briefing():
    """Prepare a News Anchor Mode voice briefing from the Front Page.

    Body (all optional): {edition_id}. Returns the anchor script that the
    frontend feeds to the live voice session as its opening text turn, plus a
    structured list of sources (with #:~:text= fragment URLs) so the chat panel
    can render clickable citation chips for the stories being read.

    Returns: {status, prompt, sources, headline, story_count, edition_id}.
    """
    data = request.get_json(silent=True) or {}
    edition_id = data.get("edition_id")
    if edition_id:
        edition = _read_front_page(edition_id)
    else:
        listing = _list_front_pages()
        edition = _read_front_page(listing[0]["id"]) if listing else None
    if not edition:
        return jsonify({"status": "error",
                        "message": "No Front Page yet — generate one first."}), 404
    prompt, sources = _build_anchor_briefing(edition)
    if not prompt:
        return jsonify({"status": "error",
                        "message": "This Front Page has no content to read."}), 400
    return jsonify({
        "status": "ok",
        "prompt": prompt,
        "sources": sources,
        "headline": edition.get("headline") or "Friday's Front Page",
        "story_count": len(sources),
        "edition_id": edition.get("id"),
    })


@news_bp.route('/api/news/share-to-draft', methods=['POST'])
def news_share_to_draft():
    """Seed the Draft workspace from a news article.

    Body: {article_id?, article_title, article_url, article_snippet}.
    Persists a draft seed at ~/.friday/drafts/from_news/<id>.json and returns
    its draft_id. The seed carries a ready-to-run LinkedIn/blog prompt so the
    Draft workspace can pre-populate and let the user hit Generate.
    """
    data = request.get_json(silent=True) or {}
    title = (data.get("article_title") or "").strip()
    url = (data.get("article_url") or "").strip()
    snippet = (data.get("article_snippet") or "").strip()
    if not title and not url:
        return jsonify({"status": "error",
                        "message": "article_title or article_url required"}), 400

    raw_id = (data.get("article_id") or url or title)
    draft_id = _hashlib.sha1(raw_id.encode("utf-8")).hexdigest()[:12]
    quote = snippet[:280]
    prompt = (
        f'Help me draft a LinkedIn post (or a short blog response) reacting to this '
        f'article: "{title}". '
        f'{("Source: " + url + ". ") if url else ""}'
        f'{("Key excerpt: " + chr(34) + quote + chr(34) + ". ") if quote else ""}'
        f"Make it thoughtful and in my voice, with a clear point of view and a "
        f"question at the end to spark discussion."
    )
    context = "\n".join(filter(None, [
        f"Article: {title}" if title else "",
        f"URL: {url}" if url else "",
        f"Excerpt: {quote}" if quote else "",
    ]))
    seed = {
        "draft_id": draft_id,
        "article_id": raw_id,
        "article_title": title,
        "article_url": url,
        "article_snippet": snippet,
        "quote": quote,
        "mode": "linkedin_post",
        "prompt": prompt,
        "context": context,
        "created_at": datetime.now().isoformat(timespec='seconds'),
    }
    DRAFTS_FROM_NEWS_DIR.mkdir(parents=True, exist_ok=True)
    (DRAFTS_FROM_NEWS_DIR / f"{draft_id}.json").write_text(
        json.dumps(seed, indent=2), encoding="utf-8")
    return jsonify({"status": "ok", "draft_id": draft_id, "seed": seed})


@news_bp.route('/api/news/share-to-draft/<draft_id>')
def news_share_to_draft_get(draft_id):
    """Fetch a stored draft seed by id (consumed by the Draft workspace)."""
    safe = re.sub(r"[^0-9a-zA-Z]", "", draft_id)
    path = DRAFTS_FROM_NEWS_DIR / f"{safe}.json"
    if not path.exists():
        return jsonify({"status": "not_found"}), 404
    try:
        return jsonify({"status": "ok",
                        "seed": json.loads(path.read_text(encoding="utf-8"))})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@news_bp.route('/api/news/annotate', methods=['POST', 'DELETE'])
def news_annotate():
    """Attach (or clear) personal notes on an article.

    POST   {article_id, text, article_title?, article_url?} → append a note,
           stored at ~/.friday/news/annotations/<url_hash>.json
    DELETE {article_id} → remove every note for that article
    """
    data = request.get_json(silent=True) or {}
    article_id = (data.get("article_id") or data.get("article_url") or "").strip()
    if not article_id:
        return jsonify({"status": "error", "message": "article_id required"}), 400

    NEWS_ANNOTATIONS_DIR.mkdir(parents=True, exist_ok=True)
    with _ANNOTATION_LOCK:
        if request.method == 'DELETE':
            p = _annotation_path(article_id)
            try:
                if p.exists():
                    p.unlink()
            except Exception:
                pass
            return jsonify({"status": "ok", "article_id": article_id})

        text = (data.get("text") or "").strip()
        if not text:
            return jsonify({"status": "error", "message": "text required"}), 400
        rec = _load_annotation_record(article_id) or {
            "article_id": article_id,
            "article_title": data.get("article_title") or "",
            "article_url": data.get("article_url") or article_id,
            "url_hash": _annotation_hash(article_id),
            "notes": [],
        }
        if data.get("article_title"):
            rec["article_title"] = data["article_title"]
        if data.get("article_url"):
            rec["article_url"] = data["article_url"]
        note = {"text": text[:2000],
                "timestamp": datetime.now().isoformat(timespec='seconds')}
        rec.setdefault("notes", []).append(note)
        rec["updated_at"] = note["timestamp"]
        _annotation_path(article_id).write_text(
            json.dumps(rec, indent=2), encoding="utf-8")
    return jsonify({"status": "ok", "annotation": note, "record": rec})


@news_bp.route('/api/news/annotations')
def news_annotations_all():
    """All annotations, newest first, plus the set of annotated ids/urls so the
    feed can render the 📝 badge without a per-card lookup."""
    out = []
    if NEWS_ANNOTATIONS_DIR.exists():
        for p in NEWS_ANNOTATIONS_DIR.glob("*.json"):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            for note in rec.get("notes", []):
                out.append({
                    "article_id": rec.get("article_id"),
                    "article_title": rec.get("article_title") or "",
                    "article_url": rec.get("article_url") or "",
                    "text": note.get("text", ""),
                    "timestamp": note.get("timestamp", ""),
                })
    out.sort(key=lambda n: n.get("timestamp", ""), reverse=True)
    annotated = sorted(
        {n.get("article_id") for n in out if n.get("article_id")}
        | {n.get("article_url") for n in out if n.get("article_url")}
    )
    return jsonify({"status": "ok", "annotations": out, "annotated_ids": annotated})


@news_bp.route('/api/news/annotations/<path:article_id>')
def news_annotations_for(article_id):
    """Every note for a specific article (by article_id or its url)."""
    rec = _load_annotation_record(article_id)
    return jsonify({"status": "ok",
                    "annotations": (rec or {}).get("notes", []),
                    "record": rec})


@news_bp.route('/api/news/archive')
def news_archive():
    """Paginated access to the permanent article archive, newest first.

    Query params: offset, limit (≤200), category, source (substring match),
    date_from / date_to (YYYY-MM-DD, inclusive)."""
    try:
        offset = max(0, int(request.args.get('offset', 0)))
    except (TypeError, ValueError):
        offset = 0
    try:
        limit = max(1, min(200, int(request.args.get('limit', 50))))
    except (TypeError, ValueError):
        limit = 50
    category = (request.args.get('category') or '').strip()
    source = (request.args.get('source') or '').strip().lower()
    date_from = (request.args.get('date_from') or '').strip()
    date_to = (request.args.get('date_to') or '').strip()
    sort = (request.args.get('sort') or 'time').strip()
    records = _read_archive(category=category, source=source,
                            date_from=date_from, date_to=date_to)
    # Banned sources never surface in the feed (they may still sit in the archive
    # if banned after the fact) — mirror the live feed's exclusion.
    banned = set(_load_banned_sources())
    if banned:
        records = [a for a in records if (a.get('source') or '') not in banned]
    # _read_archive returns newest-first; re-sort for the other modes. Pagination
    # stays correct because we sort the full filtered set before slicing.
    if sort == 'relevance':
        records.sort(key=lambda a: a.get('relevance_score') or 0, reverse=True)
    elif sort == 'source':
        records.sort(key=lambda a: (a.get('source') or '').lower())
    total = len(records)
    page = records[offset:offset + limit]
    return jsonify({
        "status": "ok",
        "items": page,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": (offset + limit) < total,
    })


@news_bp.route('/api/news/archive/stats')
def news_archive_stats():
    """Archive summary: total articles, date range, per-category, per-source."""
    total = 0
    per_cat, per_source, dates = {}, {}, []
    for date_str, _path in _archive_day_files():
        day = _load_archive_day(date_str)
        if day:
            dates.append(date_str)
        for a in day:
            total += 1
            c = a.get("category") or "Uncategorized"
            per_cat[c] = per_cat.get(c, 0) + 1
            s = a.get("source") or "unknown"
            per_source[s] = per_source.get(s, 0) + 1
    dates.sort()
    by_source = dict(sorted(per_source.items(),
                            key=lambda kv: kv[1], reverse=True))
    return jsonify({
        "status": "ok",
        "total": total,
        "date_range": {
            "from": dates[0] if dates else "",
            "to": dates[-1] if dates else "",
            "days": len(dates),
        },
        "by_category": per_cat,
        "by_source": by_source,
    })


@news_bp.route('/api/news/clusters')
def news_clusters():
    """Trending clusters: stories covered by 3+ sources within the last 24h."""
    try:
        pool, _ = _gather_front_page_pool(per_cat=16)
    except Exception:
        pool = []
    if not pool:
        try:
            pool = _fetch_news_items(limit_per=8)
        except Exception:
            pool = []
    now = _time.time()
    recent = [it for it in pool
              if not it.get("ts") or (now - it["ts"]) <= 24 * 3600]
    clusters = _cluster_articles(recent)
    return jsonify({
        "status": "ok",
        "clusters": clusters,
        "total": len(clusters),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    })


# ═══════════════════════════════════════════════════════════════
#  SOURCE TRUST GRAPH  (media/agent reputation — distinct from people)
# ═══════════════════════════════════════════════════════════════

@news_bp.route('/api/source-trust')
def api_source_trust_all():
    """All learned source trust scores."""
    if not _HAS_TRUST_GRAPHS:
        return jsonify({"status": "error", "message": "source trust graph unavailable"}), 501
    try:
        g = get_source_trust_graph(friday_dir=FRIDAY_DIR)
        rows = g.leaderboard(limit=1000)
        return jsonify({"status": "ok", "sources": rows, "count": len(rows)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@news_bp.route('/api/source-trust/leaderboard')
def api_source_trust_leaderboard():
    """Sources ranked by composite trust score."""
    if not _HAS_TRUST_GRAPHS:
        return jsonify({"status": "error", "message": "source trust graph unavailable"}), 501
    try:
        limit = max(1, min(500, int(request.args.get('limit', 100))))
    except (TypeError, ValueError):
        limit = 100
    try:
        rows = get_source_trust_graph(friday_dir=FRIDAY_DIR).leaderboard(limit=limit)
        return jsonify({"status": "ok", "leaderboard": rows, "count": len(rows)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@news_bp.route('/api/source-trust/observe', methods=['POST'])
def api_source_trust_observe():
    """Manually add an observation (user correction). Body: {domain, type,
    dimension, signal, detail?, counter_sources?, name?}."""
    if not _HAS_TRUST_GRAPHS:
        return jsonify({"status": "error", "message": "source trust graph unavailable"}), 501
    data = request.get_json(silent=True) or {}
    domain = (data.get("domain") or data.get("source") or "").strip()
    dimension = (data.get("dimension") or "").strip()
    if not domain or not dimension:
        return jsonify({"status": "error", "message": "domain and dimension are required"}), 400
    try:
        rec = get_source_trust_graph(friday_dir=FRIDAY_DIR).observe(
            domain,
            obs_type=data.get("type", "user_observation"),
            dimension=dimension,
            signal=data.get("signal", 0.5),
            detail=data.get("detail", ""),
            counter_sources=data.get("counter_sources"),
            signed_by="user",
            name=data.get("name"),
        )
        if rec is None:
            return jsonify({"status": "error",
                            "message": "invalid dimension or signal"}), 400
        return jsonify({"status": "ok", "source": rec})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@news_bp.route('/api/source-trust/<path:domain>')
def api_source_trust_one(domain):
    """Single source detail with full observation history."""
    if not _HAS_TRUST_GRAPHS:
        return jsonify({"status": "error", "message": "source trust graph unavailable"}), 501
    try:
        rec = get_source_trust_graph(friday_dir=FRIDAY_DIR).get(domain)
        if not rec:
            return jsonify({"status": "error", "message": "source not found"}), 404
        return jsonify({"status": "ok", "source": rec})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
#  FEDERATION PROTOCOL  (signed source-trust attestations)
# ═══════════════════════════════════════════════════════════════

@news_bp.route('/api/federation/attestations')
def api_federation_attestations():
    """List the attestations this agent has signed, plus identity + counts."""
    if not _HAS_TRUST_GRAPHS:
        return jsonify({"status": "error", "message": "federation unavailable"}), 501
    try:
        agent_id = None
        if _HAS_INTEGRITY:
            agent_id = get_integrity_engine(
                friday_dir=FRIDAY_DIR,
                governance_key_fn=_get_governance_key).get_public_key_hex()
        signed = federation.list_attestations(friday_dir=FRIDAY_DIR)
        imported = federation.list_imported(friday_dir=FRIDAY_DIR)
        return jsonify({
            "status": "ok",
            "agent_id": agent_id,
            "attestations": signed,
            "signed_count": len(signed),
            "imported_count": len(imported),
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@news_bp.route('/api/federation/attestations/sign', methods=['POST'])
def api_federation_sign():
    """Sign a new source attestation. Body: {source_domain, observation:{type,
    claim, evidence, counter_sources}}."""
    if not _HAS_TRUST_GRAPHS:
        return jsonify({"status": "error", "message": "federation unavailable"}), 501
    data = request.get_json(silent=True) or {}
    domain = (data.get("source_domain") or data.get("domain") or "").strip()
    observation = data.get("observation") or {}
    if not domain or not observation.get("type"):
        return jsonify({"status": "error",
                        "message": "source_domain and observation.type are required"}), 400
    try:
        att = federation.sign_attestation(
            domain, observation,
            governance_key_fn=_get_governance_key, friday_dir=FRIDAY_DIR)
        if not att:
            return jsonify({"status": "error",
                            "message": "signing unavailable (no Ed25519 key)"}), 501
        federation.record_attestation(att, friday_dir=FRIDAY_DIR)
        return jsonify({"status": "ok", "attestation": att})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@news_bp.route('/api/federation/attestations/import', methods=['POST'])
def api_federation_import():
    """Import a peer attestation (verifies the signature before accepting).
    Body: a single attestation object, or {attestations: [...]}."""
    if not _HAS_TRUST_GRAPHS:
        return jsonify({"status": "error", "message": "federation unavailable"}), 501
    data = request.get_json(silent=True) or {}
    atts = data.get("attestations") if isinstance(data.get("attestations"), list) else [data]
    results = []
    for att in atts:
        try:
            results.append(federation.import_attestation(
                att, governance_key_fn=_get_governance_key, friday_dir=FRIDAY_DIR))
        except Exception as e:
            results.append({"accepted": False, "reason": str(e)})
    accepted = sum(1 for r in results if r.get("accepted"))
    return jsonify({"status": "ok", "accepted": accepted,
                    "total": len(results), "results": results})


@news_bp.route('/api/federation/trust-scores')
def api_federation_trust_scores():
    """This agent's computed source trust scores in shareable form (the
    leaderboard plus the signing identity that vouches for them)."""
    if not _HAS_TRUST_GRAPHS:
        return jsonify({"status": "error", "message": "federation unavailable"}), 501
    try:
        agent_id = None
        if _HAS_INTEGRITY:
            agent_id = get_integrity_engine(
                friday_dir=FRIDAY_DIR,
                governance_key_fn=_get_governance_key).get_public_key_hex()
        rows = get_source_trust_graph(friday_dir=FRIDAY_DIR).leaderboard(limit=1000)
        scores = [{"source_domain": r["domain"], "name": r["name"],
                   "trust_score": r["trust_score"], "scores": r["scores"],
                   "article_count": r["article_count"]} for r in rows]
        return jsonify({
            "status": "ok",
            "agent_id": agent_id,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "trust_scores": scores,
            "count": len(scores),
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@news_bp.route('/api/news/source-stats', methods=['GET', 'POST'])
def news_source_stats():
    """Per-source engagement data for the 'Your Media Diet' panel.

    POST {source, action, category?} records one interaction. Recognized
    actions: click, read, read_later, boost, ban, ignore.
    GET returns per-source counters, per-category read counts, and the
    most/least engaged sources."""
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        _record_source_event(data.get("source") or "",
                             (data.get("action") or "").strip().lower(),
                             (data.get("category") or "").strip())
        return jsonify({"status": "ok"})
    stats = _load_source_stats()
    sources = stats.get("sources", {})
    ranked = []
    for dom, rec in sources.items():
        ranked.append({"source": dom, "engagement": _source_engagement(rec),
                       **rec})
    ranked.sort(key=lambda r: r["engagement"], reverse=True)
    most = [r for r in ranked if r["engagement"] > 0][:8]
    least = [r for r in reversed(ranked) if r["engagement"] <= 0][:8]
    return jsonify({
        "status": "ok",
        "sources": sources,
        "categories": stats.get("categories", {}),
        "most_engaged": most,
        "least_engaged": least,
        "total_sources": len(sources),
    })


@news_bp.route('/api/news/wiki-connections')
def news_wiki_connections():
    """Wiki pages whose title appears in an article's title or snippet.

    Accepts ?title= & ?snippet= (the card has them) or ?article_id= to look the
    article up in the current live feed. Case-insensitive substring match
    against humanized wiki page titles."""
    title = (request.args.get("title") or "").strip()
    snippet = (request.args.get("snippet") or "").strip()
    article_id = (request.args.get("article_id") or "").strip()
    if not title and not snippet and article_id:
        try:
            for it in _fetch_news_items(limit_per=8):
                if it.get("id") == article_id:
                    title, snippet = it.get("title", ""), it.get("snippet", "")
                    break
        except Exception:
            pass
    hay = f"{title} {snippet}".lower()
    matches = []
    if hay.strip():
        for page in _wiki_title_index():
            if page["title"].lower() in hay:
                matches.append(page)
    matches.sort(key=lambda m: len(m["title"]), reverse=True)
    return jsonify({"status": "ok", "matches": matches[:5], "count": len(matches)})


@news_bp.route('/api/news/deep-dive', methods=['POST'])
def news_deep_dive():
    """Fetch an article, summarize it with Claude, and cache the result.

    Body: {url, title?, refresh?}. Returns {summary, implications, key_quotes}."""
    data = request.get_json(silent=True) or {}
    result, status = _deep_dive_article(
        data.get("url"), title=data.get("title"), refresh=bool(data.get("refresh")))
    return jsonify(result), status
