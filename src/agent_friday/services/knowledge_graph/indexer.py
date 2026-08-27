"""Tier B — GraphRAG semantic indexing (spec §6.2, §5.4).

Chunks the corpus (wiki bodies, conversation turns, cognitive-memory facts,
SOUL.md), extracts entities + relationships with the Microsoft-GraphRAG
prompt pipeline (vendored verbatim from graphrag-workbench under prompts/),
merges duplicate entities, detects communities (reusing Tier A's detector),
writes LLM community reports, and embeds entity text locally.

Sovereignty rules (non-negotiable, spec §5.4):
  * The indexer NEVER opens a socket. Every LLM call goes through
    model_router._generate_text, which seals cloud payloads via
    egress_gate.seal_outbound.
  * classify-before-extract: each chunk's sensitivity is resolved BEFORE any
    LLM call. TIER_3 chunks are indexed with a local provider or skipped —
    never sent to cloud, in any mode.
  * indexing_mode "local_only" (the default) pins every call to the local
    model. "gated_cloud" lets TIER_1 chunks use the routed cloud model
    (still sealed); TIER_2/3 stay local.
  * egress_gate.gate_operational() == False disables cloud indexing outright.
  * Derived records inherit their source's sensitivity, so the store
    encrypts anything derived from TIER_2/3 sources at rest.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from agent_friday.core import WIKI_DIR, SOUL_FILE, _load_settings

from . import kg_settings
from . import graph_analysis
from .store import (KnowledgeGraphStore, KnowledgeGraphManifest, canonical)
from .wiki_graph import list_wiki_pages, _read, _page_sensitivity, _page_key

PROMPT_DIR = Path(__file__).parent / "prompts"

TUPLE_DELIM = "<|>"
RECORD_DELIM = "##"
COMPLETION_DELIM = "<|COMPLETE|>"
ENTITY_TYPES = "person,organization,project,tool,concept,event,place"

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 100
MAX_REPORT_COMMUNITIES = 24        # cap LLM cost per index pass


def _prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.txt").read_text(encoding="utf-8")


def _register_trusted_prompts() -> None:
    """Register the prompt templates (Friday-authored constants) with the
    egress gate so scaffolding paragraphs don't trip the classifier. User
    data is interpolated at call time and is still gated span-wise."""
    try:
        from agent_friday.services.egress_gate import register_trusted_text
        for f in PROMPT_DIR.glob("*.txt"):
            register_trusted_text(f.read_text(encoding="utf-8"))
    except Exception:
        pass


_register_trusted_prompts()


# ═══════════════════════════════════════════════════════════════
#  Corpus assembly
# ═══════════════════════════════════════════════════════════════

def _chunk_text(text: str, size: int = CHUNK_SIZE,
                overlap: int = CHUNK_OVERLAP) -> list[str]:
    if len(text) <= size:
        return [text] if text.strip() else []
    out, start = [], 0
    while start < len(text):
        out.append(text[start:start + size])
        start += size - overlap
    return out


def gather_chunks(sources: Optional[dict] = None) -> list[dict]:
    """Corpus → chunk dicts: {id, text, sensitivity, provenance, source_path}.

    Sensitivity resolution happens HERE — before any LLM ever sees the text.
    """
    settings = kg_settings()
    src_cfg = sources or settings["index_sources"]
    chunks: list[dict] = []

    if src_cfg.get("wiki", True):
        for page in list_wiki_pages():
            rel = str(page.relative_to(WIKI_DIR)).replace("\\", "/")
            text = _read(page)
            if not text or text.startswith("[vault-encrypted file"):
                continue
            sens = _page_sensitivity(rel)
            for i, part in enumerate(_chunk_text(text)):
                chunks.append({
                    "id": f"wiki:{_page_key(rel)}#{i}",
                    "text": part,
                    "sensitivity": sens,
                    "source_path": str(page),
                    "provenance": {"wiki_pages": [rel], "sensitivity": sens},
                })

    if src_cfg.get("soul", True) and SOUL_FILE.exists():
        try:
            soul = SOUL_FILE.read_text(encoding="utf-8", errors="replace")
        except OSError:
            soul = ""
        for i, part in enumerate(_chunk_text(soul)):
            chunks.append({
                "id": f"soul:#{i}", "text": part, "sensitivity": 1,
                "source_path": str(SOUL_FILE),
                "provenance": {"wiki_pages": ["SOUL.md"], "sensitivity": 1},
            })

    if src_cfg.get("cognitive", True):
        for c in _cognitive_chunks():
            chunks.append(c)

    if src_cfg.get("conversations", True):
        for c in _conversation_chunks():
            chunks.append(c)

    return chunks


def _classify_free_text(text: str) -> int:
    """Sensitivity for non-wiki text. Falls back to TIER_2 (private) when the
    classifier is unavailable — memories default to not-for-cloud."""
    try:
        from agent_friday.services.sensitivity_classifier import classify, Tier
        return int(classify(text, default=Tier.PRIVATE))
    except Exception:
        return 2


def _cognitive_chunks() -> Iterable[dict]:
    try:
        from agent_friday.cognitive_memory import CognitiveMemory
        mem = CognitiveMemory()
        mem_dir = Path(getattr(mem, "memory_dir"))
    except Exception:
        return []
    out = []
    if not mem_dir.exists():
        return []
    for f in sorted(mem_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("quarantined"):
            continue
        content = str(data.get("content") or "")
        if not content.strip():
            continue
        sens = _classify_free_text(content)
        out.append({
            "id": f"cog:{f.stem}", "text": content, "sensitivity": sens,
            "source_path": str(f),
            "provenance": {"cognitive_keys": [f.stem], "sensitivity": sens},
        })
    return out


def _conversation_chunks(limit: int = 400) -> Iterable[dict]:
    try:
        from agent_friday.conversation_memory import ConversationMemory
        cm = ConversationMemory()
        if not cm.available():
            return []
        turns = cm.recent_turns(limit=limit)
    except Exception:
        return []
    out = []
    for t in turns or []:
        content = str(t.get("content") or "")
        if len(content.strip()) < 40:      # skip trivia
            continue
        sens = _classify_free_text(content)
        tid = t.get("turn_id") or hashlib.sha1(content.encode()).hexdigest()[:10]
        out.append({
            "id": f"conv:{tid}", "text": content[:CHUNK_SIZE],
            "sensitivity": sens, "source_path": f"conversation:{tid}",
            "provenance": {"conversations": [str(tid)], "sensitivity": sens},
        })
    return out


# ═══════════════════════════════════════════════════════════════
#  LLM routing (mode + sensitivity → model choice)
# ═══════════════════════════════════════════════════════════════

class CloudIndexingDisabled(RuntimeError):
    pass


def _local_model() -> str:
    """The local model to index with, falling back to model_plan's floor.

    The fallback was `gemma3:4b` in both branches — defect H3 again. Indexing
    does not itself call tools, so this one was harmless in practice, which is
    exactly why it survived: a wrong default that never visibly breaks is the
    kind that is still there years later, waiting to be copied somewhere it
    matters. Derived from the ladder now, like every other default.
    """
    from agent_friday.services.model_plan import FLOOR_MODEL
    try:
        mr = (_load_settings() or {}).get("model_routing") or {}
        return mr.get("local_model") or FLOOR_MODEL
    except Exception:
        return FLOOR_MODEL


def _resolve_model(sensitivity: int, mode: str) -> tuple[Optional[str], bool]:
    """Return (model, is_local_pin) for a chunk.

    local_only  → always the local model.
    gated_cloud → TIER_1 rides the routed default (sealed by the egress
                  gate downstream); TIER_2/3 pinned local. Cloud requires a
                  healthy gate.
    """
    if mode == "gated_cloud" and sensitivity <= 1:
        try:
            from agent_friday.services.egress_gate import gate_operational
            if not gate_operational():
                raise CloudIndexingDisabled(
                    "egress gate self-test failed — cloud indexing refused")
        except CloudIndexingDisabled:
            raise
        except Exception:
            raise CloudIndexingDisabled("egress gate unavailable")
        return None, False                 # routed default (cloud allowed)
    return _local_model(), True


def _llm(messages, system: Optional[str], sensitivity: int, mode: str,
         orb_label: Optional[str] = None) -> str:
    """Single LLM entry point for the whole indexer (spec §5.4)."""
    model, _pinned = _resolve_model(sensitivity, mode)
    from agent_friday.services.model_router import _generate_text
    return _generate_text(messages, system=system, model=model,
                          max_tokens=4096, workspace="research",
                          orb_label=orb_label)


# ═══════════════════════════════════════════════════════════════
#  Extraction parsing
# ═══════════════════════════════════════════════════════════════

_ENT_RE = re.compile(r'\("entity"\s*' + re.escape(TUPLE_DELIM)
                     + r'(.*?)\)', re.DOTALL)
_REL_RE = re.compile(r'\("relationship"\s*' + re.escape(TUPLE_DELIM)
                     + r'(.*?)\)', re.DOTALL)


def parse_extraction(raw: str) -> tuple[list[dict], list[dict]]:
    """Parse the tuple-format output of the extract_graph prompt."""
    ents, rels = [], []
    for m in _ENT_RE.finditer(raw or ""):
        parts = [p.strip() for p in m.group(1).split(TUPLE_DELIM)]
        if len(parts) >= 3 and parts[0]:
            # Local models decorate types ("<tool>", "TYPE: person") — keep
            # letters only so downstream color/filter logic stays clean.
            etype = re.sub(r"[^a-z]", "", parts[1].strip().lower())
            ents.append({"title": parts[0].strip().upper(),
                         "type": etype or "concept",
                         "description": parts[2].strip()})
    for m in _REL_RE.finditer(raw or ""):
        parts = [p.strip() for p in m.group(1).split(TUPLE_DELIM)]
        if len(parts) >= 3 and parts[0] and parts[1]:
            try:
                weight = float(parts[3]) / 10.0 if len(parts) > 3 else 0.5
            except ValueError:
                weight = 0.5
            rels.append({"source": parts[0].strip().upper(),
                         "target": parts[1].strip().upper(),
                         "description": parts[2].strip(),
                         "weight": max(0.1, min(weight, 1.0))})
    return ents, rels


def _ent_id(title: str) -> str:
    return "ent_" + hashlib.sha1(title.strip().upper().encode()).hexdigest()[:12]


# ═══════════════════════════════════════════════════════════════
#  Index pass
# ═══════════════════════════════════════════════════════════════

def reindex_tier_b(store: Optional[KnowledgeGraphStore] = None,
                   mode: str = "delta",
                   progress: Optional[Callable[[str], None]] = None,
                   llm=None) -> dict[str, Any]:
    """Run the Tier B semantic index pass.

    mode "delta" re-extracts only chunks whose source changed since the
    manifest entry; "full" re-extracts everything. ``llm`` is injectable for
    tests; defaults to the gated ``_llm``.
    """
    t0 = time.time()
    store = store or KnowledgeGraphStore()
    settings = kg_settings()
    indexing_mode = str(settings.get("indexing_mode", "local_only"))
    call = llm or _llm
    say = progress or (lambda msg: None)

    chunks = gather_chunks()
    say(f"corpus: {len(chunks)} chunks")

    manifest = KnowledgeGraphManifest(base_dir=store.base)
    if mode == "delta":
        file_paths = sorted({c["source_path"] for c in chunks
                             if not c["source_path"].startswith("conversation:")})
        d = manifest.delta(file_paths)
        stale = set(d["new"]) | set(d["changed"])
        todo = [c for c in chunks
                if (c["source_path"].startswith("conversation:")
                    and c["source_path"] not in manifest.sources)  # turns are immutable
                or (not c["source_path"].startswith("conversation:")
                    and canonical(c["source_path"]) in stale)]
    else:
        todo = list(chunks)
    say(f"to extract: {len(todo)} chunks (mode={mode})")

    # ── extract ──────────────────────────────────────────────
    extract_tpl = _prompt("extract_graph")
    entities: dict[str, dict] = {e["id"]: e for e in store.load("entities")
                                 if e.get("tier") == "B"} if mode == "delta" else {}
    relationships: dict[str, dict] = {r["id"]: r for r in
                                      store.load("relationships")
                                      if r.get("tier") == "B"} if mode == "delta" else {}
    skipped_tier3 = 0
    extracted = 0

    for chunk in todo:
        sens = chunk["sensitivity"]
        try:
            model, pinned = _resolve_model(sens, indexing_mode)
        except CloudIndexingDisabled:
            if sens <= 1:
                # cloud refused → degrade the whole pass to local
                indexing_mode = "local_only"
                model, pinned = _resolve_model(sens, indexing_mode)
            else:
                skipped_tier3 += 1
                continue
        prompt = (extract_tpl
                  .replace("{entity_types}", ENTITY_TYPES)
                  .replace("{tuple_delimiter}", TUPLE_DELIM)
                  .replace("{record_delimiter}", RECORD_DELIM)
                  .replace("{completion_delimiter}", COMPLETION_DELIM)
                  .replace("{input_text}", chunk["text"]))
        try:
            raw = call([{"role": "user", "content": prompt}], None, sens,
                       indexing_mode, orb_label="🧠 indexing knowledge")
        except Exception as e:
            say(f"extract failed for {chunk['id']}: {e}")
            continue
        ents, rels = parse_extraction(raw)
        extracted += 1
        for e in ents:
            eid = _ent_id(e["title"])
            cur = entities.get(eid)
            if cur is None:
                entities[eid] = {
                    "id": eid, "title": e["title"].title(),
                    "type": e["type"], "description": e["description"],
                    "descriptions": [e["description"]],
                    "degree": 0, "frequency": 1, "level": 0, "tier": "B",
                    "provenance": dict(chunk["provenance"]),
                }
            else:
                cur["frequency"] += 1
                cur.setdefault("descriptions", [cur.get("description", "")])
                if e["description"] not in cur["descriptions"]:
                    cur["descriptions"].append(e["description"])
                prov = cur["provenance"]
                for k, v in chunk["provenance"].items():
                    if k == "sensitivity":
                        prov["sensitivity"] = max(prov.get("sensitivity", 1), v)
                    else:
                        prov[k] = sorted(set(prov.get(k, [])) | set(v))
        for r in rels:
            sid, tid = _ent_id(r["source"]), _ent_id(r["target"])
            if sid == tid:
                continue
            rid = f"rel_{sid[4:]}_{tid[4:]}"
            if rid not in relationships:
                relationships[rid] = {
                    "id": rid, "source": sid, "target": tid,
                    "description": r["description"], "weight": r["weight"],
                    "tier": "B", "provenance": dict(chunk["provenance"]),
                }
        manifest.record(chunk["source_path"], kind="tierb",
                        produced=[chunk["id"]])

    # ASCII only: progress strings reach cp1252 Windows consoles via callbacks.
    say(f"extracted {extracted} chunks -> {len(entities)} entities, "
        f"{len(relationships)} relationships ({skipped_tier3} TIER_3 skipped)")

    # drop dangling relationships
    relationships = {rid: r for rid, r in relationships.items()
                     if r["source"] in entities and r["target"] in entities}

    # ── summarize merged descriptions ────────────────────────
    sum_tpl = _prompt("summarize_descriptions")
    for e in entities.values():
        descs = e.get("descriptions") or []
        if len(descs) > 1 and len(" ".join(descs)) > 300:
            prompt = (sum_tpl.replace("{entity_name}", e["title"])
                      .replace("{description_list}", json.dumps(descs))
                      .replace("{max_length}", "120"))
            try:
                e["description"] = call(
                    [{"role": "user", "content": prompt}], None,
                    e["provenance"].get("sensitivity", 1), indexing_mode,
                    orb_label="🧠 merging knowledge").strip()
            except Exception:
                e["description"] = " ".join(descs)[:400]
        elif descs:
            e["description"] = descs[0]
        e.pop("descriptions", None)

    # degree
    for e in entities.values():
        e["degree"] = 0
    for r in relationships.values():
        entities[r["source"]]["degree"] += 1
        entities[r["target"]]["degree"] += 1

    # ── communities (reuse Tier A detector) ──────────────────
    outgoing = {eid: [] for eid in entities}
    for r in relationships.values():
        outgoing[r["source"]].append(r["target"])
    comms = [c for c in graph_analysis.detect_communities(outgoing) if len(c) > 1]
    communities, reports = [], []
    report_tpl = _prompt("community_report_graph")
    for i, comm in enumerate(comms):
        members = sorted(comm)
        cid = f"bcom_{i}"
        comm_sens = max(entities[m]["provenance"].get("sensitivity", 1)
                        for m in members)
        crels = [r for r in relationships.values()
                 if r["source"] in comm and r["target"] in comm]
        for m in members:
            entities[m]["community"] = f"B{i}"
        communities.append({
            "id": cid, "community": f"B{i}", "level": 0, "parent": None,
            "children": [], "tier": "B",
            "title": f"cluster B{i}",
            "entity_ids": members,
            "relationship_ids": [r["id"] for r in crels],
            "size": len(members),
            "provenance": {"sensitivity": comm_sens},
        })
        if i < MAX_REPORT_COMMUNITIES:
            ctx = "Entities:\n" + "\n".join(
                f"- {entities[m]['title']} ({entities[m]['type']}): "
                f"{entities[m]['description'][:200]}" for m in members[:40])
            ctx += "\nRelationships:\n" + "\n".join(
                f"- {entities[r['source']]['title']} -> "
                f"{entities[r['target']]['title']}: {r['description'][:150]}"
                for r in crels[:60])
            prompt = (report_tpl.replace("{input_text}", ctx)
                      .replace("{max_report_length}", "600"))
            try:
                raw = call([{"role": "user", "content": prompt}], None,
                           comm_sens, indexing_mode,
                           orb_label="🧠 writing community report")
                rep = _parse_report(raw)
            except Exception:
                rep = None
            if rep:
                reports.append({
                    "id": f"brep_{i}", "community": f"B{i}", "level": 0,
                    "tier": "B",
                    "title": rep.get("title", f"cluster B{i}"),
                    "summary": rep.get("summary", ""),
                    "full_content": rep.get("full_content", ""),
                    "rank": float(rep.get("rating", 5.0) or 5.0),
                    "provenance": {"sensitivity": comm_sens},
                })
                communities[-1]["title"] = rep.get("title", communities[-1]["title"])

    # entities not in any multi-member community
    for e in entities.values():
        e.setdefault("community", "B_misc")

    # ── embeddings (local, optional) ─────────────────────────
    embedded = _embed_entities(list(entities.values()), say)

    # ── persist: merge with Tier A records ───────────────────
    tier_a_entities = [e for e in store.load("entities")
                       if e.get("tier") != "B"]
    tier_a_rels = [r for r in store.load("relationships")
                   if r.get("tier") != "B"]
    tier_a_comms = [c for c in store.load("communities")
                    if c.get("tier") != "B"]

    linked = _link_to_pages(entities, tier_a_entities)

    all_entities = tier_a_entities + list(entities.values())
    all_rels = tier_a_rels + list(relationships.values()) + linked
    all_comms = tier_a_comms + communities

    from . import layout as layout_mod
    layout_meta = layout_mod.compute_layout(
        all_entities, all_rels, all_comms,
        seed=int(settings.get("layout_seed", 1337)))

    store.save("entities", all_entities)
    store.save("relationships", all_rels)
    store.save("communities", all_comms)
    store.save("community_reports", reports)
    store.save_layout(layout_meta)
    manifest.save()

    info = {
        "tier": "B", "mode": indexing_mode,
        "chunks": len(chunks), "extracted": extracted,
        "skipped_tier3": skipped_tier3,
        "entities": len(entities), "relationships": len(relationships),
        "communities": len(communities), "reports": len(reports),
        "embedded": embedded,
        "took_ms": int((time.time() - t0) * 1000),
    }
    say(f"tier B done in {info['took_ms']}ms")
    try:
        from agent_friday.routes.knowledge_graph import emit_kg_event
        emit_kg_event("reindexed", info)
    except Exception:
        pass
    return info


def _parse_report(raw: str) -> Optional[dict]:
    """Community-report prompt returns JSON (sometimes fenced)."""
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    findings = data.get("findings") or []
    full = data.get("summary", "") + "\n\n" + "\n".join(
        f"- {f.get('summary', '')}: {f.get('explanation', '')}"
        if isinstance(f, dict) else f"- {f}" for f in findings)
    return {"title": data.get("title", ""), "summary": data.get("summary", ""),
            "full_content": full.strip(), "rating": data.get("rating", 5.0)}


def _link_to_pages(entities: dict[str, dict],
                   tier_a_entities: list[dict]) -> list[dict]:
    """Weave extracted entities into the page galaxy: if an entity's
    provenance names a wiki page that exists as a Tier A node, add a
    lightweight 'appears in' edge so the two tiers render as one graph."""
    page_by_path = {}
    for e in tier_a_entities:
        for p in (e.get("provenance") or {}).get("wiki_pages", []):
            page_by_path[p] = e["id"]
    out = []
    for e in entities.values():
        for p in (e.get("provenance") or {}).get("wiki_pages", []):
            pid = page_by_path.get(p)
            if pid:
                out.append({
                    "id": f"rel_{e['id'][4:]}_{hashlib.sha1(pid.encode()).hexdigest()[:8]}",
                    "source": e["id"], "target": pid,
                    "description": "appears in", "weight": 0.3, "tier": "B",
                    "provenance": dict(e.get("provenance") or {}),
                })
    return out


def _embed_entities(entities: list[dict], say) -> int:
    """Embed entity title+description into a dedicated local ChromaDB
    collection (all-MiniLM-L6-v2 — on-device, never leaves the machine)."""
    try:
        from agent_friday.conversation_memory import ConversationMemory
        cm = ConversationMemory()
        if not cm._ensure():
            return 0
        client = cm._client
        coll = client.get_or_create_collection("knowledge-graph")
        ids, docs, metas = [], [], []
        for e in entities:
            ids.append(e["id"])
            docs.append(f"{e['title']}: {e.get('description', '')[:500]}")
            metas.append({"type": e.get("type", ""),
                          "community": str(e.get("community", ""))})
        if ids:
            coll.upsert(ids=ids, documents=docs, metadatas=metas)
        return len(ids)
    except Exception as e:
        say(f"embeddings unavailable: {e}")
        return 0
