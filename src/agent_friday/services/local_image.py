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
import threading
import time
import uuid
import urllib.request
from pathlib import Path

from agent_friday.core import CREATIONS_DIR, runtime_dir

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


# ── Cancellation ───────────────────────────────────────────────────────────
#
# A cancel used to depend on the Arbiter's lease already existing, and the orb
# is registered BEFORE the lease is granted. Anything arriving in that window
# found no lease, found ComfyUI not yet started, reported "nothing to
# interrupt" — and the job ran to completion anyway. The lease is the wrong
# thing to hang cancellation on: it is one stage of a job with several.
#
# So cancellation is a flag on the JOB, set the instant it is asked for and
# readable from the first line of `generate` to the last. The route sets it;
# every stage checks it. It works before the lease exists, while ComfyUI is
# still loading, and mid-sample.
_CANCELLED: set = set()
_CANCEL_LOCK = threading.Lock()


class Cancelled(Exception):
    """The owner asked for this job to stop. Not an error."""


def request_cancel(job_id: str) -> bool:
    """Mark a job cancelled. Safe to call before the job has really begun."""
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


def interrupt_comfy(timeout: int = 10) -> bool:
    """Ask ComfyUI to abandon the running prompt. False if it could not be
    reached — which is normal when the job has not got that far yet."""
    try:
        urllib.request.urlopen(urllib.request.Request(
            "http://127.0.0.1:%d/interrupt" % COMFY_PORT, data=b"{}",
            headers={"Content-Type": "application/json"}), timeout=timeout)
        return True
    except Exception:
        return False


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


def _await_result(prompt_id, timeout=600, cancelled=None):
    """Wait for the prompt to produce files, or to stop producing anything.

    Two things this got wrong before 2026-08-16, both of which turned a cancel
    into a ten-minute hang:

    1. It only returned on outputs or on `status.completed`. ComfyUI writes a
       history entry ONLY when the prompt has finished (`task_done`, verified in
       execution.py) — and an INTERRUPTED prompt finishes with
       `status_str='error', completed=False` and no outputs. That is precisely
       the one shape the loop treated as "not done yet", so it kept polling a
       prompt that had already stopped, until the 600s timeout.
    2. It had no idea cancellation existed, so the only way out was to wait.

    Now: a finished-without-output entry ends the wait, and the cancel flag is
    checked every tick and actually interrupts ComfyUI rather than just noting
    that someone would like it to stop.
    """
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cancelled is not None and cancelled():
            interrupt_comfy()
            raise Cancelled("cancelled while waiting for the image")
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
            status = entry.get("status") or {}
            if status:
                # The entry exists at all, so execution is over. No outputs
                # means it was interrupted or it failed — either way, stop
                # waiting and say which.
                msgs = " ".join(str(m) for m in (status.get("messages") or []))
                if status.get("completed"):
                    return files
                if "interrupt" in msgs.lower():
                    raise Cancelled("ComfyUI reported the prompt was interrupted")
                raise RuntimeError(
                    "ComfyUI ended the prompt without producing an image: %s"
                    % (status.get("status_str") or "unknown"))
        time.sleep(1.5)
    raise TimeoutError("ComfyUI did not return a result in %ss" % timeout)


# Node id -> the phase a human would call it. The workflow in build_workflow()
# is fixed, so this is a lookup rather than a guess.
_PHASES = {
    "1": "loading the model",
    "2": "loading the text encoder",
    "3": "loading the decoder",
    "4": "reading your prompt",
    "5": "reading your prompt",
    "6": "preparing the canvas",
    "7": "sampling",
    "8": "decoding the image",
    "9": "saving",
}


