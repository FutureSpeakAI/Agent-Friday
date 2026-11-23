"""The PC's voice for a paired device (tv_gateway/voice_bridge): the wire contract, pinned.

* ``POST /tv/v1/tts`` speaks text in the PC's selected voice for any paired device, markdown stripped, with who
  spoke in the headers; refusals are coded.
* ``GET /tv/v1/voice/session`` (WebSocket, wire contract v1 agreed with the phone lane) is owner-device only,
  validates the conversation id, and bridges to the PC's own session route for the selected engine: binary PCM up
  and down, press/release/barge_in/stop/ping up, engine (the who-spoke badge: engine, local or cloud, ear/mind/mouth),
  speaking, transcript, turn_done, status, error (in words) and closed down, and nothing else either way. The last
  test runs the whole path over real sockets.
"""
from __future__ import annotations

import base64
import json
import queue
import threading

import pytest

from agent_friday.services import device_pairing
from agent_friday.tv_gateway import app as gateway
from agent_friday.tv_gateway import household, voice_bridge as vb
from tests.unit.test_tv_gateway import FakeTv, auth, open_session

LOCAL = {"engine": "local", "label": "Local (private, on-device)", "ws_url": "/ws/voice-local"}
GEMINI = {"engine": "gemini", "label": "Cloud (Gemini Live)", "ws_url": "/ws/live"}
DEMO = {"engine": "demo", "label": "Text only", "ws_url": None}


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr(household, "_path", lambda: tmp_path / "household.json")
    monkeypatch.setattr(gateway, "_BUCKET", gateway._Bucket())
    device_pairing._path().unlink(missing_ok=True)
    device_pairing._reset_for_tests()
    household.ensure_default_members()
    vb._ACTIVE.clear()
    yield
    device_pairing._reset_for_tests()
    vb._ACTIVE.clear()


@pytest.fixture
def client():
    return gateway.create_app().test_client()


def paired(kind, member):
    dev = FakeTv()
    device_pairing.add_device(dev.spki, name="A device", kind=kind)
    if member:
        household.bind_device(dev.id, member)
    return dev


# ── POST /tv/v1/tts ──────────────────────────────────────────────────────────

class _Engine:
    def __init__(self, pcm=b"\x01\x00" * 2400, fail=False):
        self.said, self.pcm, self.fail = [], pcm, fail

    def synthesize(self, text):
        self.said.append(text)
        if self.fail:
            raise RuntimeError("no voice")
        return self.pcm


def _tts(client, dev, **body):
    return client.post("/tv/v1/tts", json=body, headers=auth(open_session(client, dev)))


def test_tts_needs_a_device_session(client):
    assert client.post("/tv/v1/tts", json={"text": "hi"}).status_code == 401


def test_a_paired_tv_hears_the_text_in_the_pcs_local_voice_with_who_spoke(client, monkeypatch):
    eng = _Engine()
    monkeypatch.setattr(vb, "resolve_engine", lambda: LOCAL)
    from agent_friday.services import local_voice
    monkeypatch.setattr(local_voice, "get_local_voice_engine", lambda: eng)
    r = _tts(client, paired("tv", None), text="**Your day** is [clear](http://x.example). See https://x.example/a")
    assert r.status_code == 200 and r.mimetype == "audio/wav" and r.data[:4] == b"RIFF"
    assert r.headers["X-Friday-Voice-Engine"] == "local" and r.headers["X-Friday-Voice-Where"] == "local"
    assert eng.said == ["Your day is clear. See the link"]


def test_a_cloud_voice_speaks_through_the_pcs_key_and_says_cloud(client, monkeypatch):
    import io
    from agent_friday.routes import voice as rv
    monkeypatch.setattr(vb, "resolve_engine", lambda: GEMINI)
    monkeypatch.setattr(rv, "_synthesize_tts_wav", lambda text, voice=None, style="briefing": io.BytesIO(b"RIFFxxxx"))
    r = _tts(client, paired("tablet", "stephen"), text="Good morning.")
    assert r.status_code == 200 and r.headers["X-Friday-Voice-Where"] == "cloud"
    assert r.headers["X-Friday-Voice-Engine"] == "gemini"


