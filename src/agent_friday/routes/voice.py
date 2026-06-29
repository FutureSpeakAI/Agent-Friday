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
    _API_SESSION_TOKEN,
    _is_local_request,
    _load_agent_personality,
    _load_settings,
    _load_voice_demo,
    _loopback_trusted,
    _network_status,
    _ollama_available,
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
)  # noqa: E501

voice_bp = Blueprint('voice', __name__)

# Local-voice playback chunk size: ~0.2 s of 24 kHz mono PCM16 per WS frame.
# Small frames keep the friday-pcm-player ring buffer fed smoothly (it absorbs
# bursts), matching how the Gemini path streams audio back.
PLAYBACK_CHUNK_BYTES = 9600


def _build_realtime_input_config(types, interruption_mode="speaker"):
    """Build the Live API RealtimeInputConfig with echo-safe interruption handling.

    Google's recommended cure for the "Friday cuts herself off on speakers" bug
    is two-fold:
      • activity_handling = NO_INTERRUPTION — the model's response is NEVER
        interrupted by detected mic activity, so her own speaker bleed (echo)
        re-captured by the mic can't fire a spurious interruption. This is the
        default ("speaker" mode). Headphone users who want true barge-in pick
        "headphones", which restores START_OF_ACTIVITY_INTERRUPTS.
      • turn_coverage = TURN_INCLUDES_ONLY_ACTIVITY — the user turn counts only
        detected speech, excluding silence/background noise.

    Plus the on-disk VAD tuning: silence_duration_ms=800 (snappy turn-end),
    LOW start sensitivity (ignore quiet echo), HIGH end-of-speech sensitivity.

    `activity_handling` / `turn_coverage` are only set when the installed
    google-genai SDK exposes the enums, so older SDKs degrade to VAD-only.
    """
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

    headphones = str(interruption_mode or "").strip().lower() in (
        "headphones", "headphone", "barge", "barge-in", "bargein")

    # activity_handling — the actual echo fix. NO_INTERRUPTION on speakers.
    _ah = getattr(types, "ActivityHandling", None)
    if _ah is not None:
        member = "START_OF_ACTIVITY_INTERRUPTS" if headphones else "NO_INTERRUPTION"
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
    net = _network_status()
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
        _ui_tok_ok = bool(_ui_t) and _hmac.compare_digest(_ui_t, _API_SESSION_TOKEN)
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
            _send({"type": "error", "error": "local_voice_load_failed",
                   "detail": "Could not load the local voice models."})
            return
        # The tier may have changed (gpu → cpu fallback) during ensure_ready.
        _send({"type": "status",
               "text": ("live (GPU/NeMo)" if engine.active_tier() == "gpu" else "live")})

        # ── Brain wiring: build the spoken-style system prompt once. Vault
        # gating follows the brain's provider (a LOCAL Ollama brain keeps full
        # vault fidelity; a cloud brain redacts TIER_2/3 exactly like text). ──
        settings = _load_settings() or {}
        try:
            cr = (settings.get("capability_routing") or {}).get("reasoning") or {}
            brain_provider = cr.get("provider") or ""
        except Exception:
            brain_provider = ""
        _is_local_brain = brain_provider in ("ollama-local", "local") or not brain_provider
        _prov = "local" if _is_local_brain else "cloud"
        _vault_control = None if _is_local_brain else (
            _get_vault_control() if _vault_local_only() else None)

        voice_prefix = (
            "You are Agent Friday, a sovereign personal AI assistant in a LIVE "
            "VOICE conversation running fully ON-DEVICE (local speech-to-text and "
            "text-to-speech). Speak like a person: natural, warm, contractions.\n"
            "NEVER use markdown — no asterisks, headers, or bullets; this is read "
            "aloud. Keep replies conversational and reasonably concise; use short, "
            "clear sentences with natural pauses. Go deeper only when asked to "
            "explain or 'tell me about' something.\n"
            "Because this runs locally, you CAN discuss private vault content — it "
            "never leaves the machine.\n\n"
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

        try:
            while not done.is_set():
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
        _ui_tok_ok = bool(_ui_t) and _hmac.compare_digest(_ui_t, _API_SESSION_TOKEN)
        if (FRIDAY_PASSWORD and not session.get("authenticated")
                and not _loopback_trusted() and not _ui_tok_ok):
            _vlog('AUTH FAIL — sending unauthorized and closing')
            try:
                ws.send(json.dumps({"type": "error", "error": "unauthorized"}))
            except Exception:
                pass
            return

        # Trigger settings-fallback if env var not set (onboarding-wizard path
        # stores key in settings.json; get_genai_client() reads it + updates the global).
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
        live_context_compression = bool(live_settings.get("voice_context_compression"))
        # Interruption mode: "speaker" (echo-safe, no interruption) is the default;
        # "headphones" restores true barge-in. See _build_realtime_input_config.
        live_interruption_mode = str(
            live_settings.get("voice_interruption_mode") or "speaker").strip().lower()

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
        _vlog(f'voice interruption mode: {live_interruption_mode} '
              f'({"no-interruption / echo-safe" if live_interruption_mode != "headphones" else "barge-in"})')
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
                per_model_cfg = types.LiveConnectConfig(**per_model_kwargs)
                active_client = _client_for(api_version)
                _vlog(f'connecting to model: {model_name} (api={api_version or "default(v1beta)"}, '
                      f'affective={use_affective}, proactive={use_proactive})')
                try:
                    # ── Per-connection conversation state. Lives ACROSS the
                    # transparent session renewals below so a reconnect seam never
                    # loses an in-flight turn or the distill log. ──
                    _audio_chunks_received = 0
                    _gemini_chunks_received = 0
                    _audio_bytes_to_gemini = 0
                    _audio_bytes_from_gemini = 0
                    _safe_send_failures = 0
                    in_buf = []
                    out_buf = []
                    turn_log = []
                    resume_handle = [None]   # newest session-resumption handle from Gemini
                    greeted = [False]

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
                                result = f"(tool {fname} failed: {_te})"
                            if not isinstance(result, str):
                                result = str(result)
                            result = result[:8000]
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
                                    await sess.send_realtime_input(text=msg['text'])
                                elif t == 'end':
                                    _vlog('reader: browser sent end signal')
                                    # Explicitly flush audio stream so Gemini stops waiting for VAD.
                                    try:
                                        await sess.send_realtime_input(audio_stream_end=True)
                                        _vlog('sent audio_stream_end=True to gemini')
                                    except Exception as _e:
                                        _vlog(f'audio_stream_end send failed: {_e}')
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
                                        # Capture the newest resumption handle so the
                                        # reconnect loop can renew this exact session.
                                        _sru = getattr(chunk, 'session_resumption_update', None)
                                        if _sru is not None and getattr(_sru, 'new_handle', None):
                                            resume_handle[0] = _sru.new_handle
                                        # GoAway: Gemini is about to retire this session
                                        # (audio/context cap). End this leg cleanly so the
                                        # reconnect loop renews it via the handle above —
                                        # the user hears no break.
                                        _ga = getattr(chunk, 'go_away', None)
                                        if _ga is not None:
                                            _tl = getattr(_ga, 'time_left', None)
                                            _vlog(f'GoAway from Gemini (time_left={_tl}) — renewing session via resumption handle')
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
                                                    # Audio: PCM bytes at 24kHz in part.inline_data.data
                                                    il = getattr(part, 'inline_data', None)
                                                    if il and getattr(il, 'data', None):
                                                        _audio_bytes_from_gemini += len(il.data)
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
                                                _vlog(f'turn_complete (audio out so far: {_audio_bytes_from_gemini} bytes)')
                                                _flush_turn()
                                                _safe_send({"type": "turn_end"})
                                            if getattr(sc, 'interrupted', False):
                                                _vlog('interrupted')
                                                _safe_send({"type": "interrupted"})
                                        # Agentic step: Gemini wants to call a tool.
                                        # Execute it and feed the result back so the
                                        # model continues the turn from real data.
                                        _tc = getattr(chunk, 'tool_call', None)
                                        if _tc is not None and getattr(_tc, 'function_calls', None):
                                            await _run_tool_calls(sess, _tc)
                                        # Tool-call cancellation (barge-in during a
                                        # tool run): nothing to undo server-side —
                                        # the next turn supersedes it.
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

                    # ── Reconnect loop ──────────────────────────────────────────
                    # A single Gemini Live session is capped (~10-15 min of audio,
                    # plus a context-window cap). When Gemini sends GoAway / ends the
                    # stream while the BROWSER is still connected, we renew the session
                    # using the last resumption handle and keep going — Gemini restores
                    # the conversation context server-side, so the user hears no seam.
                    # If no handle was ever issued (resumption unsupported), this runs
                    # exactly once and behaves like the old single-session path.
                    leg = 0
                    while not done.is_set():
                        if leg > 0 and resume_handle[0] is not None and _supports_resumption:
                            _leg_kwargs = dict(per_model_kwargs)
                            _leg_kwargs["session_resumption"] = types.SessionResumptionConfig(handle=resume_handle[0])
                            _leg_cfg = types.LiveConnectConfig(**_leg_kwargs)
                            _vlog(f'reconnecting voice session (renewal #{leg}) with resumption handle')
                        else:
                            _leg_cfg = per_model_cfg
                        async with active_client.aio.live.connect(model=model_name, config=_leg_cfg) as session_ai:
                            if leg == 0:
                                _safe_send({"type": "status", "text": "live"})
                                _vlog(f'session established with {model_name}')
                            else:
                                _vlog(f'session renewed with {model_name} (renewal #{leg})')

                            # Greeting only on the very first leg — a renewal must not
                            # re-greet (and Gemini already has the restored context).
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
                            _tasks = [reader(session_ai, sdone), writer(session_ai, sdone)]
                            if leg == 0:
                                _tasks.append(no_audio_watchdog())
                            await asyncio.gather(*_tasks, return_exceptions=True)

                        # Browser gone, or no handle to renew with → terminal.
                        if done.is_set() or resume_handle[0] is None or not _supports_resumption:
                            break
                        leg += 1
                        _vlog(f'voice session leg ended without browser close — renewing (total renewals: {leg})')

                    try:
                        _flush_turn()
                    except Exception:
                        pass
                    if turn_log:
                        try:
                            _spawn_voice_distill(turn_log)
                        except Exception as e:
                            print(f'[live] voice distill spawn error: {e}')
                    break  # session completed successfully, don't try fallback
                except Exception as e:
                    last_error = e
                    import traceback as _tb
                    tb_str = _tb.format_exc()
                    _err_str = str(e)
                    _is_auth_1008 = '1008' in _err_str or 'authentication' in _err_str.lower() or 'OAuth' in _err_str
                    _vlog(f'SESSION ERROR with {model_name} (api={api_version or "default(v1beta)"}): {type(e).__name__}: {e}')
                    _vlog(f'TRACEBACK: {tb_str}')
                    traceback.print_exc()
                    if _is_auth_1008:
                        _live_key_now = core.GEMINI_API_KEY
                        _key_diag = (_live_key_now[:10] + '...') if _live_key_now else 'MISSING'
                        print(f'[live] 1008 auth error on {model_name} (api={api_version or "v1beta"}). KEY={_key_diag}. Check GEMINI_API_KEY in start.bat — likely expired/rotated.', flush=True)
                    if (api_version, model_name) == attempts[-1]:
                        if _is_auth_1008:
                            _live_key_now = core.GEMINI_API_KEY
                            _key_diag = (_live_key_now[:10] + '...') if _live_key_now else 'MISSING'
                            _safe_send({"type": "error", "error": f"Gemini API key rejected (1008 auth). Key starts with: {_key_diag}. Rotate GEMINI_API_KEY in start.bat and restart Friday."})
                        else:
                            _safe_send({"type": "error", "error": _err_str})
                    else:
                        nxt = attempts[attempts.index((api_version, model_name)) + 1]
                        _vlog(f'trying fallback: model={nxt[1]} api={nxt[0] or "default(v1beta)"}')

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(runner())
        except Exception as _top_e:
            import traceback as _tb2
            _vlog(f'TOP-LEVEL runner error: {type(_top_e).__name__}: {_top_e}')
            _vlog(f'TRACEBACK: {_tb2.format_exc()}')
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
