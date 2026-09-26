"""
Agent Friday — the desktop command channel and the live situation.

  GET   /api/desktop/events     SSE: commands pushed to the desktop page
  POST  /api/desktop/state      a page reports its windows, focus and sections
  POST  /api/desktop/ack        a page reports what a command did
  GET   /api/desktop/state      the desktop as its pages last described it
  POST  /api/desktop/open       {kind, query|id, workspace, section}: resolve,
                                show, and say what the desktop confirmed
  GET   /api/situation          the live situation (?format=brief for text)

The push uses SSE, the one-way pattern /api/knowledge-graph/events and
/api/logs/stream already use: commands flow server to page, and a page's
answers come back as ordinary POSTs.
"""
import json
import queue as _queue
import re

from flask import Blueprint, Response, jsonify, request, stream_with_context

from agent_friday.core import login_required
from agent_friday.services import desktop_bus

desktop_bp = Blueprint("desktop", __name__)

_CLIENT_ID = re.compile(r"^[A-Za-z0-9_-]{4,64}$")


def _client_id(value) -> str | None:
    value = str(value or "")
    return value if _CLIENT_ID.match(value) else None


@desktop_bp.route("/api/desktop/events", methods=["GET"])
@login_required
def desktop_events():
    cid = _client_id(request.args.get("client"))
    if not cid:
        return jsonify({"status": "error", "error": "client id required"}), 400
    kind = request.args.get("kind") or "desktop"
    if kind not in ("desktop", "tab", "chat"):
        kind = "desktop"

    def stream():
        q = desktop_bus.subscribe(cid, kind)
        try:
            yield "data: " + json.dumps({"type": "hello"}) + "\n\n"
            while True:
                try:
                    evt = q.get(timeout=25)
                    yield "data: " + json.dumps(evt) + "\n\n"
                except _queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            desktop_bus.unsubscribe(cid, q)

    return Response(stream_with_context(stream()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@desktop_bp.route("/api/desktop/state", methods=["POST"])
@login_required
def desktop_state_report():
    body = request.get_json(silent=True) or {}
    cid = _client_id(body.get("client"))
    if not cid:
        return jsonify({"status": "error", "error": "client id required"}), 400
    state = body.get("state") if isinstance(body.get("state"), dict) else {}
    want = desktop_bus.report_state(cid, state)
    return jsonify({"status": "ok", "want_manifest": want})


@desktop_bp.route("/api/desktop/ack", methods=["POST"])
@login_required
def desktop_ack():
    body = request.get_json(silent=True) or {}
    known = desktop_bus.ack(str(body.get("id") or ""), body.get("result") or {})
    return jsonify({"status": "ok" if known else "unknown"})


@desktop_bp.route("/api/desktop/state", methods=["GET"])
@login_required
def desktop_state_read():
    return jsonify({"status": "ok", "desktop": desktop_bus.state(),
                    "manifest": desktop_bus.manifest()})


@desktop_bp.route("/api/desktop/open", methods=["POST"])
@login_required
def desktop_open():
    body = request.get_json(silent=True) or {}
    from agent_friday.services.desktop_targets import open_on_desktop
    r = open_on_desktop(str(body.get("kind") or ""), query=str(body.get("query") or ""),
                        id=str(body.get("id") or ""),
                        workspace=str(body.get("workspace") or ""),
                        section=str(body.get("section") or ""),
                        account=str(body.get("account") or ""))
    return jsonify({"status": r["status"], "text": r["text"],
                    "target": (r.get("resolved") or {}).get("target"),
                    "ack": (r.get("sent") or {}).get("ack")})


@desktop_bp.route("/api/situation", methods=["GET"])
@login_required
def situation_read():
    from agent_friday.services import situation
    snap = situation.snapshot()
    if request.args.get("format") == "brief":
        return jsonify({"status": "ok", "brief": situation.brief(snap),
                        "took_ms": snap.get("took_ms")})
    return jsonify({"status": "ok", "situation": snap})
