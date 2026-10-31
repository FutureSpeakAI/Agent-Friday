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
``record_gold`` with a source in GOLD_SOURCES and a reference (the card,
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
import queue
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
    # mail acts only by moving a message INTO an actionable lane (message_triage)
    "triage.mail": {"kind": "label_conf", "action": "actionable_lanes"},
    # news is shadow only and has no actuator: counts, never miss or wrong
    "triage.news": {"kind": "label_conf", "action": "never"},
    "triage.memory": {"kind": "label_conf", "action": None},
    "triage.notify": {"kind": "label_conf", "action": "needs_you"},
    "evidence.sufficient": {"kind": "band", "high": "sufficient", "low": "search_more", "action": "search_more"},
    "evidence.supports": {"kind": "band", "high": "supports", "low": "does_not_address",
                          "action": "does_not_address", "action_gold": ("does_not_address", "contradicts")},
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
    return isinstance(gold, str) and gold in labels and gold not in ABSTAIN


def record_gold(receipt_id: str, head: str, gold, source: str, ref: str) -> bool:
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
    """(receipt id, head) -> gold; the latest valid row wins. A retraction row
    (``retract_gold``: the owner undid the correction) removes the gold that
    reference gave before it."""
    out: Dict[tuple, object] = {}
    by_ref: Dict[str, List[tuple]] = {}
    p = outcomes_path()
    if not p.exists():
        return out
    for line in p.read_text("utf-8", errors="replace").splitlines():
        try:
            r = json.loads(line)
            if isinstance(r.get("retract"), str):
                for k in by_ref.pop(r["retract"], []):
                    out.pop(k, None)
                continue
            g = r.get("gold")
            if (r.get("source") in GOLD_SOURCES and r.get("ref") and isinstance(g, (str, bool))
                    and isinstance(r.get("receipt_id"), str) and _valid_gold(str(r.get("head")), g)):
                k = (r["receipt_id"], r.get("head"))
                out[k] = g
                by_ref.setdefault(str(r["ref"]), []).append(k)
        except (ValueError, AttributeError, TypeError):
            continue
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
         should_stop: Optional[Callable[[], bool]] = None,
         gold_ids: Optional[set] = None) -> List[dict]:
    """One row per (receipt, head question) with since < at <= until. Lines are
    prefiltered on the receipt dates and on a head question before any JSON is
    parsed; a line that is not a well-formed receipt is skipped. A request-path
    reading logged again for the same text (same head and text_sha) keeps one
    row, so a message shown often does not weigh more: the latest, unless an
    earlier one carries gold (``gold_ids``), which is kept instead.

    evidence.holds is never asked on its own: its rows are derived from the
    evidence.sufficient receipts, whose p is the best passage's P(holds)."""
    import datetime as _dt
    from agent_friday.services import decision_heads
    wanted = set(heads) if heads is not None else set(decision_heads.head_ids())
    derive_holds = "evidence.holds" in wanted
    asked = set(wanted) | ({"evidence.sufficient"} if derive_holds else set())
    path = _receipt_path()
    if not path.exists() or not wanted:
        return []
    days, d = set(), _dt.date.fromtimestamp(since)
    while d <= _dt.date.fromtimestamp(until):
        days.add('"at": "%s' % d.isoformat())
        d += _dt.timedelta(days=1)
    # A question key is followed by its dict; a version tag by a string.
    names = ['"%s": {' % h for h in asked]
    gold_ids = gold_ids or set()
    by_key: Dict[tuple, dict] = {}
    plain: List[dict] = []

    def keep(key, row):
        old = by_key.get(key)
        if old is not None and old["receipt_id"] in gold_ids and row["receipt_id"] not in gold_ids:
            return
        by_key[key] = row

    with path.open("r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if should_stop and i % STOP_CHECK_EVERY == 0 and should_stop():
                raise Stopped()
            if not any(x in line for x in days) or not any(n in line for n in names):
                continue
            try:
                rec = json.loads(line)
                if not isinstance(rec, dict) or not isinstance(rec.get("questions"), dict):
                    continue
                at = _parse_at(rec.get("at"))
                if at is None or not (since < at <= until):
                    continue
                versions = rec.get("versions") if isinstance(rec.get("versions"), dict) else {}
                vheads = versions.get("heads") if isinstance(versions.get("heads"), dict) else {}
                rid, sha = rec.get("receipt_id"), rec.get("text_sha")
                for name, q in rec["questions"].items():
                    if name not in asked or not isinstance(q, dict):
                        continue
                    out = []
                    if name in wanted:
                        out.append({"receipt_id": rid, "head": name, "value": q.get("value"), "p": q.get("p"),
                                    "layer": q.get("layer"), "latency_ms": rec.get("latency_ms"),
                                    "tag": vheads.get(name)})
                    if derive_holds and name == "evidence.sufficient":
                        out.append({"receipt_id": rid, "head": "evidence.holds", "value": None, "p": q.get("p"),
                                    "layer": q.get("layer"), "latency_ms": rec.get("latency_ms"),
                                    "tag": vheads.get("evidence.holds"), "derived": True})
                    for row in out:
                        if sha:
                            keep((row["head"], sha), row)
                        else:
                            plain.append(row)
            except (ValueError, AttributeError, TypeError):
                continue
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
    return {"state": "act" if _takes(head, v) and p >= act else "hold", "label": v, "conf": p}


_ACTIONABLE_DEFAULT = frozenset({"career", "finance", "futurespeak", "family"})


def _actionable_lanes() -> frozenset:
    try:
        from agent_friday.services import message_triage
        got = frozenset(message_triage._actionable_lane_ids())
        return got or _ACTIONABLE_DEFAULT
    except Exception:  # noqa: BLE001
        return _ACTIONABLE_DEFAULT


def _takes(head: str, label) -> bool:
    """Is deciding `label` this head taking its action?"""
    spec = HEADS.get(head) or {"action": None}
    a = spec.get("action")
    if label is None or label in ABSTAIN or a == "never":
        return False
    if a is None:
        return True
    if a == "actionable_lanes":
        return label in _actionable_lanes()
    return label == a


def _is_action(head: str, gold) -> bool:
    """Does gold say this head's action was due?"""
    spec = HEADS.get(head) or {}
    if spec.get("action_gold"):
        return gold in spec["action_gold"]
    return _takes(head, gold)


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
        right = d["label"] == g or (d["state"] == "act" and spec.get("action_gold") and g in spec["action_gold"])
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


def _candidate_refusal(head: str, logged_abstain: float, cand: dict) -> Optional[str]:
    """A triage receipt already holds the abstain label where p was under the
    abstain threshold in force then (`logged_abstain`: the higher of what
    ships and what was in force); the label it would have given is not logged,
    so a candidate that LOOSENS abstain below that cannot be replayed honestly."""
    if (HEADS.get(head) or {}).get("kind") == "rules":
        return "the loop head decides by rules; there are no thresholds to replay"
    if (HEADS.get(head) or {}).get("kind") == "label_conf" and \
            float(cand.get("abstain", logged_abstain)) < float(logged_abstain):
        return "a lower abstain threshold cannot be replayed from these receipts"
    return None


# --------------------------------------------------------------------------
#  reports
# --------------------------------------------------------------------------

def _nightly_reports() -> List[dict]:
    """One report per night (forced reruns, named <night>-<n>.json, left out).
    A file that cannot be read, or is not a report, is skipped."""
    d = bench_dir()
    out = []
    if not d.exists():
        return out
    for f in sorted(d.glob("*.json")):
        if len(f.stem) != 10:
            continue
        try:
            r = json.loads(f.read_text("utf-8"))
            if isinstance(r, dict) and isinstance(r.get("heads", {}), dict):
                r["until"] = float(r.get("until") or 0.0)
                out.append(r)
        except (ValueError, TypeError, OSError):
            continue
    return out


def report(now: Optional[float] = None, candidates: Optional[Dict[str, dict]] = None,
           should_stop: Optional[Callable[[], bool]] = None) -> dict:
    """Build tonight's report (does not write it). Raises Stopped if
    ``should_stop`` turns true (the owner started a turn)."""
    from agent_friday.services import decision_heads
    now = now or time.time()
    night = night_key(now)
    # Receipts are written by a queue a little after they are stamped: read up
    # to five minutes ago, after asking the writer to finish, so a late write is
    # read tomorrow instead of falling between two windows.
    until = math.floor(now) - 300
    try:
        from agent_friday.services import laya
        if hasattr(laya, "flush"):
            laya.flush()
    except Exception:  # noqa: BLE001
        pass
    earlier = [r for r in _nightly_reports() if r.get("night", "") < night]
    last_until = max((float(r.get("until") or 0) for r in earlier), default=0.0)
    since = max(last_until, until - MAX_SPAN_S) if last_until else until - WINDOW_S
    # Receipts older than MAX_SPAN_S that no report covered are not replayed: said, not hidden.
    shortfall = round(max(0.0, (until - MAX_SPAN_S) - last_until), 0) if last_until else 0
    gold = _gold()
    all_rows = rows(since, until, should_stop=should_stop, gold_ids={k[0] for k in gold})
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
        logged_abstain = max(float(shipped_th.get("abstain", 0.0)),
                             float((sets.get("in_force") or {}).get("abstain", 0.0)))
        if (HEADS.get(hid) or {}).get("kind") == "label_conf" and \
                logged_abstain > float(shipped_th.get("abstain", 0.0)):
            # Rows under the in-force abstain were logged as abstaining: the shipped
            # replay cannot recover them, so its take rate is a lower bound.
            by_set["shipped"]["lower_bound"] = True
        if candidates and hid in candidates:
            why = _candidate_refusal(hid, logged_abstain, candidates[hid])
            by_set["candidate"] = ({"refused": why} if why else
                                   replay_head(hrows, hid, {**shipped_th, **dict(candidates[hid])}, labels, gold))
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
    return {"night": night, "since": round(since, 0), "until": until,
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


# --------------------------------------------------------------------------
#  Settings > Laya: the report card's decision-head rows
# --------------------------------------------------------------------------

#: Plain names for the report card. A head this table does not name shows its id.
HEAD_NAMES = {
    "triage.mail": "Mail lanes", "triage.news": "News value", "triage.notify": "Notification flag",
    "triage.memory": "Memory proposals", "evidence.sufficient": "Is the found memory enough?",
    "evidence.holds": "Does a passage hold the answer?", "evidence.supports": "Does a source support its sentence?",
    "loop.no_progress": "Is a task stuck?",
}


def _dict(x) -> dict:
    return x if isinstance(x, dict) else {}


def _newest_report() -> Optional[dict]:
    """The newest one-per-night report that reads as a report (reruns, named
    <night>-<n>.json, left out); an unreadable newest falls back to the one
    before. Reads one file in the usual case."""
    d = bench_dir()
    if not d.exists():
        return None
    for f in sorted((p for p in d.glob("*.json") if len(p.stem) == 10), reverse=True):
        try:
            r = json.loads(f.read_text("utf-8"))
        except (ValueError, OSError):
            continue
        if isinstance(r, dict) and isinstance(r.get("heads", {}), dict) and isinstance(r.get("night"), str):
            return r
    return None


def report_card() -> dict:
    """One row per decision head for Settings > Laya: its mode and version, and
    what the newest nightly report found under the thresholds that ship. Read
    only: it reads the head files and one report, runs nothing, and holds
    counts only. ``night`` is None before the first report."""
    from agent_friday.services import decision_heads
    latest = _newest_report()
    heads = _dict((latest or {}).get("heads"))
    rows = []
    for st in decision_heads.status():
        hid = st.get("id")
        h = _dict(heads.get(hid))
        s = _dict(_dict(h.get("by_set")).get("shipped"))
        v = _dict(s.get("verdict"))
        drift = _dict(h.get("drift"))
        tr = s.get("take_rate")
        rows.append({
            "id": hid, "name": HEAD_NAMES.get(hid, hid), "mode": st.get("mode"), "source": st.get("source"),
            "tag": st.get("tag"), "usable": bool(st.get("usable")), "problem": st.get("problem") or "",
            "decisions": h.get("n") if h else None,
            # a percent, as the tab's pct() expects; "at least" when an override hid some rows
            "take_rate_pct": round(float(tr) * 100.0, 1) if isinstance(tr, (int, float)) else None,
            "lower_bound": bool(s.get("lower_bound")),
            "pass": v.get("pass"), "fail": v.get("fail"), "uncertain": v.get("uncertain"),
            "miss": s.get("miss"), "wrong": s.get("wrong"),
            "drift": drift.get("take_rate_vs_week"), "drift_flag": bool(drift.get("flag")),
        })
    return {"night": (latest or {}).get("night"), "shortfall_s": (latest or {}).get("shortfall_s") or 0,
            "rows": rows,
            "note": ("The numbers are every decision the heads made in the period the last nightly report "
                     "covered, replayed on this PC from the labels and scores they logged, counts only. Right "
                     "and wrong need an answer you gave (a correction or a confirmation); without one a "
                     "decision is not known yet.")}


# --------------------------------------------------------------------------
#  owner corrections become gold
# --------------------------------------------------------------------------

#: How far back in the receipt log a correction looks for the decision it corrects.
TAIL_BYTES = 1024 * 1024
_gold_q: "queue.Queue" = queue.Queue(maxsize=64)
_gold_worker: Optional[threading.Thread] = None
_gold_wlock = threading.Lock()


def _ref(ref: str) -> str:
    """What outcomes.jsonl keeps of a reference: its kind, and a hash of the rest."""
    import hashlib
    ref = str(ref or "")
    return "%s:%s" % (ref.split(":")[0][:24] or "owner",
                      hashlib.sha256(ref.encode("utf-8", "replace")).hexdigest()[:12])


def _matches(rec, head: str, sha: str, cutoff: str) -> bool:
    if not isinstance(rec, dict):
        return False
    q = (rec.get("questions") or {}).get(head) if isinstance(rec.get("questions"), dict) else None
    return (rec.get("text_sha") == sha and isinstance(q, dict) and q.get("layer") != "degraded"
            and str(rec.get("at") or "") <= cutoff)


def _latest_receipt(head: str, sha: str, cutoff: str, recent: List[dict]) -> Optional[str]:
    """The newest receipt, stamped at or before ``cutoff``, in which ``head``
    answered (not degraded) for the text whose laya digest is ``sha``: the
    snapshot of Laya's recent receipts taken when the owner acted first, then
    the tail of the receipt log. None when none is found."""
    for rec in reversed(recent):
        if _matches(rec, head, sha, cutoff):
            return rec.get("receipt_id")
    path = _receipt_path()
    if not path.exists():
        return None
    with path.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - TAIL_BYTES))
        tail = f.read().decode("utf-8", errors="replace")
    key = '"%s": {' % head
    for line in reversed(tail.splitlines()):
        if sha not in line or key not in line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if _matches(rec, head, sha, cutoff):
            return rec.get("receipt_id")
    return None


