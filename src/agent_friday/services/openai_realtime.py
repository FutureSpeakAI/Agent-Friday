"""OpenAI Realtime as the third voice engine: the rules, the price and the wire messages (BD-16/BD-19 voice).

Gemini Live and Claude + ElevenLabs answer a voice session today. OpenAI's Realtime API (the tech behind
ChatGPT Advanced Voice) is the third. This module is the part that needs no socket and no Flask: when the
engine may be used and what to say when it may not, what a minute costs and when the owner's cap ends a
session, and the messages Friday sends and reads. The TV app (friday-tv, live/OpenAiRules + OpenAiProtocol)
holds the same rules, so a Talk press behaves the same on both surfaces: gpt-realtime-2.1 with gpt-realtime
tried once if the server does not know the name, about $0.10 a minute, never hidden (every refusal says why),
no kids mode, the key from the owner's own key flow only, and a visible hand-over to Gemini Live.

STANDING RULES this module keeps (cloud-voice-providers.md C1/C2):
  * Nothing here is a default. The owner chose OpenAI, or this is never asked.
  * No silent fallback. :func:`readiness` and :func:`failover` return a SAY line and an OFFER; the caller shows
    them. The one automatic hand-over is the TV's "out of credit, same press goes to Gemini Live", and it is
    announced; on the desktop it is an offer unless the owner turned ``voice_openai_auto_failover`` on.
  * The key is read from the credential store only (never an environment variable, never a URL, never logged).

The numbers come from OpenAI's pricing page as the TV lane read it on 2026-11-07: audio $32 in / $64 out per
1M tokens, text $4 / $24. The usage fields of ``response.done`` are read defensively: whatever is missing is
counted as audio, the dearer side, so a receipt never understates.
"""
from __future__ import annotations

import json
import math
from typing import Any, Dict, Iterable, List, Optional

MODEL = "gpt-realtime-2.1"
FALLBACK_MODEL = "gpt-realtime"          # tried once when the server does not know MODEL
VOICE = "marin"
SAMPLE_RATE = 24000                      # PCM16 mono, both directions
PROVIDER = "openai"                      # the credential-store name of the key
HOST = "api.openai.com"

#: USD per 1M tokens.
PRICE = {"audio_in": 32.0, "audio_out": 64.0, "text_in": 4.0, "text_out": 24.0, "cached_in": 0.40}
#: What the card says a minute costs ("about $0.10 a minute").
PER_MINUTE_USD = 0.10
#: Audio in is billed while the owner is quiet, so a silent session ends itself.
SILENCE_END_S = 30
#: A press never starts OpenAI without one minute's money left under the cap.
MIN_BUDGET_USD = PER_MINUTE_USD
#: Out of credit blocks OpenAI for this long, and the press goes elsewhere.
NO_CREDIT_BLOCK_S = 3600

# Why OpenAI is not answering (``readiness()["reason"]``).
OK = "ok"
NO_KEY = "no_key"
KEY_UNREADABLE = "key_unreadable"
LOCAL_ONLY = "local_only"
KIDS = "kids"
CAP_REACHED = "cap_reached"
NO_CREDIT = "no_credit"
OFFLINE = "offline"


def ws_url(model: str = MODEL) -> str:
    """The Realtime WebSocket. The key is NOT in it: it goes in the Authorization header."""
    return "wss://%s/v1/realtime?model=%s" % (HOST, model)


def headers(key: str) -> Dict[str, str]:
    """GA interface: no ``OpenAI-Beta`` header."""
    return {"Authorization": "Bearer " + key}


def api_key() -> Optional[str]:
    """The owner's OpenAI key, from the credential store (the key flow), or None."""
    try:
        from agent_friday.services import credential_store as cs
        return cs.get_provider_key(PROVIDER) or None
    except Exception:
        return None


def key_state() -> str:
    """``connected`` / ``missing`` / ``present_but_unreadable``: a status read, not a use of the key."""
    try:
        from agent_friday.services import credential_store as cs
        return cs.provider_key_status(PROVIDER)
    except Exception:
        return "missing"


# ── cost ─────────────────────────────────────────────────────────────────────────────────────────

def _n(d: Any, *keys: str) -> int:
    for k in keys:
        try:
            v = d.get(k) if isinstance(d, dict) else None
            if v is not None:
                return max(0, int(v))
        except (TypeError, ValueError):
            continue
    return 0


def cost_usd(usage: Any) -> float:
    """The cost of one ``response.done`` usage object. Never raises, never negative.

    Uses the per-modality split when OpenAI sends one; tokens it does not account for are priced as audio."""
    if not isinstance(usage, dict):
        return 0.0
    i_all, o_all = _n(usage, "input_tokens"), _n(usage, "output_tokens")
    ind = usage.get("input_token_details") if isinstance(usage.get("input_token_details"), dict) else {}
    outd = usage.get("output_token_details") if isinstance(usage.get("output_token_details"), dict) else {}
    i_text, i_cached = _n(ind, "text_tokens"), _n(ind, "cached_tokens")
    o_text = _n(outd, "text_tokens")
    i_audio = _n(ind, "audio_tokens") or max(0, i_all - i_text)
    o_audio = _n(outd, "audio_tokens") or max(0, o_all - o_text)
    i_cached = min(i_cached, i_audio + i_text)
    billable_in_audio = max(0, i_audio - i_cached)
    usd = (billable_in_audio * PRICE["audio_in"] + i_text * PRICE["text_in"] + i_cached * PRICE["cached_in"]
           + o_audio * PRICE["audio_out"] + o_text * PRICE["text_out"]) / 1_000_000.0
    return round(usd, 6)


