"""
Agent Friday — Knowledge Graph API (docs/KNOWLEDGE_SYSTEM_SPEC.md §5.5)

  GET   /api/knowledge-graph/summary          counts, communities, index health
  GET   /api/knowledge-graph/graph            nodes+edges (+layout) for the 3D view
  GET   /api/knowledge-graph/node/<id>        one node + neighbours + provenance
  GET   /api/knowledge-graph/neighbors/<id>   ego-graph for expand-on-click
  POST  /api/knowledge-graph/query            {question} → routed retrieval
  POST  /api/knowledge-graph/reindex          {tier?, mode?} → rebuild index
  GET   /api/knowledge-graph/search?q=        fast entity/title search
  GET   /api/knowledge-graph/events           SSE: node ignited / reindexed

Live events use SSE (the established one-way push pattern in this app, e.g.
/api/logs/stream) rather than a WebSocket — the event flow is strictly
server→client.
"""

import json
import queue as _queue
import threading
import time

from flask import Blueprint, jsonify, request, Response, stream_with_context

from agent_friday.core import login_required
from agent_friday.services.knowledge_graph import (
    kg_settings, peek_wiki_dirty, consume_wiki_dirty, mark_wiki_dirty)
from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore
from agent_friday.services.knowledge_graph import wiki_graph, structural_query

knowledge_graph_bp = Blueprint("knowledge_graph", __name__)

_rebuild_lock = threading.Lock()

# ── live event fan-out (SSE) ──────────────────────────────────
_subscribers: list[_queue.Queue] = []
_sub_lock = threading.Lock()


def emit_kg_event(event_type: str, payload: dict | None = None) -> None:
    """Publish a live graph event (node ignited, reindex done) to the 3D view."""
    evt = {"type": event_type, "ts": time.time(), **(payload or {})}
    with _sub_lock:
        for q in list(_subscribers):
            try:
                q.put_nowait(evt)
            except _queue.Full:
                pass


def _ensure_fresh() -> KnowledgeGraphStore:
    """Rebuild Tier A if the wiki changed since the last build."""
    store = KnowledgeGraphStore()
    if peek_wiki_dirty() or not (store.base / "entities.json").exists():
        with _rebuild_lock:
            if consume_wiki_dirty() or not (store.base / "entities.json").exists():
                wiki_graph.rebuild_tier_a(store=store)
    return store


# ── endpoints ─────────────────────────────────────────────────

@knowledge_graph_bp.route("/api/knowledge-graph/summary", methods=["GET"])
@login_required
def kg_summary():
    store = _ensure_fresh()
    s = store.summary()
    communities = store.load("communities")
    return jsonify({
        "status": "ok",
        "counts": s["counts"],
        "last_indexed": s["last_indexed"],
        "dropped_sensitive": s["dropped_sensitive"],
        "dirty": peek_wiki_dirty(),
        "settings": kg_settings(),
        "communities": [{"id": c["id"], "title": c["title"], "size": c["size"],
                         "level": c.get("level", 0)} for c in communities],
    })


@knowledge_graph_bp.route("/api/knowledge-graph/graph", methods=["GET"])
@login_required
def kg_graph():
    store = _ensure_fresh()
    try:
        limit = int(request.args.get("limit",
                                     kg_settings().get("max_visible_nodes", 2000)))
    except (TypeError, ValueError):
        limit = 2000
    community = request.args.get("community")

    entities = store.load("entities")
    relationships = store.load("relationships")
    communities = store.load("communities")

    if community is not None:
        entities = [e for e in entities
                    if str(e.get("community")) == str(community)]
        ids = {e["id"] for e in entities}
        relationships = [r for r in relationships
                         if r["source"] in ids and r["target"] in ids]

    truncated = len(entities) > limit
    if truncated:
        entities = sorted(entities,
                          key=lambda e: (-(e.get("degree", 0)), e["id"]))[:limit]
        ids = {e["id"] for e in entities}
        relationships = [r for r in relationships
                         if r["source"] in ids and r["target"] in ids]

    return jsonify({
        "status": "ok",
        "entities": entities,
        "relationships": relationships,
        "communities": communities,
        "layout": store.load_layout(),
        "truncated": truncated,
    })


@knowledge_graph_bp.route("/api/knowledge-graph/node/<path:node_id>",
                          methods=["GET"])
@login_required
def kg_node(node_id):
    store = _ensure_fresh()
    entities = store.load("entities")
    node = next((e for e in entities if e["id"] == node_id), None)
    if node is None:
        return jsonify({"status": "not_found", "id": node_id}), 404
    rels = [r for r in store.load("relationships")
            if r["source"] == node_id or r["target"] == node_id]
    neighbour_ids = {r["target"] if r["source"] == node_id else r["source"]
                     for r in rels}
    neighbours = [e for e in entities if e["id"] in neighbour_ids]
    reports = [r for r in store.load("community_reports")
               if str(r.get("community")) == str(node.get("community"))]
    return jsonify({"status": "ok", "node": node, "relationships": rels,
                    "neighbors": neighbours, "reports": reports})


