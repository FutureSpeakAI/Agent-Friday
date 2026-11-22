"""The PC's voice for a paired device: a voice session and a speech route over the gateway (Stephen 2026-11-21:
"PC voice cannot speak on my tablet ... the voice mode on the PC should also work"). Wire contract v1, agreed with
the phone lane on the LEDGER (2026-11-22). The full text is docs/development/gateway-voice-session.md.

* ``wss /tv/v1/voice/session`` (owner-bound personal devices only, refused as HTTP before the upgrade otherwise): the
  device speaks to the PC and the PC answers in whatever engine the PC's voice setting selects. The gateway does not
  re-implement the pipeline: it opens a loopback WebSocket to the PC's own session route for that engine
  (``/ws/voice-local`` local, ``/ws/live`` Gemini Live, ``/ws/openai-live`` OpenAI) and translates frames both ways,
  so the engine, the tools, the governance gate and the approval cards are exactly the desktop's, and a cloud engine
  uses the PC's key server-side (the device never holds one). A device is not the owner's desk voice: a SPOKEN yes
  from it never approves a card.
* ``POST /tv/v1/tts {text, voice?}`` (any paired device): the text in the PC's selected voice, as a WAV.

Device-facing frames: binary = PCM16 mono audio (16 kHz up, 24 kHz down); text = JSON control and events.

  up    start{engine?,voice?} press release barge_in stop ping           (anything else is dropped)
  down  engine{engine,where,local,label,ear?,mind?,mouth?,voice?}  speaking{who:you|friday}
        transcript{who,text,final}  turn_done{engine}  status{text}  error{code,words}  closed{reason,code}  pong

Enforced by tests/unit/test_tv_voice_bridge.py.
"""
from __future__ import annotations

import base64
import binascii
import io
import json
import logging
import threading
import time
import wave
from typing import Callable, Optional

_log = logging.getLogger("friday.tv.voice")

SESSION_PATH = "/tv/v1/voice/session"
TTS_PATH = "/tv/v1/tts"
MAX_PCM_FRAME = 64 * 1024        # bytes of device audio in one binary frame
MAX_TEXT_FRAME = 4096            # bytes of one JSON control frame
MAX_TTS_CHARS = 2000
MAX_SESSION_S = 30 * 60
IDLE_S = 120
RELEASE_SILENCE_FRAMES = 10      # 100 ms each: the silence that lets the engine's endpointer close the turn
SILENT_FRAME_B64 = base64.b64encode(b"\x00" * 3200).decode("ascii")
DEVICE_FRAMES = ("start", "press", "release", "barge_in", "stop", "ping")
UPSTREAM_PATHS = {"/ws/voice-local", "/ws/live", "/ws/openai-live"}
ENGINE_NAMES = {"gemini_live": "gemini", "gemini": "gemini", "local": "local", "openai": "openai",
                "openai_realtime": "openai"}

ERROR_WORDS = {
    "no_engine": "This computer has no voice that can talk right now.",
    "start_failed": "This computer's voice could not start.",
    "engine_not_selected": "This computer's voice is set to a different engine. Change it on the computer.",
    "unauthorized": "This computer refused the voice session.",
    "voice_error": "This computer's voice had a problem.",
}


class VoiceRefused(Exception):
    def __init__(self, status: int, message: str, code: str = ""):
        super().__init__(message)
        self.status, self.code = status, code


def engine_event(info: dict, served_by: Optional[dict] = None) -> dict:
    """The who-spoke badge: the engine, whether it runs on this PC (``local``: the audio never left it) or in the
    cloud through the PC's key, and for local voice what served the turn. ``info`` is the PC's resolved voice engine
    (routes/voice._resolve_voice_engine)."""
    engine = str((info or {}).get("engine") or "demo")
    out = {"type": "engine", "engine": engine, "where": "local" if engine == "local" else "cloud",
           "local": engine == "local", "label": str((info or {}).get("label") or engine)}
    if engine == "demo":
        out["where"] = "none"
    for key in ("ear", "mind", "mouth", "voice"):
        v = (served_by or {}).get(key)
        if v:
            out[key] = str(v)[:80]
    return out


