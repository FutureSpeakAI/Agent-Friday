"""Doctor and safe mode (services/doctor.py, services/safe_mode.py).

  GET  /doctor                        the Doctor page (plain, local)
  POST /api/doctor/report             run the checks now; the report is kept
                                      under <home>/doctor/reports/
  GET  /api/doctor/reports            the newest saved reports (id, time, counts)
  GET  /api/doctor/reports/<id>.<json|txt>
                                      a saved report as a file to save and
                                      attach by hand (A3: never sent by Friday)
  GET  /api/safe-mode                 is this run in safe mode, and the next?
  POST /api/safe-mode                 {"next_start": true|false}: start in safe
                                      mode next time, or leave it. A press on
                                      Friday's own page only.

Nothing here sends anything anywhere. Not on the observer's read-only list.
"""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

from agent_friday.core import login_required
from agent_friday.routes._errors import api_error
from agent_friday.services import doctor, safe_mode

doctor_bp = Blueprint("doctor", __name__)

_PAGE = Path(__file__).resolve().parents[3] / "static" / "doctor.html"


def _page_path() -> Path:
    # the same folder /static/ is served from (core_routes.serve_static_asset)
    import os
    for p in (Path(os.path.abspath("static")) / "doctor.html", _PAGE):
        if p.is_file():
            return p
    return _PAGE


@doctor_bp.route("/doctor", methods=["GET"])
@login_required
def doctor_page():
    from flask import Response

    from agent_friday import core
    if not core._is_local_request():
        return "The Doctor opens on this PC only.", 403
    try:
        html = _page_path().read_text(encoding="utf-8")
    except OSError:
        return "Doctor page not found", 404
    # the session token, injected the way serve_ui does it (state changes
    # from this page carry it; the safe-mode switch also needs the page click)
    tok = ('<script>window.__FRIDAY_API_TOKEN="%s";</script>'  # pragma: allowlist secret
           % core._current_api_token())
    html = html.replace("<head>", "<head>\n" + tok, 1)
    resp = Response(html, content_type="text/html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@doctor_bp.route("/api/doctor/report", methods=["POST"])
@login_required
def doctor_report():
    from agent_friday.core import _is_local_request
    if not _is_local_request():
        return jsonify({"status": "error", "error": "The Doctor runs on this PC only."}), 403
    try:
        rep = doctor.save()
        return jsonify({"status": "ok", "report": rep, "text": doctor.render_text(rep)})
    except Exception as e:  # noqa: BLE001
        return api_error(e, "The Doctor report could not be made.")


@doctor_bp.route("/api/doctor/reports", methods=["GET"])
@login_required
def doctor_reports():
    try:
        return jsonify({"status": "ok", "reports": doctor.history()})
    except Exception as e:  # noqa: BLE001
        return api_error(e, "The saved Doctor reports could not be listed.")


@doctor_bp.route("/api/doctor/reports/<rid>.<kind>", methods=["GET"])
@login_required
def doctor_report_file(rid, kind):
    p = doctor.saved(rid, kind)
    if p is None:
        return jsonify({"status": "error", "error": "No such report."}), 404
    resp = send_file(str(p), mimetype="application/json" if kind == "json" else "text/plain",
                     as_attachment=True, download_name=p.name)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@doctor_bp.route("/api/safe-mode", methods=["GET"])
@login_required
def safe_mode_state():
    return jsonify({"status": "ok", **safe_mode.plan()})


@doctor_bp.route("/api/safe-mode", methods=["POST"])
@login_required
def safe_mode_set():
    from agent_friday.core import _is_local_request
    from agent_friday.services import screen_click
    # this PC only: safe mode is never switched from a page reached through a
    # tunnel or the LAN
    if not (_is_local_request() and screen_click.screen_session(request)):
        return jsonify({"status": "error", "code": "press_required",
                        "error": "Choose this on Friday's own page, on this PC."}), 403
    body = request.get_json(silent=True) or {}
    if not isinstance(body.get("next_start"), bool):
        return jsonify({"status": "error", "error": "next_start must be true or false."}), 400
    try:
        plan = safe_mode.set_next_start(body["next_start"])
        try:
            from agent_friday.services import action_ledger
            action_ledger.try_append("decision", {
                "tool": "safe_mode:next_start", "class": "owner", "decision": "allow",
                "surface": "doctor", "reason": "on" if body["next_start"] else "off"})
        except Exception:
            pass
        return jsonify({"status": "ok", **plan,
                        "restart_needed": plan["next_start_safe"] != plan["active"]})
    except Exception as e:  # noqa: BLE001
        return api_error(e, "Safe mode could not be set for the next start.")
