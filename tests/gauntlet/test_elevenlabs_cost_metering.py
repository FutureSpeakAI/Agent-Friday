"""Gauntlet finding Q11 part (a): ElevenLabs TTS
(services/elevenlabs_tools.py `_tool_speak_text`) makes a real billed API
call -- its own tool description even says "Costs characters against the
ElevenLabs quota" -- yet had zero cost_meter references and no PRICING
entries for its voice models (docs/audits/gauntlet-2026-09-03/
findings.jsonl, Q11).

`_tool_speak_text` is a plain module-level function, so this is a real
behavioral test: monkeypatch the HTTP layer (`_request`) to return a
scripted 2xx MP3-shaped response, call the real tool function, and assert
`cost_meter.record()` was invoked with the real character count and model
id -- not a source-text pin.

Also covers the FRIDAY_TESTING short-circuit that would otherwise make
this test vacuously pass by never reaching the real code path at all
(the "evidence didn't travel" class of mistake this audit already caught
once, F35) -- explicitly patches `_tool_speak_text`'s own internal
`os.environ.get("FRIDAY_TESTING")` check out of the way for the duration
of the call under test.
"""
from __future__ import annotations

import os

import agent_friday.services.cost_meter as cost_meter
import agent_friday.services.elevenlabs_tools as et

# A minimal, valid-enough MP3 header so creative_store._looks_real() accepts
# it as real audio rather than rejecting the row before metering is reached.
_FAKE_MP3_BYTES = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" * 200


class _FakeResponse:
    def __init__(self, content):
        self.content = content
        self.status_code = 200


class TestElevenLabsCostMetering:
    def test_speak_text_records_character_count_and_model(self, monkeypatch, tmp_path):
        monkeypatch.setattr(et, "_api_key", lambda: "fake-elevenlabs-key")
        monkeypatch.setattr(et, "_settings", lambda: {})
        monkeypatch.setattr(et, "_dest_dir", lambda folder: (tmp_path, None))
        monkeypatch.setattr(
            et, "_request",
            lambda method, path, key=None, json_body=None, stream=False:
                (_FakeResponse(_FAKE_MP3_BYTES), None))
        # This tool's own FRIDAY_TESTING short-circuit sits AFTER the egress
        # gate on purpose (security-boundary.md) but BEFORE the network call
        # this test wants to actually exercise -- bypass just that guard, not
        # the surrounding security gate above it.
        monkeypatch.delenv("FRIDAY_TESTING", raising=False)

        recorded = []

        def _fake_record(provider, model, input_tokens=0, output_tokens=0, **kw):
            recorded.append((provider, model, input_tokens, output_tokens, kw))
            return 0.0

        monkeypatch.setattr(cost_meter, "record", _fake_record)

        text = "a harmless test sentence for text to speech metering"
        try:
            out = et._tool_speak_text({"text": text, "model_id": "eleven_multilingual_v2"})
        finally:
            os.environ["FRIDAY_TESTING"] = "1"

        assert "spoke" in out, f"tool call did not report success: {out}"
        assert len(recorded) == 1, (
            "cost_meter.record() was not called exactly once for a real, "
            "billed ElevenLabs speak_text call"
        )
        provider, model, in_tok, out_tok, kw = recorded[0]
        assert provider == "elevenlabs"
        assert model == "eleven_multilingual_v2"
        assert in_tok == len(text), (
            "must record the real character count sent to ElevenLabs "
            "(passed as input_tokens against a $/1K-characters PRICING row) "
            "-- a fabricated or zeroed count would misprice every row"
        )
        assert out_tok == 0
        assert kw.get("kind") == "voice"

    def test_pricing_table_has_the_default_model(self):
        assert et.DEFAULT_MODEL in cost_meter.PRICING, (
            f"the default ElevenLabs model id {et.DEFAULT_MODEL!r} has no "
            "PRICING entry -- ordinary speak_text calls with no explicit "
            "model_id would meter as $0"
        )
        rate = cost_meter.PRICING[et.DEFAULT_MODEL]
        assert rate["in"] > 0

    def test_a_failed_record_call_never_breaks_the_tool(self, monkeypatch, tmp_path):
        monkeypatch.setattr(et, "_api_key", lambda: "fake-elevenlabs-key")
        monkeypatch.setattr(et, "_settings", lambda: {})
        monkeypatch.setattr(et, "_dest_dir", lambda folder: (tmp_path, None))
        monkeypatch.setattr(
            et, "_request",
            lambda method, path, key=None, json_body=None, stream=False:
                (_FakeResponse(_FAKE_MP3_BYTES), None))
        monkeypatch.delenv("FRIDAY_TESTING", raising=False)

        def _boom(*a, **k):
            raise RuntimeError("cost_meter is down")

        monkeypatch.setattr(cost_meter, "record", _boom)

        try:
            out = et._tool_speak_text({"text": "another harmless sentence"})
        finally:
            os.environ["FRIDAY_TESTING"] = "1"
        assert "spoke" in out, f"a cost_meter failure broke the tool call: {out}"
