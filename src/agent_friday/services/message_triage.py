"""
message_triage.py — multi-account collection, scored triage, and filtering
for the Friday messages center.

WHY THIS IS A SEPARATE MODULE
-----------------------------
calendar_engine.py owns message plumbing today, but three of its pieces are
weak for a two-account inbox:

  1. ``_collect_messages()`` resolves credentials via ``_google_credentials()``,
     which returns the PRIMARY account only. A second connected Google account
     is invisible to the inbox even though ``google_accounts.merged_gmail()``
     already implements the fan-out.
  2. ``_classify_message()`` returns on the FIRST matching lane in list order,
     so lane ordering silently arbitrates every tie and a one-word keyword
     collision beats a strong sender match in a later lane.
  3. Corrections are stored per message id, so correcting the same newsletter
     twice teaches the system nothing about the sender.

This module fixes all three without editing calendar_engine, which keeps the
existing behaviour intact as a fallback and keeps the diff reviewable.

DESIGN DECISIONS (confirmed with the user)
------------------------------------------
* Learned sender corrections OUTRANK configured domain rules. Three consistent
  corrections beat a domain rule. This makes the inbox feel responsive; the
  cost is that sloppy corrections can override a deliberate rule, so
  ``forget_sender()`` exists and every classification reports its reasons.
* Lanes are read from ``calendar_engine.MESSAGE_LANES`` at runtime. Nothing
  here hardcodes lane ids, so adding a lane needs no change to this file.
* Both a merged stream and per-account views are supported; the UI chooses.
  ``account_summary()`` feeds the split view, ``apply_filters(account=...)``
  feeds the filtered single view.

STORAGE
-------
Learned signals live in their own file so this module never writes to the
existing rules or state files:
    ~/.friday/messages/sender_signals.json
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from agent_friday.services import calendar_engine as ce

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

SIGNALS_FILE = os.path.join(ce.MESSAGES_DIR, "sender_signals.json")
_SIGNAL_LOCK = threading.Lock()

# Number of consistent corrections after which a learned sender prior is
# treated as authoritative (outranking configured domain rules).
LEARN_CONFIDENT_AT = 3

# In-process TTL cache for collect(). Both Google accounts get fanned out
# on every call otherwise, which is what made the inbox take 12-20s to
# load. 45s keeps it feeling live without re-fetching on every click.
_COLLECT_CACHE_TTL_SECONDS = 45
_collect_cache_lock = threading.Lock()
_collect_cache: Dict[str, Any] = {"result": None, "fetched_monotonic": 0.0, "limit": None}

# Scoring weights. Higher wins. These are deliberately spread out so that a
# single strong signal cannot be out-voted by an accumulation of weak ones.
W_LEARNED_CONFIDENT = 1000.0   # >= LEARN_CONFIDENT_AT corrections
W_LEARNED_WEAK = 55.0          # per correction, below the confidence bar
W_SENDER_EXACT = 80.0          # configured sender match
W_DOMAIN = 50.0                # configured domain match
W_KEYWORD_SUBJECT = 14.0       # keyword hit in subject
W_KEYWORD_SNIPPET = 6.0        # keyword hit in snippet/body
W_KEYWORD_SENDER = 8.0         # keyword hit in sender name/address
P_BULK = 22.0                  # penalty applied to bulk/automated mail

# Signals that a message is bulk/automated rather than a real human ping.
_BULK_SENDER_HINTS = (
    "noreply", "no-reply", "donotreply", "do-not-reply", "notifications",
    "notification", "mailer", "bounce", "newsletter", "updates", "digest",
    "marketing", "campaign", "info@", "hello@", "team@", "support@",
)
_BULK_LABEL_HINTS = ("CATEGORY_PROMOTIONS", "CATEGORY_UPDATES", "CATEGORY_SOCIAL", "CATEGORY_FORUMS")


def _ensure_dir() -> None:
    try:
        os.makedirs(ce.MESSAGES_DIR, exist_ok=True)
    except OSError as exc:
        log.warning("message_triage: could not create %s: %s", ce.MESSAGES_DIR, exc)


def _load_signals() -> Dict[str, Any]:
    """Load learned sender signals. Never raises."""
    try:
        with open(SIGNALS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("senders"), dict):
            return data
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("message_triage: unreadable signals file, starting fresh: %s", exc)
    return {"version": 1, "senders": {}}


def _save_signals(data: Dict[str, Any]) -> bool:
    """Atomically persist learned signals. Returns success."""
    _ensure_dir()
    tmp = SIGNALS_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, SIGNALS_FILE)
        return True
    except OSError as exc:
        log.error("message_triage: failed to save signals: %s", exc)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


# --------------------------------------------------------------------------
# Lane helpers — read dynamically, never hardcoded
# --------------------------------------------------------------------------

def lanes() -> List[Dict[str, Any]]:
    """Canonical lane definitions (id/label/color/actionable) from the engine."""
    defs = getattr(ce, "MESSAGE_LANES", None) or []
    out: List[Dict[str, Any]] = []
    for lane in defs:
        if isinstance(lane, dict) and lane.get("id"):
            out.append(dict(lane))
        elif isinstance(lane, str):
            out.append({"id": lane, "label": lane.title(), "actionable": False})
    return out


def lane_ids() -> List[str]:
    ids = getattr(ce, "MESSAGE_LANE_IDS", None)
    if isinstance(ids, (list, tuple)) and ids:
        return [str(i) for i in ids]
    return [l["id"] for l in lanes()]


def _fallback_lane() -> str:
    """The lane used when nothing scores. Prefers a non-actionable lane."""
    explicit = getattr(ce, "DEFAULT_MESSAGE_LANE", None)
    if isinstance(explicit, str) and explicit:
        return explicit
    all_lanes = lanes()
    for lane in reversed(all_lanes):
        if not lane.get("actionable"):
            return lane["id"]
    return all_lanes[-1]["id"] if all_lanes else "other"


# --------------------------------------------------------------------------
# Normalisation helpers
# --------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[\w\.\-\+']+@[\w\.\-]+\.\w+")


def _email_of(msg: Dict[str, Any]) -> str:
    raw = (msg.get("sender_email") or msg.get("sender") or "").strip().lower()
    match = _EMAIL_RE.search(raw)
    return match.group(0) if match else raw


def _domain_of(msg: Dict[str, Any]) -> str:
    email = _email_of(msg)
    return email.rsplit("@", 1)[-1] if "@" in email else ""


def _is_bulk(msg: Dict[str, Any]) -> bool:
    blob = f"{msg.get('sender', '')} {_email_of(msg)}".lower()
    if any(hint in blob for hint in _BULK_SENDER_HINTS):
        return True
    labels = msg.get("labels") or []
    if isinstance(labels, (list, tuple)):
        upper = {str(l).upper() for l in labels}
        if upper & set(_BULK_LABEL_HINTS):
            return True
    return False


def _parse_ts(value: Any) -> Optional[datetime]:
    """Best-effort timestamp parse. Returns tz-aware UTC or None."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000.0 if value > 1e11 else float(value)
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if text.isdigit():
        return _parse_ts(int(text))
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        pass
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text[:31], fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------
# Classification — scored, not first-match
# --------------------------------------------------------------------------

