"""
In-UI installer for the local voice tiers (spec: docs/design/active/voice-system-spec.md §5).

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
from agent_friday.paths import friday_home

# Ordered pip stages per target. Each stage is a list of pip args (after
# `python -m pip`). Sizes are pre-flight disk requirements in GB.
_TORCH_CUDA_INDEX = os.environ.get(
    "FRIDAY_TORCH_CUDA_INDEX", "https://download.pytorch.org/whl/cu126")

#: The torch/torchaudio pair this tier installs. PINNED, and pinned to a pair
#: that has actually been loaded on this machine rather than to "whatever is
#: newest" or to two numbers that look tidy.
#:
#: The version numbers do not match, and that is correct. Checked against
#: download.pytorch.org/whl/cu126 at pin time:
#:
#:     torch      2.13.0, 2.12.1, 2.12.0, 2.11.0, ... 2.6.0
#:     torchaudio             2.11.0, 2.10.0, ... 2.6.0     <- ends at 2.11
#:
#: torchaudio's cu126 line stops at 2.11 and the wheel declares NO dependency
#: on torch at all, which is precisely why `pip install --upgrade torch
#: torchaudio` moved torch to 2.13 and left torchaudio behind without a
#: resolver complaint. A "matched" 2.13/2.13 pin is not available and would
#: fail to resolve.
#:
#: So the pair is chosen and verified by hand. torch 2.13.0 + torchaudio 2.11.0
#: import cleanly together with CUDA available, confirmed by loading them, not
#: by reading version strings. Re-verify by loading before changing either.
_TORCH_PIN = os.environ.get("FRIDAY_TORCH_PIN", "2.13.0+cu126")
_TORCHAUDIO_PIN = os.environ.get("FRIDAY_TORCHAUDIO_PIN", "2.11.0+cu126")

#: Every install writes here, append-only, and survives a restart. An
#: in-memory-only job log is discarded on restart, so an install that
#: half-failed would leave no record of what pip said beyond dist-info
#: timestamps.
def _log_path():
    p = friday_home() / "voice-install.log"
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
        # ONE stage, and the pair is PINNED. Both guard against a silent
        # failure whose recovery is not obvious.
        #
        # Split into two stages (`--upgrade torch torchaudio` first, then
        # nemo_toolkit), stage one can succeed and stage two never land,
        # leaving the machine with a freshly upgraded torch, NO nemo package
        # at all, and a UI that said "Downloading NeMo voice models…". The
        # mic meter still moves, because that is browser-side, and nothing
        # ever comes back, because the tier's models were never installed.
        # Two separate faults make that possible:
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
        # from source, and needs a C/C++ compiler. On a box with cmake but no
        # MSVC it dies with "CMAKE_C_COMPILER not set". With the tts extra the
        # tier is not installable on a stock Windows box, and the failure
        # looks like an interrupted download rather than an impossible
        # dependency.
        #
        # Nothing is lost: NeMo here is wanted for ASR (speech in). Speech OUT
        # is already served by the Tier-1 Piper path on CPU, and Japanese TTS
        # is not a feature of this product.
        "stages": [
            ["install",
             "torch==%s" % _TORCH_PIN, "torchaudio==%s" % _TORCHAUDIO_PIN,
             # Pinned with the torch pair: NeMo is installed only here, never
             # from pyproject or uv.lock (docs/security/dependency-advisories.md).
             "nemo_toolkit[asr]==3.0.0",
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
    # voice-system-clean-sheet.md §2.5 / §4.3: the CPU-mouth CANDIDATE. The
    # `kokoro-onnx` wheel (< 5 MB) plus the int8 model (~80 MB) and the
    # voices file (~27 MB) from the wrapper's own release assets. It becomes
    # the CPU default only if Phase 2's measurement says first-chunk <= 500 ms
    # on this CPU; until then it is an option with its measured number.
    # Budget (§2.5): the wheel (< 5 MB) and the voices file (~27 MB) only. The
    # model is the `model_q8f16.onnx` (86 MB) ALREADY on disk under
    # runtime/kokoro-onnx/; the 88 MB int8 release model is not downloaded
    # unless the on-disk export proves not to load (then it is a separate,
    # sized decision, not this target's).
    "kokoro-onnx": {
        "label": "Kokoro-82M via kokoro-onnx (CPU int8 candidate, ~32 MB)",
        "disk_gb": 0.1,
        # --no-deps: the wheel declares `phonemizer`, which shares its import
        # name with the `phonemizer-fork` the Kokoro torch path already uses;
        # letting pip resolve it would overwrite a working mouth's g2p.
        # onnxruntime and numpy are already present.
        "stages": [["install", "--no-deps", "kokoro-onnx>=0.4"]],
        "downloads": [
            ("https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
             "kokoro-onnx/voices-v1.0.bin", 27),
        ],
        "verify": ["kokoro_onnx"],
    },
}


# The local voice stack's pinned artifacts (services/voice_artifacts.py): one
# install target each, refused until both pins (revision + SHA-256, or an
# exact package version) are set.
def _artifact_targets() -> dict:
    from agent_friday.services.voice_artifacts import ARTIFACTS
    out = {}
    for aid, a in ARTIFACTS.items():
        out[aid] = {"label": "%s (%s MB, %s)" % (a["label"], a["size_mb"], a["licence"]),
                    "disk_gb": max(0.1, round(a["size_mb"] * 2.2 / 1024, 1)),
                    "stages": [], "artifact": aid}
    return out


TARGETS.update(_artifact_targets())


def _sha256_file(path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            if _CANCEL.is_set():
                raise RuntimeError("cancelled")
            b = f.read(8 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _safe_extract(archive, dest) -> None:
    """Unpack a .tar.bz2 under `dest` only: a member naming an absolute path,
    a parent directory, or a link is refused (no write outside `dest`)."""
    import tarfile
    from pathlib import Path
    dest = Path(dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:bz2") as t:
        members = t.getmembers()
        for m in members:
            target = (dest / m.name).resolve()
            if m.issym() or m.islnk() or not (target == dest or dest in target.parents):
                raise RuntimeError("refused archive member %r" % m.name)
        t.extractall(dest, members=members, filter="data")


def _install_artifact(aid: str) -> None:
    """Fetch one pinned artifact, check its hash, then put it in place.
    A mismatch deletes the download and fails; nothing unverified lands."""
    from pathlib import Path
    from agent_friday.services import voice_artifacts as va
    ok, why = va.pinned(aid)
    if not ok:
        raise RuntimeError("not installed: %s. Nothing was downloaded." % why)
    a = va.ARTIFACTS[aid]
    for dep in a.get("requires") or []:
        _install_artifact(dep)
    if a["kind"] == "pip":
        rc = _run_pip_stage(["install", va.pip_spec(aid)])
        if rc != 0:
            raise RuntimeError(f"pip exited with code {rc} — see log")
        return
    from agent_friday.core import runtime_dir
    # The same root voice_front / voice_ear_stream read from.
    dest = Path(runtime_dir()) / a["dest"]
    if a["kind"] == "file" and dest.exists() and _sha256_file(dest) == a["sha256"]:
        _append_log(f"present and verified: {dest.name}")
        return
    stage = Path(str(dest) + ".download")
    # The installer owns its staging folder; a downloader only writes a file.
    stage.parent.mkdir(parents=True, exist_ok=True)
    if stage.exists():
        stage.unlink()
    _download(va.url_for(aid), stage, a["size_mb"])
    got = _sha256_file(stage)
    if got != a["sha256"]:
        stage.unlink()
        raise RuntimeError("%s failed its checksum (got %s…, pinned %s…); the "
                           "download was deleted and nothing was installed"
                           % (a["label"], got[:12], a["sha256"][:12]))
    _append_log(f"verified sha256 {got[:12]}… for {a['label']}")
    if a["kind"] == "archive":
        _safe_extract(stage, dest)
        stage.unlink()
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        stage.replace(dest)
    _append_log(f"installed {a['label']} -> {dest}")


def _download(url: str, dest, size_mb: int) -> None:
    """Stream one allowlisted asset to disk with a size line in the log.
    Skips a file that already exists at a plausible size. A transfer that
    stopped part-way leaves ``<dest>.part``; the next call asks the server
    for the rest (HTTP Range) and appends, or starts over when the server
    sends the whole file again. The caller checks the pinned SHA-256."""
    import urllib.error
    import urllib.request
    from pathlib import Path
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > size_mb * 1024 * 1024 * 0.8:
        _append_log(f"present: {dest.name} ({dest.stat().st_size // (1024 * 1024)} MB)")
        return
    tmp = dest.with_suffix(dest.suffix + ".part")
    offset = tmp.stat().st_size if tmp.exists() else 0
    headers = {"User-Agent": "friday-voice-installer"}
    if offset:
        headers["Range"] = "bytes=%d-" % offset
        _append_log(f"resuming {dest.name} from {offset // (1024 * 1024)} MB…")
    else:
        _append_log(f"downloading {dest.name} (~{size_mb} MB)…")
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=60)
    except urllib.error.HTTPError as e:
        if e.code == 416 and offset:
            # The partial file already holds every byte the server has.
            tmp.replace(dest)
            _append_log(f"downloaded {dest.name}: {offset // (1024 * 1024)} MB")
            return
        raise
    with resp as r:
        resuming = bool(offset) and getattr(r, "status", 200) == 206
        if not resuming:
            offset = 0
        try:
            length = int(r.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        total = offset + length if length else 0
        got = offset
        _set_progress(got, total)
        with open(tmp, "ab" if resuming else "wb") as f:
            while True:
                if _CANCEL.is_set():
                    raise RuntimeError("cancelled")
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                _set_progress(got, total)
    if total and got < total:
        raise RuntimeError("the connection closed early (%d of %d bytes); "
                           "start again to continue where it stopped" % (got, total))
    tmp.replace(dest)
    _append_log(f"downloaded {dest.name}: {got // (1024 * 1024)} MB")


def _set_progress(done: int, total: int) -> None:
    with _LOCK:
        _JOB["progress"] = {"done": int(done), "total": int(total)}


def artifact_installed(aid: str) -> bool:
    """Is this artifact on disk now? Pip packages by installed version, a
    file by its pinned size, an archive by a non-empty target folder."""
    from pathlib import Path
    from agent_friday.services import voice_artifacts as va
    a = va.ARTIFACTS[aid]
    try:
        if a["kind"] == "pip":
            from importlib import metadata
            return metadata.version(a["package"]) == a.get("version")
        from agent_friday.core import runtime_dir
        dest = Path(runtime_dir()) / a["dest"]
        if a["kind"] == "file":
            return dest.is_file() and dest.stat().st_size == va.size_bytes(aid)
        return dest.is_dir() and any(dest.iterdir())
    except Exception:
        return False


def artifact_rows() -> list:
    """Every voice artifact for the Settings screen: what it is, its size, its
    pin, whether it is installed, and the state of its download."""
    from agent_friday.services import voice_artifacts as va
    job = status()
    out = []
    for row in va.public_rows():
        row["installed"] = artifact_installed(row["id"])
        row["job"] = None
        if job.get("target") == row["id"] and job.get("state") != "idle":
            row["job"] = {"state": job["state"], "error": job.get("error") or "",
                          "progress": job.get("progress")}
        out.append(row)
    return out

_LOCK = threading.Lock()
_JOB = {
    "id": 0, "state": "idle", "target": None, "label": "",
    "log": [], "error": "", "started": None, "finished": None,
    "progress": None,
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
    # in memory and dies with the process, so a half-install would otherwise
    # leave no evidence of what pip actually said.
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

    In a subprocess for two reasons. A half-written
    native library raises a Windows entry-point error that can pop a modal
    dialog and, in-process, would take the server down with it — so the check
    runs somewhere expendable, with the error dialog suppressed. And importing
    torch into the server process would pin the very DLLs a later install needs
    to replace.

    This is the difference between "pip exited 0" and "the feature works":
    a first pip stage can exit 0, the installer announce "✓ install
    complete", and the tier have no NeMo in it at all.
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
    be located" dialog: a DLL read mid-replacement is a half-written DLL.
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
        elif spec.get("artifact"):
            _install_artifact(spec["artifact"])
        else:
            for stage in spec["stages"]:
                if _CANCEL.is_set():
                    raise RuntimeError("cancelled")
                rc = _run_pip_stage(stage)
                if _CANCEL.is_set():
                    raise RuntimeError("cancelled")
                if rc != 0:
                    raise RuntimeError(f"pip exited with code {rc} — see log")
            # Allowlisted asset downloads (clean-sheet §5.5): fixed URLs,
            # sizes shown, under ~/.friday/runtime/. Never on a path the mic
            # click can block on.
            if spec.get("downloads"):
                from agent_friday.services.local_voice import LOCAL_VOICE_DIR
                _runtime = LOCAL_VOICE_DIR.parent / "runtime"
                for _url, _rel, _mb in spec["downloads"]:
                    _download(_url, _runtime / _rel, _mb)
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
        if TARGETS[target].get("artifact"):
            from agent_friday.services.voice_artifacts import pinned
            ok, why = pinned(TARGETS[target]["artifact"])
            if not ok:
                return {"state": "error",
                        "error": "%s. Nothing was downloaded." % why}
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
            "started": time.time(), "finished": None, "progress": None,
        })
    threading.Thread(target=_run_job, args=(target,),
                     name=f"voice-install-{target}", daemon=True).start()
    return status()