def _watch_progress(prompt_id, client_id, on_update, stop_flag):
    """Consume ComfyUI's websocket and report true progress.

    The bar was previously two values — 0.5 when sampling started and 1.0 at
    the end — because `_await_result` polls /history every 1.5s and history
    only knows "not done" and "done". Meanwhile ComfyUI has been emitting
    `progress` (step value/max) and `executing` (which node is running) the
    whole time on a socket nothing connected to. Stephen watched a bar that
    could not tell him anything, and the signal to make it true was already
    arriving.

    Runs on its own thread and never raises into the caller: a lost progress
    socket must not fail a generation that is otherwise fine.

    **The bug that made the first version of this useless.** It called
    `create_connection(url, timeout=10)`, believing that to be a CONNECT
    timeout. It is not: websocket-client passes it to `socket.settimeout`, so it
    became the timeout on every subsequent `recv()`, and an idle read raises
    `WebSocketTimeoutException`. The loop caught that with a bare
    `except Exception: break` and the thread quietly died.

    The sequence was: connect, receive `executing` for node 1 (UNETLoader),
    then ComfyUI goes silent for as long as it takes to load 14.5 GB of fp8
    weights — far more than ten seconds — and the listener was gone before a
    single sampling step was reported. Every `progress` message ComfyUI sent
    after that went to nobody. The symptom (one phase label, zero steps, a bar
    that jumped 0 → 10% → 100%) looked like ComfyUI was not sending progress.
    It was sending it the whole time.

    So: silence is not failure. A read timeout means "still working" and is the
    normal state of this socket during a model load. It is kept short only so
    that `stop_flag` is honoured promptly when the job ends.
    """
    try:
        import websocket  # websocket-client
        from websocket import WebSocketTimeoutException
    except Exception:
        return
    url = "ws://127.0.0.1:%d/ws?clientId=%s" % (COMFY_PORT, client_id)
    ws = None
    try:
        ws = websocket.create_connection(url, timeout=15)
        ws.settimeout(2)          # a poll interval, NOT a deadline
        while not stop_flag():
            try:
                raw = ws.recv()
            except WebSocketTimeoutException:
                continue          # silence — a model is loading, keep listening
            except Exception:
                break             # closed or genuinely broken
            if not raw or isinstance(raw, (bytes, bytearray)):
                continue          # binary frames are preview images, not status
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            mtype = msg.get("type")
            data = msg.get("data") or {}
            # Messages carry the prompt they belong to. Ignore anyone else's.
            if data.get("prompt_id") and data["prompt_id"] != prompt_id:
                continue
            if mtype == "progress":
                value, mx = data.get("value") or 0, data.get("max") or 0
                if mx:
                    on_update(step=value, steps=mx)
            elif mtype == "progress_state":
                # ComfyUI 0.33 emits this alongside the legacy `progress`. Take
                # whichever node is actually running, so this keeps working if
                # the legacy hook is ever dropped upstream.
                for _nid, st in (data.get("nodes") or {}).items():
                    if st.get("state") == "running" and st.get("max"):
                        on_update(step=st.get("value") or 0, steps=st["max"],
                                  phase=_PHASES.get(str(_nid)))
                        break
            elif mtype == "executing":
                node = data.get("node")
                if node is None:
                    break         # null node == this prompt is finished
                on_update(phase=_PHASES.get(str(node), "working"))
    except Exception:
        pass
    finally:
        try:
            if ws:
                ws.close()
        except Exception:
            pass


