"""Rebuild the indexes Friday derives from her own data, after an upgrade or a
restore (north star §37.3-9).

A derived index is made from stores Friday keeps (conversations, the wiki,
the Library's folders) and can always be made again from them:

* ``conversation_search``: the full-text index of every conversation
  (services/conversation_fts.build);
* ``knowledge_graph``: the knowledge graph's wiki tier
  (knowledge_graph.wiki_graph.rebuild_tier_a, under the same lock as the
  Knowledge Graph page's "Reindex now"); off when the graph is off;
* ``library``: the Library's look at every folder the owner shared with it
  (library.runtime.resweep), which queues what changed.

Rebuilds are asked for by a migration that changes what an index is made from
(Migration.rebuilds), by a restore (``restore/needs_reindex.json``, which the
bundle writes), or by the owner on the Doctor page. They are kept in
``<home>/migrations/rebuild-pending.json`` and run on a background thread a
little after the start, one at a time; never in safe mode (they wait for a
normal start). A rebuild that fails three times stops being retried and goes
to the owner's list of things to look at (services/upgrade_review.py), with
what to do. Results (index, ok, a fixed error word, when) are kept, newest 20.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

PENDING = "rebuild-pending.json"
RESULTS = "rebuild-results.json"
MAX_ATTEMPTS = 3
KEEP_RESULTS = 20
START_DELAY_S = 30.0
# Restore markers written before the index was named for what it is: the
# conversation search index (the conversation vectors travel in the bundle).
ALIASES = {"conversation_memory": "conversation_search"}
_LOCK = threading.Lock()
_RUN = threading.Lock()


def _conversation_search() -> dict:
    from agent_friday.services import conversation_fts
    out = conversation_fts.build(force=True)
    return out if isinstance(out, dict) else {"ok": True}


def _knowledge_graph() -> dict:
    from agent_friday.services.knowledge_graph import kg_settings
    if not kg_settings().get("enabled", True):
        return {"ok": True, "off": "the knowledge graph is off in Settings"}
    from agent_friday.routes.knowledge_graph import _rebuild_lock, emit_kg_event
    from agent_friday.services.knowledge_graph import consume_wiki_dirty, mark_wiki_dirty, wiki_graph
    from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore
    store = KnowledgeGraphStore()
    with _rebuild_lock:                  # the same lock as "Reindex now" and the nightly job
        before = store.refusals
        consume_wiki_dirty()
        try:
            info = wiki_graph.rebuild_tier_a(store=store)
        except Exception:
            mark_wiki_dirty("rebuild_failed")
            raise
        refused = store.refusals != before
    if refused:                          # a save refused (sealed or locked): the old graph stays
        mark_wiki_dirty("rebuild_failed")
        return {"ok": False, "error_word": "save refused"}
    try:
        emit_kg_event("reindexed", info if isinstance(info, dict) else {})
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "entities": (info or {}).get("entities") if isinstance(info, dict) else None}


def _library() -> dict:
    from agent_friday.services.library import grants, runtime
    from agent_friday.services.library.store import OWNER
    if grants.suspended():               # an unverifiable consent ledger: wait, never sweep
        return {"ok": False, "defer": "the Library's consent record cannot be checked yet"}
    return {"ok": True, "folders_queued": runtime.resweep(OWNER)}


INDEXES: dict = {
    "conversation_search": ("conversation search", _conversation_search),
    "knowledge_graph": ("the knowledge graph", _knowledge_graph),
    "library": ("the Library", _library),
}

TODO = {
    "conversation_search": "Press Rebuild now on this page.",
    "knowledge_graph": "Press Rebuild now on this page, or Reindex now on the Knowledge Graph page.",
    "library": "Press Rebuild now on this page, or Reindex in the Library.",
}


def _dir(home: Optional[Path]) -> Path:
    from agent_friday.paths import friday_home
    return Path(home or friday_home()) / "migrations"


def _read(p: Path, default):
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, type(default)) else default
    except (OSError, ValueError):
        return default


def _write(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".partial-" + uuid.uuid4().hex[:6])
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def _name(ix) -> Optional[str]:
    ix = ALIASES.get(str(ix), str(ix))
    return ix if ix in INDEXES else None


def request(indexes, why: str, home: Optional[Path] = None) -> list:
    """Ask for these indexes to be rebuilt (unknown names are ignored).
    Returns the names now waiting."""
    why = str(why or "")[:80]
    p = _dir(home) / PENDING
    with _LOCK:
        pend = _read(p, {})
        for ix in indexes or ():
            n = _name(ix)
            if not n:
                continue
            row = pend.get(n) if isinstance(pend.get(n), dict) else {"since": time.time(), "attempts": 0}
            if not isinstance(row.get("why"), list):
                row["why"] = []
            if why and why not in row["why"]:
                row["why"] = (row["why"] + [why])[-5:]
            pend[n] = row
        _write(p, pend)
        return sorted(pend)


def _fold_restore_marker(home: Optional[Path]) -> None:
    """A restore writes restore/needs_reindex.json; its indexes join the queue."""
    from agent_friday.paths import friday_home
    m = Path(home or friday_home()) / "restore" / "needs_reindex.json"
    if not m.exists():
        return
    d = _read(m, {})
    idx = d.get("indexes")
    request(idx if isinstance(idx, list) and idx else list(INDEXES), "a restore", home)
    _settle_carried_upgrade_items(home, d.get("at"))
    try:
        m.unlink()
    except OSError:
        pass


def _settle_carried_upgrade_items(home: Optional[Path], restored_at) -> None:
    """Items about an upgrade on the PC the bundle was made on (a failed, cut-off
    or blocked migration) say nothing true about this folder: they are sorted.
    Anything recorded after the restore stays."""
    try:
        at = float(restored_at)
    except (TypeError, ValueError):
        return
    try:
        from agent_friday.services import upgrade_review
        for it in upgrade_review.listing(home):
            if it["code"] in ("migration_failed", "migration_cut_off", "migration_link") \
                    and float(it.get("last_at") or 0) < at:
                upgrade_review.resolve(it["id"], home)
    except Exception:  # noqa: BLE001
        pass


def _result(home: Optional[Path], row: dict) -> None:
    p = _dir(home) / RESULTS
    with _LOCK:
        res = _read(p, [])
        res.append(row)
        _write(p, res[-KEEP_RESULTS:])


def run_pending(home: Optional[Path] = None, *, only: Optional[str] = None) -> dict:
    """Run every waiting rebuild now (or only `only`), one at a time. Never in
    safe mode. Returns {ran: [...], deferred: why or None}."""
    try:
        from agent_friday.services import safe_mode
        if safe_mode.active():
            return {"ran": [], "deferred": "safe mode: rebuilds wait for a normal start"}
    except Exception:  # noqa: BLE001
        pass
    if not _RUN.acquire(blocking=False):
        return {"ran": [], "deferred": "a rebuild is already running"}
    try:
        _fold_restore_marker(home)
        ran, tried = [], set()
        p = _dir(home) / PENDING
        while True:                      # re-read: a press while this runs joins this run
            pend_now = _read(p, {})
            for bad in [k for k in pend_now if k not in INDEXES]:
                with _LOCK:              # a name this Friday does not know (a newer one wrote it)
                    cur = _read(p, {})
                    cur.pop(bad, None)
                    _write(p, cur)
            todo = [k for k in sorted(pend_now) if k in INDEXES and k not in tried and (not only or k == only)]
            if not todo:
                break
            n = todo[0]
            tried.add(n)
            label, fn = INDEXES[n]
            t0 = time.monotonic()
            try:
                out = fn()
                ok = not (isinstance(out, dict) and out.get("ok") is False)
                err = None if ok else str((out or {}).get("error_word") or "it reported a failure")[:40]
            except Exception as e:  # noqa: BLE001 - recorded, never raised into the start
                ok, err, out = False, type(e).__name__, None
            deferred = isinstance(out, dict) and bool(out.get("defer"))
            with _LOCK:
                pend = _read(p, {})
                row = pend.get(n) if isinstance(pend.get(n), dict) else {"attempts": 0, "why": []}
                if deferred:
                    pass                 # waits for the next run without using a try
                elif ok:
                    pend.pop(n, None)
                else:
                    row["attempts"] = int(row.get("attempts") or 0) + 1
                    if row["attempts"] >= MAX_ATTEMPTS:
                        pend.pop(n, None)
                    else:
                        pend[n] = row
                _write(p, pend)
            if deferred:
                ran.append({"index": n, "ok": False, "deferred": str(out["defer"])[:80], "at": time.time()})
                continue
            res = {"index": n, "ok": ok, "error": err, "at": time.time(),
                   "seconds": round(time.monotonic() - t0, 2), "why": row.get("why") or [],
                   "off": (out or {}).get("off") if isinstance(out, dict) else None}
            _result(home, res)
            if ok:
                try:
                    from agent_friday.services import upgrade_review
                    for it in upgrade_review.listing(home):
                        if it["code"] == "rebuild_failed" and it["store"] == n:
                            upgrade_review.resolve(it["id"], home)
                except Exception:  # noqa: BLE001
                    pass
            elif row["attempts"] >= MAX_ATTEMPTS:
                from agent_friday.services import upgrade_review
                upgrade_review.add("rebuild_failed", n,
                                   "Friday could not rebuild %s after %s (%s, %d tries). Searches in it may "
                                   "miss things until it is rebuilt." % (label, " and ".join(row.get("why") or
                                                                                         ["a change"]),
                                                                         err, row["attempts"]),
                                   TODO[n], source="rebuild", home=home)
            ran.append(res)
        return {"ran": ran, "deferred": None}
    finally:
        _RUN.release()


def busy() -> bool:
    """A rebuild run is going on in this process."""
    if _RUN.acquire(blocking=False):
        _RUN.release()
        return False
    return True


def state(home: Optional[Path] = None) -> dict:
    """For the Doctor: what is waiting and the newest results."""
    pend = _read(_dir(home) / PENDING, {})
    from agent_friday.paths import friday_home
    marker = (Path(home or friday_home()) / "restore" / "needs_reindex.json").exists()
    return {"waiting": [{"index": n, "label": INDEXES[n][0],
                         "attempts": int((pend.get(n) or {}).get("attempts") or 0)}
                        for n in sorted(pend) if n in INDEXES],
            "restore_waiting": marker,
            "results": list(reversed(_read(_dir(home) / RESULTS, [])))[:10],
            "indexes": {n: v[0] for n, v in INDEXES.items()}}


def start_after_boot(delay_s: float = START_DELAY_S, sleep: Callable[[float], None] = time.sleep) -> None:
    """At start: run what is waiting, a little later, on a thread."""
    def _go():
        sleep(delay_s)
        try:
            out = run_pending()
            if out.get("ran"):
                print("  Rebuilt after an upgrade or restore: %s" % ", ".join(
                    "%s %s" % (r["index"], "ok" if r["ok"] else "failed") for r in out["ran"]))
        except Exception as e:  # noqa: BLE001
            print("  Index rebuilds: not run (%s)" % type(e).__name__)
    threading.Thread(target=_go, name="derived-rebuild", daemon=True).start()
