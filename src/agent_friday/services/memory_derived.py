"""What was made from a turn before it was corrected (north star §34.9-3/-4).

A superseded turn already stops being recalled (conversation memory,
conversation search, overnight consolidation, the knowledge graph's next
pass, the next daily summary). But things made from it EARLIER still say what
it said:

* the daily summary of the day it was said, which the next conversation
  opens with ("last session"), and
* knowledge-graph nodes extracted from it.

`after_supersession` is called by ConversationMemory whenever turns are
superseded or restored:

* the daily summaries of those days are removed at once (they may state the
  corrected fact; no copy is kept) and queued to be written again, without
  the corrected turns, by the daily summary job (`redo_summaries`);
* on a correction, the turns' contribution to the knowledge graph is taken
  out: a node only they supported is dropped, a node other sources still
  support is blanked (no longer known), relationships alike. Approved facts
  (fact_*) are the owner's and are not touched. A restored turn goes back
  into the graph on its next pass.

Never raises into the caller.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Iterable, List

_log = logging.getLogger(__name__)
_LOCK = threading.Lock()


def _redo_path() -> Path:
    from agent_friday.paths import friday_home
    return Path(friday_home()) / "memory" / "session_summaries" / "redo.json"


def _read_redo() -> List[str]:
    try:
        data = json.loads(_redo_path().read_text(encoding="utf-8"))
        return [d for d in data if isinstance(d, str)] if isinstance(data, list) else []
    except Exception:
        return []


def _write_redo(days: List[str]) -> None:
    p = _redo_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(sorted(set(days))), encoding="utf-8")
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
        for d in days:
            for ext in ("md", "json"):
                try:
                    model_router._session_summary_path(d, ext).unlink()
                except FileNotFoundError:
                    pass
                except Exception as e:
                    _log.warning("summary of %s not removed: %s", d, e)
        _write_redo(_read_redo() + days)
    return days


def redo_summaries() -> int:
    """Write again the summaries queued by a correction. Called by the daily
    summary job. Returns how many were written; a day that cannot be written
    now stays queued."""
    from agent_friday.services import model_router
    with _LOCK:
        queued = _read_redo()
    done = []
    for d in queued:
        try:
            model_router._generate_session_summary(d, force=True)
            done.append(d)                 # written, or nothing left to summarise
        except Exception as e:
            _log.warning("summary of %s not written again yet: %s", d, e)
    if done:
        with _LOCK:
            _write_redo([d for d in _read_redo() if d not in done])
    return len(done)


def _turn_ids(texts: Iterable[str]) -> set:
    """The ids the knowledge graph gave these turns (indexer._conversation_chunks)."""
    return {hashlib.sha1(str(t).encode()).hexdigest()[:10] for t in texts if str(t or "").strip()}


def retract_from_graph(texts: Iterable[str], store=None) -> dict:
    """Take these conversation turns' contribution out of the knowledge graph."""
    tids = _turn_ids(texts)
    if not tids:
        return {"dropped": 0, "blanked": 0}
    from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore, KnowledgeGraphManifest
    store = store if store is not None else KnowledgeGraphStore()
    dropped = blanked = 0
    out = {}
    for name in ("entities", "relationships"):
        keep = []
        for obj in store.load(name):
            prov = obj.get("provenance") or {}
            convs = prov.get("conversations")
            if (obj.get("tier") != "B" or str(obj.get("id", "")).startswith("fact_")
                    or not isinstance(convs, list) or not tids & {str(c) for c in convs}):
                keep.append(obj)
                continue
            prov["conversations"] = [c for c in convs if str(c) not in tids]
            if not any(isinstance(v, list) and v for v in prov.values()):
                dropped += 1
                continue                       # only these turns supported it
            obj["description"] = ""            # what they said is no longer known
            blanked += 1
            keep.append(obj)
        out[name] = keep
    if dropped or blanked:
        ids = {e.get("id") for e in out["entities"]}
        out["relationships"] = [r for r in out["relationships"]
                                if r.get("source") in ids and r.get("target") in ids]
        store.save("entities", out["entities"])
        store.save("relationships", out["relationships"])
    try:
        manifest = KnowledgeGraphManifest(base_dir=store.base)
        for tid in tids:
            manifest.forget(f"conversation:{tid}")
        manifest.save()
    except Exception as e:
        _log.warning("graph manifest not updated: %s", e)
    return {"dropped": dropped, "blanked": blanked}


def after_supersession(texts: List[str], days: List[str], superseded: bool) -> dict:
    """See the module docstring. Never raises."""
    result = {"summaries": [], "graph": None}
    try:
        result["summaries"] = mark_summaries(days)
    except Exception as e:
        _log.warning("summaries not refreshed after a correction: %s", e)
    if superseded:
        try:
            result["graph"] = retract_from_graph(texts)
        except Exception as e:
            _log.warning("graph not updated after a correction: %s", e)
    return result
