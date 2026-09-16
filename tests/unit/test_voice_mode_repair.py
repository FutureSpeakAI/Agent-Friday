"""voice-mode-diagnosis-and-repair.md, unit half: F3 (context reach is said
from the resolved tool names), F4 (selected vs effective synthesizer, and a
tier peek with no side effects), F6 (the cloud SESSION writer on the voice
indicator carries the provider and the byte count).

F1/F2 (probe cache, warn-once, conservative admission) live in
test_nemo_voice.py next to the probe they pin.
"""
import logging

import pytest

from agent_friday.services import voice_indicator as vi
from agent_friday.services.local_voice import LocalVoiceEngine
from agent_friday.routes import voice as rv


# ── F3 ───────────────────────────────────────────────────────────────────────

def test_cloud_reach_names_what_it_cannot_reach():
    r = rv._voice_context_reach("gemini", ["check_email", "query_calendar",
                                           "search_web"])
    assert r["tool_capable"] is True
    assert r["tools"] == 3
    assert r["knowledge_graph"] is False
    assert r["memory"] is False
    assert r["full_context"] is False
    assert "knowledge graph" in r["notice"] and "memory" in r["notice"]
    assert "local voice" in r["notice"]


def test_cloud_reach_with_no_tools_says_so():
    r = rv._voice_context_reach("gemini", [])
    assert r["tool_capable"] is False
    assert "NO tools" in r["notice"]


def test_cloud_reach_is_quiet_when_kg_and_memory_are_in_the_table():
    r = rv._voice_context_reach("gemini", ["knowledge_query", "memory_recall"])
    assert r["full_context"] is True
    assert r["notice"] == ""


def test_local_reach_is_full_and_quiet():
    r = rv._voice_context_reach("local")
    assert r["full_context"] is True and r["notice"] == ""


def test_cloud_reach_reads_the_real_table_by_default():
    """The default path must consult the live declarations, not a comment.

    Flipped 2026-09-16 (voice-system-clean-sheet.md §4.5): the table now
    carries `ask_friday`, so the knowledge graph and memory ARE reachable --
    through the local model -- and the notice goes quiet in favour of the
    honesty line that names the relay."""
    r = rv._voice_context_reach("gemini")
    assert r["tools"] >= 10
    assert r["knowledge_graph"] is True and r["via_local"] is True
    assert "ask_friday" in r["line"]
    assert r["notice"] == ""


# ── F4 ───────────────────────────────────────────────────────────────────────

@pytest.fixture
def eng():
    return LocalVoiceEngine()


def test_effective_tts_piper_is_piper(eng):
    e = eng.effective_tts({"local_voice_tts_engine": "piper"})
    assert e == {"selected": "piper", "engine": "piper", "device": "cpu",
                 "will_refuse": False, "reason": ""}


def test_effective_tts_kokoro_serves_on_cuda_even_on_cpu_tier(eng, monkeypatch):
    """The spec's F4 said Piper serves when Kokoro is selected on the cpu
    tier. The code decides Kokoro's device from torch's CUDA view, not the
    NeMo tier, and friday.log shows 'kokoro load ... device=cuda' on
    2026-09-10. The effective block must report what actually happens."""
    import agent_friday.services.kokoro_voice as kv
    monkeypatch.setattr(kv, "kokoro_available", lambda: True)
    monkeypatch.setattr(kv, "kokoro_gpu_status",
                        lambda: {"cuda": True, "sufficient_for_kokoro": True})
    e = eng.effective_tts({"local_voice_tts_engine": "kokoro"})
    assert e["engine"] == "kokoro" and e["device"] == "cuda"
    assert e["will_refuse"] is False


def test_effective_tts_kokoro_refuses_without_cuda_and_says_so(eng, monkeypatch):
    import agent_friday.services.kokoro_voice as kv
    monkeypatch.setattr(kv, "kokoro_available", lambda: True)
    monkeypatch.setattr(kv, "kokoro_gpu_status",
                        lambda: {"cuda": False, "sufficient_for_kokoro": False})
    e = eng.effective_tts({"local_voice_tts_engine": "kokoro",
                           "local_voice_kokoro_allow_cpu": False})
    assert e["will_refuse"] is True and e["engine"] is None
    assert "Piper" in e["reason"]
    # Explicit CPU opt-in: it runs, slowly, and the reason says so.
    e2 = eng.effective_tts({"local_voice_tts_engine": "kokoro",
                            "local_voice_kokoro_allow_cpu": True})
    assert e2["engine"] == "kokoro" and e2["device"] == "cpu"
    assert "realtime" in e2["reason"]


