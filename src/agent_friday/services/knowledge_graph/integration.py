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

#: Provenance kinds a fact may carry. "research" is a finding from the public
#: web that the user reviewed and accepted in the setup chat; it records the
#: URLs it came from, so the graph can always say where it learned something.
SOURCE_KINDS = ("cognitive", "conversation", "wiki", "research")


def ingest_fact(text: str, *, source_kind: str, source_key: str,
                category: str = "fact",
                sensitivity: Optional[int] = None,
                sources: Optional[list] = None) -> Optional[str]:
    """Write a discovered fact into the graph as a node, LLM-free.

    Returns the new entity id (or the existing one when the same fact was
    already ingested — idempotent by content hash). Emits a node_ignited
    event so the 3D view lights up live.

    `sources` is required for source_kind="research": a list of
    {"url", "fetched_at"} naming the public pages the fact came from. A
    research fact with no source is refused rather than stored unattributed.
    """
    text = (text or "").strip()
    if len(text) < 12 or not kg_settings().get("enabled", True):
        return None
    # A fact node the owner deleted from the graph is not ignited again
    # (services/tombstones, NS-30.20-1; the store refuses it as well). It is
    # named by its fact id, a hash of the WHOLE text: another fact that only
    # opens with the same words is a different fact and is not held back.
    eid = "fact_" + hashlib.sha1(text.lower().encode()).hexdigest()[:12]
    try:
        from agent_friday.services import tombstones as _tomb
        if _tomb.is_tombstoned("kg_entity", {"id": eid}):
            return None
    except Exception as e:
        import logging
        logging.getLogger("friday.tombstones").warning(
            "knowledge graph: the fact tombstone check failed (fail open): %s: %s", type(e).__name__, e)
    research_sources = []
    if source_kind == "research":
        for s in sources or []:
            url = str((s or {}).get("url") or "").strip()
            if url.startswith(("http://", "https://")):
                research_sources.append({"url": url[:500],
                                         "fetched_at": (s or {}).get("fetched_at")})
        if not research_sources:
            return None
    if sensitivity is None:
        try:
            from agent_friday.services.sensitivity_classifier import classify, Tier
            sensitivity = int(classify(text, default=Tier.PRIVATE))
        except Exception:
            sensitivity = 2

    from .store import KnowledgeGraphStore
    store = KnowledgeGraphStore()
    entities = store.load("entities")
    if any(e["id"] == eid for e in entities):
        return eid

    title = " ".join(text.split()[:8])
    prov_key = ("cognitive_keys" if source_kind == "cognitive"
                else "conversations" if source_kind == "conversation"
                else "research" if source_kind == "research"
                else "wiki_pages")
    node = {
        "id": eid, "title": title, "type": category,
        "description": text[:400], "degree": 0, "frequency": 1,
        "level": 0, "tier": "B", "community": "B_fresh",
        "provenance": {prov_key: [source_key], "sensitivity": sensitivity,
                       "learned": source_kind},
    }
    if research_sources:
        node["provenance"]["sources"] = research_sources

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
    # A cube settles into the lattice's core (avatar-visual-genome.md §13).
    try:
        from agent_friday.services import presence as _presence
        _presence.emit("memory_saved", "once")
    except Exception:
        pass
    return eid


# ── the owner deletes an entity (services/tombstones, NS-30.20-1) ──

