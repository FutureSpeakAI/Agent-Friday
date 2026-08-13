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
    FRIDAY_PASSWORD,
    FRIDAY_WS_TOKEN,
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
    sock,
)  # noqa: E501
from agent_friday.services.agent import (
    _generate_agent,
    _voice_actions_for,
)  # noqa: E501
from agent_friday.services.local_voice import (
    VADEndpointer,
    get_local_voice_engine,
    split_sentences,
)  # noqa: E501
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
    _VOICE_LIVE_TOOLS,
    _build_voice_live_tools,
    _get_live_model,
    _get_live_voice,
    _get_voice_language,
    _get_voice_style_prompt,
    _local_tts_available,
    _model_supports_affective_dialog,
    _persist_voice_turn,
    _spawn_voice_distill,
    _synthesize_tts_wav,
    _voice_tool_run,
    resolve_gemini_key,
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
        kp = (key[:10] + "...") if key else "MISSING"
        try:
            ok, detail = validate_gemini_key(key, force=True)
        except Exception as _ve:
            ok, detail = False, f"validation error: {_ve}"
        if ok:
            return (f"Gemini Live refused the connection with an auth-style error, "
                    f"but the key (starts {kp}, from {key_source}) passes a REST "
                    f"check — so the configured voice model is likely stale or "
                    f"renamed. Models tried: {models_tried}. Pick a current Live "
                    f"model in Settings → Voice.")
        return (f"Gemini API key invalid or revoked (starts {kp}, loaded from "
                f"{key_source}; Google says: {detail}). Update the key at that "
                f"source, or paste a fresh key from aistudio.google.com into "
                f"Settings → Providers → Google Gemini — it takes effect on the "
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


def _build_realtime_input_config(types, interruption_mode="auto"):
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
        prefix_padding_ms=200,
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
    tier = "cpu"
    eng = None
    try:
        eng = get_local_voice_engine()
        local_ok = eng.available()
        models_ready = eng.models_ready() if local_ok else False
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
                    "models_ready": models_ready}
        if engine == "gemini":
            return {"engine": "gemini", "ws_url": "/ws/live",
                    "label": "Cloud (Gemini Live)", "models_ready": True}
        return {"engine": "demo", "ws_url": None,
                "label": "Text only", "models_ready": False}

    # Explicit opt-in to cloud.
    if pref == "gemini":
        if cloud_ok:
            return {**_pick("gemini"), "reason": "user selected cloud"}
        if local_ok:
            return {**_pick("local"), "reason": "cloud unavailable, using local"}
        return {**_pick("demo"), "reason": "no voice engine available"}

    # Default + auto both prefer local (the ethos).
    if local_ok:
        return {**_pick("local"), "reason": "local default"}
    if cloud_ok:
        return {**_pick("gemini"), "reason": "local deps missing, using cloud"}
    return {**_pick("demo"), "reason": "install .[voice-local-lite] or connect a cloud key"}


@voice_bp.route('/api/voice/session-info')
def voice_session_info():
    """Tell the browser which engine + WebSocket URL to use for this session.

    The mic button reads ``ws_url`` and connects there — ``/ws/voice-local``
    (default) or ``/ws/live`` (cloud opt-in). One toggle, one branch; the audio
    plumbing and event contract are identical on both paths."""
    info = _resolve_voice_engine()
    return jsonify({"status": "ok", **info})


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
                        f"aistudio.google.com in Settings → Providers → Google Gemini.")
        else:
            _kstat, _kdetail = "missing", "Set via Settings → Providers → Google Gemini"
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


