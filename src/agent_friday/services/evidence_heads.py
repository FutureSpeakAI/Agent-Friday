"""Evidence heads, in shadow: do the passages Friday retrieved answer the question?

Spec: ftv/program/specs/decision_models_STORM_2026-10-24.md (DM4; owner decisions
S1 to S9, 2026-10-24). Two decision heads (services/decision_heads):

  evidence.holds       noul, label_only   P(this one passage holds the whole answer)
  evidence.sufficient  choice, add_only   sufficient | search_more | unsure, from the
                                          best passage: at or above the act threshold
                                          "sufficient", below the abstain threshold
                                          "search_more", between "unsure"

Both ship in SHADOW. In this build nothing reads their answers: the memory block
is built exactly as before, and the heads only score it afterwards, on their own
worker, and leave a Laya receipt (hashes, labels, p; never a word). In act mode
(a later phase, Stephen's press only) evidence.sufficient may only ADD a retrieval
step or an "I could not find that" line; it can never remove context.

The scorer is named by each head's file (decision_heads.SCORERS): "series_one",
the R1 MiniLM cosine through a Platt calibration fitted on the bench's synthetic
dev third (ftv/decision_eval, E1), or "laya1", Laya 1's own noul with Friday's
Library wording. No generative model scores a passage.

Rules it keeps: OFF RECORD computes and writes nothing; NEVER BLOCKS (a bounded
queue on its own thread; a full queue drops the reading); NO NETWORK (the pinned
local encoder only); a missing or refused decision head is off.
"""
from __future__ import annotations

import logging
import math
import queue
import threading
import time
from typing import Callable, Dict, List, Optional, Sequence

_log = logging.getLogger("friday.evidence_heads")

HOLDS = "evidence.holds"
SUFFICIENT = "evidence.sufficient"
#: Friday's own wording, the one the Library already asks Laya 1 (services/library/route.py).
PASSAGE_INSTRUCTIONS = "This passage contains the answer to the question."
MAX_PASSAGES = 8
CLIP_Q, CLIP_P = 300, 700
_MODE_TTL = 3.0

_mode_cache: Dict[str, tuple] = {}
#: Tests replace the scorer (a function (question, passages) -> [p]).
_scorer_override: Optional[Callable[[str, List[str]], List[float]]] = None
RUN_INLINE = False


def _clip(text, n) -> str:
    return " ".join(str(text or "").split())[:n]


def _decision(hid: str) -> Optional[dict]:
    now = time.monotonic()
    hit = _mode_cache.get(hid)
    if hit and now - hit[0] < _MODE_TTL:
        return hit[1] or None
    try:
        from agent_friday.services import decision_heads
        eff = decision_heads.effective(hid)
        spec = decision_heads.shipped(hid) if eff else None
        if eff is not None:
            eff = dict(eff, calibration=(spec or {}).get("calibration") or {})
    except Exception as e:  # noqa: BLE001
        _log.debug("decision head %s unavailable: %s", hid, e)
        eff = None
    _mode_cache[hid] = (now, eff or {})
    return eff


def clear_cache() -> None:
    _mode_cache.clear()


def mode(hid: str = SUFFICIENT) -> str:
    eff = _decision(hid)
    return str(eff.get("mode")) if eff else "off"


def _off_record() -> bool:
    try:
        from agent_friday.services import off_record as _off
        return bool(_off.active())
    except Exception:
        return False


# --------------------------------------------------------------------------
#  scorers
# --------------------------------------------------------------------------

def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def _series_one(question: str, passages: List[str], calibration: dict, *,
                shadow: bool = False) -> Optional[List[float]]:
    """R1 cosine through the head's Platt calibration. No calibration, no reading
    (an uncalibrated cosine is not a probability). In shadow it never loads the
    encoder cold, and skips the reading when the encoder is busy at that moment
    (review finding 1). It still holds the encoder for one short batch (about
    30 ms for nine texts); a live embed that arrives then waits that long.
    The calibration was fitted on Library-style passages and is applied to
    recalled conversation turns: fine for shadow, to be refitted from shadow
    receipts before any act."""
    platt = calibration.get("platt")
    if not (isinstance(platt, (list, tuple)) and len(platt) == 2):
        return None
    from agent_friday.services import laya2_encoder
    if shadow:
        if (laya2_encoder.status() or {}).get("state") != "ready":
            return None
        lock = getattr(laya2_encoder, "_lock", None)
        if lock is not None and lock.locked():
            return None
    elif not laya2_encoder.is_available():
        return None
    vecs = laya2_encoder.embed([question] + list(passages))
    q = vecs[0]
    a, b = float(platt[0]), float(platt[1])
    # The Library's own passage score (services/library/search.py): cosine x
    # (0.6 + 0.4 x keyword coverage). It is what the bench calibrated (E1 a_rule).
    from agent_friday.services.library.search import coverage, keywords
    keys = keywords(question)
    return [_sigmoid(a * (sum(x * y for x, y in zip(q, v)) * (0.6 + 0.4 * coverage(keys, p))) + b)
            for v, p in zip(vecs[1:], passages)]