def classify(
    msg: Dict[str, Any],
    rules: Optional[Dict[str, Any]] = None,
    signals: Optional[Dict[str, Any]] = None,
) -> Tuple[str, float, List[str]]:
    """
    Score every lane and return ``(lane_id, confidence, reasons)``.

    Unlike ``calendar_engine._classify_message`` this does not return on the
    first hit: all lanes are scored and the best wins, so lane ordering no
    longer silently decides ties. ``confidence`` is 0.0-1.0, derived from the
    margin between the winner and runner-up — a narrow win reports low
    confidence, which the UI can surface instead of pretending certainty.
    """
    if rules is None:
        rules = ce._load_message_rules() or {}
    if signals is None:
        signals = _load_signals()

    lane_rules = rules.get("lanes") or []
    email = _email_of(msg)
    domain = _domain_of(msg)
    subject = str(msg.get("subject") or "").lower()
    snippet = str(msg.get("snippet") or "").lower()
    sender_blob = f"{msg.get('sender', '')} {email}".lower()
    bulk = _is_bulk(msg)

    scores: Dict[str, float] = {}
    reasons: Dict[str, List[str]] = {}

    def add(lane_id: str, points: float, why: str) -> None:
        if not lane_id:
            return
        scores[lane_id] = scores.get(lane_id, 0.0) + points
        reasons.setdefault(lane_id, []).append(why)

    # --- 1. Learned sender priors (highest authority, per user's decision) ---
    prior = (signals.get("senders") or {}).get(email) if email else None
    if isinstance(prior, dict):
        counts = prior.get("lanes") or {}
        for lane_id, count in counts.items():
            try:
                count = int(count)
            except (TypeError, ValueError):
                continue
            if count <= 0:
                continue
            if count >= LEARN_CONFIDENT_AT:
                add(lane_id, W_LEARNED_CONFIDENT, f"learned: you filed this sender here {count}x")
            else:
                add(lane_id, W_LEARNED_WEAK * count, f"learned: {count} correction(s) for this sender")

    # --- 2. Configured rules ---
    for lane in lane_rules:
        if not isinstance(lane, dict):
            continue
        lane_id = lane.get("id")
        if not lane_id:
            continue

        for sender in lane.get("senders") or []:
            s = str(sender).strip().lower()
            if s and s in sender_blob:
                add(lane_id, W_SENDER_EXACT, "matched a configured sender rule")
                break

        for dom in lane.get("domains") or []:
            d = str(dom).strip().lower().lstrip("@")
            if d and (domain == d or domain.endswith("." + d)):
                add(lane_id, W_DOMAIN, "matched a configured domain rule")
                break

        kw_hits = 0
        for kw in lane.get("keywords") or []:
            k = str(kw).strip().lower()
            if not k:
                continue
            if k in subject:
                add(lane_id, W_KEYWORD_SUBJECT, "keyword in subject")
                kw_hits += 1
            elif k in snippet:
                add(lane_id, W_KEYWORD_SNIPPET, "keyword in body")
                kw_hits += 1
            elif k in sender_blob:
                add(lane_id, W_KEYWORD_SENDER, "keyword in sender")
                kw_hits += 1
            if kw_hits >= 3:  # diminishing returns; avoid keyword stuffing
                break

    # --- 3. Bulk penalty: automated mail should not win an actionable lane
    #        on keyword coincidence alone. Never penalises a learned prior. ---
    if bulk:
        actionable = {l["id"] for l in lanes() if l.get("actionable")}
        for lane_id in list(scores):
            if lane_id in actionable and scores[lane_id] < W_LEARNED_CONFIDENT:
                scores[lane_id] -= P_BULK
                reasons.setdefault(lane_id, []).append("penalised: looks like bulk/automated mail")

    positive = {k: v for k, v in scores.items() if v > 0}
    if not positive:
        return _fallback_lane(), 0.0, ["no rule or learned signal matched"]

    ranked = sorted(positive.items(), key=lambda kv: kv[1], reverse=True)
    best_id, best_score = ranked[0]
    runner = ranked[1][1] if len(ranked) > 1 else 0.0

    # Confidence: how decisively the winner beat the field.
    margin = (best_score - runner) / best_score if best_score else 0.0
    strength = min(best_score / W_SENDER_EXACT, 1.0)
    confidence = round(max(0.0, min(1.0, 0.45 * strength + 0.55 * margin)), 3)

    return best_id, confidence, reasons.get(best_id, [])


