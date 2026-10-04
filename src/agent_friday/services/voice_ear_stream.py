"""The streaming ear: true partial transcripts while the owner is speaking
(local voice spec §4.1, P2).

faster-whisper re-decodes 2-second windows and only knows the utterance
once it has ended; the streaming transducer (NVIDIA Nemotron-3.5 ASR
streaming 0.6B, int8, on the CPU through sherpa-onnx; OpenMDW-1.1) emits a
growing hypothesis every chunk, so by the time the endpointer closes the
utterance the transcript is already there and the final is one chunk's flush.

``StreamingEar`` is the session-facing object: ``feed(pcm16_16k)`` returns
the partial text when it changed, ``finish()`` flushes and returns the final
text and resets for the next utterance. A partial is "stable" once it has
not changed for ``stable_chunks`` feeds: that is the text worth prefilling
into the front's cache before the endpoint (speculative prefill).

The recognizer comes from ``make_recognizer``, the one place sherpa-onnx is
named; tests pass a fake. CPU only: zero VRAM, no network (sherpa-onnx makes
no inference-time calls; the model files are the pinned artifact
``voice-ear-streaming``).
"""
from __future__ import annotations

import logging
import struct
import threading
from pathlib import Path

log = logging.getLogger("friday.voice_ear_stream")

SAMPLE_RATE = 16000

#: Where the installer unpacks the pinned Nemotron export (runtime-relative).
MODEL_SUBDIR = "voice/ear/nemotron-3.5-streaming-int8"


def model_dir() -> Path:
    from agent_friday.core import runtime_dir
    return Path(runtime_dir()) / MODEL_SUBDIR


def _find(root: Path, stem: str) -> Path | None:
    hits = sorted(root.rglob(f"{stem}*.onnx")) if root.is_dir() else []
    int8 = [h for h in hits if "int8" in h.name]
    return (int8 or hits or [None])[0]


def installed() -> bool:
    try:
        import importlib.util
        root = model_dir()
        return (importlib.util.find_spec("sherpa_onnx") is not None
                and all(_find(root, s) for s in ("encoder", "decoder", "joiner"))
                and any(root.rglob("tokens.txt")))
    except Exception:
        return False


def make_recognizer(num_threads: int = 2):
    """A sherpa-onnx online (streaming) transducer recognizer for the pinned
    Nemotron export. Verified against the installed sherpa-onnx at install
    time (the export ships encoder/decoder/joiner + tokens.txt)."""
    import sherpa_onnx
    root = model_dir()
    tokens = next(root.rglob("tokens.txt"))
    return sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=str(tokens), encoder=str(_find(root, "encoder")),
        decoder=str(_find(root, "decoder")), joiner=str(_find(root, "joiner")),
        num_threads=int(num_threads), sample_rate=SAMPLE_RATE, feature_dim=80,
        decoding_method="greedy_search", provider="cpu")


def _pcm_to_floats(pcm: bytes) -> list:
    n = len(pcm) // 2
    return [s / 32768.0 for s in struct.unpack("<%dh" % n, pcm[:n * 2])]


class StreamingEar:
    """One utterance at a time; thread-safe."""

    name = "nemotron-streaming"
    device = "cpu"
    model = "nemotron-3.5-asr-streaming-0.6b int8"

    def __init__(self, recognizer=None, *, stable_chunks: int = 2):
        self._rec = recognizer if recognizer is not None else make_recognizer()
        self._lock = threading.Lock()
        self.stable_chunks = int(stable_chunks)
        self._new_stream()

    def _new_stream(self):
        self._stream = self._rec.create_stream()
        self._text = ""
        self._same = 0

    def _decode(self):
        while self._rec.is_ready(self._stream):
            self._rec.decode_stream(self._stream)
        r = self._rec.get_result(self._stream)
        return (getattr(r, "text", r) or "").strip()

    def feed(self, pcm16_16k: bytes):
        """Feed one mic chunk. Returns ``(text, stable)`` when the partial
        changed or just became stable, else None."""
        with self._lock:
            self._stream.accept_waveform(SAMPLE_RATE, _pcm_to_floats(pcm16_16k))
            text = self._decode()
            if text != self._text:
                self._text, self._same = text, 0
                return (text, False) if text else None
            self._same += 1
            if text and self._same == self.stable_chunks:
                return (text, True)
            return None

    def finish(self) -> str:
        """Flush the utterance (tail padding lets the transducer emit its last
        tokens), return the final text, and start a fresh stream."""
        with self._lock:
            self._stream.accept_waveform(SAMPLE_RATE, [0.0] * int(SAMPLE_RATE * 0.3))
            self._stream.input_finished()
            text = self._decode() or self._text
            self._new_stream()
            return text

    def reset(self) -> None:
        with self._lock:
            self._new_stream()

    def describe(self) -> dict:
        return {"engine": self.name, "device": "cpu", "model": self.model}
