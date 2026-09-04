"""Gauntlet finding Q6 part (c) / cost-metering batch (2026-09-04): the
Gemini Live voice websocket (routes/voice.py `ws_live`) makes real, billed
Gemini Live API calls with ZERO cost_meter integration anywhere in the
file, despite `cost_meter.PRICING` already carrying entries for exactly
these Live model ids -- clear evidence the original intent was to track
this, just never wired (docs/audits/gauntlet-2026-09-03/findings.jsonl,
Q6).

`ws_live` is a closure nested inside a Flask-Sock route registration
function, not an independently callable module-level function -- this
codebase's established pattern for pinning behavior inside it is a
source-level check (see tests/gauntlet/test_ws_live_respects_local_only.py
for precedent) rather than a full behavioral websocket test. This probe
follows that precedent, and learns its explicitly-documented lesson: pin
the fix's own specific new variable/call, not a generic ambiguous
substring (test_ws_live_respects_local_only.py's own CORRECTION note
records a prior mistake of exactly this kind in this same file).
"""
from __future__ import annotations

import inspect

import agent_friday.routes.voice as vr


def _ws_live_body() -> str:
    src = inspect.getsource(vr)
    i_def = src.index("def ws_live(ws):")
    body = src[i_def:]
    i_next_def = body.index("\n    def ", 1) if "\n    def " in body[1:] else len(body)
    return body[:i_next_def]


class TestWsLiveMetersGeminiUsage:
    def test_ws_live_meters_chunk_usage_metadata(self):
        body = _ws_live_body()

        # Pin the fix's own local variable name (`_um`, assigned from
        # `chunk.usage_metadata`) rather than the bare substring
        # "usage_metadata" or "cost_meter" -- both of those could plausibly
        # appear elsewhere in this large function for an unrelated reason in
        # the future, the same false-positive-vacuous-pass risk this file's
        # sibling probe (test_ws_live_respects_local_only.py) was corrected
        # for.
        assert "getattr(chunk, 'usage_metadata', None)" in body, (
            "ws_live's receive loop never reads chunk.usage_metadata at all "
            "-- there is no way to meter real Gemini Live spend without it"
        )
        assert "_cm.meter(\"gemini\", model_name" in body, (
            "ws_live reads usage_metadata but never calls "
            "cost_meter.meter('gemini', model_name, ...) with it -- real "
            "spend is read but never recorded"
        )
        i_usage_read = body.index("getattr(chunk, 'usage_metadata', None)")
        i_meter_call = body.index("_cm.meter(\"gemini\", model_name")
        assert i_usage_read < i_meter_call, (
            "the usage_metadata read must happen before the meter() call "
            "that consumes it"
        )

    def test_meter_call_is_wrapped_so_it_cannot_break_the_voice_bridge(self):
        body = _ws_live_body()
        i_meter_call = body.index("_cm.meter(\"gemini\", model_name")
        # The nearest preceding `try:` before the meter call must be closer
        # than the nearest preceding `sc = getattr(chunk, 'server_content'`
        # marker (the next unrelated statement after the metering block),
        # proving the call sits inside its own try/except rather than the
        # bare receive-loop body where an exception would tear down the
        # entire voice session over a metering failure.
        preceding = body[:i_meter_call]
        i_try = preceding.rindex("try:")
        i_server_content_marker = preceding.rfind("sc = getattr(chunk, 'server_content'")
        assert i_try > i_server_content_marker, (
            "the cost_meter.meter() call for Gemini Live usage does not sit "
            "inside its own try/except -- a metering failure could raise "
            "and tear down the live voice bridge, which must never happen "
            "for instrumentation code"
        )

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
