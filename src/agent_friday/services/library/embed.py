"""The Library's sentence encoder: a 22M-parameter MiniLM on the CPU.

all-MiniLM-L6-v2 (Apache-2.0) exported to ONNX turns a line of text into a
384-dimensional unit vector. Every Library menu is answered first by cosine
similarity to precomputed vectors, so a question costs one embedding (tens of
milliseconds) rather than a language-model call per menu.

Rules this module keeps:
  * There is one encoder session in the process and this is not it. The
    artifact is the one already installed for Laya 2 (`models/laya2-encoder`
    under Friday's home), loaded, sha256-pinned and thread-capped by
    `services/laya2_encoder`; this module only asks it for vectors. A mismatch
    reports "missing" exactly as an absent file does. Nothing is downloaded.
  * No telemetry and no sockets (the shared session sets both up).
  * Optional: `available()` is False without the files, and the Library falls
    back to keyword search with a plain status line.
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")

from agent_friday.services import laya2_encoder  # noqa: E402

DIRNAME = laya2_encoder.ENCODER_DIRNAME
MODEL_ID = laya2_encoder.MODEL_ID
MODEL_SHA256 = laya2_encoder.MODEL_SHA256
MODEL_NAME = laya2_encoder.MODEL_NAME
TOKENIZER_NAME = laya2_encoder.TOKENIZER_NAME
DIM = laya2_encoder.EMBED_DIM
MAX_LEN = laya2_encoder.DEFAULT_MAX_LEN


def artifacts_dir() -> Path:
    """Where the encoder files live; a rig may point FRIDAY_LIBRARY_ENCODER_DIR at
    an existing copy while it runs with a scratch Friday home."""
    return laya2_encoder.artifacts_dir()


def files_present() -> bool:
    return laya2_encoder.is_available()


def stamp_id() -> str:
    """The encoder as a citation stamp: model id and the start of its pinned hash."""
    return "%s@%s" % (MODEL_ID, MODEL_SHA256[:12])


def status() -> dict:
    """{state, detail} in the Library's plain wording (no local paths)."""
    s = laya2_encoder.status()
    state = s.get("state", "unloaded")
    if state == "missing":
        pinned = "sha256" in (s.get("detail") or "")
        return {"state": "missing",
                "detail": ("the encoder file does not match the pinned hash" if pinned
                           else "the encoder files are not installed")}
    if state == "error":
        return {"state": "failed", "detail": str(s.get("detail") or "").split(":")[0]}
    return {"state": state, "detail": ""}


def available() -> bool:
    return laya2_encoder.load()


def embed(texts: list[str], batch: int = 16):
    """(n, 384) float32 unit vectors, or None when the encoder is unavailable."""
    if not laya2_encoder.load():
        return None
    import numpy as np
    out = []
    for i in range(0, len(texts), batch):
        out.append(np.asarray(laya2_encoder.embed([str(t) for t in texts[i:i + batch]], MAX_LEN),
                              dtype=np.float32))
    return np.vstack(out) if out else np.zeros((0, DIM), dtype=np.float32)


def to_blob(vec) -> bytes:
    import numpy as np
    return np.asarray(vec, dtype=np.float16).tobytes()


def from_blobs(blobs):
    import numpy as np
    if not blobs:
        return np.zeros((0, DIM), dtype=np.float32)
    return np.frombuffer(b"".join(blobs), dtype=np.float16).reshape(-1, DIM).astype(np.float32)
