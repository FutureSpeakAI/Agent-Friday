"""Search past conversations (voice and chat) and remember recent voice calls.

Every conversation's turns live in the conversation store
(services/conversations). `search` finds turns matching a query, newest first,
with dates, never returning off-record turns. `recent_voice_pin` is the
compact "what we talked about last time" block a new Gemini Live call starts
with; it is built only from turns that were already spoken to google-gemini
(services/conversation_provenance), so pinning them exposes nothing new.

What a cloud call may hear directly is decided by provenance: turns already
sent to that provider are returned as they are (through the normal egress
gate); every other match reaches the cloud only through the local model and
the payload card (services/local_context).
"""
from __future__ import annotations

import re
import time
from datetime import datetime

from agent_friday.services import conversation_provenance as prov

_STOP = {"the", "and", "for", "with", "that", "this", "what", "when", "where", "who",
         "did", "was", "were", "you", "your", "our", "about", "have", "has", "had",
         "from", "they", "them", "then", "there", "their", "said", "tell", "talk",
         "talked", "last", "time", "remember", "earlier", "before", "any", "some"}


def _terms(query: str) -> list:
    return [w for w in re.findall(r"[a-z0-9']+", str(query or "").lower())
            if len(w) >= 3 and w not in _STOP]


def _day(value) -> float | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").timestamp()
    except ValueError:
        return None


def _iter_messages():
    from agent_friday.services import conversations as cv
    for conv in cv.list_all(include_archived=True):
        cid = conv.get("id") or conv.get("conversation_id")
        if not cid:
            continue
        try:
            msgs = cv.messages(cid)
        except Exception:
            continue
        for m in msgs:
            yield conv, cid, m


def search(query: str, since=None, until=None, limit: int = 8, windows=None) -> list:
    """Matching turns, best and newest first. Off-record turns are never returned."""
    terms = _terms(query)
    if not terms:
        return []
    lo, hi = _day(since), _day(until)
    if hi is not None:
        hi += 86400
    if windows is None:
        windows = prov.gemini_windows()
    hits = []
    for conv, cid, m in _iter_messages():
        text = str(m.get("text") or "")
        meta = m.get("meta") or {}
        if not text or prov.is_off_record(m) or meta.get("kind") not in (None, "turn"):
            continue
        ts = float(m.get("ts") or 0)
        if (lo is not None and ts < lo) or (hi is not None and ts >= hi):
            continue
        low = text.lower()
        score = sum(1 for t in terms if t in low)
        if not score:
            continue
        hits.append({"conversation_id": cid, "title": conv.get("title") or "",
                     "ts": ts, "date": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "",
                     "role": m.get("role"), "via": meta.get("via") or "chat",
                     "text": text[:400], "sent_to": sorted(prov.sent_to(m, windows)),
                     "score": score})
    hits.sort(key=lambda h: (-h["score"], -h["ts"]))
    return hits[:max(1, int(limit))]


def split_by_provenance(hits: list, provider: str = prov.GEMINI):
    """(direct, via_card): what this provider already received, and the rest."""
    direct = [h for h in hits if provider in (h.get("sent_to") or [])]
    rest = [h for h in hits if provider not in (h.get("sent_to") or [])]
    return direct, rest


def format_hits(hits: list) -> str:
    return "\n".join(f"- {h['date']} ({h['via']}, {'him' if h['role'] == 'user' else 'Friday'}): "
                     f"{h['text']}" for h in hits)


def recent_voice_pin(max_sessions: int = 3, max_chars: int = 900, windows=None) -> str:
    """What recent voice calls were about, from turns already spoken to Gemini."""
    if windows is None:
        windows = prov.gemini_windows()
    sessions = {}
    for conv, cid, m in _iter_messages():
        meta = m.get("meta") or {}
        if meta.get("via") != "voice" or prov.is_off_record(m):
            continue
        if prov.GEMINI not in prov.sent_to(m, windows):
            continue
        ts = float(m.get("ts") or 0)
        key = (cid, time.strftime("%Y-%m-%d", time.localtime(ts)))
        s = sessions.setdefault(key, {"ts": ts, "title": conv.get("title") or "", "lines": []})
        s["ts"] = max(s["ts"], ts)
        if m.get("role") == "user" and len(s["lines"]) < 3:
            s["lines"].append(str(m.get("text") or "").strip()[:140])
    if not sessions:
        return ""
    recent = sorted(sessions.values(), key=lambda s: -s["ts"])[:max_sessions]
    out = ["=== RECENT VOICE CONVERSATIONS (from earlier calls with you; search_past_conversations for more) ==="]
    for s in recent:
        when = datetime.fromtimestamp(s["ts"]).strftime("%a %b %d, %I:%M %p")
        said = " / ".join(x for x in s["lines"] if x)
        out.append(f"- {when}: he said: {said}" if said else f"- {when}")
    text = "\n".join(out)
    return text[:max_chars] + "\n"
