"""
Agent Friday — Local Voice Engine (Tier-1, CPU-only)

Offline, provider-agnostic voice: **faster-whisper** ASR + **Piper** TTS, with
energy/Silero VAD endpointing. This is the universal CPU path — no torch, no
CUDA — that runs on every machine. It lives ALONGSIDE Gemini Live and is the
DEFAULT engine (cloud is opt-in), per Stephen's ethos: "Local is the default,
cloud is the opt in, always."

Pipeline shape (the brain is NOT in here — it's the existing LLM router):

    mic → VAD → [faster-whisper ASR] → text → [model_router brain] → text
        → [Piper TTS] → 24 kHz PCM16 → existing friday-pcm-player worklet → speaker

Design goals
------------
* **Graceful degradation** — importing this module NEVER raises and never pulls
  a heavy dependency. If faster-whisper / piper aren't installed, the engine
  reports ``available=False`` and callers fall back to text/cloud.
* **Lazy models** — checkpoints download on first activation (not at install),
  with a progress callback so the UI can show a one-time "downloading voice
  models…" orb.
* **Testability** — the ASR and TTS backends are swappable attributes on the
  singleton (``engine._asr`` / ``engine._tts``). Tests inject a ``FakeASR`` /
  ``FakeTTS`` exactly the way the suite stubs the LLM entry points, so the whole
  orchestration runs in CI with no model and no GPU.
* **Identical client contract** — TTS output is resampled server-side to 24 kHz
  PCM16 mono so it flows through the same ``{type:'audio'}`` frames + worklet +
  analyser the Gemini path uses. The holographic cube is therefore source-
  agnostic by construction (see VOICE_INTEGRATION_SPEC §11).
"""
from __future__ import annotations

import array
import io
import math
import os
import threading
import time
import wave
from pathlib import Path

# Where downloaded checkpoints live. Honors $HOME redirection used by tests.
_HOME = Path(os.environ.get("FRIDAY_HOME") or Path.home())
LOCAL_VOICE_DIR = _HOME / ".friday" / "local_voice"
WHISPER_DIR = LOCAL_VOICE_DIR / "whisper"
PIPER_DIR = LOCAL_VOICE_DIR / "piper"

# Audio rates. The browser captures 16 kHz mono PCM16; the worklet plays 24 kHz.
ASR_RATE = 16000
PLAYBACK_RATE = 24000

# Defaults (settings-overridable). whisper "small" is the quality/latency sweet
# spot on CPU; "base" is the lighter option. Piper amy-medium is a clean default.
DEFAULT_WHISPER_MODEL = "small"
DEFAULT_PIPER_VOICE = "en_US-amy-medium"

# Piper voices are published as <name>.onnx + <name>.onnx.json on Hugging Face
# (rhasspy/piper-voices). We resolve the on-disk path lazily and download on
# first use if absent. Path layout inside the repo: en/en_US/amy/medium/...
_PIPER_HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
_PIPER_VOICE_PATHS = {
    "en_US-amy-medium": "en/en_US/amy/medium/en_US-amy-medium.onnx",
    "en_US-lessac-medium": "en/en_US/lessac/medium/en_US-lessac-medium.onnx",
}

_TESTING = bool(os.environ.get("FRIDAY_TESTING"))


# ═══════════════════════════════════════════════════════════════════════════
#  Dependency / availability probing (never imports the heavy libs eagerly)
# ═══════════════════════════════════════════════════════════════════════════

def _module_installed(name: str) -> bool:
    """True if an importable module is installed, WITHOUT importing it (cheap)."""
    try:
        import importlib.util
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def deps_status() -> dict:
    """Which Tier-1 voice dependencies are importable on this machine."""
    return {
        "faster_whisper": _module_installed("faster_whisper"),
        "piper": _module_installed("piper"),
        "onnxruntime": _module_installed("onnxruntime"),
        "silero_vad": _module_installed("silero_vad"),
    }


def deps_installed() -> bool:
    """True when the minimum Tier-1 stack (ASR + TTS) is importable."""
    d = deps_status()
    return bool(d["faster_whisper"] and d["piper"])


# ═══════════════════════════════════════════════════════════════════════════
#  Audio helpers — pure-Python, stdlib only (no numpy/audioop dependency)
# ═══════════════════════════════════════════════════════════════════════════

