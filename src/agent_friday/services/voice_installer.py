"""
In-UI installer for the local voice tiers (spec: docs/VOICE_SYSTEM_SPEC.md §5).

Runs pip / model downloads as a SINGLE background job with streamed progress,
so the Voice Setup Wizard can offer "Install" buttons instead of pointing users
at pip incantations. Design constraints:

  * Fixed allowlisted targets only — this is NOT an arbitrary-package installer.
  * One job at a time; state is poll-able and the job is cancellable.
  * No request-thread work: the old agent-tool pip path died at a 180 s
    subprocess timeout, which a torch-CUDA download can never meet. The job
    thread has no timeout; liveness is visible through the streamed log.
  * The GPU target installs the CUDA torch wheel EXPLICITLY (pip extras cannot
    express "replace the CPU wheel with the cu126 build").
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time

# Ordered pip stages per target. Each stage is a list of pip args (after
# `python -m pip`). Sizes are pre-flight disk requirements in GB.
_TORCH_CUDA_INDEX = os.environ.get(
    "FRIDAY_TORCH_CUDA_INDEX", "https://download.pytorch.org/whl/cu126")

TARGETS = {
    "voice-local-lite": {
        "label": "Tier-1 local voice (CPU) dependencies",
        "disk_gb": 1.0,
        "stages": [
            ["install", "faster-whisper>=1.0", "piper-tts>=1.2",
             "onnxruntime>=1.17", "pyttsx3>=2.90"],
        ],
    },
    "voice-local-gpu": {
        "label": "Tier-2 local voice (GPU): torch-CUDA + NVIDIA NeMo",
        "disk_gb": 12.0,
        "stages": [
            # Replace any CPU-only torch with the CUDA build first — NeMo on a
            # +cpu wheel imports fine and then can never run.
            ["install", "--upgrade", "torch", "torchaudio",
             "--index-url", _TORCH_CUDA_INDEX],
            ["install", "nemo_toolkit[asr,tts]>=2.6"],
        ],
    },
    # Not a pip target: downloads the Tier-1 ASR/TTS checkpoints via the
    # engine's own ensure_ready() (same code path as first voice session).
    "tier1-models": {
        "label": "Tier-1 voice model checkpoints (~300 MB)",
        "disk_gb": 0.5,
        "stages": [],
    },
}

_LOCK = threading.Lock()
_JOB = {
    "id": 0, "state": "idle", "target": None, "label": "",
    "log": [], "error": "", "started": None, "finished": None,
}
_PROC: subprocess.Popen | None = None
_CANCEL = threading.Event()


def _append_log(line: str):
    line = (line or "").rstrip()
    if not line:
        return
    with _LOCK:
        _JOB["log"].append(line)
        if len(_JOB["log"]) > 400:  # ring buffer — keep the tail
            del _JOB["log"][:200]


def status() -> dict:
    with _LOCK:
        out = dict(_JOB)
        out["log"] = list(_JOB["log"][-60:])
        return out


def cancel() -> dict:
    global _PROC
    _CANCEL.set()
    with _LOCK:
        proc = _PROC
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass
    return status()


def _disk_ok(need_gb: float) -> tuple[bool, str]:
    try:
        free_gb = shutil.disk_usage(os.path.expanduser("~")).free / 1e9
        if free_gb < need_gb:
            return False, (f"Not enough disk space: {free_gb:.1f} GB free, "
                           f"~{need_gb:.0f} GB needed. Free up space and retry.")
        return True, ""
    except Exception:
        return True, ""  # preflight is advisory — never block on probe failure


def _run_pip_stage(args: list) -> int:
    global _PROC
    cmd = [sys.executable, "-m", "pip"] + list(args) + ["--progress-bar", "off"]
    _append_log("$ pip " + " ".join(args))
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    with _LOCK:
        _PROC = proc
    try:
        for line in proc.stdout:  # streams until the process exits — no timeout
            _append_log(line)
            if _CANCEL.is_set():
                break
        proc.wait()
        return proc.returncode if not _CANCEL.is_set() else -15
    finally:
        with _LOCK:
            _PROC = None


def _run_job(target: str):
    spec = TARGETS[target]
    try:
        if target == "tier1-models":
            from agent_friday.services.local_voice import get_local_voice_engine
            eng = get_local_voice_engine()
            ok = eng.ensure_ready(progress=_append_log)
            if not ok:
                raise RuntimeError(getattr(eng, "last_error", "") or
                                   "model download failed")
        else:
            for stage in spec["stages"]:
                if _CANCEL.is_set():
                    raise RuntimeError("cancelled")
                rc = _run_pip_stage(stage)
                if _CANCEL.is_set():
                    raise RuntimeError("cancelled")
                if rc != 0:
                    raise RuntimeError(f"pip exited with code {rc} — see log")
        with _LOCK:
            _JOB["state"] = "done"
            _JOB["finished"] = time.time()
        _append_log("✓ install complete — start a voice session to use it "
                    "(no server restart needed for models; pip installs may "
                    "need a restart to take effect)")
    except Exception as e:
        with _LOCK:
            _JOB["state"] = "cancelled" if str(e) == "cancelled" else "error"
            _JOB["error"] = str(e)[:400]
            _JOB["finished"] = time.time()
        _append_log(f"✗ {e}")


def start(target: str) -> dict:
    """Start an install job. Returns the job status dict (state 'running',
    or 'error' with the reason when it can't start)."""
    if target not in TARGETS:
        return {"state": "error",
                "error": f"unknown target '{target}' — valid: {sorted(TARGETS)}"}
    with _LOCK:
        if _JOB["state"] == "running":
            return {**_JOB, "log": [],
                    "error": "an install is already running — wait or cancel it"}
        ok, why = _disk_ok(TARGETS[target]["disk_gb"])
        if not ok:
            return {"state": "error", "error": why}
        _CANCEL.clear()
        _JOB.update({
            "id": _JOB["id"] + 1, "state": "running", "target": target,
            "label": TARGETS[target]["label"], "log": [], "error": "",
            "started": time.time(), "finished": None,
        })
    threading.Thread(target=_run_job, args=(target,),
                     name=f"voice-install-{target}", daemon=True).start()
    return status()
