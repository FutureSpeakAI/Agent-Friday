"""Calibration and abstention: one method for every Laya head (swot spec c.4).

Three things, all small and all pure until they touch the shipped table:

  TEMPERATURE   one scalar per head. A head scores a turn as cosines against
                its prototypes; today a hand-set temperature turns them into
                probabilities. ``fit_temperature`` finds the one that makes the
                probabilities honest on labelled held-out turns.
  CONFORMAL SET at a target coverage. ``conformal_quantile`` turns the held-out
                scores into a cut-off, ``prediction_set`` applies it. One option
                in the set: the rung may act (if policy allows). Two or more:
                ask if a person is present, else escalate. None: "none of my
                routes".
  ECE           expected calibration error, reported per head before and after.

The shipped table (resources/laya_calibration.json) holds numbers and ids only:
head names, temperatures, cut-offs, thresholds, counts. It is fitted by
tools/laya_calibrate.py on dev sets 1-3 and never sees set 4 or set 5 (the
fitter refuses their files by name). No turn text is in it, and a test scans
for that (invariant 5).

SHADOW. ``reading`` is what laya.ask logs next to the current decision. It
changes nothing until the lead certifies it.

INVARIANT 6, learning only tightens. The shipped act thresholds are a floor.
``tighten`` can raise a threshold; ``lower`` and ``apply_refit`` can bring one
below the shipped value only with an owner-approval token, which is a real
approved card in services/approvals for exactly that head, name and value.
``may_graduate`` never lets an outward or sensitive tool leave "ask" through
learning, token or not.
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

_log = logging.getLogger("friday.laya_calibration")

TABLE_NAME = "laya_calibration.json"
TARGET_COVERAGE = 0.90
#: The only labelled sets a fit may read. Set 4 is spent and set 5 is sealed.
DEV_SETS = ("laya_router_eval.json", "laya_router_holdout2.json", "laya_router_holdout3.json")
FORBIDDEN_SET_MARKERS = ("holdout4", "holdout5", "set4", "set5", "sealed")
APPROVAL_KIND = "laya_threshold"

# ---------------------------------------------------------------------------
#  PURE MATHS
# ---------------------------------------------------------------------------


def softmax(logits: Sequence[float]) -> List[float]:
    if not logits:
        return []
    top = max(logits)
    exps = [math.exp(x - top) for x in logits]
    z = sum(exps) or 1.0
    return [e / z for e in exps]


def probs_from_sims(sims: Sequence[float], tau: float) -> List[float]:
    """Probabilities from cosines at temperature ``tau`` (the router's own
    form: softmax(sim / tau))."""
    return softmax([s / tau for s in sims])


def nll(sim_rows: Sequence[Sequence[float]], truth: Sequence[int], tau: float) -> float:
    total = 0.0
    for sims, t in zip(sim_rows, truth):
        total -= math.log(max(probs_from_sims(sims, tau)[t], 1e-12))
    return total / max(1, len(truth))


def fit_temperature(sim_rows: Sequence[Sequence[float]], truth: Sequence[int],
                    base: float = 0.10, lo: float = 0.02, hi: float = 1.0, steps: int = 160) -> float:
    """The temperature that minimises held-out negative log-likelihood, by a
    log-spaced grid (a one-parameter fit needs nothing cleverer). With no data
    the base temperature stands."""
    if not sim_rows:
        return base
    best, best_v = base, nll(sim_rows, truth, base)
    for i in range(steps + 1):
        tau = lo * (hi / lo) ** (i / steps)
        v = nll(sim_rows, truth, tau)
        if v < best_v - 1e-12:
            best, best_v = tau, v
    return round(best, 5)


def ece(conf: Sequence[float], correct: Sequence[bool], bins: int = 10) -> float:
    """Expected calibration error of top-1 confidence, equal-width bins."""
    n = len(conf)
    if n == 0:
        return 0.0
    total = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, c in enumerate(conf) if (lo <= c < hi) or (b == bins - 1 and c == 1.0)]
        if not idx:
            continue
        acc = sum(1 for i in idx if correct[i]) / len(idx)
        avg = sum(conf[i] for i in idx) / len(idx)
        total += len(idx) / n * abs(acc - avg)
    return round(total, 4)


def conformal_quantile(scores: Sequence[float], coverage: float = TARGET_COVERAGE) -> float:
    """Split-conformal cut-off on nonconformity scores (1 - p of the true
    label): the ceil((n+1)*coverage)-th smallest. Above 1.0 means "every label
    is in the set" (too little data to promise coverage)."""
    n = len(scores)
    if n == 0:
        return 1.0
    k = math.ceil((n + 1) * coverage)
    if k > n:
        return 1.0
    return round(sorted(scores)[k - 1], 5)