def _error(code: str) -> dict:
    return {"type": "error", "code": code, "words": ERROR_WORDS.get(code, ERROR_WORDS["voice_error"])}


class Bridge:
    """The frame rules between one device and the PC's session. Pure of sockets: ``to_device(obj_or_bytes)`` and
    ``to_upstream(raw)`` are given, so the rules are tested without a network."""

    def __init__(self, info: dict, to_device: Callable, to_upstream: Callable[[str], None],
                 clock: Callable[[], float] = time.monotonic):
        self.info = info or {}
        self._to_device = to_device
        self._to_upstream = to_upstream
        self._clock = clock
        self.started = self.last_seen = clock()
        self.served_by: dict = {}
        self.speaker: Optional[str] = None             # who is talking now: "you", "friday" or nobody
        self.closed: Optional[tuple] = None            # (code, reason) once the session is over

    # ── lifecycle ──
    def open(self) -> None:
        self._to_device(engine_event(self.info))

    def close(self, code: str, reason: str) -> None:
        if self.closed is None:
            self.closed = (code, reason)

    def timed_out(self) -> Optional[tuple]:
        now = self._clock()
        if now - self.started > MAX_SESSION_S:
            return ("time_limit", "The session reached its 30 minute limit.")
        if now - self.last_seen > IDLE_S:
            return ("idle", "Nothing was said for two minutes.")
        return None

    def _up(self, obj: dict) -> None:
        self._to_upstream(json.dumps(obj))

    def _say(self, who: str) -> None:
        if self.speaker != who:
            self.speaker = who
            self._to_device({"type": "speaking", "who": who})

    # ── device -> PC ──
    def from_device(self, frame) -> bool:
        """One frame from the device. False ends the session (``self.closed`` says why)."""
        self.last_seen = self._clock()
        if isinstance(frame, (bytes, bytearray)):
            if len(frame) > MAX_PCM_FRAME:
                self.close("frame_too_large", "An audio frame was too large.")
                return False
            self._up({"type": "audio", "data": base64.b64encode(bytes(frame)).decode("ascii")})
            return True
        if not isinstance(frame, str) or len(frame) > MAX_TEXT_FRAME:
            self.close("frame_too_large", "A control frame was too large.")
            return False
        try:
            obj = json.loads(frame)
        except ValueError:
            return True
        kind = obj.get("type") if isinstance(obj, dict) else None
        if kind not in DEVICE_FRAMES:
            return True
        if kind == "start":
            asked = ENGINE_NAMES.get(str(obj.get("engine") or "").strip().lower())
            chosen = str(self.info.get("engine") or "")
            if obj.get("engine") not in (None, "", "pc") and asked != chosen:
                self._to_device(_error("engine_not_selected"))
                self.close("engine_not_selected", ERROR_WORDS["engine_not_selected"])
                return False
        elif kind == "press":
            if self.speaker == "friday":
                self._up({"type": "barge"})
                self._say("you")
        elif kind == "release":
            for _ in range(RELEASE_SILENCE_FRAMES):
                self._up({"type": "audio", "data": SILENT_FRAME_B64})
        elif kind == "barge_in":
            self._up({"type": "barge"})
            self._say("you")
        elif kind == "ping":
            self._to_device({"type": "pong"})
        elif kind == "stop":
            self._up({"type": "end"})
            self.close("stopped", "Ended from the device.")
            return False
        return True

    # ── PC -> device ──
    def from_upstream(self, raw) -> None:
        """One frame from the PC's session, translated to the device contract."""
        try:
            obj = json.loads(raw) if isinstance(raw, (str, bytes)) else None
        except ValueError:
            obj = None
        if not isinstance(obj, dict):
            return
        kind = obj.get("type")
        text = str(obj.get("text") or "")
        if kind == "served_by":
            self.served_by = {k: obj.get(k) for k in ("ear", "mind", "mouth", "voice")}
            self._to_device(engine_event(self.info, self.served_by))
        elif kind == "audio":
            try:
                pcm = base64.b64decode(str(obj.get("data") or ""), validate=True)
            except (binascii.Error, ValueError):
                return
            if pcm:
                self._say("friday")
                self._to_device(pcm)
        elif kind in ("partial_transcript", "input_transcript"):
            if text:
                self._say("you")
                self._to_device({"type": "transcript", "who": "you", "text": text,
                                 "final": kind == "input_transcript"})
        elif kind == "text":
            if text:
                self._to_device({"type": "transcript", "who": "friday", "text": text, "final": False})
        elif kind == "status":
            if text:
                self._to_device({"type": "status", "text": text})
        elif kind in ("voice_turn_done", "turn_end"):
            said = str(obj.get("agent_text") or "")
            if said:
                self._to_device({"type": "transcript", "who": "friday", "text": said, "final": True})
            self._to_device({"type": "turn_done", "engine": engine_event(self.info, self.served_by)})
            self.speaker = None
        elif kind == "interrupted":
            self._say("you")
        elif kind == "error":
            code = "unauthorized" if str(obj.get("error") or "") == "unauthorized" else "voice_error"
            self._to_device(_error(code))
        # every other desktop frame (manifest, contract, context_reach, stage, receipts) is the desktop's own


