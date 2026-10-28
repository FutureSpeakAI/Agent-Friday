"""Citation support head, in shadow: does the passage a reply cites support the claim?

Spec: ftv/program/specs/decision_models_STORM_2026-10-24.md, head 5 (the second
wave, "evidence.supports"). One decision head (services/decision_heads):

  evidence.supports   choice, label_only   supports | contradicts | does_not_address | unsure

It reads a finished reply. For each [web:URL] or [lib:doc#block] footnote whose
passage this turn's own tools returned, it scores the sentence that carries the
footnote against that passage. Today's checks only ask whether a cited URL was
touched this turn (services/response_provenance) and whether a reply makes
claims at all (services/citation_enforcement); nothing asks whether the source
says what the sentence says.

It ships in SHADOW. The reply is shown, saved and spoken exactly as before; the
reading is taken afterwards on this module's own worker and leaves one Laya
receipt with labels, scores and counts, never a word. In act mode (a later
phase, Stephen's press only) it could only ANNOTATE a footnote as "not
supported by the cited source"; it can never delete a citation or a claim.

The scorer is Series One: the same Library passage score and Platt calibration
as the evidence heads (services/evidence_heads, DM4), taken from this head's own
file. That calibration was fitted for question-to-passage evidence, not
claim-to-passage support, so it is PROVISIONAL: act is set out of reach, and the
shadow receipts are what a later refit is built from. A similarity scorer cannot
tell a contradiction from support, so it never says "contradicts"; the label
exists for a later scorer that can.

Rules it keeps: OFF RECORD computes and writes nothing; NEVER BLOCKS (a bounded
queue on its own thread; a full queue drops the reading); NO NETWORK (only what
this turn's tools already returned, and the pinned local encoder); a missing or
refused decision head is off.
"""
from __future__ import annotations

import hashlib
import json
import logging
import queue
import re
import threading
import time
from typing import Callable, Dict, List, Optional, Sequence, Tuple

_log = logging.getLogger("friday.citation_heads")

HEAD = "evidence.supports"
MAX_PAIRS = 8
CLIP_CLAIM, CLIP_PASSAGE = 300, 700
_MODE_TTL = 3.0

WEB_RE = re.compile(r"\[web:(https?://[^\]\s]+)\]")
LIB_RE = re.compile(r"\[lib:(\d+)#(\d+)\]")
_ANY_TOKEN = re.compile(r"\[(?:unverified-)?(?:web|lib|wiki|news|memory):[^\]]*\]")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_EVIDENCE = re.compile(r"<evidence-(\w+) label=\"([^\"]*)\"[^>]*>\n(.*?)\n</evidence-\1>", re.S)

_mode_cache: Dict[str, tuple] = {}
#: Tests replace the scorer: a function (claim, passage) -> p, or None.
_scorer_override: Optional[Callable[[str, str], Optional[float]]] = None
RUN_INLINE = False


def _clip(text, n) -> str:
    return " ".join(str(text or "").split())[:n]


def _strip_url(u: str) -> str:
    return u.rstrip(").,;:!?\"'")


def _decision() -> Optional[dict]:
    now = time.monotonic()
    hit = _mode_cache.get(HEAD)
    if hit and now - hit[0] < _MODE_TTL:
        return hit[1] or None
    try:
        from agent_friday.services import decision_heads
        eff = decision_heads.effective(HEAD)
        spec = decision_heads.shipped(HEAD) if eff else None
        if eff is not None:
            eff = dict(eff, calibration=(spec or {}).get("calibration") or {})
    except Exception as e:  # noqa: BLE001
        _log.debug("decision head %s unavailable: %s", HEAD, e)
        eff = None
    _mode_cache[HEAD] = (now, eff or {})
    return eff


def clear_cache() -> None:
    _mode_cache.clear()


def mode() -> str:
    eff = _decision()
    return str(eff.get("mode")) if eff else "off"


def _off_record() -> bool:
    try:
        from agent_friday.services import off_record as _off
        return bool(_off.active())
    except Exception:
        return False


def _generation() -> Optional[int]:
    try:
        from agent_friday.services import off_record as _off
        return int(_off.generation())
    except Exception:
        return None


