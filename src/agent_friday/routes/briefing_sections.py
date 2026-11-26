"""The briefing's section skills, for the News workspace's Customize Briefing panel.

Thin: the list, its states and the one toggle live in services/briefing_sections.

GET  /api/briefing/sections                    -> {status, sections: [{skill, heading, days, enabled, state, today}]}
POST /api/briefing/sections {skill, enabled}   -> the same list after the change; 404 for a skill not in the list
"""
from datetime import datetime

from flask import Blueprint, jsonify, request

from agent_friday.routes._errors import api_error

briefing_sections_bp = Blueprint('briefing_sections', __name__)


@briefing_sections_bp.route('/api/briefing/sections', methods=['GET', 'POST'])
def briefing_section_skills():
    from agent_friday.services import briefing_sections
    try:
        if request.method == 'POST':
            data = request.get_json(silent=True) or {}
            skill, enabled = data.get("skill"), data.get("enabled")
            if not isinstance(skill, str) or not isinstance(enabled, bool):
                return jsonify({"status": "error", "message": "Send a skill name and enabled true or false."}), 400
            try:
                briefing_sections.set_enabled(skill, enabled)
            except KeyError:
                return jsonify({"status": "error", "message": "That skill is not one of the briefing's sections."}), 404
        return jsonify({"status": "ok", "sections": briefing_sections.overview(datetime.now())})
    except Exception as e:
        return api_error(e, "Couldn't read the briefing's sections")
