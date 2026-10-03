"""The stack preview: solid blocks stay loaded, dashed blocks take turns
with their swap time, the reserve is visible, and the sentence is the
planner's rules in words."""
from __future__ import annotations

import pytest

from agent_friday.services import model_fit as mf
from agent_friday.services import model_stack as ms
from tests import residency_fixtures as fx

BONSAI = {"id": "bonsai2:27b", "label": "Bonsai 2", "kind": "text", "role": "interactive_brain",
          "file_bytes": 5946648928, "context_cap": 131072, "measured_load_s": 20.0,
          "layout": {"blocks": 64, "kv_heads": 4, "head_dim_k": 256, "head_dim_v": 256,
                     "full_attention_layers": 16, "window": None, "window_layers": 0}}
# The peak the live run measured with ComfyUI holding the display reserve.
ZIMAGE = {"id": "z-image-turbo-fp8", "label": "Z-Image", "kind": "image", "peak_vram_mib": 6900,
          "file_bytes": 6150000000, "measured_load_s": 30.0}
LAYA = {"id": "laya", "label": "Laya", "kind": "cpu", "host_ram_mib": 1700}
KOKORO = {"id": "kokoro", "label": "Kokoro voice", "kind": "cpu", "host_ram_mib": 330}
HUGE = {"id": "huge", "label": "A 40 GB model", "kind": "text", "file_bytes": 40 * 2 ** 30}


@pytest.fixture(autouse=True)
def _no_cal(tmp_path, monkeypatch):
    monkeypatch.setattr(mf, "calibration_path", lambda: tmp_path / "c.json")


def test_the_reference_stack_shows_the_reserve_the_brain_and_a_dashed_image_seat():
    out = ms.preview(fx.P1, [BONSAI, ZIMAGE, LAYA, KOKORO], overhead_tokens=25000)
    gpu = out["pools"][0]
    kinds = [b["kind"] for b in gpu["blocks"]]
    assert kinds[0] == "reserve" and gpu["blocks"][0]["mib"] > 2000, "the reserve is a labelled block"
    brain = next(b for b in gpu["blocks"] if b.get("model_id") == "bonsai2:27b")
    assert brain["kind"] == "pinned" and brain["context"] >= 32768
    img = next(b for b in gpu["blocks"] if b.get("model_id") == "z-image-turbo-fp8")
    assert img["kind"] == "leased" and img["swap_s"] == 50, "brain reload 20 s + image load 30 s"
    assert img["displaces"] == ["Bonsai 2"]
    ram = out["pools"][1]
    assert {b["label"] for b in ram["blocks"]} == {"Laya", "Kokoro voice"}
    assert out["fits_together"] is False
    assert out["sentence"].startswith("These take turns: Z-Image evicts Bonsai 2 for about 50 s")


def test_two_text_models_that_do_not_fit_together_take_turns():
    second = dict(BONSAI, id="other:27b", label="Other 27B", role="heavy_hitter")
    out = ms.preview(fx.P1, [BONSAI, second], overhead_tokens=25000)
    gpu = out["pools"][0]
    assert [b["kind"] for b in gpu["blocks"] if b.get("model_id")] == ["pinned", "leased"]
    assert out["turns"][0]["label"] == "Other 27B"


def test_a_24gb_card_holds_more_context_and_the_sentence_names_the_spare():
    out = ms.preview(fx.P3, [BONSAI], overhead_tokens=25000)
    assert out["fits_together"] is True
    assert out["sentence"].startswith("These fit together,")
    brain = out["pools"][0]["blocks"][-1]
    assert brain["context"] >= 131072


def test_what_cannot_run_even_taking_turns_says_how_much_is_missing():
    out = ms.preview(fx.P2, [BONSAI, HUGE], overhead_tokens=25000)
    assert out["cannot"] and out["cannot"][0]["id"] == "huge"
    assert "cannot run on this computer even taking turns" in out["sentence"]
    assert out["cannot"][0]["shortfall_mib"] > 20000


def test_a_cpu_only_machine_has_one_pool_and_cpu_seats_in_it():
    out = ms.preview(fx.P5, [LAYA, KOKORO], overhead_tokens=25000)
    assert [p["name"] for p in out["pools"]] == ["Processor RAM"]
    assert out["fits_together"] is True


def test_a_pretend_card_marks_the_preview_simulated():
    sim = mf.what_if(fx.P2, vram_total_mib=24576, gpu_name="NVIDIA GeForce RTX 4090")
    out = ms.preview(sim, [BONSAI, ZIMAGE], overhead_tokens=25000)
    assert out["simulated"] is True
    assert next(b for b in out["pools"][0]["blocks"] if b.get("model_id") == "bonsai2:27b")["kind"] == "pinned"
