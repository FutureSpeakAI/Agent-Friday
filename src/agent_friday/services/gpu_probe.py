"""Is there a CUDA card CTranslate2 (faster-whisper) can use, and how much of
it is free? Answered without torch.

The bundled torch is a CPU-only build, so ``torch.cuda.is_available()`` says
False on a machine with an RTX card, and the ear that used it transcribed on
the processor: 5-9 s per Alt+T on a laptop whose card does it in about 1 s.
CTranslate2 is what faster-whisper actually runs on, and it answers the
question itself (``ctranslate2.get_cuda_device_count()``); the driver's
``nvidia-smi`` answers it when CTranslate2 is not importable, and is the only
place free VRAM comes from. torch is never imported here.

Cached: the device count for the life of the process (it does not change
under a running server), free VRAM for a couple of seconds (the brain loads
and unloads on the same card).

``CUDA_VISIBLE_DEVICES`` set to ``-1`` or empty hides every card from CUDA, so
this reports none without running anything: nvidia-smi ignores that variable
and would otherwise contradict what CTranslate2 will actually see. Tests run
with it set, so no test reaches a real GPU.

cuBLAS: CTranslate2's CUDA build loads ``cublas64_12.dll`` the first time it
runs a model on the card, with a plain ``LoadLibrary`` by name. That call uses
the legacy search order (the application directory, the system directories,
then PATH) and NOT the directories added with ``os.add_dll_directory``, which
only ``LoadLibraryEx`` with the ``LOAD_LIBRARY_SEARCH_USER_DIRS`` /
``SEARCH_DEFAULT_DIRS`` flags consults, so adding the folder that way does
nothing (measured on the reporting machine). The llama.cpp runtime Friday
already ships for the brain (``~/.friday/runtime/llama.cpp-bonsai``, CUDA 12)
carries that DLL, so when no cuBLAS 12 is otherwise findable,
``ensure_cublas_on_path()`` prepends that folder to the GPU voice worker's
environment, and to this process's PATH only right before the in-process ear
loads on CUDA (``prepare_cublas_for_cuda`` says why that cannot be narrower);
it says so in friday.log, and only if the folder exists and actually holds the
DLL. Setting ``local_voice_borrow_llama_cpp_cublas`` to False leaves PATH
alone. When cuBLAS cannot be found either way the ear is not put on the card
at all (``pick_ct2_device``), and a CUDA library error at transcription
reloads it on the CPU once (local_voice.WhisperASR.transcribe).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, Optional

log = logging.getLogger("friday.gpu_probe")

CUBLAS_DLL = "cublas64_12.dll"
#: The llama.cpp runtime folder that ships cuBLAS 12 with the brain, tried
#: first; any other ``llama.cpp*`` folder under the runtime root after it.
PREFERRED_RUNTIME = "llama.cpp-bonsai"
SMI_TIMEOUT_S = 5.0
FREE_TTL_S = 2.0
_POPEN_FLAGS = 0x08000000 if sys.platform == "win32" else 0   # CREATE_NO_WINDOW
#: cuBLAS borrowing is a Windows DLL-search matter (tests flip this).
_IS_WINDOWS = os.name == "nt"

_LOCK = threading.Lock()
_COUNT: Optional[dict] = None           # {"devices", "source", "detail"}
_FREE: Optional[tuple] = None           # (monotonic time, smi dict or None)
_PATH_NOTED: set = set()


def reset() -> None:
    """Forget every cached answer (tests; a driver installed mid-session)."""
    global _COUNT, _FREE
    with _LOCK:
        _COUNT = None
        _FREE = None
        _PATH_NOTED.clear()


def _cuda_hidden() -> bool:
    v = os.environ.get("CUDA_VISIBLE_DEVICES")
    return v is not None and v.strip() in ("", "-1")


# ── nvidia-smi ──────────────────────────────────────────────────────────────

def _smi_exe() -> Optional[str]:
    exe = shutil.which("nvidia-smi")
    if exe:
        return exe
    if os.name == "nt":
        for base in (os.environ.get("SystemRoot") or r"C:\Windows",):
            p = Path(base) / "System32" / "nvidia-smi.exe"
            if p.is_file():
                return str(p)
    return None


def parse_smi(text: str) -> list:
    """``nvidia-smi --query-gpu=memory.free,memory.total,name
    --format=csv,noheader,nounits`` output -> one dict per card, in index
    order. Lines that do not parse are skipped, not guessed at."""
    out = []
    for line in (text or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            free, total = int(float(parts[0])), int(float(parts[1]))
        except ValueError:
            continue
        out.append({"free_mib": free, "total_mib": total,
                    "name": ",".join(parts[2:]).strip() or None})
    return out


def _run_smi() -> Optional[list]:
    """The cards nvidia-smi reports, or None when it cannot answer. Bounded:
    no shell, a fixed argument list, a timeout."""
    exe = _smi_exe()
    if not exe:
        return None
    try:
        r = subprocess.run(
            [exe, "--query-gpu=memory.free,memory.total,name",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=SMI_TIMEOUT_S, shell=False,
            creationflags=_POPEN_FLAGS)
    except Exception as e:  # noqa: BLE001 - timeout, missing driver, access
        log.info("gpu probe: nvidia-smi did not answer: %s", str(e)[:120])
        return None
    if r.returncode != 0:
        log.info("gpu probe: nvidia-smi exited %s: %s", r.returncode,
                 (r.stderr or r.stdout or "").strip()[:120])
        return None
    return parse_smi(r.stdout)


# ── CTranslate2 ─────────────────────────────────────────────────────────────

def _ct2_device_count() -> Optional[int]:
    """CTranslate2's own count, or None when it is not importable here."""
    try:
        if "ctranslate2" not in sys.modules:
            import importlib.util
            if importlib.util.find_spec("ctranslate2") is None:
                return None
        import ctranslate2
        return int(ctranslate2.get_cuda_device_count())
    except Exception as e:  # noqa: BLE001 - a CPU-only build, a broken DLL
        log.info("gpu probe: ctranslate2 could not count CUDA devices: %s", str(e)[:120])
        return None


