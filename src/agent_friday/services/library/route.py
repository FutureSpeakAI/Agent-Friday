"""Search as a chain of small multiple-choice questions.

Every menu is a choice among at most eight children of one node (a folder, a
document, a section) plus "none of these". Two answerers take the same menu:

  E  the sentence encoder: cosine similarity between the question and each
     child's precomputed profile vector, turned into probabilities with a
     temperature and a learned no-match floor. Tens of microseconds per menu
     after one embedding of the question.
  L  Laya, asked as a `choice` question over the same menu, only when E is
     unsure and only when Laya is already loaded (this module never loads it).

The wording of the Laya questions below is Friday's own. Its accuracy is
measured with tools/library_eval.py before Laya answers any menu by default.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from agent_friday.services.library.store import library_root
from agent_friday.services.library.textclean import one_line

MAX_MENU = 8
ZERO = "Z"
KEYS = "ABCDEFGH"

DEFAULTS = {
    "beam": 4, "beam_floor": 3, "max_menus": 24, "max_laya": 4,
    "e_conf": 0.55, "e_margin": 0.10,          # E answers alone above both
    "act_conf": 0.55, "act_lead": 0.20,        # a menu acts (takes one child) above both
    "temp": {"folder": 0.04, "document": 0.04, "section": 0.04, "group": 0.04},
    "floor": 0.30,                              # similarity a child must beat to beat "none of these"
    "p_strong": 0.62, "p_weak": 0.28,
    "noul_hold": 0.5, "noul_stop": 0.8,
    "max_passages": 12, "max_chars": 6000,
    "laya_budget_ms": 1500,
}

#: What a menu question says to Laya. Friday's own wording.
ROUTE_INSTRUCTIONS = "Where in this collection is the answer to the question most likely to be found?"
NONE_OF_THESE = "None of these is likely to hold the answer."
PASSAGE_INSTRUCTIONS = "This passage contains the answer to the question."


def config() -> dict:
    """Defaults, overlaid with the certified calibration when one exists."""
    cfg = json.loads(json.dumps(DEFAULTS))
    p = library_root() / "calibration.json"
    try:
        over = json.loads(p.read_text(encoding="utf-8"))
        for k, v in over.items():
            if k in cfg and isinstance(v, type(cfg[k])):
                if isinstance(v, dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
    except Exception:
        pass
    return cfg


@dataclass
class Node:
    kind: str                       # folder | document | section | group
    id: object
    label: str                      # the routing profile shown to an answerer
    vec: object = None              # numpy unit vector or None
    doc_id: int | None = None
    shelf: str = "open"
    hint: bool = False
    extra: dict = field(default_factory=dict)
    children: Callable[[], list] | None = None

    @property
    def key(self) -> tuple:
        return (self.kind, self.id)


@dataclass
class Answer:
    probs: dict                     # option letter (and "Z") -> probability
    answerer: str                   # "E" | "L"
    top: str = ""
    conf: float = 0.0
    lead: float = 0.0

    @staticmethod
    def of(probs: dict, answerer: str) -> "Answer":
        ranked = sorted(probs.items(), key=lambda kv: -kv[1])
        top, p1 = ranked[0]
        p2 = ranked[1][1] if len(ranked) > 1 else 0.0
        return Answer(probs, answerer, top, p1, p1 - p2)


def sanitize_profile(text: str) -> str:
    """A routing profile as an option: control characters gone, one line, capped."""
    return one_line(text or "", 240)


# ── grouping wide nodes ──────────────────────────────────────────────────────

def _mean_vec(vs):
    vs = [v for v in vs if v is not None]
    if not vs:
        return None
    import numpy as np
    m = np.mean(np.vstack(vs), axis=0)
    n = float(np.linalg.norm(m))
    return (m / n).astype(np.float32) if n > 0 else None


def _make_group(name: str, members: list[Node], gid: str) -> Node:
    titles = "; ".join(one_line(m.extra.get("title") or m.label, 36) for m in members[:6])
    g = Node("group", gid, sanitize_profile(f"{name}: {len(members)} items — {titles}"),
             vec=_mean_vec([m.vec for m in members]), doc_id=None,
             extra={"members": members})
    g.children = lambda m=members: group_nodes(m)
    return g


def group_nodes(nodes: list[Node], _depth: int = 0) -> list[Node]:
    """At most MAX_MENU nodes: a wide list is grouped by type, then by year,
    then alphabetically, and each group is itself a menu when opened."""
    if len(nodes) <= MAX_MENU:
        return nodes
    for name, keyfn in (("type", lambda n: n.extra.get("ext") or n.kind), ("year", lambda n: n.extra.get("year"))):
        buckets: dict = defaultdict(list)
        for n in nodes:
            k = keyfn(n)
            if k is not None:
                buckets[k].append(n)
        if 1 < len(buckets) <= MAX_MENU and sum(len(v) for v in buckets.values()) == len(nodes):
            return [_make_group(f"{k}", v, f"g:{_depth}:{name}:{k}") for k, v in sorted(buckets.items(), key=lambda kv: str(kv[0]))]
    ordered = sorted(nodes, key=lambda n: (n.extra.get("title") or n.label).lower())
    size = math.ceil(len(ordered) / MAX_MENU)
    out = []
    for i in range(0, len(ordered), size):
        chunk = ordered[i:i + size]
        first = one_line(chunk[0].extra.get("title") or chunk[0].label, 18)
        last = one_line(chunk[-1].extra.get("title") or chunk[-1].label, 18)
        out.append(_make_group(f"{first} to {last}", chunk, f"g:{_depth}:az:{i}"))
    return out


# ── answerers ────────────────────────────────────────────────────────────────

def answer_e(qvec, options: list[Node], kind: str, cfg: dict) -> Answer:
    """Softmax over cosine similarities, with a floor option for 'none of these'."""
    import numpy as np
    T = float(cfg["temp"].get(kind, 0.06))
    floor = float(cfg["floor"])
    sims = []
    for o in options:
        sims.append(float(np.dot(qvec, o.vec)) if o.vec is not None and qvec is not None else floor - 0.05)
    logits = np.array([s / T for s in sims] + [floor / T], dtype=np.float64)
    logits -= logits.max()
    p = np.exp(logits)
    p /= p.sum()
    probs = {KEYS[i]: float(p[i]) for i in range(len(options))}
    probs[ZERO] = float(p[-1])
    return Answer.of(probs, "E")


def _laya_ready() -> bool:
    try:
        from agent_friday.services import laya_backend
        return laya_backend._agent is not None
    except Exception:
        return False


def laya_available() -> bool:
    return _laya_ready()


def answer_l(question: str, options: list[Node], cfg: dict) -> Answer | None:
    """Laya's answer to the same menu, or None when it cannot answer in budget."""
    if not _laya_ready():
        return None
    try:
        from agent_friday.services import laya_runtime
        crit = {KEYS[i]: sanitize_profile(o.label) for i, o in enumerate(options)}
        crit[ZERO] = NONE_OF_THESE
        q = {"route": {"type": "choice", "instructions": ROUTE_INSTRUCTIONS, "criteria": crit}}
        r = laya_runtime.predict_bounded(one_line(question, 300), q, budget_ms=cfg["laya_budget_ms"])
        if r["status"] != "ok":
            return None
        probs = (((r["result"] or {}).get("answers") or {}).get("route") or {}).get("probabilities") or {}
        probs = {k: float(v) for k, v in probs.items() if k in crit}
        return Answer.of(probs, "L") if probs else None
    except Exception:
        return None


def laya_holds(question: str, passage: str, cfg: dict) -> float | None:
    """P(this passage answers the question), or None when Laya cannot say."""
    if not _laya_ready():
        return None
    try:
        from agent_friday.services import laya_runtime
        state = f"Question: {one_line(question, 300)}\nPassage: {one_line(passage, 700)}"
        q = {"holds": {"type": "noul", "instructions": PASSAGE_INSTRUCTIONS}}
        r = laya_runtime.predict_bounded(state, q, budget_ms=cfg["laya_budget_ms"])
        if r["status"] != "ok":
            return None
        a = ((r["result"] or {}).get("answers") or {}).get("holds") or {}
        return float(a.get("noul")) if a.get("noul") is not None else None
    except Exception:
        return None


def e_is_sure(a: Answer, cfg: dict) -> bool:
    return a.conf >= cfg["e_conf"] and a.lead >= cfg["e_margin"]


def acts(a: Answer, cfg: dict) -> bool:
    """A menu takes its top child alone only above both the confidence and the lead."""
    return a.top != ZERO and a.conf >= cfg["act_conf"] and a.lead >= cfg["act_lead"]
