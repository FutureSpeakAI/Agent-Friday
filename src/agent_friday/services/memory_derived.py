"""What was made from a turn before it was corrected (north star §34.9-3/-4).

A superseded turn already stops being recalled (conversation memory,
conversation search, overnight consolidation, the knowledge graph's next
pass, the next daily summary). But things made from it EARLIER still say what
it said:

* the daily summary of the day it was said, which the next conversation
  opens with ("last session"), and
* knowledge-graph nodes extracted from it.

`after_supersession` is called by ConversationMemory whenever turns are
superseded or restored. It does the work on one background worker, so the
owner's correction is never held up by it:

* the daily summaries of those days are removed (they may state the
  corrected fact; no copy is kept) and queued to be written again, without
  the corrected turns, by the daily summary job (`redo_summaries`). A day
  leaves the queue only when its summary is written, or when none of its
  turns are left to summarise, and only if it was not queued again meanwhile;
* on a correction, the turns are queued for the knowledge graph
  (`kg-retract-pending.json`, ids only) and taken out with the same rules the
  Library purge uses (indexer.purge_conversation_turns): a node only they
  supported goes, a node other sources support loses its description, the
  community reports that mention it go, and its search entries go. It runs at
  once when no graph pass is running; otherwise the running pass applies it
  after it saves, so a pass that started earlier cannot put the nodes back.
  A file that refuses the save keeps the ids queued. Approved facts (fact_*)
  are the owner's and are not touched. A restored turn goes back into the
  graph on its next pass.

Never raises into the caller.
"""
from __future__ import annotations

import hashlib
import json
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Iterable, List, Optional

_log = logging.getLogger(__name__)
_LOCK = threading.RLock()
_JOBS: "queue.Queue" = queue.Queue(maxsize=512)
_WORKER: Optional[threading.Thread] = None


# ── the background worker ──────────────────────────────────────────────────

def _work() -> None:
    while True:
        job = _JOBS.get()
        try:
            job()
        except Exception as e:
            _log.warning("after a correction: %s", e)
        finally:
            _JOBS.task_done()


def _submit(job) -> bool:
    global _WORKER
    with _LOCK:
        if _WORKER is None or not _WORKER.is_alive():
            _WORKER = threading.Thread(target=_work, name="memory-derived", daemon=True)
            _WORKER.start()
    try:
        _JOBS.put_nowait(job)
        return True
    except queue.Full:
        _log.warning("after a correction: queue full; this update runs inline")
        job()
        return False


def drain(timeout: float = 10.0) -> bool:
    end = time.time() + timeout
    while _JOBS.unfinished_tasks and time.time() < end:
        time.sleep(0.02)
    return not _JOBS.unfinished_tasks


# ── daily summaries ────────────────────────────────────────────────────────

def _redo_path() -> Path:
    from agent_friday.paths import friday_home
    return Path(friday_home()) / "memory" / "session_summaries" / "redo.json"


def _read_redo() -> dict:
    """{day: mark}; the mark changes each time the day is queued again."""
    try:
        data = json.loads(_redo_path().read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(data, list):                         # an older plain list
        return {d: 0 for d in data if isinstance(d, str)}
    return {k: v for k, v in data.items() if isinstance(k, str)} if isinstance(data, dict) else {}


def _write_redo(days: dict) -> None:
    p = _redo_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(dict(sorted(days.items()))), encoding="utf-8")
    tmp.replace(p)


def _valid_day(d) -> bool:
    import re
    return isinstance(d, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) is not None


def mark_summaries(days: Iterable[str]) -> List[str]:
    """Remove the daily summaries of these days and queue them to be written
    again. Returns the days queued."""
    days = sorted({d for d in days if _valid_day(d)})
    if not days:
        return []
    from agent_friday.services import model_router
    with _LOCK:
        queued = _read_redo()
        for d in days:
            for ext in ("md", "json"):
                try:
                    model_router._session_summary_path(d, ext).unlink()
                except FileNotFoundError:
                    pass
                except Exception as e:
                    _log.warning("summary of %s not removed: %s", d, e)
            queued[d] = int(queued.get(d) or 0) + 1
        _write_redo(queued)
    return days


def _nothing_left(day: str) -> bool:
    """True when the day has no turn left that a summary would use."""
    try:
        from agent_friday.services import model_router
        mem = model_router._get_conversation_memory()
        if not mem.available():
            return False
        return not [t for t in mem.get_session(day) if not t.get("superseded")]
    except Exception:
        return False


