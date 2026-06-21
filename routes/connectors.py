"""routes/connectors.py — one-click connector registry API.

A single, uniform surface over every external integration (Google OAuth +
the Slack/GitHub/Linear/Notion/Discord MCP connectors). The UI renders a card
per connector from GET /api/connectors and drives connect/disconnect from here;
all the actual logic lives in services/connectors.py.

    GET  /api/connectors                  → all connectors + health summary
    GET  /api/connectors/health           → ambient health snapshot
    GET  /api/connectors/intelligence     → cross-connector briefing signals
    GET  /api/connectors/<key>            → one connector's live status
    POST /api/connectors/<key>/connect    → initiate a connection
    POST /api/connectors/<key>/disconnect → tear down a connection
"""

import traceback
from flask import Blueprint, jsonify, request

from services.connectors import (
    list_connectors,
    get_connector,
    connect_connector,
    disconnect_connector,
    connectors_health,
    connector_intelligence,
    meeting_context,
    workspace_connectors,
)

connectors_bp = Blueprint('connectors', __name__)


@connectors_bp.route('/api/connectors', methods=['GET'])
def api_connectors_list():
    """Every connector with live status, plus a one-line health summary."""
    try:
        conns = list_connectors()
        health = connectors_health()
        return jsonify({
            "status": "ok",
            "connectors": conns,
            "health": {
                "summary": health["summary"],
                "connected": health["connected"],
                "total": health["total"],
                "degraded": health["degraded"],
                "connecting": health["connecting"],
            },
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@connectors_bp.route('/api/connectors/health', methods=['GET'])
def api_connectors_health():
    """Ambient health snapshot — drives the connector status strip + monitoring."""
    try:
        return jsonify({"status": "ok", "health": connectors_health()})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@connectors_bp.route('/api/connectors/intelligence', methods=['GET'])
def api_connectors_intelligence():
    """Cross-connector signals synthesized for the morning briefing."""
    try:
        return jsonify({"status": "ok", "intelligence": connector_intelligence()})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@connectors_bp.route('/api/connectors/meeting-context', methods=['POST'])
def api_connectors_meeting_context():
    """Cross-connector context for a calendar meeting.

    Body: an event ({title/summary, attendees:[...]}). Returns recent Gmail
    threads with the attendees plus which other connected sources could be
    queried — so the agent can prep a meeting from every connected source, not
    just the calendar entry itself.
    """
    try:
        event = request.get_json(silent=True) or {}
        return jsonify({"status": "ok", "context": meeting_context(event)})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@connectors_bp.route('/api/connectors/<key>', methods=['GET'])
@connectors_bp.route('/api/connectors/<key>/status', methods=['GET'])
def api_connector_get(key):
    """Live status for a single connector."""
    try:
        conn = get_connector(key)
        if conn is None:
            return jsonify({"status": "error", "message": f"unknown connector {key!r}"}), 404
        return jsonify({"status": "ok", "connector": conn})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@connectors_bp.route('/api/workspaces/<ws>/connectors', methods=['GET'])
def api_workspace_connectors(ws):
    """Connectors feeding a given workspace, with live status — drives the
    'Connected Sources' indicator a workspace header can render."""
    try:
        return jsonify({"status": "ok", "workspace": ws,
                        "connectors": workspace_connectors(ws)})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@connectors_bp.route('/api/connectors/<key>/connect', methods=['POST'])
def api_connector_connect(key):
    """Initiate a connection. For OAuth this opens the consent flow; for MCP it
    accepts the credential fields ({FIELD_KEY: value}) and starts the server."""
    try:
        if get_connector(key) is None:
            return jsonify({"status": "error", "message": f"unknown connector {key!r}"}), 404
        data = request.get_json(silent=True) or {}
        host_url = request.host_url
        result = connect_connector(key, data, host_url=host_url)
        # Always return the freshest status so the UI can update its badge.
        result["connector"] = get_connector(key)
        code = 200 if result.get("ok") else 400
        return jsonify({"status": "ok" if result.get("ok") else "error", **result}), code
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@connectors_bp.route('/api/connectors/<key>/disconnect', methods=['POST'])
def api_connector_disconnect(key):
    """Tear down a connection (OAuth token removed; MCP server disabled)."""
    try:
        if get_connector(key) is None:
            return jsonify({"status": "error", "message": f"unknown connector {key!r}"}), 404
        result = disconnect_connector(key)
        result["connector"] = get_connector(key)
        code = 200 if result.get("ok") else 400
        return jsonify({"status": "ok" if result.get("ok") else "error", **result}), code
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500
