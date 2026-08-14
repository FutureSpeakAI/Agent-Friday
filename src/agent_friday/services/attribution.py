"""Per-turn generation attribution — the badge names the model that
ACTUALLY generated, never the seat the router intended.

2026-08-14, sharpened defect #6: every one of the morning's replies was
badged 'qwen3.6-35b-a3b-iq4nl' while the brain never bound its port — the
'local'-seat turns were really gemma4:e4b (seat-gate substitution below the
attribution layer) and the 'cloud'-seat turns were really Claude (ladder
fallback). Attribution was captured at ROUTING time; dispatch decides below
it, so the badge lied in both directions.

Contract: each provider primitive calls record_generation() at the moment
it produces final text, with the model id it truly ran; every abandoned leg
or seat substitution calls note_fallback(). The chat route resets at
dispatch start and reads the LAST recorded generation when building the
persisted message — validator retries and ladder falls therefore resolve to
whatever actually answered. Thread-local: one chat turn == one thread.
"""
from __future__ import annotations

import threading

_tls = threading.local()


def reset():
    _tls.generation = None
    _tls.chain = []


def note_fallback(step: str):
    chain = getattr(_tls, "chain", None)
    if chain is None:
        chain = []
        _tls.chain = chain
    chain.append(str(step)[:300])


def record_generation(model, provider=None, seat=None):
    _tls.generation = {"model": model, "provider": provider, "seat": seat}


def last_generation():
    return getattr(_tls, "generation", None)


def fallback_chain():
    return list(getattr(_tls, "chain", []) or [])
