"""
Agent Friday — local image generation (Z-Image Turbo via ComfyUI).

Decision D8 held routed local image generation until a residency scheduler
existed, "with the documented evict → generate → reload sequence". That
scheduler now exists, so this is the routed path: generation runs under the
Arbiter's **exclusive image lease**, which performs the eviction and the
reload, and hands the GPU back afterwards.

Why a lease and not just "start ComfyUI": the measured VRAM ceiling makes it
mandatory rather than advisory. Z-Image's weights are ~14.5 GB against a
12282 MiB card, so the language seats must be out of VRAM before it loads.
Without the lease the two simply fight, which is the failure the residency
layer was built to stop.

The workflow is the one proven on the reference instance 2026-08-13
(`UNETLoader(fp8_e4m3fn) → CLIPLoader → VAELoader → CLIPTextEncode ×2 →
EmptySD3LatentImage → KSampler(euler/simple, 8 steps, cfg 1.0) → VAEDecode →
SaveImage`), measured at 28.10 s warm for 1024×1024.

Nothing here touches the network: this is on-device generation, so no egress
gate applies to the prompt. That is the point of it.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request
from pathlib import Path

from agent_friday.core import runtime_dir

_log = logging.getLogger("friday.local_image")

MODEL_ID = "z-image-turbo-fp8"
PROVIDER = "local-comfyui"
COMFY_PORT = 8188
DEFAULT_STEPS = 8
DEFAULT_CFG = 1.0

_SIZES = {
    "1:1": (1024, 1024),
    "16:9": (1344, 768),
    "9:16": (768, 1344),
    "4:3": (1152, 896),
    "3:4": (896, 1152),
}


def comfy_root() -> Path:
    return runtime_dir() / "ComfyUI"


def is_installed() -> bool:
    """Is the Z-Image build actually present? Availability is earned, not
    declared — the picker must not offer a model this machine cannot run."""
    root = comfy_root()
    return (root.is_dir()
            and (root / "models" / "diffusion_models"
                 / "z_image_turbo_fp8_e4m3fn.safetensors").exists()
            and (root / "models" / "text_encoders"
                 / "qwen_3_4b.safetensors").exists()
            and (root / "models" / "vae" / "ae.safetensors").exists())


def build_workflow(prompt: str, *, negative: str = "", width: int = 1024,
                   height: int = 1024, steps: int = DEFAULT_STEPS,
                   cfg: float = DEFAULT_CFG, seed: int = 0,
                   filename_prefix: str = "friday_local") -> dict:
    return {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": "z_image_turbo_fp8_e4m3fn.safetensors",
                         "weight_dtype": "fp8_e4m3fn"}},
        # `stable_diffusion` is correct and deliberate: comfy/sd.py routes a
        # detected QWEN3_4B encoder to comfy.text_encoders.z_image whenever
        # clip_type is not FLUX/FLUX2. There is no "z_image" entry in the
        # CLIPLoader type list — verified by reading the cloned source.
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": "qwen_3_4b.safetensors",
                         "type": "stable_diffusion", "device": "default"}},
        "3": {"class_type": "VAELoader",
              "inputs": {"vae_name": "ae.safetensors"}},
        "4": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["2", 0], "text": prompt}},
        "5": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["2", 0], "text": negative}},
        "6": {"class_type": "EmptySD3LatentImage",
              "inputs": {"width": width, "height": height, "batch_size": 1}},
        "7": {"class_type": "KSampler",
              "inputs": {"model": ["1", 0], "positive": ["4", 0],
                         "negative": ["5", 0], "latent_image": ["6", 0],
                         "seed": seed, "steps": steps, "cfg": cfg,
                         "sampler_name": "euler", "scheduler": "simple",
                         "denoise": 1.0}},
        "8": {"class_type": "VAEDecode",
              "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
        "9": {"class_type": "SaveImage",
              "inputs": {"images": ["8", 0],
                         "filename_prefix": filename_prefix}},
    }


def _post(path, body, timeout=60):
    req = urllib.request.Request(
        f"http://127.0.0.1:{COMFY_PORT}{path}",
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _get(path, timeout=30):
    with urllib.request.urlopen(
            f"http://127.0.0.1:{COMFY_PORT}{path}", timeout=timeout) as r:
        return json.loads(r.read().decode())


def _await_result(prompt_id, timeout=600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            hist = _get(f"/history/{prompt_id}")
        except Exception:
            hist = {}
        entry = (hist or {}).get(prompt_id)
        if entry:
            outs = (entry.get("outputs") or {})
            files = []
            for node in outs.values():
                for img in (node.get("images") or []):
                    files.append(img)
            if files:
                return files
            if entry.get("status", {}).get("completed"):
                return files
        time.sleep(1.5)
    raise TimeoutError("ComfyUI did not return a result in %ss" % timeout)


def generate(prompt: str, *, aspect_ratio: str = "1:1", negative: str = "",
             steps: int = DEFAULT_STEPS, seed: int = 0,
             arbiter=None, lease_ttl_s: int = 900) -> dict:
    """Generate one image on-device, under the Arbiter's exclusive image lease.

    Returns {status, files, model, provider, elapsed_s, ...}. Never raises.
    """
    if not is_installed():
        return {"status": "unavailable", "provider": PROVIDER,
                "reason": "Z-Image build not found under %s" % comfy_root()}

    width, height = _SIZES.get(aspect_ratio, _SIZES["1:1"])
    if arbiter is None:
        from agent_friday.services.residency_arbiter import get_arbiter
        arbiter = get_arbiter()

    lease = None
    t0 = time.time()
    try:
        if arbiter is not None:
            lease = arbiter.grant("image_job", ttl_s=lease_ttl_s)
            if not lease.get("ok"):
                # A refusal is an answer. Say which rule and stop — do not
                # start ComfyUI anyway and fight the language seats for VRAM.
                return {"status": "refused", "provider": PROVIDER,
                        "reason": lease.get("error"),
                        "rule_id": (lease.get("refused") or {}).get("rule_id")}
        else:
            # No arbiter governing this process: start ComfyUI directly, and
            # say so, because nothing is protecting the GPU in that case.
            from agent_friday.services.residency_arbiter import ComfyUIBackend
            ComfyUIBackend().start()
            _log.warning("local image: no arbiter — GPU is unmanaged for this "
                         "generation")

        wf = build_workflow(prompt, negative=negative, width=width,
                            height=height, steps=steps, seed=seed)
        sub = _post("/prompt", {"prompt": wf})
        pid = sub.get("prompt_id")
        if not pid:
            return {"status": "error", "provider": PROVIDER,
                    "reason": "ComfyUI rejected the workflow: %s" % sub}
        images = _await_result(pid)
        out_dir = comfy_root() / "output"
        files = [str(out_dir / (i.get("subfolder") or "") / i["filename"])
                 for i in images if i.get("filename")]
        return {
            "status": "ok" if files else "error",
            "provider": PROVIDER,
            "model": MODEL_ID,
            "files": files,
            "prompt": prompt,
            "width": width, "height": height, "steps": steps,
            "elapsed_s": round(time.time() - t0, 1),
            "local": True,
        }
    except Exception as e:
        return {"status": "error", "provider": PROVIDER,
                "reason": "%s: %s" % (type(e).__name__, e),
                "elapsed_s": round(time.time() - t0, 1)}
    finally:
        # The GPU goes back whatever happened. A lease that is not released is
        # the failure mode that strands the machine with no language seats.
        if lease is not None and lease.get("ok") and arbiter is not None:
            try:
                arbiter.release()
            except Exception as e:
                _log.error("local image: lease release failed: %s", e)
