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


#: A stream's signature covers only the fixed media URL, the same for every call, so
#: the "direction" parameter cannot be trusted on its own: a captured signature could
#: open a stream claiming "inbound". An inbound stream must carry a single-use ticket
#: Friday minted in that call's own TwiML; without a good one the call is treated as
#: one with someone else (NS-34.17-2).
_TICKET_TTL_S = 120
#: A call Friday places carries a ticket too (minted before Twilio has a call SID, so
#: it is bound to no SID): it has to last through the ringing.
_OUTBOUND_TICKET_TTL_S = 600
_USED_TICKETS: dict = {}
_TICKET_LOCK = threading.Lock()


def _ticket_key() -> bytes:
    from agent_friday.governance.action_gate import _governance_key
    import hashlib as _h
    return _h.sha256(b"agent-friday/call-ticket/v1\0" + _governance_key()).digest()


def mint_ticket(direction: str, call_sid: str) -> str:
    import hashlib as _h
    import hmac as _hm
    import secrets as _s
    body = "%s|%s|%d|%s" % (direction, call_sid or "", int(time.time()), _s.token_hex(8))
    sig = _hm.new(_ticket_key(), body.encode("utf-8"), _h.sha256).hexdigest()
    return body + "|" + sig


def ticket_direction(ticket: str, call_sid: str = "") -> str:
    """The direction a good, fresh, unused ticket for THIS call names; "" otherwise
    (spent here)."""
    import hashlib as _h
    import hmac as _hm
    try:
        body, sig = str(ticket or "").rsplit("|", 1)
        direction, sid, ts, _nonce = body.split("|")
        good = _hm.compare_digest(sig, _hm.new(_ticket_key(), body.encode("utf-8"),
                                               _h.sha256).hexdigest())
    except Exception:
        return ""
    now = time.time()
    ttl = _OUTBOUND_TICKET_TTL_S if direction == "outbound" else _TICKET_TTL_S
    if not good or not (0 <= now - int(ts) <= ttl):
        return ""
    if sid != str(call_sid or ""):        # bound to its own call (or approved card)
        return ""
    with _TICKET_LOCK:
        for k in [k for k, t in _USED_TICKETS.items() if now - t > 2 * _OUTBOUND_TICKET_TTL_S]:
            _USED_TICKETS.pop(k, None)
        if body in _USED_TICKETS:
            return ""
        _USED_TICKETS[body] = now
    return direction


def connect_twiml(cfg: dict, call_sid: str) -> str:
    """An inbound call from the owner's cell, answered live."""
    try:
        ticket = mint_ticket("inbound", call_sid)
    except Exception:
        ticket = ""                     # without one the call is treated as someone else
    return ("<Connect><Stream url=%s><Parameter name=\"direction\" value=\"inbound\"/>"
            "<Parameter name=\"ticket\" value=%s/>"
            "</Stream></Connect>" % (quoteattr(_stream_url(cfg)), quoteattr(ticket)))


def outbound_twiml(cfg: dict, *, disclosure: str, opening: str, approval_id: str) -> str:
    """An approved outbound live call. The disclosure is spoken by Twilio
    first, before the stream, so it cannot be skipped by a bridge failure."""
    say = ("<Say>%s</Say>" % escape(disclosure.strip())) if disclosure.strip() else ""
    try:
        ticket = mint_ticket("outbound", approval_id)     # bound to the approved call
    except Exception:
        ticket = ""          # without one the call is unverified: no free/busy either
    return ("<Response>%s<Connect><Stream url=%s>"
            "<Parameter name=\"direction\" value=\"outbound\"/>"
            "<Parameter name=\"approval_id\" value=%s/>"
            "<Parameter name=\"opening\" value=%s/>"
            "<Parameter name=\"ticket\" value=%s/>"
            "</Stream></Connect></Response>"
            % (say, quoteattr(_stream_url(cfg)), quoteattr(approval_id),
               quoteattr(opening[:400]), quoteattr(ticket)))


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


