"""
Agent Friday — Local Voice Engine (Tier-1, CPU-only)

Offline, provider-agnostic voice: **faster-whisper** ASR + **Piper** TTS, with
energy/Silero VAD endpointing. This is the universal CPU path — no torch, no
CUDA — that runs on every machine. It lives ALONGSIDE Gemini Live and is the
DEFAULT engine (cloud is opt-in), per the maintainer's ruling: "Local is the
default, cloud is the opt in, always."

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
  agnostic by construction.
"""
from __future__ import annotations

import array
import io
import logging
import math
import os
import threading
import time
import wave
from pathlib import Path

from agent_friday.paths import friday_home, voice_assets_dir

# Local voice had no logger at all — its entire diagnostic output was two bare
# print() calls landing in an unrotated server_stderr.log the repo elsewhere
# describes as lost. This joins the convention every other subsystem already
# uses, so records land in ~/.friday/friday.log under its existing rotation.
# See services/voice_receipt.py for the per-turn receipt built on top of it.
log = logging.getLogger("friday.local_voice")

# Where downloaded checkpoints live. Honors $HOME redirection used by tests.

_OS_MODE_TRUTHY = {"1", "true", "yes", "on"}


def _os_mode_active() -> bool:
    """True when FRIDAY_OS_MODE is on.

    Deliberately duplicated from (not imported from) agent_friday.core.
    os_mode.is_os_mode(): that module lives inside the `agent_friday.core`
    PACKAGE, and importing any name from a submodule of a package forces
    Python to execute that package's __init__.py first — a ~2600-line Flask
    app bootstrap plus a legacy `~/wiki` migration that touches the REAL
    home directory regardless of FRIDAY_HOME (see agent_friday/paths.py's
    module docstring for the full history, from PR-1 of this OS-mode
    sequence). This module is imported by `friday doctor` / `friday models`
    (agent_friday/cli.py, lazily, inside those command functions) and by
    services/prewarm.py — none of which otherwise import agent_friday.core,
    and none of which should start paying that import's real-home-touching
    cost just to answer "is OS mode on?". See core/os_mode.py for the
    canonical version used everywhere already inside the core-initialized
    server process.
    """
    return os.environ.get("FRIDAY_OS_MODE", "").strip().lower() in _OS_MODE_TRUTHY


LOCAL_VOICE_DIR = friday_home() / "local_voice"
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
                 min_speech_ms=200, use_silero=None):
        self.rate = rate
        self.silence_ms = silence_ms
        self.start_rms = start_rms
        self.min_speech_ms = min_speech_ms
        # use_silero: None (default) = auto — prefer Silero when installed,
        # skipped under FRIDAY_TESTING. False = always the energy gate. Tests
        # pass False so their synthetic tones don't depend on which VAD backend
        # happens to be installed (a real model rightly rejects square waves).
        self._use_silero = use_silero
        self._buf = bytearray()
        self._in_speech = False
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        self._silero = None
        self._silero_tried = False
        # Pre-roll: the last few non-speech chunks, prepended when speech
        # starts. Without it, the chunk in which the utterance BEGINS (often
        # classified non-speech when the onset sits late in the chunk) was
        # discarded outright, clipping the first syllables off transcripts.
        self._preroll = []
        self._preroll_max = 3  # ~250 ms at the client's ~85 ms chunks

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
        if self._use_silero is False:
            return None
        if self._silero_tried:
            return self._silero
        self._silero_tried = True
        # Deterministic under test: a REAL VAD model rightly refuses the
        # suite's synthetic square-wave "speech", so tests exercise the RMS
        # endpointing logic instead.
        if _TESTING or os.environ.get("FRIDAY_TESTING"):
            return None
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
                # Score EVERY full 512-sample window and max-pool. The client
                # sends ~1365-sample (~85 ms) chunks; judging only arr[:512]
                # (the first 32 ms) classified chunks whose speech starts later
                # as silence — clipping utterance onsets and endpointing
                # mid-sentence. Sequential windows also keep Silero's streaming
                # state fed with contiguous audio instead of disjoint slices.
                best = 0.0
                for off in range(0, arr.size - 511, 512):
                    prob = float(model(torch.from_numpy(arr[off:off + 512]), self.rate).item())
                    if prob > best:
                        best = prob
                return best >= 0.5

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
            if not self._in_speech and self._preroll:
                # Utterance onset: include the immediately-preceding audio so
                # the decoder sees the first syllables the VAD missed.
                for _pre in self._preroll:
                    self._buf.extend(_pre)
                self._preroll.clear()
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
        else:
            self._preroll.append(pcm)
            if len(self._preroll) > self._preroll_max:
                self._preroll.pop(0)
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
        self._preroll = []