def _resample_pcm16(pcm: bytes, in_rate: int, out_rate: int) -> bytes:
    """Linear-interpolation resample of mono PCM16 little-endian bytes.

    stdlib-only (``audioop`` was removed in Python 3.13, so we don't use it).
    Good enough for speech playback and dependency-free, which is the whole
    point of the Tier-1 path.
    """
    if not pcm or in_rate == out_rate:
        return pcm
    src = array.array("h")
    src.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    n_in = len(src)
    if n_in == 0:
        return b""
    n_out = max(1, int(n_in * out_rate / in_rate))
    out = array.array("h", bytes(2 * n_out))
    ratio = in_rate / out_rate
    for i in range(n_out):
        pos = i * ratio
        i0 = int(pos)
        i1 = min(i0 + 1, n_in - 1)
        frac = pos - i0
        out[i] = int(src[i0] * (1.0 - frac) + src[i1] * frac)
    return out.tobytes()


def _pcm16_rms(pcm: bytes) -> float:
    """Root-mean-square amplitude (0..32768) of mono PCM16 bytes."""
    if not pcm:
        return 0.0
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if not samples:
        return 0.0
    acc = 0
    for s in samples:
        acc += s * s
    return math.sqrt(acc / len(samples))


def _wav_to_pcm16(wav_bytes: bytes):
    """Extract (pcm16_mono_bytes, sample_rate) from WAV container bytes."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        rate = wf.getframerate()
        n_ch = wf.getnchannels()
        width = wf.getsampwidth()
        frames = wf.readframes(wf.getnframes())
    if width != 2:
        # Only 16-bit is handled; anything else is returned as-is best-effort.
        return frames, rate
    if n_ch == 2:
        # Downmix stereo → mono by averaging channels.
        stereo = array.array("h")
        stereo.frombytes(frames[: len(frames) - (len(frames) % 4)])
        mono = array.array("h", bytes(2 * (len(stereo) // 2)))
        for i in range(len(mono)):
            mono[i] = (stereo[2 * i] + stereo[2 * i + 1]) // 2
        frames = mono.tobytes()
    return frames, rate


# ═══════════════════════════════════════════════════════════════════════════
#  VAD endpointer — accumulate speech, fire on trailing silence
# ═══════════════════════════════════════════════════════════════════════════

class VADEndpointer:
    """Energy-based voice-activity endpointer (Silero-upgradeable).

    Fed 16 kHz mono PCM16 chunks via :meth:`feed`. Returns the full accumulated
    utterance bytes when end-of-speech is detected (``silence_ms`` of trailing
    quiet after speech started), else ``None``. Mirrors the cloud tuning:
    ~800 ms trailing silence, LOW start sensitivity so speaker echo doesn't
    false-trigger.

    Energy-based by default (zero deps, CI-safe). When ``silero-vad`` is
    installed it is preferred for the per-chunk speech decision; the
    accumulate/endpoint logic is identical either way.
    """

    def __init__(self, rate=ASR_RATE, silence_ms=800, start_rms=600.0,
                 min_speech_ms=200):
        self.rate = rate
        self.silence_ms = silence_ms
        self.start_rms = start_rms
        self.min_speech_ms = min_speech_ms
        self._buf = bytearray()
        self._in_speech = False
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        self._silero = None
        self._silero_tried = False

    def _chunk_ms(self, pcm: bytes) -> float:
        return (len(pcm) / 2) / self.rate * 1000.0

    def _is_speech(self, pcm: bytes) -> bool:
        # Prefer Silero when available; fall back to RMS energy gate.
        sv = self._maybe_silero()
        if sv is not None:
            try:
                return sv(pcm)
            except Exception:
                pass
        return _pcm16_rms(pcm) >= self.start_rms

    def _maybe_silero(self):
        if self._silero_tried:
            return self._silero
        self._silero_tried = True
        if not _module_installed("silero_vad"):
            return None
        try:  # pragma: no cover - exercised only when silero-vad is installed
            from silero_vad import load_silero_vad
            import numpy as np  # silero pulls numpy
            model = load_silero_vad(onnx=True)

            def _decide(pcm: bytes) -> bool:
                arr = np.frombuffer(pcm, dtype=np.int16).astype("float32") / 32768.0
                if arr.size < 512:
                    return _pcm16_rms(pcm) >= self.start_rms
                import torch
                prob = float(model(torch.from_numpy(arr[:512]), self.rate).item())
                return prob >= 0.5

            self._silero = _decide
        except Exception:
            self._silero = None
        return self._silero

    def feed(self, pcm: bytes):
        """Feed one chunk. Returns finalized utterance bytes on endpoint, else None."""
        if not pcm:
            return None
        dur = self._chunk_ms(pcm)
        speech = self._is_speech(pcm)
        if speech:
            self._in_speech = True
            self._speech_ms += dur
            self._silence_ms = 0.0
            self._buf.extend(pcm)
            return None
        # Non-speech chunk.
        if self._in_speech:
            self._silence_ms += dur
            self._buf.extend(pcm)  # keep a little trailing audio for the decoder
            if (self._silence_ms >= self.silence_ms
                    and self._speech_ms >= self.min_speech_ms):
                return self.flush()
        return None

    def flush(self):
        """Return accumulated audio and reset, or None if nothing buffered."""
        out = bytes(self._buf)
        self.reset()
        return out or None

    def reset(self):
        self._buf = bytearray()
        self._in_speech = False
        self._speech_ms = 0.0
        self._silence_ms = 0.0


# ═══════════════════════════════════════════════════════════════════════════
#  ASR backend — faster-whisper (CTranslate2, CPU INT8)
# ═══════════════════════════════════════════════════════════════════════════

class WhisperASR:
    """faster-whisper ASR. Loads lazily; transcribes 16 kHz mono PCM16 bytes."""

    def __init__(self, model_size=DEFAULT_WHISPER_MODEL):
        self.model_size = model_size or DEFAULT_WHISPER_MODEL
        self._model = None
        self._lock = threading.Lock()

    def load(self, progress=None):
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            if progress:
                progress(f"Loading speech model ({self.model_size})…")
            from faster_whisper import WhisperModel
            WHISPER_DIR.mkdir(parents=True, exist_ok=True)
            # CPU INT8 — the whole point of Tier-1. download_root keeps the
            # checkpoint under ~/.friday so it survives and is inspectable.
            self._model = WhisperModel(
                self.model_size, device="cpu", compute_type="int8",
                download_root=str(WHISPER_DIR))

    def transcribe(self, pcm16_16k: bytes) -> str:
        if not pcm16_16k:
            return ""
        self.load()
        import numpy as np
        audio = np.frombuffer(pcm16_16k, dtype=np.int16).astype("float32") / 32768.0
        segments, _info = self._model.transcribe(
            audio, language=None, beam_size=1, vad_filter=False)
        return "".join(seg.text for seg in segments).strip()


# ═══════════════════════════════════════════════════════════════════════════
#  TTS backend — Piper (VITS → ONNX, CPU)
# ═══════════════════════════════════════════════════════════════════════════

class PiperTTS:
    """Piper TTS. Loads a voice lazily; synthesizes → 24 kHz PCM16 mono bytes."""

    def __init__(self, voice=DEFAULT_PIPER_VOICE):
        self.voice = voice or DEFAULT_PIPER_VOICE
        self._piper = None
        self._lock = threading.Lock()

    def _voice_path(self) -> Path:
        rel = _PIPER_VOICE_PATHS.get(self.voice)
        fname = (rel.split("/")[-1] if rel else f"{self.voice}.onnx")
        return PIPER_DIR / fname

    def _ensure_voice_file(self, progress=None) -> Path:
        path = self._voice_path()
        cfg = path.with_suffix(path.suffix + ".json")
        if path.exists() and cfg.exists():
            return path
        rel = _PIPER_VOICE_PATHS.get(self.voice)
        if not rel:
            raise FileNotFoundError(
                f"Piper voice '{self.voice}' not bundled and not on disk at {path}")
        PIPER_DIR.mkdir(parents=True, exist_ok=True)
        import urllib.request
        for url, dest in ((f"{_PIPER_HF_BASE}/{rel}", path),
                          (f"{_PIPER_HF_BASE}/{rel}.json", cfg)):
            if dest.exists():
                continue
            if progress:
                progress(f"Downloading voice ({self.voice})…")
            tmp = dest.with_suffix(dest.suffix + ".part")
            urllib.request.urlretrieve(url, str(tmp))
            tmp.replace(dest)
        return path

    def load(self, progress=None):
        if self._piper is not None:
            return
        with self._lock:
            if self._piper is not None:
                return
            from piper import PiperVoice
            path = self._ensure_voice_file(progress=progress)
            self._piper = PiperVoice.load(str(path))

    def _native_rate(self) -> int:
        try:
            return int(self._piper.config.sample_rate)
        except Exception:
            return 22050

    def synthesize(self, text: str) -> bytes:
        """Synthesize `text` → 24 kHz PCM16 mono bytes (playback-ready)."""
        if not text or not str(text).strip():
            return b""
        self.load()
        raw = self._synthesize_native(text)
        return _resample_pcm16(raw, self._native_rate(), PLAYBACK_RATE)

    def _synthesize_native(self, text: str) -> bytes:
        """Return PCM16 bytes at the voice's native sample rate.

        Handles both the streaming-raw API and the write-to-WAV API across
        piper-tts versions, plus AudioChunk objects from newer releases.
        """
        piper = self._piper
        # Newer piper: synthesize() yields AudioChunk objects.
        if hasattr(piper, "synthesize"):
            try:
                out = bytearray()
                produced = False
                for chunk in piper.synthesize(text):
                    produced = True
                    data = getattr(chunk, "audio_int16_bytes", None)
                    if data is None:
                        data = getattr(chunk, "audio", None)
                    if data is not None:
                        out.extend(data if isinstance(data, (bytes, bytearray)) else bytes(data))
                if produced:
                    return bytes(out)
            except TypeError:
                pass  # signature mismatch → fall through to other APIs
        # Older piper: synthesize_stream_raw() yields raw PCM bytes.
        if hasattr(piper, "synthesize_stream_raw"):
            out = bytearray()
            for b in piper.synthesize_stream_raw(text):
                out.extend(b)
            return bytes(out)
        # Fallback: synthesize to a WAV file object.
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            piper.synthesize(text, wf)
        pcm, _rate = _wav_to_pcm16(buf.getvalue())
        return pcm


# ═══════════════════════════════════════════════════════════════════════════
#  Engine — ties ASR + TTS + readiness together (the public surface)
# ═══════════════════════════════════════════════════════════════════════════

class LocalVoiceEngine:
    """Singleton wiring the swappable ASR + TTS backends and readiness state.

    Two tiers share this one engine and the one ``/ws/voice-local`` contract:
      * **cpu** (Tier-1, default) — faster-whisper + Piper, this module.
      * **gpu** (Tier-2, premium) — Nemotron + NeMo TTS, ``services.nemo_voice``.

    The active tier is resolved from the ``voice_engine`` setting at session
    start (:meth:`select_tier_from_settings`). Switching tiers hot-swaps the
    backends — no server restart — and a GPU tier that can't load falls back to
    CPU gracefully, so the user always gets *some* local voice.

    Tests swap ``engine._asr`` / ``engine._tts`` for fakes; production code only
    touches :meth:`transcribe`, :meth:`synthesize`, :meth:`select_tier_from_settings`,
    :meth:`ensure_ready`, and :meth:`health`.
    """

    def __init__(self):
        self._asr = None
        self._tts = None
        self._ready = False
        self._tier = None          # "cpu" | "gpu" | None (unselected → cpu)
        self._lock = threading.Lock()
        # Rolling perf so users can compare Tier-1 vs Tier-2 (spec §"Performance
        # Monitoring"). Surfaced in health() + /api/health/full.
        self._perf = {
            "asr_ms": None, "asr_count": 0,        # last + count: speech→transcript
            "tts_ms": None, "tts_count": 0,        # last + count: text→audio bytes
        }

    def _settings(self):
        try:
            import core
            return core._load_settings() or {}
        except Exception:
            return {}

    # ── Tier resolution + hot-swap ──────────────────────────────────────────

    def active_tier(self) -> str:
        return self._tier or "cpu"

    def _gpu_tier_ready(self) -> bool:
        """True when the Tier-2 GPU stack can actually run (NeMo+torch+CUDA+VRAM)."""
        try:
            from services.nemo_voice import gpu_tier_ready
            return gpu_tier_ready()
        except Exception:
            return False

    def resolve_tier(self, settings=None) -> str:
        """Pick "gpu" or "cpu" from the ``voice_engine`` setting + hardware.

        * ``local-gpu`` / ``gpu`` / ``nemo`` → gpu when ready, else cpu (fallback).
        * ``auto``                            → gpu when ready, else cpu.
        * anything else (``local`` default)   → cpu (the universal Tier-1 path).
        """
        s = settings if settings is not None else self._settings()
        pref = str(s.get("voice_engine") or "local").strip().lower()
        if pref in ("local-gpu", "gpu", "nemo", "nvidia-nemo"):
            return "gpu" if self._gpu_tier_ready() else "cpu"
        if pref == "auto":
            return "gpu" if self._gpu_tier_ready() else "cpu"
        return "cpu"

    def _swap_tier(self, tier):
        """Drop current backends and arm the engine for ``tier`` (next load builds it)."""
        self._asr = None
        self._tts = None
        self._ready = False
        self._tier = tier

    def select_tier(self, tier):
        """Switch tiers if different from the loaded one (hot-swap, no restart)."""
        tier = "gpu" if str(tier).lower() in ("gpu", "local-gpu", "nemo") else "cpu"
        if tier != self._tier:
            self._swap_tier(tier)
        return self._tier

    def select_tier_from_settings(self, settings=None) -> str:
        """Resolve + select the tier for this session. Returns the active tier."""
        return self.select_tier(self.resolve_tier(settings))

    def _active_tier_deps_ok(self) -> bool:
        if self.active_tier() == "gpu":
            try:
                from services.nemo_voice import nemo_deps_installed
                return nemo_deps_installed()
            except Exception:
                return False
        return deps_installed()

    def _get_asr(self):
        if self._asr is None:
            s = self._settings()
            if self.active_tier() == "gpu":
                from services.nemo_voice import NeMoASR, NEMO_ASR_MODEL
                self._asr = NeMoASR(s.get("local_voice_gpu_asr_model") or NEMO_ASR_MODEL)
            else:
                self._asr = WhisperASR(s.get("local_voice_asr_model") or DEFAULT_WHISPER_MODEL)
        return self._asr

    def _get_tts(self):
        if self._tts is None:
            s = self._settings()
            if self.active_tier() == "gpu":
                from services.nemo_voice import NeMoTTS
                self._tts = NeMoTTS(s.get("local_voice_gpu_tts") or "fastpitch-hifigan")
            else:
                self._tts = PiperTTS(s.get("local_voice_tts_voice") or DEFAULT_PIPER_VOICE)
        return self._tts

    def available(self) -> bool:
        """Tier-1 deps importable — the universal floor for *any* local voice.

        Kept tier-agnostic on purpose: even when the user picked the GPU tier,
        local voice is "available" as long as the CPU fallback can run, so the
        engine selector never declares local voice dead because NeMo is missing.
        """
        return deps_installed()

    def models_ready(self) -> bool:
        """Best-effort check that the *Tier-1* ASR + TTS checkpoints are on disk.

        Reports the CPU tier specifically (the ``local-voice-lite`` provider's
        readiness) regardless of the active backend, so a GPU-active session
        never disturbs this answer. GPU-tier readiness is reported separately by
        ``services.nemo_voice.nemo_models_ready`` / ``nemo_health``.
        """
        try:
            whisper_ok = WHISPER_DIR.exists() and any(WHISPER_DIR.iterdir())
        except Exception:
            whisper_ok = False
        try:
            voice = self._settings().get("local_voice_tts_voice") or DEFAULT_PIPER_VOICE
            vp = PiperTTS(voice)._voice_path()
            piper_ok = vp.exists() and vp.with_suffix(vp.suffix + ".json").exists()
        except Exception:
            piper_ok = False
        return bool(whisper_ok and piper_ok)

    def ensure_ready(self, progress=None) -> bool:
        """Lazily load/download the active tier's ASR + TTS. True when usable.

        ``progress(msg)`` surfaces a one-time "downloading voice models…" orb.
        Never raises. The GPU tier degrades gracefully: if NeMo isn't ready, or
        loading it fails, the engine silently swaps to the Tier-1 CPU backend so
        the user still gets local voice (spec §6.6 / §13).
        """
        if self._ready:
            return True
        with self._lock:
            if self._ready:
                return True
            # GPU tier requested but not actually runnable → fall back to CPU
            # before we even try to import the heavy stack.
            if self.active_tier() == "gpu" and not self._gpu_tier_ready():
                if progress:
                    progress("GPU voice not ready — using local CPU voice")
                self._swap_tier("cpu")
            if not self._active_tier_deps_ok():
                return False
            try:
                self._get_asr().load(progress=progress)
                self._get_tts().load(progress=progress)
                self._ready = True
            except Exception as e:  # pragma: no cover - real model load only
                print(f"[local_voice] {self.active_tier()} model load failed: {e}")
                # GPU load failed → one graceful retry on the CPU tier.
                if self.active_tier() == "gpu" and deps_installed():
                    if progress:
                        progress("GPU voice failed to load — falling back to CPU voice")
                    self._swap_tier("cpu")
                    try:
                        self._get_asr().load(progress=progress)
                        self._get_tts().load(progress=progress)
                        self._ready = True
                    except Exception as e2:  # pragma: no cover
                        print(f"[local_voice] CPU fallback load failed: {e2}")
                        self._ready = False
                else:
                    self._ready = False
            return self._ready

    def _record(self, kind, ms):
        try:
            self._perf[f"{kind}_ms"] = round(float(ms), 1)
            self._perf[f"{kind}_count"] = self._perf.get(f"{kind}_count", 0) + 1
        except Exception:
            pass

    def perf_stats(self) -> dict:
        """Last-measured ASR/TTS latencies (ms) + counts, plus the active tier."""
        return {**self._perf, "tier": self.active_tier()}

    def transcribe(self, pcm16_16k: bytes) -> str:
        t0 = time.perf_counter()
        out = self._get_asr().transcribe(pcm16_16k)
        self._record("asr", (time.perf_counter() - t0) * 1000.0)
        return out

    def synthesize(self, text: str) -> bytes:
        """Text → 24 kHz PCM16 mono bytes (ready for the playback worklet)."""
        t0 = time.perf_counter()
        out = self._get_tts().synthesize(text)
        self._record("tts", (time.perf_counter() - t0) * 1000.0)
        return out

    def synthesize_b64(self, text: str) -> str:
        import base64
        return base64.b64encode(self.synthesize(text)).decode("ascii")

    def health(self) -> dict:
        """Status block for /api/health/full + provider_health.

        Reports the Tier-1 (``local-voice-lite``) deps/model readiness — the
        universal floor — and carries the resolved active tier, last-measured
        ASR/TTS latencies, and a best-effort Tier-2 (NeMo/GPU) sub-block so the
        UI can offer the GPU upgrade and users can compare tier performance.
        Model names are read from settings (not the live backend) so a GPU-active
        session never disturbs this answer.
        """
        deps = deps_status()
        avail = self.available()
        ready = self.models_ready() if avail else False
        if not avail:
            status, detail = "missing", "Tier-1 voice deps not installed (.[voice-local-lite])"
        elif not ready:
            status, detail = "needs_download", "voice models not downloaded yet (one-time)"
        else:
            status, detail = "ok", "local voice ready"
        s = self._settings()
        out = {
            "engine": "local-voice-lite",
            "status": status,
            "detail": detail,
            "available": avail,
            "models_ready": ready,
            "deps": deps,
            "asr_model": s.get("local_voice_asr_model") or DEFAULT_WHISPER_MODEL,
            "tts_voice": s.get("local_voice_tts_voice") or DEFAULT_PIPER_VOICE,
            "active_tier": self.active_tier(),
            "perf": self.perf_stats(),
        }
        # Tier-2 (GPU/NeMo) readiness — best-effort, never fatal to this block.
        try:
            from services.nemo_voice import nemo_health
            out["gpu"] = nemo_health()
        except Exception as e:
            out["gpu"] = {"engine": "nvidia-nemo", "status": "error",
                          "detail": str(e)[:120], "available": False}
        return out


_engine = None
_engine_lock = threading.Lock()


def get_local_voice_engine() -> LocalVoiceEngine:
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = LocalVoiceEngine()
    return _engine


def local_voice_health() -> dict:
    """Module-level convenience for health endpoints (never raises)."""
    try:
        return get_local_voice_engine().health()
    except Exception as e:
        return {"engine": "local-voice-lite", "status": "error",
                "detail": str(e)[:160], "available": False, "models_ready": False}


def split_sentences(text: str):
    """Split assistant text into speakable sentences for per-sentence TTS.

    Keeps latency low: the WS orchestrator synthesizes + streams the first
    sentence while later ones are still being produced. Deliberately simple —
    splits on sentence-final punctuation, never mid-number-ish, and strips
    markdown that would be read aloud awkwardly.
    """
    import re
    if not text:
        return []
    # Strip markdown emphasis/headers/bullets that TTS would otherwise vocalize.
    clean = re.sub(r"[*_`#>]+", "", str(text))
    clean = re.sub(r"\s+", " ", clean).strip()
    if not clean:
        return []
    parts = re.split(r"(?<=[.!?])\s+", clean)
    return [p.strip() for p in parts if p.strip()]