class _Slot:
    def __init__(self):
        self.stop = threading.Event()
        self.closed: Optional[tuple] = None


_ACTIVE: dict = {}
_ACTIVE_LOCK = threading.Lock()


def resolve_engine() -> dict:
    """The PC's voice setting as the desktop resolves it (engine, label, the WebSocket path to run)."""
    from agent_friday.routes import voice as _rv
    return _rv._resolve_voice_engine()


def upstream_url(info: dict, conversation_id: str = "") -> str:
    from agent_friday import core
    path = str((info or {}).get("ws_url") or "")
    if path not in UPSTREAM_PATHS:
        raise VoiceRefused(503, "this computer has no voice engine ready right now", code="no_engine")
    q = ("?conversation_id=" + conversation_id) if conversation_id else ""
    return "ws://127.0.0.1:%d%s%s" % (int(core.SERVER_PORT), path, q)


def wav_bytes(pcm16: bytes, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm16)
    return buf.getvalue()


def speak(text: str, voice: str = "") -> tuple:
    """The text as a WAV in the PC's selected voice, and the badge saying who spoke it.
    Raises VoiceRefused when this PC can't speak right now."""
    from agent_friday.tv_gateway.voice_reply import speech_text
    clean = speech_text(text).strip()
    if not clean:
        raise VoiceRefused(400, "there is nothing to say", code="empty")
    if len(clean) > MAX_TTS_CHARS:
        raise VoiceRefused(413, "that is too long to speak in one go", code="too_long")
    info = resolve_engine()
    engine = str(info.get("engine") or "demo")
    if engine == "local":
        from agent_friday.services.local_voice import get_local_voice_engine
        try:
            pcm = get_local_voice_engine().synthesize(clean)
        except Exception as e:
            _log.info("local speech failed: %s", type(e).__name__)
            raise VoiceRefused(503, "this computer's voice is not ready", code="voice_unavailable")
        if not pcm:
            raise VoiceRefused(503, "this computer's voice is not ready", code="voice_unavailable")
        return wav_bytes(pcm), engine_event(info)
    if engine == "gemini":
        from agent_friday.routes import voice as _rv
        try:
            buf = _rv._synthesize_tts_wav(clean, voice=voice or None, style="briefing")
        except Exception as e:
            _log.info("cloud speech failed: %s", type(e).__name__)
            raise VoiceRefused(503, "this computer's cloud voice did not answer", code="voice_unavailable")
        return (buf.getvalue() if hasattr(buf, "getvalue") else bytes(buf)), engine_event(info)
    raise VoiceRefused(503, "this computer has no voice that can speak text right now", code="no_engine")


def _pump(client, bridge: Bridge, slot: _Slot) -> None:
    """PC -> device, until either side ends."""
    while not slot.stop.is_set():
        try:
            raw = client.receive(timeout=1)
        except Exception:
            break
        if raw is None:
            continue
        try:
            bridge.from_upstream(raw)
        except Exception:
            break
    if bridge.closed is None and slot.closed is None:
        bridge.close("upstream_ended", "The computer's voice session ended.")
    slot.stop.set()


