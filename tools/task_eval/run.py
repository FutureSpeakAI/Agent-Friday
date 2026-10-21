"""Run practice tasks with Friday's current instructions and report what happened.

    python tools/task_eval/run.py --endpoint http://127.0.0.1:8090/v1 --model bonsai2:27b \
        --out RUN_DIR [--tasks mail-01,cal-01] [--repeats 1]

Uses the model that is ALREADY serving at --endpoint; nothing new is loaded.
Each episode runs in its own child process with a scratch home and only the
virtual tools; the grader runs as a separate process. Results stay in RUN_DIR.

It measures real episode times (wall seconds, rounds, tokens) so the R0
experiment can be re-sized before it starts (spec §6). Without --endpoint it
refuses to run: there is no default seat.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", required=True, help="an already-running OpenAI-compatible seat, e.g. http://127.0.0.1:8090/v1")
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pack", default="dev")
    ap.add_argument("--tasks", default="", help="comma-separated task ids (default: the whole pack)")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--max-tokens", type=int, default=2048)
    opts = ap.parse_args(argv)

    from agent_friday.task_eval import pack as P
    from agent_friday.task_eval import runs as R
    from agent_friday.task_eval.runner import run_episode

    tasks = P.load_pack(opts.pack)
    if opts.tasks:
        want = {t.strip() for t in opts.tasks.split(",") if t.strip()}
        tasks = [t for t in tasks if t["id"] in want]
    manifest = R.make_manifest(kind="measure", tasks=tasks, arms={"a": None}, model=opts.model,
                               endpoint=opts.endpoint, repeats=opts.repeats, rule_version="-", seed=0)
    run_dir = R.open_run(Path(opts.out), manifest)
    rows = []
    for t in tasks:
        for r in range(opts.repeats):
            fp = R.fingerprint(t, None, opts.model, r, "a")
            res = R.stored(run_dir, fp)
            if res is None:
                t0 = time.time()
                res = run_episode(t, arm="a", repeat=r, endpoint=opts.endpoint, model=opts.model,
                                  out_dir=run_dir / "episodes" / fp, pack=opts.pack,
                                  max_tokens=opts.max_tokens)
                res["elapsed"] = round(time.time() - t0, 1)
                R.store(run_dir, fp, res)
            g = res["grade"]
            rows.append({"task": t["id"], "repeat": r, "outcome": res.get("outcome"),
                         "status": g.get("status"), "pass": g.get("pass"),
                         "safety_fail": g.get("safety_fail"), "wall_seconds": res.get("wall_seconds"),
                         "rounds": res.get("rounds"), "elapsed": res.get("elapsed")})
            print(json.dumps(rows[-1]), flush=True)
    graded = [r for r in rows if r["status"] == "graded"]
    walls = [r["wall_seconds"] for r in rows if isinstance(r.get("wall_seconds"), (int, float))]
    summary = {"episodes": len(rows), "graded": len(graded), "missing": len(rows) - len(graded),
               "passed": sum(bool(r["pass"]) for r in graded),
               "safety_failures": sum(bool(r["safety_fail"]) for r in graded),
               "median_wall_seconds": statistics.median(walls) if walls else None,
               "total_wall_hours": round(sum(walls) / 3600, 2) if walls else None}
    (run_dir / "summary.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
