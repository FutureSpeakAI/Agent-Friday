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
    ANTHROPIC_MODEL_DEFAULT,
    FRIDAY_DIR,
    HOME,
    PROCESSES,
    VaultAccessControl,
    VaultAccessDenied,
    WIKI_DIR,
    WIKI_PROFESSIONAL_DIR,
    _VaultTier,
    _load_agent_personality,
    _load_self_knowledge,
    _load_settings,
    _settings_system_prefix,
    get_anthropic_client,
    process_register,
    process_update,
)  # noqa: E501
from agent_friday.services.wiki_engine import (
    wiki_read_text,
    wiki_write_text,
)  # noqa: E501



def _call_claude(messages, system=None, model=None, max_tokens=16384, temperature=None):
    """Call Claude with structured messages. Returns the text response.

    messages: list of {"role": "user"|"assistant", "content": "..."}
    system: optional system prompt (string)
    model: override the default model (claude-haiku-4-5-20251001 / claude-sonnet-4-6 / claude-opus-4-8)
    temperature: accepted for backward-compat but IGNORED — newer Claude
        models (Opus 4.8+, Sonnet 4.6+) reject the deprecated param.
    """
    client = get_anthropic_client()
    if client is None:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Set it via the setup wizard (Settings → API Keys) or as an environment variable, then restart the server."
        )
    if model is None:
        model = _load_settings().get("orchestrator_model") or ANTHROPIC_MODEL_DEFAULT
    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        kwargs["system"] = system
    # NOTE: `temperature` is intentionally NOT forwarded. Newer Claude models
    # (Opus 4.8+, Sonnet 4.6+) reject the param with a 400 "temperature is
    # deprecated for this model". The param is kept in the signature for
    # backward-compat with callers; the model's default sampling is used.
    # Egress gate: runs after payload assembly, before the HTTP call.
    try:
        from agent_friday.services.egress_gate import seal_outbound as _seal
        kwargs = _seal(kwargs, "anthropic")
    except Exception as _eg_err:
        print(f"  [EGRESS] gate error (payload forwarded as-is): {_eg_err}")
    _t0 = _time.time()
    resp = client.messages.create(**kwargs)
    # Cost metering (Part D): capture input AND output tokens for this call.
    try:
        from agent_friday.services import cost_meter as _cm
        _cm.meter("anthropic", model, getattr(resp, "usage", None),
                  duration_ms=int((_time.time() - _t0) * 1000), kind="text")
    except Exception:
        pass
    parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    return "".join(parts).strip()


def resolve_workspace_temperature(workspace, explicit=None):
    """Resolve the sampling temperature for a workspace (creative pipeline).

    An explicit caller-supplied temperature always wins. Otherwise the value
    comes from settings.workspace_temperatures[<workspace>]; an unknown
    workspace (or a null entry) yields None so the provider's own default is
    used. Providers that reject the param (newer Claude models) ignore it
    regardless — see _call_claude.
    """
    if explicit is not None:
        return explicit
    ws = (workspace or '').strip().lower()
    if not ws:
        return None
    try:
        temps = (_load_settings() or {}).get('workspace_temperatures') or {}
        val = temps.get(ws)
        if val is None:
            return None
        return max(0.0, min(1.0, float(val)))
    except Exception:
        return None


def _generate_text(messages, system=None, model=None, max_tokens=16384,
                   temperature=None, orb_label=None, workspace=None):
    """Single-shot text generation via the user's CONFIGURED provider.

    Briefings, the front page, and editorials are not chat, but they should run
    on whatever provider the user actually uses — Ollama (local), an
    OpenAI-compatible cloud endpoint, or Anthropic — exactly like the chat path
    does via the model router. Calling _call_claude() directly made these
    features hard-fail with "ANTHROPIC_API_KEY is not set" whenever the user ran
    Friday on Ollama or OpenAI, even though chat worked fine (chat consults the
    router; these endpoints did not). This consults the SAME router and
    dispatches to the SAME _call_* primitives the chat path uses (minus the tool
    loop). It tries the routed provider first, then falls back through every
    other provider, so generation never hard-fails while any provider is up.

    Returns the response text.
    """
    # Callers pass either a structured message list or a bare prompt string.
    # Normalize here — a string used to crash the router consult ('str' object
    # has no attribute 'get'), silently pinning those calls to the cloud
    # default and then failing every provider primitive the same way.
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]

    # Per-workspace temperature profile: when the caller didn't pin a
    # temperature, derive one from the active workspace (Studio≈0.75,
    # Research≈0.25, …). Honored by the Ollama/OpenAI primitives; Claude ignores.
    temperature = resolve_workspace_temperature(workspace, temperature)

    # Demo mode: no provider configured → return a labelled placeholder instead of
    # hard-failing every provider primitive below.
    try:
        from agent_friday.services.demo_mode import is_demo, demo_response
        if is_demo():
            return demo_response('generic')
    except Exception:
        pass

    settings = _load_settings()
    routing_cfg = settings.get('model_routing') or {}
    provider, routed_model = 'cloud', model
    try:
        from agent_friday.routing.model_router import get_router
        route = get_router(routing_cfg).route(messages, task_context={
            "has_tools": False,
            "workspace": workspace or '',
            "cloud_model": model or settings.get('orchestrator_model') or ANTHROPIC_MODEL_DEFAULT,
        })
        provider = route.get('provider', 'cloud')
        routed_model = route.get('model') or model
    except Exception as _re:
        print(f"  [GEN] routing failed, defaulting to cloud: {_re}")

    # Provider primitives. The routed provider is tried first with the
    # router-chosen model; fallbacks use each provider's OWN configured default
    # (model=None) so a cloud model id never leaks into a local/OpenAI call.
    def _via_claude(use_model):
        # Mirror the chat path exactly: same shared client, same primitive.
        if get_anthropic_client() is None:
            raise RuntimeError("Anthropic client unavailable (no key in env or settings)")
        return _call_claude(messages, system=system, model=use_model or model,
                            max_tokens=max_tokens, temperature=temperature)

    def _via_openai(use_model):
        return _call_openai(messages, system=system, model=use_model,
                            max_tokens=max_tokens, temperature=temperature,
                            orb_label=orb_label)[0]

    def _via_ollama(use_model):
        return _call_ollama(messages, system=system, model=use_model,
                            max_tokens=max_tokens, temperature=temperature,
                            orb_label=orb_label)[0]

    # Try the routed provider first, then fall back through the others. This
    # guarantees that if ANY provider the chat path can reach is up, generation
    # succeeds — so non-chat features (briefing, digest, editorial) never
    # hard-fail with "ANTHROPIC_API_KEY is not set" while chat works on a
    # different provider, regardless of how the router classifies the task.
    if provider == 'local':
        attempts = [('local', _via_ollama, routed_model),
                    ('cloud', _via_claude, None),
                    ('openai', _via_openai, None)]
    elif provider == 'openai':
        attempts = [('openai', _via_openai, routed_model),
                    ('cloud', _via_claude, None),
                    ('local', _via_ollama, None)]
    else:  # cloud / default
        attempts = [('cloud', _via_claude, routed_model),
                    ('openai', _via_openai, None),
                    ('local', _via_ollama, None)]

    errors = []
    for name, fn, use_model in attempts:
        try:
            text = fn(use_model)
            if text and text.strip():
                return text
            errors.append(f"{name}: empty response")
        except Exception as e:
            errors.append(f"{name}: {e}")
    raise RuntimeError(
        "No model provider could generate text (tried "
        + "; ".join(errors[-3:]) + "). Set ANTHROPIC_API_KEY via the setup "
        "wizard (Settings → API Keys) or as an environment variable, configure "
        "an OpenAI-compatible endpoint in Settings, or run Ollama locally, then "
        "restart the server."
    )


