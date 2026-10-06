"""Real synthesis options, semantic phrase boundaries and thread-local continuity."""
import types

import pytest

from agent_friday.services import voice_delivery as delivery


def test_slow_down_reaches_native_piper_without_retiming(monkeypatch):
    from agent_friday.services.local_voice import PiperTTS
    observed = []
    class Native:
        config = types.SimpleNamespace(sample_rate=24000)
        def synthesize_stream_raw(self, text, length_scale=1.0):
            observed.append(length_scale)
            yield b"\x01\x00" * 100
    engine = PiperTTS()
    engine._piper = Native()
    monkeypatch.setattr(delivery, "settings_snapshot", lambda: {})
    session = {"conversation_id": "conv-one"}
    override = delivery.update_session_preferences(session, "Slow down, please.")
    with delivery.using_preferences(override):
        pcm = engine.synthesize("This number is 123.45.")
    assert observed == [pytest.approx(1 / 0.9)]
    assert pcm[:200] == b"\x01\x00" * 100
    assert len(pcm) > 200  # real PCM pause after the sentence, same sample rate
    assert delivery.current_overrides() == {}


def test_pace_and_depth_are_scoped_to_call_and_conversation(monkeypatch):
    monkeypatch.setattr(delivery, "settings_snapshot", lambda: {})
    first = {"conversation_id": "conv-one"}
    delivery.update_session_preferences(first, "Talk it through.")
    delivery.update_session_preferences(first, "Speak faster.")
    assert delivery.session_preferences(first) == {"voice_response_depth": "detailed", "voice_speaking_pace": "brisk"}
    assert delivery.session_preferences(first, "conv-other") == {}
    assert delivery.session_preferences({"conversation_id": "conv-one"}) == {}
    result = delivery.preference_action({"action": "reset", "scope": "session"}, first)
    assert result["depth"] == result["pace"] == "adaptive"
    assert delivery.session_preferences(first) == {}
    delivery.update_session_preferences(first, 'The phrase "slow down" is in the document.')
    assert delivery.session_preferences(first) == {}


def test_saved_defaults_apply_to_next_call_and_session_reset_restores_call_snapshot(monkeypatch):
    saved = {"voice_speaking_pace": "natural", "voice_response_depth": "detailed"}
    first = {"conversation_id": "conv-one"}
    delivery.initialize_session_preferences(first, saved)
    saved.update(voice_speaking_pace="brisk", voice_response_depth="concise")
    # Reusing a call never replaces its initial defaults with a settings edit.
    delivery.initialize_session_preferences(first, saved)
    second = {"conversation_id": "conv-one"}
    delivery.initialize_session_preferences(second, saved)
    monkeypatch.setattr(delivery, "settings_snapshot", lambda: pytest.fail("call reloaded global settings"))
    with delivery.using_preferences(delivery.session_preferences(first)):
        assert delivery.synthesis_plan("One idea.")["speed"] == 1.0
        assert delivery.preferences()["depth"] == "detailed"
    with delivery.using_preferences(delivery.session_preferences(second)):
        assert delivery.synthesis_plan("One idea.")["speed"] == 1.08
        assert delivery.preferences()["depth"] == "concise"
    delivery.update_session_preferences(first, "Slow down.")
    with delivery.using_preferences(delivery.session_preferences(first)):
        assert delivery.synthesis_plan("One idea.")["speed"] == .9
    reset = delivery.preference_action({"scope": "session", "action": "reset"}, first)
    assert reset["pace"] == "natural" and reset["depth"] == "detailed"
    assert delivery.session_preferences(first, "conv-other") == {}


def test_cloud_chat_switch_finishes_under_old_owner_and_discards_call_context():
    session = {"conversation_id": "conv-one", "delivery_preferences": {
        "conv-one": {"voice_speaking_pace": "measured"}}, "owner_text": "Old instruction",
        "conv_state": {"topic": "Old topic"}, "spoken": ["Old answer"]}
    incoming, outgoing, injected, resume = ["Old question"], ["Old answer"], ["Old result"], ["Old handle"]
    events = []
    def flush(cid):
        events.append(("persist", cid, incoming[:], outgoing[:]))
    def clear_resume():
        resume.clear()
        events.append(("clear_resume",))
    def end():
        events.append(("end",))
    options = dict(flush_turn=flush, pending=(incoming, outgoing, injected), clear_resume=clear_resume, end_call=end)
    assert not delivery.end_live_on_conversation_change(session, "conv-one", **options)
    assert not events
    assert delivery.end_live_on_conversation_change(session, "conv-two", **options)
    assert events == [("persist", "conv-one", ["Old question"], ["Old answer"]), ("clear_resume",), ("end",)]
    assert session["conversation_id"] == "conv-one"
    assert not incoming and not outgoing and not injected and not resume
    assert not session["delivery_preferences"] and not session["conv_state"] and not session["spoken"]