def redo_summaries() -> int:
    """Write again the summaries queued by a correction. Called by the daily
    summary job. Returns how many days left the queue."""
    from agent_friday.services import model_router
    with _LOCK:
        queued = dict(_read_redo())
    done = []
    for d, mark in queued.items():
        try:
            model_router._generate_session_summary(d, force=True)
        except Exception as e:
            _log.warning("summary of %s not written again yet: %s", d, e)
        if model_router._load_session_summary(d).strip() or _nothing_left(d):
            done.append((d, mark))
    if done:
        with _LOCK:
            now = _read_redo()
            for d, mark in done:
                if now.get(d) == mark:                     # not queued again meanwhile
                    now.pop(d, None)
            _write_redo(now)
    return len(done)


# ── the knowledge graph ────────────────────────────────────────────────────

def _turn_ids(texts: Iterable[str]) -> set:
    """The ids the knowledge graph gave these turns (indexer._conversation_chunks)."""
    return {hashlib.sha1(str(t).encode()).hexdigest()[:10] for t in texts if str(t or "").strip()}


def _pending_path() -> Path:
    from agent_friday.services.knowledge_graph.store import KG_DIR
    return Path(KG_DIR) / "kg-retract-pending.json"


def _read_pending() -> set:
    try:
        return {str(x) for x in json.loads(_pending_path().read_text(encoding="utf-8"))}
    except Exception:
        return set()


def _write_pending(ids: set) -> None:
    p = _pending_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(sorted(ids)), encoding="utf-8")
    tmp.replace(p)


def _pass_state():
    """(running, started_at) of the semantic graph pass, or None when it
    cannot be read (then nothing is applied: it waits for the next pass)."""
    try:
        from agent_friday.routes import knowledge_graph as kg_routes
        st = kg_routes._TIER_B_STATE
        return bool(st.get("running")), st.get("started_at")
    except Exception:
        return None


def apply_pending_graph_retractions(store=None, clear_if=None) -> dict:
    """Take the queued turns out of the graph. The caller holds the graph's
    rebuild lock (the worker below, or a semantic pass right after its own
    save). Ids stay queued when a file refused the save, and when `clear_if`
    (a check run after the purge) says a pass may have loaded the graph
    before it: that pass applies them again after it saves."""
    from agent_friday.services.knowledge_graph import indexer
    with _LOCK:
        ids = _read_pending()
    if not ids:
        return {"applied": 0}
    out = indexer.purge_conversation_turns(ids, store=store)
    if not out.get("refused") and (clear_if is None or clear_if()):
        with _LOCK:
            _write_pending(_read_pending() - ids)
    return out


def queue_graph_retraction(texts: Iterable[str]) -> dict:
    """Queue these turns for removal from the graph and apply now if no graph
    pass is running; a running pass applies them after it saves."""
    tids = _turn_ids(texts)
    if not tids:
        return {"queued": 0}
    with _LOCK:
        _write_pending(_read_pending() | tids)
    state = _pass_state()
    try:
        from agent_friday.routes import knowledge_graph as kg_routes
        lock = kg_routes._rebuild_lock
    except Exception:
        lock = None
    if state is None or lock is None or state[0] or not lock.acquire(blocking=False):
        return {"queued": len(tids), "deferred": True}
    try:
        started = state[1]
        # Cleared only if no semantic pass started meanwhile: one that did
        # may have loaded the old graph, and applies the queue after it saves.
        return dict(apply_pending_graph_retractions(
            clear_if=lambda: _pass_state() == (False, started)), queued=len(tids))
    finally:
        lock.release()


# ── the hook ───────────────────────────────────────────────────────────────

def after_supersession(texts: List[str], days: List[str], superseded: bool) -> None:
    """See the module docstring. Returns at once; never raises."""
    texts, days = list(texts), list(days)

    def job():
        try:
            mark_summaries(days)
        except Exception as e:
            _log.warning("summaries not refreshed after a correction: %s", e)
        if superseded:
            try:
                queue_graph_retraction(texts)
            except Exception as e:
                _log.warning("graph not updated after a correction: %s", e)
    try:
        _submit(job)
    except Exception as e:
        _log.warning("after a correction: %s", e)
