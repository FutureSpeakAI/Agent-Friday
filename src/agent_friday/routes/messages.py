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
from agent_friday.services import message_triage
from agent_friday.services.calendar_engine import (
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
from agent_friday.services.model_router import (
    _gated_vault_control,
    _generate_text,
    _get_friday_system_prompt,
    _predict_route_provider,
)  # noqa: E501

messages_bp = Blueprint('messages', __name__)


def _qbool(value):
    """Coerce a raw querystring value to True/False/None. None means
    "the filter was not requested" -- apply_filters treats that as a
    no-op rather than an implicit False, so an absent flag never
    silently drops mail."""
    if value is None:
        return None
    return str(value).strip().lower() in ("1", "true", "yes", "on")



@messages_bp.route('/api/messages')
def api_messages():
    """Classified message cards. ?lane= filters to a single lane;
    ?include_archived=1 keeps archived/snoozed cards in the result."""
    lane = (request.args.get("lane") or "").strip().lower()
    account = request.args.get("account")
    query = (request.args.get("query") or "").strip() or None
    flagged = request.args.get("flagged")
    min_confidence = request.args.get("min_confidence", type=float)
    exclude_bulk = request.args.get("exclude_bulk")
    has_attachments = request.args.get("has_attachments")
    include_archived = request.args.get("include_archived") in ("1", "true", "yes")
    try:
        limit = max(5, min(100, int(request.args.get("limit", 40))))
    except (TypeError, ValueError):
        limit = 40
    # ?q= is a Gmail search, sent to Gmail's own q= across every account.
    gmail_q = (request.args.get("q") or "").strip() or None
    result = message_triage.collect(limit_per_account=limit, query=gmail_q)
    cards, source = result["messages"], result["source"]
    now_iso = datetime.now().isoformat(timespec="seconds")
    if not include_archived:
        cards = [c for c in cards if not c["archived"]
                 and not (c["snoozed_until"] and c["snoozed_until"] > now_iso)]
    if lane and lane in MESSAGE_LANE_IDS:
        cards = [c for c in cards if c["lane"] == lane]
    cards = message_triage.apply_filters(
        cards,
        account=account,
        query=query,
        flagged=_qbool(flagged),
        min_confidence=min_confidence,
        exclude_bulk=bool(_qbool(exclude_bulk)),
        has_attachment=_qbool(has_attachments),
    )
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
    out = {
        "status": "ok",
        "messages": cards,
        "total": len(cards),
        "source": source,
        "lanes": MESSAGE_LANES,
        "generated_at": now_iso,
        "query": gmail_q,
        "errors": result.get("errors") or [],
        "partial": bool(result.get("partial")),
        "rate_limited": bool(result.get("rate_limited")),
    }
    if result.get("search_failed"):
        # Nothing could be read: a failure, never "no mail". No total.
        out.update(status="error", search_failed=True, total=None,
                   error=_failure_text(result.get("errors") or []))
    return jsonify(out)


def _failure_text(errors):
    parts = []
    for e in errors:
        who = e.get("label") or "An account"
        parts.append("%s: %s" % (who, e.get("error") or "unknown error"))
    return "Couldn't read mail. " + " ".join(parts) if parts else "Couldn't read mail."



@messages_bp.route('/api/messages/stats')
def api_messages_stats():
    """Per-lane counts + an actionable (non-noise/sub, unread, active) badge."""
    result = message_triage.collect(limit_per_account=80)
    cards, source = result["messages"], result["source"]
    now_iso = datetime.now().isoformat(timespec="seconds")
    active = [c for c in cards if not c["archived"]
              and not (c["snoozed_until"] and c["snoozed_until"] > now_iso)]
    counts = {l["id"]: 0 for l in MESSAGE_LANES}
    for c in active:
        counts[c["lane"]] = counts.get(c["lane"], 0) + 1
    actionable_lanes = {l["id"] for l in MESSAGE_LANES if l["actionable"]}
    actionable = sum(1 for c in active
                     if c["lane"] in actionable_lanes and c["unread"])
    out = {
        "status": "ok",
        "counts": counts,
        "total": len(active),
        "actionable": actionable,
        "source": source,
        "lanes": MESSAGE_LANES,
        "per_account": message_triage.account_summary(active),
        "errors": result.get("errors") or [],
        "partial": bool(result.get("partial")),
        "rate_limited": bool(result.get("rate_limited")),
    }
    if result.get("search_failed"):
        # The dock badge must not read a failure as "0 to do".
        out.update(status="error", search_failed=True, counts=None, total=None,
                   actionable=None, error=_failure_text(result.get("errors") or []))
    return jsonify(out)


@messages_bp.route('/api/messages/attachment')
def api_message_attachment():
    """One attachment's bytes (read-only). Images and PDFs display inline;
    everything else downloads. Never rendered as a page on Friday's origin."""
    from agent_friday.services import gmail_api, gmail_read
    a = request.args
    aid, mid, att = a.get("account", ""), a.get("message", ""), a.get("id", "")
    if not (aid and mid and att):
        return jsonify({"status": "error", "error": "account, message and id are required"}), 400
    try:
        data = gmail_read.get_attachment(aid, mid, att)
    except gmail_api.GmailError as e:
        return jsonify({"status": "error", "kind": e.kind, "error": e.message}), 502
    name = re.sub(r'[\r\n"\\]', '_', a.get("name") or "attachment")
    mime = (a.get("mime") or "application/octet-stream").lower()
    inline = mime.startswith("image/") and mime != "image/svg+xml" or mime == "application/pdf"
    resp = Response(data, mimetype=mime if inline else "application/octet-stream")
    resp.headers["Content-Disposition"] = ('inline' if inline else 'attachment') + '; filename="%s"' % name
    resp.headers["X-Content-Type-Options"] = "nosniff"
    if mime != "application/pdf":
        resp.headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self' data:"
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


@messages_bp.route('/api/messages/<thread_id>')
def api_message_thread(thread_id):
    """A whole Gmail thread from the account it belongs to (?account=<id>;
    without one, every connected account is tried). A Gmail error is
    reported as an error, not as "not found"; the offline cache answers
    only when no account is connected at all."""
    from agent_friday.services import gmail_read
    res = gmail_read.get_thread(thread_id, request.args.get("account") or None)
    if res.get("status") == "ok":
        return jsonify({**res, "source": "gmail"})
    if res.get("kind") in ("auth",) or res.get("kind") == "not_found":
        rules = _load_message_rules()
        state = _load_message_state()
        for r in _load_cached_messages():
            if str(_message_id(r)) == str(thread_id) or str(r.get("thread_id")) == str(thread_id):
                card = _normalize_message(r, rules, state)
                return jsonify({"status": "ok", "thread_id": thread_id, "messages": [{
                    "id": card["id"], "sender": card["sender"],
                    "subject": card["subject"], "timestamp": card["timestamp"],
                    "body": r.get("body") or card["snippet"], "snippet": card["snippet"],
                    "html": "", "attachments": [], "to": "", "cc": "",
                }], "source": "cache", "note": res.get("error")})
    code = 404 if res.get("kind") == "not_found" else 502
    return jsonify({"status": "error", "thread_id": thread_id, "messages": [],
                    "kind": res.get("kind"), "error": res.get("error")}), code


@messages_bp.route('/api/messages/classify', methods=['POST'])
def api_messages_classify():
    """Manually reclassify a message into a lane, persisting an override so it
    sticks across refreshes, AND teaching the sender prior. Body: {id, lane,
    learn?}.

    THE CORRECTION USED TO BE THROWN AWAY. message_triage.record_signal() has
    always existed, /api/messages/learn has always exposed it, and
    message_triage's own docstring promises that "correcting one newsletter
    fixes every future newsletter from that sender". Nothing ever called it:
    index.html's reclassify() posts here and only here, so every correction
    moved one message and taught nothing. ~/.friday/messages/sender_signals.json
    was 38 bytes with zero senders recorded after months of use.

    Learning here rather than in the client because there are two front-ends
    (index.html and ui_parts/app.html) plus the agent tools, and a teaching
    step that lives in one of them is a teaching step the other two skip.

    A single correction is deliberately weak, not authoritative: classify()
    weights a learned sender at W_LEARNED_WEAK per correction until
    LEARN_CONFIDENT_AT of them agree. So a one-off move ("this person usually
    writes about X but this once wrote about Y") nudges rather than rewrites,
    and the response says exactly how many more it would take.
    """
    data = request.get_json(silent=True) or {}
    mid = str(data.get("id") or "").strip()
    lane = str(data.get("lane") or "").strip().lower()
    learn = data.get("learn", True)
    if not mid or lane not in MESSAGE_LANE_IDS:
        return jsonify({"status": "error",
                        "message": "id and a valid lane are required"}), 400
    sender = ""
    with _MESSAGE_LOCK:
        # Persist a lane override onto the cached message so reclassification
        # survives the next live fetch (overrides are honored in _normalize).
        cached = _load_cached_messages()
        found = False
        for r in cached:
            if str(_message_id(r)) == mid:
                r["lane"] = lane
                sender = str(r.get("sender") or r.get("from") or "")
                found = True
                break
        if found:
            _cache_messages(cached)
        # Also record in state for messages not in cache (live-only).
        state = _load_message_state()
        before = dict(state.get(mid, {}))
        st = state.get(mid, {})
        st["lane_override"] = lane
        # Remember the sender so a live-only message can still teach; without
        # this, correcting anything not in the cache is silently unlearnable.
        if sender:
            st["sender"] = sender
        sender = sender or str(st.get("sender") or "")
        state[mid] = st
        _save_message_state(state)

    learned = None
    if learn and sender:
        try:
            learned = message_triage.record_signal(sender, lane)
        except Exception as e:
            # A correction that fails to teach must still move the message.
            learned = {"ok": False, "error": str(e)}
    message_triage._collect_cache.clear()
    # `before` lets the UI undo the move with /api/messages/restore. The
    # sender lesson is not unlearned by an undo; it is one weak vote.
    return jsonify({"status": "ok", "id": mid, "lane": lane,
                    "learned": learned, "before": {mid: before}})


_LOCAL_ACTIONS = ("archive", "unarchive", "snooze", "unsnooze", "flag", "unflag", "read", "unread")


def _apply_local(st, action, data):
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
        st.pop("unread", None)
    elif action == "unread":
        st["read"] = False
        st["unread"] = True
    return st


@messages_bp.route('/api/messages/action', methods=['POST'])
def api_messages_action():
    """Archive / snooze / flag / mark read or unread, for one message
    ({id}) or many ({ids}). Every call returns `before` (each message's
    previous local state) so the UI can undo it exactly with
    /api/messages/restore.

    With `gmail: [{id, account_id, thread_id}]`, archive, read/unread and
    flag (star) also change Gmail itself for accounts that granted
    gmail.modify. Gmail goes first: a conversation Gmail refused is not
    changed in Friday either, so the two never disagree. `gmail_changes`
    (exactly what was added and removed per conversation) goes back to
    /api/messages/restore to undo it in Gmail too. Snooze stays Friday's own:
    Gmail's API has none. Accounts without the permission change in Friday
    only, and `gmail_status` says so."""
    data = request.get_json(silent=True) or {}
    ids = [str(i).strip() for i in (data.get("ids") or []) if str(i).strip()]
    if not ids and str(data.get("id") or "").strip():
        ids = [str(data.get("id")).strip()]
    action = str(data.get("action") or "").strip().lower()
    if not ids or not action:
        return jsonify({"status": "error", "message": "id(s) and action required"}), 400
    if action not in _LOCAL_ACTIONS:
        return jsonify({"status": "error", "message": f"unknown action {action}"}), 400
    gmail_changes, gmail_status, not_changed = _sync_gmail(action, ids, data.get("gmail"))
    ids = [i for i in ids if i not in not_changed]
    before = {}
    with _MESSAGE_LOCK:
        state = _load_message_state()
        for mid in ids[:500]:
            before[mid] = dict(state.get(mid, {}))
            state[mid] = _apply_local(dict(state.get(mid, {})), action, data)
        _save_message_state(state)
    message_triage._collect_cache.clear()
    out = {"status": "ok", "ids": list(before), "action": action, "before": before,
           "gmail_changes": gmail_changes, "gmail_status": gmail_status,
           "not_changed": not_changed}
    if not before and not_changed:
        out["status"] = "error"
        out["message"] = "Gmail did not make the change: " + next(iter(not_changed.values()))
    if len(before) == 1:
        mid = next(iter(before))
        out.update(id=mid, state=state[mid])
    return jsonify(out)


def _sync_gmail(action, ids, items):
    """Apply a Friday action to Gmail for the accounts that allow it.
    -> (gmail_changes {account_id: {thread_id: {added, removed}}},
        gmail_status {account_id: "synced" | "not_permitted" | "failed"},
        not_changed {card_id: why})"""
    from agent_friday.services import gmail_mailbox as gm
    changes, status, not_changed = {}, {}, {}
    if action not in gm.ACTIONS or not isinstance(items, list):
        return changes, status, not_changed
    wanted = set(ids)
    by_acct = {}
    for it in items:
        if not isinstance(it, dict) or str(it.get("id")) not in wanted:
            continue
        aid, tid = str(it.get("account_id") or ""), str(it.get("thread_id") or "")
        if aid and tid:
            by_acct.setdefault(aid, []).append((str(it["id"]), tid))
    for aid, pairs in by_acct.items():
        if not gm.can_modify(aid):
            status[aid] = "not_permitted"
            continue
        try:
            res = gm.apply_action(aid, [t for _, t in pairs], action)
        except Exception as e:
            status[aid] = "failed"
            for cid, _ in pairs:
                not_changed[cid] = str(e)
            continue
        changes[aid] = res["changed"]
        status[aid] = "failed" if res["failed"] and not res["changed"] else "synced"
        for cid, tid in pairs:
            if tid in res["failed"]:
                not_changed[cid] = res["failed"][tid]
    return changes, status, not_changed


@messages_bp.route('/api/messages/restore', methods=['POST'])
def api_messages_restore():
    """Undo: put each message's local state back to exactly what an action
    reported as `before`, and in Gmail reverse exactly the `gmail_changes` it
    reported. Body: {states: {id: {...}}, gmail_changes?: {...}}."""
    body = request.get_json(silent=True) or {}
    states = body.get("states") or {}
    gfailed = {}
    for aid, changed in (body.get("gmail_changes") or {}).items():
        try:
            from agent_friday.services import gmail_mailbox as gm
            gfailed.update(gm.undo(aid, changed)["failed"])
        except Exception as e:
            gfailed[aid] = str(e)
    if not isinstance(states, dict) or not states:
        if body.get("gmail_changes"):
            message_triage._collect_cache.clear()
            return jsonify({"status": "ok" if not gfailed else "partial", "restored": 0, "gmail_failed": gfailed})
        return jsonify({"status": "error", "message": "states required"}), 400
    allowed = {"archived", "snoozed_until", "flagged", "read", "unread", "lane_override", "sender"}
    with _MESSAGE_LOCK:
        state = _load_message_state()
        for mid, st in list(states.items())[:500]:
            clean = {k: v for k, v in (st or {}).items() if k in allowed}
            if clean:
                state[str(mid)] = clean
            else:
                state.pop(str(mid), None)
        _save_message_state(state)
    message_triage._collect_cache.clear()
    return jsonify({"status": "ok" if not gfailed else "partial", "restored": len(states),
                    "gmail_failed": gfailed})


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
        _kw = subject + " " + snippet
        system = _get_friday_system_prompt(
            keywords=_kw, workspace="draft",
            provider=_predict_route_provider(keywords=_kw, workspace="draft"),
            vault_control=_gated_vault_control())
        draft = _generate_text([{"role": "user", "content": prompt}],
                               system=system, max_tokens=1200, workspace='messages')
        return jsonify({"status": "ok", "draft": draft})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@messages_bp.route('/api/messages/learn', methods=['POST'])
def api_messages_learn():
    """Record a manual sender->lane correction. Three consistent corrections
    for the same sender outrank configured domain/keyword rules. Body:
    {sender, lane}."""
    data = request.get_json(silent=True) or {}
    sender = str(data.get("sender") or "").strip().lower()
    lane = str(data.get("lane") or "").strip().lower()
    if not sender or lane not in MESSAGE_LANE_IDS:
        return jsonify({"status": "error",
                        "message": "sender and a valid lane are required"}), 400
    result = message_triage.record_signal(sender, lane)
    return jsonify({"status": "ok", "sender": sender, "lane": lane, "result": result})


@messages_bp.route('/api/messages/forget', methods=['POST'])
def api_messages_forget():
    """Remove a learned sender override, reverting to configured/default
    rules. Body: {sender}."""
    data = request.get_json(silent=True) or {}
    sender = str(data.get("sender") or "").strip().lower()
    if not sender:
        return jsonify({"status": "error", "message": "sender is required"}), 400
    result = message_triage.forget_sender(sender)
    return jsonify({"status": "ok", "sender": sender, "result": result})

