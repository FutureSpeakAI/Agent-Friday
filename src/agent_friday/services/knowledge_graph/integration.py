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
    """Graph pointers for the current message. Structural only — free.

    This block is folded into EVERY system prompt by context_injection's
    build_injected_context(), for whatever provider is handling the turn --
    cloud included -- with no per-provider tier gating downstream (unlike
    the deliberately tier-gated _build_context_prompt() path two lines away
    in model_router._get_friday_system_prompt(), which redacts TIER_2/3
    content per provider). A candidate's `summary` is a real plaintext
    excerpt of the page (wiki_graph._first_paragraph, taken from the
    decrypted body whenever the vault is unlocked), so a TIER_3 page --
    one living in a user-designated encrypted wiki section -- must never
    reach this always-on, ambient path. Filtered here rather than in
    structural_query itself,
    since that module has other, differently-gated callers (e.g. the
    explicit read_wiki tool).
    """
    msg = (message or "").strip()
    if len(msg) < 12 or not kg_settings().get("enabled", True):
        return []
    try:
        from . import structural_query
        result = structural_query.query(msg, max_should_read=max_items)
    except Exception:
        return []
    try:
        from agent_friday.services.wiki_engine import _wiki_encrypted_sections
        encrypted_sections = _wiki_encrypted_sections()
    except Exception:
        encrypted_sections = set()
    lines = []
    # _wiki_encrypted_sections() always returns LOWERCASED names (it lower()s
    # every configured section on the way in) -- but a candidate's `section`
    # here is the raw, case-preserved directory name from wiki_graph.py's
    # indexing (`section = rel.split("/")[0]`, never lowercased there). A
    # section literally named "Private" (any case other than what the user
    # typed into settings) compared unlowered against this set never
    # matches, silently defeating this whole filter for that section.
    # wiki_engine.py's own _wiki_path_is_sensitive() already lower()s its
    # side of this exact comparison; mirrored here.
    safe_candidates = [c for c in (result.get("candidates") or [])
                       if str(c.get("section") or "").strip().lower()
                       not in encrypted_sections]
    for c in safe_candidates[:max_items]:
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
    """Scheduler entry: Tier A rebuild + Tier B delta (settings-gated).

    Goes through the SAME concurrency guards as the manual
    ``/api/knowledge-graph/reindex`` HTTP route (routes/knowledge_graph.py's
    ``_rebuild_lock`` for Tier A, ``_TIER_B_STATE`` for Tier B). Calling
    ``wiki_graph.rebuild_tier_a()`` / ``indexer.reindex_tier_b()`` directly
    would let a user clicking "Reindex now" in the Knowledge Graph panel
    while the nightly schedule (or its own earlier Run Now) is mid-run start
    two fully concurrent, uncoordinated rebuilds of the same on-disk KG
    store. Tier A blocks on the same lock the manual route blocks on
    (serializes rather than racing); Tier B checks the same running flag the
    manual route checks and skips this pass rather than starting a second
    concurrent index when one is already in flight.
    """
    settings = kg_settings()
    if not settings.get("enabled", True) or not settings.get("nightly_reindex",
                                                             True):
        return {"skipped": True}
    from . import wiki_graph
    from agent_friday.routes.knowledge_graph import _rebuild_lock, _TIER_B_STATE
    mark_wiki_dirty("nightly")
    with _rebuild_lock:
        info_a = wiki_graph.rebuild_tier_a()
    if _TIER_B_STATE.get("running"):
        info_b = {"skipped": "tier_b_already_running"}
    else:
        _TIER_B_STATE["running"] = True
        _TIER_B_STATE["started_at"] = time.time()
        try:
            from . import indexer
            info_b = indexer.reindex_tier_b(mode="delta")
            _TIER_B_STATE["last"] = info_b
        except Exception as e:
            info_b = {"error": str(e)}
            _TIER_B_STATE["last"] = info_b
        finally:
            _TIER_B_STATE["running"] = False
    return {"tier_a": info_a, "tier_b": info_b}
