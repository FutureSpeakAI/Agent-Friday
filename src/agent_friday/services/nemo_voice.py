"""
Agent Friday — Local Voice Engine (Tier-2, NVIDIA NeMo, GPU-accelerated)

The *premium* on-device voice tier: GPU streaming ASR + higher-fidelity TTS for
users with an RTX-class card. It sits BEHIND the exact same backend interface as
the Tier-1 CPU path (``services.local_voice.WhisperASR`` / ``PiperTTS``) so the
``LocalVoiceEngine`` swaps it in without any change to the ``/ws/voice-local``
WebSocket contract, the browser audio plumbing, or the holographic signals.

    Tier-1 (CPU):  faster-whisper + Piper      → services/local_voice.py
    Tier-2 (GPU):  Nemotron-3.5 ASR + NeMo TTS → THIS module

Models (verified June 2026):
  * ASR — ``nvidia/nemotron-3.5-asr-streaming-0.6b`` (cache-aware FastConformer-
    RNNT, 600M params, GPU-only, ~2–3 GB VRAM single-stream). License OpenMDW-1.1.
  * TTS — FastPitch (``nvidia/tts_en_fastpitch``) + HiFi-GAN
    (``nvidia/tts_hifigan``), 22.05 kHz, resampled to 24 kHz to match the worklet.

Design rules (identical philosophy to Tier-1, so both tiers coexist cleanly):
  * **Never import torch / nemo at module load.** Everything heavy is imported
    lazily inside ``load()``/``transcribe()``/``synthesize()``. Importing this
    module is free and CI-safe — the GPU stack is an opt-in install
    (``pip install -e .[voice-local-gpu]`` + a torch-CUDA wheel).
  * **Graceful degradation.** If torch/NeMo aren't importable or no CUDA GPU is
    present, the backends report unavailable and the engine falls back to Tier-1
    (CPU) — the user always gets *some* local voice.
  * **Same interface.** ``NeMoASR.transcribe(pcm16_16k) -> str`` and
    ``NeMoTTS.synthesize(text) -> 24 kHz PCM16 bytes`` mirror the Tier-1 classes
    exactly, so the engine and the tests treat them interchangeably.
  * **Lazy download.** Checkpoints fetch on first GPU voice activation (not at
    install) into ``~/.friday/models/nemo/`` with a progress callback for the
    one-time "Downloading NeMo voice models…" orb.

Windows note: NeMo is Linux-first. On Windows+RTX it usually works under a recent
torch-CUDA wheel, but if a clean install proves painful the engine's automatic
fallback to Tier-1 (onnxruntime, rock-solid on Windows) keeps voice working. See
docs/user-guide/local-voice-gpu-tier.md + tests/MANUAL_TEST_PROCEDURES.md.
"""
from __future__ import annotations

import array
import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger("friday.nemo_voice")

# Reuse the Tier-1 audio + probing helpers so the two tiers share one resample /
# dependency-probe implementation (no duplication, identical 24 kHz output).
from agent_friday.paths import friday_home
from agent_friday.services.local_voice import (
    PLAYBACK_RATE,
    _module_installed,
    _resample_pcm16,
)

# Cache the (large) NeMo/HF checkpoints under ~/.friday/models/nemo so they
# survive reinstalls, are inspectable, and never pollute the Tier-1
# ~/.friday/local_voice dir.
NEMO_DIR = friday_home() / "models" / "nemo"

# Target models. Settings can override the ASR id (the TTS pair is fixed for v1).
NEMO_ASR_MODEL = "nvidia/nemotron-3.5-asr-streaming-0.6b"
NEMO_FASTPITCH_MODEL = "nvidia/tts_en_fastpitch"
NEMO_HIFIGAN_MODEL = "nvidia/tts_hifigan"
NEMO_TTS_NATIVE_RATE = 22050      # FastPitch+HiFi-GAN synthesize at 22.05 kHz

# Minimum *free* VRAM (GB) to offer the GPU tier. 0.6B RNN-T fp16 is ~2–3 GB
# single-stream; 4 GB leaves headroom for TTS sharing the card. Below this we
# stay on Tier-1 (CPU). (See spec §5.1 — "16+ GB" claims are batch-128 configs.)
MIN_VRAM_GB = 4.0

