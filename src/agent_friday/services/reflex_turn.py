"""Laya 2 tier 1 on every turn, in shadow: what shape is this turn?

Before the brain sees a message, the small encoder (services/laya2_encoder)
scores it against the synthetic prototypes of services/laya2_seeds and answers
three questions: the turn's SHAPE (a one-tool reflex, conversation, a question
for the brain, deep coding or analysis, an approval reply), whether the words
are READ-ONLY, STATE-CHANGING or an ASK, and whether they are IN SCOPE at all.
From those, `classify` names a route ("reflex" or "brain") and the reasoning
effort the brain should spend (services/reasoning_policy reads it).

The rules that cannot move (speed spec section 3.2):

  ADD-ONLY      a tier-1 verdict can only escalate a governance verdict.
                `combine_gate` is that lattice; `laya_backend.union_backend`
                stays OR. Nothing here grants, runs or approves anything.
  NEVER BLOCKS  `classify` waits at most `budget_ms`; a missing or slow
                encoder degrades to route "brain", effort "medium", and the
                miss is counted. The first call warms the encoder in the
                background and is itself a counted miss.
  LOW CONFIDENCE GOES TO THE BRAIN
                "reflex" needs confidence >= ACT_THRESHOLD, a margin over the
                runner-up >= MARGIN_THRESHOLD, an in-scope verdict and a
                mutation that is not state-changing. Anything else is the
                brain's turn.
  SHADOW ONLY   `shadow` logs a verdict per turn with a hash of the text and
                never the text; nothing acts on the route until the labels
                say the thresholds hold.

The thresholds are starting points for the shadow period, not certified
numbers; `tools/laya2_certify.py` (owed) replaces them from labelled data.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutureTimeout
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from agent_friday.services import laya2_encoder, laya2_seeds

_log = logging.getLogger("friday.reflex_turn")

ACT_THRESHOLD = 0.55
MARGIN_THRESHOLD = 0.10
#: Best raw cosine below this: the seeds know nothing like it, so it is out of
#: scope whatever the nearest prototype is called.
OOS_FLOOR = 0.30
#: Softmax temperature over prototype cosines; cosines of a sentence encoder
#: live in a narrow band, so a flat softmax would never reach ACT_THRESHOLD.
TEMPERATURE = 0.10
DEFAULT_BUDGET_MS = 50
SHADOW_DIRNAME = "laya2"
SHADOW_NAME = "reflex_shadow.jsonl"

#: Governance severity lattice (services/decisions `policy_class`), low to high.
GATE_ORDER = ("internal", "outward", "hard")

_proto_lock = threading.Lock()
_protos: Optional[Dict[str, Tuple[List[str], list]]] = None
_exec_lock = threading.Lock()
_executor: Optional[ThreadPoolExecutor] = None
_shadow_lock = threading.Lock()
_counters = {"classified": 0, "degraded_missing": 0, "degraded_budget": 0,
             "degraded_error": 0}


# ---------------------------------------------------------------------------
#  PROTOTYPES
# ---------------------------------------------------------------------------

def _mean_unit(vectors: List[List[float]]) -> List[float]:
    n = len(vectors)
    dim = len(vectors[0])
    acc = [0.0] * dim
    for v in vectors:
        for i in range(dim):
            acc[i] += v[i]
    acc = [x / n for x in acc]
    norm = math.sqrt(sum(x * x for x in acc)) or 1.0
    return [x / norm for x in acc]


def _build_prototypes() -> Dict[str, Tuple[List[str], list]]:
    out = {}
    for head, seeds in (("shape", laya2_seeds.SHAPE_SEEDS),
                        ("mutation", laya2_seeds.MUTATION_SEEDS)):
        labels = list(seeds.keys())
        texts = [t for label in labels for t in seeds[label]]
        vecs = laya2_encoder.embed(texts)
        protos, i = [], 0
        for label in labels:
            k = len(seeds[label])
            protos.append(_mean_unit(vecs[i:i + k]))
            i += k
        out[head] = (labels, protos)
    return out


def _prototypes() -> Dict[str, Tuple[List[str], list]]:
    global _protos
    with _proto_lock:
        if _protos is None:
            _protos = _build_prototypes()
        return _protos


def reset_cache() -> None:
    """Drop the cached prototypes (tests, and after a seed or encoder change)."""
    global _protos
    with _proto_lock:
        _protos = None


def _score(vec: List[float], labels: List[str], protos: list) -> Tuple[str, float, float, float]:
    """(label, raw cosine, softmax confidence, margin to the runner-up)."""
    sims = [sum(a * b for a, b in zip(p, vec)) for p in protos]
    order = sorted(range(len(sims)), key=lambda i: sims[i], reverse=True)
    top, second = order[0], (order[1] if len(order) > 1 else order[0])
    peak = sims[top]
    weights = [math.exp((s - peak) / TEMPERATURE) for s in sims]
    conf = weights[top] / (sum(weights) or 1.0)
    margin = sims[top] - sims[second] if top != second else sims[top]
    return labels[top], sims[top], conf, margin


def _classify_now(text: str) -> dict:
    protos = _prototypes()
    (vec,) = laya2_encoder.embed([text])
    labels, p = protos["shape"]
    shape, shape_sim, shape_conf, margin = _score(vec, labels, p)
    labels, p = protos["mutation"]
    mutation, _, mutation_conf, _ = _score(vec, labels, p)
    oos = shape == "out_of_scope" or shape_sim < OOS_FLOOR
    return {"shape": shape, "shape_conf": round(shape_conf, 4),
            "shape_sim": round(shape_sim, 4), "margin": round(margin, 4),
            "mutation": mutation, "mutation_conf": round(mutation_conf, 4),
            "oos": bool(oos)}


# ---------------------------------------------------------------------------
#  THE RULE
# ---------------------------------------------------------------------------

def decide(verdict: dict) -> Tuple[str, str]:
    """(route, effort) for a scored verdict. Pure.

    "reflex" is the only route that could skip the brain, so it is the one
    that needs every condition; everything else is "brain". Effort: deep
    shapes think hardest, a reflex the brain still has to answer thinks
    least, and every other turn keeps the default `medium`.
    """
    shape = str(verdict.get("shape") or "")
    reflex = (shape in laya2_seeds.REFLEX_SHAPES
              and float(verdict.get("shape_conf") or 0.0) >= ACT_THRESHOLD
              and float(verdict.get("margin") or 0.0) >= MARGIN_THRESHOLD
              and not verdict.get("oos")
              and verdict.get("mutation") != "state_changing")
    route = "reflex" if reflex else "brain"
    if shape in laya2_seeds.DEEP_SHAPES:
        effort = "xhigh"
    elif route == "reflex":
        effort = "none"
    else:
        effort = "medium"
    return route, effort


def _degraded(reason: str, elapsed_ms: float) -> dict:
    _counters["degraded_" + reason] = _counters.get("degraded_" + reason, 0) + 1
    return {"shape": "brain", "shape_conf": 0.0, "margin": 0.0,
            "mutation": "unknown", "oos": False, "route": "brain",
            "effort": "medium", "elapsed_ms": round(elapsed_ms, 2),
            "degraded": True, "reason": reason}


def _pool() -> ThreadPoolExecutor:
    global _executor
    with _exec_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="laya2-tier1")
        return _executor


def classify(text: str, *, budget_ms: int = DEFAULT_BUDGET_MS) -> dict:
    """Score one turn within `budget_ms`. Never raises, never waits longer.

    The encoder runs on one worker thread; this call waits for its answer at
    most `budget_ms` and otherwise returns a degraded verdict while the worker
    finishes (and, on the first call, loads the model and builds the
    prototypes) in the background. A degraded verdict routes to the brain at
    `medium` effort, which is exactly today's behaviour.
    """
    t0 = time.perf_counter()
    text = " ".join(str(text or "").split())
    if not laya2_encoder.is_available():
        return _degraded("missing", (time.perf_counter() - t0) * 1000)
    try:
        future = _pool().submit(_classify_now, text)
        scored = future.result(timeout=max(0.0, float(budget_ms)) / 1000.0)
    except _FutureTimeout:
        return _degraded("budget", (time.perf_counter() - t0) * 1000)
    except Exception as e:
        _log.debug("laya2 tier 1 error: %s", e)
        return _degraded("error", (time.perf_counter() - t0) * 1000)
    route, effort = decide(scored)
    _counters["classified"] += 1
    out = dict(scored)
    out.update(route=route, effort=effort, degraded=False,
               elapsed_ms=round((time.perf_counter() - t0) * 1000, 2))
    return out


def counters() -> dict:
    return dict(_counters)


# ---------------------------------------------------------------------------
#  ADD-ONLY
# ---------------------------------------------------------------------------

def tier1_gate_opinion(tier1: Optional[dict]) -> Optional[str]:
    """What tier 1 has to say to the governance gate, or None for nothing.

    A confident state-changing reading is worth an "outward" opinion: the
    words ask to change something. A degraded, out-of-scope or read-only
    verdict says nothing, and nothing is the only other answer: tier 1 never
    argues a gate down.
    """
    if not tier1 or tier1.get("degraded"):
        return None
    if tier1.get("mutation") == "state_changing":
        return "outward"
    return None


def combine_gate(keyword_verdict: str, tier1: Optional[dict]) -> str:
    """keyword OR tier 1 on the severity lattice; never below the keyword.

    A verdict the lattice does not know passes through unchanged: a label
    this module has not seen must not be rewritten by it.
    """
    opinion = tier1_gate_opinion(tier1)
    if opinion is None or keyword_verdict not in GATE_ORDER:
        return keyword_verdict
    return max(keyword_verdict, opinion, key=GATE_ORDER.index)


# ---------------------------------------------------------------------------
#  SHADOW
# ---------------------------------------------------------------------------

def shadow_path() -> Path:
    from agent_friday.core import FRIDAY_DIR
    return FRIDAY_DIR / "runtime" / SHADOW_DIRNAME / SHADOW_NAME


def shadow(text: str, conversation_id: Optional[str] = None) -> Optional[dict]:
    """Classify one turn and append its verdict to the shadow log.

    The line carries a hash of the text, its length and the verdict; never
    the words themselves, and nothing that names the conversation. Returns
    the verdict so the caller may carry it (as advice, never as a grant), or
    None when even that failed. Never raises.
    """
    try:
        verdict = classify(text)
    except Exception:
        return None
    try:
        digest = hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()[:16]
        line = {"ts": round(time.time(), 3), "text_sha16": digest,
                "len": len(str(text or ""))}
        for key in ("shape", "shape_conf", "margin", "mutation", "oos", "route",
                    "effort", "elapsed_ms", "degraded"):
            line[key] = verdict.get(key)
        path = shadow_path()
        with _shadow_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(line, separators=(",", ":")) + "\n")
    except Exception as e:
        _log.debug("laya2 shadow log skipped: %s", e)
    return verdict
