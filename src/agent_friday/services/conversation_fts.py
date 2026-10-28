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
  durably in ``forgotten-turns.json`` beside the forget-person tombstones (ids
  only, written atomically) and mirrored into the ``forgotten`` table here, so
  a rebuild from the transcripts never brings them back, even when this index
  (a rebuildable cache) was deleted. A bulk rebuild (``build``, ``resync``, a
  rebuild of the vector memory inside ``rebuilding()``) that cannot read the
  durable list indexes nothing rather than risk a forgotten turn; the live
  single-turn paths fail open (``forgotten_ids``).

Deleting is total and immediate: ``delete_message`` removes the line from the
transcript, this index, Friday's vector memory (ChromaDB) and the chat mirror
in one call; a conversation cleared, pruned or rewritten is re-synced from its
file at once (``resync``).

Provenance travels with each row (``sent_to``, services/conversation_provenance)
so a cloud caller can be given only what that provider already heard.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
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

#: Stands in for a turn forget-person took out wherever a transcript is
#: replayed to a model (routes/chat, voice, Crew): the owner's history still
#: shows it, the model does not read it again, and the roles still alternate.
FORGOTTEN_NOTE = "[This message was removed from Friday's memory at the owner's request.]"

#: The durable forgotten-turn list, beside the forget-person tombstones.
FORGOTTEN_FILE = "forgotten-turns.json"
#: Serialises writers of the durable list only (never held by a reader, and
#: never the index's _LOCK, so a lookup does not wait for a build).
_DURABLE_LOCK = threading.Lock()
_REBUILD = threading.local()


class ForgottenListUnreadable(RuntimeError):
    """The durable forgotten-turn list exists but cannot be read: a bulk
    rebuild refuses rather than index a turn the owner had forgotten."""


def _path() -> Path:
    """Beside the transcripts it indexes (the store's own root)."""
    try:
        from agent_friday.services import conversations
        return conversations._root() / "index.sqlite"
    except Exception:
        return friday_home() / "conversations" / "index.sqlite"


# ── the durable forgotten-turn list ──────────────────────────────────────────
def _durable_path() -> Path:
    """Beside the forget-person tombstones (forgotten-people.json), outside the
    conversations folder whose index is a rebuildable cache."""
    try:
        from agent_friday.services import forget_person as fp
        return fp._tombstone_path().with_name(FORGOTTEN_FILE)
    except Exception:
        return friday_home() / FORGOTTEN_FILE


def _read_durable(strict: bool = False) -> set:
    """The forgotten message ids kept durably. No file: no forget has run (an
    empty set). A file that cannot be read or parsed: with `strict` (the bulk
    rebuild paths) ForgottenListUnreadable; otherwise logged and empty (the
    live paths fail open)."""
    p = _durable_path()
    try:
        if not p.exists():
            return set()
        data = json.loads(p.read_text(encoding="utf-8"))
        ids = data.get("ids") if isinstance(data, dict) else None
        if not isinstance(ids, list):
            raise ValueError("no id list")
        return {str(i) for i in ids if str(i or "").strip()}
    except Exception as e:
        if strict:
            raise ForgottenListUnreadable(
                "the forgotten-turn list could not be read (%s)" % type(e).__name__) from e
        _log.warning("conversation search: the forgotten-turn list could not be read: %s", type(e).__name__)
        return set()


def _add_durable(mids: Iterable[str]) -> None:
    """Add ids to the durable list (atomic: temp file, then os.replace). An
    existing list that cannot be read is not overwritten: raises, so the
    forget says it could not be kept (and running it again retries)."""
    new = {str(m) for m in (mids or ()) if str(m or "").strip()}
    if not new:
        return
    with _DURABLE_LOCK:
        have = _read_durable(strict=True)
        if new <= have:
            return
        p = _durable_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps({"version": 1, "ids": sorted(have | new)}, indent=0), encoding="utf-8")
        os.replace(tmp, p)