# Cache-aware streaming chunk: att_context_size [56,3] ≈ 320 ms — the latency/WER
# sweet spot from the model card (80 ms … 1.12 s dial). Used best-effort on load.
NEMO_ATT_CONTEXT_SIZE = [56, 3]

_TESTING = bool(os.environ.get("FRIDAY_TESTING"))


# ═══════════════════════════════════════════════════════════════════════════
#  Dependency + GPU probing (cheap, never imports torch/nemo)
# ═══════════════════════════════════════════════════════════════════════════

def nemo_deps_status() -> dict:
    """Which Tier-2 GPU deps are importable, WITHOUT importing them."""
    return {
        # nemo_toolkit installs the importable package `nemo`.
        "nemo": _module_installed("nemo") or _module_installed("nemo_toolkit"),
        "torch": _module_installed("torch"),
        # FastPitch's English tokenizer instantiates EnglishG2p, whose module
        # imports nltk. `nemo_toolkit[asr,tts]` does NOT pull it in, and its
        # absence is the entire reason GPU voice was silent (see below).
        "nltk": _module_installed("nltk"),
    }


def nemo_deps_installed() -> bool:
    """True when the minimum Tier-2 stack (torch + NeMo + g2p) is importable.

    ``nltk`` is in this list because of a reproducible failure mode worth
    keeping written down.

    FastPitch's checkpoint config names its g2p as
    ``nemo.collections.tts.torch.g2ps.EnglishG2p``. NeMo resolves that path
    through an allow-list; the resolver imports the target, and the import
    raised ``ModuleNotFoundError: No module named 'nltk'``. An import error is
    indistinguishable from a disallowed target to that code, so the allow-list
    reported the target as unsafe and NeMo raised:

        UnsafeTargetError: Instantiation of unsafe target
        'nemo.collections.tts.torch.g2ps.EnglishG2p' is blocked ...
        to prevent potential arbitrary code execution.

    A missing pip package is therefore reported as a SECURITY refusal — which
    reads as "GPU voice is broken" rather than "one dependency is missing".
    A tier gate that consults only torch/NeMo/CUDA/VRAM would have health
    report "NeMo GPU voice ready" while the TTS half cannot load at all.
    Checking it here is what makes that claim honest.
    """
    d = nemo_deps_status()
    return bool(d["nemo"] and d["torch"] and d["nltk"])


#: How long one GPU reading stays good for. Measured 2026-09-10 on the
#: reference machine: every health poll re-ran a torch CUDA query AND an
#: ``nvidia-smi`` subprocess, 12 times a minute steady and 47 in the minute a
#: Gemini Live session was running -- with NeMo not in the loop at all. Those
#: spawns share the process with the audio bridge, and the audio was skipping.
#: 30 s is ample for an admission check; when no local GPU voice consumer is
#: even selected, a five-minute reading is plenty for a settings panel.
_GPU_STATUS_TTL_S = 30.0
_GPU_STATUS_IDLE_TTL_S = 300.0
_gpu_cache_lock = threading.Lock()
_gpu_cache = {"at": 0.0, "info": None}
#: The last admission verdict that was LOGGED. The dispute line is a warning
#: the first time and whenever the verdict changes; otherwise it is debug.
#: Twelve identical warnings a minute made friday.log unreadable.
_last_logged_verdict = [None]


def _local_gpu_voice_selected() -> bool:
    """Is any local GPU voice consumer selected right now?

    NeMo (the ``local-gpu``/``auto`` engine) and Kokoro (a CUDA synthesizer
    even on the CPU tier) both admit against this reading. Cloud voice and
    Piper-only local voice do not, so a stale reading costs them nothing.
    """
    try:
        import agent_friday.core as core
        s = core._load_settings() or {}
    except Exception:
        return False
    pref = str(s.get("voice_engine") or "local").strip().lower()
    if pref in ("local-gpu", "gpu", "nemo", "nvidia-nemo", "auto"):
        return True
    return str(s.get("local_voice_tts_engine") or "piper").strip().lower() == "kokoro"


def reset_gpu_status_cache() -> None:
    with _gpu_cache_lock:
        _gpu_cache["at"] = 0.0
        _gpu_cache["info"] = None
    _last_logged_verdict[0] = None


