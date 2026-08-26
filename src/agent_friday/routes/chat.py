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
    _call_claude,
    _call_ollama,
    _call_openai,
    _compress_trajectory,
    _current_session_id,
    _factcheck_news_citations,
    validate_toolcall_integrity,
    _generate_text,
    _get_context_compressor,
    _get_context_pruner,
    _get_conversation_memory,
    _get_friday_system_prompt,
    _get_vault_control,
    _index_chat_turn,
    _predict_route_provider,
    _vault_cloud_fallback,
    _vault_local_only,
)  # noqa: E501
from agent_friday.services.response_provenance import (
    mark_unverified_citations as _mark_unverified_citations,
    warn_if_ungrounded_claim as _warn_if_ungrounded_claim,
)  # noqa: E501

chat_bp = Blueprint('chat', __name__)

# Chat-turn failures must survive a pythonw launch (no console): the "friday"
# logger writes to ~/.friday/friday.log, so log tracebacks there — stderr from
# traceback.print_exc() is simply lost in production.
import logging as _logging
_LOG = _logging.getLogger("friday.chat")



# ── Conversations (docs/design/conversations-and-concurrency.md §3.1) ───────
#
# Every turn belongs to a conversation. Until now there was ONE transcript: the
# global CHAT_HISTORY list, which "+ New Chat" deleted outright. Two chats open
# at once shared it, so concurrent turns interleaved into each other's context.
#
# The legacy list is still written, deliberately, for one release: /api/chat/
# history, the voice path and several panels still read it, and changing them
# all in the same commit would make a large change unreviewable. The
# conversation store is the source of truth for CONTEXT — which is the half
# that corrupts — and the legacy list is a mirror that will be dropped.



def _fit_tools(model_id, tools, prompt_cost=0):
    """As much of the tool registry as this seat can hold. Never raises.

    `prompt_cost` — estimated tokens of system prompt + transcript, so the
    budget covers the request as a whole, not the tool preamble in isolation.
    """
    try:
        from agent_friday.services.tool_budget import fit_tools_to_seat
        return fit_tools_to_seat(model_id, tools, prompt_cost=prompt_cost)
    except Exception:
        return list(tools or []), None

def _conv_id_from(data):
    """The conversation this request addresses; Main when unaddressed.

    Voice, channels and the scheduler predate conversations and send nothing;
    they must keep working, and their output needs a real destination.
    """
    from agent_friday.services import conversations as _conv
    return _conv.resolve(((data or {}).get('conversation_id') or '').strip() or None)


def _conv_context(cid, limit=100):
    """This conversation's replayable turns — never another conversation's."""
    from agent_friday.services import conversations as _conv
    out = []
    for m in _conv.messages(cid, limit):
        # System lines (seat changes, interruption notices) are transparency
        # surfaces, not conversation, and are never replayed into model context.
        if m.get('role') in ('system', 'system_report'):
            continue
        role = 'user' if m.get('role') == 'user' else 'assistant'
        text = m.get('text') or ''
        if text:
            out.append({"role": role, "content": text})
    return out


def _persist_turn(cid, user_msg, friday_msg, meta=None):
    """Write a completed exchange to its conversation (and the legacy mirror)."""
    from agent_friday.services import conversations as _conv
    try:
        _conv.append(cid, {"id": user_msg.get('id'), "role": "user",
                           "text": user_msg.get('text') or '',
                           "pinned": bool(user_msg.get('pinned')),
                           "meta": {"kind": "turn"}})
        _conv.append(cid, {"id": friday_msg.get('id'), "role": "friday",
                           "text": friday_msg.get('text') or '',
                           "pinned": bool(friday_msg.get('pinned')),
                           "meta": dict(meta or {}, kind="turn",
                                        sources=friday_msg.get('sources') or [])})
        _conv.prune(cid)
    except Exception as _e:
        print(f"  [conversations] could not persist turn to {cid}: {_e}")
    # Legacy mirror — see the note above.
    # Stamp the mirror row with its thread so /api/chat/history can be filtered
    # per conversation instead of returning every thread merged into one window.
    for _m in (user_msg, friday_msg):
        try:
            _m.setdefault('conversation_id', cid)
        except Exception:
            pass
    CHAT_HISTORY.append(user_msg)
    CHAT_HISTORY.append(friday_msg)


