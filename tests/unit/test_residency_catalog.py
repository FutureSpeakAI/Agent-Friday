"""CatalogEntry — what a model costs on THIS machine.

Two properties carry most of the weight:
  * VRAM is measured, never derived from artifact size (the e2b is a 7.2 GB
    file occupying 1763 MiB — a 4x error that inverts size-based ranking);
  * capabilities discriminate models that cannot generate and models that
    think, which is what makes the health probe stop reporting false failures.
"""
from __future__ import annotations

import pytest

from agent_friday.services import residency_catalog as rc

P1 = {
    "gpus": [{"index": 0, "name": "NVIDIA GeForce RTX 4070",
              "vram_total_mib": 12282}],
    "ram": {"total_mib": 32620},
    "disk": {"read_mib_s": 427.0},
}


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch, tmp_path):
    """Never touch the real measurement store."""
    monkeypatch.setattr(rc, "store_path", lambda: tmp_path / "m.json")
    rc.reset_cache()


@pytest.fixture
def caps(monkeypatch):
    def _set(mapping):
        monkeypatch.setattr(
            rc, "_show",
            lambda mid, *a, **k: {"capabilities": mapping.get(mid, []),
                                  "details": {}})
    return _set


# ── fingerprint ──────────────────────────────────────────────────────────────

def test_fingerprint_is_readable_and_stable():
    assert rc.profile_fingerprint(P1) == rc.P1_FINGERPRINT


def test_cpu_only_host_gets_its_own_fingerprint():
    assert rc.profile_fingerprint(
        {"gpus": [], "ram": {"total_mib": 32768}}) == "cpu-only|32768"


# ── seeded measurements ──────────────────────────────────────────────────────

def test_seed_carries_the_reference_measurements():
    rows = rc.measurements("gemma4:12b", rc.P1_FINGERPRINT)
    # 65536 and 131072 were added 2026-08-15 by the KV sweep that settled how
    # much a bigger window actually costs: 7718 -> 7750 -> 7814 MiB. 96 MiB for
    # 4x the context is the evidence behind sizing the brain from the whole
    # prompt rather than from the tool registry alone.
    assert [r["num_ctx"] for r in rows] ==         [4096, 8192, 16384, 32768, 65536, 131072]
    assert all(r["source"] == "seed" for r in rows)


def test_vram_is_measured_not_derived_from_artifact_size():
    """The e2b is a 7.2 GB artifact occupying 1763 MiB — a 4x error."""
    assert rc.vram_at("gemma4:e2b", rc.P1_FINGERPRINT, 8192) == 1763


def test_size_ranking_would_invert_the_true_vram_order():
    """Documents why _pick_local_model's artifact-size heuristic is unusable."""
    e2b_vram = rc.vram_at("gemma4:e2b", rc.P1_FINGERPRINT, 8192)
    e4b_vram = rc.vram_at("gemma4:e4b", rc.P1_FINGERPRINT, 8192)
    # e2b's FILE (7.2 GB) is smaller than e4b's (9.6 GB) and so is its VRAM,
    # but the ratio is nothing like the file ratio: 1763 vs 3081.
    assert e2b_vram < e4b_vram
    assert e4b_vram / e2b_vram > 1.7


def test_moe_total_and_active_params_are_distinguished():
    assert rc.KNOWN_ACTIVE_PARAMS_B["gemma4:26b"] == 4.0


def test_baseline_ms_per_token_is_available_for_the_5x_rule():
    assert rc.baseline_ms_per_token("gemma4:12b", rc.P1_FINGERPRINT) == 20.26


def test_unmeasured_model_has_no_baseline():
    assert rc.baseline_ms_per_token("nope:1b", rc.P1_FINGERPRINT) is None


# ── vram lookup is pessimistic, never optimistic ─────────────────────────────

def test_unmeasured_ctx_uses_the_nearest_measured_above():
    """Asking for 12288 must not answer with the 8192 figure."""
    assert rc.vram_at("gemma4:12b", rc.P1_FINGERPRINT, 12288) == 8001