def gpu_status(fresh: bool = False) -> dict:
    """Detect CUDA availability + VRAM for GPU-tier auto-selection.

    Cached. ``fresh=True`` bypasses the cache and is what an ADMISSION decision
    (the moment a model is about to be loaded) must pass; health and settings
    readers take the cached reading. Prefers torch (accurate *free* VRAM via
    ``cuda.mem_get_info``); falls back to ``nvidia-smi`` (total VRAM only)
    through ollama_manager when torch isn't installed yet. Never raises.
    ``sufficient`` reflects ``MIN_VRAM_GB`` against the CONSERVATIVE figure
    whenever nvidia-smi corroborated the reading (see ``_probe_gpu_status``).
    """
    ttl = _GPU_STATUS_TTL_S if _local_gpu_voice_selected() else _GPU_STATUS_IDLE_TTL_S
    with _gpu_cache_lock:
        cached = _gpu_cache["info"]
        if (not fresh and cached is not None
                and (time.monotonic() - _gpu_cache["at"]) < ttl):
            return dict(cached)
        info = _probe_gpu_status()
        _gpu_cache["info"] = dict(info)
        _gpu_cache["at"] = time.monotonic()
        return info


def _log_dispute(info: dict) -> None:
    """Warn when the admission verdict CHANGES; otherwise debug."""
    verdict = (bool(info.get("vram_measurement_disputed")),
               bool(info.get("sufficient")),
               info.get("sufficient_reachable"),
               info.get("sufficient_real"),
               bool(info.get("contended")))
    changed = verdict != _last_logged_verdict[0]
    _last_logged_verdict[0] = verdict
    (log.warning if changed else log.debug)(
        "VRAM measurement disputed: torch=%sGB nvidia-smi=%sGB gap=%sGB; "
        "admission=%s (conservative figure); verdict from torch=%s, from "
        "nvidia-smi=%s",
        info.get("vram_free_gb"), info.get("vram_free_real_gb"),
        info.get("vram_dispute_gb"), info.get("sufficient"),
        info.get("sufficient_reachable"), info.get("sufficient_real"))