def device_count(refresh: bool = False) -> dict:
    """``{"devices": n, "source": ..., "detail": ...}``; cached."""
    global _COUNT
    with _LOCK:
        if _COUNT is not None and not refresh:
            return dict(_COUNT)
    if _cuda_hidden():
        res = {"devices": 0, "source": "CUDA_VISIBLE_DEVICES",
               "detail": "CUDA_VISIBLE_DEVICES hides every card"}
    else:
        n = _ct2_device_count()
        if n is not None:
            res = {"devices": n, "source": "ctranslate2",
                   "detail": "" if n else "CTranslate2 sees no CUDA device"}
        else:
            cards = _run_smi()
            if cards is None:
                res = {"devices": 0, "source": "none",
                       "detail": "neither CTranslate2 nor nvidia-smi reports a CUDA device"}
            else:
                res = {"devices": len(cards), "source": "nvidia-smi",
                       "detail": "" if cards else "nvidia-smi lists no card"}
    with _LOCK:
        _COUNT = res
    return dict(res)


def smi_row_for_cuda_device_0() -> Optional[int]:
    """Which nvidia-smi row is the card CTranslate2 calls device 0.

    CTranslate2 uses CUDA's device 0, which is the first entry of
    ``CUDA_VISIBLE_DEVICES`` when that is set ("1,0" makes physical card 1
    device 0); nvidia-smi ignores the variable and lists every card by its
    own index. So a numeric first entry names the row to read; unset means
    row 0. A UUID or MIG entry ("GPU-...", "MIG-...") cannot be mapped from
    this query, so None (free memory unknown, the ear stays on the CPU and
    says so) rather than reading another card's memory. nvidia-smi's index is
    PCI bus order; CUDA's default order is fastest-first, so on a machine
    with several different cards the two agree only with
    ``CUDA_DEVICE_ORDER=PCI_BUS_ID`` (single-card laptops are unaffected)."""
    v = os.environ.get("CUDA_VISIBLE_DEVICES")
    if v is None:
        return 0
    first = v.split(",")[0].strip()
    if first.isdigit():
        return int(first)
    return None


