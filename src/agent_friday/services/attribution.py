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
import time

from agent_friday.user_errors import clip

_tls = threading.local()


def reset():
    _tls.generation = None
    _tls.chain = []
    _tls.cloud_intents = []
    _tls.routes = []


def note_route(record: dict):
    """A routing decision this turn (services/route_record). Several can land in one
    turn (the chat route's own, the agent ladder's); route_record() picks the one
    that matches the model that actually answered."""
    if not isinstance(record, dict):
        return
    rows = getattr(_tls, "routes", None)
    if rows is None:
        rows = []
        _tls.routes = rows
    rows.append((time.monotonic(), dict(record)))
    del rows[:-10]


def route_record():
    """The route record for this turn's reply, or None: the last decision whose model
    is the one that generated (else the last decision), with latency_ms from that
    decision to now, and ``answered_by`` when something else answered."""
    rows = list(getattr(_tls, "routes", []) or [])
    if not rows:
        return None
    gen = getattr(_tls, "generation", None) or {}
    gm = str(gen.get("model") or "")
    pick = None
    if gm:
        for t0, rec in reversed(rows):
            if str(rec.get("model") or "") == gm:
                pick = (t0, rec)
                break
    t0, rec = pick or rows[-1]
    out = dict(rec)
    out["latency_ms"] = int((time.monotonic() - t0) * 1000)
    if gm and gm != str(out.get("model") or ""):
        out["answered_by"] = gm
        out["substituted"] = True
    return out


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


def note_cloud_intent(token: dict):
    """A cloud call went out under this intent (services/cloud_intent.require).

    The chat routes put the last one on the turn's receipt, so who answered and
    why the cloud was allowed are both on the page.
    """
    rows = getattr(_tls, "cloud_intents", None)
    if rows is None:
        rows = []
        _tls.cloud_intents = rows
    rows.append(dict(token))
    del rows[:-20]


def cloud_intents():
    return list(getattr(_tls, "cloud_intents", []) or [])


def last_generation():
    return getattr(_tls, "generation", None)


def fallback_chain():
    return list(getattr(_tls, "chain", []) or [])