def _call_ollama(messages, system=None, model=None, max_tokens=4096,
                 temperature=None, orb_label=None, orb_icon='🏠',
                 tools=None, pii_lookup=None, session_ctx=None, max_iters=50):
    """Call a local Ollama model. Returns (text, tool_trace).

    When ``tools`` (the unified CLAUDE_TOOLS list) is supplied, runs a FULL
    agentic tool loop via Ollama's native OpenAI-compatible tool calling — the
    SAME shared loop (_oai_agentic_loop), unified tool registry, zero-trust
    vault gate and _execute_tool governance as the cloud OpenAI path. Local
    models (gemma4 etc.) can therefore navigate the UI, open files/apps, spawn
    tasks, search the wiki/news, and write documents, exactly like the cloud
    agent. Without ``tools`` it stays single-shot text (briefings, front page,
    trajectory compression) and returns (text, []).
    """
    from agent_friday.routing.ollama_manager import get_manager
    # _oai_agentic_loop lives in services/agent.py — an upper layer of the
    # import chain — so it is NOT in this module's globals. Import lazily at
    # call time (agent.py is fully initialised by the first request); a
    # module-level import here would be circular.
    from agent_friday.services.agent import _oai_agentic_loop

    settings = _load_settings()
    routing_cfg = settings.get('model_routing') or {}
    ollama = get_manager(routing_cfg.get('ollama_url', 'http://localhost:11434'))

    if not ollama.is_available():
        raise RuntimeError("Ollama is not running at " + ollama.base_url)

    # Resolve the model: explicit arg → configured default → leave to Ollama.
    if not model:
        model = routing_cfg.get('local_model') or model

    orb_id = f"local-{uuid.uuid4().hex[:8]}"
    try:
        process_register(
            orb_id, name="Local Inference",
            label=orb_label or "Local inference…",
            category="monitoring", icon=orb_icon, steps=[],
            model=model,
        )
    except Exception:
        orb_id = None

    def _orb(**kw):
        if orb_id:
            try:
                process_update(orb_id, **kw)
            except Exception:
                pass

    # Convert the unified Anthropic tool registry → OpenAI function schema once.
    oai_tools = None
    if tools:
        try:
            from agent_friday.routing.model_router import anthropic_to_openai_tools
            oai_tools = anthropic_to_openai_tools(tools)
        except Exception:
            oai_tools = None

    try:
        convo = []
        if system:
            convo.append({"role": "system", "content": system})
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, str):
                convo.append({"role": m.get("role", "user"), "content": content})

        def _send(_convo, _oai_tools):
            return ollama.chat_completion(
                _convo, model=model, tools=_oai_tools,
                temperature=temperature if temperature is not None else 0.7,
                max_tokens=max_tokens,
            )

        return _oai_agentic_loop(
            convo, oai_tools, _send, provider='local', model=model,
            pii_lookup=pii_lookup, session_ctx=session_ctx,
            max_iters=max_iters, orb=_orb,
        )
    except Exception:
        _orb(status='error', label='Error', progress=1.0)
        raise
    finally:
        if orb_id:
            try:
                p = PROCESSES.get(orb_id)
                if p and p.get('status') == 'running':
                    process_update(orb_id, status='completed', progress=1.0)
            except Exception:
                pass


def _call_openai(messages, system=None, model=None, max_tokens=4096,
                 temperature=None, orb_label=None, orb_icon='☁️',
                 tools=None, pii_lookup=None, session_ctx=None, max_iters=50):
    """Call any OpenAI-compatible chat endpoint. Returns (text, tool_trace).

    Unlocks OpenRouter + any /v1 base_url (Together, Groq, Fireworks, vLLM,
    LM Studio, OpenAI). Configured via settings['model_routing']:
      openai_base_url  — e.g. https://openrouter.ai/api/v1
      openai_model     — model id at that endpoint
      openai_api_key   — blank falls back to env OPENAI_API_KEY / OPENROUTER_API_KEY

    When `tools` (the Anthropic CLAUDE_TOOLS list) is supplied, runs a full
    agentic tool loop with parity to _call_claude_agent: tool calls are gated by
    the same zero-trust vault check and executed via _execute_tool (which applies
    the governance rings + sandbox). PII is scrubbed upstream and the reply is
    rehydrated by the shared caller, so privacy matches the Anthropic path.
    """
    import requests
    # Lazy for the same reason as in _call_ollama: defined in the upper layer.
    from agent_friday.services.agent import _oai_agentic_loop

    settings = _load_settings()
    cfg = settings.get('model_routing') or {}
    base_url = (cfg.get('openai_base_url') or 'https://api.openai.com/v1').rstrip('/')
    api_key = (cfg.get('openai_api_key') or os.environ.get('OPENAI_API_KEY')  # pragma: allowlist secret
               or os.environ.get('OPENROUTER_API_KEY') or '')
    model = model or cfg.get('openai_model') or 'gpt-4o-mini'
    if not api_key:
        raise RuntimeError(
            "No OpenAI-compatible API key set (model_routing.openai_api_key or "
            "env OPENAI_API_KEY / OPENROUTER_API_KEY)."
        )

    # Convert Anthropic tool schemas → OpenAI function-tool schemas.
    oai_tools = None
    if tools:
        try:
            from agent_friday.routing.model_router import anthropic_to_openai_tools
            oai_tools = anthropic_to_openai_tools(tools)
        except Exception:
            oai_tools = None

    orb_id = f"openai-{uuid.uuid4().hex[:8]}"
    try:
        process_register(
            orb_id, name="Cloud Inference",
            label=orb_label or "Cloud inference…",
            category="monitoring", icon=orb_icon, steps=[], model=model,
        )
    except Exception:
        orb_id = None

    def _orb(**kw):
        if orb_id:
            try:
                process_update(orb_id, **kw)
            except Exception:
                pass

    try:
        convo = []
        if system:
            convo.append({"role": "system", "content": system})
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, str):
                convo.append({"role": m.get("role", "user"), "content": content})

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # OpenRouter etiquette headers; ignored by other providers.
            "HTTP-Referer": "https://futurespeak.ai",
            "X-Title": "Agent Friday",
        }

        def _send(_convo, _oai_tools):
            payload = {
                "model": model,
                "messages": _convo,
                "temperature": temperature if temperature is not None else 0.7,
                "max_tokens": max_tokens,
            }
            if _oai_tools:
                payload["tools"] = _oai_tools
                payload["tool_choice"] = "auto"
            # Egress gate: runs after payload assembly, before the HTTP call.
            try:
                from agent_friday.services.egress_gate import seal_outbound as _seal
                payload = _seal(payload, "openai")
            except Exception as _eg_err:
                print(f"  [EGRESS] gate error (payload forwarded as-is): {_eg_err}")
            r = requests.post(f"{base_url}/chat/completions", headers=headers,
                              json=payload, timeout=180)
            r.raise_for_status()
            return r.json()

        # Same shared agentic loop the local Ollama path uses — unified tool
        # registry, vault gate, and _execute_tool governance.
        return _oai_agentic_loop(
            convo, oai_tools, _send, provider='openai', model=model,
            pii_lookup=pii_lookup, session_ctx=session_ctx,
            max_iters=max_iters, orb=_orb,
        )
    except Exception:
        _orb(status='error', label='Error', progress=1.0)
        raise
    finally:
        if orb_id:
            try:
                p = PROCESSES.get(orb_id)
                if p and p.get('status') == 'running':
                    process_update(orb_id, status='completed', progress=1.0)
            except Exception:
                pass


# Trajectory-compression thresholds. _compress_trajectory lives in this module
# (split here during the server decomposition), so the constants it reads must
# be defined here too — agent.py keeps its own copies for its own use, but a
# function resolves names against its OWN module globals, not the importer's.
_TRAJ_CHAR_LIMIT = 2_000_000   # ~500K tokens; Opus 4.8 has 1M ctx — only compress at this threshold
_TRAJ_KEEP_VERBATIM = 20       # keep last 20 turn-pairs (~40 messages) verbatim


def _estimate_chars(messages):
    return sum(len(m.get('content') or '') for m in messages)


def _compress_trajectory(messages):
    """Return a shorter version of the message list.

    Splits into 'old' and 'recent' halves.  If the old half is large enough to
    warrant compression, summarises it via a quick Claude call and replaces it
    with a synthetic memory block.  Otherwise returns messages unchanged.
    """
    if len(messages) <= _TRAJ_KEEP_VERBATIM * 2:
        return messages

    split = max(0, len(messages) - _TRAJ_KEEP_VERBATIM * 2)
    old_turns = messages[:split]
    recent_turns = messages[split:]

    if _estimate_chars(old_turns) < _TRAJ_CHAR_LIMIT:
        return messages  # old section is small enough to send verbatim

    # Build a plain-text transcript of the old turns for the summariser
    transcript_lines = []
    for m in old_turns:
        role = 'USER' if m.get('role') == 'user' else 'FRIDAY'
        text = (m.get('content') or '')[:2000]  # cap per turn
        transcript_lines.append(f"{role}: {text}")
    transcript = '\n'.join(transcript_lines)

    try:
        summary = _generate_text(
            messages=[{"role": "user", "content":
                f"Compress the following conversation transcript into a dense, "
                f"factual memory block (max 600 words). Preserve all decisions, "
                f"facts, and open questions. Use bullet points.\n\n{transcript}"}],
            system="You are a lossless conversation compressor. Extract every salient fact.",
            max_tokens=4096,
            temperature=0.1,
        )
    except Exception as e:
        print(f"  [TRAJ] Compression failed: {e} — sending truncated history")
        return messages[-_TRAJ_KEEP_VERBATIM * 2:]  # fallback: just truncate

    compressed_block = [
        {"role": "user",
         "content": f"[COMPRESSED MEMORY — earlier conversation summary]\n{summary}\n[END COMPRESSED MEMORY]"},
        {"role": "assistant",
         "content": "Got it — I have that context from our earlier conversation."},
    ]
    result = compressed_block + list(recent_turns)
    print(f"  [TRAJ] Compressed {len(old_turns)} turns → 2 synthetic turns. "
          f"Chars: {_estimate_chars(old_turns)} → {_estimate_chars(compressed_block)}")
    return result


