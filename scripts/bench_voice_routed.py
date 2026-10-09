"""Bench the ROUTED local voice turn: system one (services/laya_router)
chooses the tool, the voice front only speaks (ftv/program/reference/
laya_router_addendum_2026-10-09.md).

Rig-only, like scripts/bench_voice_turn.py: a front served by hand on
``--port`` (or the armed one on :8125). For each utterance it measures, from
the endpoint:

  route_ms           the router's decision
  first_audio_ms     no-tool turn: the first clause the front speaks, through
                     the mouth; tool turn: the acknowledgement, through the mouth
  answer_token_ms    tool turn: the front's first token AFTER the result
  answer_audio_ms    tool turn: the answer's first clause through the mouth

Tools are never really run: the governed runner is replaced by a stub that
waits ``--tool-ms`` and returns a fixed result carrying a marker fact, so the
receipt can say whether the answer used the result (``faithful``) and
whether the front ever spoke tool syntax (``leaked_syntax``).

Gates (addendum section 5): no-tool first audio p50 <= 900 ms; tool-turn
acknowledgement audio p50 <= 900 ms.

    python scripts/bench_voice_routed.py --model ternary-bonsai:1.7b --port 8191 --runs 2
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_voice_turn as bvt  # noqa: E402

NO_TOOL = [
    "Good morning, how are you today?",
    "Tell me something interesting about octopuses.",
    "How should I think about refinancing right now?",
    "Thanks, that's all for now.",
    "What would you cook on a rainy evening?",
]
#: (utterance, the stub's result, facts: the answer is faithful when it states
#: at least one of them, in any wording listed).
TOOL = [
    ("What's on my calendar this afternoon?", "Dentist at 3:40 PM with Dr. Okafor.",
     ["okafor", "3:40", "three forty", "dentist"]),
    ("Any urgent emails?", "1 urgent: from the property manager, subject 'Water shut-off Tuesday'.",
     ["water", "shut"]),
    ("Search the web for the Louvre's opening hours.", "Louvre: 9:00 to 18:00, closed Tuesdays.",
     ["tuesday", "9:00", "9 a", "nine", "6 p", "six", "18:00"]),
    ("Give me my morning briefing.", "Briefing: rail strike called off; markets flat.",
     ["strike"]),
    ("What did I say about the roof repair in an earlier conversation?", "You said the roofer quoted 4,200 dollars.",
     ["4,200", "4200", "forty-two hundred", "forty two hundred", "four thousand two hundred"]),
]


def one(seat, prompt, text, user, mouth, temp, canned, tool_ms, facts=()):
    from agent_friday.services import laya_router as lr
    from agent_friday.services.voice_session import ClauseChunker
    t0 = time.perf_counter()
    r = lr.route(text, log=False)
    route_ms = (time.perf_counter() - t0) * 1000
    marks = {"first": None, "clause": None, "after_ack": None, "answer_clause": None}
    first_clause = {"text": None}
    answer_clause = {"text": None}
    chunk = ClauseChunker()
    answer_chunk = ClauseChunker()
    ack = lr.acknowledgement(r) if r.decision == "tool" else ""
    state = {"ack_done": not ack, "spoken": []}

    def on_delta(piece):
        now = time.perf_counter()
        state["spoken"].append(piece)
        if marks["first"] is None:
            marks["first"] = now
        if first_clause["text"] is None:
            got = chunk.feed(piece)
            if got:
                first_clause["text"], marks["clause"] = got[0], now
        if ack and not state["ack_done"]:
            if piece.strip() == ack.strip() or piece.startswith(ack):
                state["ack_done"] = True
                return
        if ack and state["ack_done"] and marks["after_ack"] is None and piece.strip() and not piece.startswith(ack):
            marks["after_ack"] = now
        if ack and marks["after_ack"] is not None and answer_clause["text"] is None:
            got = answer_chunk.feed(piece)
            if got:
                answer_clause["text"], marks["answer_clause"] = got[0], now

    def run_tool(name, args):
        time.sleep(tool_ms / 1000.0)
        return canned or "(bench: no data)"
    seat.routed_turn(prompt, [{"role": "user", "content": user(text)}],
                     tool=r.tool if r.decision == "tool" else None, args=r.args, ack=ack,
                     question=r.question if r.decision == "ask" else "",
                     run_tool=run_tool, on_delta=on_delta, max_tokens=200, temperature=temp)
    ms = lambda k: (marks[k] - t0) * 1000 if marks[k] else None
    out = {"utterance": text, "decision": r.decision, "tool": r.tool, "args": r.args,
           "layer": r.layer, "route_ms": round(route_ms, 2),
           "first_clause_ms": ms("clause"), "answer_token_ms": ms("after_ack"),
           "answer_clause_ms": ms("answer_clause")}
    spoken = "".join(state["spoken"])
    out["reply"] = spoken[:300]
    out["leaked_syntax"] = any(s in spoken for s in ("<tool_call>", "</tool_call>", '"name":', "<tool_response>"))
    if canned:
        answer = spoken[len(ack):].lower() if ack and spoken.startswith(ack) else spoken.lower()
        out["faithful"] = any(f.lower() in answer for f in facts)
    if mouth is not None:
        def audio(clause, at_ms):
            if not clause or at_ms is None:
                return None
            t = time.perf_counter()
            next(iter(mouth.synthesize_stream(clause)), None)
            return at_ms + (time.perf_counter() - t) * 1000
        out["first_audio_ms"] = audio(first_clause["text"], out["first_clause_ms"])
        out["answer_audio_ms"] = audio(answer_clause["text"], out["answer_clause_ms"])
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--port", type=int)
    ap.add_argument("--runs", type=int, default=2, help="passes over the 10 utterances")
    ap.add_argument("--no-mouth", action="store_true")
    ap.add_argument("--volatile", choices=("full", "clock"), default="clock")
    ap.add_argument("--temperature", type=float)
    ap.add_argument("--tool-ms", type=int, default=300)
    a = ap.parse_args(argv)
    from agent_friday.core import _load_settings
    from agent_friday.routes.voice import _build_front_speaker_prompt
    from agent_friday.services import laya_router as lr
    from agent_friday.services import voice_front as vf
    from agent_friday.services.residency_arbiter import runtime_dir
    settings = _load_settings() or {}
    seat = vf.FrontSeat(a.port) if a.port else vf.get()
    seat.model = a.model
    if not seat.healthy():
        print("the voice front is not serving on :%d" % seat.port)
        return 2
    prompt = _build_front_speaker_prompt(settings, vf.FRONT_MODELS[a.model]["label"])
    temp = a.temperature if a.temperature is not None else settings.get("temperature")
    user = bvt.context_block(settings, a.volatile)
    lr._prototypes()
    before = bvt.nvidia_smi()
    warm = seat.prefill(prompt, {"tools": []})
    mouth = None
    if not a.no_mouth:
        from agent_friday.services import voice_manifest as vm
        from agent_friday.services import voice_workers as vw
        mouth = vw.build_mouth(vm.read_selection(settings)["mouth"])
    turns = []
    for _ in range(a.runs):
        for t in NO_TOOL:
            turns.append(one(seat, prompt, t, user, mouth, temp, None, a.tool_ms))
        for t, canned, facts in TOOL:
            turns.append(one(seat, prompt, t, user, mouth, temp, canned, a.tool_ms, facts))
    after = bvt.nvidia_smi()
    nt = [t for t in turns if t["decision"] != "tool"]
    tt = [t for t in turns if t["decision"] == "tool"]
    key = "first_audio_ms" if mouth else "first_clause_ms"
    summary = {
        "model": a.model, "volatile": a.volatile, "mouth": mouth.describe() if mouth else None,
        "prompt_tokens": (warm or {}).get("prompt_n"),
        "route_ms_p95": bvt.pct([t["route_ms"] for t in turns], 95),
        "no_tool_first_audio": {"p50": bvt.pct([t.get(key) for t in nt if t.get(key)], 50),
                                "p95": bvt.pct([t.get(key) for t in nt if t.get(key)], 95)},
        "tool_ack_audio": {"p50": bvt.pct([t.get(key) for t in tt if t.get(key)], 50),
                           "p95": bvt.pct([t.get(key) for t in tt if t.get(key)], 95)},
        "tool_answer_token": {"p50": bvt.pct([t["answer_token_ms"] for t in tt if t["answer_token_ms"]], 50)},
        "tool_answer_audio": {"p50": bvt.pct([t.get("answer_audio_ms") for t in tt if t.get("answer_audio_ms")], 50)},
        "routed_as_expected": "%d/%d" % (sum(t["decision"] == "no_tool" for t in nt) + len(tt), len(turns)),
        "faithful": "%d/%d" % (sum(bool(t.get("faithful")) for t in tt), len(tt)),
        "leaked_syntax": sum(t["leaked_syntax"] for t in turns),
    }
    summary["gate_no_tool"] = bool(summary["no_tool_first_audio"]["p50"]) and summary["no_tool_first_audio"]["p50"] <= 900 and mouth is not None
    summary["gate_tool_ack"] = bool(summary["tool_ack_audio"]["p50"]) and summary["tool_ack_audio"]["p50"] <= 900 and mouth is not None
    receipt = dict(summary, at=time.strftime("%Y-%m-%dT%H:%M:%S"), runs=a.runs, tool_ms=a.tool_ms,
                   vram_before=before, vram_after=after, prefill=warm, turns=turns)
    out = Path(runtime_dir()) / "voice" / "bench" / ("routed_%s_%d.json" % (bvt.safe_name(a.model), int(time.time())))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    for t in turns:
        print("  %-7s %-50s ack/first %s  answer %s  %s%s" % (
            t["decision"], t["utterance"][:50], t.get(key) and round(t[key]),
            t.get("answer_audio_ms") and round(t["answer_audio_ms"]),
            "" if t.get("faithful") in (None, True) else "UNFAITHFUL ",
            "LEAK" if t["leaked_syntax"] else ""))
    print("receipt:", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
