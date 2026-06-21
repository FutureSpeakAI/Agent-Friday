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
import core
from core import (
    FRIDAY_DIR,
    HOME,
)  # noqa: E501


def _recent_unread_emails(limit=12):
    """Read recent unread (or last-24h) messages from the local Gmail cache.

    Mirrors the candidate paths used by the Gmail signal watcher
    (_trigger_gmail_signals). Returns a list of message dicts, most recent first.
    Empty list if no cache exists — the Gmail connector exports into these files.
    """
    candidates = [
        FRIDAY_DIR / "gmail" / "inbox.json",
        FRIDAY_DIR / "gmail-cache.json",
    ]
    inbox_path = next((p for p in candidates if p.exists()), None)
    if not inbox_path:
        return []
    try:
        data = json.loads(inbox_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    messages = data if isinstance(data, list) else data.get("messages", [])
    if not isinstance(messages, list):
        return []

    cutoff = datetime.utcnow() - timedelta(days=1)
    selected = []
    for m in reversed(messages):  # cache appends chronologically; newest last
        if not isinstance(m, dict):
            continue
        # Prefer an explicit unread flag; otherwise treat "newer_than:1d" as the
        # signal — the same filter the scheduled briefing uses.
        is_unread = m.get('unread') if 'unread' in m else (not m.get('read', False))
        ts_raw = m.get('received_at') or m.get('date') or m.get('timestamp')
        recent = True
        try:
            recent = datetime.fromisoformat(str(ts_raw).replace("Z", "")) >= cutoff
        except Exception:
            recent = True  # undated — include rather than silently drop
        if is_unread or recent:
            selected.append(m)
        if len(selected) >= limit:
            break
    return selected


# ═══════════════════════════════════════════════════════════════
#  GOOGLE (Gmail + Calendar) — live, read-only via the official API
# ═══════════════════════════════════════════════════════════════
# Read-only scopes for both Gmail and Calendar, served by one shared OAuth
# client (the same Desktop client the gmail-mcp-multi setup already created).
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]
GOOGLE_TOKEN_PATH = FRIDAY_DIR / "google_token.json"


def _google_client_type(data):
    """Return the client *kind* — "installed" (Desktop/CLI) or "web" — if the
    file holds real credentials under that key, else None.

    Desktop clients nest under "installed"; Web clients under "web". We prefer
    "installed" when a single file somehow carries both, because the Desktop flow
    accepts any loopback redirect without GCP registration (no
    redirect_uri_mismatch), whereas the Web flow requires the exact callback URL
    to be pre-registered in the console.
    """
    if not isinstance(data, dict):
        return None
    for kind in ("installed", "web"):  # order = preference
        block = data.get(kind)
        if not isinstance(block, dict):
            continue
        cid = (block.get("client_id") or "").strip()
        csec = (block.get("client_secret") or "").strip()
        if not cid or not csec:
            continue
        # Reject the template placeholders Google ships (e.g.
        # "YOUR_CLIENT_ID.apps.googleusercontent.com") — a structurally-valid but
        # unfilled template would otherwise produce an auth URL with the literal
        # placeholder in it.
        if "YOUR_CLIENT" in cid.upper() or "YOUR_CLIENT" in csec.upper():
            continue
        # A real OAuth client_id always ends with this Google-issued suffix.
        if not cid.endswith(".apps.googleusercontent.com"):
            continue
        return kind
    return None


def _google_client_block(data):
    """Return the inner client block ('installed' or 'web') if the file holds
    *real* credentials, else None. Prefers the Desktop ("installed") block.
    """
    kind = _google_client_type(data)
    return data.get(kind) if kind else None