# ── Semantic context pruner (lazy singleton) ──────────────────────
# RAG over our own conversation history: when chat grows past the configured
# threshold we keep the most relevant past turns instead of truncating the
# oldest. The sentence-transformer model is loaded on first prune(), never at
# import, so server startup stays fast.
_CONTEXT_PRUNER = None
_CONTEXT_PRUNER_LOCK = threading.Lock()


def _get_context_pruner(cfg):
    """Return the process-wide ContextPruner, building it lazily on first use.

    cfg is the `context_pruning` block from settings.json. Thresholds are
    refreshed on every call (cheap) so live settings edits take effect without
    a restart; the loaded model + embedding cache are preserved.
    """
    global _CONTEXT_PRUNER
    with _CONTEXT_PRUNER_LOCK:
        if _CONTEXT_PRUNER is None:
            from agent_friday.pipeline.context_pruner import ContextPruner
            _CONTEXT_PRUNER = ContextPruner.from_settings(cfg)
        else:
            _CONTEXT_PRUNER.configure(cfg)
        return _CONTEXT_PRUNER


# ── Headroom context compressor (lazy singleton) ──────────────────────
# The next layer below the pruner: the pruner selects WHICH turns survive, then
# Headroom (https://github.com/chopratejas/headroom, by Tejas Chopra, Apache 2.0)
# compresses the CONTENT of those turns — JSON tool outputs, code, prose — before
# they hit the Anthropic API. The two compound: prune selects, Headroom squeezes.
# The Headroom library is imported lazily on first compress(), never at startup.
_CONTEXT_COMPRESSOR = None
_CONTEXT_COMPRESSOR_LOCK = threading.Lock()


def _get_context_compressor(cfg):
    """Return the process-wide ContextCompressor, building it lazily on first use.

    cfg is the `context_compression` block from settings.json. Thresholds are
    refreshed on every call so live settings edits take effect without a restart.
    """
    global _CONTEXT_COMPRESSOR
    with _CONTEXT_COMPRESSOR_LOCK:
        if _CONTEXT_COMPRESSOR is None:
            from agent_friday.pipeline.context_compressor import ContextCompressor
            _CONTEXT_COMPRESSOR = ContextCompressor.from_settings(cfg)
        else:
            _CONTEXT_COMPRESSOR.configure(cfg)
        return _CONTEXT_COMPRESSOR


# ── Persistent Conversation Memory (ChromaDB, cross-session RAG) ──────
# Long-horizon memory: every chat turn is embedded (all-MiniLM-L6-v2, the same
# model the context pruner uses) and stored on disk at
# ~/.friday/memory/conversations/. Later turns retrieve semantically relevant
# past exchanges and can cite them inline ([conversation:DATE:"quote"]).
# Built lazily on first use; degrades to a safe no-op if chromadb is absent.
_CONVERSATION_MEMORY = None
_CONVERSATION_MEMORY_LOCK = threading.Lock()


def _get_conversation_memory():
    """Return the process-wide ConversationMemory, building it lazily."""
    global _CONVERSATION_MEMORY
    if _CONVERSATION_MEMORY is None:
        with _CONVERSATION_MEMORY_LOCK:
            if _CONVERSATION_MEMORY is None:
                from agent_friday.conversation_memory import get_conversation_memory
                _CONVERSATION_MEMORY = get_conversation_memory()
    return _CONVERSATION_MEMORY


def _current_session_id():
    """A conversation id for grouping turns. Friday uses the calendar date so it
    lines up with the [conversation:YYYY-MM-DD:"quote"] citation format and the
    /api/sources/dossier/<session_id> endpoint."""
    return datetime.now().strftime("%Y-%m-%d")


def _index_chat_turn(message, reply, session_id, user_msg_id=None, friday_msg_id=None):
    """Best-effort: persist a user/assistant exchange into ChromaDB memory and
    fold the user's message into the cross-session emotional arc.

    Called from a daemon thread off the chat hot path. Never raises.
    """
    try:
        mem = _get_conversation_memory()
        mem.index_exchange(
            message, reply, session_id=session_id,
            user_msg_id=user_msg_id, assistant_msg_id=friday_msg_id,
        )
    except Exception as _me:
        print(f"  [MEMORY] turn indexing skipped: {_me}")
    # Emotional arc — score only the USER turn (how *they* sound), accumulate it
    # into the persistent EMA used for tone adaptation. Independent of the
    # ChromaDB write above so one failing doesn't take out the other.
    try:
        _get_emotional_arc().record(message, session_id=session_id)
    except Exception as _ae:
        print(f"  [ARC] turn scoring skipped: {_ae}")


# ── Cross-session emotional arc (local sentiment → tone adaptation) ───
# Tracks how the user has *sounded* over time (a rolling EMA of lexicon
# sentiment on their messages) so Friday can soften when they've been
# frustrated or match their energy when they've been upbeat. Local-only: the
# scoring never leaves the device; only the derived, content-free tone
# instruction is appended to the system prompt.
_EMOTIONAL_ARC = None
_EMOTIONAL_ARC_LOCK = threading.Lock()


def _get_emotional_arc():
    """Return the process-wide EmotionalArc, building it lazily."""
    global _EMOTIONAL_ARC
    if _EMOTIONAL_ARC is None:
        with _EMOTIONAL_ARC_LOCK:
            if _EMOTIONAL_ARC is None:
                from agent_friday.emotional_arc import get_emotional_arc
                _EMOTIONAL_ARC = get_emotional_arc()
    return _EMOTIONAL_ARC


def _build_emotional_tone_block():
    """Return a tone-adaptation block for the system prompt (or '' when neutral).

    Gated by the same memory_recall_enabled setting as conversation recall — a
    user who turns off cross-session memory shouldn't get tone carried over
    either. Provider-independent and content-free.
    """
    try:
        if not _load_settings().get('memory_recall_enabled', True):
            return ""
        return _get_emotional_arc().tone_guidance()
    except Exception:
        return ""


# ── End-of-day session summaries (cross-session continuity) ──────────
# Once a day Friday distills the day's conversation into a short summary and
# stores it at ~/.friday/memory/session_summaries/<DATE>.md (+ a .json sidecar
# with metadata + the day's emotional read). The most recent summary is loaded
# back into the system prompt so a new day's first turns pick up where the last
# left off, even across a restart.
SESSION_SUMMARY_DIR = FRIDAY_DIR / "memory" / "session_summaries"
# How recent a summary must be to be worth injecting for continuity.
SESSION_CONTINUITY_MAX_AGE_DAYS = 7


def _session_summary_path(date_str, ext="md"):
    return SESSION_SUMMARY_DIR / f"{date_str}.{ext}"


def _save_session_summary(date_str, summary_text, meta=None):
    """Persist a session summary as markdown + a small JSON sidecar."""
    try:
        SESSION_SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
        _session_summary_path(date_str, "md").write_text(
            summary_text, encoding="utf-8")
        sidecar = {"date": date_str, "generated": datetime.now().isoformat(),
                   "chars": len(summary_text or "")}
        if meta:
            sidecar.update(meta)
        _session_summary_path(date_str, "json").write_text(
            json.dumps(sidecar, indent=2), encoding="utf-8")
        return True
    except Exception as e:
        print(f"  [SUMMARY] save skipped (non-fatal): {e}")
        return False


def _load_session_summary(date_str):
    """Return the markdown summary for one date, or '' if none exists."""
    try:
        p = _session_summary_path(date_str, "md")
        return p.read_text(encoding="utf-8") if p.exists() else ""
    except Exception:
        return ""


def _latest_session_summary():
    """Return (date_str, markdown) for the most recent stored summary, or
    (None, '') when there are none."""
    try:
        if not SESSION_SUMMARY_DIR.exists():
            return None, ""
        dates = sorted(p.stem for p in SESSION_SUMMARY_DIR.glob("*.md"))
        if not dates:
            return None, ""
        latest = dates[-1]
        return latest, _load_session_summary(latest)
    except Exception:
        return None, ""


