"""Media: one card per piece of work (services/media_index.py).

| Route                                  | Method | What it does                                      |
|----------------------------------------|--------|---------------------------------------------------|
| /api/media                             | GET    | cards for a view, with filters, counts, projects  |
| /api/media                             | POST   | a new card Media owns (idea or draft)             |
| /api/media/calendar                    | GET    | timed cards between ?from and ?to                 |
| /api/media/reindex                     | POST   | walk the roots again                              |
| /api/media/<id>                        | GET    | one card, its relations and its text              |
| /api/media/<id>                        | PATCH  | status (never published), project, title, when   |
| /api/media/<id>                        | DELETE | the card (a file stays; Files 3D deletes files)   |
| /api/media/<id>/body                   | PUT    | the text                                          |
| /api/media/<id>/publish                | POST   | raises the one approval card                      |
| /api/media/<id>/unpublish              | POST   | back to a draft on this PC                        |
| /api/media/<id>/turn-into              | POST   | a new card made from this one                     |
| /api/media/<id>/file                   | GET    | the file behind a file-backed card                |
| /api/media/<id>/render/<n>             | GET    | a document's rendered page                        |

Local user only, like podcasts. Every write wants a JSON body from this origin.
"""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

from agent_friday.services import media_index as mi

media_bp = Blueprint('media', __name__)


@media_bp.before_request
def _gate():
    try:
        from flask import g
        if (getattr(g, "friday_principal", "user") or "user") != "user":
            return jsonify({"status": "denied", "message": "Media is only available to the local user"}), 403
        from agent_friday.core import _is_local_request
        if not _is_local_request():
            return jsonify({"status": "denied", "message": "Media is only available on this machine"}), 403
    except Exception:
        return jsonify({"status": "denied", "message": "could not establish that this request is local"}), 403
    mi.register_hooks()
    return None


def _json() -> dict:
    return request.get_json(silent=True) or {}


@media_bp.route('/api/media', methods=['GET'])
def media_list():
    a = request.args
    try:
        limit = int(a.get('limit', 200))
    except ValueError:
        limit = 200
    res = mi.query(
        view=a.get('view', 'all') or 'all', q=a.get('q', '') or '', kind=a.get('kind') or None,
        project=a.get('project') if 'project' in a else None, privacy=a.get('privacy') or None,
        unsigned=a.get('unsigned') in ('1', 'true'), status=a.get('status') or None,
        sort=a.get('sort', 'next') or 'next', limit=limit, offset=int(a.get('offset', 0) or 0),
    )
    res["status"] = "ok"
    return jsonify(res)


@media_bp.route('/api/media', methods=['POST'])
def media_create():
    b = _json()
    card = mi.create_card(kind=str(b.get('kind') or 'draft'), title=str(b.get('title') or 'Untitled'), body=str(b.get('body') or ''),
                          project=b.get('project') or None, status=str(b.get('status') or 'idea'))
    return jsonify({"status": "ok", "card": card})


@media_bp.route('/api/media/calendar', methods=['GET'])
def media_calendar():
    frm = request.args.get('from') or ''
    to = request.args.get('to') or ''
    return jsonify({"status": "ok", "cards": mi.calendar(frm, to)})


@media_bp.route('/api/media/reindex', methods=['POST'])
def media_reindex():
    return jsonify({"status": "ok", "counts": mi.reindex()})


@media_bp.route('/api/media/<card_id>', methods=['GET'])
def media_get(card_id):
    c = mi.get(card_id)
    if c is None:
        return jsonify({"status": "not_found"}), 404
    body = c.pop("body", None)
    rels = c.pop("relations", [])
    return jsonify({"status": "ok", "card": c, "relations": rels, "body": body})


@media_bp.route('/api/media/<card_id>', methods=['PATCH'])
def media_patch(card_id):
    b = _json()
    res = mi.patch(card_id, status=b.get('status'), project=b.get('project'), title=b.get('title'), when=b.get('when'))
    code = {"not_found": 404, "denied": 403, "error": 400}.get(res.get("status"), 200)
    return jsonify(res), code


@media_bp.route('/api/media/<card_id>', methods=['DELETE'])
def media_delete(card_id):
    res = mi.delete(card_id)
    code = {"not_found": 404, "denied": 403, "error": 400}.get(res.get("status"), 200)
    return jsonify(res), code


@media_bp.route('/api/media/<card_id>/body', methods=['PUT'])
def media_body(card_id):
    b = _json()
    res = mi.set_body(card_id, str(b.get('text') or ''))
    code = {"not_found": 404, "denied": 403, "error": 400}.get(res.get("status"), 200)
    return jsonify(res), code


@media_bp.route('/api/media/<card_id>/publish', methods=['POST'])
def media_publish(card_id):
    res = mi.publish(card_id)
    code = {"not_found": 404, "denied": 403, "error": 500, "pending": 202}.get(res.get("status"), 200)
    return jsonify(res), code


@media_bp.route('/api/media/<card_id>/unpublish', methods=['POST'])
def media_unpublish(card_id):
    res = mi.unpublish(card_id)
    code = {"not_found": 404, "denied": 403}.get(res.get("status"), 200)
    return jsonify(res), code


@media_bp.route('/api/media/<card_id>/turn-into', methods=['POST'])
def media_turn_into(card_id):
    b = _json()
    res = mi.turn_into(card_id, str(b.get('kind') or ''))
    code = {"not_found": 404, "denied": 403, "error": 400, "unavailable": 501}.get(res.get("status"), 200)
    return jsonify(res), code


@media_bp.route('/api/media/<card_id>/file', methods=['GET'])
def media_file(card_id):
    c = mi.get(card_id)
    if c is None or not c.get("path") or not Path(c["path"]).is_file():
        return jsonify({"status": "not_found"}), 404
    return send_file(c["path"], conditional=True)


@media_bp.route('/api/media/<card_id>/render/<int:n>', methods=['GET'])
def media_render(card_id, n):
    c = mi.get(card_id)
    renders = ((c or {}).get("extra") or {}).get("renders") or []
    if not c or n < 0 or n >= len(renders) or not Path(renders[n]).is_file():
        return jsonify({"status": "not_found"}), 404
    return send_file(renders[n], conditional=True)
