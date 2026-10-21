"""Run the R0 replication (harness2 adoption spec §6) against an already-serving seat.

    python tools/task_eval/r0.py --endpoint http://127.0.0.1:8090/v1 --model bonsai2:27b \
        --out RUN_DIR [--repeats 2] [--tasks mail-01,...] [--max-hours 3]

The local model is both agent and editor. Nothing new is loaded; no cloud;
$0. Resumable: run it again with the same arguments to continue (a different
configuration is refused). Stops cleanly at --max-hours (default 3, the
nightly cap, decision HD7). When every cell is done it writes result.json and
report.md (aggregate numbers only) into RUN_DIR.

Run it only in a window the release lead names: it holds the brain seat.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def _chat(endpoint: str, model: str, max_tokens: int):
    import requests
    url = endpoint.rstrip("/") + "/chat/completions"

    def call(system: str, user: str) -> str:
        r = requests.post(url, json={"model": model, "max_tokens": max_tokens, "stream": False,
                                     "messages": [{"role": "system", "content": system},
                                                  {"role": "user", "content": user}]}, timeout=900)
        r.raise_for_status()
        return ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    return call


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--tasks", default="")
    ap.add_argument("--max-hours", type=float, default=3.0)
    ap.add_argument("--max-tokens", type=int, default=2048)
    opts = ap.parse_args(argv)

    from agent_friday.task_eval import pack as P, r0
    from agent_friday.task_eval.runner import run_episode, scan_for_canary

    tasks = P.load_pack("dev")
    if opts.tasks:
        want = {t.strip() for t in opts.tasks.split(",") if t.strip()}
        tasks = [t for t in tasks if t["id"] in want]
    stop_at = time.time() + opts.max_hours * 3600

    def run_one(**kw):
        return run_episode(endpoint=opts.endpoint, model=opts.model, max_tokens=opts.max_tokens, **kw)

    out = r0.run(tasks=tasks, repeats=opts.repeats, run_dir=Path(opts.out), run_one=run_one,
                 call_model=_chat(opts.endpoint, opts.model, opts.max_tokens),
                 scan=lambda text: scan_for_canary(text), model=opts.model, endpoint=opts.endpoint,
                 should_yield=lambda: time.time() > stop_at)
    if out.get("complete"):
        (Path(opts.out) / "report.md").write_text(r0.report(out), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "decision"}, indent=1))
    if out.get("decision"):
        print(json.dumps(out["decision"]["verdict"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
