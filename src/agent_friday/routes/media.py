"""Media: one card per piece of work (services/media_index.py).

| Route                                  | Method | What it does                                      |
|----------------------------------------|--------|---------------------------------------------------|
| /api/media                             | GET    | cards for a view, with filters, counts, projects  |
| /api/media                             | POST   | a new card Media owns (idea or draft)             |
| /api/media/calendar                    | GET    | timed cards between ?from and ?to                 |
| /api/media/reindex                     | POST   | walk the roots again, now, and wait               |
| /api/media/status                      | GET    | built, building, or fresh; the progress count     |
| /api/media/<id>                        | GET    | one card, its relations and its text              |
| /api/media/<id>                        | PATCH  | status (never published), project, title, when   |
| /api/media/<id>                        | DELETE | the card (a file stays; Files 3D deletes files)   |
| /api/media/<id>/body                   | PUT    | the text                                          |
| /api/media/<id>/publish                | POST   | raises the one approval card                      |
| /api/media/<id>/unpublish              | POST   | back to draft, or to kept for a thing made here   |
| /api/media/<id>/turn-into              | POST   | a new card made from this one                     |
| /api/media/<id>/file                   | GET    | the file behind a file-backed card                |
| /api/media/<id>/render/<n>             | GET    | a document's rendered page                        |
| /api/media/<id>/preview                | GET    | the card's preview image (made locally, cached)   |
| /api/media/<id>/strip                  | GET    | a video's hover-scrub frame strip                 |
| /api/media/<id>/open                   | POST   | open the file in its app on this PC               |
| /api/media/<id>/reveal                 | POST   | show the file in its folder on this PC            |
| /api/media/previews/status             | GET    | the preview pass: pending, done, building         |
| /api/media/collections                 | GET    | the saved collections (smart filters)             |
| /api/media/collections                 | POST   | save one: {name, filters} (+id to rename/refilter)|
| /api/media/collections/<id>            | GET    | the collection's cards, evaluated now             |
| /api/media/collections/<id>            | DELETE | forget the collection; its cards stay             |
| /api/media/bulk                        | POST   | {ids, project?, add_tags?, remove_tags?, favorite?}|
| /api/media/tidy                        | GET    | what a tidy-up would do (near-duplicates, stale)  |
| /api/media/tidy                        | POST   | offer it: ONE approval card; nothing moves before |
| /api/media/trash                       | GET    | Friday's recoverable trash                        |
| /api/media/trash/<entry>/restore       | POST   | put an entry back where it came from              |

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
    try:
        from agent_friday.services import media_tidy
        media_tidy.register_hooks()
    except Exception:
        pass
    return None


def _json() -> dict:
    return request.get_json(silent=True) or {}


@media_bp.route('/api/media', methods=['GET'])
def media_list():
    """Every list checks the index is fresh: built now if it never was (in the
    background, so the page shows progress rather than waiting), refreshed when
    a source folder changed since the last pass. ``indexing`` carries that state."""
    a = request.args
    indexing = mi.ensure_fresh("open")
    try:
        limit = int(a.get('limit', 200))
    except ValueError:
        limit = 200
    since = until = None
    if a.get('when'):
        from agent_friday.services.media_card_tools import period
        since, until = period(a.get('when'))
    res = mi.query(
        view=a.get('view', 'all') or 'all', q=a.get('q', '') or '', kind=a.get('kind') or None,
        project=a.get('project') if 'project' in a else None, privacy=a.get('privacy') or None,
        unsigned=a.get('unsigned') in ('1', 'true'), status=a.get('status') or None,
        sort=a.get('sort', 'next') or 'next', limit=limit, offset=int(a.get('offset', 0) or 0),
        since=since, until=until, favorite=a.get('favorite') in ('1', 'true'), tag=a.get('tag') or None,
    )
    res["collections"] = mi.collections()
    res["turns"] = mi.turn_capabilities()
    res["status"] = "ok"
    res["indexing"] = indexing
    try:
        from agent_friday.services import media_previews as mp
        res["previews"] = mp.status()
    except Exception:
        res["previews"] = {"pending": 0, "done": 0}
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
    return jsonify({"status": "ok", "counts": mi.reindex("asked")})


@media_bp.route('/api/media/status', methods=['GET'])
def media_status():
    return jsonify({"status": "ok", "indexing": mi.status()})


@media_bp.route('/api/media/turns', methods=['GET'])
def media_turns():
    """What this PC can turn a card into, and why not when it cannot."""
    return jsonify({"status": "ok", "turns": mi.turn_capabilities()})


@media_bp.route('/api/media/previews/status', methods=['GET'])
def media_previews_status():
    from agent_friday.services import media_previews as mp
    return jsonify({"status": "ok", "previews": mp.status()})


@media_bp.route('/api/media/<card_id>', methods=['GET'])
def media_get(card_id):
    c = mi.get(card_id)
    if c is None:
        return jsonify({"status": "not_found"}), 404
    body = c.pop("body", None)
    rels = c.pop("relations", [])
    out = {"status": "ok", "card": c, "relations": rels, "body": body}
    q = (request.args.get('q') or '').strip()
    if q and c.get("transcript"):
        # ?q= names the words searched for: when they were said, so the player can start there
        from agent_friday.services import media_transcripts as mt
        out["hit_t"] = mt.hit_time(c, q)
    return jsonify(out)


@media_bp.route('/api/media/collections', methods=['GET'])
def media_collections():
    return jsonify({"status": "ok", "collections": mi.collections()})


@media_bp.route('/api/media/collections', methods=['POST'])
def media_collection_save():
    b = _json()
    res = mi.save_collection(str(b.get('name') or ''), b.get('filters') or {}, b.get('id') or None)
    return jsonify(res), (200 if res.get("status") == "ok" else 400)


@media_bp.route('/api/media/collections/<collection_id>', methods=['GET'])
def media_collection_get(collection_id):
    try:
        limit = int(request.args.get('limit', 200))
    except ValueError:
        limit = 200
    res = mi.collection_query(collection_id, limit=limit)
    return jsonify(res), (200 if res.get("status") == "ok" else 404)


@media_bp.route('/api/media/collections/<collection_id>', methods=['DELETE'])
def media_collection_delete(collection_id):
    res = mi.delete_collection(collection_id)
    return jsonify(res), (200 if res.get("status") == "ok" else 404)


@media_bp.route('/api/media/tidy', methods=['GET'])
def media_tidy_report():
    from agent_friday.services import media_tidy
    rep = media_tidy.report()
    rep["status"] = "ok"
    return jsonify(rep)


@media_bp.route('/api/media/tidy', methods=['POST'])
def media_tidy_propose():
    from agent_friday.services import media_tidy
    b = _json()
    ids = [str(x) for x in b.get('ids')] if isinstance(b.get('ids'), list) else None
    res = media_tidy.propose(requested_by="user", ids=ids)
    code = {"pending": 202, "denied": 403, "error": 500, "nothing": 200}.get(res.get("status"), 200)
    return jsonify(res), code


@media_bp.route('/api/media/trash', methods=['GET'])
def media_trash():
    from agent_friday.services import media_tidy
    return jsonify({"status": "ok", "entries": media_tidy.trash_list(), "folder": str(media_tidy.trash_dir())})


@media_bp.route('/api/media/trash/<entry>/restore', methods=['POST'])
def media_trash_restore(entry):
    from agent_friday.services import media_tidy
    if "/" in entry or "\\" in entry or ".." in entry:
        return jsonify({"status": "denied"}), 400
    res = media_tidy.restore(entry)
    return jsonify(res), (200 if res.get("status") == "ok" else 404)


@media_bp.route('/api/media/bulk', methods=['POST'])
def media_bulk():
    b = _json()
    ids = [str(x) for x in (b.get('ids') or [])][:500]
    res = mi.bulk(ids, project=b.get('project') if 'project' in b else None, add_tags=b.get('add_tags') or None,
                  remove_tags=b.get('remove_tags') or None, favorite=b.get('favorite') if 'favorite' in b else None)
    return jsonify(res)


@media_bp.route('/api/media/<card_id>', methods=['PATCH'])
def media_patch(card_id):
    b = _json()
    res = mi.patch(card_id, status=b.get('status'), project=b.get('project'), title=b.get('title'), when=b.get('when'),
                   favorite=b.get('favorite') if 'favorite' in b else None,
                   tags=[str(t) for t in b.get('tags')] if isinstance(b.get('tags'), list) else None)
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


@media_bp.route('/api/media/<card_id>/preview', methods=['GET'])
def media_preview(card_id):
    from agent_friday.services import media_previews as mp
    c = mi.get(card_id)
    p = mp.image_path(c) if c else None
    if p is None:
        if c is not None and not mp.ready(c):
            mp.enqueue([c], front=True)
        return jsonify({"status": "not_ready" if c else "not_found"}), 404
    return send_file(str(p), mimetype="image/webp", conditional=True, max_age=86400)


@media_bp.route('/api/media/<card_id>/strip', methods=['GET'])
def media_strip(card_id):
    from agent_friday.services import media_previews as mp
    c = mi.get(card_id)
    p = mp.strip_path(c) if c else None
    if p is None:
        return jsonify({"status": "not_found"}), 404
    return send_file(str(p), mimetype="image/webp", conditional=True, max_age=86400)


def _local_file(card_id):
    c = mi.get(card_id)
    if c is None:
        return None
    from agent_friday.services import media_previews as mp
    p = mp.media_path(c)
    return p if p is not None and p.exists() else None


@media_bp.route('/api/media/<card_id>/open', methods=['POST'])
def media_open(card_id):
    """Open the file in whatever this PC opens it with. Local user only (the gate above)."""
    p = _local_file(card_id)
    if p is None:
        return jsonify({"status": "not_found"}), 404
    try:
        import os as _os, subprocess as _sp, sys as _sys
        if _sys.platform == "win32":
            _os.startfile(str(p))  # type: ignore[attr-defined]
        elif _sys.platform == "darwin":
            _sp.Popen(["open", str(p)])
        else:
            _sp.Popen(["xdg-open", str(p)])
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)[:200]}), 500
    return jsonify({"status": "ok", "path": str(p)})


@media_bp.route('/api/media/<card_id>/reveal', methods=['POST'])
def media_reveal(card_id):
    """Show the file in its folder on this PC."""
    p = _local_file(card_id)
    if p is None:
        return jsonify({"status": "not_found"}), 404
    try:
        import subprocess as _sp, sys as _sys
        if _sys.platform == "win32":
            _sp.Popen(["explorer", "/select,", str(p)])
        elif _sys.platform == "darwin":
            _sp.Popen(["open", "-R", str(p)])
        else:
            _sp.Popen(["xdg-open", str(p.parent)])
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)[:200]}), 500
    return jsonify({"status": "ok", "path": str(p)})


@media_bp.route('/api/media/<card_id>/render/<int:n>', methods=['GET'])
def media_render(card_id, n):
    c = mi.get(card_id)
    renders = ((c or {}).get("extra") or {}).get("renders") or []
    if not c or n < 0 or n >= len(renders) or not Path(renders[n]).is_file():
        return jsonify({"status": "not_found"}), 404
    return send_file(renders[n], conditional=True)
