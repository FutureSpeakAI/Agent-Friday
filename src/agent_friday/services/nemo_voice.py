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
docs/TIER2_NEMO_VOICE.md + tests/MANUAL_TEST_PROCEDURES.md.
"""
from __future__ import annotations

import array
import os
import threading
from pathlib import Path

# Reuse the Tier-1 audio + probing helpers so the two tiers share one resample /
# dependency-probe implementation (no duplication, identical 24 kHz output).
from agent_friday.services.local_voice import (
    PLAYBACK_RATE,
    _module_installed,
    _resample_pcm16,
)

_HOME = Path(os.environ.get("FRIDAY_HOME") or Path.home())
# Cache the (large) NeMo/HF checkpoints under ~/.friday/models/nemo so they
# survive reinstalls, are inspectable, and never pollute the Tier-1
# ~/.friday/local_voice dir.
NEMO_DIR = _HOME / ".friday" / "models" / "nemo"

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

    ``nltk`` is in this list because of a real, reproduced failure, and the way
    it failed is worth keeping written down.

    FastPitch's checkpoint config names its g2p as
    ``nemo.collections.tts.torch.g2ps.EnglishG2p``. NeMo resolves that path
    through an allow-list; the resolver imports the target, and the import
    raised ``ModuleNotFoundError: No module named 'nltk'``. An import error is
    indistinguishable from a disallowed target to that code, so the allow-list
    reported the target as unsafe and NeMo raised:

        UnsafeTargetError: Instantiation of unsafe target
        'nemo.collections.tts.torch.g2ps.EnglishG2p' is blocked ...
        to prevent potential arbitrary code execution.

    A missing pip package was therefore reported as a SECURITY refusal — which
    is why this read as "GPU voice is broken" rather than "one dependency is
    missing" for as long as it did. Meanwhile the tier gate consulted only
    torch/NeMo/CUDA/VRAM, all of which passed, so health cheerfully reported
    "NeMo GPU voice ready" while the TTS half could not load at all. Checking
    it here is what makes that claim honest. (Fixed 2026-08-25; installing nltk
    made TTS load in 52s and synthesise 3.3s of audio in 0.96s.)
    """
    d = nemo_deps_status()
    return bool(d["nemo"] and d["torch"] and d["nltk"])


def gpu_status() -> dict:
    """Detect CUDA availability + VRAM for GPU-tier auto-selection.

    Prefers torch (accurate *free* VRAM via ``cuda.mem_get_info``); falls back to
    ``nvidia-smi`` (total VRAM only) through ollama_manager when torch isn't
    installed yet. Never raises. ``sufficient`` reflects ``MIN_VRAM_GB``.
    """
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
                # Measured on this machine 2026-08-24, same card, same second,
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
                # NeMo ASR session wants DID succeed against 0.4 GB of real free
                # memory -- and the resident brain went from 0.30 s to 3.08 s per
                # turn, a 10.3x slowdown, sustained for as long as the memory was
                # held, recovering to 0.43 s once released. Nothing crashed. The
                # cost was invisible and entirely in latency.
                #
                # So the gate no longer decides on torch's number alone. It keeps
                # torch's answer (that IS what allocation will see) and adds the
                # real figure, so callers can say what it will cost instead of
                # discovering it as a mysteriously slow Friday.
                info.update(_contention_probe(info["vram_free_gb"]))
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
#: note, and is the size that was actually allocated in the 2026-08-24 test.
_ASR_WORKING_SET_GB = 3.0


def _contention_probe(torch_free_gb: float) -> dict:
    """Ask nvidia-smi what is *genuinely* free, and say whether we'd contend.

    Returns keys merged into ``gpu_status()``. Never raises and never blocks the
    tier: a machine where the GPU voice models would displace something is still
    a machine where they RUN. The point is to make the trade visible, not to
    make it for the user.
    """
    out = {"vram_free_real_gb": None, "contended": False, "contention_detail": ""}
    try:
        import subprocess
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=6)
        line = (r.stdout or "").strip().splitlines()
        if not line:
            return out
        real = round(int(line[0].strip()) / 1024.0, 1)   # MiB -> GiB
        out["vram_free_real_gb"] = real
        if real < _ASR_WORKING_SET_GB:
            out["contended"] = True
            out["contention_detail"] = (
                f"Only {real}GB of VRAM is genuinely free ({torch_free_gb}GB is "
                f"reachable but the driver would page other work out to get it). "
                f"GPU voice needs about {_ASR_WORKING_SET_GB}GB, so it will "
                f"compete with whatever model is loaded. Measured cost on this "
                f"card: local replies slowed roughly 10x while the voice models "
                f"were held, and recovered when released.")
    except Exception:
        pass
    return out


def gpu_tier_ready() -> bool:
    """True when the GPU tier can actually run: NeMo + torch installed AND a
    CUDA GPU with sufficient free VRAM is present. Used by the engine's tier
    resolver and by provider availability / health."""
    if not nemo_deps_installed():
        return False
    g = gpu_status()
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
            if progress:
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
            return {
                "engine": "nvidia-nemo", "status": "down",
                "detail": (f"insufficient VRAM ({g.get('vram_free_gb')}GB free; "
                           f"need ≥{MIN_VRAM_GB}GB) — using Tier-1 (CPU)"),
                "deps": deps, "gpu": g, "available": False, "models_ready": False,
            }
        ready = nemo_models_ready()
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
            "deps": deps, "gpu": g, "available": True, "models_ready": ready,
        }
    except Exception as e:
        return {"engine": "nvidia-nemo", "status": "error",
                "detail": str(e)[:160], "available": False, "models_ready": False}
