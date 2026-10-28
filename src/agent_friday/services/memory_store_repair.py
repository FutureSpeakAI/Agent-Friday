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
  (the Doctor's vector_memory check). It is NOT rebuilt here yet: a safe rebuild must also
  re-point every live holder of the store, hold writes and deletes while it runs, and keep
  the owner's corrections (superseded rows), and that is its own slice (LEDGER, BD-16 C2b).
  Until then the Doctor says what is wrong and that recall from past conversations is
  limited; nothing is deleted.
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

def vector_health() -> Dict[str, Any]:
    """{"state": ok | missing | damaged | unavailable | unreadable}. Never raises; opens
    nothing for writing and loads no model (the store object is only constructed; its SQLite
    file is checked read-only and immutable, and only when no write-ahead log is pending)."""
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
        if db.exists() and not (wal.exists() and wal.stat().st_size):
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


def drop_kept_copies(base: Path = None, *, why: str) -> int:
    """Remove every kept damaged graph file: after a forget or a Library purge, what was
    forgotten must not survive in a copy that cannot be scrubbed record by record."""
    base = Path(base) if base else _kg_dir()
    n = 0
    for p in (base.glob("*.json.damaged-*") if base.exists() else []):
        try:
            p.unlink()
            n += 1
        except OSError as e:
            _log.error("knowledge graph: kept copy %s could not be removed (%s)", p.name,
                       type(e).__name__)
    if n:
        _log.warning("knowledge graph: %d kept damaged file(s) removed (%s)", n, why)
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
