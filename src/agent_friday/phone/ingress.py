"""The public phone ingress: the only part of Friday the internet can reach.

WHAT IT IS. A separate Flask app, with its own WSGI server bound to
127.0.0.1:<ingress_port>. A named Cloudflare tunnel forwards the public phone
hostname to that port and nowhere else. The app holds exactly these routes:

    POST /twilio/sms           a text arrived
    POST /twilio/sms-status    delivery status of a text Friday sent
    POST /twilio/voice         a call arrived: greeting + voicemail, or a live call
    POST /twilio/voice-done    after voicemail: say goodbye, hang up
    POST /twilio/recording     a voicemail recording is ready
    POST /twilio/call-status   a call ended
    WS   /twilio/media         Phase 2: live call audio (Media Streams)

Any other path is 404, and no request can reach Friday's main app through this
socket: the main app is a different object on a different port. There is no
session, no login, no cookie and no notion of a local user here, so the
"loopback means the owner" rule that the main app uses cannot apply by
accident: the tunnel connects from loopback, and this app never asks.

WHAT EVERY REQUEST MUST PASS, in order, and each failure stops it:

  1. a rate limit (per client, and globally)             -> 429
  2. the phone is switched on and has its auth token     -> 503
  3. X-Twilio-Signature, over the configured PUBLIC URL  -> 403
  4. the AccountSid in the body is this account's        -> 403
  5. not a replay of a request already accepted          -> 200, no effect
  6. the event is written to the spool                   -> 503 if it cannot be

A failed signature also costs the client from a much smaller budget, so a scan
runs out fast. Bodies over 32 KB are refused before parsing.

What the ingress does NOT do is act. It writes the validated event to the
spool and answers Twilio with fixed TwiML. Everything else, including deciding
what an inbound text means, happens in service.py, with the text marked as
untrusted input.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional
from xml.sax.saxutils import escape, quoteattr

from flask import Flask, Response, request

from agent_friday.phone import config, guard, signature, spool

_log = logging.getLogger("friday.phone.ingress")

MAX_BODY = 32 * 1024

_GUARD = guard.IngressGuard()
_LISTENER: Optional[Callable[[str], None]] = None


def set_listener(fn: Optional[Callable[[str], None]]) -> None:
    """service.py registers a wake-up here; it is called with the event kind
    after a new event is spooled. A listener failure never fails the request."""
    global _LISTENER
    _LISTENER = fn


def _twiml(body: str = "") -> Response:
    return Response('<?xml version="1.0" encoding="UTF-8"?><Response>%s</Response>' % body,
                    status=200, mimetype="text/xml")


def _plain(status: int) -> Response:
    return Response("", status=status, mimetype="text/plain")


def _client_key() -> str:
    """Only for sharing out the rate limit. Never evidence of identity."""
    return (request.headers.get("CF-Connecting-IP")
            or request.remote_addr or "?").strip()[:64]


def _public_url(cfg: dict, *, ws: bool = False) -> str:
    base = cfg.get("public_base_url") or ""
    if ws:
        base = "wss://" + base.split("://", 1)[-1]
    qs = request.query_string.decode("latin-1")
    return base + request.path + (("?" + qs) if qs else "")


def _auth_tokens() -> list:
    tok = config.get_secret("auth_token")
    return [tok] if tok else []


def _check(kind: str, *, ws: bool = False):
    """Steps 1-5 above. Returns (cfg, params) on success, or a Response."""
    ck = _client_key()
    if not _GUARD.admit(ck):
        return _plain(429)
    cfg = config.load()
    tokens = _auth_tokens()
    if not cfg.get("enabled") or not cfg.get("public_base_url") or not tokens:
        return _plain(503)
    params = [] if ws else list(request.form.items(multi=True))
    url = _public_url(cfg, ws=ws)
    sig = request.headers.get("X-Twilio-Signature", "")
    if not signature.validate(tokens, url, params, sig):
        _log.warning("phone ingress: bad signature on %s", request.path)
        return _plain(403 if _GUARD.record_bad(ck) else 429)
    if not ws:
        acct = dict(params).get("AccountSid", "")
        if not cfg.get("account_sid") or acct != cfg["account_sid"]:
            return _plain(403)
    try:
        if spool.seen_before(guard.replay_key(url, params, sig)):
            return ("replay", cfg, dict(params))
    except Exception as e:
        _log.error("phone ingress: replay store unavailable (%s); refusing", e)
        return _plain(503)
    return (kind, cfg, dict(params))


def _spool(kind: str, sid: str, data: dict) -> Optional[Response]:
    if not sid:
        return _plain(400)
    try:
        new = spool.put_event(kind, sid, data)
    except Exception as e:
        _log.error("phone ingress: spool unavailable (%s); refusing", e)
        return _plain(503)
    if new and _LISTENER:
        try:
            _LISTENER(kind)
        except Exception:
            pass
    return None


# Only the fields Friday uses are kept. Twilio sends location guesses
# (FromCity, FromZip, ...) and more; none of it is stored.
_SMS_FIELDS = ("MessageSid", "From", "To", "Body", "NumMedia", "NumSegments")
_CALL_FIELDS = ("CallSid", "From", "To", "CallStatus", "Direction", "CallDuration")
_REC_FIELDS = ("RecordingSid", "CallSid", "RecordingStatus", "RecordingDuration")
_SMS_STATUS_FIELDS = ("MessageSid", "MessageStatus", "ErrorCode", "To")


def _pick(p: dict, fields) -> dict:
    return {k: p.get(k, "") for k in fields}


def voicemail_twiml(cfg: dict) -> str:
    base = cfg["public_base_url"]
    return ("<Say>%s</Say>"
            "<Record maxLength=\"120\" timeout=\"5\" playBeep=\"true\" trim=\"trim-silence\""
            " action=%s method=\"POST\""
            " recordingStatusCallback=%s recordingStatusCallbackMethod=\"POST\"/>"
            "<Say>No message was recorded. Goodbye.</Say><Hangup/>"
            % (escape(cfg.get("greeting") or config.DEFAULTS["greeting"]),
               quoteattr(base + "/twilio/voice-done"),
               quoteattr(base + "/twilio/recording")))


def create_app() -> Flask:
    app = Flask("friday_phone_ingress")
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY

    @app.after_request
    def _headers(resp):
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    @app.errorhandler(404)
    @app.errorhandler(405)
    @app.errorhandler(413)
    def _bare(e):
        return _plain(getattr(e, "code", 404))

    @app.route("/twilio/sms", methods=["POST"])
    def sms_in():
        r = _check("sms_in")
        if isinstance(r, Response):
            return r
        kind, _cfg, p = r
        if kind != "replay":
            err = _spool("sms_in", p.get("MessageSid", ""), _pick(p, _SMS_FIELDS))
            if err:
                return err
        return _twiml()          # replies go out later through the API, if at all

    @app.route("/twilio/sms-status", methods=["POST"])
    def sms_status():
        r = _check("sms_status")
        if isinstance(r, Response):
            return r
        kind, _cfg, p = r
        if kind != "replay":
            sid = "%s:%s" % (p.get("MessageSid", ""), p.get("MessageStatus", ""))
            err = _spool("sms_status", sid, _pick(p, _SMS_STATUS_FIELDS))
            if err:
                return err
        return _plain(204)

    @app.route("/twilio/voice", methods=["POST"])
    def voice_in():
        r = _check("call_in")
        if isinstance(r, Response):
            return r
        kind, cfg, p = r
        if kind != "replay":
            err = _spool("call_in", p.get("CallSid", ""), _pick(p, _CALL_FIELDS))
            if err:
                return err
        return _twiml(answer_twiml(cfg, p))

    @app.route("/twilio/voice-done", methods=["POST"])
    def voice_done():
        r = _check("voice_done")
        if isinstance(r, Response):
            return r
        return _twiml("<Say>Thank you. Goodbye.</Say><Hangup/>")

    @app.route("/twilio/recording", methods=["POST"])
    def recording():
        r = _check("recording")
        if isinstance(r, Response):
            return r
        kind, _cfg, p = r
        if kind != "replay" and p.get("RecordingStatus") == "completed":
            err = _spool("recording", p.get("RecordingSid", ""), _pick(p, _REC_FIELDS))
            if err:
                return err
        return _plain(204)

    @app.route("/twilio/call-status", methods=["POST"])
    def call_status():
        r = _check("call_status")
        if isinstance(r, Response):
            return r
        kind, _cfg, p = r
        if kind != "replay":
            sid = "%s:%s" % (p.get("CallSid", ""), p.get("CallStatus", ""))
            err = _spool("call_status", sid, _pick(p, _CALL_FIELDS))
            if err:
                return err
        return _plain(204)

    _register_media_ws(app)
    return app


def answer_twiml(cfg: dict, p: dict) -> str:
    """What happens when someone calls the number.

    A live conversation only when live calls are on, the caller ID is the
    verified owner's cell, and the voice bridge says it can serve. Caller ID
    can be spoofed, so a live call has the same read-only limits as a text.
    Everyone else gets the greeting and voicemail (or a polite refusal when
    voicemail is off).
    """
    owner = config.verified_owner_cell()
    if cfg.get("live_calls") and owner and p.get("From") == owner:
        try:
            from agent_friday.phone import live_call
            if live_call.available():
                return live_call.connect_twiml(cfg, p.get("CallSid", ""))
        except Exception as e:
            _log.warning("live call unavailable (%s); taking voicemail", e)
    if cfg.get("voicemail", True):
        return voicemail_twiml(cfg)
    return ("<Say>Hi, you've reached Friday, an AI assistant. "
            "This line is not taking calls right now. Goodbye.</Say><Hangup/>")


def _register_media_ws(app: Flask) -> None:
    try:
        from flask_sock import Sock
    except Exception:                                # voice extras not installed
        return
    sock = Sock(app)

    @sock.route("/twilio/media")
    def media(ws):
        r = _check("media", ws=True)
        if isinstance(r, Response):
            ws.close(reason=1008)
            return
        from agent_friday.phone import live_call
        live_call.serve_media_stream(ws, r[1])


# ── lifecycle ────────────────────────────────────────────────────────────────

_SERVER = None
_THREAD: Optional[threading.Thread] = None
_SRV_LOCK = threading.Lock()


def running() -> dict:
    with _SRV_LOCK:
        if _SERVER is None:
            return {"running": False}
        return {"running": True, "host": _SERVER.host, "port": _SERVER.port}


def start(port: int) -> dict:
    """Serve the ingress on 127.0.0.1:port. Idempotent. Never binds anything
    but loopback; the tunnel is the only way in from outside."""
    global _SERVER, _THREAD
    from werkzeug.serving import make_server
    with _SRV_LOCK:
        if _SERVER is not None:
            if _SERVER.port == port:
                return {"running": True, "host": _SERVER.host, "port": port}
            _SERVER.shutdown()
            _SERVER = None
        srv = make_server("127.0.0.1", int(port), create_app(), threaded=True)
        t = threading.Thread(target=srv.serve_forever, name="phone-ingress", daemon=True)
        t.start()
        _SERVER, _THREAD = srv, t
        _log.info("phone ingress listening on 127.0.0.1:%s", port)
        return {"running": True, "host": "127.0.0.1", "port": port}


def stop() -> None:
    global _SERVER, _THREAD
    with _SRV_LOCK:
        if _SERVER is not None:
            _SERVER.shutdown()
            _log.info("phone ingress stopped")
        _SERVER, _THREAD = None, None