def _laya1(question: str, passages: List[str], budget_ms: float) -> Optional[List[float]]:
    from agent_friday.services import laya_runtime
    out = []
    for p in passages:
        state = "Question: %s\nPassage: %s" % (question, p)
        r = laya_runtime.predict_bounded(state, {"holds": {"type": "noul", "instructions": PASSAGE_INSTRUCTIONS}},
                                         budget_ms=budget_ms)
        if r.get("status") != "ok":
            return None
        a = ((r.get("result") or {}).get("answers") or {}).get("holds") or {}
        if a.get("noul") is None:
            return None
        out.append(float(a["noul"]))
    return out


def score_holds(question: str, passages: Sequence[str], *, shadow: bool = False) -> Optional[List[float]]:
    """P(holds) per passage from the scorer the head names, or None (no reading).
    In shadow the Laya 1 scorer is refused: eight sequential CPU questions would
    take the approval gate's scoring slot and stack stuck Laya workers."""
    q = _clip(question, CLIP_Q)
    ps = [_clip(p, CLIP_P) for p in list(passages)[:MAX_PASSAGES]]
    if not q or not ps:
        return None
    if _scorer_override is not None:
        return _scorer_override(q, ps)
    eff = _decision(HOLDS)
    if not eff:
        return None
    try:
        if eff.get("scorer") == "laya1":
            return None if shadow else _laya1(q, ps, float(eff.get("budget_ms") or 1500))
        return _series_one(q, ps, eff.get("calibration") or {}, shadow=shadow)
    except Exception as e:  # noqa: BLE001
        _log.debug("evidence scorer failed: %s", e)
        return None


def sufficiency(best: float) -> str:
    """The evidence.sufficient label for the best passage's P(holds)."""
    eff = _decision(SUFFICIENT) or {}
    th = eff.get("thresholds") or {}
    act, abstain = float(th.get("act", 1.01)), float(th.get("abstain", 0.0))
    if best >= act:
        return "sufficient"
    if best < abstain:
        return "search_more"
    return "unsure"


# --------------------------------------------------------------------------
#  shadow: the memory block, read after it is built
# --------------------------------------------------------------------------

_q: "queue.Queue" = queue.Queue(maxsize=16)
_worker: Optional[threading.Thread] = None
_wlock = threading.Lock()
_counters = {"queued": 0, "dropped": 0, "read": 0, "failed": 0}


def stats() -> dict:
    return dict(_counters)


def _work() -> None:
    while True:
        job = _q.get()
        try:
            job()
        except Exception as e:  # noqa: BLE001
            _counters["failed"] += 1
            _log.debug("evidence shadow job failed: %s", e)
        finally:
            _q.task_done()


def _submit(job: Callable[[], None]) -> bool:
    global _worker
    if RUN_INLINE:
        job()
        return True
    with _wlock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, name="evidence-shadow", daemon=True)
            _worker.start()
    try:
        _q.put_nowait(job)
        _counters["queued"] += 1
        return True
    except queue.Full:
        _counters["dropped"] += 1
        return False


def _generation() -> Optional[int]:
    try:
        from agent_friday.services import off_record as _off
        return int(_off.generation())
    except Exception:
        return None


def _turn_id():
    try:
        from agent_friday import core
        return getattr(core._TURN_LOCAL, "turn_id", None)
    except Exception:
        return None


_seen_lock = threading.Lock()
_seen: List[tuple] = []
_SEEN_MAX = 64


