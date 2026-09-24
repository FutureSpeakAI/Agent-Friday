"""friday-voice-worker: one GPU-resident voice engine in a child process.

voice-system-clean-sheet.md §3.2 / §5.2. Releasing GPU memory is
``worker.terminate()``: the driver reclaims everything a dead process held,
which is the only unload that is actually guaranteed on Windows/CUDA (torch
does not reliably return device memory; CTranslate2 owns its own).

Wire protocol over stdio, both directions, one frame at a time::

    [1 byte kind: b"J" json | b"B" binary][4 bytes big-endian length][payload]

Parent -> child ops (JSON):
    {"op":"load"}                       -> {"ok":true,"resident_mib":N,"device":..,"model":..}
    {"op":"synth","text":..,"job":id}   -> n x B(PCM16 24k mono) ... {"op":"done","job":id}
    {"op":"transcribe","job":id} + B(PCM16 16k mono)
                                        -> {"text":..,"job":id}
    {"op":"cancel"}                     -> abandons the running synth at the next chunk
    {"op":"ping"}                       -> {"pong":true}
    {"op":"quit"}                       -> exits 0

Any failure is ``{"error": code, "detail": ..}`` for that op; the process
stays up unless the engine itself is unusable (``load`` failed), in which
case it exits non-zero after reporting.

Engines: ``kokoro-cuda`` (Kokoro-82M via torch on CUDA), ``whisper-cuda``
(faster-whisper on CTranslate2 CUDA int8_float16 / float16) and ``fake`` (a
tone generator + fixed transcript, for the protocol tests; loads nothing).
The child NEVER talks to the arbiter, settings, or the network.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import struct
import sys
import threading
import time

_KIND_JSON = b"J"
_KIND_BIN = b"B"
_HDR = struct.Struct(">cI")


# ── framing ──────────────────────────────────────────────────────────────────

def write_frame(fh, kind: bytes, payload: bytes) -> None:
    fh.write(_HDR.pack(kind, len(payload)))
    fh.write(payload)
    fh.flush()


def read_frame(fh):
    """Returns (kind, payload) or None at EOF."""
    hdr = fh.read(_HDR.size)
    if not hdr or len(hdr) < _HDR.size:
        return None
    kind, n = _HDR.unpack(hdr)
    buf = b""
    while len(buf) < n:
        chunk = fh.read(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return kind, buf


def send_json(fh, obj: dict) -> None:
    write_frame(fh, _KIND_JSON, json.dumps(obj).encode("utf-8"))


# ── engines ──────────────────────────────────────────────────────────────────

def _cuda_free_mib():
    try:
        import torch
        if torch.cuda.is_available():
            free, _total = torch.cuda.mem_get_info(0)
            return int(free // (1024 * 1024))
    except Exception:
        pass
    return None


class _Fake:
    device = "fake"
    model = "fake"

    def load(self, progress=None):
        return {"resident_mib": 7}

    def synth_stream(self, text: str, cancelled):
        # 24 kHz PCM16 mono; ~0.08 s per word so a nine-word line is ~0.7 s.
        seconds = max(0.3, 0.08 * len(text.split()))
        n = int(24000 * seconds)
        chunk = 4800
        for off in range(0, n, chunk):
            if cancelled.is_set():
                return
            frames = min(chunk, n - off)
            yield b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * 440 * (off + i) / 24000)))
                           for i in range(frames))
            if os.environ.get("FRIDAY_VOICE_FAKE_SLOW_MS"):
                time.sleep(int(os.environ["FRIDAY_VOICE_FAKE_SLOW_MS"]) / 1000.0)

    def transcribe(self, pcm: bytes) -> str:
        return os.environ.get("FRIDAY_VOICE_FAKE_TEXT", "Friday, what time is it right now?")


class _KokoroCuda:
    device = "cuda"
    model = "kokoro-82M"

    def __init__(self, voice: str):
        self.voice = voice
        self._tts = None

    def load(self, progress=None):
        from agent_friday.services.kokoro_voice import KokoroTTS
        before = _cuda_free_mib()
        self._tts = KokoroTTS(self.voice, allow_cpu=False)
        self._tts.load(progress=progress)
        self.device = self._tts._device or "cuda"
        after = _cuda_free_mib()
        resident = (before - after) if (before is not None and after is not None) else None
        return {"resident_mib": max(0, resident) if resident is not None else None,
                "voice": self.voice}

    def synth_stream(self, text: str, cancelled):
        # KokoroTTS.synthesize returns the whole clause; one clause is one
        # job, so chunking here is only to keep frames small on the pipe.
        pcm = self._tts.synthesize(text)
        step = 9600
        for off in range(0, len(pcm), step):
            if cancelled.is_set():
                return
            yield pcm[off:off + step]

    def transcribe(self, pcm: bytes) -> str:
        raise RuntimeError("kokoro is a mouth, not an ear")


class _WhisperCuda:
    device = "cuda"

    def __init__(self, size: str, compute: str = "int8_float16"):
        self.size = size
        self.compute = compute
        self.model = f"{size} {compute}"
        self._m = None

    def load(self, progress=None):
        from faster_whisper import WhisperModel
        from agent_friday.services.local_voice import WHISPER_DIR
        before = _cuda_free_mib()
        WHISPER_DIR.mkdir(parents=True, exist_ok=True)
        try:
            self._m = WhisperModel(self.size, device="cuda", compute_type=self.compute,
                                   download_root=str(WHISPER_DIR))
        except Exception as e:
            # int8_float16 needs a capable card; float16 is the §2.4 "best"
            # figure anyway. One fallback, then the error propagates.
            if self.compute != "float16":
                self.compute = "float16"
                self.model = f"{self.size} float16"
                self._m = WhisperModel(self.size, device="cuda", compute_type="float16",
                                       download_root=str(WHISPER_DIR))
            else:
                raise e
        after = _cuda_free_mib()
        resident = (before - after) if (before is not None and after is not None) else None
        return {"resident_mib": max(0, resident) if resident is not None else None}

    def synth_stream(self, text, cancelled):
        raise RuntimeError("whisper is an ear, not a mouth")

    def transcribe(self, pcm: bytes) -> str:
        import numpy as np
        audio = np.frombuffer(pcm, dtype=np.int16).astype("float32") / 32768.0
        segments, _info = self._m.transcribe(audio, language=None, beam_size=1,
                                             vad_filter=False)
        return "".join(seg.text for seg in segments).strip()


def build_engine(name: str, **kw):
    if name == "fake":
        return _Fake()
    if name == "kokoro-cuda":
        return _KokoroCuda(kw.get("voice") or "af_heart")
    if name == "whisper-cuda":
        return _WhisperCuda(kw.get("model") or "small", kw.get("compute") or "int8_float16")
    raise ValueError(f"unknown voice worker engine {name!r}")


# ── main loop ────────────────────────────────────────────────────────────────

def serve(engine, inp, out) -> int:
    cancelled = threading.Event()
    pending = []
    lock = threading.Condition()
    eof = threading.Event()

    # LOAD BEFORE ANY OTHER THREAD EXISTS.
    #
    # Measured on the reference machine: with the reader thread
    # already blocked in ReadFile on the stdin pipe, the engine's first
    # `import numpy` (faster-whisper and torch both pull it in on load)
    # never returned -- py-spy showed the main thread parked inside
    # numpy/__config__ with 0.3 s of CPU used, for as long as anyone waited.
    # The same worker with numpy imported before `serve()` loaded in 10 s.
    # Both GPU engines therefore died on the parent's 120 s load timeout
    # and every session fell to the CPU with "crashed on the GPU".
    #
    # So the first frame is read synchronously here, and if it is the load
    # it is performed on a single-threaded process. Cancel frames cannot
    # arrive during a load anyway: the parent's only lever during load is
    # its timeout, which kills us.
    first = read_frame(inp)
    if first is None:
        return 0
    if first[0] == _KIND_JSON:
        try:
            first_msg = json.loads(first[1].decode("utf-8"))
        except Exception:
            first_msg = {}
        pending.append(("J", first_msg))
    else:
        pending.append(("B", first[1]))
    loaded = False
    if pending and pending[0][0] == "J" and pending[0][1].get("op") == "load":
        _, msg = pending.pop(0)
        job = msg.get("job")
        try:
            t0 = time.perf_counter()
            info = engine.load() or {}
            loaded = True
            send_json(out, {"ok": True, "job": job,
                            "resident_mib": info.get("resident_mib"),
                            "device": engine.device, "model": engine.model,
                            "voice": info.get("voice"),
                            "load_ms": int((time.perf_counter() - t0) * 1000),
                            "threads_at_load": threading.active_count()})
        except Exception as e:  # noqa: BLE001
            code = getattr(e, "code", None) or "engine_error"
            send_json(out, {"error": str(code), "job": job,
                            "detail": f"{type(e).__name__}: {str(e)[:300]}"})
            return 3

    def reader():
        while True:
            fr = read_frame(inp)
            if fr is None:
                eof.set()
                with lock:
                    lock.notify_all()
                return
            kind, payload = fr
            if kind == _KIND_JSON:
                try:
                    msg = json.loads(payload.decode("utf-8"))
                except Exception:
                    continue
                if msg.get("op") == "cancel":
                    cancelled.set()          # out of band: no queueing
                    continue
                with lock:
                    pending.append(("J", msg))
                    lock.notify_all()
            else:
                with lock:
                    pending.append(("B", payload))
                    lock.notify_all()

    threading.Thread(target=reader, daemon=True).start()

    def _next():
        with lock:
            while not pending and not eof.is_set():
                lock.wait(0.5)
            if pending:
                return pending.pop(0)
            return None

    while True:
        item = _next()
        if item is None:
            return 0
        kind, msg = item
        if kind != "J":
            continue                          # stray binary without an op
        op = msg.get("op")
        job = msg.get("job")
        try:
            if op == "load":
                t0 = time.perf_counter()
                info = engine.load() or {}
                loaded = True
                send_json(out, {"ok": True, "job": job,
                                "resident_mib": info.get("resident_mib"),
                                "device": engine.device, "model": engine.model,
                                "voice": info.get("voice"),
                                "load_ms": int((time.perf_counter() - t0) * 1000)})
            elif op == "ping":
                send_json(out, {"pong": True, "job": job, "loaded": loaded})
            elif op == "synth":
                cancelled.clear()
                n = 0
                for chunk in engine.synth_stream(str(msg.get("text") or ""), cancelled):
                    if chunk:
                        write_frame(out, _KIND_BIN, chunk)
                        n += len(chunk)
                send_json(out, {"op": "done", "job": job, "bytes": n,
                                "cancelled": cancelled.is_set()})
            elif op == "transcribe":
                nxt = _next()
                if nxt is None:
                    return 0
                pcm = nxt[1] if nxt[0] == "B" else b""
                t0 = time.perf_counter()
                text = engine.transcribe(pcm)
                send_json(out, {"text": text, "job": job,
                                "ms": int((time.perf_counter() - t0) * 1000)})
            elif op == "quit":
                send_json(out, {"bye": True, "job": job})
                return 0
            else:
                send_json(out, {"error": "unknown_op", "detail": str(op), "job": job})
        except Exception as e:  # noqa: BLE001
            code = getattr(e, "code", None) or "engine_error"
            send_json(out, {"error": str(code), "job": job,
                            "detail": f"{type(e).__name__}: {str(e)[:300]}"})
            if op == "load":
                return 3


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="friday-voice-worker")
    ap.add_argument("--engine", required=True)
    ap.add_argument("--voice", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--compute", default=None)
    args = ap.parse_args(argv)
    # Never let a library print to stdout: it is the wire.
    out = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    sys.stdout = sys.stderr
    inp = os.fdopen(sys.stdin.fileno(), "rb", buffering=0)
    try:
        engine = build_engine(args.engine, voice=args.voice, model=args.model,
                              compute=args.compute)
    except Exception as e:  # noqa: BLE001
        send_json(out, {"error": "bad_engine", "detail": str(e)})
        return 2
    return serve(engine, inp, out)


if __name__ == "__main__":
    sys.exit(main())
