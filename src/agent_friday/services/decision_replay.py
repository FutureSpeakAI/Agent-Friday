"""Nightly full replay of the decision heads (DM6, layer 2 of spec section 6.4).

Spec: ftv/program/specs/decision_models_STORM_2026-10-24.md, section 6.4.

Every Laya receipt from the last 24 hours that answered a decision head
(services/decision_heads) is replayed here, on the CPU, through the thresholds
that ship and through the thresholds in force (an owner's or learning's
override), and through any candidate thresholds a trial passes in. No model
runs: the replay uses the label and score each receipt already logged. Per head
it writes:

  - the take rate (how often the head would act) and its drift against the
    last week's nights, flagged at 10 points or more with at least 20
    decisions in the week, shown and never notified;
  - pass, uncertain and fail per decision. Pass agrees with a gold outcome,
    fail disagrees, uncertain has no gold yet or sits in the abstain band;
  - miss versus wrong on the rows with a gold outcome: a miss is a head that
    held back where the gold says it should have spoken, a wrong is a head
    that would have acted on the wrong label;
  - ECE in 10 bins on the gold rows;
  - p50 and p95 latency of the receipts.

Gold outcomes are never the head's own word (DM-E11). They come from
``record_outcome``: an owner correction, a confirmed choice, or a practice-task
answer key. A brain choice that nobody confirmed is not gold.

The report goes to ``<FRIDAY_HOME>/bench/decision_heads/<date>.json``,
append-only (a second run on one date gets its own file), with counts and
receipt ids only, never a word. It runs inside the nightly practice-trials
job (services/trial_job): on AC power only, counted against the shared nightly
testing time, and it stops if the owner starts a turn.
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

_log = logging.getLogger("friday.decision_replay")

WINDOW_S = 24 * 3600
DRIFT_POINTS = 0.10
DRIFT_MIN_WEEK = 20
MAX_IDS = 50
ABSTAIN = frozenset({"other", "unsure", "none"})
_lock = threading.Lock()


# --------------------------------------------------------------------------
#  where things live
# --------------------------------------------------------------------------

def _friday_dir() -> Path:
    from agent_friday.core import FRIDAY_DIR
    return Path(FRIDAY_DIR)


def bench_dir() -> Path:
    return _friday_dir() / "bench" / "decision_heads"


def outcomes_path() -> Path:
    return _friday_dir() / "runtime" / "laya2" / "outcomes.jsonl"


def _receipt_paths() -> List[Path]:
    from agent_friday.services import laya
    p = Path(laya.log_path())
    return [q for q in (p.with_suffix(p.suffix + ".1"), p) if q.exists()]


# --------------------------------------------------------------------------
#  gold outcomes
# --------------------------------------------------------------------------

#: Sources that may write gold. The head itself, and an unconfirmed brain
#: choice, are not among them.
GOLD_SOURCES = frozenset({"owner_correction", "owner_confirmed", "answer_key"})


def record_outcome(receipt_id: str, head: str, gold, source: str) -> bool:
    """Record the true answer for one head's decision in one receipt. Only the
    sources in GOLD_SOURCES count; anything else is refused. Off the record,
    nothing is written. Labels and ids only."""
    if source not in GOLD_SOURCES or not receipt_id or not head:
        return False
    try:
        from agent_friday.services import off_record
        if off_record.active():
            return False
    except Exception:  # noqa: BLE001
        pass
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "receipt_id": str(receipt_id), "head": str(head),
           "gold": gold if isinstance(gold, (bool, int, float)) else str(gold)[:40], "source": source}
    try:
        p = outcomes_path()
        with _lock:
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, separators=(",", ":")) + "\n")
        return True
    except Exception as e:  # noqa: BLE001
        _log.debug("outcome not recorded: %s", e)
        return False


def _gold() -> Dict[tuple, object]:
    """(receipt id, head) -> gold; the latest row wins."""
    out: Dict[tuple, object] = {}
    p = outcomes_path()
    if not p.exists():
        return out
    for line in p.read_text("utf-8", errors="replace").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("source") in GOLD_SOURCES:
            out[(r.get("receipt_id"), r.get("head"))] = r.get("gold")
    return out


# --------------------------------------------------------------------------
#  rows
# --------------------------------------------------------------------------

def _parse_at(s) -> Optional[float]:
    try:
        return time.mktime(time.strptime(str(s), "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError):
        return None


def rows(now: Optional[float] = None, window_s: float = WINDOW_S,
         heads: Optional[Iterable[str]] = None) -> List[dict]:
    """One row per (receipt, head question) in the window: receipt id, head,
    value, p, layer, latency, and the head tag the receipt names. Degraded
    answers are kept (they count as no decision)."""
    from agent_friday.services import decision_heads
    wanted = set(heads) if heads is not None else set(decision_heads.head_ids())
    now = now or time.time()
    out = []
    for path in _receipt_paths():
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"questions"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                t = _parse_at(rec.get("at"))
                if t is None or not (now - window_s <= t <= now):
                    continue
                vheads = ((rec.get("versions") or {}).get("heads") or {})
                for name, q in (rec.get("questions") or {}).items():
                    if name not in wanted:
                        continue
                    out.append({"receipt_id": rec.get("receipt_id"), "head": name, "value": q.get("value"),
                                "p": q.get("p"), "layer": q.get("layer"), "latency_ms": rec.get("latency_ms"),
                                "tag": vheads.get(name)})
    return out


# --------------------------------------------------------------------------
#  the replay (pure)
# --------------------------------------------------------------------------

def _num(x) -> Optional[float]:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    return float(x) if math.isfinite(float(x)) else None


def decide(row: dict, thresholds: dict, labels: Optional[List[str]]) -> dict:
    """What a head would decide on this row under these thresholds: act (take),
    hold back (a label below act), or abstain (degraded, an abstain label, or
    p below the abstain threshold)."""
    p = _num(row.get("p"))
    v = row.get("value")
    abstain_label = next((l for l in (labels or []) if l in ABSTAIN), None)
    if row.get("layer") == "degraded" or v is None or p is None:
        return {"state": "abstain", "label": None, "p": p}
    if (abstain_label is not None and v == abstain_label) or p < float(thresholds.get("abstain", 0.0)):
        return {"state": "abstain", "label": abstain_label, "p": p}
    if p >= float(thresholds.get("act", 1.01)):
        return {"state": "act", "label": v, "p": p}
    return {"state": "hold", "label": v, "p": p}


def ece(probs: List[float], correct: List[bool], bins: int = 10) -> Optional[float]:
    if not probs:
        return None
    total, n = 0.0, len(probs)
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, p in enumerate(probs) if (lo <= p < hi) or (b == bins - 1 and p == 1.0)]
        if idx:
            conf = sum(probs[i] for i in idx) / len(idx)
            acc = sum(1 for i in idx if correct[i]) / len(idx)
            total += len(idx) / n * abs(acc - conf)
    return round(total, 4)


def _pct(xs: List[float], q: float) -> Optional[float]:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = max(0, min(len(xs) - 1, int(math.ceil(q * len(xs))) - 1))
    return round(xs[k], 2)


def replay_head(hrows: List[dict], thresholds: dict, labels: Optional[List[str]],
                gold: Dict[tuple, object]) -> dict:
    n = len(hrows)
    counts = {"act": 0, "hold": 0, "abstain": 0}
    verdict = {"pass": 0, "uncertain": 0, "fail": 0}
    miss = wrong = 0
    probs, correct, fail_ids = [], [], []
    for r in hrows:
        d = decide(r, thresholds, labels)
        counts[d["state"]] += 1
        g = gold.get((r.get("receipt_id"), r.get("head")))
        if g is None or d["state"] == "abstain":
            verdict["uncertain"] += 1
            if g is not None and g not in ABSTAIN and d["state"] == "abstain":
                miss += 1
            continue
        right = d["label"] == g
        verdict["pass" if right else "fail"] += 1
        if not right:
            if d["state"] == "act":
                wrong += 1
            if len(fail_ids) < MAX_IDS:
                fail_ids.append(r.get("receipt_id"))
        elif d["state"] == "hold" and g not in ABSTAIN:
            miss += 1                       # right label, but it held back
        if d["p"] is not None and 0.0 <= d["p"] <= 1.0:
            probs.append(d["p"])
            correct.append(right)
    return {"n": n, "take_rate": round(counts["act"] / n, 4) if n else None,
            "abstain_rate": round(counts["abstain"] / n, 4) if n else None,
            "counts": counts, "verdict": verdict, "miss": miss, "wrong": wrong,
            "ece": ece(probs, correct), "n_gold": len(probs), "fail_ids": fail_ids}


def _week_before(today: str) -> List[dict]:
    """The reports of the 7 nights before ``today`` (oldest first)."""
    out = []
    d = bench_dir()
    if not d.exists():
        return out
    t0 = time.mktime(time.strptime(today, "%Y-%m-%d"))
    for f in sorted(d.glob("*.json")):
        try:
            day = f.name[:10]
            t = time.mktime(time.strptime(day, "%Y-%m-%d"))
        except ValueError:
            continue
        if t0 - 7 * 86400 <= t < t0:
            try:
                out.append(json.loads(f.read_text("utf-8")))
            except ValueError:
                continue
    return out


def report(now: Optional[float] = None, candidates: Optional[Dict[str, dict]] = None,
           should_stop: Optional[Callable[[], bool]] = None) -> dict:
    """Build tonight's report (does not write it). ``candidates``: {head:
    thresholds} a threshold trial wants replayed beside what ships."""
    from agent_friday.services import decision_heads
    now = now or time.time()
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    all_rows = rows(now)
    gold = _gold()
    week = _week_before(today)
    heads_out: Dict[str, dict] = {}
    stopped = False
    for hid in decision_heads.head_ids():
        if should_stop and should_stop():
            stopped = True
            break
        spec = decision_heads.shipped(hid)
        if spec is None:
            continue
        labels = spec.get("labels")
        hrows = [r for r in all_rows if r["head"] == hid]
        eff = decision_heads.effective(hid) or {}
        sets = {"shipped": spec.get("thresholds") or {}}
        if eff.get("source") == "override":
            sets["in_force"] = eff.get("thresholds") or {}
        if candidates and hid in candidates:
            sets["candidate"] = dict(candidates[hid])
        by_set = {name: replay_head(hrows, th, labels, gold) for name, th in sets.items()}
        lat = [_num(r.get("latency_ms")) for r in hrows]
        prev = [w.get("heads", {}).get(hid, {}) for w in week]
        prev_rates = [p.get("by_set", {}).get("shipped", {}).get("take_rate") for p in prev]
        prev_rates = [x for x in prev_rates if x is not None]
        week_n = sum(int(p.get("n") or 0) for p in prev) + len(hrows)
        tr = by_set["shipped"]["take_rate"]
        base = (sum(prev_rates) / len(prev_rates)) if prev_rates else None
        drift = None if (tr is None or base is None) else round(tr - base, 4)
        heads_out[hid] = {
            "n": len(hrows), "tags": sorted({str(r.get("tag")) for r in hrows if r.get("tag")}),
            "by_set": by_set,
            "latency_ms": {"p50": _pct([x for x in lat if x is not None], 0.5),
                           "p95": _pct([x for x in lat if x is not None], 0.95)},
            "drift": {"take_rate_vs_week": drift, "decisions_in_week": week_n,
                      "flag": bool(drift is not None and abs(drift) >= DRIFT_POINTS and week_n >= DRIFT_MIN_WEEK)},
        }
    return {"date": today, "window_s": WINDOW_S, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "rows": len(all_rows), "heads": heads_out, "stopped": stopped,
            "note": "counts and receipt ids only; gold from owner corrections, confirmed choices and answer keys"}


def write(rep: dict) -> Path:
    """Append-only: a date that already has a report gets <date>-2.json, -3..."""
    d = bench_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / ("%s.json" % rep["date"])
    k = 2
    while p.exists():
        p = d / ("%s-%d.json" % (rep["date"], k))
        k += 1
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rep, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(p)
    return p


def run_nightly(should_stop: Optional[Callable[[], bool]] = None, now: Optional[float] = None) -> dict:
    """The nightly entry (services/trial_job). Off the record, nothing runs."""
    try:
        from agent_friday.services import off_record
        if off_record.active():
            return {"skipped": "off the record"}
    except Exception:  # noqa: BLE001
        pass
    t0 = time.time()
    now = now or t0
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    if (bench_dir() / ("%s.json" % today)).exists():
        return {"skipped": "already replayed today", "seconds": 0.0}
    rep = report(now=now, should_stop=should_stop)
    if rep.get("stopped"):
        return {"skipped": "the owner started a turn", "seconds": round(time.time() - t0, 2)}
    path = write(rep)
    return {"written": path.name, "heads": len(rep["heads"]), "rows": rep["rows"],
            "seconds": round(time.time() - t0, 2)}
