import os
import io
import json
import glob
import subprocess
import base64
import secrets
import sys
import traceback
import uuid
import threading
import asyncio
import re
import html
import calendar
import logging

_log = logging.getLogger("friday.routes.voice")
import time as _time
import hashlib as _hashlib
import hmac as _hmac
import queue as _queue
import difflib as _difflib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta
from pathlib import Path
from collections import deque as _deque
from functools import wraps
from flask import (Flask, Blueprint, jsonify, request, send_from_directory,
                   send_file, session, redirect, url_for, Response, stream_with_context)
import agent_friday.core as core
from agent_friday.core import (
    ConnectionClosed,
    FRIDAY_DIR,
    FRIDAY_WS_TOKEN,
    _HTTP_AUTH_KEY,
    TEMP_AUDIO_DIR,
    _api_token_valid,
    _is_local_request,
    _load_agent_personality,
    _load_settings,
    _load_voice_demo,
    _loopback_trusted,
    _network_status,
    _ollama_available,
    login_required,
    process_log,
    process_register,
    process_remove,
    process_update,
    sock,
)  # noqa: E501
from agent_friday.services.agent import (
    _generate_agent,
    _voice_actions_for,
)  # noqa: E501
from agent_friday.services.local_voice import (
    ASR_RATE,
    VADEndpointer,
    get_local_voice_engine,
    split_sentences,
)  # noqa: E501
from agent_friday.services import voice_manifest as _vm

#: The local voice path's log channel. Named `friday.local_voice` so the
#: /ws/voice-local handler's records interleave with the engine's own in
#: ~/.friday/friday.log — one channel for one subsystem, rather than a route
#: log and a service log that have to be correlated by timestamp.
_vlog = logging.getLogger("friday.local_voice")
from agent_friday.services.model_router import (
    _build_emotional_tone_block,
    _build_session_continuity_block,
    _get_friday_system_prompt,
    _get_vault_control,
    _vault_cloud_fallback,
    _vault_local_only,
)  # noqa: E501
from agent_friday.services.voice_engine import (
    LIVE_MODEL_FALLBACK,
    LIVE_MODEL_FALLBACK2,
    LIVE_MODEL_FALLBACK3,
    _VOICE_LIVE_TOOLS,
    _build_voice_live_tools,
    _voice_tool_names,
    _get_live_model,
    _get_live_voice,
    _get_voice_language,
    _get_voice_style_prompt,
    _local_tts_available,
    _model_rejects_thinking_config,
    _model_requires_non_blocking_tools,
    _model_requires_thinking_config,
    _model_supports_affective_dialog,
    _model_supports_proactivity_config,
    _persist_voice_turn,
    _spawn_voice_distill,
    _synthesize_tts_wav,
    _voice_tool_run,
    resolve_gemini_key,
    resolve_live_thinking_level,
    validate_gemini_key,
)  # noqa: E501

voice_bp = Blueprint('voice', __name__)

# Local-voice playback chunk size: ~0.2 s of 24 kHz mono PCM16 per WS frame.
# Small frames keep the friday-pcm-player ring buffer fed smoothly (it absorbs
# bursts), matching how the Gemini path streams audio back.
PLAYBACK_CHUNK_BYTES = 9600

# ── Gemini Live liveness / continuity tuning ──────────────────────────────
# LIVE_STALL_SECONDS: how long the bridge tolerates ZERO traffic from Gemini
# while the user is audibly speaking before it declares the upstream socket
# stalled and renews the session leg. With input transcription enabled Gemini
# streams transcript events *while* the user talks, so sustained speech with a
# silent upstream is definitively a sick connection — and a false positive
# only costs a handle-based renewal the user never hears.
LIVE_STALL_SECONDS = 40
#: A voice tool slower than this is logged as a warning: it is time the
#: caller spent listening to silence.
VOICE_TOOL_SLOW_S = 5.0
# RMS floor that counts a mic chunk as "speech" for the stall watchdog.
# Room noise / speaker echo sits well below this; normal speech well above.
LIVE_SPEECH_RMS = 400
# Server → browser heartbeat period. Lets the CLIENT detect a half-open
# socket (it force-reconnects when nothing arrives for ~3 beats) and lets the
# SERVER notice a dead browser socket even while Gemini is silent.
LIVE_HEARTBEAT_SECONDS = 15.0

# ── Cross-connection Gemini Live resumption cache ─────────────────────────
# When the BROWSER socket drops mid-conversation (wifi blip, sleep/wake, tab
# reload) the client auto-reconnects to /ws/live. Without this cache the new
# handler would start a fresh Gemini session — re-greeting, amnesiac. With it,
# the newest session-resumption handle survives across WS handlers, so a
# reconnect resumes the SAME Gemini conversation server-side and the user
# hears no seam. Single-user app: one global slot, last-writer-wins. Cleared
# on a clean user stop ({type:'end'}) so a deliberate new call starts fresh.
_LIVE_RESUME_TTL_S = 600
_LIVE_RESUME_LOCK = threading.Lock()
_LIVE_RESUME = {"handle": None, "ts": 0.0, "model": None, "voice": None}
# Connection-generation fence. Every /ws/live handler takes the next
# generation number; only the CURRENT generation may write the resume cache
# or renew session legs. A zombie handler on a half-open socket (browser gone,
# TCP hasn't noticed) can therefore neither fight the reconnected handler for
# the Gemini session nor re-pollute the cache after a deliberate stop.
_LIVE_CONN_GEN = [0]


def _live_conn_next():
    with _LIVE_RESUME_LOCK:
        _LIVE_CONN_GEN[0] += 1
        return _LIVE_CONN_GEN[0]


def _live_conn_current(gen):
    with _LIVE_RESUME_LOCK:
        return gen == _LIVE_CONN_GEN[0]


def _live_resume_store(handle, model, voice, gen=None):
    with _LIVE_RESUME_LOCK:
        if gen is not None and gen != _LIVE_CONN_GEN[0]:
            return   # stale handler — never clobber the live conversation's cache
        _LIVE_RESUME.update(handle=handle, ts=_time.time(), model=model, voice=voice)


def _live_resume_clear(gen=None):
    with _LIVE_RESUME_LOCK:
        if gen is not None and gen != _LIVE_CONN_GEN[0]:
            return   # a zombie's late bye/end must not clear the new handler's cache
        _LIVE_RESUME.update(handle=None, ts=0.0, model=None, voice=None)


def _live_resume_load(model, voice):
    """Return a fresh stored resumption handle for this (model, voice), else None."""
    with _LIVE_RESUME_LOCK:
        h = _LIVE_RESUME["handle"]
        if not h:
            return None
        if (_time.time() - _LIVE_RESUME["ts"]) > _LIVE_RESUME_TTL_S:
            return None
        if _LIVE_RESUME["model"] != model or _LIVE_RESUME["voice"] != voice:
            return None
        return h


# ── Barge-in (speaker mode) ───────────────────────────────────────────────
# In "speaker" interruption mode Gemini runs with ActivityHandling.
# NO_INTERRUPTION (the echo-safety fix), which per the Live API reference
# means "The model's response will not be interrupted" — by ANYTHING the
# VAD hears. So barge-in must be implemented by this bridge: detect the user
# deliberately talking over Friday, stop her audio at the source, and cancel
# the in-flight generation. The cancel uses the one documented client-side
# interrupt: "A message here [BidiGenerateContentClientContent] will
# interrupt any current model generation" (ai.google.dev/api/live).
LIVE_BARGE_RMS_FLOOR = 550       # absolute RMS floor for deliberate speech
LIVE_BARGE_BASELINE_MULT = 3.0   # ...and ≥3× the learned speaker-bleed level
LIVE_BARGE_COOLDOWN_S = 1.5      # refractory period between barge firings


