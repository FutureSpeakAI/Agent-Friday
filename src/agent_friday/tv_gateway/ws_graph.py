"""The knowledge graph, paged by level of detail for a phone (PHONE_WORKSPACES_PARITY section 2.3, PW-G3).

    GET /tv/v1/graph/summary
    GET /tv/v1/graph?lod=0                                   communities as super-nodes and the links between them
    GET /tv/v1/graph?lod=1&community=<key>&cursor=&limit=    one community's nodes by degree, a page at a time
    GET /tv/v1/graph/node/<id>/neighbors?depth=&limit=       a node's neighbourhood
    GET /tv/v1/graph/search?q=                               node hits, with their community
    GET /tv/v1/graph/changes?since=<rev>

Reads the desktop's own /api/knowledge-graph routes in process (GET only). A community is named by the `community`
value its nodes carry (the desktop's own filter), never by the community record's id. A node is {i, id, label,
type, community, degree, pos}; `i` is its rank in its community and an edge is [i, i, weight], so pages can be added
without renumbering. A page sends at most EDGES_PAGE_MAX edges, heaviest first, and says how many it left out. The
phone never receives a description or a wiki path here. The real graph is 12,431 nodes and 89,317 links, so one
parsed snapshot is kept per graph revision. tests/unit/test_tv_ws_graph.py holds the limits and refusals.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

from flask import Flask, Response, jsonify, request

from agent_friday.tv_gateway import pc_read, ws_blocks

_log = logging.getLogger(__name__)

LOD0_NODES_MAX = 300
LOD1_PAGE = 500
EDGES_PAGE_MAX = 4000
NEIGHBORS_MAX = 200
NEIGHBOR_DEPTH_MAX = 2
QUERY_MAX = 200
MAX_BODY = 512 * 1024
FEED_LIMIT = 200000                      # the whole graph is read here, ranked and cut here, never on the phone
_NODE_ID = re.compile(r"^[^\x00-\x1f\\]{1,200}$")
_COMMUNITY = re.compile(r"^[^\x00-\x1f\\]{1,40}$")


class Missing(Exception):
    """No such node or community."""


class Unavailable(Exception):
    """The PC could not read its graph."""


def _feed(path: str, params: Optional[dict] = None) -> dict:
    status, body, _ = pc_read._desk(path, params)
    if status == 404:
        raise Missing(path)
    if status != 200:
        raise Unavailable(path)
    try:
        data = json.loads(body)
    except ValueError as e:
        raise Unavailable(path) from e
    if not isinstance(data, dict) or data.get("status") not in (None, "ok"):
        raise Unavailable(path)
    return data


def _pos(e: dict) -> Optional[list]:
    try:
        return [round(float(e[k]), 3) for k in ("x", "y", "z")]
    except (KeyError, TypeError, ValueError):
        return None


def _weight(r: dict) -> float:
    try:
        return round(float(r.get("weight") or 1), 3)
    except (TypeError, ValueError):
        return 1.0


def _node(e: dict, i: int) -> dict:
    return {"i": i, "id": str(e.get("id"))[:200], "label": ws_blocks.line(e.get("title"), 120),
            "type": ws_blocks.line(e.get("type"), 40), "community": e.get("community"),
            "degree": int(e.get("degree") or 0), "pos": _pos(e)}


def rev(summary: dict) -> str:
    counts = summary.get("counts") or {}
    return "g_%s_%s_%s" % (counts.get("entities", 0), counts.get("relationships", 0),
                           re.sub(r"\W", "", str(summary.get("last_indexed") or "")))


@dataclass
class Snapshot:
    """One revision of the graph, ranked once: nodes by community, links by community, and the links between."""
    rev: str
    counts: dict
    titles: dict          # community key -> title
    ranked: dict          # community key -> entities, by degree then id
    inside: dict          # community key -> [(rank a, rank b, weight)], by the later of the two ranks
    between: dict         # (community key, community key) -> link count
    centres: dict         # community key -> [x, y, z]


_SNAPSHOT: Optional[Snapshot] = None
_LOCK = threading.Lock()


def snapshot() -> Snapshot:
    global _SNAPSHOT
    head = _feed("/api/knowledge-graph/summary")
    now = rev(head)
    with _LOCK:
        if _SNAPSHOT is not None and _SNAPSHOT.rev == now:
            return _SNAPSHOT
    data = _feed("/api/knowledge-graph/graph", {"view": "compact", "limit": FEED_LIMIT})
    entities = [e for e in data.get("entities") or [] if isinstance(e, dict) and e.get("id")]
    ranked: dict = {}
    for e in sorted(entities, key=lambda e: (-int(e.get("degree") or 0), str(e["id"]))):
        ranked.setdefault(str(e.get("community")), []).append(e)
    place = {e["id"]: (c, i) for c, rows in ranked.items() for i, e in enumerate(rows)}
    inside: dict = {}
    between: dict = {}
    for r in data.get("relationships") or []:
        a, b = place.get(r.get("source")), place.get(r.get("target"))
        if a is None or b is None:
            continue
        if a[0] == b[0]:
            inside.setdefault(a[0], []).append((a[1], b[1], _weight(r)))
        else:
            key = tuple(sorted((a[0], b[0])))
            between[key] = between.get(key, 0) + 1
    for rows in inside.values():
        rows.sort(key=lambda x: (max(x[0], x[1]), x[0], x[1]))
    centres = {}
    for c, rows in ranked.items():
        ps = [p for p in (_pos(e) for e in rows) if p]
        if ps:
            centres[c] = [round(sum(p[k] for p in ps) / len(ps), 3) for k in range(3)]
    titles = {str(c.get("community") if c.get("community") is not None else c.get("id")): c.get("title")
              for c in data.get("communities") or [] if isinstance(c, dict)}
    made = Snapshot(now, {k: v for k, v in (head.get("counts") or {}).items() if isinstance(v, int)},
                    titles, ranked, inside, between, centres)
    with _LOCK:
        _SNAPSHOT = made
    return made


def summary() -> dict:
    s = snapshot()
    return {"rev": s.rev, "counts": s.counts,
            "communities": [{"id": c, "label": ws_blocks.line(s.titles.get(c) or c, 120), "size": len(rows),
                             "centre": s.centres.get(c)}
                            for c, rows in sorted(s.ranked.items(), key=lambda kv: (-len(kv[1]), kv[0]))]}


def lod0() -> dict:
    s = snapshot()
    chosen = sorted(s.ranked, key=lambda c: (-len(s.ranked[c]), c))[:LOD0_NODES_MAX]
    at = {c: i for i, c in enumerate(chosen)}
    nodes = [{"i": at[c], "id": c, "label": ws_blocks.line(s.titles.get(c) or c, 120), "type": "community",
              "community": c, "degree": len(s.ranked[c]), "pos": s.centres.get(c)} for c in chosen]
    edges = [[at[a], at[b], n] for (a, b), n in sorted(s.between.items()) if a in at and b in at]
    return {"rev": s.rev, "level": 0, "nodes": nodes, "edges": edges, "edges_left_out": 0, "next": None}


def lod1(community: str, start: int, limit: int) -> dict:
    s = snapshot()
    rows = s.ranked.get(community)
    if not rows:
        raise Missing(community)
    sent = start + limit
    fresh = [e for e in s.inside.get(community, []) if start <= max(e[0], e[1]) < sent]
    kept = sorted(fresh, key=lambda e: (-e[2], e[0], e[1]))[:EDGES_PAGE_MAX]
    return {"rev": s.rev, "level": 1, "community": community, "total": len(rows),
            "nodes": [_node(rows[i], i) for i in range(start, min(sent, len(rows)))],
            "edges": [list(e) for e in sorted(kept)], "edges_left_out": len(fresh) - len(kept),
            "next": str(sent) if sent < len(rows) else None}


def neighbors(node_id: str, depth: int, limit: int) -> dict:
    data = _feed("/api/knowledge-graph/neighbors/" + quote(node_id, safe="/:"), {"depth": depth})
    found = [e for e in data.get("entities") or [] if isinstance(e, dict) and e.get("id")]
    found.sort(key=lambda e: (e["id"] != node_id, -int(e.get("degree") or 0), str(e["id"])))
    found = found[:limit]
    at = {e["id"]: i for i, e in enumerate(found)}
    links = [[at[r["source"]], at[r["target"]], _weight(r)] for r in data.get("relationships") or []
             if isinstance(r, dict) and r.get("source") in at and r.get("target") in at]
    kept = sorted(links, key=lambda e: (-e[2], e[0], e[1]))[:EDGES_PAGE_MAX]
    return {"level": 2, "center": node_id, "nodes": [_node(e, i) for i, e in enumerate(found)],
            "edges": sorted(kept), "edges_left_out": len(links) - len(kept), "next": None}


def search(q: str) -> list[dict]:
    out = []
    for hit in _feed("/api/knowledge-graph/search", {"q": q}).get("results") or []:
        if isinstance(hit, dict) and hit.get("id"):
            path = hit.get("path")
            out.append({"id": str(hit["id"])[:200], "label": ws_blocks.line(hit.get("title"), 120),
                        "type": ws_blocks.line(hit.get("type"), 40), "community": hit.get("community"),
                        "ref": ("page:%s" % path) if isinstance(path, str) and path.endswith((".md", ".txt")) else None})
    return out


def _bad(status: int, why: str, code: str) -> Response:
    return pc_read._refuse(status, why, code=code)


def _out(payload: dict) -> Response:
    resp = jsonify({"ok": True, "as_of": int(time.time()), **payload})
    if resp.content_length is not None and resp.content_length > MAX_BODY:
        return _bad(413, "that is too big to send to a phone; ask for a smaller page", "too_large")
    return resp


def _run(area: str, call):
    try:
        return _out(call())
    except Missing:
        return _bad(404, "this PC does not have that", "unknown")
    except Exception as e:
        _log.warning("pc_read graph %s failed: %s", area, type(e).__name__)
        return _bad(503, "the PC could not read its graph right now", "pc_unavailable")


def register(app: Flask) -> None:
    @app.get("/tv/v1/graph/summary")
    def graph_summary():
        return pc_read._guard("graph") or _run("summary", summary)

    @app.get("/tv/v1/graph")
    def graph_level():
        if (denied := pc_read._guard("graph")) is not None:
            return denied
        lod = request.args.get("lod", "0")
        if lod == "0":
            return _run("lod0", lod0)
        community = request.args.get("community", "")
        if lod != "1" or not _COMMUNITY.match(community):
            return _bad(400, "ask for lod 0, or lod 1 with a community", "bad_request")
        limit = max(1, pc_read._int("limit", LOD1_PAGE, LOD1_PAGE))
        start = pc_read._int("cursor", 0, 10_000_000)
        return _run("lod1", lambda: lod1(community, start, limit))

    @app.get("/tv/v1/graph/node/<path:node_id>/neighbors")
    def graph_neighbors(node_id):
        if (denied := pc_read._guard("graph")) is not None:
            return denied
        if not _NODE_ID.match(node_id) or ".." in node_id.split("/"):
            return _bad(404, "this PC does not have that", "unknown")
        depth = min(max(1, pc_read._int("depth", 1, NEIGHBOR_DEPTH_MAX)), NEIGHBOR_DEPTH_MAX)
        limit = max(1, pc_read._int("limit", NEIGHBORS_MAX, NEIGHBORS_MAX))
        return _run("neighbors", lambda: neighbors(node_id, depth, limit))

    @app.get("/tv/v1/graph/search")
    def graph_search():
        if (denied := pc_read._guard("graph")) is not None:
            return denied
        q = (request.args.get("q") or "").strip()
        if not q:
            return _bad(400, "say what to look for", "bad_query")
        if len(q) > QUERY_MAX:
            return _bad(413, "that search is too long", "too_large")
        return _run("search", lambda: {"rows": search(q)})

    @app.get("/tv/v1/graph/changes")
    def graph_changes():
        if (denied := pc_read._guard("graph")) is not None:
            return denied

        def changed():
            now = rev(_feed("/api/knowledge-graph/summary"))
            return {"changed": request.args.get("since") != now, "rev": now}
        return _run("changes", changed)
