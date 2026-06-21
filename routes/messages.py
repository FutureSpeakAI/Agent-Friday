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
from services.calendar_engine import (
    MESSAGE_LANES,
    MESSAGE_LANE_IDS,
    _MESSAGE_LOCK,
    _cache_messages,
    _collect_messages,
    _events_for_day,
    _extract_gmail_body,
    _google_credentials,
    _load_cached_messages,
    _load_message_rules,
    _load_message_state,
    _message_id,
    _normalize_message,
    _save_message_state,
)  # noqa: E501
from services.model_router import (
    _generate_text,
    _get_friday_system_prompt,
)  # noqa: E501

messages_bp = Blueprint('messages', __name__)



@messages_bp.route('/api/messages')
def api_messages():
    """Classified message cards. ?lane= filters to a single lane;
    ?include_archived=1 keeps archived/snoozed cards in the result."""
    lane = (request.args.get("lane") or "").strip().lower()
    include_archived = request.args.get("include_archived") in ("1", "true", "yes")
    try:
        limit = max(5, min(100, int(request.args.get("limit", 40))))
    except (TypeError, ValueError):
        limit = 40
    cards, source = _collect_messages(limit=limit)
    now_iso = datetime.now().isoformat(timespec="seconds")
    if not include_archived:
        cards = [c for c in cards if not c["archived"]
                 and not (c["snoozed_until"] and c["snoozed_until"] > now_iso)]
    if lane and lane in MESSAGE_LANE_IDS:
        cards = [c for c in cards if c["lane"] == lane]
    # Cross-reference: flag messages whose sender is an attendee of an upcoming
    # event (next 7 days). Best-effort — failures must not break the inbox.
    try:
        email_events = {}
        for i in range(7):
            d = date.today() + timedelta(days=i)
            for ev in _events_for_day(d):
                for a in ev.get("attendees", []):
                    email_events.setdefault((a or "").lower(), []).append({
                        "id": ev.get("id"), "title": ev.get("title"),
                        "start_time": ev.get("start_time"),
                    })
        for c in cards:
            hit = email_events.get((c.get("sender_email") or "").lower())
            if hit:
                c["related_event"] = hit[0]
    except Exception:
        pass
    return jsonify({
        "status": "ok",
        "messages": cards,
        "total": len(cards),
        "source": source,
        "lanes": MESSAGE_LANES,
        "generated_at": now_iso,
    })


@messages_bp.route('/api/messages/stats')
def api_messages_stats():
    """Per-lane counts + an actionable (non-noise/sub, unread, active) badge."""
    cards, source = _collect_messages(limit=80)
    now_iso = datetime.now().isoformat(timespec="seconds")
    active = [c for c in cards if not c["archived"]
              and not (c["snoozed_until"] and c["snoozed_until"] > now_iso)]
    counts = {l["id"]: 0 for l in MESSAGE_LANES}
    for c in active:
        counts[c["lane"]] = counts.get(c["lane"], 0) + 1
    actionable_lanes = {l["id"] for l in MESSAGE_LANES if l["actionable"]}
    actionable = sum(1 for c in active
                     if c["lane"] in actionable_lanes and c["unread"])
    return jsonify({
        "status": "ok",
        "counts": counts,
        "total": len(active),
        "actionable": actionable,
        "source": source,
        "lanes": MESSAGE_LANES,
    })


@messages_bp.route('/api/messages/<thread_id>')
def api_message_thread(thread_id):
    """Full thread for a message. Pulls the whole Gmail thread when linked,
    otherwise returns the single cached message body."""
    rules = _load_message_rules()
    state = _load_message_state()
    creds = _google_credentials()
    if creds is not None:
        try:
            from googleapiclient.discovery import build
            svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
            thread = svc.users().threads().get(
                userId="me", id=thread_id, format="full").execute()
            out = []
            for msg in thread.get("messages", []):
                headers = {h["name"].lower(): h["value"]
                           for h in msg.get("payload", {}).get("headers", [])}
                body = _extract_gmail_body(msg.get("payload", {}))
                ts = msg.get("internalDate")
                try:
                    ts_iso = (datetime.fromtimestamp(int(ts) / 1000).isoformat()
                              if ts else headers.get("date", ""))
                except Exception:
                    ts_iso = headers.get("date", "")
                out.append({
                    "id": msg.get("id"),
                    "sender": headers.get("from", "unknown"),
                    "to": headers.get("to", ""),
                    "subject": headers.get("subject", "(no subject)"),
                    "timestamp": ts_iso,
                    "body": body or (msg.get("snippet") or ""),
                    "snippet": msg.get("snippet", ""),
                })
            return jsonify({"status": "ok", "thread_id": thread_id,
                            "messages": out, "source": "gmail"})
        except Exception as e:
            # fall through to cache on any API error
            pass
    # Cache fallback — find by id/thread.
    for r in _load_cached_messages():
        if str(_message_id(r)) == str(thread_id) or str(r.get("thread_id")) == str(thread_id):
            card = _normalize_message(r, rules, state)
            return jsonify({"status": "ok", "thread_id": thread_id, "messages": [{
                "id": card["id"], "sender": card["sender"],
                "subject": card["subject"], "timestamp": card["timestamp"],
                "body": r.get("body") or card["snippet"], "snippet": card["snippet"],
            }], "source": "cache"})
    return jsonify({"status": "not_found", "thread_id": thread_id, "messages": []}), 404


