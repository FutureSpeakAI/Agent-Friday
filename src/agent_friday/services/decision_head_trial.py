"""Old-versus-new trial for a decision head's thresholds (DM6, spec section 6.3).

Spec: ftv/program/specs/decision_models_STORM_2026-10-24.md, section 6.3, the
subject kind ``decision_head_threshold``: same scorer, new numbers.

Arm A is the thresholds in force and arm B the candidate. Both are replayed from
the scores the receipts already logged (services/decision_replay), over the last
28 days, so no model runs and no GPU is used. The rule, fixed and versioned:

  - B is recommended only if wrong acts on gold rows do not rise, AND misses
    fall by at least one, AND no regression task newly fails when its head
    receipts are re-graded under B;
  - otherwise keep current; ties go to current.

Latency cannot tell the arms apart here (the same logged scores, the same
scorer), so only the miss clause can recommend a candidate. Rows with no gold
count for nothing: a head never grades itself (DM-E11), and gold comes only
from the nightly replay's own gold store (owner and answer-key paths). A candidate may carry only ``act``, ``margin``
and ``abstain``; direction and mode are code and the owner's press.

This module reads and reports. It writes no threshold, no card and no gold, and
no Laya or head module imports it (spec A4): a recommendation is evidence for
the owner's click on a screen-only card, never a change.

The verdict holds counts, rates and receipt ids only, never words.
"""
from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional

RULE_VERSION = "decision-head-trial-rule-1 (2026-11-04)"
SUBJECT_KIND = "decision_head_threshold"
WINDOW_DAYS = 28
CANDIDATE_KEYS = frozenset({"act", "margin", "abstain"})


def _arm(r: dict) -> dict:
    return {"n": r["n"], "miss": r["miss"], "wrong": r["wrong"], "take_rate": r["take_rate"],
            "abstain_rate": r["abstain_rate"], "n_gold_scored": r["n_gold_scored"],
            "ece": r["ece"], "fail_ids": list(r["fail_ids"])}


def _keep(head: str, reason: str, **extra) -> dict:
    out = {"rule": RULE_VERSION, "subject": {"kind": SUBJECT_KIND, "id": head}, "window_days": WINDOW_DAYS,
           "recommend": "current", "label": "keep current", "reason": reason}
    out.update(extra)
    return out


def _refusal(head: str, current: dict, candidate: dict, shipped: Optional[dict]) -> Optional[str]:
    from agent_friday.services import decision_replay as dr
    if not isinstance(candidate, dict) or not candidate:
        return "the candidate carries no thresholds"
    extra = sorted(set(candidate) - CANDIDATE_KEYS)
    if extra:
        return "a candidate may carry only %s; %s is code or the owner's press" % (
            "/".join(sorted(CANDIDATE_KEYS)), "/".join(str(k) for k in extra))
    for k, v in candidate.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0.0 <= float(v) <= 1.0:
            return "%s must be a number from 0 to 1" % k
    logged = max(float((shipped or {}).get("abstain", 0.0)), float(current.get("abstain", 0.0)))
    return dr._candidate_refusal(head, logged, candidate)


def _failed_tasks(tasks: Dict[str, List[dict]], head: str, th: dict, labels, gold) -> set:
    from agent_friday.services import decision_replay as dr
    out = set()
    for task, rows in tasks.items():
        r = dr.replay_head(list(rows), head, th, labels, gold)
        if r["verdict"]["fail"] or r["wrong"]:
            out.add(task)
    return out


def verdict(head: str, *, current: dict, candidate: dict, rows: List[dict], labels: Optional[List[str]],
            gold: Dict[tuple, object], regression: Optional[Dict[str, List[dict]]] = None,
            shipped: Optional[dict] = None) -> dict:
    """The section 6.3 verdict for one head. Pure: ``rows`` are the head's logged
    rows (decision_replay.rows), ``gold`` is {(receipt id, head): gold}, and
    ``regression`` maps each regression practice task to its head rows. Without
    a regression set the answer is keep current."""
    from agent_friday.services import decision_replay as dr
    why = _refusal(head, current, candidate, shipped)
    if why:
        return _keep(head, "refused: " + why, refused=why)
    cand_th = {**current, **candidate}
    a = dr.replay_head(rows, head, current, labels, gold)
    b = dr.replay_head(rows, head, cand_th, labels, gold)
    arms = {"current": _arm(a), "candidate": _arm(b)}
    if regression is None:
        return _keep(head, "no regression set was supplied; the trial cannot clear the regression clause", arms=arms)
    new_fail = sorted(_failed_tasks(regression, head, cand_th, labels, gold)
                      - _failed_tasks(regression, head, current, labels, gold))
    reg = {"tasks": len(regression), "new_failures": new_fail}
    if b["wrong"] > a["wrong"]:
        return _keep(head, "wrong acts on gold rows would rise (%d to %d)" % (a["wrong"], b["wrong"]),
                     arms=arms, regression=reg)
    if a["miss"] - b["miss"] < 1:
        return _keep(head, "misses do not fall by at least one (%d to %d); ties go to current"
                     % (a["miss"], b["miss"]), arms=arms, regression=reg)
    if new_fail:
        return _keep(head, "a regression task newly fails under the candidate", arms=arms, regression=reg)
    return {"rule": RULE_VERSION, "subject": {"kind": SUBJECT_KIND, "id": head}, "window_days": WINDOW_DAYS,
            "recommend": "candidate", "label": "the new numbers did better",
            "reason": "misses fell from %d to %d and wrong acts did not rise (%d to %d)"
                      % (a["miss"], b["miss"], a["wrong"], b["wrong"]),
            "arms": arms, "regression": reg}


def run(head: str, candidate: dict, *, now: Optional[float] = None,
        regression: Optional[Dict[str, List[dict]]] = None,
        should_stop: Optional[Callable[[], bool]] = None) -> dict:
    """Read the last 28 days of this head's receipts and the gold recorded for
    them, then give the section 6.3 verdict for ``candidate`` against the
    thresholds in force. Reads only; sets and writes nothing."""
    from agent_friday.services import decision_heads, decision_replay as dr
    spec = decision_heads.shipped(head)
    if spec is None:
        return _keep(head, "no such decision head")
    eff = decision_heads.effective(head) or {}
    current = dict(eff.get("thresholds") or spec.get("thresholds") or {})
    now = now or time.time()
    gold = dr._gold()
    hrows = dr.rows(now - WINDOW_DAYS * 86400, now, [head], should_stop=should_stop,
                    gold_ids={k[0] for k in gold})
    return verdict(head, current=current, candidate=candidate, rows=hrows, labels=spec.get("labels"),
                   gold=gold, regression=regression, shipped=spec.get("thresholds") or {})