def _google_client_config():
    """Locate the OAuth *client* secrets (the app credentials, not the token).

    Returns (config_dict, source_label) or (None, None). Checks, in order:
    ~/.friday/credentials.json, the existing gmail-mcp Desktop client at
    ~/.gmail-mcp/oauth-keys.json, ~/.friday/oauth-keys.json, any
    client_secret*.json that Google delivers into ~/.friday, ~/.gmail-mcp or
    ~/Downloads, then the GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET env vars.
    Files that exist but only contain template placeholders are skipped so a
    stale ~/.gmail-mcp/oauth-keys.json can't shadow the real credentials.

    A Desktop ("installed") client is preferred over a Web ("web") client
    *across all candidates*: if credentials.json still holds the old Web client
    but a freshly-downloaded client_secret*.json holds the new Desktop client,
    the Desktop one wins — because only the Desktop flow can use a loopback
    redirect without hitting redirect_uri_mismatch.
    """
    candidates = [
        FRIDAY_DIR / "credentials.json",
        HOME / ".gmail-mcp" / "oauth-keys.json",
        FRIDAY_DIR / "oauth-keys.json",
    ]
    # Google's console downloads the client as client_secret_*.json; pick those
    # up automatically rather than forcing a manual rename.
    for d in (FRIDAY_DIR, HOME / ".gmail-mcp", HOME / "Downloads"):
        try:
            candidates.extend(sorted(d.glob("client_secret*.json")))
        except Exception:
            pass

    # First valid match of each kind, in candidate order; prefer Desktop overall.
    first_installed = None  # (data, source)
    first_web = None
    for p in candidates:
        if not p.exists():
            continue
        try:
            # utf-8-sig: the gmail-mcp file is a PowerShell-written BOM'd JSON.
            data = json.loads(p.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        kind = _google_client_type(data)
        if kind == "installed" and first_installed is None:
            first_installed = (data, str(p))
            break  # Desktop is the top preference — stop as soon as we find one.
        if kind == "web" and first_web is None:
            first_web = (data, str(p))
    if first_installed is not None:
        return first_installed
    if first_web is not None:
        return first_web

    cid = os.environ.get("GOOGLE_CLIENT_ID")
    csec = os.environ.get("GOOGLE_CLIENT_SECRET")
    if cid and csec:
        return {
            "installed": {
                "client_id": cid,
                "client_secret": csec,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        }, "env"
    return None, None


def _google_credentials():
    """Load stored Google OAuth credentials, refreshing if expired.

    Returns a google.oauth2 Credentials object, or None if the user hasn't
    connected Google yet (no token) or the libraries are missing. On a silent
    refresh the rotated token is written back to disk.

    Multi-account aware: once any account exists in the encrypted multi-account
    store (services.google_accounts), this resolves through the PRIMARY account
    so the legacy single-account callers (briefing, messages, calendar) keep
    working against encrypted-at-rest credentials. The plaintext
    ~/.friday/google_token.json is migrated into that store on first use and then
    removed. Lazy import avoids a calendar_engine <-> google_accounts cycle.
    """
    try:
        from services.google_accounts import has_accounts, primary_credentials
        if has_accounts():
            return primary_credentials()
    except Exception:
        pass
    if not GOOGLE_TOKEN_PATH.exists():
        return None
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request as GoogleRequest
    except Exception:
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(GOOGLE_TOKEN_PATH), GOOGLE_SCOPES)
    except Exception as e:
        print(f"[google] could not load stored token: {e}", flush=True)
        return None
    # Refresh whenever we hold a refresh token and the access token is missing or
    # expired. Desktop-client refresh tokens are long-lived (they don't expire in
    # 7 days the way unverified Web "Testing" tokens do), so a successful refresh
    # here keeps Friday connected indefinitely without re-consent.
    if creds and creds.refresh_token and (creds.expired or not creds.valid):
        try:
            creds.refresh(GoogleRequest())
            _write_google_token(creds)
            print("[google] refreshed and persisted access token", flush=True)
        except Exception as e:
            # A revoked grant or rotated client secret lands here — surface it so
            # the cause is diagnosable rather than a silent "not connected".
            print(f"[google] token refresh failed ({e}); reconnect at "
                  f"/api/google/auth", flush=True)
            return None
    if not creds or not creds.valid:
        return None
    return creds


def _fetch_gmail_recent(limit=15):
    """Recent Gmail messages (last 24h, unread first) via the Gmail API.

    Returns a list of {sender, subject, snippet, timestamp, thread_id, labels}.
    If Google isn't connected, returns a single-item list with an 'error' key
    pointing the caller at /api/google/auth — callers can detect that and fall
    back to the local cache.
    """
    creds = _google_credentials()
    if not creds:
        return [{"error": "Google not connected. Visit /api/google/auth to link Gmail (read-only)."}]
    try:
        from googleapiclient.discovery import build
    except Exception as e:
        return [{"error": f"google-api-python-client not installed: {e}"}]
    try:
        svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
        # Unread-in-last-day first, then anything from the last day, deduped.
        seen, out = set(), []
        for q in ("is:unread newer_than:1d", "newer_than:1d"):
            resp = svc.users().messages().list(userId="me", q=q, maxResults=limit).execute()
            for ref in resp.get("messages", []):
                mid = ref.get("id")
                if not mid or mid in seen:
                    continue
                seen.add(mid)
                msg = svc.users().messages().get(
                    userId="me", id=mid, format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                ).execute()
                headers = {
                    h["name"].lower(): h["value"]
                    for h in msg.get("payload", {}).get("headers", [])
                }
                ts = msg.get("internalDate")
                try:
                    ts_iso = datetime.fromtimestamp(int(ts) / 1000).isoformat() if ts else (headers.get("date") or "")
                except Exception:
                    ts_iso = headers.get("date") or ""
                out.append({
                    "sender": headers.get("from", "unknown"),
                    "subject": headers.get("subject", "(no subject)"),
                    "snippet": (msg.get("snippet") or "").strip(),
                    "timestamp": ts_iso,
                    "thread_id": msg.get("threadId", ""),
                    "labels": msg.get("labelIds", []),
                })
                if len(out) >= limit:
                    break
            if len(out) >= limit:
                break
        return out
    except Exception as e:
        return [{"error": f"Gmail fetch failed: {e}"}]


def _fetch_calendar_today():
    """Today's + tomorrow's Google Calendar events via the Calendar API.

    Tomorrow is included so the briefing can flag prep needed tonight. Returns a
    list of {title, start_time, end_time, location, attendees, description}, or a
    single-item list with an 'error' key if Google isn't connected.
    """
    creds = _google_credentials()
    if not creds:
        return [{"error": "Google not connected. Visit /api/google/auth to link Calendar (read-only)."}]
    try:
        from googleapiclient.discovery import build
    except Exception as e:
        return [{"error": f"google-api-python-client not installed: {e}"}]
    try:
        svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
        now = datetime.now().astimezone()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=2)  # today + tomorrow (for prep)
        resp = svc.events().list(
            calendarId="primary",
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=50,
        ).execute()
        out = []
        for ev in resp.get("items", []):
            s, e = ev.get("start", {}), ev.get("end", {})
            attendees = [a.get("email", "") for a in ev.get("attendees", []) if a.get("email")]
            out.append({
                "title": ev.get("summary", "(untitled)"),
                "start_time": s.get("dateTime") or s.get("date") or "",
                "end_time": e.get("dateTime") or e.get("date") or "",
                "location": ev.get("location", ""),
                "attendees": attendees,
                "description": (ev.get("description") or "").strip()[:500],
            })
        return out
    except Exception as e:
        return [{"error": f"Calendar fetch failed: {e}"}]


