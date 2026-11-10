"""/ws/openai-live: the bridge between the page and OpenAI Realtime (S2). A fake page socket and a fake
OpenAI socket stand in; nothing connects anywhere and no key is used."""
from __future__ import annotations

import base64
import importlib
import importlib.util
import json
import queue
import threading
import time

import pytest

MOD = "agent_friday.routes.openai_live"


class _Mod:
    """The module, imported on first use so a missing file fails the test on an assertion; sets go through."""

    def _real(self):
        assert importlib.util.find_spec(MOD) is not None, "routes/openai_live.py does not exist"
        return importlib.import_module(MOD)

    def __getattr__(self, name):
        return getattr(self._real(), name)

    def __setattr__(self, name, value):
        setattr(self._real(), name, value)


@pytest.fixture
def ol(monkeypatch):
    m = _Mod()
    from agent_friday.services import openai_realtime as orx
    monkeypatch.setattr(orx, "key_state", lambda: "connected")
    monkeypatch.setattr(orx, "api_key", lambda: "sk-test")
    m._BLOCKED_UNTIL[0] = 0.0
    monkeypatch.setattr(m, "month_spent_usd", lambda: 0.0)
    monkeypatch.setattr(m, "_gemini_ready", lambda s: True)
    monkeypatch.setattr(m, "_local_ready", lambda: True)
    monkeypatch.setattr(importlib.import_module("agent_friday.services.spend_guard"), "check",
                        lambda *a, **k: None)
    yield m
    m._BLOCKED_UNTIL[0] = 0.0


class FakeUp:
    def __init__(self):
        self.sent, self.inbox, self.closed = [], queue.Queue(), False

    def send(self, data):
        self.sent.append(json.loads(data))

    def receive(self, timeout=None):
        try:
            return self.inbox.get(timeout=min(timeout or 0.05, 0.05))
        except queue.Empty:
            return None

    def close(self, *a, **k):
        self.closed = True

    def types(self):
        return [m["type"] for m in self.sent]


def _bridge(ol, **kw):
    up, out = FakeUp(), []
    b = ol.Bridge(up, lambda o: out.append(o) or True, kw.pop("settings", {}), **kw)
    return b, up, out


def test_a_missing_key_is_said_not_hidden(ol, monkeypatch):
    from agent_friday.services import openai_realtime as orx
    monkeypatch.setattr(orx, "key_state", lambda: "missing")
    no = ol.refusal({})
    assert no["error"] == "no_key" and no["detail"] == "Add an OpenAI key to use this."
    assert no["offer"]["engine"] == "gemini" and no["offer"]["automatic"] is False


def test_local_only_refuses_and_offers_no_cloud_voice(ol):
    no = ol.refusal({"model_routing": {"mode": "local_only"}})
    assert no["error"] == "local_only" and no["offer"]["engine"] == "local"


def test_the_owner_cap_refuses_before_a_session_starts(ol, monkeypatch):
    monkeypatch.setattr(ol, "month_spent_usd", lambda: 4.95)
    assert ol.refusal({"voice_openai_monthly_cap_usd": 5})["error"] == "cap_reached"
    assert ol.refusal({"voice_openai_monthly_cap_usd": 50}) is None


def test_serve_sends_the_refusal_as_one_error_frame_and_connects_nowhere(ol, monkeypatch):
    from agent_friday.services import openai_realtime as orx
    monkeypatch.setattr(orx, "key_state", lambda: "missing")
    frames = []

    class WS:
        def send(self, d):
            frames.append(json.loads(d))
    called = []
    ol.serve(WS(), {}, connect=lambda *a: called.append(a), settings={})
    assert [f["type"] for f in frames] == ["error"] and frames[0]["error"] == "no_key" and not called


def test_page_audio_is_resampled_to_24k_and_forwarded(ol):
    import array
    b, up, _ = _bridge(ol)
    pcm = array.array("h", [0, 1000, 2000, 3000, 4000, 5000]).tobytes()
    b.to_upstream({"type": "audio", "data": base64.b64encode(pcm).decode()})
    m = up.sent[0]
    assert m["type"] == "input_audio_buffer.append" and len(base64.b64decode(m["audio"])) == 9 * 2
    assert b.bytes_in == len(pcm)


