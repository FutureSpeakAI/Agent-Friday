"""Output-liveness audit — one page answering "is anything quietly idle?"

  GET /api/liveness        JSON report
  GET /api/liveness?text=1 the plain-text page

Read-only. It runs no jobs and repairs nothing.
"""
from flask import Blueprint, jsonify, request, Response

liveness_bp = Blueprint("liveness", __name__)


@liveness_bp.route("/api/liveness", methods=["GET"])
def liveness():
    from agent_friday.services import liveness_audit
    report = liveness_audit.audit()
    if request.args.get("text"):
        return Response(liveness_audit.render_text(report),
                        mimetype="text/plain; charset=utf-8")
    return jsonify(report)