def owner_delete_entity(node_id: str, store=None) -> dict:
    """The owner deletes one graph entity, and it stays deleted.

    The graph is derived: removing the record alone is undone by the next
    Tier A/Tier B pass, which re-reads the same pages and turns. So, as
    forget-person does, the tombstone is written FIRST (a keyed hash of what
    the entity IS, never its title: its type and title, or a fact node's own
    fact id; services/tombstones.entity_ident), then that entity, any
    duplicate of exactly the same thing (same type and title), and their edges
    and community memberships go, and the community reports lose its title
    (whole words, forget-person's prose scrub). Nothing else is touched: a
    page "Mercury" stays when the person "Mercury" goes, and so does every
    other fact that opens with the same words. Every rebuild path (wiki_graph,
    indexer, a live fact, the store itself) skips it. The pages and turns it
    came from are the owner's own and are untouched. Never raises.

    Returns {"ok", "deleted": [{"id", "title", "type"}], "entities",
    "relationships", "reports_scrubbed", "tombstoned"}; when the tombstone
    cannot be written nothing is deleted (it would only come back) and the
    result says so ("tombstoned": False, "warning").
    """
    try:
        from .store import KnowledgeGraphStore
        from agent_friday.services import tombstones
        store = store or KnowledgeGraphStore()
        entities = store.load("entities")
        node = next((e for e in entities if e.get("id") == str(node_id)), None)
        if node is None:
            return {"ok": False, "error": "no such entity"}
        ident = tombstones.entity_ident(node)
        t = tombstones.add("kg_entity", node)
        if not t.get("ok"):
            return {"ok": False, "tombstoned": False,
                    "warning": ("the deletion could not be recorded, so the next rebuild would make this "
                                "entity again; nothing was deleted"),
                    "error": "not deleted: %s" % t.get("error")}
        doomed = [e for e in entities if e.get("id") == str(node_id)
                  or (ident and tombstones.entity_ident(e) == ident)]
        gone = {e.get("id") for e in doomed}
        kept = [e for e in entities if e.get("id") not in gone]
        r1 = store.save("entities", kept)
        rels = store.load("relationships")
        rels_kept = [r for r in rels if r.get("source") not in gone and r.get("target") not in gone]
        r2 = store.save("relationships", rels_kept) if len(rels_kept) != len(rels) else {}
        comms = store.load("communities")
        changed = False
        for c in comms:
            ids = c.get("entity_ids")
            if isinstance(ids, list) and any(i in gone for i in ids):
                c["entity_ids"] = [i for i in ids if i not in gone]
                c["size"] = len(c["entity_ids"])
                changed = True
        if changed:
            store.save("communities", [c for c in comms if c.get("size", 1)])
        # Community reports are LLM prose: the title is in the sentence, so
        # it is scrubbed (whole words, forget-person's rule) and the cluster
        # is kept. A fact node's title is a sentence prefix, not a name: skipped.
        r3, scrubbed_n = {}, 0
        titles = sorted({str(e.get("title") or "").strip() for e in doomed
                         if not str(e.get("id") or "").startswith("fact_")} - {""})
        if titles:
            try:
                from agent_friday.services.forget_person import _scrub_name_from_text
                reports = store.load("community_reports")
                if reports:
                    scrubbed = _scrub_name_from_text(reports, titles)
                    if scrubbed != reports:
                        scrubbed_n = sum(1 for a, b in zip(reports, scrubbed) if a != b)
                        r3 = store.save("community_reports", scrubbed)
            except Exception as e:
                r3 = {"refused": "the community reports could not be scrubbed (%s)" % type(e).__name__}
        refused = [r.get("refused") for r in (r1, r2, r3) if isinstance(r, dict) and r.get("refused")]
        try:
            from agent_friday.routes.knowledge_graph import emit_kg_event
            emit_kg_event("node_removed", {"ids": sorted(gone), "ts": time.time()})
        except Exception:
            pass
        out = {"ok": not refused, "entities": len(gone), "relationships": len(rels) - len(rels_kept),
               "reports_scrubbed": scrubbed_n, "tombstoned": True,
               "deleted": [{"id": e.get("id"), "title": e.get("title"), "type": e.get("type")}
                           for e in doomed]}
        if refused:
            out["error"] = ("the deletion is recorded and every rebuild skips it, but the graph file "
                            "could not be written now (%s)" % refused[0])
        return out
    except Exception as e:
        return {"ok": False, "error": "not deleted (%s)" % type(e).__name__}


def owner_restore_entity(title: str = "", type: Optional[str] = None,
                         node_id: Optional[str] = None) -> dict:
    """The owner's undo: lift the tombstone; the next rebuild derives the
    entity again from its sources (nothing deleted is restored here).

    An entity is named as its delete named it: a fact node by its id, any
    other by type and title (the delete's "deleted" list carries both). With
    a title alone, the usual entity types are tried."""
    from agent_friday.services import tombstones
    nid = str(node_id or "").strip()
    if nid.startswith("fact_"):
        candidates = [{"id": nid}]
    elif type:
        candidates = [{"type": type, "title": title}]
    else:
        candidates = [{"type": t, "title": title} for t in tombstones.RESTORE_TYPES]
    removed, r = 0, {"ok": True, "kind": "kg_entity", "removed": 0}
    for c in candidates:
        r = tombstones.remove("kg_entity", c)
        if not r.get("ok"):
            return r
        removed += int(r.get("removed") or 0)
    r = {**r, "removed": removed}
    if removed:
        try:
            mark_wiki_dirty("entity restored")
        except Exception:
            pass
    return r


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