def test_openai_audio_and_words_reach_the_page(ol):
    b, up, out = _bridge(ol)
    b.from_upstream(json.dumps({"type": "response.output_audio.delta", "delta": "AAAA", "item_id": "i1"}))
    b.from_upstream({"type": "response.output_audio_transcript.delta", "delta": "Hello"})
    b.from_upstream({"type": "conversation.item.input_audio_transcription.completed", "transcript": "hi there"})
    assert out == [{"type": "audio", "data": "AAAA"}, {"type": "text", "text": "Hello"},
                   {"type": "input_transcript", "text": "hi there"}]
    assert b.session["owner_text"] == "hi there"


def test_barge_in_flushes_the_page_cancels_and_truncates_at_what_was_heard(ol):
    t = [100.0]
    b, up, out = _bridge(ol, clock=lambda: t[0])
    one_second = base64.b64encode(b"\x00\x01" * 24000).decode()          # 1.0 s of 24 kHz PCM16
    b.from_upstream({"type": "response.output_audio.delta", "delta": one_second, "item_id": "item_5"})
    t[0] = 100.4                                                            # the owner spoke 0.4 s in
    b.from_upstream({"type": "input_audio_buffer.speech_started"})
    assert {"type": "interrupted"} in out
    assert up.types() == ["response.cancel", "conversation.item.truncate"]
    assert up.sent[1]["item_id"] == "item_5" and up.sent[1]["audio_end_ms"] == 400
    assert b.item_id is None


def test_a_tool_call_runs_through_the_one_tool_path_and_its_result_goes_back_gated(ol, monkeypatch):
    ran = []
    monkeypatch.setattr(ol, "_gate", lambda text, label, withheld: text.upper())
    b, up, out = _bridge(ol, run_tool=lambda name, args, send, session: ran.append((name, args)) or "rain at 4")
    b.from_upstream({"type": "response.done", "response": {"status": "completed", "output": [
        {"type": "function_call", "name": "search_web", "call_id": "c1", "arguments": "{\"q\": \"rain\"}"}]}})
    deadline = time.time() + 3
    while len(up.sent) < 2 and time.time() < deadline:
        time.sleep(0.01)
    assert ran == [("search_web", {"q": "rain"})]
    assert up.sent[0] == {"type": "conversation.item.create", "item": {
        "type": "function_call_output", "call_id": "c1", "output": "RAIN AT 4"}}
    assert up.sent[1] == {"type": "response.create"}
    assert {"type": "turn_end"} not in out                                # the answer is still to come


def test_a_failing_tool_says_so_and_the_call_goes_on(ol):
    def boom(*a):
        raise RuntimeError("x")
    b, up, _ = _bridge(ol, run_tool=boom)
    b._tools([{"call_id": "c2", "name": "search_web", "args": {}}])
    assert "search_web tool failed" in up.sent[0]["item"]["output"] and up.sent[1] == {"type": "response.create"}


def test_each_response_is_costed_recorded_and_shown_per_minute(ol, monkeypatch):
    rec = []
    from agent_friday.services import cost_meter
    monkeypatch.setattr(cost_meter, "record", lambda *a, **k: rec.append((a, k)))
    b, up, out = _bridge(ol)
    b.from_upstream({"type": "response.done", "response": {"status": "completed",
                     "usage": {"input_tokens": 1000, "output_tokens": 1000}}})
    cost = [o for o in out if o["type"] == "cost"][0]
    assert cost["usd"] == pytest.approx(0.096) and cost["per_minute"] == "about $0.10 a minute"
    assert rec[0][0][:2] == ("openai", "gpt-realtime-2.1") and rec[0][1]["purpose"] == "voice.openai"
    assert rec[0][1]["cost_usd"] == pytest.approx(0.096)
    assert {"type": "turn_end"} in out