def _probe_gpu_status() -> dict:
    """The uncached probe behind ``gpu_status``. One torch query plus one
    ``nvidia-smi`` subprocess; call through the cache, not directly."""
    info = {
        "cuda": False, "device": None,
        "vram_gb": 0.0, "vram_free_gb": 0.0,
        "sufficient": False, "source": "none", "detail": "",
    }
    # 1) torch — the authoritative source (and the runtime NeMo actually needs).
    if _module_installed("torch"):
        try:  # pragma: no cover - requires a real torch+CUDA install
            import torch
            if torch.cuda.is_available():
                idx = torch.cuda.current_device()
                info["cuda"] = True
                info["device"] = torch.cuda.get_device_name(idx)
                try:
                    free, total = torch.cuda.mem_get_info(idx)
                    info["vram_free_gb"] = round(free / 1e9, 1)
                    info["vram_gb"] = round(total / 1e9, 1)
                except Exception:
                    props = torch.cuda.get_device_properties(idx)
                    info["vram_gb"] = round(props.total_memory / 1e9, 1)
                    info["vram_free_gb"] = info["vram_gb"]
                info["sufficient"] = info["vram_free_gb"] >= MIN_VRAM_GB
                info["source"] = "torch"
                info["detail"] = (f"CUDA {info['device']} — "
                                  f"{info['vram_free_gb']}GB free / {info['vram_gb']}GB")
                # TWO AUTHORITIES THAT DISAGREE BY 9.5 GB.
                #
                # Measured on the reference machine, same card, same second,
                # with gemma4:12b resident on llama.cpp:
                #
                #     torch.cuda.mem_get_info  ->  10.0 GB free
                #     nvidia-smi               ->   0.4 GB free
                #
                # Neither is lying; they answer different questions. Windows
                # WDDM lets the driver page GPU memory out to system RAM, so
                # torch reports what CUDA could *obtain* (after eviction) while
                # nvidia-smi reports what is genuinely unused right now.
                #
                # Believing torch alone is not harmless. Allocating the 3 GB a
                # NeMo ASR session wants succeeds against 0.4 GB of real free
                # memory -- and the resident brain goes from 0.30 s to 3.08 s per
                # turn (measured on the reference machine), a 10x slowdown,
                # sustained for as long as the memory is held, recovering once
                # released. Nothing crashes. The cost is invisible and entirely
                # in latency.
                #
                # So the gate no longer decides on torch's number alone. It keeps
                # torch's answer (that IS what allocation will see) and adds the
                # real figure, so callers can say what it will cost instead of
                # discovering it as a mysteriously slow Friday.
                info.update(_contention_probe(info["vram_free_gb"]))
                # ADMISSION TAKES THE CONSERVATIVE FIGURE. torch's number is
                # what an allocation would obtain by making the driver page
                # other work out; admitting on it is how the display got
                # starved to 448 MiB against a 2,560 MiB reserve (2026-09-10
                # 08:38) and the holographic scene died. torch's verdict is
                # kept as `sufficient_reachable` for reporting; `sufficient`
                # -- the field every admission path reads -- is nvidia-smi's
                # whenever nvidia-smi answered.
                info["sufficient_reachable"] = info["sufficient"]
                if info.get("sufficient_real") is not None:
                    info["sufficient"] = bool(info["sufficient_real"])
                # Never quote one authority as if it were the measurement when
                # a second one is available and disagrees. `detail` is what the
                # settings UI renders, so this is where "11.6GB free" stops
                # being the whole story.
                if info.get("vram_free_real_gb") is not None:
                    info["detail"] = (
                        f"CUDA {info['device']} — {info['vram_free_gb']}GB "
                        f"reachable / {info['vram_free_real_gb']}GB genuinely "
                        f"free / {info['vram_gb']}GB total")
                    if info.get("vram_measurement_disputed"):
                        info["detail"] += " (readings disagree — see note)"
                        _log_dispute(info)
                return info
            # torch is installed but CPU-only. Do NOT return here: fall through
            # to the nvidia-smi probe so a physical GPU is still detected and
            # the "install torch-CUDA" remediation can surface. Early-returning
            # made an RTX 4070 invisible to health/diagnostics whenever the
            # venv shipped a +cpu torch wheel — exactly the machine state the
            # Tier-2 upgrade hint was written for.
            info["source"] = "torch"
            info["detail"] = "torch installed but CUDA not available"
        except Exception as e:
            info["detail"] = f"torch probe failed: {str(e)[:80]}"

    # 2) nvidia-smi (via ollama_manager.detect_hardware) — total VRAM only.
    try:
        from agent_friday.routing.ollama_manager import get_manager
        hw = get_manager().detect_hardware()
        gpu = hw.get("gpu")
        vram = float(hw.get("vram_gb") or 0)
        if gpu and "nvidia" in str(gpu).lower():
            _cpu_only_torch = info["source"] == "torch"
            info["device"] = gpu
            info["vram_gb"] = vram
            info["vram_free_gb"] = vram  # free unknown without torch — assume total
            info["sufficient"] = vram >= MIN_VRAM_GB
            info["source"] = "nvidia-smi"
            if _cpu_only_torch:
                info["detail"] = (f"{gpu} ({vram}GB) detected, but the installed "
                                  f"torch is CPU-only — install a torch-CUDA wheel "
                                  f"to enable GPU voice")
            else:
                info["detail"] = (f"{gpu} ({vram}GB) — install torch-CUDA to run NeMo")
    except Exception as e:
        if not info["detail"]:
            info["detail"] = str(e)[:120]
    return info


#: What a single-stream NeMo ASR session actually wants resident (GB). Used to
#: decide whether the GPU tier would be contending with something else rather
#: than filling idle memory. Matches the 0.6B RNN-T fp16 figure in MIN_VRAM_GB's
#: note, and is the size actually allocated when measured on the reference
#: machine.
_ASR_WORKING_SET_GB = 3.0


#: How far the two VRAM authorities may disagree before the measurement is
#: reported as DISPUTED rather than as a number. Below this they are answering
#: the same question with rounding noise; above it they are answering different
#: questions and quoting either one alone is a claim the machine does not
#: support. 1.5 GB is half the ASR working set — the point at which the
#: disagreement is large enough to change the admission decision.
_VRAM_DISPUTE_GB = 1.5


