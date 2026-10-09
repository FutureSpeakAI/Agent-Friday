"""The avatar's look: the genome the page draws, its history, and the weekly
step's controls (docs/design/active/avatar-visual-genome.md §7, §8).

GET  /api/avatar/genome      the active genome as drawn: expression, palette, sigil
GET  /api/avatar/history     every step, with verification and flags
GET  /api/avatar/status      on/off, author, apply mode, waiting, next due
POST /api/avatar/settings    {enabled?, author?, apply_mode?}
POST /api/avatar/evolve-now  run a step now
POST /api/avatar/use-local   {model}: the waiting notice's "use it instead"
POST /api/avatar/undo        back to the previous look
POST /api/avatar/rollback    {step}: any earlier look
POST /api/avatar/reset       back to Friday as she first looked
POST /api/avatar/pending     {action: apply|decline} in ask-first mode
POST /api/avatar/hide        {step, hidden}
POST /api/avatar/delete      {step}: to trash, restorable for 30 days
POST /api/avatar/restore     {step}

The seed never leaves this machine through any of these.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from agent_friday.core import login_required
from agent_friday.routes._errors import api_error

avatar_bp = Blueprint("avatar", __name__)


def _g():
    from agent_friday.services import avatar_genome
    return avatar_genome


def _gr():
    from agent_friday.services import avatar_growth
    return avatar_growth


def _step_arg():
    body = request.get_json(silent=True) or {}
    return body.get("step")


def _missing():
    return jsonify({"status": "error", "error": "no such look"}), 404


@avatar_bp.route("/api/avatar/genome", methods=["GET"])
@login_required
def avatar_genome_route():
    return jsonify(_g().public_view())


@avatar_bp.route("/api/avatar/history", methods=["GET"])
@login_required
def avatar_history_route():
    g = _g()
    st = g.state()
    return jsonify({"steps": g.history(), "active": st.get("active"),
                    "undone": st.get("undone", []), "deleted": st.get("deleted", {})})


@avatar_bp.route("/api/avatar/status", methods=["GET"])
@login_required
def avatar_status_route():
    return jsonify(_gr().status())


@avatar_bp.route("/api/avatar/settings", methods=["POST"])
@login_required
def avatar_settings_route():
    body = request.get_json(silent=True) or {}
    try:
        _gr().set_settings(enabled=body.get("enabled"), author=body.get("author"),
                           apply_mode=body.get("apply_mode"))
    except ValueError as e:
        return api_error(e, "Couldn't change that avatar setting", 400, key="error")
    return jsonify(_gr().status())


@avatar_bp.route("/api/avatar/evolve-now", methods=["POST"])
@login_required
def avatar_evolve_now_route():
    return jsonify(_gr().evolve_now())


@avatar_bp.route("/api/avatar/use-local", methods=["POST"])
@login_required
def avatar_use_local_route():
    model = (request.get_json(silent=True) or {}).get("model")
    try:
        return jsonify(_gr().use_local_instead(model))
    except ValueError as e:
        return api_error(e, "Couldn't change that avatar setting", 400, key="error")


@avatar_bp.route("/api/avatar/undo", methods=["POST"])
@login_required
def avatar_undo_route():
    return jsonify(_gr().undo())


@avatar_bp.route("/api/avatar/rollback", methods=["POST"])
@login_required
def avatar_rollback_route():
    try:
        step = _g().rollback(_step_arg())
    except (KeyError, TypeError):
        return _missing()
    except ValueError as e:
        return api_error(e, "Couldn't change that avatar step", 409, key="error")
    return jsonify({"status": "ok", "step": step["content_hash"]})


@avatar_bp.route("/api/avatar/reset", methods=["POST"])
@login_required
def avatar_reset_route():
    _g().reset()
    return jsonify({"status": "ok"})


@avatar_bp.route("/api/avatar/pending", methods=["POST"])
@login_required
def avatar_pending_route():
    action = (request.get_json(silent=True) or {}).get("action")
    if action == "apply":
        return jsonify(_gr().apply_pending())
    if action == "decline":
        return jsonify(_gr().decline_pending())
    return jsonify({"status": "error", "error": "action must be apply or decline"}), 400


@avatar_bp.route("/api/avatar/hide", methods=["POST"])
@login_required
def avatar_hide_route():
    body = request.get_json(silent=True) or {}
    try:
        if not _g().load_step(body.get("step")):
            return _missing()
    except (KeyError, TypeError):
        return _missing()
    _g().set_hidden(body.get("step"), bool(body.get("hidden", True)))
    return jsonify({"status": "ok"})


@avatar_bp.route("/api/avatar/delete", methods=["POST"])
@login_required
def avatar_delete_route():
    try:
        _g().delete(_step_arg())
    except (KeyError, TypeError):
        return _missing()
    except ValueError as e:
        return api_error(e, "Couldn't change that avatar step", 409, key="error")
    return jsonify({"status": "ok"})


@avatar_bp.route("/api/avatar/restore", methods=["POST"])
@login_required
def avatar_restore_route():
    try:
        _g().restore(_step_arg())
    except (KeyError, TypeError):
        return _missing()
    return jsonify({"status": "ok"})
