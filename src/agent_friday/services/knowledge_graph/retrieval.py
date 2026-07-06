"""Tier B retrieval — local / global / drift search + the query router.

Four retrieval paths (spec §6.4), auto-routed by query type:

  structural — Tier A, zero LLM (structural_query). Default and cheapest.
  local      — entity-centric facts: top entities (embedding or keyword
               match) + their relationships + provenance → one LLM call.
  global     — thematic questions: map over community reports → reduce.
  drift      — hybrid: local search primed with the top community context.

Every LLM call rides model_router._generate_text (sealed downstream); the
same local-only pinning as the indexer applies. If Tier B artifacts don't
exist yet, everything degrades to structural.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from . import kg_settings
from . import structural_query
from .indexer import _prompt, _llm
from .store import KnowledgeGraphStore

MAX_MAP_REPORTS = 8
MAX_LOCAL_ENTITIES = 12


def _b_entities(store: KnowledgeGraphStore) -> list[dict]:
    return [e for e in store.load("entities") if e.get("tier") == "B"]


def _reports(store: KnowledgeGraphStore) -> list[dict]:
    return sorted(store.load("community_reports"),
                  key=lambda r: -float(r.get("rank", 0)))


def _max_sensitivity(records) -> int:
    out = 1
    for r in records:
        out = max(out, int((r.get("provenance") or {}).get("sensitivity", 1)))
    return out


# ── candidate entities for local search ───────────────────────

def _match_entities(question: str, store: KnowledgeGraphStore,
                    top_n: int = MAX_LOCAL_ENTITIES) -> list[dict]:
    ents = _b_entities(store)
    if not ents:
        return []
    by_id = {e["id"]: e for e in ents}

    # Embedding match first (local ChromaDB collection, on-device).
    try:
        from agent_friday.conversation_memory import ConversationMemory
        cm = ConversationMemory()
        if cm._ensure():
            coll = cm._client.get_or_create_collection("knowledge-graph")
            res = coll.query(query_texts=[question], n_results=top_n)
            ids = (res.get("ids") or [[]])[0]
            hits = [by_id[i] for i in ids if i in by_id]
            if hits:
                return hits
    except Exception:
        pass

    # Keyword fallback.
    terms = [w.strip("?,.'\"").lower() for w in question.split() if len(w) > 2]
    scored = []
    for e in ents:
        hay = (e.get("title", "") + " " + e.get("description", "")).lower()
        score = sum(2 if t in e.get("title", "").lower() else 1
                    for t in terms if t in hay)
        if score:
            scored.append((score, e))
    scored.sort(key=lambda x: (-x[0], x[1]["id"]))
    return [e for _s, e in scored[:top_n]]


# ── search modes ──────────────────────────────────────────────

def local_search(question: str,
                 store: Optional[KnowledgeGraphStore] = None,
                 llm=None) -> dict[str, Any]:
    store = store or KnowledgeGraphStore()
    call = llm or _llm
    mode = str(kg_settings().get("indexing_mode", "local_only"))
    ents = _match_entities(question, store)
    if not ents:
        return {"mode": "local", "answer": None,
                "note": "no matching entities in the semantic index"}
    ids = {e["id"] for e in ents}
    rels = [r for r in store.load("relationships")
            if r.get("tier") == "B" and (r["source"] in ids or r["target"] in ids)]

    ctx_lines = ["-- Entities --"]
    for e in ents:
        prov = e.get("provenance") or {}
        src = ", ".join(prov.get("wiki_pages", [])[:2]
                        + prov.get("cognitive_keys", [])[:2]) or "conversation"
        ctx_lines.append(f"{e['title']} ({e.get('type', '')}): "
                         f"{e.get('description', '')} [source: {src}]")
    ctx_lines.append("-- Relationships --")
    by_id = {e["id"]: e for e in _b_entities(store)}
    for r in rels[:40]:
        s = by_id.get(r["source"], {}).get("title", r["source"])
        t = by_id.get(r["target"], {}).get("title", r["target"])
        ctx_lines.append(f"{s} -> {t}: {r.get('description', '')}")

    system = (_prompt("local_search_system_prompt")
              .replace("{context_data}", "\n".join(ctx_lines))
              .replace("{response_type}", "concise paragraph"))
    sens = _max_sensitivity(ents)
    answer = call([{"role": "user", "content": question}], system, sens, mode,
                  orb_label="🧠 local search")
    return {"mode": "local", "answer": answer,
            "entities": [{"id": e["id"], "title": e["title"],
                          "provenance": e.get("provenance")} for e in ents]}


def global_search(question: str,
                  store: Optional[KnowledgeGraphStore] = None,
                  llm=None) -> dict[str, Any]:
    store = store or KnowledgeGraphStore()
    call = llm or _llm
    mode = str(kg_settings().get("indexing_mode", "local_only"))
    reports = _reports(store)[:MAX_MAP_REPORTS]
    if not reports:
        return {"mode": "global", "answer": None,
                "note": "no community reports — run a Tier B reindex first"}

    map_tpl = _prompt("global_search_map_system_prompt")
    points = []
    for rep in reports:
        system = (map_tpl
                  .replace("{context_data}", rep.get("full_content")
                           or rep.get("summary", ""))
                  .replace("{max_length}", "300"))
        sens = int((rep.get("provenance") or {}).get("sensitivity", 1))
        try:
            raw = call([{"role": "user", "content": question}], system, sens,
                       mode, orb_label="🧠 global search (map)")
        except Exception:
            continue
        m = re.search(r"\{.*\}", raw or "", re.DOTALL)
        if m:
            try:
                for p in (json.loads(m.group(0)).get("points") or []):
                    points.append({"report": rep.get("title", ""),
                                   "answer": p.get("description", ""),
                                   "score": p.get("score", 0)})
                continue
            except ValueError:
                pass
        if raw and raw.strip():
            points.append({"report": rep.get("title", ""),
                           "answer": raw.strip()[:400], "score": 50})

    points.sort(key=lambda p: -float(p.get("score") or 0))
    if not points:
        return {"mode": "global", "answer": None, "note": "map stage empty"}

    reduce_tpl = _prompt("global_search_reduce_system_prompt")
    report_data = "\n\n".join(
        f"[{p['report']}] (score {p['score']}): {p['answer']}"
        for p in points[:12])
    system = (reduce_tpl.replace("{report_data}", report_data)
              .replace("{response_type}", "concise, well-structured answer")
              .replace("{max_length}", "400"))
    sens = _max_sensitivity(reports)
    answer = call([{"role": "user", "content": question}], system, sens, mode,
                  orb_label="🧠 global search (reduce)")
    return {"mode": "global", "answer": answer,
            "reports_used": [r.get("title") for r in reports],
            "points": points[:12]}


def drift_search(question: str,
                 store: Optional[KnowledgeGraphStore] = None,
                 llm=None) -> dict[str, Any]:
    """Hybrid: prime local search with the top community summaries."""
    store = store or KnowledgeGraphStore()
    primer = "\n".join(f"- {r.get('title')}: {r.get('summary', '')[:200]}"
                       for r in _reports(store)[:4])
    primed = (f"{question}\n\n(Background themes in this knowledge base:\n"
              f"{primer})" if primer else question)
    out = local_search(primed, store=store, llm=llm)
    out["mode"] = "drift"
    return out


# ── router ────────────────────────────────────────────────────

def route_query(question: str, mode: Optional[str] = None,
                store: Optional[KnowledgeGraphStore] = None,
                llm=None) -> dict[str, Any]:
    """One entry point for knowledge questions (spec §6.4).

    mode: force "structural" | "local" | "global" | "drift"; default auto.
    Structural always runs (it's free) and rides along as candidates /
    should_read; Tier B modes add an LLM answer when the index exists.
    """
    store = store or KnowledgeGraphStore()
    structural = structural_query.query(question)
    answer_type = structural["answer_type"]

    chosen = mode
    if chosen is None:
        has_b = bool(_b_entities(store))
        has_reports = bool(store.load("community_reports"))
        if answer_type == "thematic" and has_reports:
            chosen = "global"
        elif answer_type in ("path", "list", "gap"):
            chosen = "structural"      # graph structure answers these free
        elif has_b and not structural["index_only"]:
            chosen = "local"
        else:
            chosen = "structural"

    result: dict[str, Any] = {"question": question,
                              "structural": structural,
                              "mode": chosen}
    if chosen == "structural":
        result.update(structural)          # flat contract for pure-structural
    if chosen == "local":
        result.update(local_search(question, store=store, llm=llm))
    elif chosen == "global":
        result.update(global_search(question, store=store, llm=llm))
    elif chosen == "drift":
        result.update(drift_search(question, store=store, llm=llm))
    result["mode"] = chosen
    return result