def generate(prompt: str, *, aspect_ratio: str = "1:1", negative: str = "",
             steps: int = DEFAULT_STEPS, seed: int = 0,
             arbiter=None, lease_ttl_s: int = 900,
             system: bool = False) -> dict:
    """Generate one image on-device, under the Arbiter's exclusive image lease.

    Returns {status, files, model, provider, elapsed_s, ...}. Never raises.

    `system=True` marks a generation Friday made for her own purposes —
    verification, diagnostics, a smoke test. Those are NOT published to the
    creations gallery and are flagged in the manifest, because test output
    landing in Stephen's gallery is indistinguishable from his own work and he
    had to go and delete mine by hand.
    """
    if not is_installed():
        return {"status": "unavailable", "provider": PROVIDER,
                "reason": "Z-Image build not found under %s" % comfy_root()}

    # An empty prompt is not a request, and the pipeline could not tell.
    #
    # build_workflow("") produces a perfectly VALID graph whose positive
    # CLIPTextEncode carries "" — so ComfyUI renders whatever a diffusion model
    # produces from no conditioning, saves it, and the caller gets
    # {"status": "ok"} with a file. Success reported for output nobody asked
    # for. That is how a generation can silently emit garbage that looks like
    # it worked, and it would leave Friday unable to say what she had made
    # because there was nothing to say.
    if not (prompt or "").strip():
        return {"status": "error", "provider": PROVIDER,
                "reason": "no prompt — refusing to generate. An empty prompt "
                          "renders successfully and produces something nobody "
                          "asked for, so it is rejected here rather than "
                          "reported as a success."}

    width, height = _SIZES.get(aspect_ratio, _SIZES["1:1"])
    if arbiter is None:
        from agent_friday.services.residency_arbiter import get_arbiter
        arbiter = get_arbiter()

    lease = None
    t0 = time.time()
    # What to expect, before it starts. A warning without a number is just an
    # apology — the pause forecast already knows this job costs ~93s warm and
    # ~180s from a cold ComfyUI, measured on this machine.
    try:
        from agent_friday.services import pause_forecast as _pf
        _fc = _pf.before_image()
        _eta_s = int(_fc.get("seconds") or 0)
    except Exception:
        _eta_s = 0
    # An orb for the IMAGE job, naming the image model.
    #
    # There was none. Stephen asked for an image and saw "a gemma4 process orb
    # instead of the stable diffusion orb" — because the only orb in flight was
    # the language model's chat turn, correctly badged with the model that was
    # running the conversation. The picture was being made by z-image-turbo-fp8
    # with nothing on screen to say so.
    #
    # It also carries a description rather than a status word, per the orb work:
    # "Image: santa clause riding a polar bear…" says what it IS.
    orb_pid = None
    try:
        from agent_friday.core import process_register, process_update
        orb_pid = "image-%s" % uuid.uuid4().hex[:8]
        _short = (prompt or "").strip().replace("\n", " ")[:38]
        process_register(orb_pid, name="Image",
                         label="Image: %s%s" % (_short,
                                                "…" if len(prompt or "") > 38 else ""),
                         category="monitoring", icon="🎨", steps=[],
                         model=MODEL_ID, eta_s=_eta_s or None)
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
        # Before the lease. This is the window the old cancel could not reach:
        # the orb exists and is on screen, so it can be cancelled, but nothing
        # had been started yet for a lease-based cancel to find.
        if _cancelled():
            raise Cancelled("cancelled before the GPU was taken")
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

        # Taking the lease evicts the language seats and starts ComfyUI, which
        # is the slowest part of the job — a cancel very often arrives during
        # exactly this. Give the GPU straight back rather than generating an
        # image nobody is waiting for any more.
        if _cancelled():
            raise Cancelled("cancelled while the GPU was being prepared")

        wf = build_workflow(prompt, negative=negative, width=width,
                            height=height, steps=steps, seed=seed)
        client_id = uuid.uuid4().hex[:12]
        sub = _post("/prompt", {"prompt": wf, "client_id": client_id})
        pid = sub.get("prompt_id")
        if not pid:
            return {"status": "error", "provider": PROVIDER,
                    "reason": "ComfyUI rejected the workflow: %s" % sub}

        # Real progress, from the socket, on its own thread.
        _state = {"stop": False, "phase": "starting", "step": 0, "steps": steps}

        def _on_update(step=None, steps=None, phase=None):
            if step is not None:
                _state["step"] = step
            if steps:
                _state["steps"] = steps
            if phase:
                _state["phase"] = phase
            st, mx = _state["step"], max(1, _state["steps"])
            # Sampling is the long pole but not the whole job, so it maps onto
            # the middle of the bar rather than all of it. A bar that hits 100%
            # and then keeps going is worse than no bar.
            frac = 0.15 + 0.7 * (st / mx) if _state["phase"] == "sampling" \
                else (0.1 if st == 0 else 0.9)
            label = "Image: %s" % _state["phase"]
            if _state["phase"] == "sampling" and mx:
                label = "Image: sampling, step %d of %d" % (st, mx)
            _orb(progress=round(min(frac, 0.97), 3), label=label,
                 step={"type": "phase", "name": _state["phase"],
                       "step": st, "steps": mx, "ts": time.time()})

        _watcher = threading.Thread(
            target=_watch_progress,
            args=(pid, client_id, _on_update, lambda: _state["stop"]),
            daemon=True)
        _watcher.start()
        try:
            images = _await_result(pid, cancelled=_cancelled)
        finally:
            _state["stop"] = True
        out_dir = comfy_root() / "output"
        # The SAME envelope the cloud path returns: a list of dicts with
        # filename/path/url, not bare path strings.
        #
        # This returned `files: ["C:\\...\\x.png"]` until 2026-08-15, and
        # every caller does `result['files'][0].get('filename')` —
        # routes/creations._flatten_first_file among them, which raised
        # `'str' object has no attribute 'get'` and turned a SUCCESSFUL
        # 108-second generation into an HTTP 500. The image was on disk; the
        # envelope was the wrong shape. A local path that returns a different
        # contract from the cloud path is not a local path, it is a second
        # code path pretending to be one.
        #
        # The file is also COPIED into the creations directory, because that
        # is the only place `/api/creations/<filename>` serves from. A picture
        # left in ComfyUI's own output folder exists but cannot be looked at,
        # which is its own quiet way of not working.
        import shutil as _shutil

        from agent_friday.core import CREATIONS_DIR
        files = []
        for i in images:
            if not i.get("filename"):
                continue
            src = out_dir / (i.get("subfolder") or "") / i["filename"]
            dest = src
            if system:
                # Left where ComfyUI wrote it, deliberately. The gallery lists
                # CREATIONS_DIR, so not copying is what keeps Friday's own test
                # output out of Stephen's work.
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
                _log.warning("local image: could not publish %s to the "
                             "gallery: %s", i["filename"], _cp)
            files.append({"filename": i["filename"], "path": str(dest),
                          "url": "/api/creations/%s" % i["filename"],
                          "source_path": str(src)})
        # A manifest of what was made and from what. Friday could generate an
        # image and then be unable to tell Stephen its filename — she guessed
        # at a naming convention instead, and was wrong. ComfyUI chooses the
        # name, so the only reliable record is the one written here at the
        # moment the file lands.
        try:
            _man = CREATIONS_DIR / "creations-manifest.jsonl"
            with open(_man, "a", encoding="utf-8") as _mf:
                for _f in files:
                    _mf.write(json.dumps({
                        "filename": _f.get("filename"),
                        "path": _f.get("path"),
                        "prompt": prompt,
                        "model": MODEL_ID,
                        "width": width, "height": height, "steps": steps,
                        "created_at": time.time(),
                        "system_generated": bool(system),
                    }) + "\n")
        except Exception as _me:
            _log.warning("local image: could not record the manifest entry: %s",
                         _me)

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
    except Cancelled as c:
        # Cancellation is an outcome, not a failure. It must not be reported as
        # an error and must not be reported as a finished image.
        _outcome["status"] = "cancelled"
        return {"status": "cancelled", "provider": PROVIDER, "model": MODEL_ID,
                "reason": str(c), "prompt": prompt, "files": [],
                "elapsed_s": round(time.time() - t0, 1), "local": True}
    except Exception as e:
        _outcome["status"] = "failed"
        return {"status": "error", "provider": PROVIDER,
                "reason": "%s: %s" % (type(e).__name__, e),
                "elapsed_s": round(time.time() - t0, 1)}
    finally:
        if _outcome["status"] == "cancelled":
            _orb(status='cancelled', label="Image: cancelled")
        elif _outcome["status"] == "failed":
            _orb(status='failed')
        else:
            _orb(status='completed', progress=1.0)
        clear_cancel(orb_pid)
        # The GPU goes back whatever happened. A lease that is not released is
        # the failure mode that strands the machine with no language seats.
        if lease is not None and lease.get("ok") and arbiter is not None:
            try:
                arbiter.release()
            except Exception as e:
                _log.error("local image: lease release failed: %s", e)