def _first_time(key: tuple) -> bool:
    """The memory block can be built twice for one turn (chat builds it on two
    paths); a reading is taken once per (turn, question)."""
    with _seen_lock:
        if key in _seen:
            return False
        _seen.append(key)
        del _seen[:-_SEEN_MAX]
        return True


def shadow_memory(question: str, snippets: Sequence[str], *, surface: str = "chat") -> bool:
    """Called by the memory block (services/model_router) AFTER it has decided
    what to include. Queues a reading of the two heads; changes nothing. False
    when either head is off, the owner is off the record, the reading was
    already taken this turn, or there is nothing to read.

    The question and passages are clipped before they are queued. The scores
    are taken on the worker BEFORE Laya is asked (so the ask itself does no
    model work), the ask keeps its receipt in memory, and the receipt is
    written only if the owner is still on the record in the same
    private-memory generation as when the turn asked (LESSONS 274-284). The
    receipt's text hash is left empty: the question rides in opts only."""
    if mode(SUFFICIENT) == "off" or mode(HOLDS) == "off" or _off_record():
        return False
    q = _clip(question, CLIP_Q)
    ps = [_clip(s, CLIP_P) for s in (snippets or []) if str(s or "").strip()][:MAX_PASSAGES]
    if not q or not ps:
        return False
    import hashlib
    tid = _turn_id() or "t%d" % int(time.time() // 60)      # no turn id: the same question within a minute
    if not _first_time((tid, hashlib.sha256(q.encode("utf-8", "replace")).hexdigest()[:16])):
        return False
    gen = _generation()

    def job():
        if _off_record() or _generation() != gen:
            return
        got = score_holds(q, ps, shadow=True)
        if not got:
            _counters["failed"] += 1
            return
        from agent_friday.services import laya
        r = laya.ask({"text": "", "surface": "background",
                      "opts": {"question": q, "scores": got, "origin": surface, "log": False}},
                     {SUFFICIENT: {"type": "choice", "preset": SUFFICIENT}}, "shadow.%s" % SUFFICIENT,
                     budget_ms=500)
        if not _off_record() and _generation() == gen:
            laya.persist_receipt(r.get("receipt_id"))
        _counters["read"] += 1
    return _submit(job)


def drain(timeout: float = 5.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if _q.unfinished_tasks == 0:
            return True
        time.sleep(0.01)
    return _q.unfinished_tasks == 0


# --------------------------------------------------------------------------
#  the Laya presets (services/laya registers these names)
# --------------------------------------------------------------------------

def _scores(ctx) -> Optional[List[float]]:
    """Scores the caller already took (shadow), else scored here from opts."""
    pre = ctx.opts.get("scores")
    if isinstance(pre, list) and pre and all(isinstance(x, (int, float)) for x in pre):
        return [float(x) for x in pre]
    return score_holds(ctx.opts.get("question") or ctx.text, list(ctx.opts.get("passages") or []))


def preset_holds(ctx, spec: dict) -> dict:
    got = _scores(ctx)
    if not got:
        return {"answer": {"value": None, "p": None, "set": None, "abstain": True}, "layer": "degraded",
                "verdict": None, "native": None, "why": "no evidence reading (encoder or head unavailable)"}
    best = max(got)
    eff = _decision(HOLDS) or {}
    act = float((eff.get("thresholds") or {}).get("act", 1.01))
    return {"answer": {"value": best >= act, "p": round(best, 4), "set": None, "abstain": False},
            "layer": "t1" if eff.get("scorer") != "laya1" else "t2", "verdict": None, "native": got,
            "why": "best of %d passages, %s" % (len(got), eff.get("tag") or "untagged")}


def preset_sufficient(ctx, spec: dict) -> dict:
    got = _scores(ctx)
    if not got:
        return {"answer": {"value": None, "p": None, "set": None, "abstain": True}, "layer": "degraded",
                "verdict": None, "native": None, "why": "no evidence reading (encoder or head unavailable)"}
    best = max(got)
    label = sufficiency(best)
    eff = _decision(SUFFICIENT) or {}
    return {"answer": {"value": label, "p": round(best, 4), "set": None, "abstain": label == "unsure"},
            "layer": "t1" if (_decision(HOLDS) or {}).get("scorer") != "laya1" else "t2", "verdict": None,
            "native": got, "why": "best passage P(holds) %.2f; %s" % (best, eff.get("tag") or "untagged")}
