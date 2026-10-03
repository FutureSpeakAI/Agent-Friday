"""Tier 1 answers in under 50 ms on the CPU, and never waits longer than asked.

Needs the real encoder under FRIDAY_DIR/models/laya2-encoder; CI has none and
skips. The 50 ms budget is the floor machine's, so the bound is scaled by a
CPU factor measured right here: twenty 512x512 float32 matmuls, timed against
BASELINE_MS, which is that loop's median on the reference machine the budget
was set on (18.45 ms, 2026-10-03, 4 intra-op threads for the encoder). A
slower runner gets a proportionally longer bound and never a shorter one.
"""
from __future__ import annotations

import time

import pytest

from agent_friday.services import laya2_encoder, laya2_seeds, reflex_turn

BUDGET_MS = 50
BASELINE_MS = 18.45
TARGET_TOKENS = 256


def _cpu_factor():
    import numpy as np
    rng = np.random.default_rng(0)
    a = rng.standard_normal((512, 512), dtype=np.float32)
    b = rng.standard_normal((512, 512), dtype=np.float32)
    for _ in range(3):
        a @ b
    runs = []
    for _ in range(5):
        t0 = time.perf_counter()
        for _ in range(20):
            a @ b
        runs.append((time.perf_counter() - t0) * 1000)
    runs.sort()
    return max(1.0, runs[2] / BASELINE_MS)


def _long_inputs(n):
    """`n` distinct inputs of at least TARGET_TOKENS tokens (truncated to it)."""
    seeds = [t for shape in laya2_seeds.SHAPES for t in laya2_seeds.SHAPE_SEEDS[shape]]
    out = []
    for i in range(n):
        words = []
        j = i
        while len(words) < TARGET_TOKENS * 2:
            words.extend(seeds[j % len(seeds)].split())
            j += 7
        out.append(" ".join(words))
    return out


def _warm():
    """Skip without the artifact; otherwise wait for the encoder to be warm.

    The first call loads the model and builds the prototypes in the
    background and is a counted miss by design.
    """
    if not laya2_encoder.is_available():
        pytest.skip("laya2 encoder artifact not present")
    deadline = time.perf_counter() + 30
    while time.perf_counter() < deadline:
        if not reflex_turn.classify("warm up", budget_ms=5000)["degraded"]:
            break
    else:
        pytest.fail("encoder did not warm within 30 s")
    assert laya2_encoder.status()["state"] == "ready"


def test_the_inputs_really_are_256_tokens():
    _warm()
    enc = laya2_encoder._encoder
    enc._prepare(512)
    lengths = [len(e.ids) for e in enc.tokenizer.encode_batch(_long_inputs(5))]
    assert min(lengths) >= TARGET_TOKENS


def test_p95_under_the_budget_on_256_token_inputs():
    _warm()
    factor = _cpu_factor()
    times = []
    for text in _long_inputs(30):
        v = reflex_turn.classify(text, budget_ms=5000)
        assert v["degraded"] is False, v
        times.append(v["elapsed_ms"])
    times.sort()
    p95 = times[int(round(0.95 * (len(times) - 1)))]
    bound = BUDGET_MS * factor
    assert p95 <= bound, "p95 %.1f ms over %.1f ms (cpu factor %.2f); p50 %.1f" % (
        p95, bound, factor, times[len(times) // 2])


def test_a_one_millisecond_budget_degrades_instead_of_waiting():
    _warm()
    text = _long_inputs(1)[0]
    full = reflex_turn.classify(text, budget_ms=5000)
    assert full["degraded"] is False
    v = reflex_turn.classify(text, budget_ms=1)
    assert v["degraded"] is True and v["reason"] == "budget"
    assert (v["route"], v["effort"]) == ("brain", "medium")
    # Ten times the budget, plus the thread hand-off; and well under the time
    # the full answer takes, which is what "never blocks" means here.
    assert v["elapsed_ms"] <= 10 * 1 + 10, v
    assert v["elapsed_ms"] < full["elapsed_ms"], (v, full)
    time.sleep(0.1)