@messages_bp.route('/api/messages/classify', methods=['POST'])
def api_messages_classify():
    """Manually reclassify a message into a lane, persisting an override so it
    sticks across refreshes. Body: {id, lane}."""
    data = request.get_json(silent=True) or {}
    mid = str(data.get("id") or "").strip()
    lane = str(data.get("lane") or "").strip().lower()
    if not mid or lane not in MESSAGE_LANE_IDS:
        return jsonify({"status": "error",
                        "message": "id and a valid lane are required"}), 400
    with _MESSAGE_LOCK:
        # Persist a lane override onto the cached message so reclassification
        # survives the next live fetch (overrides are honored in _normalize).
        cached = _load_cached_messages()
        found = False
        for r in cached:
            if str(_message_id(r)) == mid:
                r["lane"] = lane
                found = True
                break
        if found:
            _cache_messages(cached)
        # Also record in state for messages not in cache (live-only).
        state = _load_message_state()
        st = state.get(mid, {})
        st["lane_override"] = lane
        state[mid] = st
        _save_message_state(state)
    return jsonify({"status": "ok", "id": mid, "lane": lane})


@messages_bp.route('/api/messages/action', methods=['POST'])
def api_messages_action():
    """Archive / snooze / flag / mark-read a message. Body: {id, action,
    until?(iso)}. action in archive|unarchive|snooze|unsnooze|flag|unflag|read|unread."""
    data = request.get_json(silent=True) or {}
    mid = str(data.get("id") or "").strip()
    action = str(data.get("action") or "").strip().lower()
    if not mid or not action:
        return jsonify({"status": "error", "message": "id and action required"}), 400
    with _MESSAGE_LOCK:
        state = _load_message_state()
        st = state.get(mid, {})
        if action == "archive":
            st["archived"] = True
        elif action == "unarchive":
            st["archived"] = False
        elif action == "snooze":
            st["snoozed_until"] = data.get("until") or (
                datetime.now() + timedelta(hours=4)).isoformat(timespec="seconds")
        elif action == "unsnooze":
            st["snoozed_until"] = ""
        elif action == "flag":
            st["flagged"] = True
        elif action == "unflag":
            st["flagged"] = False
        elif action == "read":
            st["read"] = True
        elif action == "unread":
            st["read"] = False
        else:
            return jsonify({"status": "error", "message": f"unknown action {action}"}), 400
        state[mid] = st
        _save_message_state(state)
    return jsonify({"status": "ok", "id": mid, "action": action, "state": st})


@messages_bp.route('/api/messages/draft', methods=['POST'])
def api_messages_draft():
    """Generate a reply draft with Claude, grounded in vault/wiki context.
    Body: {id?, sender?, subject?, snippet?, body?, instructions?}."""
    data = request.get_json(silent=True) or {}
    sender = data.get("sender") or ""
    subject = data.get("subject") or ""
    snippet = data.get("snippet") or data.get("body") or ""
    instructions = (data.get("instructions") or "").strip()
    lane = (data.get("lane") or "").strip()
    if not (sender or subject or snippet):
        return jsonify({"status": "error",
                        "message": "Provide at least sender/subject/snippet"}), 400
    lane_hint = {
        "career": ("This is career/recruiting correspondence. Be warm, "
                   "professional, concise, and enthusiastic without overselling."),
        "finance": "This is financial correspondence. Be precise and formal.",
        "futurespeak": "This is a collaborator/dev message. Be technical and direct.",
        "family": "This is family correspondence. Be warm and personal.",
    }.get(lane, "")
    prompt = (
        "Draft a reply to the email below. Return ONLY the reply body — no "
        "subject line, no preamble, no sign-off placeholder beyond a natural "
        "closing.\n\n"
        f"{lane_hint}\n\n"
        f"From: {sender}\nSubject: {subject}\n\n{snippet}\n\n"
        + (f"Extra instructions from the user: {instructions}\n" if instructions else "")
    )
    try:
        system = _get_friday_system_prompt(keywords=subject + " " + snippet,
                                           workspace="draft")
        draft = _generate_text([{"role": "user", "content": prompt}],
                               system=system, max_tokens=1200, workspace='messages')
        return jsonify({"status": "ok", "draft": draft})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
