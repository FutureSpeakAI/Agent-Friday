"""Phase 2 live-call bridge, against a simulated Media Stream."""
import array
import base64
import json

from agent_friday.phone import live_call as L
from agent_friday.services import approvals
from tests.unit.phone_fakes import PUBLIC, phone_home, quiet  # noqa: F401


def test_mulaw_matches_g711_reference_points():
    assert L.pcm16_to_ulaw(array.array("h", [0]).tobytes()) == b"\xff"
    assert L._DECODE[0xFF] == 0 and L._DECODE[0x7F] == 0
    assert L._DECODE[0x00] == -32124 and L._DECODE[0x80] == 32124
    samples = array.array("h", range(-32000, 32000, 997))
    back = array.array("h", L.ulaw_to_pcm16(L.pcm16_to_ulaw(samples.tobytes())))
    for a, b in zip(samples, back):
        assert abs(a - b) <= max(16, abs(a) // 16)


def test_outbound_twiml_discloses_before_the_stream():
    cfg = {"public_base_url": PUBLIC}
    tw = L.outbound_twiml(cfg, disclosure="This is Friday, an AI assistant.",
                          opening="Your table is ready", approval_id="appr_1")
    assert tw.index("AI assistant") < tw.index("<Connect>")
    assert 'url="wss://phone.example.test/twilio/media"' in tw
    assert L.outbound_twiml(cfg, disclosure="", opening="hi", approval_id="a").find("<Say>") == -1


def test_the_ask_marker_is_split_out_of_speech():
    spoken, asks = L.split_asks("Sure. [[ASK_OWNER: email Sam the contract]] I'll check.")
    assert spoken == "Sure.  I'll check." and asks == ["email Sam the contract"]
    assert L.split_asks("no marker") == ("no marker", [])


class FakeWS:
    def __init__(self):
        self.sent = []

    def send(self, s):
        self.sent.append(json.loads(s))


class FakeEngine:
    def __init__(self):
        self.said = []

    def synthesize(self, text):
        self.said.append(text)
        return array.array("h", [1000] * 2400).tobytes()     # 100 ms at 24 kHz

    def transcribe(self, pcm):
        return "can you email me the contract"


class OneShotVAD:
    """Ends an utterance on the third frame; reports speech in progress."""
    def __init__(self):
        self.n = 0
        self._in_speech = True

    def feed(self, pcm):
        self.n += 1
        return pcm if self.n == 3 else None


def _media(ulaw=b"\xff" * 160):
    return {"event": "media", "media": {"payload": base64.b64encode(ulaw).decode()}}


def test_a_simulated_call_speaks_asks_and_never_acts(phone_home, quiet):
    ws, eng = FakeWS(), FakeEngine()
    bridge = L.CallBridge(ws, engine=eng, vad=OneShotVAD(), threaded=False,
                          agent=lambda h: "I can't send that from a call. "
                                          "[[ASK_OWNER: email the contract to the caller]]")
    assert bridge.handle({"event": "start", "start": {
        "streamSid": "MZ1", "callSid": "CA1", "customParameters": {"opening": "Hello there"}}})
    assert eng.said == ["Hello there"]
    frames = [m for m in ws.sent if m["event"] == "media"]
    assert frames and all(len(base64.b64decode(f["media"]["payload"])) <= L.FRAME_BYTES
                          for f in frames)
    for _ in range(3):
        bridge.handle(_media())
    assert eng.said[-1] == "I can't send that from a call."          # marker not spoken
    [card] = approvals.list_approvals()
    assert card["status"] == "pending" and card["subject_type"] == "phone_midcall"
    assert card["payload"]["handler"] == "phone_midcall"
    assert bridge.handle({"event": "stop"}) is False


def test_speaking_over_friday_clears_the_queued_audio(phone_home):
    ws, eng = FakeWS(), FakeEngine()
    bridge = L.CallBridge(ws, engine=eng, vad=OneShotVAD(), threaded=False,
                          agent=lambda h: "ok", clock=lambda: 0.0)
    bridge.stream_sid = "MZ1"
    bridge.speaking_until = 5.0
    bridge.handle(_media())
    assert ws.sent[-1]["event"] == "clear"


def test_the_caller_hears_when_the_owner_decides(phone_home, quiet, monkeypatch):
    ws, eng = FakeWS(), FakeEngine()
    t = [0.0]
    bridge = L.CallBridge(ws, engine=eng, vad=OneShotVAD(), threaded=False,
                          agent=lambda h: "[[ASK_OWNER: book the table]]", clock=lambda: t[0])
    bridge.handle({"event": "start", "start": {"streamSid": "MZ", "callSid": "CA2"}})
    for _ in range(3):
        bridge.handle(_media())
    monkeypatch.setattr(approvals, "_HOOKS", {})
    approvals.decide(bridge.asks[0], "approve")
    t[0] = 10.0
    bridge.speaking_until = 0
    bridge.handle(_media())
    assert "approved" in eng.said[-1]


def test_a_phone_turn_in_a_call_is_read_only(phone_home, monkeypatch):
    seen = {}
    import agent_friday.services.agent as agent
    monkeypatch.setattr(agent, "_generate_agent",
                        lambda msgs, **kw: (seen.update(kw) or "hi", []))
    bridge = L.CallBridge(FakeWS(), engine=FakeEngine(), vad=OneShotVAD(), threaded=False)
    bridge._default_agent([{"who": "caller", "text": "hello"}])
    assert seen["session_ctx"] == {"origin": "phone", "authenticated": False}