def test_effective_tts_kokoro_not_importable_refuses(eng, monkeypatch):
    import agent_friday.services.kokoro_voice as kv
    monkeypatch.setattr(kv, "kokoro_available", lambda: False)
    e = eng.effective_tts({"local_voice_tts_engine": "kokoro"})
    assert e["will_refuse"] is True and "importable" in e["reason"]


def test_peek_tier_has_no_side_effects(eng, monkeypatch, caplog):
    """resolve_tier logs a degrade warning and records last_downgrade every
    call; health polls every 20 s, so health must use the silent peek."""
    monkeypatch.setattr(eng, "_gpu_tier_ready", lambda **_k: False)
    eng.last_downgrade = ""
    with caplog.at_level(logging.DEBUG, logger="friday.local_voice"):
        assert eng.peek_tier({"voice_engine": "local-gpu"}) == "cpu"
        assert eng.peek_tier({"voice_engine": "auto"}) == "cpu"
        assert eng.peek_tier({"voice_engine": "local"}) == "cpu"
    assert eng.last_downgrade == ""
    assert not [r for r in caplog.records if "degrade" in r.getMessage()]
    monkeypatch.setattr(eng, "_gpu_tier_ready", lambda **_k: True)
    assert eng.peek_tier({"voice_engine": "local-gpu"}) == "gpu"
    assert eng.peek_tier({"voice_engine": "local"}) == "cpu"


def test_health_carries_resolved_tier_and_effective_tts(eng, monkeypatch):
    monkeypatch.setattr(eng, "_settings",
                        lambda: {"voice_engine": "local",
                                 "local_voice_tts_engine": "piper"})
    h = eng.health()
    assert h["resolved_tier"] == "cpu"
    assert h["tier_reason"] == ""
    assert h["effective_tts"]["engine"] == "piper"


# ── F6 ───────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_indicator():
    vi.reset_for_tests()
    yield
    vi.reset_for_tests()


def test_cloud_session_open_names_provider_and_is_not_dismissible():
    vi.record_cloud_session(provider="google-gemini", label="Gemini Live",
                            event="open", model="gemini-live-x")
    s = vi.snapshot()
    assert s["provider"] == "google-gemini"
    assert s["is_cloud"] is True
    assert s["session_active"] is True
    assert s["dismissible"] is False
    assert s["mic_audio_bytes"] == 0


def test_cloud_session_close_accumulates_bytes_across_legs():
    vi.record_cloud_session(provider="google-gemini", label="Gemini Live",
                            event="start")
    vi.record_cloud_session(provider="google-gemini", label="Gemini Live",
                            event="open")
    vi.record_cloud_session(provider="google-gemini", label="Gemini Live",
                            event="close", bytes_sent=4_000_000)
    # A renewal leg on the same session keeps counting.
    vi.record_cloud_session(provider="google-gemini", label="Gemini Live",
                            event="open")
    vi.record_cloud_session(provider="google-gemini", label="Gemini Live",
                            event="close", bytes_sent=3_294_560)
    s = vi.snapshot()
    assert s["session_active"] is False
    assert s["mic_audio_bytes"] == 7_294_560
    assert s["mic_audio_provider"] == "google-gemini"
    # The NEXT browser session starts from zero.
    vi.record_cloud_session(provider="google-gemini", label="Gemini Live",
                            event="start")
    assert vi.snapshot()["mic_audio_bytes"] == 0


def test_cloud_session_writer_cannot_be_confused_with_an_interaction():
    """record_served is for priced interactions; the session writer must not
    invent a $0.00 line for a stream that cost_meter prices elsewhere."""
    vi.record_cloud_session(provider="google-gemini", label="Gemini Live",
                            event="open")
    s = vi.snapshot()
    assert s["interactions"] == []
    assert s["session_cost_usd"] == 0.0 and s["session_cost_exact"] is True
