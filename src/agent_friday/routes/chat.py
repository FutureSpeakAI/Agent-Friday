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
    CHAT_HISTORY,
    FRIDAY_PASSWORD,
    _load_agent_personality,
    _load_chat_history,
    _load_settings,
    _log_context,
    _rehydrate_pii,
    _save_chat_history,
    _scrub_pii,
    _settings_system_prefix,
    get_anthropic_client,
    process_register,
    process_remove,
)  # noqa: E501
from agent_friday.services.agent import (
    ACTION_PERMISSION_POLICY,
    prepare_confirmation_ctx,
    CLAUDE_TOOLS,
    _CC_PERMISSION,
    _call_claude_agent,
    _generate_agent,
    _maybe_handle_navigate_intent,
    _maybe_handle_open_intent,
    _resolve_workspace,
)  # noqa: E501
from agent_friday.services.model_router import (
    CITATION_INSTRUCTIONS,
    _build_context_prompt,
    _build_emotional_tone_block,
    _build_memory_context_block,
    _build_session_continuity_block,
    _call_ollama,
    _call_openai,
    _compress_trajectory,
    _current_session_id,
    _factcheck_news_citations,
    _generate_text,
    _get_context_compressor,
    _get_context_pruner,
    _get_conversation_memory,
    _get_friday_system_prompt,
    _get_vault_control,
    _index_chat_turn,
    _vault_cloud_fallback,
    _vault_local_only,
)  # noqa: E501

chat_bp = Blueprint('chat', __name__)



