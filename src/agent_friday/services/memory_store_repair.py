"""services/memory_store_repair.py -- the Doctor watches and repairs Friday's other memory
stores (BD-16; North Star 32.5 "the Doctor offers index repair", NS-32.6-1).

FT4 (#404) did the conversation search index. This module covers the two other stores Friday
recalls from:

* The knowledge graph's plain files (knowledge-graph/<artifact>.json). A file that does not
  parse used to read as empty, and the next save then wrote over it. Now it is said (recall
  health, the Doctor's knowledge_graph check), and before anything writes over it the file is
  kept aside as <artifact>.json.damaged-<time> with a signed receipt. The owner's press in the
  Doctor keeps any damaged file aside and rebuilds the wiki layer (Tier A) from the wiki pages
  on this PC. Facts a model extracted (Tier B) are not re-extracted here: that runs a model,
  so it stays a choice on the knowledge graph page; the ones that were in a damaged file are
  in its kept copy. A forget or a Library purge removes the kept copies too (they cannot be
  scrubbed record by record, and what was forgotten must not survive in them).
* The conversation vector store (conversation_memory, ChromaDB). Damage is found and said
  (the Doctor's vector_memory check), and the owner's press there rebuilds it (BD-16 C2b):
  - A NEW store is built from the transcripts in a folder beside the live one
    (memory/conversations.rebuild-<time>). The live store is not touched while it is built,
    and Friday keeps using it.
  - The owner's corrections are kept: each superseded turn keeps its reason (a memory-card
    correction can still be undone), read from the old store when it opens, else from
    conversation search (which now keeps the reason too; a correction made before that is
    kept as "superseded").
  - Every chat write, delete and correction made while it builds is also noted, and replayed
    onto the new store before the switch. The switch holds those writes for its moment,
    rewrites one pointer file (atomically), and closes the old store; anything still holding
    the old store object has its writes sent to the new one, and the chat path fetches the
    new one.
  - The knowledge graph's entity vectors (they live in the same store) are re-embedded from
    the graph on this PC; an entity forgotten meanwhile is left out.
  - The old store is then deleted (it may hold words forgotten since); if Windows still holds
    it open, at the next start. A rebuild that fails deletes its new folder and changes
    nothing.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

_log = logging.getLogger("friday.memory_store_repair")
_KG_REPAIRING = threading.Event()
_LOCK = threading.Lock()


def _receipt(target: str, decision: str, by: str, **extra: Any) -> None:
    from agent_friday.governance import action_gate
    entry = {"tool": "doctor_repair", "class": action_gate.INTERNAL, "decision": decision,
             "surface": "doctor", "chosen_by": str(by)[:40], "target": target}
    entry.update({k: v for k, v in extra.items() if v is not None})
    action_gate._receipt(entry)


# ── the conversation vector store: found and said ────────────────────────────

def vector_health(_ignore_running: bool = False) -> Dict[str, Any]:
    """{"state": ok | missing | damaged | unavailable | unreadable}. Never raises; opens
    nothing for writing and loads no model (the store object is only constructed; its SQLite
    file is checked read-only and immutable, and only when no write-ahead log is pending)."""
    if _VEC_REBUILDING.is_set() and not _ignore_running:
        return {"state": "repairing"}
    try:
        from agent_friday import conversation_memory as cm
        mem = cm.get_conversation_memory()
        err = getattr(mem, "_init_error", None)
        if err is not None:
            if isinstance(err, ImportError):
                return {"state": "unavailable"}        # ChromaDB not installed: not damage
            return {"state": "damaged", "why": type(err).__name__}
        d = Path(mem.persist_dir)
        if not d.exists():
            return {"state": "missing"}
        db = d / "chroma.sqlite3"
        wal = d / "chroma.sqlite3-wal"
        journal = d / "chroma.sqlite3-journal"
        if db.exists() and not (wal.exists() and wal.stat().st_size) and not journal.exists():
            conn = sqlite3.connect(db.resolve().as_uri() + "?mode=ro&immutable=1", uri=True, timeout=5)
            try:
                row = conn.execute("PRAGMA quick_check").fetchone()
            finally:
                conn.close()
            if not row or str(row[0]).lower() != "ok":
                return {"state": "damaged", "why": "quick_check"}
        return {"state": "ok"}
    except sqlite3.DatabaseError:
        return {"state": "damaged", "why": "unreadable"}
    except Exception as e:  # noqa: BLE001
        return {"state": "unreadable", "why": type(e).__name__}


_VEC_REBUILDING = threading.Event()


def vector_rebuild_running() -> bool:
    return _VEC_REBUILDING.is_set()


def begin_vector_rebuild() -> bool:
    """Claim the one conversation-memory rebuild (the route claims it before its thread)."""
    with _LOCK:
        if _VEC_REBUILDING.is_set():
            return False
        _VEC_REBUILDING.set()
        return True


class NeedsConfirm(RuntimeError):
    """The store in use cannot be read, so a rebuild would keep only what the transcripts hold."""


#: The last rebuild's outcome (the Doctor says it).
LAST_VECTOR_REBUILD: Dict[str, Any] = {}


def _copy_collection(src, dst, *, vectors: bool) -> Dict[str, str]:
    """Copy every row of `src` into `dst` (with its vectors when they are in the same model's
    space). Returns {id: words}. Raises when a page cannot be read."""
    out: Dict[str, str] = {}
    inc = ["documents", "metadatas"] + (["embeddings"] if vectors else [])
    every = [str(i) for i in (src.get(include=[]).get("ids") or [])]
    for start in range(0, len(every), 500):
        got = src.get(ids=every[start:start + 500], include=inc)
        ids = list(got.get("ids") or [])         # one deleted since it was listed is not here
        if not ids:
            continue
        docs = list(got.get("documents") or [None] * len(ids))
        metas = [dict(m or {}) for m in (got.get("metadatas") or [{}] * len(ids))]
        kw = {"ids": ids, "documents": [d or "" for d in docs], "metadatas": metas}
        embs = got.get("embeddings") if vectors else None
        if embs is not None and len(embs) == len(ids):
            kw["embeddings"] = [list(map(float, e)) for e in embs]
        dst.upsert(**kw)
        for i, d in zip(ids, docs):
            out[str(i)] = " ".join(str(d or "").split())
    return out


def _same_space(old, new) -> bool:
    from agent_friday import conversation_memory as cm
    try:
        a = dict(getattr(old._collection, "metadata", None) or {})
        b = dict(getattr(new._collection, "metadata", None) or {})
        return bool(a.get(cm._MODEL_META_KEY)) and a.get(cm._MODEL_META_KEY) == b.get(cm._MODEL_META_KEY) \
            and a.get(cm._DIM_META_KEY) == b.get(cm._DIM_META_KEY)
    except Exception:
        return False


def _seed(old, staging) -> tuple:
    """Copy the store in use into the new one: every turn as it is (its words as the live path
    cleaned them, its correction and the correction's reason), and the graph's entity vectors.
    Returns ({id: words} of the turns copied, whether all of it could be read)."""
    try:
        if old is None or not old._ensure():
            return {}, False
        vectors = _same_space(old, staging)
        # Not under the old store's lock: chat keeps writing to it, and every such write is in
        # the journal (started before this read), replayed onto the new store afterwards.
        turns = _copy_collection(old._collection, staging._collection, vectors=vectors)
        try:
            _copy_collection(old._client.get_or_create_collection("knowledge-graph"),
                             staging._client.get_or_create_collection("knowledge-graph"),
                             vectors=True)          # always kg_vectors' own; never re-embedded
        except Exception as e:  # noqa: BLE001
            _log.warning("the graph's entity vectors were not copied (%s); re-embedded", type(e).__name__)
            _embed_graph(staging)
        return turns, True
    except Exception as e:  # noqa: BLE001
        _log.warning("the store in use could not be read (%s)", type(e).__name__)
        return {}, False


def _kg_entities_to_embed() -> List[Dict[str, Any]]:
    """The entities the graph indexer embeds (its own, never a Library link node)."""
    try:
        from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore
        return [e for e in (KnowledgeGraphStore(base_dir=_kg_dir()).load("entities") or [])
                if isinstance(e, dict) and e.get("tier") == "B" and e.get("id")
                and not str(e["id"]).startswith("lib_")]
    except Exception:
        return []


def _embed_graph(staging) -> int:
    from agent_friday.services.knowledge_graph.indexer import _embed_entities
    ents = _kg_entities_to_embed()
    return _embed_entities(ents, lambda msg: None, cm=staging) if ents else 0


def rebuild_vector(by: str, *, begun: bool = False, progress=None,
                   accept_loss: bool = False) -> Dict[str, Any]:
    """Build a new conversation store beside the one in use and switch to it.

    The store in use is copied in first (every turn as it is, with its correction), then any
    turn the transcripts hold that it is missing is added, then every write made meanwhile is
    replayed, then the switch. When the store in use cannot be read the transcripts are all
    there is (they keep the last turns of each conversation): that is done only with the
    owner's `accept_loss`. On any failure before the switch the new folder is deleted and
    nothing changes."""
    if not begun and not begin_vector_rebuild():
        return {"ok": False, "error": "already rebuilding"}
    import shutil
    from agent_friday import conversation_memory as cm
    say = progress or (lambda msg: None)
    staging = None
    new_dir = None
    switched = False
    replayed = 0
    deleted_ids: set = set()
    deleted_words: set = set()

    def replay(ops):
        nonlocal replayed
        for op, kw in ops:
            if op == "delete":
                deleted_ids.update(str(i) for i in kw.get("ids") or [])
                if kw.get("exact_text"):
                    deleted_words.add(" ".join(str(kw["exact_text"]).split()))
        cm.journal_replay(staging, ops)
        replayed += len(ops)
    try:
        _receipt("vector_memory", "rebuild_started", by)
        if cm.rewrite_running():
            raise RuntimeError("a Library purge is running")
        purges = cm.rewrites()
        cm.journal_start()                       # before anything of the old store is read
        try:
            old = cm.get_conversation_memory()
        except Exception:
            old = None
        base = cm.DEFAULT_PERSIST_DIR.parent
        base.mkdir(parents=True, exist_ok=True)
        new_dir = base / ("conversations.rebuild-%d" % int(time.time() * 1000))
        cm.build_mark(new_dir).write_text("%d:%d" % (os.getpid(), int(time.time())), encoding="utf-8")
        staging = cm.ConversationMemory(persist_dir=new_dir)
        staging._staging = True
        if not staging._ensure():
            raise RuntimeError("the new store could not be opened")
        seeded, readable = _seed(old, staging)
        if not readable:
            if not accept_loss:
                raise NeedsConfirm("the conversation memory in use cannot be read")
            _embed_graph(staging)
        out = staging.rebuild_from_transcripts(say) or {}
        if out.get("error"):
            raise RuntimeError(out["error"])
        for _ in range(5):                       # catch up outside the switch, so chat waits least
            ops = cm.journal_take()
            if not ops:
                break
            replay(ops)
        with cm.SWAP_LOCK:
            if cm.rewrites() != purges or cm.rewrite_running():
                raise RuntimeError("a Library purge ran while this was built")
            replay(cm.journal_take(stop=True))
            have = set(staging._collection.get(include=[]).get("ids") or [])
            lost = [i for i, w in seeded.items()
                    if i not in have and i not in deleted_ids and w not in deleted_words]
            if lost:
                raise RuntimeError("%d turns of the store in use are missing from the new one" % len(lost))
            try:
                if staging._client is not None:
                    staging._client.close()
            except Exception:
                pass
            cm.build_mark(new_dir).unlink(missing_ok=True)   # a finished store carries no mark
            cm.set_live_dir(new_dir)
            switched = True                       # from here nothing deletes the new store
            cm.close_conversation_memory()
            if old is not None:
                old._closed = True
            staging = None
        left = 0
        try:
            left = cm.drop_stale_stores()
            from agent_friday.services import recall_health
            recall_health.note_ok("past_conversations")
        except Exception:
            pass
        res = {"ok": True, "copied": len(seeded), "indexed": out.get("indexed", 0),
               "failed": out.get("failed", 0), "replayed": replayed,
               "from_transcripts_only": not readable, "old_copy_left": bool(left)}
        try:
            _receipt("vector_memory", "rebuilt", by, copied=len(seeded),
                     indexed=int(out.get("indexed") or 0), failed=int(out.get("failed") or 0),
                     replayed=replayed, from_transcripts_only=(not readable) or None,
                     old_copy_left=left or None)
        except Exception as e:  # noqa: BLE001
            _log.error("the rebuild switched stores but its receipt was not written (%s)", type(e).__name__)
        LAST_VECTOR_REBUILD.clear()
        LAST_VECTOR_REBUILD.update(res)
        return res
    except Exception as e:  # noqa: BLE001
        cm.journal_take(stop=True)
        if switched:                              # the new store is live: never undone here
            _log.error("after the switch: %s", type(e).__name__)
            return {"ok": True, "warning": type(e).__name__}
        if staging is not None:
            try:
                if staging._client is not None:
                    staging._client.close()
            except Exception:
                pass
        if new_dir is not None:
            try:
                if new_dir.exists():
                    shutil.rmtree(new_dir)
                cm.build_mark(new_dir).unlink(missing_ok=True)
            except OSError:
                # Still held open: marked failed, so it is stale (deleted at the next chance) and
                # never taken for a finished store.
                try:
                    cm.build_mark(new_dir).write_text("failed", encoding="utf-8")
                except OSError:
                    pass
        confirm = isinstance(e, NeedsConfirm)
        _receipt("vector_memory", "rebuild_needs_confirm" if confirm else "rebuild_failed", by,
                 error=type(e).__name__)
        res = {"ok": False, "needs_confirm": confirm or None,
               "error": ("the conversation memory in use cannot be read, so a rebuild would keep only "
                         "what your conversations still hold (the most recent turns of each); "
                         "older recall would be lost" if confirm else
                         "the conversation memory could not be rebuilt (%s); the store in use is "
                         "unchanged" % type(e).__name__)}
        LAST_VECTOR_REBUILD.clear()
        LAST_VECTOR_REBUILD.update(res)
        return res
    finally:
        _VEC_REBUILDING.clear()


# ── the knowledge graph's plain files ─────────────────────────────────────────

def _kg_dir() -> Path:
    from agent_friday.services.knowledge_graph import KG_DIR
    return Path(KG_DIR)


def _artifacts() -> tuple:
    from agent_friday.services.knowledge_graph.store import ARTIFACT_NAMES
    return ARTIFACT_NAMES


def plain_damaged(path: Path) -> bool:
    """A plain graph file that exists and does not parse as a JSON list (every artifact is
    written as a list)."""
    try:
        if not path.exists():
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
        return not isinstance(data, list)
    except (OSError, ValueError):
        return True


def keep_aside(path: Path, *, by: str = "friday:save") -> Path:
    """Keep a damaged plain graph file beside itself before anything writes over it. A unique
    name, so an earlier kept copy is never replaced; a signed receipt."""
    dest = path.with_name("%s.damaged-%s-%d" % (path.name, time.strftime("%Y%m%d-%H%M%S"),
                                                 time.time_ns() % 1_000_000_000))
    os.rename(path, dest)                         # fails, never overwrites, if dest exists
    _log.error("knowledge graph: %s did not parse; kept as %s", path.name, dest.name)
    try:
        _receipt("knowledge_graph", "kept_damaged_file", by, file=dest.name)
    except Exception:
        pass
    return dest


def drop_kept_copies(base: Path = None, *, why: str, damaged_live: bool = False) -> int:
    """Remove every kept damaged graph file (and, with ``damaged_live``, a live plain file
    that does not parse): after a forget or a Library purge, what was forgotten must not
    survive in a file that cannot be scrubbed record by record. A signed receipt names how many."""
    base = Path(base) if base else _kg_dir()
    n = 0
    targets = list(base.glob("*.json.damaged-*")) if base.exists() else []
    if damaged_live and base.exists():
        targets += [base / ("%s.json" % a) for a in _artifacts() if plain_damaged(base / ("%s.json" % a))]
    for p in targets:
        try:
            p.unlink()
            n += 1
        except OSError as e:
            _log.error("knowledge graph: kept copy %s could not be removed (%s)", p.name,
                       type(e).__name__)
    if n:
        _log.warning("knowledge graph: %d kept damaged file(s) removed (%s)", n, why)
        try:
            _receipt("knowledge_graph", "kept_copies_removed", "friday:" + why.replace(" ", "_"),
                     removed=n)
        except Exception:
            pass
    return n


def kg_health() -> Dict[str, Any]:
    """{"state": ok | damaged | repairing, "damaged": [artifact names], "kept": n}."""
    if _KG_REPAIRING.is_set():
        return {"state": "repairing", "damaged": [], "kept": 0}
    try:
        base = _kg_dir()
        bad = [n for n in _artifacts() if plain_damaged(base / ("%s.json" % n))]
        kept = len(list(base.glob("*.json.damaged-*"))) if base.exists() else 0
        return {"state": "damaged" if bad else "ok", "damaged": bad, "kept": kept}
    except Exception as e:  # noqa: BLE001
        return {"state": "unreadable", "damaged": [], "kept": 0, "why": type(e).__name__}


def kg_repair_running() -> bool:
    return _KG_REPAIRING.is_set()


def begin_kg_repair() -> bool:
    """Claim the one graph repair (the route claims it before starting its thread)."""
    with _LOCK:
        if _KG_REPAIRING.is_set():
            return False
        _KG_REPAIRING.set()
        return True


def repair_kg(by: str, *, begun: bool = False) -> Dict[str, Any]:
    """Keep each damaged plain file aside, then rebuild the wiki layer from the wiki pages.
    Runs no model."""
    if not begun and not begin_kg_repair():
        return {"ok": False, "error": "already rebuilding"}
    try:
        _receipt("knowledge_graph", "rebuild_started", by)
        base = _kg_dir()
        kept: List[str] = []
        for n in _artifacts():
            p = base / ("%s.json" % n)
            if plain_damaged(p):
                kept.append(keep_aside(p, by=by).name)
        try:
            from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore
            from agent_friday.services.knowledge_graph.wiki_graph import rebuild_tier_a
            store = KnowledgeGraphStore(base_dir=base)
            out = rebuild_tier_a(store=store) or {}
        except Exception as e:  # noqa: BLE001
            _receipt("knowledge_graph", "rebuild_failed", by, error=type(e).__name__, kept=kept)
            return {"ok": False, "kept": kept,
                    "error": "the wiki layer could not be rebuilt (%s); the damaged files are kept"
                             % type(e).__name__}
        refused = list(getattr(store, "refused", []) or [])
        if refused:
            _receipt("knowledge_graph", "rebuild_refused", by, kept=kept,
                     refused=[r.get("artifact") for r in refused])
            return {"ok": False, "kept": kept, "refused": refused,
                    "error": "part of the graph was not written (a sealed file could not be "
                             "replaced safely); nothing was lost"}
        try:
            from agent_friday.services import recall_health
            recall_health.note_ok("knowledge_graph")
        except Exception:
            pass
        _receipt("knowledge_graph", "rebuilt", by, kept=kept)
        return {"ok": True, "kept": kept, "result": {k: v for k, v in dict(out).items()
                                                      if isinstance(v, (int, float, str))}}
    finally:
        _KG_REPAIRING.clear()