def test_cloud_chat_switch_closes_even_when_old_turn_cannot_be_saved():
    session = {"conversation_id": "conv-one"}
    pending, events = ["Old result"], []
    def failed_flush(cid):
        raise OSError("synthetic save failure")
    with pytest.raises(OSError):
        delivery.end_live_on_conversation_change(session, "conv-two", flush_turn=failed_flush,
            pending=(pending,), clear_resume=lambda: events.append("clear"), end_call=lambda: events.append("end"))
    assert not pending and events == ["clear", "end"]


def test_local_chat_switch_discards_microphone_and_queued_results_without_retargeting():
    from unittest.mock import patch
    from agent_friday.services.voice_session import VoiceSession
    frames, generated, retargeted = [], [], []
    class Vad:
        _buf = bytearray(b"old microphone bytes")
        def reset(self):
            self._buf.clear()
    preferences = {"conversation_id": "conv-one", "delivery_preferences": {
        "conv-one": {"voice_speaking_pace": "measured"}}}
    # This boundary test exercises no microphone, model or worker thread.
    with patch("agent_friday.services.voice_session.threading.Thread"):
        session = VoiceSession(frames.append, ear=object(), mouth=object(), vad=Vad(),
            generate=lambda *args: generated.append(args), hooks={"retarget": retargeted.append},
            delivery_session=preferences)
    try:
        session.conversation_id = "conv-one"
        session._hearing = True
        session._partials = ["Old unfinished instruction"]
        session.deliver("Old project result")
        assert not session._inject_q.empty()
        session.handle({"type": "conversation", "id": "conv-two"})
        assert session.done.is_set() and session.conversation_id == "conv-one"
        assert not session.vad._buf and not session._partials and session._inject_q.empty()
        assert not preferences["delivery_preferences"] and not retargeted
        session.deliver("Another late result")
        session.handle({"type": "text", "text": "Words in a different chat"})
        session.run_turn("A turn already waiting for the old call")
        assert not generated and session._inject_q.empty()
        assert any(frame.get("type") == "interrupted" for frame in frames)
        assert any("changed chats" in frame.get("error", "") for frame in frames)
    finally:
        session.close()
        session._speaker.join(1.5)


def test_explicit_owner_budget_is_respected_and_explanations_get_room():
    from agent_friday.routes.voice import _voice_reply_cap
    assert _voice_reply_cap({"voice_max_tokens": 5000}) == 5000
    assert _voice_reply_cap({"voice_max_tokens": 120}, "Explain the tradeoffs") == 120
    assert _voice_reply_cap({}, "How would these systems work together?") > _voice_reply_cap({}, "What time is it?")
    assert _voice_reply_cap({}, "Briefly explain the tradeoffs") == 400


def test_default_chunker_preserves_a_natural_sentence():
    from agent_friday.services.voice_session import ClauseChunker
    sentence = "The reason this matters becomes clear when we compare the two approaches, because each solves a different problem."
    chunker = ClauseChunker()
    assert chunker.feed(sentence + " Next") == [sentence]
    assert chunker.flush() == "Next"
    assert ClauseChunker().feed("One idea.\n\nA new idea") == ["One idea.\n\n"]


def test_native_plan_slows_dense_detail_without_fixed_word_chunks(monkeypatch):
    monkeypatch.setattr(delivery, "settings_snapshot", lambda: pytest.fail("synthesis reloaded settings"))
    assert delivery.synthesis_plan("Ordinary standalone speech.")["speed"] == .98
    regular = delivery.synthesis_plan("That sounds reasonable.", {})
    dense = delivery.synthesis_plan("The estimate is 18.25 percent, up from 12.75 percent.", {})
    assert dense["speed"] < regular["speed"]
    assert dense["pause_ms"] > regular["pause_ms"]
    assert delivery.synthesis_plan("an unfinished phrase", {})["pause_ms"] == 0
    assert delivery.synthesis_plan("123 456.", {"voice_speaking_pace": "brisk"})["speed"] == 1.08


def test_kokoro_receives_native_speed_even_on_its_generation_thread(monkeypatch):
    from agent_friday.services import kokoro_voice
    np = pytest.importorskip("numpy")
    observed = []
    def pipeline(text, voice=None, speed=1.0):
        observed.append(speed)
        yield "hello", "hello", np.ones(100, dtype="float32") * 0.1
    engine = kokoro_voice.KokoroTTS()
    engine._pipeline = pipeline
    engine._device = "fake"
    monkeypatch.setattr(delivery, "settings_snapshot", lambda: {})
    with delivery.using_preferences({"voice_speaking_pace": "measured"}):
        pcm = engine.synthesize("Hello.")
    assert observed == [0.9]
    assert len(pcm) > 200


