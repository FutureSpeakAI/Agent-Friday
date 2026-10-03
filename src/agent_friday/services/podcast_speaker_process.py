"""The podcast speaker's own process: Kokoro on the processor, one line at a time.

`podcast_render.speaker()` starts this as a child of the server and releases it
when the render queue is empty. Everything a render loads (torch, its thread
pools, the model and its native buffers) lives and dies with this process, so
the server returns to its own size when an episode is done.

Protocol on the child's stdin/stdout, every message a 4-byte big-endian length
followed by that many bytes:

    request  JSON {"op": "speak", "text": .., "voice": ..}
    reply    JSON {"ok": true, "samples": n, "private_mb": m}  then  n float32
             samples (24 kHz); m is this process's private memory after the line
         or  JSON {"ok": false, "code": .., "message": ..}
    request  JSON {"op": "quit"}           the process exits 0

Anything the libraries print goes to stderr; stdout carries only the protocol.
FRIDAY_PODCAST_SPEAKER_FAKE=1 answers with a tone instead of loading Kokoro, for
tests that exercise the process and not the model (and
FRIDAY_PODCAST_SPEAKER_FAKE_MB makes it report that much private memory).
The process runs with a small thread cap (FRIDAY_PODCAST_SPEAKER_THREADS):
every torch, OpenMP and MKL thread keeps its own scratch memory.
"""
from __future__ import annotations

import json
import os
import struct
import sys


def write_msg(fh, payload: bytes) -> None:
    fh.write(struct.pack(">I", len(payload)))
    fh.write(payload)
    fh.flush()


def read_msg(fh):
    head = fh.read(4)
    if len(head) < 4:
        return None
    (n,) = struct.unpack(">I", head)
    body = fh.read(n)
    return body if len(body) == n else None


def _fake_speak(text: str, voice: str):
    import numpy as np
    if text.strip() == "FAIL":
        raise RuntimeError("the fake speaker was asked to fail")
    n = int(0.05 * 24000) * max(1, len(text.split()))
    t = np.arange(n, dtype="float32") / 24000.0
    hz = 220.0 if (voice or "").startswith("a") else 330.0
    return (0.1 * np.sin(2 * np.pi * hz * t)).astype("float32")


def _private_mb() -> int:
    """This process's private (committed) memory, in MB."""
    fake = os.environ.get("FRIDAY_PODCAST_SPEAKER_FAKE_MB")
    if fake:
        return int(fake)
    try:
        import psutil
        info = psutil.Process().memory_info()
        return int(getattr(info, "private", 0) or info.rss) // (1024 * 1024)
    except Exception:
        return 0


def serve(inp, out) -> int:
    fake = os.environ.get("FRIDAY_PODCAST_SPEAKER_FAKE") == "1"
    speaker = None
    while True:
        raw = read_msg(inp)
        if raw is None:
            return 0
        try:
            msg = json.loads(raw.decode("utf-8"))
        except ValueError:
            write_msg(out, json.dumps({"ok": False, "code": "voice_failed",
                                       "message": "unreadable request"}).encode())
            continue
        op = msg.get("op")
        if op == "quit":
            return 0
        if op != "speak":
            write_msg(out, json.dumps({"ok": False, "code": "voice_failed",
                                       "message": "unknown op %r" % op}).encode())
            continue
        try:
            text, voice = str(msg.get("text") or ""), str(msg.get("voice") or "")
            if fake:
                samples = _fake_speak(text, voice)
            else:
                if speaker is None:
                    from agent_friday.services.podcast_render import CpuKokoro
                    speaker = CpuKokoro(threads=int(os.environ.get("FRIDAY_PODCAST_SPEAKER_THREADS") or 0) or None)
                samples = speaker.speak(text, voice)
            import numpy as np
            data = np.asarray(samples, dtype="<f4").tobytes()
            write_msg(out, json.dumps({"ok": True, "samples": len(data) // 4,
                                       "private_mb": _private_mb()}).encode())
            write_msg(out, data)
        except Exception as e:  # noqa: BLE001 - every failure goes back as a reply
            code = getattr(e, "code", None) or "voice_failed"
            write_msg(out, json.dumps({"ok": False, "code": code,
                                       "message": str(e)[:300]}).encode())


def main() -> int:
    # The protocol gets a private copy of stdout; descriptor 1 itself then points
    # at stderr, so prints from Python or from native libraries never reach it.
    sys.stdout.flush()
    out = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return serve(sys.stdin.buffer, out)


if __name__ == "__main__":
    sys.exit(main())
