"""Phase 4 — knowledge graph integration hooks.

Three touch points (spec §9 Phase 4):

  * knowledge_context_block — graph-aware always-on context: the structural
    query's should_read shortlist for the current message, injected into the
    system prompt by context_injection (zero LLM, microseconds).
  * ingest_fact — memory_dreaming / learning_loop post-step: a newly learned
    fact becomes a graph node immediately (LLM-free; Tier B enrichment picks
    it up on the next nightly pass) and ignites live in the 3D view.
  * run_nightly_reindex — scheduler job: Tier A rebuild + Tier B delta,
    honoring the local-only default and the nightly_reindex setting.
"""

from __future__ import annotations

import hashlib
import re
import time
from typing import Optional

from . import kg_settings, mark_wiki_dirty


# ── always-on context ─────────────────────────────────────────

def knowledge_context_block(message: str, max_items: int = 3) -> list[str]:
    """Graph pointers for the current message. Structural only — free."""
    msg = (message or "").strip()
    if len(msg) < 12 or not kg_settings().get("enabled", True):
        return []
    try:
        from . import structural_query
        result = structural_query.query(msg, max_should_read=max_items)
    except Exception:
        return []
    lines = []
    for c in (result.get("candidates") or [])[:max_items]:
        if c.get("summary"):
            lines.append(f"- {c['title']} ({c['page']}): {c['summary'][:160]}")
    should = result.get("should_read") or []
    if should:
        lines.append("Open with read_wiki for detail: " + ", ".join(should))
    if not lines:
        return []
    return ["[Knowledge graph — related pages]"] + lines


# ── live fact ingestion ───────────────────────────────────────

def ingest_fact(text: str, *, source_kind: str, source_key: str,
                category: str = "fact",
                sensitivity: Optional[int] = None) -> Optional[str]:
    """Write a discovered fact into the graph as a node, LLM-free.

    Returns the new entity id (or the existing one when the same fact was
    already ingested — idempotent by content hash). Emits a node_ignited
    event so the 3D view lights up live.
    """
    text = (text or "").strip()
    if len(text) < 12 or not kg_settings().get("enabled", True):
        return None
    if sensitivity is None:
        try:
            from agent_friday.services.sensitivity_classifier import classify, Tier
            sensitivity = int(classify(text, default=Tier.PRIVATE))
        except Exception:
            sensitivity = 2

    from .store import KnowledgeGraphStore
    store = KnowledgeGraphStore()
    eid = "fact_" + hashlib.sha1(text.lower().encode()).hexdigest()[:12]
    entities = store.load("entities")
    if any(e["id"] == eid for e in entities):
        return eid

    title = " ".join(text.split()[:8])
    prov_key = ("cognitive_keys" if source_kind == "cognitive"
                else "conversations" if source_kind == "conversation"
                else "wiki_pages")
    node = {
        "id": eid, "title": title, "type": category,
        "description": text[:400], "degree": 0, "frequency": 1,
        "level": 0, "tier": "B", "community": "B_fresh",
        "provenance": {prov_key: [source_key], "sensitivity": sensitivity,
                       "learned": source_kind},
    }

    # LLM-free weaving: link the fact to pages whose title appears in it.
    rels = []
    low = text.lower()
    for e in entities:
        if e.get("type") not in ("page", "soul"):
            continue
        t = (e.get("title") or "").lower()
        if len(t) >= 4 and re.search(r"(?<![\w-])" + re.escape(t) + r"(?![\w-])",
                                     low):
            node["degree"] += 1
            rels.append({
                "id": f"rel_{eid[5:]}_{hashlib.sha1(e['id'].encode()).hexdigest()[:8]}",
                "source": eid, "target": e["id"],
                "description": "learned about", "weight": 0.5, "tier": "B",
                "provenance": dict(node["provenance"]),
            })

    # Place near its first linked page (or origin) so it ignites somewhere
    # sensible; the next full layout pass will settle it.
    anchor = next((e for e in entities
                   if rels and e["id"] == rels[0]["target"]), None)
    jit = (int(hashlib.sha1(eid.encode()).hexdigest()[:4], 16) % 17) - 8
    node["x"] = (anchor or {}).get("x", 0) + jit
    node["y"] = (anchor or {}).get("y", 0) + 9
    node["z"] = (anchor or {}).get("z", 0) - jit

    entities.append(node)
    store.save("entities", entities)
    if rels:
        store.save("relationships", store.load("relationships") + rels)

    try:
        from agent_friday.routes.knowledge_graph import emit_kg_event
        emit_kg_event("node_ignited", {"id": eid, "title": title,
                                       "ts": time.time()})
    except Exception:
        pass
    return eid


# ── nightly reindex job ───────────────────────────────────────

def run_nightly_reindex() -> dict:
    """Scheduler entry: Tier A rebuild + Tier B delta (settings-gated)."""
    settings = kg_settings()
    if not settings.get("enabled", True) or not settings.get("nightly_reindex",
                                                             True):
        return {"skipped": True}
    from . import wiki_graph
    mark_wiki_dirty("nightly")
    info_a = wiki_graph.rebuild_tier_a()
    info_b = {}
    try:
        from . import indexer
        info_b = indexer.reindex_tier_b(mode="delta")
    except Exception as e:
        info_b = {"error": str(e)}
    return {"tier_a": info_a, "tier_b": info_b}
