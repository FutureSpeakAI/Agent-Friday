"""Phase 2: a live call in Friday's own voice, over Twilio Media Streams.

THE PATH. Twilio opens a WebSocket to the ingress (`/twilio/media`, signature
checked on the upgrade) and streams the caller's audio as 8 kHz mu-law frames.
This module decodes it, finds the ends of utterances with the local VAD,
transcribes each one on this machine, asks Friday for a reply, speaks it with
the local TTS, and streams it back as mu-law. No audio goes to any cloud
service; Twilio carries the call, as it must.

The voice pieces are the desktop's own: `local_voice.get_local_voice_engine()`
for speech-to-text and text-to-speech, and `VADEndpointer` for endpointing.
They are shared with the desktop voice session, so a live call and a desktop
voice session at the same time compete for the same engine.

WHAT A CALL MAY DO. Exactly what a text may do: read and answer. A caller who
matches the owner's cell is still unverified (caller ID can be forged), and on
an outbound call the other person is not the owner at all. So:

  * every agent turn runs with `origin: phone` (ring 0 only; see
    agent._governance_check), and
  * THE MID-CALL APPROVAL PATTERN: when an outward action comes up in the call
    ("email me that", "book it"), Friday says it has to check with the owner,
    and writes `[[ASK_OWNER: <what>]]` in its reply. The bridge removes the
    marker from speech, raises an approval card for it (texting the owner a
    code when approval by text is on), and tells the caller when the owner
    has answered, if the call is still up. The action itself never runs from
    the call; it runs only through the approved card's own handler, like any
    other approval.

DISCLOSURE. On an outbound call to anyone but the owner, the first thing said
is the fixed disclosure that Friday is an AI, spoken by Twilio from the TwiML
before the stream starts, so it is heard even if the bridge fails.

STATUS: built and unit-tested against a simulated stream (codec, framing,
endpointing, barge-in, the approval marker). It has not yet carried a real
call; it needs the owner's credentials, a tunnel, and a ready local voice
engine, and `live_calls` is off by default.
"""
from __future__ import annotations

import array
import base64
import json
import logging
import re
import threading
import time
from typing import Callable, Optional
from xml.sax.saxutils import escape, quoteattr

from agent_friday.user_errors import ExceptionText

_log = logging.getLogger("friday.phone.live")

TWILIO_RATE = 8000
ASR_RATE = 16000
FRAME_MS = 20
FRAME_BYTES = TWILIO_RATE * FRAME_MS // 1000          # 160 mu-law bytes per frame
MAX_CALL_S = 20 * 60

# ── G.711 mu-law, pure Python (audioop is gone in Python 3.13) ───────────────

_BIAS = 0x84
_CLIP = 32635


def _ulaw_decode_byte(u: int) -> int:
    u = ~u & 0xFF
    sign = u & 0x80
    exponent = (u >> 4) & 0x07
    mantissa = u & 0x0F
    sample = ((mantissa << 3) + _BIAS) << exponent
    sample -= _BIAS
    return -sample if sign else sample


