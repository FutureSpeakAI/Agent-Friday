"""The generic workspace content routes for an owner's phone or tablet (PW-G2; contract R2, R3, R4, R7).

    GET /tv/v1/workspaces/<ws>/list?section=&facet.folder=&cursor=&limit=
    GET /tv/v1/workspaces/<ws>/search?q=&cursor=&limit=
    GET /tv/v1/workspaces/<ws>/item/<ref>?cursor=
    GET /tv/v1/workspaces/<ws>/changes?since=<rev>

A workspace is served only when its manifest has an exposed `device` block (ws_device) AND an adapter is
registered here; a block with no adapter answers 404, never an empty list. Each adapter is a module with the
functions ws_knowledge has. tests/unit/test_tv_ws_content.py holds the limits and refusals below.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from flask import Flask, Response, jsonify, request

from agent_friday.tv_gateway import pc_read, ws_device, ws_knowledge

_log = logging.getLogger(__name__)

ROWS_MAX = 40
BLOCKS_MAX = 60
QUERY_MAX = 200
MAX_BODY = 256 * 1024

ADAPTERS = {"knowledge": ws_knowledge}


def _now() -> str:
    return datetime.fromtimestamp(time.time(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _out(payload: dict) -> Response:
    resp = jsonify({"ok": True, "as_of": _now(), **payload})
    if resp.content_length is not None and resp.content_length > MAX_BODY:
        return pc_read._refuse(413, "that is too big to send to a phone; ask for a smaller page", code="too_large")
    return resp


def _unknown() -> Response:
    return pc_read._refuse(404, "this PC does not serve that", code="unknown")


def _offset() -> int:
    return pc_read._int("cursor", 0, 1_000_000)


def _serving(ws: str):
    """(adapter, device block) for a workspace this PC serves to a phone, or None."""
    from agent_friday.services import laya_manifests as lm
    from agent_friday.services import workspace_registry as reg
    entry = reg.get(ws) if pc_read._ID.match(ws) else None
    block = ws_device.device_block(lm.get(ws)) if entry is not None and not reg.is_held(entry) else None
    adapter = ADAPTERS.get(ws)
    return (adapter, block) if adapter is not None and block is not None else None


def _section(adapter, block: dict) -> Optional[str]:
    name = request.args.get("section") or next(iter(adapter.SECTIONS))
    section = (block.get("sections") or {}).get(name)
    return name if name in adapter.SECTIONS and section is not None else None


def _failed(e: Exception, area: str) -> Response:
    if isinstance(e, ws_knowledge.Missing):
        return _unknown()
    if isinstance(e, ws_knowledge.Locked):
        return pc_read._refuse(423, "that page is locked on the PC", code="locked")
    _log.warning("pc_read %s failed: %s", area, type(e).__name__)
    return pc_read._refuse(503, "the PC could not read that right now", code="pc_unavailable")


def register(app: Flask) -> None:
    @app.get("/tv/v1/workspaces/<ws>/list")
    def ws_list(ws):
        denied = pc_read._guard("workspaces")
        if denied is not None:
            return denied
        served = _serving(ws)
        section = _section(*served) if served else None
        if section is None:
            return _unknown()
        adapter, block = served
        limit, start = max(1, pc_read._int("limit", ROWS_MAX, ROWS_MAX)), _offset()
        try:
            rows = adapter.pages(block["sensitivity"])
        except Exception as e:
            return _failed(e, "list")
        folder = request.args.get("facet.folder")
        shown = [r for r in rows if folder is None or r["line"] == folder]
        page = shown[start:start + limit]
        return _out({"rev": adapter.rev(rows), "next": str(start + limit) if start + limit < len(shown) else None,
                     "total": len(shown), "facets": adapter.facets(rows), "rows": page})

    @app.get("/tv/v1/workspaces/<ws>/search")
    def ws_search(ws):
        denied = pc_read._guard("workspaces")
        if denied is not None:
            return denied
        served = _serving(ws)
        if served is None:
            return _unknown()
        adapter, block = served
        q = (request.args.get("q") or "").strip()
        if not q:
            return pc_read._refuse(400, "say what to look for", code="bad_query")
        if len(q) > QUERY_MAX:
            return pc_read._refuse(413, "that search is too long", code="too_large")
        limit, start = max(1, pc_read._int("limit", ROWS_MAX, ROWS_MAX)), _offset()
        try:
            rows = adapter.search(q, block["sensitivity"])
        except Exception as e:
            return _failed(e, "search")
        return _out({"rev": None, "next": str(start + limit) if start + limit < len(rows) else None,
                     "total": len(rows), "facets": [], "rows": rows[start:start + limit]})

    @app.get("/tv/v1/workspaces/<ws>/item/<path:ref>")
    def ws_item(ws, ref):
        denied = pc_read._guard("workspaces")
        if denied is not None:
            return denied
        served = _serving(ws)
        if served is None:
            return _unknown()
        adapter, block = served
        try:
            body = adapter.item(ref, block["sensitivity"], _offset(), BLOCKS_MAX)
        except Exception as e:
            return _failed(e, "item")
        kind = (block.get("kinds") or {}).get(body["kind"]) or {}
        body["verbs"] = list(kind.get("item_verbs") or [])
        return _out(body)

    @app.get("/tv/v1/workspaces/<ws>/changes")
    def ws_changes(ws):
        denied = pc_read._guard("workspaces")
        if denied is not None:
            return denied
        served = _serving(ws)
        if served is None:
            return _unknown()
        adapter, block = served
        try:
            now = adapter.rev(adapter.pages(block["sensitivity"]))
        except Exception as e:
            return _failed(e, "changes")
        return _out({"changed": request.args.get("since") != now, "rev": now})