def _gold_work() -> None:
    while True:
        job = _gold_q.get()
        try:
            job()
        except Exception as e:  # noqa: BLE001
            _log.debug("gold not recorded: %s", e)
        finally:
            _gold_q.task_done()


def _gold_submit(job: Callable[[], None]) -> bool:
    global _gold_worker
    with _gold_wlock:
        if _gold_worker is None or not _gold_worker.is_alive():
            _gold_worker = threading.Thread(target=_gold_work, name="decision-gold", daemon=True)
            _gold_worker.start()
    try:
        _gold_q.put_nowait(job)
        return True
    except queue.Full:
        return False


def gold_for_text(head: str, text: str, gold, source: str, ref: str, *, background: bool = True) -> bool:
    """The owner's correction of what ``head`` would decide for ``text`` (a mail
    moved to another lane, say) becomes gold for the head's latest decision on
    that text MADE BEFORE the correction: receipts stamped after this call (the
    head's own re-read of the corrected message, say) are never graded by it.
    Call it before anything that reads the text again. Called only from the
    owner's own paths, on the owner's press (a guard test keeps every head and
    Laya from calling it). Nothing is written off the record, for an invalid
    label, or when no earlier decision of the head on that text is found. The
    search runs on one bounded background worker, so the owner's request never
    waits on it and a bulk move cannot pile up threads."""
    if source not in GOLD_SOURCES or _off_record() or not _valid_gold(head, gold) or not text:
        return False
    from agent_friday.services import laya
    sha = laya._sha(text)
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%S")
    with laya._lock:
        recent = list(laya._ring.values())           # before anything new is asked
    hashed = _ref(ref)

    def work() -> bool:
        rid = _latest_receipt(head, sha, cutoff, recent)
        return bool(rid) and record_gold(rid, head, gold, source, hashed)
    if background:
        return _gold_submit(work)
    return work()


def retract_gold(ref: str) -> bool:
    """The owner undid the correction that gave gold under ``ref``: a
    retraction row, honoured by _gold(), so a misclick never stays a right
    answer. Off the record nothing is written."""
    if not str(ref or "").strip() or _off_record():
        return False
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "retract": _ref(ref)}
    try:
        p = outcomes_path()
        with _lock:
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, separators=(",", ":")) + "\n")
        return True
    except Exception as e:  # noqa: BLE001
        _log.debug("retraction not recorded: %s", e)
        return False
