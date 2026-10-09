"""Bench the local voice turn, post-endpoint (local voice spec §9, P1 gate).

Rig-only. Run it with the voice front SERVING (a session armed, or the
front started through the Arbiter) and nothing else of Friday's disturbed;
GPU runs happen only in a lane gap the lead grants. It measures, for each
utterance, what the owner waits for after they stop talking:

  front first token  - prompt sent (prefix cached) -> first streamed token
  first clause       - -> the first clause boundary the speaker would cut
  mouth first audio  - Kokoro synthesising that clause (GPU worker or CPU)

and writes a receipt to <runtime>/voice/bench/turn_<model>_<ts>.json with
p50/p95, the front's prefill and decode rates from llama-server's own
timings, and nvidia-smi used/free MiB before and after. The P1 gate is
post-endpoint first audio <= 0.9 s p50 warm (ASR final is P2's number).

    FRIDAY_HOME=<home> python scripts/bench_voice_turn.py --runs 10

``--model`` and ``--port`` bench a front served by hand (a bench llama-server
outside the Arbiter's survey window) instead of the saved choice on :8125.
``--tools-eval`` adds the tool-call check: each request in TOOL_CASES must
produce, on its first round, a call the contract validates
(``voice_front.validate_tool_call``) to one of the expected tools, and the
small-talk cases must produce no call at all.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

UTTERANCES = [
    "What's on my calendar this afternoon?",
    "Tell me something interesting about octopuses.",
    "Any urgent emails?",
    "How should I think about refinancing right now?",
    "Good morning, how are you today?",
]


#: (request, tools that answer it; an empty set means "no tool").
TOOL_CASES = [
    ("What's on my calendar this afternoon?", {"query_calendar", "show_my_day"}),
    ("Any urgent emails?", {"check_email"}),
    ("Search my email for the invoice from Acme.", {"search_email"}),
    ("What's the latest news about the Artemis moon mission?", {"search_news"}),
    ("Search the web for the Louvre's opening hours.", {"search_web"}),
    ("Give me my morning briefing.", {"get_briefing", "show_my_day"}),
    ("Find the file on my computer called budget.", {"search_files"}),
    ("What did I say about the roof repair in an earlier conversation?",
     {"search_past_conversations"}),
    ("Search my wiki for the page about the garden project.", {"search_wiki"}),
    ("Good morning, how are you today?", set()),
    ("Thanks, that's all for now.", set()),
]


def safe_name(model: str) -> str:
    """A model id as a file-name part (``ternary-bonsai:1.7b`` has a colon,
    which Windows refuses in a file name)."""
    return "".join(c if c.isalnum() or c in "-._" else "-" for c in str(model))


def tool_case(seat, prompt, contract, text, expect) -> dict:
    """One request; the first round's calls are checked, never run."""
    from agent_friday.routes.voice import _voice_user_message
    from agent_friday.services import voice_front as vf
    seen = []

    def run_tool(name, args):
        seen.append({"name": name, "args": args})
        return "(bench: no data)"
    t0 = time.perf_counter()
    err = None
    try:
        reply = seat.run_turn(prompt, [{"role": "user", "content": _voice_user_message(text)}],
                              contract, run_tool=run_tool, max_tokens=200)
    except Exception as e:  # noqa: BLE001
        reply, err = "", f"{type(e).__name__}: {e}"
    first = seen[0] if seen else None
    if expect:
        ok = bool(first) and first["name"] in expect
    else:
        ok = not seen and bool(reply.strip())
    return {"utterance": text, "expected": sorted(expect), "first_call": first,
            "calls": len(seen), "reply": reply[:160], "error": err, "correct": ok,
            "ms": round((time.perf_counter() - t0) * 1000, 1)}


def nvidia_smi() -> dict:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.free,memory.total",
             "--format=csv,noheader,nounits"], capture_output=True, text=True,
            timeout=10).stdout.strip().splitlines()[0]
        used, free, total = (int(x) for x in out.split(","))
        return {"used_mib": used, "free_mib": free, "total_mib": total}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def pct(xs, p):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = max(0, min(len(xs) - 1, int(round((p / 100.0) * (len(xs) - 1)))))
    return round(xs[k], 1)