# --------------------------------------------------------------------------
# Learning
# --------------------------------------------------------------------------

def invalidate_cache() -> None:
    """Force the next collect() to re-fetch instead of serving cache.

    Called by record_signal()/forget_sender() so a correction shows up
    immediately instead of waiting out the TTL.
    """
    with _collect_cache_lock:
        _collect_cache["result"] = None
        _collect_cache["fetched_monotonic"] = 0.0


def record_signal(sender_email: str, lane_id: str, weight: int = 1) -> Dict[str, Any]:
    """
    Learn that mail from ``sender_email`` belongs in ``lane_id``.

    Learns per SENDER, not per message id, so correcting one newsletter fixes
    every future newsletter from that sender. Returns the updated record.
    """
    email = (sender_email or "").strip().lower()
    match = _EMAIL_RE.search(email)
    if match:
        email = match.group(0)
    if not email or not lane_id:
        return {"ok": False, "error": "sender_email and lane_id are required"}
    if lane_id not in lane_ids():
        return {"ok": False, "error": f"unknown lane: {lane_id}"}

    with _SIGNAL_LOCK:
        data = _load_signals()
        senders = data.setdefault("senders", {})
        record = senders.setdefault(email, {"lanes": {}, "updated": None})
        record.setdefault("lanes", {})
        record["lanes"][lane_id] = int(record["lanes"].get(lane_id, 0)) + int(weight)
        record["updated"] = datetime.now(timezone.utc).isoformat()
        ok = _save_signals(data)
    if ok:
        invalidate_cache()

    count = record["lanes"][lane_id]
    return {
        "ok": ok,
        "sender": email,
        "lane": lane_id,
        "count": count,
        "authoritative": count >= LEARN_CONFIDENT_AT,
        "needed_for_authority": max(0, LEARN_CONFIDENT_AT - count),
    }


