"""Full-text search over every conversation: a local SQLite FTS5 index.

``~/.friday/conversations/index.sqlite`` holds one row per stored chat or
voice turn (services/conversations), written as the turn is appended and built
once from the transcripts on first use (reference/hermes_memory_eval_2026-10-14.md
R3). Two FTS5 tables: one with the porter stemmer for words ("walked" finds
"walking"), one with the trigram tokenizer for parts of words and names.
Results are real message snippets with dates; no model summarises them.

What never reaches the index:

* **A key or a password the owner pasted.** Every text is cleaned with
  ``pasted_secrets.scrub`` before it is written (the definition of a key the
  egress gate, the tool-output redactor and the conversation store share). If
  that module cannot be loaded, nothing is indexed: the index fails closed.
* **Off-the-record turns**, and messages that are not conversation turns.
* **Turns taken out by forget-person** (``forget_turns``): their ids are kept
  in ``forgotten`` so a rebuild from the transcripts never brings them back.

Deleting is total and immediate: ``delete_message`` removes the line from the
transcript, this index, Friday's vector memory (ChromaDB) and the chat mirror
in one call; a conversation cleared, pruned or rewritten is re-synced from its
file at once (``resync``).

Provenance travels with each row (``sent_to``, services/conversation_provenance)
so a cloud caller can be given only what that provider already heard.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from agent_friday.paths import friday_home

_log = logging.getLogger("friday.conversation_fts")

#: Bump to rebuild every index from the transcripts (schema or cleaning change).
VERSION = 1
SNIPPET_CHARS = 400

#: The first append builds in the background (tests turn this off).
BACKGROUND_BUILD = True

_LOCK = threading.RLock()
_BUILT: Dict[str, bool] = {}


def _path() -> Path:
    """Beside the transcripts it indexes (the store's own root)."""
    try:
        from agent_friday.services import conversations
        return conversations._root() / "index.sqlite"
    except Exception:
        return friday_home() / "conversations" / "index.sqlite"


def _redactor():
    """The shared key redactor (services/pasted_secrets.scrub)."""
    from agent_friday.services.pasted_secrets import scrub
    return scrub


def _clean(text: str) -> Optional[str]:
    """The text as it may be indexed, or None when it may not be (fail closed)."""
    try:
        scrub = _redactor()
    except Exception:
        _log.error("conversation search: the key redactor is unavailable; nothing is indexed")
        return None
    try:
        from agent_friday.services import memory_fence
        text = memory_fence.strip(text)
    except Exception:
        pass
    out = scrub(text)
    return out if isinstance(out, str) else None


def _connect() -> sqlite3.Connection:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
        CREATE TABLE IF NOT EXISTS msgs(
            id INTEGER PRIMARY KEY, mid TEXT UNIQUE, cid TEXT, ts REAL,
            role TEXT, via TEXT, sent_to TEXT);
        CREATE INDEX IF NOT EXISTS msgs_cid ON msgs(cid);
        CREATE TABLE IF NOT EXISTS superseded(mid TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS forgotten(mid TEXT PRIMARY KEY);
        CREATE VIRTUAL TABLE IF NOT EXISTS msgs_fts USING fts5(text, tokenize='porter unicode61');
        """)
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS msgs_tri USING fts5(text, tokenize='trigram')")
    except sqlite3.OperationalError:
        pass                                   # an old SQLite: words only
    return conn


def _has_trigram(conn) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='msgs_tri'").fetchone())


def _indexable(m: dict) -> bool:
    meta = m.get("meta") or {}
    if not isinstance(m.get("text"), str) or not m["text"].strip() or not m.get("id"):
        return False
    if m.get("off_record") or meta.get("off_record"):
        return False
    return meta.get("kind") in (None, "turn")


def _insert(conn, cid: str, m: dict) -> bool:
    if not _indexable(m):
        return False
    # A turn taken out by forget-person stays out: a rebuild or a resync from
    # the transcript (which keeps it) never indexes it again (forget_turns).
    if conn.execute("SELECT 1 FROM forgotten WHERE mid=?", (str(m["id"]),)).fetchone():
        return False
    text = _clean(m["text"])
    if text is None or not text.strip():
        return False
    try:
        from agent_friday.services import conversation_provenance as prov
        sent = sorted(prov.sent_to(m))
    except Exception:
        sent = []
    meta = m.get("meta") or {}
    _delete_mid(conn, str(m["id"]))
    cur = conn.execute(
        "INSERT INTO msgs(mid, cid, ts, role, via, sent_to) VALUES(?,?,?,?,?,?)",
        (str(m["id"]), cid, float(m.get("ts") or 0), str(m.get("role") or ""),
         str(meta.get("via") or m.get("via") or "chat"), json.dumps(sent)))
    rid = cur.lastrowid
    conn.execute("INSERT INTO msgs_fts(rowid, text) VALUES(?,?)", (rid, text))
    if _has_trigram(conn):
        conn.execute("INSERT INTO msgs_tri(rowid, text) VALUES(?,?)", (rid, text))
    return True


def _delete_mid(conn, mid: str) -> int:
    row = conn.execute("SELECT id FROM msgs WHERE mid=?", (mid,)).fetchone()
    if not row:
        return 0
    conn.execute("DELETE FROM msgs_fts WHERE rowid=?", (row[0],))
    if _has_trigram(conn):
        conn.execute("DELETE FROM msgs_tri WHERE rowid=?", (row[0],))
    conn.execute("DELETE FROM msgs WHERE id=?", (row[0],))
    return 1


def _delete_cid(conn, cid: str) -> None:
    ids = [r[0] for r in conn.execute("SELECT id FROM msgs WHERE cid=?", (cid,))]
    for i in ids:
        conn.execute("DELETE FROM msgs_fts WHERE rowid=?", (i,))
        if _has_trigram(conn):
            conn.execute("DELETE FROM msgs_tri WHERE rowid=?", (i,))
    conn.execute("DELETE FROM msgs WHERE cid=?", (cid,))


# ── writing ───────────────────────────────────────────────────────────────────
def add(cid: str, message: dict) -> bool:
    """Index one appended message (called by conversations.append). The first
    append of a run starts the one-time build in the background, so no turn
    waits for it."""
    try:
        ensure_built(background=True)
        with _LOCK:
            conn = _connect()
            try:
                ok = _insert(conn, cid, message)
                conn.commit()
            finally:
                conn.close()
        return ok
    except Exception as e:
        _log.warning("conversation search: could not index a message: %s", e)
        return False


def resync(cid: str) -> int:
    """Make the index match one conversation's transcript, now (after a clear,
    a prune or a rewrite). Returns rows indexed."""
    from agent_friday.services import conversations as cv
    with _LOCK:
        conn = _connect()
        try:
            _delete_cid(conn, cid)
            n = sum(1 for m in cv._stored_messages(cid) if _insert(conn, cid, m))
            conn.commit()
        finally:
            conn.close()
    return n


def forget_conversation(cid: str) -> None:
    with _LOCK:
        conn = _connect()
        try:
            _delete_cid(conn, cid)
            conn.commit()
        finally:
            conn.close()


def build(force: bool = False) -> Dict[str, Any]:
    """Index every stored conversation from its transcript."""
    from agent_friday.services import conversations as cv
    t0 = time.time()
    n = 0
    with _LOCK:
        conn = _connect()
        try:
            if not force and (conn.execute("SELECT v FROM meta WHERE k='version'").fetchone() or [None])[0] == str(VERSION):
                return {"ok": True, "skipped": True}
            conn.execute("DELETE FROM msgs")
            conn.execute("DELETE FROM msgs_fts")
            if _has_trigram(conn):
                conn.execute("DELETE FROM msgs_tri")
            for conv in cv.list_all(include_archived=True):
                cid = conv.get("id")
                if not cid:
                    continue
                for m in cv._stored_messages(cid):
                    n += int(_insert(conn, cid, m))
            conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('version', ?)", (str(VERSION),))
            conn.commit()
        finally:
            conn.close()
    _log.info("conversation search: indexed %d messages in %.1fs", n, time.time() - t0)
    return {"ok": True, "indexed": n}


def ensure_built(background: bool = False) -> None:
    """Build the index once per Friday home and run. In the background, the
    caller goes on at once; a search waits for it."""
    key = str(_path())
    if _BUILT.get(key):
        return
    _BUILT[key] = True
    if background and BACKGROUND_BUILD:
        threading.Thread(target=_build_quietly, name="conversation-fts-build", daemon=True).start()
    else:
        _build_quietly()


def _build_quietly() -> None:
    try:
        build()
    except Exception as e:
        _BUILT.pop(str(_path()), None)
        _log.warning("conversation search: build failed: %s", e)


# ── deleting one message everywhere ───────────────────────────────────────────
def delete_message(cid: str, mid: str) -> Dict[str, Any]:
    """Remove one message from the transcript, this index, the vector memory
    and the chat mirror, at once."""
    from agent_friday.services import conversations as cv
    mid = str(mid or "").strip()
    if not mid:
        return {"ok": False, "error": "which message?"}
    gone = {"text": None}

    def _drop(m):
        if str(m.get("id")) == mid:
            gone["text"] = m.get("text")
            return {"__deleted__": True}
        return m
    changed = cv.delete_lines(cid, lambda m: str(m.get("id")) == mid, on_match=_drop)
    if not changed:
        return {"ok": False, "error": "that message is not in this conversation"}
    with _LOCK:
        conn = _connect()
        try:
            _delete_mid(conn, mid)
            conn.commit()
        finally:
            conn.close()
    removed_vec = 0
    try:
        from agent_friday.services.model_router import _get_conversation_memory
        mem = _get_conversation_memory()
        removed_vec = mem.delete(ids=[mid])
        text = gone["text"] or ""
        # An older turn was stored under a random id: remove it by its exact
        # text, but only a text long enough not to match other people's "ok".
        if not removed_vec and len(" ".join(text.split())) >= 20:
            removed_vec = mem.delete(exact_text=text)
    except Exception as e:
        _log.warning("conversation search: vector memory delete failed: %s", e)
    try:
        from agent_friday.core import CHAT_HISTORY, _save_chat_history
        before = len(CHAT_HISTORY)
        CHAT_HISTORY[:] = [m for m in CHAT_HISTORY if str(m.get("id")) != mid]
        if len(CHAT_HISTORY) != before:
            _save_chat_history(CHAT_HISTORY)
    except Exception:
        pass
    try:
        from agent_friday.services import memory_review
        memory_review.forget_text(gone["text"] or "")
    except Exception:
        pass
    return {"ok": True, "deleted": mid, "vector_rows": removed_vec}


# ── searching ─────────────────────────────────────────────────────────────────
_STOP = {"the", "and", "for", "with", "that", "this", "what", "when", "where", "who",
         "did", "was", "were", "you", "your", "our", "about", "have", "has", "had",
         "from", "they", "them", "then", "there", "their", "said", "tell", "talk",
         "talked", "last", "time", "remember", "earlier", "before", "any", "some"}


def _terms(query: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9']+", str(query or "").lower())
            if len(w) >= 3 and w not in _STOP][:12]


def _day(value) -> Optional[float]:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").timestamp()
    except ValueError:
        return None


def search(query: str, since=None, until=None, limit: int = 8) -> List[Dict[str, Any]]:
    """Matching turns, best first (BM25), with snippets and dates."""
    terms = _terms(query)
    if not terms:
        return []
    ensure_built()
    lo, hi = _day(since), _day(until)
    if hi is not None:
        hi += 86400
    words = " OR ".join('"' + t.replace('"', "") + '"' for t in terms)
    rows: Dict[int, tuple] = {}
    with _LOCK:
        conn = _connect()
        try:
            for r in conn.execute(
                    "SELECT rowid, bm25(msgs_fts), text FROM msgs_fts WHERE msgs_fts MATCH ? "
                    "ORDER BY bm25(msgs_fts) LIMIT 200", (words,)):
                rows[r[0]] = (r[1], r[2])
            if _has_trigram(conn):
                tri = " OR ".join('"' + t.replace('"', "") + '"' for t in terms if len(t) >= 3)
                if tri:
                    for r in conn.execute(
                            "SELECT rowid, bm25(msgs_tri), text FROM msgs_tri WHERE msgs_tri MATCH ? "
                            "ORDER BY bm25(msgs_tri) LIMIT 200", (tri,)):
                        if r[0] not in rows:
                            rows[r[0]] = (r[1] + 1.0, r[2])      # partial matches rank below words
            meta = {}
            if rows:
                marks = ",".join("?" * len(rows))
                # A turn superseded in memory is not returned as evidence
                # (§34.9-4); it stays in the transcript, flagged there.
                for r in conn.execute(f"SELECT id, mid, cid, ts, role, via, sent_to FROM msgs "
                                      f"WHERE id IN ({marks}) "
                                      f"AND mid NOT IN (SELECT mid FROM superseded)", list(rows)):
                    meta[r[0]] = r
        finally:
            conn.close()
    try:
        from agent_friday.services import conversations as cv
        titles = {c.get("id"): c.get("title") or "" for c in cv.list_all(include_archived=True)}
    except Exception:
        titles = {}
    hits = []
    for rid, (rank, text) in rows.items():
        m = meta.get(rid)
        if not m:
            continue
        _id, mid, cid, ts, role, via, sent = m
        if (lo is not None and ts < lo) or (hi is not None and ts >= hi):
            continue
        hits.append({"message_id": mid, "conversation_id": cid, "title": titles.get(cid, ""),
                     "ts": ts, "date": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "",
                     "role": role, "via": via or "chat", "text": _snippet(text, terms),
                     "sent_to": json.loads(sent or "[]"), "score": -float(rank)})
    hits.sort(key=lambda h: (-h["score"], -h["ts"]))
    return hits[:max(1, int(limit))]


def mark_superseded(mids, on: bool = True) -> int:
    """Record (or clear) that these message ids are superseded, so search
    leaves them out. Survives a rebuild of the index. Returns how many."""
    mids = [str(m) for m in (mids or []) if str(m).strip()]
    if not mids:
        return 0
    with _LOCK:
        conn = _connect()
        try:
            if on:
                conn.executemany("INSERT OR IGNORE INTO superseded(mid) VALUES(?)", [(m,) for m in mids])
            else:
                conn.executemany("DELETE FROM superseded WHERE mid = ?", [(m,) for m in mids])
            conn.commit()
        finally:
            conn.close()
    return len(mids)


def forget_turns(match, mids: Iterable[str] = (), apply: bool = True) -> Dict[str, Any]:
    """Forget-person (services/forget_derived, NS-15.7-2): the indexed turns
    whose text `match` accepts, and the transcript message ids `mids`, leave
    this index and stay out of it (the `forgotten` table survives a rebuild,
    like `superseded`). The transcript itself is not touched. With
    `apply=False` only counts. Returns {"removed", "mids", "dates"}."""
    extra = {str(m) for m in (mids or ()) if str(m or "").strip()}
    if not _path().exists() and not extra:
        return {"removed": 0, "mids": [], "dates": []}
    with _LOCK:
        conn = _connect()
        try:
            hits = []
            for mid, ts, text in conn.execute(
                    "SELECT m.mid, m.ts, f.text FROM msgs m JOIN msgs_fts f ON f.rowid = m.id"):
                if text and match(text):
                    hits.append((str(mid), ts))
            indexed = {h[0] for h in hits}
            for mid in extra:
                row = conn.execute("SELECT ts FROM msgs WHERE mid=?", (mid,)).fetchone()
                if row and mid not in indexed:
                    hits.append((mid, row[0]))
                    indexed.add(mid)
            removed = len(indexed)
            if apply:
                for mid in indexed:
                    _delete_mid(conn, mid)
                conn.executemany("INSERT OR IGNORE INTO forgotten(mid) VALUES(?)",
                                 [(m,) for m in sorted(indexed | extra)])
                conn.commit()
        finally:
            conn.close()
    dates = sorted({datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d") for _m, ts in hits if ts})
    return {"removed": removed, "mids": sorted(indexed), "dates": dates}


def _snippet(text: str, terms: Iterable[str]) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= SNIPPET_CHARS:
        return text
    low = text.lower()
    at = min((low.find(t) for t in terms if low.find(t) >= 0), default=0)
    start = max(0, at - SNIPPET_CHARS // 3)
    out = text[start:start + SNIPPET_CHARS]
    return ("…" if start else "") + out + ("…" if start + SNIPPET_CHARS < len(text) else "")