def run_session(ws, device: dict, conversation_id: str = "", connect: Optional[Callable] = None) -> None:
    """The body of the WebSocket route: bridge this device to the PC's voice session until it ends."""
    dev_id = str((device or {}).get("id") or "")
    slot = _Slot()
    with _ACTIVE_LOCK:
        old = _ACTIVE.get(dev_id)
        _ACTIVE[dev_id] = slot
    if old is not None:
        old.closed = ("replaced", "Another voice session started on this device.")
        old.stop.set()
    send_lock = threading.Lock()

    def to_device(obj):
        with send_lock:
            ws.send(obj if isinstance(obj, (bytes, bytearray)) else json.dumps(obj))

    client = None
    code, reason = "stopped", "The session ended."
    try:
        info = resolve_engine()
        url = upstream_url(info, conversation_id)
        if connect is None:
            from simple_websocket import Client
            connect = Client.connect
        client = connect(url)
        client_lock = threading.Lock()

        def to_upstream(raw):
            with client_lock:
                client.send(raw)

        bridge = Bridge(info, to_device, to_upstream)
        bridge.open()
        threading.Thread(target=_pump, args=(client, bridge, slot), name="tv-voice-pump", daemon=True).start()
        while not slot.stop.is_set():
            try:
                frame = ws.receive(timeout=1)
            except Exception:
                bridge.close("device_gone", "The device went away.")
                break
            if frame is None:
                over = bridge.timed_out()
                if over:
                    bridge.close(*over)
                    break
                continue
            if not bridge.from_device(frame):
                break
        slot.stop.set()
        code, reason = slot.closed or bridge.closed or ("stopped", "The session ended.")
    except VoiceRefused as e:
        code, reason = e.code or "no_engine", str(e)
        to_device(_error(code))
    except Exception as e:                              # the PC's session could not start
        _log.info("voice session not started: %s", type(e).__name__)
        code, reason = "start_failed", ERROR_WORDS["start_failed"]
        try:
            to_device(_error("start_failed"))
        except Exception:
            pass
    finally:
        slot.stop.set()
        try:
            to_device({"type": "closed", "reason": reason, "code": code})
        except Exception:
            pass
        try:
            if client is not None:
                client.send(json.dumps({"type": "end"}))
                client.close()
        except Exception:
            pass
        with _ACTIVE_LOCK:
            if _ACTIVE.get(dev_id) is slot:
                _ACTIVE.pop(dev_id, None)


def register(app, refuse) -> None:
    """GET (WebSocket) /tv/v1/voice/session and POST /tv/v1/tts on the gateway app."""
    from flask import Response, g, request
    from flask_sock import Sock
    sock = Sock(app)

    @app.before_request
    def _voice_gate():
        if request.path != SESSION_PATH:
            return None
        from agent_friday.tv_gateway import owner_turn
        if not owner_turn.is_owner_device(getattr(g, "device", None)):
            return refuse(403, "voice with this computer is for the owner's own phone or tablet",
                          code="owner_only")
        cid = (request.args.get("conversation_id") or "").strip()
        if cid and not owner_turn.valid_cid(cid):
            return refuse(400, "that conversation id is not valid", code="bad_request")
        return None

    @sock.route(SESSION_PATH)
    def voice_session(ws):
        run_session(ws, g.device, (request.args.get("conversation_id") or "").strip())

    @app.post(TTS_PATH)
    def tts():
        body = request.get_json(silent=True) or {}
        try:
            wav, who = speak(str(body.get("text") or ""), str(body.get("voice") or ""))
        except VoiceRefused as e:
            return refuse(e.status, str(e), code=e.code)
        resp = Response(wav, mimetype="audio/wav")
        resp.headers["X-Friday-Voice-Engine"] = who["engine"]
        resp.headers["X-Friday-Voice-Where"] = who["where"]
        return resp
