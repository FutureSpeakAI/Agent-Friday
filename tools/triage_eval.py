"""Measure Friday's email triage against a human standard, before swapping it.

WHY THIS, AND WHY TRIAGE
------------------------
Stephen's own note, ~/.friday/wiki/professional/email-triage-preferences.md,
is the clearest statement of the problem in the whole system:

    "Stephen does NOT want email triage limited to literal 'urgent'-flagged
    messages... Apply editorial judgment to surface real signal ... vs. daily
    clutter. Friday misread the request as 'search for urgent-flagged mail'
    TWICE before Stephen clarified... so Friday knows what counts as signal
    vs. noise WITHOUT RE-EXPLAINING IT EACH TIME."

That is a typed judgment over a state, repeated thousands of times, where
being wrong is felt immediately. It is the decision in Friday most likely to
change how the product feels, and the one where "calibrated probability"
means something concrete: a confident wrong answer buries a real message.

WHY A HARNESS BEFORE A MODEL
----------------------------
Nobody knows how good today's triage is. It has never been scored. Swapping
in a classifier without a baseline would produce an unfalsifiable claim of
improvement - and today's classifier is not a naive keyword scan: it scores
every lane, weighs learned sender priors above domain rules, and already
returns a confidence from the winner/runner-up margin. It may well be good.

So this measures the incumbent first, on real mail, against labels. Any
second scorer plugs into the same interface and is scored the same way.

THE LABELS ARE THE HARD PART, and this is honest about it. There is no
existing ground truth: ~/.friday/messages/sender_signals.json is 38 bytes
with zero corrections recorded, and approvals.json holds one record. Friday
has been judging for months and learning from none of it. So labels are
produced by review and stored alongside, never inferred from the classifier
being tested - a label derived from the thing under test measures nothing.

PRIVACY. Everything stays on this machine and under ~/.friday/eval/. Bodies
are never collected; sender, subject and Gmail's own snippet are what the
classifier sees, so they are all the harness stores.

USAGE
    python tools/triage_eval.py collect [--live N]
    python tools/triage_eval.py score [--scorer rules]
    python tools/triage_eval.py labels          # writes a review sheet
    python tools/triage_eval.py report
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from collections import Counter, defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

EVAL_DIR = pathlib.Path.home() / ".friday" / "eval"
SET_FILE = EVAL_DIR / "triage_set.jsonl"
LABEL_FILE = EVAL_DIR / "triage_labels.jsonl"
SHEET_FILE = EVAL_DIR / "triage_review.tsv"


def _scores_file(scorer: str) -> pathlib.Path:
    return EVAL_DIR / f"triage_scores_{scorer}.jsonl"


def _read_jsonl(p: pathlib.Path) -> list:
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def _write_jsonl(p: pathlib.Path, rows: list) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


# ---------------------------------------------------------------------------
#  COLLECT
# ---------------------------------------------------------------------------

def _card_id(m: dict) -> str:
    """Stable id. Gmail ids are not always present in the cache, so fall back
    to the tuple that actually identifies a message to a human."""
    return (str(m.get("id") or m.get("thread_id") or "")
            or f"{m.get('sender','')}|{m.get('subject','')}|{m.get('timestamp','')}")


def collect(live: int = 0) -> int:
    """Gather messages the classifier will be judged on.

    The local cache first because it costs nothing; `--live N` additionally
    pulls from the connected accounts for a larger, fresher sample.
    """
    rows = {r["id"]: r for r in _read_jsonl(SET_FILE)}
    before = len(rows)

    cache = pathlib.Path.home() / ".friday" / "messages" / "cache.json"
    msgs = []
    if cache.exists():
        try:
            msgs.extend(json.loads(cache.read_text(encoding="utf-8")).get("messages") or [])
        except Exception as e:
            print(f"  cache unreadable: {e}")

    if live:
        try:
            from agent_friday.services import google_accounts as ga
            merged = ga.merged_gmail(limit_per_account=int(live))
            msgs.extend(merged.get("messages") or merged.get("emails") or [])
        except Exception as e:
            print(f"  live pull unavailable ({e}); cache only")

    for m in msgs:
        mid = _card_id(m)
        if not mid:
            continue
        rows[mid] = {
            "id": mid,
            "sender": str(m.get("sender") or m.get("from") or "")[:200],
            "subject": str(m.get("subject") or "")[:300],
            "snippet": str(m.get("snippet") or "")[:500],
            "timestamp": m.get("timestamp") or m.get("date"),
            "labels": m.get("labels") or [],
        }
    _write_jsonl(SET_FILE, list(rows.values()))
    print(f"  collected {len(rows)} message(s) (+{len(rows) - before} new) -> {SET_FILE}")
    return len(rows)


# ---------------------------------------------------------------------------
#  SCORERS
# ---------------------------------------------------------------------------

def _scorer_rules(cards: list) -> list:
    """Today's triage: services/message_triage.classify.

    Loaded once outside the loop - rules and learned signals are read from
    disk per call otherwise, which would measure file I/O as classifier cost.
    """
    from agent_friday.services import message_triage as mt
    from agent_friday.services import calendar_engine as ce
    rules = ce._load_message_rules() or {}
    signals = mt._load_signals()
    out = []
    import time as _t
    for c in cards:
        t0 = _t.time()
        try:
            lane, conf, reasons = mt.classify(c, rules=rules, signals=signals)
        except Exception as e:
            lane, conf, reasons = "ERROR", 0.0, [f"{type(e).__name__}: {e}"]
        out.append({"id": c["id"], "lane": lane, "confidence": round(float(conf), 4),
                    "reasons": reasons[:4], "ms": round((_t.time() - t0) * 1000, 2)})
    return out


SCORERS = {"rules": _scorer_rules}


def score(scorer: str = "rules") -> int:
    cards = _read_jsonl(SET_FILE)
    if not cards:
        print("  no message set - run `collect` first")
        return 0
    fn = SCORERS.get(scorer)
    if not fn:
        print(f"  unknown scorer {scorer!r}; have {sorted(SCORERS)}")
        return 0
    rows = fn(cards)
    _write_jsonl(_scores_file(scorer), rows)
    lanes = Counter(r["lane"] for r in rows)
    ms = [r["ms"] for r in rows] or [0]
    print(f"  scored {len(rows)} with {scorer!r} -> {_scores_file(scorer)}")
    print(f"  lanes: {lanes.most_common()}")
    print(f"  latency: mean {sum(ms)/len(ms):.2f} ms, max {max(ms):.2f} ms")
    conf = [r["confidence"] for r in rows]
    print(f"  confidence: mean {sum(conf)/len(conf):.3f}, "
          f"at-zero {sum(1 for c in conf if c == 0)}/{len(conf)}")
    return len(rows)


# ---------------------------------------------------------------------------
#  LABELS
# ---------------------------------------------------------------------------

def labels() -> int:
    """Write a review sheet: one message per line, lane left blank.

    Deliberately NOT pre-filled with the classifier's answer. A reviewer shown
    a proposed label agrees with it far more often than they would have
    chosen it, and a label anchored on the thing under test measures nothing.
    """
    cards = _read_jsonl(SET_FILE)
    if not cards:
        print("  no message set - run `collect` first")
        return 0
    done = {r["id"] for r in _read_jsonl(LABEL_FILE)}
    todo = [c for c in cards if c["id"] not in done]
    try:
        from agent_friday.services import message_triage as mt
        lane_ids = mt.lane_ids()
    except Exception:
        lane_ids = []
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    with open(SHEET_FILE, "w", encoding="utf-8") as fh:
        fh.write("# Fill the LANE column. One per line. Lanes: %s\n"
                 % (", ".join(lane_ids) or "(engine lanes unavailable)"))
        fh.write("# Also mark REPLY as y/n: does this need a reply from you?\n")
        fh.write("# LANE\tREPLY\tSENDER\tSUBJECT\tID\n")
        for c in todo:
            fh.write("\t\t%s\t%s\t%s\n" % (c["sender"][:60], c["subject"][:90], c["id"]))
    print(f"  {len(todo)} unlabelled of {len(cards)} -> {SHEET_FILE}")
    print(f"  fill it in, then: python tools/triage_eval.py ingest")
    return len(todo)


def ingest() -> int:
    """Read the filled sheet back into triage_labels.jsonl."""
    if not SHEET_FILE.exists():
        print("  no review sheet - run `labels` first")
        return 0
    rows = {r["id"]: r for r in _read_jsonl(LABEL_FILE)}
    added = 0
    for line in SHEET_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        lane, reply, _sender, _subject, mid = (parts + [""] * 5)[:5]
        lane, reply, mid = lane.strip(), reply.strip().lower(), mid.strip()
        if not lane or not mid:
            continue
        rows[mid] = {"id": mid, "lane": lane,
                     "needs_reply": reply in ("y", "yes", "1", "true")}
        added += 1
    _write_jsonl(LABEL_FILE, list(rows.values()))
    print(f"  ingested {added} label(s); {len(rows)} total -> {LABEL_FILE}")
    return added


# ---------------------------------------------------------------------------
#  REPORT
# ---------------------------------------------------------------------------

def _ece(pairs, bins: int = 10) -> float:
    """Expected calibration error over (confidence, correct) pairs.

    The number Laya's card leads with, so it is the number any replacement has
    to beat on THIS data rather than on a public benchmark.
    """
    if not pairs:
        return float("nan")
    buckets = defaultdict(list)
    for conf, ok in pairs:
        b = min(int(conf * bins), bins - 1)
        buckets[b].append((conf, ok))
    total = len(pairs)
    err = 0.0
    for b, items in buckets.items():
        acc = sum(1 for _, ok in items if ok) / len(items)
        avg = sum(c for c, _ in items) / len(items)
        err += (len(items) / total) * abs(acc - avg)
    return err


def report() -> None:
    cards = {c["id"]: c for c in _read_jsonl(SET_FILE)}
    labels_by_id = {r["id"]: r for r in _read_jsonl(LABEL_FILE)}
    print(f"  messages: {len(cards)}   labelled: {len(labels_by_id)}")
    if not labels_by_id:
        print("  no labels yet - accuracy cannot be computed, and a report "
              "without it would only be describing the classifier to itself.")
    for scorer in sorted(SCORERS):
        rows = _read_jsonl(_scores_file(scorer))
        if not rows:
            continue
        print(f"\n  == {scorer} ==")
        lanes = Counter(r["lane"] for r in rows)
        print(f"     lane distribution : {lanes.most_common()}")
        graded = [(r, labels_by_id[r["id"]]) for r in rows if r["id"] in labels_by_id]
        if not graded:
            print("     (unlabelled - no accuracy)")
            continue
        ok = [r["lane"] == l["lane"] for r, l in graded]
        acc = sum(ok) / len(ok)
        pairs = [(r["confidence"], r["lane"] == l["lane"]) for r, l in graded]
        print(f"     accuracy          : {acc:.3f}  ({sum(ok)}/{len(ok)})")
        print(f"     ECE               : {_ece(pairs):.3f}")
        wrong_confident = [(r, l) for r, l in graded
                           if r["lane"] != l["lane"] and r["confidence"] >= 0.7]
        print(f"     confidently wrong : {len(wrong_confident)}  "
              f"(the ones that bury a real message)")
        per = defaultdict(lambda: [0, 0])
        for r, l in graded:
            per[l["lane"]][1] += 1
            if r["lane"] == l["lane"]:
                per[l["lane"]][0] += 1
        for lane, (good, tot) in sorted(per.items()):
            print(f"       {lane:16s} {good}/{tot}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["collect", "score", "labels", "ingest", "report"])
    ap.add_argument("--live", type=int, default=0,
                    help="also pull this many messages per connected account")
    ap.add_argument("--scorer", default="rules")
    a = ap.parse_args()
    if a.cmd == "collect":
        collect(live=a.live)
    elif a.cmd == "score":
        score(scorer=a.scorer)
    elif a.cmd == "labels":
        labels()
    elif a.cmd == "ingest":
        ingest()
    else:
        report()


if __name__ == "__main__":
    main()
