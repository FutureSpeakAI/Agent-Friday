"""The Library's sentence encoder: a 22M-parameter MiniLM on the CPU.

all-MiniLM-L6-v2 (Apache-2.0) exported to ONNX turns a line of text into a
384-dimensional unit vector. Every Library menu is answered first by cosine
similarity to precomputed vectors, so a question costs one embedding (tens of
milliseconds) rather than a language-model call per menu.

Rules this module keeps:
  * The artifact is the one already installed for Laya 2 (`models/laya2-encoder`
    under Friday's home), pinned by sha256 and hashed once at load; a mismatch
    reports "missing" exactly as an absent file does. Nothing is downloaded.
  * No telemetry: onnxruntime's events are disabled, HF_HUB_OFFLINE is set, and
    nothing here opens a socket.
  * Bounded threads (the brain and voice share this CPU).
  * Optional: `available()` is False without the files, and the Library falls
    back to keyword search with a plain status line.
"""
from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")

from agent_friday import paths  # noqa: E402

DIRNAME = "laya2-encoder"
MODEL_SHA256 = "6fd5d72fe4589f189f8ebc006442dbb529bb7ce38f8082112682524616046452"
MODEL_NAME = "model.onnx"
TOKENIZER_NAME = "tokenizer.json"
DIM = 384
MAX_LEN = 256
MAX_THREADS = 4

_lock = threading.Lock()
_enc = None
_status = {"state": "unloaded", "detail": ""}


def artifacts_dir() -> Path:
    return paths.friday_home() / "models" / DIRNAME


def files_present() -> bool:
    d = artifacts_dir()
    return (d / MODEL_NAME).is_file() and (d / TOKENIZER_NAME).is_file()


def status() -> dict:
    return dict(_status)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class _Encoder:
    def __init__(self, directory: Path):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        if hasattr(ort, "disable_telemetry_events"):
            ort.disable_telemetry_events()
        self.np = np
        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        so.intra_op_num_threads = max(1, min(MAX_THREADS, (os.cpu_count() or 4) // 2))
        so.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(directory / MODEL_NAME), so, providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.session.get_inputs()}
        self.tok = Tokenizer.from_file(str(directory / TOKENIZER_NAME))
        self.tok.enable_truncation(MAX_LEN)
        self.tok.enable_padding(pad_id=self.tok.token_to_id("[PAD]") or 0, pad_token="[PAD]")

    def embed(self, texts: list[str]):
        np = self.np
        enc = self.tok.encode_batch([str(t) for t in texts])
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self.inputs:
            feed["token_type_ids"] = np.zeros_like(ids)
        hidden = self.session.run(None, feed)[0]                      # (n, len, 384)
        m = mask[..., None].astype(np.float32)
        pooled = (hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
        norm = np.linalg.norm(pooled, axis=1, keepdims=True)
        return (pooled / np.clip(norm, 1e-9, None)).astype(np.float32)


def _load():
    global _enc
    with _lock:
        if _enc is not None:
            return _enc
        if not files_present():
            _status.update(state="missing", detail="the encoder files are not installed")
            return None
        d = artifacts_dir()
        try:
            if _sha(d / MODEL_NAME) != MODEL_SHA256:
                _status.update(state="missing", detail="the encoder file does not match the pinned hash")
                return None
            _enc = _Encoder(d)
            _status.update(state="ready", detail="")
        except Exception as e:  # noqa: BLE001 - reported as unavailable, never raised
            _status.update(state="failed", detail=type(e).__name__)
            _enc = None
        return _enc


def available() -> bool:
    return _load() is not None


def embed(texts: list[str], batch: int = 16):
    """(n, 384) float32 unit vectors, or None when the encoder is unavailable."""
    enc = _load()
    if enc is None:
        return None
    import numpy as np
    out = []
    for i in range(0, len(texts), batch):
        out.append(enc.embed(texts[i:i + batch]))
    return np.vstack(out) if out else np.zeros((0, DIM), dtype=np.float32)


def to_blob(vec) -> bytes:
    import numpy as np
    return np.asarray(vec, dtype=np.float16).tobytes()


def from_blobs(blobs):
    import numpy as np
    if not blobs:
        return np.zeros((0, DIM), dtype=np.float32)
    return np.frombuffer(b"".join(blobs), dtype=np.float16).reshape(-1, DIM).astype(np.float32)