def _contention_probe(torch_free_gb: float) -> dict:
    """Ask nvidia-smi what is *genuinely* free, and say whether we'd contend.

    Returns keys merged into ``gpu_status()``. Never raises and never blocks the
    tier: a machine where the GPU voice models would displace something is still
    a machine where they RUN. The point is to make the trade visible, not to
    make it for the user. (This non-gating contract is deliberate; see
    docs/design/active/local-voice-repair-and-native-audio.md §4.4 R3.4.)

    What changed, and why: this probe previously reported ``contended`` only
    when the real figure fell below the ASR working set — so on the reference
    machine, measured 2026-09-09, torch reported 11.6 GB free while nvidia-smi
    reported 3.1 GB, and because 3.1 > 3.0 the probe said ``contended: False``
    and health reported "NeMo GPU voice ready". A 8.5 GB disagreement between
    the two authorities produced a clean bill of health by a margin of 0.1 GB.
    That is not a measurement, it is a coin landing on its edge.

    So the probe now reports the DISAGREEMENT itself, separately from the
    verdict. When the two authorities differ materially, callers are told the
    measurement is disputed and given BOTH numbers, rather than being handed
    whichever one happened to be consulted.
    """
    out = {"vram_free_real_gb": None, "contended": False, "contention_detail": "",
           "vram_measurement_disputed": False, "vram_dispute_gb": None,
           "sufficient_real": None}
    try:
        import subprocess
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=6)
        line = (r.stdout or "").strip().splitlines()
        if not line:
            # No second opinion available. Say so — an unverified torch reading
            # is not the same thing as a corroborated one, and a caller that
            # cannot tell the difference will treat a guess as a measurement.
            out["vram_measurement_disputed"] = True
            out["contention_detail"] = (
                "Could not corroborate the GPU memory reading: nvidia-smi "
                "returned nothing. Only torch's figure "
                f"({torch_free_gb}GB reachable) is available, and on Windows "
                "that number counts memory the driver would have to page other "
                "work out to obtain. Treat GPU voice availability as unverified.")
            return out
        real = round(int(line[0].strip()) / 1024.0, 1)   # MiB -> GiB
        out["vram_free_real_gb"] = real
        out["sufficient_real"] = real >= MIN_VRAM_GB
        gap = round(float(torch_free_gb) - real, 1)
        out["vram_dispute_gb"] = gap
        disputed = gap >= _VRAM_DISPUTE_GB
        out["vram_measurement_disputed"] = disputed
        if real < _ASR_WORKING_SET_GB:
            out["contended"] = True
            out["contention_detail"] = (
                f"Only {real}GB of VRAM is genuinely free ({torch_free_gb}GB is "
                f"reachable but the driver would page other work out to get it). "
                f"GPU voice needs about {_ASR_WORKING_SET_GB}GB, so it will "
                f"compete with whatever model is loaded. Measured cost on this "
                f"card: local replies slowed roughly 10x while the voice models "
                f"were held, and recovered when released.")
        elif disputed:
            # Above the working set, so not "contended" in the old sense — but
            # the two authorities disagree by enough that "ready" would be
            # overclaiming. Name the gap instead of picking a winner.
            out["contended"] = True
            out["contention_detail"] = (
                f"GPU memory cannot be measured reliably right now: torch "
                f"reports {torch_free_gb}GB free and nvidia-smi reports "
                f"{real}GB — a {gap}GB disagreement. Both are correct about "
                f"different questions (torch counts memory it could obtain by "
                f"making the driver page other work out; nvidia-smi counts "
                f"memory genuinely unused). GPU voice needs about "
                f"{_ASR_WORKING_SET_GB}GB. Going ahead is your call: it will "
                f"run, and if the smaller figure is the true one it will slow "
                f"whatever model is currently resident.")
    except Exception as e:
        out["vram_measurement_disputed"] = True
        out["contention_detail"] = (
            f"GPU memory reading could not be corroborated ({type(e).__name__}). "
            f"Only torch's {torch_free_gb}GB figure is available.")
        log.warning("VRAM contention probe failed: %s: %s", type(e).__name__, e)
    return out


def gpu_tier_ready(fresh: bool = False) -> bool:
    """True when the GPU tier can actually run: NeMo + torch installed AND a
    CUDA GPU with sufficient free VRAM is present. Used by the engine's tier
    resolver and by provider availability / health. Pass ``fresh=True`` at
    the moment a model is actually about to load (an admission decision);
    everything else reads the cached probe."""
    if not nemo_deps_installed():
        return False
    g = gpu_status(fresh=fresh)
    return bool(g.get("cuda") and g.get("sufficient"))


