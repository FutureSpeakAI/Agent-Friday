"""Structural (no-LLM) query over the Tier A graph.

Ported from obsidian-wiki's ``graphrag.py`` (MIT): classify the question,
rank candidate pages from index metadata, BFS multi-hop paths, and return a
``should_read`` shortlist so the agent opens 2-3 pages instead of 10+.

Operates on the in-memory index produced by ``wiki_graph.build_wiki_index``
(never re-reads page bodies), so a query is microseconds and works offline.
"""

from __future__ import annotations

import re
from collections import deque
from typing import Any

from .wiki_graph import build_wiki_index, _slug


def _resolve_key(index: dict[str, dict], term: str) -> str:
    """Map a human term to a page key (path key, stem, or best candidate)."""
    t = _slug(term)
    if t in index:
        return t
    for key, entry in sorted(index.items()):
        if entry.get("stem") == t:
            return key
    cands = rank_candidates(index, [term], top_n=1)
    return cands[0]["slug"] if cands else t


# ── scoring / ranking ─────────────────────────────────────────

def _score(slug: str, entry: dict, terms: list[str]) -> float:
    score = 0.0
    title_lower = entry["title"].lower()
    summary_lower = entry["summary"].lower()
    tags_lower = [t.lower() for t in entry["tags"]]
    section_lower = (entry.get("section") or "").lower()
    stem = entry.get("stem", slug)
    for term in terms:
        t = term.lower()
        if t == slug or t == stem or t == title_lower or _slug(t) == stem:
            score += 10.0
        elif t in title_lower:
            score += 6.0
        elif any(t in tag for tag in tags_lower):
            score += 4.0
        elif t and t in section_lower:
            score += 3.0
        elif t in summary_lower:
            score += 2.0

    if score > 0:
        # Degree bonus only when a term matched — prevents hub noise.
        degree = len(entry["in_links"]) + len(entry["out_links"])
        score += min(degree * 0.1, 2.0)
    return score


def rank_candidates(index: dict[str, dict], terms: list[str],
                    top_n: int = 8) -> list[dict]:
    scored = [
        {
            "slug": slug,
            "page": entry["path"],
            "title": entry["title"],
            "score": _score(slug, entry, terms),
            "summary": entry["summary"],
            "section": entry.get("section", ""),
            "in_degree": len(entry["in_links"]),
        }
        for slug, entry in index.items()
    ]
    scored.sort(key=lambda x: (-x["score"], -x["in_degree"], x["slug"]))
    return [c for c in scored[:top_n] if c["score"] > 0]


# ── multi-hop path (BFS over undirected link graph) ───────────

def find_path(index: dict[str, dict], source_slug: str, target_slug: str,
              max_depth: int = 4) -> list[str] | None:
    if source_slug not in index or target_slug not in index:
        return None
    if source_slug == target_slug:
        return [source_slug]

    def neighbours(slug: str) -> list[str]:
        entry = index[slug]
        outs = [t for t, _k in entry["out_links"]]
        return outs + entry["in_links"]

    queue: deque[tuple[str, list[str]]] = deque([(source_slug, [source_slug])])
    visited = {source_slug}
    while queue:
        node, path = queue.popleft()
        if len(path) > max_depth:
            continue
        for nb in neighbours(node):
            if nb in visited:
                continue
            visited.add(nb)
            new_path = path + [nb]
            if nb == target_slug:
                return new_path
            queue.append((nb, new_path))
    return None


# ── query classification ──────────────────────────────────────

_PATH_PATTERNS = re.compile(
    r"how (?:is|are|does) (.+?) (?:connected|related|linked) to (.+?)[\?]?$"
    r"|trace (?:the )?(?:chain|path) from (.+?) to (.+?)[\?]?$"
    r"|what connects (.+?) (?:to|and) (.+?)[\?]?$",
    re.IGNORECASE,
)
_GAP_PATTERNS = re.compile(
    r"what (?:do|don'?t) I (?:not )?know about|what.?s missing|what gaps|open questions",
    re.IGNORECASE,
)
_LIST_PATTERNS = re.compile(
    r"(?:list|show|find|give me) (?:all|every|pages about)",
    re.IGNORECASE,
)
_THEMATIC_PATTERNS = re.compile(
    r"big picture|overall|main themes?|summar(?:y|ize) (?:of )?(?:my|the) "
    r"(?:knowledge|wiki|notes)|what (?:areas|topics) ",
    re.IGNORECASE,
)