# ═══════════════════════════════════════════════════════════════════════════
#  ASR backend — faster-whisper (CTranslate2, CPU INT8)
# ═══════════════════════════════════════════════════════════════════════════

class WhisperASR:
    """faster-whisper ASR. Loads lazily; transcribes 16 kHz mono PCM16 bytes."""

    def __init__(self, model_size=DEFAULT_WHISPER_MODEL):
        self.model_size = model_size or DEFAULT_WHISPER_MODEL
        self._model = None
        self._lock = threading.Lock()

    def _download_root(self) -> Path:
        """Where faster-whisper should look for (and, if absent, download)
        this model's checkpoint.

        Under OS mode, prefer a baked-in copy at FRIDAY_VOICE_ASSETS/whisper
        if one exists — the sealed kiosk image ships models there precisely
        so faster_whisper's own cache lookup finds them and never reaches
        Hugging Face. faster_whisper treats download_root as a read-through
        cache (it only fetches what's missing), so pointing it at a
        directory that already has the checkpoint is sufficient; nothing
        else about how the model loads needs to change. A Windows-default
        install (OS mode off) is completely unaffected — it always resolves
        to the pre-existing ~/.friday/local_voice/whisper.
        """
        if _os_mode_active():
            baked = voice_assets_dir() / "whisper"
            if baked.exists() and any(baked.iterdir()):
                return baked
        return WHISPER_DIR

    def load(self, progress=None):
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            if progress:
                progress(f"Loading speech model ({self.model_size})…")
            from faster_whisper import WhisperModel
            download_root = self._download_root()
            # exist_ok=True makes this a no-op against a read-only baked
            # directory that already exists (the OS-mode branch above).
            download_root.mkdir(parents=True, exist_ok=True)
            # CPU INT8 — the whole point of Tier-1. download_root keeps the
            # checkpoint under ~/.friday so it survives and is inspectable
            # (or, under OS mode with a baked copy present, reads straight
            # from the sealed image's own asset directory instead).
            self._model = WhisperModel(
                self.model_size, device="cpu", compute_type="int8",
                download_root=str(download_root))

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

    def _baked_voice_path(self) -> Path | None:
        """A Piper voice already sitting under FRIDAY_VOICE_ASSETS, if any.

        The sealed Friday Linux image bakes voice assets at build time
        (default /usr/share/friday/voice/ — see agent_friday.paths.
        voice_assets_dir()) specifically so a kiosk deployment never needs
        to reach Hugging Face for a model it already ships with. Checked
        ONLY under OS mode (core/os_mode.py) — a Windows-default install
        keeps downloading into ~/.friday/local_voice/piper exactly as
        before, unconditionally, even if FRIDAY_VOICE_ASSETS happens to be
        set in that environment for some other reason.
        """
        if not _os_mode_active():
            return None
        rel = _PIPER_VOICE_PATHS.get(self.voice)
        if not rel:
            return None
        fname = rel.split("/")[-1]
        path = voice_assets_dir() / fname
        cfg = path.with_suffix(path.suffix + ".json")
        if path.exists() and cfg.exists():
            return path
        return None

    def _ensure_voice_file(self, progress=None) -> Path:
        baked = self._baked_voice_path()
        if baked is not None:
            return baked
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
            # Streamed download with a socket timeout. urlretrieve has NO
            # timeout: a stalled connection here blocked forever while holding
            # the engine lock, silently hanging every voice session until a
            # server restart.
            try:
                with urllib.request.urlopen(url, timeout=30) as resp, \
                        open(tmp, "wb") as out:
                    while True:
                        block = resp.read(1 << 16)
                        if not block:
                            break
                        out.write(block)
            except Exception as e:
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    pass
                raise RuntimeError(
                    f"Voice model download failed for '{self.voice}' "
                    f"({type(e).__name__}: {e}) — check your connection and "
                    f"retry, or pick a different voice in Settings → Voice."
                ) from e
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
        self.last_error = ""       # last model-load failure, surfaced in health()
        self.last_error_code = ""  # structured code when one is available
        self.last_downgrade = ""   # why an explicit GPU preference got CPU instead
        # Rolling perf so users can compare Tier-1 vs Tier-2 (spec §"Performance
        # Monitoring"). Surfaced in health() + /api/health/full.
        self._perf = {
            "asr_ms": None, "asr_count": 0,        # last + count: speech→transcript
            "tts_ms": None, "tts_count": 0,        # last + count: text→audio bytes
        }

    def _settings(self):
        try:
            import agent_friday.core as core
            return core._load_settings() or {}
        except Exception:
            return {}

    # ── Tier resolution + hot-swap ──────────────────────────────────────────

    def active_tier(self) -> str:
        return self._tier or "cpu"

    def _gpu_tier_ready(self, fresh: bool = False) -> bool:
        """True when the Tier-2 GPU stack can actually run (NeMo+torch+CUDA+VRAM).

        ``fresh=True`` re-measures VRAM instead of taking the cached reading;
        it is for the one call that decides whether to LOAD (ensure_ready),
        not for the advisory tier resolution that precedes it.
        """
        try:
            from agent_friday.services.nemo_voice import gpu_tier_ready
            return gpu_tier_ready(fresh=fresh)
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
            if self._gpu_tier_ready():
                self.last_downgrade = ""
                return "gpu"
            # The user EXPLICITLY chose the GPU tier — record why they are not
            # getting it so the session can announce the downgrade instead of
            # silently running CPU voice (auto mode stays quiet by design).
            self.last_downgrade = self._gpu_unready_reason()
            # Degradation point 1 of 3 (pre-flight).
            log.warning("degrade gpu->cpu at=resolve_tier requested=%s reason=%s",
                        pref, self.last_downgrade)
            return "cpu"
        if pref == "auto":
            self.last_downgrade = ""
            _tier = "gpu" if self._gpu_tier_ready() else "cpu"
            if _tier == "cpu":
                # `auto` stays quiet to the USER by design, but staying quiet to
                # the LOG is what left an auto-mode user on CPU with no receipt
                # at all — the exact "voice was worse tonight and nothing says
                # why" case. Silent in the UI, never silent in friday.log.
                log.info("auto selected cpu (gpu tier not ready) reason=%s",
                         self._gpu_unready_reason())
            return _tier
        self.last_downgrade = ""
        return "cpu"

    def peek_tier(self, settings=None) -> str:
        """The tier ``resolve_tier`` WOULD pick, with no side effects.

        ``resolve_tier`` records the downgrade reason and logs a warning every
        time it runs; that is right at session start and wrong on a 20-second
        health poll. Health reads this one.
        """
        s = settings if settings is not None else self._settings()
        pref = str(s.get("voice_engine") or "local").strip().lower()
        if pref in ("local-gpu", "gpu", "nemo", "nvidia-nemo", "auto"):
            return "gpu" if self._gpu_tier_ready() else "cpu"
        return "cpu"

    def effective_tts(self, settings=None) -> dict:
        """Which synthesizer a session started NOW would actually run, and why
        that differs from the selection when it does.

        Mirrors the decisions ``_build_cpu_tier_tts`` + ``KokoroTTS.load`` make
        (F4 of voice-mode-diagnosis-and-repair.md): Kokoro's device is decided
        by whether torch sees CUDA, not by the NeMo ASR tier, so Kokoro can
        serve on the GPU while the tier reads "cpu". When Kokoro cannot run the
        session REFUSES and offers Piper -- it does not substitute -- and this
        says so, so the settings panel shows the refusal before the mic click.
        """
        s = settings if settings is not None else self._settings()
        sel = str(s.get("local_voice_tts_engine") or "piper").strip().lower()
        out = {"selected": sel, "engine": sel, "device": "cpu",
               "will_refuse": False, "reason": ""}
        if sel != "kokoro":
            if sel not in self.TTS_ENGINES:
                out["engine"] = "piper"
                out["reason"] = f"unknown engine {sel!r}; Piper serves"
            return out
        try:
            from agent_friday.services.kokoro_voice import (
                kokoro_available, kokoro_gpu_status)
            if not kokoro_available():
                out.update(engine=None, device=None, will_refuse=True,
                           reason="Kokoro is selected but not importable in the "
                                  "server environment; the session will refuse "
                                  "and offer Piper.")
                return out
            g = kokoro_gpu_status()
        except Exception as e:
            out.update(engine=None, device=None, will_refuse=True,
                       reason=f"Kokoro readiness could not be checked ({type(e).__name__}).")
            return out
        if g.get("cuda"):
            out["device"] = "cuda"
            if not g.get("sufficient_for_kokoro"):
                out["reason"] = ("Kokoro will load on the GPU, but only "
                                 f"{g.get('kokoro_headroom_basis_gb')}GB is "
                                 "genuinely free -- it will share the card with "
                                 "whatever is resident.")
            return out
        if s.get("local_voice_kokoro_allow_cpu"):
            out["reason"] = ("Kokoro on CPU by explicit opt-in: roughly realtime "
                             "synthesis, too slow to converse.")
            return out
        out.update(engine=None, device=None, will_refuse=True,
                   reason="Kokoro needs a CUDA GPU and this environment's PyTorch "
                          "reports none; the session will refuse and offer Piper.")
        return out

    def _gpu_unready_reason(self) -> str:
        """One-line, actionable reason the GPU tier can't run right now."""
        try:
            from agent_friday.services.nemo_voice import nemo_health
            h = nemo_health() or {}
            detail = h.get("detail") or h.get("status") or "GPU tier not ready"
        except Exception:
            detail = "GPU tier not ready"
        return (f"GPU voice unavailable ({detail}) — using local CPU voice. "
                f"See Settings → Voice to set up the GPU tier.")

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
                from agent_friday.services.nemo_voice import nemo_deps_installed
                return nemo_deps_installed()
            except Exception:
                return False
        return deps_installed()

    def _get_asr(self):
        if self._asr is None:
            s = self._settings()
            if self.active_tier() == "gpu":
                from agent_friday.services.nemo_voice import NeMoASR, NEMO_ASR_MODEL
                self._asr = NeMoASR(s.get("local_voice_gpu_asr_model") or NEMO_ASR_MODEL)
            else:
                self._asr = WhisperASR(s.get("local_voice_asr_model") or DEFAULT_WHISPER_MODEL)
        return self._asr

    def _get_tts(self):
        if self._tts is None:
            s = self._settings()
            if self.active_tier() == "gpu":
                from agent_friday.services.nemo_voice import NeMoTTS
                self._tts = NeMoTTS(s.get("local_voice_gpu_tts") or "fastpitch-hifigan")
            else:
                self._tts = self._build_cpu_tier_tts(s)
        return self._tts

    #: Which synthesizer the CPU tier uses. Piper is the default and stays the
    #: default: it is the only local synthesizer that runs acceptably without a
    #: GPU, which is most machines. Kokoro is an ADDITION, selected explicitly.
    TTS_ENGINES = ("piper", "kokoro")

    def _build_cpu_tier_tts(self, settings):
        """Build the Tier-1 synthesizer, honouring `local_voice_tts_engine`.

        Kokoro's unavailability is deliberately NOT swallowed here. If the user
        selected Kokoro and it cannot run, the KokoroUnavailable propagates out
        of the eventual load() with its reason and its offer, so the session can
        surface it and let the user choose Piper. Catching it here and
        substituting Piper would give the user a different voice than they
        picked with no way to notice — the silent substitution C2 forbids.
        """
        engine = str(settings.get("local_voice_tts_engine") or "piper").strip().lower()
        if engine == "kokoro":
            from agent_friday.services.kokoro_voice import (
                DEFAULT_KOKORO_VOICE, KokoroTTS)
            voice = settings.get("local_voice_kokoro_voice") or DEFAULT_KOKORO_VOICE
            log.info("tier1 tts engine=kokoro voice=%s", voice)
            return KokoroTTS(voice, allow_cpu=bool(
                settings.get("local_voice_kokoro_allow_cpu")))
        if engine not in self.TTS_ENGINES:
            # An unrecognised value is a settings bug, not a reason to be
            # silent about which voice is actually running.
            log.warning("unknown local_voice_tts_engine=%r — using piper", engine)
        return PiperTTS(settings.get("local_voice_tts_voice") or DEFAULT_PIPER_VOICE)

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
        from agent_friday.services.voice_receipt import log_readiness
        whisper_root = WHISPER_DIR
        try:
            whisper_root = self._get_asr_download_root()
            whisper_ok = whisper_root.exists() and any(whisper_root.iterdir())
        except Exception:
            whisper_ok = False
        vp = None
        _s = self._settings()
        _tts_engine = str(_s.get("local_voice_tts_engine") or "piper").strip().lower()
        if _tts_engine == "kokoro":
            # Ask the question that matches the SELECTED synthesizer. Checking
            # for a Piper .onnx while Kokoro is selected would report ready
            # against a file the run will never open.
            try:
                from agent_friday.services.kokoro_voice import kokoro_available
                piper_ok = kokoro_available()
                vp = "kokoro (package import)"
            except Exception:
                piper_ok = False
        else:
            try:
                voice = _s.get("local_voice_tts_voice") or DEFAULT_PIPER_VOICE
                vp = PiperTTS(voice)._voice_path()
                piper_ok = vp.exists() and vp.with_suffix(vp.suffix + ".json").exists()
            except Exception:
                piper_ok = False
        # Name the ROOT THAT WAS ACTUALLY CONSULTED, not the default constant.
        # The asset root is overridable (voice_assets_dir() under OS mode), so
        # "voice models not downloaded" without the path is the message that
        # made this subsystem undiagnosable: it is true of a directory the
        # reader cannot identify.
        log_readiness("tier1.asr", whisper_ok, checked_path=whisper_root,
                      missing=None if whisper_ok else "no checkpoint under this root")
        log_readiness("tier1.tts", piper_ok, checked_path=vp,
                      missing=None if piper_ok else "voice .onnx and/or .onnx.json absent")
        return bool(whisper_ok and piper_ok)

    def _get_asr_download_root(self) -> Path:
        """The whisper root readiness should check — the same one the loader
        will use, including the OS-mode override. Asking a different question
        than the loader asks is how readiness and reality drift apart."""
        try:
            voice_model = self._settings().get("local_voice_asr_model") or DEFAULT_WHISPER_MODEL
            return WhisperASR(voice_model)._download_root()
        except Exception:
            return WHISPER_DIR

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
            if self.active_tier() == "gpu" and not self._gpu_tier_ready(fresh=True):
                if progress:
                    progress("GPU voice not ready — using local CPU voice")
                # Degradation point 2 of 3 (pre-import gate).
                log.warning("degrade gpu->cpu at=ensure_ready.pre_import reason=%s",
                            self._gpu_unready_reason())
                self._swap_tier("cpu")
            if not self._active_tier_deps_ok():
                self.last_error = ("Tier-1 voice dependencies not installed "
                                   "(pip install -e .[voice-local-lite])")
                return False
            try:
                # TTS FIRST on the GPU tier. Both halves must load, and the
                # NeMo TTS pair is ~200MB against a 2.4GB ASR checkpoint — so
                # loading ASR first meant a broken TTS was discovered only
                # after several minutes of GPU work that then got thrown away
                # on the fallback. Fail on the cheap half. (Tier-1 keeps the
                # original order: Whisper is the slow half there and TTS cannot
                # fail in a way this reordering would catch.)
                if self.active_tier() == "gpu":
                    self._get_tts().load(progress=progress)
                    self._get_asr().load(progress=progress)
                else:
                    self._get_asr().load(progress=progress)
                    self._get_tts().load(progress=progress)
                self._ready = True
                self.last_error = ""
            except Exception as e:  # pragma: no cover - real model load only
                # Kokoro refusing to run on CPU is a DELIBERATE, explained
                # refusal, not a crash. It carries its own reason and its own
                # offer, and it must reach the user intact rather than being
                # flattened into "could not load the local voice models" — the
                # generic message would send someone to check their network for
                # a problem that is entirely about which device is available.
                from agent_friday.services.kokoro_voice import KokoroUnavailable
                if isinstance(e, KokoroUnavailable):
                    self.last_error = f"{e.message} {e.offer}"
                    self.last_error_code = e.code
                    log.warning("kokoro unavailable: code=%s %s", e.code, e.message)
                    self._ready = False
                    return False
                self.last_error = f"{type(e).__name__}: {e}"
                log.error("model load failed tier=%s: %s: %s",
                          self.active_tier(), type(e).__name__, e, exc_info=True)
                # GPU load failed → one graceful retry on the CPU tier.
                if self.active_tier() == "gpu" and deps_installed():
                    if progress:
                        progress("GPU voice failed to load — falling back to CPU voice")
                    # Degradation point 3 of 3 (post-load-failure retry). All
                    # three now leave a record; previously only resolve_tier
                    # recorded anything, and only when the user had explicitly
                    # chosen GPU.
                    log.warning("degrade gpu->cpu at=ensure_ready.post_load_failure "
                                "reason=%s", self.last_error)
                    self._swap_tier("cpu")
                    try:
                        self._get_asr().load(progress=progress)
                        self._get_tts().load(progress=progress)
                        self._ready = True
                        self.last_error = ""
                    except Exception as e2:  # pragma: no cover
                        self.last_error = f"{type(e2).__name__}: {e2}"
                        log.error("CPU fallback load failed after GPU degrade: "
                                  "%s: %s", type(e2).__name__, e2, exc_info=True)
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
        """Text → 24 kHz PCM16 mono bytes (ready for the playback worklet).

        A synthesis-time refusal is recorded on ``last_error_code`` before it is
        re-raised, because that is the channel the voice session already reads
        to turn a failure into a coded error frame. Without this the code exists
        on the exception and nothing ever looks at it -- a receipt written where
        nobody reads it. The exception still propagates: this records, it does
        not swallow, and it never substitutes a different voice.
        """
        t0 = time.perf_counter()
        try:
            out = self._get_tts().synthesize(text)
        except Exception as e:
            code = getattr(e, "code", "")
            if code:
                self.last_error_code = code
                self.last_error = getattr(e, "message", None) or str(e)[:200]
                log.error("tts refusal code=%s %s", code, self.last_error)
            raise
        self._record("tts", (time.perf_counter() - t0) * 1000.0)
        return out

    def synthesize_b64(self, text: str) -> str:
        import base64
        return base64.b64encode(self.synthesize(text)).decode("ascii")

    #: Class name -> the engine id the settings UI uses. Kept here rather than
    #: inferred in the UI so there is one place that knows the mapping.
    _TTS_CLASS_IDS = {"PiperTTS": "piper", "KokoroTTS": "kokoro",
                      "NeMoTTS": "nemo"}

    def running_status(self):
        """What is loaded and serving RIGHT NOW, or None when nothing is.

        Deliberately never falls back to settings. Every other field in
        ``health()`` is read from settings by design, which makes them a report
        of *intent*; intent and reality diverge exactly where this subsystem's
        bugs have lived (a tier that degraded silently, an engine that was
        selected but refused to load). A UI that cannot tell "Kokoro is
        serving" from "Kokoro is selected" is the UI that tells the user the
        wrong thing, so reality gets its own block and is allowed to be empty.
        """
        tts, asr = self._tts, self._asr
        if tts is None and asr is None:
            return None
        tts_cls = type(tts).__name__ if tts is not None else None
        return {
            "tier": self._tier or None,
            "ready": bool(self._ready),
            "asr_class": type(asr).__name__ if asr is not None else None,
            "asr_model": getattr(asr, "model_size", None),
            "tts_class": tts_cls,
            "tts_engine": self._TTS_CLASS_IDS.get(tts_cls or "", None),
            "tts_voice": getattr(tts, "voice", None),
            "tts_device": getattr(tts, "device", None),
        }

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
            "tts_engine": str(s.get("local_voice_tts_engine") or "piper").lower(),
            "tts_voice": s.get("local_voice_tts_voice") or DEFAULT_PIPER_VOICE,
            "active_tier": self.active_tier(),
            "perf": self.perf_stats(),
            "last_error": self.last_error,
            "downgrade": self.last_downgrade,
        }
        # What is ACTUALLY loaded, as distinct from what settings ask for.
        # None when nothing is loaded yet -- an empty answer, never an echo of
        # the request dressed up as an observation.
        out["running"] = self.running_status()
        # F4: what a session started NOW would resolve to, next to what is
        # selected, with the reason when they differ. Side-effect free.
        try:
            out["resolved_tier"] = self.peek_tier(s)
            _pref = str(s.get("voice_engine") or "local").strip().lower()
            out["tier_reason"] = (
                self._gpu_unready_reason()
                if out["resolved_tier"] == "cpu"
                and _pref in ("local-gpu", "gpu", "nemo", "nvidia-nemo", "auto")
                else "")
        except Exception as e:
            out["resolved_tier"] = self.active_tier()
            out["tier_reason"] = f"could not resolve ({type(e).__name__})"
        try:
            out["effective_tts"] = self.effective_tts(s)
        except Exception as e:
            out["effective_tts"] = {"selected": out["tts_engine"], "engine": None,
                                    "will_refuse": True,
                                    "reason": f"could not resolve ({type(e).__name__})"}
        # Kokoro readiness — reported ALWAYS, not only when selected, so the
        # settings UI can offer it (or grey it with a reason) rather than
        # presenting a control whose availability is unknown until it fails.
        try:
            from agent_friday.services.kokoro_voice import kokoro_health
            out["kokoro"] = kokoro_health()
        except Exception as e:
            out["kokoro"] = {"engine": "local-kokoro", "status": "error",
                             "detail": str(e)[:120], "available": False}
        # Tier-2 (GPU/NeMo) readiness — best-effort, never fatal to this block.
        try:
            from agent_friday.services.nemo_voice import nemo_health
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
