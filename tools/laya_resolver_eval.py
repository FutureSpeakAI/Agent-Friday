"""Score the item resolver on a labelled set of requests.

    python tools/laya_resolver_eval.py SET.jsonl [SET2.jsonl ...] [--out results.json]

Each line: {"text": ..., "expect": "command" | "brain",
            "kind": optional desktop_targets kind or "news" or "workspace",
            "ids": optional list of acceptable item ids (workspace id for a
                   workspace), "set": optional name, "origin": "real" |
                   "templated"}

Runs services/laya_resolver.resolve in-process with Laya loaded on the engine
Friday uses, against the real indexes of the home it runs in. It decides
only: no command is run. Reports, per set:

  routing        brain vs reflex decided correctly
  top1           expect=command rows answered with a command for an
                 acceptable id (or the right workspace)
  ask_back       expect=command rows answered with an ask-back
  right_in_ask   ask-backs whose choices include an acceptable id
  wrong_command  a command for the wrong item, or for a brain request:
                 the error that must stay near zero
  latency        p50 / p95 of resolve(), ms

Labelled sets hold the owner's phrasing and item ids and stay out of the
repository (~/.friday/bench/laya-reflexes/).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")


def _pct(xs, f):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(len(xs) * f))], 1) if xs else None


def _wait_idle(lb, limit_s=60.0):
    """Start each request with the scorer free. A request that timed out
    leaves its scoring running on a worker thread; firing the next one at
    once measures that backlog ("laya busy"), not the resolver. Requests in
    real use arrive seconds apart."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < limit_s:
        held = [lb._reserve_scoring(pilot=False) for _ in range(lb._MAX_SCORING)]
        free = all(held)
        for r in held:
            if r:
                r()
        if free:
            return
        time.sleep(0.05)


def score(rows, results):
    n = len(rows)
    cmd_rows = [(r, x) for r, x in zip(rows, results) if r["expect"] == "command"]
    brain_rows = [(r, x) for r, x in zip(rows, results) if r["expect"] == "brain"]

    def ok_id(r, x):
        ids = set(r.get("ids") or [])
        c = x.get("command") or {}
        inp = c.get("input") or {}
        got = inp.get("id") or inp.get("workspace") or (x.get("chosen") or {}).get("id")
        if not ids:
            return x.get("kind") == r.get("kind")
        return got in ids

    top1 = sum(1 for r, x in cmd_rows if x["status"] == "command" and ok_id(r, x))
    asks = [(r, x) for r, x in cmd_rows if x["status"] == "ask"]
    right_in_ask = sum(1 for r, x in asks
                       if set(r.get("ids") or []) & {c["id"] for c in x.get("choices") or []})
    wrong = sum(1 for r, x in cmd_rows if x["status"] == "command" and not ok_id(r, x))
    wrong += sum(1 for r, x in brain_rows if x["status"] == "command")
    routed = sum(1 for r, x in cmd_rows if x["status"] in ("command", "ask", "not_found")) \
        + sum(1 for r, x in brain_rows if x["status"] == "brain")
    lat = [x["timings_ms"].get("total", 0.0) for x in results]
    return {"n": n, "n_command": len(cmd_rows), "n_brain": len(brain_rows),
            "routing": round(routed / n, 3) if n else None,
            "top1": round(top1 / len(cmd_rows), 3) if cmd_rows else None,
            "top1_count": top1,
            "ask_back": round(len(asks) / len(cmd_rows), 3) if cmd_rows else None,
            "right_in_ask": right_in_ask, "asks": len(asks),
            "wrong_command": wrong,
            "not_found": sum(1 for _r, x in cmd_rows if x["status"] == "not_found"),
            "p50_ms": _pct(lat, .5), "p95_ms": _pct(lat, .95)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("sets", nargs="+")
    p.add_argument("--out")
    p.add_argument("--no-mail", action="store_true",
                   help="skip rows whose kind is email (Gmail is a network search)")
    a = p.parse_args()
    from agent_friday.services import laya_backend, laya_resolver
    laya_backend.register()
    t0 = time.time()
    laya_backend._load_now()
    print(json.dumps({"engine": getattr(laya_backend._agent, "_friday_engine", None),
                      "load_s": round(time.time() - t0, 1)}), flush=True)
    report = {}
    for path in a.sets:
        rows = [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]
        if a.no_mail:
            rows = [r for r in rows if r.get("kind") != "email"]
        results = []
        for r in rows:
            _wait_idle(laya_backend)
            res = laya_resolver.resolve(r["text"]).to_dict()
            results.append(res)
        name = Path(path).stem
        report[name] = {"score": score(rows, results),
                        "by_origin": {o: score([r for r in rows if r.get("origin") == o],
                                               [x for r, x in zip(rows, results) if r.get("origin") == o])
                                      for o in sorted({r.get("origin") for r in rows if r.get("origin")})},
                        "rows": [{"expect": r["expect"], "kind": r.get("kind"),
                                  "status": x["status"], "got_kind": x.get("kind"),
                                  "reason": x.get("reason"), "ms": x["timings_ms"].get("total"),
                                  "ok": (x["status"] == "command" and (x.get("command") or {}).get("input", {}).get("id", (x.get("command") or {}).get("input", {}).get("workspace")) in set(r.get("ids") or []))}
                                 for r, x in zip(rows, results)]}
        print(name, json.dumps(report[name]["score"]), flush=True)
    if a.out:
        Path(a.out).write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