def _mirror_durable(conn, gone: set) -> None:
    """Copy the durable ids into this index's `forgotten` table (the mirror
    _insert checks), so a rebuilt index honours every earlier forget."""
    if gone:
        conn.executemany("INSERT OR IGNORE INTO forgotten(mid) VALUES(?)", [(m,) for m in sorted(gone)])


@contextlib.contextmanager
def rebuilding():
    """For a bulk rebuild of the vector memory from the transcripts (M4's
    rebuild_from_transcripts re-indexes each line through
    ConversationMemory.index with its id): inside this block index() skips
    every forgotten id, read ONCE from the durable list and this index; when
    the durable list cannot be read, index() skips every turn (the rebuild
    indexes nothing rather than a turn the owner forgot)."""
    prev = getattr(_REBUILD, "state", None)
    try:
        gone = _read_durable(strict=True) | _mirror_ids(None)
        _REBUILD.state = {"gone": gone, "refused": False}
    except ForgottenListUnreadable:
        _log.error("conversation memory rebuild: the forgotten-turn list cannot be read; nothing is re-indexed")
        _REBUILD.state = {"gone": set(), "refused": True}
    try:
        yield _REBUILD.state
    finally:
        _REBUILD.state = prev


def rebuild_state() -> Optional[Dict[str, Any]]:
    """The current thread's rebuild context ({"gone", "refused"}), or None."""
    return getattr(_REBUILD, "state", None)


def skip_on_rebuild(mid) -> bool:
    """For ConversationMemory.index(..., rebuild=True) outside rebuilding():
    True when `mid` is forgotten, or when the durable list cannot be read
    (a bulk rebuild skips everything rather than risk a forgotten turn)."""
    state = rebuild_state()
    if state is not None:
        return state["refused"] or (bool(mid) and str(mid) in state["gone"])
    try:
        return bool(mid) and str(mid) in (_read_durable(strict=True) | _mirror_ids([str(mid)]))
    except ForgottenListUnreadable:
        _log.error("conversation memory rebuild: the forgotten-turn list cannot be read; turn skipped")
        return True


def _mirror_ids(mids) -> set:
    """_table_forgotten, failing open: the index is a cache; the durable list
    is the record a strict reader relies on."""
    try:
        return _table_forgotten(mids)
    except Exception as e:
        _log.warning("conversation search: the forgotten-turn mirror could not be read: %s", type(e).__name__)
        return set()


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
    # The durable forgotten list first: when it cannot be read, the
    # conversation's old rows still go (a clear must clear) and nothing is
    # indexed from the transcript until a later resync or build can read it.
    try:
        gone, refused = _read_durable(strict=True), False
    except ForgottenListUnreadable as e:
        gone, refused = set(), True
        _log.error("conversation search: %s; conversation %s is not re-indexed", e, cid)
    with _LOCK:
        conn = _connect()
        try:
            _delete_cid(conn, cid)
            n = 0
            if not refused:
                _mirror_durable(conn, gone)
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
    """Index every stored conversation from its transcript.

    Every earlier forget is honoured: the durable forgotten list is read
    (strictly) and mirrored into ``forgotten`` before anything is indexed.
    When that list cannot be read the build is refused before the index is
    touched (ForgottenListUnreadable): the old index stays as it was."""
    from agent_friday.services import conversations as cv
    t0 = time.time()
    n = 0
    with _LOCK:
        conn = _connect()
        try:
            if not force and (conn.execute("SELECT v FROM meta WHERE k='version'").fetchone() or [None])[0] == str(VERSION):
                return {"ok": True, "skipped": True}
            gone = _read_durable(strict=True)          # raises: nothing below runs
            _mirror_durable(conn, gone)
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
    out = _drop_everywhere([(mid, gone["text"] or "")], main=str(cid) == cv.MAIN_ID)
    return {"ok": True, "deleted": mid, "vector_rows": out["vector_rows"]}


def _norm_text(text: Any) -> str:
    return " ".join(str(text or "").split())


