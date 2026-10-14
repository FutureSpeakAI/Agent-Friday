"""The one MiniLM (all-MiniLM-L6-v2) that every Friday embedder shares.

Before this module the same model was loaded up to six times: the pinned ONNX
session in services/laya2_encoder, a second ONNX session in the Library, and a
sentence-transformers (torch) copy in each of the privacy classifier, the tool
selector, the context pruner and conversation memory's Chroma embedder. This
module hands every one of them the same object:

  * the pinned ONNX session of services/laya2_encoder when its files are on
    disk (sha256 checked once at load, no torch needed), else
  * ONE shared sentence-transformers model, built the first time something asks
    for it, for a machine that has the Python packages but not the encoder
    files. That is the old behaviour with the duplicates removed.

`model(feature)` returns an object with the slice of the SentenceTransformer API
these callers use: `encode(text_or_list, normalize_embeddings=..., ...)` returns
384-d unit vectors, a 1-D array for a string and a 2-D array for a list. Both
backends mean-pool the last hidden state and L2-normalise, so they agree to
within float noise (tests pin cosine >= 0.999). Both truncate at 256 tokens.

Nothing here downloads anything on its own: the torch path goes through
services/embedder_cache.ensure_available(feature), the visible-notice fetch it
always used. With neither backend present `model()` raises, and each caller
degrades exactly as it did before.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

_log = logging.getLogger("friday.minilm")

MODEL = "all-MiniLM-L6-v2"
DIM = 384
BATCH = 32

_lock = threading.Lock()
#: Taken only while holding the shared ML import lock (services/ml_imports), never
#: the other way round, so a caller already inside `guarded()` cannot deadlock
#: against one that is not.
_torch_lock = threading.Lock()
_torch_model = None
_onnx_adapter = None


class OnnxSentenceModel:
    """`encode()` over the pinned ONNX session. Always unit-length output."""

    backend = "onnx"
    max_seq_length = 256

    def get_sentence_embedding_dimension(self) -> int:
        return DIM

    def encode(self, sentences, batch_size: int = BATCH, normalize_embeddings: bool = True,
               convert_to_numpy: bool = True, show_progress_bar: bool = False, **_ignored):
        import numpy as np
        from agent_friday.services import laya2_encoder
        one = isinstance(sentences, str)
        items = [sentences] if one else [str(s) for s in sentences]
        if not items:
            return np.zeros((0, DIM), dtype=np.float32)
        rows = []
        step = max(1, int(batch_size or BATCH))
        for i in range(0, len(items), step):
            rows.extend(laya2_encoder.embed(items[i:i + step], laya2_encoder.DEFAULT_MAX_LEN))
        out = np.asarray(rows, dtype=np.float32)
        return out[0] if one else out


def backend() -> Optional[str]:
    """'onnx' when the pinned encoder is on disk, 'torch' when a shared
    sentence-transformers model has been built, else None. Never loads."""
    try:
        from agent_friday.services import laya2_encoder
        if laya2_encoder.is_available():
            return "onnx"
    except Exception:
        pass
    return "torch" if _torch_model is not None else None


def onnx_model() -> Optional[OnnxSentenceModel]:
    """The shared ONNX-backed model, or None when the encoder files are absent
    or fail their pin. Loads the session on first use."""
    global _onnx_adapter
    try:
        from agent_friday.services import laya2_encoder
        if not laya2_encoder.load():
            return None
    except Exception:
        return None
    with _lock:
        if _onnx_adapter is None:
            _onnx_adapter = OnnxSentenceModel()
        return _onnx_adapter


def torch_model(feature: str = ""):
    """The one shared sentence-transformers model (built once, for every caller).
    Raises when sentence-transformers is not installed or the weights cannot be
    had; the visible-notice download (embedder_cache) runs first."""
    global _torch_model
    if _torch_model is not None:
        return _torch_model
    from agent_friday.services.ml_imports import ML_IMPORT_LOCK, import_module as _ml_import  # shared ML import lock
    with ML_IMPORT_LOCK, _torch_lock:
        if _torch_model is not None:
            return _torch_model
        SentenceTransformer = _ml_import("sentence_transformers").SentenceTransformer
        from agent_friday.services import embedder_cache
        embedder_cache.ensure_available(feature)
        _torch_model = SentenceTransformer(MODEL, device="cpu")
        return _torch_model


def model(feature: str = ""):
    """The MiniLM for `feature`: the pinned ONNX session when it is there,
    otherwise the one shared torch model. Raises when neither can be had."""
    m = onnx_model()
    if m is not None:
        return m
    return torch_model(feature)


_chroma_class = None


def chroma_embedding_function_class():
    """A Chroma embedding function that runs the shared MiniLM.

    It subclasses chromadb's own SentenceTransformerEmbeddingFunction and keeps
    its name, config and spaces, so a collection created with that embedder
    opens unchanged; the one difference is that it is handed the shared model
    instead of loading a sentence-transformers copy of its own. Construct it
    with the model from `model()`. Raises ImportError when chromadb is absent."""
    global _chroma_class
    with _lock:
        if _chroma_class is None:
            from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction as _Base

            class SharedMiniLMEmbeddingFunction(_Base):
                def __init__(self, shared_model):
                    # Same fields the base __init__ records, minus the model load.
                    self.model_name = MODEL
                    self.device = "cpu"
                    self.normalize_embeddings = False
                    self.kwargs = {}
                    self._model = shared_model

            _chroma_class = SharedMiniLMEmbeddingFunction
        return _chroma_class


def reset() -> None:
    """Forget the cached adapters (tests, and a model swap)."""
    global _torch_model, _onnx_adapter
    with _lock:
        _onnx_adapter = None
    with _torch_lock:
        _torch_model = None
