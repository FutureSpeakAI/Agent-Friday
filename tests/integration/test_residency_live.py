"""Live residency cycles on the reference instance. Opt-in.

    pytest tests/integration/test_residency_live.py --run-live-residency

These load real multi-GB models, start real processes, and take minutes. They
are the only proof that the plan survives contact with the machine; everything
else in the residency suite is offline.

Skipped unless the flag is passed AND the hardware is actually present, so the
file is inert on CI and on any other machine.
"""
from __future__ import annotations

import os
import pathlib
import time

import pytest

from agent_friday.services import hardware_profile as hwp
from agent_friday.services import residency_arbiter as ra
from agent_friday.services import residency_catalog as rc

pytestmark = pytest.mark.live_residency

# The root conftest redirects HOME to a throwaway temp dir so the offline suite
# never touches real data. These tests must reach the REAL blob store and
# runtime stack, so they use the home the conftest captured before that
# redirect. It comes through an env var deliberately: importing a module
# constant from conftest re-executes it post-redirect and yields the temp home.
REAL_HOME = pathlib.Path(os.environ.get("FRIDAY_REAL_HOME")
                         or pathlib.Path.home())
GGUF_DIR = REAL_HOME / ".friday" / "runtime" / "models" / "gguf"
BLOBS = REAL_HOME / ".ollama" / "models" / "blobs"

# llama-server loads Ollama's content-addressed blobs directly (verified
# 2026-08-14), so pinned seats cost no extra disk.
PINNED_GGUF = {
    "gemma4:12b": BLOBS / ("sha256-1278394b693672ac2799eadc9a83fd98259a6a88"
                           "a40acfb1dcaa6c6fc895a606"),
    "gemma4:e2b": BLOBS / ("sha256-4e30e2665218745ef463f722c0bf86be0cab6ee6"
                           "76320f1cfadf91e989107448"),
}
HEAVY_GGUF = {"gemma4:26b": GGUF_DIR / "gemma-4-26B-A4B-it-UD-Q4_K_M.gguf"}


@pytest.fixture(scope="module")
def arbiter(request):
    if not request.config.getoption("--run-live-residency", default=False):
        pytest.skip("needs --run-live-residency")
    # runtime_dir() resolves against the isolated home too, so point it at the
    # real stack: llama-server, ComfyUI and the GGUFs all live under it.
    os.environ["FRIDAY_RUNTIME_DIR"] = str(REAL_HOME / ".friday" / "runtime")
    profile = hwp.get(force=True)
    if not profile.get("gpus"):
        pytest.skip("no GPU on this host")
    paths = {k: v for k, v in {**PINNED_GGUF, **HEAVY_GGUF}.items()
             if v.exists()}
    if "gemma4:12b" not in paths:
        pytest.skip("reference models are not installed here")
    a = ra.Arbiter(profile=profile, entries=rc.installed_entries(profile),
                   gguf_paths=paths)
    yield a
    a.shutdown()


def _gpu_used():
    import subprocess
    r = subprocess.run(["nvidia-smi", "--query-gpu=memory.used",
                        "--format=csv,noheader,nounits"],
                       capture_output=True, text=True, timeout=10)
    return int(r.stdout.strip().splitlines()[0])


def test_boots_to_its_default_plan(arbiter):
    plan = arbiter.boot()
    assert arbiter.state == ra.STATE_DEFAULT
    assert plan["seats"]["interactive_brain"]["model_id"] == "gemma4:12b"
    assert "gemma4:12b" in arbiter.llama.procs


def test_the_measured_idle_floor_was_recorded_at_boot(arbiter):
    """Boot is the one honest moment to measure it: nothing of ours is up."""
    gpu = arbiter.profile["gpus"][0]
    assert isinstance(gpu["vram_baseline_mib"], int)
    assert gpu["vram_baseline_at"]


def test_heavy_lease_cycle_is_timed_in_both_directions(arbiter):
    """12b -> 26b -> 12b, the transition this whole layer exists to make safe."""
    grant = arbiter.grant("heavy_turn", ttl_s=600)
    assert grant["ok"] is True, grant.get("error")
    assert arbiter.state == ra.STATE_LEASED
    assert "gemma4:26b" in arbiter.llama.procs
    assert "gemma4:12b" not in arbiter.llama.procs

    release = arbiter.release()
    assert release["ok"] is True
    assert arbiter.state == ra.STATE_DEFAULT
    assert "gemma4:12b" in arbiter.llama.procs

    print("\nheavy lease  grant %.2fs  release %.2fs"
          % (grant["transition_s"], release["transition_s"]))
    assert grant["transition_s"] > 0 and release["transition_s"] > 0


def test_image_lease_starts_comfyui_and_hands_the_gpu_back(arbiter):
    before = _gpu_used()
    grant = arbiter.grant("image_job", ttl_s=900)
    assert grant["ok"] is True, grant.get("error")
    assert arbiter.comfy.running() is True

    release = arbiter.release()
    assert release["ok"] is True
    assert arbiter.comfy.running() is False
    time.sleep(5)
    print("\nimage lease  grant %.2fs  release %.2fs  gpu %d -> %d MiB"
          % (grant["transition_s"], release["transition_s"], before,
             _gpu_used()))


def test_a_load_that_would_breach_the_ram_ceiling_is_refused(arbiter):
    """Simulated by shrinking the profile's RAM, not by exhausting the machine."""
    tiny = dict(arbiter.profile, ram={"total_mib": 8192,
                                      "available_mib": 2000})
    probe = ra.Arbiter(profile=tiny, entries=arbiter.entries)
    probe.compute_plan()
    heavy = [e for e in probe.entries if e.get("is_moe")]
    if not heavy:
        pytest.skip("no MoE model installed")
    out = probe.admit(heavy[0])
    assert out["ok"] is False
    assert out["rule_id"] == "R2"
    assert "hard ceiling" in out["explanation"]
    print("\nRAM refusal: " + out["explanation"])
