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


@desktop_bp.route("/api/desktop/board", methods=["GET", "POST"])
@login_required
def desktop_board():
    from agent_friday.services import desktop_cards as cards
    from agent_friday.routes._errors import api_error
    try:
        if request.method == "GET":
            return jsonify(cards.read_board())
        # Body parsing may wait across a privacy-session transition.
        origin = cards._admit()
        return jsonify(cards.change_board(request.get_json(silent=True), origin=origin))
    except cards.BoardConflict as exc:
        return jsonify({"status": "error", "code": "board_changed", "message": str(exc)}), 409
    except cards.CardError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    except Exception as exc:
        return api_error(exc, "Couldn't read Home" if request.method == "GET" else "Couldn't save the Home change")


@desktop_bp.route("/api/desktop/cards", methods=["GET", "POST"])
@login_required
def desktop_cards():
    from agent_friday.services import desktop_cards as cards
    from agent_friday.routes._errors import api_error
    try:
        if request.method == "GET":
            return jsonify({"status": "ok", "cards": cards.list_cards()})
        origin = cards._admit()
        card = cards.upsert_card(request.get_json(silent=True), origin=origin)
        return jsonify({"status": "ok", "card": card})
    except cards.CardError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    except Exception as exc:
        return api_error(exc, "Couldn't load Home cards" if request.method == "GET" else "Couldn't save the Home card")


@desktop_bp.route("/api/desktop/cards/<card_id>", methods=["DELETE"])
@login_required
def desktop_card_remove(card_id):
    from agent_friday.services import desktop_cards as cards
    from agent_friday.routes._errors import api_error
    try:
        return jsonify({"status": "ok", "removed": cards.remove_card(card_id)})
    except cards.CardError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    except Exception as exc:
        return api_error(exc, "Couldn't remove the Home card")

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
                        account=str(body.get("account") or ""),
                        new_tab=bool(body.get("new_tab")),
                        maximize=bool(body.get("max", body.get("new_tab"))))
    return jsonify({"status": r["status"], "text": r["text"],
                    "target": (r.get("resolved") or {}).get("target"),
                    "ack": (r.get("sent") or {}).get("ack")})


@desktop_bp.route("/api/camera/holders", methods=["GET"])
@login_required
def camera_holders():
    """Which app holds the webcam: what the page shows when its own open
    fails with a bare 'not readable' (services/camera_holders)."""
    from agent_friday.services import camera_holders
    snap = camera_holders.snapshot()
    return jsonify({"status": "ok", "holders": snap["holders"],
                    "candidates": snap["candidates"]})


@desktop_bp.route("/api/call/state", methods=["GET"])
@login_required
def call_state():
    """Whether Friday is standing back for a call, and the call_mode setting."""
    from agent_friday.services import call_watch
    return jsonify(dict(status="ok", **call_watch.watch().snapshot()))


@desktop_bp.route("/api/call/start", methods=["POST"])
@login_required
def call_start():
    """Stand back by hand (the chip, or a spoken 'I'm on a call')."""
    from agent_friday.services import call_watch
    body = request.get_json(silent=True) or {}
    return jsonify(dict(status="ok", **call_watch.watch().start(str(body.get("app") or ""))))


@desktop_bp.route("/api/call/end", methods=["POST"])
@login_required
def call_end():
    from agent_friday.services import call_watch
    return jsonify(dict(status="ok", **call_watch.watch().end()))


@desktop_bp.route("/api/call/decide", methods=["POST"])
@login_required
def call_decide():
    """The answer to an 'ask' chip: {accept: bool, app}."""
    from agent_friday.services import call_watch
    body = request.get_json(silent=True) or {}
    return jsonify(dict(status="ok", **call_watch.watch().decide(
        bool(body.get("accept")), str(body.get("app") or ""))))


@desktop_bp.route("/api/situation", methods=["GET"])
@login_required
def situation_read():
    from agent_friday.services import situation
    snap = situation.snapshot()
    if request.args.get("format") == "brief":
        return jsonify({"status": "ok", "brief": situation.brief(snap),
                        "took_ms": snap.get("took_ms")})
    return jsonify({"status": "ok", "situation": snap})