@knowledge_graph_bp.route("/api/knowledge-graph/neighbors/<path:node_id>",
                          methods=["GET"])
@login_required
def kg_neighbors(node_id):
    store = _ensure_fresh()
    try:
        depth = min(max(int(request.args.get("depth", 1)), 1), 3)
    except (TypeError, ValueError):
        depth = 1
    entities = {e["id"]: e for e in store.load("entities")}
    if node_id not in entities:
        return jsonify({"status": "not_found", "id": node_id}), 404
    rels = store.load("relationships")
    adj: dict[str, set] = {}
    for r in rels:
        adj.setdefault(r["source"], set()).add(r["target"])
        adj.setdefault(r["target"], set()).add(r["source"])

    frontier, seen = {node_id}, {node_id}
    for _ in range(depth):
        frontier = {nb for n in frontier for nb in adj.get(n, ())} - seen
        seen |= frontier
    ego_rels = [r for r in rels if r["source"] in seen and r["target"] in seen]
    return jsonify({"status": "ok",
                    "entities": [entities[i] for i in sorted(seen)
                                 if i in entities],
                    "relationships": ego_rels})


@knowledge_graph_bp.route("/api/knowledge-graph/query", methods=["POST"])
@login_required
def kg_query():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"status": "error", "message": "question required"}), 400
    mode = data.get("mode")               # structural|local|global|drift|None
    _ensure_fresh()
    if mode == "structural" or mode is None and data.get("structural_only"):
        result = structural_query.query(question)
        result["mode"] = "structural"
    else:
        from agent_friday.services.knowledge_graph import retrieval
        result = retrieval.route_query(question, mode=mode)
    result["status"] = "ok"
    return jsonify(result)


_TIER_B_STATE = {"running": False, "last": None}


@knowledge_graph_bp.route("/api/knowledge-graph/reindex", methods=["POST"])
@login_required
def kg_reindex():
    data = request.get_json(silent=True) or {}
    tier = (data.get("tier") or "A").upper()
    if tier == "A":
        with _rebuild_lock:
            info = wiki_graph.rebuild_tier_a()
        consume_wiki_dirty()
        emit_kg_event("reindexed", info)
        return jsonify({"status": "ok", **info})
    if tier == "B":
        if _TIER_B_STATE["running"]:
            return jsonify({"status": "busy",
                            "message": "Tier B index already running"}), 409
        mode = data.get("mode") or "delta"
        sync = bool(data.get("sync"))     # tests / small corpora

        def run():
            _TIER_B_STATE["running"] = True
            try:
                from agent_friday.services.knowledge_graph import indexer
                info = indexer.reindex_tier_b(
                    mode=mode,
                    progress=lambda msg: emit_kg_event("progress",
                                                       {"message": msg}))
                _TIER_B_STATE["last"] = info
                return info
            except Exception as e:
                _TIER_B_STATE["last"] = {"error": str(e)}
                emit_kg_event("progress", {"message": f"tier B failed: {e}"})
                return {"error": str(e)}
            finally:
                _TIER_B_STATE["running"] = False

        if sync:
            info = run()
            if "error" in info:
                return jsonify({"status": "error", **info}), 500
            return jsonify({"status": "ok", **info})
        threading.Thread(target=run, daemon=True,
                         name="kg-tier-b-index").start()
        return jsonify({"status": "started", "tier": "B", "mode": mode})
    return jsonify({"status": "error", "message": f"unknown tier {tier}"}), 400


@knowledge_graph_bp.route("/api/knowledge-graph/reindex/status", methods=["GET"])
@login_required
def kg_reindex_status():
    return jsonify({"status": "ok", "running": _TIER_B_STATE["running"],
                    "last": _TIER_B_STATE["last"]})


@knowledge_graph_bp.route("/api/knowledge-graph/search", methods=["GET"])
@login_required
def kg_search():
    q = (request.args.get("q") or "").strip().lower()
    if not q:
        return jsonify({"status": "ok", "results": []})
    store = _ensure_fresh()
    results = []
    for e in store.load("entities"):
        title = e.get("title", "").lower()
        hay = title + " " + e.get("description", "").lower()
        if q in hay:
            results.append({
                "id": e["id"], "title": e["title"],
                "type": e.get("type"), "community": e.get("community"),
                "path": (e.get("provenance") or {}).get("wiki_pages", [None])[0],
                "exact": title.startswith(q),
            })
    results.sort(key=lambda r: (not r["exact"], r["title"]))
    return jsonify({"status": "ok", "results": results[:25]})


@knowledge_graph_bp.route("/api/knowledge-graph/events", methods=["GET"])
@login_required
def kg_events():
    def stream():
        q: _queue.Queue = _queue.Queue(maxsize=100)
        with _sub_lock:
            _subscribers.append(q)
        try:
            yield "data: " + json.dumps({"type": "hello"}) + "\n\n"
            while True:
                try:
                    evt = q.get(timeout=25)
                    yield "data: " + json.dumps(evt) + "\n\n"
                except _queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            with _sub_lock:
                try:
                    _subscribers.remove(q)
                except ValueError:
                    pass

    return Response(stream_with_context(stream()),
                    mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})
