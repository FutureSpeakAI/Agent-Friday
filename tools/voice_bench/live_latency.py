"""Time a Gemini Live turn against the real API: connect, first audio, tools.

    python tools/voice_bench/live_latency.py                       # defaults
    python tools/voice_bench/live_latency.py --model gemini-3.8-live
    python tools/voice_bench/live_latency.py --tool-seconds 12 --behavior NON_BLOCKING

Opt-in and paid (a few Live seconds per run). It reads the Gemini key the way
Friday does (agent_friday.core, which loads it from the launch scripts or the
environment) and never prints it.

What it measures, per run, all from the moment the question is sent:

  connect      open the socket until setup_complete
  first_audio  the question is sent until the first audio byte comes back
  tool_call    until Gemini asks for the tool (tool runs only)
  after_tool   the tool result is sent until the next audio byte
  done         until turn_complete

The question is sent as text, which isolates the model and the network from
end-of-speech detection. Friday's server-side VAD adds its configured silence
window (800 ms) in front of every spoken turn; that figure is reported, not
measured, here.

`--prompt-chars` pads the system instruction to Friday's real size (about
74,000 characters with the full context), because first-audio latency on a
long prompt is what the user hears.

With a tool, the fake tool sleeps `--tool-seconds` before answering. BLOCKING
is the old behaviour (Gemini waits in silence); NON_BLOCKING lets the model
keep talking, and the result is delivered with scheduling=WHEN_IDLE.

`--friday` uses Friday's own session instead: its real tool declarations, VAD,
transcription, context compression and resumption, and a question that needs a
real tool ("what's the top news story?"), executed through Friday's governed
voice tool path exactly as a live call runs it. `--cold` clears Friday's read
caches first; otherwise the session warm-up runs, as it does on a real open.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "src"))

QUESTION = "In one short sentence: what is the capital of France?"
TOOL_QUESTION = ("Use the lookup_headlines tool to check today's top headline, "
                 "then tell me what it says.")
PAD = ("Background context the assistant may use. This line is padding that "
       "stands in for Friday's real memory, wiki and personality context. ")


def _key() -> str:
    try:
        from agent_friday import core
        k = getattr(core, "GEMINI_API_KEY", "") or ""
    except Exception:
        k = ""
    return k or os.environ.get("GEMINI_API_KEY", "")


FRIDAY_QUESTION = "What's the top news story right now? One sentence, please."


def _friday_config(types, args):
    from agent_friday.routes import voice as V
    from agent_friday.services.voice_engine import _build_voice_live_tools
    sys_text = V.VOICE_TOOL_CHOREOGRAPHY + V._voice_tool_surface_note()
    if args.prompt_chars > len(sys_text):
        sys_text = (PAD * (args.prompt_chars // len(PAD) + 1))[: args.prompt_chars - len(sys_text)] + sys_text
    kw = dict(
        response_modalities=[types.Modality.AUDIO],
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=args.voice))),
        system_instruction=types.Content(parts=[types.Part(text=sys_text)]),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        realtime_input_config=V._build_realtime_input_config(types, "auto"),
        context_window_compression=types.ContextWindowCompressionConfig(
            sliding_window=types.SlidingWindow()),
        tools=_build_voice_live_tools(types),
    )
    if hasattr(types, "SessionResumptionConfig"):
        kw["session_resumption"] = types.SessionResumptionConfig()
    return types.LiveConnectConfig(**kw)


def _friday_tool_runner(types):
    from agent_friday.routes import voice as V
    from agent_friday.services.voice_engine import _voice_tool_run

    async def run(fc_list):
        async def one(fc):
            t = time.perf_counter()
            res = await asyncio.to_thread(_voice_tool_run, fc.name, dict(fc.args or {}),
                                          lambda _o: None)
            res = V._gate_voice_tool_result(str(res)[:8000], fc.name)
            print("   tool %s took %.2fs" % (fc.name, time.perf_counter() - t), flush=True)
            kw = {"name": fc.name, "response": {"result": res}}
            if fc.id is not None:
                kw["id"] = fc.id
            return types.FunctionResponse(**kw)
        return await V._run_calls_concurrently(fc_list, one)
    return run


def _config(types, args, with_tool: bool):
    sys_text = ("You are a concise, friendly voice assistant. "
                "When you call a tool that may take a while, first say one short "
                "sentence such as 'Let me check.' and then call it.\n\n")
    if args.prompt_chars > len(sys_text):
        sys_text += (PAD * (args.prompt_chars // len(PAD) + 1))[: args.prompt_chars - len(sys_text)]
    kw = dict(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=args.voice))),
        system_instruction=types.Content(parts=[types.Part(text=sys_text)]),
    )
    if with_tool:
        decl = dict(name="lookup_headlines",
                    description="Look up today's top news headline. Slow: takes several seconds.",
                    parameters=types.Schema(type=types.Type.OBJECT, properties={}))
        if args.behavior == "NON_BLOCKING":
            decl["behavior"] = types.Behavior.NON_BLOCKING
        kw["tools"] = [types.Tool(function_declarations=[types.FunctionDeclaration(**decl)])]
    return types.LiveConnectConfig(**kw)


async def one_run(client, types, args, with_tool: bool) -> dict:
    out = {"model": args.model, "tool": with_tool,
           "behavior": args.behavior if with_tool else None}
    cfg = _friday_config(types, args) if args.friday else _config(types, args, with_tool)
    friday_run = _friday_tool_runner(types) if args.friday else None
    t_open = time.perf_counter()
    async with client.aio.live.connect(model=args.model, config=cfg) as sess:
        out["connect"] = time.perf_counter() - t_open
        t0 = time.perf_counter()
        question = FRIDAY_QUESTION if args.friday else (TOOL_QUESTION if with_tool else QUESTION)
        await sess.send_client_content(
            turns={"role": "user", "parts": [{"text": question}]}, turn_complete=True)
        tool_sent_at = None
        pending = []
        async def answer_tool(fc_list):
            nonlocal tool_sent_at
            if friday_run is not None:
                frs = await friday_run(fc_list)
                tool_sent_at = time.perf_counter()
                await sess.send_tool_response(function_responses=frs)
                return
            await asyncio.sleep(args.tool_seconds)
            frs = []
            for fc in fc_list:
                resp = {"result": "Top headline: Local library extends weekend hours."}
                kw = {"name": fc.name, "response": resp}
                if fc.id is not None:
                    kw["id"] = fc.id
                if args.behavior == "NON_BLOCKING":
                    resp["scheduling"] = "WHEN_IDLE"
                frs.append(types.FunctionResponse(**kw))
            tool_sent_at = time.perf_counter()
            await sess.send_tool_response(function_responses=frs)
        deadline = t0 + args.timeout
        while time.perf_counter() < deadline:
            async for msg in sess.receive():
                now = time.perf_counter()
                sc = msg.server_content
                if msg.tool_call and msg.tool_call.function_calls:
                    out.setdefault("tool_call", now - t0)
                    pending.append(asyncio.create_task(answer_tool(msg.tool_call.function_calls)))
                if sc is not None and sc.model_turn and sc.model_turn.parts:
                    for p in sc.model_turn.parts:
                        if p.inline_data and p.inline_data.data:
                            out.setdefault("first_audio", now - t0)
                            if tool_sent_at is not None:
                                out.setdefault("after_tool", now - tool_sent_at)
                            out["silent_gap_max"] = max(out.get("silent_gap_max", 0.0),
                                                        now - out.get("_last_audio", now))
                            out["_last_audio"] = now
                if sc is not None and sc.turn_complete:
                    # A tool turn completes twice: once for the acknowledgment,
                    # once after the result. Stop when the result has been spoken.
                    if not (with_tool or args.friday) or "after_tool" in out:
                        out["done"] = now - t0
                        break
            if "done" in out:
                break
        for p in pending:
            p.cancel()
    out.pop("_last_audio", None)
    return out


async def main_async(args) -> list:
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=_key())  # pragma: allowlist secret
    if args.friday:
        from agent_friday.services import news_engine, voice_engine
        news_engine._NEWS_CACHE.clear()
        voice_engine._VOICE_READS.clear()
        if not args.cold:
            voice_engine.warm_voice_reads()
            await asyncio.sleep(8)          # a real session's first seconds
    results = []
    for i in range(args.runs):
        r = await one_run(client, types, args, with_tool=args.tool_seconds > 0)
        results.append(r)
        print(json.dumps({k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()}),
              flush=True)
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", default="gemini-2.5-flash-native-audio-latest")
    ap.add_argument("--voice", default="Aoede")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--prompt-chars", type=int, default=74000)
    ap.add_argument("--tool-seconds", type=float, default=0.0)
    ap.add_argument("--behavior", choices=["BLOCKING", "NON_BLOCKING"], default="BLOCKING")
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument("--friday", action="store_true",
                    help="Friday's real session config and a real governed tool call")
    ap.add_argument("--cold", action="store_true",
                    help="with --friday: no warm-up, empty caches")
    args = ap.parse_args(argv)
    if not _key():
        print("no Gemini key found", file=sys.stderr)
        return 2
    res = asyncio.run(main_async(args))
    for field in ("connect", "first_audio", "tool_call", "after_tool", "done"):
        vals = [r[field] for r in res if field in r]
        if vals:
            print("%-12s median %.2fs  (n=%d, min %.2f, max %.2f)"
                  % (field, statistics.median(vals), len(vals), min(vals), max(vals)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