@chat_bp.route('/api/chat', methods=['POST'])
def chat():
    """Text chat — powered by Anthropic Claude.

    Vision (screenshot description) still routes through Gemini Flash, since vision
    is a designer/perception task. Reasoning stays on Claude.
    """
    try:
        data = request.get_json(silent=True) or {}
        message = data.get('message', '')
        workspace = data.get('workspace', '')
        workspace_context = data.get('workspaceContext', None)
        # v5: feed the LOCAL user model from each user turn (best-effort — never
        # blocks or fails the chat turn). Personalizes future system prompts.
        try:
            from agent_friday.services import user_model as _um
            _um.observe_message(message, role='user', workspace=workspace)
        except Exception:
            pass
        include_vision = data.get('includeVision', False)
        voice_mode = bool(data.get('voice_mode', False))
        # Source Production Mode — when true, Friday cites every factual claim
        # inline. Falls back to the persisted settings toggle so the preference
        # survives across turns even if the client omits the flag.
        settings_early = _load_settings()
        cite_sources = bool(data.get('cite_sources',
                                     settings_early.get('cite_sources', False)))
        session_id = _current_session_id()
        vision_description = None

        # ── UI Navigation: "open studio" / "switch to news" → drive the frontend ──
        # Returns a structured `actions` payload the client executes (via
        # window.fridayOpenWorkspace). Runs BEFORE the OS open-intent check so a
        # curated workspace name wins over a same-named home folder (e.g. "open
        # home" → Home workspace, not the home directory). Anything that ISN'T a
        # known workspace — "open downloads", "open the projects folder",
        # "open notepad" — resolves to None here and falls through to the OS path
        # below. Provider-independent: works on a local-only (Ollama, no key, no
        # tool loop) install too, so normal chat is never hijacked.
        try:
            _nav = _maybe_handle_navigate_intent(message)
        except Exception as _nie:
            print(f"  [NAV-INTENT] skipped: {_nie}")
            _nav = None
        if _nav is not None:
            _nav_reply, _nav_ws = _nav
            _u = {'id': str(uuid.uuid4()), 'timestamp': datetime.now().isoformat(),
                  'role': 'user', 'text': message, 'pinned': False, 'workspace': workspace}
            _f = {'id': str(uuid.uuid4()), 'timestamp': datetime.now().isoformat(),
                  'role': 'friday', 'text': _nav_reply, 'pinned': False, 'sources': []}
            CHAT_HISTORY.append(_u)
            CHAT_HISTORY.append(_f)
            try:
                _save_chat_history(CHAT_HISTORY)
            except Exception:
                pass
            return jsonify({
                "response": _nav_reply, "user_msg": _u, "friday_msg": _f, "sources": [],
                "tool_trace": [{"name": "navigate", "input": {"workspace": _nav_ws},
                                "result": _nav_reply}],
                "actions": [{"type": "navigate", "workspace": _nav_ws}],
            })

        # ── Computer Control: deterministic open-file/folder/app intent ──
        # Runs BEFORE the model so it works on every provider — including a
        # local-only (Ollama) install with no API key and no tool-use loop.
        # Only fires when the target resolves to something real (see
        # _maybe_handle_open_intent), so normal chat is never hijacked. Low-risk:
        # it reveals/opens a path or launches a known app, never writing/deleting.
        try:
            _open_reply = _maybe_handle_open_intent(message)
        except Exception as _oie:
            print(f"  [OPEN-INTENT] skipped: {_oie}")
            _open_reply = None
        if _open_reply is not None:
            _u = {'id': str(uuid.uuid4()), 'timestamp': datetime.now().isoformat(),
                  'role': 'user', 'text': message, 'pinned': False, 'workspace': workspace}
            _f = {'id': str(uuid.uuid4()), 'timestamp': datetime.now().isoformat(),
                  'role': 'friday', 'text': _open_reply, 'pinned': False, 'sources': []}
            CHAT_HISTORY.append(_u)
            CHAT_HISTORY.append(_f)
            try:
                _save_chat_history(CHAT_HISTORY)
            except Exception:
                pass
            return jsonify({
                "response": _open_reply, "user_msg": _u, "friday_msg": _f,
                "sources": [], "tool_trace": [{"tool": "open_path", "result": _open_reply}],
            })

        # Vision capture (Gemini, designer role). Accept either `screenshot`
        # (legacy) or `image` (Camera Mode frames). If an image is sent at all,
        # use it — no need for the explicit includeVision flag.
        screenshot_b64 = data.get('image') or data.get('screenshot') or None
        if screenshot_b64 and (include_vision or data.get('image') is not None):
            try:
                from google import genai
                from google.genai import types
                gclient = genai.Client(api_key=core.GEMINI_API_KEY)  # pragma: allowlist secret
                img_bytes = base64.b64decode(screenshot_b64)
                mime = 'image/jpeg' if data.get('image') else 'image/png'
                vision_resp = gclient.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=[
                        "Briefly describe what is visible on this screen. Focus on text, UI elements, and data shown. Be concise (2-3 sentences).",
                        types.Part.from_bytes(data=img_bytes, mime_type=mime),
                    ],
                )
                vision_description = vision_resp.text
            except Exception as ve:
                vision_description = f"[Vision unavailable: {ve}]"

        settings = _load_settings()
        personality = _load_agent_personality()

        # ── Demo mode ── no provider configured → return a labelled canned reply so
        # a new user can explore the UI without an error. (The deterministic nav /
        # open-intent handlers above still run first — they need no provider.)
        try:
            from agent_friday.services.demo_mode import is_demo, demo_response
            if is_demo(settings):
                _dr = demo_response('chat', message)
                _u = {'id': str(uuid.uuid4()), 'timestamp': datetime.now().isoformat(),
                      'role': 'user', 'text': message, 'pinned': False, 'workspace': workspace}
                _f = {'id': str(uuid.uuid4()), 'timestamp': datetime.now().isoformat(),
                      'role': 'friday', 'text': _dr, 'pinned': False, 'sources': []}
                CHAT_HISTORY.append(_u)
                CHAT_HISTORY.append(_f)
                try:
                    _save_chat_history(CHAT_HISTORY)
                except Exception:
                    pass
                return jsonify({"response": _dr, "user_msg": _u, "friday_msg": _f,
                                "sources": [], "demo_mode": True, "tool_trace": []})
        except Exception as _de:
            print(f"  [DEMO] skipped: {_de}")

        # Build conversation history as Anthropic-format messages.
        # Pull up to 40 turns, then run trajectory compression if the total
        # char count is above the soft limit — older turns get summarised.
        raw_history = []
        for msg in CHAT_HISTORY[-100:]:
            role = 'user' if msg.get('role') == 'user' else 'assistant'
            text = msg.get('text', '')
            if text:
                raw_history.append({"role": role, "content": text})
        messages = _compress_trajectory(raw_history)
        messages.append({"role": "user", "content": message})

        # ── Semantic context pruning (RAG over our own history) ──
        # When the conversation is long, keep the turns most relevant to the
        # current prompt instead of letting the oldest ones fall off. Only the
        # messages SENT to the API are pruned — CHAT_HISTORY (the session
        # archive) is untouched, so future turns can still retrieve everything.
        _prune_cfg = settings.get('context_pruning') or {}
        if _prune_cfg.get('enabled', True):
            try:
                pruner = _get_context_pruner(_prune_cfg)
                if pruner.should_prune(messages):
                    _orig_count = len(messages)
                    messages = pruner.prune(messages, message)
                    _pruned_count = len(messages)
                    _topk = _prune_cfg.get('top_k', 10)
                    print(f"Context pruned: {_orig_count} turns → "
                          f"{_pruned_count} turns ({_topk} semantic matches)")
                    # Brief process orb so the user can see pruning happen.
                    _prune_pid = f"prune-{uuid.uuid4().hex[:8]}"
                    try:
                        process_register(
                            _prune_pid, name="Context Pruning",
                            label="Context Pruning", category="monitoring",
                            icon="🧠",
                        )
                        threading.Timer(2.0, process_remove, args=(_prune_pid,)).start()
                    except Exception:
                        pass
            except Exception as _pe:
                # Pruning is best-effort — never block a chat on it.
                print(f"  [PRUNE] skipped: {_pe}")

        # ── Headroom compression (compress the CONTENT of the kept turns) ──
        # The pruner just chose WHICH turns survive; Headroom now squeezes the
        # JSON tool outputs, code, and prose INSIDE them before they hit the API.
        # Runs before PII scrubbing so the [PII:...] tags it inserts stay intact.
        # Best-effort: any failure falls back to the uncompressed messages.
        _compress_cfg = settings.get('context_compression') or {}
        if _compress_cfg.get('enabled', True):
            try:
                compressor = _get_context_compressor(_compress_cfg)
                if compressor.should_compress(messages):
                    _selected_model = settings.get('orchestrator_model') or 'claude-opus-4-8'
                    # Brief process orb so the user can see compression happen.
                    _comp_pid = f"compress-{uuid.uuid4().hex[:8]}"
                    try:
                        process_register(
                            _comp_pid, name="Compressing Context",
                            label="Compressing Context", category="monitoring",
                            icon="📦",
                        )
                        threading.Timer(2.0, process_remove, args=(_comp_pid,)).start()
                    except Exception:
                        pass
                    messages = compressor.compress(messages, model=_selected_model)
            except Exception as _ce:
                # Compression is best-effort — never block a chat on it.
                print(f"  [HEADROOM] skipped: {_ce}")

        # ── Model Routing: decide local vs cloud BEFORE building the prompt. ──
        # The routing decision drives the whole privacy posture downstream:
        #   • route.is_local       → True for Ollama (on-device)
        #   • route.vault_allowed  → raw vault content may be sent (local only)
        #   • route.scrub_pii      → PII scrubber must run (cloud only)
        # We ALWAYS consult the router now — even in cloud_only mode — so a
        # vault-touching request is force-routed local (or refused) and vault
        # data never reaches the cloud.
        _routing_cfg = settings.get('model_routing') or {}
        _orb_label = (message or '').strip().splitlines()[0][:24] or 'Chat'
        try:
            from agent_friday.routing.model_router import get_router
            _router = get_router(_routing_cfg)
            _route_info = _router.route(messages, task_context={
                "has_tools": True,
                "workspace": workspace,
                "cloud_model": settings.get('orchestrator_model') or 'claude-opus-4-8',
            })
        except Exception as _re:
            print(f"  [ROUTER] routing failed, defaulting to cloud: {_re}")
            _route_info = {
                "provider": "cloud",
                "model": settings.get('orchestrator_model') or 'claude-opus-4-8',
                "is_local": False, "vault_allowed": False, "scrub_pii": True,
                "vault_access": False, "refuse": False, "warning": None,
            }

        _provider = _route_info.get('provider', 'cloud')
        _routed_local = bool(_route_info.get('is_local'))
        _vault_access = bool(_route_info.get('vault_access'))

        def _vault_orb(label):
            """Show the green 🔒 vault orb (monitoring) for ~3s."""
            _vpid = f"vault-{uuid.uuid4().hex[:8]}"
            try:
                process_register(_vpid, name="Vault Access", label=label,
                                 category="monitoring", icon="🔒", color=0x22c55e)
                threading.Timer(3.0, process_remove, args=(_vpid,)).start()
            except Exception:
                pass

        # ── Refuse: a vault request that cannot be served locally (deny/warn). ──
        # Never send vault data to the cloud — return the warning instead.
        if _route_info.get('refuse'):
            _warn = _route_info.get('warning') or (
                "This request needs vault access which requires a local model. "
                "Please install Ollama or switch to local routing mode."
            )
            _vault_orb("Vault Access — Blocked")
            user_msg = {
                'id': str(uuid.uuid4()), 'timestamp': datetime.now().isoformat(),
                'role': 'user', 'text': message, 'pinned': False, 'workspace': workspace,
            }
            friday_msg = {
                'id': str(uuid.uuid4()), 'timestamp': datetime.now().isoformat(),
                'role': 'friday', 'text': _warn, 'pinned': False, 'sources': [],
            }
            CHAT_HISTORY.append(user_msg)
            CHAT_HISTORY.append(friday_msg)
            _save_chat_history(CHAT_HISTORY)
            return jsonify({
                "response": _warn, "user_msg": user_msg, "friday_msg": friday_msg,
                "sources": [], "tool_trace": [], "vault_blocked": True,
            })

        if _vault_access and _routed_local:
            _vault_orb("Vault Access — Local Only")

        # ── Computer Control needs the cloud tool-use loop. ──
        # The local (Ollama) path is single-shot text: no tools, no vision-in, no
        # agentic loop — so a local model literally cannot see the screen or drive
        # the mouse. When the user has Computer Control enabled, force this turn to
        # the cloud model (which has the tool loop), UNLESS the turn touches the
        # vault — vault data must never leave the device, so privacy wins there.
        if _CC_PERMISSION.is_set() and _routed_local and not _vault_access:
            print("  [ROUTER] Computer Control enabled — routing to cloud for the tool-use loop")
            _routed_local = False
            _provider = 'cloud'
            _route_info['model'] = settings.get('orchestrator_model') or ANTHROPIC_MODEL_DEFAULT

        # ── Build the (vault-gated) system prompt + scrub PII for the provider. ──
        # Cloud: vault TIER_2/TIER_3 content is gated out and PII is scrubbed.
        # Local: raw vault content flows and the PII scrubber is SKIPPED entirely
        # (the data never leaves the device). Returns the per-request lookup used
        # to rehydrate PII tags out of the cloud model's reply.
        # ── Persistent memory recall + citation instructions ──
        # Provider-independent text appended to the system prompt. Memory recall
        # gives Friday cross-session continuity (RAG over past conversations);
        # the citation block (only in cite_sources mode) tells Friday to
        # attribute every factual claim. Both are PII-scrubbed for cloud below.
        _extra_system = _build_memory_context_block(message, session_id)
        # Continuity from the most recent end-of-day session summary + tone
        # adaptation from the accumulated emotional arc. Both are content-free /
        # already-summarised, provider-independent, and PII-scrubbed for cloud
        # below like the rest of _extra_system.
        _extra_system += _build_session_continuity_block()
        _extra_system += _build_emotional_tone_block()
        # Ask-first action policy — the model asks before acting; the gate in
        # _execute_tool enforces it mechanically if the model forgets.
        _extra_system += "\n\n" + ACTION_PERMISSION_POLICY
        if cite_sources:
            _extra_system += CITATION_INSTRUCTIONS

        def _prep_for(provider):
            vc = _get_vault_control() if _vault_local_only() else None
            sp, src = _build_context_prompt(
                message, workspace, workspace_context, vision_description,
                provider=provider, vault_control=vc,
                vault_fallback=_vault_cloud_fallback(),
            )
            sp = _settings_system_prefix(settings, personality) + (sp or '')
            if _extra_system:
                sp = sp + "\n" + _extra_system
            if voice_mode:
                sp = (
                    "=== VOICE MODE ACTIVE ===\n"
                    "The user is speaking to you via microphone. Your reply will be read aloud.\n"
                    "Rules: Keep it SHORT (1-3 sentences). Never use markdown — no asterisks, "
                    "headers, bullet points, or code blocks. Use natural speech patterns and "
                    "contractions. Ask a follow-up question to keep the conversation flowing.\n"
                    "=========================\n\n"
                ) + sp
            lookup = {}
            # Scrub only when the turn is cloud-bound. Scrubbing every message
            # (not just the new one) means a cached LOCAL reply retrieved by the
            # pruner is scrubbed at retrieval time before it can reach the cloud.
            if provider != 'local':
                if sp:
                    sp, sub = _scrub_pii(sp)
                    lookup.update(sub)
                for m in messages:
                    c = m.get('content')
                    if isinstance(c, str) and c:
                        m['content'], sub = _scrub_pii(c)
                        lookup.update(sub)
                if lookup:
                    sp += (
                        "\n\n== PRIVACY PLACEHOLDERS ==\n"
                        "Some private values in your context appear as tags like "
                        "[PII:type:hash] (types: addr, phone, email, ssn, cc, name). "
                        "These are stable references to real data on the user's device. "
                        "Use them in your reply EXACTLY as written when you need to "
                        "reference the underlying value — they will be substituted "
                        "with the real data before the user sees your response."
                    )
            return sp, src, lookup

        system_prompt, sources, pii_lookup = _prep_for(_provider)

        _sess_ctx = {
            "authenticated": bool(session.get("authenticated")) or not bool(FRIDAY_PASSWORD),
            "provider": _provider,
        }
        # Wire this turn into the ask-first action flow: stamps the session id so
        # the confirmation gate is live, and grants a pending action when this
        # message is the user's "yes" to a question Friday asked last turn.
        _sess_ctx = prepare_confirmation_ctx(session_id, message, _sess_ctx)

        # ── Safety net: local-first, no Anthropic key. ──
        # The router classifies every tool-enabled chat as TOOL_USE → cloud (and
        # in the default cloud_only mode everything is cloud). With no
        # ANTHROPIC_API_KEY that sends the turn to _call_claude_agent, which raises
        # "ANTHROPIC_API_KEY is not set" — the outer handler turns that into
        # "[Friday offline]", so the chat silently dies and every holographic orb /
        # scene-state cue that rides on a live turn never fires. When Ollama is
        # healthy, run the turn locally instead of crashing. Only triggers when the
        # alternative is a guaranteed failure, so it can't regress a working setup.
        if (not _routed_local) and _provider == 'cloud' and get_anthropic_client() is None:
            try:
                from agent_friday.routing.ollama_manager import get_manager
                _om = get_manager((settings.get('model_routing') or {}).get(
                    'ollama_url', 'http://localhost:11434'))
                _om_models = _om.list_models() if _om.is_available() else []
                if _om_models:
                    # Prefer a real on-device model; skip ':cloud' passthrough stubs.
                    # Pick the SMALLEST by size — it's the fastest to respond, which
                    # matters for live/interactive use (an 8B 'thinking' model can take
                    # minutes per turn). A configured model_routing.local_model wins.
                    _cfg_local = (settings.get('model_routing') or {}).get('local_model')
                    _real = [m for m in _om_models
                             if not str(m.get('name', '')).endswith(':cloud')] or _om_models
                    if _cfg_local and any(m.get('name') == _cfg_local for m in _real):
                        _local_pick = _cfg_local
                    else:
                        _local_pick = min(
                            _real,
                            key=lambda m: m.get('size') or m.get('size_gb') or 1e18,
                        )['name']
                    print(f"  [ROUTER] No Anthropic key; Ollama healthy — routing chat "
                          f"to local model {_local_pick}")
                    _routed_local = True
                    _provider = 'local'
                    _route_info['model'] = _local_pick
                    _route_info['is_local'] = True
                    # Rebuild the prompt for local (vault content allowed, no PII scrub).
                    system_prompt, sources, pii_lookup = _prep_for('local')
            except Exception as _safe_e:
                print(f"  [ROUTER] local safety-net routing failed: {_safe_e}")

        # ── Dispatch. ──
        reply, tool_trace = None, []
        if _routed_local:
            try:
                reply, tool_trace = _call_ollama(
                    messages, system=system_prompt,
                    model=_route_info['model'],
                    temperature=settings.get('temperature'),
                    orb_label=f"🏠 {_orb_label}",
                    orb_icon='🏠',
                    # Local models drive the full agent loop too: same unified
                    # tool registry, vault gate, and governance as the cloud path.
                    tools=CLAUDE_TOOLS, pii_lookup=pii_lookup, session_ctx=_sess_ctx,
                )
            except Exception as _ole:
                # A vault request must NEVER silently fall back to cloud with raw
                # vault data — fail loudly instead.
                if _vault_access:
                    print(f"  [ROUTER] local vault inference failed; refusing cloud fallback: {_ole}")
                    raise
                print(f"  [ROUTER] local inference failed, falling back to cloud: {_ole}")
                _routed_local = False
                _provider = 'cloud'
                # Rebuild the prompt for cloud (gated) and scrub before sending.
                system_prompt, sources, pii_lookup = _prep_for('cloud')

        if not _routed_local:
            if _provider == 'openai':
                # OpenAI-compatible cloud path (OpenRouter / any /v1 endpoint),
                # with a full agentic tool loop. Records its own cost.
                reply, tool_trace = _call_openai(
                    messages, system=system_prompt, model=_route_info.get('model'),
                    temperature=settings.get('temperature'),
                    orb_label=f"☁️ {_orb_label}", orb_icon='☁️',
                    tools=CLAUDE_TOOLS, pii_lookup=pii_lookup, session_ctx=_sess_ctx,
                )
            else:
                # Honor the user's chosen Claude model (orchestrator selection)
                # rather than always using the Anthropic default. Only forward a
                # claude-* id; anything unexpected falls back to the default.
                _cloud_model = _route_info.get('model')
                _claude_model = _cloud_model if str(_cloud_model or '').startswith('claude') else None
                reply, tool_trace = _call_claude_agent(
                    messages, system=system_prompt, model=_claude_model,
                    temperature=settings.get('temperature'),
                    pii_lookup=pii_lookup, session_ctx=_sess_ctx,
                    orb_label=_orb_label, orb_category='default', orb_icon='💬',
                )
                if _routing_cfg.get('cost_tracking', True):
                    try:
                        from agent_friday.routing.model_router import get_router
                        _router = get_router()
                        _est_tokens = len(str(messages)) // 4 + len(reply) // 4
                        _router.cost_tracker.record(
                            "cloud",
                            settings.get('orchestrator_model') or 'claude-opus-4-8',
                            prompt_tokens=_est_tokens, completion_tokens=len(reply) // 4,
                        )
                    except Exception:
                        pass

        # ── Rehydrate: restore real PII before returning to the user. ──
        if pii_lookup:
            reply = _rehydrate_pii(reply, pii_lookup)
            # Also rehydrate the tool trace so the UI shows real values.
            for entry in tool_trace:
                if isinstance(entry.get('result'), str):
                    entry['result'] = _rehydrate_pii(entry['result'], pii_lookup)

        # ── Fact-check: flag low-trust news citations. ──
        # When Friday cites a news outlet, consult its Source Trust Graph score
        # and append a verify-independently warning for anything below 0.5.
        # Cheap and self-gating (no-op unless the reply contains a [news:...]).
        reply = _factcheck_news_citations(reply)

        # Store in history with IDs, timestamps, and context metadata
        user_msg = {
            'id': str(uuid.uuid4()),
            'timestamp': datetime.now().isoformat(),
            'role': 'user',
            'text': message,
            'pinned': False,
            'workspace': workspace,
        }
        friday_msg = {
            'id': str(uuid.uuid4()),
            'timestamp': datetime.now().isoformat(),
            'role': 'friday',
            'text': reply,
            'pinned': False,
            'sources': sources,
        }
        CHAT_HISTORY.append(user_msg)
        CHAT_HISTORY.append(friday_msg)

        # ── Context log: append both turns unless off-record. ──
        if not settings.get('off_record'):
            _log_context("chat_user", {
                "message": message,
                "workspace": workspace,
                "had_image": bool(screenshot_b64),
            })
            _log_context("chat_agent", {
                "reply": reply,
                "sources": sources,
                "tool_count": len(tool_trace or []),
            })

        # Epistemic scoring — score this turn in background
        try:
            from agent_friday.epistemic_engine import get_epistemic_engine
            threading.Thread(
                target=lambda m=message, r=reply: get_epistemic_engine().score_turn(m, r),
                daemon=True,
            ).start()
        except Exception:
            pass

        # Closed-loop learning — capture the turn trajectory + accumulate skill
        # metrics in the background. Feeds the nightly SkillOpt optimizer.
        try:
            import agent_friday.skill_capture as _skcap
            threading.Thread(
                target=lambda m=message, r=reply, tt=tool_trace, ws=workspace:
                    _skcap.capture(m, r, tool_trace=tt, workspace=ws),
                daemon=True,
            ).start()
        except Exception:
            pass

        # Persistent conversation memory — index both turns into ChromaDB in the
        # background so future sessions can recall and cite this exchange. Skip
        # when the user is off-record. Best-effort; never blocks the response.
        if not settings.get('off_record'):
            try:
                threading.Thread(
                    target=_index_chat_turn,
                    args=(message, reply, session_id, user_msg['id'], friday_msg['id']),
                    daemon=True,
                ).start()
            except Exception:
                pass

        # ── UI actions the model requested via tools → forward to the client. ──
        # When the agent loop calls the `navigate` tool, the on-screen move has to
        # happen in the browser, not the server. Surface it as a structured action
        # (alongside the text) so the frontend can execute it. Extra navigations
        # are deduped; the last one wins for the visible focus.
        actions = []
        _seen_nav = set()
        for _entry in (tool_trace or []):
            if _entry.get('name') != 'navigate':
                continue
            # Only move the UI if the navigate tool ACTUALLY executed. When it was
            # held back for confirmation (or denied by a gate), the result carries
            # a bracketed sentinel and we must NOT navigate — that would be the
            # very surprise the confirmation flow exists to prevent.
            _res = str(_entry.get('result') or '')
            if _res.lstrip().startswith(('[CONFIRMATION REQUIRED]', '[GOVERNANCE DENY]',
                                         '[SANDBOX DENY]', '[VAULT')):
                continue
            _ws = _resolve_workspace(str((_entry.get('input') or {}).get('workspace', '')))
            if _ws and _ws not in _seen_nav:
                _seen_nav.add(_ws)
                actions.append({"type": "navigate", "workspace": _ws})

        # Prune: keep pinned forever, others for 30 days, cap at 500 messages
        cutoff = (datetime.now() - timedelta(days=30)).isoformat()
        CHAT_HISTORY[:] = [m for m in CHAT_HISTORY if m.get('pinned') or m.get('timestamp', '') >= cutoff][-500:]
        _save_chat_history(CHAT_HISTORY)

        return jsonify({
            "response": reply,
            "user_msg": user_msg,
            "friday_msg": friday_msg,
            "sources": sources,
            "tool_trace": tool_trace,
            "actions": actions,
            "cite_sources": cite_sources,
            "session_id": session_id,
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"response": f"[Friday offline] {str(e)}"})