def forget_sender(sender_email: str, lane_id: Optional[str] = None) -> Dict[str, Any]:
    """Undo learning for a sender (one lane, or all of them). The off switch."""
    invalidate_cache()
    email = (sender_email or "").strip().lower()
    match = _EMAIL_RE.search(email)
    if match:
        email = match.group(0)
    with _SIGNAL_LOCK:
        data = _load_signals()
        senders = data.get("senders") or {}
        if email not in senders:
            return {"ok": True, "sender": email, "removed": False, "note": "nothing learned"}
        if lane_id:
            senders[email].get("lanes", {}).pop(lane_id, None)
            if not senders[email].get("lanes"):
                senders.pop(email, None)
        else:
            senders.pop(email, None)
        ok = _save_signals(data)
    return {"ok": ok, "sender": email, "removed": True, "lane": lane_id}


def learned_senders() -> List[Dict[str, Any]]:
    """Everything the triage engine has learned — so the user can audit it."""
    data = _load_signals()
    out = []
    for email, rec in (data.get("senders") or {}).items():
        counts = rec.get("lanes") or {}
        if not counts:
            continue
        top_lane, top_count = max(counts.items(), key=lambda kv: kv[1])
        out.append({
            "sender": email,
            "lane": top_lane,
            "count": top_count,
            "authoritative": top_count >= LEARN_CONFIDENT_AT,
            "all_lanes": counts,
            "updated": rec.get("updated"),
        })
    return sorted(out, key=lambda r: r["count"], reverse=True)


# --------------------------------------------------------------------------
# Collection — multi-account
# --------------------------------------------------------------------------