@pytest.mark.parametrize("body,info,status,code", [
    ({"text": ""}, LOCAL, 400, "empty"),
    ({"text": "```code```"}, LOCAL, 400, "empty"),
    ({"text": "x" * 2001}, LOCAL, 413, "too_long"),
    ({"text": "hello"}, DEMO, 503, "no_engine"),
    ({"text": "hello"}, {"engine": "openai", "ws_url": "/ws/openai-live"}, 503, "no_engine"),
])
def test_tts_refusals_are_coded(client, monkeypatch, body, info, status, code):
    monkeypatch.setattr(vb, "resolve_engine", lambda: info)
    r = _tts(client, paired("tv", None), **body)
    assert r.status_code == status and r.get_json()["code"] == code


def test_a_local_voice_that_cannot_speak_is_a_503_not_silence(client, monkeypatch):
    from agent_friday.services import local_voice
    monkeypatch.setattr(vb, "resolve_engine", lambda: LOCAL)
    monkeypatch.setattr(local_voice, "get_local_voice_engine", lambda: _Engine(fail=True))
    r = _tts(client, paired("tv", None), text="hello")
    assert r.status_code == 503 and r.get_json()["code"] == "voice_unavailable"


# ── the session's HTTP gate ──────────────────────────────────────────────────

def _session_get(client, dev, **q):
    return client.get(vb.SESSION_PATH, query_string=q, headers=auth(open_session(client, dev)))


def test_a_voice_session_needs_a_device_session(client):
    assert client.get(vb.SESSION_PATH).status_code == 401


@pytest.mark.parametrize("kind,member", [("tv", None), ("phone", "dana")])
def test_a_device_that_is_not_the_owners_personal_device_is_403(client, kind, member):
    if member and member not in [m["id"] for m in household.list_members()]:
        pytest.skip("no such household member in this fixture")
    r = _session_get(client, paired(kind, member))
    assert r.status_code == 403 and r.get_json()["code"] == "owner_only"


def test_the_owners_watch_is_refused_voice_with_the_pc(client):
    r = _session_get(client, paired("watch", "stephen"))
    assert r.status_code == 403 and r.get_json()["code"] == "owner_only"


def test_a_bad_conversation_id_is_refused_before_the_upgrade(client):
    r = _session_get(client, paired("phone", "stephen"), conversation_id="../x")
    assert r.status_code == 400 and r.get_json()["code"] == "bad_request"


def test_the_owners_phone_passes_the_gate(client):
    r = _session_get(client, paired("phone", "stephen"), conversation_id="conv-1")
    assert r.status_code not in (401, 403)


# ── the bridge's frame rules (wire contract v1) ──────────────────────────────

def _bridge(info=LOCAL, clock=None):
    sent, up = [], []
    kw = {"clock": clock} if clock else {}
    return vb.Bridge(info, sent.append, up.append, **kw), sent, up


def _ups(up):
    return [json.loads(x) for x in up]


def test_the_engine_event_is_the_badge_and_says_local_or_cloud():
    b, sent, _ = _bridge(LOCAL)
    b.open()
    assert sent == [{"type": "engine", "engine": "local", "where": "local", "local": True,
                     "label": "Local (private, on-device)"}]
    b, sent, _ = _bridge(GEMINI)
    b.open()
    assert sent[0]["engine"] == "gemini" and sent[0]["where"] == "cloud" and sent[0]["local"] is False
    b, sent, _ = _bridge(DEMO)
    b.open()
    assert sent[0]["where"] == "none"


