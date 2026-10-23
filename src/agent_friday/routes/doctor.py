"""Doctor and safe mode (services/doctor.py, services/safe_mode.py).

  GET  /doctor                        the Doctor page (plain, local)
  POST /api/doctor/report             run the checks now; the report is kept
                                      under <home>/doctor/reports/
  GET  /api/doctor/reports            the newest saved reports (id, time, counts)
  GET  /api/doctor/reports/<id>.<json|txt>
                                      a saved report as a file to save and
                                      attach by hand (A3: never sent by Friday)
  GET  /api/doctor/stores             every store Friday keeps: version, size,
                                      location (read-only, this PC only)
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


@doctor_bp.route("/api/doctor/stores", methods=["GET"])
@login_required
def doctor_stores():
    """Every store Friday keeps: version, size, location (§37.3-1). Read-only;
    on this PC only, like the page that shows it."""
    from agent_friday.core import _is_local_request
    if not _is_local_request():
        return jsonify({"status": "error", "error": "The Doctor runs on this PC only."}), 403
    try:
        from agent_friday.services import store_inventory
        inv = store_inventory.inventory(doctor._home())
        resp = jsonify({"status": "ok", **doctor._clean(inv, doctor._private_markers())})
        resp.headers["Cache-Control"] = "no-store"
        return resp
    except Exception as e:  # noqa: BLE001
        return api_error(e, "The list of stores could not be made.")


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
    from agent_friday.services import passphrase_strength
    weak = passphrase_strength.refusal(pw)
    if weak:
        return jsonify({"status": "error", "code": "weak_passphrase", "error": weak}), 400
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
        dest_dir = fb.documents_dir() / "Friday Bundles"
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


# ── Scheduled bundles and the strength meter (services/backup_schedule.py) ──

@doctor_bp.route("/api/bundle/strength", methods=["POST"])
@login_required
def bundle_strength():
    """How hard a bundle passphrase is to guess. This PC only; nothing kept."""
    from agent_friday.core import _is_local_request
    if not _is_local_request():
        return jsonify({"status": "error", "error": "On this PC only."}), 403
    from agent_friday.services import passphrase_strength
    body = request.get_json(silent=True) or {}
    pw = body.get("passphrase")
    a = passphrase_strength.assess(pw if isinstance(pw, str) else "")
    return jsonify({"status": "ok", **a})


@doctor_bp.route("/api/bundle/schedule", methods=["GET"])
@login_required
def bundle_schedule_get():
    from agent_friday.core import _is_local_request
    from agent_friday.services import backup_schedule
    try:
        st = backup_schedule.public_state()
        if not _is_local_request() and st.get("folder"):
            st["folder"] = "(shown on this PC only)"      # a share name can say a lot
        return jsonify({"status": "ok", "schedule": st})
    except Exception as e:  # noqa: BLE001
        return api_error(e, "The backup schedule could not be read.")


@doctor_bp.route("/api/bundle/schedule", methods=["POST"])
@login_required
def bundle_schedule_set():
    """{"enabled", "every", "folder", "keep", "rehearse_days", "passphrase"}.
    A press on Friday's own page, on this PC."""
    if not _press_here():
        return jsonify({"status": "error", "code": "press_required",
                        "error": "Change the backup schedule on Friday's own page, on this PC."}), 403
    from agent_friday.services import backup_schedule
    body = request.get_json(silent=True) or {}
    try:
        st = backup_schedule.set_schedule(
            enabled=body.get("enabled"), every=str(body.get("every") or "weekly"),
            folder=body.get("folder"), keep=body.get("keep", backup_schedule.DEFAULT_KEEP),
            rehearse_days=body.get("rehearse_days", backup_schedule.DEFAULT_REHEARSE_DAYS),
            passphrase=(body.get("passphrase") or None) if isinstance(body.get("passphrase"), str) else None)
        return jsonify({"status": "ok", "schedule": st})
    except backup_schedule.ScheduleError as e:
        return jsonify({"status": "error", "error": str(e)}), 400
    except Exception as e:  # noqa: BLE001
        return api_error(e, "The backup schedule could not be changed.")


@doctor_bp.route("/api/bundle/schedule/<what>", methods=["POST"])
@login_required
def bundle_schedule_act(what):
    """run: make a scheduled bundle now; rehearse: the restore rehearsal now.
    A press on Friday's own page, on this PC. Runs in the background."""
    if what not in ("run", "rehearse"):
        return jsonify({"status": "error", "error": "Unknown action."}), 404
    if not _press_here():
        return jsonify({"status": "error", "code": "press_required",
                        "error": "Do this on Friday's own page, on this PC."}), 403
    from agent_friday.services import backup_schedule

    def _go():
        try:
            if what == "run":
                backup_schedule.run_now()
            else:
                backup_schedule.rehearse()
        except Exception:  # noqa: BLE001 - the state says what happened
            pass
    _threading.Thread(target=_go, name="bundle-schedule-" + what, daemon=True).start()
    return jsonify({"status": "ok", "started": what})


# ── The weekly "send a Doctor report?" ask (A3; services/doctor_weekly.py) ──