def nemo_models_ready() -> bool:
    """Best-effort: have the NeMo checkpoints been downloaded yet?

    NeMo/HF cache layout varies; we treat "any .nemo file under NEMO_DIR" as
    downloaded. Conservative — a false "not ready" just re-checks the cache.

    NEMO_DIR is only where the loaders POINT HF_HOME, and they do that with
    ``setdefault`` — so when HF_HOME is already set in the environment (or the
    checkpoints were fetched by anything that did not go through ``load()``),
    the files land in the ordinary HF cache instead and this returned False
    with 2.5GB of correctly-downloaded models sitting on disk. Check both.
    """
    def _has_ckpt(root):
        try:
            if not root.exists():
                return False
            for p in root.rglob("*"):
                if p.is_file() and p.suffix in (".nemo", ".ckpt"):
                    return True
        except Exception:
            pass
        return False

    if _has_ckpt(NEMO_DIR):
        return True
    # The default HF hub cache, and an explicit HF_HOME if one is set.
    roots = []
    try:
        env_home = os.environ.get("HF_HOME")
        if env_home:
            roots.append(Path(env_home) / "hub")
        roots.append(Path.home() / ".cache" / "huggingface" / "hub")
    except Exception:
        pass
    return any(_has_ckpt(r) for r in roots)


# ═══════════════════════════════════════════════════════════════════════════
#  Audio helper — float waveform → PCM16 (NeMo TTS emits float32 -1..1)
# ═══════════════════════════════════════════════════════════════════════════

def _float_to_pcm16(samples) -> bytes:
    """Convert a float32 waveform (range ~[-1,1]) to mono PCM16 LE bytes.

    Accepts a numpy array or any iterable of floats. Clamps out-of-range values
    so a hot synthesizer can't wrap-around into noise.
    """
    if samples is None:
        return b""
    try:
        import numpy as np
        arr = np.asarray(samples, dtype="float32").reshape(-1)
        arr = np.clip(arr, -1.0, 1.0)
        return (arr * 32767.0).astype("<i2").tobytes()
    except Exception:
        out = array.array("h")
        for s in samples:
            v = int(max(-1.0, min(1.0, float(s))) * 32767.0)
            out.append(v)
        return out.tobytes()


def _hyp_text(hyps) -> str:
    """Normalize NeMo's various transcribe() return shapes to a single string.

    NeMo has returned, across versions: list[str], list[Hypothesis] (with
    ``.text``), or a nested ``(best, all)`` tuple. Be liberal in what we accept.
    """
    if hyps is None:
        return ""
    if isinstance(hyps, str):
        return hyps.strip()
    # (best_hypotheses, all_hypotheses) tuple — take the first element.
    if isinstance(hyps, tuple) and hyps:
        hyps = hyps[0]
    if isinstance(hyps, (list, tuple)):
        parts = []
        for h in hyps:
            if isinstance(h, str):
                parts.append(h)
            else:
                t = getattr(h, "text", None)
                if t:
                    parts.append(t)
        return " ".join(p for p in parts if p).strip()
    return str(getattr(hyps, "text", hyps) or "").strip()


# ═══════════════════════════════════════════════════════════════════════════
#  ASR backend — Nemotron-3.5 streaming (GPU). Same interface as WhisperASR.
# ═══════════════════════════════════════════════════════════════════════════