# --------------------------------------------------------------------------
#  claims and the passages they cite, from the reply and this turn's trace
# --------------------------------------------------------------------------

def _web_passage(url: str, tool_trace) -> Optional[str]:
    """What this turn's tools returned for a URL: the result of a call whose
    input was that URL, else the text right after the URL in a result that
    listed it (a search result's snippet). None when nothing returned it."""
    url = _strip_url(url)
    listed = None
    for row in tool_trace or []:
        if not isinstance(row, dict):
            continue
        res = row.get("result")
        if not isinstance(res, str) or not res.strip():
            continue
        inp = row.get("input")
        if isinstance(inp, dict) and _strip_url(str(inp.get("url") or "")) == url:
            return res
        if listed is None:
            i = res.find(url)
            if i >= 0:
                listed = res[i + len(url):i + len(url) + CLIP_PASSAGE * 2]
    return listed


def _lib_passages(tool_trace) -> Dict[str, str]:
    """ref ("lib:doc#block") -> passage text, for every passage search_library
    returned this turn in its fenced evidence."""
    out: Dict[str, str] = {}
    for row in tool_trace or []:
        if not isinstance(row, dict) or row.get("name") != "search_library":
            continue
        res = row.get("result")
        if not isinstance(res, str):
            continue
        head, _, body = res.partition("\n")
        try:
            refs = (json.loads(head) or {}).get("refs") or {}
        except (ValueError, AttributeError):
            continue
        texts = {label: text for _, label, text in _EVIDENCE.findall(body)}
        for label, ref in refs.items():
            if str(label) in texts:
                out[str(ref)] = texts[str(label)]
    return out


def pairs(reply: str, tool_trace) -> List[Tuple[str, str, str]]:
    """(kind, claim, passage) for each cited passage this turn returned. The claim
    is the sentence that carries the footnote, with every footnote taken out.
    A footnote the trace cannot back (no passage) is left out: provenance
    already marks those."""
    if not reply or ("[web:" not in reply and "[lib:" not in reply):
        return []
    lib = None
    out: List[Tuple[str, str, str]] = []
    seen: set = set()
    for sentence in _SENTENCE_END.split(reply):
        cites = [("web", m.group(1)) for m in WEB_RE.finditer(sentence)]
        cites += [("lib", "lib:%s#%s" % (m.group(1), m.group(2))) for m in LIB_RE.finditer(sentence)]
        if not cites:
            continue
        claim = _clip(_ANY_TOKEN.sub("", sentence), CLIP_CLAIM)
        if len(claim) < 12:
            continue
        for kind, ref in cites:
            if (claim, ref) in seen:
                continue
            seen.add((claim, ref))
            if kind == "web":
                passage = _web_passage(ref, tool_trace)
            else:
                if lib is None:
                    lib = _lib_passages(tool_trace)
                passage = lib.get(ref)
            passage = _clip(passage, CLIP_PASSAGE)
            if passage:
                out.append((kind, claim, passage))
            if len(out) >= MAX_PAIRS:
                return out
    return out


# --------------------------------------------------------------------------
#  scoring and labels
# --------------------------------------------------------------------------

def score(claim: str, passage: str, *, shadow: bool = True) -> Optional[float]:
    """P(the passage supports the claim) on the provisional calibration, or None."""
    if _scorer_override is not None:
        return _scorer_override(claim, passage)
    eff = _decision()
    if not eff or eff.get("scorer") != "series_one":
        return None
    try:
        from agent_friday.services import evidence_heads
        got = evidence_heads._series_one(claim, [passage], eff.get("calibration") or {}, shadow=shadow)
    except Exception as e:  # noqa: BLE001
        _log.debug("citation scorer failed: %s", e)
        return None
    return float(got[0]) if got else None


def label(p: float) -> str:
    """supports at or above act, does_not_address below abstain, else unsure.
    Series One never says contradicts."""
    th = (_decision() or {}).get("thresholds") or {}
    act, abstain = float(th.get("act", 1.01)), float(th.get("abstain", 0.0))
    if p >= act:
        return "supports"
    if p < abstain:
        return "does_not_address"
    return "unsure"


