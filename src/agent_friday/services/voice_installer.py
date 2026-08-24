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

#: The torch/torchaudio pair this tier installs. PINNED, and pinned to a pair
#: that has actually been loaded on this machine rather than to "whatever is
#: newest" or to two numbers that look tidy.
#:
#: The version numbers do not match, and that is correct. Checked against
#: download.pytorch.org/whl/cu126 on 2026-08-24:
#:
#:     torch      2.13.0, 2.12.1, 2.12.0, 2.11.0, ... 2.6.0
#:     torchaudio             2.11.0, 2.10.0, ... 2.6.0     <- ends at 2.11
#:
#: torchaudio's cu126 line stops at 2.11 and the wheel declares NO dependency
#: on torch at all, which is precisely why `pip install --upgrade torch
#: torchaudio` moved torch to 2.13 and left torchaudio behind without a
#: resolver complaint. A "matched" 2.13/2.13 pin is not available and would
#: fail to resolve — an earlier draft of this file pinned exactly that, and a
#: dry run caught it before it ran.
#:
#: So the pair is chosen and verified by hand. torch 2.13.0 + torchaudio 2.11.0
#: import cleanly together with CUDA available, confirmed by loading them, not
#: by reading version strings. Re-verify by loading before changing either.
_TORCH_PIN = os.environ.get("FRIDAY_TORCH_PIN", "2.13.0+cu126")
_TORCHAUDIO_PIN = os.environ.get("FRIDAY_TORCHAUDIO_PIN", "2.11.0+cu126")

#: Every install writes here, append-only, and survives a restart. The job log
#: used to live only in memory, last 60 lines, discarded on restart — so an
#: install that half-failed left literally no record of what pip said. The only
#: way to reconstruct 2026-08-24 was reading dist-info timestamps.
def _log_path():
    from pathlib import Path
    p = Path(os.path.expanduser("~")) / ".friday" / "voice-install.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p

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
        # ONE stage, and the pair is PINNED. Both of those are fixes for what
        # happened on 2026-08-24, which is worth writing down because the
        # failure was silent and the recovery was not obvious.
        #
        # It used to be two stages: `--upgrade torch torchaudio` first, then
        # nemo_toolkit. Stage one succeeded at 10:31:58 and 10:32:37; stage two
        # never landed, and the machine was left with a freshly upgraded torch,
        # NO nemo package at all, and a UI that had said "Downloading NeMo
        # voice models…". The mic meter still moved, because that is
        # browser-side, and nothing ever came back, because the tier's models
        # were never installed. Two separate faults made that possible:
        #
        #   1. Splitting the install meant a shared, load-bearing dependency
        #      (torch — also under sentence-transformers, silero-vad and
        #      transformers) was mutated BEFORE the thing that needed it was
        #      known to be installable. One resolver pass either gets a
        #      consistent set or fails having changed nothing.
        #   2. `--upgrade` unpinned takes whatever is newest on the index.
        #      A voice toggle should not be able to decide the torch version
        #      for the embedder.
        #
        # Change the pin deliberately, together, after testing the trio.
        # [asr] ONLY, not [asr,tts]. The tts extra pulls `pyopenjtalk`, a
        # Japanese text-to-speech frontend that ships no Windows wheel, builds
        # from source, and needs a C/C++ compiler. On this machine cmake 4.4.2
        # is present and MSVC is not, so it dies with "CMAKE_C_COMPILER not
        # set" — which is what actually killed the 2026-08-24 install. The tier
        # was never installable on a stock Windows box, and the failure looked
        # like an interrupted download rather than an impossible dependency.
        #
        # Nothing is lost: NeMo here is wanted for ASR (speech in). Speech OUT
        # is already served by the Tier-1 Piper path on CPU, and Japanese TTS
        # is not a feature of this product.
        "stages": [
            ["install",
             "torch==%s" % _TORCH_PIN, "torchaudio==%s" % _TORCHAUDIO_PIN,
             "nemo_toolkit[asr]>=2.6",
             "--extra-index-url", _TORCH_CUDA_INDEX],
        ],
        # Reported success means THIS imports, in a subprocess, after pip is
        # done. Not that pip exited 0.
        "verify": ["torch", "torchaudio", "nemo"],
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
    # AND to disk, append-only. The ring buffer above is the live view; it is
    # in memory and dies with the process, which is why the 2026-08-24
    # half-install left no evidence of what pip actually said and had to be
    # reconstructed from dist-info timestamps.
    try:
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), line))
    except Exception:
        pass


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


