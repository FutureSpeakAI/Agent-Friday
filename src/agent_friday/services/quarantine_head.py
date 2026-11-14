"""The quarantine instruction head, in shadow: is this line addressed to an assistant?

Decision head ``quarantine.instruction`` (services/decision_heads; noul, label_only, shadow).
The quarantined reader (services/quarantine_reader) drops a line by a phrase list. Measured on
fresh lines (ftv/program/reference/quarantine_reader_eval_2026-11-06.md, addendum 2026-11-08) the
list is exact but misses most new phrasing (recall 0.10 at precision 1.00); the R1 MiniLM encoder
ranks the same lines at AUROC 0.91. This head scores each line of a read the gate quarantined,
beside the list, and the gate's receipt (``quarantine.read``) gets COUNTS: how many lines were
scored, how many the head would flag, how many the phrase list flagged, how many only the head
flagged. Nothing here changes what the writer sees; the phrase list stays the gate.

Rules it keeps: no text leaves this module (counts and numbers only); no network (the pinned local
encoder only); it never loads the encoder cold and skips a reading while the encoder is busy; a
missing, off or refused head scores nothing; OFF RECORD scores nothing.
"""
from __future__ import annotations

import logging
import math
from typing import Callable, Dict, List, Optional

_log = logging.getLogger("friday.quarantine_head")

HEAD = "quarantine.instruction"
MAX_LINES = 60
MIN_LINE, MAX_LINE = 12, 400

#: Tests replace the scorer: a function (lines) -> [p].
_scorer_override: Optional[Callable[[List[str]], List[float]]] = None
_seed_cache: Dict[str, tuple] = {}


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def lines_of(text: str) -> List[str]:
    """The lines worth scoring: sentences or lines of a useful length, deduplicated, capped."""
    out, seen = [], set()
    for raw in str(text or "").replace("\r", "\n").split("\n"):
        for part in raw.replace(". ", ".\n").split("\n"):
            s = " ".join(part.split())
            if len(s) >= MIN_LINE and s not in seen:
                seen.add(s)
                out.append(s[:MAX_LINE])
    return out[:MAX_LINES]


def _decision() -> Optional[dict]:
    try:
        from agent_friday.services import decision_heads
        eff = decision_heads.effective(HEAD)
        if not eff:
            return None
        spec = decision_heads.shipped(HEAD) or {}
        return dict(eff, calibration=spec.get("calibration") or {}, seeds=spec.get("seeds") or {})
    except Exception as e:  # noqa: BLE001
        _log.debug("quarantine head unavailable: %s", e)
        return None


def mode() -> str:
    eff = _decision()
    return str(eff.get("mode")) if eff else "off"


def _off_record() -> bool:
    try:
        from agent_friday.services import off_record as _off
        return bool(_off.active())
    except Exception:  # noqa: BLE001
        return False


def _cos(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


def _score(lines: List[str], eff: dict) -> Optional[List[float]]:
    """P(instruction) per line: MiniLM margin (best cosine to the instruction seeds minus best cosine
    to the ordinary seeds) through the head's Platt fit. Shadow: never a cold load, skipped when busy."""
    platt = (eff.get("calibration") or {}).get("platt")
    seeds = eff.get("seeds") or {}
    pos, neg = list(seeds.get("instruction") or []), list(seeds.get("ordinary") or [])
    if not (isinstance(platt, (list, tuple)) and len(platt) == 2 and pos and neg):
        return None
    from agent_friday.services import laya2_encoder
    if (laya2_encoder.status() or {}).get("state") != "ready":
        return None
    lock = getattr(laya2_encoder, "_lock", None)
    if lock is not None and lock.locked():
        return None
    key = "%d:%d:%d" % (len(pos), len(neg), hash(tuple(pos + neg)))
    if key not in _seed_cache:
        vecs = laya2_encoder.embed(pos + neg)
        _seed_cache.clear()
        _seed_cache[key] = (vecs[:len(pos)], vecs[len(pos):])
    ps, ns = _seed_cache[key]
    a, b = float(platt[0]), float(platt[1])
    out = []
    for v in laya2_encoder.embed(lines):
        m = max(_cos(v, s) for s in ps) - max(_cos(v, s) for s in ns)
        out.append(_sigmoid(a * m + b))
    return out


def observe(text: str, phrase_flag: Callable[[str], bool]) -> Optional[dict]:
    """Counts for one read, or None (head off, off the record, nothing to score, encoder unavailable).
    ``phrase_flag`` is the quarantine reader's own phrase list, so the two can be compared."""
    if _off_record():
        return None
    ls = lines_of(text)
    if not ls:
        return None
    try:
        if _scorer_override is not None:
            ps = _scorer_override(ls)
            thr = 0.5
        else:
            eff = _decision()
            if not eff or eff.get("mode") == "off":
                return None
            ps = _score(ls, eff)
            thr = float((eff.get("thresholds") or {}).get("abstain", 0.5))
    except Exception as e:  # noqa: BLE001
        _log.debug("quarantine head scoring failed: %s", e)
        return None
    if not ps or len(ps) != len(ls):
        return None
    head = [p >= thr for p in ps]
    phrase = [bool(phrase_flag(x)) for x in ls]
    return {"lines": len(ls), "head_flagged": sum(head), "phrase_flagged": sum(phrase),
            "head_only": sum(1 for h, f in zip(head, phrase) if h and not f),
            "phrase_only": sum(1 for h, f in zip(head, phrase) if f and not h),
            "p_max": round(max(ps), 3), "threshold": thr}


def preset(ctx, spec: dict) -> dict:
    """The Laya preset for ``quarantine.instruction``: scores ``ctx.text`` as one line."""
    eff = _decision() or {}
    try:
        got = (_scorer_override([ctx.text]) if _scorer_override is not None
               else _score([str(ctx.text or "")[:MAX_LINE]], eff))
    except Exception:  # noqa: BLE001
        got = None
    if not got:
        return {"answer": {"value": None, "p": None, "set": None, "abstain": True}, "layer": "degraded",
                "verdict": None, "native": None, "why": "no instruction reading (encoder or head unavailable)"}
    p = got[0]
    act = float((eff.get("thresholds") or {}).get("act", 1.01))
    return {"answer": {"value": p >= act, "p": round(p, 4), "set": None, "abstain": False},
            "layer": "t1", "verdict": None, "native": got, "why": "MiniLM margin, %s" % (eff.get("tag") or "untagged")}
