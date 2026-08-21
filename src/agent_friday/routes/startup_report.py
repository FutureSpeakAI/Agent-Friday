"""Startup integrity endpoint - what loaded, what didn't, and what that costs.

Exists because a whole API surface (routes/jobs.py, the career pipeline) went
missing for seven weeks and ~70 restarts while the server reported itself
healthy. The only trace was a WARNING in a log nobody opens. See
docs/audits/server-death-forensics.md.

Standing principle: degradation must cost something visible. This is the
machine-readable half of that - the UI and any monitoring can poll one endpoint
and know whether the app is whole. The human-readable halves are a high-priority
notification and ~/.friday/startup-report.json, both raised by
server._enforce_blueprint_policy().

NOTE: /api/health does not yet carry this block. Adding it is a small edit to
routes/core_routes.py, which was owned by another in-flight change at the time
of writing and deliberately left untouched. Until then this endpoint is the
authoritative source.
"""
from __future__ import annotations

import json
import sys

from flask import Blueprint, jsonify

from agent_friday.core import FRIDAY_DIR

startup_report_bp = Blueprint("startup_report", __name__)

_REPORT_FILE = FRIDAY_DIR / "startup-report.json"


def _server_module():
    """The already-imported server module, or None.

    Looked up through sys.modules rather than imported: routes/ is imported BY
    server.py during discovery, so a top-level `import agent_friday.server`
    here would be circular. At request time the module is long since loaded.
    """
    return sys.modules.get("agent_friday.server")


def _live_report() -> dict | None:
    srv = _server_module()
    if srv is None:
        return None
    report = getattr(srv, "BLUEPRINT_REPORT", None)
    return report if isinstance(report, dict) and report.get("registered") else None


def _disk_report() -> dict | None:
    try:
        data = json.loads(_REPORT_FILE.read_text(encoding="utf-8"))
        blueprints = data.get("blueprints")
        return blueprints if isinstance(blueprints, dict) else None
    except Exception:
        return None


def _labels() -> dict:
    srv = _server_module()
    return getattr(srv, "ROUTE_LABELS", {}) if srv is not None else {}


@startup_report_bp.route("/api/startup-report", methods=["GET"])
def api_startup_report():
    """Report whether every route module registered.

    `status` is "ok" or "degraded". HTTP stays 200 either way: the tray treats
    a non-2xx as "server is dead" and would restart a server that is running
    fine but incomplete. Liveness and integrity are different signals - the
    same reasoning /api/health already applies to inference health.
    """
    report = _live_report() or _disk_report() or {
        "registered": [], "skipped": [], "blueprint_count": 0,
    }
    skipped = report.get("skipped") or []
    labels = _labels()

    return jsonify({
        "status": "degraded" if skipped else "ok",
        "degraded_capabilities": [
            labels.get(s.get("module"), s.get("module")) for s in skipped
        ],
        "blueprints": {
            "registered_count": len(report.get("registered") or []),
            "blueprint_count": report.get("blueprint_count", 0),
            "skipped": skipped,
        },
        "report_file": str(_REPORT_FILE),
    })