def _drop_mirror(pairs, main: bool, whole: bool = False) -> int:
    """The chat mirror (core.CHAT_HISTORY and chat_history.json) lets the
    deleted messages go: by id, and in Main (where the old chat_history.json
    was imported, its id-less rows given new ids) also a row WITHOUT an id
    whose text is a deleted message's (for one message, only a text of 20+
    characters, so another "ok" stays; for all of Main, every one). On Main
    the file is checked too, since the mirror in memory holds only its last
    30 days. Returns errors (0/1)."""
    try:
        from agent_friday import core
        ids = {m for m, _t in pairs if m}
        texts = ({_norm_text(t) for _m, t in pairs if _norm_text(t) and (whole or len(_norm_text(t)) >= 20)}
                 if main else set())

        def _goes(row) -> bool:
            if not isinstance(row, dict):
                return False
            if row.get("id"):
                return str(row.get("id")) in ids
            return bool(texts) and _norm_text(row.get("text")) in texts
        before = len(core.CHAT_HISTORY)
        core.CHAT_HISTORY[:] = [m for m in core.CHAT_HISTORY if not _goes(m)]
        changed = len(core.CHAT_HISTORY) != before
        if main and texts and not changed:
            try:
                on_disk = json.loads(Path(core.CHAT_HISTORY_FILE).read_text(encoding="utf-8"))
                changed = isinstance(on_disk, list) and any(_goes(r) for r in on_disk)
            except (OSError, ValueError):
                pass
        if changed:
            core._save_chat_history(core.CHAT_HISTORY)
        return 0
    except Exception as e:
        _log.warning("conversation search: chat mirror delete failed: %s", type(e).__name__)
        return 1


def _drop_everywhere(pairs, cid: Optional[str] = None, legacy_text: bool = True,
                     main: bool = False) -> Dict[str, Any]:
    """The indexes a deleted message leaves (its transcript line is already
    gone): this index, the vector memory, the chat mirror and the review
    buffers. `pairs` are (message id, text). With `cid` every row this index
    holds for that conversation goes too (a whole conversation deleted).
    `legacy_text`: a vector row stored under a random id before turns carried
    their message id is removed by its exact text (20+ characters), as one
    message's delete always has; a whole conversation's delete passes False,
    since forget_conversation already took its legacy rows by day and text and
    the same words said in another conversation are that conversation's.
    `main`: the messages were Main's (see _drop_mirror).
    Each store is its own step: one that fails is counted in "errors" and
    the others still run.
    Returns {"vector_rows", "errors"} (counts)."""
    pairs = [(str(m or ""), str(t or "")) for m, t in pairs]
    errors = 0
    try:
        with _LOCK:
            conn = _connect()
            try:
                for mid, _t in pairs:
                    if mid:
                        _delete_mid(conn, mid)
                if cid:
                    _delete_cid(conn, cid)
                conn.commit()
            finally:
                conn.close()
    except Exception as e:
        errors += 1
        _log.warning("conversation search: index delete failed: %s", type(e).__name__)
    removed_vec = 0
    try:
        from agent_friday.services.model_router import _get_conversation_memory
        mem = _get_conversation_memory()
        for mid, text in pairs:
            n = int(mem.delete(ids=[mid]) or 0) if mid else 0
            # An older turn was stored under a random id: remove it by its exact
            # text, but only a text long enough not to match other people's "ok".
            if not n and legacy_text and len(" ".join(text.split())) >= 20:
                n = int(mem.delete(exact_text=text) or 0)
            removed_vec += n
    except Exception as e:
        errors += 1
        _log.warning("conversation search: vector memory delete failed: %s", e)
    errors += _drop_mirror(pairs, main, whole=bool(cid))
    try:
        from agent_friday.services import memory_review
        for _m, text in pairs:
            memory_review.forget_text(text)
    except Exception:
        pass
    return {"vector_rows": removed_vec, "errors": errors}


