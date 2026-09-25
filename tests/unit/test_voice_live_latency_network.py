"""The live latency budget, against the real Gemini Live API. Opt-in and paid.

    pytest tests/unit/test_voice_live_latency_network.py --run-network -n 0

One real turn with Friday's own session config and a real, governed news
lookup (tools/voice_bench/live_latency.py --friday). It fails when the caller
would wait too long for the first word or sit through a long silence: the
2026-09-25 stall was 40 s of silence before any word at all.
"""
import argparse
import asyncio
import os
import sys

import pytest

pytestmark = pytest.mark.network

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "tools", "voice_bench"))

FIRST_AUDIO_BUDGET_S = 3.0      # the model's own time to its first word
SILENCE_BUDGET_S = 8.0          # the longest gap inside the turn


@pytest.mark.parametrize("model", ["gemini-2.5-flash-native-audio-latest", "gemini-3.8-live"])
def test_a_news_question_starts_talking_fast_and_never_goes_quiet(model):
    import live_latency as ll
    if not ll._key():
        pytest.skip("no Gemini key configured")
    args = argparse.Namespace(model=model, voice="Aoede", runs=1, prompt_chars=74000,
                              tool_seconds=0.0, behavior="BLOCKING", timeout=60.0,
                              friday=True, cold=False)
    # Hard bound: a live test must fail, never hang a suite.
    [r] = asyncio.run(asyncio.wait_for(ll.main_async(args), timeout=150))
    assert r.get("first_audio") is not None, "no audio at all: %s" % r
    assert r["first_audio"] < FIRST_AUDIO_BUDGET_S, r
    assert r.get("silent_gap_max", 0.0) < SILENCE_BUDGET_S, r
