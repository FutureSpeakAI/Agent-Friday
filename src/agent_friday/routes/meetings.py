"""Meeting capture API (services/meeting_capture.py).

    GET    /api/meetings/status            recording state for the indicator
    GET    /api/meetings/suggest           a title from the calendar event now
    POST   /api/meetings/start             {consent_ack, consent_version, title,
                                            event_id, keep_audio, max_duration_s}
    POST   /api/meetings/stop
    GET    /api/meetings                   past meetings (no transcript)
    GET    /api/meetings/<id>              one meeting: transcript and notes
    DELETE /api/meetings/<id>              removes transcript, notes and audio
    POST   /api/meetings/<id>/notes        write the notes again (local model)
    POST   /api/meetings/<id>/tasks        {items: [index, ...]}
    GET    /api/meetings/<id>/follow-up    ?kind=thank_you|follow_up -> a draft
    POST   /api/meetings/<id>/follow-up    {to, subject, body} -> approval card
    GET    /api/meetings/<id>/audio        WAV, only when the owner kept audio

LOCAL ONLY. Starting a recording turns on this machine's microphone, and a
transcript holds what other people said. Both stay with the person sitting at
this machine: a remote session or an observer credential is refused.
"""

from flask import Blueprint, Response, jsonify, request

from agent_friday.services import meeting_capture as mc
from agent_friday.routes._errors import api_error, public_result

meetings_bp = Blueprint('meetings', __name__)


@meetings_bp.before_request
def _gate():
    try:
        from flask import g
        if (getattr(g, "friday_principal", "user") or "user") != "user":
            return jsonify({"error": "meetings are only available to the local user"}), 403
        from agent_friday.core import _is_local_request
        if not _is_local_request():
            return jsonify({"error": "meetings are only available on this machine, "
                                     "not over a remote connection"}), 403
    except Exception:
        return jsonify({"error": "could not establish that this request is local"}), 403
    return None


def _fail(e: mc.MeetingError):
    return jsonify({"status": "error", "code": e.code, "error": e.user_message}), e.status


@meetings_bp.route('/api/meetings/status')
def meetings_status():
    return jsonify({"status": "ok"} | mc.get_manager().status())


@meetings_bp.route('/api/meetings/suggest')
def meetings_suggest():
    try:
        ev = mc.get_manager().event_lookup()
    except Exception:
        ev = None
    return jsonify({"status": "ok", "event": ev or None})


@meetings_bp.route('/api/meetings/start', methods=['POST'])
def meetings_start():
    d = request.get_json(silent=True) or {}
    try:
        st = mc.get_manager().start(
            consent_ack=d.get("consent_ack") is True,
            consent_version=d.get("consent_version"),
            title=d.get("title") or "",
            event_id=d.get("event_id") or "",
            keep_audio=bool(d.get("keep_audio")),
            max_duration_s=d.get("max_duration_s"))
    except mc.MeetingError as e:
        return _fail(e)
    return jsonify({"status": "ok"} | st)


@meetings_bp.route('/api/meetings/stop', methods=['POST'])
def meetings_stop():
    try:
        row = mc.get_manager().stop(reason="owner")
    except mc.MeetingError as e:
        return _fail(e)
    return jsonify({"status": "ok", "meeting": row})


@meetings_bp.route('/api/meetings')
def meetings_list():
    return jsonify(public_result({"status": "ok", "meetings": mc.get_manager().list()}, "Couldn't list the meetings"))


@meetings_bp.route('/api/meetings/<mid>', methods=['GET', 'DELETE'])
def meetings_one(mid):
    m = mc.get_manager()
    try:
        if request.method == 'DELETE':
            return jsonify({"status": "ok"} | m.delete(mid))
        return jsonify({"status": "ok", "meeting": m.get(mid)})
    except mc.MeetingError as e:
        return _fail(e)


@meetings_bp.route('/api/meetings/<mid>/notes', methods=['POST'])
def meetings_notes(mid):
    try:
        return jsonify({"status": "ok", "summary": mc.get_manager().write_notes(mid)})
    except mc.MeetingError as e:
        return _fail(e)


@meetings_bp.route('/api/meetings/<mid>/tasks', methods=['POST'])
def meetings_tasks(mid):
    d = request.get_json(silent=True) or {}
    items = d.get("items")
    if not isinstance(items, list) or not items:
        return jsonify({"status": "error", "code": "no_items",
                        "error": "Choose which action items to add."}), 400
    try:
        return jsonify({"status": "ok"} | mc.get_manager().create_tasks(mid, items))
    except mc.MeetingError as e:
        return _fail(e)


@meetings_bp.route('/api/meetings/<mid>/follow-up', methods=['GET', 'POST'])
def meetings_follow_up(mid):
    m = mc.get_manager()
    try:
        if request.method == 'GET':
            kind = request.args.get("kind") or "thank_you"
            return jsonify({"status": "ok"} | m.follow_up_draft(mid, kind))
        d = request.get_json(silent=True) or {}
        to = (d.get("to") or "").strip()
        subject = (d.get("subject") or "").strip()
        body = d.get("body") or ""
        if not to or not subject or not body.strip():
            return jsonify({"status": "error", "code": "incomplete",
                            "error": "A follow-up needs a recipient, a subject "
                                     "and a message."}), 400
        try:
            out = m.request_follow_up(mid, to=to, subject=subject, body=body)
        except mc.MeetingError:
            raise
        except Exception as e:
            return api_error(e, "Couldn't prepare the follow-up", 400, key="error", code="not_queued")
        return jsonify({"status": "ok"} | out)
    except mc.MeetingError as e:
        return _fail(e)


@meetings_bp.route('/api/meetings/<mid>/audio')
def meetings_audio(mid):
    try:
        wav = mc.get_manager().audio_wav(mid)
    except mc.MeetingError as e:
        return _fail(e)
    return Response(wav, mimetype="audio/wav",
                    headers={"Content-Disposition":
                             "attachment; filename=meeting-%s.wav" % mid,
                             "Cache-Control": "no-store"})