@doctor_bp.route("/api/export/readable", methods=["POST"])
@login_required
def export_readable():
    """Write the human-readable export (services/readable_export.py) to
    Documents/Friday Exports. A press on Friday's own page, on this PC: it
    writes the owner's data out unencrypted."""
    if not _press_here():
        return jsonify({"status": "error", "code": "press_required",
                        "error": "Export on Friday's own page, on this PC."}), 403
    from agent_friday.services import friday_bundle, readable_export
    body = request.get_json(silent=True) or {}
    only = body.get("only")
    if only is not None and not (isinstance(only, list) and all(isinstance(c, str) for c in only)):
        return jsonify({"status": "error", "error": "only is a list of categories."}), 400
    dest = friday_bundle.documents_dir() / "Friday Exports"
    if readable_export.cloud_synced(dest) and body.get("cloud_ok") is not True:
        # the export is unencrypted: a synced Documents folder copies it off this PC
        return jsonify({"status": "error", "code": "cloud_folder", "folder": str(dest),
                        "error": "Documents on this PC is synced to the cloud (%s), so the plain "
                                 "files would be copied off this PC. Export anyway?" % dest}), 409
    try:
        r = readable_export.export(dest, only=only, by="owner:ui")
        return jsonify({"status": "ok", **r})
    except readable_export.ExportError as e:
        return jsonify({"status": "error", "error": str(e)}), 400
    except Exception as e:  # noqa: BLE001
        return api_error(e, "The export could not be made.")


@doctor_bp.route("/api/bundle/first-run", methods=["GET"])
@login_required
def first_backup_status():
    """Whether the first backup is still to be set up (§34.4-9)."""
    from agent_friday.services import first_backup
    return jsonify({"status": "ok", **first_backup.status()})


@doctor_bp.route("/api/bundle/first-run/answer", methods=["POST"])
@login_required
def first_backup_answer():
    """{choice: not_now | never}, a press on Friday's own page, on this PC.
    Saying yes is turning scheduled bundles on."""
    if not _press_here():
        return jsonify({"status": "error", "code": "press_required",
                        "error": "Decide this on Friday's own page, on this PC."}), 403
    from agent_friday.services import first_backup
    body = request.get_json(silent=True) or {}
    try:
        return jsonify({"status": "ok", **first_backup.answer(str(body.get("choice") or ""))})
    except first_backup.FirstBackupError as e:
        return jsonify({"status": "error", "error": str(e)}), 400
    except Exception as e:  # noqa: BLE001
        return api_error(e, "That could not be saved.")


@doctor_bp.route("/api/doctor/weekly", methods=["GET"])
@login_required
def doctor_weekly_status():
    from agent_friday.services import doctor_weekly
    return jsonify({"status": "ok", **doctor_weekly.status()})


@doctor_bp.route("/api/doctor/weekly/<what>", methods=["POST"])
@login_required
def doctor_weekly_act(what):
    """answer {choice, local_only_too} | prepare | send {sha256, account_id} | skip.
    prepare shows the exact text; every other step is a press on Friday's own
    page, on this PC. Send raises the normal approval card: nothing leaves until
    the owner approves it there."""
    from agent_friday.core import _is_local_request
    from agent_friday.services import doctor_weekly
    if what not in ("answer", "prepare", "send", "skip"):
        return jsonify({"status": "error", "error": "Unknown action."}), 404
    if what == "prepare":
        if not _is_local_request():
            return jsonify({"status": "error", "error": "On this PC only."}), 403
    elif not _press_here():
        return jsonify({"status": "error", "code": "press_required",
                        "error": "Decide this on Friday's own page, on this PC."}), 403
    body = request.get_json(silent=True) or {}
    try:
        if what == "answer":
            return jsonify({"status": "ok", **doctor_weekly.answer(
                str(body.get("choice") or ""), local_only_too=body.get("local_only_too") is True)})
        if what == "prepare":
            return jsonify({"status": "ok", **doctor_weekly.prepare()})
        if what == "send":
            return jsonify({"status": "ok", **doctor_weekly.send(
                str(body.get("sha256") or ""), account_id=body.get("account_id") or None)})
        return jsonify({"status": "ok", **doctor_weekly.skip()})
    except doctor_weekly.WeeklyError as e:
        return jsonify({"status": "error", "error": str(e)}), 400
    except Exception as e:  # noqa: BLE001
        return api_error(e, "That could not be done.")


# ── Revoke all action authority (north star §18.12; services/authority.py) ──

@doctor_bp.route("/api/authority", methods=["GET"])
@login_required
def authority_status():
    from agent_friday.services import authority
    return jsonify({"status": "ok", **authority.status()})


@doctor_bp.route("/api/authority/revoke-all", methods=["POST"])
@login_required
def authority_revoke_all():
    """Take back all action authority. The owner's own session (the page's
    token), from this PC or the owner's remote session: taking authority away
    is never made harder than giving it. Restoring is on this PC only."""
    from agent_friday import core
    from agent_friday.services import authority
    if not core._api_token_valid(request.headers.get("X-Friday-Token")):
        return jsonify({"status": "error", "error": "Do this from Friday's own page."}), 403
    try:
        return jsonify({"status": "ok", **authority.revoke_all("owner:ui")})
    except authority.AuthorityError as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@doctor_bp.route("/api/authority/restore", methods=["POST"])
@login_required
def authority_restore():
    if not _press_here():
        return jsonify({"status": "error", "code": "press_required",
                        "error": "Restore action authority on Friday's own page, on this PC."}), 403
    from agent_friday.services import authority
    try:
        return jsonify({"status": "ok", **authority.restore("owner:ui")})
    except authority.AuthorityError as e:
        return jsonify({"status": "error", "error": str(e)}), 500