_STOP = {"what", "the", "a", "an", "is", "are", "how", "does", "do", "in",
         "of", "to", "for", "and", "or", "my", "me", "about"}


def classify_query(question: str) -> tuple[str, list[str]]:
    """Return (answer_type, terms).

    answer_type: "path" | "gap" | "list" | "thematic" | "direct".
    "thematic" is a Tier B hint (global search over community reports); the
    structural fallback answers it with community labels.
    """
    m = _PATH_PATTERNS.search(question)
    if m:
        groups = [g for g in m.groups() if g]
        terms = groups[:2] if len(groups) >= 2 else [question]
        return "path", terms

    if _GAP_PATTERNS.search(question):
        terms = re.sub(
            r"what (?:do|don't) I (?:not )?know about|what.?s missing", "",
            question, flags=re.IGNORECASE).strip().split()
        return "gap", [t for t in terms if t]

    if _THEMATIC_PATTERNS.search(question):
        return "thematic", _terms(question)

    if _LIST_PATTERNS.search(question):
        terms = re.sub(r"(?:list|show|find|give me) (?:all|every|pages about)",
                       "", question, flags=re.IGNORECASE).strip().split()
        return "list", [t for t in terms if t]

    return "direct", _terms(question)


def _terms(question: str) -> list[str]:
    return [w.strip("?,.'\"") for w in question.split()
            if w.lower().strip("?,.'\"") not in _STOP and len(w) > 2]


# ── main entry point ──────────────────────────────────────────

def query(question: str, index: dict[str, dict] | None = None, *,
          top_n: int = 8, max_should_read: int = 3) -> dict[str, Any]:
    """Answer a question from graph structure alone — zero LLM calls."""
    if index is None:
        index = build_wiki_index()
    if not index:
        return {"answer_type": "direct", "candidates": [], "path": [],
                "god_nodes_relevant": [], "should_read": [],
                "index_only": True, "note": "Wiki appears empty.",
                "stats": {"indexed_pages": 0, "query_terms": []}}

    answer_type, terms = classify_query(question)

    degree = {s: len(e["in_links"]) + len(e["out_links"])
              for s, e in index.items()}
    god_slugs = sorted(degree, key=lambda s: (-degree[s], s))[:10]
    term_set = {t.lower() for t in terms}
    god_relevant = [
        index[s]["path"] for s in god_slugs
        if any(t in index[s]["title"].lower()
               or t in " ".join(index[s]["tags"]).lower() for t in term_set)
    ][:5]

    path_result: list[str] = []
    if answer_type == "path" and len(terms) >= 2:
        src_slug = _resolve_key(index, terms[0])
        tgt_slug = _resolve_key(index, terms[1])
        raw_path = find_path(index, src_slug, tgt_slug)
        if raw_path:
            path_result = [index[s]["path"] for s in raw_path if s in index]

    candidates = rank_candidates(index, terms, top_n=top_n)

    top_candidate = candidates[0] if candidates else None
    index_only = bool(top_candidate and top_candidate["score"] >= 10.0
                      and top_candidate["summary"])

    should_read = [c["page"] for c in candidates[:max_should_read]
                   if not index_only]
    if path_result and not index_only:
        for p in path_result:
            if p not in should_read:
                should_read.append(p)
        should_read = should_read[:max_should_read + 2]

    return {
        "answer_type": answer_type,
        "candidates": [
            {"page": c["page"], "title": c["title"],
             "score": round(c["score"], 2), "summary": c["summary"],
             "section": c["section"]}
            for c in candidates
        ],
        "path": path_result,
        "god_nodes_relevant": god_relevant,
        "should_read": should_read,
        "index_only": index_only,
        "stats": {"indexed_pages": len(index), "query_terms": terms},
    }