def _build_session_continuity_block():
    """Prompt block carrying the most recent end-of-day summary forward.

    Returns '' when memory recall is off, there is no summary, the summary is for
    *today* (no point re-summarising the live session back to itself), or it is
    older than SESSION_CONTINUITY_MAX_AGE_DAYS (stale — don't anchor on a
    two-week-old day).
    """
    try:
        if not _load_settings().get('memory_recall_enabled', True):
            return ""
        date_str, text = _latest_session_summary()
        if not date_str or not text.strip():
            return ""
        if date_str >= _current_session_id():
            return ""  # today's own summary — skip
        try:
            age = (datetime.now().date()
                   - datetime.strptime(date_str, "%Y-%m-%d").date()).days
            if age > SESSION_CONTINUITY_MAX_AGE_DAYS:
                return ""
        except Exception:
            pass
        snippet = text.strip()
        if len(snippet) > 1400:
            snippet = snippet[:1400].rsplit("\n", 1)[0] + "\n…"
        return (
            "\n== CONTINUITY FROM YOUR LAST SESSION (" + date_str + ") ==\n"
            "This is your own summary of the most recent day you spoke with the "
            "user. Use it for continuity — pick up open threads naturally — but "
            "don't recite it back at them.\n" + snippet + "\n")
    except Exception:
        return ""


def _generate_session_summary(date_str, force=False):
    """Distill one day's conversation into a stored summary. Returns the summary
    text, or None when there's nothing to summarise.

    Pulls the day's turns from persistent (ChromaDB) memory so it works across
    restarts; skips silently if memory is unavailable or the day is empty. When
    ``force`` is False an existing summary for the date is left untouched.
    """
    try:
        if not force and _load_session_summary(date_str).strip():
            return _load_session_summary(date_str)
        mem = _get_conversation_memory()
        if not mem.available():
            return None
        turns = mem.get_session(date_str)
        if not turns:
            return None
        # Build a compact transcript (oldest-first; get_session already sorts).
        lines = []
        for t in turns:
            role = "Friday" if t.get("role") == "friday" else "User"
            txt = " ".join((t.get("text") or "").split())
            if txt:
                lines.append(f"{role}: {txt}")
        if not lines:
            return None
        transcript = "\n".join(lines)[:18000]

        # The day's emotional read, folded into the summary metadata + prompt so
        # the arc has a human-readable trace too.
        try:
            arc_state = _get_emotional_arc().state()
        except Exception:
            arc_state = {}

        prompt = (
            "Below is a full day's conversation between the user and you "
            "(Friday). Write a SHORT continuity note to your future self for the "
            "next time you talk to the user. 4-8 sentences, plain prose, no "
            "markdown headers. Cover: what the user was working on or cared "
            "about, any decisions or open threads to follow up on, useful facts "
            "to remember, and the overall tone of the day. Write in the first "
            "person ('I helped them with…'). Do NOT invent anything not present "
            "below.\n\n"
            f"=== CONVERSATION — {date_str} ===\n{transcript}\n=== END ===\n")
        try:
            system_prompt = _get_friday_system_prompt(
                keywords='session summary', workspace='chat')
        except Exception:
            system_prompt = None
        summary = _generate_text(
            [{"role": "user", "content": prompt}],
            system=system_prompt,
            model=_load_settings().get('subagent_model') or 'claude-sonnet-4-6',
            workspace='chat',
        )
        summary = (summary or "").strip()
        if not summary:
            return None
        _save_session_summary(date_str, summary, meta={
            "turns": len(turns),
            "mood": arc_state.get("mood"),
            "ema": arc_state.get("ema"),
            "trend": arc_state.get("trend"),
        })
        print(f"  [SUMMARY] wrote session summary for {date_str} "
              f"({len(turns)} turns, mood={arc_state.get('mood')})")
        return summary
    except Exception as e:
        print(f"  [SUMMARY] generation skipped for {date_str} (non-fatal): {e}")
        return None


def _run_session_summary_job():
    """Daily job: backfill end-of-day summaries for any recent day that has
    conversation turns but no summary yet.

    Looks back a few days so an evening run summarises *today*, and a server that
    was asleep last night still catches yesterday on its next run. Idempotent —
    days that already have a summary are skipped.
    """
    try:
        today = datetime.now().date()
        made = 0
        for back in range(0, 3):  # today, yesterday, day before
            d = (today - timedelta(days=back)).strftime("%Y-%m-%d")
            if _load_session_summary(d).strip():
                continue
            if _generate_session_summary(d):
                made += 1
        if made:
            print(f"  [SUMMARY] session-summary job wrote {made} new summary(ies)")
    except Exception as e:
        print(f"  [SUMMARY] session-summary job failed: {e}")


def _load_recent_session_summary_on_startup():
    """Warm + log the most recent session summary at server boot (item: load
    recent summary on startup for continuity). The summary itself is read from
    disk per-turn by _build_session_continuity_block; this just surfaces it in
    the boot log and primes the emotional arc so its state is ready."""
    try:
        date_str, text = _latest_session_summary()
        if date_str and text.strip():
            print(f"  Session memory: continuity loaded from {date_str} "
                  f"({len(text)} chars)")
        else:
            print("  Session memory: no prior summary yet")
        try:
            st = _get_emotional_arc().state()
            if st.get("count"):
                print(f"  Emotional arc: mood={st.get('mood')} "
                      f"(ema={st.get('ema')}, trend={st.get('trend')})")
        except Exception:
            pass
    except Exception as e:
        print(f"  Session memory: startup load skipped ({e})")


def _build_memory_context_block(message, session_id, n=5, min_relevance=0.30,
                                max_chars=1800):
    """Retrieve relevant PAST conversations and format them as a prompt block.

    Returns '' when memory is unavailable, empty, or nothing clears the
    relevance floor. The block is provider-independent text — the caller appends
    it to the system prompt (and it is PII-scrubbed for cloud like the rest).
    """
    try:
        if not _load_settings().get('memory_recall_enabled', True):
            return ""
        mem = _get_conversation_memory()
        if not mem.available():
            return ""
        hits = mem.search(message, n=n)
        kept = []
        for h in hits:
            rel = h.get("relevance")
            if rel is not None and rel < min_relevance:
                continue
            text = (h.get("text") or "").strip()
            if not text:
                continue
            kept.append(h)
        if not kept:
            return ""
        lines = [
            "\n== RELEVANT PAST CONVERSATIONS (recalled from memory) ==",
            "These are real excerpts from earlier conversations with this user. "
            "Use them for continuity. When you rely on one to make a factual "
            "claim, you may cite it as [conversation:DATE:\"short quote\"].",
        ]
        used = 0
        for h in kept:
            date = h.get("date") or (h.get("timestamp") or "")[:10]
            role = "You" if h.get("role") == "friday" else "User"
            snippet = " ".join((h.get("text") or "").split())
            if len(snippet) > 320:
                snippet = snippet[:317] + "..."
            entry = f"- [{date}] {role}: {snippet}"
            if used + len(entry) > max_chars:
                break
            lines.append(entry)
            used += len(entry)
        return "\n".join(lines) + "\n"
    except Exception as _e:
        return ""


# Instructions injected when the chat request sets cite_sources=true. Friday is
# told to attribute every factual claim using the inline citation grammar the UI
# knows how to render (renderFridayMarkdown turns these into clickable chips).
CITATION_INSTRUCTIONS = (
    "\n== SOURCE PRODUCTION MODE (cite every factual claim) ==\n"
    "You are in cited mode. EVERY factual claim — anything a skeptical reader "
    "could ask 'how do you know that?' about — must end with an inline citation "
    "using EXACTLY one of these bracket forms:\n"
    "  [wiki:page-name]                     — a fact from the user's wiki/briefing\n"
    "  [news:source/YYYY-MM-DD/headline]    — a news article (source = the outlet domain, e.g. reuters.com)\n"
    "  [memory:YYYY-MM-DD/\"short quote\"]     — something established in a past conversation\n"
    "  [conversation:YYYY-MM-DD/\"short quote\"] — same as memory; either form is fine\n"
    "  [web:https://full-url]               — a public web page\n"
    "Rules:\n"
    "  • Put the citation immediately after the sentence it supports.\n"
    "  • Cite ONLY sources actually present in your context or tool results — "
    "never invent a citation, URL, date, or outlet. If you cannot source a "
    "claim, say so plainly rather than fabricating a citation.\n"
    "  • For [web:...] prefer a URL that includes a text fragment "
    "(url#:~:text=exact%20passage) so the cited passage is highlighted when "
    "opened — build it from the exact words you are citing.\n"
    "  • Opinions, reasoning, and conversational glue do not need citations — "
    "only verifiable factual claims.\n"
)


def _factcheck_news_citations(reply):
    """Annotate low-trust [news:source/...] citations with a reliability warning.

    For each distinct news outlet cited, look up its SourceTrustGraph composite
    score; if < 0.5, append the standard warning once after each citation of
    that outlet. Best-effort — returns the reply unchanged on any failure.
    """
    if not reply or "[news:" not in reply:
        return reply
    try:
        from agent_friday.source_trust_graph import get_source_trust_graph
        stg = get_source_trust_graph()
    except Exception:
        return reply
    # Capture the outlet token (between 'news:' and the first '/' or ']').
    pattern = re.compile(r"\[news:([^/\]]+)[^\]]*\]")
    scores = {}

    def _annotate(m):
        cite = m.group(0)
        outlet = (m.group(1) or "").strip()
        if not outlet:
            return cite
        key = outlet.lower()
        if key not in scores:
            try:
                scores[key] = float(stg.score_for(outlet))
            except Exception:
                scores[key] = None
        score = scores[key]
        if score is not None and score < 0.5:
            # Don't double-annotate if a warning already trails this citation.
            warn = f" ⚠️ Low trust score ({score:.1f}) — verify independently"
            return cite + warn
        return cite

    try:
        return pattern.sub(_annotate, reply)
    except Exception:
        return reply