# --------------------------------------------------------------------------
#  shadow: the reply, read after it is final
# --------------------------------------------------------------------------

_q: "queue.Queue" = queue.Queue(maxsize=16)
_worker: Optional[threading.Thread] = None
_wlock = threading.Lock()
_counters = {"queued": 0, "dropped": 0, "read": 0, "failed": 0}
_seen_lock = threading.Lock()
_seen: List[str] = []
_SEEN_MAX = 64


def stats() -> dict:
    return dict(_counters)


def _work() -> None:
    while True:
        job = _q.get()
        try:
            job()
        except Exception as e:  # noqa: BLE001
            _counters["failed"] += 1
            _log.debug("citation shadow job failed: %s", e)
        finally:
            _q.task_done()


def _submit(job: Callable[[], None]) -> bool:
    global _worker
    if RUN_INLINE:
        job()
        return True
    with _wlock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, name="citation-shadow", daemon=True)
            _worker.start()
    try:
        _q.put_nowait(job)
        _counters["queued"] += 1
        return True
    except queue.Full:
        _counters["dropped"] += 1
        return False


def _first_time(key: str) -> bool:
    with _seen_lock:
        if key in _seen:
            return False
        _seen.append(key)
        del _seen[:-_SEEN_MAX]
        return True


def shadow_reply(reply: str, tool_trace: Sequence[dict], *, surface: str = "chat",
                 gen: Optional[int] = None) -> bool:
    """Called by the chat route AFTER the reply is final (provenance and Library
    footnotes checked). Queues one reading; changes nothing. False when the head
    is off, the owner is off the record, the private-memory generation changed
    since ``gen`` (the turn's start), this reply was already read, or no
    footnote has a passage this turn returned.

    The claims and passages are extracted and clipped here, on the caller's
    thread, so the queued job holds no reference to the live trace. The receipt
    is written only if the owner is still on the record in the same generation."""
    if mode() == "off" or _off_record():
        return False
    now_gen = _generation()
    if gen is not None and gen != now_gen:
        return False
    found = pairs(reply, tool_trace)
    if not found:
        return False
    if not _first_time(hashlib.sha256(str(reply).encode("utf-8", "replace")).hexdigest()[:16]):
        return False

    def job():
        if _off_record() or _generation() != now_gen:
            return
        scores, kinds = [], []
        for kind, claim, passage in found:
            p = score(claim, passage, shadow=True)
            if p is None:
                _counters["failed"] += 1
                return
            scores.append(round(p, 4))
            kinds.append(kind)
        from agent_friday.services import laya
        r = laya.ask({"text": "", "surface": "background",
                      "opts": {"scores": scores, "kinds": kinds, "origin": surface, "log": False}},
                     {HEAD: {"type": "choice", "preset": HEAD}}, "shadow.%s" % HEAD, budget_ms=500)
        if not _off_record() and _generation() == now_gen:
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


def preset(ctx, spec: dict) -> dict:
    """laya.ask preset "evidence.supports": one label for the reply, the weakest
    of its footnotes (does_not_address before unsure before supports), with
    every footnote's own label and score in native. Never a word of text."""
    got = ctx.opts.get("scores")
    if not (isinstance(got, list) and got and all(isinstance(x, (int, float)) for x in got)):
        return {"answer": {"value": None, "p": None, "set": None, "abstain": True}, "layer": "degraded",
                "verdict": None, "native": None, "why": "no citation reading (encoder or head unavailable)"}
    labels = [label(float(p)) for p in got]
    order = ["does_not_address", "unsure", "supports"]
    worst = min(labels, key=order.index)
    eff = _decision() or {}
    kinds = ctx.opts.get("kinds") or []
    return {"answer": {"value": worst, "p": round(min(float(p) for p in got), 4), "set": None,
                       "abstain": worst == "unsure"},
            "layer": "t1", "verdict": None,
            "native": [{"kind": k, "p": float(p), "label": lab}
                       for k, p, lab in zip(kinds or ["?"] * len(got), got, labels)],
            "why": "%d footnote(s): %d not addressed, %d unsure, %d supported; %s" % (
                len(got), labels.count("does_not_address"), labels.count("unsure"),
                labels.count("supports"), eff.get("tag") or "untagged")}
