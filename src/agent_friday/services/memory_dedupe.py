"""Is this the same fact again? Near-duplicate checks for what Friday stores.

Exact-match dedupe misses "Prefers short answers." against "Prefers short
answers to long ones." Before a line lands on the memory card or in its review
queue (services/core_memory), it is compared with what is already there; a
near duplicate is skipped (or reported) instead of being stored twice
(reference/hermes_memory_eval_2026-10-14.md R5).

Two measures, and the stronger one available is used:

* MiniLM cosine through the pinned ONNX encoder (services/minilm.onnx_model):
  CPU only, no torch, no download. A duplicate at ``EMBED_THRESHOLD``.
* Without the encoder files, word-set overlap (Jaccard over normalised
  words, small words dropped): a duplicate at ``LEXICAL_THRESHOLD``.

Never raises; a failure of the encoder falls back to the lexical measure.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional

EMBED_THRESHOLD = 0.9
LEXICAL_THRESHOLD = 0.75

_SMALL = {"a", "an", "the", "and", "or", "to", "of", "in", "on", "at", "for", "is", "are",
          "was", "be", "it", "that", "this", "with", "as", "by", "they", "their", "them",
          "he", "she", "his", "her", "user", "users"}


def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", str(text or "").lower()) if w not in _SMALL}


def lexical(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _encode(texts: List[str]):
    try:
        from agent_friday.services import minilm
        m = minilm.onnx_model()
        if m is None:
            return None
        return m.encode(texts, normalize_embeddings=True)
    except Exception:
        return None


def near_duplicate(text: str, candidates: Iterable[str]) -> Optional[str]:
    """The first candidate that says the same thing as `text`, or None."""
    text = str(text or "").strip()
    cands = [str(c) for c in candidates if str(c or "").strip()]
    if not text or not cands:
        return None
    low = text.lower()
    for c in cands:
        if c.strip().lower() == low:
            return c
    vecs = _encode([text] + cands)
    if vecs is not None:
        try:
            q = vecs[0]
            for c, v in zip(cands, vecs[1:]):
                if float(sum(x * y for x, y in zip(q, v))) >= EMBED_THRESHOLD:
                    return c
            return None
        except Exception:
            pass
    for c in cands:
        if lexical(text, c) >= LEXICAL_THRESHOLD:
            return c
    return None
