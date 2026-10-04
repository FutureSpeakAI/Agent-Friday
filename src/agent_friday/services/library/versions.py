"""Which versions made a search or a citation.

An answer found in October should be traceable next March to the encoder, the
menu wording and the calibration that produced it. The stamp is small, derived
from the artifacts themselves (the wording's own text, the pinned encoder hash,
the active parameters), so a change cannot forget to bump it.
"""
from __future__ import annotations

import hashlib
import json

from agent_friday.services.library import embed, route
from agent_friday.services.library.store import INDEX_VERSION


def wording_version() -> str:
    """A short hash of every question wording Laya is asked."""
    text = "\n".join((route.ROUTE_INSTRUCTIONS, route.NONE_OF_THESE, route.PASSAGE_INSTRUCTIONS))
    return "w" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def calibration_id(cfg: dict | None = None) -> str:
    """A short hash of the parameters that decide routing and 'found'."""
    cfg = cfg or route.config()
    keep = {k: cfg[k] for k in ("temp", "floor", "e_conf", "e_margin", "act_conf", "act_lead", "p_strong", "p_weak",
                                "beam", "beam_floor", "max_menus", "max_laya") if k in cfg}
    return "c" + hashlib.sha256(json.dumps(keep, sort_keys=True).encode("utf-8")).hexdigest()[:8]


def laya_id() -> str | None:
    try:
        from agent_friday.services import laya_backend
        return laya_backend.MODEL_ID
    except Exception:
        return None


def stamp(*, laya_used: bool = False, cfg: dict | None = None) -> dict:
    """{encoder, wording, calibration, index, laya?}. `laya` appears only when
    Laya answered a menu in this search."""
    out = {"encoder": embed.stamp_id(), "wording": wording_version(), "calibration": calibration_id(cfg),
           "index": INDEX_VERSION}
    if laya_used:
        out["laya"] = laya_id()
    return out


def compact(s: dict | None) -> str | None:
    """One line for a table column or a tooltip."""
    if not s:
        return None
    return json.dumps(s, sort_keys=True, separators=(",", ":"))