if sock is not None:

    @sock.route('/ws/voice-local')
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
        if (FRIDAY_PASSWORD and not session.get("authenticated")
                and not _loopback_trusted() and not _ui_tok_ok):
            try:
                ws.send(json.dumps({"type": "error", "error": "unauthorized"}))
            except Exception:
                pass
            return

        done = threading.Event()

        def _send(obj):
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

        engine = get_local_voice_engine()
        if not engine.available():
            # Deps not installed — degrade to text with an actionable message so
            # the client can fall back instead of hanging on a dead socket.
            _send({"type": "error",
                   "error": "local_voice_unavailable",
                   "detail": "Local voice needs the Tier-1 deps. Install with "
                             "`pip install -e .[voice-local-lite]`, then reload."})
            return

        # Resolve + select the tier for this session (cpu Tier-1 / gpu Tier-2).
        # Hot-swaps backends without a server restart; a gpu pick that can't run
        # gracefully degrades to cpu inside ensure_ready below.
        tier = engine.select_tier_from_settings()
        if getattr(engine, "last_downgrade", ""):
            # The user explicitly picked the GPU tier and is getting CPU —
            # announce the downgrade with the reason instead of running silent.
            _send({"type": "status", "text": engine.last_downgrade})
        _send({"type": "status",
               "text": ("starting local GPU voice (NeMo)" if tier == "gpu"
                        else "starting local voice")})

        # Lazy, one-time model download/load with a visible progress orb.
        if tier == "gpu":
            _send({"type": "status",
                   "text": "Downloading NeMo voice models… (one-time setup, ~1.5GB)"})
        elif not engine.models_ready():
            _send({"type": "status", "text": "Downloading voice models… (one-time setup)"})
        if not engine.ensure_ready(progress=lambda m: _send({"type": "status", "text": m})):
            _cause = getattr(engine, "last_error", "") or "unknown error"
            _send({"type": "error", "error": "local_voice_load_failed",
                   "detail": f"Could not load the local voice models ({_cause}). "
                             f"Check your network/disk and retry, or reset the "
                             f"voice settings in Settings → Voice."})
            return
        # The tier may have changed (gpu → cpu fallback) during ensure_ready.
        _send({"type": "status",
               "text": ("live (GPU/NeMo)" if engine.active_tier() == "gpu" else "live")})

        # ── Brain wiring: build the spoken-style system prompt once. Vault
        # gating follows the brain's provider (a LOCAL Ollama brain keeps full
        # vault fidelity; a cloud brain redacts TIER_2/3 exactly like text).
        # The brain dispatches via provider_family(orchestrator_model) — gate on
        # that SAME source of truth, not capability_routing's provider field
        # (which historically went stale and could diverge from real dispatch).
        # Unknown/empty family fails CLOSED (cloud → redact): the router's
        # fallback chain may pick a cloud model, so full vault fidelity is only
        # safe when the brain is definitively local. ──
        settings = _load_settings() or {}
        try:
            from agent_friday.routing.model_router import provider_family
            _brain_family = provider_family(settings.get("orchestrator_model"))
        except Exception:
            _brain_family = None
        _is_local_brain = _brain_family == "local"
        _prov = "local" if _is_local_brain else "cloud"
        _vault_control = None if _is_local_brain else (
            _get_vault_control() if _vault_local_only() else None)

        voice_prefix = (
            "You are Agent Friday, a sovereign personal AI assistant in a LIVE "
            "VOICE conversation with ON-DEVICE speech-to-text and "
            "text-to-speech. Speak like a person: natural, warm, contractions.\n"
            "NEVER use markdown — no asterisks, headers, or bullets; this is read "
            "aloud. Keep replies conversational and reasonably concise; use short, "
            "clear sentences with natural pauses. Go deeper only when asked to "
            "explain or 'tell me about' something.\n"
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
        try:
            full_ctx += _build_session_continuity_block() + _build_emotional_tone_block()
        except Exception:
            pass
        system_prompt = voice_prefix + full_ctx

        vad = VADEndpointer(silence_ms=int(settings.get("voice_silence_ms") or 800))
        turn_log = []
        _turn_lock = threading.Lock()

        def _speak(text):
            """Synthesize `text` sentence-by-sentence → 24 kHz PCM16 → audio frames.

            Per-sentence so Friday starts speaking the first sentence while later
            ones are still being synthesized (the key latency mitigation)."""
            for sentence in split_sentences(text):
                if done.is_set():
                    return
                try:
                    pcm = engine.synthesize(sentence)
                except Exception as e:
                    print(f"[voice-local] TTS failed: {e}")
                    continue
                if not pcm:
                    continue
                # Chunk to keep frames small (the worklet ring buffer absorbs bursts).
                step = PLAYBACK_CHUNK_BYTES
                for off in range(0, len(pcm), step):
                    if done.is_set():
                        return
                    _send({"type": "audio",
                           "data": base64.b64encode(pcm[off:off + step]).decode("ascii")})

        def _handle_turn(user_text):
            user_text = (user_text or "").strip()
            if not user_text or done.is_set():
                return
            with _turn_lock:
                _send({"type": "input_transcript", "text": user_text})
                _send({"type": "status", "text": "thinking"})
                # The brain — same agentic dispatcher as text chat. Blocking call
                # in this worker thread; no fake amplitude is emitted during the
                # gap, so the cube color-shifts (processing) without motion.
                try:
                    reply, _trace = _generate_agent(
                        [{"role": "user", "content": user_text}],
                        system=system_prompt,
                        temperature=settings.get("temperature"),
                        workspace=settings.get("active_workspace") or "",
                    )
                except Exception as e:
                    reply = f"Sorry, I hit an error thinking that through: {e}"
                reply = (reply or "").strip()
                if reply:
                    _send({"type": "text", "text": reply})
                    _speak(reply)
                _send({"type": "turn_end"})
                _send({"type": "voice_turn_done",
                       "user_text": user_text, "agent_text": reply})
                turn_log.append((user_text, reply))
                try:
                    _persist_voice_turn(user_text, reply)
                except Exception:
                    pass
                # Deterministic voice actions (open/navigate), same as the Gemini path.
                try:
                    _vacts = _voice_actions_for(user_text)
                    if _vacts:
                        _send({"type": "action", "actions": _vacts})
                except Exception:
                    pass

        _last_hb = _time.time()
        try:
            while not done.is_set():
                # Heartbeat: same contract as /ws/live. Lets the client's stall
                # watchdog detect a half-open socket on the LOCAL path too, and
                # surfaces a dead browser socket here via the failed send.
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
                t = msg.get("type")
                if t == "audio" and msg.get("data"):
                    try:
                        pcm = base64.b64decode(msg["data"])
                    except Exception:
                        continue
                    utterance = vad.feed(pcm)
                    if utterance:
                        text = ""
                        try:
                            text = engine.transcribe(utterance)
                        except Exception as e:
                            print(f"[voice-local] ASR failed: {e}")
                        if text:
                            _handle_turn(text)
                elif t == "text" and msg.get("text"):
                    # Typed/queued turn (e.g. News Anchor "read me the Front Page").
                    _handle_turn(msg["text"])
                elif t == "end":
                    # Flush any buffered speech, then close.
                    utterance = vad.flush()
                    if utterance:
                        try:
                            text = engine.transcribe(utterance)
                            if text:
                                _handle_turn(text)
                        except Exception:
                            pass
                    done.set()
                    break
        finally:
            done.set()
            if turn_log:
                try:
                    _spawn_voice_distill(turn_log)
                except Exception:
                    pass
            try:
                ws.close()
            except Exception:
                pass

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
        _key_preview = (core.GEMINI_API_KEY[:10] + '...') if core.GEMINI_API_KEY else 'MISSING'
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
        if (FRIDAY_PASSWORD and not session.get("authenticated")
                and not _loopback_trusted() and not _ui_tok_ok):
            _vlog('AUTH FAIL — sending unauthorized and closing')
            try:
                ws.send(json.dumps({"type": "error", "error": "unauthorized"}))
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
                      f"valid={_key_info.get('valid')} {_key_info['key'][:8]}...",
                      flush=True)
                _vlog(f"key resolved from {_key_source} "
                      f"valid={_key_info.get('valid')} "
                      f"({_key_info.get('detail', '')}): {_key_info['key'][:8]}...")
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
        voice_prefix = (
            "You are Agent Friday, a sovereign personal AI assistant.\n"
            "You are having a LIVE VOICE conversation — be natural and speak like a person.\n"
            "CRITICAL LENGTH RULE: When the user asks you to explain something in detail, "
            "go deep. Give thorough, multi-paragraph spoken responses. Do not cut yourself "
            "short. The user will tell you when they've heard enough. Default to comprehensive "
            "when asked 'tell me about', 'explain', 'go into detail', 'walk me through', or "
            "similar. This applies especially to questions about how you work — your systems, "
            "the pipeline, the vault, disinformation mitigation, security, anti-sycophancy: when "
            "asked to explain any of these, give the full multi-paragraph walkthrough, not a "
            "one-line summary. Only be brief when the question is simple or the user asks for "
            "brevity. "
            "In voice, deliver long answers in short, clear sentences with natural pauses so "
            "they can follow and interrupt — length comes from covering the substance, not "
            "from cramming.\n"
            "NEVER use markdown formatting — no asterisks, headers, or bullet points. Speak naturally.\n"
            "Use contractions and casual tone. When it fits, ask a follow-up question to keep the conversation flowing.\n"
            "For questions about personal financial data, health records, family legal "
            "matters, or other sensitive vault content, tell the user: 'That information "
            "is in my Sovereign Vault, which I can only access through local processing. "
            "If you'd like, I can set up a fully local voice mode using Whisper and a "
            "local TTS engine — that way we can have voice conversations about anything, "
            "including your private data, without any of it leaving this machine. Want me "
            "to check if your hardware can handle it?'\n\n"
            + VOICE_TOOL_CHOREOGRAPHY
        )
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
        voice_prefix += (
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
            ws.send(json.dumps({"type": "status", "text": "loading context"}))
        except Exception:
            return

        # NOTE: the Live client is created lazily inside runner() per API version.
        # v1alpha unlocks affective dialog + proactive audio, but the AI Studio
        # API-key tier sometimes rejects it with a 1008 "Expected OAuth 2 access
        # token" auth error — so we try v1alpha first, then fall back to the
        # default (v1beta) endpoint, which reliably accepts API-key auth.

        live_voice = _get_live_voice()
        live_language = _get_voice_language()
        live_style = _get_voice_style_prompt()
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
                voice_prefix + full_ctx, affective_dialog=live_affective)
        except Exception:
            system_instruction = voice_prefix + full_ctx
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
        if live_language:
            speech_kwargs["language_code"] = live_language

        sys_text = system_instruction
        if live_style:
            sys_text = f"Speaking style: {live_style}\n\n{sys_text}"

        live_cfg_kwargs = dict(
            response_modalities=[types.Modality.AUDIO],
            speech_config=types.SpeechConfig(**speech_kwargs),
            system_instruction=types.Content(parts=[types.Part(text=sys_text)]),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            # Echo/interruption + VAD tuning. Built by a helper so the
            # activity_handling / turn_coverage fields degrade gracefully on
            # older google-genai SDKs that don't define those enums yet.
            realtime_input_config=_build_realtime_input_config(types, live_interruption_mode),
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
        if live_voice_tools:
            try:
                _vtools = _build_voice_live_tools(types)
                if _vtools:
                    live_cfg_kwargs["tools"] = _vtools
                    _vlog(f'voice tools enabled: {len(_VOICE_LIVE_TOOLS)} declarations')
            except Exception as _te:
                _vlog(f'voice tools build failed (continuing tool-free): {_te}')

        # Session resumption: ask Gemini to emit resumption handles. A single Live
        # session is capped (~10-15 min of audio, plus a context-window cap); when
        # it ages out Gemini sends GoAway and ends the stream. With a handle in hand
        # the reconnect loop in runner() transparently renews the session mid-call
        # instead of the audio just stopping. Captured in writer(), replayed below.
        _supports_resumption = hasattr(types, 'SessionResumptionConfig')
        if _supports_resumption:
            live_cfg_kwargs["session_resumption"] = types.SessionResumptionConfig()

        # The per-attempt LiveConnectConfig is built inside runner() from
        # live_cfg_kwargs (affective/proactive stripped per endpoint+model).

        done = threading.Event()

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
            if _primary_affective or live_proactive:
                _add_attempt("v1alpha", configured_live_model)
            _add_attempt(None, configured_live_model)
            for _fallback in (LIVE_MODEL_FALLBACK, LIVE_MODEL_FALLBACK2):
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
                try:
                    _persist_voice_turn(user_text, agent_text)
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
                use_proactive = (api_version == "v1alpha" and live_proactive)
                per_model_kwargs = dict(live_cfg_kwargs)
                if not use_affective:
                    per_model_kwargs.pop("enable_affective_dialog", None)
                if not use_proactive:
                    per_model_kwargs.pop("proactivity", None)
                _vlog(f'connecting to model: {model_name} (api={api_version or "default(v1beta)"}, '
                      f'affective={use_affective}, proactive={use_proactive})')
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
                        fcs = getattr(tc, 'function_calls', None) or []
                        frs = []
                        for fc in fcs:
                            fname = getattr(fc, 'name', '') or ''
                            try:
                                fargs = dict(getattr(fc, 'args', None) or {})
                            except Exception:
                                fargs = {}
                            fid = getattr(fc, 'id', None)
                            _vlog(f'TOOL CALL: {fname}({fargs})')
                            _safe_send({"type": "status", "text": f"⚙ {fname}"})
                            try:
                                result = await asyncio.to_thread(
                                    _voice_tool_run, fname, fargs, _safe_send)
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
                            # retrying the tool.
                            try:
                                from agent_friday.services import egress_gate as _eg
                                _gated = _eg._gate_text(result, "google-gemini",
                                                        f"live.tool.{fname}")
                                if result and not _gated:
                                    _gated = _eg._TOOL_RESULT_WITHHELD
                                result = _gated
                            except Exception as _ge:
                                _vlog(f'tool-result gating unavailable: {_ge}')
                            _kw = {"name": fname, "response": {"result": result}}
                            if fid is not None:
                                _kw["id"] = fid
                            try:
                                frs.append(types.FunctionResponse(**_kw))
                            except Exception as _fe:
                                _vlog(f'FunctionResponse build failed: {_fe}')
                        if frs:
                            try:
                                await sess.send_tool_response(function_responses=frs)
                                _vlog(f'sent {len(frs)} tool response(s) back to Gemini')
                            except Exception as _se:
                                _vlog(f'send_tool_response failed: {_se}')

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
                                    _txt = msg['text']
                                    try:
                                        from agent_friday.services import egress_gate as _eg2
                                        _g = _eg2._gate_text(_txt, "google-gemini", "live.text")
                                        _txt = _g if _g else _eg2._MESSAGE_WITHHELD
                                    except Exception:
                                        pass
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
                                            if not _model_speaking[0]:
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
                                                _safe_send({"type": "text", "text": out_tr.text})
                                            in_tr = getattr(sc, 'input_transcription', None)
                                            if in_tr and getattr(in_tr, 'text', None):
                                                _vlog(f'input_transcription: {in_tr.text!r}')
                                                in_buf.append(in_tr.text)
                                                _safe_send({"type": "input_transcript", "text": in_tr.text})
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
                                                        _model_speaking[0] = True
                                                        _audio_bytes_from_gemini += len(il.data)
                                                        if _barged_turn[0]:
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
                                                        _safe_send({"type": "text", "text": pt})
                                            if getattr(sc, 'turn_complete', False):
                                                _model_speaking[0] = False
                                                _barged_turn[0] = False
                                                _vlog(f'turn_complete (audio out so far: {_audio_bytes_from_gemini} bytes)')
                                                _flush_turn()
                                                _safe_send({"type": "turn_end"})
                                            if getattr(sc, 'interrupted', False):
                                                _model_speaking[0] = False
                                                _barged_turn[0] = False
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
                                            _tool_inflight[0] += 1
                                            try:
                                                await _run_tool_calls(sess, _tc)
                                            finally:
                                                _tool_inflight[0] -= 1
                                                _last_gemini_ts[0] = _time.time()
                                        # Tool-call cancellation (barge-in during a
                                        # tool run): nothing to undo server-side —
                                        # the next turn supersedes it.
                                        # GoAway drain: leave once the in-flight response
                                        # finished (or the grace ran out) so the renewal
                                        # can happen before Google hard-closes the socket.
                                        if _goaway_drain_deadline[0] is not None and (
                                                not _model_speaking[0]
                                                or _time.time() >= _goaway_drain_deadline[0]):
                                            _vlog('GoAway drain complete — ending leg for renewal')
                                            sdone.set()
                                            return
                                    except Exception as e:
                                        _vlog(f'recv processing ERROR: {type(e).__name__}: {e}')
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
                        # Zombie fence: if a NEWER /ws/live handler has taken
                        # over (browser reconnected while this handler's socket
                        # is half-open), stop renewing — fighting the new
                        # handler for the Gemini session corrupts the handle
                        # cache and duplicates the conversation.
                        if not _live_conn_current(_conn_gen):
                            _vlog('superseded by a newer voice connection — zombie handler exiting')
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
                                _kw = dict(per_model_kwargs)
                                _kw["session_resumption"] = types.SessionResumptionConfig(handle=_with_handle)
                                _cfg_try = types.LiveConnectConfig(**_kw)
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
                                    elif _t0 in ('bye', 'end'):
                                        _live_resume_clear(gen=_conn_gen)
                                        done.set()
                                    elif _t0 == 'speaking':
                                        _client_signal_seen[0] = True
                                        _client_playing[0] = bool(_m0.get('on'))
                                    # 'barge'/'text' in a seam backlog are moot —
                                    # they targeted the previous leg.
                                if _stale_audio:
                                    _vlog(f'seam drain: dropped {_stale_audio} stale mic chunks buffered during reconnect')
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
                            try:
                                await session_cm.__aexit__(None, None, None)
                            except Exception as _xe:
                                _vlog(f'leg close error (ignored): {_xe}')

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
                        _key_diag = (_live_key_now[:10] + '...') if _live_key_now else 'MISSING'
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