# ═══════════════════════════════════════════════════════════════
#  PERSISTENT CHAT HISTORY ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@chat_bp.route('/api/chat/history', methods=['GET'])
def chat_history():
    """Return chat history (last 30 days, pinned messages included)."""
    messages = _load_chat_history()
    return jsonify({"status": "ok", "messages": messages, "count": len(messages)})


@chat_bp.route('/api/chat/send', methods=['POST'])
def chat_send():
    """Send a message, save to persistent history, return Friday's response.
    Accepts context-aware payload: {message, workspace, workspaceContext, includeVision, screenshot}.
    Text reasoning is Claude; vision (screenshot description) stays on Gemini.
    """
    try:
        data = request.get_json(silent=True) or {}
        message = data.get('message', '')
        workspace = data.get('workspace', '')
        workspace_context = data.get('workspaceContext', None)
        # v5: feed the LOCAL user model from each user turn (best-effort — never
        # blocks or fails the chat turn). Personalizes future system prompts.
        try:
            from agent_friday.services import user_model as _um
            _um.observe_message(message, role='user', workspace=workspace)
        except Exception:
            pass
        include_vision = data.get('includeVision', False)
        vision_description = None

        if not message.strip():
            return jsonify({"status": "error", "message": "Empty message"}), 400

        # Vision capture (Gemini, designer role). Accept either `screenshot`
        # (legacy) or `image` (Camera Mode frames).
        screenshot_b64 = data.get('image') or data.get('screenshot') or None
        if screenshot_b64 and (include_vision or data.get('image') is not None):
            try:
                from google import genai
                from google.genai import types
                gclient = genai.Client(api_key=core.GEMINI_API_KEY)  # pragma: allowlist secret
                img_bytes = base64.b64decode(screenshot_b64)
                mime = 'image/jpeg' if data.get('image') else 'image/png'
                vision_resp = gclient.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=[
                        "Briefly describe what is visible on this screen. Focus on text, UI elements, and data shown. Be concise (2-3 sentences).",
                        types.Part.from_bytes(data=img_bytes, mime_type=mime),
                    ],
                )
                vision_description = vision_resp.text
            except Exception as ve:
                vision_description = f"[Vision unavailable: {ve}]"

        # Build context-enriched system prompt. This endpoint always goes to
        # Anthropic (cloud), so vault TIER_2/TIER_3 content is gated out here.
        settings = _load_settings()
        system_prompt, sources = _build_context_prompt(
            message, workspace, workspace_context, vision_description,
            provider='cloud',
            vault_control=(_get_vault_control() if _vault_local_only() else None),
            vault_fallback=_vault_cloud_fallback(),
        )

        # Prepend user-configured agent personality + response prefs + cLaws
        personality = _load_agent_personality()
        system_prompt = _settings_system_prefix(settings, personality) + (system_prompt or '')
        # Ask-first action policy (enforced by the gate in _execute_tool).
        system_prompt = system_prompt + "\n\n" + ACTION_PERMISSION_POLICY

        # Cross-session memory: recall relevant past exchanges + carry forward
        # the last session summary + adapt tone from the accumulated arc. This
        # endpoint is cloud-bound, so the appended text is gated/scrubbed by the
        # _generate_agent path like the rest of the prompt.
        _session_id = _current_session_id()
        try:
            _mem_block = (_build_memory_context_block(message, _session_id)
                          + _build_session_continuity_block()
                          + _build_emotional_tone_block())
            if _mem_block:
                system_prompt = system_prompt + "\n" + _mem_block
        except Exception as _mb_err:
            print(f"  [MEMORY] /chat/send recall skipped: {_mb_err}")

        # Anthropic-format message history
        messages = []
        for msg in CHAT_HISTORY[-100:]:
            role = 'user' if msg.get('role') == 'user' else 'assistant'
            text = msg.get('text', '')
            if text:
                messages.append({"role": role, "content": text})
        messages.append({"role": "user", "content": message})

        _sess_ctx = {
            "authenticated": bool(session.get("authenticated")) or not bool(FRIDAY_PASSWORD),
        }
        # Same ask-first action flow as /api/chat: enforce confirmation and honor
        # a "yes" reply to a question Friday asked on the previous turn.
        _sess_ctx = prepare_confirmation_ctx(_session_id, message, _sess_ctx)
        # Route through the provider-agnostic agent dispatcher rather than the
        # bare Anthropic loop, so this endpoint works on a local/OpenAI setup
        # instead of hard-failing with "ANTHROPIC_API_KEY is not set".
        reply, tool_trace = _generate_agent(
            messages, system=system_prompt, temperature=settings.get('temperature'),
            session_ctx=_sess_ctx, workspace=workspace,
        )

        # Create persistent message objects
        user_msg = {
            'id': str(uuid.uuid4()),
            'timestamp': datetime.now().isoformat(),
            'role': 'user',
            'text': message,
            'pinned': False,
            'workspace': workspace,
        }
        friday_msg = {
            'id': str(uuid.uuid4()),
            'timestamp': datetime.now().isoformat(),
            'role': 'friday',
            'text': reply,
            'pinned': False,
            'sources': sources,
        }
        CHAT_HISTORY.append(user_msg)
        CHAT_HISTORY.append(friday_msg)

        # Prune and save
        cutoff = (datetime.now() - timedelta(days=30)).isoformat()
        CHAT_HISTORY[:] = [m for m in CHAT_HISTORY if m.get('pinned') or m.get('timestamp', '') >= cutoff][-500:]
        _save_chat_history(CHAT_HISTORY)

        # Persistent conversation memory + emotional arc — index this exchange in
        # the background (skip when off-record). Best-effort; never blocks.
        if not settings.get('off_record'):
            try:
                threading.Thread(
                    target=_index_chat_turn,
                    args=(message, reply, _session_id, user_msg['id'], friday_msg['id']),
                    daemon=True,
                ).start()
            except Exception:
                pass

        return jsonify({"status": "ok", "user_msg": user_msg, "friday_msg": friday_msg, "sources": sources, "tool_trace": tool_trace})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@chat_bp.route('/api/chat/pin/<msg_id>', methods=['POST'])
