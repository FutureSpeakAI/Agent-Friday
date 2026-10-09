"""Score services/laya_router against tests/fixtures/laya_router_eval.json.

A case is correct only when the decision, the tool AND every argument check
hold. Prints n/N per category, every miss, and routing latency (p50/p95,
warm). CPU only; loads the Laya 2 encoder that the turn-shape shadow uses.

    python tools/laya_router_eval.py [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def check(case: dict, r) -> list:
    """Reasons the route is wrong; empty when it is right."""
    why = []
    if r.decision != case["decision"]:
        why.append("decision %s, wanted %s" % (r.decision, case["decision"]))
        return why
    if case.get("tool") and r.tool != case["tool"]:
        why.append("tool %s, wanted %s" % (r.tool, case["tool"]))
    for k, want in (case.get("args_equal") or {}).items():
        if r.args.get(k) != want:
            why.append("%s=%r, wanted %r" % (k, r.args.get(k), want))
    for k, words in (case.get("args_contains") or {}).items():
        got = str(r.args.get(k) or "").lower()
        for w in words:
            if w.lower() not in got:
                why.append("%s=%r lacks %r" % (k, r.args.get(k), w))
    return why


def pct(xs, p):
    xs = sorted(xs)
    return round(xs[max(0, min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1)))))], 2)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--set", default="laya_router_eval.json")
    a = ap.parse_args(argv)
    from agent_friday.services import laya_router as lr
    cases = json.loads((ROOT / "tests/fixtures" / a.set).read_text(encoding="utf-8"))["cases"]
    t = time.perf_counter()
    lr._prototypes()
    warm_ms = (time.perf_counter() - t) * 1000
    rows, times = [], []
    for c in cases:
        r = lr.route(c["text"], budget_ms=1000, log=False)
        times.append(r.elapsed_ms)
        rows.append({"case": c, "route": r.as_dict(), "why": check(c, r)})
    by = {}
    for row in rows:
        key = row["case"].get("tool") or row["case"]["decision"]
        n, ok = by.get(key, (0, 0))
        by[key] = (n + 1, ok + (not row["why"]))
    right = sum(not r["why"] for r in rows)
    tool_cases = [r for r in rows if r["case"]["decision"] == "tool"]
    print("router: %d/%d correct (decision, tool and arguments)" % (right, len(rows)))
    print("  tool requests: %d/%d   no-tool: %d/%d   defer: %d/%d" % (
        sum(not r["why"] for r in tool_cases), len(tool_cases),
        *[(sum(not r["why"] for r in rows if r["case"]["decision"] == d),
           sum(1 for r in rows if r["case"]["decision"] == d)) for d in ("no_tool",)][0],
        *[(sum(not r["why"] for r in rows if r["case"]["decision"] == d),
           sum(1 for r in rows if r["case"]["decision"] == d)) for d in ("defer",)][0]))
    for k, (n, ok) in sorted(by.items()):
        print("  %-28s %d/%d" % (k, ok, n))
    print("latency warm: p50 %.2f ms  p95 %.2f ms  max %.2f ms   (first call incl. load %.0f ms)" % (
        pct(times, 50), pct(times, 95), max(times), warm_ms))
    layers = {}
    for r in rows:
        layers[r["route"]["layer"]] = layers.get(r["route"]["layer"], 0) + 1
    print("decided by:", layers)
    for r in rows:
        if r["why"]:
            print("  MISS %-60s -> %s %s %s | %s" % (r["case"]["text"][:60], r["route"]["decision"],
                  r["route"]["tool"] or "", json.dumps(r["route"]["args"]), "; ".join(r["why"])))
    if a.json:
        Path(a.json).write_text(json.dumps({"right": right, "of": len(rows), "rows": rows,
                                            "p50_ms": pct(times, 50), "p95_ms": pct(times, 95)},
                                           indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