@chat_bp.route('/api/chat', methods=['POST'])
def chat():
    """Text chat — powered by Anthropic Claude.

    Vision (screenshot description) still routes through Gemini Flash, since vision
    is a designer/perception task. Reasoning stays on Claude.
    """
    try:
        # Fresh receipt book for this turn. Everything _execute_tool actually
        # runs gets recorded against it, so the reply can be checked against
        # what happened rather than taken on trust.
        from agent_friday.services import tool_receipts as _receipts
        _receipts.begin_turn()
        data = request.get_json(silent=True) or {}
        # Resolve the addressed conversation FIRST: the context build below reads
        # from it, and everything downstream persists into it.
        _conversation_id = _conv_id_from(data)
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
        # An empty message with no image is unanswerable — refuse it cleanly
        # here instead of crashing downstream (the orb-label slice indexed
        # splitlines()[0] on '' and every such request died as
        # "[Friday offline] list index out of range"). An empty message WITH
        # an image stays valid: Camera Mode sends bare frames.
        if not (message or '').strip() and not (data.get('image') or data.get('screenshot')):
            if not request.get_data(cache=True, as_text=False):
                _LOG.warning("chat: request body empty or unparseable "
                             "(content-type=%s)", request.content_type)
            return jsonify({
                "response": "I didn't receive a message — the request body "
                            "was empty or couldn't be read.",
                "error": "empty_message",
                "sources": [], "tool_trace": [], "actions": [],
            }), 400
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

        # ── B2: seat-change visibility. ANY change to the seat-relevant
        # settings (UI save, CLI raw write, direct settings.json edit) is
        # detected here on the very next turn — persisted as a system line
        # and returned so the client renders it before this turn. ──
        try:
            from agent_friday.services.seat_transparency import observe_seats
            _seat_events = observe_seats(settings_early)
        except Exception as _ste:
            print(f"  [SEAT-TRANSPARENCY] skipped: {_ste}")
            _seat_events = []

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
            _persist_turn(_conv_id_from(data), _u, _f)
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
            _persist_turn(_conv_id_from(data), _u, _f)
            try:
                _save_chat_history(CHAT_HISTORY)
            except Exception:
                pass
            return jsonify({
                "response": _open_reply, "user_msg": _u, "friday_msg": _f,
                "sources": [], "tool_trace": [{"tool": "open_path", "result": _open_reply}],
            })

        # ── Vision capture. THE ROUTING MODE IS CHECKED BEFORE THE SEND. ──
        #
        # Accept either `screenshot` (legacy) or `image` (Camera Mode frames).
        #
        # This block used to run unconditionally, at this line, while
        # `model_routing` was not read until ~120 lines below. So a user on
        # **Local only** — help text: "Never leaves the machine. If a local
        # model cannot answer, I say so rather than using the cloud" — had a
        # screenshot of their DESKTOP sent to Google on every image. Not a
        # picture they chose: whatever was on screen, including the vault, a
        # terminal, or someone else's message. A control that promises the
        # opposite of what it does is worse than an undocumented egress,
        # because the user made a decision on the strength of it.
        #
        # The old comment reasoned: image bytes cannot be text-classified by
        # the egress gate, "so there is nothing to gate here". True premise,
        # false conclusion. The BYTES are unclassifiable; the DECISION TO SEND
        # THEM is entirely gateable, and that is what was missing.
        #
        # Three things now hold:
        #   1. local_only never reaches Gemini. It describes the image on the
        #      resident seat or says plainly that it could not.
        #   2. Every send — local or cloud — is recorded in the egress ledger
        #      via record_binary_egress, so an image appears in the same file
        #      that already logs a four-word prompt to the same provider.
        #   3. A cloud send is disclosed IN THE TURN, not only in a document.
        #      Same rule as the tool-disclosure line: rare, and where he is
        #      looking.
        screenshot_b64 = data.get('image') or data.get('screenshot') or None
        _vision_events = []
        if screenshot_b64 and (include_vision or data.get('image') is not None):
            _routing_mode = str(((settings_early.get('model_routing') or {})
                                 .get('mode') or 'smart')).lower()
            _mime = 'image/jpeg' if data.get('image') else 'image/png'
            _is_screen = data.get('screenshot') is not None
            _what = 'a screenshot of your desktop' if _is_screen else 'the camera frame'
            try:
                _img_len = len(base64.b64decode(screenshot_b64))
            except Exception:
                _img_len = 0
            _prefer_local = _routing_mode in ('local_only', 'local_preferred')

            def _record(provider, action, reason):
                try:
                    from agent_friday.services.egress_gate import record_binary_egress
                    record_binary_egress(provider, 'vision_image',
                                         action=action, reason=reason,
                                         byte_len=_img_len)
                except Exception as _ee:
                    print(f"  [VISION] egress record failed: {_ee}")

            # 1 ── local first when the mode asks for local.
            if _prefer_local:
                try:
                    from agent_friday.services import local_vision as _lv
                    _res = _lv.describe(screenshot_b64, mime=_mime,
                                        settings=settings_early)
                except Exception as _lve:
                    _res = {"ok": False, "text": None,
                            "reason": f"local vision raised {_lve}"}
                if _res.get("ok"):
                    vision_description = _res["text"]
                    _record('local:' + str(_res.get('model')), 'allow',
                            'described on-device; nothing left the machine')
                elif _routing_mode == 'local_only':
                    # The promise is kept by REFUSING, and by saying so. This
                    # is the "I say so rather than using the cloud" half of the
                    # mode's own help text, which had no implementation.
                    vision_description = (
                        "[I could not look at that image without leaving the "
                        "machine, and you have chosen Local only, so I did "
                        "not send it. Reason: %s]" % _res.get("reason"))
                    _record('gemini', 'block',
                            'local_only: image withheld from the cloud (%s)'
                            % _res.get("reason"))
                    _vision_events.append({
                        "kind": "vision_withheld",
                        "text": "🔒 I did not describe that image. Local only "
                                "is on and the local seat could not do it — %s."
                                % _res.get("reason"),
                        "ts": _time.time(),
                    })
                    screenshot_b64 = None      # nothing may send it later

            # 2 ── cloud, only when the mode permits it, and never silently.
            if screenshot_b64 and vision_description is None:
                try:
                    from google import genai
                    from google.genai import types
                    gclient = genai.Client(api_key=core.GEMINI_API_KEY)  # pragma: allowlist secret
                    img_bytes = base64.b64decode(screenshot_b64)
                    vision_resp = gclient.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=[
                            "Briefly describe what is visible on this screen. Focus on text, UI elements, and data shown. Be concise (2-3 sentences).",
                            types.Part.from_bytes(data=img_bytes, mime_type=_mime),
                        ],
                    )
                    vision_description = vision_resp.text
                    _record('gemini', 'allow',
                            'routing mode %s permits cloud vision' % _routing_mode)
                    _vision_events.append({
                        "kind": "vision_cloud",
                        "text": "👁 %s went to Google (Gemini) to be described. "
                                "Switch to Local only in Settings → "
                                "Intelligence to keep images on this machine."
                                % _what.capitalize(),
                        "ts": _time.time(),
                    })
                except Exception as ve:
                    vision_description = f"[Vision unavailable: {ve}]"
                    _record('gemini', 'error', f'cloud vision failed: {ve}')

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
                _persist_turn(_conv_id_from(data), _u, _f)
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
        # THIS conversation's history. Reading the global list here is what
        # let two open chats contaminate each other's context.
        raw_history = _conv_context(_conversation_id, 100)
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
                    _selected_model = settings.get('orchestrator_model') or 'claude-opus-5'
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
        # Badge truth (2026-08-14): attribution is collected per-turn from
        # the dispatch layer itself — reset before any primitive runs.
        try:
            from agent_friday.services import attribution as _attr
            _attr.reset()
        except Exception:
            _attr = None
        _routing_cfg = settings.get('model_routing') or {}
        _orb_label = ((message or '').strip().splitlines() or ['Chat'])[0][:24] or 'Chat'
        try:
            from agent_friday.routing.model_router import get_router
            _router = get_router(_routing_cfg)
            # The conversation's own binding, if it has one. A null seat
            # means "follow the global default", resolved per turn.
            _conv_seat = None
            try:
                from agent_friday.services import conversations as _convs
                _conv_seat = (_convs.load(_conversation_id) or {}).get("seat")
            except Exception:
                pass
            # A conversation bound to a model that is GONE.
            #
            # A binding to a missing model 404s and falls through to the cloud.
            # §3.8: a stated choice to rebind, never a silent substitution.
            # Answering as Claude when he asked for a local model is the defect
            # this whole day has been about.
            #
            # "Gone" is asked of every place Friday can serve from, not just
            # the catalogue. The catalogue omitted `gemma4:e2b` while that seat
            # was live on 127.0.0.1:8092, so a chat bound to one of her OWN
            # models was told it was no longer installed — refusing to answer
            # from a model that was running two ports away. A wrong "it's
            # gone" is as damaging as a silent substitution: both end with him
            # not getting the model he chose.
            if isinstance(_conv_seat, dict) and (_conv_seat.get('model') or ''):
                _want = _conv_seat['model']
                _known = False
                try:
                    from agent_friday.services.model_catalog import build_catalog
                    _known = any(m.get('id') == _want
                                 for m in (build_catalog().get('models') or []))
                    if not _known:
                        from agent_friday.services import local_seats
                        _known = any(n == _want for n, _ in local_seats.installed())
                    if not _known:
                        from agent_friday.services.local_call import seat_endpoint
                        _known = bool(seat_endpoint(_want))
                except Exception:
                    _known = True          # cannot check → do not block the turn
                if not _known:
                    _gone = (
                        f"This chat is bound to **{_want}**, and that model is no "
                        f"longer installed.\n\n"
                        f"I have not answered from a different model, because you "
                        f"asked for that one. Pick another for this chat with the "
                        f"model button in the chat header, or say "
                        f"\"switch to <model>\" and I will rebind it."
                    )
                    user_msg = {
                        'id': str(uuid.uuid4()), 'timestamp': datetime.now().isoformat(),
                        'role': 'user', 'text': message, 'pinned': False, 'sources': [],
                    }
                    friday_msg = {
                        'id': str(uuid.uuid4()), 'timestamp': datetime.now().isoformat(),
                        'role': 'friday', 'text': _gone, 'pinned': False, 'sources': [],
                    }
                    _persist_turn(_conversation_id, user_msg, friday_msg,
                                  meta={'kind': 'seat_missing', 'model': _want})
                    return jsonify({
                        "response": _gone, "user_msg": user_msg,
                        "friday_msg": friday_msg, "sources": [], "tool_trace": [],
                        "seat_missing": {"model": _want,
                                         "conversation_id": _conversation_id},
                    })

            _route_info = _router.route(messages, task_context={
                "has_tools": True,
                "workspace": workspace,
                "conversation_id": _conversation_id,
                "conversation_seat": _conv_seat,
                "cloud_model": settings.get('orchestrator_model') or 'claude-opus-5',
            })
        except Exception as _re:
            print(f"  [ROUTER] routing failed, defaulting to cloud: {_re}")
            _route_info = {
                "provider": "cloud",
                "model": settings.get('orchestrator_model') or 'claude-opus-5',
                "is_local": False, "vault_allowed": False, "scrub_pii": True,
                "vault_access": False, "refuse": False, "warning": None,
            }

        # ── Per-turn escape hatch for "Friday is about to go quiet" ──
        #
        # Friday now warns before a local pause (services/pause_forecast.py)
        # and offers the cloud instead. That offer has to be honourable for one
        # turn without changing any setting — otherwise the warning is a
        # notification rather than a choice.
        #
        # It CANNOT override a vault-forced local route. `_route_vault` exists
        # precisely so TIER_2/3 material never leaves the machine, and a UI
        # button must not be able to talk it round. The forecast already omits
        # the cloud option for vault work; this refuses it again here, because
        # a rule enforced only in the interface is not enforced.
        _route_mode = str((data or {}).get('route_mode') or '').strip().lower()
        if _route_mode == 'cloud' and not _route_info.get('vault_access'):
            _route_info = dict(
                _route_info, provider='cloud', is_local=False,
                vault_allowed=False, vault_access=False, scrub_pii=True,
                model=settings.get('orchestrator_model') or 'claude-opus-5',
                route_override='user chose the cloud to avoid a local pause')
        elif _route_mode == 'cloud':
            print("  [ROUTER] refusing route_mode=cloud: this turn touches "
                  "vault-tier material and stays local")

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
            _persist_turn(_conv_id_from(data), user_msg, friday_msg)
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
            # v5 personalization: fold in the LOCAL user model + learned heuristics
            # (the same blocks _get_friday_system_prompt injects). /api/chat builds
            # its prompt via _build_context_prompt directly, so without this the
            # flagship interactive path FED observe_message() but never USED what
            # it learned. Best-effort, TIER_1 text, and still scrubbed/gated below
            # before any cloud send. Guarded so it can never break a chat turn.
            try:
                from agent_friday.services.user_model import render_user_model_prompt
                _um_block = render_user_model_prompt()
                if _um_block:
                    sp = sp + "\n\n== USER MODEL ==\n" + _um_block + "\n"
            except Exception:
                pass
            try:
                from agent_friday.services.learning_loop import render_heuristics_prompt
                _heur_block = render_heuristics_prompt(task_type=workspace or None)
                if _heur_block:
                    sp = sp + "\n\n== LEARNED HEURISTICS (advisory) ==\n" + _heur_block + "\n"
            except Exception:
                pass
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
        # healthy, run the turn locally instead of crashing.
        #
        # CLOUD ONLY MEANS CLOUD ONLY.
        #
        # This branch used to justify itself with "only triggers when the
        # alternative is a guaranteed failure, so it can't regress a working
        # setup". That premise is true on the machine it was written on and
        # false on a new one. The author always has a key and only ever loses
        # it for a moment. Someone who has just installed Friday has never had
        # one — so the net is not a net, it is the route: every turn, forever,
        # with no setting able to switch it off, because this test never read
        # `mode`.
        #
        # Measured on Janet's laptop 2026-08-26, the first install of Friday by
        # someone who did not write her. She found the routing mode, set it to
        # cloud only, and was still answered on-device. The router had already
        # decided correctly one line above — both lines, same turn, in order:
        #
        #   [ROUTER] chose cloud/claude-sonnet-5 | Routing mode is cloud_only
        #   [ROUTER] No Anthropic key; Ollama healthy — routing chat to local
        #            model gemma3:4b
        #
        # The mirror image was fixed on 2026-08-18: a local seat that fails
        # refuses the cloud rather than quietly crossing the line, and says so
        # ("LOCAL ONLY MEANS LOCAL ONLY", below). Only the direction the author
        # travels had been treated. This is the other direction, and it is the
        # one a new user meets first.
        #
        # Refusing costs her an answer she did not want: a turn she asked to
        # keep off this machine, answered on it. Saying why — and where the key
        # goes — is worth more than a reply from a model she declined.
        if (not _routed_local) and _provider == 'cloud' and get_anthropic_client() is None:
            _mode = str((_routing_cfg or {}).get('mode') or 'smart').lower()
            if _mode == 'cloud_only':
                print("  [ROUTER] No Anthropic key and mode is cloud_only — "
                      "refusing the local fallback and asking for a key")
                _no_key_msg = (
                    "I'm set to **cloud only**, and there's no cloud AI key on "
                    "this computer yet — so there's nothing for me to think "
                    "with. I haven't sent this anywhere.\n\n"
                    "Add a key in **Settings → Providers**. Anthropic's Claude "
                    "is what I use by default; the panel there has a button "
                    "through to the signup page and takes the key straight "
                    "from you — nothing to edit by hand.\n\n"
                    "If you would rather I ran on this laptop instead, switch "
                    "to **Smart** in Settings → Intelligence and I will use a "
                    "local model whenever there is no key."
                )
                user_msg = {
                    'id': str(uuid.uuid4()), 'timestamp': datetime.now().isoformat(),
                    'role': 'user', 'text': message, 'pinned': False, 'sources': [],
                }
                friday_msg = {
                    'id': str(uuid.uuid4()), 'timestamp': datetime.now().isoformat(),
                    'role': 'friday', 'text': _no_key_msg, 'pinned': False,
                    'sources': [],
                }
                _persist_turn(_conv_id_from(data), user_msg, friday_msg)
                _save_chat_history(CHAT_HISTORY)
                return jsonify({
                    "response": _no_key_msg, "user_msg": user_msg,
                    "friday_msg": friday_msg, "sources": [], "tool_trace": [],
                    "model": None, "seat": "cloud",
                    "cloud_only_no_key": True,
                })
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
        _fell_back_from_local = None
        if _routed_local:
            # As much of the tool registry as this seat can physically hold.
            # 112 tools cost ~46k tokens; his seat's window is 32,768, so
            # every local turn 400'd with "exceeds the available context
            # size" and the router fell back to Anthropic. The model, the
            # seat, the picker and the mode were all fine — the request could
            # not be built. See services/tool_budget.py.
            #
            # The budget covers the WHOLE request: the 2026-08-19 400 was
            # tools "within budget" landing on top of an ordinary prompt.
            _prompt_cost = (len(system_prompt or '') + sum(
                len(m.get('content')) for m in messages
                if isinstance(m.get('content'), str))) // 4
            _local_tools, _tool_note = _fit_tools(
                _route_info.get('model'), CLAUDE_TOOLS,
                prompt_cost=_prompt_cost)
            if _tool_note:
                system_prompt = (system_prompt or '') + "\n\n[SEAT] " + _tool_note
            try:
                reply, tool_trace = _call_ollama(
                    messages, system=system_prompt,
                    model=_route_info['model'],
                    temperature=settings.get('temperature'),
                    orb_label=f"🏠 {_orb_label}",
                    orb_icon='🏠',
                    # Local models drive the full agent loop too: same
                    # unified tool registry, vault gate and governance as the
                    # cloud path -- but only as much of the registry as fits.
                    tools=_local_tools, pii_lookup=pii_lookup, session_ctx=_sess_ctx,
                )
            except Exception as _ole:
                # A vault request must NEVER silently fall back to cloud with raw
                # vault data — fail loudly instead.
                if _vault_access:
                    print(f"  [ROUTER] local vault inference failed; refusing cloud fallback: {_ole}")
                    raise
                # LOCAL ONLY MEANS LOCAL ONLY.
                #
                # Stephen, 2026-08-18: "It took forever to reply then kicked
                # back to Sonnet 4.6 again, which I do not want." The mode
                # setting is exactly the control that should prevent this, and
                # this fallback never consulted it — so "local only" still
                # reached Anthropic whenever a local seat had a bad minute.
                # Refusing is the honest outcome: he asked for on-device work,
                # and a slow answer from the model he chose beats a fast one
                # from a model he rejected.
                _mode = str((_routing_cfg or {}).get('mode') or 'smart').lower()
                if _mode == 'local_only':
                    print(f"  [ROUTER] local inference failed and mode is "
                          f"local_only — refusing the cloud: {_ole}")
                    _local_only_msg = (
                        "I could not get an answer out of "
                        + str(_route_info.get('model') or 'the local model')
                        + " just now (" + str(_ole)[:160] + ").\n\n"
                        "You are in **local only** mode, so I did not send this "
                        "to a cloud model. Options: wait and try again once the "
                        "GPU is free, pick a smaller local model, or switch the "
                        "mode to Local preferred in Settings -> Intelligence if "
                        "you want me to fall back when local is busy."
                    )
                    user_msg = {
                        'id': str(uuid.uuid4()), 'timestamp': datetime.now().isoformat(),
                        'role': 'user', 'text': message, 'pinned': False, 'sources': [],
                    }
                    friday_msg = {
                        'id': str(uuid.uuid4()), 'timestamp': datetime.now().isoformat(),
                        'role': 'friday', 'text': _local_only_msg, 'pinned': False,
                        'sources': [],
                    }
                    _persist_turn(_conv_id_from(data), user_msg, friday_msg)
                    _save_chat_history(CHAT_HISTORY)
                    return jsonify({
                        "response": _local_only_msg, "user_msg": user_msg,
                        "friday_msg": friday_msg, "sources": [], "tool_trace": [],
                        "model": _route_info.get('model'), "seat": "local",
                        "local_only_refused": True,
                    })
                print(f"  [ROUTER] local inference failed, falling back to cloud: {_ole}")
                # He chose a local seat. Answering from the cloud instead is a
                # decision he did not make, about data he chose to keep on the
                # machine, and it must not happen quietly — a silent fallback
                # is indistinguishable from "changing the model does nothing",
                # which is precisely how this was reported.
                #
                # friday.log too, with the full error: the tray DEVNULLs
                # stdout, so the print above vanishes — the 2026-08-19 400's
                # body (which named the exact token count) survived in no log
                # anywhere, and the diagnosis had to be rebuilt from replay.
                import logging as _logging
                _logging.getLogger("friday.local_fallback").warning(
                    "local seat %s failed, falling back to cloud: %s",
                    _route_info.get('model'), str(_ole)[:800])
                _fell_back_from_local = {
                    "model": _route_info.get('model'),
                    "why": str(_ole)[:200],
                }
                _routed_local = False
                _provider = 'cloud'
                # Rebuild the prompt for cloud (gated) and scrub before sending.
                system_prompt, sources, pii_lookup = _prep_for('cloud')

        if not _routed_local:
            if _provider == 'openai':
                # OpenAI-compatible cloud path with a full agentic tool loop.
                # The route decision carries the RESOLVED registry provider
                # (openrouter / groq / huggingface / a custom endpoint ...), so
                # each request hits that provider's own base_url + credentials
                # (multi-provider dispatch, GAP-3 fix). provider_name=None
                # keeps the legacy single-slot settings behavior.
                reply, tool_trace = _call_openai(
                    messages, system=system_prompt, model=_route_info.get('model'),
                    temperature=settings.get('temperature'),
                    orb_label=f"☁️ {_orb_label}", orb_icon='☁️',
                    tools=CLAUDE_TOOLS, pii_lookup=pii_lookup, session_ctx=_sess_ctx,
                    provider=_route_info.get('provider_name'),
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
                            settings.get('orchestrator_model') or 'claude-opus-5',
                            prompt_tokens=_est_tokens, completion_tokens=len(reply) // 4,
                        )
                    except Exception:
                        pass

        # ── FR-2: tool-call integrity — scan for fabricated bracket-syntax
        # pseudo-tool-calls (e.g. [query_calendar], [search_email(...)])
        # BEFORE the reply is stored, rendered, or spoken. Corrective-retries
        # through the SAME dispatch path that produced the leak, then falls
        # back to an honest failure — never renders/speaks a fabrication, and
        # never executes the leaked bracket text as a real tool call. ──
        def _redispatch_for_integrity(corrective_note):
            _retry_messages = messages + [{"role": "user", "content": corrective_note}]
            if _routed_local:
                # The FITTED registry, not CLAUDE_TOOLS: the primary dispatch
                # trimmed the tools to what this seat can hold, and a retry
                # that re-sends the full registry is a guaranteed 400 on any
                # seat whose window the connectors do not fit.
                return _call_ollama(
                    _retry_messages, system=system_prompt, model=_route_info['model'],
                    temperature=settings.get('temperature'),
                    orb_label=f"🏠 {_orb_label}", orb_icon='🏠',
                    tools=_local_tools, pii_lookup=pii_lookup, session_ctx=_sess_ctx,
                )
            if _provider == 'openai':
                return _call_openai(
                    _retry_messages, system=system_prompt, model=_route_info.get('model'),
                    temperature=settings.get('temperature'),
                    orb_label=f"☁️ {_orb_label}", orb_icon='☁️',
                    tools=CLAUDE_TOOLS, pii_lookup=pii_lookup, session_ctx=_sess_ctx,
                    provider=_route_info.get('provider_name'),
                )
            _cloud_model = _route_info.get('model')
            _claude_model = _cloud_model if str(_cloud_model or '').startswith('claude') else None
            return _call_claude_agent(
                _retry_messages, system=system_prompt, model=_claude_model,
                temperature=settings.get('temperature'),
                pii_lookup=pii_lookup, session_ctx=_sess_ctx,
                orb_label=_orb_label, orb_category='default', orb_icon='💬',
            )

        # ── B7: honest-failure ladder — after ONE corrective retry, one
        # last attempt with tools stripped so the model can answer plainly
        # (a single-shot generation, no agentic tool loop — fast), before
        # the honest-failure message. ──
        def _redispatch_no_tools(corrective_note):
            _retry_messages = messages + [{"role": "user", "content": corrective_note}]
            if _routed_local:
                return _call_ollama(
                    _retry_messages, system=system_prompt, model=_route_info['model'],
                    temperature=settings.get('temperature'),
                    orb_label=f"🏠 {_orb_label}", orb_icon='🏠',
                    tools=None, pii_lookup=pii_lookup, session_ctx=_sess_ctx,
                )
            if _provider == 'openai':
                return _call_openai(
                    _retry_messages, system=system_prompt, model=_route_info.get('model'),
                    temperature=settings.get('temperature'),
                    orb_label=f"☁️ {_orb_label}", orb_icon='☁️',
                    tools=None, pii_lookup=pii_lookup, session_ctx=_sess_ctx,
                    provider=_route_info.get('provider_name'),
                )
            _cloud_model = _route_info.get('model')
            _claude_model = _cloud_model if str(_cloud_model or '').startswith('claude') else None
            return _call_claude(
                _retry_messages, system=system_prompt, model=_claude_model,
                temperature=settings.get('temperature'),
            ), []

        reply, tool_trace, _integrity_meta = validate_toolcall_integrity(
            reply, tool_trace, [t['name'] for t in CLAUDE_TOOLS],
            redispatch=_redispatch_for_integrity,
            redispatch_no_tools=_redispatch_no_tools,
        )
        if _integrity_meta.get('blocked'):
            _resolved = not (_integrity_meta['final_leaks']
                             or _integrity_meta.get('final_claims'))
            print(f"  [INTEGRITY] fabrication caught "
                  f"(retries={_integrity_meta['retries']}, "
                  f"tools_stripped={_integrity_meta.get('tools_stripped_retry')}, "
                  f"resolved={'yes' if _resolved else 'no'})")

        # ── Source Production Mode: were there any citations AT ALL? ──
        #
        # The provenance pass below checks whether each citation is BACKED. It
        # never asked whether the reply produced any, so with the mode on a
        # confident unsourced essay passed silently — the toggle promising
        # something the system did not deliver.
        #
        # Enforcement only when the heuristic is confident; see
        # services/citation_enforcement for exactly what it cannot tell. On a
        # confident miss the model gets ONE corrective retry through the same
        # redispatch the tool-integrity ladder uses, and if the retry is still
        # unsourced the reply ships VISIBLY MARKED rather than quietly.
        # MEASUREMENT ONLY, DELIBERATELY. Enforcement is written and NOT wired,
        # because the heuristic that would trigger it cannot do the job. See
        # services/citation_enforcement and the note below; this is a decision
        # pending, not an oversight.
        #
        # Measured live 2026-08-24 on "what changed in EU AI regulation during
        # 2024?" with the mode on: five sentences, zero citations, and the
        # heuristic scored one claim-shaped sentence. The four it missed are
        # the ones that matter --
        #   "It shifted the regulatory focus toward a risk-based approach..."
        #   "It also introduced transparency requirements for GPAI models..."
        #   "Key shifts included the formalization of rules for systemic risk..."
        #   "the legislation introduced strict protections for fundamental
        #    rights..."
        # -- every one a checkable claim about the world, and not one carrying
        # a number, an attribution verb or a proper noun the pattern can see.
        # They are factual because of what they MEAN.
        #
        # Adding verbs of enactment (introduced / established / required) would
        # catch them and would equally catch "the 12b is the better seat",
        # which is a judgement. Fact and opinion are not separable here on
        # surface features, so the honest options are a model call or nothing —
        # and a model call is a latency decision, not a tweak.
        #
        # So the mode still does not enforce, and this says so out loud rather
        # than shipping a trigger that stays silent on the flagship case, which
        # would leave the same hole while looking fixed. What it does do is
        # COUNT, which is real: `citation_check` rides in the chat response, so
        # the gap is measurable per turn instead of invisible.
        #
        # The print below is best-effort only — under the tray's pythonw launch
        # these stdout lines were NOT reaching ~/.friday/server_stderr.log when
        # checked, so the response field is the signal to rely on, not the log.
        _cite_meta = None
        if cite_sources:
            from agent_friday.services import citation_enforcement as _ce
            _cite_meta = _ce.assess(reply)
            _cite_meta["enforced"] = False
            # ROUTING (item 4).
            #
            # A vault turn is checked ON THIS MACHINE or not at all. Sending a
            # reply built from vault material to a cloud model to ask whether
            # it makes claims would leak exactly the content the vault exists
            # to keep here, and it would do it inside the feature whose whole
            # purpose is trustworthiness.
            #
            # `_vault_local_only()` is the standing "vault content never goes
            # to the cloud" setting; `_routed_local` says this turn was served
            # on-device. Either makes the check local-only. That is broader
            # than "this turn definitely read the vault", and deliberately so:
            # the safe direction is to over-restrict a one-word check.
            _cite_local_only = bool(_vault_local_only()) or bool(_routed_local)
            if _cite_meta["citations"]:
                # The reply already cited. Nothing to judge, nothing to spend.
                # This is also item 4's other half: a cloud-served turn was
                # told to cite on the first pass and did, so no second call
                # happens at all.
                _cite_meta["judge"] = {"decided": False, "claims": False,
                                       "reason": "reply already cites; no judge needed",
                                       "seconds": 0.0, "via": None}
                _needs = False
            else:
                _j = _ce.judge_claims(reply, local_only=_cite_local_only,
                                      settings=settings)
                _cite_meta["judge"] = _j
                _cite_meta["judge_local_only"] = _cite_local_only
                # Fall back to the regex ONLY when no model could answer. A
                # failed judge must never read as "clean" — that is the silent
                # pass this whole feature exists to remove.
                _needs = _j["claims"] if _j["decided"] else _cite_meta["confident"]
                if not _j["decided"]:
                    _cite_meta["fallback"] = "regex"

            if _needs:
                print("  [CITE] unsourced; retrying once (judge=%s)"
                      % (_cite_meta["judge"].get("via") or "regex"))
                try:
                    _r2, _t2 = _redispatch_for_integrity(_ce.RETRY_INSTRUCTION)
                    _a2 = _ce.assess(_r2 or "")
                    _cite_meta["retried"] = True
                    _cite_meta["after_retry"] = _a2
                    if _r2 and _a2["citations"]:
                        reply = _r2
                        tool_trace = (tool_trace or []) + (_t2 or [])
                        _cite_meta["resolved"] = True
                        _cite_meta["enforced"] = True
                    else:
                        # Keep the better answer and say plainly it is
                        # unchecked. Marking beats suppressing: the content may
                        # be right, and the user is owed the distinction rather
                        # than a blank.
                        if _r2:
                            reply = _r2
                            tool_trace = (tool_trace or []) + (_t2 or [])
                        reply = (reply or "") + _ce.UNSOURCED_NOTICE
                        _cite_meta["resolved"] = False
                        _cite_meta["enforced"] = True
                except Exception as _cee:
                    print(f"  [CITE] retry failed, marking unsourced: {_cee}")
                    reply = (reply or "") + _ce.UNSOURCED_NOTICE
                    _cite_meta["resolved"] = False
                    _cite_meta["enforced"] = True

        # ── FR-3: provenance — only executed-tool-result URLs render clickable.
        # A [web:URL] citation not backed by anything this turn's tools
        # actually touched becomes [unverified-web:URL], which the client
        # renders inert instead of a real link. Also warn-logs a turn that
        # cites a source with zero tools executed at all. ──
        reply, _unverified_urls = _mark_unverified_citations(reply, tool_trace)
        if _unverified_urls:
            print(f"  [PROVENANCE] {len(_unverified_urls)} uncorroborated "
                  f"URL citation(s) rendered inert: {_unverified_urls}")
        _warn_if_ungrounded_claim(reply, tool_trace)

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
        # ── B1: model attribution on every assistant message, persisted so
        # it survives reloads. seat is the class ("local"/"cloud"/"openai"),
        # model is the exact id that generated this reply. ──
        # ── Badge truth (2026-08-14): attribute the ACTUAL responding model,
        # recorded by the primitive that generated the final text — never the
        # router's intent. The morning's live failure: every reply badged
        # 'qwen3.6-35b-a3b-iq4nl' while the brain never bound its port and
        # gemma4:e4b (seat substitution) or Claude (ladder) actually answered.
        # Routing intent remains only as a last-resort fallback when no
        # primitive recorded (it always records on success). ──
        _gen = None
        _fallback_chain = []
        try:
            if _attr is not None:
                _gen = _attr.last_generation()
                _fallback_chain = _attr.fallback_chain()
        except Exception:
            pass
        if _gen and _gen.get('model'):
            _seat_model = _gen['model']
            _seat_class = _gen.get('seat') or (
                'local' if _routed_local else 'cloud')
        else:
            _seat_class = 'local' if _routed_local else (
                'openai' if _provider == 'openai' else 'cloud')
            _seat_model = (_route_info.get('model')
                           or settings.get('orchestrator_model') or '')
        friday_msg = {
            'id': str(uuid.uuid4()),
            'timestamp': datetime.now().isoformat(),
            'role': 'friday',
            'text': reply,
            'pinned': False,
            'sources': sources,
            'model': _seat_model,
            'seat': _seat_class,
        }
        if _fallback_chain:
            friday_msg['fallback_chain'] = _fallback_chain
        _persist_turn(_conversation_id, user_msg, friday_msg,
                      meta={'model': _seat_model, 'seat': _seat_class})

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

        # No receipt, no claim. If the reply names a tool that never ran this
        # turn, say so in the reply itself rather than letting a fabricated
        # tool result stand — the failure that made this necessary was a seat
        # reporting "SUCCESS: Balance retrieved" from a call it never made.
        _unbacked = []
        try:
            _unbacked = _receipts.unbacked_claims(reply)
            if _unbacked:
                reply = (reply or "") + _receipts.correction_note(_unbacked)
                print("  [receipts] UNBACKED CLAIM in reply: %s"
                      % ", ".join(c["tool"] for c in _unbacked))
        except Exception:
            pass

        return jsonify({
            "response": reply,
            "tools_ran": _receipts.summary(),
            "unbacked_claims": _unbacked,
            "user_msg": user_msg,
            "friday_msg": friday_msg,
            "sources": sources,
            "tool_trace": tool_trace,
            "actions": actions,
            "cite_sources": cite_sources,
            # Counts, not a verdict — the heuristic's own limits are in
            # services/citation_enforcement. Present only when the mode is on.
            "citation_check": _cite_meta,
            "session_id": session_id,
            "model": _seat_model,
            "seat": _seat_class,
            "seat_events": _seat_events,
            # An image reaching a cloud provider, or being withheld because the
            # mode forbids it, is disclosed in the turn. Empty on every turn
            # without an image, which is nearly all of them — a disclosure that
            # fires constantly is wallpaper (KNOWN_ISSUES.md §1).
            "vision_events": _vision_events,
            "fallback_chain": _fallback_chain,
            # Present only when his chosen local seat could not answer and the
            # cloud did instead. The client renders it as a system line so the
            # substitution is visible in the transcript, not just in a log.
            "local_fallback": _fell_back_from_local,
        })
    except Exception as e:
        traceback.print_exc()  # console launches; a no-op loss under pythonw
        _LOG.exception("chat turn failed")
        return jsonify({"response": f"[Friday offline] {str(e)}"})


