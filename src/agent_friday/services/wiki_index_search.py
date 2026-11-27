"""Wiki search that starts from the knowledge graph's index (KG latency fix 1, 2026-11-26).

What was slow: `search_wiki` read and decrypted every wiki file in turn (6,377 files, 37 MB on the
reference machine) until it had five hits, matching the query as ONE exact phrase. A question
worded the way people speak ("the core philosophy of the FutureSpeak aesthetic") matches no
file word for word, so it read the whole wiki on every call, with no time limit on a cloud turn.
On the phone at 14:59 on 11-26 two wiki calls took about 41 s of a 54 s answer.

What this module gives the tool instead:

  * ``candidate_pages``: the pages whose knowledge-graph entity (title and description, the index
    /api/knowledge-graph/search already serves in a quarter of a second) shares the query's words,
    best first. Reading those few pages answers most questions without walking the wiki.
  * ``words`` / ``excerpt_for``: a page matches when it holds the exact phrase OR every content
    word of the query, so a spoken question finds the page that answers it.
  * ``SCAN_BUDGET_S``: the walk that still runs for what the index does not know stops after this
    many seconds whoever called it (a local seat's tighter budget still wins) and says it is partial.

Nothing here writes, and nothing leaves the PC: it reads the same files the tool always read.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

#: Seconds the fallback walk may take before it returns what it has, on any seat.
SCAN_BUDGET_S = 4.0

_STOP = frozenset("""
a about above after again all also am an and any are as at be been before being below between both but
by can could did do does doing down during each few for from further had has have having he her here
hers him his how i if in into is it its itself just me more most my myself no nor not now of off on once
only or other our ours out over own please same she should so some such tell than that the their them
then there these they this those through to too under until up very was we were what when where which
while who whom why will with would you your yours know knows say said says find show give get
""".split())

_WORD = re.compile(r"[a-z0-9][a-z0-9'\-]*")


def words(query: str) -> List[str]:
    """The query's content words, lower case, in order, without repeats or stop words."""
    out: List[str] = []
    for w in _WORD.findall(str(query or "").lower()):
        w = w.strip("'-")
        if len(w) < 3 or w in _STOP or w in out:
            continue
        out.append(w)
    return out


def excerpt_for(content: str, query: str, qwords: Optional[List[str]] = None) -> Optional[str]:
    """An excerpt around the match, or None when the page does not match.

    A match is the exact phrase, or every content word of the query somewhere on the page."""
    low = content.lower()
    q = str(query or "").strip().lower()
    idx = low.find(q) if q else -1
    if idx < 0:
        qwords = words(query) if qwords is None else qwords
        if not qwords:
            return None
        found = [low.find(w) for w in qwords]
        if any(i < 0 for i in found):
            return None
        idx = min(found)
    start = max(0, idx - 120)
    return content[start:min(len(content), idx + 280)].strip()


# ── the index: knowledge-graph entities, cached until their files change ─────────

_CACHE: Dict[str, object] = {"key": None, "rows": []}
_LOCK = threading.Lock()


def _entity_files() -> List[Path]:
    from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore
    base = KnowledgeGraphStore().base
    return [base / "entities.json", base / "entities.sensitive.json"]


def _load_entities() -> List[dict]:
    from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore
    return KnowledgeGraphStore().load("entities")


def _rows() -> List[Tuple[str, str, str]]:
    """(title lower, title+description lower, first wiki page) per entity that names a page."""
    try:
        key = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) if p.exists() else (str(p), 0, 0)
                    for p in _entity_files())
    except Exception:
        key = None
    with _LOCK:
        if key is not None and _CACHE["key"] == key:
            return _CACHE["rows"]  # type: ignore[return-value]
        rows = []
        for e in _load_entities() or []:
            pages = (e.get("provenance") or {}).get("wiki_pages") or []
            if not pages or not pages[0]:
                continue
            title = str(e.get("title") or "").lower()
            rows.append((title, title + " " + str(e.get("description") or "").lower(), str(pages[0])))
        _CACHE["key"], _CACHE["rows"] = key, rows
        return rows


def candidate_pages(query: str, limit: int = 8) -> List[str]:
    """Wiki pages (relative paths) the index says the query is about, best first.

    An entity counts when its title and description hold at least half the query's content words
    (all of them for a one- or two-word query); a word in the title counts double. Never raises."""
    qwords = words(query)
    if not qwords:
        return []
    need = len(qwords) if len(qwords) <= 2 else (len(qwords) + 1) // 2
    try:
        rows = _rows()
    except Exception:
        return []
    scored: Dict[str, float] = {}
    for title, hay, page in rows:
        hits = [w for w in qwords if w in hay]
        if len(hits) < need:
            continue
        score = len(hits) + sum(1 for w in hits if w in title)
        if score > scored.get(page, -1):
            scored[page] = score
    return [p for p, _ in sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))[:max(1, int(limit))]]


def reset_cache() -> None:
    with _LOCK:
        _CACHE["key"], _CACHE["rows"] = None, []
