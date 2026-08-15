"""Routed local image generation under the arbiter's exclusive lease (D8).

D8 held this until a residency scheduler existed, because Z-Image's ~14.5 GB of
weights and the language seats cannot share a 12 GB card. The lease performs
the evict → generate → reload sequence; without it the two simply fight.
"""
from __future__ import annotations

import pytest

from agent_friday.services import local_image as li


class FakeArbiter:
    def __init__(self, grant_ok=True, error=None):
        self.grant_ok = grant_ok
        self.error = error
        self.granted = []
        self.released = 0

    def grant(self, kind, ttl_s=900):
        self.granted.append(kind)
        if not self.grant_ok:
            return {"ok": False, "error": self.error or "refused",
                    "refused": {"rule_id": "R2"}}
        return {"ok": True, "lease": {"kind": kind}}

    def release(self):
        self.released += 1
        return {"ok": True}


@pytest.fixture
def installed(monkeypatch):
    monkeypatch.setattr(li, "is_installed", lambda: True)


# ── the workflow ─────────────────────────────────────────────────────────────

def test_workflow_matches_the_proven_z_image_graph():
    wf = li.build_workflow("a red bicycle")
    assert wf["1"]["class_type"] == "UNETLoader"
    assert wf["1"]["inputs"]["weight_dtype"] == "fp8_e4m3fn"
    assert wf["7"]["class_type"] == "KSampler"
    assert wf["7"]["inputs"]["sampler_name"] == "euler"
    assert wf["7"]["inputs"]["cfg"] == 1.0
    assert wf["4"]["inputs"]["text"] == "a red bicycle"


def test_clip_loader_type_is_stable_diffusion_deliberately():
    """comfy/sd.py routes a detected QWEN3_4B encoder to text_encoders.z_image
    whenever clip_type is not FLUX/FLUX2. There is no 'z_image' loader type."""
    assert li.build_workflow("x")["2"]["inputs"]["type"] == "stable_diffusion"


@pytest.mark.parametrize("ar,size", [
    ("1:1", (1024, 1024)), ("16:9", (1344, 768)), ("9:16", (768, 1344)),
])
def test_aspect_ratios_map_to_sizes(ar, size):
    assert li._SIZES[ar] == size


# ── availability is earned ───────────────────────────────────────────────────

def test_missing_weights_report_unavailable_rather_than_failing(monkeypatch):
    monkeypatch.setattr(li, "is_installed", lambda: False)
    out = li.generate("x")
    assert out["status"] == "unavailable"
    assert "not found" in out["reason"]


# ── the lease ────────────────────────────────────────────────────────────────

def test_generation_takes_an_image_lease(installed, monkeypatch):
    arb = FakeArbiter()
    monkeypatch.setattr(li, "_post", lambda p, b, timeout=60: {"prompt_id": "1"})
    monkeypatch.setattr(li, "_await_result",
                        lambda pid, timeout=600: [{"filename": "a.png",
                                                   "subfolder": ""}])
    out = li.generate("x", arbiter=arb)
    assert arb.granted == ["image_job"]
    assert out["status"] == "ok"
    assert out["local"] is True


def test_the_lease_is_released_even_when_generation_fails(installed,
                                                          monkeypatch):
    """An unreleased lease strands the machine with no language seats."""
    arb = FakeArbiter()

    def boom(*a, **k):
        raise RuntimeError("comfy exploded")
    monkeypatch.setattr(li, "_post", boom)
    out = li.generate("x", arbiter=arb)
    assert out["status"] == "error"
    assert arb.released == 1


def test_a_refused_lease_stops_generation_and_names_the_rule(installed,
                                                             monkeypatch):
    """Starting ComfyUI anyway would fight the pinned seats for VRAM."""
    arb = FakeArbiter(grant_ok=False, error="refused: RAM ceiling")
    called = {"n": 0}

    def counted(*a, **k):
        called["n"] += 1
        return {"prompt_id": "1"}
    monkeypatch.setattr(li, "_post", counted)
    out = li.generate("x", arbiter=arb)
    assert out["status"] == "refused"
    assert out["rule_id"] == "R2"
    assert called["n"] == 0, "no workflow may be submitted after a refusal"
    assert arb.released == 0


def test_no_arbiter_still_generates_but_is_flagged(installed, monkeypatch):
    """A process with no residency layer must still work — and say the GPU is
    unmanaged rather than pretending it is governed."""
    started = {"n": 0}

    class _Comfy:
        def start(self, timeout=300):
            started["n"] += 1
            return 1.0
    monkeypatch.setattr(
        "agent_friday.services.residency_arbiter.ComfyUIBackend", _Comfy)
    monkeypatch.setattr(
        "agent_friday.services.residency_arbiter.get_arbiter", lambda: None)
    monkeypatch.setattr(li, "_post", lambda p, b, timeout=60: {"prompt_id": "1"})
    monkeypatch.setattr(li, "_await_result",
                        lambda pid, timeout=600: [{"filename": "a.png",
                                                   "subfolder": ""}])
    out = li.generate("x")
    assert out["status"] == "ok"
    assert started["n"] == 1


# ── dispatch from the creative engine ────────────────────────────────────────

def test_creative_engine_routes_the_local_model_without_a_cloud_key(
        monkeypatch):
    """`is_available()` asks whether the GEMINI client is configured. An
    on-device generation must not be refused for want of a cloud credential —
    that is exactly why a working local stack stayed invisible."""
    from agent_friday.services import creative_engine as ce
    monkeypatch.setattr(ce, "is_available", lambda: False)
    monkeypatch.setattr(ce, "_configured_image_model", lambda: li.MODEL_ID)
    monkeypatch.setattr(ce, "check_content_safety", lambda p: (True, None))
    monkeypatch.setattr(li, "generate",
                        lambda p, **k: {"status": "ok", "files": ["a.png"],
                                        "local": True})
    out = ce.generate_image("a red bicycle")
    assert out["status"] == "ok"
    assert out["local"] is True


def test_a_cloud_seat_is_untouched_by_the_local_path(monkeypatch):
    from agent_friday.services import creative_engine as ce
    monkeypatch.setattr(ce, "_configured_image_model",
                        lambda: "gemini-nano-banana-2")
    monkeypatch.setattr(ce, "check_content_safety", lambda p: (True, None))
    monkeypatch.setattr(ce, "is_available", lambda: False)
    out = ce.generate_image("x")
    assert out["status"] in ("unavailable", "error", "blocked")
    assert not out.get("local")


# ── classification ───────────────────────────────────────────────────────────

def test_the_comfyui_adapter_can_earn_local():
    from agent_friday.routing.provider_descriptors import (
        classification_of, LOCAL_CAPABLE_ADAPTERS, ADAPTER_COMFYUI)
    assert ADAPTER_COMFYUI in LOCAL_CAPABLE_ADAPTERS
    assert classification_of({"type": "comfyui", "classification": "local",
                              "base_url": "http://127.0.0.1:8188"}) == "local"


def test_a_public_comfyui_url_is_still_cloud():
    """Local is earned by adapter AND private address, never claimed."""
    from agent_friday.routing.provider_descriptors import classification_of
    assert classification_of({"type": "comfyui", "classification": "local",
                              "base_url": "https://comfy.example.com"}) == "cloud"
