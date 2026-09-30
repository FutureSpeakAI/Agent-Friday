"""Podcast API (services/podcast_engine.py).

    GET    /api/podcasts                   episodes, newest first (?routine, ?run_id, ?limit)
    POST   /api/podcasts                   {sources, title, length, mode, instructions, voice}
    GET    /api/podcasts/for-run           ?routine=&run_id= -> that run's episode or null
    GET    /api/podcasts/voices            installed local voices and the podcast settings
    POST   /api/podcasts/now-playing       {episode_id, t, playing} from the desktop player
    GET    /api/podcasts/<id>              one episode: chapters, cited lines, sources, check
    GET    /api/podcasts/<id>/audio        the audio (range requests supported)
    GET    /api/podcasts/<id>/captions.vtt timed captions
    GET    /api/podcasts/<id>/transcript.txt the transcript, checks and linked sources
    GET    /api/podcasts/<id>/charts/<f>   a data-mode chart (SVG)
    POST   /api/podcasts/<id>/cancel
    POST   /api/podcasts/<id>/retry        a failed episode, from the stage it failed at
    DELETE /api/podcasts/<id>

LOCAL ONLY. Episodes may be built from mail, the vault, the wiki and the
owner's files, so they are served to the person at this machine: a remote
session or an observer credential is refused, as for meetings.
"""

from flask import Blueprint, jsonify, request, send_file

from agent_friday.routes._errors import api_error, public_result
from agent_friday.services import podcast_engine as pe

podcasts_bp = Blueprint('podcasts', __name__)


@podcasts_bp.before_request
def _gate():
    try:
        from flask import g
        if (getattr(g, "friday_principal", "user") or "user") != "user":
            return jsonify({"error": "podcasts are only available to the local user"}), 403
        from agent_friday.core import _is_local_request
        if not _is_local_request():
            return jsonify({"error": "podcasts are only available on this machine, "
                                     "not over a remote connection"}), 403
    except Exception:
        return jsonify({"error": "could not establish that this request is local"}), 403
    return None


def _episode_or_404(eid):
    ep = pe.load(eid)
    if ep is None:
        return None, (jsonify({"status": "not_found"}), 404)
    return ep, None


@podcasts_bp.route('/api/podcasts', methods=['GET'])
def podcasts_list():
    try:
        limit = max(1, min(200, int(request.args.get("limit") or 50)))
    except (TypeError, ValueError):
        limit = 50
    eps = pe.list_episodes(routine=request.args.get("routine") or "",
                           run_id=request.args.get("run_id") or "", limit=limit)
    return jsonify({"status": "ok", "episodes": [pe.summary(e) for e in eps]})


@podcasts_bp.route('/api/podcasts', methods=['POST'])
def podcasts_create():
    data = request.get_json(silent=True) or {}
    refs = [r for r in (data.get("sources") or []) if isinstance(r, dict)]
    topic = str(data.get("topic") or "").strip()
    if topic:
        from agent_friday.services import podcast_sources
        found = podcast_sources.find_topic(topic)
        if not found and not refs:
            return jsonify({"status": "not_found",
                            "message": "I couldn't find wiki pages about that."}), 404
        refs += found
    try:
        ep = pe.create(refs, title=str(data.get("title") or ""),
                       length=str(data.get("length") or "standard"),
                       mode=str(data.get("mode") or "auto"),
                       instructions=str(data.get("instructions") or ""),
                       voice_engine=str(data.get("voice") or "local"), origin="user")
    except Exception as e:
        return api_error(e, "Couldn't start the episode", status=400)
    return jsonify(public_result({"status": "ok", "episode": pe.summary(ep)},
                                 "Couldn't start the episode")), 202


@podcasts_bp.route('/api/podcasts/for-run', methods=['GET'])
def podcasts_for_run():
    ep = pe.for_run(request.args.get("routine") or "", request.args.get("run_id") or "")
    return jsonify({"status": "ok", "episode": pe.summary(ep) if ep else None})


@podcasts_bp.route('/api/podcasts/voices', methods=['GET'])
def podcasts_voices():
    from agent_friday.services import podcast_render
    return jsonify({"status": "ok", "installed": podcast_render.installed_voices(),
                    "settings": pe.settings()})


@podcasts_bp.route('/api/podcasts/formats', methods=['GET'])
def podcasts_formats():
    """Who is on each show (solo or two hosts), with the recommended format."""
    return jsonify({"status": "ok", "formats": pe.formats()})


