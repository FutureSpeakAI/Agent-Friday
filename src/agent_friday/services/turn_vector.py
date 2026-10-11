"""One embedding of the user's message per turn, shared by the consumers that
only want to know what the message is about.

Before this module the same message was run through the same MiniLM up to four
times on the non-privacy side of a turn (the turn-shape head, the tool
selector, the context pruner, and the memory recall query), each in its own
copy of the model, and the router and the memory write took two more. `compute()`
embeds the message once with the sha-pinned ONNX encoder (services/laya2_encoder)
and caches the unit vector under a hash of the text; `peek()` hands it to a
consumer that has the same text and the same model, and returns None when there
is nothing to hand over, in which case the consumer embeds with the one shared
MiniLM (services/minilm) exactly as it always did.

What is NOT shared, on purpose (spec 9.4, critic B6): the router's vault check
and the egress gate. They truncate differently (text[:512] characters against
256 tokens here) and a shared vector could move a vault turn between local and
cloud. Nothing in the privacy path imports this module; a test pins that.

A long message is embedded from its first and last 128 tokens, so the opening
request and the closing question both count and a pasted document in the
middle does not dilute them. Two consumers want the plain first-256-tokens
reading instead (the router's tier-1 score, and the memory write, which stores
the vector beside the text): `compute_head()` and `peek_full()` give them the
same vector whenever the message fits in 256 tokens, which is every ordinary
turn, and their own head-truncated vector when it does not.

The cache is content-addressed (a sha256 of the exact text, no text kept),
bounded to MAX_CACHED vectors, and a vector expires after TTL_S seconds, so
nothing outlives the turn it was made for. A caller that knows the turn id may
pass it: `end_turn(turn_id)` then drops that turn's vectors at once. The key is
the text hash and not the turn id on purpose: the consumers run on different
threads (the reflex pool, the router pool, the request thread) and a context
variable does not follow them, so a turn-scoped key would make them miss one
another and embed again.

Concurrent callers with the same text wait for the first one's encode (bounded
by WAIT_S) instead of starting their own.

Nothing here downloads anything, raises, or waits on a model that is not
there: with no encoder on disk `compute()` is None and every consumer behaves
as it did before.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

_log = logging.getLogger("friday.turn_vector")

#: A turn touches one message; the cache only has to span a turn and the
#: retries and parallel consumers around it.
MAX_CACHED = 64
#: A vector is dropped this long after it was made, whether or not the cache is full.
TTL_S = 300.0
#: How long a second caller waits for the first one's encode of the same text.
WAIT_S = 3.0
#: Messages longer than 2 * EDGE_TOKENS are cut to their first and last EDGE_TOKENS.
EDGE_TOKENS = 128

_lock = threading.Lock()
#: key -> (vector, expires_at, turn_id)
_cache: "OrderedDict[str, Tuple[List[float], float, str]]" = OrderedDict()
_inflight: Dict[str, threading.Event] = {}
_stats = {"encodes": 0, "hits": 0, "misses": 0, "unavailable": 0}


def _normalise(text) -> str:
    return " ".join(str(text or "").split())


def _key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _encoder():
    from agent_friday.services import laya2_encoder
    return laya2_encoder


def prepare(text) -> str:
    """The text that is actually embedded: whitespace folded, and for a long
    message its first and last EDGE_TOKENS tokens. Never raises."""
    norm = _normalise(text)
    if not norm:
        return ""
    try:
        return _encoder().edge_text(norm, EDGE_TOKENS)
    except Exception:
        return norm


def _fits(norm: str) -> bool:
    """True when the message is short enough that the edge reading and the plain
    first-256-tokens reading are the same text."""
    return prepare(norm) == norm


def _get_locked(key: str) -> Optional[List[float]]:
    entry = _cache.get(key)
    if entry is None:
        return None
    if entry[1] < time.monotonic():
        del _cache[key]
        return None
    _cache.move_to_end(key)
    return entry[0]


def _put_locked(key: str, vec: List[float], turn_id) -> None:
    _cache[key] = (list(vec), time.monotonic() + TTL_S, str(turn_id or ""))
    _cache.move_to_end(key)
    while len(_cache) > MAX_CACHED:
        _cache.popitem(last=False)


def peek(text) -> Optional[List[float]]:
    """The cached vector for `text`, or None. Never encodes, never raises."""
    norm = _normalise(text)
    if not norm:
        return None
    key = _key(norm)
    with _lock:
        vec = _get_locked(key)
        if vec is None:
            _stats["misses"] += 1
            return None
        _stats["hits"] += 1
        return list(vec)


def peek_full(text) -> Optional[List[float]]:
    """Like `peek`, but only for a message that fits in 256 tokens, where the
    cached vector is exactly what embedding the whole text would give. A longer
    message returns None (its cached vector is the edge reading)."""
    vec = peek(text)
    if vec is None:
        return None
    return vec if _fits(_normalise(text)) else None


def _encode_once(key: str, embed_text: str, turn_id, max_len: Optional[int]) -> Optional[List[float]]:
    """The cached vector for `key`, encoding `embed_text` if no one has yet.
    One encode per key even when several threads ask at once."""
    while True:
        with _lock:
            vec = _get_locked(key)
            if vec is not None:
                _stats["hits"] += 1
                return list(vec)
            pending = _inflight.get(key)
            if pending is None:
                pending = _inflight[key] = threading.Event()
                owner = True
            else:
                owner = False
        if owner:
            break
        # Another thread is encoding this very text: take its result.
        if not pending.wait(WAIT_S):
            _stats["unavailable"] += 1
            return None
        with _lock:
            vec = _get_locked(key)
            if vec is not None:
                _stats["hits"] += 1
                return list(vec)
        return None             # the other thread's encode failed; do not retry here
    try:
        enc = _encoder()
        if not enc.is_available():
            _stats["unavailable"] += 1
            return None
        (vec,) = (enc.embed([embed_text]) if max_len is None
                  else enc.embed([embed_text], max_len))
    except Exception as e:
        _stats["unavailable"] += 1
        _log.debug("turn vector unavailable: %s", e)
        return None
    else:
        with _lock:
            _stats["encodes"] += 1
            _put_locked(key, vec, turn_id)
        return list(vec)
    finally:
        with _lock:
            ev = _inflight.pop(key, None)
        if ev is not None:
            ev.set()


def compute(text, turn_id=None) -> Optional[List[float]]:
    """The 384-d unit vector for `text`, encoded at most once per distinct
    message. None when the encoder is not available or the text is empty.
    Never raises."""
    norm = _normalise(text)
    if not norm:
        return None
    return _encode_once(_key(norm), prepare(norm), turn_id, None)


def compute_head(text, turn_id=None) -> Optional[List[float]]:
    """The vector for the first 256 tokens of `text` (no edge cut): what the
    router's tier-1 score always embedded. For a message that fits in 256 tokens
    this is the turn's shared vector, so no second encode; for a longer one it is
    its own cached vector. None when the encoder is not available."""
    norm = _normalise(text)
    if not norm:
        return None
    if _fits(norm):
        return compute(norm, turn_id)
    return _encode_once("head:" + _key(norm), norm, turn_id, _encoder().DEFAULT_MAX_LEN)


def end_turn(turn_id=None, text=None) -> int:
    """Drop every vector made for `turn_id`, and the one for `text` (the turn's
    message, for a caller that has no id). Returns how many went."""
    tid = str(turn_id or "")
    norm = _normalise(text)
    with _lock:
        gone = [k for k, (_, _, t) in _cache.items() if tid and t == tid]
        if norm:
            k = _key(norm)
            if k in _cache and k not in gone:
                gone.append(k)
            hk = "head:" + k
            if hk in _cache:
                gone.append(hk)
        for k in gone:
            del _cache[k]
        return len(gone)


def stats() -> dict:
    with _lock:
        return dict(_stats, cached=len(_cache))


def reset() -> None:
    """Forget every vector and zero the counters (tests, and a model swap)."""
    with _lock:
        _cache.clear()
        for k in _stats:
            _stats[k] = 0
