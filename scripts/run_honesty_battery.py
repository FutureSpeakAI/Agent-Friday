"""Run the A4 honesty battery against a live model and record evidence.

Usage:
  python scripts/run_honesty_battery.py --local gemma4:latest
  python scripts/run_honesty_battery.py --anthropic claude-sonnet-5

The Anthropic key is resolved from the environment (ANTHROPIC_API_KEY) or,
failing that, bootstrapped the same way the server does from the launch
scripts. The key value is never printed.

A red result is a valid outcome — commit the evidence whatever it shows.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("FRIDAY_TESTING", "0")


def _resolve_anthropic_key():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    try:
        from agent_friday.core import _bootstrap_env_from_launch_scripts
        _bootstrap_env_from_launch_scripts()
        return os.environ.get("ANTHROPIC_API_KEY")
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", help="Ollama model id to test")
    ap.add_argument("--anthropic", help="Anthropic model id to test")
    ap.add_argument("--evidence", action="store_true",
                    help="also write the repo evidence copy")
    args = ap.parse_args()

    from agent_friday.services import honesty_battery as hb

    if args.local:
        result = hb.run_battery(args.local, provider="local")
    elif args.anthropic:
        key = _resolve_anthropic_key()
        if not key:
            print("ANTHROPIC_API_KEY not resolvable — aborting.")
            return 2
        result = hb.run_battery(args.anthropic, provider="anthropic",
                                api_key=key)
    else:
        ap.print_help()
        return 2

    if args.evidence:
        path = hb.save_honesty_evidence(result)
        print(f"evidence written: {path}")

    summary = {k: result[k] for k in
               ("model", "provider", "score", "passed", "by_category")}
    print(json.dumps(summary, indent=2))
    for r in result["results"]:
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"  [{mark}] {r['id']} ({r['category']})")
        if not r["passed"]:
            print(f"         detail: {json.dumps(r['detail'])[:200]}")
            print(f"         said: {r['content_excerpt'][:160]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
