"""Per-turn generation attribution — the badge names the model that
ACTUALLY generated, never the seat the router intended.

Attribution captured at ROUTING time is wrong in both directions: seat-gate
substitution (a 'local' seat whose brain never bound its port answers from a
fallback model) and ladder fallback (a 'cloud' seat answered by a different
provider) both happen below the router, so a badge stamped at routing names
a model that never generated.

Contract: each provider primitive calls record_generation() at the moment
it produces final text, with the model id it truly ran; every abandoned leg
or seat substitution calls note_fallback(). The chat route resets at
dispatch start and reads the LAST recorded generation when building the
persisted message — validator retries and ladder falls therefore resolve to
whatever actually answered. Thread-local: one chat turn == one thread.
"""
from __future__ import annotations

import threading

from agent_friday.user_errors import clip

_tls = threading.local()


def reset():
    _tls.generation = None
    _tls.chain = []


def note_fallback(step: str):
    chain = getattr(_tls, "chain", None)
    if chain is None:
        chain = []
        _tls.chain = chain
    chain.append(clip(step, 300))


def record_generation(model, provider=None, seat=None):
    _tls.generation = {"model": model, "provider": provider, "seat": seat}
    # Where this call ran, for the lattice (avatar-visual-genome.md §13).
    try:
        from agent_friday.services import presence as _presence
        from agent_friday.services.egress_gate import is_local_provider
        local = bool(provider) and is_local_provider(str(provider)) and seat != "cloud"
        _presence.emit("route", "step", route="local" if local else "cloud",
                       turn=_presence.current_turn())
    except Exception:
        pass


def last_generation():
    return getattr(_tls, "generation", None)


def fallback_chain():
    return list(getattr(_tls, "chain", []) or [])