def collect(limit_per_account: int = 25, use_cache_on_error: bool = True) -> Dict[str, Any]:
    """
    Fetch messages across ALL connected Google accounts.

    Returns::

        {
          "messages": [card, ...],   # newest first, account-stamped
          "accounts": [{id, label, color, email_masked, count, unread}, ...],
          "errors":   [{account_id, label, error}, ...],
          "source":   "merged" | "legacy" | "cache",
          "fetched_at": iso8601,
        }

    Degrades honestly: merged fan-out first, then the legacy single-account
    path, then cache. ``source`` always says which one produced the result, so
    the UI can tell the user it is showing stale data instead of pretending.
    """
    now = time.monotonic()
    with _collect_cache_lock:
        cached_entry = _collect_cache.get("result")
        cached_age = now - float(_collect_cache.get("fetched_monotonic") or 0.0)
        cached_limit = _collect_cache.get("limit")
    if (
        cached_entry is not None
        and cached_limit == limit_per_account
        and cached_age < _COLLECT_CACHE_TTL_SECONDS
    ):
        stale_copy = dict(cached_entry)
        stale_copy["source"] = f"{cached_entry.get('source', 'merged')}-cached"
        stale_copy["cache_age_seconds"] = round(cached_age, 1)
        return stale_copy

    rules = ce._load_message_rules() or {}
    state = ce._load_message_state() or {}
    signals = _load_signals()
    errors: List[Dict[str, Any]] = []
    raw_messages: List[Dict[str, Any]] = []
    source = "merged"

    account_meta = _account_meta()

    try:
        from agent_friday.services import google_accounts as ga
        merged = ga.merged_gmail(limit_per_account=limit_per_account) or {}
        raw_messages = list(merged.get("messages") or [])
        for err in merged.get("errors") or []:
            if isinstance(err, dict):
                errors.append({
                    "account_id": err.get("account_id"),
                    "label": err.get("label"),
                    "error": str(err.get("error") or "unknown error"),
                })
    except Exception as exc:
        log.warning("message_triage: merged fan-out failed (%s), trying legacy", exc)
        errors.append({"account_id": None, "label": "merged fetch", "error": str(exc)})
        raw_messages = []

    # Fallback 1: legacy single-account path.
    if not raw_messages:
        try:
            legacy = ce._collect_messages(limit=limit_per_account * 2) or []
            if legacy:
                raw_messages = list(legacy)
                source = "legacy"
        except Exception as exc:
            log.warning("message_triage: legacy collect failed: %s", exc)
            errors.append({"account_id": None, "label": "legacy fetch", "error": str(exc)})

    # Fallback 2: cache.
    if not raw_messages and use_cache_on_error:
        try:
            cached = ce._load_cached_messages() or []
            if cached:
                raw_messages = list(cached)
                source = "cache"
        except Exception as exc:
            log.warning("message_triage: cache read failed: %s", exc)

    cards: List[Dict[str, Any]] = []
    for raw in raw_messages:
        try:
            card = _build_card(raw, rules, state, signals, account_meta)
            if card:
                cards.append(card)
        except Exception as exc:
            log.debug("message_triage: skipped a malformed message: %s", exc)

    cards.sort(key=lambda c: c.get("_sort_ts") or 0, reverse=True)
    for card in cards:
        card.pop("_sort_ts", None)

    result = {
        "messages": cards,
        "accounts": _summarise_accounts(cards, account_meta),
        "errors": errors,
        "source": source,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    if source == "merged" and not errors:
        with _collect_cache_lock:
            _collect_cache["result"] = result
            _collect_cache["fetched_monotonic"] = time.monotonic()
            _collect_cache["limit"] = limit_per_account
    return result


def _account_meta() -> Dict[str, Dict[str, Any]]:
    """Map account_id -> {label, color, email_masked}. Email is masked."""
    meta: Dict[str, Dict[str, Any]] = {}
    try:
        from agent_friday.services import google_accounts as ga
        for acct in ga.list_accounts() or []:
            if not isinstance(acct, dict):
                continue
            aid = acct.get("id")
            if not aid:
                continue
            meta[aid] = {
                "id": aid,
                "label": acct.get("label") or "Account",
                "color": acct.get("color") or "#7c8aa5",
                "email_masked": _mask_email(acct.get("email") or ""),
                "status": acct.get("status"),
            }
    except Exception as exc:
        log.debug("message_triage: account metadata unavailable: %s", exc)
    return meta


def _mask_email(email: str) -> str:
    """user@example.com -> u••r@example.com. Enough to tell accounts apart."""
    email = (email or "").strip()
    if "@" not in email:
        return email
    local, _, domain = email.partition("@")
    if len(local) <= 2:
        return f"{local[:1]}•@{domain}"
    return f"{local[0]}{'•' * min(len(local) - 2, 4)}{local[-1]}@{domain}"


def _build_card(
    raw: Dict[str, Any],
    rules: Dict[str, Any],
    state: Dict[str, Any],
    signals: Dict[str, Any],
    account_meta: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Normalise one raw message into an account-aware, scored card."""
    if not isinstance(raw, dict):
        return None

    # Reuse the engine's normaliser when possible so the card model stays in
    # sync with the rest of the app; fall back to a local build if it errors.
    card: Dict[str, Any]
    try:
        card = dict(ce._normalize_message(raw, rules, state) or {})
    except Exception:
        card = {}

    if not card:
        try:
            mid = ce._message_id(raw)
        except Exception:
            mid = raw.get("id") or raw.get("thread_id") or ""
        if not mid:
            return None
        sender = str(raw.get("sender") or "")
        card = {
            "id": mid,
            "thread_id": raw.get("thread_id"),
            "sender": sender,
            "sender_email": _email_of(raw),
            "subject": raw.get("subject") or "(no subject)",
            "snippet": raw.get("snippet") or "",
            "timestamp": raw.get("timestamp"),
            "labels": raw.get("labels") or [],
            "unread": bool(raw.get("unread")),
            "flagged": False,
            "archived": False,
            "has_attachment": False,
            "snoozed_until": None,
            "initial": (sender.strip()[:1] or "?").upper(),
        }

    # --- account stamping -------------------------------------------------
    # merged_gmail stamps each message with account_id and label; older or
    # legacy paths may not, so every key is probed defensively.
    aid = raw.get("account_id") or card.get("account_id")
    meta = account_meta.get(aid or "", {})
    card["account_id"] = aid
    card["account_label"] = raw.get("label") or raw.get("account_label") or meta.get("label") or "Unknown account"
    card["account_color"] = raw.get("account_color") or meta.get("color") or "#7c8aa5"
    card["account_email"] = meta.get("email_masked") or ""

    # --- scored classification -------------------------------------------
    lane, confidence, why = classify(card, rules=rules, signals=signals)
    override = (state.get(card.get("id"), {}) or {}).get("lane_override")
    if override and override in lane_ids():
        card["lane"] = override
        card["lane_confidence"] = 1.0
        card["lane_reasons"] = ["you set this lane manually for this message"]
        card["lane_overridden"] = True
    else:
        card["lane"] = lane
        card["lane_confidence"] = confidence
        card["lane_reasons"] = why
        card["lane_overridden"] = False

    card["is_bulk"] = _is_bulk(card)
    dt = _parse_ts(card.get("timestamp"))
    card["_sort_ts"] = dt.timestamp() if dt else 0
    card["age_hours"] = round((time.time() - dt.timestamp()) / 3600.0, 1) if dt else None
    return card


def _summarise_accounts(
    cards: List[Dict[str, Any]],
    account_meta: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for aid, meta in account_meta.items():
        mine = [c for c in cards if c.get("account_id") == aid]
        out.append({
            **meta,
            "count": len(mine),
            "unread": sum(1 for c in mine if c.get("unread")),
            "actionable": sum(1 for c in mine if c.get("lane") in _actionable_lane_ids()),
        })
    unknown = [c for c in cards if c.get("account_id") not in account_meta]
    if unknown:
        out.append({
            "id": None,
            "label": "Unassigned",
            "color": "#7c8aa5",
            "email_masked": "",
            "status": None,
            "count": len(unknown),
            "unread": sum(1 for c in unknown if c.get("unread")),
            "actionable": 0,
        })
    return out


def _actionable_lane_ids() -> set:
    return {l["id"] for l in lanes() if l.get("actionable")}


# --------------------------------------------------------------------------
# Filtering
# --------------------------------------------------------------------------

def apply_filters(
    messages: Iterable[Dict[str, Any]],
    lane: Optional[str] = None,
    account: Optional[str] = None,
    query: Optional[str] = None,
    unread: Optional[bool] = None,
    flagged: Optional[bool] = None,
    has_attachment: Optional[bool] = None,
    include_archived: bool = False,
    include_snoozed: bool = False,
    min_confidence: Optional[float] = None,
    since_hours: Optional[float] = None,
    sender: Optional[str] = None,
    exclude_bulk: bool = False,
    sort: str = "newest",
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    One filter pipeline for the whole messages center.

    Every parameter is optional and ``None`` means "don't filter on this",
    which keeps the API honest: an absent filter never silently drops mail.
    """
    now = time.time()
    out: List[Dict[str, Any]] = []

    q = (query or "").strip().lower()
    sender_q = (sender or "").strip().lower()

    for msg in messages or []:
        if not isinstance(msg, dict):
            continue

        if not include_archived and msg.get("archived"):
            continue

        if not include_snoozed:
            snoozed = _parse_ts(msg.get("snoozed_until"))
            if snoozed and snoozed.timestamp() > now:
                continue

        if lane and lane != "all" and msg.get("lane") != lane:
            continue
        if account and account != "all" and msg.get("account_id") != account:
            continue
        if unread is not None and bool(msg.get("unread")) != unread:
            continue
        if flagged is not None and bool(msg.get("flagged")) != flagged:
            continue
        if has_attachment is not None and bool(msg.get("has_attachment")) != has_attachment:
            continue
        if exclude_bulk:
            # is_bulk is precomputed by _build_card, but apply_filters must
            # also work on raw/unbuilt dicts -- compute on demand rather than
            # failing open and silently letting bulk mail through the filter.
            bulk = msg.get("is_bulk")
            if bulk is None:
                bulk = _is_bulk(msg)
            if bulk:
                continue
        if min_confidence is not None and float(msg.get("lane_confidence") or 0) < min_confidence:
            continue

        if since_hours is not None:
            age = msg.get("age_hours")
            if age is None or age > since_hours:
                continue

        if sender_q:
            blob = f"{msg.get('sender', '')} {msg.get('sender_email', '')}".lower()
            if sender_q not in blob:
                continue

        if q:
            haystack = " ".join(str(msg.get(k) or "") for k in
                                ("subject", "snippet", "sender", "sender_email", "account_label")).lower()
            if not all(term in haystack for term in q.split()):
                continue

        out.append(msg)

    if sort == "oldest":
        out.sort(key=lambda m: _sort_key(m), reverse=False)
    elif sort == "confidence":
        out.sort(key=lambda m: float(m.get("lane_confidence") or 0), reverse=True)
    elif sort == "sender":
        out.sort(key=lambda m: str(m.get("sender") or "").lower())
    else:  # newest
        out.sort(key=lambda m: _sort_key(m), reverse=True)

    if limit is not None and limit > 0:
        out = out[:limit]
    return out


def _sort_key(msg: Dict[str, Any]) -> float:
    dt = _parse_ts(msg.get("timestamp"))
    return dt.timestamp() if dt else 0.0


# --------------------------------------------------------------------------
# Rollups for the UI
# --------------------------------------------------------------------------

def account_summary(messages: Optional[Iterable[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    Per-account and per-lane rollups — the data behind the split view.

    Returns lane totals overall, plus a per-account breakdown so the UI can
    render either one merged stream with account badges, or two columns.
    """
    if messages is None:
        collected = collect()
        messages = collected["messages"]
        errors = collected["errors"]
        source = collected["source"]
    else:
        messages = list(messages)
        errors = []
        source = "provided"

    messages = [m for m in messages if isinstance(m, dict) and not m.get("archived")]
    all_lanes = lanes()
    meta = _account_meta()

    def lane_counts(subset: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [{
            "id": lane["id"],
            "label": lane.get("label") or lane["id"],
            "color": lane.get("color"),
            "actionable": bool(lane.get("actionable")),
            "count": sum(1 for m in subset if m.get("lane") == lane["id"]),
            "unread": sum(1 for m in subset if m.get("lane") == lane["id"] and m.get("unread")),
        } for lane in all_lanes]

    per_account = []
    for aid, info in meta.items():
        subset = [m for m in messages if m.get("account_id") == aid]
        per_account.append({
            **info,
            "total": len(subset),
            "unread": sum(1 for m in subset if m.get("unread")),
            "lanes": lane_counts(subset),
        })

    low_conf = [m for m in messages if float(m.get("lane_confidence") or 0) < 0.35
                and not m.get("lane_overridden")]

    return {
        "total": len(messages),
        "unread": sum(1 for m in messages if m.get("unread")),
        "lanes": lane_counts(messages),
        "per_account": per_account,
        "needs_review": len(low_conf),
        "learned_senders": len(_load_signals().get("senders") or {}),
        "errors": errors,
        "source": source,
    }


def reclassify_all(messages: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Re-score an existing card list in place (after a correction)."""
    rules = ce._load_message_rules() or {}
    signals = _load_signals()
    state = ce._load_message_state() or {}
    out = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        card = dict(msg)
        override = (state.get(card.get("id"), {}) or {}).get("lane_override")
        if override and override in lane_ids():
            card["lane"] = override
            card["lane_confidence"] = 1.0
            card["lane_overridden"] = True
            card["lane_reasons"] = ["you set this lane manually for this message"]
        else:
            lane, conf, why = classify(card, rules=rules, signals=signals)
            card["lane"] = lane
            card["lane_confidence"] = conf
            card["lane_reasons"] = why
            card["lane_overridden"] = False
        out.append(card)
    return out