# ═══════════════════════════════════════════════════════════════
#  PERSISTENT CHAT HISTORY ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@chat_bp.route('/api/chat/history', methods=['GET'])
def chat_history():
    """Return chat history (last 30 days, pinned messages included)."""
    # B2: a seat flip that happened while the tab was closed becomes a
    # persisted system line the moment history is rehydrated.
    try:
        from agent_friday.services.seat_transparency import observe_seats
        observe_seats(_load_settings())
    except Exception:
        pass
    messages = _load_chat_history()
    # Per-thread history. Without a conversation_id the response is unchanged
    # (every thread, as before) so existing callers keep working. With one, the
    # caller gets only that thread plus untagged legacy rows written before
    # conversations existed -- which is what stops a voice session and a separate
    # text thread from rendering interleaved in the same chat window.
    #
    # Untagged rows belong to MAIN, not to everyone. Every one of the ~500 rows
    # this file already held predates conversation stamping, so letting `None`
    # match any _cid made a brand-new thread answer with the entire archive --
    # the caller could not tell a fresh chat from the old one. Main inherits the
    # untagged past because that is the thread it was actually typed into.
    _cid = (request.args.get('conversation_id') or '').strip()
    if _cid:
        try:
            from agent_friday.services import conversations as _convs
            _is_main = (_cid == _convs.MAIN_ID)
        except Exception:
            _is_main = False
        messages = [m for m in messages
                    if m.get('conversation_id') == _cid
                    or (_is_main and not m.get('conversation_id'))
                    or m.get('pinned')]
    return jsonify({"status": "ok", "messages": messages,
                    "count": len(messages), "conversation_id": _cid or None})