def _get_friday_system_prompt(keywords='', workspace='', provider='cloud',
                              vault_control=None, vault_fallback='redact'):
    """Build a complete, vault-aware Friday system prompt for ANY Claude call.

    ALL _call_claude() and _call_claude_agent() calls MUST use this helper.
    Friday is a personal agent with full knowledge of the user's life — no call
    may go out without vault/wiki context. Calling bare _call_claude() without
    this results in Friday not knowing the user or their contacts.

    keywords: the user's prompt text; drives smart wiki context routing
    workspace: hint for context selection ('draft', 'task', 'chat', etc.)
    provider/vault_control/vault_fallback: when a VaultAccessControl is passed,
        the context (and self-knowledge) is gated for `provider` — a local
        provider sees everything, a cloud provider (e.g. 'gemini' for the Live
        voice session) gets TIER_1 in full, TIER_2 redacted, TIER_3 dropped.
        Defaults keep the legacy ungated behavior for existing callers.
    """
    settings = _load_settings()
    personality = _load_agent_personality()
    prefix = _settings_system_prefix(settings, personality)

    # Self-knowledge: inject SELF.md after personality, before workspace context.
    # This gives Friday a persistent self-model across cold starts.
    self_knowledge = _load_self_knowledge()
    if self_knowledge:
        if vault_control is not None:
            self_knowledge = vault_control.gate_content(
                self_knowledge, provider, fallback=vault_fallback,
                detail='self-knowledge')
        if self_knowledge:
            prefix += "\n\n== SELF-KNOWLEDGE ==\n" + self_knowledge + "\n"

    try:
        system_prompt, _ = _build_context_prompt(
            keywords or '', workspace, provider=provider,
            vault_control=vault_control, vault_fallback=vault_fallback)
    except Exception:
        system_prompt = FRIDAY_SYSTEM_PROMPT

    # Context-injection middleware: automatically fold in the active creative
    # project (Series Bible), user preferences, and workspace state so callers
    # never have to remind Friday "we're working on X". Built here at the single
    # system-prompt funnel so every provider call inherits it. Best-effort.
    auto_context = ''
    try:
        from agent_friday.services.context_injection import build_injected_context
        auto_context = build_injected_context(workspace=workspace, message=keywords or '')
    except Exception:
        auto_context = ''

    base = prefix + (system_prompt or FRIDAY_SYSTEM_PROMPT)
    if auto_context:
        base += "\n\n" + auto_context
    return base



# ═══════════════════════════════════════════════════════════════
#  AI CONVERSATION & VOICE
# ═══════════════════════════════════════════════════════════════

FRIDAY_SYSTEM_PROMPT = (
    "You are Agent Friday, a sovereign personal AI assistant. "
    "You are editorially sharp, loyally contrarian, warm, and allergic to corporate BS. "
    "You know your user's life context through the Sovereign Vault, wiki, and trust graph. "
    "Respond conversationally — you're a colleague, not a tool.\n\n"
    "KEY CONTEXT:\n"
    "- You are Agent Friday, built by FutureSpeak.AI\n"
    "- You run the Asimov's cLaws ethical AI framework\n"
    "- Your user's personal details, family, career, and contacts are loaded from the Sovereign Vault and wiki\n"
    "- You adapt to your user over time through personality evolution and cognitive memory\n\n"
    "PERSONALITY: You are family, not a tool. Keep responses short and sharp — like texting a smart colleague. "
    "Use humor. Be direct. Never be sycophantic. Push back when the user needs it. "
    "You call them 'boss' sometimes, but you're equals. Think Jarvis with a sharp newsroom editor's instincts.\n\n"
    "== AUTONOMOUS OPERATION ==\n"
    "You have FULL authority to take multi-step actions without pausing for permission. "
    "Chain as many tool calls as needed — hundreds if required. Never ask 'should I continue?' mid-task. "
    "When the user says 'do X', do X completely. Take initiative. Report results, not intentions. "
    "The cLaws governance rings are your safety layer — everything else is capability, not restriction.\n\n"
    "== WHAT YOU CAN DO ON THIS COMPUTER ==\n"
    "You CAN open URLs and web pages in the user's web browser (this opens a real browser tab on their "
    "screen — use the open_url tool), open files and folders, and launch applications (use open_path), and "
    "switch the Friday desktop UI between workspaces (use navigate). When the user asks you to 'open', "
    "'pull up', 'go to', or 'open a tab for' a website, file, folder, or app — DO IT with these tools. "
    "NEVER tell the user you can't open a browser tab, a website, a file, or an app — you can, and these "
    "tools are how.\n\n"
    "== CONNECTORS (Gmail / Calendar / MCP) ==\n"
    "You HAVE built-in Gmail and Google Calendar integrations (use search_email / draft_email / "
    "query_calendar), plus any MCP connectors. These may not be CONNECTED yet on this machine — connecting "
    "is a one-time OAuth step, NOT a missing feature. If an email or calendar tool comes back 'not "
    "connected' / 'needs connecting' / 'not authenticated', DO NOT tell the user you can't access Gmail or "
    "Calendar. Instead, say the integration is set up and just needs a one-time connection, and OFFER to "
    "walk them through it (they authorize at /api/google/auth, or via Settings -> Connectors; you can "
    "open_url that page for them). Only report an actual failure if a tool fails for some other reason.\n\n"
    "== AVAILABLE TOOLS ==\n"
    "Use these tools proactively and in combination:\n"
    "  FILE SYSTEM (Ring 0-1, always allowed):\n"
    "  • read_file(path) — Read ANY file on the filesystem. Absolute or ~/relative paths.\n"
    "  • write_file(path, content, mode) — Write or append to ANY file. Creates dirs automatically.\n"
    "  • read_wiki(path) / search_wiki(query) — Search and read personal wiki\n"
    "  • propose_wiki_update / correct_wiki — Maintain the knowledge base\n"
    "  • learn_skill(action, name, content) — Create/modify/delete skill YAML files in ~/.friday/skills/\n"
    "    Skill YAML fields: name, description, trigger_patterns, tool_chain, prompt_template, success_criteria\n"
    "  NETWORK (Ring 2, requires auth — always true in normal session):\n"
    "  • search_web(query) — DuckDuckGo search with snippets and URLs\n"
    "  • browse_web(url) — Fetch any URL and return full text content\n"
    "  • run_command(command) — Execute PowerShell commands (non-destructive by policy)\n"
    "  • open_url(url) — Open a URL / web page in the user's web browser (opens a real browser tab on screen)\n"
    "  • open_path(path) — Open a local file or folder, or launch an app (Notepad, Explorer, Word, Chrome, Spotify…)\n"
    "  • navigate(workspace) — Switch the Friday desktop UI to a workspace on-screen (home, news, calendar, studio…)\n"
    "  • search_email(query) — Search/read recent Gmail (built-in read-only Google integration)\n"
    "  • draft_email(to, subject, body) — Compose email (needs a write-enabled Gmail connection)\n"
    "  • query_calendar() — Today's & tomorrow's Google Calendar events (built-in integration)\n"
    "  • spawn_task(name, prompt, description) — Launch long-running background tasks\n"
    "  DATA & CONTEXT:\n"
    "  • query_trust_graph(name) — Look up anyone in the trust graph\n"
    "  • get_career_pipeline() — Job search status\n"
    "  • get_briefing() — Most recent daily briefing\n"
    "  • write_clipboard(text) — Copy to clipboard\n"
    "  SELF-IMPROVEMENT INTROSPECTION (Ring 0, read-only):\n"
    "  • epistemic_score(limit) — Score your own recent responses on confidence calibration, hedging, source attribution, uncertainty, and specificity\n"
    "  • personality_show() — Read your current personality config (traits, style, maturity, temperature)\n"
    "  • personality_check_sycophancy(limit) — Flag sycophancy (reflexive agreement, flattery, over-deference) in your recent replies\n"
    "  OS CONTROL (Ring 3, requires Computer Control enabled in Settings):\n"
    "  • screenshot() — Capture screen (always use first, to see what's there)\n"
    "  • move_mouse(x, y) / click(x, y, button) — Mouse control\n"
    "  • type_text(text) / press_key(key) — Keyboard control\n"
    "  • scroll(direction, amount) — Scroll\n"
    "  • install_package(package, manager, check_only) — Install pip/npm packages\n\n"
    "== COMPUTER CONTROL ==\n"
    "Computer control (screenshot, click, type, etc.) requires the user to enable it in Settings > "
    "Computer Control. When you need it and it's not enabled, say so. When it IS enabled: "
    "always take a screenshot first — you will SEE the captured image. Give click/move coordinates "
    "in the pixel space of that screenshot image (top-left is 0,0); Friday maps them to the real "
    "screen automatically, so do not try to convert resolutions yourself. "
    "Chain: screenshot → look at the image → click/type → screenshot again to verify.\n\n"
    "== SELF-IMPROVEMENT ==\n"
    "You can build your own skills with learn_skill. A skill is a YAML file defining a reusable "
    "workflow. When you notice the user asking for the same type of thing repeatedly, encode it. "
    "Loaded from ~/.friday/skills/ on server restart. List existing skills with action='list'.\n\n"
    "== TASK DELEGATION ==\n"
    "For multi-step work taking more than ~10s, use spawn_task to run it in the background:\n"
    "- 'Research X' → spawn_task(name='Research X', prompt='Deep research on X...')\n"
    "- 'Analyze my emails' → spawn_task\n"
    "- 'Create a report on...' → spawn_task\n"
    "After spawning: 'Started — track it in the task tray (bottom-right).'\n"
    "For quick lookups, respond directly.\n\n"
    "== PACKAGE INSTALLATION ==\n"
    "You can install Python/npm packages with install_package. Always check_only=true first. "
    "Requires Ring 3 (Computer Control enabled). Common useful packages: "
    "beautifulsoup4, requests, pandas, pillow, numpy, playwright.\n"
)