def _google_section_error(items):
    """Return the 'error' note if a fetch helper returned its error sentinel."""
    if items and isinstance(items[0], dict) and "error" in items[0]:
        return items[0]["error"]
    return None


# ═══════════════════════════════════════════════════════════════
#  MESSAGES — "Friday's Comms Center"
#  Smart triage that auto-classifies every message into lanes using
#  sender domain + subject keyword rules. Rules live at
#  ~/.friday/message_rules.json so the user can tune routing without a
#  redeploy. Live mail comes from the Gmail API when Google is linked,
#  otherwise from the heartbeat-populated cache at
#  ~/.friday/messages/cache.json. Classification is applied either way
#  before anything is served to the UI.
#
#  PRIVACY NOTE: the defaults shipped in source are deliberately
#  DOMAIN/keyword-based only — no personal names. Personal-name routing
#  (family, colleagues) lives ONLY in the user's local
#  ~/.friday/message_rules.json, which is outside the (public) repo.
# ═══════════════════════════════════════════════════════════════
MESSAGES_DIR = FRIDAY_DIR / "messages"
MESSAGE_CACHE_FILE = MESSAGES_DIR / "cache.json"
MESSAGE_RULES_FILE = FRIDAY_DIR / "message_rules.json"
MESSAGE_STATE_FILE = MESSAGES_DIR / "state.json"  # archived/snoozed/flagged per message
_MESSAGE_LOCK = threading.Lock()