class NeMoASR:
    """NVIDIA Nemotron streaming ASR. Loads lazily; transcribes 16 kHz PCM16.

    Interface-compatible with ``services.local_voice.WhisperASR``:
    ``load(progress)`` and ``transcribe(pcm16_16k) -> str``. The WS orchestrator
    feeds it VAD-endpointed utterances (the same shape the Tier-1 path uses), so
    no client change is needed. Cache-aware streaming chunking is enabled on the
    model when supported, ready for a future true-partials path.
    """

    def __init__(self, model_name=NEMO_ASR_MODEL):
        self.model_name = model_name or NEMO_ASR_MODEL
        self.model_size = self.model_name           # parity with WhisperASR.model_size
        self._model = None
        self._device = "cuda"
        self._lock = threading.Lock()

    def load(self, progress=None):
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            # ONLY say "downloading" when something is actually going to be
            # downloaded. This line used to fire unconditionally on every GPU
            # voice activation, so a user with 2.9 GB of correctly-cached
            # checkpoints was told Friday was fetching 1.5 GB every single time
            # he opened GPU voice — which is indistinguishable, from the
            # outside, from Friday actually re-downloading them. The check is
            # one call that already existed.
            _cached = nemo_models_ready()
            log.info("nemo asr load model=%s cached=%s root=%s",
                     self.model_name, _cached, NEMO_DIR)
            if progress and not _cached:
                progress("Downloading NeMo voice models… (one-time setup, ~1.5GB)")
            NEMO_DIR.mkdir(parents=True, exist_ok=True)
            # Keep the heavy downloads under ~/.friday/models/nemo.
            os.environ.setdefault("NEMO_CACHE_DIR", str(NEMO_DIR))
            os.environ.setdefault("HF_HOME", str(NEMO_DIR / "hf"))
            import torch  # noqa: F401  (pragma: requires GPU stack)
            from nemo.collections.asr.models import ASRModel
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            if progress:
                progress(f"Loading streaming ASR ({self.model_name})…")
            model = ASRModel.from_pretrained(model_name=self.model_name)
            try:
                model = model.to(self._device)
                model.eval()
            except Exception:
                pass
            # Cache-aware streaming chunk size (latency dial). Best-effort: not
            # all checkpoints expose this setter.
            try:
                model.encoder.set_default_att_context_size(NEMO_ATT_CONTEXT_SIZE)
            except Exception:
                pass
            self._model = model

    def transcribe(self, pcm16_16k: bytes) -> str:
        if not pcm16_16k:
            return ""
        self.load()
        import numpy as np
        audio = np.frombuffer(pcm16_16k, dtype=np.int16).astype("float32") / 32768.0
        try:
            import torch
            with torch.no_grad():
                hyps = self._model.transcribe([audio], batch_size=1, verbose=False)
        except TypeError:
            # Older signatures don't accept verbose=/batch_size=.
            hyps = self._model.transcribe([audio])
        return _hyp_text(hyps)


# ═══════════════════════════════════════════════════════════════════════════
#  TTS backend — NeMo FastPitch + HiFi-GAN (GPU). Same interface as PiperTTS.
# ═══════════════════════════════════════════════════════════════════════════

class NeMoTTS:
    """NeMo FastPitch + HiFi-GAN TTS → 24 kHz PCM16 mono bytes (playback-ready).

    Interface-compatible with ``services.local_voice.PiperTTS``: ``load(progress)``
    and ``synthesize(text) -> bytes``. Produces noticeably better prosody than
    Piper at the cost of the GPU + torch the ASR already pulls in. Output is
    resampled 22.05 kHz → 24 kHz so it flows through the same worklet, exactly
    like the Piper path.
    """

    def __init__(self, voice="fastpitch-hifigan"):
        self.voice = voice or "fastpitch-hifigan"
        self._spec = None     # FastPitch spectrogram generator
        self._voc = None      # HiFi-GAN vocoder
        self._device = "cuda"
        self._lock = threading.Lock()

    def load(self, progress=None):
        if self._spec is not None and self._voc is not None:
            return
        with self._lock:
            if self._spec is not None and self._voc is not None:
                return
            NEMO_DIR.mkdir(parents=True, exist_ok=True)
            os.environ.setdefault("NEMO_CACHE_DIR", str(NEMO_DIR))
            os.environ.setdefault("HF_HOME", str(NEMO_DIR / "hf"))
            import torch
            from nemo.collections.tts.models import FastPitchModel, HifiGanModel
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            if progress:
                progress("Loading NeMo voice (FastPitch + HiFi-GAN)…")
            spec = FastPitchModel.from_pretrained(model_name=NEMO_FASTPITCH_MODEL)
            voc = HifiGanModel.from_pretrained(model_name=NEMO_HIFIGAN_MODEL)
            for m in (spec, voc):
                try:
                    m.to(self._device)
                    m.eval()
                except Exception:
                    pass
            self._spec, self._voc = spec, voc

    def _native_rate(self) -> int:
        # FastPitch+HiFi-GAN English models synthesize at 22.05 kHz.
        try:
            return int(self._voc.cfg.sample_rate)
        except Exception:
            return NEMO_TTS_NATIVE_RATE

    def synthesize(self, text: str) -> bytes:
        """Synthesize `text` → 24 kHz PCM16 mono bytes (playback-ready)."""
        if not text or not str(text).strip():
            return b""
        self.load()
        import torch
        with torch.no_grad():
            tokens = self._spec.parse(str(text))
            spectrogram = self._spec.generate_spectrogram(tokens=tokens)
            audio = self._voc.convert_spectrogram_to_audio(spec=spectrogram)
        try:
            wav = audio.to("cpu").detach().numpy().reshape(-1)
        except Exception:
            wav = audio
        pcm = _float_to_pcm16(wav)
        return _resample_pcm16(pcm, self._native_rate(), PLAYBACK_RATE)