def _ulaw_encode_sample(s: int) -> int:
    sign = 0x80 if s < 0 else 0
    if s < 0:
        s = -s
    if s > _CLIP:
        s = _CLIP
    s += _BIAS
    exponent = 7
    mask = 0x4000
    while exponent > 0 and not (s & mask):
        exponent -= 1
        mask >>= 1
    mantissa = (s >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


_DECODE = [_ulaw_decode_byte(i) for i in range(256)]
_ENCODE: Optional[bytes] = None


def _encode_table() -> bytes:
    global _ENCODE
    if _ENCODE is None:
        _ENCODE = bytes(_ulaw_encode_sample(s - 32768) for s in range(65536))
    return _ENCODE


def ulaw_to_pcm16(data: bytes) -> bytes:
    out = array.array("h", (_DECODE[b] for b in data))
    return out.tobytes()


def pcm16_to_ulaw(pcm: bytes) -> bytes:
    table = _encode_table()
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    return bytes(table[s + 32768] for s in samples)


def _resample(pcm: bytes, a: int, b: int) -> bytes:
    from agent_friday.services.local_voice import _resample_pcm16
    return _resample_pcm16(pcm, a, b)


# ── availability ─────────────────────────────────────────────────────────────

def why_unavailable() -> str:
    try:
        import flask_sock  # noqa: F401
    except Exception:
        return "flask-sock is not installed"
    try:
        from agent_friday.services import local_voice as lv
        if not lv.deps_installed():
            return "local voice (faster-whisper and piper) is not installed"
        if not lv.get_local_voice_engine().available():
            return "the local voice engine is not ready"
    except Exception as e:
        return ExceptionText("local voice could not be checked: %s" % e)
    return ""


def available() -> bool:
    return why_unavailable() == ""


# ── TwiML ────────────────────────────────────────────────────────────────────

def _stream_url(cfg: dict) -> str:
    return "wss://" + cfg["public_base_url"].split("://", 1)[-1] + "/twilio/media"


def connect_twiml(cfg: dict, call_sid: str) -> str:
    """An inbound call from the owner's cell, answered live."""
    return ("<Connect><Stream url=%s><Parameter name=\"direction\" value=\"inbound\"/>"
            "</Stream></Connect>" % quoteattr(_stream_url(cfg)))


def outbound_twiml(cfg: dict, *, disclosure: str, opening: str, approval_id: str) -> str:
    """An approved outbound live call. The disclosure is spoken by Twilio
    first, before the stream, so it cannot be skipped by a bridge failure."""
    say = ("<Say>%s</Say>" % escape(disclosure.strip())) if disclosure.strip() else ""
    return ("<Response>%s<Connect><Stream url=%s>"
            "<Parameter name=\"direction\" value=\"outbound\"/>"
            "<Parameter name=\"approval_id\" value=%s/>"
            "<Parameter name=\"opening\" value=%s/>"
            "</Stream></Connect></Response>"
            % (say, quoteattr(_stream_url(cfg)), quoteattr(approval_id),
               quoteattr(opening[:400])))


# ── the mid-call approval marker ─────────────────────────────────────────────

_ASK = re.compile(r"\[\[\s*ASK_OWNER\s*:\s*(.+?)\s*\]\]", re.S)

CALL_NOTE = (
    "[You are Friday, speaking on a live phone call. Keep replies short and "
    "spoken. The caller is UNVERIFIED: caller ID can be forged, and on a call "
    "you placed, the other person is not the owner. You may look things up and "
    "answer. You cannot act during a call. If an action comes up (sending, "
    "booking, buying, changing anything), say you need to check with the person "
    "you work for, and write [[ASK_OWNER: <one line saying what, for whom>]] in "
    "your reply. That marker is not spoken; it asks the owner for approval.]")


def split_asks(reply: str):
    """(text to speak, [approval requests]) from an agent reply."""
    asks = [m.strip()[:300] for m in _ASK.findall(reply or "")]
    spoken = _ASK.sub("", reply or "").strip()
    return spoken, asks


def mid_call_request(call_sid: str, what: str, party: str) -> dict:
    """Raise an approval card for an action asked for during a call. It
    records the request; it performs nothing. The owner decides, and whatever
    handler owns that kind of action does it."""
    from agent_friday.services import approvals as ap
    import uuid
    return ap.create_approval(
        kind="external_message", subject_type="phone_midcall",
        subject_id="%s:%s" % (call_sid, uuid.uuid4().hex[:8]),
        title="During a call: %s" % what[:120],
        action_description=("Asked for during a live call with %s:\n\n%s\n\nApproving "
                            "records your answer and Friday tells the caller, if the "
                            "call is still up. Nothing is done from the call itself: "
                            "to have it done, ask Friday in the app."
                            % (party or "a caller", what)),
        description="Requested by a caller, not by you.",
        force_gate=True, requested_by="phone:call",
        payload={"handler": "phone_midcall", "call_sid": call_sid, "what": what})


# ── the bridge ───────────────────────────────────────────────────────────────

def _gate_spoken(text: str) -> str:
    """The egress gate on a spoken reply. If the gate cannot run, a fixed line
    is spoken instead of the reply."""
    try:
        from agent_friday.services.channels.manager import gate_reply
        return gate_reply(text, "phone")
    except Exception:
        return "I can't share that on this call."


class CallBridge:
    """One call's audio loop. Engine, agent and clock are injectable for tests."""

    def __init__(self, ws, *, engine=None, agent: Optional[Callable[[list], str]] = None,
                 vad=None, clock=time.monotonic, threaded: bool = True):
        self.ws = ws
        self.threaded = threaded
        self.stream_sid = ""
        self.call_sid = ""
        self.params: dict = {}
        self.party = ""
        self.history: list = []
        self.asks: list = []
        self.speaking_until = 0.0
        self._clock = clock
        self._send_lock = threading.Lock()
        self._turn_lock = threading.Lock()
        self._told: set = set()
        self._last_poll = clock()
        if engine is None:
            from agent_friday.services.local_voice import get_local_voice_engine
            engine = get_local_voice_engine()
        self.engine = engine
        if vad is None:
            from agent_friday.services.local_voice import VADEndpointer
            vad = VADEndpointer(rate=ASR_RATE)
        self.vad = vad
        self.agent = agent or self._default_agent
        self._started = clock()

    # outbound frames
    def _send(self, msg: dict) -> None:
        with self._send_lock:
            self.ws.send(json.dumps(msg))

    def say(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        pcm24 = self.engine.synthesize(text)
        ulaw = pcm16_to_ulaw(_resample(pcm24, 24000, TWILIO_RATE))
        for i in range(0, len(ulaw), FRAME_BYTES):
            self._send({"event": "media", "streamSid": self.stream_sid,
                        "media": {"payload": base64.b64encode(ulaw[i:i + FRAME_BYTES])
                                  .decode("ascii")}})
        self._send({"event": "mark", "streamSid": self.stream_sid,
                    "mark": {"name": "said-%d" % len(self.history)}})
        self.speaking_until = self._clock() + len(ulaw) / TWILIO_RATE

    def barge_in(self) -> None:
        """The caller spoke over Friday: drop what Twilio still has queued."""
        self._send({"event": "clear", "streamSid": self.stream_sid})
        self.speaking_until = 0.0

    def _default_agent(self, history: list) -> str:
        """One read-only turn, with the same system prompt a text gets: the
        vault's private sections are held back from a caller's conversation."""
        from agent_friday.services.agent import _generate_agent
        from agent_friday.services.channels.manager import _gated_system_prompt
        msgs = [{"role": "user" if h["who"] == "caller" else "assistant",
                 "content": h["text"]} for h in history[-12:]]
        msgs[0]["content"] = CALL_NOTE + "\n\n" + msgs[0]["content"]
        last = history[-1]["text"] if history else ""
        reply, _ = _generate_agent(
            msgs, session_ctx={"origin": "phone", "authenticated": False},
            system_builder=lambda provider: _gated_system_prompt(provider, keywords=last),
            workspace="chat", max_tokens=400)
        return reply or ""

    def poll_asks(self) -> None:
        """Tell the caller once the owner has decided something asked for."""
        if not self.asks:
            return
        from agent_friday.services import approvals as ap
        for aid in self.asks:
            if aid in self._told:
                continue
            rec = ap.get_approval(aid) or {}
            st = rec.get("status")
            if st in ("approved", "denied", "expired"):
                self._told.add(aid)
                what = (rec.get("payload") or {}).get("what") or "that"
                self.say("I've heard back about %s: %s." % (
                    what[:80], "it's approved" if st == "approved" else "not this time"))

    def on_utterance(self, pcm16k: bytes) -> None:
        with self._turn_lock:
            self._turn(pcm16k)

    def _turn(self, pcm16k: bytes) -> None:
        text = (self.engine.transcribe(pcm16k) or "").strip()
        if not text:
            return
        self.history.append({"who": "caller", "text": text})
        try:
            reply = self.agent(self.history)
        except Exception as e:
            _log.warning("live call agent turn failed: %s", e)
            reply = "Sorry, I lost my train of thought. Could you say that again?"
        spoken, asks = split_asks(reply)
        for what in asks:
            try:
                card = mid_call_request(self.call_sid, what, self.party)
                self.asks.append(card.get("approval_id"))
            except Exception as e:
                _log.warning("mid-call approval not raised: %s", e)
        # Everything said on a call leaves the machine: Twilio carries the
        # audio and the caller hears it. It passes the egress gate a text does.
        spoken = _gate_spoken(spoken)
        self.history.append({"who": "friday", "text": spoken})
        self.say(spoken)

    def handle(self, msg: dict) -> bool:
        """One Twilio message. Returns False when the stream is over."""
        ev = msg.get("event")
        if ev == "start":
            st = msg.get("start") or {}
            self.stream_sid = st.get("streamSid") or msg.get("streamSid") or ""
            self.call_sid = st.get("callSid") or ""
            self.params = st.get("customParameters") or {}
            opening = self.params.get("opening") or ""
            if opening:
                self.history.append({"who": "friday", "text": opening})
                self.say(opening)
            return True
        if ev == "media":
            if self._clock() - self._started > MAX_CALL_S:
                self.say("I have to end the call here. Goodbye.")
                return False
            payload = base64.b64decode((msg.get("media") or {}).get("payload") or "")
            pcm16k = _resample(ulaw_to_pcm16(payload), TWILIO_RATE, ASR_RATE)
            done = self.vad.feed(pcm16k)
            if self.speaking_until and self._clock() < self.speaking_until and \
                    getattr(self.vad, "_in_speech", False):
                self.barge_in()
            if done:
                if self.threaded:
                    threading.Thread(target=self.on_utterance, args=(done,),
                                     name="phone-turn", daemon=True).start()
                else:
                    self.on_utterance(done)
            if self._clock() - self._last_poll > 3:
                self._last_poll = self._clock()
                try:
                    self.poll_asks()
                except Exception as e:
                    _log.debug("approval poll: %s", e)
            return True
        if ev == "stop":
            return False
        return True


def serve_media_stream(ws, cfg: dict) -> None:
    """The ingress hands a validated WebSocket here."""
    bridge = CallBridge(ws)
    try:
        while True:
            raw = ws.receive(timeout=60)
            if raw is None:
                break
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if not bridge.handle(msg):
                break
    except Exception as e:
        _log.warning("live call ended with an error: %s", e)
    finally:
        try:
            from agent_friday.phone import spool
            spool.ledger_update_by_sid(bridge.call_sid,
                                       detail="live call, %d turns%s"
                                       % (len(bridge.history),
                                          ", %d approval(s) asked" % len(bridge.asks)
                                          if bridge.asks else ""))
        except Exception:
            pass