def per_minute_line() -> str:
    return "about $%.2f a minute" % PER_MINUTE_USD


# ── may it answer, and what to say when it may not ───────────────────────────────────────────────

def readiness(*, key: Optional[str] = None, key_status: Optional[str] = None, local_only: bool = False,
              kids: bool = False, offline: bool = False, spent_month_usd: float = 0.0,
              cap_month_usd: Optional[float] = None, blocked_until: float = 0.0,
              now: float = 0.0) -> Dict[str, Any]:
    """Can OpenAI Realtime answer a Talk press now? ``{"ok", "reason", "say"}``; ``say`` is what the owner
    reads when it can not (never empty then). ``cap_month_usd`` None means the owner set no cap."""
    status = key_status or ("connected" if key else "missing")
    if kids:
        return _no(KIDS, "Kids mode does not use OpenAI voice. Friday's own kids voice answers.")
    if local_only:
        return _no(LOCAL_ONLY, "Local-only mode is on, so your voice stays on this PC. OpenAI voice is off.")
    if status == "present_but_unreadable":
        return _no(KEY_UNREADABLE, "Your OpenAI key is saved but can not be read on this PC. Add it again.")
    if status != "connected":
        return _no(NO_KEY, "Add an OpenAI key to use this.")
    if offline:
        return _no(OFFLINE, "This PC is offline, so OpenAI voice can not connect.")
    if blocked_until and now < blocked_until:
        return _no(NO_CREDIT, "Your OpenAI account is out of credit. Friday answers another way until it is topped up.")
    if cap_month_usd is not None and (cap_month_usd - max(0.0, spent_month_usd)) < MIN_BUDGET_USD:
        return _no(CAP_REACHED, "You reached your monthly OpenAI voice limit ($%.2f). Raise it in Settings to go on."
                   % cap_month_usd)
    return {"ok": True, "reason": OK, "say": ""}


def _no(reason: str, say: str) -> Dict[str, Any]:
    return {"ok": False, "reason": reason, "say": say}


def failover(reason: str, *, gemini_ready: bool, local_ready: bool, auto: bool = False) -> Dict[str, Any]:
    """What happens to a press OpenAI can not answer. Always visible; ``auto`` is the owner's own setting.

    ``{"engine": "gemini"|"local"|None, "automatic": bool, "say": str}``. With ``automatic`` False the caller
    shows ``say`` as an offer and does nothing until the owner agrees."""
    # Kids, local-only and an unreadable or missing key are the owner's own state, not an outage: offer, never auto.
    outage = reason in (NO_CREDIT, OFFLINE, CAP_REACHED)
    if reason == LOCAL_ONLY or reason == KIDS:
        gemini_ready = False                  # cloud voice stays off in these modes
    if gemini_ready:
        eng, name = "gemini", "Gemini Live"
    elif local_ready:
        eng, name = "local", "Friday's own voice"
    else:
        return {"engine": None, "automatic": False,
                "say": "No other voice is ready. Press Talk again after fixing the one above."}
    automatic = bool(auto and outage)
    if automatic:
        say = "OpenAI voice is not available, so %s is answering this press." % name
    else:
        say = "Switch to %s for this press?" % name
    return {"engine": eng, "automatic": automatic, "say": say}


def which_answers(*, engine: str, model: str = MODEL, reason: str = "") -> str:
    """The plain words for "which model answers"."""
    if engine == "openai":
        return "OpenAI Realtime (%s) is answering." % model
    why = (" (%s)" % reason) if reason else ""
    return {"gemini": "Gemini Live is answering" + why + ".",
            "local": "Friday's own voice is answering" + why + "."}.get(engine, "Text only" + why + ".")


# ── the wire ─────────────────────────────────────────────────────────────────────────────────────

def flat_tools(contract_tools: Iterable[dict]) -> List[dict]:
    """voice_engine.build_voice_tool_contract()["tools"] (chat-completions shape) as Realtime tools."""
    out = []
    for t in contract_tools or ():
        f = (t or {}).get("function") if isinstance(t, dict) else None
        if isinstance(f, dict) and f.get("name"):
            out.append({"type": "function", "name": f["name"], "description": f.get("description") or "",
                        "parameters": f.get("parameters") or {"type": "object", "properties": {}}})
    return out


