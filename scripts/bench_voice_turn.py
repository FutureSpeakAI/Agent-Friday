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
``--tools-eval`` adds the tool-call check: the first call the model EMITS for
each request in TOOL_CASES must be one of the expected tools and pass the
contract's validator (``voice_front.validate_tool_call``); the small-talk
cases must produce no call at all.

Every round of every turn is recorded (finish reason, the calls the model
emitted with their raw arguments and the validator's verdict, reasoning and
content heads), so a miss says whether the model never called, called the
wrong tool, or called and was refused. ``--volatile clock`` sends only the
clock section of the per-turn context block, and ``--thinking`` lets a
Qwen3-family front think: experiment switches, not product modes.
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


class Rounds:
    """Records what each round of a turn really did, by wrapping the stream
    reader and the tool-call validator the turn itself uses."""

    def __init__(self):
        from agent_friday.services import model_router as mr
        from agent_friday.services import voice_front as vf
        self.log, self._calls = [], []
        orig_v, orig_c = vf.validate_tool_call, mr._consume_sse_completion

        def validate(call, contract):
            name, args, why = orig_v(call, contract)
            self._calls.append({"name": name,
                                "raw": ((call or {}).get("function") or {}).get("arguments"),
                                "refused": why})
            return name, args, why

        def consume(resp, **kw):
            out = orig_c(resp, **kw)
            ch = (out.get("choices") or [{}])[0]
            msg = ch.get("message") or {}
            t = out.get("timings") or {}
            self.log.append({
                "finish": ch.get("finish_reason"),
                "emitted": [(c.get("function") or {}).get("name") for c in msg.get("tool_calls") or []],
                "content": (msg.get("content") or "")[:160],
                "reasoning_chars": len(msg.get("reasoning_content") or ""),
                "predicted_n": t.get("predicted_n"), "prompt_n": t.get("prompt_n")})
            return out
        vf.validate_tool_call = validate
        mr._consume_sse_completion = consume

    def take(self):
        rounds, calls = self.log, self._calls
        self.log, self._calls = [], []
        return rounds, calls


def context_block(settings, which):
    """The per-turn user message, with the volatile block built ONCE for the
    run (turns in the same minute see the same block), or only its clock
    section for ``clock``."""
    from agent_friday.routes.voice import _build_voice_system_prompt, _voice_user_message
    vol = (_build_voice_system_prompt(settings)[1].get("volatile") or "").strip()
    if which == "clock":
        vol = vol.split("\n== ", 1)[0].strip() if vol.startswith("== AUTHORITATIVE CLOCK") else ""
    return lambda text: _voice_user_message(text, settings, volatile=vol)


def safe_name(model: str) -> str:
    """A model id as a file-name part (``ternary-bonsai:1.7b`` has a colon,
    which Windows refuses in a file name)."""
    return "".join(c if c.isalnum() or c in "-._" else "-" for c in str(model))


def tool_case(seat, prompt, contract, text, expect, user, rec, temp=None) -> dict:
    """One request. The first call the model emits is scored, and it counts
    only if the validator accepts it. Tools are never really run."""
    seen = []

    def run_tool(name, args):
        seen.append({"name": name, "args": args})
        return "(bench: no data)"
    t0 = time.perf_counter()
    err = None
    try:
        reply = seat.run_turn(prompt, [{"role": "user", "content": user(text)}],
                              contract, run_tool=run_tool, max_tokens=200, temperature=temp)
    except Exception as e:  # noqa: BLE001
        reply, err = "", f"{type(e).__name__}: {e}"
    rounds, calls = rec.take()
    first = calls[0] if calls else None
    if expect:
        ok = bool(first) and first["name"] in expect and not first["refused"]
    else:
        ok = not calls and bool(reply.strip())
    return {"utterance": text, "expected": sorted(expect), "first_call": first,
            "calls": len(calls), "ran": len(seen), "reply": reply[:300], "error": err,
            "correct": ok, "rounds": rounds,
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


def one_turn(seat, prompt, contract, text, user, rec, mouth=None, temp=None) -> dict:
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
    seat.run_turn(prompt, [{"role": "user", "content": user(text)}],
                  contract, on_delta=on_delta, run_tool=lambda n, a: "(bench: tool not run)",
                  max_tokens=200, timings=timings, temperature=temp)
    rounds, _calls = rec.take()
    out = {"utterance": text, "rounds": rounds,
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
    ap.add_argument("--volatile", choices=("full", "clock"), default="full")
    ap.add_argument("--thinking", action="store_true")
    ap.add_argument("--temperature", type=float, help="default: the owner's setting, as a call sends it")
    ap.add_argument("--cases", help="comma-separated TOOL_CASES indices (default: all)")
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
    if a.thinking:
        vf._NO_THINKING = {"enable_thinking": True}
    user = context_block(settings, a.volatile)
    temp = a.temperature if a.temperature is not None else settings.get("temperature")
    rec = Rounds()
    before = nvidia_smi()
    warm = seat.prefill(prompt, contract)
    mouth = None
    if not a.no_mouth:
        from agent_friday.services import voice_manifest as vm
        from agent_friday.services import voice_workers as vw
        mouth = vw.build_mouth(vm.read_selection(settings)["mouth"])
    turns = [one_turn(seat, prompt, contract, UTTERANCES[i % len(UTTERANCES)], user, rec, mouth, temp)
             for i in range(a.runs)]
    tools = None
    if a.tools_eval:
        pick = [int(i) for i in a.cases.split(",")] if a.cases else range(len(TOOL_CASES))
        cases = [tool_case(seat, prompt, contract, *TOOL_CASES[i], user, rec, temp) for i in pick]
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
        "volatile": a.volatile, "thinking": a.thinking, "temperature": temp,
        "user_block_chars": len(user("")),
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
            fc = c["first_call"] or {}
            print("  %s %-62s -> %s%s" % ("ok " if c["correct"] else "BAD", c["utterance"][:62],
                                           fc.get("name") or c["error"] or "(no call)",
                                           (" REFUSED: " + fc["refused"]) if fc.get("refused") else ""))
    print("receipt:", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