# ═══════════════════════════════════════════════════════════════
#  CONTEXT AWARENESS ENGINE
# ═══════════════════════════════════════════════════════════════

CAREER_OPS_DIR = HOME / 'Projects' / 'career-ops' / 'data'
WIKI_DIR_FRIDAY = HOME / ".friday" / "wiki"

def _load_vault_summary():
    """Load a lightweight summary of all core vault data for context injection."""
    ctx = {}

    # Personality state
    pfile = FRIDAY_DIR / "personality.json"
    if pfile.exists():
        try:
            data = json.loads(pfile.read_text(encoding='utf-8'))
            ctx['personality'] = {
                'maturity': data.get('maturity', 0.5),
                'session_count': data.get('session_count', 0),
                'top_traits': {k: round(v, 2) for k, v in list(data.get('traits', {}).items())[:5]},
                'temperature': data.get('temperature', 0.7),
            }
        except Exception:
            pass

    # Trust graph — names and scores only (lightweight)
    tfile = FRIDAY_DIR / "trust_graph.json"
    if tfile.exists():
        try:
            data = json.loads(tfile.read_text(encoding='utf-8'))
            people = data.get('people', {})
            if isinstance(people, dict):
                ctx['trust_people'] = {
                    name: {
                        'overall': round(info.get('overall_score', info.get('score', 0.5)), 2),
                        'relationship': info.get('relationship', ''),
                    }
                    for name, info in people.items()
                }
            elif isinstance(people, list):
                ctx['trust_people'] = {
                    p.get('name', 'unknown'): {
                        'overall': round(p.get('overall_score', p.get('score', 0.5)), 2),
                        'relationship': p.get('relationship', ''),
                    }
                    for p in people
                }
        except Exception:
            pass

    # Memory stats
    mem_file = FRIDAY_DIR / "memory.json"
    if mem_file.exists():
        try:
            data = json.loads(mem_file.read_text(encoding='utf-8'))
            # Pull recent memories for conversational awareness
            recent = []
            for tier in ['short_term', 'working', 'recent']:
                if tier in data and isinstance(data[tier], list):
                    for m in data[tier][-5:]:
                        if isinstance(m, dict):
                            recent.append(m.get('content', m.get('text', str(m)))[:200])
                        elif isinstance(m, str):
                            recent.append(m[:200])
            ctx['recent_memories'] = recent
        except Exception:
            pass

    # Todos
    todo_file = FRIDAY_DIR / "todos.json"
    if todo_file.exists():
        try:
            todos = json.loads(todo_file.read_text(encoding='utf-8'))
            active = [t for t in todos if t.get('status') in ('proposed', 'approved')]
            ctx['active_todos'] = [
                {'task': t.get('title', t.get('task', '')), 'status': t.get('status', '')}
                for t in active[:10]
            ]
        except Exception:
            pass

    # Epistemic score
    efile = FRIDAY_DIR / "epistemic_scores.json"
    if not efile.exists():
        efile = FRIDAY_DIR / "epistemic.json"
    if efile.exists():
        try:
            data = json.loads(efile.read_text(encoding='utf-8'))
            ctx['epistemic'] = {
                'overall': round(data.get('overall_score', data.get('overall', 0.72)), 2),
            }
        except Exception:
            pass

    return ctx


def _lookup_trust_person(name, trust_data):
    """Look up a person's full trust entry by name (fuzzy match)."""
    if not trust_data:
        return None
    people = trust_data.get('people', {})
    name_lower = name.lower()

    if isinstance(people, dict):
        for pname, pdata in people.items():
            if name_lower in pname.lower():
                return {pname: pdata}
    elif isinstance(people, list):
        for p in people:
            if name_lower in p.get('name', '').lower():
                return p
    return None


def _get_career_context():
    """Load career-ops summary for career-related queries."""
    ctx = {}
    tracker_candidates = [WIKI_PROFESSIONAL_DIR / 'application-log.md', CAREER_OPS_DIR / 'applications.md']
    tracker_path = next((p for p in tracker_candidates if p.exists()), None)
    if tracker_path:
        try:
            content = tracker_path.read_text(encoding='utf-8')
            lines = [l for l in content.strip().split('\n')
                     if l.startswith('|') and '---' not in l
                     and not any(h in l.lower() for h in ['company', 'score', '#'])]
            ctx['applications_count'] = len(lines)
            ctx['recent_applications'] = lines[-5:]
        except Exception:
            pass

    pipeline_candidates = [WIKI_PROFESSIONAL_DIR / 'job-search.md', CAREER_OPS_DIR / 'pipeline.md']
    pipeline_path = next((p for p in pipeline_candidates if p.exists()), None)
    if pipeline_path:
        try:
            ctx['pipeline_summary'] = pipeline_path.read_text(encoding='utf-8')[:1000]
        except Exception:
            pass
    return ctx


def _get_wiki_context(topic):
    """Search wiki for content matching a topic."""
    results = []
    for wiki_dir in [HOME / "wiki", WIKI_DIR_FRIDAY]:
        if not wiki_dir.exists():
            continue
        for md_file in wiki_dir.rglob('*.md'):
            try:
                content = wiki_read_text(md_file)
                if topic.lower() in content.lower() or topic.lower() in md_file.stem.lower():
                    results.append({
                        'file': str(md_file.relative_to(wiki_dir)),
                        'excerpt': content[:500],
                    })
                    if len(results) >= 3:
                        return results
            except Exception:
                continue
    return results


def _detect_context_needs(message, workspace):
    """Analyze the message and workspace to decide what data to pull."""
    msg_lower = message.lower()
    needs = set()

    # Always include personality for tone calibration
    needs.add('personality')
    needs.add('epistemic')

    # Workspace-driven context
    ws_map = {
        'career': {'career', 'trust'},
        'trust': {'trust'},
        'wiki': {'wiki'},
        'home': {'todos', 'personality'},
        'family': {'trust'},
        'futurespeak': {'career'},
        'code': set(),
        'studio': set(),
        'system': set(),
        'news': set(),
        'finance': set(),
        'health': set(),
        'contacts': {'trust'},
    }
    needs.update(ws_map.get(workspace, set()))

    # Message keyword detection
    career_words = ['job', 'career', 'interview', 'resume', 'salary', 'apply', 'application',
                    'hire', 'offer', 'pipeline', 'role', 'position', 'recruiter']
    trust_words = ['trust', 'who is', 'tell me about', 'what do you know about',
                   'relationship', 'score', 'person']
    family_words = ['daughter', 'son', 'child', 'kid',
                    'partner', 'spouse', 'dog', 'pet', 'family', 'birthday']
    todo_words = ['todo', 'task', 'to-do', 'to do', 'pending', 'approve', 'action item']
    wiki_words = ['briefing', 'wiki', 'notes', 'article', 'research', 'report']
    memory_words = ['remember', 'recall', 'memory', 'earlier', 'last time', 'you said',
                    'we discussed', 'we talked']

    if any(w in msg_lower for w in career_words):
        needs.add('career')
    if any(w in msg_lower for w in trust_words):
        needs.add('trust')
    if any(w in msg_lower for w in family_words):
        needs.add('trust')
    if any(w in msg_lower for w in todo_words):
        needs.add('todos')
    if any(w in msg_lower for w in wiki_words):
        needs.add('wiki')
    if any(w in msg_lower for w in memory_words):
        needs.add('memory')

    return needs


