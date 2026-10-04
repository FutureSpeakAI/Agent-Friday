"""Laya 2 tier 1: a 22M-parameter sentence encoder, in-process, on the CPU.

One small encoder (`sentence-transformers/all-MiniLM-L6-v2`, Apache-2.0,
exported to ONNX) turns a line of text into a 384-dimensional unit vector.
Every tier-1 head in `services/reflex_turn` is a nearest-prototype lookup over
those vectors, so this module is the only place the model is touched.

The rules it keeps:

  * The artifact is pinned. `model.onnx` is hashed ONCE at load and compared
    with `MODEL_SHA256`; a mismatch refuses to load and reports "missing",
    exactly as an absent file does. Nothing here downloads anything.
  * Zero telemetry. onnxruntime's telemetry events are disabled before a
    session exists, `HF_HUB_OFFLINE` is set, and no network is touched.
  * Bounded threads. CPUExecutionProvider, intra-op threads capped well under
    the machine's core count, inter-op 1: the brain and the voice front share
    this CPU.
  * Optional. `is_available()` is False when the files are not on disk (CI has
    none), and every consumer degrades to the brain when it is.

`embed()` runs the tokenizer (the `tokenizers` crate, from the shipped
`tokenizer.json`), the encoder, mean pooling over the attention mask and L2
normalisation, which is the recipe the model card publishes.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from pathlib import Path
from typing import List, Optional

os.environ.setdefault("HF_HUB_OFFLINE", "1")

_log = logging.getLogger("friday.laya2_encoder")

ENCODER_DIRNAME = "laya2-encoder"
MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_LICENCE = "Apache-2.0"
#: The published sha256 of the ONNX export this runtime accepts.
MODEL_SHA256 = "6fd5d72fe4589f189f8ebc006442dbb529bb7ce38f8082112682524616046452"
MODEL_NAME = "model.onnx"
TOKENIZER_NAME = "tokenizer.json"
REQUIRED_FILES = (MODEL_NAME, TOKENIZER_NAME)
EMBED_DIM = 384
DEFAULT_MAX_LEN = 256
#: A 6-layer, 384-wide encoder saturates long before the brain's thread count;
#: four threads measured 23 ms p50 on a 256-token input, and more would only
#: take cores from the rest of Friday.
MAX_THREADS = 4

_lock = threading.Lock()
_encoder: Optional["_Encoder"] = None
_status = {"state": "unloaded", "detail": ""}


def artifacts_dir() -> Path:
    from agent_friday.core import FRIDAY_DIR
    return FRIDAY_DIR / "models" / ENCODER_DIRNAME


def is_available() -> bool:
    """True when the artifact files are on disk. Does not load or hash."""
    try:
        d = artifacts_dir()
        return all((d / name).is_file() for name in REQUIRED_FILES)
    except Exception:
        return False


def default_threads() -> int:
    from agent_friday.services.laya_runtime import default_threads as _base
    return max(1, min(MAX_THREADS, int(_base())))


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class _Encoder:
    def __init__(self, directory: Path, threads: int):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        if hasattr(ort, "disable_telemetry_events"):
            ort.disable_telemetry_events()
        self._np = np
        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        so.intra_op_num_threads = max(1, int(threads))
        so.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(directory / MODEL_NAME), so,
                                            providers=["CPUExecutionProvider"])
        self._inputs = {i.name for i in self.session.get_inputs()}
        self.tokenizer = Tokenizer.from_file(str(directory / TOKENIZER_NAME))
        self._pad_id = self.tokenizer.token_to_id("[PAD]") or 0
        self._max_len = 0
        self.threads = threads

    def _prepare(self, max_len: int) -> None:
        if max_len != self._max_len:
            self.tokenizer.enable_truncation(max_len)
            self.tokenizer.enable_padding(pad_id=self._pad_id, pad_token="[PAD]")
            self._max_len = max_len

    def embed(self, texts: List[str], max_len: int) -> List[List[float]]:
        np = self._np
        self._prepare(max_len)
        encodings = self.tokenizer.encode_batch([str(t) for t in texts])
        ids = np.array([e.ids for e in encodings], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._inputs:
            feed["token_type_ids"] = np.zeros_like(ids)
        (hidden,) = self.session.run(["last_hidden_state"], feed)
        # Mean pooling over the tokens the mask keeps, then L2 normalise: the
        # model card's recipe, and what the prototypes are built with.
        m = mask[:, :, None].astype(hidden.dtype)
        pooled = (hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        pooled = pooled / np.clip(norms, 1e-12, None)
        return pooled.astype(np.float32).tolist()


def _load_locked() -> Optional["_Encoder"]:
    global _encoder
    if _encoder is not None:
        return _encoder
    if _status["state"] in ("missing", "error"):
        return None
    d = artifacts_dir()
    if not is_available():
        _status.update(state="missing", detail="encoder files not found under %s" % d)
        return None
    t0 = time.perf_counter()
    try:
        digest = _sha256_of(d / MODEL_NAME)
        if digest != MODEL_SHA256:
            _status.update(state="missing",
                           detail="model.onnx sha256 %s... does not match the pinned "
                                  "artifact; refusing to load" % digest[:12])
            _log.warning("laya2 encoder: %s", _status["detail"])
            return None
        enc = _Encoder(d, default_threads())
    except Exception as e:
        _status.update(state="error", detail="%s: %s" % (type(e).__name__, e))
        _log.warning("laya2 encoder failed to load: %s", _status["detail"])
        return None
    _encoder = enc
    _status.update(state="ready", detail="",
                   load_ms=int((time.perf_counter() - t0) * 1000),
                   threads=enc.threads)
    return enc


def load() -> bool:
    """Load the encoder now (idempotent). True when it is ready."""
    with _lock:
        return _load_locked() is not None


def embed(texts: List[str], max_len: int = DEFAULT_MAX_LEN) -> List[List[float]]:
    """Unit vectors for `texts`, one per input. Raises when the encoder is
    not available; callers with a budget wrap this (services/reflex_turn)."""
    if not texts:
        return []
    with _lock:
        enc = _load_locked()
        if enc is None:
            raise RuntimeError("laya2 encoder unavailable: %s" % (_status["detail"] or _status["state"]))
        return enc.embed(list(texts), int(max_len))


def status() -> dict:
    out = dict(_status)
    out["available"] = is_available()
    out["model_id"] = MODEL_ID
    out["sha256"] = MODEL_SHA256
    out["licence"] = MODEL_LICENCE
    try:
        out["dir"] = str(artifacts_dir())
    except Exception:
        out["dir"] = ""
    return out


def reset() -> None:
    """Forget the loaded session and its status (tests, and a re-fetch)."""
    global _encoder
    with _lock:
        _encoder = None
        _status.clear()
        _status.update(state="unloaded", detail="")