def _verify_imports(modules: list) -> tuple[bool, str]:
    """Import each module IN A SUBPROCESS and report what actually loaded.

    In a subprocess for two reasons, both learned the hard way. A half-written
    native library raises a Windows entry-point error that can pop a modal
    dialog and, in-process, would take the server down with it — so the check
    runs somewhere expendable, with the error dialog suppressed. And importing
    torch into the server process would pin the very DLLs a later install needs
    to replace.

    This is the difference between "pip exited 0" and "the feature works".
    On 2026-08-24 pip's first stage exited 0, the installer announced
    "✓ install complete", and the tier had no NeMo in it at all.
    """
    if not modules:
        return True, ""
    probe = (
        "import ctypes,sys\n"
        "try: ctypes.windll.kernel32.SetErrorMode(0x0001|0x0002|0x0004)\n"
        "except Exception: pass\n"
        "bad=[]\n"
        "for m in %r:\n"
        "    try: __import__(m)\n"
        "    except BaseException as e: bad.append('%%s: %%s: %%s' %% (m, type(e).__name__, str(e)[:160]))\n"
        "print('OK' if not bad else 'BAD ' + ' | '.join(bad))\n" % (list(modules),)
    )
    try:
        r = subprocess.run([sys.executable, "-c", probe],
                           capture_output=True, text=True, timeout=300,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:
        return False, "verification could not run: %s" % e
    out = (r.stdout or "").strip().splitlines()
    line = out[-1] if out else ""
    if line == "OK":
        return True, ""
    if line.startswith("BAD "):
        return False, line[4:]
    return False, ("verification produced no verdict (exit %s): %s"
                   % (r.returncode, (r.stderr or "")[-200:]))


def _torch_in_use() -> bool:
    """Is torch already loaded in THIS process? Then replacing it is unsafe.

    Overwriting c10.dll / c10_cuda.dll while a process holds them open is the
    likeliest source of the "entry point ??0AcceleratorError@c10@@... could not
    be located" dialog Stephen saw: both DLLs carry a timestamp between the two
    pip stages, so something read them mid-replacement.
    """
    return "torch" in sys.modules


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
        # VERIFY BEFORE CLAIMING SUCCESS.
        _verify = spec.get("verify") or []
        if _verify:
            _append_log("verifying: importing %s…" % ", ".join(_verify))
            ok, detail = _verify_imports(_verify)
            if not ok:
                raise RuntimeError(
                    "pip finished but the tier does not load, so it is NOT "
                    "installed: %s. Your existing setup is unchanged apart "
                    "from any packages pip replaced — the full pip output is "
                    "in %s." % (detail, _log_path()))
            _append_log("verified: %s all import" % ", ".join(_verify))
        with _LOCK:
            _JOB["state"] = "done"
            _JOB["finished"] = time.time()
        _append_log("✓ install complete and verified — start a voice session "
                    "to use it (models need no restart; a pip install may)")
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
        # REFUSE rather than replace a library this process is holding open.
        # pip will happily overwrite torch/lib/*.dll underneath a live process;
        # what the user gets is a native "entry point could not be located"
        # dialog naming a DLL path, which is unrecoverable-looking and says
        # nothing about voice. Better to decline with an instruction.
        if TARGETS[target].get("verify") and _torch_in_use():
            return {
                "state": "error",
                "error": ("Friday is currently using PyTorch (the memory "
                          "embedder loads it), and installing the GPU voice "
                          "tier replaces PyTorch's native libraries. Doing "
                          "that now can break the running app with a Windows "
                          "'entry point not found' error.\n\n"
                          "Quit Friday from the tray, run the install, then "
                          "start Friday again. Nothing has been changed."),
            }
        _CANCEL.clear()
        _JOB.update({
            "id": _JOB["id"] + 1, "state": "running", "target": target,
            "label": TARGETS[target]["label"], "log": [], "error": "",
            "started": time.time(), "finished": None,
        })
    threading.Thread(target=_run_job, args=(target,),
                     name=f"voice-install-{target}", daemon=True).start()
    return status()