# Lane definitions: id, label, UI color key (mirrored in head.html CSS), and
# whether the lane counts toward the "actionable" notification badge. Order is
# priority order — the first lane whose rules match wins.
MESSAGE_LANES = [
    {"id": "career", "label": "Career", "color": "career", "actionable": True},
    {"id": "finance", "label": "Finance", "color": "finance", "actionable": True},
    {"id": "futurespeak", "label": "Projects", "color": "futurespeak", "actionable": True},
    {"id": "family", "label": "Family", "color": "family", "actionable": True},
    {"id": "subscriptions", "label": "Subscriptions", "color": "subscriptions", "actionable": False},
    {"id": "noise", "label": "Noise", "color": "noise", "actionable": False},
]
MESSAGE_LANE_IDS = {l["id"] for l in MESSAGE_LANES}


def _default_message_rules():
    """Generic, non-PII default triage rules (safe for a public repo).

    Matches on sender domain and subject/snippet keywords only. Personal-name
    routing is layered on top from the user's local message_rules.json.
    """
    return {
        "version": 1,
        "lanes": [
            {
                "id": "career",
                "domains": [
                    "linkedin.com", "indeed.com", "greenhouse.io", "lever.co",
                    "ashbyhq.com", "workday.com", "myworkday.com", "jobvite.com",
                    "smartrecruiters.com", "icims.com", "ziprecruiter.com",
                    "hire.lever.co", "us.greenhouse-mail.io", "glassdoor.com",
                    "wellfound.com", "angel.co", "dice.com", "builtin.com",
                ],
                "senders": ["recruiting", "recruiter", "talent", "careers", "jobs", "no-reply@hi.wellfound"],
                "keywords": [
                    "application received", "your application", "interview", "recruiter",
                    "next steps", "phone screen", "we received your application",
                    "thanks for applying", "job opportunity", "hiring", "offer letter",
                ],
            },
            {
                "id": "finance",
                "domains": [
                    "capitalone.com", "americanexpress.com", "aexp.com",
                    "chase.com", "bankofamerica.com", "wellsfargo.com",
                    "fidelity.com", "schwab.com", "vanguard.com", "intuit.com",
                    "venmo.com", "paypal.com", "discover.com",
                ],
                "senders": ["alerts@", "statements@", "no-reply@alerts", "secure@"],
                "keywords": [
                    "statement is ready", "payment due", "transaction alert",
                    "account alert", "balance", "invoice", "deposit", "wire transfer",
                    "your statement", "tax document", "1099", "k-1",
                ],
            },
            {
                "id": "futurespeak",
                "domains": ["github.com", "notifications.github.com", "gitlab.com"],
                "senders": ["notifications@github.com", "noreply@github.com"],
                "keywords": [
                    "pull request", "merged", "opened an issue", "commented on",
                    "new release", "review requested", "ci failed", "workflow run",
                ],
            },
            {
                "id": "family",
                "domains": [],
                "senders": [],
                "keywords": ["school", "pta", "parent portal", "report card", "field trip"],
            },
            {
                "id": "subscriptions",
                "domains": ["substack.com", "mailchimp.com", "beehiiv.com", "ghost.io"],
                "senders": ["newsletter@", "digest@", "updates@", "hello@", "team@"],
                "keywords": [
                    "newsletter", "unsubscribe", "this week in", "weekly digest",
                    "daily digest", "your weekly", "subscribe", "view in browser",
                ],
            },
        ],
    }


def _load_message_rules():
    """Load triage rules, seeding the defaults on first run. Fail-soft."""
    try:
        if MESSAGE_RULES_FILE.exists():
            data = json.loads(MESSAGE_RULES_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("lanes"), list):
                return data
    except Exception:
        pass
    rules = _default_message_rules()
    try:
        FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
        MESSAGE_RULES_FILE.write_text(json.dumps(rules, indent=2), encoding="utf-8")
    except Exception:
        pass
    return rules


def _sender_domain(sender):
    """Pull the bare domain out of a 'Name <user@host>' From header."""
    s = (sender or "").lower()
    m = re.search(r"@([a-z0-9.\-]+)", s)
    if not m:
        return ""
    dom = m.group(1).strip(".")
    # Collapse mail subdomains (mail.github.com -> github.com) for matching,
    # but keep the full host too so subdomain-specific rules still hit.
    return dom


