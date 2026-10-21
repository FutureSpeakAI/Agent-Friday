"""Command line of the grader process.

  python -m agent_friday.task_eval_grader grade --episode EP.json [--pack dev | --answers DIR]
      prints one grade (JSON) for the episode's task_id
  python -m agent_friday.task_eval_grader scan --text-file F [--pack dev | --answers DIR]
      prints {"hit": bool, "task": id-or-null}: whether any answer's canary is in F
      (used to check editor, composer and selector inputs without the caller
      ever opening the answers folder)

Exit 0 whenever it printed a result, including "missing". Exit 2 on bad usage.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_friday.task_eval_grader.checks import grade

ANSWERS_ROOT = Path(__file__).resolve().parent / "answers"


def _answers_dir(opts) -> Path:
    if opts.answers:
        return Path(opts.answers)
    return ANSWERS_ROOT / (opts.pack or "dev")


def _load_answers(folder: Path) -> dict:
    out = {}
    for p in sorted(folder.glob("*.json")):
        a = json.loads(p.read_text(encoding="utf-8"))
        out[a["task_id"]] = a
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="task_eval_grader")
    sub = ap.add_subparsers(dest="cmd")
    g = sub.add_parser("grade")
    g.add_argument("--episode", required=True)
    g.add_argument("--pack", default="dev")
    g.add_argument("--answers", default="")
    s = sub.add_parser("scan")
    s.add_argument("--text-file", required=True)
    s.add_argument("--pack", default="dev")
    s.add_argument("--answers", default="")
    opts = ap.parse_args(argv)
    if opts.cmd not in ("grade", "scan"):
        ap.print_usage(sys.stderr)
        return 2
    try:
        answers = _load_answers(_answers_dir(opts))
    except Exception as e:
        print(json.dumps({"status": "missing", "reason": f"grader could not load answers ({type(e).__name__})"}))
        return 0
    canaries = [a.get("canary", "") for a in answers.values()]
    if opts.cmd == "scan":
        try:
            text = Path(opts.text_file).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(json.dumps({"hit": True, "task": None, "reason": f"unreadable input ({type(e).__name__})"}))
            return 0
        for tid, a in answers.items():
            if a.get("canary") and a["canary"] in text:
                print(json.dumps({"hit": True, "task": tid}))
                return 0
        print(json.dumps({"hit": False, "task": None}))
        return 0
    try:
        episode = json.loads(Path(opts.episode).read_text(encoding="utf-8"))
    except Exception as e:
        print(json.dumps({"status": "missing", "reason": f"episode unreadable ({type(e).__name__})"}))
        return 0
    answer = answers.get(episode.get("task_id"))
    if answer is None:
        print(json.dumps({"status": "missing", "reason": "no answer for this task"}))
        return 0
    try:
        result = grade(episode, answer, canaries)
    except Exception as e:
        result = {"status": "missing", "reason": f"grader error ({type(e).__name__}: {e})"}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