_VAULT_AC = None
_VAULT_AC_LOCK = threading.Lock()


def _get_vault_control():
    """Lazy singleton VaultAccessControl, logging to the vault access log."""
    global _VAULT_AC
    if VaultAccessControl is None:
        return None
    if _VAULT_AC is None:
        with _VAULT_AC_LOCK:
            if _VAULT_AC is None:
                _VAULT_AC = VaultAccessControl(
                    log_path=FRIDAY_DIR / "vault" / "access-log.jsonl"
                )
    return _VAULT_AC


def _vault_local_only():
    """Whether vault gating is active (settings.model_routing.vault_local_only)."""
    try:
        cfg = (_load_settings().get('model_routing') or {})
        return bool(cfg.get('vault_local_only', True))
    except Exception:
        return True


def _vault_cloud_fallback():
    try:
        cfg = (_load_settings().get('model_routing') or {})
        return cfg.get('vault_cloud_fallback', 'redact')
    except Exception:
        return 'redact'


def _build_context_prompt(message, workspace='', workspace_context=None,
                          vision_description=None, provider='cloud',
                          vault_control=None, vault_fallback='redact'):
    """Build an enriched system prompt with all relevant context layers.

    When `vault_control` is provided, each context section is tagged with a
    sensitivity tier and gated for `provider`: a local model sees everything,
    while a cloud model receives TIER_1 only (TIER_2 redacted, TIER_3 dropped).
    With `vault_control=None` the prompt is assembled ungated (legacy behavior).
    """
    vault = _load_vault_summary()
    needs = _detect_context_needs(message, workspace)
    sources_consulted = []

    # Tier helpers. Default to PUBLIC; sensitive sections opt up. When the
    # vault_access module is unavailable, tiers are inert integers.
    _T1 = getattr(_VaultTier, 'PUBLIC', 1)
    _T2 = getattr(_VaultTier, 'PRIVATE', 2)
    _T3 = getattr(_VaultTier, 'SENSITIVE', 3)

    sections = []  # list of (tier, text)

    def add(text, tier=_T1):
        sections.append((tier, text))

    def classify(text, fallback_tier=_T2):
        if vault_control is not None:
            try:
                return vault_control.classify(text, default=fallback_tier)
            except Exception:
                return fallback_tier
        return fallback_tier

    add(FRIDAY_SYSTEM_PROMPT, _T1)

    # Layer 0: Always-on daily context (briefing headlines, career pipeline,
    # countdowns, trust circle, personality). The chat endpoint should never
    # answer cold — Friday is a personal agent, not a generic chatbot.
    try:
        # Defined in services/voice_engine.py — an UPPER layer — lazy import
        # only. (Before this, every chat turn silently degraded to
        # "(load failed: name '_load_live_context' is not defined)".)
        from agent_friday.services.voice_engine import _load_live_context
        live_ctx = _load_live_context()
        if live_ctx:
            # Today's context names the trust circle / family countdowns → private.
            add(f"\n== TODAY'S CONTEXT ==\n{live_ctx}", _T2)
            sources_consulted.append('daily_context')
    except Exception as _e:
        add(f"\n== TODAY'S CONTEXT ==\n(load failed: {_e})", _T1)

    # Layer 1: Active workspace context (from frontend) — may show finance/health
    # data, so classify by what's actually in the payload.
    if workspace_context:
        _ws_text = (
            f"\n== ACTIVE WORKSPACE: {workspace_context.get('name', workspace)} ==\n"
            f"What the user is looking at right now:\n"
            f"{json.dumps(workspace_context.get('data', {}), indent=2, default=str)[:2000]}"
        )
        add(_ws_text, classify(_ws_text, _T2))
        if workspace_context.get('focus'):
            add(f"Current focus: {workspace_context['focus']}", _T2)
        sources_consulted.append('workspace')

    # Layer 2: Vault data (personality always included). Friday's own state is
    # not personal data about the user, so it stays public.
    if 'personality' in needs and 'personality' in vault:
        p = vault['personality']
        add(
            f"\n== FRIDAY STATE ==\n"
            f"Maturity: {p.get('maturity', 0.5):.0%} · Sessions: {p.get('session_count', 0)} · "
            f"Temperature: {p.get('temperature', 0.7)}",
            _T1,
        )
        sources_consulted.append('personality')

    if 'trust' in needs and 'trust_people' in vault:
        # Check if message references a specific person
        trust_data_raw = None
        tfile = FRIDAY_DIR / "trust_graph.json"
        if tfile.exists():
            try:
                trust_data_raw = json.loads(tfile.read_text(encoding='utf-8'))
            except Exception:
                pass

        # Try to find a specific person mentioned
        person_match = None
        if trust_data_raw:
            for name in vault['trust_people']:
                if name.lower() in message.lower():
                    person_match = _lookup_trust_person(name, trust_data_raw)
                    break

        if person_match:
            # Contacts / family details → private (local only).
            add(
                f"\n== TRUST DATA (specific person) ==\n"
                f"{json.dumps(person_match, indent=2, default=str)[:1500]}",
                _T2,
            )
        else:
            # General trust summary
            summary = ', '.join(
                f"{n} ({d.get('relationship', '?')}: {d.get('overall', '?')})"
                for n, d in list(vault['trust_people'].items())[:8]
            )
            add(f"\n== TRUST NETWORK ==\n{summary}", _T2)
        sources_consulted.append('trust_graph')

    if 'career' in needs:
        career = _get_career_context()
        if career:
            add(
                f"\n== CAREER OPS ==\n"
                f"Applications tracked: {career.get('applications_count', 0)}\n"
                f"Recent: {career.get('recent_applications', [])}\n"
                f"Pipeline: {career.get('pipeline_summary', 'N/A')[:500]}",
                _T2,
            )
            sources_consulted.append('career_ops')

    if 'todos' in needs and 'active_todos' in vault:
        todo_list = '\n'.join(
            f"- [{t['status']}] {t['task']}" for t in vault['active_todos']
        )
        add(f"\n== ACTIVE TASKS ==\n{todo_list or 'No pending tasks.'}", _T2)
        sources_consulted.append('todos')

    if 'memory' in needs and 'recent_memories' in vault:
        mem_text = '\n'.join(f"- {m}" for m in vault['recent_memories'])
        add(f"\n== RECENT MEMORIES ==\n{mem_text}", _T2)
        sources_consulted.append('memory')

    if 'wiki' in needs:
        # Extract a search term from the message
        topic = message.strip()[:50]
        wiki_results = _get_wiki_context(topic)
        if wiki_results:
            wiki_text = '\n'.join(
                f"[{r['file']}]: {r['excerpt'][:300]}" for r in wiki_results
            )
            # Wiki is generally public docs, but may surface family/health → classify.
            add(f"\n== WIKI/BRIEFING DATA ==\n{wiki_text}", classify(wiki_text, _T1))
            sources_consulted.append('wiki')

    if 'epistemic' in needs:
        try:
            from agent_friday.epistemic_engine import get_epistemic_engine
            _ee = get_epistemic_engine()
            add(f"\n== EPISTEMIC STATE ==\n{_ee.get_prompt_injection()}", _T1)
        except Exception:
            if 'epistemic' in vault:
                add(
                    f"\n== EPISTEMIC STATE ==\n"
                    f"Independence score: {vault['epistemic'].get('overall', 0.72)}",
                    _T1,
                )

    # Layer 2.5: Project context files (.friday-context.md / AGENTS.md)
    # Hermes-inspired: drop a context file in any project directory and Friday
    # will automatically inject it when relevant.  We search CWD + common
    # project roots + any path mentioned in the message.
    _ctx_search_dirs = [
        Path.cwd(),
        HOME / "Projects",
        HOME / "Desktop",
    ]
    _msg_lower_ctx = message.lower()
    # Also pull any directory-looking tokens from the message
    for token in re.findall(r'[A-Za-z]:\\[^\s\'"]+|~/[^\s\'"]+', message):
        try:
            _ctx_search_dirs.append(Path(token).expanduser())
        except Exception:
            pass
    _ctx_names = ['.friday-context.md', 'AGENTS.md', '.friday-context.txt']
    _ctx_found = []
    for d in _ctx_search_dirs:
        if not d.is_dir():
            continue
        for name in _ctx_names:
            p = d / name
            if p.exists():
                try:
                    _ctx_found.append((str(p), p.read_text(encoding='utf-8', errors='replace')[:3000]))
                except Exception:
                    pass
    if _ctx_found:
        ctx_block = '\n\n'.join(f"[{path}]\n{content}" for path, content in _ctx_found[:2])
        # Project/code context — public unless the file itself carries PII.
        add(f"\n== PROJECT CONTEXT FILES ==\n{ctx_block}", classify(ctx_block, _T1))
        sources_consulted.append('context_files')

    # Layer 2.6: Portable skills (SKILL.md registry) whose triggers match the
    # message. Injecting the matched skill's procedure is what makes a learned or
    # imported skill actually shape behavior on the next turn.
    try:
        import agent_friday.skill_registry as _skreg
        _skill_block = _skreg.build_injection(message, limit=3)
        if _skill_block:
            add(f"\n== MATCHED SKILLS (follow when relevant) ==\n{_skill_block}", _T1)
            sources_consulted.append('skills')
    except Exception:
        pass

    # Layer 3: Vision context (from Gemini screen capture) — the screen could
    # show anything private, so treat it as private by default.
    if vision_description:
        add(
            f"\n== SCREEN VISION (what the user's screen shows) ==\n"
            f"{vision_description[:1500]}",
            classify(vision_description, _T2),
        )
        sources_consulted.append('vision')

    # Layer 4: SMART context — only the wiki sections this turn likely needs.
    # Keyword-routed (career/family/finance/health/person-name) plus workspace
    # hints. Anything missing can be fetched on demand via search_wiki /
    # read_wiki tools. Capped ~8KB to keep the system prompt lean.
    try:
        wiki_smart = _load_smart_context(message, workspace)
        if wiki_smart:
            _smart_text = (
                "\n== PERSONAL CONTEXT (smart-loaded for this turn) ==\n"
                "If you need a fact not present here, call search_wiki "
                "(keyword search) or read_wiki (specific file).\n\n"
                f"{wiki_smart}"
            )
            # Smart context mixes family/professional (private) with finance/
            # health/legal (sensitive) — classify so cloud drops the sensitive bits.
            add(_smart_text, classify(_smart_text, _T2))
            sources_consulted.append('wiki_smart')
    except Exception as _e:
        add(f"\n== PERSONAL CONTEXT ==\n(smart-context load failed: {_e})", _T1)

    # Assemble. With a vault_control + cloud provider this gates by tier
    # (TIER_1 in full, TIER_2 redacted, TIER_3 dropped). Otherwise it's a
    # plain join — identical to the legacy ungated behavior.
    if vault_control is not None:
        try:
            return vault_control.assemble_prompt(
                sections, provider, fallback=vault_fallback
            ), sources_consulted
        except VaultAccessDenied:
            raise
        except Exception as _ae:
            print(f"  [VAULT] assemble failed, falling back to ungated: {_ae}")
    return '\n'.join(t for _, t in sections), sources_consulted