def one_turn(seat, prompt, contract, text, mouth=None) -> dict:
    from agent_friday.routes.voice import _voice_user_message
    from agent_friday.services.voice_session import ClauseChunker
    marks = {"t0": time.perf_counter(), "first_token": None, "first_clause": None}
    chunker = ClauseChunker()
    first = {"clause": None}

    def on_delta(piece):
        now = time.perf_counter()
        if marks["first_token"] is None:
            marks["first_token"] = now
        if first["clause"] is None:
            got = chunker.feed(piece)
            if got:
                first["clause"] = got[0]
                marks["first_clause"] = now
    timings = {}
    seat.run_turn(prompt, [{"role": "user", "content": _voice_user_message(text)}],
                  contract, on_delta=on_delta, run_tool=lambda n, a: "(bench: tool not run)",
                  max_tokens=200, timings=timings)
    out = {"utterance": text,
           "first_token_ms": (marks["first_token"] - marks["t0"]) * 1000
           if marks["first_token"] else None,
           "first_clause_ms": (marks["first_clause"] - marks["t0"]) * 1000
           if marks["first_clause"] else None,
           "prompt_n": timings.get("prompt_n"),
           "prompt_per_second": timings.get("prompt_per_second"),
           "predicted_per_second": timings.get("predicted_per_second")}
    if mouth is not None and first["clause"]:
        t = time.perf_counter()
        next(iter(mouth.synthesize_stream(first["clause"])), None)
        out["mouth_first_audio_ms"] = (time.perf_counter() - t) * 1000
        out["post_endpoint_first_audio_ms"] = (out["first_clause_ms"] or 0) + \
            out["mouth_first_audio_ms"]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--no-mouth", action="store_true")
    ap.add_argument("--model", help="a voice_front.FRONT_MODELS key (default: the saved choice)")
    ap.add_argument("--port", type=int, help="where the front is served (default: the front port)")
    ap.add_argument("--tools-eval", action="store_true")
    a = ap.parse_args(argv)
    from agent_friday.core import _load_settings
    from agent_friday.routes.voice import _build_front_system_prompt
    from agent_friday.services import voice_front as vf
    from agent_friday.services.residency_arbiter import runtime_dir
    from agent_friday.services.voice_engine import build_voice_tool_contract
    settings = _load_settings() or {}
    model = a.model or vf.selected_model(settings)
    if model not in vf.FRONT_MODELS:
        print("unknown front model %r; known: %s" % (model, ", ".join(vf.FRONT_MODELS)))
        return 2
    seat = vf.FrontSeat(a.port) if a.port else vf.get()
    seat.model = model
    if not seat.healthy():
        print("the voice front is not serving on :%d; arm a session first" % seat.port)
        return 2
    contract = build_voice_tool_contract()
    prompt = _build_front_system_prompt(settings, contract, vf.FRONT_MODELS[model]["label"])
    before = nvidia_smi()
    warm = seat.prefill(prompt, contract)
    mouth = None
    if not a.no_mouth:
        from agent_friday.services import voice_manifest as vm
        from agent_friday.services import voice_workers as vw
        mouth = vw.build_mouth(vm.read_selection(settings)["mouth"])
    turns = [one_turn(seat, prompt, contract, UTTERANCES[i % len(UTTERANCES)], mouth)
             for i in range(a.runs)]
    tools = None
    if a.tools_eval:
        cases = [tool_case(seat, prompt, contract, t, e) for t, e in TOOL_CASES]
        tools = {"correct": sum(c["correct"] for c in cases), "of": len(cases),
                 "cases": cases}
    after = nvidia_smi()
    key = "post_endpoint_first_audio_ms" if mouth else "first_clause_ms"
    receipt = {
        "model": model, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "contract_tokens": contract["tokens"], "contract_tools": len(contract["names"]),
        "prefill_warm": warm, "runs": a.runs,
        "mouth": (mouth.describe() if mouth else None),
        "first_token_ms": {"p50": pct([t["first_token_ms"] for t in turns], 50),
                           "p95": pct([t["first_token_ms"] for t in turns], 95)},
        key: {"p50": pct([t.get(key) for t in turns], 50),
              "p95": pct([t.get(key) for t in turns], 95)},
        "gate_p50_ms": 900,
        "vram_before": before, "vram_after": after, "turns": turns,
        "prompt_tokens_prefill": (warm or {}).get("prompt_n"),
        "window": vf.FRONT_MODELS[model]["ctx"],
        "tools_eval": tools,
    }
    receipt["gate_passed"] = (receipt[key]["p50"] is not None
                              and receipt[key]["p50"] <= 900 and mouth is not None)
    out = Path(runtime_dir()) / "voice" / "bench" / ("turn_%s_%d.json" % (safe_name(model), int(time.time())))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps({k: receipt[k] for k in ("model", "first_token_ms", key,
                                              "gate_passed", "prompt_tokens_prefill",
                                              "window")}, indent=2))
    if tools:
        print("tool calls: %d of %d correct" % (tools["correct"], tools["of"]))
        for c in tools["cases"]:
            print("  %s %-62s -> %s" % ("ok " if c["correct"] else "BAD", c["utterance"][:62],
                                         (c["first_call"] or {}).get("name") or c["error"] or "(no call)"))
    print("receipt:", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