@chat_bp.route('/api/chat/send', methods=['POST'])
def chat_send():
    """Send a message, save to persistent history, return Friday's response.
    Accepts context-aware payload: {message, workspace, workspaceContext, includeVision, screenshot}.
    Text reasoning is Claude; vision (screenshot description) stays on Gemini.
    """
    try:
        data = request.get_json(silent=True) or {}
        # Same addressing rule as /api/chat: unaddressed callers reach Main.
        _conversation_id = _conv_id_from(data)
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
            if msg.get('role') == 'system':
                # B2/B5: transparency system lines are never model context.
                continue
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
        try:
            from agent_friday.services import attribution as _attr2
            _attr2.reset()
        except Exception:
            _attr2 = None
        reply, tool_trace = _generate_agent(
            messages, system=system_prompt, temperature=settings.get('temperature'),
            session_ctx=_sess_ctx, workspace=workspace,
        )

        # ── FR-2/A7 on this endpoint too: pseudo-tool-call leaks and
        # unreceipted completion claims are stripped and corrective-retried
        # through the same dispatcher (previously /api/chat/send ran no
        # integrity validation at all). ──
        def _send_redispatch(corrective_note):
            return _generate_agent(
                messages + [{"role": "user", "content": corrective_note}],
                system=system_prompt, temperature=settings.get('temperature'),
                session_ctx=_sess_ctx, workspace=workspace,
            )

        reply, tool_trace, _send_integrity = validate_toolcall_integrity(
            reply, tool_trace, [t['name'] for t in CLAUDE_TOOLS],
            redispatch=_send_redispatch,
        )

        # ── B2: seat-change visibility on this endpoint too. ──
        try:
            from agent_friday.services.seat_transparency import (
                effective_seat, observe_seats)
            _seat_events = observe_seats(settings)
            _seat_model, _seat_class = effective_seat(settings)
        except Exception:
            _seat_events, _seat_model, _seat_class = [], '', ''
        # Badge truth: prefer the ACTUAL generator over the configured seat.
        _fallback_chain = []
        try:
            if _attr2 is not None:
                _gen = _attr2.last_generation()
                _fallback_chain = _attr2.fallback_chain()
                if _gen and _gen.get('model'):
                    _seat_model = _gen['model']
                    _seat_class = _gen.get('seat') or _seat_class
        except Exception:
            pass

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
            'model': _seat_model,
            'seat': _seat_class,
        }
        if _fallback_chain:
            friday_msg['fallback_chain'] = _fallback_chain
        _persist_turn(_conversation_id, user_msg, friday_msg,
                      meta={'model': _seat_model, 'seat': _seat_class})

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

        return jsonify({"status": "ok", "user_msg": user_msg, "friday_msg": friday_msg,
                        "sources": sources, "tool_trace": tool_trace,
                        "model": _seat_model, "seat": _seat_class,
                        "seat_events": _seat_events,
                        "fallback_chain": _fallback_chain})
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
        #
        # SECURITY (2026-08-25, sweep followup): found while migrating every
        # caller to the now-required provider/vault_control params — this one
        # had neither, same bug class as the 22 other sites fixed the same
        # day. Predicting on `dossier_prompt` (the real transcript-bearing
        # content), not the inert 'source dossier' keywords literal used only
        # for context-section selection, mirrors what the real dispatch call
        # below actually routes on.
        system_prompt = _get_friday_system_prompt(
            keywords='source dossier', workspace='chat',
            provider=_predict_route_provider(keywords=dossier_prompt, workspace='chat'),
            vault_control=_get_vault_control() if _vault_local_only() else None)
        markdown = _generate_text(
            [{"role": "user", "content": dossier_prompt}],
            system=system_prompt,
            model=_load_settings().get('subagent_model') or 'claude-sonnet-5',
            workspace='news',
        )
        # Fact-check pass so low-trust news sources in the sheet get flagged too.
        markdown = _factcheck_news_citations(markdown)
        # FR-2: this route offers no tools (pure summarization over past
        # turns), so a leaked CLAUDE_TOOLS name here can only be fabrication,
        # not a real call gone unrendered — no redispatch path exists (there's
        # no tool_trace to retry against), so a leak fails the whole dossier
        # rather than rendering a document that narrates invented tool calls.
        from agent_friday.services.tool_integrity import find_pseudo_toolcalls
        _dossier_leaks = find_pseudo_toolcalls(markdown, [t['name'] for t in CLAUDE_TOOLS])
        if _dossier_leaks:
            print(f"  [INTEGRITY] source dossier draft contained fabricated "
                  f"tool-call syntax {_dossier_leaks} — discarding draft")
            markdown = (
                f"# 📋 Source Dossier — {session_id}\n\n"
                "_Couldn't generate a reliable dossier this time — the draft "
                "referenced tool calls that were never actually executed. "
                "Try again._\n"
            )

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
