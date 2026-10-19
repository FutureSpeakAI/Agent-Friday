"""Pairing, missingness and exact sign tests for practice-task comparisons.

The unit of evidence is the TASK, not the run: two repeats of one task are
correlated, so counting them as independent would overstate the evidence.
A (task, repeat) pair with either side missing is dropped from both arms, so
missingness cannot favour one arm. Stdlib only.
"""
from __future__ import annotations

import math
import random
from typing import Dict, Iterable, List, Optional, Tuple

#: More than this share missing in either arm makes a comparison inconclusive.
MAX_MISSING_SHARE = 0.10
#: One arm missing more than this many times as often as the other (once at
#: least two are missing) makes it inconclusive too.
MAX_MISSING_RATIO = 2.0


def _binom_tail_ge(k: int, n: int) -> float:
    """P(X >= k) for X ~ Binomial(n, 1/2), exact."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    return sum(math.comb(n, i) for i in range(k, n + 1)) / (2 ** n)


def sign_test_one_sided(wins: int, losses: int) -> float:
    """Exact one-sided p-value that the 'wins' side is better (ties excluded)."""
    return _binom_tail_ge(int(wins), int(wins) + int(losses))


def sign_test_two_sided(wins: int, losses: int) -> float:
    n = int(wins) + int(losses)
    if n == 0:
        return 1.0
    k = max(int(wins), int(losses))
    return min(1.0, 2.0 * _binom_tail_ge(k, n))


def smallest_one_sided_wins(n: int, alpha: float = 0.10) -> Optional[int]:
    """The fewest wins out of n non-tied tasks that reach p <= alpha (None if none)."""
    for w in range(n + 1):
        if sign_test_one_sided(w, n - w) <= alpha:
            return w
    return None


def is_pass(grade: Optional[dict]) -> Optional[bool]:
    """True/False for a graded episode, None for a missing one."""
    if not grade or grade.get("status") != "graded":
        return None
    return bool(grade.get("pass"))


def pair_up(rows: Iterable[dict]) -> Tuple[List[dict], Dict[str, int]]:
    """`rows`: {"task", "repeat", "a": grade, "b": grade}. Returns the complete
    pairs and missing counts {"a", "b", "pairs_dropped", "planned"}."""
    complete, miss = [], {"a": 0, "b": 0, "pairs_dropped": 0, "planned": 0}
    for r in rows:
        miss["planned"] += 1
        pa, pb = is_pass(r.get("a")), is_pass(r.get("b"))
        if pa is None:
            miss["a"] += 1
        if pb is None:
            miss["b"] += 1
        if pa is None or pb is None:
            miss["pairs_dropped"] += 1
            continue
        complete.append({"task": r["task"], "repeat": r.get("repeat", 0), "a": pa, "b": pb,
                         "a_safety": bool((r.get("a") or {}).get("safety_fail")),
                         "b_safety": bool((r.get("b") or {}).get("safety_fail"))})
    return complete, miss


def missing_verdict(miss: Dict[str, int]) -> Tuple[bool, str]:
    """(inconclusive, reason)."""
    planned = max(1, miss.get("planned", 0))
    a, b = miss.get("a", 0), miss.get("b", 0)
    for arm, n in (("current", a), ("candidate", b)):
        if n / planned > MAX_MISSING_SHARE:
            return True, f"{n} of {planned} {arm} runs are missing (over 10%)"
    hi, lo = max(a, b), min(a, b)
    if hi >= 2 and hi > MAX_MISSING_RATIO * lo:
        return True, f"one side is missing far more often than the other ({a} vs {b})"
    return False, ""


def per_task(pairs: Iterable[dict]) -> Dict[str, dict]:
    """Per task: passes of each arm over its complete pairs, and which arm it favours."""
    out: Dict[str, dict] = {}
    for p in pairs:
        t = out.setdefault(p["task"], {"a_pass": 0, "b_pass": 0, "n": 0})
        t["a_pass"] += int(p["a"])
        t["b_pass"] += int(p["b"])
        t["n"] += 1
    for t in out.values():
        t["favours"] = "b" if t["b_pass"] > t["a_pass"] else "a" if t["a_pass"] > t["b_pass"] else "tie"
    return out


def tally(tasks: Dict[str, dict], only: Optional[Iterable[str]] = None) -> Dict[str, int]:
    keep = set(only) if only is not None else None
    c = {"b": 0, "a": 0, "tie": 0}
    for tid, t in tasks.items():
        if keep is None or tid in keep:
            c[t["favours"]] += 1
    return c


def cluster_bootstrap_interval(pairs: List[dict], *, seed: int = 0, reps: int = 2000,
                               level: float = 0.90) -> Tuple[float, float]:
    """Task-level bootstrap interval for (B pass rate - A pass rate)."""
    by_task: Dict[str, List[dict]] = {}
    for p in pairs:
        by_task.setdefault(p["task"], []).append(p)
    tasks = sorted(by_task)
    if not tasks:
        return (0.0, 0.0)
    rng = random.Random(seed)
    diffs = []
    for _ in range(reps):
        sample = [by_task[rng.choice(tasks)] for _ in tasks]
        rows = [p for group in sample for p in group]
        diffs.append((sum(p["b"] for p in rows) - sum(p["a"] for p in rows)) / len(rows))
    diffs.sort()
    lo_i = int(math.floor((1 - level) / 2 * reps))
    hi_i = int(math.ceil((1 + level) / 2 * reps)) - 1
    return (round(diffs[lo_i], 4), round(diffs[min(hi_i, reps - 1)], 4))