def test_served_by_updates_the_badge_with_ear_mind_mouth_and_hides_the_brain_state():
    b, sent, _ = _bridge(LOCAL)
    b.from_upstream(json.dumps({"type": "served_by", "ear": "faster-whisper@cuda", "mind": "Ternary Bonsai 1.7B@gpu",
                                "brain": "parked (voice call)", "mouth": "kokoro@cuda", "voice": "af_bella"}))
    assert [f["type"] for f in sent] == ["engine"]
    assert sent[0]["ear"] == "faster-whisper@cuda" and sent[0]["mouth"] == "kokoro@cuda" and sent[0]["voice"] == "af_bella"
    assert "brain" not in sent[0]


def test_device_audio_goes_up_as_the_desktops_base64_frame():
    b, _, up = _bridge()
    pcm = bytes(range(64)) * 50
    assert b.from_device(pcm)
    assert _ups(up) == [{"type": "audio", "data": base64.b64encode(pcm).decode()}]


def test_press_does_nothing_unless_she_is_speaking_and_then_it_is_a_barge_in():
    b, sent, up = _bridge()
    assert b.from_device(json.dumps({"type": "press"})) and up == []
    b.from_upstream(json.dumps({"type": "audio", "data": base64.b64encode(b"\x01\x00" * 8).decode()}))
    sent.clear()
    assert b.from_device(json.dumps({"type": "press"}))
    assert _ups(up) == [{"type": "barge"}] and sent == [{"type": "speaking", "who": "you"}]


def test_release_appends_a_second_of_silence_so_the_engine_closes_the_turn():
    b, _, up = _bridge()
    assert b.from_device(json.dumps({"type": "release"}))
    frames = _ups(up)
    assert len(frames) == vb.RELEASE_SILENCE_FRAMES and all(f["type"] == "audio" for f in frames)
    assert set(base64.b64decode(f["data"]) for f in frames) == {b"\x00" * 3200}


def test_barge_in_stops_her_and_starts_the_turn():
    b, sent, up = _bridge()
    assert b.from_device(json.dumps({"type": "barge_in"}))
    assert _ups(up) == [{"type": "barge"}] and sent == [{"type": "speaking", "who": "you"}]


def test_stop_ends_the_pcs_session_and_the_bridge():
    b, _, up = _bridge()
    assert b.from_device(json.dumps({"type": "stop"})) is False
    assert _ups(up) == [{"type": "end"}] and b.closed[0] == "stopped"


def test_ping_is_answered_and_forwards_nothing():
    b, sent, up = _bridge()
    assert b.from_device(json.dumps({"type": "ping"}))
    assert sent == [{"type": "pong"}] and up == []


@pytest.mark.parametrize("asked,chosen,ok", [("gemini_live", "gemini", True), ("local", "local", True),
                                             ("pc", "local", True), (None, "gemini", True),
                                             ("local", "gemini", False), ("gemini_live", "local", False),
                                             ("klingon", "local", False)])
def test_start_names_an_engine_only_as_advice_the_pc_never_switches(asked, chosen, ok):
    b, sent, up = _bridge({"engine": chosen, "label": chosen, "ws_url": "/ws/live"})
    frame = {"type": "start"}
    if asked is not None:
        frame["engine"] = asked
    assert b.from_device(json.dumps(frame)) is ok
    if not ok:
        assert sent[0]["type"] == "error" and sent[0]["code"] == "engine_not_selected"
        assert b.closed[0] == "engine_not_selected" and up == []


def test_the_device_may_send_only_the_listed_frames_everything_else_is_dropped():
    b, sent, up = _bridge()
    for frame in ({"type": "image", "data": "/9j/"}, {"type": "setup", "model": "x"}, {"type": "text", "text": "hi"},
                  {"type": "phone_action", "verb": "x"}, ["not", "a", "dict"], {"no": "type"}):
        assert b.from_device(json.dumps(frame))
    assert b.from_device("{broken")
    assert up == [] and sent == []