# ═══════════════════════════════════════════════════════════════════════════
#  Health — the nemo-local provider's status block
# ═══════════════════════════════════════════════════════════════════════════

def nemo_health() -> dict:
    """Tier-2 status for provider_health (nemo-local) + /api/health/full.

    Status ladder (most → least ready):
      ok            — deps + CUDA + VRAM + checkpoints downloaded
      needs_download— deps + GPU ready, models not fetched yet
      down          — deps installed but no usable CUDA GPU / too little VRAM
      missing       — torch/NeMo not installed (Tier-2 opt-in not done)
    Never raises.
    """
    try:
        deps = nemo_deps_status()
        g = gpu_status()
        if not nemo_deps_installed():
            # Name the ACTUAL missing piece. "Opt-in to the GPU tier" is useless
            # advice to someone who already opted in and is missing one wheel —
            # and in the nltk case NeMo's own error blamed an unsafe target, so
            # the honest name is the only way anyone finds it.
            _missing = [k for k in ("torch", "nemo", "nltk") if not deps.get(k)]
            if deps.get("nemo") and deps.get("torch") and not deps.get("nltk"):
                _detail = ("NeMo GPU voice can't synthesise: `nltk` is missing, "
                           "which FastPitch needs for grapheme-to-phoneme. NeMo "
                           "reports this as an 'unsafe target' error, not a "
                           "missing module. Fix: pip install nltk")
            else:
                _detail = ("NeMo GPU voice not installed (missing: "
                           + ", ".join(_missing) + ") — opt-in "
                           "`.[voice-local-gpu]` + a torch-CUDA wheel")
            return {
                "engine": "nvidia-nemo", "status": "missing",
                "detail": _detail,
                "deps": deps, "gpu": g, "available": False, "models_ready": False,
            }
        if not g.get("cuda"):
            return {
                "engine": "nvidia-nemo", "status": "down",
                "detail": "NeMo installed but no CUDA GPU available — using Tier-1 (CPU)",
                "deps": deps, "gpu": g, "available": False, "models_ready": False,
            }
        if not g.get("sufficient"):
            _free = (g.get("vram_free_real_gb")
                     if g.get("vram_free_real_gb") is not None
                     else g.get("vram_free_gb"))
            return {
                "engine": "nvidia-nemo", "status": "down",
                "detail": (f"insufficient VRAM ({_free}GB genuinely free; "
                           f"need ≥{MIN_VRAM_GB}GB) — using Tier-1 (CPU)"),
                "deps": deps, "gpu": g, "available": False, "models_ready": False,
            }
        ready = nemo_models_ready()
        # "ready" is a claim about this machine right now. When the VRAM reading
        # is disputed, the honest status is that it can run but the headroom is
        # unverified — not a green light. The user still gets to choose; the
        # difference is that they are choosing rather than being told.
        if g.get("vram_measurement_disputed"):
            detail = ("NeMo GPU voice can run, but free VRAM cannot be measured "
                      "reliably right now" if ready
                      else "NeMo models not downloaded yet (one-time, ~1.5GB)")
        else:
            detail = ("NeMo GPU voice ready" if ready
                      else "NeMo models not downloaded yet (one-time, ~1.5GB)")
        # The tier runs, but on a card that is already holding a model it runs
        # at a cost the user cannot see from anywhere else. Say so here rather
        # than letting it surface as "Friday got slow after I turned voice on".
        if g.get("contended"):
            detail += " — note: " + g.get("contention_detail", "GPU memory is contended")
        return {
            "engine": "nvidia-nemo",
            "status": "ok" if ready else "needs_download",
            "detail": detail,
            "contended": bool(g.get("contended")),
            "vram_measurement_disputed": bool(g.get("vram_measurement_disputed")),
            "deps": deps, "gpu": g, "available": True, "models_ready": ready,
        }
    except Exception as e:
        return {"engine": "nvidia-nemo", "status": "error",
                "detail": str(e)[:160], "available": False, "models_ready": False}
