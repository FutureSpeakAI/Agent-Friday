"""Knowledge graph package — Friday's connected second brain.

Two-tier graph over the wiki, conversation memory, and cognitive memory
(docs/design/implemented/knowledge-system-spec.md):

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
#: Legacy indexing_mode values, mapped to the current two-value choice.
#: "gated_cloud" was a short-lived per-TIER compromise (TIER_1 could ride
#: the cloud route, TIER_2/3 stayed pinned local no matter what the user
#: picked). The maintainer's ruling: the system does what the person
#: picked rather than deciding for them — a strict per-user choice,
#: "local" or "cloud", with the egress gate (not this module) deciding
#: what content is safe to send. A settings.json written under the old
#: scheme still reads correctly rather than silently reverting to the
#: default.
_LEGACY_INDEXING_MODES = {"local_only": "local", "gated_cloud": "cloud"}

KG_DEFAULT_SETTINGS = {
    "enabled": True,
    # "local"  — Tier B indexing always uses an installed local model
    #            (default). Nothing ever reaches the network.
    # "cloud"  — Tier B always routes through the egress-gated cloud
    #            default, same rules as every other cloud call in the app.
    "indexing_mode": "local",
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
    mode = merged.get("indexing_mode")
    if mode in _LEGACY_INDEXING_MODES:
        merged["indexing_mode"] = _LEGACY_INDEXING_MODES[mode]
    elif mode not in ("local", "cloud"):
        merged["indexing_mode"] = "local"   # unrecognized -> the private default
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