@pytest.mark.parametrize("frame,code", [
    (b"\x00" * (vb.MAX_PCM_FRAME + 1), "frame_too_large"),
    ("x" * (vb.MAX_TEXT_FRAME + 1), "frame_too_large"),
    (12345, "frame_too_large"),
])
def test_an_oversize_or_odd_frame_ends_the_session_and_is_not_forwarded(frame, code):
    b, _, up = _bridge()
    assert b.from_device(frame) is False
    assert up == [] and b.closed[0] == code


def test_pc_audio_goes_down_as_binary_and_marks_her_speaking_once():
    b, sent, _ = _bridge()
    pcm = b"\x01\x00\x02\x00" * 10
    for _ in range(3):
        b.from_upstream(json.dumps({"type": "audio", "data": base64.b64encode(pcm).decode()}))
    assert sent == [{"type": "speaking", "who": "friday"}, pcm, pcm, pcm]
    b.from_upstream(json.dumps({"type": "audio", "data": "!!not base64!!"}))
    assert len(sent) == 4


def test_transcripts_carry_who_and_whether_final_and_hearing_you_marks_you_speaking():
    b, sent, _ = _bridge()
    b.from_upstream(json.dumps({"type": "partial_transcript", "text": "Good"}))
    b.from_upstream(json.dumps({"type": "input_transcript", "text": "Good morning"}))
    b.from_upstream(json.dumps({"type": "text", "text": "Morning!"}))
    assert sent == [{"type": "speaking", "who": "you"},
                    {"type": "transcript", "who": "you", "text": "Good", "final": False},
                    {"type": "transcript", "who": "you", "text": "Good morning", "final": True},
                    {"type": "transcript", "who": "friday", "text": "Morning!", "final": False}]


@pytest.mark.parametrize("done", [{"type": "voice_turn_done", "user_text": "hi", "agent_text": "Hello."},
                                  {"type": "turn_end"}])
def test_a_turn_ends_with_turn_done_carrying_the_engine_and_the_final_text(done):
    b, sent, _ = _bridge(LOCAL)
    b.from_upstream(json.dumps({"type": "served_by", "mouth": "kokoro@cuda"}))
    sent.clear()
    b.from_upstream(json.dumps(done))
    td = sent[-1]
    assert td["type"] == "turn_done" and td["engine"]["where"] == "local" and td["engine"]["mouth"] == "kokoro@cuda"
    if done["type"] == "voice_turn_done":
        assert sent[0] == {"type": "transcript", "who": "friday", "text": "Hello.", "final": True}
    assert b.speaker is None


def test_interrupted_hands_the_floor_to_you():
    b, sent, _ = _bridge()
    b.from_upstream(json.dumps({"type": "audio", "data": base64.b64encode(b"\x01\x00").decode()}))
    sent.clear()
    b.from_upstream(json.dumps({"type": "interrupted"}))
    assert sent == [{"type": "speaking", "who": "you"}]


def test_errors_are_said_in_words_and_never_pass_the_desktops_raw_text():
    b, sent, _ = _bridge()
    b.from_upstream(json.dumps({"type": "error", "error": "Traceback: C:\\Users\\x\\secret.py line 3"}))
    b.from_upstream(json.dumps({"type": "error", "error": "unauthorized"}))
    assert [f["code"] for f in sent] == ["voice_error", "unauthorized"]
    assert all(f["words"] and "Traceback" not in f["words"] and "secret" not in f["words"] for f in sent)


def test_the_desktops_own_panels_never_reach_the_device():
    b, sent, _ = _bridge()
    for t in ("manifest", "contract", "context_reach", "stage", "route", "receipt", "voiced_cards", "something_new"):
        b.from_upstream(json.dumps({"type": t, "x": 1}))
    b.from_upstream("not json")
    b.from_upstream(None)
    assert sent == []