class LiveBargeDetector:
    """Echo-aware talk-over detector for open-speaker rigs.

    The failure mode that killed the old client-side detector was speaker
    bleed: Friday's own voice re-captured by the mic tripped a fixed RMS
    threshold and clipped her mid-sentence. This detector is relative, not
    absolute: during the GRACE window at the start of each spoken response it
    only *learns* the bleed level (EMA of chunk RMS — that's what THIS device
    at THIS volume leaks back), and afterwards it fires only on speech that is
    both above an absolute floor AND ≥ `mult`× the learned bleed, sustained
    for `sustain_ms`. Browser AEC (echoCancellation:true) keeps the bleed
    small, so real speech clears the bar; the bleed itself never can, because
    the bar is defined relative to it.

    Single-threaded use (asyncio reader task); `reset_turn()` is called when a
    model response starts speaking, `feed()` per mic chunk while it speaks.
    """

    # Bleed learning is CAPPED: with browser AEC on, real speaker bleed sits
    # well under this, so anything hotter in the grace window is the user
    # already talking (or a truly broken rig) — either way, learning it as
    # "baseline" would raise the bar so high the user could never interrupt.
    BLEED_EMA_CAP = 1500

    def __init__(self, grace_ms=800, sustain_ms=200,
                 floor=LIVE_BARGE_RMS_FLOOR, mult=LIVE_BARGE_BASELINE_MULT):
        self.grace_ms = max(0, float(grace_ms))
        self.sustain_ms = max(0, float(sustain_ms))
        self.floor = float(floor)
        self.mult = float(mult)
        self.started_ts = 0.0
        self.ema = 0.0
        self.sustained = 0.0
        self._grace_samples = []

    def reset_turn(self, now=None):
        """A new model response started speaking — relearn bleed from scratch."""
        self.started_ts = now if now is not None else _time.time()
        self.ema = 0.0
        self.sustained = 0.0
        self._grace_samples = []

    def _learn(self, rms, alpha):
        ema = rms if self.ema <= 0 else (1 - alpha) * self.ema + alpha * rms
        self.ema = min(ema, self.BLEED_EMA_CAP)

    def _seed_from_grace(self):
        # Bleed is CONTINUOUS; user speech during the grace window is bursty.
        # Seeding from the 25th percentile of the grace samples means an
        # interjection over part of the window can't drag the baseline up to
        # the user's own voice level and lock them out — only the quietest
        # quartile (the actual bleed floor) defines the bar.
        if self._grace_samples:
            s = sorted(self._grace_samples)
            self.ema = min(float(s[len(s) // 4]), self.BLEED_EMA_CAP)
            self._grace_samples = []

    def feed(self, rms, chunk_ms, now=None):
        """Feed one mic chunk captured WHILE Friday is speaking.

        Returns True when a deliberate talk-over is confirmed (caller applies
        its own cooldown and fires the interrupt).
        """
        now = now if now is not None else _time.time()
        elapsed_ms = (now - self.started_ts) * 1000.0
        if elapsed_ms < self.grace_ms:
            # Grace window: collect samples, never fire. The baseline is
            # seeded from these AFTER the window (percentile — see above).
            self._grace_samples.append(float(rms))
            self.sustained = 0.0
            return False
        if self._grace_samples:
            self._seed_from_grace()
        threshold = max(self.floor, self.mult * self.ema)
        if rms >= threshold:
            self.sustained += max(0.0, float(chunk_ms))
            return self.sustained >= self.sustain_ms
        # Below the bar: this is bleed/room noise — keep tracking it slowly so
        # volume changes mid-response don't stale the baseline.
        self._learn(rms, alpha=0.1)
        self.sustained = 0.0
        return False


def _quick_rms(pcm):
    """Cheap RMS of a PCM16-LE chunk (samples ≤160 points) for speech gating."""
    n = len(pcm) // 2
    if n == 0:
        return 0
    import struct as _st
    step = max(1, n // 160)
    total = 0
    count = 0
    for i in range(0, n, step):
        s = _st.unpack_from('<h', pcm, i * 2)[0]
        total += s * s
        count += 1
    return int((total / max(1, count)) ** 0.5)


def _duration_to_seconds(v):
    """Best-effort parse of a Live API duration ('5s', timedelta, number) → float seconds."""
    try:
        if v is None:
            return None
        if hasattr(v, 'total_seconds'):
            return float(v.total_seconds())
        if isinstance(v, (int, float)):
            return float(v)
        return float(str(v).strip().rstrip('s'))
    except Exception:
        return None


def _classify_live_error(err_str):
    """Classify a Gemini Live connect/session error.

    Returns 'model-missing' | 'auth' | 'other'. The Live API closes with code
    1008 for BOTH bad credentials AND unknown/retired model ids, so a close
    code alone must never be read as an auth verdict — that misdirection
    ("rotate your key") once cost days when the real problem was a retired
    fallback model id. Match on the message text instead.
    """
    el = (err_str or "").lower()
    if ("not found for api version" in el
            or "not supported for bidigeneratecontent" in el
            or ("model" in el and "was not found" in el)):
        return "model-missing"
    if ("api key not valid" in el or "api_key_invalid" in el
            or "api key expired" in el or "expected oauth" in el
            or "unauthenticated" in el or "permission denied" in el
            or "permission_denied" in el
            or ("1008" in el and ("auth" in el or "api key" in el
                                  or "oauth" in el))):
        return "auth"
    return "other"


def _compose_final_voice_error(attempt_errors, key_source):
    """One truthful, actionable error line after every connect attempt failed.

    attempt_errors: [(model, api_version, kind, message), ...]. When any
    attempt was auth-flavored the key is re-validated over REST (force) so the
    message can say definitively whether the KEY is bad (and which source it
    came from) or whether auth is fine and the MODEL id is the problem.
    """
    kinds = [k for _m, _a, k, _e in attempt_errors]
    models_tried = ", ".join(dict.fromkeys(m for m, _a, _k, _e in attempt_errors))
    if "auth" in kinds:
        key = core.GEMINI_API_KEY or ""
        try:
            ok, detail = validate_gemini_key(key, force=True)
        except Exception as _ve:
            ok, detail = False, f"validation error: {_ve}"
        if ok:
            return (f"Gemini Live refused the connection with an auth-style error, "
                    f"but the key (from {key_source}) passes a REST "
                    f"check — so the configured voice model is likely stale or "
                    f"renamed. Models tried: {models_tried}. Pick a current Live "
                    f"model in Settings → Voice.")
        return (f"Gemini API key invalid or revoked (loaded from "
                f"{key_source}; Google says: {detail}). Update the key at that "
                f"source, or paste a fresh key from aistudio.google.com into "
                f"Settings → Accounts & Keys → Google Gemini — it takes effect on the "
                f"next voice session, no restart needed.")
    if kinds and all(k == "model-missing" for k in kinds):
        # Don't recommend a specific model here: every ID in our own chain was
        # just tried and failed, so naming one would send the user in a circle.
        return (f"No configured voice model is available on this API tier "
                f"(tried: {models_tried}) — this is a MODEL problem, not an "
                f"API-key problem. Open Settings → Voice and pick a different "
                f"Live model — or clear the Voice model setting to use the "
                f"built-in verified default.")
    first = attempt_errors[0] if attempt_errors else ("?", None, "other", "unknown error")
    return f"Voice connect failed on {first[0]}: {str(first[3])[:300]}"


# Spoken tool choreography — injected into BOTH voice session prompts (Gemini
# Live and local). Without it the model calls tools mid-sentence: the action's
# tts_pause lands while she's speaking, the fetch blocks with dead air, and the
# turn resumes incoherently — the on-stage "freeze then stutter". The contract
# is announce → act → confirm, one action at a time.
#: Default ceiling on a SPOKEN reply, in tokens. ~300 tokens is ~225 words is
#: ~90 seconds of speech — a generous backstop, not the target (the target is
#: the LENGTH paragraph in the voice prompt, which asks for under 60 words).
_VOICE_REPLY_TOKENS_DEFAULT = 300


def _voice_reply_cap(settings=None) -> int:
    """How many tokens a single spoken turn may generate.

    THIS IS NOT ONLY A BREVITY DIAL — it is what keeps the local seat alive.

    The local voice path called ``_generate_agent`` without a cap, so it
    inherited that function's TEXT default of 16,384. llama.cpp counts the
    requested generation against the context window, so a voice turn asked the
    seat for:

        13,669 (voice prompt = the full text-chat system prompt)
      + 11,131 (all 67 tool schemas, which the tool budgeter passed as fitting)
      + 16,384 (the unset reply cap)
      = 41,184 tokens against a served window of 32,768

    and gemma4:12b answers ``500 Context size has been exceeded``
    (reproducible on demand). Because a vault-touching turn is
    forbidden from retrying on a cloud provider, that 500 was the whole turn:
    Friday could not call a single tool. The identical request with this cap at
    300 succeeded in 19 seconds and called tools normally.

    So "voice can't call tools" and "voice drones on" were ONE defect wearing
    two faces — an unset spoken-reply length. Keep this small. A voice reply
    that needs thousands of tokens is a background task, not a spoken turn.
    """
    try:
        s = settings if settings is not None else (_load_settings() or {})
        n = int(s.get("voice_max_tokens") or 0)
    except Exception:
        n = 0
    # 0 means "unset" in settings today (the Gemini path reads it the same way)
    # and MUST NOT mean "unlimited" here, which is the bug above.
    if n <= 0:
        return _VOICE_REPLY_TOKENS_DEFAULT
    # A cap larger than the seat can hold is the same defect with a nicer name.
    return max(64, min(n, 2048))


async def _run_calls_concurrently(calls, one):
    """Run `one(call)` for every call at once; results in call order.

    Gemini waits for every function response in a tool-call message before it
    speaks, so the silence is the slowest call when they run together and the
    SUM of them when they run one after another (a 9 s mail check followed by a
    31 s news search was 40 s of dead air). A call that raises is logged and
    dropped; the others still answer. `None` results are dropped too.
    """
    got = await asyncio.gather(*(one(c) for c in calls), return_exceptions=True)
    out = []
    for g in got:
        if isinstance(g, BaseException):
            _log.error("voice tool call crashed: %s", g)
        elif g is not None:
            out.append(g)
    return out


#: No voice tool may hold a live call longer than this. Past it the model is
#: told plainly that nothing came back; the worker thread finishes on its own
#: (a deep-dive still lands in its cache for the next ask).
VOICE_TOOL_HARD_LIMIT_S = 20.0
#: A result slower than this is "late": the conversation has likely moved on.
VOICE_TOOL_STALE_S = 15.0


async def _voice_tool_with_limit(fname, fargs, send, session=None, limit=None, runner=None):
    """Run one voice tool on a worker thread, bounded by VOICE_TOOL_HARD_LIMIT_S."""
    runner = runner or _voice_tool_run
    limit = VOICE_TOOL_HARD_LIMIT_S if limit is None else limit
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(runner, fname, fargs, send, session), limit)
    except asyncio.TimeoutError:
        _log.warning("voice tool %s passed its %gs limit; answered without a result",
                     fname, limit)
        return (f"The {fname} tool did not finish within {limit:g} seconds, so "
                f"there is no result. Tell the user in one short sentence that it "
                f"is taking too long, and offer to try again or do something else. "
                f"Do not guess at what it would have said.")


#: A reconnect shorter than this carries the user's words across; a longer
#: one drops them, since an answer to half-minute-old speech is the "two
#: parallel conversations" failure the seam drain exists to prevent.
SEAM_REPLAY_MAX_GAP_S = 10.0
#: At most this much of the newest buffered mic audio is replayed.
SEAM_REPLAY_MAX_AUDIO_S = 10.0


def _leg_config(types, per_model_kwargs, handle=None):
    """The LiveConnectConfig for one leg of a call.

    Every leg, including a resumed one, is built from the same kwargs, so the
    system instruction (with the persona) and the context-window compression
    setting are sent again in full on each reconnect; resumption only adds the
    handle. Within a leg the bridge also repeats the persona as a note every
    few turns (voice_persona.persona_due), so holding the character does not
    depend on what the server's compression keeps.
    """
    kw = dict(per_model_kwargs)
    if handle:
        kw["session_resumption"] = types.SessionResumptionConfig(handle=handle)
    return types.LiveConnectConfig(**kw)


def _seam_replay_plan(heard_text, answered, seam_chunks, gap_s):
    """What to carry into a renewed Gemini leg: (unanswered words, mic chunks).

    heard_text: the user's transcribed words since Friday's last turn ended.
    answered: whether Friday had begun replying to them.
    seam_chunks: 16 kHz PCM16 mic chunks the browser sent during the reconnect.
    gap_s: seconds since the previous leg closed; None when unknown.
    """
    if gap_s is None or gap_s > SEAM_REPLAY_MAX_GAP_S:
        return None, []
    text = (heard_text or "").strip()
    text = text if (text and not answered) else None
    budget = int(SEAM_REPLAY_MAX_AUDIO_S * 32000)      # PCM16 @ 16 kHz
    keep, total = [], 0
    for c in reversed(seam_chunks or []):
        if total + len(c) > budget:
            break
        keep.append(c)
        total += len(c)
    keep.reverse()
    if not any(_quick_rms(c) >= LIVE_SPEECH_RMS for c in keep):
        keep = []                                       # silence is not worth replaying
    return text, keep


def _mark_if_stale(result, fname, started_at, user_spoke_at, now):
    """Prefix a result that arrives after the conversation has moved on.

    A tool call runs while the conversation continues, so a slow answer can
    land after the user has asked something else, and the model then answers
    the old question as if it were the new one. A result is late when it took
    longer than VOICE_TOOL_STALE_S, or took longer than VOICE_TOOL_SLOW_S and
    the user spoke after the call began.
    """
    took = now - started_at
    spoke_since = user_spoke_at > started_at + 2.0
    if took <= VOICE_TOOL_STALE_S and not (spoke_since and took > VOICE_TOOL_SLOW_S):
        return result
    why = ("the user has spoken since you asked for it" if spoke_since
           else f"it took {took:.0f} seconds")
    return (f"[LATE RESULT for {fname}: {why}, so the conversation may have moved on. "
            f"Do NOT answer an earlier question with it or bring it up unprompted. "
            f"If the user's current topic is this, use it; otherwise say in one short "
            f"sentence that it is ready if they want it.]\n{result}")


# The persona, the adaptive length rule, the news rules and the vault-aware
# personal-questions rule are shared with the briefings: services/voice_persona.py.
from agent_friday.services.voice_persona import (  # noqa: E402
    VOICE_ANCHOR_RULES, VOICE_LENGTH_RULE, compose_live_instruction,
    persona_due, persona_reminder, strip_text_chat_hints, vault_rule,
)

# Several people may be in the room, and the live model answers every
# utterance it hears.
VOICE_CROSSTALK_RULE = (
    "PEOPLE TALKING TO EACH OTHER: There may be more than one person in the room. "
    "If what you hear is people talking to each other, a fragment, a single word, "
    "or background speech that is not addressed to you, do not answer it: stay "
    "silent and wait. Answer when someone speaks to you (by name, or with a "
    "question or request clearly meant for you), or when they are replying to "
    "something you just asked.\n"
)

# The live model's own speech recognition sometimes hears English as another
# language and then answers in it.
VOICE_ENGLISH_RULE = (
    "LANGUAGE: Always speak {language}. If what you heard looks like another "
    "language, it is almost certainly {language} misheard: answer in {language}, "
    "and if it made no sense, ask them to say it again.\n"
)

#: The language the live session hears and speaks when none is configured.
VOICE_DEFAULT_LANGUAGE = "en-US"
_LANGUAGE_NAMES = {"en": "English", "fr": "French", "es": "Spanish", "de": "German",
                   "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "ja": "Japanese",
                   "ko": "Korean", "zh": "Chinese", "hi": "Hindi", "ar": "Arabic"}


def _live_language(configured) -> tuple:
    """(BCP-47 code, spoken name) for the live session; English unless set."""
    code = str(configured or "").strip() or VOICE_DEFAULT_LANGUAGE
    return code, _LANGUAGE_NAMES.get(code.split("-")[0].lower(), code)


def _addressed(user_text, friday_last_text, since_friday_s, window_s=10.0) -> bool:
    """In a room of several people: was this said to Friday?

    Yes when it names her, when she just asked a question, or when it follows
    her last reply closely enough to be an answer to it.
    """
    if "friday" in (user_text or "").lower():
        return True
    if since_friday_s is None:
        return False
    if (friday_last_text or "").rstrip().endswith("?") and since_friday_s <= 30.0:
        return True
    return since_friday_s <= window_s


VOICE_TOOL_CHOREOGRAPHY = (
    "TOOL CHOREOGRAPHY (voice): Before EVERY tool call, first finish speaking "
    "one short sentence announcing what you're about to do — for example "
    "'Pulling that story up now.' — and END the sentence before invoking the "
    "tool. Never call a tool mid-sentence. While the tool runs, stay silent; "
    "do not narrate progress. When the result comes back, confirm completion "
    "in one short sentence ('Okay, it's up on screen.') before discussing the "
    "result. One action at a time — finish announcing and confirming each "
    "before starting the next.\n\n"
)


def _voice_tool_surface_note():
    """The authoritative list of tools the LIVE VOICE session actually has.

    The voice system prompt is assembled from ``_get_friday_system_prompt()`` --
    the TEXT-CHAT prompt -- whose "== AVAILABLE TOOLS ==" section advertises the
    full text toolbox (read_file, write_file, run_command, browse_web,
    search_email, draft_email, open_path, learn_skill, query_trust_graph,
    get_briefing, the OS-control ring ...). The Live API is handed only
    ``_VOICE_LIVE_TOOLS``. Every tool named in the prompt but absent from that
    list is uncallable, and a voice model that believes it has a tool it cannot
    invoke does the one thing left to it: it narrates the action ("Pulling those
    numbers up now.") and nothing happens. That is indistinguishable from a lie.

    This block is appended LAST, after the text-chat context, so it wins: it
    replaces the advertised inventory with the real one and says plainly what to
    do about everything else.
    """
    try:
        # Generated from the RESOLVED surface -- the native table plus whatever
        # of _VOICE_SHARED_TOOLS actually resolved out of the text registry --
        # so this block cannot name a tool the Live API was not handed, which
        # is the whole failure it exists to prevent.
        names = _voice_tool_names()
    except Exception:
        names = []
    header = (
        "\n\n=== VOICE TOOL SURFACE (OVERRIDES ANY TOOL LIST ABOVE) ===\n"
    )
    if not names:
        return (
            header +
            "You have NO callable tools in this voice session. Any tool named "
            "earlier in this prompt is unavailable here. Never announce an action "
            "you cannot perform - say plainly that you can't do it in voice and "
            "offer to handle it in text chat instead.\n\n"
        )
    return (
        header +
        "This is a LIVE VOICE session. The tool list in the '== AVAILABLE TOOLS =='\n"
        "section above describes the TEXT CHAT surface and does NOT apply here.\n"
        "In voice you can call EXACTLY these " + str(len(names)) +
        " tools, and nothing else:\n"
        + "".join("  \u2022 " + n + "\n" for n in names) +
        "\n"
        "Anything named in that section but NOT in the list above - running "
        "shell commands, browsing a URL's full text, drafting email, editing "
        "the wiki, learning skills, the trust graph, briefings, the clipboard, "
        "or mouse and keyboard control - is NOT callable in voice.\n\n"
        "THE RULE: never announce an action you cannot actually take. If the user "
        "asks for something outside the list above, say so in one plain sentence "
        "(\"I can't draft email from voice mode - want me to start that as a "
        "background task instead?\") and offer the nearest thing you CAN do. "
        "Saying 'pulling that up now' or 'checking my records' when no tool ran "
        "is a fabrication, exactly as bad as inventing the result.\n\n"
        "THE FILE TOOLS ARE REAL AND THEY RUN ON THIS MACHINE. read_file, "
        "write_file and open_path reach the user's actual disk - absolute paths "
        "(C:\\...) or ~/ paths. Writing a briefing to the desktop and then "
        "opening it is TWO calls, write_file then open_path with in_browser=true; "
        "open_url is for web pages only and will refuse a local path. Never "
        "invent an http:// URL to stand in for a file you just wrote. If a call "
        "comes back with an error or a refusal, report THAT - do not describe the "
        "file as though it appeared.\n\n"
        "screenshot needs Computer Control switched on in Settings. If it comes "
        "back denied, say the permission is off and offer to walk the user to the "
        "toggle. Do not say you took one.\n\n"
        "FOR ANYTHING SUBSTANTIAL, USE spawn_task. Research, analysis, drafting, "
        "monitoring, or any multi-step job belongs in a background task. Call "
        "spawn_task with a clear title and a full instruction, then tell the user "
        "it is running in the Task Tray. Do not narrate the work as though you are "
        "doing it inline - in voice you are not.\n\n"
    )


def _voice_orb_start(fname):
    """Register the process orb for one live-voice tool call.

    The orb is the user-visible EXECUTION RECEIPT. The text agent loop has
    emitted one per tool call for a long time (services/agent.py
    ``_orb_tool_trace``); the Gemini Live path emitted none, which meant the
    strongest signal the user had -- "no orb appeared, so nothing ran" -- was
    not actually load-bearing on this surface. It is now: every call that
    reaches ``_voice_tool_run`` registers one, so an announced action with no
    orb is narration, provably.

    Returns the orb id, or None if registration failed (callers treat None as
    "no orb" and carry on -- a receipt that cannot be written must never cost
    the user the action).
    """
    try:
        from agent_friday.services.agent import _tool_orb_meta
        category, icon, _label = _tool_orb_meta(fname)
    except Exception:
        category, icon = "default", "⚡"
    orb_id = f"voice-{uuid.uuid4().hex[:8]}"
    try:
        process_register(orb_id, name="Friday (voice)", label=fname,
                         category=category, icon=icon, steps=[],
                         model=_get_live_model())
        return orb_id
    except Exception:
        return None


def _voice_orb_finish(orb_id, fname, fargs, result, duration_ms):
    """Close the orb with the call's outcome, tier-redacted.

    Status is classified by the SAME sentinels the text path uses, so a denied
    or failed voice tool reads as denied or failed in the tray rather than as a
    success. Args and results go through ``_tier_safe_summary`` because
    /api/processes is readable without the vault.
    """
    if not orb_id:
        return
    try:
        from agent_friday.services.agent import (_tool_call_status,
                                                 _tier_safe_summary)
        status = _tool_call_status(result)
        process_log(orb_id, f"tool {fname} -> {status} ({int(duration_ms)}ms)")
        process_update(orb_id, progress=1.0,
                       status="completed" if status == "ok" else "error",
                       step={"type": "tool", "name": fname, "status": status,
                             "args": _tier_safe_summary(fargs, kind="args"),
                             "result": _tier_safe_summary(result, kind="result"),
                             "duration_ms": int(duration_ms),
                             "ts": _time.time()},
                       result=_tier_safe_summary(result, limit=400, kind="result"))
    except Exception:
        pass
    # Leave it on screen briefly so a sub-second tool call is still SEEN --
    # an orb that appears and vanishes inside one frame is not a receipt.
    try:
        threading.Timer(6.0, lambda: process_remove(orb_id)).start()
    except Exception:
        pass


# What the live.text path sends to Gemini in place of a withheld/blocked
# typed message. Local to voice.py — egress_gate._MESSAGE_WITHHELD is worded
# for a chat *turn* ("their last message"); this is worded for a live voice
# reply reading it back to the user who just typed it.
_LIVE_TEXT_WITHHELD = ("[message withheld — this contained sensitive content "
                       "that stays on this device; switch to a local model to "
                       "discuss it]")

# What either voice-egress path sends when the gate module itself could not
# be reached at all (import failure). Distinct from a normal gate verdict so
# the log/behavior difference is visible if it ever fires.
_GATE_UNREACHABLE_TOOL = ("[tool result withheld — the privacy gate could not "
                          "be reached, so nothing was sent to the cloud]")
_GATE_UNREACHABLE_TEXT = ("[message withheld — the privacy gate could not be "
                          "reached, so nothing was sent to the cloud]")


def _gate_voice_tool_result(result: str, fname: str) -> str:
    """Gate a voice tool result before it reaches Gemini. FAIL-CLOSED.

    Every exit is either the gated text or an explanatory withheld
    placeholder — NEVER the raw `result` passed in. Wrapping the whole gate
    call in one broad `except Exception: <log and fall through>` is the
    defect this guards against: `_gate_text` raises
    `egress_gate.NeverSendBlocked` for never-send material BY DESIGN (see its
    docstring) — so the gate's strongest verdict was exactly the case that
    fell through to the pre-gate, ungated `result`. A gate that fails open on
    its own escalation is worse than no gate.
    """
    try:
        from agent_friday.services import egress_gate as _eg
    except Exception as _ie:
        _log.error("voice tool-result gating module unavailable for %s: %s — "
                  "withholding rather than sending ungated", fname, _ie)
        return _GATE_UNREACHABLE_TOOL
    try:
        gated = _eg._gate_text(result, "google-gemini", f"live.tool.{fname}")
        if result and not gated:
            gated = _eg._TOOL_RESULT_WITHHELD
        return gated
    except _eg.NeverSendBlocked as _nb:
        _log.warning("voice tool-result NEVER-SEND blocked for %s: %s", fname, _nb)
        return _eg._TOOL_RESULT_WITHHELD
    except Exception as _ge:
        _log.warning("voice tool-result gating unavailable for %s: %s — "
                    "withholding rather than sending ungated", fname, _ge)
        return _eg._TOOL_RESULT_WITHHELD


def _gate_voice_text(text: str) -> str:
    """Gate a typed message before it reaches Gemini over live.text.
    FAIL-CLOSED — same shape and same fix as `_gate_voice_tool_result` (N-1's
    sibling: the old code had a bare `except Exception: pass` here, which
    left the ungated typed message to be sent as-is)."""
    try:
        from agent_friday.services import egress_gate as _eg
    except Exception as _ie:
        _log.error("voice live.text gating module unavailable: %s — "
                  "withholding rather than sending ungated", _ie)
        return _GATE_UNREACHABLE_TEXT
    try:
        gated = _eg._gate_text(text, "google-gemini", "live.text")
        return gated if gated else _LIVE_TEXT_WITHHELD
    except _eg.NeverSendBlocked as _nb:
        _log.warning("voice live.text NEVER-SEND blocked: %s", _nb)
        return _LIVE_TEXT_WITHHELD
    except Exception as _ge:
        _log.warning("voice live.text gating unavailable: %s — withholding "
                    "rather than sending ungated", _ge)
        return _LIVE_TEXT_WITHHELD


# security-boundary.md §19 row 1: what the model sees in place of a fully
# withheld Live system instruction (a whole-payload never-send match). The
# session still opens — refusing to connect at all would be a worse failure
# than a degraded one — but Friday's own words say so, not a fabrication.
_VOICE_SYS_INSTRUCTION_WITHHELD = (
    "You are Agent Friday, a sovereign personal AI assistant having a live "
    "voice conversation. Your full personal context could not be sent to "
    "this cloud voice provider because it contained content on the user's "
    "never-send list. Tell the user their personal context is only "
    "available through local processing right now, and that switching to a "
    "local model would let you discuss it. Continue the conversation "
    "normally for anything else."
)


def _gate_voice_system_instruction(sys_text: str) -> str:
    """Gate the Live voice system instruction before Google ever sees it.

    security-boundary.md §19 row 1: this is the assembled context prompt —
    personality, self-knowledge, and (when the vault gate is on) TIER-gated
    vault material — going straight to Google as `system_instruction=`. The
    vault-assembly gate (`_get_vault_control()`) only runs when
    `vault_local_only` is true; with it false, `_get_friday_system_prompt`
    assembles ungated, so this gate is the only thing standing between the
    result and Google — the same egress-gate call the sibling tool-result
    and live.text paths just above make. FAIL-CLOSED, same shape as those:
    any gate exception,
    including NeverSendBlocked, withholds rather than sends the raw prompt.
    """
    try:
        from agent_friday.services import egress_gate as _eg
    except Exception as _ie:
        _log.error("voice system-instruction gating module unavailable: %s — "
                  "withholding rather than sending ungated", _ie)
        return _VOICE_SYS_INSTRUCTION_WITHHELD
    try:
        gated = _eg._gate_text(sys_text, "google-gemini", "voice_system_instruction")
        return gated if gated else _VOICE_SYS_INSTRUCTION_WITHHELD
    except _eg.NeverSendBlocked as _nb:
        _log.warning("voice system-instruction NEVER-SEND blocked: %s", _nb)
        return _VOICE_SYS_INSTRUCTION_WITHHELD
    except Exception as _ge:
        _log.warning("voice system-instruction gating unavailable: %s — "
                    "withholding rather than sending ungated", _ge)
        return _VOICE_SYS_INSTRUCTION_WITHHELD


def _record_mic_audio_egress(event: str, byte_len: int = 0) -> None:
    """security-boundary.md §19 row 2: Live microphone audio streamed to
    Gemini with NO ledger row of any kind — contrast routes/chat.py:341,
    which at least records binary egress for an uploaded image. PCM audio
    cannot be text-classified, so this is not a gate; it is the same
    `record_binary_egress` primitive every other binary path already uses,
    making the send itself (and its size) part of the one file that is
    supposed to enumerate everything that left the machine.

    `event` is "open" (session start, 0 bytes — the session existing at all
    is the fact worth recording) or "close" (session end, `byte_len` the
    bytes actually forwarded this leg). Never raises: a ledger failure must
    not take the voice session down, but it must not pretend to have
    written a row either — that is why this logs rather than swallowing.
    """
    try:
        from agent_friday.services import egress_gate as _eg
    except Exception as _ie:
        _log.error("mic-audio ledger unavailable (module import failed): %s", _ie)
        return
    reason = ("live voice session opened" if event == "open"
              else "live voice session closed")
    try:
        _eg.record_binary_egress("google-gemini", "mic_audio", action="allow",
                                 reason=reason, byte_len=byte_len)
    except Exception as _re:
        _log.warning("mic-audio ledger row failed (%s): %s", event, _re)


#: How long speech must last before it counts as the user starting to talk
#: (the Live API's prefix_padding_ms). A cough, a one-word aside or a fragment
#: of someone else's conversation is shorter than this, so it neither starts a
#: turn nor cuts Friday off; a real interruption is sustained. "room" (several
#: people talking, not all to Friday) asks for longer.
VOICE_SPEECH_START_MS = {"one": 400, "room": 700}


def _voice_room_mode(settings) -> str:
    """'room' when several people are talking in the room, else 'one'."""
    v = str((settings or {}).get("voice_room_mode") or "one").strip().lower()
    return "room" if v == "room" else "one"


def _build_realtime_input_config(types, interruption_mode="auto", room_mode="one"):
    """Build the Live API RealtimeInputConfig.

    Barge-in is ON by default. Per Google's current Live API docs
    (ai.google.dev/gemini-api/docs/live-api/capabilities), the barge-in
    behavior is activity_handling = START_OF_ACTIVITY_INTERRUPTS: the start of
    user speech interrupts the model's response. NO_INTERRUPTION means VAD
    still fires but the model never stops — which reads to the user as "voice
    won't interrupt". We only use NO_INTERRUPTION for an EXPLICIT opt-out
    ("speaker-safe" / "no-barge") chosen by users on open speakers who get
    spurious self-interruption from echo the browser AEC can't fully cancel.

    Interruptible modes still lean on echo mitigations to avoid Friday cutting
    herself off: LOW start sensitivity (her own speaker bleed is quieter than a
    real user, so require clearer speech to trip VAD) and the client's
    echoCancellation:true mic constraint. turn_coverage =
    TURN_INCLUDES_ONLY_ACTIVITY counts only detected speech.

    `activity_handling` / `turn_coverage` are only set when the installed
    google-genai SDK exposes the enums, so older SDKs degrade to VAD-only.
    """
    mode = str(interruption_mode or "").strip().lower()
    # Explicit no-barge opt-out. Everything else (auto/speaker/headphones/…)
    # is interruptible — barge-in is the default the requirement asks for.
    no_barge = mode in ("no-barge", "no_barge", "nobarge", "speaker-safe",
                        "speaker_safe", "none", "off")
    aad = types.AutomaticActivityDetection(
        disabled=False,
        silence_duration_ms=800,
        prefix_padding_ms=VOICE_SPEECH_START_MS.get(room_mode, VOICE_SPEECH_START_MS["one"]),
        # LOW start sensitivity: require louder/clearer speech to trip VAD.
        # Friday's own speaker bleed (echo) is quieter than a real user, so LOW
        # makes the server less likely to mistake echo for the start of a turn.
        start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_LOW,
        end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_HIGH,
    )
    kwargs = {"automatic_activity_detection": aad}

    # activity_handling — barge-in unless the user explicitly opted out.
    _ah = getattr(types, "ActivityHandling", None)
    if _ah is not None:
        member = "NO_INTERRUPTION" if no_barge else "START_OF_ACTIVITY_INTERRUPTS"
        val = getattr(_ah, member, None)
        if val is not None:
            kwargs["activity_handling"] = val

    # turn_coverage — only count actual speech activity, not silence/noise.
    _tc = getattr(types, "TurnCoverage", None)
    if _tc is not None:
        val = getattr(_tc, "TURN_INCLUDES_ONLY_ACTIVITY", None)
        if val is not None:
            kwargs["turn_coverage"] = val

    try:
        return types.RealtimeInputConfig(**kwargs)
    except Exception:
        # Older SDK without activity_handling / turn_coverage — VAD only.
        return types.RealtimeInputConfig(automatic_activity_detection=aad)


@voice_bp.route('/api/voice/tts', methods=['POST'])
def tts():
    """Text-to-speech using Gemini 2.5 Flash TTS model — returns WAV binary directly.

    Default voice is "Aoede" (warm female). Callers can override via `voice`
    in the JSON body. The text is wrapped with a conversational style hint so
    the model delivers it as a news anchor rather than reading robotically.
    """
    try:
        text = request.json.get('text', '')
        if not text:
            return jsonify({"status": "error", "message": "No text provided"}), 400
        buf = _synthesize_tts_wav(
            text,
            voice=request.json.get('voice'),
            style=request.json.get('style', 'briefing'),
        )
        return send_file(buf, mimetype='audio/wav')

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@voice_bp.route('/api/audio/<path:filename>')
def serve_audio(filename):
    return send_from_directory(str(TEMP_AUDIO_DIR), filename)


@voice_bp.route('/api/voice/fallback-status')
def voice_fallback_status():
    """Report what voice capabilities are available given the current network.

    The UI uses this to decide whether voice can run when offline: cloud Gemini
    Live needs connectivity, but local pyttsx3 TTS + Ollama keep a degraded
    spoken experience working on-device. Returns the network state plus
    `cloud_voice`, `local_tts`, and `local_llm` flags and a recommended mode.
    """
    net = _network_status()
    cloud_voice = bool(core.GEMINI_API_KEY) and not net.get("offline")
    local_tts = _local_tts_available()
    local_llm = _ollama_available()
    if cloud_voice:
        mode = "cloud"          # full Gemini Live (best)
    elif local_tts and local_llm:
        mode = "local"          # pyttsx3 readback + Ollama reasoning
    elif local_tts:
        mode = "tts_only"       # can speak canned text, no local reasoning
    else:
        mode = "unavailable"
    return jsonify({
        "status": "ok",
        "network": net,
        "cloud_voice": cloud_voice,
        "local_tts": local_tts,
        "local_llm": local_llm,
        "recommended_mode": mode,
        "voice_model": _get_live_model(),
    })


def _local_brain_ready() -> bool:
    """True when a local generation seat is actually resident and can answer.

    Deliberately NOT `_ollama_available()`, which asks whether the daemon
    socket accepts a connection. The daemon being up says nothing about whether
    any model is loaded — that is the gap that let a voice session transcribe
    speech and then fall silent. This asks the residency layer to name a seat,
    and a name it will not give is the honest answer that local voice cannot
    run right now.
    """
    try:
        from agent_friday.services import local_seats as _seats
        # "brain" is the ROLE token _ROLE_TO_CAPABILITY maps to the
        # "reasoning" capability -- passing "reasoning" itself isn't a
        # valid role, so _configured() returned None immediately and this
        # never consulted the user's actual orchestrator model.
        return bool(_seats.resolve("brain"))
    except Exception:
        return False


#: Tool names that put the user's OWN context in reach of the voice model.
#: The local path exposes the core surface (knowledge_query among ~75); the
#: cloud path's fixed table has neither. Checked by name so the answer tracks
#: the tables rather than a comment about them.
_KG_TOOL_NAMES = ("knowledge_query",)
_MEMORY_TOOL_NAMES = ("memory_recall", "recall_memory", "search_memory",
                      "memory_search", "remember")


def _local_mind_proven() -> bool:
    """Is the local mind PROVEN right now (clean-sheet §3.1), not merely
    configured? The `ask_friday` relay only reaches the user's context when
    there is a local model to relay to. Never raises; unknown reads as not
    proven, because a relay that may not exist is not reach."""
    try:
        from agent_friday.services import voice_manifest as _vm
        return bool(_vm.get_manifest().snapshot_stage("mind").get("ready"))
    except Exception:
        return False


def _voice_context_reach(engine, tool_names=None, local_mind_ready=None, vault_open=None):
    """F3 of voice-mode-diagnosis-and-repair.md: can the model in this voice
    loop call tools and reach the knowledge graph / memory? Said at session
    start, in one line, instead of letting a cloud session run a degraded loop
    that triages email fine and then cannot answer a question about the
    user's own notes. Never raises.

    `local_mind_ready` lets a caller (or a test) state whether the local mind
    is proven; None asks the manifest. The relay through `ask_friday` is real
    reach ONLY when that is true -- saying "full context via the local model"
    in the same payload whose manifest says the local model is not available
    is the self-description lie §3.1 exists to make impossible.

    `vault_open` (None reads model_routing.vault_local_only) keeps the HUD in
    step with what the model is told: with the vault open to cloud sessions an
    unready local model closes only memory and the knowledge graph, not the
    user's notes.
    """
    engine = str(engine or "local").strip().lower()
    if engine == "gemini":
        names = tool_names
        if names is None:
            try:
                names = list(_voice_tool_names())
            except Exception:
                names = []
        names = [str(n) for n in names]
        kg = any(n in names for n in _KG_TOOL_NAMES)
        mem = any(n in names for n in _MEMORY_TOOL_NAMES)
        # Clean-sheet §4.5 (D7): `ask_friday` reaches the knowledge graph and
        # memory THROUGH the local model. That is real reach, said honestly:
        # the line names the relay rather than claiming the tools directly.
        relay = "ask_friday" in names
        full = bool(names) and ((kg and mem) or relay)
        if not names:
            notice = ("Cloud voice (Gemini Live) is running with NO tools this "
                      "session: it can talk, but cannot check email, search, or "
                      "reach your knowledge graph or memory.")
        elif relay and not (kg and mem):
            mind_ok = (bool(local_mind_ready) if local_mind_ready is not None
                       else _local_mind_proven())
            if mind_ok:
                return {"engine": "gemini", "tool_capable": True,
                        "tools": len(names), "knowledge_graph": True, "memory": True,
                        "full_context": True, "via_local": True,
                        "line": (f"{len(names) - 1} native tools + ask_friday → your "
                                 "context is reached through Friday's local model"),
                        "notice": ""}
            # The relay exists but has nothing to relay to. Say that, in the
            # same words the manifest's describe_for_model() uses, so the
            # HUD and the model agree.
            if vault_open is None:
                try:
                    vault_open = not _vault_local_only()
                except Exception:
                    vault_open = False
            if vault_open:
                return {"engine": "gemini", "tool_capable": True,
                        "tools": len(names), "knowledge_graph": False, "memory": False,
                        "full_context": False, "via_local": False, "vault_open": True,
                        "line": (f"{len(names) - 1} native tools + ask_friday; Friday's "
                                 "local model is not running, so memory and the "
                                 "knowledge graph are out of reach, but your vault is "
                                 "open to this session: your notes reach it through its "
                                 "context and wiki search"),
                        "notice": ("Cloud voice (Gemini Live) is running. Friday's local "
                                   "model is not available right now, so it cannot reach "
                                   "your memory or knowledge graph; your vault is open to "
                                   "cloud sessions, so your notes are available through "
                                   "its context and wiki search.")}
            return {"engine": "gemini", "tool_capable": True,
                    "tools": len(names), "knowledge_graph": False, "memory": False,
                    "full_context": False, "via_local": False,
                    "line": (f"{len(names) - 1} native tools + ask_friday, but "
                             "Friday's local model is not proven right now — "
                             "your notes, memory and knowledge graph are out of "
                             "reach"),
                    "notice": ("Cloud voice (Gemini Live) is running, but Friday's "
                               "local model is not available right now, so it "
                               "cannot reach your knowledge graph or memory. Open "
                               "Settings → Voice to prove the local model, or "
                               "switch to local voice.")}
        elif not full:
            missing = [w for w, ok in (("knowledge graph", kg), ("memory", mem)) if not ok]
            notice = (f"Cloud voice (Gemini Live) runs with {len(names)} fixed "
                      f"tools and cannot reach your {' or '.join(missing)}. "
                      "For questions about your own context, switch to local voice.")
        else:
            notice = ""
        return {"engine": "gemini", "tool_capable": bool(names),
                "tools": len(names), "knowledge_graph": kg, "memory": mem,
                "full_context": full, "notice": notice}
    if engine == "local":
        return {"engine": "local", "tool_capable": True, "tools": None,
                "knowledge_graph": True, "memory": True, "full_context": True,
                "notice": ""}
    return {"engine": engine, "tool_capable": False, "tools": 0,
            "knowledge_graph": False, "memory": False, "full_context": False,
            "notice": "No model is in the voice loop; nothing can call tools."}


def _resolve_voice_engine(settings=None):
    """Resolve which voice engine a session should use, honoring the ethos:
    LOCAL is the default, cloud (Gemini Live) is the opt-in.

    Reads ``settings.voice_engine`` ∈ {"local","gemini","auto"} (default "local")
    and degrades gracefully through a strict, no-dead-ends order:
      local-lite (deps present) → Gemini cloud (key + online) → demo/text.

    Returns ``{engine, ws_url, label, models_ready, reason}``. ``engine`` is one
    of "local" | "gemini" | "demo"; the browser connects the mic to ``ws_url``.
    """
    settings = settings if settings is not None else (_load_settings() or {})
    pref = str(settings.get("voice_engine") or "local").strip().lower()
    # Auto-correct stale/retired voice model — the #1 cause of "voice is broken"
    # reports. A retired model id surfaces as a connect failure that looks like
    # an auth error but isn't. Fix it HERE so the user's next voice session
    # just works instead of failing 3 times through the fallback chain.
    if pref == "gemini":
        try:
            from agent_friday.services.voice_engine import validate_live_model, LIVE_MODEL
            mv = validate_live_model()
            if not mv.get("ok") and mv.get("status") in ("unknown", "retired"):
                # The configured model is stale/retired — reset to the default
                _log.warning("Auto-correcting stale voice model %r → %s",
                             mv.get("model"), LIVE_MODEL)
                settings["voice_model"] = LIVE_MODEL
                try:
                    from agent_friday.core import _save_settings
                    # Persist ONLY the delta: `settings` came from _load_settings(),
                    # which applies the never-persist offline routing overlay —
                    # writing the whole dict would pin local_only routing to disk.
                    _save_settings({"voice_model": LIVE_MODEL})
                except Exception:
                    pass
        except Exception:
            pass
    net = _network_status()
    # cloud_ok needs a key that actually AUTHENTICATES (cheap cached REST
    # probe), not merely a non-empty string — a revoked key must route the
    # mic to local voice instead of a doomed /ws/live session.
    try:
        _ki = resolve_gemini_key()
        cloud_ok = bool(_ki.get("valid")) and not net.get("offline")
    except Exception:
        cloud_ok = bool(core.GEMINI_API_KEY) and not net.get("offline")
    # Local-only is an absolute override, the same guarantee routes/chat.py's
    # vision path already enforces — it
    # must win regardless of `voice_engine` preference or whether the Gemini
    # key is valid. Before this, a user with Local-Only Mode on but the
    # Tier-1 voice deps not installed (`pip install -e .[voice-local-lite]`,
    # an easy-to-skip separate step) got their microphone audio and Friday's
    # spoken replies streamed to Gemini Live anyway — silently.
    _local_only = str(((settings.get('model_routing') or {})
                       .get('mode')) or '').strip().lower() == 'local_only'
    if _local_only:
        cloud_ok = False
    tier = "cpu"
    eng = None
    try:
        eng = get_local_voice_engine()
        local_ok = eng.available()
        # THREE requirements, three checks. `eng.models_ready()` certifies the
        # Whisper checkpoint and the Piper voice — the two ends of the cascade —
        # and says nothing about the brain in the middle. A session could
        # therefore be declared ready, transcribe speech, and then have nothing
        # to think with. Local voice needs ASR *and* a resident brain *and* TTS;
        # anything less is not a local voice session, it is a microphone.
        models_ready = eng.models_ready() if local_ok else False
        if models_ready and not _local_brain_ready():
            models_ready = False
            _log.warning("local voice: ASR and TTS are ready but no local brain "
                         "seat is resident — refusing the local engine rather "
                         "than starting a session that cannot answer")
    except Exception:
        local_ok = False
        models_ready = False
    # Which tier the /ws/voice-local handler will actually run (gpu falls back to
    # cpu automatically when NeMo/CUDA aren't ready). Guarded separately so it
    # never affects local availability resolution.
    try:
        tier = eng.resolve_tier(settings) if eng is not None else "cpu"
    except Exception:
        tier = "cpu"

    def _pick(engine):
        if engine == "local":
            label = ("Local GPU (NeMo, private)" if tier == "gpu"
                     else "Local (private, on-device)")
            return {"engine": "local", "ws_url": "/ws/voice-local",
                    "label": label, "tier": tier,
                    "models_ready": models_ready,
                    "context_reach": _voice_context_reach("local")}
        if engine == "gemini":
            return {"engine": "gemini", "ws_url": "/ws/live",
                    "label": "Cloud (Gemini Live)", "models_ready": True,
                    "context_reach": _voice_context_reach("gemini")}
        return {"engine": "demo", "ws_url": None,
                "label": "Text only", "models_ready": False,
                "context_reach": _voice_context_reach("demo")}

    # Explicit opt-in to cloud.
    if pref == "gemini":
        if cloud_ok:
            return {**_pick("gemini"), "reason": "user selected cloud"}
        if local_ok:
            return {**_pick("local"), "reason": (
                "local-only mode is on, cloud voice is disabled"
                if _local_only else "cloud unavailable, using local")}
        return {**_pick("demo"), "reason": (
            "local-only mode is on and no local voice engine is ready"
            if _local_only else "no voice engine available")}

    # `auto` means LOCAL ONLY, and says so. (R4.1 of
    # local-voice-repair-and-native-audio.md 4.5; voice-mode.md open item 1).
    #
    # voice-system-spec.md 7.2 used to permit `auto` to reach Tier 3 "(cloud
    # only if key present AND not local-only)". That parenthetical is deleted:
    # it contradicted cloud-voice-providers.md 6.3, which forbids promoting to
    # cloud on a LOCAL failure without consent. A mode that can silently send a
    # user's voice to a third party is precisely the failure the transparency
    # commitment exists to prevent, and "auto" is the friendliest possible name
    # for it. The label states the scope: "Automatic (local only)".
    #
    # `auto` therefore terminates at text-only rather than crossing to cloud.
    # The cloud path stays reachable -- by the user selecting it, which is C1 --
    # and the local failure surfaces with its reason, which is C2.
    if pref == "auto":
        if local_ok:
            return {**_pick("local"), "reason": "automatic (local only)"}
        return {**_pick("demo"), "reason": (
            "Automatic mode uses only the local voice, and it is not ready. "
            "Friday will not send your voice to the cloud without you "
            "choosing it -- pick a cloud provider in Settings if you want one.")}

    # `local` TERMINATES. It does not fall through to the cloud. This is the
    # more important half of the `auto` rule above.
    #
    # What used to happen: a user who selected the mode named "local", with the
    # Tier-1 deps not installed (a separate opt-in step that is easy to skip)
    # and a Gemini key present, had their microphone audio and Friday's spoken
    # replies streamed to Gemini Live. Silently. The guard that existed caught
    # this only when Local-Only Mode was ALSO on, so the protection required the
    # user to say "local" twice in two different places, and the plain reading
    # of the word they did select bought them nothing.
    #
    # A user who picks a mode called "local" and receives a cloud provider has
    # been lied to by the word itself. That is worse than the `auto` case, which
    # at least admits ambiguity in its name. The honest failure is strictly
    # better than the dishonest success, so this terminates at text-only and
    # says what happened and what the user can do about it.
    #
    # This is a deliberate behaviour change for existing users. Anyone relying
    # on the old path was relying on being deceived.
    #
    # Note on cloud voice providers (cloud-voice-providers.md 3.3): when `pref`
    # names an ElevenLabs/Inworld provider, the recommended configuration is
    # "Local listening, cloud voice" -- STT stays local, so the LOCAL session is
    # the correct transport here and synthesis is served separately by
    # routes/cloud_voice_routes.py. The provider indicator is written from that
    # served path, never from this label.
    if local_ok:
        return {**_pick("local"), "reason": "local default"}
    if _local_only:
        return {**_pick("demo"), "reason": (
            "local-only mode is on and no local voice engine is ready; "
            "voice will not use the cloud. Install .[voice-local-lite]")}
    return {**_pick("demo"), "reason": (
        "Friday's local voice is not ready, so it cannot speak on this "
        "machine. It will not send your voice to the cloud unless you choose "
        "that. Install .[voice-local-lite] to fix the local voice, or pick a "
        "cloud provider in Settings if you would rather use one.")}


def _build_voice_system_prompt(settings=None, description=None):
    """The LOCAL voice system prompt, assembled prefix-stable (clean-sheet §4.4).

    Order: (1) the manifest's self-description — changes only when a proof
    changes; (2) persona + the voice rules + tool choreography, constant;
    (3) the stable context from ``_get_friday_system_prompt`` whose clock
    block is ORDERED LAST and is where ``prompt_cache.VOLATILE_MARKER``
    splits; (4) everything volatile after it. Returns ``(prompt, meta)`` where
    ``meta`` carries ``is_local_brain``/``provider`` for the turn's session_ctx.

    Used by the ws handler AND registered as the manifest's prompt builder so
    the capability contract (§3.3) is measured against the prompt actually
    served, not a stand-in.
    """
    settings = settings if settings is not None else (_load_settings() or {})
    try:
        from agent_friday.routing.model_router import provider_family
        _brain_family = provider_family(settings.get("orchestrator_model"))
    except Exception:
        _brain_family = None
    _is_local_brain = _brain_family == "local"
    _prov = "local" if _is_local_brain else "cloud"
    _vault_control = None if _is_local_brain else (
        _get_vault_control() if _vault_local_only() else None)
    if description is None:
        try:
            description = _vm.get_manifest().describe_for_model()
        except Exception:
            description = ""
    voice_prefix = (
        "You are Agent Friday, a sovereign personal AI assistant in a LIVE "
        "VOICE conversation. " + (description or "") + "\n"
        "Speak like a person: natural, warm, contractions.\n"
        "NEVER use markdown — no asterisks, headers, or bullets; this is read "
        "aloud. An asterisk is SPOKEN as 'asterisk', so a bulleted list is "
        "unlistenable. Numbered points must be said the way a person says "
        "them: 'first … second …', inside ordinary sentences.\n"
        # LENGTH. This is the local path's ONLY brevity control that the
        # model itself can honour, and it has to be concrete: "reasonably
        # concise" produced 183-word answers to "how does your vault work"
        # — over a minute of uninterruptible speech for
        # one question. Even frontier speech-to-speech models still need an
        # explicit "avoid long answers" line; no model choice removes this.
        "LENGTH — THIS IS SPOKEN, SO LENGTH IS TIME. Answer in ONE to THREE "
        "short sentences, under about 60 spoken words. That is the DEFAULT "
        "for every turn, including questions about yourself, your systems, "
        "the vault, or how you work. Give the single most useful answer, "
        "then STOP and let them respond — a voice reply is a turn in a "
        "conversation, not a briefing. Never deliver a list, a walkthrough, "
        "or a multi-paragraph explanation unless they explicitly ask you to "
        "go deep ('walk me through', 'give me the full version', 'go on'), "
        "and even then deliver it in chunks and stop for their reply between "
        "them. If the honest answer is genuinely long, say the headline in "
        "one sentence and offer the detail: 'The short version is X — want "
        "the long one?'\n"
        + ("Your reasoning also runs locally, so you CAN discuss private "
           "vault content — it never leaves the machine.\n\n"
           if _is_local_brain else "\n")
        + VOICE_TOOL_CHOREOGRAPHY
    )
    try:
        full_ctx = _get_friday_system_prompt(
            provider=_prov, vault_control=_vault_control,
            vault_fallback=_vault_cloud_fallback())
    except Exception as e:
        full_ctx = f"(context load failed: {e})"
    # Volatile blocks go AFTER the context (whose clock block is its last
    # section), so the byte-identical prefix survives across turns.
    try:
        full_ctx += _build_session_continuity_block() + _build_emotional_tone_block()
    except Exception:
        pass
    # THE VOLATILE TAIL LEAVES THE SYSTEM MESSAGE.
    #
    # Measured against the FridayWeaver seat: with its chat
    # template, ANY change to the system message -- 54 characters at the very
    # end -- re-prefills the whole prompt (17,203 of 17,203 tokens in the
    # probe; 27,480 on a real session), while a changed user message costs
    # ~10. The clock, auto-context, continuity and tone blocks after
    # `VOLATILE_MARKER` change between builds, so every session's first
    # turn paid ~8 s and a warm could never help. They now ride in the user
    # turn instead (`_voice_user_message`), which keeps the system text
    # byte-identical across proofs, warms and sessions -- and gives the
    # model a fresh clock every turn instead of the one frozen at session
    # start. ~500 tokens per turn is the whole cost.
    try:
        from agent_friday.services.prompt_cache import VOLATILE_MARKER
        idx = full_ctx.find(VOLATILE_MARKER)
    except Exception:
        idx = -1
    volatile = ""
    if idx >= 0:
        volatile = full_ctx[idx:]
        full_ctx = full_ctx[:idx]
    # The action policy is appended after the clock, so the split above
    # carried it into the user turn with the continuity and tone blocks after
    # it. It is constant text: it goes last in the stable system prompt, where
    # it costs the cache nothing, and the derived volatile tail is stripped of
    # anything claiming authority over it.
    from agent_friday.services.action_policy import (
        ACTION_PERMISSION_POLICY, seal_system_prompt, strip_authority_overrides)
    volatile = strip_authority_overrides(
        volatile.replace(ACTION_PERMISSION_POLICY, ""), source="voice volatile context")
    return (seal_system_prompt(voice_prefix + full_ctx, "local voice prompt"),
            {"is_local_brain": _is_local_brain, "provider": _prov,
             "volatile": volatile})


def _voice_user_message(user_text, settings=None, volatile=None):
    """The user turn: the volatile context block (clock, auto-context,
    continuity, tone), rebuilt fresh (~45 ms), then what was said."""
    if volatile is None:
        try:
            volatile = _build_voice_system_prompt(settings)[1].get("volatile") or ""
        except Exception:
            volatile = ""
    volatile = (volatile or "").strip()
    if not volatile:
        return user_text
    return volatile + "\n\n== THE USER JUST SAID ==\n" + user_text


def _voice_prompt_for_contract():
    return _build_voice_system_prompt()[0]


_vm.set_prompt_builder(_voice_prompt_for_contract)


def _warm_seat_prefix() -> dict:
    """F5 / clean-sheet §4.4: put the session's prompt prefix into the seat's
    KV cache right after the proofs, through the SAME call a turn makes.

    Measured: the first utterance after arming prefilled 27,460
    tokens and took ~8 s to first audio; the second prefilled 15 and took
    ~1.5 s. The proofs themselves cannot warm it, because the manifest's
    self-description is the prompt's first line and it changes when the
    proofs land. A one-token completion with the post-proof prompt, the
    resolved seat and the voice session context makes turn one warm.
    """
    t0 = _time.time()
    settings = _load_settings() or {}
    try:
        system_prompt, _pmeta = _build_voice_system_prompt(settings)
        from agent_friday.services import local_seats as _seats
        _brain = _seats.resolve("brain")
        from agent_friday.services.model_router import TIMINGS_SINK
        timings = {}
        _tok = TIMINGS_SINK.set(lambda t: timings.update(t or {}))
        try:
            _generate_agent(
                [{"role": "user",
                  "content": _voice_user_message("OK.", settings,
                                                 volatile=_pmeta.get("volatile"))}],
                system=system_prompt, model=_brain, max_tokens=1,
                temperature=settings.get("temperature"),
                session_ctx={"authenticated": True, "provider": _pmeta["provider"],
                             "is_voice": True, "prefix_warm": True},
                workspace=settings.get("active_workspace") or "",
            )
        finally:
            TIMINGS_SINK.reset(_tok)
        out = {"warmed": True, "seat": _brain, "prompt_n": timings.get("prompt_n"),
               "ms": int((_time.time() - t0) * 1000)}
        _log.info("voice prefix warm: seat=%s prompt_n=%s in %d ms",
                  _brain, timings.get("prompt_n"), out["ms"])
        return out
    except Exception as e:  # noqa: BLE001
        _log.warning("voice prefix warm failed: %s: %s", type(e).__name__, e)
        return {"warmed": False, "reason": f"{type(e).__name__}: {e}"}


try:
    _vm.get_manifest().after_prove = _warm_seat_prefix
except Exception:
    pass


@voice_bp.route('/api/voice/session-info')
def voice_session_info():
    """Tell the browser which engine + WebSocket URL to use for this session.

    The mic button reads ``ws_url`` and connects there — ``/ws/voice-local``
    (default) or ``/ws/live`` (cloud opt-in). One toggle, one branch; the audio
    plumbing and event contract are identical on both paths.

    ``manifest`` is the Voice Manifest snapshot (clean-sheet §3.1 rule 4): the
    same object the Settings card, the HUD and the model's self-description
    render. Reading it never runs a proof; ``/api/voice/arm`` does that."""
    info = _resolve_voice_engine()
    try:
        m = _vm.get_manifest()
        m.refresh_selection()
        info["manifest"] = m.snapshot()
    except Exception as e:
        info["manifest"] = {"error": f"{type(e).__name__}: {e}"}
    return jsonify({"status": "ok", **info})


@voice_bp.route('/api/voice/arm', methods=['GET', 'POST'])
@login_required
def voice_arm():
    """POST: run the three proofs (single-flight, background) and return the
    snapshot. GET: the snapshot with per-stage progress. Clean-sheet §6.3.

    Proofs that are still fresh (inside their TTL) are not re-run unless
    ``?force=1`` (the Settings "Prove voice now" button uses /api/voice/prove).
    ``/api/voice/warm`` is kept as the engine pre-loader (F5); arming proves.
    """
    m = _vm.get_manifest()
    m.refresh_selection()
    started = False
    if request.method == 'POST':
        force = str(request.args.get("force") or
                    (request.get_json(silent=True) or {}).get("force") or "").lower() in ("1", "true")
        stale = [k for k in _vm.STAGES if force or m.is_stale(k)]
        if m.mode == "gemini":
            stale = [k for k in stale if k == "mind"]
        if stale:
            started = m.prove_async(tuple(stale))
    snap = m.snapshot()
    snap["started"] = started
    return jsonify({"status": "ok", **snap})


@voice_bp.route('/api/voice/prove', methods=['POST'])
@login_required
def voice_prove():
    """Force a re-proof of every stage regardless of TTL."""
    m = _vm.get_manifest()
    m.refresh_selection()
    stages = ("mind",) if m.mode == "gemini" else _vm.STAGES
    started = m.prove_async(stages)
    snap = m.snapshot()
    snap["started"] = started
    return jsonify({"status": "ok", **snap})


# ── F5: pre-warm the local voice engine ─────────────────────────────────────
# Measured: a cold KokoroTTS load is 39-56 s, and without this it is paid on
# the first spoken turn. Warm it when the Voice panel opens or voice is armed;
# the /ws/voice-local handler's ensure_ready() then returns immediately.
_WARM_LOCK = threading.Lock()
_WARM = {"state": "idle", "progress": "", "started": 0.0, "finished": 0.0,
         "tier": None, "error": "", "engine": None}


def _warm_snapshot():
    with _WARM_LOCK:
        snap = dict(_WARM)
    now = _time.time()
    if snap["state"] == "loading" and snap["started"]:
        snap["elapsed_s"] = round(now - snap["started"], 1)
    elif snap["finished"] and snap["started"]:
        snap["elapsed_s"] = round(snap["finished"] - snap["started"], 1)
    else:
        snap["elapsed_s"] = 0.0
    try:
        snap["running"] = get_local_voice_engine().running_status()
    except Exception:
        snap["running"] = None
    return snap


def _warm_local_voice_async():
    """Start loading the selected local engine on a background thread.
    Returns the snapshot; a second call while loading is a no-op."""
    with _WARM_LOCK:
        if _WARM["state"] == "loading":
            return None
        try:
            eng = get_local_voice_engine()
            if eng._ready:
                _WARM.update(state="ready", progress="already loaded",
                             tier=eng.active_tier(), error="",
                             engine=(eng.running_status() or {}).get("tts_engine"))
                return None
        except Exception:
            pass
        _WARM.update(state="loading", progress="starting", started=_time.time(),
                     finished=0.0, error="", tier=None, engine=None)

    def _run():
        eng = get_local_voice_engine()

        def _prog(msg):
            with _WARM_LOCK:
                _WARM["progress"] = str(msg or "")
        try:
            eng.select_tier(eng.resolve_tier())
            ok = eng.ensure_ready(progress=_prog)
            with _WARM_LOCK:
                _WARM["finished"] = _time.time()
                _WARM["tier"] = eng.active_tier()
                if ok:
                    _WARM["state"] = "ready"
                    _WARM["progress"] = "ready"
                    _WARM["engine"] = (eng.running_status() or {}).get("tts_engine")
                else:
                    _WARM["state"] = "failed"
                    _WARM["error"] = getattr(eng, "last_error", "") or "load failed"
        except Exception as e:
            with _WARM_LOCK:
                _WARM.update(state="failed", finished=_time.time(),
                             error=f"{type(e).__name__}: {str(e)[:160]}")
            _log.warning("voice warm failed: %s", e)

    threading.Thread(target=_run, name="voice-warm", daemon=True).start()
    return None


@voice_bp.route('/api/voice/warm', methods=['GET', 'POST'])
@login_required
def voice_warm():
    """GET: warm state. POST: start loading the selected local engine now.

    POST is a no-op (200, state reported) when cloud voice is selected, when a
    load is already running, or when the engine is already loaded. A local
    engine that is not installed reports ``skipped`` with the reason rather
    than spawning a thread that would fail.
    """
    if request.method == 'POST':
        info = _resolve_voice_engine()
        if info.get("engine") != "local":
            return jsonify({"status": "ok", "state": "skipped",
                            "reason": f"{info.get('engine')} voice selected; "
                                      "nothing local to warm",
                            **{k: v for k, v in _warm_snapshot().items()
                               if k not in ("state",)}})
        if info.get("models_ready") is False:
            return jsonify({"status": "ok", "state": "skipped",
                            "reason": "local voice models are not ready; the "
                                      "first session downloads them",
                            **{k: v for k, v in _warm_snapshot().items()
                               if k not in ("state",)}})
        _warm_local_voice_async()
    snap = _warm_snapshot()
    return jsonify({"status": "ok", **snap})


@voice_bp.route('/api/voice/transcribe', methods=['POST'])
@login_required
def voice_transcribe():
    """One shot of speech in, one line of text out, entirely on this machine.

    Push-to-transcribe is the thing people reach for when they do not want a
    conversation — a sentence into whatever window has focus. That makes it
    the surface where "local by default" has to be literal rather than
    aspirational: this route has NO cloud path, not even a fallback one, so a
    dictated password or a sentence about someone's health cannot leave the
    machine because a model failed to load. If the local ear is unavailable
    this returns 503 and says why.

    Accepts a WAV body (``audio/wav``) or JSON ``{audio_b64, rate}`` of raw
    16-bit mono PCM. Returns ``{text, device, ms}``.
    """
    from agent_friday.services.local_voice import (
        _wav_to_pcm16, _resample_pcm16, ASR_RATE, get_local_voice_engine)

    raw = request.get_data() or b""
    rate = ASR_RATE
    # Decided by the declared content type alone. Sniffing for a leading "{"
    # would misread raw PCM whose first byte happens to be 0x7B, which is one
    # sample value in 256 — a corrupt transcript roughly every 256 dictations.
    ctype = (request.content_type or "").lower()
    if "json" in ctype:
        try:
            body = request.get_json(force=True, silent=True) or {}
        except Exception:
            body = {}
        b64 = body.get("audio_b64") or ""
        try:
            pcm = base64.b64decode(b64) if b64 else b""
        except Exception:
            return jsonify({"status": "error",
                            "error": "audio_b64 is not valid base64"}), 400
        rate = int(body.get("rate") or ASR_RATE)
    elif raw[:4] == b"RIFF":
        try:
            pcm, rate = _wav_to_pcm16(raw)
        except Exception as e:
            return jsonify({"status": "error",
                            "error": f"could not read the WAV: {e}"}), 400
    else:
        pcm = raw

    if not pcm:
        return jsonify({"status": "error", "error": "no audio"}), 400
    if rate != ASR_RATE:
        pcm = _resample_pcm16(pcm, rate, ASR_RATE)

    # Long enough to be a sentence, short enough not to be a recording session
    # someone forgot about. 16-bit mono at 16 kHz is 32000 bytes/second.
    seconds = len(pcm) / float(ASR_RATE * 2)
    if seconds > 120:
        return jsonify({"status": "error",
                        "error": "that is over two minutes of audio; "
                                 "push-to-transcribe is for a sentence"}), 413

    eng = get_local_voice_engine()
    t0 = _time.time()
    try:
        asr = eng._get_asr()
        text = asr.transcribe(pcm)
    except Exception as e:
        try:
            from agent_friday.services.voice_manifest import plain_language_refusal
            msg, _action = plain_language_refusal(e, "ear")
        except Exception:
            msg = f"{type(e).__name__}: {e}"
        _log.warning("push-to-transcribe failed: %s: %s", type(e).__name__, e)
        return jsonify({"status": "error", "error": msg,
                        "engine": "local"}), 503

    return jsonify({"status": "ok", "text": text, "engine": "local",
                    "device": getattr(asr, "_device", None) or "cpu",
                    "why_cpu": getattr(asr, "_why_cpu", "") or "",
                    "audio_s": round(seconds, 2),
                    "ms": int((_time.time() - t0) * 1000)})


@voice_bp.route('/api/voice/setup/status')
@login_required
def voice_setup_status():
    """Return voice setup readiness for the first-run wizard.

    Response shape::

        {
          "ready": bool,           # true = voice is fully configured and working
          "engine": "local"|"gemini"|"...",
          "steps": [
            {"id": "deps", "label": "Dependencies", "status": "ok"|"missing", "detail": "..."},
            {"id": "models", "label": "ASR / TTS Models", "status": "ok"|"needs_download", "detail": "..."},
            {"id": "mic", "label": "Microphone", "status": "ok"|"unknown", "detail": "..."},
            {"id": "key", "label": "API Key", "status": "ok"|"missing", "detail": "..."},
          ]
        }
    """
    steps = []
    engine_info = {}
    try:
        engine_info = _resolve_voice_engine()
    except Exception as _e:
        _log.warning("voice_setup_status: _resolve_voice_engine failed: %s", _e)

    resolved = engine_info.get("engine", "local")

    if resolved in ("local", "local-gpu"):
        try:
            from agent_friday.services.local_voice import local_voice_health
            lv = local_voice_health()
            # Derive each step from the dedicated health fields, not the combined
            # status string: health() emits 'missing'/'needs_download'/'ok'/'error'
            # (never 'unavailable'), and needs_download does NOT mean deps missing.
            steps.append({
                "id": "deps",
                "label": "Python dependencies (faster-whisper / piper)",
                "status": "ok" if lv.get("available") else "missing",
                "detail": lv.get("detail", "") if not lv.get("available")
                          else "Tier-1 voice dependencies installed.",
            })
            if not lv.get("available"):
                models_status, models_detail = "unknown", "Install dependencies first."
            elif lv.get("models_ready"):
                models_status, models_detail = "ok", "Voice models downloaded."
            else:
                models_status = "needs_download"
                models_detail = lv.get("detail", "Models download on first voice session.")
            steps.append({
                "id": "models",
                "label": "ASR / TTS model files (~300 MB, one-time download)",
                "status": models_status,
                "detail": models_detail,
            })
            # Tier-2 (GPU/NeMo) — informational: never gates Tier-1 readiness,
            # but a user who owns a capable GPU should SEE the upgrade path
            # (and a user who picked local-gpu should see why they're on CPU).
            gpu = lv.get("gpu") or {}
            if gpu:
                _gpu_ready = str(gpu.get("status", "")) == "ok"
                steps.append({
                    "id": "gpu",
                    "label": "GPU voice tier (NVIDIA NeMo) — optional",
                    "status": "ok" if _gpu_ready else "unknown",
                    "detail": gpu.get("detail", ""),
                })
        except Exception as _e:
            steps.append({"id": "deps", "label": "Local voice deps",
                          "status": "unavailable", "detail": str(_e)})
        steps.append({"id": "mic", "label": "Microphone",
                      "status": "unknown",
                      "detail": "Click the mic button to test — browser will prompt for permission."})
    else:
        # Cloud / Gemini voice — needs an API key that actually authenticates.
        try:
            _ki = resolve_gemini_key()
        except Exception:
            from agent_friday.core import GEMINI_API_KEY
            _ki = {"key": GEMINI_API_KEY, "valid": bool(GEMINI_API_KEY),
                   "source": "server", "detail": ""}
        if _ki.get("key") and _ki.get("valid"):
            _kstat, _kdetail = "ok", f"key from {_ki.get('source')}"
        elif _ki.get("key"):
            _kstat = "invalid"
            _kdetail = (f"Key from {_ki.get('source')} was rejected by Google "
                        f"({_ki.get('detail')}). Paste a fresh key from "
                        f"aistudio.google.com in Settings → Accounts & Keys → Google Gemini.")
        else:
            _kstat, _kdetail = "missing", "Set via Settings → Accounts & Keys → Google Gemini"
        steps.append({"id": "key", "label": "Gemini API Key",
                      "status": _kstat, "detail": _kdetail})
        # Validate the configured Live model id — a stale/renamed id surfaces as
        # a connect failure that looks like an auth error but isn't (H8).
        try:
            from agent_friday.services.voice_engine import validate_live_model
            mv = validate_live_model()
            # A failed model check must GATE readiness ('invalid'), not hide in
            # 'unknown' (which the readiness aggregate deliberately excludes —
            # that state is reserved for cannot-check items like mic permission).
            steps.append({"id": "model",
                          "label": f"Voice model ({mv['model'] or 'default'})",
                          "status": "ok" if mv["ok"] else "invalid",
                          "detail": mv["detail"]})
        except Exception as _e:
            _log.warning("voice_setup_status: model validation failed: %s", _e)
        steps.append({"id": "mic", "label": "Microphone",
                      "status": "unknown",
                      "detail": "Browser will prompt for mic permission on first session."})

    all_ok = all(s["status"] == "ok" for s in steps if s["status"] != "unknown")
    return jsonify({"ready": all_ok, "engine": resolved, "steps": steps,
                    "engine_info": engine_info})


@voice_bp.route('/api/voice/setup/test', methods=['POST'])
@login_required
def voice_setup_test():
    """Run a short TTS test utterance and return the audio as base64 PCM16.
    The wizard plays this back to confirm the TTS pipeline is working end-to-end.
    """
    data = request.get_json(silent=True) or {}
    text = data.get("text", "Hello, I'm Friday. Voice setup is complete.").strip()
    try:
        from agent_friday.services.voice_engine import _synthesize_tts_wav
        wav_buf = _synthesize_tts_wav(text, allow_local=True)  # io.BytesIO, pos 0
        import base64 as _b64
        wav_data = wav_buf.getvalue() if wav_buf is not None else b""
        if not wav_data:
            return jsonify({
                "status": "error",
                "message": "TTS produced no audio — no TTS engine is available. "
                           "Install local voice (Settings → Voice → Setup Wizard) "
                           "or configure a Gemini API key.",
            }), 503
        return jsonify({
            "status": "ok",
            "audio_b64": _b64.b64encode(wav_data).decode(),
            "format": "wav",
        })
    except Exception as e:
        _log.warning("voice setup test TTS failed: %s", e, exc_info=True)
        return jsonify({
            "status": "error",
            "message": f"TTS synthesis failed: {type(e).__name__} — {e}. "
                       f"Check that voice dependencies are installed.",
        }), 500


@voice_bp.route('/api/voice/setup/install', methods=['POST'])
@login_required
def voice_setup_install():
    """Start a background install of a voice tier (allowlisted targets only).

    Body: {"target": "voice-local-lite" | "voice-local-gpu" | "tier1-models"}.
    Long-running (torch-CUDA is multi-GB) — poll /api/voice/setup/install/status.
    """
    data = request.get_json(silent=True) or {}
    target = str(data.get("target") or "").strip()
    from agent_friday.services import voice_installer
    job = voice_installer.start(target)
    code = 200 if job.get("state") == "running" else 400
    return jsonify(job), code


@voice_bp.route('/api/voice/setup/install/status', methods=['GET'])
@login_required
def voice_setup_install_status():
    from agent_friday.services import voice_installer
    return jsonify(voice_installer.status())


@voice_bp.route('/api/voice/setup/install/cancel', methods=['POST'])
@login_required
def voice_setup_install_cancel():
    from agent_friday.services import voice_installer
    return jsonify(voice_installer.cancel())


def _ws_auth_ok(ui_tok_ok: bool) -> bool:
    """Mirror core.login_required()'s fail-closed semantics for a WebSocket
    handshake.

    Both `/ws/voice-local` and `/ws/live` used to gate on bare `FRIDAY_PASSWORD`
    directly: `if FRIDAY_PASSWORD and not authenticated and not loopback and
    not ui_tok: deny`. When no password was configured at all, that whole
    condition short-circuited False and the block was skipped ENTIRELY —
    not even checking loopback — so the socket accepted any connection
    unconditionally, non-loopback included. `login_required()` never does
    this for HTTP: `if not _HTTP_AUTH_KEY: return f(...) if loopback else 403`
    — a non-loopback caller is denied even with no key configured. This
    mirrors that exact structure, using `_HTTP_AUTH_KEY` (FRIDAY_REMOTE_KEY or
    FRIDAY_PASSWORD) rather than bare FRIDAY_PASSWORD so a FRIDAY_REMOTE_KEY-
    only configuration is covered too, not just the bare-FRIDAY_PASSWORD case
    the finding named.
    """
    if not _HTTP_AUTH_KEY:
        # No key configured anywhere: same as login_required's fail-closed
        # branch — only loopback is trusted, not the ephemeral UI token, since
        # login_required's own equivalent branch doesn't consult it either.
        return _loopback_trusted()
    return bool(session.get("authenticated") or _loopback_trusted() or ui_tok_ok)


def _meter_gemini_live_chunk(chunk, model_name: str) -> bool:
    """Record cost_meter usage from one Gemini Live streaming chunk, if it
    carries usage_metadata. Returns whether a charge was recorded.

    Extracted out of ws_live's receive loop so this has its own testable
    identity. ws_live
    itself is a closure nested inside a Flask-Sock route registration
    function, deeply inside an async Gemini Live streaming session --
    reaching this exact line from a test previously meant either mocking
    that entire session or pinning the surrounding source text (variable
    names, try/except structure) instead of exercising real behavior.
    Never raises -- a metering failure must not break the live voice
    bridge, exactly as the original inline try/except guaranteed.
    """
    _um = getattr(chunk, "usage_metadata", None)
    if _um is None:
        return False
    try:
        from agent_friday.services import cost_meter as _cm
        _cm.meter("gemini", model_name, {
            "input_tokens": getattr(_um, "prompt_token_count", 0) or 0,
            "output_tokens": getattr(_um, "response_token_count", 0) or 0,
        }, kind="voice")
        return True
    except Exception:
        return False


if sock is not None:

    def ws_voice_local(ws):
        """Tier-1 LOCAL voice: mic → VAD → faster-whisper → LLM brain → Piper → speaker.

        Speaks the SAME browser↔server contract as ``/ws/live`` so the client
        audio plumbing, the friday-pcm-player worklet, and the holographic cube
        signals are reused unchanged:

          browser → server:  {type:'audio', data:<b64 PCM16@16k>} | {type:'text'} | {type:'end'}
          server → browser:  {type:'status'} {type:'input_transcript'} {type:'text'}
                             {type:'audio', data:<b64 PCM16@24k>} {type:'turn_end'}
                             {type:'voice_turn_done',user_text,agent_text} {type:'error'}

        The brain is the EXISTING agentic pipeline (`_generate_agent`) — the same
        code path a typed chat turn uses — so tools, vault gating, and provider
        routing all behave identically to text chat.
        """
        # ── Auth (mirror /ws/live: loopback trusted, else token/password) ──
        # Accept: (a) FRIDAY_WS_TOKEN env-var token in ?token=, (b) ephemeral
        # UI session token in ?t= (injected into HTML as window.__FRIDAY_API_TOKEN),
        # (c) existing HTTP session cookie, or (d) loopback auto-trust.
        if FRIDAY_WS_TOKEN:
            _tok = request.args.get('token', '')
            if not _hmac.compare_digest(_tok, FRIDAY_WS_TOKEN):
                try:
                    ws.send(json.dumps({"type": "error", "error": "unauthorized"}))
                except Exception:
                    pass
                return
        _ui_t = request.args.get('t', '')
        _ui_tok_ok = _api_token_valid(_ui_t)
        if not _ws_auth_ok(_ui_tok_ok):
            try:
                ws.send(json.dumps({"type": "error", "error": "unauthorized"}))
            except Exception:
                pass
            return

        # ── Carry the authenticated identity into the turn ──────────────────
        # Reaching this line MEANS the socket is authenticated: either no
        # password is set, or the Flask session is logged in, or the origin is
        # trusted loopback, or the UI token checked out. That fact then has to
        # travel WITH the turn, because two gates downstream ask for it and the
        # local voice path never told them:
        #
        #   * governance ring policy — `is_auth = ctx["authenticated"] or
        #     ctx["is_background_task"]`. Ring 2 is EVERY network tool. With no
        #     session_ctx the gate saw {} and denied search_news, search_web and
        #     every other network op with "ring-2 network op requires
        #     authenticated session". Friday, handed a governance refusal, told
        #     the user she was locked out of the news — which reads as the
        #     vault refusing them ON A LOCAL SESSION. The vault had
        #     nothing to do with it and logged ALLOW / TIER_1 on the same call.
        #   * zero-trust vault check_action — `session_ctx.get("provider",
        #     "cloud")`. Absent a ctx it evaluated a LOCAL brain as cloud.
        #
        # /api/chat has always passed this (routes/chat.py). Voice was the only
        # tool-using surface that did not, which is exactly why news worked in
        # text chat and refused in voice.
        #
        # Captured HERE, at connect time, and never re-read later: `session` and
        # `request` are request-context bound, and turns now run on their own
        # thread where neither is available.
        _ws_authenticated = _ws_auth_ok(_ui_tok_ok)

        done = threading.Event()

        # The thread the user has OPEN, carried by the client on the socket.
        # A list, not a plain name, because the receive loop rebinds it when the
        # user switches conversations mid-call and _handle_turn must see the change.
        # None means "no open thread" -- _persist_voice_turn falls back to Main
        # explicitly for that case rather than sending everyone there.
        _open_cid = [(request.args.get('conversation_id') or '').strip() or None]

        # Turns now run on their own thread (see _spawn_turn), so audio frames
        # and the loop's heartbeats reach ws.send() concurrently. A WebSocket
        # frame is not atomic across threads; two interleaved sends corrupt
        # both. One lock, held only for the send itself.
        _send_lock = threading.Lock()

        def _send(obj):
            if done.is_set():
                return False
            try:
                payload = json.dumps(obj)
                with _send_lock:
                    ws.send(payload)
                return True
            except ConnectionClosed:
                done.set()
                return False
            except Exception:
                return False

        # ── Clean-sheet Phase 2: the route is thin. Everything about the turn
        # lives in services/voice_session.VoiceSession; this handler does auth
        # (above), picks the PROVEN engines the manifest holds, binds the mind
        # to the prefix-stable prompt, and pumps frames. ──
        from agent_friday.services import voice_workers as _vw
        from agent_friday.services.voice_session import VoiceSession
        from agent_friday.services.voice_receipt import TurnReceipt, log_route

        settings = _load_settings() or {}
        _manifest = _vm.get_manifest()
        _manifest.refresh_selection(settings)
        _msnap = _manifest.snapshot()
        # A stage the manifest has PROVEN to fail refuses the session with the
        # taxonomy's message and action (§7); an unproven stage is loaded now
        # with visible progress (arming before the click is the client's job).
        _refused = [k for k, st in (_msnap.get("stages") or {}).items()
                    if (st.get("proof") or {}).get("state") == "refused"]
        if _refused:
            _k = _refused[0]
            _st = _msnap["stages"][_k]
            _send({"type": "manifest", **_msnap})
            _send({"type": "error",
                   "error": (_st.get("proof") or {}).get("code") or "voice_stage_unproven",
                   "detail": _st.get("reason") or
                   f"Friday couldn't prove her {_vm.VoiceManifest._noun(_k)} works right now.",
                   "action": _st.get("action")})
            return

        _vsession = uuid.uuid4().hex[:8]
        _sel = _vm.read_selection(settings)
        _prog = lambda m: _send({"type": "status", "text": str(m)})  # noqa: E731
        try:
            _send({"type": "status", "text": "starting local voice"})
            ear = _vw.held("ear") or _vw.build_ear(_sel["ear"], progress=_prog)
            mouth = _vw.held("mouth") or _vw.build_mouth(_sel["mouth"], progress=_prog)
        except _vw.GpuRefused as _ge:
            _send({"type": "error", "error": _ge.code, "detail": _ge.message})
            return
        except Exception as _le:
            _vlog.error("session aborted: engine load failed: %s: %s",
                        type(_le).__name__, _le)
            _send({"type": "error", "error": "local_voice_load_failed",
                   "detail": f"Could not load the local voice engines "
                             f"({type(_le).__name__}: {str(_le)[:160]}). Check "
                             f"Settings → Voice → Voice Stack for the refused row."})
            return
        # The clause-fallback floor (§4.3): Piper speaks any clause the primary
        # mouth fails on. Loaded lazily on first failure so a Kokoro session
        # does not pay for it up front.
        _fallback = None
        if getattr(mouth, "name", "") != "piper":
            try:
                _fallback = _vw.PiperMouth(settings.get("local_voice_tts_voice")
                                           or "en_US-amy-medium")
            except Exception:
                _fallback = None
        # Admission / eviction notices raised while building (one each).
        for _n in list(_vw.NOTICES):
            _send({"type": "error-nonfatal", "code": _n.get("code"),
                   "message": _n.get("message"), "action": None})
        del _vw.NOTICES[:]
        try:
            log_route(_vsession,
                      requested=settings.get("voice_engine") or "local",
                      selected=f"ear={ear.describe().get('device')} "
                               f"mouth={mouth.describe().get('engine')}/"
                               f"{mouth.describe().get('device')}",
                      reason="; ".join(st.get("reason", "") for st in
                                       (_msnap.get("stages") or {}).values()
                                       if st.get("reason")))
        except Exception:
            pass

        # ── Mind: the agentic pipeline on the resident seat, streamed, under
        # the prefix-stable prompt (§4.4). ──
        system_prompt, _pmeta = _build_voice_system_prompt(settings)
        _prov = _pmeta["provider"]
        _brain = None
        try:
            from agent_friday.services import local_seats as _seats
            _brain = _seats.resolve("brain")
        except Exception:
            pass
        _timings = {}

        def _generate(user_text, on_delta, cancel):
            from agent_friday.services.model_router import TIMINGS_SINK
            _timings.clear()
            _tok = TIMINGS_SINK.set(lambda t: _timings.update(t or {}))
            try:
                reply, _trace = _generate_agent(
                    [{"role": "user",
                      "content": _voice_user_message(user_text, settings)}],
                    system=system_prompt,
                    model=_brain,
                    max_tokens=_voice_reply_cap(settings),
                    temperature=settings.get("temperature"),
                    session_ctx={"authenticated": _ws_authenticated,
                                 "provider": _prov,
                                 "is_voice": True},
                    workspace=settings.get("active_workspace") or "",
                    on_text_delta=on_delta,
                )
            finally:
                TIMINGS_SINK.reset(_tok)
            return reply

        def _receipt():
            try:
                return TurnReceipt(session_id=_vsession,
                                   tier=("gpu" if ear.describe().get("device") == "cuda" else "cpu"),
                                   asr_model=ear.describe().get("model"),
                                   tts_voice=mouth.describe().get("voice"),
                                   brain_seat=_brain or "unresolved")
            except Exception:
                return None

        hooks = {
            "persist": lambda u, a, cid: _persist_voice_turn(u, a, conversation_id=cid),
            "distill": _spawn_voice_distill,
            "actions": _voice_actions_for,
            "receipt": _receipt,
            "timings": lambda: dict(_timings),
        }
        vad = VADEndpointer(silence_ms=int(settings.get("voice_silence_ms") or 800))
        sess = VoiceSession(_send, ear=ear, mouth=mouth, fallback_mouth=_fallback,
                            vad=vad, generate=_generate, hooks=hooks,
                            manifest_snapshot=_msnap,
                            contract=_msnap.get("contract") or {},
                            gpu_queue=_vw.gpu_queue(), session_id=_vsession)
        sess.conversation_id = _open_cid[0]
        _sub = lambda snap: _send({"type": "manifest", **snap})  # noqa: E731
        _manifest.subscribe(_sub)
        sess.start()

        _last_hb = _time.time()
        try:
            while not done.is_set() and not sess.done.is_set():
                if _time.time() - _last_hb >= 15.0:
                    _last_hb = _time.time()
                    _send({"type": "hb", "ts": int(_last_hb)})
                try:
                    raw = ws.receive(timeout=1.0)
                except ConnectionClosed:
                    break
                except Exception:
                    continue
                if raw is None:
                    continue
                if isinstance(raw, bytes):
                    try:
                        raw = raw.decode("utf-8")
                    except Exception:
                        continue
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                try:
                    sess.handle(msg)
                except Exception as _he:
                    _vlog.error("voice frame failed: %s: %s", type(_he).__name__, _he)
        finally:
            done.set()
            _manifest.unsubscribe(_sub)
            sess.close()
            try:
                ws.close()
            except Exception:
                pass

    # Clean-sheet §6.3: `/ws/voice` is the local route; `/ws/voice-local` is
    # kept as an alias for one release. ONE implementation, two paths.
    #
    # flask-sock's `route` decorator returns None, so a decorated
    # `ws_voice_local` was None by the time an alias function called it:
    # every /ws/voice connect answered HTTP 500 ("'NoneType' object is not
    # callable") while /ws/voice-local -- the URL
    # session-info hands the mic -- kept working. Register the undecorated
    # implementation under both paths, with distinct endpoint names so
    # Flask does not see one endpoint mapped to two view functions.
    sock.route('/ws/voice-local', endpoint='ws_voice_local')(ws_voice_local)
    sock.route('/ws/voice', endpoint='ws_voice')(ws_voice_local)

    @sock.route('/ws/live')
    def ws_live(ws):
        """Bridge a browser WebSocket to a Gemini Live API session.

        Messages from browser -> Gemini:
          { type: 'audio', data: <b64 PCM16 @ 16 kHz> }
          { type: 'image', data: <b64 JPEG> }
          { type: 'text', text: "..." }
          { type: 'end' }
        Messages from Gemini -> browser:
          { type: 'audio', data: <b64 PCM16 @ 24 kHz> }
          { type: 'text', text: "..." }           # model text or transcript
          { type: 'input_transcript', text: ... } # user transcript
          { type: 'status', text: "..." }
          { type: 'turn_end' }
          { type: 'error', error: "..." }
        """
        import time as _time
        _vlog_path = FRIDAY_DIR / 'voice_debug.log'
        # Per-chunk voice logging is OFF by default — it is noisy and only useful
        # when diagnosing the live-audio bridge. Enable by setting the env var
        # FRIDAY_VOICE_DEBUG=1 (mirrors window.FRIDAY_VOICE_DEBUG on the client).
        _voice_debug = bool(os.environ.get('FRIDAY_VOICE_DEBUG'))
        def _vlog(msg):
            if not _voice_debug:
                return
            line = f"{_time.strftime('%H:%M:%S')} {msg}\n"
            try:
                with open(_vlog_path, 'a', encoding='utf-8') as _f:
                    _f.write(line)
            except Exception:
                pass
            print(f'[live] {msg}')

        _vlog(f'=== WS connection from {request.remote_addr} ===')
        # Claim the connection generation: this handler is now the ONE owner
        # of the resume cache and renewal rights; any earlier handler still
        # alive on a half-open socket becomes a fenced-off zombie.
        _conn_gen = _live_conn_next()
        from agent_friday.routing.provider_descriptors import key_presence as _key_presence
        _key_preview = _key_presence(core.GEMINI_API_KEY)
        print(f'[live] WS connect from {request.remote_addr} | auth={session.get("authenticated")} local={_is_local_request()} | GEMINI_KEY={_key_preview}', flush=True)
        _vlog(f'session.authenticated={session.get("authenticated")} local={_is_local_request()} GEMINI_KEY={_key_preview}')

        # Auth enforcement (before_request already redirects unauthenticated HTML
        # requests, but be defensive in case /ws/ paths were excluded).
        # Loopback connections are always trusted — same-machine usage skips
        # auth so the user never hits an "unauthorized" voice error locally.
        if FRIDAY_WS_TOKEN:
            _tok = request.args.get('token', '')
            if not _hmac.compare_digest(_tok, FRIDAY_WS_TOKEN):
                _vlog('AUTH FAIL — bad/missing ws token')
                try:
                    ws.send(json.dumps({"type": "error", "error": "unauthorized"}))
                except Exception:
                    pass
                return
        _ui_t = request.args.get('t', '')
        _ui_tok_ok = _api_token_valid(_ui_t)
        if not _ws_auth_ok(_ui_tok_ok):
            _vlog('AUTH FAIL — sending unauthorized and closing')
            try:
                ws.send(json.dumps({"type": "error", "error": "unauthorized"}))
            except Exception:
                pass
            return

        # Local-only is an absolute override (the same guarantee F16 already
        # enforces for _resolve_voice_engine and _synthesize_tts_wav) — it
        # must win at the actual dispatch point too, not just in the
        # advisory /api/voice/session-info recommendation. Before this, a
        # stale tab that fetched session-info before local-only was turned
        # on (or any client that connects to /ws/live directly, bypassing
        # the recommendation) could stream mic audio and conversation text
        # to Gemini regardless of the setting.
        try:
            _ws_local_only = str(((_load_settings() or {}).get('model_routing') or {})
                                 .get('mode') or '').strip().lower() == 'local_only'
        except Exception:
            _ws_local_only = False
        if _ws_local_only:
            _vlog('REFUSED — local-only mode is on, /ws/live is a cloud path')
            try:
                ws.send(json.dumps({
                    "type": "error",
                    "error": "local-only mode is on — voice will not use "
                             "Gemini Live; use the local voice engine instead"}))
            except Exception:
                pass
            return

        # Resolve the freshest WORKING key across every source (process env,
        # settings.json, the Windows user-registry env) with a cached REST
        # probe. A long-running process whose launcher pinned a rotated key
        # self-heals here: the stale env candidate fails validation and the
        # next valid source wins — no restart required.
        _key_source = 'unknown'
        try:
            _key_info = resolve_gemini_key()
            _key_source = _key_info.get('source') or 'unknown'
            if _key_info.get('key'):
                print(f"[live] gemini key ← {_key_source} "
                      f"valid={_key_info.get('valid')}", flush=True)
                _vlog(f"key resolved from {_key_source} "
                      f"valid={_key_info.get('valid')} "
                      f"({_key_info.get('detail', '')})")
        except Exception as _kre:
            _vlog(f'key resolution failed (using existing core key): {_kre}')
        if not core.GEMINI_API_KEY:
            try:
                core.get_genai_client()
            except Exception:
                pass

        if not core.GEMINI_API_KEY:
            _vlog('ERROR — GEMINI_API_KEY not set')
            print('[live] ERROR — GEMINI_API_KEY missing; voice unavailable', flush=True)
            try:
                ws.send(json.dumps({"type": "error", "error": "GEMINI_API_KEY not set"}))
            except Exception:
                pass
            return

        try:
            from google import genai
            from google.genai import types
        except ImportError as _ie:
            _vlog(f'ERROR — google-genai not installed: {_ie}')
            try:
                ws.send(json.dumps({"type": "error", "error": "google-genai not installed"}))
            except Exception:
                pass
            return

        # Vault gating: the Live voice system instruction is sent to Google's
        # cloud servers, so it must be gated as a CLOUD provider. TIER_1 passes
        # through; TIER_2 is redacted; TIER_3 is dropped. This extends the
        # local-only vault policy to voice without breaking the experience.
        _vault_control = _get_vault_control() if _vault_local_only() else None
        _vault_fallback = _vault_cloud_fallback()
        try:
            personality = _load_agent_personality()
            full_ctx = _get_friday_system_prompt(
                provider='gemini',
                vault_control=_vault_control,
                vault_fallback=_vault_fallback,
            )
        except Exception as e:
            personality = ''
            full_ctx = f"(context load failed: {e})"
        # Cross-session continuity + tone adaptation. Per-turn semantic recall
        # isn't practical in a streaming session, but the most-recent end-of-day
        # summary and the accumulated emotional arc are session-level and apply
        # for the whole conversation — inject them so the native voice path picks
        # up open threads and adapts tone just like the text chat does.
        try:
            full_ctx += _build_session_continuity_block()
            full_ctx += _build_emotional_tone_block()
        except Exception as _mc_err:
            _vlog(f'voice memory/tone context skipped: {_mc_err}')
        if _vault_control is not None:
            _vlog('voice system prompt gated for cloud provider=gemini (vault local-only)')
        # Clean-sheet §3.1 rule 3 / §4.5: the first paragraph is the manifest's
        # self-description for the CLOUD path -- "You are Gemini Live; the
        # microphone audio is sent to Google. Questions about the user's own
        # context are answered by their local model through `ask_friday`" (or,
        # with no resident seat, that there is NO such path). Gemini has no
        # other source for what it is.
        # The real vault setting, not an assumption: with vault_local_only
        # false the user chose to let their vault reach cloud models (through
        # the privacy gate), and the context above already carries it.
        _vault_open = not _vault_local_only()
        _mind_ready = False
        try:
            _cm = _vm.get_manifest()
            _cm.refresh_selection(_load_settings() or {})
            _mind_ready = bool(_cm.snapshot_stage("mind").get("ready"))
            _cloud_self = (_cm.describe_for_model(vault_open=_vault_open)
                           if _cm.mode == "gemini" else "")
        except Exception:
            _cloud_self = ""
        live_language, live_language_name = _live_language(_get_voice_language())
        live_style = _get_voice_style_prompt()
        # Voice sets its own length and, with a persona, its own tone: the
        # text-chat settings' "be reasonably brief" / "professional" lines
        # would otherwise contradict both (services/voice_persona.py).
        full_ctx = strip_text_chat_hints(full_ctx, keep_tone=not live_style)
        voice_prefix = (
            "You are Agent Friday, a sovereign personal AI assistant.\n"
            + (_cloud_self + "\n" if _cloud_self else "")
            + "You are having a LIVE VOICE conversation — be natural and speak like a person.\n"
            + VOICE_LENGTH_RULE +
            "NEVER use markdown formatting — no asterisks, headers, or bullet points. Speak naturally.\n"
            "Use contractions. When it fits, ask a follow-up question to keep the conversation flowing.\n"
            "Never state that an action succeeded unless the tool result in this turn says so. "
            "A withheld, failed, or missing result is reported as exactly that — say what "
            "happened, not what you expected to happen. This complements, not replaces, your "
            "anti-fabrication directive.\n"
            + vault_rule(_vault_open, _mind_ready) + VOICE_CROSSTALK_RULE
            + VOICE_ENGLISH_RULE.format(language=live_language_name) + "\n"
            + VOICE_ANCHOR_RULES + "\n"
            + VOICE_TOOL_CHOREOGRAPHY
        )
        # security-boundary.md §19 row 1: this whole literal is Friday-authored
        # boilerplate with no user data, and it says the words "financial",
        # "health", "legal" — exactly the keywords the TIER-2/3 classifier
        # exists to catch in USER content. Registering it trusted (self-healing
        # every connection, same pattern as REFUSAL_HONESTY_DIRECTIVE and
        # SELF.md in model_router.py) means the new gate below classifies it
        # as public rather than over-redacting Friday's own policy text.
        try:
            from agent_friday.services.egress_gate import register_trusted_text as _rvp
            _rvp(voice_prefix)
        except Exception:
            pass
        if personality:
            voice_prefix += f"=== YOUR PERSONALITY ===\n{personality}\n\n"

        # Voice demo spec sheet: Tier 1 (public) product knowledge, injected
        # UNGATED so Gemini Live always knows what Friday IS. This sits between
        # the personality prefix and the vault-gated context — it is never
        # passed through vault_control, so it survives cloud gating intact and
        # Friday can always answer "what are you?" / "how do you work?" instead
        # of deflecting to the Sovereign Vault.
        voice_demo = _load_voice_demo()
        if voice_demo:
            voice_prefix += (
                "=== ABOUT AGENT FRIDAY (PUBLIC / ALWAYS SHAREABLE) ===\n"
                "The following is public product knowledge. You may speak any of "
                "it aloud to anyone — it is never private vault data, so never "
                "deflect these topics to the Sovereign Vault.\n\n"
                + voice_demo + "\n\n"
            )

        # Ask-first action policy for the live voice agent. The confirmed=true gate
        # on the open_url / navigate_workspace tools enforces this mechanically.
        _voice_actions_policy = (
            "=== TAKING ACTIONS (ASK FIRST) ===\n"
            "Before you open a URL or switch the on-screen workspace, ASK the user "
            "out loud for permission and wait for them to say yes — unless they "
            "JUST asked you to do exactly that in their previous message. Only call "
            "those tools with confirmed=true after the user has agreed (or just "
            "requested it). While the action runs, stop talking; once it's done, "
            "tell the user plainly what happened ('Done — I've opened it for you.'). "
            "If it fails, say so and offer another approach. Only open links that "
            "came from real data you were given — never a URL you guessed.\n\n"
        )
        try:
            from agent_friday.services.egress_gate import register_trusted_text as _rap
            _rap(_voice_actions_policy)
        except Exception:
            pass
        voice_prefix += _voice_actions_policy

        try:
            ws.send(json.dumps({"type": "status", "text": "loading context"}))
        except Exception:
            return

        # NOTE: the Live client is created lazily inside runner() per API version.
        # v1alpha unlocks affective dialog + proactive audio, but the AI Studio
        # API-key tier sometimes rejects it with a 1008 "Expected OAuth 2 access
        # token" auth error — so we try v1alpha first, then fall back to the
        # default (v1beta) endpoint, which reliably accepts API-key auth.

        live_voice = _get_live_voice()
        live_settings = _load_settings() or {}

        live_temperature = live_settings.get("voice_temperature")
        try:
            live_temperature = float(live_temperature) if live_temperature is not None else None
        except (TypeError, ValueError):
            live_temperature = None
        try:
            live_max_tokens = int(live_settings.get("voice_max_tokens") or 0)
        except (TypeError, ValueError):
            live_max_tokens = 0
        _configured_live_model = _get_live_model()
        _model_is_25 = _model_supports_affective_dialog(_configured_live_model)
        live_affective = live_settings.get("voice_affective", _model_is_25)
        if live_affective is None:
            live_affective = _model_is_25
        live_affective = bool(live_affective)
        live_proactive = live_settings.get("voice_proactive", _model_is_25)

        # Build system instruction with mood + affective dialog awareness
        try:
            from agent_friday.voice_personality import get_voice_personality
            _vp = get_voice_personality()
            _vp.affective_dialog = live_affective
            system_instruction = _vp.build_system_instruction(
                voice_prefix + full_ctx + _voice_tool_surface_note(),
                affective_dialog=live_affective, persona=bool(live_style))
        except Exception:
            system_instruction = voice_prefix + full_ctx + _voice_tool_surface_note()
        if live_proactive is None:
            live_proactive = _model_is_25
        live_proactive = bool(live_proactive)
        # Context window compression is ON by default. Without it the Live
        # session hits a hard duration cap (~15 min audio-only) and terminates,
        # losing the conversation — the opposite of the "fluid for hours"
        # requirement. The sliding window prunes only the OLDEST turns once the
        # context approaches the model limit; recent context is preserved, and
        # durable facts live in Friday's own memory subsystem. Explicit False
        # opts out. (Google Live API docs → live-session.)
        live_context_compression = live_settings.get("voice_context_compression")
        live_context_compression = (True if live_context_compression is None
                                    else bool(live_context_compression))
        # Interruption mode: barge-in is ON by default ("auto"). Only an
        # explicit no-barge opt-out disables it. See _build_realtime_input_config.
        live_interruption_mode = str(
            live_settings.get("voice_interruption_mode") or "auto").strip().lower()
        # Bridge-side barge-in is a secondary path for the explicit no-barge
        # mode (where NO_INTERRUPTION disables Gemini's own barge-in). In the
        # default interruptible modes, START_OF_ACTIVITY_INTERRUPTS gives
        # native, instant barge-in, so the bridge detector is not needed.
        live_barge_enabled = live_interruption_mode in (
            "no-barge", "no_barge", "nobarge", "speaker-safe", "speaker_safe",
            "none", "off")
        try:
            _barge_grace_ms = int(live_settings.get("voice_barge_grace_ms") or 800)
        except (TypeError, ValueError):
            _barge_grace_ms = 800
        try:
            _barge_sustain_ms = int(live_settings.get("voice_barge_sustain_ms") or 200)
        except (TypeError, ValueError):
            _barge_sustain_ms = 200

        _vlog(
            f'voice cfg: voice={live_voice}; lang={live_language or "default"}; '
            f'temp={live_temperature}; max_tokens={live_max_tokens or "inf"}; '
            f'affective={live_affective}; proactive={live_proactive}; '
            f'ctx_compress={live_context_compression}; '
            f'style={(live_style[:60] + "...") if len(live_style) > 60 else (live_style or "default")}'
        )

        speech_kwargs = {
            "voice_config": types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=live_voice)
            )
        }
        # Always set: with no language_code the model chose its own, and once
        # answered in Italian mid-conversation.
        speech_kwargs["language_code"] = live_language

        # The persona opens and closes the instruction (voice_persona), so it
        # is neither the first line of a long prompt that everything after it
        # outvotes, nor lost at the far end of the context.
        sys_text = compose_live_instruction(live_style, system_instruction)
        # Continuity, tone and the tool-surface note are appended after the
        # context, so the action policy is re-placed last and derived text
        # stripped of overrides before the gate sees the final instruction.
        from agent_friday.services.action_policy import seal_system_prompt
        sys_text = seal_system_prompt(sys_text, "Gemini Live prompt")
        # security-boundary.md §19 row 1: the egress gate, not just the
        # (possibly off) vault-assembly gate, stands between the assembled
        # context prompt and Google before it becomes system_instruction=.
        sys_text = _gate_voice_system_instruction(sys_text)
        # Hard spending cap (services/spend_guard): a NEW live session is a
        # new paid stream, so it is refused when the cap has tripped. A
        # session already open is never cut mid-sentence -- see the
        # boundary rules in spend_guard's module docstring.
        from agent_friday.services import spend_guard as _sg
        _sg.check("gemini", what="a Gemini Live voice session")

        live_cfg_kwargs = dict(
            response_modalities=[types.Modality.AUDIO],
            speech_config=types.SpeechConfig(**speech_kwargs),
            system_instruction=types.Content(parts=[types.Part(text=sys_text)]),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            # Echo/interruption + VAD tuning. Built by a helper so the
            # activity_handling / turn_coverage fields degrade gracefully on
            # older google-genai SDKs that don't define those enums yet.
            realtime_input_config=_build_realtime_input_config(
                types, live_interruption_mode, _voice_room_mode(live_settings)),
        )
        _no_barge = live_interruption_mode in (
            "no-barge", "no_barge", "nobarge", "speaker-safe", "speaker_safe",
            "none", "off")
        _vlog(f'voice interruption mode: {live_interruption_mode} '
              f'({"no-interruption / echo-safe (bridge talk-over + Esc only)" if _no_barge else "barge-in (native START_OF_ACTIVITY_INTERRUPTS)"})')
        if live_temperature is not None:
            live_cfg_kwargs["temperature"] = live_temperature
        if live_max_tokens > 0:
            live_cfg_kwargs["max_output_tokens"] = live_max_tokens
        if live_affective:
            live_cfg_kwargs["enable_affective_dialog"] = True
        if live_proactive:
            live_cfg_kwargs["proactivity"] = types.ProactivityConfig(proactive_audio=True)
        if live_context_compression:
            live_cfg_kwargs["context_window_compression"] = types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow(),
            )

        # Agentic voice: hand the Live model a real tool surface (search the news
        # feed, search the web, open a source in the browser with a highlight,
        # pull a source's trust score, deep-dive a story, switch workspaces, check
        # the wiki). When Gemini returns a tool_call, writer() executes it and
        # send_tool_response()s the result back so Friday speaks from real data —
        # this is what makes News Anchor Mode a true agent, not a scripted reader.
        # Default on; settable via voice_tools. The deterministic _voice_actions_for
        # path still runs every turn as a belt-and-suspenders fallback for nav/open.
        live_voice_tools = live_settings.get("voice_tools", True)
        if not live_voice_tools:
            _log.warning("voice live tools DISABLED by settings['voice_tools'] - "
                         "Friday will have no callable tools this session")
        # F3: one line, at session start, from the names the API is actually
        # given -- not from a comment about them. The client keeps it on
        # screen for the session; it is not a transient status.
        try:
            _reach_names = list(_voice_tool_names()) if live_voice_tools else []
        except Exception:
            _reach_names = []
        try:
            _reach = _voice_context_reach("gemini", _reach_names)
            ws.send(json.dumps({"type": "context_reach", **_reach}))
            # Clean-sheet §6.2/§7: the `contract` frame for a cloud session and
            # the persistent `cloud_voice_context_via_local` line. Neither is
            # a transient status; the client keeps both on screen.
            ws.send(json.dumps({"type": "contract", "engine": "gemini",
                                "tools": list(_reach_names),
                                "native_tools": len([n for n in _reach_names
                                                     if n != "ask_friday"]),
                                "ask_friday": "ask_friday" in _reach_names,
                                "knowledge_graph": bool(_reach.get("knowledge_graph")),
                                "memory": bool(_reach.get("memory")),
                                "line": _reach.get("line") or
                                (f"{len(_reach_names)} native tools; no path to your context")}))
            if "ask_friday" in _reach_names:
                ws.send(json.dumps({
                    "type": "error-nonfatal", "code": "cloud_voice_context_via_local",
                    "message": ("Gemini Live is speaking; questions about your own "
                                "context are answered by Friday's local model and "
                                "relayed after the privacy gate."),
                    "action": None}))
        except Exception:
            pass
        # F6: a new session's byte total starts at zero here; the legs that
        # follow only add to it.
        try:
            from agent_friday.services import voice_indicator as _vi0
            _vi0.record_cloud_session(provider="google-gemini", label="Gemini Live",
                                      event="start")
        except Exception:
            pass
        if live_voice_tools:
            try:
                _vtools = _build_voice_live_tools(types)
                if _vtools:
                    live_cfg_kwargs["tools"] = _vtools
                    # Log the RESOLVED names, not the native table's length --
                    # an undercount here is how a surface drifts unnoticed.
                    _vnames = _voice_tool_names()
                    _vlog(f'voice tools enabled: {len(_vnames)} declarations')
                    _log.info("voice live tools enabled: %d declarations (%s)",
                              len(_vnames), ", ".join(_vnames))
                else:
                    _log.warning("voice live tools: builder returned NOTHING - "
                                 "this session runs tool-free and will narrate "
                                 "actions it cannot take")
            except Exception as _te:
                _vlog(f'voice tools build failed (continuing tool-free): {_te}')
                _log.error("voice live tools build FAILED (continuing tool-free): %s",
                           _te, exc_info=True)

        # Session resumption: ask Gemini to emit resumption handles. A single Live
        # session is capped (~10-15 min of audio, plus a context-window cap); when
        # it ages out Gemini sends GoAway and ends the stream. With a handle in hand
        # the reconnect loop in runner() transparently renews the session mid-call
        # instead of the audio just stopping. Captured in writer(), replayed below.
        _supports_resumption = hasattr(types, 'SessionResumptionConfig')
        if _supports_resumption:
            live_cfg_kwargs["session_resumption"] = types.SessionResumptionConfig()

        # Calendar, mail and news are the reads a spoken session asks for
        # first, and each is a multi-second network round trip. Start them now,
        # in the background, so the first "what's on today?" answers from
        # memory instead of from Google while the caller waits.
        if live_voice_tools:
            try:
                from agent_friday.services.voice_engine import warm_voice_reads
                warm_voice_reads()
            except Exception as _we:
                _log.info("voice warm-up not started: %s", _we)

        # The per-attempt LiveConnectConfig is built inside runner() from
        # live_cfg_kwargs (affective/proactive stripped per endpoint+model).

        done = threading.Event()

        # The thread the user has OPEN, carried by the client on the socket, and
        # rebound below if the user switches conversations mid-call. Same contract as
        # /ws/voice-local. None means "no open thread", which
        # _persist_voice_turn resolves to Main as an explicit fallback.
        _open_cid = [(request.args.get('conversation_id') or '').strip() or None]

        def _safe_send(obj):
            if done.is_set():
                return False
            try:
                ws.send(json.dumps(obj))
                return True
            except ConnectionClosed:
                done.set()
                return False
            except Exception:
                return False

        async def runner():
            # The actual connection happens inside `async with`, so the fallback
            # must wrap the entire session block, not just the connect() call.
            configured_live_model = _get_live_model()

            # Build an ordered attempt plan of (api_version, model_name).
            #  • v1alpha is tried first ONLY when affective/proactive would actually
            #    be used (those features require v1alpha). If v1alpha rejects the
            #    API key (1008 "Expected OAuth 2 access token"), we fall through.
            #  • The default endpoint (api_version=None → v1beta) reliably accepts
            #    API-key auth; affective/proactive are stripped there.
            attempts = []
            _seen = set()
            def _add_attempt(api_version, model_name):
                key = (api_version, model_name)
                if key not in _seen:
                    _seen.add(key)
                    attempts.append(key)

            _primary_affective = live_affective and _model_supports_affective_dialog(configured_live_model)
            # v1alpha exists in this list ONLY to carry affective/proactive.
            # A 3.8 model takes neither (affective is not its feature,
            # proactivity is not a field it has), so asking for v1alpha on its
            # behalf spends an attempt — and a possible 1008 "Expected OAuth 2
            # access token" — to deliver nothing.
            _primary_proactive = live_proactive and _model_supports_proactivity_config(
                configured_live_model)
            if _primary_affective or _primary_proactive:
                _add_attempt("v1alpha", configured_live_model)
            _add_attempt(None, configured_live_model)
            for _fallback in (LIVE_MODEL_FALLBACK, LIVE_MODEL_FALLBACK2,
                              LIVE_MODEL_FALLBACK3):
                _add_attempt(None, _fallback)

            # Lazily create (and cache) a client per API version.
            _clients = {}
            def _client_for(api_version):
                if api_version not in _clients:
                    if api_version:
                        _clients[api_version] = genai.Client(
                            api_key=core.GEMINI_API_KEY, http_options={"api_version": api_version})  # pragma: allowlist secret
                    else:
                        _clients[api_version] = genai.Client(api_key=core.GEMINI_API_KEY)  # pragma: allowlist secret
                return _clients[api_version]

            last_error = None
            attempt_errors = []   # (model, api_version, kind, message) per failure

            # ── Per-CONVERSATION state, hoisted ABOVE the attempt loop. A
            # mid-conversation fallback (renewal handle rejected, model retired
            # under us) must keep the transcript buffers, the distill log and
            # the greeted flag — the old per-attempt reset made Friday blurt a
            # fresh greeting into the middle of an ongoing call and dropped the
            # accumulated turn log. ──
            _audio_chunks_received = 0
            _gemini_chunks_received = 0
            _audio_bytes_to_gemini = 0
            _audio_bytes_from_gemini = 0
            _safe_send_failures = 0
            in_buf = []
            out_buf = []
            turn_log = []
            resume_handle = [None]   # newest session-resumption handle from Gemini
            _handle_model = [None]   # model id the handle belongs to (handles don't cross models)
            greeted = [False]
            # Liveness bookkeeping (single-element lists so closures can mutate).
            _last_gemini_ts = [_time.time()]  # last time ANY chunk arrived from Gemini
            _last_speech_ts = [0.0]           # last time browser mic audio looked like speech
            _model_speaking = [False]         # audio parts seen since last turn_complete
            _goaway_drain_deadline = [None]   # set when GoAway arrives mid-response
            # Barge-in (speaker mode): detector + per-turn drop flag + cooldown.
            _barge = LiveBargeDetector(grace_ms=_barge_grace_ms,
                                       sustain_ms=_barge_sustain_ms)
            _barged_turn = [False]            # swallow this turn's remaining audio
            _last_barge_ts = [0.0]
            _tool_inflight = [0]              # voice tool runs currently executing
            _tool_tasks = set()               # their asyncio tasks (kept referenced)
            # Turn timing, logged at INFO for every turn: how long after the
            # user's last transcribed words the first audio came back. A stall
            # like the 40 s one this replaced is then one grep away.
            _user_words_ts = [0.0]
            _turn_timed = [True]
            _turn_tools = []
            # What this call has offered and said, for the voice tools
            # (services/voice_engine.py): the news tools skip stories Friday
            # has already told instead of reading the same five again.
            _voice_session = {"news_offered": [], "spoken": []}
            # Room mode (Settings: several people talking): a reply to speech
            # that was not said to Friday is kept off the speakers and the
            # transcript. The model still answers everything it hears; this
            # is the bridge deciding what is voiced (see _addressed).
            _room = _voice_room_mode(live_settings) == "room"
            _quiet_turn = [False]
            _friday_done_ts = [None]          # when Friday last finished a voiced reply
            _leg_ended_ts = [None]            # when the previous Gemini leg closed (seam replay)
            # The saved persona is repeated to the model after every reconnect
            # and every PERSONA_REPIN_EVERY_TURNS voiced replies (voice_persona).
            _persona_note = persona_reminder(live_style)
            _voiced_turns = [0]
            # The barge window must track CLIENT PLAYBACK, not model streaming:
            # Gemini generates faster than real-time, so the turn often finishes
            # streaming seconds before Friday's voice finishes coming out of the
            # speakers — and that's exactly when users interrupt. The client
            # reports playback transitions ({type:'speaking',on}); older clients
            # (the PWA) that don't are covered by an estimate accumulated from
            # forwarded audio bytes (24 kHz mono PCM16 → 48000 bytes/second).
            _client_playing = [False]
            _client_signal_seen = [False]
            _est_play_end_ts = [0.0]

            def _play_window_open(now):
                if _client_signal_seen[0]:
                    return _client_playing[0]
                return _model_speaking[0] or now < _est_play_end_ts[0]

            def _flush_turn():
                user_text = ''.join(in_buf).strip()
                agent_text = ''.join(out_buf).strip()
                in_buf.clear()
                out_buf.clear()
                if not user_text and not agent_text:
                    return
                if agent_text:
                    _voice_session["spoken"].append(agent_text)
                    del _voice_session["spoken"][:-60]
                try:
                    _persist_voice_turn(user_text, agent_text,
                                        conversation_id=_open_cid[0])
                except Exception as e:
                    print(f'[live] persist_voice_turn error: {e}')
                _safe_send({
                    "type": "voice_turn_done",
                    "user_text": user_text,
                    "agent_text": agent_text,
                })
                turn_log.append((user_text, agent_text))
                # Voice is an agent too: run the same deterministic
                # open/navigate intent detection the text chat uses. UI
                # navigation is sent to the browser to execute via the
                # action bus; OS opens (folders/apps) are performed
                # server-side inside the helper. Best-effort — an action
                # must never break the voice turn.
                try:
                    _vacts = _voice_actions_for(user_text)
                    if _vacts:
                        _safe_send({"type": "action", "actions": _vacts})
                except Exception as _ae:
                    print(f'[live] voice action dispatch error: {_ae}')

            for api_version, model_name in attempts:
                # affective dialog + proactive audio are only valid on native-audio
                # models AND only on the v1alpha endpoint. Strip them otherwise so a
                # user who has the toggle on doesn't see the standard endpoint/model
                # fail with 1011 (unsupported field) or 1008 (auth).
                use_affective = (api_version == "v1alpha" and live_affective
                                 and _model_supports_affective_dialog(model_name))
                use_proactive = (api_version == "v1alpha" and live_proactive
                                 and _model_supports_proactivity_config(model_name))
                per_model_kwargs = dict(live_cfg_kwargs)
                if not use_affective:
                    per_model_kwargs.pop("enable_affective_dialog", None)
                if not use_proactive:
                    per_model_kwargs.pop("proactivity", None)
                # Gemini 3.8 Live: thinking_config is mandatory on the
                # extended-thinking variant and fatal on the plain one, so it
                # is decided per attempt rather than once for the session —
                # the fallback chain can hand this loop either kind.
                _think_level = None
                if _model_requires_thinking_config(model_name):
                    _think_level = resolve_live_thinking_level(
                        live_settings.get("voice_thinking_level"))
                    try:
                        per_model_kwargs["thinking_config"] = types.ThinkingConfig(
                            thinking_level=_think_level)
                    except Exception as _tce:
                        # No ThinkingConfig in this SDK means this model cannot
                        # be connected at all. Skip it rather than send a bare
                        # config the server rejects with a message about
                        # thinking levels, which reads like a model outage.
                        _vlog(f'{model_name} needs thinking_config and this '
                              f'google-genai cannot build one ({_tce}); skipping')
                        continue
                elif _model_rejects_thinking_config(model_name):
                    per_model_kwargs.pop("thinking_config", None)
                # Same shape for tools: extended-thinking refuses BLOCKING
                # function calls, so its declarations are re-rendered
                # NON_BLOCKING. Only done when required — rebuilding the whole
                # tool surface for every attempt would be wasted work, and
                # plain 3.8-live accepts either mode.
                if (per_model_kwargs.get("tools")
                        and _model_requires_non_blocking_tools(model_name)):
                    try:
                        _nb_tools = _build_voice_live_tools(types, behavior="NON_BLOCKING")
                        if _nb_tools:
                            per_model_kwargs["tools"] = _nb_tools
                    except Exception as _nbe:
                        _vlog(f'NON_BLOCKING tool rebuild failed for {model_name}: {_nbe}')
                _vlog(f'connecting to model: {model_name} (api={api_version or "default(v1beta)"}, '
                      f'affective={use_affective}, proactive={use_proactive}'
                      + (f', thinking={_think_level}' if _think_level else '') + ')')
                try:
                    # Config/client construction stays INSIDE the per-attempt
                    # try: LiveConnectConfig is a pydantic model that raises
                    # ValidationError on fields an older google-genai lacks —
                    # outside the try, that skipped the error frame entirely
                    # and the browser hung on a dead socket with no message.
                    per_model_cfg = types.LiveConnectConfig(**per_model_kwargs)
                    active_client = _client_for(api_version)
                    async def _fire_barge(sess, source):
                        # The user is talking over Friday. Stop her at every
                        # layer we control: (1) swallow the rest of this turn's
                        # audio server-side, (2) flush what the browser has
                        # buffered (its 'interrupted' handler already does
                        # exactly that), (3) cancel the in-flight generation —
                        # per the Live API reference, a client_content message
                        # "will interrupt any current model generation", and
                        # it is the ONLY documented client-side cancel (VAD
                        # can't help under NO_INTERRUPTION). turn_complete=
                        # False so the marker joins the user's in-progress
                        # utterance instead of demanding its own response.
                        _last_barge_ts[0] = _time.time()
                        # Swallow streamed audio only if the turn is actually
                        # still streaming — if generation already finished
                        # (faster than real-time) the leftover flag would eat
                        # the NEXT turn's audio.
                        _barged_turn[0] = _model_speaking[0]
                        _client_playing[0] = False
                        _est_play_end_ts[0] = 0.0
                        _barge.sustained = 0.0
                        _vlog(f'BARGE-IN ({source}): dropping turn audio, flushing client, cancelling generation')
                        _safe_send({"type": "interrupted"})
                        _safe_send({"type": "status", "text": "listening"})
                        try:
                            await sess.send_client_content(
                                turns={"role": "user", "parts": [{"text":
                                    "[The user interrupted you mid-response — "
                                    "stop talking and listen to what they say "
                                    "next.]"}]},
                                turn_complete=False,
                            )
                        except Exception as _be:
                            _vlog(f'barge client_content send failed: {_be}')

                    async def _run_tool_calls(sess, tc):
                        # Gemini asked to call one or more tools. Execute each in a
                        # worker thread (the handlers do blocking network/LLM work),
                        # then send_tool_response() the results back so the model
                        # speaks from real data. Side effects (UI navigate, citation
                        # chips) are emitted to the browser from inside _voice_tool_run.
                        #
                        # The calls in one message run CONCURRENTLY. Gemini
                        # can ask for several at once ("any urgent email, and
                        # what's in the news?") and waits for every answer
                        # before it speaks, so running them one after another
                        # made the silence the SUM of the tools (measured: 9 s
                        # + 31 s = 40 s) instead of the slowest one.
                        fcs = getattr(tc, 'function_calls', None) or []

                        async def _one(fc):
                            fname = getattr(fc, 'name', '') or ''
                            try:
                                fargs = dict(getattr(fc, 'args', None) or {})
                            except Exception:
                                fargs = {}
                            fid = getattr(fc, 'id', None)
                            _vlog(f'TOOL CALL: {fname}({fargs})')
                            # Unconditional: _vlog is gated behind
                            # FRIDAY_VOICE_DEBUG and the tray DEVNULLs stdio,
                            # so without this a voice tool call (or its
                            # failure) is invisible after the fact.
                            _log.info("voice tool call: %s(%s)", fname, fargs)
                            _turn_tools.append(fname)
                            _safe_send({"type": "status", "text": f"⚙ {fname}"})
                            # PROCESS ORB -- the execution receipt. Every text
                            # tool call registers one (services/agent.py
                            # _orb_tool_trace); the live voice path registered
                            # none, so "no orb appeared" could not distinguish
                            # a tool that never ran from one that ran silently.
                            # Now it can: an orb is proof of execution, and its
                            # absence is proof of narration.
                            _orb_id, _orb_t0 = _voice_orb_start(fname), _time.time()
                            try:
                                result = await _voice_tool_with_limit(
                                    fname, fargs, _safe_send, _voice_session)
                            except Exception as _te:
                                _log.error("Voice tool %r failed: %s", fname, _te, exc_info=True)
                                result = (f"I hit a problem with the {fname} tool "
                                          f"({type(_te).__name__}). Please try again.")
                            if not isinstance(result, str):
                                result = str(result)
                            result = result[:8000]
                            # Tool results are CLOUD EGRESS: emails, wiki
                            # excerpts, calendar attendees ship to Google here.
                            # Run the same gate as the text-chat path; a fully
                            # withheld result becomes an explanatory marker so
                            # the model reports the withholding instead of
                            # retrying the tool. Fail-closed — see
                            # `_gate_voice_tool_result`'s docstring for the N-1
                            # bug this replaced (a broad `except` that let a
                            # NeverSendBlocked verdict fall through to the
                            # ungated result).
                            result = _gate_voice_tool_result(result, fname)
                            _took = _time.time() - _orb_t0
                            result = _mark_if_stale(result, fname, _orb_t0,
                                                    _user_words_ts[0], _time.time())
                            _log.info("voice tool result: %s -> %d chars in %.2fs",
                                      fname, len(result or ''), _took)
                            if _took > VOICE_TOOL_SLOW_S:
                                _log.warning("voice tool %s took %.1fs; the caller heard "
                                             "silence for that long", fname, _took)
                            _voice_orb_finish(_orb_id, fname, fargs, result, _took * 1000.0)
                            _kw = {"name": fname, "response": {"result": result}}
                            if fid is not None:
                                _kw["id"] = fid
                            try:
                                return types.FunctionResponse(**_kw)
                            except Exception as _fe:
                                _vlog(f'FunctionResponse build failed: {_fe}')
                                return None

                        frs = await _run_calls_concurrently(fcs, _one)
                        if frs:
                            try:
                                await sess.send_tool_response(function_responses=frs)
                                _vlog(f'sent {len(frs)} tool response(s) back to Gemini')
                            except Exception as _se:
                                _vlog(f'send_tool_response failed: {_se}')
                                _log.error("voice send_tool_response failed: %s", _se,
                                           exc_info=True)

                    # reader()/writer() are bound to ONE Gemini session via `sess`
                    # and stop on either `done` (browser closed — terminal) or
                    # `sdone` (this session leg ended — GoAway/timeout/drop, renew).
                    async def reader(sess, sdone):
                        nonlocal _audio_chunks_received, _audio_bytes_to_gemini
                        while not done.is_set() and not sdone.is_set():
                            try:
                                raw = await asyncio.to_thread(ws.receive, 1.0)
                            except ConnectionClosed:
                                _vlog('reader: ConnectionClosed from browser')
                                done.set()
                                return
                            except Exception as e:
                                continue
                            if raw is None:
                                continue
                            if isinstance(raw, bytes):
                                try:
                                    raw = raw.decode('utf-8')
                                except Exception:
                                    continue
                            try:
                                msg = json.loads(raw)
                            except Exception:
                                continue
                            t = msg.get('type')
                            try:
                                if t == 'audio' and msg.get('data'):
                                    data = base64.b64decode(msg['data'])
                                    _audio_chunks_received += 1
                                    _audio_bytes_to_gemini += len(data)
                                    _rms = _quick_rms(data)
                                    if _rms >= LIVE_SPEECH_RMS:
                                        _last_speech_ts[0] = _time.time()
                                    # Bridge-side barge-in (speaker mode): under
                                    # NO_INTERRUPTION Gemini never stops on its
                                    # own, so deliberate talk-over is detected
                                    # HERE, on the raw mic feed, for as long as
                                    # Friday's voice is coming out of the
                                    # speakers (client playback window — NOT
                                    # model streaming, which ends much earlier).
                                    _now_b = _time.time()
                                    if (live_barge_enabled and _play_window_open(_now_b)
                                            and (_now_b - _last_barge_ts[0]) > LIVE_BARGE_COOLDOWN_S):
                                        # PCM16 @ 16 kHz → 32 bytes per ms.
                                        if _barge.feed(_rms, len(data) / 32.0, now=_now_b):
                                            await _fire_barge(sess, f'talk-over rms={_rms}')
                                    if _audio_chunks_received in (1, 5, 25) or _audio_chunks_received % 50 == 0:
                                        # Log RMS amplitude so we can tell speech from silence.
                                        try:
                                            import struct as _st
                                            _n = len(data) // 2
                                            if _n > 0:
                                                _samples = _st.unpack(f'<{_n}h', data)
                                                _peak = max(abs(s) for s in _samples)
                                                _sumsq = sum(s * s for s in _samples)
                                                _rms = int((_sumsq / _n) ** 0.5)
                                            else:
                                                _peak = _rms = 0
                                        except Exception:
                                            _peak = _rms = -1
                                        _vlog(f'browser->gemini: chunk #{_audio_chunks_received} ({len(data)} bytes, total {_audio_bytes_to_gemini}, rms={_rms}, peak={_peak})')
                                    await sess.send_realtime_input(
                                        audio=types.Blob(data=data, mime_type='audio/pcm;rate=16000')
                                    )
                                elif t == 'image' and msg.get('data'):
                                    data = base64.b64decode(msg['data'])
                                    await sess.send_realtime_input(
                                        video=types.Blob(data=data, mime_type='image/jpeg')
                                    )
                                elif t == 'text' and msg.get('text'):
                                    _vlog(f'browser->gemini: text {msg["text"]!r}')
                                    # Typed turns are cloud egress like any chat
                                    # message — gate them, and never forward an
                                    # emptied string (Gemini treats it as noise).
                                    # Fail-closed — see `_gate_voice_text`'s
                                    # docstring for the N-1-sibling bug this
                                    # replaced (a bare `except: pass` that let
                                    # the ungated typed message through).
                                    _txt = _gate_voice_text(msg['text'])
                                    await sess.send_realtime_input(text=_txt)
                                elif t == 'barge':
                                    # EXPLICIT interrupt from the client (Escape
                                    # key). Trust it unconditionally — no play-
                                    # window gate: the client's own local flush
                                    # may have already sent {'speaking',off}
                                    # ahead of this frame, which would close the
                                    # window and turn the barge into a no-op
                                    # (the reviewed Escape-defeats-itself bug).
                                    # Short manual cooldown only.
                                    if (_time.time() - _last_barge_ts[0]) > 0.5:
                                        await _fire_barge(sess, 'client request')
                                elif t == 'conversation':
                                    # He switched threads while the mic was
                                    # live. Voice follows the conversation on
                                    # screen, so retarget from here on.
                                    _open_cid[0] = (msg.get('id') or '').strip() or None
                                elif t == 'speaking':
                                    # Client playback transition — the precise
                                    # barge window. A closed→open transition is
                                    # the start of audible output: relearn the
                                    # speaker-bleed baseline from that moment.
                                    _client_signal_seen[0] = True
                                    _on = bool(msg.get('on'))
                                    if _on and not _client_playing[0]:
                                        _barge.reset_turn()
                                    if not _on:
                                        _barge.sustained = 0.0
                                    _client_playing[0] = _on
                                elif t == 'end':
                                    _vlog('reader: browser sent end signal')
                                    # Explicitly flush audio stream so Gemini stops waiting for VAD.
                                    try:
                                        await sess.send_realtime_input(audio_stream_end=True)
                                        _vlog('sent audio_stream_end=True to gemini')
                                    except Exception as _e:
                                        _vlog(f'audio_stream_end send failed: {_e}')
                                    # Deliberate stop: forget the resumption handle so
                                    # the user's NEXT voice session starts fresh instead
                                    # of resuming this one.
                                    _live_resume_clear(gen=_conn_gen)
                                    done.set()
                                    return
                                elif t == 'bye':
                                    # Deliberate client stop (mic button) WITHOUT the
                                    # audio_stream_end flush — the client doesn't want
                                    # one last response generated into teardown. Clear
                                    # the resumption cache so the next session starts
                                    # fresh; an unexpected drop skips this, which is
                                    # exactly what lets it resume.
                                    _vlog('reader: browser sent bye (deliberate stop)')
                                    _live_resume_clear(gen=_conn_gen)
                                    done.set()
                                    return
                            except Exception as e:
                                _vlog(f'send-to-gemini ERROR: {type(e).__name__}: {e}')
                                traceback.print_exc()

                    async def writer(sess, sdone):
                        nonlocal _gemini_chunks_received, _audio_bytes_from_gemini, _safe_send_failures
                        try:
                            while not done.is_set() and not sdone.is_set():
                                async for chunk in sess.receive():
                                    if done.is_set() or sdone.is_set():
                                        return
                                    try:
                                        _gemini_chunks_received += 1
                                        _last_gemini_ts[0] = _time.time()
                                        # Capture the newest resumption handle so the
                                        # reconnect loop can renew this exact session —
                                        # and mirror it to the module-level cache so a
                                        # browser-side reconnect can resume it too.
                                        _sru = getattr(chunk, 'session_resumption_update', None)
                                        if _sru is not None and getattr(_sru, 'new_handle', None):
                                            resume_handle[0] = _sru.new_handle
                                            _handle_model[0] = model_name
                                            _live_resume_store(_sru.new_handle, model_name, live_voice, gen=_conn_gen)
                                        # Cost metering: the Gemini Live session is a
                                        # real, billed call and must be metered like any
                                        # other (PRICING carries rates for this exact
                                        # model). usage_metadata arrives
                                        # per-chunk on the live stream (cumulative for the
                                        # session so far, per the API's own semantics) —
                                        # metered as its own row every time it shows up
                                        # rather than only once at teardown, since a leg can
                                        # end (GoAway, error, disconnect) without a clean
                                        # close. _meter_gemini_live_chunk() never raises.
                                        _meter_gemini_live_chunk(chunk, model_name)
                                        # GoAway: Gemini is about to retire this session
                                        # (connection lifetime / context cap). Don't cut a
                                        # response mid-word: if Friday is speaking, drain
                                        # until turn_complete or the GoAway deadline, THEN
                                        # end the leg so the reconnect loop renews it via
                                        # the handle above — the user hears no break.
                                        _ga = getattr(chunk, 'go_away', None)
                                        if _ga is not None:
                                            _tl = getattr(_ga, 'time_left', None)
                                            _secs = _duration_to_seconds(_tl)
                                            _grace = max(0.5, min(_secs if _secs is not None else 3.0, 8.0))
                                            _goaway_drain_deadline[0] = _time.time() + _grace
                                            _vlog(f'GoAway from Gemini (time_left={_tl}) — draining ≤{_grace:.1f}s, then renewing via resumption handle')
                                            # A tool still running would answer a
                                            # dead leg: drain it too, within the grace.
                                            if not _model_speaking[0] and _tool_inflight[0] == 0:
                                                sdone.set()
                                                return
                                        if _gemini_chunks_received <= 5 or _gemini_chunks_received % 20 == 0:
                                            _resume = _sru
                                            _va = getattr(chunk, 'voice_activity', None) or getattr(chunk, 'voice_activity_detection_signal', None)
                                            _vlog(f'gemini chunk #{_gemini_chunks_received}: setup={chunk.setup_complete is not None} sc={chunk.server_content is not None} tool={chunk.tool_call is not None} resume={_resume is not None} va={_va is not None}')
                                        sc = getattr(chunk, 'server_content', None)
                                        if sc is not None:
                                            out_tr = getattr(sc, 'output_transcription', None)
                                            if out_tr and getattr(out_tr, 'text', None):
                                                _vlog(f'output_transcription: {out_tr.text!r}')
                                                out_buf.append(out_tr.text)
                                                if not _quiet_turn[0]:
                                                    _safe_send({"type": "text", "text": out_tr.text})
                                            in_tr = getattr(sc, 'input_transcription', None)
                                            if in_tr and getattr(in_tr, 'text', None):
                                                _vlog(f'input_transcription: {in_tr.text!r}')
                                                in_buf.append(in_tr.text)
                                                _safe_send({"type": "input_transcript", "text": in_tr.text})
                                                _user_words_ts[0] = _time.time()
                                                _turn_timed[0] = False
                                            mt = getattr(sc, 'model_turn', None)
                                            if mt and getattr(mt, 'parts', None):
                                                for part in mt.parts:
                                                    # Skip "thinking" parts (part.thought=True on
                                                    # thinking-enabled Live models): internal
                                                    # reasoning must not reach the on-screen
                                                    # transcript, out_buf, or the persisted turn.
                                                    if getattr(part, 'thought', False):
                                                        continue
                                                    # Audio: PCM bytes at 24kHz in part.inline_data.data
                                                    il = getattr(part, 'inline_data', None)
                                                    if il and getattr(il, 'data', None):
                                                        if not _model_speaking[0] and not _client_signal_seen[0]:
                                                            # New spoken response starting and
                                                            # no client playback signal —
                                                            # anchor the bleed-baseline grace
                                                            # window here (best available
                                                            # approximation of audio onset).
                                                            _barge.reset_turn()
                                                        _reply_starting = not _model_speaking[0]
                                                        _model_speaking[0] = True
                                                        if not _turn_timed[0]:
                                                            _turn_timed[0] = True
                                                            _log.info(
                                                                "voice turn: first audio %.2fs after the user's last words (%s)%s",
                                                                _time.time() - _user_words_ts[0], model_name,
                                                                (" after tools " + ", ".join(_turn_tools)) if _turn_tools else "")
                                                            _turn_tools.clear()
                                                            # Decided once, as a reply starts: words
                                                            # heard while she is already answering
                                                            # never silence a reply midway.
                                                            if _room and _reply_starting:
                                                                _heard = ''.join(in_buf).strip()
                                                                _since = (None if _friday_done_ts[0] is None
                                                                          else _time.time() - _friday_done_ts[0])
                                                                _last = (_voice_session["spoken"] or [""])[-1]
                                                                if not _addressed(_heard, _last, _since):
                                                                    _quiet_turn[0] = True
                                                                    _log.info("voice turn: not voiced, room mode and "
                                                                              "not addressed to Friday (%d chars heard)",
                                                                              len(_heard))
                                                        _audio_bytes_from_gemini += len(il.data)
                                                        if _barged_turn[0] or _quiet_turn[0]:
                                                            # User barged in — swallow the
                                                            # rest of this turn's audio so
                                                            # nothing more reaches the
                                                            # speakers.
                                                            continue
                                                        # Playback-window estimate for clients
                                                        # that don't report playback: 24 kHz
                                                        # mono PCM16 plays at 48000 bytes/s.
                                                        _est_play_end_ts[0] = max(
                                                            _est_play_end_ts[0], _time.time(),
                                                        ) + len(il.data) / 48000.0
                                                        if _audio_bytes_from_gemini <= 50000 or _gemini_chunks_received % 20 == 0:
                                                            _vlog(f'gemini->browser: audio {len(il.data)} bytes ({il.mime_type}); total {_audio_bytes_from_gemini}')
                                                        ok = _safe_send({
                                                            "type": "audio",
                                                            "data": base64.b64encode(il.data).decode('ascii'),
                                                        })
                                                        if not ok:
                                                            _safe_send_failures += 1
                                                            _vlog(f'ws.send FAILED for audio chunk (cumulative failures: {_safe_send_failures})')
                                                    pt = getattr(part, 'text', None)
                                                    if pt:
                                                        out_buf.append(pt)
                                                        if not _quiet_turn[0]:
                                                            _safe_send({"type": "text", "text": pt})
                                            if getattr(sc, 'turn_complete', False):
                                                _model_speaking[0] = False
                                                _barged_turn[0] = False
                                                _repin = False
                                                if _quiet_turn[0]:
                                                    out_buf.clear()   # never voiced: not Friday's words
                                                    _quiet_turn[0] = False
                                                elif out_buf:
                                                    _friday_done_ts[0] = _time.time()
                                                    _voiced_turns[0] += 1
                                                    _repin = persona_due(_voiced_turns[0])
                                                _vlog(f'turn_complete (audio out so far: {_audio_bytes_from_gemini} bytes)')
                                                _flush_turn()
                                                _safe_send({"type": "turn_end"})
                                                if _repin and _persona_note:
                                                    # Between turns, as a note that joins the
                                                    # user's next turn (turn_complete=False),
                                                    # so it neither interrupts nor asks for a reply.
                                                    _repin = False
                                                    try:
                                                        await sess.send_client_content(
                                                            turns={"role": "user", "parts": [{"text": _persona_note}]},
                                                            turn_complete=False)
                                                    except Exception as _pne:
                                                        _vlog(f'persona re-pin failed: {_pne}')
                                            if getattr(sc, 'interrupted', False):
                                                _model_speaking[0] = False
                                                _barged_turn[0] = False
                                                if _quiet_turn[0]:
                                                    out_buf.clear()
                                                    _quiet_turn[0] = False
                                                # The client flushes its buffer on
                                                # this signal — playback stops now.
                                                _est_play_end_ts[0] = 0.0
                                                _vlog('interrupted')
                                                _safe_send({"type": "interrupted"})
                                        # Agentic step: Gemini wants to call a tool.
                                        # Execute it and feed the result back so the
                                        # model continues the turn from real data.
                                        _tc = getattr(chunk, 'tool_call', None)
                                        if _tc is not None and getattr(_tc, 'function_calls', None):
                                            # Mark the tool run so the liveness
                                            # watchdog doesn't mistake a long
                                            # (blocking) tool call for a dead
                                            # upstream and tear down the leg
                                            # mid-run.
                                            #
                                            # The tools run as a TASK, not inline:
                                            # awaiting them here stopped this loop
                                            # reading Gemini for as long as they
                                            # ran, so transcripts, interruptions and
                                            # GoAway all queued behind a slow tool.
                                            _tool_inflight[0] += 1

                                            async def _tool_job(_s=sess, _c=_tc):
                                                try:
                                                    await _run_tool_calls(_s, _c)
                                                finally:
                                                    _tool_inflight[0] -= 1
                                                    _last_gemini_ts[0] = _time.time()

                                            _tj = asyncio.create_task(_tool_job())
                                            _tool_tasks.add(_tj)
                                            _tj.add_done_callback(_tool_tasks.discard)
                                        # Tool-call cancellation (barge-in during a
                                        # tool run): nothing to undo server-side —
                                        # the next turn supersedes it.
                                        # GoAway drain: leave once the in-flight response
                                        # finished (or the grace ran out) so the renewal
                                        # can happen before Google hard-closes the socket.
                                        if _goaway_drain_deadline[0] is not None and (
                                                (not _model_speaking[0] and _tool_inflight[0] == 0)
                                                or _time.time() >= _goaway_drain_deadline[0]):
                                            _vlog('GoAway drain complete — ending leg for renewal')
                                            sdone.set()
                                            return
                                    except Exception as e:
                                        _vlog(f'recv processing ERROR: {type(e).__name__}: {e}')
                                        _log.error("voice recv processing error: %s: %s",
                                                   type(e).__name__, e, exc_info=True)
                                        traceback.print_exc()
                                # session.receive() iterator ends after a turn; re-enter to keep listening
                                _vlog(f'receive iterator completed (after {_gemini_chunks_received} chunks), re-entering for next turn')
                        except Exception as e:
                            _vlog(f'writer EXCEPTION: {type(e).__name__}: {e}')
                        finally:
                            _vlog(f'writer leg done. stats: gemini_chunks={_gemini_chunks_received}, audio_in_bytes={_audio_bytes_to_gemini}, audio_out_bytes={_audio_bytes_from_gemini}, send_fails={_safe_send_failures}')
                            # End THIS leg only. Whether the whole connection is over
                            # (done) is decided by reader/GoAway, not by the receive
                            # stream ending — an unexpected stream end with a handle in
                            # hand should renew, not terminate.
                            sdone.set()

                    async def no_audio_watchdog():
                        # If the browser sends zero audio chunks within 5s of the
                        # session opening, log a clear warning. This catches "WS
                        # connected but mic never streams" cases that otherwise
                        # look identical to "user just isn't talking yet".
                        try:
                            await asyncio.sleep(5.0)
                        except asyncio.CancelledError:
                            return
                        if done.is_set():
                            return
                        if _audio_chunks_received == 0:
                            _vlog('WARNING: no audio chunks received from browser after 5s — mic likely silent or WS not flowing')
                            _safe_send({"type": "status", "text": "no mic audio reaching server"})

                    async def liveness_watchdog(sdone):
                        # Catches the silent-hang failure mode: the upstream
                        # Gemini socket half-dies (no FIN — sleep/wake, NAT drop,
                        # proxy), writer sits in receive() forever, and the user
                        # keeps talking into a void while the UI still says
                        # "live". With input transcription enabled Gemini streams
                        # transcript events WHILE the user talks, so sustained
                        # speech with zero upstream traffic means the leg is sick
                        # → end it; the reconnect loop renews via the handle and
                        # the conversation continues where it left off.
                        while not done.is_set() and not sdone.is_set():
                            try:
                                await asyncio.sleep(5.0)
                            except asyncio.CancelledError:
                                return
                            if done.is_set() or sdone.is_set():
                                return
                            if _tool_inflight[0] > 0:
                                # A voice tool is executing (blocking network /
                                # LLM work awaited inline by the writer) — the
                                # upstream isn't stalled, the writer is busy.
                                # Tearing down here would drop the tool result.
                                continue
                            now = _time.time()
                            quiet_s = now - _last_gemini_ts[0]
                            speech_ended_s = now - _last_speech_ts[0]
                            speech_after_quiet = _last_speech_ts[0] > _last_gemini_ts[0] + 5.0
                            # Fire ONLY after the user has FINISHED talking and
                            # Gemini still hasn't answered. The old "user is
                            # speaking + upstream quiet" condition false-fired
                            # in the middle of long monologues (models that
                            # don't stream input transcription mid-turn look
                            # "quiet" the whole time), and every needless
                            # renewal desynced the conversation.
                            if (quiet_s > LIVE_STALL_SECONDS
                                    and speech_after_quiet
                                    and 8.0 <= speech_ended_s <= 90.0):
                                _vlog(f'liveness watchdog: user speaking but no Gemini traffic for {quiet_s:.0f}s — forcing leg renewal')
                                _log.warning("voice: no answer from Gemini %.0fs after the user spoke; "
                                             "renewing the session (%s)", quiet_s, model_name)
                                _safe_send({"type": "status", "text": "connection stalled — renewing"})
                                sdone.set()
                                return

                    async def heartbeat(sdone):
                        # Server→browser beat. The client force-reconnects when
                        # nothing (not even this) arrives for ~3 beats, which is
                        # how a half-open browser socket gets detected; and a DEAD
                        # browser socket surfaces here as a failed send
                        # (ConnectionClosed → done) even while Gemini is quiet.
                        while not done.is_set() and not sdone.is_set():
                            try:
                                await asyncio.sleep(LIVE_HEARTBEAT_SECONDS)
                            except asyncio.CancelledError:
                                return
                            if done.is_set() or sdone.is_set():
                                return
                            _safe_send({"type": "hb", "ts": int(_time.time())})

                    # ── Reconnect loop ──────────────────────────────────────────
                    # A single Gemini Live CONNECTION is capped (~10 min); the
                    # logical session outlives it via resumption handles, and with
                    # context compression on there is no session-duration cap —
                    # together that is what makes an hours-long call possible.
                    # Legs end on GoAway, stream end, or watchdog stall; each end
                    # renews the session with the freshest handle so Gemini
                    # restores the conversation server-side and the user hears no
                    # seam. A brand-new WS connection can also RESUME a
                    # conversation whose browser socket dropped (client
                    # auto-reconnect) via the module-level handle cache.
                    if resume_handle[0] is None and _supports_resumption:
                        _stored = _live_resume_load(model_name, live_voice)
                        if _stored:
                            resume_handle[0] = _stored
                            _handle_model[0] = model_name
                            greeted[0] = True   # mid-conversation — never re-greet
                            _vlog('browser reconnect: resuming previous conversation from stored handle')

                    leg = 0
                    _quick_deaths = 0   # consecutive legs that died <10s after connect
                    while not done.is_set():
                        # Local-only is checked once at connect, above -- but
                        # THIS loop exists specifically to keep one logical
                        # call alive across many ~10-min Gemini legs, and a
                        # renewal re-dials Gemini exactly like the first
                        # connect did. Without rechecking here, turning
                        # local-only on mid-call has no effect on a call
                        # already in progress: audio keeps streaming to
                        # Gemini for as long as the call runs, which the
                        # renewal loop's own docstring says can be hours.
                        try:
                            _renewal_local_only = str(
                                ((_load_settings() or {}).get('model_routing') or {})
                                .get('mode') or '').strip().lower() == 'local_only'
                        except Exception:
                            _renewal_local_only = False
                        if _renewal_local_only:
                            _vlog('local-only mode turned on mid-call — ending this Gemini Live call instead of renewing')
                            _safe_send({"type": "status",
                                       "text": "local-only mode is on — ending this call; "
                                                "use the local voice engine instead"})
                            done.set()
                            break
                        # Zombie fence: if a NEWER /ws/live handler has taken
                        # over (browser reconnected while this handler's socket
                        # is half-open), stop renewing — fighting the new
                        # handler for the Gemini session corrupts the handle
                        # cache and duplicates the conversation. Every OTHER
                        # way this loop ends a call the browser didn't ask to
                        # end (local-only above, the giveup/GoAway paths
                        # below) sends a status/error frame before done.set()
                        # -- _safe_send() itself no-ops once done is set, so
                        # this is the one exit that must send first. Without
                        # it, opening voice mode in a second tab silently
                        # killed the first tab's call with no signal at all.
                        if not _live_conn_current(_conn_gen):
                            _vlog('superseded by a newer voice connection — zombie handler exiting')
                            _safe_send({"type": "status",
                                       "text": "this call ended — voice was opened in another "
                                                "window or tab"})
                            done.set()
                            break
                        _use_handle = (resume_handle[0]
                                       if (_supports_resumption
                                           and resume_handle[0] is not None
                                           and _handle_model[0] == model_name)
                                       else None)
                        # Connect this leg. When we hold a handle, ride a short
                        # retry ladder (handle, handle, fresh) — a renewal seam
                        # must survive a transient connect failure instead of
                        # killing an hours-long call or falling back to another
                        # model mid-conversation.
                        session_cm = None
                        session_ai = None
                        _max_tries = 3 if _use_handle else 1
                        for _try in range(1, _max_tries + 1):
                            _with_handle = _use_handle if (_use_handle and _try < _max_tries) else None
                            if _with_handle:
                                _cfg_try = _leg_config(types, per_model_kwargs, _with_handle)
                            else:
                                _cfg_try = per_model_cfg
                            if _use_handle and not _with_handle:
                                # Last rung: the handle keeps getting rejected —
                                # drop it. A fresh session loses Gemini-side
                                # context (the transcript log survives here) but
                                # beats hanging up on the user.
                                _vlog('renewal: handle attempts failed — reconnecting FRESH (server context resets)')
                                _safe_send({"type": "status", "text": "reconnecting"})
                                resume_handle[0] = None
                            try:
                                session_cm = active_client.aio.live.connect(model=model_name, config=_cfg_try)
                                session_ai = await session_cm.__aenter__()
                                break
                            except Exception as _ce:
                                session_cm = None
                                _ckind = _classify_live_error(str(_ce))
                                _vlog(f'leg connect failed (try {_try}/{_max_tries}) [{_ckind}]: {_ce}')
                                if (_try >= _max_tries or done.is_set()
                                        or _ckind in ('auth', 'model-missing')):
                                    raise
                                await asyncio.sleep(float(_try))
                        if session_ai is None:
                            break

                        _leg_started = _time.time()
                        _leg_bytes_at_start = _audio_bytes_to_gemini
                        _record_mic_audio_egress("open")
                        # F6: name the provider receiving the microphone, on
                        # screen, for as long as the leg is open. Written from
                        # the served path (this leg just connected), never
                        # from the engine setting.
                        try:
                            from agent_friday.services import voice_indicator as _vi
                            _vi.record_cloud_session(
                                provider="google-gemini", label="Gemini Live",
                                event="open", model=model_name)
                        except Exception:
                            pass
                        _safe_send({"type": "egress_notice",
                                    "provider": "google-gemini",
                                    "label": "Google (Gemini Live)",
                                    "field": "mic_audio",
                                    "text": "Microphone audio is streaming to Google (Gemini Live)."})
                        try:
                            if leg == 0 and _use_handle:
                                _vlog(f'session resumed with {model_name} from stored handle')
                            elif leg == 0:
                                _vlog(f'session established with {model_name}')
                            else:
                                _vlog(f'session renewed with {model_name} (renewal #{leg})')

                            # Renewal/resume seam hygiene: mic audio that piled
                            # up in the socket buffer while we were between
                            # sessions must NOT be burst-fed into the fresh
                            # session — Gemini would transcribe a flood of
                            # half-minute-old speech and answer utterances the
                            # user has already moved past ("two parallel
                            # conversations"). Drain and drop stale audio;
                            # honor any control frames found in the backlog.
                            if leg > 0 or _use_handle:
                                _stale_audio = 0
                                _seam_chunks = []
                                while not done.is_set():
                                    try:
                                        _raw0 = ws.receive(timeout=0)
                                    except Exception:
                                        break
                                    if _raw0 is None:
                                        break
                                    try:
                                        if isinstance(_raw0, bytes):
                                            _raw0 = _raw0.decode('utf-8')
                                        _m0 = json.loads(_raw0)
                                    except Exception:
                                        continue
                                    _t0 = _m0.get('type')
                                    if _t0 == 'audio':
                                        _stale_audio += 1
                                        try:
                                            _seam_chunks.append(base64.b64decode(_m0.get('data') or ''))
                                        except Exception:
                                            pass
                                    elif _t0 in ('bye', 'end'):
                                        _live_resume_clear(gen=_conn_gen)
                                        done.set()
                                    elif _t0 == 'speaking':
                                        _client_signal_seen[0] = True
                                        _client_playing[0] = bool(_m0.get('on'))
                                    # 'barge'/'text' in a seam backlog are moot —
                                    # they targeted the previous leg.
                                # A short seam carries the user's words over
                                # instead of dropping them: what they said
                                # that got no answer before the old leg ended,
                                # and what they said while it reconnected.
                                _gap = (None if (leg == 0 or _leg_ended_ts[0] is None)
                                        else _time.time() - _leg_ended_ts[0])
                                _pending_txt, _replay = _seam_replay_plan(
                                    ''.join(in_buf), bool(''.join(out_buf).strip()),
                                    _seam_chunks, _gap)
                                # A new leg: hold the character across the seam.
                                # First, so carried-over words follow it.
                                if _persona_note and not done.is_set():
                                    try:
                                        await session_ai.send_client_content(
                                            turns={"role": "user", "parts": [{"text": _persona_note}]},
                                            turn_complete=False)
                                    except Exception as _pne:
                                        _vlog(f'persona re-pin (reconnect) failed: {_pne}')
                                if _pending_txt and not done.is_set():
                                    try:
                                        await session_ai.send_client_content(
                                            turns={"role": "user", "parts": [{"text":
                                                "[The connection was renewed before you "
                                                "answered. The user had just said: "
                                                + _gate_voice_text(_pending_txt)
                                                + " -- answer that now.]"}]},
                                            turn_complete=not _replay,
                                        )
                                    except Exception as _pe:
                                        _vlog(f'seam replay (text) failed: {_pe}')
                                for _c in _replay:
                                    if done.is_set():
                                        break
                                    try:
                                        await session_ai.send_realtime_input(
                                            audio=types.Blob(data=_c, mime_type='audio/pcm;rate=16000'))
                                        _audio_bytes_to_gemini += len(_c)
                                    except Exception as _pe:
                                        _vlog(f'seam replay (audio) failed: {_pe}')
                                        break
                                if _pending_txt or _replay:
                                    _log.info("voice seam: carried over %s%s across a %.1fs reconnect",
                                              "unanswered words" if _pending_txt else "",
                                              (" and " if _pending_txt and _replay else "")
                                              + (f"{len(_replay)} mic chunks" if _replay else ""),
                                              _gap or 0.0)
                                if _stale_audio - len(_replay):
                                    _vlog(f'seam drain: dropped {_stale_audio - len(_replay)} stale mic chunks buffered during reconnect')
                            if done.is_set():
                                break
                            _safe_send({"type": "status", "text": "live"})

                            # Greeting only on a brand-new conversation — a renewal
                            # or a resumed conversation must not re-greet.
                            if not greeted[0]:
                                greeted[0] = True
                                try:
                                    await session_ai.send_client_content(
                                        turns={"role": "user", "parts": [{"text": "Greet me in one short sentence."}]},
                                        turn_complete=True,
                                    )
                                    _vlog('sent initial greeting prompt')
                                except Exception as _e:
                                    _vlog(f'greeting send failed: {_e}')

                            sdone = asyncio.Event()
                            _goaway_drain_deadline[0] = None
                            _model_speaking[0] = False
                            # A barge whose turn_complete never arrived (leg died
                            # first) must not swallow the NEXT leg's audio; the
                            # stale playback estimate must not hold the barge
                            # window open either. Tool-inflight can also strand
                            # if the leg was torn down mid-run.
                            _barged_turn[0] = False
                            _est_play_end_ts[0] = 0.0
                            _tool_inflight[0] = 0
                            _last_gemini_ts[0] = _time.time()
                            _core = {asyncio.create_task(reader(session_ai, sdone)),
                                     asyncio.create_task(writer(session_ai, sdone)),
                                     asyncio.create_task(liveness_watchdog(sdone))}
                            _aux = {asyncio.create_task(heartbeat(sdone))}
                            if leg == 0:
                                _aux.add(asyncio.create_task(no_audio_watchdog()))
                            # First core exit ends the leg: reader (browser closed),
                            # writer (stream ended / GoAway), or watchdog (stall).
                            await asyncio.wait(_core, return_when=asyncio.FIRST_COMPLETED)
                            sdone.set()
                            # Give healthy tasks a moment to wind down; a writer
                            # hung on a dead upstream socket is cancelled here —
                            # without this, one stuck receive() froze the whole
                            # call forever (the original silent-dropout bug).
                            _all = _core | _aux
                            _done_t, _pending = await asyncio.wait(_all, timeout=6.0)
                            for _p in _pending:
                                _p.cancel()
                            await asyncio.gather(*_all, return_exceptions=True)
                        finally:
                            _leg_ended_ts[0] = _time.time()
                            try:
                                await session_cm.__aexit__(None, None, None)
                            except Exception as _xe:
                                _vlog(f'leg close error (ignored): {_xe}')
                            _leg_bytes = max(0, _audio_bytes_to_gemini - _leg_bytes_at_start)
                            _record_mic_audio_egress("close", byte_len=_leg_bytes)
                            # F6: the receipt names the byte count. Cumulative
                            # for the session so a renewed leg does not reset
                            # what the user is told left the machine.
                            try:
                                from agent_friday.services import voice_indicator as _vi
                                _vi.record_cloud_session(
                                    provider="google-gemini", label="Gemini Live",
                                    event="close", model=model_name,
                                    bytes_sent=_leg_bytes)
                            except Exception:
                                pass
                            _safe_send({"type": "egress_receipt",
                                        "provider": "google-gemini",
                                        "label": "Google (Gemini Live)",
                                        "field": "mic_audio",
                                        "bytes": int(_audio_bytes_to_gemini),
                                        "text": (f"Sent {_audio_bytes_to_gemini / 1e6:.1f} MB "
                                                 "of microphone audio to Google this session.")})

                        if done.is_set():
                            break
                        # Leg over but the browser is still here → keep the call
                        # alive. Renew via handle when we have one; otherwise go
                        # fresh rather than hanging up — unless legs are dying
                        # immediately, which means something systemic is wrong and
                        # surfacing the error beats a reconnect storm.
                        if (_time.time() - _leg_started) < 10.0:
                            _quick_deaths += 1
                            if _quick_deaths >= 3:
                                raise RuntimeError(
                                    'voice session legs died <10s after connect '
                                    '3 times in a row — giving up on this model')
                        else:
                            _quick_deaths = 0
                        if not _supports_resumption or resume_handle[0] is None:
                            _vlog('leg ended with no resumption handle — continuing with a FRESH session')
                            _safe_send({"type": "status", "text": "reconnecting"})
                        leg += 1
                        _vlog(f'voice session leg ended without browser close — renewing (total renewals: {leg})')
                        _log.info("voice: Gemini session leg ended; renewing (renewal #%d)", leg)

                    break  # conversation over on this model — don't try fallbacks
                except Exception as e:
                    last_error = e
                    import traceback as _tb
                    tb_str = _tb.format_exc()
                    _err_str = str(e)
                    _kind = _classify_live_error(_err_str)
                    attempt_errors.append((model_name, api_version, _kind, _err_str))
                    _vlog(f'SESSION ERROR with {model_name} (api={api_version or "default(v1beta)"}) [{_kind}]: {type(e).__name__}: {e}')
                    _vlog(f'TRACEBACK: {tb_str}')
                    traceback.print_exc()
                    if _kind == 'auth':
                        _live_key_now = core.GEMINI_API_KEY
                        from agent_friday.routing.provider_descriptors import key_presence as _kp2
                        _key_diag = _kp2(_live_key_now)
                        print(f'[live] auth error on {model_name} (api={api_version or "v1beta"}). KEY={_key_diag} (from {_key_source}).', flush=True)
                    elif _kind == 'model-missing':
                        print(f'[live] model unavailable: {model_name} (api={api_version or "v1beta"}) — NOT an auth/key problem.', flush=True)
                    if (api_version, model_name) == attempts[-1]:
                        _final = _compose_final_voice_error(attempt_errors, _key_source)
                        _vlog(f'FINAL voice error → {_final}')
                        _safe_send({"type": "error", "error": _final})
                    else:
                        nxt = attempts[attempts.index((api_version, model_name)) + 1]
                        _vlog(f'trying fallback: model={nxt[1]} api={nxt[0] or "default(v1beta)"}')

            # Conversation over (or every attempt failed) — persist whatever we
            # captured. Runs on ALL exits so a failed fallback can't eat the
            # transcript or the distill.
            try:
                _flush_turn()
            except Exception:
                pass
            if turn_log:
                try:
                    _spawn_voice_distill(turn_log)
                except Exception as e:
                    print(f'[live] voice distill spawn error: {e}')

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(runner())
        except Exception as _top_e:
            import traceback as _tb2
            _vlog(f'TOP-LEVEL runner error: {type(_top_e).__name__}: {_top_e}')
            _vlog(f'TRACEBACK: {_tb2.format_exc()}')
            # The browser must never sit on a dead socket with no explanation:
            # _vlog is a no-op unless FRIDAY_VOICE_DEBUG=1, so without this
            # frame a runner crash was completely silent to the user.
            try:
                ws.send(json.dumps({
                    "type": "error",
                    "error": f"voice session crashed: {type(_top_e).__name__}: "
                             f"{str(_top_e)[:200]}",
                }))
            except Exception:
                pass
        finally:
            done.set()
            try:
                loop.close()
            except Exception:
                pass
            try:
                ws.close()
            except Exception:
                pass
            _vlog('=== WS handler done ===')
