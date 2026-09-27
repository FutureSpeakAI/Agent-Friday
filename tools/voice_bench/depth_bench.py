"""Measure how long and how list-like Gemini Live's spoken replies are.

    python tools/voice_bench/depth_bench.py                 # both conditions
    python tools/voice_bench/depth_bench.py --model gemini-3.8-live

Opt-in and paid (a few Live seconds per turn). Reads the Gemini key the way
Friday does and never prints it.

Two scripted exchanges on general-knowledge topics (nothing personal):
  detail  a person digging into one subject (why/how questions, follow-ups,
          "tell me more"): replies should get substantially longer and connected;
  quick   a quick back-and-forth: replies should stay short.
Two conditions:
  old     the length rule Friday used before (kept verbatim below);
  new     services/voice_persona.VOICE_LENGTH_RULE plus the per-turn
          "conversation so far" note from services/voice_conversation_state,
          sent exactly as the live bridge sends it (turn_complete=False before
          the next user turn).
Replies are read from the output audio transcription. Reported per script and
condition: words per reply (median, mean, list), and replies that read as a
list ("first... second...", "two things", "1. 2.").
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "src"))

BASE = ("You are Friday, a personal assistant in a LIVE VOICE conversation. Speak "
        "naturally, like a person. Never use markdown. Use contractions.\n")

OLD_RULE = (
    "HOW LONG TO TALK: Match the moment. Quick back-and-forth, a yes or no, a "
    "confirmation or small talk gets a sentence or two. The news, the briefing, "
    "an explanation, a story, or 'walk me through it' gets room: several spoken "
    "paragraphs in short sentences, until the substance is covered. Follow their "
    "cues and keep following them: 'tell me more', 'go on' or 'why?' means go "
    "longer; 'keep it short', 'just the headline' or 'bottom line' means go "
    "shorter until they say otherwise. Long answers come in short sentences with "
    "natural pauses so they can follow and interrupt.\n")

SCRIPTS = {
    "detail": [
        "How does refinancing a mortgage actually work?",
        "Why would anyone refinance when rates are higher than when they bought?",
        "How do closing costs change whether it's worth it?",
        "Tell me more about working out the break-even point.",
    ],
    "quick": [
        "What's the boiling point of water in Fahrenheit?",
        "Is that at sea level?",
        "Cool, thanks.",
        "What day comes after Tuesday?",
    ],
}

#: Enumeration markers. A single "first, you..." walking through a procedure is
#: a step, so a reply counts as a list only with two or more markers.
LIST_MARKERS = re.compile(r"(?i)\b(?:first(?:ly)?|second(?:ly)?|third(?:ly)?|finally)\s*,"
                          r"|\b(?:two|three|a couple of|a few) things\b"
                          r"|\bhere are (?:a few|some|two|three)\b|\boption (?:one|two)\b"
                          r"|(?:^|\s)[1-3][.)]\s")


def _listy(reply: str) -> bool:
    return len(LIST_MARKERS.findall(reply or "")) >= 2


def _key() -> str:
    try:
        from agent_friday import core
        k = getattr(core, "GEMINI_API_KEY", "") or ""
    except Exception:
        k = ""
    return k or os.environ.get("GEMINI_API_KEY", "")


async def _turn(sess, text) -> str:
    await sess.send_client_content(turns={"role": "user", "parts": [{"text": text}]},
                                   turn_complete=True)
    said = []
    async for msg in sess.receive():
        sc = msg.server_content
        if sc is None:
            continue
        tr = getattr(sc, "output_transcription", None)
        if tr and getattr(tr, "text", None):
            said.append(tr.text)
        if sc.turn_complete:
            break
    return "".join(said).strip()


async def run_script(client, types, model, voice, condition, lines) -> list:
    from agent_friday.services import voice_conversation_state as vcs
    from agent_friday.services import voice_persona as vp
    rule = vp.VOICE_LENGTH_RULE if condition == "new" else OLD_RULE
    cfg = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(language_code="en-US", voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))),
        system_instruction=types.Content(parts=[types.Part(text=BASE + rule)]),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )
    state = vcs.new_state()
    replies = []
    async with client.aio.live.connect(model=model, config=cfg) as sess:
        for line in lines:
            reply = await _turn(sess, line)
            replies.append(reply)
            if condition == "new":
                vcs.update(state, line, reply)
                await sess.send_client_content(
                    turns={"role": "user", "parts": [{"text": vcs.render(state)}]},
                    turn_complete=False)
    return replies


def summarize(replies) -> dict:
    words = [len(r.split()) for r in replies]
    return {"words": words, "median": statistics.median(words) if words else 0,
            "mean": round(statistics.mean(words), 1) if words else 0,
            "listy": sum(1 for r in replies if _listy(r))}


async def main_async(args) -> dict:
    from google import genai
    from google.genai import types
    key = _key()
    if not key:
        raise SystemExit("no Gemini key available")
    client = genai.Client(api_key=key)
    out = {}
    for script in args.scripts:
        for condition in args.conditions:
            replies = await run_script(client, types, args.model, args.voice, condition,
                                       SCRIPTS[script])
            out[f"{script}/{condition}"] = summarize(replies)
            if args.show_text:
                out[f"{script}/{condition}"]["replies"] = replies
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gemini-3.8-live")
    ap.add_argument("--voice", default="Aoede")
    ap.add_argument("--scripts", nargs="+", default=["detail", "quick"])
    ap.add_argument("--conditions", nargs="+", default=["old", "new"])
    ap.add_argument("--show-text", action="store_true")
    args = ap.parse_args(argv)
    print(json.dumps(asyncio.run(main_async(args)), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
