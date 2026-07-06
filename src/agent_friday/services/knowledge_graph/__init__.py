"""Knowledge graph package — Friday's connected second brain.

Two-tier graph over the wiki, conversation memory, and cognitive memory
(docs/KNOWLEDGE_SYSTEM_SPEC.md):

  Tier A — structural: wiki pages as nodes, [[wikilinks]]/markdown-links/title
           mentions as edges, communities from link structure. No LLM, offline,
           cheap enough to rebuild on every wiki save.
  Tier B — semantic: GraphRAG entity/relationship/community-report extraction,
           routed through model_router + egress_gate, local-only by default.

The wiki markdown remains the source of truth; everything under
~/.friday/knowledge-graph/ is derived and may be deleted and rebuilt at any
time without data loss.
"""

from __future__ import annotations

import threading

from agent_friday.core import FRIDAY_DIR, _load_settings

KG_DIR = FRIDAY_DIR / "knowledge-graph"

# Defaults live here (not in core.DEFAULT_SETTINGS) so the knowledge graph is
# fully self-contained: settings.json only needs a "knowledge_graph" block when
# the user changes something.
KG_DEFAULT_SETTINGS = {
    "enabled": True,
    # "local_only"  — Tier B indexing uses local providers exclusively (default).
    # "gated_cloud" — TIER_1 content may use cloud models through the egress
    #                 gate; TIER_2/3 stays local-only regardless.
    "indexing_mode": "local_only",
    "power_indexer": "native",           # "native" | "microsoft" (opt-in)
    "index_sources": {
        "wiki": True,
        "conversations": True,
        "cognitive": True,
        "soul": True,
    },
    "mention_edges": True,               # Tier A implicit title-mention edges
    "community_mode": "auto",            # "auto" | "links" | "section"
    "nightly_reindex": True,
    "max_visible_nodes": 2000,
    "layout_seed": 1337,
}


def kg_settings() -> dict:
    """Effective knowledge_graph settings (user overlay on defaults)."""
    merged = {k: (dict(v) if isinstance(v, dict) else v)
              for k, v in KG_DEFAULT_SETTINGS.items()}
    try:
        user = (_load_settings() or {}).get("knowledge_graph") or {}
    except Exception:
        user = {}
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k].update(v)
        else:
            merged[k] = v
    return merged


# ── Dirty flag ────────────────────────────────────────────────
# Wiki saves mark the structural graph stale; the next /graph or /summary
# request (or an explicit reindex) rebuilds it. Cheap by design — Tier A on a
# few hundred pages is milliseconds.
_dirty_lock = threading.Lock()
_wiki_dirty = {"dirty": True, "reason": "startup"}


def mark_wiki_dirty(reason: str = "wiki_edit") -> None:
    with _dirty_lock:
        _wiki_dirty["dirty"] = True
        _wiki_dirty["reason"] = str(reason)[:200]


def consume_wiki_dirty() -> bool:
    """Return whether the graph was dirty, clearing the flag."""
    with _dirty_lock:
        was = _wiki_dirty["dirty"]
        _wiki_dirty["dirty"] = False
        return was


def peek_wiki_dirty() -> bool:
    with _dirty_lock:
        return _wiki_dirty["dirty"]