def test_reaching_the_cap_mid_call_ends_it_with_a_sentence(ol, monkeypatch):
    from agent_friday.services import cost_meter
    monkeypatch.setattr(cost_meter, "record", lambda *a, **k: None)
    monkeypatch.setattr(ol, "month_spent_usd", lambda: 5.0)
    b, up, out = _bridge(ol, settings={"voice_openai_monthly_cap_usd": 5})
    b.from_upstream({"type": "response.done", "response": {"usage": {"input_tokens": 10, "output_tokens": 10}}})
    err = [o for o in out if o["type"] == "error"][0]
    assert err["error"] == "cap_reached" and "$5.00" in err["detail"] and b.done.is_set()


def test_out_of_credit_blocks_openai_ends_the_call_and_offers_the_next_voice(ol):
    b, up, out = _bridge(ol, clock=lambda: 1000.0)
    b.from_upstream({"type": "error", "error": {"code": "insufficient_quota", "message": "quota"}})
    err = [o for o in out if o["type"] == "error"][0]
    assert err["error"] == "no_credit" and err["offer"]["engine"] == "gemini" and err["offer"]["automatic"] is False
    assert b.done.is_set() and ol._BLOCKED_UNTIL[0] == 1000.0 + 3600
    assert ol.refusal({}, now=1500.0)["error"] == "no_credit"


def test_a_rate_limit_is_an_error_frame_but_does_not_end_the_call_or_block_openai(ol):
    b, up, out = _bridge(ol)
    b.from_upstream({"type": "error", "error": {"type": "rate_limit_exceeded", "message": "slow down"}})
    assert [o["error"] for o in out if o["type"] == "error"] == ["openai_error"]
    assert not b.done.is_set() and ol._BLOCKED_UNTIL[0] == 0.0


def test_thirty_seconds_of_silence_ends_the_session(ol):
    t = [0.0]
    b, _, _ = _bridge(ol, clock=lambda: t[0])
    assert not b.silent_too_long()
    t[0] = 31.0
    assert b.silent_too_long()
    b.from_upstream({"type": "conversation.item.input_audio_transcription.completed", "transcript": "x"})
    t[0] = 31.0
    assert not b.silent_too_long()


def test_a_whole_session_sets_up_names_the_model_and_logs_the_mic(ol, monkeypatch):
    up = FakeUp()
    ledger = []
    monkeypatch.setattr(ol, "_mic_ledger", lambda ev, n: ledger.append((ev, n)))
    monkeypatch.setattr(ol, "build_instruction", lambda s: "You are Friday.")
    monkeypatch.setattr(ol, "build_tools", lambda: [])
    frames = []
    inbox = [json.dumps({"type": "end"})]

    class WS:
        def send(self, d):
            frames.append(json.loads(d))

        def receive(self, timeout=None):
            return inbox.pop(0) if inbox else None
    ol.serve(WS(), {}, connect=lambda url, headers: (
        (_ for _ in ()).throw(AssertionError("key in url")) if "sk-test" in url else
        (_ for _ in ()).throw(AssertionError("no bearer")) if headers != {"Authorization": "Bearer sk-test"} else up),
        settings={})
    assert up.sent[0]["type"] == "session.update" and up.sent[0]["session"]["instructions"] == "You are Friday."
    status = frames[0]
    assert status["type"] == "status" and status["engine"] == "openai" and "gpt-realtime-2.1" in status["text"]
    assert ledger == [("open", 0), ("close", 0)] and up.closed


def test_the_engine_resolver_reports_why_openai_cannot_answer_and_never_picks_gemini(monkeypatch):
    from agent_friday.routes import voice as V
    from agent_friday.services import openai_realtime as orx
    monkeypatch.setattr(orx, "key_state", lambda: "missing")
    info = V._resolve_voice_engine({"voice_engine": "openai"})
    assert info["engine"] != "gemini" and info["reason"] == "Add an OpenAI key to use this."
    assert info["openai_refused"]["error"] == "no_key"
    monkeypatch.setattr(orx, "key_state", lambda: "connected")
    ok = V._resolve_voice_engine({"voice_engine": "openai"})
    assert ok["engine"] == "openai" and ok["ws_url"] == "/ws/openai-live" and "gpt-realtime-2.1" in ok["label"]
    assert ok["per_minute"] == "about $0.10 a minute"