def test_ctx_above_everything_measured_uses_the_largest():
    """Never extrapolate downward: under-estimating VRAM fails at load time."""
    # 7814 is the 131072 row — the largest measured CONTEXT, which is what
    # the lookup returns. Not the largest NUMBER: the 16384 row reads 8001,
    # higher than several rows above it, because it was taken under an older
    # Ollama whose allocator differed.
    #
    # Asked at 262144, above every measured row. 65536 and 131072 stopped
    # being valid probes for this on 2026-08-15 when the KV sweep measured
    # them directly — they now return exact matches, which is a different
    # code path.
    assert rc.vram_at("gemma4:12b", rc.P1_FINGERPRINT, 262144) == 7814
    assert rc.vram_at("gemma4:12b", rc.P1_FINGERPRINT, 65536) == 7750


# ── recorded measurements beat the seed ──────────────────────────────────────

def test_recorded_measurement_overrides_the_seed_at_the_same_ctx():
    rc.record_measurement("gemma4:12b", rc.P1_FINGERPRINT,
                          {"num_ctx": 16384, "vram_mib": 8200,
                           "ms_per_token": 19.0})
    assert rc.vram_at("gemma4:12b", rc.P1_FINGERPRINT, 16384) == 8200
    rows = {r["num_ctx"]: r for r in
            rc.measurements("gemma4:12b", rc.P1_FINGERPRINT)}
    assert rows[16384]["source"] == "measured"
    assert rows[8192]["source"] == "seed", "other contexts keep their seed"


def test_recording_is_idempotent_at_a_given_ctx():
    for v in (8100, 8150, 8200):
        rc.record_measurement("gemma4:12b", rc.P1_FINGERPRINT,
                              {"num_ctx": 16384, "vram_mib": v})
    rows = [r for r in rc.measurements("gemma4:12b", rc.P1_FINGERPRINT)
            if r["num_ctx"] == 16384]
    assert len(rows) == 1 and rows[0]["vram_mib"] == 8200


# ── capabilities: the two probe defects ──────────────────────────────────────

def test_embedding_model_is_not_a_generation_candidate(caps):
    """Live defect: the probe sent qwen3-embedding to /api/generate and the
    whole local provider reported down."""
    caps({"qwen3-embedding:0.6b": ["tools", "thinking", "embedding"]})
    assert rc.can_generate("qwen3-embedding:0.6b") is False
    assert rc.is_embedding("qwen3-embedding:0.6b") is True


def test_chat_model_is_a_generation_candidate(caps):
    caps({"gemma4:12b": ["completion", "vision", "audio", "tools", "thinking"]})
    assert rc.can_generate("gemma4:12b") is True
    assert rc.is_embedding("gemma4:12b") is False


def test_thinking_models_are_flagged(caps):
    """A 1-token probe against these returns empty from a healthy model."""
    caps({"gemma4:12b": ["completion", "thinking"],
          "plain:1b": ["completion"]})
    assert rc.needs_think_disabled("gemma4:12b") is True
    assert rc.needs_think_disabled("plain:1b") is False


def test_unknown_capabilities_assume_generation(caps):
    """An older daemon reporting nothing must not break dispatch."""
    caps({})
    assert rc.can_generate("mystery:7b") is True


def test_unknown_capabilities_still_catch_an_obvious_embedder(caps):
    caps({})
    assert rc.can_generate("some-embedding:0.6b") is False


# ── load-time estimator ──────────────────────────────────────────────────────

def test_load_estimate_tracks_the_measured_cold_load():
    """12b: 7.6 GB artifact, measured 20.49 s cold."""
    est = rc.est_load_s(int(7.6 * 1024 ** 3), P1)
    assert 17.0 <= est <= 24.0


def test_moe_carries_a_multiplier():
    """The 26b ran ~35% over the dense fit — expert placement, not bandwidth."""
    dense = rc.est_load_s(int(17 * 1024 ** 3), P1, is_moe=False)
    moe = rc.est_load_s(int(17 * 1024 ** 3), P1, is_moe=True)
    assert moe > dense


def test_no_disk_rate_yields_no_estimate_rather_than_a_guess():
    assert rc.est_load_s(1024, {"disk": {}}) is None


def test_params_parsing():
    assert rc._parse_params_b("11.9B") == pytest.approx(11.9)
    assert rc._parse_params_b("595.78M") == pytest.approx(0.59578)
    assert rc._parse_params_b("") is None
