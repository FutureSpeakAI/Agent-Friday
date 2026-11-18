"""The generic workspace content routes for an owner's phone or tablet (PW-G2; contract R2, R3, R4, R7).

    GET /tv/v1/workspaces/<ws>/list?section=&facet.<name>=&cursor=&limit=
    GET /tv/v1/workspaces/<ws>/search?q=&section=&cursor=&limit=
    GET /tv/v1/workspaces/<ws>/item/<ref>?cursor=
    GET /tv/v1/workspaces/<ws>/changes?section=&since=<rev>

A section of a workspace is served only when its manifest has an exposed `device` block that declares the section AND
an adapter is registered here for it; otherwise 404, never an empty list. An adapter is a module with PREFIX (the
start of the refs it makes), FACETS ({facet name: function of a row}), and pages(sensitivity), facets(rows),
rev(rows), search(q, sensitivity), item(ref, sensitivity, offset, limit), each raising ws_adapter.Missing, Locked or
Unavailable and nothing else the phone could read. Search with no section asks every adapter of the workspace.
tests/unit/test_tv_ws_content.py holds the limits and refusals.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from flask import Flask, Response, jsonify, request

from agent_friday.tv_gateway import pc_read, ws_adapter, ws_calendar, ws_device, ws_knowledge, ws_news, ws_people

_log = logging.getLogger(__name__)

ROWS_MAX = 40
BLOCKS_MAX = 60
QUERY_MAX = 200
MAX_BODY = 256 * 1024

ADAPTERS = {"knowledge": {"pages": ws_knowledge, "people": ws_people}, "calendar": {"agenda": ws_calendar},
            "news": {"latest": ws_news}}


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
    """({section: adapter}, device block) for a workspace this PC serves to a phone, or None. Only sections the
    manifest declares and an adapter exists for are in the dict."""
    from agent_friday.services import laya_manifests as lm
    from agent_friday.services import workspace_registry as reg
    entry = reg.get(ws) if pc_read.ID_PATTERN.match(ws) else None
    block = ws_device.device_block(lm.get(ws)) if entry is not None and not reg.is_held(entry) else None
    declared = (block or {}).get("sections") or {}
    adapters = {name: a for name, a in ADAPTERS.get(ws, {}).items() if name in declared}
    return (adapters, block) if adapters else None


def _by_ref(adapters: dict, ref: str):
    return next((a for a in adapters.values() if ref.startswith(a.PREFIX)), None)


def _failed(e: Exception, area: str) -> Response:
    if isinstance(e, ws_adapter.Missing):
        return _unknown()
    if isinstance(e, ws_adapter.Locked):
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
        adapters = served[0] if served else {}
        section = request.args.get("section") or next(iter(adapters), None)
        if section not in adapters:
            return _unknown()
        adapter, block = adapters[section], served[1]
        limit, start = max(1, pc_read._int("limit", ROWS_MAX, ROWS_MAX)), _offset()
        try:
            rows = adapter.pages(block["sensitivity"])
        except Exception as e:
            return _failed(e, "list")
        wanted = [(name, request.args[key]) for name in adapter.FACETS if (key := "facet." + name) in request.args]
        shown = [r for r in rows if all(adapter.FACETS[name](r) == value for name, value in wanted)]
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
        adapters, block = served
        section = request.args.get("section")
        if section is not None and section not in adapters:
            return _unknown()
        q = (request.args.get("q") or "").strip()
        if not q:
            return pc_read._refuse(400, "say what to look for", code="bad_query")
        if len(q) > QUERY_MAX:
            return pc_read._refuse(413, "that search is too long", code="too_large")
        limit, start = max(1, pc_read._int("limit", ROWS_MAX, ROWS_MAX)), _offset()
        rows, failure, answered = [], None, False
        for adapter in ([adapters[section]] if section else adapters.values()):
            try:
                rows += adapter.search(q, block["sensitivity"])
                answered = True
            except Exception as e:                       # one section being down must not hide another's hits
                failure = e
        if not answered and failure is not None:
            return _failed(failure, "search")
        return _out({"rev": None, "next": str(start + limit) if start + limit < len(rows) else None,
                     "total": len(rows), "facets": [], "rows": rows[start:start + limit]})

    @app.get("/tv/v1/workspaces/<ws>/item/<path:ref>")
    def ws_item(ws, ref):
        denied = pc_read._guard("workspaces")
        if denied is not None:
            return denied
        served = _serving(ws)
        adapter = _by_ref(served[0], ref) if served else None
        if adapter is None:
            return _unknown()
        block = served[1]
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
        adapters, block = served
        section = request.args.get("section")
        if section is not None and section not in adapters:
            return _unknown()
        try:
            now = "+".join(a.rev(a.pages(block["sensitivity"])) for a in
                           ([adapters[section]] if section else adapters.values()))
        except Exception as e:
            return _failed(e, "changes")
        return _out({"changed": request.args.get("since") != now, "rev": now})