def _classify_message(msg, rules=None):
    """Return a lane id for a single message using domain + keyword rules.

    Evaluation is two-pass within the lane priority order: a domain/sender
    match is stronger than a keyword match, so we first look for any lane whose
    domain or sender matches, and only fall back to keyword matching if none do.
    Anything unmatched lands in 'noise'.
    """
    rules = rules or _load_message_rules()
    sender = (msg.get("sender") or msg.get("from") or "").lower()
    subject = (msg.get("subject") or "").lower()
    snippet = (msg.get("snippet") or msg.get("preview") or "").lower()
    domain = _sender_domain(sender)
    lanes = rules.get("lanes", [])

    # Pass 1 — domain / explicit sender substring (high confidence)
    for lane in lanes:
        lid = lane.get("id")
        if lid not in MESSAGE_LANE_IDS:
            continue
        for d in lane.get("domains", []):
            d = (d or "").lower().strip()
            if d and (domain == d or domain.endswith("." + d) or d in sender):
                return lid
        for s in lane.get("senders", []):
            s = (s or "").lower().strip()
            if s and s in sender:
                return lid

    # Pass 2 — subject / snippet keywords
    hay = subject + " \n " + snippet
    for lane in lanes:
        lid = lane.get("id")
        if lid not in MESSAGE_LANE_IDS:
            continue
        for kw in lane.get("keywords", []):
            kw = (kw or "").lower().strip()
            if kw and kw in hay:
                return lid

    return "noise"


def _message_id(msg):
    """Stable id for a message: prefer Gmail id/thread, else hash of fields."""
    mid = msg.get("id") or msg.get("message_id")
    if mid:
        return str(mid)
    tid = msg.get("thread_id")
    if tid:
        return str(tid)
    raw = (msg.get("sender", "") + "|" + msg.get("subject", "") + "|" +
           str(msg.get("timestamp", "")))
    return _hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:16]


