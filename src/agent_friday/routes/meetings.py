"""Meeting capture API (services/meeting_capture.py).

    GET    /api/meetings/status            recording state for the indicator
    GET    /api/meetings/suggest           a title from the calendar event now
    GET    /api/meetings/start-card        the consent statement, its version, the
                                           jurisdiction note and a single-use token
    POST   /api/meetings/start             {consent_ack, nonce, consent_version,
                                            title, event_id, keep_audio,
                                            max_duration_s, system_audio,
                                            speak_consent, consent_line}
    POST   /api/meetings/stop
    GET    /api/meetings/live              live partial text while recording
    GET    /api/meetings/consent           the editable consent line and note
    POST   /api/meetings/consent           {line, mode, speak}
    GET    /api/meetings/retention         the choices, nothing preselected
    POST   /api/meetings/retention         {audio, text, days}
    GET    /api/meetings/suggestion        a "take notes?" card, at most once per event
    POST   /api/meetings/suggestion        {action: accept|decline}
    POST   /api/meetings/suggestion/reset  turn suggestions back on
    GET    /api/meetings                   past meetings (no transcript)
    GET    /api/meetings/<id>              one meeting: transcript and notes
    DELETE /api/meetings/<id>              removes transcript, notes and audio
    DELETE /api/meetings/<id>/audio        removes the audio only
    POST   /api/meetings/<id>/notes        write the notes again (local model)
    POST   /api/meetings/<id>/tasks        {items: [index, ...]}
    GET    /api/meetings/<id>/follow-up    ?kind=thank_you|follow_up -> a draft
    POST   /api/meetings/<id>/follow-up    {to, subject, body} -> approval card
    GET    /api/meetings/<id>/audio        WAV, only when the owner kept audio

LOCAL ONLY. Starting a recording turns on this machine's microphone, and a
transcript holds what other people said. Both stay with the person sitting at
this machine: a remote session or an observer credential is refused.

NEVER AUTO-STARTED. `meetings_start` is the only caller of
MeetingManager.start. It forwards `consent_ack` and the start card's `nonce`
exactly as the page sent them; the manager refuses anything but True for the
first and a token it handed out a moment ago, once, for the second. A
`confirm` in the body is ignored. The suggestion routes only ever offer a card;
accepting one opens the start card and starts nothing.
"""

from flask import Blueprint, Response, jsonify, request

from agent_friday.services import meeting_capture as mc
from agent_friday.services import meeting_policy as mp
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


@meetings_bp.route('/api/meetings/start-card')
def meetings_start_card():
    """What the start card shows (the consent statement, its version, the
    jurisdiction note and the editable line) and the single-use token its start
    button sends. Asking for it starts nothing."""
    card = mc.get_manager().issue_start_card()
    return jsonify({"status": "ok"} | mp.consent() | card)


@meetings_bp.route('/api/meetings/start', methods=['POST'])
def meetings_start():
    d = request.get_json(silent=True) or {}
    try:
        # `confirm` is never read from the request: the start is confirmed by
        # the token the start card was handed, which is single use and short
        # lived. A body that merely says confirm:true starts nothing.
        st = mc.get_manager().start(
            consent_ack=d.get("consent_ack") is True,
            consent_version=d.get("consent_version"),
            title=d.get("title") or "",
            event_id=d.get("event_id") or "",
            keep_audio=bool(d.get("keep_audio")),
            max_duration_s=d.get("max_duration_s"),
            system_audio=d.get("system_audio") is True,
            speak_consent=d.get("speak_consent") is True,
            consent_line=d.get("consent_line"),
            nonce=d.get("nonce"))
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


@meetings_bp.route('/api/meetings/live')
def meetings_live():
    return jsonify({"status": "ok"} | mc.get_manager().live())


_BAD = ({"status": "error", "code": "bad_request",
         "error": "That choice was not understood."}, 400)


@meetings_bp.route('/api/meetings/consent', methods=['GET', 'POST'])
def meetings_consent():
    if request.method == 'GET':
        return jsonify({"status": "ok"} | mp.consent())
    d = request.get_json(silent=True) or {}
    try:
        return jsonify({"status": "ok"} | mp.set_consent(
            line=d.get("line"), mode=d.get("mode"), speak=d.get("speak")))
    except (ValueError, TypeError):
        return jsonify(_BAD[0]), _BAD[1]


@meetings_bp.route('/api/meetings/retention', methods=['GET', 'POST'])
def meetings_retention():
    if request.method == 'GET':
        return jsonify({"status": "ok"} | mp.retention())
    d = request.get_json(silent=True) or {}
    try:
        return jsonify({"status": "ok"} | mp.set_retention(
            audio=d.get("audio"), text=d.get("text"), days=d.get("days")))
    except (ValueError, TypeError):
        return jsonify(_BAD[0]), _BAD[1]


@meetings_bp.route('/api/meetings/suggestion', methods=['GET', 'POST'])
def meetings_suggestion():
    if request.method == 'POST':
        d = request.get_json(silent=True) or {}
        try:
            return jsonify({"status": "ok"} | mp.answer_suggestion(d.get("action"), d.get("key")))
        except (ValueError, TypeError):
            return jsonify(_BAD[0]), _BAD[1]
    m = mc.get_manager()
    try:
        ev = m.event_lookup()
    except Exception:
        ev = None
    # Asking does not use the card up: the page says "shown" once it is on screen.
    card = mp.suggest(ev, recording=bool(m.status().get("recording")))
    return jsonify({"status": "ok", "suggestion": card} | {"state": mp.suggestion_state()})


@meetings_bp.route('/api/meetings/suggestion/reset', methods=['POST'])
def meetings_suggestion_reset():
    return jsonify({"status": "ok"} | mp.reset_suggestions())


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


@meetings_bp.route('/api/meetings/<mid>/audio', methods=['DELETE'])
def meetings_audio_delete(mid):
    try:
        return jsonify({"status": "ok"} | mc.get_manager().delete_audio(mid))
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