def test_front_honors_explicit_budget_within_actual_context_and_continues_default(monkeypatch):
    from agent_friday.services import voice_front, model_router
    seat = voice_front.FrontSeat()
    requests = []
    monkeypatch.setattr(seat, "_post", lambda body, **kw: requests.append(body) or types.SimpleNamespace(raise_for_status=lambda: None))
    replies = iter([
        {"choices": [{"message": {"content": "The first reason is"}, "finish_reason": "length"}]},
        {"choices": [{"message": {"content": "that context matters."}, "finish_reason": "stop"}]},
    ])
    monkeypatch.setattr(model_router, "_consume_sse_completion", lambda *a, **kw: next(replies))
    monkeypatch.setattr(model_router, "turn_cancelled", lambda: False)
    result = seat.run_turn("Synthetic identity", [{"role": "user", "content": "Explain."}], {}, max_tokens=1400, allow_continuation=True)
    assert result == "The first reason is that context matters."
    assert len(requests) == 2 and requests[0]["max_tokens"] == 1400
    assert requests[1]["messages"][-1]["content"].startswith("Continue the unfinished thought")
    requests.clear()
    monkeypatch.setattr(model_router, "_consume_sse_completion", lambda *a, **kw: {"choices": [{"message": {"content": "Short."}, "finish_reason": "length"}]})
    seat.run_turn("Synthetic identity", [{"role": "user", "content": "Explain."}], {}, max_tokens=99999)
    assert len(requests) == 1
    window = voice_front.FRONT_MODELS[voice_front.DEFAULT_FRONT_MODEL]["ctx"]
    assert 2048 < requests[0]["max_tokens"] < window


def test_local_thread_context_carries_actual_correction_and_not_another_chat(monkeypatch):
    from agent_friday.services import conversations
    from agent_friday.routes import voice
    seen = []
    monkeypatch.setattr(conversations, "load", lambda cid: {"id": cid})
    def messages(cid, limit=None):
        seen.append(cid)
        return [{"role": "user", "text": "Please use the revised estimate, 14 units."},
                {"role": "friday", "text": "I had used 12; I will use 14."}]
    monkeypatch.setattr(conversations, "messages", messages)
    result = voice._local_voice_messages("conv-one", "Explain the implications.", {}, volatile="")
    assert seen == ["conv-one"]
    assert [item["role"] for item in result] == ["user", "assistant", "user"]
    assert result[0]["content"].endswith("14 units.")
    assert sum(item["content"].count("Explain the implications.") for item in result) == 1


def test_history_budget_does_not_replace_recent_long_turn_with_older_context(monkeypatch):
    from agent_friday.services import conversations
    monkeypatch.setattr(conversations, "load", lambda cid: {"id": cid})
    monkeypatch.setattr(conversations, "messages", lambda cid, limit=None: [
        {"role": "user", "text": "Unrelated older subject"},
        {"role": "user", "text": "Recent correction " * 100}])
    assert delivery.local_history("conv-one", max_chars=100) == []


def test_saved_preferences_use_existing_store_and_off_record_refuses(monkeypatch):
    import agent_friday.core as core
    saved = {}
    monkeypatch.setattr(core, "_save_settings", lambda delta: saved.update(delta))
    monkeypatch.setattr(delivery, "settings_snapshot", lambda: saved)
    with pytest.raises(ValueError, match="active voice call"):
        delivery.preference_action({"action": "set", "pace": "brisk"})
    assert saved == {}
    result = delivery.preference_action({"action": "set", "scope": "default", "pace": "measured", "depth": "detailed"})
    assert result["pace"] == "measured"
    assert saved == {"voice_speaking_pace": "measured", "voice_response_depth": "detailed"}
    saved["off_record"] = True
    with pytest.raises(ValueError, match="off the record"):
        delivery.preference_action({"action": "set", "scope": "default", "pace": "brisk"})
    assert saved["voice_speaking_pace"] == "measured"


def test_persona_and_delivery_preferences_reach_local_and_front_prompts(monkeypatch):
    from agent_friday.routes import voice
    from agent_friday.services import voice_context_digest
    monkeypatch.setattr(voice, "_get_friday_system_prompt", lambda **kw: "Stable identity")
    monkeypatch.setattr(voice, "_get_voice_style_prompt", lambda: "Dry humor and thoughtful disagreement.")
    monkeypatch.setattr(voice_context_digest, "build", lambda settings=None: "Known context")
    settings = {"voice_response_depth": "detailed", "voice_speaking_pace": "measured"}
    local, _ = voice._build_voice_system_prompt(settings, description="", seat="synthetic-seat")
    front = voice._build_front_system_prompt(settings, contract={"names": []})
    for prompt in (local, front):
        assert "Prefer developed explanations" in prompt
        assert "measured speaking pace" in prompt
        assert "revise it when" in prompt
        assert "Dry humor and thoughtful disagreement." in prompt
