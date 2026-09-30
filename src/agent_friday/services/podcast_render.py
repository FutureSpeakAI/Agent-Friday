"""Speaking and checking a podcast script, entirely on this computer.

Two invariants hold everywhere in this module:

* **No network.** Kokoro's own loaders (`KModel()`, `KPipeline.load_single_voice`)
  call `hf_hub_download`, which asks Hugging Face for the latest revision even
  when the files are already cached, and silently downloads a voice that is
  not. Every file here is resolved with `try_to_load_from_cache`, which only
  reads the local cache, and handed to Kokoro as a path. A voice that is not
  installed is refused by name; it is never fetched.
* **No GPU.** The local model holds nearly all of the card. Speech runs on the
  processor with a capped thread count, and the listening check uses
  faster-whisper on the CPU with `local_files_only=True`.
"""

from __future__ import annotations

import io
import logging
import os
import re
import subprocess
import threading
import time
import wave
from pathlib import Path

log = logging.getLogger(__name__)

RATE = 24000
KOKORO_REPO = "hexgrad/Kokoro-82M"
WHISPER_REPO = "Systran/faster-whisper-base.en"

#: Silence between lines, between speakers, and at a chapter break (seconds).
GAP_SAME_S = 0.18
GAP_TURN_S = 0.30
GAP_CHAPTER_S = 0.90

#: A CPU line gets twice the GPU budget: measured roughly realtime on 8 threads.
CPU_BUDGET_FACTOR = 2.0

#: Word error rate at or under which an episode counts as "checked".
WER_OK = 0.20


class RenderError(RuntimeError):
    """A render that cannot proceed; `code` names why, the message says it plainly."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# ── local files only ────────────────────────────────────────────────────────

def _cached(repo: str, filename: str) -> str | None:
    """The cached path of `filename`, or None. Never touches the network."""
    try:
        from huggingface_hub import try_to_load_from_cache
    except Exception:
        return None
    try:
        p = try_to_load_from_cache(repo, filename)
    except Exception:
        return None
    return p if isinstance(p, str) and os.path.isfile(p) else None


def installed_voices() -> list[str]:
    """Kokoro voices on this computer, e.g. ['af_heart', 'bf_emma']."""
    cfg = _cached(KOKORO_REPO, "config.json")
    if not cfg:
        return []
    vdir = Path(cfg).parent / "voices"
    try:
        return sorted(p.stem for p in vdir.glob("*.pt"))
    except Exception:
        return []


def voice_installed(voice: str) -> bool:
    return bool(voice) and _cached(KOKORO_REPO, f"voices/{voice}.pt") is not None


def lang_of(voice: str) -> str:
    """Kokoro's g2p language for a voice: 'b' for British voices, else 'a'."""
    return "b" if (voice or "").startswith("b") else "a"


# ── the CPU speaker ─────────────────────────────────────────────────────────

