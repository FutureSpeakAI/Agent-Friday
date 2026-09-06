"""
Agent Friday — local video generation (Wan 2.2 / CogVideoX via ComfyUI).

Sibling to services/local_image.py, built on the same ComfyUI process and the
same Arbiter lease — there is only ONE on-device generation backend, and it
serves both media types. That is why `generate()` below grants the Arbiter's
`"image_job"` lease kind rather than inventing a `"video_job"` one: the lease
kind names the GPU transition (evict language seats, start ComfyUI, hand the
card back after), not the media the job produces, and the Arbiter's `grant()`
only recognizes `"heavy_turn"` and `"image_job"` (residency_arbiter.py). A
`"video_job"` lease would be refused outright with "unknown lease" and no
video would ever render — this is the one thing in this module most worth not
getting wrong.

Three models, each earning its place in the picker only when its files are
really on disk (`is_installed`), same discipline as local_image:

  * wan2.2-ti2v-5b   — single GGUF diffusion model, text+image-to-video unified
  * wan2.2-14b-a14b  — TWO GGUF diffusion models (high-noise + low-noise
                       experts), sampled in two KSamplerAdvanced passes that
                       hand the latent from one expert to the other partway
                       through the schedule — that hand-off, not a bigger
                       single model, is what "14B" means for Wan 2.2.
  * cogvideox-2b     — packaging unconfirmed at the time this was written; see
                       the TODO beside COGVIDEOX_ID below.

Nothing here touches the network: on-device generation, no egress gate.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid

from agent_friday.services.local_image import (
    comfy_root, PROVIDER, COMFY_PORT, interrupt_comfy,
)

_log = logging.getLogger("friday.local_video")

WAN_5B_ID = "wan2.2-ti2v-5b"
WAN_14B_ID = "wan2.2-14b-a14b-gguf"
COGVIDEOX_ID = "cogvideox-2b"
# 14B, not 5B: on the reference 12GB card the 5B's sampling is reliable but
# its VAE decode hangs for 15+ minutes with no result, at two different clip
# lengths (81 and 29 frames) — length-independent, not just slow. 14B
# completes cleanly. Revisit once the 5B's decode issue is root-caused.
DEFAULT_MODEL_ID = WAN_14B_ID

# ── The installed video models ─────────────────────────────────────────────
#
# `files` is (subdirectory, filename), same contract as local_image.MODELS —
# a model is offered ONLY when every one of its files is on disk. Only the
# diffusion_models entries are actually GGUF-quantized; text_encoders and vae
# files are ordinary safetensors, loaded through the normal CLIPLoader/
# VAELoader rather than a GGUF-specific variant.
MODELS: dict = {
    WAN_5B_ID: {
        "label": "Wan 2.2 TI2V 5B (local video)",
        "short": "Wan 2.2 5B",
        "files": [("diffusion_models", "Wan2.2-TI2V-5B-Q8_0.gguf"),
                  ("text_encoders", "umt5_xxl_fp8_e4m3fn_scaled.safetensors"),
                  ("vae", "wan2.2_vae.safetensors")],
        "width": 832, "height": 480, "length": 81, "fps": 16.0,
        "steps": 20, "cfg": 5.0, "shift": 5.0,
        "sampler": "uni_pc", "scheduler": "simple",
        "note": "NOT currently reliable on 12GB cards — sampling completes "
                "cleanly (~9 min for 20 steps at 832x480), but VAE decode "
                "hung for 15+ minutes with no result in two separate tests "
                "(81-frame and 29-frame clips) — length-independent. Prefer "
                "the 14B GGUF until this is root-caused.",
        "licence": "Apache 2.0",
        "licence_note": "no commercial restriction",
    },
    WAN_14B_ID: {
        "label": "Wan 2.2 A14B GGUF (local video)",
        "short": "Wan 2.2 14B",
        # Two experts, not one file — high-noise handles the early/coarse
        # denoising steps, low-noise the later/detail ones.
        "files": [("diffusion_models", "Wan2.2-T2V-A14B-HighNoise-Q3_K_M.gguf"),
                  ("diffusion_models", "Wan2.2-T2V-A14B-LowNoise-Q3_K_M.gguf"),
                  # Shared with the 5B — same text encoder, not re-downloaded.
                  ("text_encoders", "umt5_xxl_fp8_e4m3fn_scaled.safetensors"),
                  ("vae", "wan_2.1_vae.safetensors")],
        "width": 832, "height": 480, "length": 81, "fps": 16.0,
        "steps": 20, "cfg": 5.0, "shift": 8.0,
        "boundary_step": 10,          # where sampling hands off high→low noise
        "sampler": "uni_pc", "scheduler": "simple",
        "note": "default video model — the only one of the two Wan tiers "
                "confirmed reliable on this hardware. Measured ~8.1 min for "
                "a short (~1.8s) clip at 832x480, ~9.2-9.7GB VRAM peak on a "
                "4070 12GB. Two-expert model, so a full 5s clip will take "
                "proportionally longer — not yet measured.",
        "licence": "Apache 2.0",
        "default": True,
    },
    # Runs through the third-party ComfyUI-CogVideoXWrapper (kijai) custom
    # node pack, not native ComfyUI — this checkout has the model/VAE
    # architecture (comfy/ldm/cogvideo/) but no native empty-latent/sampling
    # glue node for it (no "EmptyCogVideoXLatentVideo" the way Mochi/Hunyuan/
    # Cosmos each have one). Node chain and widget order verified against the
    # wrapper's own example_workflows/cogvideox_1_0_5b_T2V_02.json rather than
    # guessed: CLIPLoader(type=sd3) -> CogVideoTextEncode x2 (chained, not
    # independent — the negative node's `clip` input comes from the
    # positive node's `clip` OUTPUT) -> CogVideoXModelLoader +
    # CogVideoXVAELoader (the example uses DownloadAndLoadCogVideoModel,
    # which pulls from HuggingFace; swapped for the local-file loader pair so
    # nothing phones home) -> EmptyLatentImage -> CogVideoSampler ->
    # CogVideoDecode -> SaveWEBM (VHS_VideoCombine is the example's save node,
    # but that pack isn't installed here — SaveWEBM is the same native node
    # already proven for the Wan graphs, and CogVideoDecode's IMAGE output is
    # the same shape SaveWEBM already consumes).
    #
    # The weights are the ones the download agent actually fetched
    # (HuggingFace diffusers format), relocated: CogVideoXModelLoader and
    # CogVideoXVAELoader detect model variant from tensor shape and use their
    # own bundled configs, so only the raw transformer/VAE state-dict file
    # needs to sit in ComfyUI's model folders — the diffusers config.json
    # files were never read by anything and are not needed.
    #
    # The wrapper's own example loads a T5 encoder specifically labelled
    # "encoderonly" (`t5\google_t5-v1_1-xxl_encoderonly-fp8_e4m3fn.safetensors`)
    # via CLIPLoader type=sd3. This entry instead reuses the T5-XXL fp8
    # encoder already on disk for FLUX (comfyanonymous/flux_text_encoders) —
    # confirmed working on a real generation (measured on the reference
    # machine: 143.8s for a short clip, ~5GB VRAM peak), so the state dict
    # keys do match; no need for a second T5 download.
    COGVIDEOX_ID: {
        "label": "CogVideoX 2B (local video)",
        "short": "CogVideoX 2B",
        "files": [("diffusion_models", "CogVideoX-2b-transformer.safetensors"),
                  ("text_encoders", "t5xxl_fp8_e4m3fn.safetensors"),
                  ("vae", "CogVideoX-2b-vae.safetensors")],
        "width": 720, "height": 480, "length": 49, "fps": 8.0,
        "steps": 50, "cfg": 6.0,
        "sampler": "ddim", "scheduler": "simple",
        "precision": "fp16",           # official recommendation for the 2B
        "note": "6 seconds, fixed 480x720, 8fps — reliable but the most "
                "limited of the three video options. Measured ~2.4 min for "
                "a short (~1.6s) clip, ~5GB VRAM peak on a 4070 12GB — the "
                "lightest of the three, and it worked on the first attempt.",
        "licence": "Apache 2.0",
    },
}


def model_spec(model_id: str | None = None) -> dict:
    return MODELS.get(model_id or DEFAULT_MODEL_ID) or MODELS[DEFAULT_MODEL_ID]


# Aspect ratio -> (width, height), 480p-class — matched to what Wan 2.2 was
# actually trained/validated at. A model's own spec (width/height above) is
# the true default; this is only consulted when a caller asks for a shape
# other than the model's native one.
_SIZES = {
    "16:9": (832, 480),
    "9:16": (480, 832),
    "1:1": (624, 624),
}


# ── Cancellation — a separate flag set from local_image's, keyed by the same
# "video-" vs "image-" orb-id prefix convention routes/tasks.py already reads
# to decide which module to ask. Kept distinct (not shared with local_image's
# _CANCELLED) because the two jobs are otherwise fully independent processes
# from the caller's point of view, and merging state that never needs to be
# shared is its own source of bugs. ─────────────────────────────────────────
_CANCELLED: set = set()
_CANCEL_LOCK = threading.Lock()


class Cancelled(Exception):
    """The owner asked for this job to stop. Not an error."""


def request_cancel(job_id: str) -> bool:
    if not job_id:
        return False
    with _CANCEL_LOCK:
        _CANCELLED.add(str(job_id))
    return True


def is_cancelled(job_id) -> bool:
    if not job_id:
        return False
    with _CANCEL_LOCK:
        return str(job_id) in _CANCELLED


def clear_cancel(job_id) -> None:
    if not job_id:
        return
    with _CANCEL_LOCK:
        _CANCELLED.discard(str(job_id))


def is_installed(model_id: str | None = None) -> bool:
    """Are this model's weights actually present? Same earned-availability
    rule as local_image.is_installed — a half-downloaded model is absent, not
    offered-and-broken."""
    root = comfy_root()
    if not root.is_dir():
        return False
    spec = MODELS.get(model_id or DEFAULT_MODEL_ID)
    if not spec:
        return False
    return all((root / "models" / sub / name).exists()
               for sub, name in spec["files"])


def available_models() -> list:
    return [mid for mid in MODELS if is_installed(mid)]


def _post(path, body, timeout=60):
    import urllib.request
    req = urllib.request.Request(
        f"http://127.0.0.1:{COMFY_PORT}{path}",
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _get(path, timeout=30):
    import urllib.request
    with urllib.request.urlopen(
            f"http://127.0.0.1:{COMFY_PORT}{path}", timeout=timeout) as r:
        return json.loads(r.read().decode())


def build_workflow(prompt: str, *, negative: str = "", width: int = 0,
                   height: int = 0, length: int = 0, fps: float = 0,
                   steps: int = 0, cfg: float = 0, seed: int = 0,
                   filename_prefix: str = "friday_local_video",
                   model_id: str | None = None) -> dict:
    """The ComfyUI graph for one clip, on whichever video model is seated.

    All three end at the same pair of nodes — VAEDecode → SaveWEBM — because
    ComfyUI's video output node reports through the SAME "images" key ordinary
    SaveImage uses (verified by reading comfy_api/latest/_ui.py:
    PreviewVideo.as_dict() returns `{"images": self.values, "animated":
    (True,)}` — there is no separate "gifs"/"videos" history key in this
    checkout, despite that being the more common assumption). They differ
    upstream: 5B is one model, 14B is two sampled in sequence, CogVideoX is
    architecturally unrelated to either.
    """
    spec = model_spec(model_id)
    mid = model_id or DEFAULT_MODEL_ID
    width = width or spec["width"]
    height = height or spec["height"]
    length = length or spec["length"]
    fps = fps or spec["fps"]
    steps = steps or spec["steps"]
    cfg = cfg or spec["cfg"]
    if mid == WAN_14B_ID:
        return _wan_a14b_workflow(prompt, negative=negative, width=width,
                                  height=height, length=length, fps=fps,
                                  steps=steps, cfg=cfg, seed=seed,
                                  filename_prefix=filename_prefix, spec=spec)
    if mid == COGVIDEOX_ID:
        return _cogvideox_workflow(prompt, negative=negative, width=width,
                                   height=height, length=length, fps=fps,
                                   steps=steps, cfg=cfg, seed=seed,
                                   filename_prefix=filename_prefix, spec=spec)
    return _wan_5b_workflow(prompt, negative=negative, width=width,
                            height=height, length=length, fps=fps,
                            steps=steps, cfg=cfg, seed=seed,
                            filename_prefix=filename_prefix, spec=spec)


def _wan_5b_workflow(prompt, *, negative, width, height, length, fps, steps,
                     cfg, seed, filename_prefix, spec) -> dict:
    """Wan 2.2 TI2V 5B, text-to-video (no start image).

    `WanImageToVideo` (comfy_extras/nodes_wan.py) is the same node the image-
    to-video graph uses; leaving `start_image` unconnected is what makes this
    text-to-video rather than a second, redundant node type. `ModelSamplingSD3`
    applies the shift Wan's reference workflow calls for — feeding the
    unpatched model into KSampler produces a noticeably worse motion result.
    """
    unet_name = spec["files"][0][1]
    clip_name = spec["files"][1][1]
    vae_name = spec["files"][2][1]
    return {
        "1": {"class_type": "UnetLoaderGGUF",
              "inputs": {"unet_name": unet_name}},
        "1b": {"class_type": "ModelSamplingSD3",
               "inputs": {"model": ["1", 0], "shift": spec.get("shift", 5.0)}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": clip_name, "type": "wan",
                         "device": "default"}},
        "3": {"class_type": "VAELoader",
              "inputs": {"vae_name": vae_name}},
        "4": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["2", 0], "text": prompt}},
        "5": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["2", 0], "text": negative}},
        "6": {"class_type": "WanImageToVideo",
              "inputs": {"positive": ["4", 0], "negative": ["5", 0],
                         "vae": ["3", 0], "width": width, "height": height,
                         "length": length, "batch_size": 1}},
        "7": {"class_type": "KSampler",
              "inputs": {"model": ["1b", 0], "positive": ["6", 0],
                         "negative": ["6", 1], "latent_image": ["6", 2],
                         "seed": seed, "steps": steps, "cfg": cfg,
                         "sampler_name": spec.get("sampler", "uni_pc"),
                         "scheduler": spec.get("scheduler", "simple"),
                         "denoise": 1.0}},
        "8": {"class_type": "VAEDecode",
              "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
        "9": {"class_type": "SaveWEBM",
              "inputs": {"images": ["8", 0], "filename_prefix": filename_prefix,
                         "codec": "vp9", "fps": fps, "crf": 24.0}},
    }


def _wan_a14b_workflow(prompt, *, negative, width, height, length, fps, steps,
                       cfg, seed, filename_prefix, spec) -> dict:
    """Wan 2.2 A14B — two experts, one sampling pass each, latent handed off
    between them at `boundary_step`. This is the published MoE structure of
    Wan 2.2's 14B tier: the high-noise expert denoises the coarse/early steps,
    the low-noise expert refines the rest. `KSamplerAdvanced` with
    `add_noise=disable` on the second stage is what makes it a CONTINUATION of
    the first stage's latent rather than a second, independent denoise.
    """
    unet_high, unet_low = spec["files"][0][1], spec["files"][1][1]
    clip_name = spec["files"][2][1]
    vae_name = spec["files"][3][1]
    boundary = spec.get("boundary_step", max(1, steps // 2))
    return {
        "1h": {"class_type": "UnetLoaderGGUF",
               "inputs": {"unet_name": unet_high}},
        "1hb": {"class_type": "ModelSamplingSD3",
                "inputs": {"model": ["1h", 0], "shift": spec.get("shift", 8.0)}},
        "1l": {"class_type": "UnetLoaderGGUF",
               "inputs": {"unet_name": unet_low}},
        "1lb": {"class_type": "ModelSamplingSD3",
                "inputs": {"model": ["1l", 0], "shift": spec.get("shift", 8.0)}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": clip_name, "type": "wan",
                         "device": "default"}},
        "3": {"class_type": "VAELoader",
              "inputs": {"vae_name": vae_name}},
        "4": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["2", 0], "text": prompt}},
        "5": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["2", 0], "text": negative}},
        "6": {"class_type": "WanImageToVideo",
              "inputs": {"positive": ["4", 0], "negative": ["5", 0],
                         "vae": ["3", 0], "width": width, "height": height,
                         "length": length, "batch_size": 1}},
        # Stage 1: high-noise expert, steps [0, boundary), noise added.
        "7h": {"class_type": "KSamplerAdvanced",
               "inputs": {"model": ["1hb", 0], "positive": ["6", 0],
                          "negative": ["6", 1], "latent_image": ["6", 2],
                          "add_noise": "enable", "noise_seed": seed,
                          "steps": steps, "cfg": cfg,
                          "sampler_name": spec.get("sampler", "uni_pc"),
                          "scheduler": spec.get("scheduler", "simple"),
                          "start_at_step": 0, "end_at_step": boundary,
                          "return_with_leftover_noise": "enable"}},
        # Stage 2: low-noise expert continues from stage 1's latent, no new
        # noise injected, to the end of the schedule.
        "7l": {"class_type": "KSamplerAdvanced",
               "inputs": {"model": ["1lb", 0], "positive": ["6", 0],
                          "negative": ["6", 1], "latent_image": ["7h", 0],
                          "add_noise": "disable", "noise_seed": seed,
                          "steps": steps, "cfg": cfg,
                          "sampler_name": spec.get("sampler", "uni_pc"),
                          "scheduler": spec.get("scheduler", "simple"),
                          "start_at_step": boundary, "end_at_step": 10000,
                          "return_with_leftover_noise": "disable"}},
        "8": {"class_type": "VAEDecode",
              "inputs": {"samples": ["7l", 0], "vae": ["3", 0]}},
        "9": {"class_type": "SaveWEBM",
              "inputs": {"images": ["8", 0], "filename_prefix": filename_prefix,
                         "codec": "vp9", "fps": fps, "crf": 24.0}},
    }


def _cogvideox_workflow(prompt, *, negative, width, height, length, fps,
                        steps, cfg, seed, filename_prefix, spec) -> dict:
    """CogVideoX-2b, via the third-party ComfyUI-CogVideoXWrapper (kijai)
    custom node pack — see the long comment beside COGVIDEOX_ID in MODELS for
    what was verified against the wrapper's own example workflow and what
    remains a first-test unknown (the T5 encoder file reuse).
    """
    unet_name = spec["files"][0][1]
    clip_name = spec["files"][1][1]
    vae_name = spec["files"][2][1]
    precision = spec.get("precision", "fp16")
    return {
        "1": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": clip_name, "type": "sd3",
                         "device": "default"}},
        # Chained, not independent — matches the reference workflow: the
        # negative encode's `clip` input is the positive encode's `clip`
        # OUTPUT, so the text encoder loads once and offloads once.
        "2": {"class_type": "CogVideoTextEncode",
              "inputs": {"clip": ["1", 0], "prompt": prompt,
                         "strength": 1.0, "force_offload": False}},
        "3": {"class_type": "CogVideoTextEncode",
              "inputs": {"clip": ["2", 1], "prompt": negative,
                         "strength": 1.0, "force_offload": True}},
        "4": {"class_type": "CogVideoXModelLoader",
              "inputs": {"model": unet_name, "base_precision": precision,
                         "quantization": "disabled",
                         "load_device": "main_device",
                         "enable_sequential_cpu_offload": False}},
        "5": {"class_type": "CogVideoXVAELoader",
              "inputs": {"model_name": vae_name, "precision": precision}},
        "6": {"class_type": "EmptyLatentImage",
              "inputs": {"width": width, "height": height, "batch_size": 1}},
        "7": {"class_type": "CogVideoSampler",
              "inputs": {"model": ["4", 0], "positive": ["2", 0],
                         "negative": ["3", 0], "samples": ["6", 0],
                         "num_frames": length, "steps": steps, "cfg": cfg,
                         "seed": seed,
                         "scheduler": spec.get("cog_scheduler",
                                               "CogVideoXDDIM")}},
        "8": {"class_type": "CogVideoDecode",
              "inputs": {"vae": ["5", 0], "samples": ["7", 0],
                         "enable_vae_tiling": True,
                         "tile_sample_min_height": 240,
                         "tile_sample_min_width": 360,
                         "tile_overlap_factor_height": 0.2,
                         "tile_overlap_factor_width": 0.2,
                         "auto_tile_size": True}},
        "9": {"class_type": "SaveWEBM",
              "inputs": {"images": ["8", 0], "filename_prefix": filename_prefix,
                         "codec": "vp9", "fps": fps, "crf": 24.0}},
    }


def _await_result(prompt_id, timeout=1800, cancelled=None):
    """Wait for the clip to appear, or for the prompt to stop without one.

    Same shape as local_image._await_result (same reasoning: an INTERRUPTED
    prompt finishes with no outputs and must not be mistaken for "still
    running"). The output key really is "images" here too — SaveWEBM's
    `PreviewVideo` UI wrapper reports under that key
    (comfy_api/latest/_ui.py), not a video-specific one — but this also
    checks "gifs"/"videos" defensively in case a differently-configured save
    node (or a future ComfyUI version) reports under one of those instead.
    Video renders run far longer than a picture, hence the longer timeout.
    """
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cancelled is not None and cancelled():
            interrupt_comfy()
            raise Cancelled("cancelled while waiting for the video")
        try:
            hist = _get(f"/history/{prompt_id}")
        except Exception:
            hist = {}
        entry = (hist or {}).get(prompt_id)
        if entry:
            outs = (entry.get("outputs") or {})
            files = []
            for node in outs.values():
                for key in ("images", "gifs", "videos"):
                    for f in (node.get(key) or []):
                        files.append(f)
            if files:
                return files
            status = entry.get("status") or {}
            if status:
                msgs = " ".join(str(m) for m in (status.get("messages") or []))
                if status.get("completed"):
                    return files
                if "interrupt" in msgs.lower():
                    raise Cancelled("ComfyUI reported the prompt was interrupted")
                raise RuntimeError(
                    "ComfyUI ended the prompt without producing a video: %s"
                    % (status.get("status_str") or "unknown"))
        time.sleep(2.0)
    raise TimeoutError("ComfyUI did not return a result in %ss" % timeout)


# Node id -> the phase a human would call it. Node ids vary between the
# single-model and two-expert graphs, so this maps every id build_workflow()
# ever emits — lookups for the wrong graph's ids simply miss and fall back to
# "working" in _watch_progress below, they never raise.
_PHASES = {
    "1": "loading the model", "1b": "preparing the model",
    "1h": "loading the high-noise model", "1hb": "preparing the high-noise model",
    "1l": "loading the low-noise model", "1lb": "preparing the low-noise model",
    "2": "loading the text encoder", "3": "loading the decoder",
    "4": "reading your prompt", "5": "reading your prompt",
    "6": "preparing the clip", "7": "sampling",
    "7h": "sampling (high-noise pass)", "7l": "sampling (low-noise pass)",
    "8": "decoding the video", "9": "saving",
}


def _watch_progress(prompt_id, client_id, on_update, stop_flag):
    """Consume ComfyUI's websocket for real progress. Identical mechanics to
    local_image._watch_progress (see that function's docstring for the
    read-timeout-vs-connect-timeout bug this avoids) — kept as a separate copy
    rather than a shared import because _PHASES differs per module and the
    function closes over it by name."""
    try:
        import websocket  # websocket-client
        from websocket import WebSocketTimeoutException
    except Exception:
        return
    url = "ws://127.0.0.1:%d/ws?clientId=%s" % (COMFY_PORT, client_id)
    ws = None
    try:
        ws = websocket.create_connection(url, timeout=15)
        ws.settimeout(2)
        while not stop_flag():
            try:
                raw = ws.recv()
            except WebSocketTimeoutException:
                continue
            except Exception:
                break
            if not raw or isinstance(raw, (bytes, bytearray)):
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            mtype = msg.get("type")
            data = msg.get("data") or {}
            if data.get("prompt_id") and data["prompt_id"] != prompt_id:
                continue
            if mtype == "progress":
                value, mx = data.get("value") or 0, data.get("max") or 0
                if mx:
                    on_update(step=value, steps=mx)
            elif mtype == "progress_state":
                for _nid, st in (data.get("nodes") or {}).items():
                    if st.get("state") == "running" and st.get("max"):
                        on_update(step=st.get("value") or 0, steps=st["max"],
                                  phase=_PHASES.get(str(_nid)))
                        break
            elif mtype == "executing":
                node = data.get("node")
                if node is None:
                    break
                on_update(phase=_PHASES.get(str(node), "working"))
    except Exception:
        pass
    finally:
        try:
            if ws:
                ws.close()
        except Exception:
            pass


def generate(prompt: str, *, aspect_ratio: str = "16:9", negative: str = "",
             duration_seconds: int = 0, seed: int = 0,
             arbiter=None, lease_ttl_s: int = 1800,
             system: bool = False, model: str | None = None) -> dict:
    """Generate one video clip on-device, under the Arbiter's exclusive image
    lease — see the module docstring for why it is "image_job" and not a
    video-specific kind. Returns the SAME envelope shape as local_image's
    generate(): {status, files, model, provider, elapsed_s, ...}. Never raises.
    """
    _model_id = model or DEFAULT_MODEL_ID
    if not is_installed(_model_id):
        _model_id = DEFAULT_MODEL_ID
    if not is_installed(_model_id):
        return {"status": "unavailable", "provider": PROVIDER,
                "reason": "no local video model found under %s" % comfy_root()}

    if not (prompt or "").strip():
        return {"status": "error", "provider": PROVIDER,
                "reason": "no prompt — refusing to generate."}

    _spec = model_spec(_model_id)
    width, height = _spec["width"], _spec["height"]
    # CogVideoX-2b's note says "fixed 480x720" and means it — an aspect-ratio
    # override would silently contradict what the picker told the user to
    # expect, so only the Wan models (whose note makes no such promise) honour
    # one.
    if (_model_id != COGVIDEOX_ID and aspect_ratio in _SIZES
            and aspect_ratio != "16:9"):
        width, height = _SIZES[aspect_ratio]
    length, fps = _spec["length"], _spec["fps"]
    if duration_seconds:
        try:
            # Wan's temporal VAE wants (length - 1) % 4 == 0 — round to the
            # nearest frame count that satisfies it rather than handing the
            # sampler a shape it will reject.
            wanted = max(1, int(round(duration_seconds * fps)))
            length = ((wanted - 1) // 4) * 4 + 1
        except (TypeError, ValueError):
            pass

    if arbiter is None:
        from agent_friday.services.residency_arbiter import get_arbiter
        arbiter = get_arbiter()

    lease = None
    t0 = time.time()
    try:
        from agent_friday.services import pause_forecast as _pf
        _eta_s = int((_pf.before_image() or {}).get("seconds") or 0)
    except Exception:
        _eta_s = 0

    orb_pid = None
    try:
        from agent_friday.core import process_register
        orb_pid = "video-%s" % uuid.uuid4().hex[:8]
        _short = (prompt or "").strip().replace("\n", " ")[:38]
        process_register(orb_pid, name="Video",
                         label="Video: %s%s" % (_short,
                                                "…" if len(prompt or "") > 38 else ""),
                         category="monitoring", icon="🎬", steps=[],
                         model=_model_id, eta_s=_eta_s or None)
    except Exception:
        orb_pid = None

    def _orb(**kw):
        if orb_pid:
            try:
                from agent_friday.core import process_update as _pu
                _pu(orb_pid, **kw)
            except Exception:
                pass

    def _cancelled() -> bool:
        return is_cancelled(orb_pid)

    _outcome = {"status": "ok"}

    try:
        if _cancelled():
            raise Cancelled("cancelled before the GPU was taken")
        if arbiter is not None:
            # "image_job" is deliberate — see module docstring.
            lease = arbiter.grant("image_job", ttl_s=lease_ttl_s)
            if not lease.get("ok"):
                return {"status": "refused", "provider": PROVIDER,
                        "reason": lease.get("error"),
                        "rule_id": (lease.get("refused") or {}).get("rule_id")}
        else:
            from agent_friday.services.residency_arbiter import ComfyUIBackend
            ComfyUIBackend().start()
            _log.warning("local video: no arbiter — GPU is unmanaged for this "
                         "generation")

        if _cancelled():
            raise Cancelled("cancelled while the GPU was being prepared")

        _seed = seed if seed else uuid.uuid4().int % (2 ** 63)
        wf = build_workflow(prompt, negative=negative, width=width,
                            height=height, length=length, fps=fps, seed=_seed,
                            model_id=_model_id)
        client_id = uuid.uuid4().hex[:12]
        sub = _post("/prompt", {"prompt": wf, "client_id": client_id})
        pid = sub.get("prompt_id")
        if not pid:
            return {"status": "error", "provider": PROVIDER,
                    "reason": "ComfyUI rejected the workflow: %s" % sub}

        _state = {"stop": False, "phase": "starting", "step": 0,
                  "steps": _spec["steps"]}

        def _on_update(step=None, steps=None, phase=None):
            if step is not None:
                _state["step"] = step
            if steps:
                _state["steps"] = steps
            if phase:
                _state["phase"] = phase
            st, mx = _state["step"], max(1, _state["steps"])
            frac = 0.1 + 0.85 * (st / mx) if "sampling" in _state["phase"] \
                else (0.05 if st == 0 else 0.95)
            frac = max(frac, _state.get("floor", 0.0))
            _state["floor"] = frac
            label = "Video: %s" % _state["phase"]
            if "sampling" in _state["phase"] and mx:
                label = "Video: %s, step %d of %d" % (_state["phase"], st, mx)
            _orb(progress=round(min(frac, 0.97), 3), label=label,
                 step={"type": "phase", "name": _state["phase"], "step": st,
                       "steps": mx, "ts": time.time()})

        _watcher = threading.Thread(
            target=_watch_progress,
            args=(pid, client_id, _on_update, lambda: _state["stop"]),
            daemon=True)
        _watcher.start()
        try:
            outs = _await_result(pid, cancelled=_cancelled)
        finally:
            _state["stop"] = True

        out_dir = comfy_root() / "output"
        import shutil as _shutil

        from agent_friday.core import CREATIONS_DIR
        files = []
        for i in outs:
            if not i.get("filename"):
                continue
            src = out_dir / (i.get("subfolder") or "") / i["filename"]
            dest = src
            if system:
                files.append({"filename": i["filename"], "path": str(src),
                              "url": None, "source_path": str(src),
                              "system_generated": True})
                continue
            try:
                CREATIONS_DIR.mkdir(parents=True, exist_ok=True)
                dest = CREATIONS_DIR / i["filename"]
                if src.resolve() != dest.resolve():
                    _shutil.copy2(src, dest)
            except Exception as _cp:
                _log.warning("local video: could not publish %s to the "
                             "gallery: %s", i["filename"], _cp)
            files.append({"filename": i["filename"], "path": str(dest),
                          "url": "/api/creations/%s" % i["filename"],
                          "source_path": str(src)})

        try:
            _man = CREATIONS_DIR / "creations-manifest.jsonl"
            with open(_man, "a", encoding="utf-8") as _mf:
                for _f in files:
                    _mf.write(json.dumps({
                        "filename": _f.get("filename"),
                        "path": _f.get("path"),
                        "prompt": prompt,
                        "model": _model_id,
                        "width": width, "height": height,
                        "length": length, "fps": fps,
                        "created_at": time.time(),
                        "system_generated": bool(system),
                    }) + "\n")
        except Exception as _me:
            _log.warning("local video: could not record the manifest entry: %s",
                         _me)

        return {
            "status": "ok" if files else "error",
            "provider": PROVIDER,
            "model": _model_id,
            "files": files,
            "prompt": prompt,
            "width": width, "height": height, "length": length, "fps": fps,
            "elapsed_s": round(time.time() - t0, 1),
            "local": True,
        }
    except Cancelled as c:
        _outcome["status"] = "cancelled"
        return {"status": "cancelled", "provider": PROVIDER, "model": _model_id,
                "reason": str(c), "prompt": prompt, "files": [],
                "elapsed_s": round(time.time() - t0, 1), "local": True}
    except Exception as e:
        _outcome["status"] = "failed"
        return {"status": "error", "provider": PROVIDER,
                "reason": "%s: %s" % (type(e).__name__, e),
                "elapsed_s": round(time.time() - t0, 1)}
    finally:
        if _outcome["status"] == "cancelled":
            _orb(status='cancelled', label="Video: cancelled")
        elif _outcome["status"] == "failed":
            _orb(status='failed')
        else:
            _orb(status='completed', progress=1.0)
        clear_cancel(orb_pid)
        if lease is not None and lease.get("ok") and arbiter is not None:
            try:
                arbiter.release()
            except Exception as e:
                _log.error("local video: lease release failed: %s", e)
