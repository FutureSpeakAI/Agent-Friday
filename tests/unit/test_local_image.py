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
    monkeypatch.setattr(li, "is_installed", lambda mid=None: True)


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
    monkeypatch.setattr(li, "is_installed", lambda mid=None: False)
    out = li.generate("x")
    assert out["status"] == "unavailable"
    assert "not found" in out["reason"]


# ── the lease ────────────────────────────────────────────────────────────────

def test_generation_takes_an_image_lease(installed, monkeypatch):
    arb = FakeArbiter()
    monkeypatch.setattr(li, "_post", lambda p, b, timeout=60: {"prompt_id": "1"})
    monkeypatch.setattr(
        li, "_await_result",
        lambda pid, timeout=600, cancelled=None: [
            {"filename": "a.png", "subfolder": ""}])
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
    monkeypatch.setattr(
        li, "_await_result",
        lambda pid, timeout=600, cancelled=None: [
            {"filename": "a.png", "subfolder": ""}])
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
    # The dispatch gate checks the weights are really on disk, so a model that
    # is configured but half-downloaded falls through to cloud instead of
    # failing at the sampler. Under test the runtime dir is a temp path, so
    # that check has to be stated rather than inherited from the real machine.
    monkeypatch.setattr(li, "is_installed", lambda mid=None: True)
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


# ── cancellation ─────────────────────────────────────────────────────────────
#
# Every test here is a defect that shipped on 2026-08-16 described as working.
# The orb is registered BEFORE the lease is granted, so a cancel arriving in
# that window found no lease, reported "nothing to interrupt", and the job ran
# to completion anyway. Cancellation now hangs on a flag on the JOB, which
# exists from the first line of generate() to the last.

class _SlowArbiter(FakeArbiter):
    """Grants a lease slowly, the way an eviction + ComfyUI start really does."""

    def __init__(self, delay=0.4):
        super().__init__()
        self.delay = delay

    def grant(self, kind, ttl_s=900):
        import time
        time.sleep(self.delay)
        return super().grant(kind, ttl_s=ttl_s)


def _capture_job_id(monkeypatch, then=None):
    """Give a test the orb id generate() invents, so it can cancel that job."""
    import agent_friday.core as core
    seen = {}
    real = core.process_register

    def spy(pid, **kw):
        seen["pid"] = pid
        if then:
            then(pid)
        return real(pid, **kw)
    monkeypatch.setattr(core, "process_register", spy)
    return seen


def test_cancel_before_the_lease_stops_the_job(installed, monkeypatch):
    """THE defect: cancelling while the orb was up but the lease was not yet
    granted did nothing at all, and the image generated anyway."""
    posts = {"n": 0}
    monkeypatch.setattr(li, "_post",
                        lambda p, b, timeout=60: posts.__setitem__("n", posts["n"] + 1)
                        or {"prompt_id": "1"})
    arb = FakeArbiter()
    seen = _capture_job_id(monkeypatch, then=li.request_cancel)
    out = li.generate("x", arbiter=arb)
    li.clear_cancel(seen.get("pid"))
    assert out["status"] == "cancelled"
    assert posts["n"] == 0, "a cancelled job must never reach ComfyUI"
    assert arb.granted == [], "and must not take the GPU it no longer needs"


def test_cancel_during_the_lease_gives_the_gpu_back(installed, monkeypatch):
    """Taking the lease is the slowest part of the job, so this is when a
    cancel most often lands. The GPU must come straight back."""
    import threading
    posts = {"n": 0}
    monkeypatch.setattr(li, "_post",
                        lambda p, b, timeout=60: posts.__setitem__("n", posts["n"] + 1)
                        or {"prompt_id": "1"})
    arb = _SlowArbiter(delay=0.5)
    seen = _capture_job_id(
        monkeypatch,
        then=lambda pid: threading.Timer(0.1, li.request_cancel, args=(pid,)).start())
    out = li.generate("x", arbiter=arb)
    li.clear_cancel(seen.get("pid"))
    assert out["status"] == "cancelled"
    assert posts["n"] == 0
    assert arb.released == 1, "a cancelled lease that is not released strands the GPU"


def test_an_interrupted_prompt_ends_the_wait_immediately(monkeypatch):
    """ComfyUI writes a history entry ONLY when the prompt is over. An
    interrupted one has status_str='error', completed=False and no outputs —
    the exact shape _await_result used to read as 'not finished yet' and then
    poll for the remaining ten minutes."""
    monkeypatch.setattr(li, "_get", lambda path, timeout=30: {"p1": {
        "outputs": {},
        "status": {"status_str": "error", "completed": False,
                   "messages": [["execution_interrupted", {}]]}}})
    with pytest.raises(li.Cancelled):
        li._await_result("p1", timeout=30)


def test_a_failed_prompt_is_an_error_not_a_cancellation(monkeypatch):
    monkeypatch.setattr(li, "_get", lambda path, timeout=30: {"p1": {
        "outputs": {},
        "status": {"status_str": "error", "completed": False,
                   "messages": [["execution_error", {}]]}}})
    with pytest.raises(RuntimeError):
        li._await_result("p1", timeout=30)


def test_the_wait_honours_the_cancel_flag_and_interrupts_comfyui(monkeypatch):
    interrupted = {"n": 0}
    monkeypatch.setattr(li, "_get", lambda path, timeout=30: {})
    monkeypatch.setattr(li, "interrupt_comfy",
                        lambda timeout=10: interrupted.__setitem__("n", 1) or True)
    with pytest.raises(li.Cancelled):
        li._await_result("p1", timeout=30, cancelled=lambda: True)
    assert interrupted["n"] == 1, "stopping the wait must also stop the sampler"


