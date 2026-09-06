"""Gauntlet finding Q6 part (c) / cost-metering batch (2026-09-04): the
Gemini Live voice websocket (routes/voice.py `ws_live`) makes real, billed
Gemini Live API calls with ZERO cost_meter integration anywhere in the
file, despite `cost_meter.PRICING` already carrying entries for exactly
these Live model ids -- clear evidence the original intent was to track
this, just never wired (docs/history/audits/gauntlet-2026-09-03/findings.jsonl,
Q6).

`ws_live` is a closure nested inside a Flask-Sock route registration
function, deeply inside an async Gemini Live streaming session -- not an
independently callable module-level function, and not something a test
can reach without mocking that whole session. The ORIGINAL version of
this probe (kept here as historical record of the correction) pinned the
inline block's source text instead -- real today, silently defeated
tomorrow by a rename or reformat with the underlying behavior completely
unaffected either way (weak-probe audit, 2026-09-05).

Fix: the metering logic itself (read usage_metadata, call cost_meter.
meter(), never raise) is now voice.py's own module-level
_meter_gemini_live_chunk(chunk, model_name) -- independently callable
with a lightweight fake chunk, no Gemini session or websocket required.
ws_live's receive loop calls it in one line. TestMeterGeminiLiveChunk
below is real behavioral coverage of the extracted function;
TestWsLiveCallsTheMeteringFunction is the much simpler structural check
the extraction leaves behind (one function call to find, not an inline
block's variable names and try/except structure).
"""
from __future__ import annotations

import inspect
import types

import agent_friday.routes.voice as vr


def _fake_chunk(prompt_tokens=None, response_tokens=None, usage_metadata=True):
    if not usage_metadata:
        return types.SimpleNamespace(usage_metadata=None)
    um = types.SimpleNamespace(
        prompt_token_count=prompt_tokens, response_token_count=response_tokens)
    return types.SimpleNamespace(usage_metadata=um)


class TestMeterGeminiLiveChunk:
    """Real behavioral coverage: calls the actual extracted function with a
    lightweight fake chunk, no mocking of the Gemini session or websocket
    needed at all."""

    def test_a_chunk_with_usage_metadata_is_metered(self, monkeypatch):
        recorded = []
        monkeypatch.setattr(
            "agent_friday.services.cost_meter.meter",
            lambda provider, model, usage, **k: recorded.append((provider, model, usage, k)),
        )
        chunk = _fake_chunk(prompt_tokens=100, response_tokens=50)

        result = vr._meter_gemini_live_chunk(chunk, "gemini-2.5-flash-live")

        assert result is True
        assert len(recorded) == 1, (
            "a chunk carrying real usage_metadata did not result in exactly "
            "one cost_meter.meter() call -- real Gemini Live spend is not "
            "being recorded"
        )
        provider, model, usage, kwargs = recorded[0]
        assert provider == "gemini"
        assert model == "gemini-2.5-flash-live"
        assert usage == {"input_tokens": 100, "output_tokens": 50}
        assert kwargs.get("kind") == "voice"

    def test_a_chunk_without_usage_metadata_is_not_metered(self, monkeypatch):
        recorded = []
        monkeypatch.setattr(
            "agent_friday.services.cost_meter.meter",
            lambda *a, **k: recorded.append((a, k)),
        )
        chunk = _fake_chunk(usage_metadata=False)

        result = vr._meter_gemini_live_chunk(chunk, "gemini-2.5-flash-live")

        assert result is False
        assert recorded == [], (
            "a chunk with no usage_metadata at all still triggered a "
            "cost_meter.meter() call -- this would record a fabricated "
            "$0-token charge instead of correctly recording nothing"
        )

    def test_missing_token_counts_default_to_zero_not_a_crash(self, monkeypatch):
        recorded = []
        monkeypatch.setattr(
            "agent_friday.services.cost_meter.meter",
            lambda provider, model, usage, **k: recorded.append(usage),
        )
        chunk = _fake_chunk(prompt_tokens=None, response_tokens=None)

        result = vr._meter_gemini_live_chunk(chunk, "gemini-2.5-flash-live")

        assert result is True
        assert recorded[0] == {"input_tokens": 0, "output_tokens": 0}

    def test_a_metering_exception_never_raises_out_of_the_function(self, monkeypatch):
        """The original inline try/except's whole point: a metering failure
        must never break the live voice bridge. Proven directly by making
        cost_meter.meter() itself raise, and confirming the call site is
        never bothered by it."""
        def _boom(*a, **k):
            raise RuntimeError("simulated cost_meter failure")

        monkeypatch.setattr("agent_friday.services.cost_meter.meter", _boom)
        chunk = _fake_chunk(prompt_tokens=10, response_tokens=5)

        result = vr._meter_gemini_live_chunk(chunk, "gemini-2.5-flash-live")

        assert result is False, (
            "a metering exception propagated as a truthy/unexpected result "
            "instead of being swallowed -- this instrumentation must never "
            "be able to tear down the voice session"
        )


class TestWsLiveCallsTheMeteringFunction:
    """The much simpler structural check the extraction leaves behind:
    does ws_live's receive loop actually call the (now independently
    tested) metering function, rather than the metering logic having been
    quietly removed or never wired back in after some future refactor."""

    def test_ws_live_calls_the_extracted_metering_function(self):
        src = inspect.getsource(vr)
        i_def = src.index("def ws_live(ws):")
        body = src[i_def:]
        i_next_def = body.index("\n    def ", 1) if "\n    def " in body[1:] else len(body)
        body = body[:i_next_def]
        assert "_meter_gemini_live_chunk(chunk, model_name)" in body, (
            "ws_live no longer calls _meter_gemini_live_chunk() -- Gemini "
            "Live usage is read from the stream (if at all) but never "
            "reaches cost_meter"
        )


class TestPricingTableCoversTheLiveFallbackChain:
    def test_pricing_table_has_entries_for_the_live_fallback_chain(self):
        import agent_friday.services.cost_meter as cost_meter
        from agent_friday.services.voice_engine import (
            LIVE_MODEL, LIVE_MODEL_FALLBACK, LIVE_MODEL_FALLBACK2,
        )
        for model_id in (LIVE_MODEL, LIVE_MODEL_FALLBACK, LIVE_MODEL_FALLBACK2):
            assert model_id in cost_meter.PRICING, (
                f"{model_id!r} (part of the Live model fallback chain) has "
                "no PRICING entry -- a connection that falls back to this "
                "model would meter as $0"
            )