def drop_conversation(cid: str, messages, main: bool = False) -> Dict[str, Any]:
    """A whole conversation's messages leave every index, through the same
    paths one message's delete takes (``_drop_everywhere``). Called by
    conversations.delete BEFORE the transcript goes, so a delete that stops
    part way can be run again with every message id still known."""
    pairs = [(m.get("id"), m.get("text")) for m in (messages or []) if isinstance(m, dict)]
    return _drop_everywhere(pairs, cid=str(cid or ""), legacy_text=False, main=bool(main))


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
    `apply=False` only counts. Returns {"removed", "mids", "dates",
    "durable"}: "durable" is False when the ids could not be added to the
    durable list (forgotten-turns.json), so a rebuild after the index is
    deleted might not know them; the caller says so in the receipt.

    The ids go to the durable list FIRST, then this index's mirror."""
    extra = {str(m) for m in (mids or ()) if str(m or "").strip()}
    if not _path().exists() and not extra:
        return {"removed": 0, "mids": [], "dates": [], "durable": True}
    durable = True
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
                try:
                    _add_durable(indexed | extra)
                except Exception as e:
                    durable = False
                    _log.error("conversation search: the forgotten-turn list could not be saved: %s",
                               type(e).__name__)
                for mid in indexed:
                    _delete_mid(conn, mid)
                conn.executemany("INSERT OR IGNORE INTO forgotten(mid) VALUES(?)",
                                 [(m,) for m in sorted(indexed | extra)])
                conn.commit()
        finally:
            conn.close()
    dates = sorted({datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d") for _m, ts in hits if ts})
    return {"removed": removed, "mids": sorted(indexed), "dates": dates, "durable": durable}


def _table_forgotten(mids) -> set:
    """The ids in this index's `forgotten` mirror (all of them when `mids` is
    None). Read-only and WITHOUT the index's _LOCK: a lookup never waits
    behind a build (SQLite WAL readers do not block on its writer; a short
    busy timeout covers the rest). Raises when it cannot be read."""
    p = _path()
    if not p.exists():
        return set()
    conn = sqlite3.connect(str(p), timeout=1.0)          # queries only; never written here
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='forgotten'").fetchone():
            return set()
        if mids is None:
            return {str(r[0]) for r in conn.execute("SELECT mid FROM forgotten")}
        out = set()
        for i in range(0, len(mids), 500):
            chunk = mids[i:i + 500]
            out |= {str(r[0]) for r in conn.execute(
                "SELECT mid FROM forgotten WHERE mid IN (%s)" % ",".join("?" * len(chunk)), chunk)}
        return out
    finally:
        conn.close()


def forgotten_ids(mids: Iterable[str]) -> set:
    """The ids among `mids` that forget-person took out (``forget_turns``):
    the durable list (forgotten-turns.json) and this index's mirror. For the
    live paths (a turn indexed, a transcript replayed to a model): takes no
    index lock, and fails open, each source on its own: a source that cannot
    be read reports nothing and is logged, and callers then keep (or index)
    the turn as before. Bulk rebuilds read strictly instead (build, resync,
    rebuilding(), skip_on_rebuild)."""
    want = [str(m) for m in (mids or ()) if str(m or "").strip()]
    if not want:
        return set()
    return (_read_durable(strict=False) & set(want)) | _mirror_ids(want)


def is_forgotten(mid) -> bool:
    """True when forget-person took this message id out (ConversationMemory.index
    refuses it, so a rebuild through it cannot bring the turn back)."""
    return bool(mid) and str(mid) in forgotten_ids([str(mid)])


def _snippet(text: str, terms: Iterable[str]) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= SNIPPET_CHARS:
        return text
    low = text.lower()
    at = min((low.find(t) for t in terms if low.find(t) >= 0), default=0)
    start = max(0, at - SNIPPET_CHARS // 3)
    out = text[start:start + SNIPPET_CHARS]
    return ("…" if start else "") + out + ("…" if start + SNIPPET_CHARS < len(text) else "")