def free_vram_mib(refresh: bool = False) -> Optional[int]:
    """Free memory in MiB on the card CTranslate2 would use (nvidia-smi, the
    row ``smi_row_for_cuda_device_0`` names), or None when unknown."""
    global _FREE
    now = time.monotonic()
    with _LOCK:
        if _FREE is not None and not refresh and now - _FREE[0] < FREE_TTL_S:
            card = _FREE[1]
            return card["free_mib"] if card else None
    if _cuda_hidden():
        card = None
    else:
        row = smi_row_for_cuda_device_0()
        cards = _run_smi() if row is not None else None
        if row is None:
            log.info("gpu probe: CUDA_VISIBLE_DEVICES names its card by id, which "
                     "nvidia-smi's row cannot be matched to; free memory unknown")
        card = cards[row] if cards and row is not None and row < len(cards) else None
    with _LOCK:
        _FREE = (now, card)
    return card["free_mib"] if card else None


def probe(refresh: bool = False) -> dict:
    """Everything the ear's device choice needs, in one dict."""
    c = device_count(refresh)
    free = free_vram_mib(refresh) if c["devices"] else None
    return {"cuda": c["devices"] > 0, "devices": c["devices"], "source": c["source"],
            "free_mib": free, "detail": c["detail"]}


# ── cuBLAS for CTranslate2 ──────────────────────────────────────────────────

def _runtime_root() -> Optional[Path]:
    try:
        from agent_friday.paths import runtime_dir
        return Path(runtime_dir())
    except Exception:
        return None


def cublas_dir(root: Optional[Path] = None) -> Optional[Path]:
    """The llama.cpp runtime folder that holds cuBLAS 12, or None."""
    root = root if root is not None else _runtime_root()
    if root is None or not root.is_dir():
        return None
    cands = [root / PREFERRED_RUNTIME]
    try:
        cands += sorted(p for p in root.glob("llama.cpp*")
                        if p.is_dir() and p.name != PREFERRED_RUNTIME)
    except OSError:
        pass
    for d in cands:
        if (d / CUBLAS_DLL).is_file():
            return d
    return None


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p)) if p else ""


def cublas_findable(env: Optional[Dict[str, str]] = None) -> bool:
    """Would CTranslate2's LoadLibrary find cuBLAS 12 already: on PATH, or
    beside CTranslate2's own DLLs?"""
    path = (env if env is not None else os.environ).get("PATH", "")
    for p in path.split(os.pathsep):
        if p and (Path(p) / CUBLAS_DLL).is_file():
            return True
    try:
        import importlib.util
        spec = importlib.util.find_spec("ctranslate2")
        for loc in (spec.submodule_search_locations or []) if spec else []:
            if (Path(loc) / CUBLAS_DLL).is_file():
                return True
    except Exception:
        pass
    return False


def _borrow_allowed() -> bool:
    try:
        import agent_friday.core as core
        return bool((core._load_settings() or {}).get(
            "local_voice_borrow_llama_cpp_cublas", True))
    except Exception:
        return True


def ensure_cublas_on_path(env: Optional[Dict[str, str]] = None, *,
                          root: Optional[Path] = None) -> dict:
    """Make cuBLAS 12 findable by CTranslate2: prepend the llama.cpp runtime
    folder that holds it to PATH (``env``, or this process's environment).

    Windows only; never when cuBLAS is already findable, when the folder does
    not exist or lacks the DLL, or when the setting says not to. Returns
    ``{"added": bool, "dir": str|None, "reason": str}`` and logs what it did
    (once per folder for this process)."""
    target = os.environ if env is None else env
    if not _IS_WINDOWS:
        return {"added": False, "dir": None, "reason": "not Windows"}
    if not _borrow_allowed():
        return {"added": False, "dir": None,
                "reason": "local_voice_borrow_llama_cpp_cublas is off"}
    if cublas_findable(target):
        return {"added": False, "dir": None, "reason": "cuBLAS 12 is already findable"}
    d = cublas_dir(root)
    if d is None:
        log.info("gpu probe: no %s on PATH and none under the llama.cpp runtime; "
                 "CTranslate2 on CUDA will not find cuBLAS", CUBLAS_DLL)
        return {"added": False, "dir": None,
                "reason": "no llama.cpp runtime folder holds %s" % CUBLAS_DLL}
    parts = [p for p in target.get("PATH", "").split(os.pathsep) if p]
    if _norm(str(d)) not in {_norm(p) for p in parts}:
        target["PATH"] = os.pathsep.join([str(d)] + parts)
    key = (_norm(str(d)), env is None)
    with _LOCK:
        first = key not in _PATH_NOTED
        _PATH_NOTED.add(key)
    if first:
        log.info("gpu probe: prepended %s to %s PATH so CTranslate2 finds %s "
                 "(os.add_dll_directory is not searched by its LoadLibrary)",
                 d, "this process's" if env is None else "a voice worker's", CUBLAS_DLL)
    return {"added": True, "dir": str(d), "reason": ""}


