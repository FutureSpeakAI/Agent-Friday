"""Persistent conversation memory for Agent Friday — ChromaDB-backed RAG over
every chat turn Friday has ever had.

This is the long-horizon memory layer. It is distinct from, and complementary
to, the two existing in-context systems:

  • context_pruner.py     — picks WHICH turns of the *current* session survive
                            into the API call (RAG over the live transcript).
  • cognitive_memory.py   — episodic/working-memory consolidation.

ConversationMemory persists ACROSS sessions and process restarts. Every user
and assistant message is embedded (all-MiniLM-L6-v2 — the same model the
context pruner uses, so no second model download) and stored in a ChromaDB
collection on disk at ``~/.friday/memory/conversations/``. Later turns — even
weeks later — can retrieve semantically relevant past exchanges and cite them
inline (``[conversation:2026-06-05:"exact quote"]``).

Design rules, consistent with the rest of the codebase:
  • Degrade gracefully. If ``chromadb`` is not installed (or fails to init on
    this platform), every method becomes a safe no-op and ``available()``
    returns False. A chat must NEVER fail because memory is unavailable.
  • Lazy. The client, collection, and embedding model are built on first use,
    never at import time — importing this module is free.
  • Best-effort writes. Indexing happens off the hot path (the server calls it
    from a daemon thread) and swallows its own errors.
"""

from __future__ import annotations

import os
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

# ── Storage location ──────────────────────────────────────────────────
# Mirrors server.py's FRIDAY_DIR = ~/.friday. Kept independent so this module
# has no import dependency on server.py (avoids a circular import).
HOME = Path(os.path.expanduser("~"))
FRIDAY_DIR = HOME / ".friday"
DEFAULT_PERSIST_DIR = FRIDAY_DIR / "memory" / "conversations"

EMBED_MODEL = "all-MiniLM-L6-v2"
COLLECTION_NAME = "conversations"

# Lightweight English stopword set for topic-keyword extraction. Not exhaustive
# — just enough to keep the keyword tags meaningful.
_STOPWORDS = frozenset("""
a about above after again against all am an and any are aren't as at be because
been before being below between both but by can't cannot could couldn't did
didn't do does doesn't doing don't down during each few for from further had
hadn't has hasn't have haven't having he he'd he'll he's her here here's hers
herself him himself his how how's i i'd i'll i'm i've if in into is isn't it
it's its itself let's me more most mustn't my myself no nor not of off on once
only or other ought our ours ourselves out over own same shan't she she'd
she'll she's should shouldn't so some such than that that's the their theirs
them themselves then there there's these they they'd they'll they're they've
this those through to too under until up very was wasn't we we'd we'll we're
we've were weren't what what's when when's where where's which while who who's
whom why why's with won't would wouldn't you you'd you'll you're you've your
yours yourself yourselves just like get got really thing things want need know
think going make made also even still much many one two
""".split())

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9'\-]{2,}")