def prediction_set(probs: Dict[str, float], q_hat: float) -> List[str]:
    """Labels whose nonconformity (1 - p) is within the cut-off, best first."""
    kept = [(l, p) for l, p in probs.items() if 1.0 - p <= q_hat + 1e-12]
    return [l for l, _ in sorted(kept, key=lambda r: -r[1])]


def decide(label_set: Sequence[str], *, person_present: bool) -> str:
    """act | ask | escalate | none, from the conformal set's size."""
    if not label_set:
        return "none"
    if len(label_set) == 1:
        return "act"
    return "ask" if person_present else "escalate"


# ---------------------------------------------------------------------------
#  THE SHIPPED TABLE
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_table: Optional[dict] = None
_table_override: Optional[dict] = None


def table_path() -> Path:
    return Path(__file__).resolve().parents[1] / "resources" / TABLE_NAME


def load_table() -> dict:
    """The shipped table, or an empty one. Never raises."""
    global _table
    with _lock:
        if _table_override is not None:
            return _table_override
        if _table is None:
            try:
                _table = json.loads(table_path().read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                _log.debug("no calibration table: %s", e)
                _table = {"version": "none", "heads": {}}
        return _table


def set_table(table: Optional[dict]) -> None:
    """Tests, and a refit that has been certified: use ``table`` instead of the
    shipped file (None goes back to the file)."""
    global _table_override, _table
    with _lock:
        _table_override = table
        _table = None


def table_version() -> str:
    t = load_table()
    return str(t.get("version") or "none")


def head_entry(name: str) -> Optional[dict]:
    e = (load_table().get("heads") or {}).get(name)
    return e if isinstance(e, dict) and e.get("fitted") else None


def reading(name: str, sims: Dict[str, float], *, person_present: bool = True) -> Optional[dict]:
    """The calibrated reading of one head for one turn, or None when the head
    has no fitted entry. ``sims`` is {label: raw cosine}. Pure; shadow only."""
    e = head_entry(name)
    if e is None or not sims:
        return None
    labels = list(sims)
    probs = dict(zip(labels, probs_from_sims([sims[l] for l in labels], float(e["tau"]))))
    label_set = prediction_set(probs, float(e["q_hat"]))
    top = max(probs, key=lambda l: probs[l])
    return {"head": name, "label": top, "p": round(probs[top], 4), "set": label_set,
            "decision": decide(label_set, person_present=person_present),
            "tau": float(e["tau"]), "coverage": float(e.get("coverage", TARGET_COVERAGE))}


# ---------------------------------------------------------------------------
#  INVARIANT 6: learning only tightens
# ---------------------------------------------------------------------------

_learned_override: Optional[dict] = None


def _learned_path() -> Path:
    from agent_friday.core import FRIDAY_DIR
    return FRIDAY_DIR / "runtime" / "laya2" / "thresholds.json"


def _read_learned() -> dict:
    if _learned_override is not None:
        return _learned_override
    try:
        return json.loads(_learned_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_learned(data: dict) -> None:
    global _learned_override
    if _learned_override is not None:
        _learned_override = data
        return
    try:
        p = _learned_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, sort_keys=True, indent=1), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        _log.debug("learned thresholds not saved: %s", e)


def use_memory_store(initial: Optional[dict] = None) -> None:
    """Tests: keep learned thresholds in memory instead of the runtime folder."""
    global _learned_override
    _learned_override = {} if initial is None else initial


def use_disk_store() -> None:
    global _learned_override
    _learned_override = None


def shipped_threshold(head: str, name: str) -> Optional[float]:
    """The act threshold the build shipped (a floor), or None if it has none."""
    t = ((load_table().get("heads") or {}).get(head) or {}).get("thresholds") or {}
    v = t.get(name)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def token_valid(token, head: str, name: str, value: float) -> bool:
    """True only for an approved owner card of kind ``laya_threshold`` whose
    payload names exactly this head, threshold and value. Anything else,
    including a string that merely looks like an id, is False."""
    try:
        if not token or not isinstance(token, str):
            return False
        from agent_friday.services import approvals
        rec = approvals.get_approval(token)
        if not rec or rec.get("status") != "approved" or rec.get("kind") != APPROVAL_KIND:
            return False
        p = rec.get("payload") or {}
        return (p.get("head") == head and p.get("name") == name
                and abs(float(p.get("value")) - float(value)) < 1e-9)
    except Exception:
        return False


def effective_threshold(head: str, name: str) -> Optional[float]:
    """What the head must clear to act: the shipped floor, raised by anything
    learned. A learned value below the floor counts only if the approved owner
    card it cites still checks out."""
    base = shipped_threshold(head, name)
    rec = (_read_learned().get(head) or {}).get(name)
    if base is None:
        return None
    if not isinstance(rec, dict):
        return base
    try:
        v = float(rec.get("value"))
    except Exception:
        return base
    if v >= base:
        return v
    return v if token_valid(rec.get("owner_token"), head, name, v) else base


def tighten(head: str, name: str, value: float) -> float:
    """Raise a threshold. A value at or below the current one changes nothing.
    Returns the threshold now in force."""
    cur = effective_threshold(head, name)
    if cur is None:
        raise KeyError("no shipped threshold %s/%s" % (head, name))
    if float(value) <= cur:
        return cur
    data = _read_learned()
    data.setdefault(head, {})[name] = {"value": round(float(value), 6), "at": int(time.time())}
    _write_learned(data)
    return effective_threshold(head, name)


def lower(head: str, name: str, value: float, *, owner_token) -> float:
    """Lower a threshold below its current value. Needs the owner's approval;
    without a valid token this changes nothing and returns the value in force."""
    cur = effective_threshold(head, name)
    if cur is None:
        raise KeyError("no shipped threshold %s/%s" % (head, name))
    if float(value) >= cur:
        return tighten(head, name, value)
    if not token_valid(owner_token, head, name, float(value)):
        return cur
    data = _read_learned()
    data.setdefault(head, {})[name] = {"value": round(float(value), 6), "owner_token": owner_token,
                                        "at": int(time.time())}
    _write_learned(data)
    return effective_threshold(head, name)


def apply_refit(head: str, proposed: Dict[str, float], owner_token=None) -> Dict[str, float]:
    """An automatic refit proposes thresholds. Raises are applied; a proposal
    below what is in force is applied only with a valid owner token for that
    exact value, else dropped. Returns the thresholds now in force."""
    out: Dict[str, float] = {}
    for name, v in proposed.items():
        if shipped_threshold(head, name) is None:
            continue
        cur = effective_threshold(head, name)
        out[name] = tighten(head, name, v) if float(v) >= cur else lower(
            head, name, v, owner_token=owner_token)
    return out


def may_graduate(tool: str, *, owner_token=None) -> bool:
    """May learning move ``tool`` from "ask" to "act"? Never for an outward or
    sensitive tool (read from the governed tool table, not from any model or
    manifest); for any other tool only with the owner's approval card."""
    try:
        from agent_friday.services import laya_router, tool_catalogue
        if tool in laya_router.ASK_BEFORE_GUESSING:
            return False
        a = tool_catalogue.annotations(tool)
        if a.get("openWorldHint") or a.get("destructiveHint") or not a.get("readOnlyHint"):
            return False
    except Exception:
        return False
    return token_valid(owner_token, "graduate", tool, 1.0)


# ---------------------------------------------------------------------------
#  FITTING (used by tools/laya_calibrate.py; never by the request path)
# ---------------------------------------------------------------------------

def check_fit_sources(paths: Iterable[str]) -> None:
    """Refuse anything but dev sets 1-3. Judged by file name, on every path."""
    for p in paths:
        name = Path(str(p)).name.lower()
        if name not in DEV_SETS or any(m in name for m in FORBIDDEN_SET_MARKERS):
            raise ValueError("calibration may fit only on dev sets 1-3, not %r" % name)


def fit_head(sim_rows: Sequence[Sequence[float]], truth: Sequence[int], test_rows: Sequence[Sequence[float]],
             test_truth: Sequence[int], *, base_tau: float, coverage: float = TARGET_COVERAGE,
             ) -> dict:
    """Fit on (sim_rows, truth); report on the untouched (test_rows, test_truth).
    Returns tau, q_hat, ECE before/after (fit and test splits) and the set sizes
    and coverage actually seen on the test split."""
    tau = fit_temperature(sim_rows, truth, base=base_tau)

    def stats(rows, t, temp):
        pr = [probs_from_sims(r, temp) for r in rows]
        conf = [max(p) for p in pr]
        ok = [p.index(max(p)) == y for p, y in zip(pr, t)]
        return conf, ok, pr

    cal_scores = [1.0 - probs_from_sims(r, tau)[y] for r, y in zip(sim_rows, truth)]
    q_hat = conformal_quantile(cal_scores, coverage)
    out = {"fitted": True, "tau": tau, "base_tau": base_tau, "q_hat": q_hat, "coverage": coverage,
           "n_fit": len(truth), "n_test": len(test_truth)}
    for split, rows, t in (("fit", sim_rows, truth), ("test", test_rows, test_truth)):
        if not t:
            continue
        c0, ok0, _ = stats(rows, t, base_tau)
        c1, ok1, pr1 = stats(rows, t, tau)
        covered = sum(1 for p, y in zip(pr1, t) if 1.0 - p[y] <= q_hat + 1e-12)
        sizes = [sum(1 for x in p if 1.0 - x <= q_hat + 1e-12) for p in pr1]
        out[split] = {"ece_before": ece(c0, ok0), "ece_after": ece(c1, ok1),
                      "accuracy": round(sum(ok1) / len(t), 4),
                      "coverage_seen": round(covered / len(t), 4),
                      "set_size_mean": round(sum(sizes) / len(sizes), 3),
                      "singletons": round(sum(1 for s in sizes if s == 1) / len(sizes), 3)}
    return out