#: The whole system prompt on a call with someone other than the owner: who Friday is
#: and how to behave, and nothing of the owner's notes, memory or files.
OTHER_PARTY_SYSTEM = (
    "You are Friday, an AI assistant, speaking on a phone call on behalf of the person "
    "you work for. Treat the other person as someone else, not the person you work for. You know "
    "only what was said on this call and the purpose you were given for it. Do not "
    "guess at, or reveal, anything about the person you work for: their schedule, "
    "contacts, files, notes or plans. If the other person asks for something that needs "
    "them, say you will check, and write [[ASK_OWNER: <one line>]] in your reply. Keep "
    "replies short and spoken.")

#: Added to OTHER_PARTY_SYSTEM on a call Friday placed once the talk turns to times
#: (owner ruling 7): when the person you work for is busy, and nothing else about it.
FREE_BUSY_NOTE = (
    "\n\nTo arrange a time you may use when the person you work for is busy. Over the "
    "next %d days (their local time) they are busy at: %s. Every other time is free. "
    "Share only whether a time is free or busy: never what they are doing, where, or "
    "with whom. Offering or agreeing a time is still a request: write [[ASK_OWNER: "
    "book <time> with <who>]] for it.")
FREE_BUSY_DAYS = 7
_TIME_TALK = re.compile(
    r"\b(time|times|when|free|busy|available|availability|schedul\w*|book\w*|appointment|"
    r"meet\w*|call back|today|tomorrow|tonight|week|weekend|morning|afternoon|evening|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|\d{1,2}\s*(?:am|pm|a\.m\.|p\.m\.)|"
    r"o'?clock)\b", re.I)

#: The whole system prompt on a call from the owner's cell before the call PIN is said
#: (owner ruling 8): nothing of the owner's records, and how to open them.
PIN_NEEDED_SYSTEM = (
    "You are Friday, an AI assistant, on a phone call that came from the number of the "
    "person you work for. Caller ID can be forged, so until the caller says their call "
    "PIN you know nothing about the person you work for and open none of their records: "
    "no schedule, contacts, files, notes or plans. If they ask for anything that needs "
    "those, ask them to say their call PIN. Never repeat or guess a PIN. Keep replies "
    "short and spoken.")
PIN_UNSET_SYSTEM = (
    "You are Friday, an AI assistant, on a phone call that came from the number of the "
    "person you work for. Calls from their phone need a spoken call PIN before you open "
    "any of their records, and no PIN has been set up yet, so on this call you open none "
    "of them: no schedule, contacts, files, notes or plans. If they ask for those, say "
    "the call PIN can be set in Friday's phone settings on the computer Friday runs on. "
    "Keep replies short and spoken.")


def free_busy_text(days: int = FREE_BUSY_DAYS, now=None) -> str:
    """The owner's busy intervals for the next `days` days in their local time, times
    only ("Tue 29 Oct 14:00-15:30"); "" when no calendar could be read."""
    from datetime import datetime, timedelta, timezone
    from agent_friday.services import scheduling as sch
    zone = sch.zone_for(sch.scheduling_settings().get("timezone"))
    start = now or datetime.now(timezone.utc)
    got = sch.busy_across_accounts(start, start + timedelta(days=int(days)), zone)
    if not got.get("checked") or got.get("errors"):
        return ""          # a calendar that could not be read: "free" would not be true
    parts = []
    for s, e in (got.get("busy") or [])[:60]:
        s, e = s.astimezone(zone), e.astimezone(zone)
        same = s.date() == e.date()
        parts.append("%s %d %s %s-%s" % (s.strftime("%a"), s.day, s.strftime("%b"), s.strftime("%H:%M"),
                                         e.strftime("%H:%M") if same else
                                         "%s %d %s %s" % (e.strftime("%a"), e.day, e.strftime("%b"),
                                                          e.strftime("%H:%M"))))
    return "; ".join(parts) or "nothing (free all of it)"


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