def chat_pin(msg_id):
    """Toggle pin status on a chat message. Pinned messages are never pruned."""
    for msg in CHAT_HISTORY:
        if msg.get('id') == msg_id:
            msg['pinned'] = not msg.get('pinned', False)
            _save_chat_history(CHAT_HISTORY)
            return jsonify({"status": "ok", "id": msg_id, "pinned": msg['pinned']})
    return jsonify({"status": "error", "message": "Message not found"}), 404


@chat_bp.route('/api/chat/search', methods=['GET'])
def chat_search():
    """Search chat history by text query."""
    query = request.args.get('q', '').lower().strip()
    if not query:
        return jsonify({"status": "ok", "results": [], "count": 0})

    results = [m for m in CHAT_HISTORY if query in m.get('text', '').lower()]
    return jsonify({"status": "ok", "results": results[-50:], "count": len(results)})


@chat_bp.route('/api/chat/clear', methods=['POST'])
def chat_clear():
    """Reset the chat panel's conversation. Pinned messages survive unless
    `pinned=true` is sent in the body. Append-only context log is NOT touched."""
    keep_pinned = True
    try:
        data = request.get_json(silent=True) or {}
        if data.get('include_pinned'):
            keep_pinned = False
    except Exception:
        pass
    before = len(CHAT_HISTORY)
    if keep_pinned:
        CHAT_HISTORY[:] = [m for m in CHAT_HISTORY if m.get('pinned')]
    else:
        CHAT_HISTORY.clear()
    _save_chat_history(CHAT_HISTORY)
    return jsonify({"status": "ok", "removed": before - len(CHAT_HISTORY), "remaining": len(CHAT_HISTORY)})