class CpuKokoro:
    """One Kokoro model on the CPU, with a pipeline per accent.

    Separate from voice mode's GPU `KokoroTTS`: a render must never take the
    card, and voice mode must never wait behind a thirty-minute episode.
    """

    def __init__(self, threads: int | None = None):
        self._model = None
        self._pipes: dict = {}
        self._lock = threading.Lock()
        self._threads = threads
        self._stuck = None

    def loaded(self) -> bool:
        return self._model is not None

    def load(self):
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            cfg = _cached(KOKORO_REPO, "config.json")
            pth = _cached(KOKORO_REPO, "kokoro-v1_0.pth")
            if not cfg or not pth:
                raise RenderError(
                    "voice_model_missing",
                    "The Kokoro voice model is not installed on this computer, so "
                    "the episode cannot be spoken locally. Nothing was downloaded.")
            from agent_friday.services import kokoro_voice as kv
            kv.ensure_espeak_fallback()
            try:
                import torch
                n = self._threads or max(2, (os.cpu_count() or 4) // 2)
                torch.set_num_threads(n)
                from kokoro import KModel
                self._model = KModel(config=cfg, model=pth).to("cpu").eval()
            except RenderError:
                raise
            except BaseException as e:  # noqa: BLE001
                raise RenderError(
                    "voice_model_failed",
                    "Kokoro could not load on the processor (%s: %s)."
                    % (type(e).__name__, str(e)[:160])) from e

    def _pipe(self, lang: str):
        p = self._pipes.get(lang)
        if p is None:
            from kokoro import KPipeline
            from agent_friday.services import kokoro_voice as kv
            p = KPipeline(lang_code=lang, model=self._model)
            fb = kv.attach_espeak_fallback(p)
            if not fb.get("fallback"):
                raise RenderError(
                    "voice_no_fallback",
                    "Kokoro's pronunciation fallback is missing, so it would stop "
                    "on unfamiliar names. " + (fb.get("detail") or ""))
            self._pipes[lang] = p
        return p

    def speak(self, text: str, voice: str):
        """`text` in `voice` → float32 numpy samples at 24 kHz."""
        import numpy as np
        text = (text or "").strip()
        if not text:
            return np.zeros(0, dtype="float32")
        vpath = _cached(KOKORO_REPO, f"voices/{voice}.pt")
        if not vpath:
            raise RenderError(
                "voice_not_installed",
                "The voice %r is not installed on this computer. Pick an "
                "installed voice (%s) in Settings → Podcasts; nothing is "
                "downloaded during a render." % (voice, ", ".join(installed_voices()) or "none"))
        self.load()
        stuck = self._stuck
        if stuck is not None and stuck.is_alive():
            raise RenderError("voice_busy", "Kokoro is still finishing an earlier line.")
        pipe = self._pipe(lang_of(voice))
        chunks, failure = [], []

        def run():
            try:
                for _gs, _ps, audio in pipe(text, voice=vpath):
                    if audio is not None:
                        chunks.append(np.asarray(audio, dtype="float32").reshape(-1))
            except BaseException as e:  # noqa: BLE001
                failure.append(e)

        from agent_friday.services.kokoro_voice import synthesis_budget_s
        budget = synthesis_budget_s(text) * CPU_BUDGET_FACTOR
        t = threading.Thread(target=run, name="podcast-kokoro", daemon=True)
        t.start()
        t.join(timeout=budget)
        if t.is_alive():
            self._stuck = t
            raise RenderError("voice_timeout",
                              "One line took longer than %.0f s to speak." % budget)
        if failure:
            e = failure[0]
            raise RenderError("voice_failed", "Kokoro failed on a line (%s: %s)."
                              % (type(e).__name__, str(e)[:140])) from e
        return np.concatenate(chunks) if chunks else np.zeros(0, dtype="float32")

    def unload(self):
        with self._lock:
            self._model = None
            self._pipes = {}


_SPEAKER = None
_SPEAKER_LOCK = threading.Lock()


def speaker() -> CpuKokoro:
    global _SPEAKER
    with _SPEAKER_LOCK:
        if _SPEAKER is None:
            _SPEAKER = CpuKokoro()
        return _SPEAKER


def release_speaker():
    """Free the CPU model's memory once the render queue is empty."""
    global _SPEAKER
    with _SPEAKER_LOCK:
        if _SPEAKER is not None:
            _SPEAKER.unload()
        _SPEAKER = None


# ── assembling the episode ──────────────────────────────────────────────────

def _to_pcm16(samples) -> bytes:
    import numpy as np
    a = np.clip(np.asarray(samples, dtype="float32"), -1.0, 1.0)
    return (a * 32767.0).astype("<i2").tobytes()


def _silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(round(seconds * RATE))


def render_lines(lines: list[dict], voices: dict, speak=None, progress=None,
                 should_stop=None) -> tuple[bytes, list[dict]]:
    """Speak every line and lay them end to end.

    `lines`: [{speaker: 'a'|'b', text, chapter: int}, ...]
    `voices`: {'a': 'af_heart', 'b': 'bf_emma'} (or cloud voice names when a
    `speak` callable for a cloud voice is passed in).
    `speak(text, voice) -> float32 samples` defaults to the CPU Kokoro.

    Returns (pcm16 bytes at 24 kHz, [{start, end}] per line in seconds), times
    computed from sample counts so captions and chapters are exact.
    """
    speak = speak or speaker().speak
    out = bytearray()
    timings = []
    prev = None
    for i, ln in enumerate(lines):
        if should_stop and should_stop():
            raise RenderError("cancelled", "The render was stopped.")
        if prev is not None:
            if ln.get("chapter") != prev.get("chapter"):
                out += _silence(GAP_CHAPTER_S)
            elif ln.get("speaker") != prev.get("speaker"):
                out += _silence(GAP_TURN_S)
            else:
                out += _silence(GAP_SAME_S)
        start = len(out) / 2 / RATE
        out += _to_pcm16(speak(ln["text"], voices[ln.get("speaker") or "a"]))
        timings.append({"start": round(start, 3), "end": round(len(out) / 2 / RATE, 3)})
        prev = ln
        if progress:
            progress(i + 1, len(lines))
    out += _silence(0.5)
    return bytes(out), timings


def write_wav(pcm: bytes, path: Path) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)


