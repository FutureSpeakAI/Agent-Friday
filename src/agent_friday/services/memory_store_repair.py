"""services/memory_store_repair.py -- the Doctor repairs Friday's other memory stores (BD-16).

North Star 32.5 ("the Doctor offers index repair") and NS-32.6-1 (a damaged index is found,
said, and rebuilt). FT4 (#404) did this for the conversation search index. This module does it
for the two other stores Friday recalls from:

* The conversation vector store (conversation_memory, ChromaDB under memory/conversations).
  Its rows are made only from the transcripts, so a damaged store is rebuilt from them:
  the old folder is moved aside, a fresh store is opened and filled from the transcripts
  (deleted and off-record turns stay out), and only once that worked is the old folder
  deleted (it holds message text, including turns the owner has since deleted). If the
  rebuild fails, the old folder is put back as it was.
* The knowledge graph's plain files (knowledge-graph/<artifact>.json). A file that does not
  parse used to read as empty, and the next save then wrote over it. Now it is noticed (the
  Doctor says so, recall health says so), and before anything is written over it the file
  is kept aside as <artifact>.json.damaged-<time>. The repair rebuilds the wiki layer (Tier A)
  from the wiki pages on this PC. Facts a model extracted (Tier B) are not re-extracted here:
  that runs a model, so it stays a choice on the knowledge graph page.

Each repair writes a signed receipt (the action checkpoint's ledger) when it starts and when
it ends, and is started by the owner's press in the Doctor (routes/doctor.py) or, for the
graph's guard, by the save itself.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

_log = logging.getLogger("friday.memory_store_repair")
_VEC_REPAIRING = threading.Event()
_KG_REPAIRING = threading.Event()
_LOCK = threading.Lock()


def _receipt(target: str, decision: str, by: str, **extra: Any) -> None:
    from agent_friday.governance import action_gate
    entry = {"tool": "doctor_repair", "class": action_gate.INTERNAL, "decision": decision,
             "surface": "doctor", "chosen_by": str(by)[:40], "target": target}
    entry.update({k: v for k, v in extra.items() if v is not None})
    action_gate._receipt(entry)


def _stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


# ── the conversation vector store ─────────────────────────────────────────────

def _cm():
    from agent_friday import conversation_memory
    return conversation_memory


def vector_health() -> Dict[str, Any]:
    """{"state": ok | missing | damaged | unavailable | repairing}. Never raises; opens nothing
    for writing (the store's own SQLite file is checked read-only and immutable)."""
    if _VEC_REPAIRING.is_set():
        return {"state": "repairing"}
    try:
        mem = _cm().get_conversation_memory()
        d = Path(mem.persist_dir)
        err = getattr(mem, "_init_error", None)
        if err is not None:
            if isinstance(err, ImportError):
                return {"state": "unavailable"}        # ChromaDB not installed: not damage
            return {"state": "damaged", "why": type(err).__name__}
        if not d.exists():
            return {"state": "missing"}
        db = d / "chroma.sqlite3"
        if db.exists():
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


def vector_repair_running() -> bool:
    return _VEC_REPAIRING.is_set()


def repair_vector(by: str) -> Dict[str, Any]:
    """Rebuild the conversation vector store from the transcripts. The transcripts are only
    read. One repair at a time."""
    with _LOCK:
        if _VEC_REPAIRING.is_set():
            return {"ok": False, "error": "already rebuilding"}
        _VEC_REPAIRING.set()
    cm = _cm()
    aside = None
    try:
        _receipt("vector_memory", "rebuild_started", by)
        d = Path(cm.get_conversation_memory().persist_dir)
        cm.close_conversation_memory()                  # release the files first (Windows)
        if d.exists():
            aside = d.with_name(d.name + ".rebuilding-" + _stamp())
            try:
                os.replace(d, aside)
            except OSError as e:
                _receipt("vector_memory", "rebuild_failed", by, error=type(e).__name__)
                return {"ok": False, "error": "the old store could not be moved aside (%s); "
                                              "nothing was changed" % type(e).__name__}
        mem = cm.get_conversation_memory()
        try:
            if not mem._ensure():
                raise RuntimeError("the new store did not open (%s)"
                                   % type(getattr(mem, "_init_error", None)).__name__)
            added = int(mem.rebuild_from_transcripts() or 0)
        except Exception as e:  # noqa: BLE001 - put the old folder back as it was
            cm.close_conversation_memory()
            if aside is not None:
                shutil.rmtree(d, ignore_errors=True)
                try:
                    os.replace(aside, d)
                except OSError:
                    pass
            _receipt("vector_memory", "rebuild_failed", by, error=type(e).__name__)
            return {"ok": False, "error": "the rebuild did not finish (%s); the old store was put back"
                                          % type(e).__name__}
        if aside is not None:
            # The old store holds message text, including turns deleted since: once the new one
            # is built from the transcripts, it goes.
            shutil.rmtree(aside, ignore_errors=True)
        try:
            from agent_friday.services import recall_health
            recall_health.note_ok("past_conversations")
        except Exception:
            pass
        _receipt("vector_memory", "rebuilt", by, rows=added)
        _log.warning("conversation memory rebuilt from the transcripts (%s; %d rows)", by, added)
        return {"ok": True, "rows": added}
    finally:
        _VEC_REPAIRING.clear()


# ── the knowledge graph's plain files ─────────────────────────────────────────

def _kg_dir() -> Path:
    from agent_friday.services.knowledge_graph import KG_DIR
    return Path(KG_DIR)


def _artifacts() -> tuple:
    from agent_friday.services.knowledge_graph.store import ARTIFACT_NAMES
    return ARTIFACT_NAMES


def plain_damaged(path: Path) -> bool:
    """A plain graph file that exists and does not parse as a JSON list."""
    try:
        if not path.exists():
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
        return not isinstance(data, list)
    except (OSError, ValueError):
        return True


def keep_aside(path: Path) -> Path:
    """Keep a damaged plain graph file beside itself before anything writes over it."""
    dest = path.with_name(path.name + ".damaged-" + _stamp())
    n = 1
    while dest.exists():
        dest = path.with_name(path.name + ".damaged-%s-%d" % (_stamp(), n))
        n += 1
    os.replace(path, dest)
    _log.error("knowledge graph: %s did not parse; kept as %s", path.name, dest.name)
    return dest


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


def repair_kg(by: str) -> Dict[str, Any]:
    """Keep each damaged plain file aside, then rebuild the wiki layer from the wiki pages.
    Runs no model."""
    with _LOCK:
        if _KG_REPAIRING.is_set():
            return {"ok": False, "error": "already rebuilding"}
        _KG_REPAIRING.set()
    try:
        _receipt("knowledge_graph", "rebuild_started", by)
        base = _kg_dir()
        kept: List[str] = []
        for n in _artifacts():
            p = base / ("%s.json" % n)
            if plain_damaged(p):
                kept.append(keep_aside(p).name)
        try:
            from agent_friday.services.knowledge_graph.wiki_graph import rebuild_tier_a
            out = rebuild_tier_a() or {}
        except Exception as e:  # noqa: BLE001
            _receipt("knowledge_graph", "rebuild_failed", by, error=type(e).__name__, kept=kept)
            return {"ok": False, "kept": kept,
                    "error": "the wiki layer could not be rebuilt (%s); the damaged files are kept"
                             % type(e).__name__}
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