# ═══════════════════════════════════════════════════════════════
#  PERSISTENT MEMORY & SOURCE PRODUCTION ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@chat_bp.route('/api/memory/search', methods=['POST'])
def memory_search():
    """Semantic search over persistent conversation memory (ChromaDB).

    Body: {query, n?, session_id?, roles?}
    Returns: {status, query, results: [{text, role, timestamp, date,
              session_id, topic_keywords, relevance}], available}
    """
    try:
        data = request.get_json(silent=True) or {}
        query = (data.get('query') or '').strip()
        if not query:
            return jsonify({"status": "error", "error": "query is required",
                            "results": []}), 400
        n = int(data.get('n', 5) or 5)
        session_id = data.get('session_id') or None
        roles = data.get('roles') or None
        mem = _get_conversation_memory()
        results = mem.search(query, n=n, session_id=session_id, roles=roles)
        return jsonify({
            "status": "ok",
            "query": query,
            "results": results,
            "count": len(results),
            "available": mem.available(),
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "error": str(e), "results": []}), 500


@chat_bp.route('/api/sources/dossier/<session_id>', methods=['GET'])
def sources_dossier(session_id):
    """Generate a source sheet for one conversation.

    Walks every Friday turn in the session, then asks Claude to extract each
    factual claim, its source, a confidence level, and a link — rendered as
    branded, exportable markdown. ``session_id`` may be a date (YYYY-MM-DD) or
    the literal 'current' for today's conversation.
    """
    try:
        if session_id in ('current', 'today', ''):
            session_id = _current_session_id()

        # Prefer the persistent memory store (it spans restarts); fall back to
        # the in-process CHAT_HISTORY when memory is unavailable.
        turns = []
        try:
            mem = _get_conversation_memory()
            if mem.available():
                turns = mem.get_session(session_id)
        except Exception:
            turns = []
        if not turns:
            # Fallback: today's CHAT_HISTORY (no session grouping there).
            for m in CHAT_HISTORY:
                if (m.get('timestamp', '')[:10] == session_id):
                    turns.append({
                        "role": 'friday' if m.get('role') == 'friday' else 'user',
                        "text": m.get('text', ''),
                        "timestamp": m.get('timestamp'),
                        "date": m.get('timestamp', '')[:10],
                    })

        friday_turns = [t for t in turns if t.get('role') == 'friday' and t.get('text')]
        if not friday_turns:
            return jsonify({
                "status": "empty",
                "session_id": session_id,
                "markdown": (
                    f"# 📋 Source Dossier — {session_id}\n\n"
                    "_No Friday responses found for this conversation yet._\n"
                ),
            })

        transcript = "\n\n".join(
            f"[{t.get('timestamp') or session_id}] {t.get('text')}"
            for t in friday_turns
        )[:24000]

        dossier_prompt = (
            "Produce a SOURCE DOSSIER for the conversation below. The dossier is "
            "a fact-check sheet: for every verifiable factual claim Friday made, "
            "extract one row.\n\n"
            "Output STRICTLY as branded markdown in this shape:\n\n"
            f"# 📋 Source Dossier — {session_id}\n\n"
            "> Generated by Agent Friday · Source Production System\n\n"
            "| # | Claim | Source | Confidence | Link |\n"
            "|---|-------|--------|------------|------|\n"
            "| 1 | <the claim, one sentence> | <wiki/news/memory/web + name> | "
            "High / Medium / Low | <url or — if none> |\n\n"
            "Then a short '## Notes' section flagging any claim that lacked a "
            "clear source or carried a low-trust warning.\n\n"
            "Rules: include ONLY claims actually present below; do not invent "
            "sources or links; if a claim cited an inline tag like "
            "[wiki:...]/[news:...]/[web:...], use that as the source. If Friday "
            "made no verifiable factual claims, say so plainly.\n\n"
            "=== CONVERSATION (Friday's turns) ===\n"
            f"{transcript}\n"
            "=== END ===\n"
        )

        # Vault-aware system prompt per the all-_call_claude-uses-vault rule.
        system_prompt = _get_friday_system_prompt(
            keywords='source dossier', workspace='chat')
        markdown = _generate_text(
            [{"role": "user", "content": dossier_prompt}],
            system=system_prompt,
            model=_load_settings().get('subagent_model') or 'claude-sonnet-4-6',
            workspace='news',
        )
        # Fact-check pass so low-trust news sources in the sheet get flagged too.
        markdown = _factcheck_news_citations(markdown)

        return jsonify({
            "status": "ok",
            "session_id": session_id,
            "claim_turns": len(friday_turns),
            "markdown": markdown,
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "error": str(e),
                        "session_id": session_id}), 500
