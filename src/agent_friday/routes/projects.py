"""
Routes — Creative Project Manager + Series Bible.

CRUD over creative projects and their Series Bible (characters, locations,
continuity, style guide, asset gallery). Backed by services/creative_memory.

By convention here, mutation endpoints return HTTP 200 with the status in the
body (matching the creative-generation routes) so the UI gets a uniform
envelope; genuine server faults still surface as 500.
"""
import traceback
from flask import Blueprint, jsonify, request

from agent_friday.services import creative_memory as cm

projects_bp = Blueprint('projects', __name__)


def _err(e, code=500):
    traceback.print_exc()
    return jsonify({"status": "error", "message": str(e)}), code


# ═══ PROJECTS ════════════════════════════════════════════════════
@projects_bp.route('/api/projects', methods=['GET'])
def projects_list():
    try:
        return jsonify({"status": "ok", "projects": cm.list_projects(),
                        "types": list(cm.PROJECT_TYPES)})
    except Exception as e:
        return _err(e)


@projects_bp.route('/api/projects', methods=['POST'])
def projects_create():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({"status": "error", "message": "name required"}), 200
    try:
        bible = cm.create_project(
            name, data.get('type') or 'general',
            style_guide=data.get('style_guide'),
            make_active=data.get('make_active', True))
        return jsonify({"status": "ok", "project": bible})
    except Exception as e:
        return _err(e)


@projects_bp.route('/api/projects/active', methods=['GET'])
def projects_active():
    try:
        return jsonify({"status": "ok", "project": cm.get_active_project(),
                        "active_id": cm.get_active_project_id()})
    except Exception as e:
        return _err(e)


@projects_bp.route('/api/projects/<pid>', methods=['GET'])
def projects_get(pid):
    try:
        bible = cm.get_project(pid)
        if not bible:
            return jsonify({"status": "error", "message": "not found"}), 404
        return jsonify({"status": "ok", "project": bible})
    except Exception as e:
        return _err(e)


@projects_bp.route('/api/projects/<pid>', methods=['PATCH', 'POST'])
def projects_update(pid):
    data = request.get_json(silent=True) or {}
    try:
        bible = cm.update_project(pid, name=data.get('name'), ptype=data.get('type'))
        if not bible:
            return jsonify({"status": "error", "message": "not found"}), 404
        return jsonify({"status": "ok", "project": bible})
    except Exception as e:
        return _err(e)


@projects_bp.route('/api/projects/<pid>', methods=['DELETE'])
def projects_delete(pid):
    try:
        return jsonify({"status": "ok", "deleted": cm.delete_project(pid)})
    except Exception as e:
        return _err(e)


@projects_bp.route('/api/projects/<pid>/activate', methods=['POST'])
def projects_activate(pid):
    try:
        if not cm.get_project(pid):
            return jsonify({"status": "error", "message": "not found"}), 404
        cm.set_active_project(pid)
        return jsonify({"status": "ok", "active_id": pid})
    except Exception as e:
        return _err(e)


# ═══ CHARACTERS ══════════════════════════════════════════════════
@projects_bp.route('/api/projects/<pid>/characters', methods=['POST'])
def character_add(pid):
    data = request.get_json(silent=True) or {}
    try:
        rec = cm.add_character(
            pid, data.get('name') or '',
            visual_description=data.get('visual_description') or '',
            voice_profile=data.get('voice_profile') or '',
            aliases=data.get('aliases'), notes=data.get('notes') or '')
        if rec is None:
            return jsonify({"status": "error",
                            "message": "project not found or name missing"}), 200
        return jsonify({"status": "ok", "character": rec})
    except Exception as e:
        return _err(e)


@projects_bp.route('/api/projects/<pid>/characters/<name>', methods=['DELETE'])
def character_remove(pid, name):
    try:
        return jsonify({"status": "ok", "removed": cm.remove_character(pid, name)})
    except Exception as e:
        return _err(e)


# ═══ LOCATIONS ═══════════════════════════════════════════════════
@projects_bp.route('/api/projects/<pid>/locations', methods=['POST'])
def location_add(pid):
    data = request.get_json(silent=True) or {}
    try:
        rec = cm.add_location(pid, data.get('name') or '',
                              description=data.get('description') or '',
                              notes=data.get('notes') or '')
        if rec is None:
            return jsonify({"status": "error",
                            "message": "project not found or name missing"}), 200
        return jsonify({"status": "ok", "location": rec})
    except Exception as e:
        return _err(e)


@projects_bp.route('/api/projects/<pid>/locations/<name>', methods=['DELETE'])
def location_remove(pid, name):
    try:
        return jsonify({"status": "ok", "removed": cm.remove_location(pid, name)})
    except Exception as e:
        return _err(e)


# ═══ CONTINUITY ══════════════════════════════════════════════════
@projects_bp.route('/api/projects/<pid>/continuity', methods=['POST'])
def continuity_add(pid):
    data = request.get_json(silent=True) or {}
    try:
        entry = cm.add_continuity(pid, data.get('note') or '',
                                  scene=data.get('scene') or '')
        if entry is None:
            return jsonify({"status": "error",
                            "message": "project not found or note missing"}), 200
        return jsonify({"status": "ok", "entry": entry})
    except Exception as e:
        return _err(e)


@projects_bp.route('/api/projects/<pid>/continuity', methods=['GET'])
def continuity_list(pid):
    try:
        return jsonify({"status": "ok", "continuity": cm.list_continuity(pid)})
    except Exception as e:
        return _err(e)


# ═══ STYLE GUIDE + ASSETS ════════════════════════════════════════
@projects_bp.route('/api/projects/<pid>/style', methods=['PUT', 'POST'])
def style_set(pid):
    data = request.get_json(silent=True) or {}
    try:
        sg = cm.set_style_guide(pid, data.get('style_guide') or data,
                                merge=data.get('merge', True))
        if sg is None:
            return jsonify({"status": "error", "message": "not found"}), 404
        return jsonify({"status": "ok", "style_guide": sg})
    except Exception as e:
        return _err(e)


@projects_bp.route('/api/projects/<pid>/assets', methods=['GET'])
def assets_list(pid):
    try:
        return jsonify({"status": "ok", "assets": cm.list_assets(pid)})
    except Exception as e:
        return _err(e)
