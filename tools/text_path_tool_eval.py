"""The text-mode baseline for the Laya router: a model's OWN tool calling on
the same held-out cases (tests/fixtures/laya_router_holdout3.json), scored by
the same checker as tools/laya_router_eval.py.

The model gets the 90-tool voice contract and a plain instruction, at
temperature 0, and its first round is read:
  a call to a READ tool  -> "tool" with that tool and arguments
  a call to anything else (send, delete, delegate, ...) -> "defer"
  no call                -> "no_tool"
Nothing is executed. Point it at any OpenAI-compatible endpoint, e.g. the
brain seat (bonsai2:27b on :8090) while it is up, or a bench front.

    python tools/text_path_tool_eval.py --port 8090 --model bonsai2:27b
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

SYSTEM = ("You are Agent Friday, a personal assistant on this computer. When the "
          "owner asks for something one of your tools can fetch, call that tool with "
          "the right arguments; when they ask you to change something, use the tool "
          "for it; otherwise just answer. Reply briefly.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--set", default="laya_router_holdout3.json")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    import laya_router_eval as ev
    from agent_friday.services import laya_router as lr
    from agent_friday.services.voice_engine import build_voice_tool_contract
    tools = build_voice_tool_contract()["tools"]
    cases = json.loads((ROOT / "tests/fixtures" / a.set).read_text(encoding="utf-8"))["cases"]
    rows, times = [], []
    for c in cases:
        body = {"model": a.model, "temperature": 0, "max_tokens": 200,
                "messages": [{"role": "system", "content": SYSTEM},
                             {"role": "user", "content": c["text"]}],
                "tools": tools, "chat_template_kwargs": {"enable_thinking": False}}
        t = time.perf_counter()
        msg = requests.post("http://127.0.0.1:%d/v1/chat/completions" % a.port,
                            json=body, timeout=300).json()["choices"][0]["message"]
        times.append((time.perf_counter() - t) * 1000)
        call = (msg.get("tool_calls") or [None])[0]
        if call:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except ValueError:
                args = {}
            r = lr.Route("tool" if name in lr.TOOLS else "defer", tool=name, args=args)
        else:
            r = lr.Route("no_tool")
        rows.append({"case": c, "route": r.as_dict(), "why": ev.check(c, r),
                     "content": (msg.get("content") or "")[:200]})
    right = sum(not r["why"] for r in rows)
    tc = [r for r in rows if r["case"]["decision"] == "tool"]
    unwanted = [r["case"]["text"] for r in rows
                if r["case"]["decision"] != "tool" and r["route"]["decision"] == "tool"]
    print("%s native tool calling: %d/%d correct; tool requests %d/%d; unwanted reads %d; "
          "p50 %.0f ms per request" % (a.model, right, len(rows), sum(not r["why"] for r in tc),
                                       len(tc), len(unwanted), ev.pct(times, 50)))
    for r in rows:
        if r["why"]:
            print("  MISS %-55s -> %s %s | %s" % (r["case"]["text"][:55], r["route"]["decision"],
                                                r["route"]["tool"] or "", "; ".join(r["why"])))
    if a.json:
        Path(a.json).write_text(json.dumps({"right": right, "of": len(rows), "rows": rows},
                                           indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
