"""The Friday Edition — E0 API. Thin Flask wrapper over services/edition_engine.

Every response that carries cards goes through edition_engine.gate_cards()
before it's built (the composer already gates internally), and this module
never constructs a card itself — it only reads what compose()/latest_edition()
already produced, so the no-receipt-no-render guarantee holds end to end.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from agent_friday.services import edition_engine as ee

edition_bp = Blueprint('edition', __name__)


@edition_bp.route('/api/edition/latest')
def edition_latest():
    """The most recent composed edition (or null if none yet), plus the index."""
    edition = ee.latest_edition()
    return jsonify({"status": "ok", "edition": edition, "editions": ee.list_editions()})


@edition_bp.route('/api/edition/<edition_id>')
def edition_get(edition_id):
    edition = ee.get_edition(edition_id)
    if not edition:
        return jsonify({"status": "not_found"}), 404
    return jsonify({"status": "ok", "edition": edition})


@edition_bp.route('/api/edition/compose', methods=['POST'])
def edition_compose():
    """Compose now, on demand."""
    try:
        edition = ee.compose()
        return jsonify({"status": "ok", "edition": edition})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@edition_bp.route('/api/edition/charter', methods=['GET', 'POST'])
def edition_charter():
    """GET the current charter (+ parsed clauses). POST a new charter body
    ({text}) — versioned; takes effect on the next compose()."""
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        text = data.get("text") or ""
        try:
            charter = ee.write_charter(text)
            return jsonify({"status": "ok", "charter": charter})
        except ValueError as e:
            return jsonify({"status": "error", "message": str(e)}), 400
    return jsonify({"status": "ok", "charter": ee.read_charter()})


@edition_bp.route('/api/edition/verb', methods=['POST'])
def edition_verb():
    """Log a card verb: keep / kill / more_like_this / why. When verb=='why'
    with a non-empty reason, this doubles as the retraction/correction
    mechanism — it will surface under Corrections in the next compose()."""
    data = request.get_json(silent=True) or {}
    card_id = (data.get("card_id") or "").strip()
    verb = (data.get("verb") or "").strip()
    if not card_id or verb not in ee.VERBS:
        return jsonify({"status": "error",
                        "message": f"card_id required; verb must be one of {ee.VERBS}"}), 400
    try:
        entry = ee.append_verb(
            card_id, verb,
            reason=data.get("reason") or "",
            card_title=data.get("card_title") or "",
            card_section=data.get("card_section") or "",
            card_source=data.get("card_source") or "",
        )
        return jsonify({"status": "ok", "entry": entry})
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