def extract_keywords(text, limit=8):
    """Cheap keyword extraction — frequency-ranked non-stopword tokens.

    Returns a list of lowercased keywords. Used to tag indexed turns so the
    dossier / stats surfaces can show what a conversation was *about* without a
    model call.
    """
    if not text:
        return []
    counts = {}
    for m in _WORD_RE.findall(text.lower()):
        if m in _STOPWORDS or len(m) < 3:
            continue
        counts[m] = counts.get(m, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [w for w, _ in ranked[:limit]]


class ConversationMemory:
    """Persistent, embedding-backed store of every chat turn.

    Thread-safe for the access pattern the server uses (background-thread
    writes, request-thread reads). All public methods are no-ops when ChromaDB
    is unavailable.
    """

    def __init__(self, persist_dir=None, model_name=EMBED_MODEL):
        self.persist_dir = Path(persist_dir) if persist_dir else DEFAULT_PERSIST_DIR
        self.model_name = model_name
        self._client = None
        self._collection = None
        self._init_attempted = False
        self._init_error = None
        self._lock = threading.Lock()

    # ── lazy initialisation ──────────────────────────────────────────
    def _ensure(self):
        """Build the client + collection on first use. Returns True on success.

        Safe to call repeatedly; the heavy work runs at most once. Any failure
        is remembered so we don't retry (and re-pay the import cost) every turn.
        """
        if self._collection is not None:
            return True
        if self._init_attempted and self._init_error is not None:
            return False
        with self._lock:
            if self._collection is not None:
                return True
            if self._init_attempted and self._init_error is not None:
                return False
            self._init_attempted = True
            try:
                import chromadb
                from chromadb.config import Settings

                self.persist_dir.mkdir(parents=True, exist_ok=True)
                self._client = chromadb.PersistentClient(
                    path=str(self.persist_dir),
                    settings=Settings(anonymized_telemetry=False, allow_reset=False),
                )
                embed_fn = self._build_embedding_function()
                # get_or_create so a restart reuses the on-disk collection.
                self._collection = self._client.get_or_create_collection(
                    name=COLLECTION_NAME,
                    embedding_function=embed_fn,
                    metadata={"hnsw:space": "cosine"},
                )
                return True
            except Exception as e:  # pragma: no cover - platform/env dependent
                self._init_error = e
                self._collection = None
                print(f"  [MEMORY] ChromaDB unavailable — conversation memory disabled: {e}")
                return False

    def _build_embedding_function(self):
        """Reuse all-MiniLM-L6-v2 — the same model context_pruner.py loads.

        Prefer the sentence-transformers backend (already a hard dependency and
        almost certainly already cached on disk from the context pruner), so we
        don't trigger ChromaDB's separate ONNX model download. Fall back to
        ChromaDB's default embedder if that path is unavailable.
        """
        from chromadb.utils import embedding_functions
        try:
            return embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=self.model_name
            )
        except Exception as e:
            print(f"  [MEMORY] sentence-transformers embedder unavailable, "
                  f"using ChromaDB default: {e}")
            return embedding_functions.DefaultEmbeddingFunction()

    def available(self):
        """True when the store is ready (or can be made ready) for use."""
        return self._ensure()

    # ── writes ───────────────────────────────────────────────────────
    def index(self, text, role, session_id="default", timestamp=None,
              topic_keywords=None, msg_id=None):
        """Index a single chat message. Returns the stored id, or None on no-op.

        text           the message body
        role           'user' | 'friday' (assistant) | any string
        session_id     groups turns into a conversation (Friday uses the date)
        timestamp      ISO-8601; defaults to now
        topic_keywords list[str] | comma-string; auto-extracted when omitted
        msg_id         stable id for idempotent upserts; uuid when omitted
        """
        text = (text or "").strip()
        if not text:
            return None
        if not self._ensure():
            return None
        try:
            ts = timestamp or datetime.now().isoformat()
            if topic_keywords is None:
                topic_keywords = extract_keywords(text)
            if isinstance(topic_keywords, (list, tuple)):
                kw_str = ", ".join(str(k) for k in topic_keywords)
            else:
                kw_str = str(topic_keywords or "")
            doc_id = str(msg_id) if msg_id else uuid.uuid4().hex
            # Normalise role to a small, queryable vocabulary.
            role_norm = "friday" if role in ("friday", "assistant") else (
                "user" if role == "user" else str(role or "unknown"))
            metadata = {
                "role": role_norm,
                "timestamp": ts,
                "date": ts[:10],
                "session_id": str(session_id or "default"),
                "topic_keywords": kw_str,
                "char_len": len(text),
            }
            with self._lock:
                # upsert → re-indexing the same msg_id updates rather than dupes.
                self._collection.upsert(
                    ids=[doc_id], documents=[text], metadatas=[metadata])
            return doc_id
        except Exception as e:  # best-effort: never raise into the chat path
            print(f"  [MEMORY] index failed (non-fatal): {e}")
            return None

    def index_exchange(self, user_text, assistant_text, session_id="default",
                       user_msg_id=None, assistant_msg_id=None):
        """Convenience: index a user turn and the assistant reply together.

        Shared topic keywords (extracted from the combined text) are attached to
        both, so a follow-up retrieves the pair as a coherent exchange. Returns
        the list of stored ids (may be shorter than 2 if a side was empty).
        """
        combined = f"{user_text or ''}\n{assistant_text or ''}"
        shared_kw = extract_keywords(combined)
        ids = []
        uid = self.index(user_text, "user", session_id=session_id,
                         topic_keywords=shared_kw, msg_id=user_msg_id)
        if uid:
            ids.append(uid)
        aid = self.index(assistant_text, "friday", session_id=session_id,
                         topic_keywords=shared_kw, msg_id=assistant_msg_id)
        if aid:
            ids.append(aid)
        return ids

    # ── reads ────────────────────────────────────────────────────────
    def search(self, query, n=5, session_id=None, roles=None):
        """Semantic search over past turns.

        Returns a list (most-relevant first) of dicts:
            {text, role, timestamp, date, session_id, topic_keywords,
             distance, relevance}
        where `relevance` is 1 - cosine_distance, clamped to [0, 1].

        session_id  optional filter to a single conversation
        roles       optional iterable of roles to restrict to (e.g. ['friday'])
        """
        query = (query or "").strip()
        if not query or not self._ensure():
            return []
        try:
            where = None
            clauses = []
            if session_id:
                clauses.append({"session_id": str(session_id)})
            if roles:
                role_list = list(roles)
                if len(role_list) == 1:
                    clauses.append({"role": role_list[0]})
                else:
                    clauses.append({"role": {"$in": role_list}})
            if len(clauses) == 1:
                where = clauses[0]
            elif len(clauses) > 1:
                where = {"$and": clauses}

            with self._lock:
                count = self._collection.count()
                if count == 0:
                    return []
                res = self._collection.query(
                    query_texts=[query],
                    n_results=max(1, min(int(n), count)),
                    where=where,
                    include=["documents", "metadatas", "distances"],
                )
            docs = (res.get("documents") or [[]])[0]
            metas = (res.get("metadatas") or [[]])[0]
            dists = (res.get("distances") or [[]])[0]
            out = []
            for doc, meta, dist in zip(docs, metas, dists):
                meta = meta or {}
                try:
                    rel = max(0.0, min(1.0, 1.0 - float(dist)))
                except (TypeError, ValueError):
                    rel = None
                out.append({
                    "text": doc,
                    "role": meta.get("role"),
                    "timestamp": meta.get("timestamp"),
                    "date": meta.get("date") or (meta.get("timestamp") or "")[:10],
                    "session_id": meta.get("session_id"),
                    "topic_keywords": [
                        k.strip() for k in (meta.get("topic_keywords") or "").split(",")
                        if k.strip()
                    ],
                    "distance": dist,
                    "relevance": rel,
                })
            return out
        except Exception as e:
            print(f"  [MEMORY] search failed (non-fatal): {e}")
            return []

    def get_session(self, session_id, limit=500):
        """Return every stored turn for one conversation, oldest-first.

        Used by the source dossier to walk a whole conversation.
        """
        if not session_id or not self._ensure():
            return []
        try:
            with self._lock:
                res = self._collection.get(
                    where={"session_id": str(session_id)},
                    include=["documents", "metadatas"],
                    limit=limit,
                )
            docs = res.get("documents") or []
            metas = res.get("metadatas") or []
            rows = []
            for doc, meta in zip(docs, metas):
                meta = meta or {}
                rows.append({
                    "text": doc,
                    "role": meta.get("role"),
                    "timestamp": meta.get("timestamp"),
                    "date": meta.get("date"),
                    "session_id": meta.get("session_id"),
                    "topic_keywords": [
                        k.strip() for k in (meta.get("topic_keywords") or "").split(",")
                        if k.strip()
                    ],
                })
            rows.sort(key=lambda r: r.get("timestamp") or "")
            return rows
        except Exception as e:
            print(f"  [MEMORY] get_session failed (non-fatal): {e}")
            return []

    # ── introspection ────────────────────────────────────────────────
    def stats(self):
        """Index size + date range + session count.

        Always returns a dict; `available` tells the caller whether the rest of
        the numbers are meaningful.
        """
        if not self._ensure():
            return {
                "available": False,
                "count": 0,
                "sessions": 0,
                "earliest": None,
                "latest": None,
                "persist_dir": str(self.persist_dir),
                "model": self.model_name,
                "error": str(self._init_error) if self._init_error else "unavailable",
            }
        try:
            with self._lock:
                count = self._collection.count()
                earliest = latest = None
                sessions = set()
                if count:
                    res = self._collection.get(include=["metadatas"])
                    for meta in (res.get("metadatas") or []):
                        ts = (meta or {}).get("timestamp")
                        if ts:
                            earliest = ts if earliest is None or ts < earliest else earliest
                            latest = ts if latest is None or ts > latest else latest
                        sid = (meta or {}).get("session_id")
                        if sid:
                            sessions.add(sid)
            return {
                "available": True,
                "count": count,
                "sessions": len(sessions),
                "earliest": earliest,
                "latest": latest,
                "persist_dir": str(self.persist_dir),
                "model": self.model_name,
            }
        except Exception as e:
            return {
                "available": True, "count": 0, "sessions": 0,
                "earliest": None, "latest": None,
                "persist_dir": str(self.persist_dir), "model": self.model_name,
                "error": str(e),
            }


# ── process-wide singleton ────────────────────────────────────────────
_instance = None
_instance_lock = threading.Lock()


def get_conversation_memory(persist_dir=None):
    """Return the process-wide ConversationMemory, building it lazily."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = ConversationMemory(persist_dir=persist_dir)
    return _instance


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    import json as _json
    mem = get_conversation_memory()
    print("available:", mem.available())
    mem.index_exchange(
        "What did we decide about the voice raspiness fix?",
        "We replaced the per-chunk BufferSource scheduler with an AudioWorklet "
        "ring-buffer player called friday-pcm-player.",
        session_id="2026-06-05",
    )
    print(_json.dumps(mem.search("voice rasp audio fix", n=3), indent=2))
    print(_json.dumps(mem.stats(), indent=2))