def cublas_status(env: Optional[Dict[str, str]] = None) -> dict:
    """Could CTranslate2 load cuBLAS 12 here? Changes nothing.

    ``{"ok", "how", "dir", "reason"}``: ``how`` is "findable" (PATH or beside
    CTranslate2), "borrowable" (a llama.cpp runtime folder holds it and the
    setting allows prepending it), "not windows" (the dynamic linker resolves
    CUDA libraries there; this is a Windows LoadLibrary matter), or "missing"
    with the reason in words."""
    target = os.environ if env is None else env
    if not _IS_WINDOWS:
        return {"ok": True, "how": "not windows", "dir": None, "reason": ""}
    if cublas_findable(target):
        return {"ok": True, "how": "findable", "dir": None, "reason": ""}
    where = "%s is not on PATH or beside CTranslate2" % CUBLAS_DLL
    if not _borrow_allowed():
        return {"ok": False, "how": "missing", "dir": None,
                "reason": where + ", and local_voice_borrow_llama_cpp_cublas is off"}
    d = cublas_dir()
    if d is None:
        return {"ok": False, "how": "missing", "dir": None,
                "reason": where + ", and no llama.cpp runtime folder holds it"}
    return {"ok": True, "how": "borrowable", "dir": str(d), "reason": ""}


def prepare_cublas_for_cuda(env: Optional[Dict[str, str]] = None) -> tuple:
    """Right before a CTranslate2 CUDA load: make cuBLAS 12 findable (borrow
    the llama.cpp runtime's copy onto ``env``'s PATH, or this process's, when
    that is the only one). ``(True, "")`` when CTranslate2 will find it, else
    ``(False, reason)`` and the caller loads on the CPU instead.

    In-process the prepend is process-wide and stays: CTranslate2 loads cuBLAS
    lazily, by name, at the model's first matrix multiply (the first
    transcription, not the load), so restoring PATH after the load would undo
    it before it is used. It is therefore done only here, when the in-process
    ear is about to load on CUDA, and never when cuBLAS is already findable;
    the GPU voice worker gets it in its own environment instead
    (voice_workers._worker_env), which is the preferred path."""
    st = cublas_status(env)
    if not st["ok"]:
        return False, st["reason"]
    if st["how"] == "borrowable":
        res = ensure_cublas_on_path(env)
        if not res.get("added") and not cublas_findable(os.environ if env is None else env):
            return False, res.get("reason") or "%s could not be put on PATH" % CUBLAS_DLL
    return True, ""


# ── the ear's device choice ─────────────────────────────────────────────────

#: Free VRAM the ear asks for before it takes the card: whisper small in
#: int8_float16 wants well under a gigabyte; the margin keeps the brain on it.
EAR_NEED_MIB = 1536


def pick_ct2_device(need_mib: int = EAR_NEED_MIB) -> tuple:
    """``(device, compute_type, why_cpu)`` for a CTranslate2 model.

    CUDA with ``int8_float16`` when a card is there, has ``need_mib`` free,
    and CTranslate2 can load cuBLAS 12 (``cublas_status``: already findable,
    or borrowable from the llama.cpp runtime); else the CPU with ``int8`` and
    the reason in words. A card without cuBLAS used to be chosen anyway: the
    load succeeded and every Alt+T then failed at its first transcription."""
    p = probe()
    if not p["cuda"]:
        return "cpu", "int8", "no CUDA device (%s)" % (p["detail"] or p["source"])
    free = p["free_mib"]
    if free is None:
        return "cpu", "int8", ("a CUDA device is present but its free memory "
                               "could not be measured (nvidia-smi did not answer)")
    if free < need_mib:
        return "cpu", "int8", ("only %.1f GiB free on the GPU; the brain has it"
                               % (free / 1024.0))
    cb = cublas_status()
    if not cb["ok"]:
        return "cpu", "int8", ("a CUDA device is present but CTranslate2 cannot "
                               "load cuBLAS 12: %s" % cb["reason"])
    return "cuda", "int8_float16", ""