def session_update(instructions: str, tools: Iterable[dict], *, model: str = MODEL, voice: str = VOICE) -> dict:
    """The one session setup: semantic VAD that interrupts the model when the owner speaks (barge-in)."""
    return {"type": "session.update", "session": {
        "type": "realtime", "model": model, "output_modalities": ["audio"], "instructions": instructions,
        "audio": {"input": {"format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
                            "turn_detection": {"type": "semantic_vad", "interrupt_response": True,
                                               "create_response": True}},
                  "output": {"format": {"type": "audio/pcm", "rate": SAMPLE_RATE}, "voice": voice}},
        "tools": flat_tools(tools), "tool_choice": "auto"}}


def audio_append(b64_pcm16: str) -> dict:
    return {"type": "input_audio_buffer.append", "audio": b64_pcm16}


def function_output(call_id: str, output: Any) -> dict:
    return {"type": "conversation.item.create", "item": {
        "type": "function_call_output", "call_id": call_id,
        "output": output if isinstance(output, str) else json.dumps(output, default=str)}}


def response_create() -> dict:
    return {"type": "response.create"}


def truncate(item_id: str, played_ms: int) -> dict:
    """Barge-in: tell the model how much of its answer the owner actually heard."""
    return {"type": "conversation.item.truncate", "item_id": item_id, "content_index": 0,
            "audio_end_ms": max(0, int(played_ms))}


def no_credit_error(err: Any) -> bool:
    """An out-of-credit error, by its code or type (NOT a rate limit, which is temporary)."""
    if not isinstance(err, dict):
        return False
    blob = " ".join(str(err.get(k) or "") for k in ("code", "type", "message")).lower()
    if "rate_limit" in blob and "quota" not in blob:
        return False
    return any(w in blob for w in ("insufficient_quota", "billing", "exceeded your current quota", "out of credit"))


def unknown_model_error(err: Any) -> bool:
    if not isinstance(err, dict):
        return False
    blob = " ".join(str(err.get(k) or "") for k in ("code", "type", "message")).lower()
    return "model" in blob and any(w in blob for w in ("not_found", "does not exist", "invalid_model", "unknown"))


def parse(event: Any) -> Optional[Dict[str, Any]]:
    """One server event as ``{"kind": ...}`` for the bridge, or None for what it ignores.

    kinds: audio(data), transcript(text), heard(text), speech_started, tool_call(call_id,name,args),
    done(cost, cancelled), item(item_id), error(no_credit, unknown_model, message)."""
    if isinstance(event, (str, bytes)):
        try:
            event = json.loads(event)
        except ValueError:
            return None
    if not isinstance(event, dict):
        return None
    t = str(event.get("type") or "")
    if t == "response.output_audio.delta":
        return {"kind": "audio", "data": event.get("delta") or "", "item_id": event.get("item_id")}
    if t == "response.output_audio_transcript.delta":
        return {"kind": "transcript", "text": event.get("delta") or ""}
    if t == "conversation.item.input_audio_transcription.completed":
        return {"kind": "heard", "text": event.get("transcript") or ""}
    if t == "input_audio_buffer.speech_started":
        return {"kind": "speech_started"}
    if t == "response.done":
        r = event.get("response") if isinstance(event.get("response"), dict) else {}
        calls = []
        for it in r.get("output") or ():
            if isinstance(it, dict) and it.get("type") == "function_call" and it.get("call_id"):
                try:
                    args = json.loads(it.get("arguments") or "{}")
                except ValueError:
                    args = {}
                calls.append({"call_id": it["call_id"], "name": it.get("name") or "",
                              "args": args if isinstance(args, dict) else {}})
        return {"kind": "done", "cost": cost_usd(r.get("usage")), "cancelled": r.get("status") == "cancelled",
                "tool_calls": calls}
    if t == "error":
        err = event.get("error") if isinstance(event.get("error"), dict) else {}
        return {"kind": "error", "no_credit": no_credit_error(err), "unknown_model": unknown_model_error(err),
                "message": str(err.get("message") or "")[:300]}
    return None


# ── audio ────────────────────────────────────────────────────────────────────────────────────────

def resample_16k_to_24k(pcm16: bytes) -> bytes:
    """The browser sends 16 kHz PCM16 (as it does to Gemini Live); Realtime wants 24 kHz. Linear, mono."""
    import array
    a = array.array("h")
    a.frombytes(pcm16[: len(pcm16) - (len(pcm16) % 2)])
    n = len(a)
    if n == 0:
        return b""
    m = int(math.floor(n * 3 / 2))
    out = array.array("h", [0]) * m
    for j in range(m):
        pos = j * 2.0 / 3.0
        i = int(pos)
        frac = pos - i
        x0 = a[i]
        x1 = a[i + 1] if i + 1 < n else x0
        out[j] = int(x0 + (x1 - x0) * frac)
    return out.tobytes()


def level(pcm16: bytes) -> float:
    """RMS 0..1 of the audio about to be played, for the audio-reactive avatar."""
    import array
    a = array.array("h")
    a.frombytes(pcm16[: len(pcm16) - (len(pcm16) % 2)])
    if not a:
        return 0.0
    return min(1.0, math.sqrt(sum(x * x for x in a) / len(a)) / 32768.0 * 4.0)
