"""The fit arithmetic behind the Models screen, on fixture machines.

Sizes come from file bytes and the attention layout, never from a
parameter-count table; the verdict is placed against the planner's own
budgets; every speed carries its basis.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import model_fit as mf
from tests import residency_fixtures as fx

BONSAI_PTQ1_BYTES = 5946648928
BONSAI_PQ2_BYTES = 7206168928
BONSAI_LAYOUT = {"blocks": 64, "kv_heads": 4, "head_dim_k": 256, "head_dim_v": 256,
                 "full_attention_layers": 16, "window": None, "window_layers": 0}


@pytest.fixture(autouse=True)
def _no_calibration(tmp_path, monkeypatch):
    monkeypatch.setattr(mf, "calibration_path", lambda: tmp_path / "calibration.json")


# ── sizing ───────────────────────────────────────────────────────────────────

def test_the_kv_layout_reads_bonsai2_hybrid_attention_from_the_header():
    meta = {"qwen35.block_count": 64, "qwen35.attention.head_count_kv": 4,
            "qwen35.attention.key_length": 256, "qwen35.attention.value_length": 256,
            "qwen35.full_attention_interval": 4}
    lay = mf.kv_layout_from_header(meta, "qwen35")
    assert lay["full_attention_layers"] == 16
    # 16 layers x 4 heads x 512 dims x 2 bytes = 64 KiB per token at f16
    assert round(mf.kv_mib(lay, 1024, "f16")) == 64
    assert round(mf.kv_mib(lay, 49152, "q8_0")) == 1632


def test_a_sliding_window_family_costs_the_window_not_the_context():
    meta = {"gemma4.block_count": 48, "gemma4.attention.head_count_kv": 8,
            "gemma4.embedding_length": 3840, "gemma4.attention.head_count": 16,
            "gemma4.attention.sliding_window": 1024, "gemma4.attention.sliding_window_pattern": 6}
    lay = mf.kv_layout_from_header(meta, "gemma4")
    assert lay["full_attention_layers"] == 8 and lay["window_layers"] == 40
    assert mf.kv_mib(lay, 131072, "f16") < mf.kv_mib({**lay, "window": None, "window_layers": 0,
                                                       "full_attention_layers": 48}, 131072, "f16") / 3


def test_sizes_come_from_bytes_not_a_parameter_table():
    s = mf.seat_mib(BONSAI_PTQ1_BYTES, BONSAI_LAYOUT, 49152, kv_type="q8_0", ub=2048, mmproj_bytes=629246976)
    assert s["weights_mib"] == 5671
    assert s["total_mib"] == 5671 + 1632 + 1200 + 600


# ── verdicts on the reference machine ────────────────────────────────────────

def test_bonsai2_ptq1_runs_well_on_the_reference_card_at_a_real_context():
    out = mf.fit(BONSAI_PTQ1_BYTES, fx.P1, layout=BONSAI_LAYOUT, context_cap=131072,
                 overhead_tokens=25000)
    assert out["verdict"] == "runs_well"
    assert out["context"] >= 32768, "fit is judged at the context Friday serves, not 8K"
    assert out["placement"]["device"] == "gpu:0"
    assert out["speed"]["basis"] == "about" and out["speed"]["tok_s"] > 20
    assert out["why"]["fits_at"].startswith("%d tokens" % out["context"])


def test_the_vision_tower_costs_the_large_compute_buffer_on_12gb():
    """The planner keeps 1 GB of VRAM beyond the display reserve. With the
    600 MiB vision tower loaded the seat only keeps its headroom with the
    small compute buffer (-ub 512), and the placement says which flag it
    needs instead of rounding the verdict up."""
    out = mf.fit(BONSAI_PTQ1_BYTES, fx.P1, layout=BONSAI_LAYOUT, context_cap=131072,
                 mmproj_bytes=629246976, overhead_tokens=25000)
    assert out["verdict"] in ("runs_well", "tight") and out["context"] >= 16384
    assert out["placement"]["ub"] == 512


def test_a_file_too_big_for_the_card_says_how_much_is_missing():
    big = 20 * 2 ** 30
    out = mf.fit(big, fx.P2, layout=BONSAI_LAYOUT, overhead_tokens=25000)
    assert out["verdict"] == "wont_fit"
    assert out["shortfall_mib"] and out["shortfall_mib"] > 10000
    assert "needs" in out["why"]["fits_at"]


def test_an_8gb_windows_laptop_runs_bonsai2_partly_on_the_processor():
    out = mf.fit(BONSAI_PTQ1_BYTES, fx.P2, layout=BONSAI_LAYOUT, blocks=64, overhead_tokens=25000)
    assert out["verdict"] in ("partial", "tight")
    if out["verdict"] == "partial":
        assert 0.3 < out["placement"]["gpu_fraction"] < 1.0
        assert out["placement"]["n_gpu_layers"] < 64
        assert "of the weights on" in out["why"]["fits_at"]
        assert "%" in out["speed"]["formula"]


def test_a_cpu_only_machine_gets_a_processor_verdict_with_a_speed():
    out = mf.fit(BONSAI_PTQ1_BYTES, fx.P5, layout=BONSAI_LAYOUT, overhead_tokens=25000)
    assert out["placement"]["device"] == "cpu"
    assert out["speed"]["basis"] == "about" and 2 < out["speed"]["tok_s"] < 12


def test_pretend_i_have_a_bigger_card_recomputes_locally():
    sim = mf.what_if(fx.P2, vram_total_mib=24576, gpu_name="NVIDIA GeForce RTX 4090")
    assert sim["simulated"] is True
    before = mf.fit(BONSAI_PQ2_BYTES, fx.P2, layout=BONSAI_LAYOUT, overhead_tokens=25000)
    after = mf.fit(BONSAI_PQ2_BYTES, sim, layout=BONSAI_LAYOUT, context_cap=262144, overhead_tokens=25000)
    assert before["verdict"] != "runs_well" and after["verdict"] == "runs_well"
    assert after["context"] > (before["context"] or 0)


def test_what_would_i_need_names_vram_ram_and_disk():
    need = mf.what_would_i_need(BONSAI_PQ2_BYTES, BONSAI_LAYOUT, 131072, mmproj_bytes=629246976)
    assert need["vram_total_mib"] > need["seat"]["total_mib"]
    assert need["disk_mib"] > 6872 + 10000


# ── speed bases and calibration ──────────────────────────────────────────────

def test_measured_beats_published_beats_about():
    assert mf.decode_estimate(BONSAI_PTQ1_BYTES, fx.P1, gpu_name="RTX 4070", measured=33.0)["basis"] == "measured"
    assert mf.decode_estimate(BONSAI_PTQ1_BYTES, fx.P1, gpu_name="RTX 4070", published=32.1)["basis"] == "published"
    about = mf.decode_estimate(BONSAI_PTQ1_BYTES, fx.P1, gpu_name="RTX 4070")
    assert about["basis"] == "about" and about["calibration"]["basis"] == "uncalibrated"


def test_one_measurement_calibrates_every_about_speed():
    before = mf.decode_estimate(BONSAI_PTQ1_BYTES, fx.P1, gpu_name="RTX 4070")["tok_s"]
    cal = mf.record_calibration("bonsai2:27b", measured_tok_s=33.0, estimated_tok_s=before)
    assert cal["basis"] == "calibrated" and 0.5 < cal["factor"] < 1.0
    after = mf.decode_estimate(BONSAI_PTQ1_BYTES, fx.P1, gpu_name="RTX 4070")
    assert abs(after["tok_s"] - 33.0) < 0.5
    other = mf.decode_estimate(2 * BONSAI_PTQ1_BYTES, fx.P1, gpu_name="RTX 4070")
    assert abs(other["tok_s"] - 16.5) < 0.5, "the factor applies to every other row"


def test_an_unknown_card_says_unknown_rather_than_guessing():
    p = json.loads(json.dumps(fx.P1))
    p["gpus"][0]["name"] = "Mystery Accelerator"
    assert mf.decode_estimate(BONSAI_PTQ1_BYTES, p, gpu_name="Mystery Accelerator")["basis"] == "unknown"