def _record_spoken(party: str, text: str, error) -> None:
    """What Friday says on a live call in the egress ledger and on the privacy
    map (§26.7): Twilio carries the audio of these words to the caller. The
    words are fingerprinted and, by default (A20), kept encrypted. Never raises."""
    try:
        from agent_friday.services import egress_gate as _eg
        _eg.record_service_send("phone_call", "twilio", destination="api.twilio.com",
                                content={"to": party, "said": text},
                                result="sent" if error is None else _eg.result_for_error(error),
                                recipients=1, part="spoken")
    except Exception:
        pass


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
        from agent_friday import brand
        text = brand.spoken((text or "").strip())
        if not text:
            return
        pcm24 = self.engine.synthesize(text)
        ulaw = pcm16_to_ulaw(_resample(pcm24, 24000, TWILIO_RATE))
        try:
            for i in range(0, len(ulaw), FRAME_BYTES):
                self._send({"event": "media", "streamSid": self.stream_sid,
                            "media": {"payload": base64.b64encode(ulaw[i:i + FRAME_BYTES])
                                      .decode("ascii")}})
            self._send({"event": "mark", "streamSid": self.stream_sid,
                        "mark": {"name": "said-%d" % len(self.history)}})
        except Exception as e:
            _record_spoken(self.party, text, e)
            raise
        _record_spoken(self.party, text, None)
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
        from agent_friday.services.agent_identity import named
        msgs[0]["content"] = named(CALL_NOTE) + "\n\n" + msgs[0]["content"]
        last = history[-1]["text"] if history else ""
        ctx = {"origin": "phone", "authenticated": False}
        builder = lambda provider: _gated_system_prompt(provider, keywords=last)  # noqa: E731
        direction = str((getattr(self, "params", None) or {}).get("direction") or "").lower()
        if direction == "inbound":
            # From the owner's cell. Records open only once the call PIN was said, or
            # when the owner switched the PIN off at the PC (owner ruling 8). Unknown
            # PIN state is treated as not said.
            state = getattr(self, "pin_state", None)
            if state not in ("verified", "not_required"):
                ctx["call_party"] = "unverified"
                prompt = PIN_UNSET_SYSTEM if state == "unset" else PIN_NEEDED_SYSTEM
                builder = lambda provider: prompt  # noqa: E731
        else:
            # A call Friday placed, or one whose direction is not verified: the other
            # person is not the owner. No tool reads the owner's records
            # (agent._governance_check), and the system prompt carries none of them
            # either: no wiki or memory pulled in by what the other person says
            # (NS-34.17-2). Anything that needs the owner goes through [[ASK_OWNER]].
            # On a call Friday placed (ticketed "outbound"), once the talk turns to
            # times, the owner's busy times go in too, and nothing else (ruling 7).
            ctx["call_party"] = "other" if direction == "outbound" else "unverified"
            prompt = OTHER_PARTY_SYSTEM
            if direction == "outbound":
                busy = self._free_busy_for(history)
                if busy:
                    prompt = OTHER_PARTY_SYSTEM + FREE_BUSY_NOTE % (FREE_BUSY_DAYS, busy)
            builder = lambda provider: prompt  # noqa: E731
        reply, _ = _generate_agent(
            msgs, session_ctx=ctx, system_builder=builder,
            workspace="chat", max_tokens=400)
        return reply or ""

    def _free_busy_for(self, history: list) -> str:
        """The owner's busy times, once the call has talked about times; read once
        per call and kept for five minutes. "" before that, or when unreadable."""
        # What the other person said, and the call's purpose without Friday's own name
        # (which is also a weekday): Friday's own lines do not count.
        said = " ".join(str(h.get("text") or "") for h in (history or [])[-12:]
                        if h.get("who") == "caller")
        purpose = re.sub(r"\bfriday\b", " ", str((getattr(self, "params", None) or {}).get("opening") or ""),
                         flags=re.I)
        if not _TIME_TALK.search(said + " " + purpose):
            return ""
        cached = getattr(self, "_busy_cache", None)
        now = time.monotonic()
        if cached and now - cached[0] < 300:
            return cached[1]
        try:
            text = free_busy_text()
        except Exception as e:  # noqa: BLE001
            _log.warning("free/busy for a call not read: %s", e)
            text = ""
        self._busy_cache = (now, text)
        return text

    def _pin_turn(self, text: str) -> bool:
        """On a call from the owner's cell before the PIN, every turn is answered here by
        the bridge, not the model; a PIN attempt is checked. The digits never reach the
        model or the history. True when the turn was handled here."""
        if str((getattr(self, "params", None) or {}).get("direction") or "").lower() != "inbound":
            return False
        state = getattr(self, "pin_state", None)
        if state in ("verified", "not_required"):
            return False
        # Before the PIN nothing the caller says reaches the model: a PIN said in a way
        # this does not read as one ("four oh seven...", split over two breaths) would
        # otherwise go to it. Words with a number in them are not kept either.
        from agent_friday.phone import call_pin
        digits = call_pin.digits_in(text)
        if state in ("failed", "unset", "") or digits is None:
            self.history.append({"who": "caller", "text": "[said a number]"
                                 if call_pin.has_number(text) else text})
            if state == "unset":
                reply = ("No call PIN is set up yet, so I can't open anything of yours on this "
                         "call. You can set one in Friday's phone settings on your computer.")
            elif state == "failed":
                reply = "Your records stay closed on this call."
            elif call_pin.has_number(text):
                reply = "Please say just your call PIN, digit by digit."
            else:
                reply = "Before I open anything of yours, please say your call PIN."
        elif call_pin.verify(digits):
            self.pin_state = "verified"
            self.history.append({"who": "caller", "text": "[said the call PIN]"})
            reply = "Thanks, that's you. What can I do?"
        else:
            self.pin_tries = int(getattr(self, "pin_tries", 0) or 0) + 1
            self.history.append({"who": "caller", "text": "[said a call PIN that did not match]"})
            if self.pin_tries >= call_pin.TRIES_PER_CALL or call_pin.locked():
                self.pin_state = "failed"
                reply = "That didn't match. I'll keep your records closed on this call."
            else:
                reply = "That PIN didn't match. Please say it again."
        self.history.append({"who": "friday", "text": reply})
        self.say(reply)
        return True

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
        if self._pin_turn(text):
            return
        self.history.append({"who": "caller", "text": text})
        from agent_friday.services import voice_repeat
        if voice_repeat.is_repeat_request(text):
            # "Say that again": the last thing said to this caller, word for
            # word, with no model call (NS-33.3-2). Already through the gate.
            last = next((h["text"] for h in reversed(self.history[:-1]) if h.get("who") == "friday"), "")
            again = _gate_spoken(voice_repeat.repeat_reply(last))     # gated again: cheap, and safe
            self.history.append({"who": "friday", "text": again})
            self.say(again)
            return
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
            self.params = dict(st.get("customParameters") or {})
            # "inbound" only with the single-use ticket this call's own TwiML carried;
            # otherwise the call is treated as one with someone else.
            claimed = str(self.params.get("direction") or "").lower()
            # An inbound ticket is bound to its call's SID; an outbound one (minted before
            # Twilio has a SID) to the approved card it places the call for.
            bound = self.call_sid if claimed == "inbound" else str(self.params.get("approval_id") or "")
            if claimed in ("inbound", "outbound") \
                    and (not bound or ticket_direction(self.params.get("ticket") or "", bound) != claimed):
                self.params["direction"] = "unverified"
            self.params.pop("ticket", None)
            # A call from the owner's cell starts with its records closed until the call
            # PIN is said (owner ruling 8; on unless switched off at the PC).
            self.pin_state, self.pin_tries = "", 0
            if self.params.get("direction") == "inbound":
                try:
                    from agent_friday.phone import call_pin
                    if not call_pin.required():
                        self.pin_state = "not_required"
                    elif not call_pin.is_set():
                        self.pin_state = "unset"
                    else:
                        self.pin_state = "needed"
                except Exception as e:  # noqa: BLE001 - unsure means closed
                    _log.warning("call PIN state not read: %s", e)
                    self.pin_state = "unset"
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
    from agent_friday.services import global_stop as _gs
    if _gs.active():
        try:
            ws.close()
        except Exception:
            pass
        return
    bridge = CallBridge(ws)

    def _hang_up():   # Stop everything ends a live phone call too
        try:
            ws.close()
        except Exception:
            pass
    _gs_off = _gs.on_stop(_hang_up)
    try:
        while True:
            if _gs.active():
                break
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
        _gs_off()
        try:
            from agent_friday.phone import spool
            spool.ledger_update_by_sid(bridge.call_sid,
                                       detail="live call, %d turns%s"
                                       % (len(bridge.history),
                                          ", %d approval(s) asked" % len(bridge.asks)
                                          if bridge.asks else ""))
        except Exception:
            pass