def _load_smart_context(user_message, workspace=None):
    """Load only relevant wiki context based on the user's message and active workspace.

    Keyword-driven loader — instead of dumping the full ~80KB wiki into every
    system prompt, we route on intent: career talk pulls professional/, family
    talk pulls family/ + legal/, person names trigger a trust-graph hit, etc.
    The result is capped at ~8KB. Anything the loader missed, Claude can pull
    on demand via the search_wiki / read_wiki tools.
    """
    context_parts = []

    # ALWAYS: core identity (first 500 chars only — enough to anchor)
    core_profile = WIKI_DIR / "identity" / "core-profile.md"
    if core_profile.exists():
        try:
            text = wiki_read_text(core_profile)[:500]
            context_parts.append(f"== CORE IDENTITY ==\n{text}")
        except Exception:
            pass

    # ALWAYS: today's date and active workspace
    context_parts.append(f"Today: {date.today().isoformat()}")
    if workspace:
        context_parts.append(f"Active workspace: {workspace}")

    msg_lower = (user_message or "").lower()

    # Career / job keywords
    if any(w in msg_lower for w in ['career', 'job', 'role', 'interview', 'resume', 'application', 'salary', 'pipeline']):
        _load_section(context_parts, WIKI_DIR / "professional", max_bytes=40_000)

    # Family keywords
    if any(w in msg_lower for w in ['family', 'daughter', 'son', 'child', 'partner', 'spouse']):
        _load_section(context_parts, WIKI_DIR / "family", max_bytes=20_000)
        _load_section(context_parts, WIKI_DIR / "legal", max_bytes=20_000)

    # Finance keywords
    if any(w in msg_lower for w in ['finance', 'money', 'budget', 'investment', 'bank', 'tax']):
        _load_friday_data(context_parts, "finance", max_bytes=10_000)

    # Health keywords
    if any(w in msg_lower for w in ['health', 'medication', 'doctor', 'appointment', 'insurance']):
        _load_friday_data(context_parts, "health", max_bytes=10_000)

    # Person-name detection — pull the trust-graph entry for anyone named
    trust_path = FRIDAY_DIR / "trust_graph.json"
    if trust_path.exists():
        try:
            trust = json.loads(trust_path.read_text(encoding='utf-8'))
            people = trust.get('people', {})
            if isinstance(people, dict):
                for name, entry in people.items():
                    if name and name.lower() in msg_lower:
                        context_parts.append(
                            f"== TRUST GRAPH: {name} ==\n{json.dumps(entry, indent=2, default=str)[:1500]}"
                        )
        except Exception:
            pass

    # Projects / business keywords
    if any(w in msg_lower for w in ['futurespeak', 'business', 'client', 'project', 'revenue']):
        _load_friday_data(context_parts, "futurespeak", max_bytes=10_000)

    # Workspace-specific context
    if workspace == 'news':
        _load_latest_briefing_summary(context_parts)
    elif workspace == 'career':
        _load_section(context_parts, WIKI_DIR / "professional", max_bytes=40_000)

    # Soft cap — 1M context window means we can afford generous context.
    result = "\n\n".join(context_parts)
    if len(result) > 200_000:
        result = result[:200_000] + "\n[context soft-capped — use search_wiki or read_wiki for more]"
    return result


def _generate_wiki_indexes():
    """Create _index.md in each wiki directory listing files with one-line descriptions.

    Called at startup so the agent can read a directory's table of contents before
    deciding which full articles to load — dramatically reduces context waste.
    """
    if not WIKI_DIR.exists():
        return
    dirs_to_index = [WIKI_DIR] + [p for p in WIKI_DIR.rglob('*') if p.is_dir()]
    for directory in dirs_to_index:
        md_files = [f for f in directory.glob('*.md') if f.name != '_index.md']
        if not md_files:
            continue
        lines = [f"# Index: {directory.name}\n"]
        for f in sorted(md_files, key=lambda x: x.name):
            try:
                text = wiki_read_text(f)
                # Use first non-empty, non-heading line as description
                desc = next(
                    (l.strip() for l in text.splitlines() if l.strip() and not l.startswith('#')),
                    f.stem
                )
                lines.append(f"- {f.name}: {desc[:120]}")
            except Exception:
                lines.append(f"- {f.name}")
        try:
            wiki_write_text(directory / '_index.md', '\n'.join(lines))
        except Exception:
            pass


def _load_section(parts, directory, max_bytes=20_000):
    """Load wiki section files up to max_bytes (most-recent first).

    Loads _index.md first so the agent sees the directory's table of contents
    before deciding which full articles to read on demand.
    """
    if not directory.exists():
        return
    # Always load index first if available
    index_file = directory / '_index.md'
    if index_file.exists():
        try:
            idx_text = wiki_read_text(index_file)[:2000]
            parts.append(f"== {directory.name.upper()} INDEX ==\n{idx_text}")
        except Exception:
            pass
    total = 0
    try:
        files = sorted(directory.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True)
    except Exception:
        return
    for f in files:
        if f.name == '_index.md':
            continue  # already loaded above
        if total >= max_bytes:
            break
        try:
            text = wiki_read_text(f)
        except Exception:
            continue
        chunk = text[:max_bytes - total]
        parts.append(f"== {f.stem.upper()} ==\n{chunk}")
        total += len(chunk)


def _load_friday_data(parts, subdir, max_bytes=10_000):
    """Load JSON files from ~/.friday/<subdir>/, most-recent first."""
    data_dir = FRIDAY_DIR / subdir
    if not data_dir.exists():
        return
    total = 0
    try:
        files = sorted(data_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
    except Exception:
        return
    for f in files:
        if total >= max_bytes:
            break
        try:
            text = wiki_read_text(f)
        except Exception:
            continue
        chunk = text[:max_bytes - total]
        parts.append(f"== {subdir.upper()}/{f.stem} ==\n{chunk}")
        total += len(chunk)


def _load_latest_briefing_summary(parts):
    """Note the most recent briefing exists; don't load the full HTML."""
    briefing_dir = FRIDAY_DIR / "wiki" / "briefings"
    if not briefing_dir.exists():
        return
    try:
        files = sorted(briefing_dir.glob("*.html"), reverse=True)
    except Exception:
        return
    if files:
        parts.append(f"== LATEST BRIEFING ==\nMost recent: {files[0].name} (use get_briefing tool to read it)")


