"""The knowledge graph, paged by level of detail for a phone (PHONE_WORKSPACES_PARITY section 2.3, PW-G3).

    GET /tv/v1/graph/summary
    GET /tv/v1/graph?lod=0                                   communities as super-nodes and the links between them
    GET /tv/v1/graph?lod=1&community=<id>&cursor=&limit=     one community's nodes by degree, a page at a time
    GET /tv/v1/graph/node/<id>/neighbors?depth=&limit=       a node's neighbourhood
    GET /tv/v1/graph/search?q=                               node hits, with their community
    GET /tv/v1/graph/changes?since=<rev>

Reads the desktop's own /api/knowledge-graph routes in process (GET only). A node is {i, id, label, type,
community, degree, pos}; `i` is its place in the list the phone is building, and an edge is [i, i, weight], so
pages can be added without renumbering. The phone never receives a description or a wiki path here: the node
sheet asks for the page (a `page:` ref on a search hit) through the workspace routes. tests/unit/test_tv_ws_graph.py
holds the limits and refusals.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional
from urllib.parse import quote

from flask import Flask, Response, jsonify, request

from agent_friday.tv_gateway import pc_read, ws_blocks

_log = logging.getLogger(__name__)

LOD0_NODES_MAX = 300
LOD1_PAGE = 500
NEIGHBORS_MAX = 200
NEIGHBOR_DEPTH_MAX = 2
QUERY_MAX = 200
MAX_BODY = 512 * 1024
FEED_LIMIT = 200000                      # the whole graph, ranked and cut here, never on the phone
_NODE_ID = re.compile(r"^[^\x00-\x1f\\]{1,200}$")


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


def _node(e: dict, i: int) -> dict:
    return {"i": i, "id": str(e.get("id"))[:200], "label": ws_blocks.line(e.get("title"), 120),
            "type": ws_blocks.line(e.get("type"), 40), "community": e.get("community"),
            "degree": int(e.get("degree") or 0), "pos": _pos(e)}


def _edge(r: dict, at: dict) -> Optional[list]:
    a, b = at.get(r.get("source")), at.get(r.get("target"))
    if a is None or b is None:
        return None
    try:
        return [a, b, round(float(r.get("weight") or 1), 3)]
    except (TypeError, ValueError):
        return [a, b, 1.0]


def rev(summary: dict) -> str:
    counts = summary.get("counts") or {}
    return "g_%s_%s_%s" % (counts.get("entities", 0), counts.get("relationships", 0),
                           re.sub(r"\W", "", str(summary.get("last_indexed") or "")))


def summary() -> dict:
    data = _feed("/api/knowledge-graph/summary")
    centres = _centres()
    return {"rev": rev(data), "counts": {k: v for k, v in (data.get("counts") or {}).items() if isinstance(v, int)},
            "communities": [{"id": c.get("id"), "label": ws_blocks.line(c.get("title"), 120), "size": int(c.get("size") or 0),
                             "centre": centres.get(str(c.get("id")))} for c in data.get("communities") or []]}


def _all() -> dict:
    return _feed("/api/knowledge-graph/graph", {"view": "compact", "limit": FEED_LIMIT})


def _centres() -> dict:
    sums: dict = {}
    for e in _all().get("entities") or []:
        p = _pos(e)
        if p:
            s = sums.setdefault(str(e.get("community")), [0.0, 0.0, 0.0, 0])
            s[0] += p[0]
            s[1] += p[1]
            s[2] += p[2]
            s[3] += 1
    return {c: [round(s[0] / s[3], 3), round(s[1] / s[3], 3), round(s[2] / s[3], 3)] for c, s in sums.items()}


def lod0() -> dict:
    data = _all()
    centres, sizes, weight = _centres(), {}, {}
    for e in data.get("entities") or []:
        sizes[str(e.get("community"))] = sizes.get(str(e.get("community")), 0) + 1
    community_of = {e.get("id"): str(e.get("community")) for e in data.get("entities") or []}
    for r in data.get("relationships") or []:
        a, b = community_of.get(r.get("source")), community_of.get(r.get("target"))
        if a is not None and b is not None and a != b:
            key = tuple(sorted((a, b)))
            weight[key] = weight.get(key, 0) + 1
    chosen = sorted(sizes, key=lambda c: (-sizes[c], c))[:LOD0_NODES_MAX]
    at = {c: i for i, c in enumerate(chosen)}
    titles = {str(c.get("id")): c.get("title") for c in data.get("communities") or []}
    nodes = [{"i": at[c], "id": c, "label": ws_blocks.line(titles.get(c) or c, 120), "type": "community",
              "community": c, "degree": sizes[c], "pos": centres.get(c)} for c in chosen]
    edges = [[at[a], at[b], w] for (a, b), w in sorted(weight.items()) if a in at and b in at]
    return {"level": 0, "nodes": nodes, "edges": edges, "next": None}


def lod1(community: str, start: int, limit: int) -> dict:
    data = _feed("/api/knowledge-graph/graph", {"view": "compact", "limit": FEED_LIMIT, "community": community})
    ranked = sorted((e for e in data.get("entities") or [] if isinstance(e, dict) and e.get("id")),
                    key=lambda e: (-int(e.get("degree") or 0), str(e["id"])))
    if not ranked:
        raise Missing(community)
    at = {e["id"]: i for i, e in enumerate(ranked)}
    window = range(start, min(start + limit, len(ranked)))
    sent = start + limit
    edges = [x for r in data.get("relationships") or [] if isinstance(r, dict) and (x := _edge(r, at))
             and max(x[0], x[1]) < sent and max(x[0], x[1]) >= start]
    return {"level": 1, "community": community, "total": len(ranked),
            "nodes": [_node(ranked[i], i) for i in window], "edges": edges,
            "next": str(sent) if sent < len(ranked) else None}


def neighbors(node_id: str, depth: int, limit: int) -> dict:
    data = _feed("/api/knowledge-graph/neighbors/" + quote(node_id, safe="/:"), {"depth": depth})
    found = [e for e in data.get("entities") or [] if isinstance(e, dict) and e.get("id")]
    found.sort(key=lambda e: (e["id"] != node_id, -int(e.get("degree") or 0), str(e["id"])))
    found = found[:limit]
    at = {e["id"]: i for i, e in enumerate(found)}
    return {"level": 2, "center": node_id, "nodes": [_node(e, i) for i, e in enumerate(found)],
            "edges": [x for r in data.get("relationships") or [] if isinstance(r, dict) and (x := _edge(r, at))],
            "next": None}


def search(q: str) -> list[dict]:
    out = []
    for hit in _feed("/api/knowledge-graph/search", {"q": q}).get("results") or []:
        if isinstance(hit, dict) and hit.get("id"):
            path = hit.get("path")
            out.append({"id": str(hit["id"])[:200], "label": ws_blocks.line(hit.get("title"), 120),
                        "type": ws_blocks.line(hit.get("type"), 40), "community": hit.get("community"),
                        "ref": ("page:%s" % path) if isinstance(path, str) and path.endswith((".md", ".txt")) else None})
    return out


def _now() -> int:
    return int(time.time())


def _bad(status: int, why: str, code: str) -> Response:
    return pc_read._refuse(status, why, code=code)


def _out(payload: dict) -> Response:
    resp = jsonify({"ok": True, "as_of": _now(), **payload})
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
    def guarded(area):
        return pc_read._guard(area)

    @app.get("/tv/v1/graph/summary")
    def graph_summary():
        return guarded("graph") or _run("summary", summary)

    @app.get("/tv/v1/graph")
    def graph_level():
        if (denied := guarded("graph")) is not None:
            return denied
        lod = request.args.get("lod", "0")
        if lod == "0":
            return _run("lod0", lambda: dict(lod0(), rev=rev(_feed("/api/knowledge-graph/summary"))))
        community = request.args.get("community", "")
        if lod != "1" or not community or len(community) > 40 or not _NODE_ID.match(community):
            return _bad(400, "ask for lod 0, or lod 1 with a community", "bad_request")
        limit = max(1, pc_read._int("limit", LOD1_PAGE, LOD1_PAGE))
        start = pc_read._int("cursor", 0, 10_000_000)
        return _run("lod1", lambda: dict(lod1(community, start, limit), rev=rev(_feed("/api/knowledge-graph/summary"))))

    @app.get("/tv/v1/graph/node/<path:node_id>/neighbors")
    def graph_neighbors(node_id):
        if (denied := guarded("graph")) is not None:
            return denied
        if not _NODE_ID.match(node_id) or ".." in node_id.split("/"):
            return _bad(404, "this PC does not have that", "unknown")
        depth = min(max(1, pc_read._int("depth", 1, NEIGHBOR_DEPTH_MAX)), NEIGHBOR_DEPTH_MAX)
        limit = max(1, pc_read._int("limit", NEIGHBORS_MAX, NEIGHBORS_MAX))
        return _run("neighbors", lambda: neighbors(node_id, depth, limit))

    @app.get("/tv/v1/graph/search")
    def graph_search():
        if (denied := guarded("graph")) is not None:
            return denied
        q = (request.args.get("q") or "").strip()
        if not q:
            return _bad(400, "say what to look for", "bad_query")
        if len(q) > QUERY_MAX:
            return _bad(413, "that search is too long", "too_large")
        return _run("search", lambda: {"rows": search(q)})

    @app.get("/tv/v1/graph/changes")
    def graph_changes():
        if (denied := guarded("graph")) is not None:
            return denied

        def changed():
            now = rev(_feed("/api/knowledge-graph/summary"))
            return {"changed": request.args.get("since") != now, "rev": now}
        return _run("changes", changed)