def test_status_text_reaches_the_device_so_a_cold_start_can_be_shown():
    b, sent, _ = _bridge()
    b.from_upstream(json.dumps({"type": "status", "text": "Warming up the voice - about 20 s"}))
    assert sent == [{"type": "status", "text": "Warming up the voice - about 20 s"}]


def test_a_session_times_out_idle_and_at_the_hard_limit():
    now = [0.0]
    b, _, _ = _bridge(clock=lambda: now[0])
    assert b.timed_out() is None
    now[0] = vb.IDLE_S + 1
    assert b.timed_out()[0] == "idle"
    b.from_device(json.dumps({"type": "ping"}))                  # a frame from the device keeps it alive
    assert b.timed_out() is None
    now[0] = vb.MAX_SESSION_S
    b.from_device(json.dumps({"type": "ping"}))                  # still talking at the limit: idle cannot fire
    now[0] = vb.MAX_SESSION_S + 1
    assert b.timed_out()[0] == "time_limit"


def test_a_control_frame_is_measured_in_bytes_not_characters():
    b, sent, _ = _bridge()
    wide = json.dumps({"type": "ping", "pad": "é" * (vb.MAX_TEXT_FRAME // 2 + 10)}, ensure_ascii=False)
    assert len(wide) <= vb.MAX_TEXT_FRAME < len(wide.encode("utf-8"))
    assert b.from_device(wide) is False and b.closed[0] == "frame_too_large"


# ── run_session over fakes ───────────────────────────────────────────────────

class _Q:
    """A socket stand-in: ``receive`` pops what the other side queued; ``send`` records."""

    def __init__(self):
        self.inbox, self.sent, self.closed = queue.Queue(), [], False

    def receive(self, timeout=None):
        try:
            return self.inbox.get(timeout=timeout or 0.05)
        except queue.Empty:
            return None

    def send(self, raw):
        self.sent.append(raw)

    def close(self):
        self.closed = True


def _run(ws, dev_id="d1", info=LOCAL, upstream=None, connect=None, monkeypatch=None, allowed=True):
    monkeypatch.setattr(vb, "resolve_engine", lambda: info)
    if allowed is True:
        monkeypatch.setattr(vb, "still_allowed", lambda device_id: True)
    up = upstream or _Q()
    t = threading.Thread(target=vb.run_session, args=(ws, {"id": dev_id}, "conv-1"),
                         kwargs={"connect": connect or (lambda url: up)}, daemon=True)
    t.start()
    return t, up


def _texts(q):
    return [json.loads(x) for x in q.sent if isinstance(x, str)]


def test_a_session_opens_with_the_engine_pipes_audio_both_ways_and_closes_on_stop(monkeypatch):
    ws = _Q()
    t, up = _run(ws, monkeypatch=monkeypatch)
    ws.inbox.put(b"\x00\x01" * 160)
    up.inbox.put(json.dumps({"type": "audio", "data": base64.b64encode(b"\x05\x00" * 8).decode()}))
    for _ in range(100):                                         # the PC's audio is through before the stop
        if b"\x05\x00" * 8 in ws.sent:
            break
        threading.Event().wait(0.02)
    ws.inbox.put(json.dumps({"type": "barge_in"}))
    ws.inbox.put(json.dumps({"type": "stop"}))
    t.join(5)
    assert not t.is_alive()
    assert _texts(ws)[0]["type"] == "engine" and b"\x05\x00" * 8 in ws.sent
    assert _texts(ws)[-1] == {"type": "closed", "reason": "Ended from the device.", "code": "stopped"}
    ups = [json.loads(x) for x in up.sent]
    assert [u["type"] for u in ups][:2] == ["audio", "barge"] and ups[-1]["type"] == "end" and up.closed


def test_the_upstream_url_is_the_engines_own_session_route_on_this_pc(monkeypatch):
    from agent_friday import core
    monkeypatch.setattr(core, "SERVER_PORT", 3999)
    assert vb.upstream_url(LOCAL, "c1") == "ws://127.0.0.1:3999/ws/voice-local?conversation_id=c1"
    assert vb.upstream_url(GEMINI) == "ws://127.0.0.1:3999/ws/live"
    with pytest.raises(vb.VoiceRefused):
        vb.upstream_url(DEMO)
    with pytest.raises(vb.VoiceRefused):
        vb.upstream_url({"ws_url": "/api/settings"})


def test_no_engine_is_an_error_and_a_closed_session_not_a_hang(monkeypatch):
    ws = _Q()
    t, _ = _run(ws, info=DEMO, monkeypatch=monkeypatch)
    t.join(5)
    texts = _texts(ws)
    assert texts[0]["type"] == "error" and texts[0]["code"] == "no_engine" and texts[0]["words"]
    assert texts[-1]["type"] == "closed" and texts[-1]["code"] == "no_engine"


def test_a_pc_session_that_cannot_start_is_told_to_the_device(monkeypatch):
    ws = _Q()

    def boom(url):
        raise OSError("refused")
    t, _ = _run(ws, connect=boom, monkeypatch=monkeypatch)
    t.join(5)
    texts = _texts(ws)
    assert texts[0]["code"] == "start_failed" and texts[-1]["code"] == "start_failed"


def test_the_pc_ending_its_session_closes_the_device_with_a_reason(monkeypatch):
    class _Dead(_Q):
        def receive(self, timeout=None):
            raise OSError("closed")
    ws = _Q()
    t, _ = _run(ws, upstream=_Dead(), monkeypatch=monkeypatch)
    t.join(5)
    assert not t.is_alive() and _texts(ws)[-1]["code"] == "upstream_ended"


def test_closed_is_the_last_frame_even_while_the_pc_is_still_sending_audio(monkeypatch):
    class _Flood(_Q):
        def receive(self, timeout=None):
            threading.Event().wait(0.001)
            return json.dumps({"type": "audio", "data": base64.b64encode(b"\x05\x00" * 8).decode()})
    ws = _Q()
    t, _ = _run(ws, upstream=_Flood(), monkeypatch=monkeypatch)
    threading.Event().wait(0.2)
    ws.inbox.put(json.dumps({"type": "stop"}))
    t.join(5)
    threading.Event().wait(0.3)                                  # anything the pump could still send has been sent
    assert not t.is_alive()
    assert isinstance(ws.sent[-1], str) and json.loads(ws.sent[-1])["type"] == "closed"


def test_the_hard_limit_closes_a_session_the_device_keeps_talking_in(monkeypatch):
    monkeypatch.setattr(vb, "MAX_SESSION_S", 0.5)
    ws = _Q()
    t, _ = _run(ws, monkeypatch=monkeypatch)
    stop_at = threading.Event()

    def talk():
        while not stop_at.is_set():
            ws.inbox.put(b"\x01\x00" * 160)
            stop_at.wait(0.05)
    threading.Thread(target=talk, daemon=True).start()
    t.join(4)
    stop_at.set()
    assert not t.is_alive(), "a device that keeps sending must not outlive the hard limit"
    assert _texts(ws)[-1]["code"] == "time_limit"


def test_a_device_revoked_mid_session_is_closed_with_revoked(monkeypatch):
    allowed = [True]
    monkeypatch.setattr(vb, "still_allowed", lambda device_id: allowed[0])
    monkeypatch.setattr(vb, "RECHECK_S", 0.05)
    ws = _Q()
    t, up = _run(ws, monkeypatch=monkeypatch, allowed=None)
    ws.inbox.put(b"\x01\x00" * 160)
    threading.Event().wait(0.2)
    assert t.is_alive()
    allowed[0] = False
    for _ in range(20):
        ws.inbox.put(b"\x01\x00" * 160)                       # still talking: the re-check is per frame too
        threading.Event().wait(0.02)
    t.join(4)
    assert not t.is_alive() and _texts(ws)[-1]["code"] == "revoked"
    assert json.loads(up.sent[-1])["type"] == "end" and up.closed


def test_still_allowed_reads_the_pairing_store_each_time(monkeypatch):
    from agent_friday.services import device_pairing
    store = {"p1": {"id": "p1", "kind": "phone"}}
    monkeypatch.setattr(device_pairing, "get_device", lambda i: store.get(i))
    from agent_friday.tv_gateway import owner_turn
    monkeypatch.setattr(owner_turn, "is_owner_device", lambda d: bool(d))
    assert vb.still_allowed("p1") is True
    store.clear()                                                # unpaired / revoked
    assert vb.still_allowed("p1") is False
    store["p1"] = {"id": "p1", "kind": "watch"}
    assert vb.still_allowed("p1") is False


def test_a_device_that_drops_while_being_answered_is_device_gone(monkeypatch):
    from simple_websocket import ConnectionClosed

    class _Gone(_Q):
        def send(self, raw):
            if isinstance(raw, str) and json.loads(raw).get("type") == "pong":
                raise ConnectionClosed()
            super().send(raw)
    ws = _Gone()
    t, _ = _run(ws, monkeypatch=monkeypatch)
    ws.inbox.put(json.dumps({"type": "ping"}))
    t.join(5)
    assert not t.is_alive() and _texts(ws)[-1]["code"] == "device_gone"


def test_a_failure_mid_session_is_not_reported_as_a_failed_start(monkeypatch):
    class _Broken(_Q):
        def send(self, raw):
            raise OSError("pipe")
    ws = _Q()
    t, _ = _run(ws, upstream=_Broken(), monkeypatch=monkeypatch)
    ws.inbox.put(b"\x01\x00" * 160)
    t.join(5)
    codes = [f.get("code") for f in _texts(ws)]
    assert "start_failed" not in codes and codes[-1] == "upstream_ended"


def test_the_socket_layer_refuses_an_oversized_frame_before_buffering_it():
    app = gateway.create_app()
    assert app.config["SOCK_SERVER_OPTIONS"]["max_message_size"] == vb.MAX_PCM_FRAME


def test_a_second_session_for_the_same_device_ends_the_first_with_replaced(monkeypatch):
    ws1, ws2 = _Q(), _Q()
    t1, up1 = _run(ws1, dev_id="same", monkeypatch=monkeypatch)
    for _ in range(100):
        if "same" in vb._ACTIVE:
            break
        threading.Event().wait(0.02)
    t2, up2 = _run(ws2, dev_id="same", monkeypatch=monkeypatch)
    t1.join(5)
    assert not t1.is_alive() and up1.closed
    assert _texts(ws1)[-1]["code"] == "replaced"
    ws2.inbox.put(json.dumps({"type": "stop"}))
    t2.join(5)


def test_an_idle_device_is_closed_after_the_idle_limit(monkeypatch):
    monkeypatch.setattr(vb, "IDLE_S", 0.2)
    ws = _Q()
    t, _ = _run(ws, monkeypatch=monkeypatch)
    t.join(5)
    assert not t.is_alive() and _texts(ws)[-1]["code"] == "idle"


# ── the whole path over real sockets ─────────────────────────────────────────

def _serve(app):
    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_a_paired_phone_talks_to_the_pcs_session_over_real_sockets(monkeypatch):
    """A stand-in for the desktop's /ws/voice-local (same frames), the real gateway app, a real device session
    token, and a WebSocket client: the engine badge arrives, binary audio goes up and comes back as binary, a
    release closes the turn, barge-in reaches the PC, and the turn's end carries the engine."""
    import time
    from flask import Flask
    from flask_sock import Sock
    from simple_websocket import Client
    from agent_friday import core

    seen = []
    desktop = Flask("fake_desktop")
    dsock = Sock(desktop)

    @dsock.route("/ws/voice-local")
    def fake_voice(ws):
        from flask import request
        seen.append(request.args.get("conversation_id"))
        ws.send(json.dumps({"type": "served_by", "ear": "faster-whisper@cuda", "mind": "Ternary Bonsai 1.7B@gpu",
                            "mouth": "kokoro@cuda"}))
        ws.send(json.dumps({"type": "manifest", "mode": "local"}))
        silent = 0
        while True:
            raw = ws.receive(timeout=5)
            if raw is None:
                return
            f = json.loads(raw)
            seen.append(f["type"])
            if f["type"] == "audio" and set(base64.b64decode(f["data"])) == {0}:
                silent += 1
                if silent == vb.RELEASE_SILENCE_FRAMES:      # the endpointer closed the turn
                    ws.send(json.dumps({"type": "input_transcript", "text": "hello"}))
                    ws.send(json.dumps({"type": "audio", "data": base64.b64encode(b"\x07\x00" * 10).decode()}))
                    ws.send(json.dumps({"type": "voice_turn_done", "user_text": "hello", "agent_text": "Hi."}))
            if f["type"] == "barge":
                ws.send(json.dumps({"type": "interrupted"}))
            if f["type"] == "end":
                return

    d_srv = _serve(desktop)
    monkeypatch.setattr(core, "SERVER_PORT", d_srv.server_port)
    monkeypatch.setattr(vb, "resolve_engine", lambda: LOCAL)
    gw_app = gateway.create_app()
    client = gw_app.test_client()
    dev = paired("phone", "stephen")
    token = open_session(client, dev)
    g_srv = _serve(gw_app)
    try:
        ws = Client.connect("ws://127.0.0.1:%d%s?conversation_id=conv-1" % (g_srv.server_port, vb.SESSION_PATH),
                            headers={"Authorization": gateway.AUTH_SCHEME + token})
        got = []

        def recv_until(pred):
            while not any(pred(g) for g in got):
                raw = ws.receive(timeout=5)
                assert raw is not None, got
                got.append(raw if isinstance(raw, bytes) else json.loads(raw))

        is_t = lambda t: (lambda g: isinstance(g, dict) and g["type"] == t)
        recv_until(is_t("engine"))
        ws.send(json.dumps({"type": "press"}))
        ws.send(b"\x01\x02" * 800)
        t0 = time.monotonic()
        ws.send(json.dumps({"type": "release"}))
        recv_until(lambda g: isinstance(g, bytes))
        first_audio_s = time.monotonic() - t0
        recv_until(is_t("turn_done"))
        ws.send(json.dumps({"type": "barge_in"}))
        after_done = len(got)
        recv_until(lambda g: any(isinstance(x, dict) and x["type"] == "speaking" and x["who"] == "you"
                                 for x in got[after_done:]))
        ws.send(json.dumps({"type": "stop"}))
        recv_until(is_t("closed"))
        dicts = [g for g in got if isinstance(g, dict)]
        assert all(d["type"] not in ("manifest", "served_by") for d in dicts)
        badge = next(d for d in dicts if d["type"] == "engine" and d.get("mouth"))
        assert badge["local"] is True and badge["where"] == "local" and badge["ear"] == "faster-whisper@cuda"
        assert b"\x07\x00" * 10 in got
        done = next(d for d in dicts if d["type"] == "turn_done")
        assert done["engine"]["where"] == "local"
        assert {"type": "transcript", "who": "friday", "text": "Hi.", "final": True} in dicts
        assert dicts[-1] == {"type": "closed", "reason": "Ended from the device.", "code": "stopped"}
        assert seen[0] == "conv-1" and "audio" in seen and "barge" in seen and seen[-1] == "end"
        assert first_audio_s < 1.0, "the bridge itself must add well under the desktop's 0.8 s bar"
    finally:
        g_srv.shutdown()
        d_srv.shutdown()
