"""Nightly full replay of the decision heads (DM6, layer 2 of spec section 6.4).

Spec: ftv/program/specs/decision_models_STORM_2026-10-24.md, section 6.4.

Every Laya receipt since the last report that answered a decision head
(services/decision_heads) is replayed here, on the CPU, from the label and
score each receipt logged. No model runs. The replay uses the thresholds that
ship, the thresholds in force (an override), and any candidate a threshold
trial passes in. Per head it writes:

  - the take rate (how often the head would take its action) and its drift
    against the week's nights. The drift is flagged at 10 points or more with
    at least 20 decisions in the week; it is shown, never notified;
  - pass, uncertain and fail per decision. Pass agrees with a gold outcome and
    fail disagrees. Uncertain has no gold yet, or the head abstained;
  - miss versus wrong, judged on the head's ACTION (see HEADS). A miss: gold
    says the action was due and the head did not take it. A wrong: the head
    took it and gold says it should not have, or took it with the wrong label;
  - ECE in 10 bins, on the probability each head actually logs;
  - p50 and p95 latency, and how many answers were degraded.

What each head's logged p means differs, so each head has an entry in HEADS
(how to decide from p, which label is its action). A head this table does not
know is reported with its counts only.

Gold outcomes are never a head's own word (DM-E11). They come only from
``record_outcome`` with a source in GOLD_SOURCES and a reference (the card,
approval or answer-key id that makes it gold). A test keeps the heads and Laya
from calling it.

Coverage: each report records the time it covered up to; the next night
replays from there (at most MAX_SPAN_S back), so a skipped night is caught up,
and a longer gap is reported as a shortfall. One report per night (Friday's
testing night runs noon to noon, services/trials); a forced rerun gets its own
file and is left out of the drift baseline. Counts and receipt ids only.
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
MAX_SPAN_S = 7 * 24 * 3600
DRIFT_POINTS = 0.10
DRIFT_MIN_WEEK = 20
MAX_IDS = 50
STOP_CHECK_EVERY = 2000
ABSTAIN = frozenset({"other", "unsure", "none"})
_lock = threading.Lock()

#: How each head decides from what its receipt logs.
#:   label_conf  p is the confidence of the logged label (the triage heads).
#:               The action is the label in ``action`` or, with action None,
#:               any non-abstain label. Below ``abstain`` the head abstains.
#:   band        p is a score; at or above act the head says ``high``, below
#:               abstain ``low``, between "unsure". ``action`` is what the
#:               head would do in act mode (add a search, annotate a footnote).
#:   noul        p is P(True); True at or above act, False below abstain.
#:   rules       no p: the logged label is the decision (the loop head).
HEADS: Dict[str, dict] = {
    "triage.mail": {"kind": "label_conf", "action": None},
    "triage.news": {"kind": "label_conf", "action": None},
    "triage.memory": {"kind": "label_conf", "action": None},
    "triage.notify": {"kind": "label_conf", "action": "needs_you"},
    "evidence.sufficient": {"kind": "band", "high": "sufficient", "low": "search_more", "action": "search_more"},
    "evidence.supports": {"kind": "band", "high": "supports", "low": "does_not_address",
                          "action": "does_not_address"},
    "evidence.holds": {"kind": "noul", "action": True},
    "loop.no_progress": {"kind": "rules", "action": "stalled"},
}


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


def _receipt_path() -> Path:
    from agent_friday.services import laya
    return Path(laya.log_path())


def _off_record() -> bool:
    """Fails closed: if off-the-record cannot be checked, treat it as on."""
    try:
        from agent_friday.services import off_record
        return bool(off_record.active())
    except Exception:  # noqa: BLE001
        return True


def night_key(now: Optional[float] = None) -> str:
    try:
        from agent_friday.services import trials
        return trials._night_key(now)
    except Exception:  # noqa: BLE001
        return time.strftime("%Y-%m-%d", time.localtime((now or time.time()) - 12 * 3600))


# --------------------------------------------------------------------------
#  gold outcomes
# --------------------------------------------------------------------------

#: Sources that may write gold. The head itself, and an unconfirmed brain
#: choice, are not among them.
GOLD_SOURCES = frozenset({"owner_correction", "owner_confirmed", "answer_key"})


def _valid_gold(head: str, gold) -> bool:
    spec = HEADS.get(head) or {}
    if spec.get("kind") == "noul":
        return isinstance(gold, bool)
    try:
        from agent_friday.services import decision_heads
        labels = (decision_heads.shipped(head) or {}).get("labels") or []
    except Exception:  # noqa: BLE001
        return False
    return isinstance(gold, str) and gold in labels


def record_outcome(receipt_id: str, head: str, gold, source: str, ref: str) -> bool:
    """Record the true answer for one head's decision in one receipt. Refused
    unless the source is in GOLD_SOURCES, ``ref`` names what makes it gold (a
    card, approval or answer-key id), and ``gold`` is one of the head's own
    labels (a bool for a yes/no head). Off the record, nothing is written."""
    if source not in GOLD_SOURCES or not receipt_id or not head or not str(ref or "").strip():
        return False
    if not _valid_gold(head, gold) or _off_record():
        return False
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "receipt_id": str(receipt_id), "head": str(head),
           "gold": gold, "source": source, "ref": str(ref)[:64]}
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
    """(receipt id, head) -> gold; the latest valid row wins."""
    out: Dict[tuple, object] = {}
    p = outcomes_path()
    if not p.exists():
        return out
    for line in p.read_text("utf-8", errors="replace").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        g = r.get("gold")
        if r.get("source") in GOLD_SOURCES and r.get("ref") and isinstance(g, (str, bool)):
            out[(r.get("receipt_id"), r.get("head"))] = g
    return out


# --------------------------------------------------------------------------
#  rows
# --------------------------------------------------------------------------

def _parse_at(s) -> Optional[float]:
    try:
        return time.mktime(time.strptime(str(s), "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError):
        return None


class Stopped(Exception):
    pass


def rows(since: float, until: float, heads: Optional[Iterable[str]] = None,
         should_stop: Optional[Callable[[], bool]] = None) -> List[dict]:
    """One row per (receipt, head question) with since < at <= until. Lines are
    prefiltered on the receipt dates and head ids before any JSON is parsed.
    A request-path reading logged again for the same text (same head and
    text_sha) keeps only its latest row, so a message shown often does not
    weigh more."""
    from agent_friday.services import decision_heads
    wanted = set(heads) if heads is not None else set(decision_heads.head_ids())
    path = _receipt_path()
    if not path.exists() or not wanted:
        return []
    days, t = set(), since
    while t <= until + 86400:
        days.add('"at": "%s' % time.strftime("%Y-%m-%d", time.localtime(t)))
        t += 86400
    names = ['"%s"' % h for h in wanted]
    by_key: Dict[tuple, dict] = {}
    plain: List[dict] = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if should_stop and i % STOP_CHECK_EVERY == 0 and should_stop():
                raise Stopped()
            if not any(d in line for d in days) or not any(n in line for n in names):
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            at = _parse_at(rec.get("at"))
            if at is None or not (since < at <= until):
                continue
            vheads = ((rec.get("versions") or {}).get("heads") or {})
            for name, q in (rec.get("questions") or {}).items():
                if name not in wanted:
                    continue
                row = {"receipt_id": rec.get("receipt_id"), "head": name, "value": q.get("value"),
                       "p": q.get("p"), "layer": q.get("layer"), "latency_ms": rec.get("latency_ms"),
                       "tag": vheads.get(name)}
                sha = rec.get("text_sha")
                if sha:
                    by_key[(name, sha)] = row
                else:
                    plain.append(row)
    return plain + list(by_key.values())


# --------------------------------------------------------------------------
#  the replay (pure)
# --------------------------------------------------------------------------

def _num(x) -> Optional[float]:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    return float(x) if math.isfinite(float(x)) else None


def decide(row: dict, head: str, thresholds: dict, labels: Optional[List[str]]) -> dict:
    """{state: act | hold | abstain | degraded, label, conf}. ``conf`` is the
    probability the logged p stands for, for ECE: of the label (label_conf), of
    ``high`` (band), of True (noul); None for rules."""
    spec = HEADS.get(head) or {"kind": "label_conf", "action": None}
    kind = spec["kind"]
    p, v = _num(row.get("p")), row.get("value")
    act, abstain = float(thresholds.get("act", 1.01)), float(thresholds.get("abstain", 0.0))
    if row.get("layer") == "degraded":
        return {"state": "degraded", "label": None, "conf": None}
    if kind == "rules":
        if v is None:
            return {"state": "degraded", "label": None, "conf": None}
        if v in ABSTAIN:
            return {"state": "abstain", "label": v, "conf": None}
        return {"state": "act" if v == spec["action"] else "hold", "label": v, "conf": None}
    if p is None:
        return {"state": "degraded", "label": None, "conf": None}
    if kind == "band":
        label = spec["high"] if p >= act else spec["low"] if p < abstain else "unsure"
        if label == "unsure":
            return {"state": "abstain", "label": label, "conf": p}
        return {"state": "act" if label == spec["action"] else "hold", "label": label, "conf": p}
    if kind == "noul":
        label = True if p >= act else False if p < abstain else None
        if label is None:
            return {"state": "abstain", "label": None, "conf": p}
        return {"state": "act" if label is spec["action"] else "hold", "label": label, "conf": p}
    # label_conf
    abstain_label = next((l for l in (labels or []) if l in ABSTAIN), None)
    if v is None:
        return {"state": "degraded", "label": None, "conf": None}
    if (abstain_label is not None and v == abstain_label) or p < abstain:
        return {"state": "abstain", "label": abstain_label, "conf": None}
    takes = (v == spec["action"]) if spec.get("action") else (v not in ABSTAIN)
    return {"state": "act" if takes and p >= act else "hold", "label": v, "conf": p}


def _is_action(head: str, label) -> bool:
    spec = HEADS.get(head) or {"action": None}
    if spec.get("action") is None:
        return label is not None and label not in ABSTAIN
    return label == spec["action"]


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


def replay_head(hrows: List[dict], head: str, thresholds: dict, labels: Optional[List[str]],
                gold: Dict[tuple, object]) -> dict:
    spec = HEADS.get(head) or {"kind": "label_conf", "action": None}
    n = len(hrows)
    counts = {"act": 0, "hold": 0, "abstain": 0, "degraded": 0}
    verdict = {"pass": 0, "uncertain": 0, "fail": 0}
    miss = wrong = 0
    probs, correct, fail_ids = [], [], []
    for r in hrows:
        d = decide(r, head, thresholds, labels)
        counts[d["state"]] += 1
        g = gold.get((r.get("receipt_id"), r.get("head")))
        if g is not None and d["conf"] is not None and 0.0 <= d["conf"] <= 1.0:
            # ECE on the probability the head logs, abstaining rows included.
            if spec["kind"] == "band":
                probs.append(d["conf"]); correct.append(g == spec["high"])
            elif spec["kind"] == "noul":
                probs.append(d["conf"]); correct.append(g is True)
            else:
                probs.append(d["conf"]); correct.append(d["label"] == g)
        if g is None or d["state"] in ("abstain", "degraded"):
            verdict["uncertain"] += 1
            if g is not None and _is_action(head, g) and d["state"] != "degraded":
                miss += 1                               # the action was due; the head held back
            continue
        right = d["label"] == g
        verdict["pass" if right else "fail"] += 1
        if d["state"] == "act" and not right:
            wrong += 1
        elif d["state"] != "act" and _is_action(head, g):
            miss += 1
        if not right and len(fail_ids) < MAX_IDS:
            fail_ids.append(r.get("receipt_id"))
    decided = n - counts["degraded"]
    return {"n": n, "take_rate": round(counts["act"] / decided, 4) if decided else None,
            "abstain_rate": round(counts["abstain"] / decided, 4) if decided else None,
            "counts": counts, "verdict": verdict, "miss": miss, "wrong": wrong,
            "ece": ece(probs, correct), "n_gold_scored": len(probs), "fail_ids": fail_ids}


def _candidate_refusal(head: str, shipped_th: dict, cand: dict) -> Optional[str]:
    """A triage receipt already holds the abstain label where p was under the
    abstain threshold in force then; the label it would have given is not
    logged, so a candidate that LOOSENS abstain cannot be replayed honestly."""
    if (HEADS.get(head) or {}).get("kind") == "rules":
        return "the loop head decides by rules; there are no thresholds to replay"
    if (HEADS.get(head) or {}).get("kind") == "label_conf" and \
            float(cand.get("abstain", 0.0)) < float(shipped_th.get("abstain", 0.0)):
        return "a lower abstain threshold cannot be replayed from these receipts"
    return None


# --------------------------------------------------------------------------
#  reports
# --------------------------------------------------------------------------

def _nightly_reports() -> List[dict]:
    """One report per night (forced reruns, named <night>-<n>.json, left out)."""
    d = bench_dir()
    out = []
    if not d.exists():
        return out
    for f in sorted(d.glob("*.json")):
        if len(f.stem) != 10:
            continue
        try:
            out.append(json.loads(f.read_text("utf-8")))
        except ValueError:
            continue
    return out


def report(now: Optional[float] = None, candidates: Optional[Dict[str, dict]] = None,
           should_stop: Optional[Callable[[], bool]] = None) -> dict:
    """Build tonight's report (does not write it). Raises Stopped if
    ``should_stop`` turns true (the owner started a turn)."""
    from agent_friday.services import decision_heads
    now = now or time.time()
    night = night_key(now)
    earlier = [r for r in _nightly_reports() if r.get("night", "") < night]
    last_until = max((float(r.get("until") or 0) for r in earlier), default=0.0)
    since = max(last_until, now - MAX_SPAN_S) if last_until else now - WINDOW_S
    # Receipts older than MAX_SPAN_S that no report covered are not replayed: said, not hidden.
    shortfall = round(max(0.0, (now - MAX_SPAN_S) - last_until), 0) if last_until else 0
    all_rows = rows(since, now, should_stop=should_stop)
    gold = _gold()
    week = [r for r in earlier if r.get("night", "") >= night_key(now - 7 * 86400)]
    heads_out: Dict[str, dict] = {}
    for hid in decision_heads.head_ids():
        if should_stop and should_stop():
            raise Stopped()
        spec = decision_heads.shipped(hid)
        if spec is None:
            continue
        labels = spec.get("labels")
        hrows = [r for r in all_rows if r["head"] == hid]
        eff = decision_heads.effective(hid) or {}
        shipped_th = spec.get("thresholds") or {}
        sets = {"shipped": shipped_th}
        if eff.get("source") == "override":
            sets["in_force"] = eff.get("thresholds") or {}
        by_set = {name: replay_head(hrows, hid, th, labels, gold) for name, th in sets.items()}
        if candidates and hid in candidates:
            why = _candidate_refusal(hid, shipped_th, candidates[hid])
            by_set["candidate"] = ({"refused": why} if why else
                                   replay_head(hrows, hid, dict(candidates[hid]), labels, gold))
        lat = [_num(r.get("latency_ms")) for r in hrows]
        prev = [w.get("heads", {}).get(hid, {}) for w in week]
        prev_rates = [p.get("by_set", {}).get("shipped", {}).get("take_rate") for p in prev]
        prev_rates = [x for x in prev_rates if x is not None]
        week_n = sum(int(p.get("n") or 0) for p in prev) + len(hrows)
        tr = by_set["shipped"]["take_rate"]
        base = (sum(prev_rates) / len(prev_rates)) if prev_rates else None
        drift = None if (tr is None or base is None) else round(tr - base, 4)
        heads_out[hid] = {
            "n": len(hrows), "known": hid in HEADS,
            "tags": sorted({str(r.get("tag")) for r in hrows if r.get("tag")}),
            "by_set": by_set,
            "latency_ms": {"p50": _pct([x for x in lat if x is not None], 0.5),
                           "p95": _pct([x for x in lat if x is not None], 0.95)},
            "drift": {"take_rate_vs_week": drift, "decisions_in_week": week_n,
                      "flag": bool(drift is not None and abs(drift) >= DRIFT_POINTS and week_n >= DRIFT_MIN_WEEK)},
        }
    return {"night": night, "since": round(since, 0), "until": round(now, 0),
            "shortfall_s": shortfall, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "rows": len(all_rows), "heads": heads_out,
            "note": "counts and receipt ids only; gold from owner corrections, confirmed choices and answer keys"}


def write(rep: dict) -> Path:
    """Append-only: a night that already has a report gets <night>-2.json, -3..."""
    d = bench_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / ("%s.json" % rep["night"])
    k = 2
    while p.exists():
        p = d / ("%s-%d.json" % (rep["night"], k))
        k += 1
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(rep, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(p)
    return p


def run_nightly(should_stop: Optional[Callable[[], bool]] = None, now: Optional[float] = None) -> dict:
    """The nightly entry (services/trial_job). Nothing runs off the record, if
    tonight already has a report, or while the owner is busy."""
    if _off_record():
        return {"skipped": "off the record"}
    t0 = time.time()
    now = now or t0
    if (bench_dir() / ("%s.json" % night_key(now))).exists():
        return {"skipped": "already replayed tonight", "seconds": 0.0}
    if should_stop and should_stop():
        return {"skipped": "the owner is busy", "seconds": 0.0}
    try:
        rep = report(now=now, should_stop=should_stop)
    except Stopped:
        return {"skipped": "the owner started a turn", "seconds": round(time.time() - t0, 2)}
    path = write(rep)
    return {"written": path.name, "heads": len(rep["heads"]), "rows": rep["rows"],
            "shortfall_s": rep["shortfall_s"], "seconds": round(time.time() - t0, 2)}
