"""Gauntlet finding Q6 part (c) / cost-metering batch (2026-09-04): Gemini
TTS (services/voice_engine.py `_synthesize_tts_wav_gemini`) makes a real,
billed Gemini API call with ZERO cost_meter integration anywhere in the
file, despite `cost_meter.PRICING` already carrying entries for the
sibling Gemini Live models -- clear evidence the original intent was to
track these calls, just never wired for TTS specifically (docs/audits/
gauntlet-2026-09-03/findings.jsonl, Q6).

`_synthesize_tts_wav_gemini` is a plain module-level function (not a
closure with no call surface, unlike the websocket routes), so this is a
real behavioral test: monkeypatch `google.genai.Client` to a fake that
returns a scripted response carrying `usage_metadata`, call the real
function, and assert `cost_meter.meter()` was invoked with the actual
model id and the actual token counts from that response -- not a
source-text pin.
"""
from __future__ import annotations

import io

import agent_friday.core as core
import agent_friday.services.cost_meter as cost_meter
import agent_friday.services.voice_engine as voice_engine
import google.genai as genai_mod


class _FakeUsage:
    def __init__(self, prompt_tokens, candidate_tokens):
        self.prompt_token_count = prompt_tokens
        self.candidates_token_count = candidate_tokens


class _FakePart:
    def __init__(self, data):
        self.inline_data = type("Inline", (), {"data": data})()


class _FakeContent:
    def __init__(self, data):
        self.parts = [_FakePart(data)]


class _FakeCandidate:
    def __init__(self, data):
        self.content = _FakeContent(data)


class _FakeResponse:
    def __init__(self, data, prompt_tokens, candidate_tokens):
        self.candidates = [_FakeCandidate(data)]
        self.usage_metadata = _FakeUsage(prompt_tokens, candidate_tokens)


class _FakeModels:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class _FakeClient:
    last_instance = None

    def __init__(self, api_key=None):
        self.api_key = api_key
        self.models = _FakeModels(_FakeResponse(b"\x00\x01" * 100, 42, 137))
        _FakeClient.last_instance = self


class TestGeminiTtsCostMetering:
    def test_synthesize_tts_records_real_token_usage(self, monkeypatch):
        monkeypatch.setattr(core, "GEMINI_API_KEY", "fake-key-for-test")
        monkeypatch.setattr(genai_mod, "Client", _FakeClient)

        recorded = []

        def _fake_meter(provider, model, usage, **kw):
            recorded.append((provider, model, dict(usage), kw))
            return 0.0

        monkeypatch.setattr(cost_meter, "meter", _fake_meter)

        buf = voice_engine._synthesize_tts_wav_gemini(
            "hello, this is a harmless test sentence", voice="Aoede", style="plain")

        assert isinstance(buf, io.BytesIO)
        assert len(recorded) == 1, (
            "cost_meter.meter() was not called exactly once for a real Gemini "
            "TTS synthesis call -- this call is real and billed and must be "
            "metered"
        )
        provider, model, usage, kw = recorded[0]
        assert provider == "gemini"
        assert model == "gemini-2.5-flash-preview-tts", (
            "metered under a model id that doesn't match the exact wire id "
            "the real generate_content() call used -- would silently price "
            "against the wrong (or missing) PRICING row"
        )
        assert usage["input_tokens"] == 42, (
            "did not carry through the response's real prompt_token_count -- "
            "a fabricated/zeroed usage value would under-report real spend"
        )
        assert usage["output_tokens"] == 137, (
            "did not carry through the response's real candidates_token_count"
        )
        assert kw.get("kind") == "voice"

    def test_pricing_table_has_an_entry_for_the_real_tts_model_id(self):
        """The model id the code actually calls must have a real PRICING row
        -- metering that resolves to $0 via a missing-key fallback is exactly
        as invisible to the budget dashboard as never metering at all."""
        assert "gemini-2.5-flash-preview-tts" in cost_meter.PRICING
        rate = cost_meter.PRICING["gemini-2.5-flash-preview-tts"]
        assert rate["in"] > 0 and rate["out"] > 0

    def test_a_failed_meter_call_never_breaks_synthesis(self, monkeypatch):
        """cost_meter.meter() raising must not prevent the user from getting
        their audio back -- metering is instrumentation, not a gate."""
        monkeypatch.setattr(core, "GEMINI_API_KEY", "fake-key-for-test")
        monkeypatch.setattr(genai_mod, "Client", _FakeClient)

        def _boom(*a, **k):
            raise RuntimeError("cost_meter is down")

        monkeypatch.setattr(cost_meter, "meter", _boom)

        buf = voice_engine._synthesize_tts_wav_gemini(
            "another harmless test sentence", voice="Aoede", style="plain")
        assert isinstance(buf, io.BytesIO)