@podcasts_bp.route('/api/podcasts/formats', methods=['PUT'])
def podcasts_set_format():
    data = request.get_json(silent=True) or {}
    try:
        routine = str(data.get("routine") or "")
        pe.set_format("" if routine == "any" else routine, str(data.get("format") or ""))
    except pe.PodcastRefused as e:
        return api_error(e, "Couldn't change the show's format", status=400)
    return jsonify({"status": "ok", "formats": pe.formats()})


@podcasts_bp.route('/api/podcasts/now-playing', methods=['POST'])
def podcasts_now_playing():
    from agent_friday.services import podcast_tools
    d = request.get_json(silent=True) or {}
    try:
        podcast_tools.set_now_playing(d.get("episode_id") or "", float(d.get("t") or 0),
                                      bool(d.get("playing")))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "t must be a number"}), 400
    return jsonify({"status": "ok"})


@podcasts_bp.route('/api/podcasts/<eid>', methods=['GET'])
def podcasts_get(eid):
    ep, err = _episode_or_404(eid)
    if err:
        return err
    ep = {k: v for k, v in ep.items() if k != "refs"}
    ep["credit"] = pe.show_credit(ep)
    return jsonify({"status": "ok", "episode": ep})


def _file(eid, name, mimetype):
    try:
        d = pe._dir(eid)
    except Exception:
        return jsonify({"status": "not_found"}), 404
    from agent_friday.paths import contained
    try:
        p = contained(d, name)
    except ValueError:
        return jsonify({"status": "not_found"}), 404
    if not p.is_file():
        return jsonify({"status": "not_found"}), 404
    return send_file(p, mimetype=mimetype, conditional=True, max_age=0)


@podcasts_bp.route('/api/podcasts/<eid>/audio', methods=['GET'])
def podcasts_audio(eid):
    ep, err = _episode_or_404(eid)
    if err:
        return err
    name = ep.get("audio") or "audio.wav"
    return _file(eid, name, "audio/mpeg" if name.endswith(".mp3") else "audio/wav")


@podcasts_bp.route('/api/podcasts/<eid>/captions.vtt', methods=['GET'])
def podcasts_captions(eid):
    return _file(eid, "captions.vtt", "text/vtt; charset=utf-8")


@podcasts_bp.route('/api/podcasts/<eid>/transcript.txt', methods=['GET'])
def podcasts_transcript(eid):
    """The transcript with its checks and linked sources, as a UTF-8 text file."""
    from flask import Response
    ep = pe.load(eid)
    if not ep or not ep.get("lines"):
        return jsonify({"status": "error", "message": "No transcript yet."}), 404
    return Response(pe.transcript_bytes(ep), mimetype="text/plain; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="%s-transcript.txt"' % eid})


@podcasts_bp.route('/api/podcasts/<eid>/charts/<name>', methods=['GET'])
def podcasts_chart(eid, name):
    if not name.endswith(".svg"):
        return jsonify({"status": "not_found"}), 404
    resp = _file(eid, "charts/" + name, "image/svg+xml")
    try:
        resp.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
    except Exception:
        pass
    return resp


@podcasts_bp.route('/api/podcasts/<eid>/cancel', methods=['POST'])
def podcasts_cancel(eid):
    ep = pe.cancel(eid)
    if ep is None:
        return jsonify({"status": "not_found"}), 404
    return jsonify({"status": "ok", "episode": pe.summary(ep)})


@podcasts_bp.route('/api/podcasts/<eid>/retry', methods=['POST'])
def podcasts_retry(eid):
    ep, err = _episode_or_404(eid)
    if err:
        return err
    if ep.get("status") not in ("failed", "cancelled"):
        return jsonify({"status": "error", "message": "only a failed or stopped episode can be retried"}), 409
    ep = pe._update(eid, status="queued", error=None, stage_detail="queued again", priority="now",
                    tries=0, retry_after=0)
    pe.wake()
    return jsonify({"status": "ok", "episode": pe.summary(ep)})


@podcasts_bp.route('/api/podcasts/<eid>', methods=['DELETE'])
def podcasts_delete(eid):
    try:
        ok = pe.delete(eid)
    except Exception as e:
        return api_error(e, "Couldn't delete the episode")
    return (jsonify({"status": "ok"}) if ok else (jsonify({"status": "not_found"}), 404))
