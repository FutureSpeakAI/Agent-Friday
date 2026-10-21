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


# ── Backup and restore (services/friday_bundle.py) ──────────────────────────

import threading as _threading

_BUNDLE_JOB: dict = {"state": "idle"}
_BUNDLE_LOCK = _threading.Lock()


def _press_here():
    from agent_friday.core import _is_local_request
    from agent_friday.services import screen_click
    return _is_local_request() and screen_click.screen_session(request)


@doctor_bp.route("/api/restore/state", methods=["GET"])
@login_required
def restore_state():
    from agent_friday.services import friday_bundle
    why = friday_bundle.restore_hold()
    return jsonify({"status": "ok", "held": bool(why), "why": why})


@doctor_bp.route("/api/restore/resume", methods=["POST"])
@login_required
def restore_resume():
    if not _press_here():
        return jsonify({"status": "error", "code": "press_required",
                        "error": "Resume on Friday's own page, on this PC."}), 403
    from agent_friday.services import friday_bundle
    try:
        done = friday_bundle.resume_after_restore("owner:ui")
        return jsonify({"status": "ok", "resumed": done})
    except Exception as e:  # noqa: BLE001
        return api_error(e, "Outward actions could not be resumed.")


@doctor_bp.route("/api/bundle/create", methods=["POST"])
@login_required
def bundle_create():
    """Make a Friday Bundle in Documents/Friday Bundles, then verify it. Runs in
    the background; GET /api/bundle/status follows it. The passphrase is used
    in memory and never stored."""
    if not _press_here():
        return jsonify({"status": "error", "code": "press_required",
                        "error": "Make a bundle from Friday's own page, on this PC."}), 403
    from agent_friday.services import friday_bundle as fb
    body = request.get_json(silent=True) or {}
    pw = body.get("passphrase") or ""
    if not isinstance(pw, str) or len(pw) < fb.MIN_PASSPHRASE:
        return jsonify({"status": "error", "error": "The passphrase must be at least %d characters."
                        % fb.MIN_PASSPHRASE}), 400
    cats = body.get("categories") or list(fb.CATEGORIES)
    if not isinstance(cats, list) or not all(c in fb.CATEGORIES for c in cats):
        return jsonify({"status": "error", "error": "Unknown category."}), 400
    with _BUNDLE_LOCK:
        if _BUNDLE_JOB.get("state") == "running":
            return jsonify({"status": "error", "error": "A bundle is already being made."}), 409
        _BUNDLE_JOB.clear()
        _BUNDLE_JOB.update(state="running", started=__import__("time").time())

    def _run():
        import time as _t
        from pathlib import Path as _P

        from agent_friday.paths import friday_home
        dest_dir = _P.home() / "Documents" / "Friday Bundles"
        dest = dest_dir / ("friday-%s%s" % (_t.strftime("%Y%m%d-%H%M%S"), fb.SUFFIX))
        try:
            m = fb.create(dest, pw, categories=cats)
            v = fb.verify(dest, pw)
            fb._remember(_P(friday_home()), dest, m["bundle_id"], verified=v["ok"])
            _BUNDLE_JOB.update(state="done", bundle_id=m["bundle_id"], file=dest.name,
                               files=len(m["files"]), verified=v["ok"])
        except Exception as e:  # noqa: BLE001
            _BUNDLE_JOB.update(state="failed", error=str(e) if isinstance(e, fb.BundleError)
                               else "The bundle could not be made (%s)." % type(e).__name__)
    _threading.Thread(target=_run, name="friday-bundle", daemon=True).start()
    return jsonify({"status": "ok", "state": "running"})


@doctor_bp.route("/api/bundle/status", methods=["GET"])
@login_required
def bundle_status():
    return jsonify({"status": "ok", **{k: v for k, v in _BUNDLE_JOB.items()}})