# ── Friday's own output is not Stephen's ─────────────────────────────────────

def test_system_generations_stay_out_of_the_creations_gallery(installed,
                                                              monkeypatch):
    """Verification images once landed in the gallery indistinguishable from
    his own work, and he had to delete them by hand."""
    monkeypatch.setattr(li, "_post", lambda p, b, timeout=60: {"prompt_id": "1"})
    monkeypatch.setattr(
        li, "_await_result",
        lambda pid, timeout=600, cancelled=None: [
            {"filename": "a.png", "subfolder": ""}])
    copied = {"n": 0}
    import shutil
    monkeypatch.setattr(shutil, "copy2",
                        lambda *a, **k: copied.__setitem__("n", copied["n"] + 1))
    out = li.generate("x", arbiter=FakeArbiter(), system=True)
    assert out["status"] == "ok"
    assert copied["n"] == 0, "a system image must not be published to the gallery"
    assert out["files"][0]["system_generated"] is True
    assert out["files"][0]["url"] is None


# ── SD 3.5 Medium, the alternative image seat ────────────────────────────────
# Added 2026-08-18. Z-Image stays the default; this model is a CHOICE, and the
# two disagree about sampler settings by enough that swapping one graph's
# numbers into the other yields a grey smear rather than a slower picture.

def test_sd35_uses_one_checkpoint_for_model_clip_and_vae():
    wf = li.build_workflow("a red bicycle", model_id=li.SD35_MEDIUM_ID)
    assert wf["1"]["class_type"] == "CheckpointLoaderSimple"
    assert wf["1"]["inputs"]["ckpt_name"].startswith("sd3.5_medium_incl_clips")
    # MODEL from slot 0, CLIP from slot 1, VAE from slot 2 — all one loader.
    assert wf["7"]["inputs"]["model"] == ["1", 0]
    assert wf["4"]["inputs"]["clip"] == ["1", 1]
    assert wf["8"]["inputs"]["vae"] == ["1", 2]


def test_sd35_does_not_inherit_z_images_turbo_settings():
    """cfg 1.0 and 8 steps are Z-Image's; SD 3.5 needs guidance and time."""
    z = li.build_workflow("x")
    s = li.build_workflow("x", model_id=li.SD35_MEDIUM_ID,
                          steps=li.MODELS[li.SD35_MEDIUM_ID]["steps"],
                          cfg=li.MODELS[li.SD35_MEDIUM_ID]["cfg"])
    assert z["7"]["inputs"]["cfg"] == 1.0
    assert z["7"]["inputs"]["steps"] == 8
    assert s["7"]["inputs"]["cfg"] == 4.5
    assert s["7"]["inputs"]["steps"] == 30
    assert s["7"]["inputs"]["scheduler"] == "sgm_uniform"


def test_the_default_seat_is_still_z_image():
    """Adding a model must not silently move the seat."""
    assert li.MODEL_ID == "z-image-turbo-fp8"
    assert li.build_workflow("x")["1"]["class_type"] == "UNETLoader"


def test_a_model_missing_its_weights_is_never_offered(monkeypatch, tmp_path):
    monkeypatch.setattr(li, "comfy_root", lambda: tmp_path)
    assert li.available_models() == []
    assert li.is_installed(li.SD35_MEDIUM_ID) is False


def test_available_models_lists_only_what_is_on_disk(monkeypatch, tmp_path):
    (tmp_path / "models" / "checkpoints").mkdir(parents=True)
    (tmp_path / "models" / "checkpoints"
     / "sd3.5_medium_incl_clips_t5xxlfp8scaled.safetensors").touch()
    monkeypatch.setattr(li, "comfy_root", lambda: tmp_path)
    assert li.available_models() == [li.SD35_MEDIUM_ID]


def test_an_uninstalled_request_falls_back_rather_than_failing(monkeypatch):
    """A seat naming absent weights must not reach the sampler and error.

    The arbiter is SUPPLIED rather than left to `get_arbiter()`. Under test the
    residency layer is absent, so `get_arbiter()` returns None, generate() takes
    its unmanaged-GPU branch and really calls `ComfyUIBackend().start()` — which
    raises FileNotFoundError on a machine with no ComfyUI beside the temp
    runtime dir. generate() catches that and returns status='error' long before
    the sampler, so the fallback this test exists to prove was never reached and
    the failure surfaced as `KeyError: 'model'`.

    An earlier attempt to head that off stubbed `li._ensure_comfy` with
    `raising=False`. There is no `_ensure_comfy` in local_image — the name was
    invented — and `raising=False` turned the mistake into a silent no-op
    instead of an AttributeError naming it.
    """
    monkeypatch.setattr(li, "is_installed",
                        lambda mid=None: mid != li.SD35_MEDIUM_ID)
    seen = {}
    monkeypatch.setattr(li, "build_workflow",
                        lambda *a, **k: seen.setdefault("model", k.get("model_id")) or {})
    monkeypatch.setattr(li, "_post", lambda p, b, timeout=60: {"prompt_id": "1"})
    monkeypatch.setattr(li, "_await_result",
                        lambda pid, timeout=600, cancelled=None: [
                            {"filename": "a.png", "subfolder": ""}])
    out = li.generate("x", model=li.SD35_MEDIUM_ID, arbiter=FakeArbiter())
    # Falling back means finishing, not erroring politely. Asserting the status
    # first is what turns any future short-circuit into a legible failure rather
    # than a KeyError on the line below.
    assert out["status"] == "ok"
    assert seen["model"] == li.MODEL_ID
    assert out["model"] == li.MODEL_ID, (
        "the envelope must name the seat that actually answered, not the one asked for")