def _load_message_state():
    """Per-message UI state: {id: {archived, snoozed_until, flagged, read}}."""
    try:
        if MESSAGE_STATE_FILE.exists():
            data = json.loads(MESSAGE_STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_message_state(state):
    MESSAGES_DIR.mkdir(parents=True, exist_ok=True)
    MESSAGE_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state


def _load_cached_messages():
    """Load heartbeat-cached messages (list of raw dicts). Fail-soft."""
    try:
        if MESSAGE_CACHE_FILE.exists():
            data = json.loads(MESSAGE_CACHE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data = data.get("messages", [])
            if isinstance(data, list):
                return [m for m in data if isinstance(m, dict)]
    except Exception:
        pass
    return []


def _cache_messages(messages):
    MESSAGES_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "messages": messages,
        "cached_at": datetime.now().isoformat(timespec="seconds"),
    }
    MESSAGE_CACHE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return messages


def _normalize_message(raw, rules, state):
    """Shape a raw mail dict into the UI card model + apply state & lane."""
    sender = raw.get("sender") or raw.get("from") or "unknown"
    # Split "Display Name <addr>" into a friendly name + address.
    name, addr = sender, ""
    m = re.match(r"\s*\"?([^\"<]*?)\"?\s*<([^>]+)>", sender)
    if m:
        name = (m.group(1).strip() or m.group(2).strip())
        addr = m.group(2).strip()
    elif "@" in sender:
        addr = sender.strip()
        name = sender.split("@")[0]
    mid = _message_id(raw)
    lane = raw.get("lane") if raw.get("lane") in MESSAGE_LANE_IDS else _classify_message(raw, rules)
    st = state.get(mid, {}) if isinstance(state, dict) else {}
    labels = raw.get("labels") or []
    unread = ("UNREAD" in labels) if labels else (not st.get("read"))
    if st.get("read"):
        unread = False
    return {
        "id": mid,
        "thread_id": raw.get("thread_id") or mid,
        "sender": name or "unknown",
        "sender_email": addr,
        "initial": (name or addr or "?").strip()[:1].upper() or "?",
        "subject": raw.get("subject") or "(no subject)",
        "snippet": (raw.get("snippet") or raw.get("preview") or "").strip()[:400],
        "timestamp": raw.get("timestamp") or raw.get("date") or "",
        "lane": lane,
        "labels": labels,
        "unread": bool(unread),
        "has_attachment": bool(raw.get("has_attachment")),
        "archived": bool(st.get("archived")),
        "snoozed_until": st.get("snoozed_until") or "",
        "flagged": bool(st.get("flagged")),
    }


def _collect_messages(limit=40):
    """Fetch the freshest messages (live Gmail if linked, else cache), classify,
    and return (cards, source) where source is 'gmail'|'cache'|'empty'."""
    rules = _load_message_rules()
    state = _load_message_state()
    raw, source = [], "empty"

    if _google_credentials() is not None:
        fetched = _fetch_gmail_recent(limit=limit)
        if not _google_section_error(fetched):
            raw = fetched
            source = "gmail"
            try:
                _cache_messages(raw)
            except Exception:
                pass
    if not raw:
        raw = _load_cached_messages()
        source = "cache" if raw else "empty"

    cards = [_normalize_message(r, rules, state) for r in raw]
    # Newest first when timestamps are comparable; stable otherwise.
    def _ts(c):
        return str(c.get("timestamp") or "")
    cards.sort(key=_ts, reverse=True)
    return cards, source


def _extract_gmail_body(payload):
    """Recursively pull a text/plain (fallback text/html-stripped) body."""
    if not isinstance(payload, dict):
        return ""
    mime = payload.get("mimeType", "")
    body = payload.get("body", {})
    data = body.get("data")
    if data and mime == "text/plain":
        try:
            return base64.urlsafe_b64decode(data + "===").decode("utf-8", "ignore")
        except Exception:
            return ""
    # Walk parts; prefer plain, keep html as a fallback.
    html_fallback = ""
    for part in payload.get("parts", []) or []:
        txt = _extract_gmail_body(part)
        if txt:
            if part.get("mimeType") == "text/plain":
                return txt
            html_fallback = html_fallback or txt
    if not html_fallback and data and mime == "text/html":
        try:
            raw = base64.urlsafe_b64decode(data + "===").decode("utf-8", "ignore")
            html_fallback = re.sub(r"<[^>]+>", " ", raw)
        except Exception:
            pass
    return re.sub(r"[ \t]+", " ", html_fallback).strip() if html_fallback else ""


# ═══════════════════════════════════════════════════════════════
#  CALENDAR — "Friday's Time Intelligence"
#  Today timeline with gap analysis + Friday annotations, a 7-day week
#  strip, on-demand prep cards, and natural-language quick-add. Live
#  events come from the Calendar API (read-only) when linked; locally
#  created quick-add events are merged from
#  ~/.friday/calendar/local_events.json. NO time-analytics feature
#  (explicitly excluded).
# ═══════════════════════════════════════════════════════════════
CALENDAR_DIR = FRIDAY_DIR / "calendar"
CAL_LOCAL_FILE = CALENDAR_DIR / "local_events.json"
CAL_ANNOTATIONS_FILE = CALENDAR_DIR / "annotations.json"
CAL_PREP_FILE = CALENDAR_DIR / "prep_cache.json"
_CALENDAR_LOCK = threading.Lock()

# Heuristic career-event detection keywords (no PII in source — finer routing
# can live in the user's local calendar rules later).
_CAREER_KW = ["interview", "screen", "recruiter", "onsite", "hiring", "panel",
              "coffee chat", "phone screen", "1:1 with"]


def _load_local_events():
    try:
        if CAL_LOCAL_FILE.exists():
            data = json.loads(CAL_LOCAL_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [e for e in data if isinstance(e, dict)]
    except Exception:
        pass
    return []


def _save_local_events(events):
    CALENDAR_DIR.mkdir(parents=True, exist_ok=True)
    CAL_LOCAL_FILE.write_text(json.dumps(events, indent=2), encoding="utf-8")
    return events


def _fetch_calendar_range(start, end):
    """Live Calendar events in [start, end) as dicts incl. stable id.
    Returns [] (not an error sentinel) when Google isn't linked, so callers can
    merge with local events transparently."""
    creds = _google_credentials()
    if not creds:
        return []
    try:
        from googleapiclient.discovery import build
        svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
        resp = svc.events().list(
            calendarId="primary",
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=250,
        ).execute()
        out = []
        for ev in resp.get("items", []):
            s, e = ev.get("start", {}), ev.get("end", {})
            out.append({
                "id": ev.get("id", ""),
                "title": ev.get("summary", "(untitled)"),
                "start_time": s.get("dateTime") or s.get("date") or "",
                "end_time": e.get("dateTime") or e.get("date") or "",
                "all_day": bool(s.get("date") and not s.get("dateTime")),
                "location": ev.get("location", ""),
                "attendees": [a.get("email", "") for a in ev.get("attendees", [])
                              if a.get("email")],
                "description": (ev.get("description") or "").strip()[:1000],
                "html_link": ev.get("htmlLink", ""),
                "source": "google",
            })
        return out
    except Exception:
        return []


def _parse_dt(s):
    """Best-effort parse of an ISO datetime or date string to a datetime."""
    if not s:
        return None
    try:
        txt = str(s)
        if len(txt) == 10:  # date only
            return datetime.fromisoformat(txt)
        return datetime.fromisoformat(txt.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        try:
            return datetime.fromisoformat(str(s)[:19])
        except Exception:
            return None


def _classify_event(ev):
    """Tag an event as career / normal from its text."""
    hay = (ev.get("title", "") + " " + ev.get("description", "") + " " +
           ev.get("location", "")).lower()
    if any(k in hay for k in _CAREER_KW):
        return "career"
    return "normal"


def _events_for_day(target_date):
    """All events (Google + local) intersecting the given date, time-sorted."""
    start = datetime(target_date.year, target_date.month, target_date.day)
    end = start + timedelta(days=1)
    events = _fetch_calendar_range(start, end)
    # Merge local quick-add events whose start falls on this day.
    for le in _load_local_events():
        sdt = _parse_dt(le.get("start_time"))
        if sdt and start <= sdt < end:
            events.append(le)
    # De-dup by id, then sort by start.
    seen, merged = set(), []
    for ev in events:
        k = ev.get("id") or (ev.get("title", "") + ev.get("start_time", ""))
        if k in seen:
            continue
        seen.add(k)
        ev = dict(ev)
        ev["type"] = ev.get("type") or _classify_event(ev)
        merged.append(ev)
    merged.sort(key=lambda e: str(e.get("start_time") or ""))
    return merged


def _gap_analysis(events, day):
    """Find free blocks between 6 AM and midnight, label them heuristically."""
    day_start = datetime(day.year, day.month, day.day, 6, 0)
    day_end = datetime(day.year, day.month, day.day, 23, 59)
    # Build busy intervals from timed (non all-day) events.
    busy = []
    for ev in events:
        if ev.get("all_day"):
            continue
        s = _parse_dt(ev.get("start_time"))
        e = _parse_dt(ev.get("end_time")) or (s + timedelta(hours=1) if s else None)
        if s and e and e > s:
            busy.append((max(s, day_start), min(e, day_end)))
    busy.sort()
    gaps, cursor = [], day_start
    for s, e in busy:
        if s > cursor:
            gaps.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < day_end:
        gaps.append((cursor, day_end))

    def _label(gs, ge):
        mins = (ge - gs).total_seconds() / 60.0
        # Lunch if the gap straddles noon and is reasonable.
        if gs.hour <= 12 <= ge.hour and 30 <= mins <= 150:
            return "Lunch"
        if mins < 30:
            return "Quick break"
        if mins <= 60:
            return "Buffer"
        if mins >= 120:
            return "Deep work window"
        return "Open block"

    out = []
    for gs, ge in gaps:
        mins = (ge - gs).total_seconds() / 60.0
        if mins < 20:  # ignore tiny slivers
            continue
        out.append({
            "start_time": gs.isoformat(timespec="minutes"),
            "end_time": ge.isoformat(timespec="minutes"),
            "minutes": int(mins),
            "label": _label(gs, ge),
        })
    return out


def _load_json_dict(path):
    try:
        if path.exists():
            d = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
    except Exception:
        pass
    return {}


def _save_json_dict(path, data):
    CALENDAR_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def _day_annotation(day, events):
    """A one-line Friday intro for the day, cached per-date (lightweight Claude)."""
    key = day.isoformat()
    cache = _load_json_dict(CAL_ANNOTATIONS_FILE)
    if key in cache:
        return cache[key]
    if not events:
        note = "Clear day — a blank canvas. Good for deep work or rest."
    else:
        try:
            # Both live in services/model_router.py — an UPPER layer — so they
            # must be imported lazily at call time.
            from services.model_router import _generate_text, _get_friday_system_prompt
            titles = "; ".join(f"{e.get('title','')} ({e.get('type','normal')})"
                               for e in events[:8])
            system = _get_friday_system_prompt(keywords=titles, workspace="chat")
            note = _generate_text([{"role": "user", "content": (
                "In ONE short sentence (max 22 words), give me a warm, sharp "
                "heads-up about my day given these events. No preamble.\n\n"
                + titles)}], system=system, max_tokens=120, workspace='calendar').strip().strip('"')
        except Exception:
            note = f"{len(events)} event{'s' if len(events) != 1 else ''} today. You've got this."
    cache[key] = note
    try:
        _save_json_dict(CAL_ANNOTATIONS_FILE, cache)
    except Exception:
        pass
    return note


def _detect_conflicts(events):
    """Return a set of event ids that overlap another timed event."""
    timed = []
    for ev in events:
        if ev.get("all_day"):
            continue
        s = _parse_dt(ev.get("start_time"))
        e = _parse_dt(ev.get("end_time")) or (s + timedelta(hours=1) if s else None)
        if s and e:
            timed.append((s, e, ev.get("id") or ev.get("title", "")))
    conflicted = set()
    for i in range(len(timed)):
        for j in range(i + 1, len(timed)):
            s1, e1, id1 = timed[i]
            s2, e2, id2 = timed[j]
            if s1 < e2 and s2 < e1:
                conflicted.add(id1)
                conflicted.add(id2)
    return conflicted


def _cached_messages_by_email():
    """{lower-email: [{subject, id, thread_id}]} from the message cache.

    Powers calendar↔email cross-references without a live Gmail round-trip.
    """
    out = {}
    rules = _load_message_rules()
    state = _load_message_state()
    for raw in _load_cached_messages():
        card = _normalize_message(raw, rules, state)
        addr = (card.get("sender_email") or "").lower()
        if not addr:
            continue
        out.setdefault(addr, []).append({
            "subject": card["subject"], "id": card["id"],
            "thread_id": card["thread_id"], "lane": card["lane"],
        })
    return out


def _enrich_events(events):
    """Attach conflict flags, prep availability, and related emails."""
    conflicts = _detect_conflicts(events)
    by_email = _cached_messages_by_email()
    for ev in events:
        # Cross-reference: emails from any attendee of this event.
        related = []
        for a in ev.get("attendees", []):
            related.extend(by_email.get((a or "").lower(), []))
        ev["related_emails"] = related[:5]
        eid = ev.get("id") or ev.get("title", "")
        ev["conflict"] = eid in conflicts
        ev["prep_available"] = ev.get("type") == "career" or bool(
            [a for a in ev.get("attendees", []) if a])
    return events


# Fixed loopback redirect for Desktop ("installed") OAuth clients. Google accepts
# any localhost/loopback redirect for installed apps *without* registering it in
# the GCP console, so we pin a deterministic URI matching the port the server
# binds to (3000). Critically, the app binds 0.0.0.0 when a tunnel password is
# set, so request.host_url can be a tunnel/LAN host that Google would REJECT for
# an installed client — pinning localhost:3000 keeps the loopback flow valid.
GOOGLE_DESKTOP_REDIRECT_URI = "http://localhost:3000/api/google/auth/callback"


def _google_redirect_uri(cfg, client_type=None):
    """Canonical OAuth redirect URI for this client config.

    Desktop ("installed") clients accept any loopback redirect without
    registering it in GCP, so we pin the deterministic localhost:3000 URI and
    never hit redirect_uri_mismatch — even when the user reaches the app through
    a tunnel or LAN IP. Web ("web") clients must match a URI pre-registered in
    the console, so we derive it from the actual request host (legacy behavior).
    """
    kind = client_type or _google_client_type(cfg) or "installed"
    if kind == "installed":
        return GOOGLE_DESKTOP_REDIRECT_URI
    return request.host_url.rstrip('/') + '/api/google/auth/callback'


def _write_google_token(creds):
    """Persist Google credentials to ~/.friday/google_token.json atomically.

    Written via temp file + rename so a crash mid-write can't truncate the token
    and force a re-auth. Returns True on success.
    """
    try:
        FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
        tmp = GOOGLE_TOKEN_PATH.with_name(GOOGLE_TOKEN_PATH.name + ".tmp")
        tmp.write_text(creds.to_json(), encoding="utf-8")
        tmp.replace(GOOGLE_TOKEN_PATH)
        return True
    except Exception as e:
        print(f"[google] could not persist token ({e})", flush=True)
        return False