def encode_mp3(wav_path: Path, mp3_path: Path, title: str = "") -> bool:
    """A smaller copy for playback, when ffmpeg is on this computer. Local only."""
    try:
        from agent_friday.services.timeline_engine import ffmpeg_exe
        exe = ffmpeg_exe()
    except Exception:
        exe = None
    if not exe:
        return False
    cmd = [exe, "-y", "-loglevel", "error", "-i", str(wav_path),
           "-codec:a", "libmp3lame", "-b:a", "96k"]
    if title:
        cmd += ["-metadata", "title=" + title[:200]]
    cmd.append(str(mp3_path))
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=600,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0 and mp3_path.is_file()
    except Exception as e:
        log.warning("mp3 encode failed: %s", e)
        return False


def _vtt_time(s: float) -> str:
    ms = int(round(s * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    sec, ms = divmod(ms, 1000)
    return "%02d:%02d:%02d.%03d" % (h, m, sec, ms)


def captions_vtt(lines: list[dict], names: dict) -> str:
    """WebVTT with a voice tag per speaker, from the exact line timings."""
    out = ["WEBVTT", ""]
    for i, ln in enumerate(lines, 1):
        who = names.get(ln.get("speaker") or "a", "")
        text = (ln.get("text") or "").replace("-->", "→")
        out += [str(i), "%s --> %s" % (_vtt_time(ln["start"]), _vtt_time(ln["end"])),
                "<v %s>%s" % (who, text) if who else text, ""]
    return "\n".join(out)


# ── listening back ──────────────────────────────────────────────────────────

_WORD_RE = re.compile(r"[a-z0-9']+")
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")


def _numbers_to_words(text: str) -> str:
    try:
        from num2words import num2words
    except Exception:
        return _NUM_RE.sub(" ", text)

    def one(m):
        raw = m.group(0).replace(",", "")
        try:
            if "." in raw:
                return " " + num2words(float(raw)) + " "
            n = int(raw)
            # A four-digit number standing alone is usually a year, and is
            # spoken as one ("twenty nineteen"); whisper writes it as digits.
            if 1100 <= n <= 2099 and len(raw) == 4:
                return " " + num2words(n, to="year") + " "
            return " " + num2words(n) + " "
        except Exception:
            return " "
    return _NUM_RE.sub(one, text)


def normalise_words(text: str) -> list[str]:
    t = (text or "").lower().replace("%", " percent ").replace("&", " and ")
    t = t.replace("-", " ")
    t = _numbers_to_words(t).replace("-", " ").replace(",", " ")
    return _WORD_RE.findall(t)


def word_error_rate(reference: str, hypothesis: str) -> float:
    ref, hyp = normalise_words(reference), normalise_words(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1] / len(ref)


_WHISPER = None
_WHISPER_LOCK = threading.Lock()


def _whisper():
    """faster-whisper base.en on the CPU, from the local cache only."""
    global _WHISPER
    with _WHISPER_LOCK:
        if _WHISPER is None:
            from faster_whisper import WhisperModel
            try:
                _WHISPER = WhisperModel("base.en", device="cpu", compute_type="int8",
                                        local_files_only=True)
            except Exception as e:
                raise RenderError(
                    "check_model_missing",
                    "The local speech recogniser (faster-whisper base.en) is not "
                    "installed, so the episode could not be checked by ear. (%s)"
                    % str(e)[:120]) from e
        return _WHISPER


def listen_back(wav_path: Path, script_text: str, transcribe=None) -> dict:
    """Transcribe the finished audio locally and compare it with the script."""
    t0 = time.time()
    if transcribe is None:
        def transcribe(p):
            segs, _info = _whisper().transcribe(str(p), beam_size=1, vad_filter=False)
            return " ".join(s.text.strip() for s in segs)
    heard = transcribe(wav_path) or ""
    wer = word_error_rate(script_text, heard)
    return {
        "wer": round(wer, 4),
        "ok": wer <= WER_OK,
        "threshold": WER_OK,
        "model": "faster-whisper base.en (CPU, local)",
        "seconds": round(time.time() - t0, 1),
        "heard_words": len(normalise_words(heard)),
        "script_words": len(normalise_words(script_text)),
    }


def wav_bytes(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return buf.getvalue()
