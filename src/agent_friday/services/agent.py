import os
import io
import json
import glob
import subprocess
import shutil
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
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta
from pathlib import Path
from collections import deque as _deque

_log = logging.getLogger("friday.agent")
from functools import wraps
from flask import (Flask, Blueprint, jsonify, request, send_from_directory,
                   send_file, session, redirect, url_for, Response, stream_with_context)
import agent_friday.core as core
from agent_friday.core import (
    _network_is_offline,
    ANTHROPIC_MODEL_DEFAULT,
    CREATIONS_DIR,
    DECISION_BOM_FILE,
    FRIDAY_DIR,
    FRIDAY_VAULT_PASSPHRASE,
    _VAULT_ENCRYPTION_STATE,
    HOME,
    JOB_SEARCH_FILE,
    PROCESSES,
    VaultAccessControl,
    WIKI_DIR,
    _HAS_BEHAVIORAL_MONITOR,
    _POPEN_FLAGS,
    _RUN_COMMAND_BLOCKLIST,
    _load_settings,
    _log_context,
    _pii_redact,
    _sandbox_policy,
    _scrub_pii,
    get_anthropic_client,
    get_behavioral_monitor,
    process_log,
    process_register,
    process_update,
)  # noqa: E501
from agent_friday.services.model_router import (
    _call_ollama,
    _call_openai,
    _gated_vault_control,
    _get_friday_system_prompt,
    _get_vault_control,
    _predict_route_provider,
    _seal_or_block,
)  # noqa: E501
from agent_friday.services import tool_hooks as _hooks
from agent_friday.services.news_engine import (
    _fetch_news_items,
)  # noqa: E501
from agent_friday.services.wiki_engine import (
    _mirror_wiki_file,
    _propose_wiki_update,
    _safe_wiki_path,
    wiki_read_text,
    wiki_write_text,
)  # noqa: E501



def _generate_agent(messages, system=None, model=None, max_tokens=16384,
                    temperature=None, session_ctx=None, pii_lookup=None,
                    orb_label=None, orb_category='default', orb_icon='🧠',
                    workspace=None, on_route=None):
    """Tool-using (agentic) generation via the user's CONFIGURED provider.

    The agentic analog of _generate_text(). Bare _call_claude_agent() requires
    an Anthropic key and hard-fails with "ANTHROPIC_API_KEY is not set" the
    instant it is reached on a local (Ollama) or OpenAI-compatible setup — the
    exact crash the background-task worker (distill-to-wiki, deep research) and
    the legacy /api/chat/send endpoint hit. This consults the SAME model router
    the /api/chat path uses (has_tools=True) and dispatches to the matching
    agentic primitive — _call_ollama (single-shot, no tool loop), _call_openai
    with the tool loop, or _call_claude_agent — then falls back through the
    other providers so a tool-using turn never hard-fails while any provider is
    up. The ONLY place _call_claude_agent should be invoked is from here (and
    the already-routed /api/chat dispatch).

    Returns (text, tool_trace) — uniform across all three primitives.
    """
    # Demo mode: no provider configured (no keys + no local Ollama) → return a
    # labelled placeholder instead of exhausting every primitive and raising
    # RuntimeError("No model provider could run the agent"). This is the agentic
    # twin of the guard in _generate_text(); without it /api/chat/send and the
    # background-task workers hard-fail with HTTP 500 on a fresh keyless install.
    try:
        from agent_friday.services.demo_mode import is_demo, demo_response
        if is_demo():
            return demo_response('generic'), []
    except Exception:
        pass

    # Per-workspace temperature profile (creative pipeline): derive a sampling
    # temperature from the active workspace when the caller didn't pin one.
    # Honored by Ollama/OpenAI primitives; newer Claude models ignore it.
    try:
        from agent_friday.services.model_router import resolve_workspace_temperature
        temperature = resolve_workspace_temperature(workspace, temperature)
    except Exception:
        pass

    settings = _load_settings()
    routing_cfg = settings.get('model_routing') or {}
    provider, routed_model, routed_provider_name = 'cloud', model, None
    route = {}
    try:
        from agent_friday.routing.model_router import get_router
        route = get_router(routing_cfg).route(messages, task_context={
            "has_tools": True,
            "workspace": workspace or '',
            "cloud_model": model or settings.get('orchestrator_model') or ANTHROPIC_MODEL_DEFAULT,
            # Unattended work is allowed to prefer a local seat. Without this
            # the router cannot tell a scheduled heartbeat from Stephen typing,
            # and every tool-using turn looked interactive.
            "is_background_task": bool((session_ctx or {}).get(
                "is_background_task")),
            "scheduled": bool((session_ctx or {}).get("scheduled")),
        }) or {}
        provider = route.get('provider', 'cloud')
        routed_model = route.get('model') or model
        routed_provider_name = route.get('provider_name')
    except Exception as _re:
        print(f"  [AGENT] routing failed, defaulting to cloud: {_re}")
    if on_route:
        # Report the ACTUAL decision to whoever wants to narrate it. Never let
        # a logging callback break a turn.
        try:
            on_route(dict(route, model=routed_model, provider=provider))
        except Exception:
            pass

    # Honor the router's verdicts BEFORE any provider sees the request.
    # refuse=True means vault access was required and the configured fallback
    # is deny/warn — no model call is permitted at all.
    if route.get('refuse'):
        return (route.get('warning')
                or "This request needs vault access, which requires a local "
                   "model. Install or start Ollama (or adjust "
                   "model_routing.vault_cloud_fallback), then retry."), []
    vault_access = bool(route.get('vault_access'))

    # Provider primitives. The routed provider is tried first with the
    # router-chosen model; fallbacks use each provider's OWN configured default
    # (model=None) so a cloud model id never leaks into a local/OpenAI call.
    def _via_claude(use_model):
        if get_anthropic_client() is None:
            raise RuntimeError("Anthropic client unavailable (no key in env or settings)")
        # 2026-08-14 incident: `use_model or model` resurrected the caller's
        # LOCAL subagent seat (gemma4:e4b) on the fallback leg → Anthropic
        # 404 'model: gemma4:e4b' → heartbeat dead all night. A cloud leg
        # runs a configured CLOUD model, never a foreign id.
        from agent_friday.services.model_router import _claude_safe_model
        return _call_claude_agent(
            messages, system=system,
            model=_claude_safe_model(use_model or model, settings),
            max_tokens=max_tokens, temperature=temperature,
            pii_lookup=pii_lookup, session_ctx=session_ctx,
            orb_label=orb_label, orb_category=orb_category, orb_icon=orb_icon,
        )

    def _via_openai(use_model):
        # Full agentic tool loop with parity to _call_claude_agent. The routed
        # model rides its RESOLVED provider (openrouter/groq/…, GAP-3 fix);
        # the fallback attempt (use_model=None) keeps the legacy single-slot.
        return _call_openai(
            messages, system=system, model=use_model,
            max_tokens=max_tokens, temperature=temperature,
            orb_label=orb_label, tools=CLAUDE_TOOLS,
            pii_lookup=pii_lookup, session_ctx=session_ctx,
            provider=routed_provider_name if use_model else None,
        )

    def _via_ollama(use_model):
        # Local models run the FULL agentic tool loop now (native OpenAI-style
        # tool calling, e.g. gemma4) — same unified CLAUDE_TOOLS registry, vault
        # gate, and _execute_tool governance as the cloud paths. Returns
        # (text, tool_trace).
        # Fit the tool payload to the local seat's context window. Without
        # this, a vault-forced local route with the full registry (~59k
        # tokens observed) exceeds n_ctx and the turn dies with a 400 —
        # chat.py's dispatch trims, but this path did not (2026-08-19).
        # NOTE: `system` is a closure variable here — assigning to it would
        # make it function-local and raise UnboundLocalError on first read
        # (that exact bug took down every local background task on
        # 2026-08-19 evening). Build the augmented prompt in a NEW name.
        _sys_out = system
        try:
            from agent_friday.services.tool_budget import fit_tools_to_seat
            # Budget the whole request, not tools in isolation (2026-08-19:
            # in-budget tools atop an ordinary prompt still overflowed the
            # seat and 400'd).
            _prompt_cost = (len(system or "") + sum(
                len(m.get("content")) for m in (messages or [])
                if isinstance(m.get("content"), str))) // 4
            _fitted, _fit_note = fit_tools_to_seat(
                use_model, CLAUDE_TOOLS, prompt_cost=_prompt_cost)
            if _fit_note:
                _sys_out = (system or "") + "\n[SEAT] " + _fit_note
        except Exception:
            _fitted = CLAUDE_TOOLS
        return _call_ollama(
            messages, system=_sys_out, model=use_model,
            max_tokens=max_tokens, temperature=temperature,
            orb_label=orb_label, tools=_fitted,
            pii_lookup=pii_lookup, session_ctx=session_ctx,
        )

    if provider == 'local':
        attempts = [('local', _via_ollama, routed_model)]
        # A vault-forced local route must NEVER retry on a cloud provider:
        # the messages were assembled for a local model and may carry
        # TIER_2/TIER_3 content. Anything else keeps the resilience chain.
        if not vault_access:
            attempts += [('cloud', _via_claude, None),
                         ('openai', _via_openai, None)]
    elif provider == 'openai':
        attempts = [('openai', _via_openai, routed_model),
                    ('cloud', _via_claude, None),
                    ('local', _via_ollama, None)]
    else:  # cloud / default
        attempts = [('cloud', _via_claude, routed_model),
                    ('openai', _via_openai, None),
                    ('local', _via_ollama, None)]

    # Health-aware ordering: an open circuit breaker ('down') demotes that
    # provider to the end of the ladder — same rule as _generate_text. The
    # vault-forced single-attempt list is untouched (sorting one item is a
    # no-op), so vault guarantees are unaffected.
    # The mode the user chose outranks the resilience ladder. Without this a
    # cloud_only machine with no Anthropic key walked cloud -> openai -> LOCAL
    # for every briefing, scheduled task and subagent turn.
    try:
        from agent_friday.services.model_router import _mode_filtered_attempts
        attempts = _mode_filtered_attempts(attempts, routing_cfg,
                                           vault_access=vault_access)
    except Exception:
        pass
    try:
        from agent_friday.services.model_router import _health_order
        attempts = _health_order(attempts, routed_provider_name)
    except Exception:
        pass

    errors = []
    for name, fn, use_model in attempts:
        # Name the model each leg actually tried — "local: HTTP 404" without
        # the model id sent Stephen log-diving during the 2026-08-14 outage.
        _leg = f"{name} ({use_model})" if use_model else name
        try:
            text, trace = fn(use_model)
            if text and text.strip():
                return text, (trace or [])
            errors.append(f"{_leg}: empty response")
        except Exception as e:
            errors.append(f"{_leg}: {e}")
        # Badge truth: every abandoned leg is part of this message's
        # provenance — the reply the user finally sees came from whichever
        # leg succeeded next.
        try:
            from agent_friday.services import attribution
            attribution.note_fallback(errors[-1])
        except Exception:
            pass
    if vault_access:
        # Refuse rather than raise: the caller surfaces this as the reply, and
        # the request was deliberately kept off every cloud provider.
        #
        # LEAD WITH WHAT FAILED, NOT WITH THE POLICY. This message used to open
        # "This request touches vault-protected data, so it was only tried on
        # the local model — which failed (...)". The real cause — a dead seat, a
        # context overflow — arrived in a parenthesis at the end, after a first
        # clause that read as a refusal. Users stop at the first clause: Stephen
        # spent a day believing the vault was blocking him because the sentence
        # opened by telling him the vault was involved. The vault was working
        # correctly every time. Cause first, policy second.
        #
        # Also: do NOT name Ollama as the thing to check. Friday's local seats
        # are served by her OWN llama-server (127.0.0.1:8090+), which is a
        # different process that Ollama's status says nothing about — so the
        # one remediation this message offered pointed at the wrong daemon.
        return ("That didn't work: the local model failed ("
                + "; ".join(errors[-1:]) +
                "). Because this request touches vault-protected data it could "
                "only run locally, so there was no cloud fallback to try — it "
                "was NOT sent to any cloud provider. This is a local-model "
                "problem, not a permissions one: check that the local seat is "
                "up, then retry."), []
    raise RuntimeError(
        "No model provider could run the agent (tried "
        + "; ".join(errors[-3:]) + "). Set ANTHROPIC_API_KEY via the setup "
        "wizard (or as an environment variable), configure an OpenAI-compatible "
        "endpoint in Settings, or run Ollama locally, then restart the server."
    )


# ── Action permission policy (injected into the chat system prompt) ──────────
# Tells the model the social contract the confirmation gate enforces mechanically:
# ask before acting, confirm, do, report. Keeping it in the prompt means the model
# asks naturally on the FIRST attempt instead of being bounced by the gate.
ACTION_PERMISSION_POLICY = (
    "=== ACTION PERMISSION POLICY (REQUIRED) ===\n"
    "Before you take any real-world action on the user's computer — opening a URL "
    "in the browser, launching an app, switching the on-screen workspace, opening "
    "a folder, or creating a file — you MUST ask permission first and wait for the "
    "user to agree. Ask a short yes/no question (e.g. \"Would you like me to open "
    "that in your browser?\" / \"I can switch to the News workspace — shall I?\"). "
    "Only after the user says yes do you perform the action. While the action runs, "
    "do not narrate over it. When it succeeds, confirm plainly what you did (e.g. "
    "\"Done — I've opened the Reuters article in your browser.\"). If it fails, say "
    "so honestly (\"That didn't work — the link looks broken.\") and offer another "
    "approach. Only open URLs that came from real data (a news item, a saved "
    "source) — never a link you reconstructed from memory. Exceptions where you do "
    "NOT need to ask: an action the user explicitly requested in their CURRENT "
    "message (e.g. they just said \"open news\"), and simply showing a notification. "
    "Never surprise the user with an action they did not approve.\n"
    "==========================================="
)


# ── Claude Tool-Use Agent ─────────────────────────────────────
# Tools Claude can call when answering the user. Each tool has a handler
# in CLAUDE_TOOL_HANDLERS. Results are PII-shielded before being sent back.
CLAUDE_TOOLS = [
    {"name": "search_web", "description": "Search the web for current information. Returns ranked snippets with URLs. Use for news, facts, people, companies, anything not in the local wiki — AND for the small factual gaps inside a task you are already doing. If Stephen asks you to add a business's phone number and you have its name and address, that is a lookup: search for it, confirm it against the business's own site or a second source, and cite where it came from. Do not ask him for a detail he would reasonably expect you to find, and never invent one.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "browse_web", "description": "Fetch a URL and return its full text content (HTML stripped). Use after search_web to read the full article/page, and to VERIFY a fact against its primary source — a business's own website beats a directory aggregator. When a detail matters enough to write somewhere permanent, confirm it on the source page rather than trusting a search snippet. Ring 2.",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string", "description": "Full https:// URL to fetch"}}, "required": ["url"]}},
    {"name": "read_file", "description": "Read any file on the local filesystem. Supports absolute paths (C:\\...) or paths relative to home (~). Extracts real text from PDF and .docx files (never raw bytes). Returns up to 500000 chars.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string", "description": "Absolute or home-relative path, e.g. ~/Projects/foo/bar.py or ~/wiki/notes.md"}}, "required": ["path"]}},
    {"name": "search_files", "description": "Find files by name on the local filesystem — the tool for 'find my resume in Downloads' or 'what's the latest report in Documents'. Searches Documents, Downloads, Desktop, and Friday's creations by default (configurable in Settings). Never searches the vault. Set content_query to also search inside extractable text (md/txt now; PDF/docx once read; hollow for other binary formats). Returns paths, names, sizes, and modified times, newest first by default.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "Filename substring/fuzzy match, e.g. 'resume' or 'cv'. Leave blank to list a root's newest files."},
         "root": {"type": "string", "description": "Restrict to one root: documents, downloads, desktop, creations, or a configured extra root. Default: search all of them."},
         "content_query": {"type": "string", "description": "Optional: also search inside file text for this phrase."},
         "newest_first": {"type": "boolean", "description": "Sort newest-modified first. Default true."},
         "limit": {"type": "integer", "description": "Max results. Default 20."},
     }, "required": []}},
    {"name": "write_file", "description": "Write or append content to any file on the local filesystem. Creates parent directories automatically.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Absolute or home-relative path"},
         "content": {"type": "string", "description": "Text to write"},
         "mode": {"type": "string", "enum": ["write", "append"], "description": "write (overwrite) or append. Default: write"},
     }, "required": ["path", "content"]}},
    {"name": "write_clipboard", "description": "Copy text to the user's Windows clipboard.",
     "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "query_trust_graph", "description": "Look up a person in the trust graph by name or alias and return their entry (scores, evidence count, last interaction).",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "annotate_calendar_events", "description": "ADD a location, phone number, or note to EVERY calendar event matching a search term — the tool for 'add the clinic's address and phone to all my chiropractor entries'. Purely additive: existing values are appended to, never replaced, and every prior value is returned so a change can be undone. Handles recurring series (edits the whole series, not one occurrence). Set dry_run to preview which events would change. If the result says the token is read-only, tell the user plainly that Google must be reconnected to grant event editing and offer to start it — do NOT substitute a map, directions, or any other action for the edit they asked for.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "Text to match against event titles/descriptions, e.g. 'chiropractor'."},
         "location": {"type": "string", "description": "Address to add to the event's location field."},
         "phone": {"type": "string", "description": "Phone number to add to the event's description."},
         "note": {"type": "string", "description": "Any other line to add to the description."},
         "apply_to_series": {"type": "boolean", "description": "Default true — edit the whole recurring series rather than a single occurrence."},
         "dry_run": {"type": "boolean", "description": "Preview the changes without writing."}},
      "required": ["query"]}},
    {"name": "create_calendar_event", "description": "Create a new event on the user's Google Calendar. Times are ISO 8601. If the token is read-only, say so plainly and offer to reconnect Google.",
     "input_schema": {"type": "object", "properties": {
         "title": {"type": "string"}, "start": {"type": "string", "description": "ISO 8601 start, e.g. 2026-08-18T11:30:00-05:00"},
         "end": {"type": "string", "description": "ISO 8601 end. Defaults to one hour after start."},
         "location": {"type": "string"}, "description": {"type": "string"},
         "attendees": {"type": "array", "items": {"type": "string"}}},
      "required": ["title", "start"]}},
    {"name": "update_calendar_event", "description": "Change one existing event by id (title, time, location, description). Use annotate_calendar_events instead when adding the same detail to several events. CLEARING a field is refused unless allow_clearing is true, because blanking loses information — if the user wants a field emptied, confirm that specifically and pass the flag.",
     "input_schema": {"type": "object", "properties": {
         "event_id": {"type": "string"}, "title": {"type": "string"},
         "start": {"type": "string"}, "end": {"type": "string"},
         "location": {"type": "string"}, "description": {"type": "string"},
         "allow_clearing": {"type": "boolean", "description": "Permit emptying a field. Only set when the user explicitly asked for erasure."}},
      "required": ["event_id"]}},
    {"name": "find_calendar_events", "description": "Search the user's calendar by text across the past 60 and next 400 days, returning event ids, titles, start times, locations and whether each belongs to a recurring series. Use before updating so you edit the right events.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string"}}, "required": ["query"]}},
    {"name": "query_calendar", "description": "Check the user's Google Calendar (today's & tomorrow's events). Built-in Google integration. If the result says 'not connected', the integration just needs a one-time OAuth connection — offer to walk the user through it; do NOT say you lack calendar access.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "search_email", "description": "Search and read the user's recent Gmail (built-in read-only Google integration). If the result says 'not connected', the integration just needs a one-time OAuth connection — offer to set it up; do NOT say you can't access Gmail.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "search_drive", "description": "Search Google Drive file/folder names across every connected Google account (built-in read-only integration). Returns each hit's id, name, mime_type, and which account it's in — pass the id + mime_type to read_doc for Docs/Sheets content. If a hit's account never granted Drive access, its error is reported per-account, not as 'not connected'.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "Name substring to search for; omit for the most recently modified files."}}}},
    {"name": "read_doc", "description": "Read a Google Doc's text or a Sheet's first-tab values, by file id (get the id from search_drive first). account_id is optional — omit it to try every connected account until one has access.",
     "input_schema": {"type": "object", "properties": {
         "file_id": {"type": "string"},
         "account_id": {"type": "string", "description": "From a prior search_drive hit's account_id; omit to auto-try all connected accounts."},
         "mime_type": {"type": "string", "description": "From a prior search_drive hit's mime_type; skips an extra lookup if provided."},
     }, "required": ["file_id"]}},
    {"name": "list_tasks", "description": "List open Google Tasks across every connected Google account (built-in read-only integration). If the result says 'not connected', offer the one-time OAuth connection; a per-account error (e.g. this account never granted Tasks access) is reported specifically, not as 'not connected'. Each task includes account_id and tasklist_id — pass those into complete_task/update_task/delete_task, don't guess them.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "complete_task", "description": "Mark one Google Task completed, in one specific account's tasklist (built-in write integration). account_id and tasklist_id are required — get them from a prior list_tasks call, never guessed; a write to the wrong account is a different class of mistake than a read from the wrong one. Fails with a clear per-account error if that account hasn't granted write-capable Tasks access yet.",
     "input_schema": {"type": "object", "properties": {
         "task_id": {"type": "string"},
         "tasklist_id": {"type": "string", "description": "From the task's tasklist_id in a prior list_tasks result."},
         "account_id": {"type": "string", "description": "From the task's account_id in a prior list_tasks result."}},
      "required": ["task_id", "tasklist_id", "account_id"]}},
    {"name": "create_task", "description": "Create a new Google Task in one specific connected account (built-in write integration). account_id is required — pick it from list_tasks or the connected-accounts list, never guessed.",
     "input_schema": {"type": "object", "properties": {
         "title": {"type": "string"},
         "account_id": {"type": "string", "description": "Which connected account to create it in — required, never guessed."},
         "tasklist_id": {"type": "string", "description": "Defaults to the account's default list if omitted."},
         "notes": {"type": "string"},
         "due": {"type": "string", "description": "RFC3339 timestamp, e.g. 2026-09-01T00:00:00Z"}},
      "required": ["title", "account_id"]}},
    {"name": "update_task", "description": "Update a Google Task's title/notes/due/status in one specific account's tasklist (built-in write integration). account_id and tasklist_id are required — get them from a prior list_tasks call, never guessed. To just mark something done, prefer complete_task.",
     "input_schema": {"type": "object", "properties": {
         "task_id": {"type": "string"},
         "tasklist_id": {"type": "string", "description": "From the task's tasklist_id in a prior list_tasks result."},
         "account_id": {"type": "string", "description": "From the task's account_id in a prior list_tasks result."},
         "title": {"type": "string"},
         "notes": {"type": "string"},
         "due": {"type": "string"},
         "status": {"type": "string", "description": "'needsAction' or 'completed'."}},
      "required": ["task_id", "tasklist_id", "account_id"]}},
    {"name": "delete_task", "description": "Permanently delete a Google Task from one specific account's tasklist (built-in write integration). This cannot be undone. account_id and tasklist_id are required — get them from a prior list_tasks call, never guessed.",
     "input_schema": {"type": "object", "properties": {
         "task_id": {"type": "string"},
         "tasklist_id": {"type": "string", "description": "From the task's tasklist_id in a prior list_tasks result."},
         "account_id": {"type": "string", "description": "From the task's account_id in a prior list_tasks result."}},
      "required": ["task_id", "tasklist_id", "account_id"]}},
    {"name": "search_contacts", "description": "Search the user's Google Contacts across every connected account by name, email, or phone substring (built-in read-only integration). Omit query to list recent contacts.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}}},
    {"name": "read_wiki", "description": "Read a markdown file from the personal wiki at ~/wiki/. Use a relative path like 'professional/job-search.md'.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "search_wiki", "description": "Keyword-search the personal wiki (and ~/.friday/wiki/) for files whose name or contents match a query. Returns up to 5 hits with a relative path and a short excerpt. Use this when the smart-loaded context didn't include the file you need; then call read_wiki on the most promising hit for the full file.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]}},
    {"name": "search_news", "description": "Search the live news feed for current stories matching a query (the same feed the News workspace shows). Returns ranked hits with title, snippet, source, trust rating, and URL. Use for 'what's the news on X', 'any headlines about Y', or to ground a claim in current reporting. Omit the query to get the top current stories.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "Keywords to match across headline/snippet/source. Blank = top current stories."}, "limit": {"type": "integer", "description": "Max stories to return (1-25, default 8)."}}}},
    {"name": "run_command", "description": "Run a non-destructive PowerShell command on the system. Destructive commands (rm, del, format, shutdown, reg delete, etc.) are blocked.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "open_url", "description": "Open a URL / web page in the user's web browser — this opens a REAL browser tab on the user's screen (Chrome, or their default browser). Use this whenever the user asks you to 'open', 'pull up', 'go to', 'open a tab for', or 'open in the browser' any website or web page. You CAN open browser tabs — do not say you can't.",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string", "description": "Full http(s):// URL of the page to open in a browser tab."}}, "required": ["url"]}},
    {"name": "open_path", "description": "Open a local file, folder, or app on the user's computer (e.g. 'Downloads', 'Projects', a file path like C:\\Users\\me\\notes.txt, or an app like Notepad/Explorer). Reveals or opens only — never deletes.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "A folder/file path or friendly name (Downloads, Desktop, Projects, an absolute path, or an app name). A bare filename Friday just created resolves against the creations folder."},
         "in_browser": {"type": "boolean", "description": "Open the file as a tab in Chrome (or the default browser) instead of the OS default app. Use this for images, PDFs and HTML when the user asks for a browser tab, or when they want several files open side by side."}},
      "required": ["path"]}},
    {"name": "switch_model",
     "description": "Change which AI model answers the user's chat (the "
                    "'reasoning' seat). Use this whenever they ask to switch, "
                    "change, use or try a different model, by any name they "
                    "use for it - 'switch to Gemma4 12B Uncensored', 'use the "
                    "small local one', 'go back to Sonnet'. Matching is "
                    "forgiving, so pass their words through. Takes effect on "
                    "the next message.",
     "input_schema": {"type": "object",
                      "properties": {"model": {"type": "string",
                                               "description": "The model the user named, in their words."}},
                      "required": ["model"]}},
    {"name": "navigate", "description": "Switch the Friday desktop UI to one of its built-in workspaces, on-screen, for the user. Use this whenever the user asks to open, show, switch to, or go to a workspace by name — this drives the ACTUAL interface, so prefer it over just describing where something is. Workspaces: home, career, wiki, studio, trust, system, news, draft, code, finance, health, contacts, content, messages, calendar, family, futurespeak.",
     "input_schema": {"type": "object", "properties": {"workspace": {"type": "string", "description": "Workspace id or spoken name, e.g. 'studio', 'news', 'calendar', 'settings'."}}, "required": ["workspace"]}},
    {"name": "revert_workspace", "description": "Undo a change Friday made to one of the user's workspaces or the liquid UI. Use whenever he says 'roll that back', 'undo that', 'put it back', or 'restore my workspace to how it was this morning'. Modes: 'undo' (the most recent change), 'as_of' (the state at a time — pass when), 'version' (a specific version_id from the history), 'reset' (back to baseline). Every undo is itself snapshotted, so an undo can be undone. Call list_workspace_history first if you need to see what changed.",
     "input_schema": {"type": "object", "properties": {
         "workspace": {"type": "string", "description": "Workspace id, e.g. 'studio', 'news', 'home'."},
         "mode": {"type": "string", "enum": ["undo", "as_of", "version", "reset"], "description": "Default 'undo'."},
         "when": {"type": "string", "description": "For mode 'as_of' — an ISO timestamp, e.g. 2026-08-17T08:00:00."},
         "version_id": {"type": "string", "description": "For mode 'version'."}},
      "required": ["workspace"]}},
    {"name": "list_workspace_history", "description": "Show what changed in a workspace, when, and how to undo each change. Read this before reverting when he is not specific about which change he means.",
     "input_schema": {"type": "object", "properties": {
         "workspace": {"type": "string"}}, "required": ["workspace"]}},
    {"name": "draft_email", "description": "Compose an email. Needs a write-enabled Gmail connection (native Google integration is read-only). If unavailable, tell the user it needs connecting and offer setup — do NOT say you can't email.",
     "input_schema": {"type": "object", "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["to", "subject", "body"]}},
    {"name": "get_career_pipeline", "description": "Get the current job-search pipeline status from the wiki.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_briefing", "description": "Get the most recent daily briefing summary.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "learn_skill", "description": "Create, modify, delete, or list skill YAML files in ~/.friday/skills/. Skills are reusable workflow definitions Friday can load. Use this for self-improvement — when you notice a pattern worth encoding. Actions: create, modify, delete, list, read.",
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["create", "modify", "delete", "list", "read"], "description": "Operation to perform"},
         "name": {"type": "string", "description": "Skill slug (alphanumeric/dashes). Required for all actions except 'list'."},
         "content": {"type": "string", "description": "YAML content for the skill (required for create/modify). Fields: name, description, trigger_patterns, tool_chain, prompt_template, success_criteria"},
     }, "required": ["action"]}},
    {"name": "install_package", "description": "Install a pip or npm package. Always check_only first to see if already installed. Ring 3 — requires Computer Control permission.",
     "input_schema": {"type": "object", "properties": {
         "package": {"type": "string", "description": "Package name, e.g. 'beautifulsoup4' or 'requests>=2.28'"},
         "manager": {"type": "string", "enum": ["pip", "npm"], "description": "Package manager. Default: pip"},
         "check_only": {"type": "boolean", "description": "If true, only checks if installed (no install). Default: false"},
     }, "required": ["package"]}},
    {"name": "epistemic_score", "description": "Self-improvement introspection: analyze Friday's own recent responses for epistemic quality. Scores the last N responses (pulled from conversation memory) on confidence calibration, hedging appropriateness, source attribution, uncertainty acknowledgment, and claim specificity, returning per-dimension averages, an overall composite, the weakest dimension, and concrete guidance. Read-only (Ring 0).",
     "input_schema": {"type": "object", "properties": {
         "limit": {"type": "integer", "description": "How many recent Friday responses to analyze (1-200, default 20)."},
     }}},
    {"name": "personality_show", "description": "Self-improvement introspection: return Friday's current personality configuration from ~/.friday/personality.json — traits, style, maturity, temperature, evolution — plus the agent identity and communication style. Read-only (Ring 0).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "personality_check_sycophancy", "description": "Self-improvement introspection: analyze Friday's recent responses for sycophantic patterns — reflexive agreement, unwarranted praise, over-deference — and cross-reference the pushback rate to flag the danger zone (lots of flattery + rare disagreement). Read-only (Ring 0).",
     "input_schema": {"type": "object", "properties": {
         "limit": {"type": "integer", "description": "How many recent Friday responses to analyze (1-200, default 20)."},
     }}},
]


def _html_to_text(html):
    """Strip HTML tags to plain text, preferring BeautifulSoup when available."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
            tag.decompose()
        text = soup.get_text(separator='\n', strip=True)
        return re.sub(r'\n{3,}', '\n\n', text)
    except ImportError:
        text = re.sub(r'<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>', ' ', html, flags=re.I | re.S)
        text = re.sub(r'<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>', ' ', text, flags=re.I | re.S)
        text = re.sub(r'<[^>]+>', ' ', text)
        return re.sub(r'\s+', ' ', text).strip()


def _tool_search_web(inp):
    """Search the web. Delegates to services.web_search (deep-research P1).

    Returns REAL hrefs, so browse_web can fetch what this found — the old
    implementation returned DuckDuckGo's display text (a truncated, scheme-less
    domain) and the pair could not work together. It also distinguishes "no
    results" from "the search tool is broken" instead of returning a
    challenge page's text under a "Search results" heading.
    """
    q = ((inp or {}).get('query') or '').strip()
    if not q:
        return "search_web error: 'query' is required."
    try:
        from agent_friday.services import web_search as _ws
    except Exception as e:
        return f"Web search unavailable (module import failed: {e}). Query: {q!r}"
    try:
        out = _ws.search(q, count=int((inp or {}).get('count') or 10))
    except Exception as e:
        return (f"Web search error: {type(e).__name__}: {e}. This is a TOOL "
                f"FAILURE, not evidence that nothing is published. Query: {q!r}")

    results = out.get('results') or []
    if not results:
        return (f"Search for {q!r} returned no results "
                f"(backend: {out.get('backend')}).\n{_ws.status_note(out)}")
    lines = [f"Search results for '{q}' (backend: {out.get('backend')}, "
             f"{len(results)} results). URLs below are real and fetchable — "
             f"pass one verbatim to browse_web:\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}\n   {r['snippet']}\n   {r['url']}")
    if out.get('detail'):
        lines.append(f"\n[note: {out['detail']}]")
    return '\n'.join(lines)[:100_000]


def _tool_browse_web(inp):
    """Fetch a page's text. Routes through services.web_fetch, which applies
    the SSRF guard to the URL AND to every redirect hop (deep-research P2).

    Before this, browse_web fetched any http(s) URL: loopback, RFC1918, the
    cloud-metadata address and Friday's own ports were all reachable by any
    URL that reached the tool — including one embedded in a page Friday was
    asked to read.
    """
    url = ((inp or {}).get('url') or '').strip()
    if not url:
        return "browse_web error: 'url' is required."
    try:
        from agent_friday.services import web_fetch as _wf
    except Exception as e:
        return f"browse_web unavailable (module import failed: {e})"

    rec = _wf.fetch(url)
    if not rec.get('ok'):
        kind = rec.get('error_kind')
        if kind == 'refused_unsafe':
            return (f"I did NOT fetch that URL — {rec.get('error')}. Internal "
                    f"and local-network addresses are off limits to this tool.")
        if kind == 'unreadable_type':
            return (f"Could not read {url} — {rec.get('error')}. Say so plainly "
                    f"rather than substituting a different source.")
        return f"Browse error ({url}): {rec.get('error')}"

    text = _wf.load_extraction(rec['id']) or ''
    _log_context("browse_web", {"url": url, "chars": len(text),
                                "cached": rec.get('from_cache')})
    header = f"[{rec.get('final_url') or url}]"
    if rec.get('redirect_chain'):
        header += f"\n[followed {len(rec['redirect_chain'])} redirect(s), each safety-checked]"
    tail = (f"\n...[truncated — {rec.get('chars')} chars extracted]"
            if rec.get('truncated') else "")
    return f"{header}\n{text}{tail}"


def _suggest_near_miss(p: Path) -> str:
    """WO-14 item 4: on file-not-found, name up to 3 similar filenames in the
    same directory instead of a bare dead end (the 09:18 failure — Friday
    guessed 'resume.pdf', it did not exist, and she had nothing better to
    offer than asking Stephen for the exact name she should have been able
    to find herself)."""
    try:
        import difflib
        parent = p.parent
        if not parent.is_dir():
            return ""
        names = [f.name for f in parent.iterdir() if f.is_file()]
        matches = difflib.get_close_matches(p.name, names, n=3, cutoff=0.4)
        if matches:
            return f" Similar files here: {', '.join(matches)}"
    except Exception:
        pass
    return ""


def _tool_read_file(inp):
    raw = (inp or {}).get('path', '')
    if not raw:
        return "read_file error: 'path' is required."
    try:
        p = Path(raw).expanduser().resolve()
    except Exception as e:
        return f"Invalid path {raw!r}: {e}"
    if not p.exists():
        return f"File not found: {p}.{_suggest_near_miss(p)}"
    if not p.is_file():
        return f"Not a file: {p}"
    try:
        from agent_friday.services.file_extraction import extract_text
        result = extract_text(p)
    except Exception as e:
        return f"Read error: {e}"
    if result.text is None:
        return f"Could not read {p.name}: {result.error}"
    text = result.text
    # WO-17 read-time feeder is registered in _hook_file_grant_registration
    # (a POST-tool hook, priority 96), not here. Found live 2026-08-25: this
    # used to call file_grants.on_file_read(p, text) at THIS point, before
    # _hook_pii_scrub (priority 95) ran — so it registered the RAW text while
    # the egress gate ultimately sees the PII-SCRUBBED text (phone/email/
    # address replaced with [PII:...] placeholders). Any paragraph containing
    # a phone number or address therefore never matched its registered span
    # and fell through to normal classification — the grant looked live
    # (ledger entry, check_grant='active') while the summary/skills section
    # of a real CV stayed withheld. Registration must happen on the exact
    # string that will actually reach the gate, which is only known after
    # the scrub hook runs.
    _log_context("file_read", {"path": str(p), "bytes": len(text)})
    limit = 500_000
    out = text[:limit] + (f"\n...[truncated — {len(text)} total chars]" if len(text) > limit else "")
    if result.truncated:
        out += "\n...[extraction truncated to the first pages of this document]"
    return out


def _tool_search_files(inp):
    inp = inp or {}
    from agent_friday.services.file_search import search_files
    try:
        result = search_files(
            query=inp.get('query') or '',
            root=inp.get('root') or None,
            content_query=inp.get('content_query') or '',
            newest_first=inp.get('newest_first', True),
            limit=inp.get('limit', 20),
        )
    except Exception as e:
        return json.dumps({"error": f"search_files failed: {e}"})
    return json.dumps(result, default=str)


def _tool_write_file(inp):
    inp = inp or {}
    raw = (inp.get('path') or '').strip()
    content = inp.get('content', '')
    mode = (inp.get('mode') or 'write').lower()
    if not raw:
        return "write_file error: 'path' is required."
    if mode not in ('write', 'append'):
        mode = 'write'
    try:
        p = Path(raw).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        if mode == 'append':
            with open(p, 'a', encoding='utf-8') as f:
                f.write(content)
        else:
            p.write_text(content, encoding='utf-8')
        _log_context("file_write", {"path": str(p), "bytes": len(content), "mode": mode})
        return f"{'Appended' if mode == 'append' else 'Wrote'} {len(content)} chars to {p}"
    except Exception as e:
        return f"Write error: {e}"


def _tool_learn_skill(inp):
    """Create, modify, delete, or list skill YAML files in ~/.friday/skills/."""
    inp = inp or {}
    action = (inp.get('action') or 'create').lower()
    skills_dir = FRIDAY_DIR / 'skills'
    skills_dir.mkdir(parents=True, exist_ok=True)

    if action == 'list':
        skills = sorted(f.stem for f in skills_dir.glob('*.yaml'))
        return json.dumps({'skills': skills, 'count': len(skills), 'path': str(skills_dir)})

    name = re.sub(r'[^\w\-]', '_', (inp.get('name') or '').strip())
    if not name:
        return "learn_skill error: 'name' is required for create/modify/delete."

    skill_file = skills_dir / f'{name}.yaml'

    if action == 'delete':
        if skill_file.exists():
            skill_file.unlink()
            return f"Skill '{name}' deleted."
        return f"Skill '{name}' not found."

    if action in ('create', 'modify', 'update'):
        content = (inp.get('content') or '').strip()
        if not content:
            return "learn_skill error: 'content' (YAML text) is required for create/modify."
        existed = skill_file.exists()
        skill_file.write_text(content, encoding='utf-8')
        _log_context("skill_write", {"name": name, "action": action})
        # Register into the portable SKILL.md registry + SkillOpt so the skill is
        # matched/injected on the very next turn (no restart needed) and enters
        # the closed-loop optimizer.
        try:
            import agent_friday.skill_registry as _skreg
            _sk = _skreg.get_skill(name)
            if _sk:
                _skreg.register_with_skillopt(_sk)
        except Exception:
            pass
        return f"Skill '{name}' {'modified' if existed else 'created'} at {skill_file}. Active now — its triggers will inject it on matching turns."

    if action == 'read':
        if not skill_file.exists():
            return f"Skill '{name}' not found."
        return skill_file.read_text(encoding='utf-8')

    return f"Unknown action '{action}'. Use: create, modify, delete, list, read."


def _tool_install_package(inp):
    """Install pip or npm packages (Ring 3 — requires CC permission)."""
    inp = inp or {}
    package = (inp.get('package') or '').strip()
    manager = (inp.get('manager') or 'pip').lower()
    check_only = bool(inp.get('check_only', False))

    if not package:
        return "install_package error: 'package' is required."
    if not re.match(r'^[a-zA-Z0-9_\-\.\[\]>=<!,~\s]+$', package):
        return f"install_package error: invalid package name: {package!r}"

    if manager == 'pip':
        bare = re.split(r'[>=<!,\[\s]', package)[0].strip()
        if check_only:
            try:
                proc = subprocess.run(
                    [sys.executable, '-m', 'pip', 'show', bare],
                    capture_output=True, text=True, timeout=15,
                    creationflags=_POPEN_FLAGS,
                )
                return f"INSTALLED:\n{proc.stdout[:800]}" if proc.returncode == 0 else f"NOT INSTALLED: {bare}"
            except Exception as e:
                return f"Check error: {e}"
        try:
            proc = subprocess.run(
                [sys.executable, '-m', 'pip', 'install', package],
                capture_output=True, text=True, timeout=180,
                creationflags=_POPEN_FLAGS,
            )
            out = (proc.stdout or '') + (('\n[stderr]\n' + proc.stderr) if proc.stderr else '')
            return f"{'SUCCESS' if proc.returncode == 0 else 'FAILED'}:\n{out[:4000]}"
        except subprocess.TimeoutExpired:
            return "pip install timed out after 180s."
        except Exception as e:
            return f"pip install error: {e}"

    elif manager == 'npm':
        cmd = ['npm', 'list', '-g', '--depth=0', package] if check_only else ['npm', 'install', '-g', package]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=180,
                creationflags=_POPEN_FLAGS,
            )
            out = (proc.stdout or '') + (('\n[stderr]\n' + proc.stderr) if proc.stderr else '')
            return f"{'SUCCESS' if proc.returncode == 0 else 'FAILED'}:\n{out[:4000]}"
        except Exception as e:
            return f"npm error: {e}"

    return f"Unknown package manager: {manager!r}. Use 'pip' or 'npm'."


def _tool_write_clipboard(inp):
    text = (inp or {}).get('text', '')
    if not text:
        return "No text provided."
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Set-Clipboard", "-Value", text],
            check=True, capture_output=True, timeout=10,
            creationflags=_POPEN_FLAGS,
        )
        return f"Copied {len(text)} chars to clipboard."
    except Exception as e:
        return f"Clipboard error: {e}"


def _tool_query_trust_graph(inp):
    name = ((inp or {}).get('name') or '').strip().lower()
    if not name:
        return "No name provided."
    # Defined in services/misc_engine.py — an UPPER layer — so it must be
    # imported lazily at call time (module-level would be circular).
    from agent_friday.services.misc_engine import _load_trust_graph
    graph = _load_trust_graph()
    people = graph.get('people') or {}
    items = people.values() if isinstance(people, dict) else people
    for p in items:
        if not isinstance(p, dict):
            continue
        if (p.get('name') or '').strip().lower() == name:
            return json.dumps(p, default=str)[:100_000]
        aliases = [str(a).lower() for a in (p.get('aliases') or [])]
        if name in aliases:
            return json.dumps(p, default=str)[:100_000]
    return f"No trust-graph entry found for {name!r}."


# When a built-in Google integration (Gmail / Calendar) isn't connected yet, the
# tool returns THIS — never "not installed". The integration EXISTS; it just
# needs a one-time OAuth connection. The wording instructs Friday to OFFER setup
# instead of telling the user she can't access their mail/calendar.
_GOOGLE_NOT_CONNECTED_NOTE = (
    "{what} is built in but NOT CONNECTED on this machine yet (no OAuth token). "
    "This is a one-time connection, not a missing feature. Tell the user {what} is "
    "set up and ready to link, and OFFER to walk them through the one-time "
    "connection — they authorize at /api/google/auth (or Settings -> Connectors). "
    "Do NOT tell them you can't access {reads}; say it just needs connecting."
)


def _summarize_multi_account_errors(result):
    """Turn a google_accounts.merged_*() result ({accounts, <items>, errors})
    into a flat per-account status list — "connected" only means an account
    exists and returned no error; a real API failure (e.g. an API not
    enabled in the GCP project) shows up as its own status/error per
    account, distinct from an account that was never connected at all."""
    by_id = {}
    for acc in (result.get("accounts") or []):
        by_id[acc.get("id")] = {"label": acc.get("label"), "email": acc.get("email"),
                                "status": "connected", "error": None}
    for e in (result.get("errors") or []):
        aid = e.get("account_id")
        entry = by_id.setdefault(aid, {"label": e.get("label"), "email": None,
                                       "status": "connected", "error": None})
        entry["status"] = "error"
        entry["error"] = e.get("error")
    return list(by_id.values())


def _calendar_write_summary(res, what):
    """Render a write result as prose Friday can repeat truthfully."""
    if not isinstance(res, dict):
        return str(res)
    if res.get("needs_reconnect"):
        return ("I could not %s: %s" % (what, res.get("error")))
    if res.get("error"):
        return "I could not %s: %s" % (what, res.get("error"))
    return json.dumps(res, default=str)[:2400]


def _tool_annotate_calendar_events(inp):
    """Add a location/phone/note to every matching event (additive)."""
    from agent_friday.services import calendar_write as cw
    inp = inp or {}
    q = (inp.get("query") or "").strip()
    if not q:
        return "annotate_calendar_events error: 'query' is required."
    res = cw.annotate_events(
        q, location=(inp.get("location") or "").strip(),
        phone=(inp.get("phone") or "").strip(),
        note=(inp.get("note") or "").strip(),
        apply_to_series=inp.get("apply_to_series", True),
        dry_run=bool(inp.get("dry_run")))
    return _calendar_write_summary(res, "update those calendar entries")


def _tool_create_calendar_event(inp):
    from agent_friday.services import calendar_write as cw
    inp = inp or {}
    res = cw.create_event(
        title=(inp.get("title") or "").strip(),
        start=(inp.get("start") or "").strip(),
        end=(inp.get("end") or "").strip(),
        location=(inp.get("location") or "").strip(),
        description=(inp.get("description") or "").strip(),
        attendees=inp.get("attendees") or None)
    return _calendar_write_summary(res, "create that event")


def _tool_update_calendar_event(inp):
    from agent_friday.services import calendar_write as cw
    inp = inp or {}
    eid = (inp.get("event_id") or "").strip()
    if not eid:
        return "update_calendar_event error: 'event_id' is required."
    res = cw.update_event(
        eid, title=inp.get("title"), start=inp.get("start"),
        end=inp.get("end"), location=inp.get("location"),
        description=inp.get("description"),
        allow_clearing=bool(inp.get("allow_clearing")))
    return _calendar_write_summary(res, "update that event")


def _tool_find_calendar_events(inp):
    from agent_friday.services import calendar_write as cw
    q = ((inp or {}).get("query") or "").strip()
    if not q:
        return "find_calendar_events error: 'query' is required."
    return _calendar_write_summary(cw.find_events(q), "search your calendar")


def _tool_revert_workspace(inp):
    """Undo a liquid-UI / workspace change. The spoken half of the undo path."""
    from agent_friday.services import workspace_studio as ws
    inp = inp or {}
    wsid = (inp.get("workspace") or "").strip()
    if not wsid:
        return "revert_workspace error: 'workspace' is required."
    mode = (inp.get("mode") or "undo").strip().lower()
    if mode == "reset":
        ws.reset_customization(wsid)
        return ("Reset %s back to its baseline. The state before the reset was "
                "snapshotted, so this is undoable." % wsid)
    if mode == "as_of":
        when = (inp.get("when") or "").strip()
        if not when:
            return "revert_workspace error: mode 'as_of' needs 'when'."
        doc, err = ws.restore_as_of(wsid, when)
        if err:
            return "I could not restore %s: %s" % (wsid, err)
        return "Restored %s to how it was at %s." % (wsid, when)
    if mode == "version":
        vid = (inp.get("version_id") or "").strip()
        if not vid:
            return "revert_workspace error: mode 'version' needs 'version_id'."
        doc = ws.revert_customization(wsid, vid)
        if doc is None:
            return "There is no version %s for %s." % (vid, wsid)
        return "Restored %s to version %s." % (wsid, vid)
    doc, err = ws.undo_last(wsid)
    if err:
        return "I could not undo the last change to %s: %s" % (wsid, err)
    return ("Undid the most recent change to %s. That undo is snapshotted too, "
            "so say the word if you want it back." % wsid)


def _tool_list_workspace_history(inp):
    from agent_friday.services import workspace_studio as ws
    wsid = ((inp or {}).get("workspace") or "").strip()
    if not wsid:
        return "list_workspace_history error: 'workspace' is required."
    return json.dumps(ws.history(wsid), default=str)[:2400]


def _tool_query_calendar(_inp):
    """Today's + tomorrow's events across every connected Google account.

    2026-08-13: rewired from the single-account bridge (which only ever
    surfaced the FIRST/primary connected account, and collapsed a real API
    error — e.g. the Calendar API not enabled in the GCP project — into the
    same generic 'needs connecting' message a genuinely-unlinked account
    would produce) to the multi-account store (services.google_accounts),
    which lists every account, loads/refreshes credentials per account, and
    reports per-account errors distinctly from 'not connected'."""
    try:
        from agent_friday.services import google_accounts as ga
    except Exception:
        return json.dumps({"connected": False, "events": [],
                           "note": _GOOGLE_NOT_CONNECTED_NOTE.format(
                               what="Google Calendar", reads="your calendar")})
    try:
        has_accounts = ga.has_accounts()
    except Exception:
        has_accounts = False
    if not has_accounts:
        return json.dumps({"connected": False, "events": [],
                           "store": "google_accounts (multi-account)",
                           "note": _GOOGLE_NOT_CONNECTED_NOTE.format(
                               what="Google Calendar", reads="your calendar")})
    try:
        result = ga.merged_calendar(days=2)
    except Exception as e:
        return json.dumps({"connected": True, "events": [],
                           "store": "google_accounts (multi-account)",
                           "note": f"Calendar fetch error: {e}"})
    accounts_status = _summarize_multi_account_errors(result)
    events = result.get("events") or []
    out = []
    for ev in events[:20]:
        out.append({
            "title": ev.get("title"),
            "start": ev.get("start_time"),
            "end": ev.get("end_time"),
            "location": ev.get("location") or "",
            "attendees": (ev.get("attendees") or [])[:6],
            "account": ev.get("account_label") or ev.get("account_email"),
        })
    payload = {
        "connected": True,  # accounts exist and are linked; see "accounts" for per-account detail
        "store": "google_accounts (multi-account)",
        "accounts": accounts_status,
        "count": len(out),
        "events": out,
    }
    errored = [a for a in accounts_status if a["status"] == "error"]
    if errored and not out:
        payload["note"] = (
            "Every connected account's live calendar fetch just failed — tell "
            "the user the SPECIFIC error(s) below, do not say Calendar 'needs "
            "connecting' (it's already connected): " +
            "; ".join(f"{a['label']}: {a['error']}" for a in errored)
        )
    return json.dumps(payload, default=str)


def _tool_search_email(inp):
    """Search recent Gmail across every connected Google account.

    2026-08-13: rewired to the multi-account store (services.google_accounts)
    when any account is connected — same reasoning as _tool_query_calendar:
    the old single-account path only ever saw the primary account and
    collapsed a real API error into a generic 'needs connecting'. When NO
    account is connected at all, this still falls back to the legacy
    _collect_messages() offline-cache path so a never-connected install
    keeps its existing (cache-based) behavior unchanged."""
    q = ((inp or {}).get('query') or '').strip()
    try:
        from agent_friday.services import google_accounts as ga
    except Exception:
        ga = None
    has_accounts = False
    if ga is not None:
        try:
            has_accounts = ga.has_accounts()
        except Exception:
            has_accounts = False

    if has_accounts:
        try:
            result = ga.merged_gmail(limit_per_account=15)
        except Exception as e:
            return json.dumps({"connected": True, "messages": [],
                               "store": "google_accounts (multi-account)",
                               "note": f"Email fetch error: {e}"})
        accounts_status = _summarize_multi_account_errors(result)
        cards = result.get("messages") or []
        ql = q.lower()
        hits = []
        for c in cards:
            blob = " ".join(str(c.get(k) or "") for k in
                            ("sender", "subject", "snippet")).lower()
            if not ql or ql in blob:
                hits.append({
                    "from": c.get("sender") or "",
                    "subject": c.get("subject") or "",
                    "snippet": (c.get("snippet") or "")[:160],
                    "unread": bool(c.get("unread")),
                    "when": c.get("timestamp") or "",
                    "account": c.get("account_label") or c.get("account_email"),
                })
        payload = {
            "connected": True,
            "store": "google_accounts (multi-account)",
            "accounts": accounts_status,
            "source": "gmail",
            "query": q,
            "count": len(hits),
            "messages": hits[:25],
        }
        errored = [a for a in accounts_status if a["status"] == "error"]
        if errored and not cards:
            payload["note"] = (
                "Every connected account's live Gmail fetch just failed — tell "
                "the user the SPECIFIC error(s) below, do not say Gmail 'needs "
                "connecting' (it's already connected): " +
                "; ".join(f"{a['label']}: {a['error']}" for a in errored)
            )
        return json.dumps(payload, default=str)

    # No account connected at all — preserve the legacy cache-fallback path.
    try:
        from agent_friday.services.calendar_engine import _collect_messages
    except Exception:
        try:
            from calendar_engine import _collect_messages  # type: ignore
        except Exception:
            return _GOOGLE_NOT_CONNECTED_NOTE.format(what="Gmail", reads="your email")
    try:
        cards, source = _collect_messages(limit=25)
    except Exception as e:
        return json.dumps({"connected": False, "messages": [], "note": f"Email fetch error: {e}"})
    if source == "empty" or not cards:
        return json.dumps({"connected": False, "messages": [], "integration": "gmail",
                           "note": _GOOGLE_NOT_CONNECTED_NOTE.format(what="Gmail", reads="your email")})
    ql = q.lower()
    hits = []
    for c in (cards or []):
        blob = " ".join(str(c.get(k) or "") for k in
                        ("sender", "from", "subject", "title", "snippet", "preview")).lower()
        if not ql or ql in blob:
            hits.append({
                "from": c.get("sender") or c.get("from") or "",
                "subject": c.get("subject") or c.get("title") or "",
                "snippet": (c.get("snippet") or c.get("preview") or "")[:160],
                "unread": bool(c.get("unread")),
                "when": c.get("timestamp") or c.get("date") or "",
            })
    return json.dumps({"connected": True, "source": source, "query": q,
                       "count": len(hits), "messages": hits[:25]}, default=str)


def _google_multi_account_tool(has_accounts_note_what, has_accounts_note_reads, fetch_fn, item_key):
    """Shared shape for the multi-account Google tools added 2026-08-13
    (search_drive/list_tasks/search_contacts): zero accounts -> the standard
    honest not-connected note; accounts exist -> per-account status, and if
    every account's live fetch failed, an explicit instruction to report the
    SPECIFIC error(s) rather than claim 'needs connecting'."""
    try:
        from agent_friday.services import google_accounts as ga
    except Exception:
        return json.dumps({"connected": False, item_key: [],
                           "note": _GOOGLE_NOT_CONNECTED_NOTE.format(
                               what=has_accounts_note_what, reads=has_accounts_note_reads)})
    try:
        has_accounts = ga.has_accounts()
    except Exception:
        has_accounts = False
    if not has_accounts:
        return json.dumps({"connected": False, item_key: [],
                           "store": "google_accounts (multi-account)",
                           "note": _GOOGLE_NOT_CONNECTED_NOTE.format(
                               what=has_accounts_note_what, reads=has_accounts_note_reads)})
    try:
        result = fetch_fn(ga)
    except Exception as e:
        return json.dumps({"connected": True, item_key: [],
                           "store": "google_accounts (multi-account)",
                           "note": f"{has_accounts_note_what} fetch error: {e}"})
    accounts_status = _summarize_multi_account_errors(result)
    items = result.get(item_key) or []
    payload = {
        "connected": True,
        "store": "google_accounts (multi-account)",
        "accounts": accounts_status,
        "count": len(items),
        item_key: items,
    }
    errored = [a for a in accounts_status if a["status"] == "error"]
    if errored and not items:
        payload["note"] = (
            f"Every connected account's live {has_accounts_note_what} fetch just "
            f"failed — tell the user the SPECIFIC error(s) below, do not say "
            f"{has_accounts_note_what} 'needs connecting' (it's already connected): " +
            "; ".join(f"{a['label']}: {a['error']}" for a in errored)
        )
    return json.dumps(payload, default=str)


def _tool_search_drive(inp):
    """Search Drive file/folder names across every connected Google account.
    2026-08-13, same multi-account/per-account-error pattern as query_calendar
    and search_email (see their docstrings for why)."""
    query = ((inp or {}).get('query') or '').strip()
    blob = _google_multi_account_tool(
        "Google Drive", "your files",
        lambda ga: ga.merged_drive_search(query=query, max_results=20),
        "files",
    )
    return blob


def _tool_read_doc(inp):
    """Read a Google Doc/Sheet by file id (from a prior search_drive hit)."""
    inp = inp or {}
    file_id = (inp.get('file_id') or '').strip()
    if not file_id:
        return json.dumps({"error": "file_id is required — get one from search_drive first."})
    mime_type = (inp.get('mime_type') or '').strip() or None
    account_id = (inp.get('account_id') or '').strip() or None
    try:
        from agent_friday.services import google_accounts as ga
    except Exception:
        return json.dumps({"connected": False,
                           "note": _GOOGLE_NOT_CONNECTED_NOTE.format(
                               what="Google Docs/Sheets", reads="your documents")})
    if not ga.has_accounts():
        return json.dumps({"connected": False,
                           "note": _GOOGLE_NOT_CONNECTED_NOTE.format(
                               what="Google Docs/Sheets", reads="your documents")})
    candidate_ids = [account_id] if account_id else [
        a["id"] for a in ga.list_accounts() if a.get("services", {}).get("docs", True)]
    if not candidate_ids:
        return json.dumps({"error": "No account has Docs/Sheets access enabled."})
    last_error = None
    for aid in candidate_ids:
        result = ga.read_doc_or_sheet(aid, file_id, mime_type=mime_type)
        if "error" not in result:
            result["account_id"] = aid
            return json.dumps(result, default=str)
        last_error = result["error"]
    return json.dumps({"error": last_error or "Doc/Sheet not readable by any connected account.",
                       "store": "google_accounts (multi-account)"})


def _tool_list_tasks(_inp):
    """List open Google Tasks across every connected Google account."""
    return _google_multi_account_tool(
        "Google Tasks", "your tasks",
        lambda ga: ga.merged_tasks(max_results=50),
        "tasks",
    )


def _tool_complete_task(inp):
    """Mark one Google Task completed in one specific account/tasklist.
    Never fans out — account_id/tasklist_id are required, unlike list_tasks."""
    inp = inp or {}
    try:
        from agent_friday.services import google_accounts as ga
    except Exception as e:
        return json.dumps({"error": f"google_accounts unavailable: {e}"})
    result = ga.complete_task(
        account_id=(inp.get('account_id') or '').strip(),
        tasklist_id=(inp.get('tasklist_id') or '').strip(),
        task_id=(inp.get('task_id') or '').strip(),
    )
    return json.dumps(result, default=str)


def _tool_create_task(inp):
    """Create a Google Task in one specific connected account."""
    inp = inp or {}
    try:
        from agent_friday.services import google_accounts as ga
    except Exception as e:
        return json.dumps({"error": f"google_accounts unavailable: {e}"})
    result = ga.create_task(
        account_id=(inp.get('account_id') or '').strip(),
        title=(inp.get('title') or '').strip(),
        tasklist_id=(inp.get('tasklist_id') or '').strip() or "@default",
        notes=(inp.get('notes') or '').strip(),
        due=(inp.get('due') or '').strip(),
    )
    return json.dumps(result, default=str)


def _tool_update_task(inp):
    """Patch a Google Task's fields in one specific account/tasklist."""
    inp = inp or {}
    try:
        from agent_friday.services import google_accounts as ga
    except Exception as e:
        return json.dumps({"error": f"google_accounts unavailable: {e}"})
    result = ga.update_task(
        account_id=(inp.get('account_id') or '').strip(),
        tasklist_id=(inp.get('tasklist_id') or '').strip(),
        task_id=(inp.get('task_id') or '').strip(),
        title=(inp.get('title') or '').strip() or None,
        notes=inp.get('notes') if inp.get('notes') is not None else None,
        due=(inp.get('due') or '').strip() or None,
        status=(inp.get('status') or '').strip() or None,
    )
    return json.dumps(result, default=str)


def _tool_delete_task(inp):
    """Permanently delete a Google Task from one specific account/tasklist."""
    inp = inp or {}
    try:
        from agent_friday.services import google_accounts as ga
    except Exception as e:
        return json.dumps({"error": f"google_accounts unavailable: {e}"})
    result = ga.delete_task(
        account_id=(inp.get('account_id') or '').strip(),
        tasklist_id=(inp.get('tasklist_id') or '').strip(),
        task_id=(inp.get('task_id') or '').strip(),
    )
    return json.dumps(result, default=str)


def _tool_search_contacts(inp):
    """Search Google Contacts across every connected Google account."""
    query = ((inp or {}).get('query') or '').strip()
    return _google_multi_account_tool(
        "Google Contacts", "your contacts",
        lambda ga: ga.search_contacts(query=query, max_results=15),
        "contacts",
    )


def _tool_read_wiki(inp):
    raw = (inp or {}).get('path', '')
    p = (WIKI_DIR / raw).resolve()
    wiki_resolved = WIKI_DIR.resolve()
    try:
        p.relative_to(wiki_resolved)
    except ValueError:
        return f"Path escapes the wiki root: {raw}"
    if not p.exists() or not p.is_file():
        return f"Wiki file not found: {raw}"
    try:
        text = wiki_read_text(p)
        return text[:200_000] + ("\n...[truncated]" if len(text) > 200_000 else "")
    except Exception as e:
        return f"Read error: {e}"


def _tool_search_wiki(inp):
    """Keyword-search the wiki and return up to N hits with excerpts."""
    inp = inp or {}
    query = (inp.get('query') or '').strip()
    if not query:
        return "search_wiki error: 'query' is required."
    try:
        limit = int(inp.get('limit') or 5)
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(20, limit))
    q_low = query.lower()

    results = []
    for root, label in [(WIKI_DIR, 'wiki'), (FRIDAY_DIR / 'wiki', 'friday-wiki')]:
        if not root.exists():
            continue
        for f in root.rglob('*'):
            if len(results) >= limit:
                break
            if not f.is_file() or f.suffix not in ('.md', '.txt'):
                continue
            try:
                content = wiki_read_text(f)
            except Exception:
                continue
            name_match = q_low in f.stem.lower()
            idx = content.lower().find(q_low)
            if not name_match and idx < 0:
                continue
            if idx < 0:
                excerpt = content[:400]
            else:
                start = max(0, idx - 120)
                end = min(len(content), idx + 280)
                excerpt = content[start:end]
            try:
                rel = str(f.relative_to(root)).replace('\\', '/')
            except ValueError:
                rel = str(f)
            results.append({
                'root': label,
                'path': rel,
                'excerpt': excerpt.strip(),
            })
        if len(results) >= limit:
            break

    if not results:
        return f"No wiki files matched {query!r}."
    return json.dumps({'query': query, 'hits': results}, default=str)[:100_000]


def _tool_search_news(inp):
    """Search the live news feed for stories matching a query.

    Pulls the current multi-category feed (the same one the News workspace
    shows) and ranks items whose title/snippet/source contain the query terms.
    Returns up to N hits as JSON; with no query, returns the top current
    stories. Used by the agent loop on every provider.
    """
    inp = inp or {}
    query = (inp.get('query') or '').strip()
    try:
        limit = int(inp.get('limit') or 8)
    except (TypeError, ValueError):
        limit = 8
    limit = max(1, min(25, limit))

    try:
        pool = _fetch_news_items(limit_per=8)
    except Exception as e:
        return f"search_news error fetching feed: {e}"

    terms = [t for t in re.split(r'\s+', query.lower()) if t]
    hits = []
    for it in pool:
        hay = f"{it.get('title','')} {it.get('snippet','')} {it.get('source','')}".lower()
        # No query → surface everything (ranked by the feed's own score);
        # with a query, require every term to appear somewhere in the item.
        if terms and not all(t in hay for t in terms):
            continue
        hits.append({
            'title': it.get('title', ''),
            'snippet': it.get('snippet', ''),
            'url': it.get('url', ''),
            'source': it.get('source', ''),
            'category': it.get('category', ''),
            'trust': it.get('trust_rating') or it.get('trust'),
            'breaking': it.get('breaking', False),
        })
        if len(hits) >= limit:
            break

    if not hits:
        return f"No current news stories matched {query!r}." if query else "No news stories available right now."
    return json.dumps({'query': query, 'hits': hits}, default=str)[:100_000]


def _tool_run_command(inp):
    cmd = ((inp or {}).get('command') or '').strip()
    if not cmd:
        return "Empty command."
    low = cmd.lower()
    for bad in _RUN_COMMAND_BLOCKLIST:
        if bad in low:
            return f"Blocked by cLaws safety: command matches blocklist token {bad!r}."
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, timeout=300,
            creationflags=_POPEN_FLAGS,
        )
        out = (proc.stdout or '') + (("\n[stderr]\n" + proc.stderr) if proc.stderr else '')
        return out[:100_000] if out else f"(exit {proc.returncode}, no output)"
    except subprocess.TimeoutExpired:
        return "Command timed out after 300s."
    except Exception as e:
        return f"Command error: {e}"


# ── URL validation (guards against malformed / hallucinated links) ──────────
# The model sometimes hands open_url a URL it invented from memory rather than
# one that came from real data (an RSS feed entry, a source-trust record). Those
# invented links — especially YouTube watch URLs with a made-up video id — are
# frequently dead. We validate format + a YouTube id sanity check + a best-effort
# reachability probe BEFORE opening, and refuse rather than launch a dead page.
_YT_HOSTS = {'youtube.com', 'www.youtube.com', 'm.youtube.com',
             'music.youtube.com', 'youtu.be'}
# A YouTube video id is EXACTLY 11 chars from [A-Za-z0-9_-].
_YT_ID_RE = re.compile(r'^[A-Za-z0-9_-]{11}$')


def _extract_youtube_id(parsed):
    """Given a urlparse() result, return the video id for a single-video YouTube
    URL, or '' when the URL is not a recognised single-video link (a non-YouTube
    host, or a channel/playlist/search page). A non-empty return is the candidate
    id the caller validates against _YT_ID_RE."""
    host = (parsed.hostname or '').lower()
    if host not in _YT_HOSTS:
        return ''  # not YouTube — skip the id check entirely
    from urllib.parse import parse_qs
    path = parsed.path or ''
    parts = [p for p in path.split('/') if p]
    if host == 'youtu.be':
        return parts[0] if parts else ''
    if path == '/watch':
        return (parse_qs(parsed.query).get('v') or [''])[0]
    if parts and parts[0] in ('embed', 'shorts', 'v') and len(parts) > 1:
        return parts[1]
    return ''  # channel / playlist / search / home — nothing to validate


def _url_head_ok(url):
    """Best-effort reachability probe. Returns (False, reason) ONLY on a definite
    dead-link signal (HTTP 404/410); every other outcome — offline, timeouts,
    connection errors, 401/403/405, HEAD-hostile servers — returns (True, ...) so
    a perfectly good link is never blocked just because we couldn't confirm it."""
    try:
        if _network_is_offline():
            return True, "offline — reachability skipped"
    except Exception:
        pass
    try:
        import requests as _req
        _hdrs = {'User-Agent': 'Mozilla/5.0 FridayAgent/1.0'}
        resp = _req.head(url, timeout=6, allow_redirects=True, headers=_hdrs)
        if resp.status_code in (404, 410):
            return False, f"the page returned HTTP {resp.status_code}"
        if resp.status_code == 405:
            # Some servers reject HEAD — confirm with a 1-byte ranged GET.
            g = _req.get(url, timeout=6, allow_redirects=True, stream=True,
                         headers={**_hdrs, 'Range': 'bytes=0-0'})
            code = g.status_code
            g.close()
            if code in (404, 410):
                return False, f"the page returned HTTP {code}"
        return True, "reachable"
    except Exception:
        return True, "reachability unknown (allowed)"


def _validate_url(url, *, check_reachable=True):
    """Validate a URL before opening it. Returns (ok: bool, reason: str).

    Checks, in order: (1) http/https scheme, (2) a real-looking host, (3) a
    well-formed 11-char id on single-video YouTube links, (4) best-effort
    reachability (never blocks when offline / on HEAD-hostile sites)."""
    from urllib.parse import urlparse
    raw = (url or '').strip()
    if not raw:
        return False, "no URL was provided"
    try:
        p = urlparse(raw)
    except Exception as e:
        return False, f"it could not be parsed ({e})"
    if p.scheme not in ('http', 'https'):
        return False, f"it must start with http:// or https:// (got {p.scheme or 'none'!r})"
    host = (p.hostname or '').lower()
    if not host:
        return False, "it has no domain"
    if host != 'localhost' and '.' not in host:
        return False, f"the domain looks malformed ({host!r})"
    yt = _extract_youtube_id(p)
    if yt and not _YT_ID_RE.match(yt):
        return False, f"the YouTube video id is malformed ({yt!r} — expected 11 characters)"
    if check_reachable:
        ok, reason = _url_head_ok(raw)
        if not ok:
            return False, reason
    return True, "ok"


def _open_url_in_browser(url):
    """Actually open `url` in the user's browser (Chrome preferred, falls
    back to the OS default). Shared by _tool_open_url's direct path and the
    google-oauth-connect approval hook below — one place that touches the OS."""
    try:
        chrome_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
        for cp in chrome_paths:
            if Path(cp).exists():
                subprocess.Popen([cp, url])
                return f"Opened in Chrome: {url}"
        os.startfile(url)  # type: ignore[attr-defined]
        return f"Opened in default browser: {url}"
    except Exception as e:
        return f"Open URL error: {e}"


# FR-6 (toolcall-integrity-v5): connecting Google is a one-time OAuth
# authorization, not a routine "open a link" — route it through Phase A3's
# human-gate primitive (services/approvals.py) instead of opening the
# consent screen unattended. force_gate=True means it is ALWAYS gated
# (never auto-approved), regardless of the default approvals_policy table.
_GOOGLE_OAUTH_URL_MARKERS = ('/api/google/auth', '/api/google/accounts/connect')


def _is_google_oauth_url(url):
    return any(m in url for m in _GOOGLE_OAUTH_URL_MARKERS)


def _resume_google_oauth_open(record):
    """Decision-hook callback: fires when the "connect google" approval card
    is decided. Only acts on approval — a denial or expiry does nothing."""
    if record.get("status") != "approved":
        return
    url = (record.get("payload") or {}).get("url")
    if url:
        _open_url_in_browser(url)


try:
    from agent_friday.services import approvals as _approvals_for_oauth
    _approvals_for_oauth.register_decision_hook(
        "connector_auth", _resume_google_oauth_open)
except Exception:
    pass


def _looks_like_local_path(value):
    """Return a usable local path if `value` names one, else None.

    Deliberately conservative: a file:// URL, a Windows drive path, a UNC path,
    or a ~/ path. Bare relative strings are NOT treated as paths, because a
    model that mistypes a domain must not have it silently reinterpreted as a
    filename.
    """
    v = (value or '').strip().strip('"').strip("'")
    if not v:
        return None
    if v.lower().startswith('file:///'):
        from urllib.parse import unquote
        return unquote(v[8:]).replace('/', os.sep)
    if v.lower().startswith('file://'):
        from urllib.parse import unquote
        return unquote(v[7:])
    if v.startswith('~'):
        return v
    if v.startswith('\\\\'):          # UNC \\server\\share
        return v
    if len(v) > 2 and v[1] == ':' and v[2] in ('\\', '/'):
        return v
    return None


def _tool_open_url(inp):
    url = ((inp or {}).get('url') or '').strip()
    if not (url.startswith('http://') or url.startswith('https://')):
        # A LOCAL path is not a bad URL, it is the wrong tool. Friday writes a
        # briefing to disk and is then asked to show it; open_url refused
        # (correctly -- it is a web tool) and the model, with no other option
        # on the voice surface, invented a placeholder http:// URL and opened
        # that instead. Generating a document and showing it to the user is one
        # action, so name the tool that completes it rather than stopping at a
        # refusal. open_path(path, in_browser=true) is the browser-tab form.
        _local = _looks_like_local_path(url)
        if _local:
            return (f"open_url is for web pages only, and {url!r} is a local "
                    f"path -- so nothing was opened. Call open_path with "
                    f"path={_local!r} instead (add in_browser=true for a "
                    f"browser tab). Do NOT substitute a made-up http:// URL: "
                    f"that opens the wrong thing and reports success.")
        return (f"Refusing to open non-http(s) URL: {url!r} -- nothing was "
                f"opened. Do not report that you opened it.")
    ok, why = _validate_url(url)
    if not ok:
        return (f"I did NOT open that link — it appears invalid because {why}. "
                f"This often means the URL was guessed rather than taken from real "
                f"data. Tell the user the link looks broken and offer to search for "
                f"the correct source instead. URL: {url!r}")
    if _is_google_oauth_url(url):
        try:
            from agent_friday.services import approvals as _appr
            result = _appr.gate_action(
                kind="connector_auth", subject_type="connector", subject_id="google",
                title="Connect Google (Calendar + Gmail, read-only)",
                action_description=(
                    "Open Google's OAuth consent screen to link Calendar "
                    "(read-only) and Gmail (read-only) to Friday. One-time "
                    "authorization. Friday never requests the gmail.send "
                    "scope — it cannot send email on your behalf."
                ),
                force_gate=True, payload={"url": url},
            )
        except Exception as e:
            return f"Couldn't start the Google connection approval flow: {e}"
        status = result.get("status")
        if status in ("auto_approved", "approved"):
            return _open_url_in_browser(url)
        if status == "denied":
            return "Connecting Google was declined — not opening the authorization page."
        return (
            "I've sent an approval request to connect Google (Calendar + Gmail, "
            "read-only) — approve it from Settings > Approvals (or the push "
            "notification) and I'll open the authorization page right after."
        )
    return _open_url_in_browser(url)


# ── Open local file / folder / app (computer control, low-risk) ──
# Parallels open_url: reveals or opens a target, never writes or deletes. Works
# WITHOUT the cloud tool-loop or an API key, so it functions on a local-only
# (Ollama) install — which is why a deterministic intent handler (below) calls
# straight into it from /api/chat instead of relying on the model to tool-call.
# Apps launchable by a bare executable on PATH / System32.
_OPEN_APPS = {
    'notepad': 'notepad', 'calculator': 'calc', 'calc': 'calc', 'paint': 'mspaint',
    'file explorer': 'explorer', 'explorer': 'explorer', 'windows explorer': 'explorer',
    'task manager': 'taskmgr', 'taskmgr': 'taskmgr', 'snipping tool': 'snippingtool',
}

# Apps best launched through the Windows shell ("start"), which consults the
# App Paths registry — covers browsers and Office/desktop apps that aren't on
# PATH. Keep keys free of workspace-alias collisions (e.g. no 'code'/'settings');
# navigate-intent runs first for those and wins.
_OPEN_SHELL_APPS = {
    'chrome': 'chrome', 'google chrome': 'chrome',
    'edge': 'msedge', 'microsoft edge': 'msedge',
    'firefox': 'firefox', 'mozilla firefox': 'firefox',
    'brave': 'brave', 'brave browser': 'brave',
    'word': 'winword', 'microsoft word': 'winword', 'ms word': 'winword',
    'excel': 'excel', 'microsoft excel': 'excel',
    'powerpoint': 'powerpnt', 'outlook': 'outlook',
    'spotify': 'spotify', 'discord': 'discord', 'slack': 'slack',
}


def _open_app(name):
    """Launch a known GUI app by friendly name. Returns a confirmation string, or
    None if the name isn't a recognized app."""
    if sys.platform != 'win32':
        return None
    key = re.sub(r'\s+', ' ', (name or '').lower().strip())
    exe = _OPEN_APPS.get(key)
    if exe:
        try:
            subprocess.Popen([exe])
            return f"Done — I launched **{name.strip()}** for you."
        except Exception as e:
            return f"I tried to launch {name.strip()} but hit an error: {e}"
    shell_exe = _OPEN_SHELL_APPS.get(key)
    if shell_exe:
        try:
            # `start "" <exe>` resolves the App Paths registry (browsers, Office)
            # without needing the full install path.
            subprocess.Popen(['cmd', '/c', 'start', '', shell_exe])
            return f"Done — I launched **{name.strip()}** for you."
        except Exception as e:
            return f"I tried to launch {name.strip()} but hit an error: {e}"
    return None


def _resolve_open_target(target):
    """Resolve a friendly folder name, alias, or path to an existing filesystem
    path string. Returns None if nothing concrete matches (so the caller can fall
    through to the model instead of guessing)."""
    if not target:
        return None
    raw = target.strip().strip('"').strip("'")
    low = re.sub(r'\s+', ' ', raw.lower()).strip()
    low = re.sub(r'\s+(folder|directory|dir|file)$', '', low).strip()
    repo = Path(__file__).resolve().parents[3]  # agent.py is src/agent_friday/services/ → repo root
    aliases = {
        'downloads': HOME / 'Downloads', 'download': HOME / 'Downloads',
        'documents': HOME / 'Documents', 'docs': HOME / 'Documents',
        'desktop': HOME / 'Desktop', 'pictures': HOME / 'Pictures', 'photos': HOME / 'Pictures',
        'music': HOME / 'Music', 'videos': HOME / 'Videos', 'video': HOME / 'Videos',
        'home': HOME, 'user': HOME, 'user profile': HOME, 'home folder': HOME,
        'projects': HOME / 'Projects', 'project': HOME / 'Projects',
        'creations': CREATIONS_DIR, 'friday creations': CREATIONS_DIR, 'gallery': CREATIONS_DIR,
        'wiki': HOME / 'wiki',
        'friday': repo, 'friday desktop': repo, 'friday folder': repo,
        'this': repo, 'this folder': repo, 'current folder': repo,
    }
    if low in aliases:
        p = aliases[low]
        if p and p.exists():
            return str(p)
    # Explicit path (contains a separator, ~, or a drive letter).
    if re.search(r'[\\/]', raw) or raw.startswith('~') or re.match(r'^[a-zA-Z]:', raw):
        try:
            p = Path(raw).expanduser()
            if p.exists():
                return str(p.resolve())
        except Exception:
            pass
    # A bare name, looked for where Friday's own output and the user's files
    # actually live.
    #
    # This only checked HOME, so `open_path("friday_local_00005_.png")` resolved
    # to <HOME>/friday_local_00005_.png, which does not exist, and the
    # tool answered "couldn't find anything matching". The file was in
    # CREATIONS_DIR — Friday had generated it there minutes earlier and named it
    # correctly. She would have failed at this even if she HAD called the tool
    # instead of promising to.
    #
    # Creations first, because a bare filename in conversation is nearly always
    # something she just produced. Bounded to a handful of known directories:
    # no recursive walk, no guessing at partial names.
    for base in (CREATIONS_DIR, HOME / 'Desktop', HOME / 'Downloads',
                 HOME / 'Documents', HOME / 'Pictures', HOME):
        try:
            cand = base / raw
            if cand.exists():
                return str(cand.resolve())
        except Exception:
            continue
    # Same filename, different extension or trailing underscore — ComfyUI names
    # files friday_local_00005_.png and a model quoting it back may drop the
    # dot-extension. Exact stem match only; never a fuzzy guess.
    try:
        stem = Path(raw).stem.rstrip('_')
        if stem and len(stem) >= 6 and CREATIONS_DIR.exists():
            for f in CREATIONS_DIR.iterdir():
                if f.is_file() and f.stem.rstrip('_') == stem:
                    return str(f.resolve())
    except Exception:
        pass
    return None


def _browser_command():
    """The user's browser, preferring Chrome. Returns an argv prefix or None.

    Stephen asked specifically for images "in their own Chrome tab", which
    `os.startfile` cannot do — that hands the file to whatever app owns .png
    (Photos on Windows) and there is no way to say 'in a browser instead'.
    """
    if sys.platform == 'win32':
        for p in (
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
            / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
            / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "Google/Chrome/Application/chrome.exe",
        ):
            try:
                if p.is_file():
                    return [str(p)]
            except Exception:
                continue
    elif sys.platform == 'darwin':
        return ["open", "-a", "Google Chrome"]
    else:
        for exe in ("google-chrome", "chromium", "chromium-browser"):
            if shutil.which(exe):
                return [exe]
    return None


def _perform_open(target, in_browser=False):
    """Open an app or a resolved path. Returns a human-facing confirmation, or
    None if nothing concrete could be resolved.

    `in_browser` opens the file as a file:// URL in a browser tab (Chrome when
    it is installed) instead of handing it to the OS default application.
    """
    if not target:
        return "open_path error: no path/target provided."
    app = _open_app(target)
    if app is not None:
        return app
    resolved = _resolve_open_target(target)
    if not resolved:
        return None
    if in_browser:
        try:
            url = Path(resolved).resolve().as_uri()
        except Exception:
            url = "file:///" + str(resolved).replace("\\", "/")
        cmd = _browser_command()
        try:
            if cmd:
                subprocess.Popen(cmd + [url])
                where = "a Chrome tab"
            else:
                import webbrowser
                if not webbrowser.open(url):
                    raise RuntimeError("no browser could be launched")
                where = "your browser"
        except Exception as e:
            return (f"I tried to open {resolved} in a browser tab but hit an "
                    f"error: {e}")
        return (f"Done — I opened **{Path(resolved).name}** in {where}."
                f"\n\n`{url}`")
    try:
        if sys.platform == 'win32':
            os.startfile(resolved)  # type: ignore[attr-defined]
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', resolved])
        else:
            subprocess.Popen(['xdg-open', resolved])
    except Exception as e:
        return f"I tried to open {resolved} but hit an error: {e}"
    name = Path(resolved).name or resolved
    return f"Done — I opened **{name}** for you.\n\n`{resolved}`"


def _tool_open_path(inp):
    inp = inp or {}
    target = (inp.get('path') or inp.get('target') or '').strip()
    in_browser = bool(inp.get('in_browser'))
    result = _perform_open(target, in_browser=in_browser)
    if result is None:
        return f"Couldn't find anything matching {target!r} to open."
    return result


# Verb patterns that signal an "open this on my computer" request.
_OPEN_VERB_RE = re.compile(
    r'^\s*(?:can you |could you |would you |will you |please |hey |ok |okay |yo |'
    r'friday[,:\s]+)*'
    r'(open up|open|launch|reveal|show me|show|bring up|pull up|take me to|'
    r'switch to|switch|go to|jump to|navigate to)\s+'
    r'(.+?)[\s?.!]*$',
    re.IGNORECASE,
)


def _maybe_handle_open_intent(message):
    """If `message` is a clear request to open a local file/folder/app AND the
    target resolves to something real, perform it and return a confirmation
    string. Otherwise return None so the normal chat pipeline handles it.

    Deliberately conservative: only fires when the target actually resolves, so
    phrases like 'open the news' or 'show me my calendar' fall through to the
    model rather than being hijacked."""
    if not message:
        return None
    m = _OPEN_VERB_RE.match(message.strip())
    if not m:
        return None
    target = m.group(2).strip()
    target = re.sub(r'^(the|my|a|an|up|to|that|this)\s+', '', target, flags=re.IGNORECASE).strip()
    if not target or re.match(r'^https?://', target, re.IGNORECASE):
        return None  # URLs are handled by the browser / open_url path
    return _perform_open(target)


# ── Friday UI workspace navigation (in-app deep-link targets) ──
# Mirror of the dock workspace ids in ui_parts/app.html (DOCK_GROUPS → WS /
# wsMap). Maps each canonical id plus the names a user actually speaks to the id
# the frontend's window.fridayOpenWorkspace() understands. This is what turns a
# chat turn like "open the studio" or "switch to news" into a REAL on-screen
# navigation (a structured action the client executes) instead of text that only
# claims it will. Keep keys lowercase and singular-ish; the resolver normalizes.
_WORKSPACE_ALIASES = {
    'home': 'home', 'dashboard': 'home', 'overview': 'home',
    'main': 'home', 'start': 'home', 'launchpad': 'home',
    'career': 'career', 'jobs': 'career', 'job search': 'career',
    'job pipeline': 'career', 'careers': 'career', 'job': 'career', 'work': 'career',
    'wiki': 'wiki', 'notes': 'wiki', 'knowledge': 'wiki', 'knowledge base': 'wiki',
    'knowledgebase': 'wiki', 'second brain': 'wiki',
    'studio': 'studio', 'creations': 'studio', 'gallery': 'studio',
    'create': 'studio', 'art': 'studio', 'creative': 'studio',
    'trust': 'trust', 'trust graph': 'trust', 'reputation': 'trust', 'trust score': 'trust',
    'system': 'system', 'system health': 'system', 'health check': 'system',
    # Settings is its OWN workspace (dock id 'settings') — for months this
    # table sent "open settings" to the System workspace and Friday looked
    # like she didn't know her own UI.
    'settings': 'settings', 'setting': 'settings', 'settings menu': 'settings',
    'system settings': 'settings', 'preferences': 'settings',
    'options': 'settings', 'config': 'settings', 'configuration': 'settings',
    'workflows': 'workflows', 'workflow': 'workflows',
    'scheduled tasks': 'workflows', 'schedules': 'workflows',
    'pipelines': 'workflows', 'automations': 'workflows',
    'marketplace': 'marketplace', 'market': 'marketplace',
    'store': 'marketplace', 'shop': 'marketplace', 'skill store': 'marketplace',
    'news': 'news', 'headlines': 'news', 'feed': 'news', 'newsfeed': 'news',
    'front page': 'news', 'frontpage': 'news', 'top stories': 'news',
    'breaking news': 'news', 'newspaper': 'news', 'the news': 'news',
    'draft': 'draft', 'drafts': 'draft', 'writing': 'draft', 'writer': 'draft',
    'code': 'code', 'coding': 'code', 'editor': 'code', 'ide': 'code',
    'code editor': 'code',
    'finance': 'finance', 'money': 'finance', 'budget': 'finance', 'finances': 'finance',
    'banking': 'finance', 'accounts': 'finance', 'spending': 'finance',
    'health': 'health', 'wellness': 'health', 'fitness': 'health', 'medical': 'health',
    'contacts': 'contacts', 'people': 'contacts', 'people graph': 'contacts',
    'address book': 'contacts', 'relationships': 'contacts',
    'content': 'content', 'content studio': 'content',
    'messages': 'messages', 'inbox': 'messages', 'dms': 'messages',
    'chats': 'messages', 'texts': 'messages', 'messaging': 'messages',
    'calendar': 'calendar', 'schedule': 'calendar', 'agenda': 'calendar',
    'events': 'calendar', 'cal': 'calendar',
    'family': 'family', 'household': 'family',
    'futurespeak': 'futurespeak', 'sites': 'futurespeak', 'future speak': 'futurespeak',
    'website': 'futurespeak', 'websites': 'futurespeak', 'web': 'futurespeak',
}

# Display labels for the confirmation message (a few don't title-case cleanly).
_WORKSPACE_LABELS = {
    'home': 'Home', 'career': 'Career', 'wiki': 'Wiki', 'studio': 'Studio',
    'trust': 'Trust', 'system': 'System', 'news': 'News', 'draft': 'Draft',
    'code': 'Code', 'finance': 'Finance', 'health': 'Health',
    'contacts': 'Contacts', 'content': 'Content', 'messages': 'Messages',
    'calendar': 'Calendar', 'family': 'Family',
    'futurespeak': 'FutureSpeak', 'settings': 'Settings',
    'marketplace': 'Marketplace', 'workflows': 'Workflows',
}


def _resolve_workspace(name):
    """Resolve a spoken workspace name/alias to a canonical workspace id the UI
    knows, or None if nothing matches (so the caller falls through to the model
    instead of guessing). Strips trailing 'workspace/tab/panel/...' and a leading
    'the/my'."""
    if not name:
        return None
    low = re.sub(r'\s+', ' ', str(name).lower()).strip().strip('"').strip("'")
    low = re.sub(r'^(the|my|a|an)\s+', '', low).strip()
    # Try the full phrase first so a legitimate multi-word alias ("front page",
    # "people graph", "trust score") isn't destroyed by the trailing-noise
    # stripper below — "page" would otherwise turn "front page" into "front".
    hit = _WORKSPACE_ALIASES.get(low)
    if hit:
        return hit
    # Fall back to stripping a trailing UI-noise word: "news tab" → "news".
    stripped = re.sub(r'\s+(workspace|tab|panel|page|screen|view|window|section|menu)$', '', low).strip()
    return _WORKSPACE_ALIASES.get(stripped)


def _maybe_handle_navigate_intent(message):
    """If `message` is a request to open/switch-to a Friday UI workspace AND the
    target resolves to a known workspace, return (reply_text, workspace_id).
    Otherwise None so the normal chat pipeline handles it.

    Reuses the same verb grammar as the OS open-intent handler. It is meant to
    run AFTER _maybe_handle_open_intent, so a real folder/app ('open Downloads')
    still wins and only an unmatched name ('open Studio', 'switch to news') is
    treated as UI navigation."""
    if not message:
        return None
    m = _OPEN_VERB_RE.match(message.strip())
    if not m:
        return None
    target = m.group(2).strip()
    target = re.sub(r'^(the|my|a|an|up|to|that|this)\s+', '', target, flags=re.IGNORECASE).strip()
    ws = _resolve_workspace(target)
    if not ws:
        return None
    label = _WORKSPACE_LABELS.get(ws, ws.title())
    return (f"Opening the **{label}** workspace for you.", ws)


def _voice_actions_for(user_text):
    """Map a voice turn's user transcript to executable actions, mirroring the
    /api/chat deterministic dispatch so voice is as agentic as text.

    - A known workspace ("open studio", "switch to news") → a {navigate} action
      the browser executes via fridayRunActions (UI moves are client-side).
    - A real folder/app/file ("open downloads", "open chrome") is opened here on
      the machine (same host as the browser) and needs no client action.

    Mirrors the /api/chat ordering: navigate wins over OS-open so a curated
    workspace name beats a same-named home folder. Returns a list of client-side
    actions (possibly empty). Never raises.

    Fallback safety net for News Anchor Mode: when the Live model's own function
    calling isn't available, "open that story / open it / show me the source"
    maps to an {open_last_source} action the browser resolves against the last
    citation chip it surfaced — so "open that one" still works deterministically."""
    if not user_text:
        return []
    try:
        nav = _maybe_handle_navigate_intent(user_text)
    except Exception:
        nav = None
    if nav is not None:
        return [{"type": "navigate", "workspace": nav[1]}]
    # News-anchor deterministic fallback: "open that article / open it / show me
    # the source / open the link". Deliberately narrow — an explicit open verb
    # plus a story/source referent — so normal speech doesn't trip it. The
    # browser opens the most recent source it rendered (no URL is known here).
    try:
        _t = user_text.lower()
        if (re.search(r"\b(open|show|pull up|bring up|go to)\b", _t)
                and re.search(r"\b(that|this|the|it)\b", _t)
                and re.search(r"\b(story|article|source|link|piece|one|page)\b", _t)):
            return [{"type": "open_last_source"}]
    except Exception:
        pass
    try:
        # Performs the open server-side (os.startfile / launch) as a side effect.
        _maybe_handle_open_intent(user_text)
    except Exception:
        pass
    return []


def _tool_navigate(inp):
    """Tool handler: switch the Friday UI to a workspace. The actual on-screen
    move happens client-side — the chat endpoint reads this from the tool trace
    and returns a structured action. We encode the resolved id as `NAV_OK:<id>`
    so the model gets a clear, machine-readable confirmation."""
    raw = ((inp or {}).get('workspace') or (inp or {}).get('target')
           or (inp or {}).get('name') or '').strip()
    ws = _resolve_workspace(raw)
    if not ws:
        return (f"NAV_FAIL: {raw!r} isn't a known workspace. Valid: "
                + ", ".join(sorted(set(_WORKSPACE_ALIASES.values()))))
    label = _WORKSPACE_LABELS.get(ws, ws.title())
    return f"NAV_OK:{ws} — Opening the {label} workspace for the user now."


def _tool_switch_model(inp):
    """Change the chat model seat by name.

    Stephen, 2026-08-18: "I asked twice for Gemma4:12B Uncensored and it didn't
    know how to switch." There was no conversational path to a seat change at
    all — only the UI controls — so the most natural way to ask was the one way
    that could not work.

    Writes `capability_routing.reasoning`, which is what dispatch reads, and
    verifies the write by reading it back. Matching is forgiving because he
    types what he means, not model ids: "gemma4 12b uncensored" has to find
    hf.co/HauhauCS/Gemma4-12B-QAT-Uncensored-HauhauCS-Balanced:Q4_K_M.
    """
    want = ((inp or {}).get('model') or (inp or {}).get('name') or '').strip()
    if not want:
        return "SWITCH_FAIL: no model named."

    try:
        from agent_friday.core import _load_settings, _save_settings
        from agent_friday.services.model_catalog import build_catalog
    except Exception as e:
        return f"SWITCH_FAIL: cannot reach the catalogue ({e})."

    try:
        cat = build_catalog()
        ids = []
        seen = set()
        for m in (cat.get('models') or []):
            mid = m.get('id')
            if mid and mid not in seen:
                seen.add(mid)
                ids.append((mid, m.get('label') or mid,
                            bool(m.get('local')) or m.get('classification') == 'local',
                            (m.get('providers') or [m.get('provider')])[0]
                            if m.get('providers') else m.get('provider')))
    except Exception as e:
        return f"SWITCH_FAIL: cannot read the catalogue ({e})."

    def norm(x):
        return ''.join(ch for ch in str(x).lower() if ch.isalnum())

    nw = norm(want)
    # Exact id, then id/label containment, then all-tokens-present. First match
    # in catalogue order wins, and local models sort ahead of cloud ones.
    # Local first, then first-party ids over aggregator ones: "Sonnet 5" should
    # find claude-sonnet-5 on Anthropic, not anthropic/claude-sonnet-5 on an
    # OpenRouter account he has no key for.
    ranked = sorted(ids, key=lambda t: (not t[2], '/' in t[0], t[0]))
    hit = None
    for mid, label, is_local, prov in ranked:
        if norm(mid) == nw or norm(label) == nw:
            hit = (mid, label, prov)
            break
    if hit is None:
        for mid, label, is_local, prov in ranked:
            if nw and (nw in norm(mid) or nw in norm(label)):
                hit = (mid, label, prov)
                break
    if hit is None:
        toks = [t for t in ''.join(
            c if c.isalnum() else ' ' for c in want.lower()).split() if t]
        for mid, label, is_local, prov in ranked:
            blob = norm(mid) + ' ' + norm(label)
            if toks and all(norm(t) in blob for t in toks):
                hit = (mid, label, prov)
                break
    if hit is None:
        locals_ = [m for m, l, loc, pr in ranked if loc][:8]
        return ("SWITCH_FAIL: nothing in the catalogue matches %r. "
                "Local models installed: %s" % (want, ", ".join(locals_) or "none"))

    mid, label, prov = hit
    try:
        cur = _load_settings() or {}
        routing = dict(cur.get('capability_routing') or {})
        routing['reasoning'] = {'model': mid, 'provider': prov or 'ollama-local'}
        _save_settings({'capability_routing': routing, 'orchestrator_model': mid})
        after = ((( _load_settings() or {}).get('capability_routing') or {})
                 .get('reasoning') or {}).get('model')
    except Exception as e:
        return f"SWITCH_FAIL: could not save the seat ({e})."

    if after != mid:
        return (f"SWITCH_FAIL: the save did not take — the seat still reads "
                f"{after!r}. Nothing was changed.")
    return (f"SWITCH_OK:{mid} — the chat seat is now {label}. It takes effect on "
            f"the next message; if it is cold, the first reply waits for it to load.")


def _tool_draft_email(inp):
    """Compose an email. The native Google integration is READ-ONLY, so composing
    needs a write-enabled Gmail connection (the gmail-mcp connector can send once
    authenticated). Report accurately and offer setup — never 'not installed'."""
    to = ((inp or {}).get('to') or '').strip()
    subject = ((inp or {}).get('subject') or '').strip()
    return ("Sending/drafting email needs a write-enabled Gmail connection. Gmail is "
            "built in but currently read-only / not yet authenticated for sending. Tell "
            "the user you can read and search their mail once connected, and that sending "
            "needs the Gmail connector authenticated (its `authenticate` tool, or connect "
            "at /api/google/auth). OFFER to walk them through it — do NOT say you can't "
            f"email. (Draft was to={to!r}, subject={subject!r}.)")


def _tool_get_career_pipeline(_inp):
    try:
        if JOB_SEARCH_FILE.exists():
            text = JOB_SEARCH_FILE.read_text(encoding='utf-8', errors='replace')
            return text[:500_000] + ("\n...[truncated]" if len(text) > 500_000 else "")
        return "No career pipeline file found at ~/wiki/professional/job-search.md."
    except Exception as e:
        return f"Pipeline read error: {e}"


def _tool_get_briefing(_inp):
    """Return the most recent daily briefing (HTML stripped, plus markdown)."""
    candidates = []
    briefings_dir = FRIDAY_DIR / "wiki" / "briefings"
    if briefings_dir.exists():
        for f in briefings_dir.iterdir():
            if f.is_file() and f.suffix in ('.html', '.md'):
                candidates.append(f)
    creations_dir = CREATIONS_DIR
    if creations_dir.exists():
        for f in creations_dir.iterdir():
            if f.is_file() and f.name.startswith('daily-briefing') and f.suffix in ('.html', '.md'):
                candidates.append(f)
    if not candidates:
        return "No briefings found."
    latest = max(candidates, key=lambda f: f.stat().st_mtime)
    try:
        text = latest.read_text(encoding='utf-8', errors='replace')
        if latest.suffix == '.html':
            text = re.sub(r'<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>', ' ', text, flags=re.I)
            text = re.sub(r'<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>', ' ', text, flags=re.I)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()
        return f"[{latest.name}]\n{text[:100_000]}"
    except Exception as e:
        return f"Briefing read error: {e}"


# ═══ BACKGROUND TASK RUNNER ═══════════════════════════════════
# In-process registry of long-running tasks spawned via /api/tasks or
# the spawn_task tool. Each entry is a plain dict; mutation happens
# from the worker thread, so callers should always copy before returning.
TASKS = {}
TASKS_LOCK = threading.Lock()

# Per-task follow-up queue for dual-loop steering (POST /api/agent/steer)
_FOLLOW_UP_QUEUES: dict = {}
_FOLLOW_UP_LOCK = threading.Lock()


def _task_log(task_id, line):
    with TASKS_LOCK:
        t = TASKS.get(task_id)
        if not t:
            return
        t.setdefault('log', []).append(str(line))
        # Cap log length to keep payloads small
        if len(t['log']) > 200:
            t['log'] = t['log'][-200:]


def _task_set(task_id, **fields):
    with TASKS_LOCK:
        t = TASKS.get(task_id)
        if not t:
            return
        t.update(fields)


def _task_snapshot(task_id=None):
    with TASKS_LOCK:
        if task_id is not None:
            t = TASKS.get(task_id)
            if not t:
                return None
            t = dict(t)
            if t.get('started'):
                t['elapsed'] = int(_time.time() - t['started']) - (0 if t.get('status') == 'running' else 0)
                if t.get('ended'):
                    t['elapsed'] = int(t['ended'] - t['started'])
            return t
        out = []
        for tid, t in TASKS.items():
            row = dict(t)
            if row.get('started'):
                end = row.get('ended') or _time.time()
                row['elapsed'] = int(end - row['started'])
            out.append(row)
        return out


def _evaluate_output(task_id, goal, output, *, local_only=False):
    """Grade task output with a fresh Claude call that has no build history.

    TWO THINGS WORTH KNOWING BEFORE READING ON.

    First: this runs at the end of EVERY background task, not only the ones
    anyone is watching. It is a cloud call per task that nobody had counted,
    and it sends the goal and up to 4,000 characters of the output.

    Second, and the reason for `local_only`: on 2026-08-24 a vault-protected
    task correctly refused the cloud for the work itself and told the user
    "It was NOT sent to a cloud provider" — and then this evaluator called
    Anthropic about that same task's goal and output. Nothing here consulted
    the vault policy. It was caught only because the API key was out of credit;
    with a working key, vault-derived text would have gone silently, inside the
    feature whose message promised it would not.

    The payload WAS gated — `_seal_or_block` classifies and redacts before the
    send, and someone clearly thought about that. It is the third time this
    exact distinction has been the bug (routes/chat.py's screen capture,
    /api/analyze's uploads, now here), so it is a class rather than three
    incidents:

        Gating the CONTENT is not the same as gating the DECISION TO SEND.
        A redactor answers "what may leave?". It never answers "should this
        call happen at all?" — and on a vault turn the answer is no, whatever
        the redactor would have made of the text.

    Returns None when it must not or cannot run, so the caller records no
    grade rather than a misleading one.
    """
    if local_only:
        return None
    client = get_anthropic_client()
    if client is None:
        return None
    try:
        eval_prompt = (
            f"You are a strict, impartial evaluator. Read the goal and output below, "
            f"then grade the output.\n\n"
            f"GOAL:\n{goal[:1500]}\n\n"
            f"OUTPUT:\n{output[:4000]}\n\n"
            f"Respond ONLY in this exact format:\n"
            f"GRADE: [PASS/PARTIAL/FAIL]\n"
            f"REASON: [one sentence]"
        )
        # Egress gate (fail-closed): the goal + task output can carry whatever the
        # task touched (files, vault reads, PII). This evaluator call went to the
        # cloud ungated; route it through the shared wrapper like every other path.
        _eval_kwargs = _seal_or_block({
            "model": ANTHROPIC_MODEL_DEFAULT,
            "max_tokens": 128,
            "messages": [{"role": "user", "content": eval_prompt}],
        }, "anthropic")
        resp = client.messages.create(**_eval_kwargs)
        if resp.content:
            return resp.content[0].text.strip()
        # An evaluator that could not run must not return the same verdict as
        # one that ran and found the work middling. It used to answer
        # "GRADE: PARTIAL" to both, so on 2026-08-24 a step that produced
        # NOTHING was graded PARTIAL — the grader's own failure became the
        # score, in a place people read as a judgement of the work.
        return "GRADE: UNAVAILABLE\nREASON: The evaluator returned no content."
    except Exception as e:
        return ("GRADE: UNAVAILABLE\nREASON: The evaluator could not run, so "
                "this output has NOT been assessed: %s" % str(e)[:200])


TASK_TIMEOUT_SECONDS = int(os.environ.get('FRIDAY_TASK_TIMEOUT', 1800))  # 30 min default


def _summarize_task_outcome(name, reply, tool_trace, status='complete'):
    """Build a guaranteed-non-empty, human-readable result for a finished task.

    A completion notification must always describe something Friday actually
    did. When the agent returns prose we use it verbatim. When it returns
    nothing textual (e.g. a distill pass that only called `propose_wiki_update`,
    or a no-op that found nothing), we synthesize an honest summary from the
    tool trace instead of leaving the modal showing "(no result text)".
    """
    name = (name or 'Task').strip()
    reply = (reply or '').strip()
    # Real prose from the agent — use it as-is. Treat the placeholder sentinels
    # ('(no response)', etc.) as empty so they get a synthesized summary.
    if reply and reply not in ('(no response)', '(no result text)', '(timed out)',
                               '(timed out before completion)'):
        return reply

    trace = tool_trace or []
    is_wiki = any(k in name.lower() for k in ('wiki', 'distill'))
    wiki_calls = [t for t in trace if t.get('name') == 'propose_wiki_update']

    if wiki_calls:
        files = ', '.join(dict.fromkeys(
            (t.get('input') or {}).get('file', '?') for t in wiki_calls))
        n = len(wiki_calls)
        return (f"Reviewed the session and proposed {n} wiki update"
                f"{'s' if n != 1 else ''} for your approval "
                f"(`{files}`). Approve or dismiss them in the Wiki workspace.")

    if trace:
        # Summarize what the agent actually did, even with no closing prose.
        counts = {}
        for t in trace:
            tn = t.get('name', '?')
            counts[tn] = counts.get(tn, 0) + 1
        actions = ', '.join(f"{k}×{v}" if v > 1 else k for k, v in counts.items())
        return (f"**{name}** finished — ran {len(trace)} tool call"
                f"{'s' if len(trace) != 1 else ''} ({actions}) but didn't return a "
                f"written summary. The work above is what it touched.")

    # No prose and no tools: an honest description of the no-op.
    if is_wiki:
        return ("Distill-to-wiki pass completed — nothing new or wiki-worthy came "
                "up in this session, so no updates were proposed.")
    if status in ('timeout',):
        return (f"**{name}** hit the time limit before producing a result. "
                f"Nothing was saved. You can re-run it or narrow the scope.")
    return (f"**{name}** completed without producing any output and used no tools — "
            f"there was nothing actionable to do.")


def _task_worker(task_id, name, prompt, description='', orb_icon='🛰',
                 model=None):
    """Run a Claude agent prompt to completion and store results.

    Heuristic log lines come from inspecting the tool_trace returned by
    _call_claude_agent so the UI can show what the agent did step-by-step.
    Timeout guard: if the task runs longer than TASK_TIMEOUT_SECONDS (default
    30 min, configurable via FRIDAY_TASK_TIMEOUT env var or settings), it is
    terminated gracefully.
    """
    timeout = _load_settings().get('task_timeout_seconds', TASK_TIMEOUT_SECONDS)
    _task_set(task_id, status='running', started=_time.time())
    _task_log(task_id, f'Spawning agent: {name} (timeout: {timeout}s)')
    if description:
        _task_log(task_id, description)
    try:
        # Each task gets its own fresh single-turn conversation.
        messages = [{"role": "user", "content": prompt}]
        # Load full vault/wiki context so the agent knows the user's context.
        _task_log(task_id, 'Loading vault context…')
        # SECURITY (2026-08-25): this prompt used to be built with NO
        # vault_control at all, so _get_friday_system_prompt fell through to
        # its "legacy ungated" default and every TIER_2 vault/self-knowledge
        # section rode into the system prompt in the clear. For a task that
        # then routes to a cloud model, the egress gate's field-wise keyword
        # classifier was the ONLY thing standing between that raw personal
        # context and Anthropic — the same classifier that already has a
        # documented TIER_2 gap. chat.py and voice.py never rely on the gate
        # alone: they pre-decide the provider and gate the prompt itself
        # (routes/chat.py:698, routes/voice.py:1159), so cloud calls see only
        # TIER_1. This does the same for background tasks.
        #
        # `_generate_agent` below routes again internally to pick the actual
        # model — deliberately not duplicated for the LOG line at
        # `_log_route` — but routing is a pure function of settings +
        # `messages` and nothing here mutates either between this call and
        # that one, so predicting it a second time, only to decide how to
        # gate the prompt, is safe and cannot land on a different answer.
        _task_provider = _predict_route_provider(
            keywords=prompt, workspace='task', has_tools=True)
        system = _get_friday_system_prompt(
            prompt, workspace='task', provider=_task_provider,
            vault_control=_gated_vault_control()) + (
            "\n\n== BACKGROUND TASK MODE ==\n"
            "You are operating as an autonomous background task. Take initiative, "
            "use available tools, and produce a concrete, useful result the user can read.\n\n"
            "== RESEARCH DISCIPLINE ==\n"
            "When doing research tasks: after your first round of findings, identify which "
            "side of the question has WEAKER evidence. Run a second round explicitly targeting "
            "that weaker side to avoid confirmation bias. State both sides in your output."
        )
        # Which model actually serves this is decided by the router INSIDE
        # _generate_agent, so the old line here — a hardcoded 'Calling Claude…'
        # written before routing — was a guess printed as a fact. It said
        # Claude even when a local seat answered. `on_route` fires the moment
        # the decision is made, with the decision itself, so the log names the
        # real responder without duplicating the routing logic.
        def _log_route(route):
            try:
                m = route.get('model') or '(unnamed model)'
                seat = 'local' if route.get('is_local') else 'cloud'
                _task_log(task_id, 'Asking %s (%s)…' % (m, seat))
                # Warn-before-silence, applied to unattended work too. Nobody
                # is watching a 3am heartbeat, but the pause is real and it is
                # the reason a run that normally takes 20s sometimes takes 90 —
                # so it belongs in the log Stephen reads afterwards rather than
                # being left as an unexplained gap in the timings.
                if route.get('is_local'):
                    from agent_friday.services import pause_forecast as _pf
                    f = _pf.before_local_turn(m)
                    if f.get('will_pause'):
                        _task_log(task_id, '  %s pause expected (%s): %s'
                                  % (f.get('confidence') or 'likely',
                                     _pf._plural(f.get('seconds') or 0),
                                     (f.get('why') or '')[:120]))
            except Exception:
                pass
        # A per-task seat override (workflow chains carry one) beats the
        # global subagent seat — a heavy creative workflow can ask for the
        # orchestrator-grade model without reseating all background work.
        subagent_model = (model
                          or _load_settings().get("subagent_model")
                          or ANTHROPIC_MODEL_DEFAULT)
        _bg_label = (name or prompt or 'Task')[:24]
        # Route through the provider-agnostic agent dispatcher so a background
        # task (distill-to-wiki, deep research) never hard-fails with
        # "ANTHROPIC_API_KEY is not set" on a local/OpenAI setup.
        reply, tool_trace = _generate_agent(
            messages, system=system, max_tokens=16384, model=subagent_model,
            session_ctx={"authenticated": True, "is_background_task": True,
                         "task_id": task_id},
            orb_label=_bg_label, orb_category='monitoring', orb_icon=orb_icon,
            workspace='task', on_route=_log_route,
        )
        # Tool lines are written by _task_log_tool AS EACH CALL HAPPENS now,
        # so replaying the trace here would print every tool twice. What the
        # trace still adds is the SHAPE of the run, once, at the end.
        if tool_trace:
            _task_log(task_id, 'Used %d tool call(s): %s'
                      % (len(tool_trace),
                         ', '.join(sorted({s.get('name', '?')
                                           for s in tool_trace}))))

        # ── Timeout check ──
        _task_elapsed = _time.time() - (TASKS.get(task_id, {}).get('started') or _time.time())
        if _task_elapsed > timeout:
            _task_log(task_id, f'TIMEOUT after {int(_task_elapsed)}s — terminating gracefully')
            _task_set(task_id, status='timeout',
                      result=_summarize_task_outcome(name, reply, tool_trace, status='timeout'),
                      ended=_time.time())
            return

        # ── Dual-loop: drain the follow-up queue ──────────────────
        # External callers can POST /api/agent/steer to push follow-up
        # prompts that re-enter the agent after the first pass completes.
        combined_reply = reply or ''
        combined_trace = list(tool_trace or [])
        _drain_iters = 0
        while _drain_iters < 5:
            # Check timeout before each steer iteration
            _task_elapsed = _time.time() - (TASKS.get(task_id, {}).get('started') or _time.time())
            if _task_elapsed > timeout:
                _task_log(task_id, f'TIMEOUT during steer loop after {int(_task_elapsed)}s')
                _task_set(task_id, status='timeout',
                          result=_summarize_task_outcome(name, combined_reply, combined_trace, status='timeout'),
                          ended=_time.time())
                return
            with _FOLLOW_UP_LOCK:
                pending = _FOLLOW_UP_QUEUES.pop(task_id, [])
            if not pending:
                break
            _drain_iters += 1
            for steer_msg in pending:
                _task_log(task_id, f'[steer] {steer_msg[:80]}')
                steer_reply, steer_trace = _generate_agent(
                    [{"role": "user", "content": steer_msg}],
                    system=system, max_tokens=16384, model=subagent_model,
                    session_ctx={"authenticated": True, "is_background_task": True,
                         "task_id": task_id},
                    orb_label=f"steer: {steer_msg[:18]}", orb_category='monitoring', orb_icon='🎯',
                    workspace='task',
                )
                combined_trace.extend(steer_trace or [])
                if steer_reply:
                    combined_reply += f"\n\n---\n{steer_reply}"

        reply = combined_reply
        tool_trace = combined_trace

        # ── Evidence gate: require tool use for verified completion ──
        evidence = [t for t in tool_trace if t.get('name') not in ('spawn_task',)]
        verified = len(evidence) > 0
        verification_summary = ', '.join(dict.fromkeys(t['name'] for t in evidence[:10])) if evidence else 'no tools used'
        final_status = 'complete' if verified else 'completed_unverified'

        # A reply that IS a provider error is not a completed task, whatever the
        # verifier concluded — the verifier grades the work, and there is no work
        # here to grade. Without this a 404 comes back as prose, passes straight
        # through _summarize_task_outcome verbatim, and the task announces
        # "finished". See _looks_like_provider_failure for what this cost.
        if _looks_like_provider_failure(reply):
            final_status = 'failed'
            _task_log(task_id,
                      'FAILED: the model provider returned an error instead of '
                      'a result, so nothing was produced. Reported as failed '
                      'rather than complete. Provider said: %s'
                      % (reply or '').strip()[:200])

        # v5: feed the learning loop with this task's outcome. The *approach* is
        # the tool strategy used (deduped tool names), so repeated tasks that
        # share a strategy form mineable buckets — "for agent_task tasks,
        # [search_web, browse_web] works well." Best-effort, local, never raises.
        try:
            from agent_friday.services import learning_loop as _ll
            _ll.observe('agent_task', prompt or '',
                        approach=(verification_summary or 'no_tools'),
                        success=verified, workspace='task')
        except Exception:
            pass

        _task_log(task_id, 'Finalizing response')
        result_text = _summarize_task_outcome(name, reply, tool_trace, status=final_status)
        _task_set(task_id, status=final_status, result=result_text, ended=_time.time(),
                  verified=verified, verification_evidence=verification_summary)

        # ── Fresh-context evaluator ────────────────────────────────
        # The evaluator is a CLOUD call. A task that refused the cloud for its
        # own work must not be graded by it — see _evaluate_output. Same signal
        # the work used, read the same way, so the two cannot drift apart.
        _eval_local_only = False
        try:
            from agent_friday.core import _vault_local_only as _vlo
            _eval_local_only = bool(_vlo())
        except Exception:
            _eval_local_only = True   # cannot tell → do not send
        if _eval_local_only:
            _task_log(task_id, 'Skipping quality evaluation — it is a cloud '
                               'call and this task is vault-protected.')
            evaluation = None
        else:
            _task_log(task_id, 'Running quality evaluation (cloud)…')
            evaluation = _evaluate_output(task_id, prompt, reply or '',
                                          local_only=_eval_local_only)
        if evaluation:
            _task_set(task_id, evaluation=evaluation)
            lines = evaluation.splitlines()
            grade_line = next((l for l in lines if l.startswith('GRADE:')), '')
            reason_line = next((l for l in lines if l.startswith('REASON:')), '')
            if grade_line:
                # The REASON used to be stored and never shown — not in the
                # log, and not rendered in the panel either. So a run ended on
                # a bare "Eval: GRADE: FAIL" with no way to find out what
                # failed. The actual reason for the heartbeat's FAIL was:
                # "does not reply exactly NO CHANGE — it appends an
                # unrequested reminder note". Friday was marked down for
                # telling Stephen about tomorrow's appointment.
                _task_log(task_id, 'Eval: %s' % grade_line)
                if reason_line:
                    _task_log(task_id, '  %s' % reason_line)
                # And say what the grade DOES, because the answer is nothing.
                # A grader whose verdict changes no outcome is a comment, and
                # it should read as one rather than as a failure.
                if 'UNAVAILABLE' in grade_line:
                    # Say that nothing was assessed. An absent judgement read
                    # as a middling one is exactly what PARTIAL used to do.
                    _task_log(task_id,
                              '  (the evaluator did not run — this output has '
                              'NOT been assessed either way)')
                elif 'FAIL' in grade_line or 'PARTIAL' in grade_line:
                    _task_log(task_id,
                              '  (advisory only — the result below was '
                              'delivered unchanged)')

        _task_log(task_id, 'Done.')

        # ── P4: say so, unprompted ──
        # A finished background task used to sit in an in-memory dict until
        # something polled it. By the output-liveness rule that is a failure
        # even though it exits zero: work completed that the user never hears
        # about is work that did not happen, from where they are sitting.
        _report_task_completion(task_id, name, final_status, result_text)

        # ── Task chaining: spawn the next link if this task defines one ──
        try:
            _advance_task_chain(task_id, result_text)
        except Exception as ce:
            _task_log(task_id, f'Chain advance error: {ce}')
    except Exception as e:
        traceback.print_exc()
        _task_set(task_id, status='failed', result=f'[Error] {e}', ended=_time.time())
        _task_log(task_id, f'Error: {e}')
        # A FAILED task must report too. Silence on failure is the worse half
        # of this gap: it reads exactly like success to anyone not watching.
        _report_task_completion(task_id, name, 'failed', f'[Error] {e}')
        # A failed CHAIN link retries itself (per-step budget) instead of the
        # chain dying silently mid-run — the workflow UI shows the retry.
        try:
            _retry_chain_step(task_id, str(e))
        except Exception as re_:
            _task_log(task_id, f'Chain retry error: {re_}')


def _report_task_completion(task_id, name, status, result_text):
    """Push a finished background task into the conversation (P4 / RS9).

    Best-effort by design — a notification that raises must not turn a
    completed task into a failed one — but never silent: a failure to notify
    is logged into the task's own log, where it is visible.
    """
    try:
        # notifications_engine lives at the PACKAGE ROOT, not under services/.
        # Getting this wrong is invisible: the ImportError lands in the except
        # below and the task completes looking green with nobody told — which
        # is the exact defect this function exists to fix.
        import agent_friday.notifications_engine as notifications_engine
        body = (result_text or '').strip()
        lede = body if len(body) <= 400 else body[:400].rstrip() + '…'
        ok = status not in ('failed', 'error')
        notifications_engine.push(
            title=f"Task {'finished' if ok else 'failed'}: {name}",
            body=lede or ('Finished with no output.' if ok else 'Failed.'),
            proactive_chat=True,
            chat_message=(
                f"Background task **{name}** {'finished' if ok else 'FAILED'}.\n\n"
                f"{lede or '(no output)'}"
            ),
            target={"kind": "task", "id": task_id},
        )
    except Exception as ne:
        try:
            _task_log(task_id, f'Completion notice could not be sent: {ne}')
        except Exception:
            pass


def _spawn_task(name, prompt, description='', on_complete=None,
                chain=None, chain_step=0, orb_icon='🛰', scope=None,
                model=None):
    """Spawn a background task.

    on_complete: optional dict {"spawn": "<next step name>", "prompt": "<optional
        full instruction>", "with_context": true} — when this task finishes
        successfully, that follow-up is spawned. If with_context (default true),
        this task's result is fed in as context for the next.
    chain / chain_step: set when this task is one link of a named workflow chain
        stored in ~/.friday/workflows/. Completion advances to the next step.
    scope: optional services.subagents scope NAME applied to this task_id
        BEFORE its worker thread starts (registered synchronously, here, not
        after th.start()) — starting the thread first and scoping it second
        would leave a real race where the worker's first tool call could run
        before the scope was in place. None (the default) leaves the task
        unscoped, exactly as before this parameter existed. If the named
        scope can't be applied, the task is NOT spawned at all (fails closed
        — a caller that asked for a safety scope must never silently get an
        unscoped dispatch instead); raises RuntimeError in that case.
    """
    task_id = str(uuid.uuid4())
    if scope:
        try:
            from agent_friday.services.subagents import register_scope_for_task
            register_scope_for_task(task_id, scope)
        except Exception as e:
            import logging as _log
            _log.getLogger(__name__).error(
                "subagent scope %r could not be applied to task %s — "
                "refusing to spawn UNSCOPED: %s", scope, task_id, e)
            raise RuntimeError(f"could not apply required scope {scope!r}: {e}") from e
    with TASKS_LOCK:
        TASKS[task_id] = {
            'task_id': task_id,
            'name': name,
            'description': description,
            'prompt': prompt,
            'status': 'queued',
            'created': _time.time(),
            'started': None,
            'ended': None,
            'log': [],
            'result': '',
            'on_complete': on_complete,
            'chain': chain,
            'chain_step': chain_step,
            'model': model,
        }
    _log_context("task_spawn", {
        "task_id": task_id,
        "name": name,
        "description": description,
        "prompt": prompt[:1000],
        "chain": chain,
        "chain_step": chain_step,
    })
    # B4: subagent spawns land in the global activity ledger (metadata only —
    # the ledger schema drops anything beyond task_id/description/model).
    try:
        from agent_friday.services import activity_ledger as _al
        _al.record(
            "subagent_spawn",
            task_id=task_id,
            description=(description or name or "")[:200],
            model=_load_settings().get("subagent_model") or ANTHROPIC_MODEL_DEFAULT,
        )
    except Exception:
        pass
    th = threading.Thread(target=_task_worker,
                          args=(task_id, name, prompt, description),
                          kwargs={'orb_icon': orb_icon, 'model': model},
                          daemon=True)
    th.start()
    return task_id


# ═══ TASK CHAINING / WORKFLOW CHAINS ══════════════════════════
# Chain definitions are JSON files in ~/.friday/workflows/. A chain is an ordered
# list of steps; each step has a name + prompt and (implicitly) feeds its output
# into the next. Running a chain spawns step 0 wired so each completion advances
# to the next link until the chain is exhausted.
WORKFLOWS_DIR = FRIDAY_DIR / "workflows"


def _workflows_dir():
    WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
    return WORKFLOWS_DIR


def _chain_slug(name):
    slug = re.sub(r'[^a-z0-9_-]+', '-', (name or '').strip().lower()).strip('-')
    return slug or 'chain'


def load_workflow_chain(name):
    """Load a chain definition by name (or slug). Returns dict or None."""
    d = _workflows_dir()
    for cand in (d / f"{name}.json", d / f"{_chain_slug(name)}.json"):
        if cand.exists():
            try:
                return json.loads(cand.read_text(encoding='utf-8'))
            except Exception:
                return None
    return None


def save_workflow_chain(defn):
    """Persist a chain definition. Requires 'name' and a non-empty 'steps' list of
    {name, prompt, with_context?}. Returns the normalized stored dict."""
    name = (defn or {}).get('name') or ''
    steps = (defn or {}).get('steps') or []
    if not name or not isinstance(steps, list) or not steps:
        raise ValueError("chain requires 'name' and a non-empty 'steps' list")
    norm_steps = []
    for i, s in enumerate(steps):
        s = s or {}
        if not (s.get('prompt') or '').strip():
            raise ValueError(f"step {i} is missing a 'prompt'")
        norm_steps.append({
            'name': (s.get('name') or f'Step {i + 1}').strip()[:120],
            'prompt': s['prompt'].strip(),
            'with_context': bool(s.get('with_context', True)),
            # Per-step seat override (model id); falls back to the chain seat.
            'seat': (s.get('seat') or '').strip() or None,
            # How many times a FAILED step is retried before the chain halts.
            'retries': max(0, min(3, int(s.get('retries', 1)))),
        })
    stored = {
        'name': name.strip()[:120],
        'slug': _chain_slug(name),
        'description': (defn.get('description') or '').strip(),
        # Chain-level seat: which model runs the steps (e.g. the orchestrator
        # model for heavy creative work). None = the global subagent seat.
        'seat': ((defn.get('seat') or '').strip() or None),
        'steps': norm_steps,
        'updated': datetime.now().isoformat(),
    }
    d = _workflows_dir()
    (d / f"{stored['slug']}.json").write_text(json.dumps(stored, indent=2), encoding='utf-8')
    return stored


def list_workflow_chains():
    d = _workflows_dir()
    out = []
    for f in sorted(d.glob('*.json')):
        try:
            c = json.loads(f.read_text(encoding='utf-8'))
            out.append({
                'name': c.get('name'),
                'slug': c.get('slug') or f.stem,
                'description': c.get('description', ''),
                'steps': len(c.get('steps') or []),
                'updated': c.get('updated'),
            })
        except Exception:
            pass
    return out


def delete_workflow_chain(name):
    d = _workflows_dir()
    f = d / f"{_chain_slug(name)}.json"
    if f.exists():
        f.unlink()
        return True
    return False


def run_workflow_chain(name):
    """Kick off a stored chain at step 0. Returns the first task_id (or None)."""
    chain = load_workflow_chain(name)
    if not chain:
        return None
    steps = chain.get('steps') or []
    if not steps:
        return None
    slug = chain.get('slug') or _chain_slug(name)
    first = steps[0]
    return _spawn_task(
        name=first.get('name') or f"{chain.get('name')} · Step 1",
        prompt=first['prompt'],
        description=f"Chain '{chain.get('name')}' · step 1/{len(steps)}",
        chain=slug, chain_step=0,
        model=first.get('seat') or chain.get('seat'),
    )


def chain_run_status(name):
    """Live status of a chain's most recent run, assembled from the task
    registry: one row per spawned step task (running, done, failed), plus the
    chain definition so the UI can show pending steps too."""
    chain = load_workflow_chain(name)
    if not chain:
        return None
    slug = chain.get('slug') or _chain_slug(name)
    steps = chain.get('steps') or []
    with TASKS_LOCK:
        rows = [dict(t) for t in TASKS.values() if t.get('chain') == slug]
    rows.sort(key=lambda t: t.get('created') or 0)
    # Only the latest run: walk back from the end until chain_step resets.
    latest = []
    for t in rows:
        if latest and int(t.get('chain_step', 0)) <= int(latest[-1].get('chain_step', 0)):
            latest = []
        latest.append(t)
    def _norm(st):
        """Collapse the task registry's richer statuses ('complete',
        'completed_unverified', 'done') onto the four the panel knows."""
        st = (st or 'pending').lower()
        if st.startswith('complete') or st == 'done':
            return 'completed'
        if st in ('queued', 'running', 'failed'):
            return st
        return st
    out_steps = []
    for i, s in enumerate(steps):
        row = next((t for t in latest if int(t.get('chain_step', -1)) == i), None)
        out_steps.append({
            'index': i, 'name': s.get('name'),
            'status': _norm((row or {}).get('status')),
            'task_id': (row or {}).get('task_id'),
            'started': (row or {}).get('started'),
            'ended': (row or {}).get('ended'),
            'result_tail': ((row or {}).get('result') or '')[-400:],
            'log_tail': ((row or {}).get('log') or [])[-3:],
        })
    running = any(s['status'] in ('queued', 'running') for s in out_steps)
    failed = any(s['status'] == 'failed' for s in out_steps)
    done = all(s['status'] == 'completed' for s in out_steps) if out_steps else False
    return {'name': chain.get('name'), 'slug': slug,
            'state': 'running' if running else
                     'failed' if failed else
                     'completed' if done else 'idle',
            'steps': out_steps}


# ── Workflow chains as agent TOOLS ──────────────────────────────────────────
# Until 2026-08-20 chains could only be authored via the HTTP API or the UI —
# the seat itself could run one-off tasks but could not build a multi-step
# pipeline. These three tools close that loop: Friday can now design a chain,
# launch it, and watch it, entirely from a chat turn or a scheduled prompt.

def _tool_create_workflow(inp):
    inp = inp or {}
    try:
        stored = save_workflow_chain({
            'name': inp.get('name'),
            'description': inp.get('description') or '',
            'seat': inp.get('seat'),
            'steps': inp.get('steps') or [],
        })
        return ("workflow '%s' saved with %d steps (slug: %s). Run it with "
                "run_workflow." % (stored['name'], len(stored['steps']),
                                   stored['slug']))
    except ValueError as ve:
        return "create_workflow error: %s" % ve
    except Exception as e:
        return "create_workflow error: %s" % e


def _tool_run_workflow(inp):
    inp = inp or {}
    name = (inp.get('name') or '').strip()
    if not name:
        return "run_workflow error: 'name' is required."
    tid = run_workflow_chain(name)
    if not tid:
        return "run_workflow error: no chain named %r (or it has no steps)." % name
    return ("workflow '%s' started (first task %s). Steps auto-advance; check "
            "progress with workflow_status." % (name, tid))


def _tool_workflow_status(inp):
    inp = inp or {}
    name = (inp.get('name') or '').strip()
    if not name:
        chains = list_workflow_chains()
        return "workflows on file: " + (", ".join(
            "%s (%d steps)" % (c['slug'], c['steps']) for c in chains) or "none")
    st = chain_run_status(name)
    if st is None:
        return "workflow_status error: no chain named %r." % name
    lines = ["%s: %s" % (st['name'], st['state'])]
    for s in st['steps']:
        lines.append("  %d. %s - %s" % (s['index'] + 1, s['name'], s['status']))
        if s['status'] == 'failed' and s.get('result_tail'):
            lines.append("     failure tail: %s" % s['result_tail'][-200:])
    return "\n".join(lines)


CLAUDE_TOOLS.extend([
    {
        "name": "create_workflow",
        "description": (
            "Create or update a stored multi-step workflow (chain). Each step "
            "is a full autonomous agent run with all your tools; steps run in "
            "order and each receives the previous step's result as context. "
            "Use for long multi-stage productions (films, research pipelines) "
            "instead of trying to do everything in one turn. steps: list of "
            "{name, prompt, retries?}; optional seat = model id override."),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Workflow name."},
                "description": {"type": "string"},
                "seat": {"type": "string", "description": "Optional model id to run steps on."},
                "steps": {"type": "array", "description": "Ordered steps: {name, prompt, retries (0-3, default 1)}."},
            },
            "required": ["name", "steps"],
        },
    },
    {
        "name": "run_workflow",
        "description": ("Start a stored workflow (chain) by name. Steps "
                        "auto-advance on completion and retry on failure."),
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "workflow_status",
        "description": ("Live status of a stored workflow's most recent run "
                        "(per-step states, failure tails). Without a name, "
                        "lists the stored workflows."),
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        },
    },
])


def _retry_chain_step(task_id, error_text):
    """Respawn a failed chain link if its step has retry budget left."""
    with TASKS_LOCK:
        t = dict(TASKS.get(task_id) or {})
    slug = t.get('chain')
    if not slug:
        return None
    chain = load_workflow_chain(slug)
    steps = (chain or {}).get('steps') or []
    idx = int(t.get('chain_step', 0))
    if idx >= len(steps):
        return None
    step = steps[idx]
    used = int(t.get('chain_retry', 0))
    if used >= int(step.get('retries', 1)):
        _task_log(task_id, f'Chain halted: step {idx + 1} failed with no retries left.')
        return None
    _task_log(task_id, f'→ retrying step {idx + 1} ({used + 1}/{step.get("retries", 1)})')
    prompt = (f"The previous attempt at this step FAILED with: {error_text[:500]}\n"
              f"Diagnose what went wrong and complete the step properly this time.\n\n"
              f"---\n\n{step['prompt']}")
    new_id = _spawn_task(
        name=step.get('name') or f'Step {idx + 1} (retry)',
        prompt=prompt,
        description=f"Chain '{(chain or {}).get('name')}' · step {idx + 1}/{len(steps)} · retry {used + 1}",
        chain=slug, chain_step=idx,
        model=step.get('seat') or (chain or {}).get('seat'),
    )
    with TASKS_LOCK:
        if new_id in TASKS:
            TASKS[new_id]['chain_retry'] = used + 1
    return new_id


#: Reply shapes that mean the agent never actually worked — the provider
#: refused or crashed and the ERROR TEXT became the task's "result". A chain
#: that advances past one of these ships nothing while reporting success
#: (run 2 of the teen storybook advanced through five such steps). Treated
#: as step failure: retried if budget remains, else the chain halts.
#: Reply shapes that mean the provider never ran the work, whatever the status
#: says. These arrive as ordinary prose in the reply — nothing raises — so a task
#: carrying one of them reports "complete" and the caller believes it.
#:
#: 2026-08-21: every voice session's wiki distillation had been lost this way for
#: weeks. `_spawn_voice_distill` routed to a model that is not resident, the call
#: 404'd, the 404 text came back as the reply, and the task announced "Task
#: complete." The record of every spoken conversation was discarded while
#: asserting success — which is worse than failing, because nobody investigates
#: a green light.
_CHAIN_FAILURE_SIGNATURES = (
    "no model provider could run the agent",
    "exceeds the available context size",
    "it was not sent to a cloud provider",
    "[friday offline]",
    "credit balance is too low",
    # Ollama / llama-server model-not-resident shapes
    "model not found",
    "model '",                      # {"error":"model 'x' not found"}
    "http 404",
    "connection refused",
    "no local seat available",
)


def _looks_like_provider_failure(text) -> bool:
    """True when a reply is a provider error rather than work product.

    Used for EVERY background task, not just chain links. The chain path grew
    this check first, but a one-off task that silently swallows a 404 is the
    same defect with a smaller blast radius — and the voice distill proved the
    blast radius is not small.
    """
    low = (text or "").strip().lower()
    if not low:
        return False
    return any(sig in low for sig in _CHAIN_FAILURE_SIGNATURES)


def _advance_task_chain(task_id, result_text):
    """Called when a task finishes. If it's a chain link, spawn the next step;
    otherwise honor a one-off on_complete spec. The completed task's result is
    threaded forward as context when requested."""
    with TASKS_LOCK:
        t = dict(TASKS.get(task_id) or {})
    result_text = (result_text or '').strip()

    # A "completed" chain link whose result is a provider-failure message did
    # not do its work — route it through the retry path instead of advancing.
    if t.get('chain'):
        low = result_text.lower()
        if any(sig in low for sig in _CHAIN_FAILURE_SIGNATURES):
            _task_log(task_id, 'Chain link result is a provider failure — '
                               'not advancing; retrying this step.')
            return _retry_chain_step(task_id, result_text[:500])

    # 1) Named workflow chain — advance to the next step.
    chain_slug = t.get('chain')
    if chain_slug:
        chain = load_workflow_chain(chain_slug)
        steps = (chain or {}).get('steps') or []
        nxt = int(t.get('chain_step', 0)) + 1
        if chain and nxt < len(steps):
            step = steps[nxt]
            prompt = step['prompt']
            if step.get('with_context', True) and result_text:
                prompt = (f"Context from the previous step "
                          f"(\"{t.get('name')}\"):\n\n{result_text[:6000]}\n\n"
                          f"---\n\nYour task:\n{prompt}")
            _task_log(task_id, f"→ chaining to step {nxt + 1}/{len(steps)}: {step['name']}")
            return _spawn_task(
                name=step['name'],
                prompt=prompt,
                description=f"Chain '{chain.get('name')}' · step {nxt + 1}/{len(steps)}",
                chain=chain_slug, chain_step=nxt,
                model=step.get('seat') or chain.get('seat'),
            )
        return None

    # 2) One-off on_complete spec.
    oc = t.get('on_complete')
    if isinstance(oc, dict) and (oc.get('spawn') or oc.get('prompt')):
        nxt_name = (oc.get('spawn') or 'Follow-up task').strip()[:120]
        prompt = (oc.get('prompt') or oc.get('spawn') or '').strip()
        if oc.get('with_context', True) and result_text:
            prompt = (f"Context from the previous task (\"{t.get('name')}\"):\n\n"
                      f"{result_text[:6000]}\n\n---\n\nYour task:\n{prompt}")
        _task_log(task_id, f"→ on_complete: spawning '{nxt_name}'")
        return _spawn_task(
            name=nxt_name, prompt=prompt,
            description=f"Spawned on completion of '{t.get('name')}'",
            on_complete=oc.get('then'),  # allow nesting via {"then": {...}}
        )
    return None


def _tool_spawn_task(inp):
    """Claude-facing tool: spawn a background research/analysis task."""
    name = ((inp or {}).get('name') or 'Background task').strip()[:120]
    prompt = ((inp or {}).get('prompt') or '').strip()
    desc = ((inp or {}).get('description') or '').strip()[:200]
    if not prompt:
        return "spawn_task error: 'prompt' is required."
    on_complete = (inp or {}).get('on_complete')
    if on_complete is not None and not isinstance(on_complete, dict):
        on_complete = None
    tid = _spawn_task(name, prompt, desc, on_complete=on_complete)
    return json.dumps({
        'task_id': tid,
        'status': 'running',
        'message': f"Spawned background task '{name}'. The user can watch progress in the Task Tray (bottom-right) and you can tell them you've started working on it.",
    })


# Register the spawn_task tool
CLAUDE_TOOLS.append({
    "name": "spawn_task",
    "description": "Start a background research or analysis task that runs while the user does other work. Use this when the user asks for something that will take a while (deep research, multi-step analysis, writing a long brief). The task runs autonomously and the result appears in the Task Tray in the UI.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Short, human-readable task title (e.g., 'Research Bobby Tahir')."},
            "description": {"type": "string", "description": "Optional one-line subtitle shown in the Task Tray."},
            "prompt": {"type": "string", "description": "The full instruction the background agent should execute."},
            "on_complete": {
                "type": "object",
                "description": "Optional follow-up to chain after this task finishes. {\"spawn\": \"<next task title>\", \"prompt\": \"<full instruction for the next task>\", \"with_context\": true} — when set, that follow-up auto-starts on success, and (if with_context) this task's result is fed in as its context.",
                "properties": {
                    "spawn": {"type": "string"},
                    "prompt": {"type": "string"},
                    "with_context": {"type": "boolean"},
                },
            },
        },
        "required": ["name", "prompt"],
    },
})


def _tool_propose_wiki_update(inp):
    """Queue a wiki update as pending — the user approves it in the Wiki workspace."""
    inp = inp or {}
    file = (inp.get("file") or "").strip()
    new_value = inp.get("new_value") or ""
    if not file or not new_value:
        return "propose_wiki_update error: 'file' and 'new_value' are required."
    section = (inp.get("section") or "").strip()
    reason = (inp.get("reason") or "Agent-proposed update.").strip()
    if _safe_wiki_path(file) is None:
        return f"propose_wiki_update error: invalid wiki path {file!r} (must stay inside ~/wiki/)."
    pid = _propose_wiki_update(file=file, section=section, new_value=new_value, reason=reason)
    return f"Wiki update proposed (id={pid}) — awaiting your approval in the Wiki workspace."


def _tool_correct_wiki(inp):
    """Replace old_text with new_text across every wiki file and ~/.friday JSONs."""
    inp = inp or {}
    old_text = inp.get("old_text") or ""
    new_text = inp.get("new_text") or ""
    if not old_text:
        return "correct_wiki error: 'old_text' is required."
    modified = []
    if WIKI_DIR.exists():
        for f in WIKI_DIR.rglob('*'):
            if not f.is_file() or f.suffix not in ('.md', '.txt'):
                continue
            try:
                text = wiki_read_text(f)
            except Exception:
                continue
            if old_text in text:
                try:
                    rel = str(f.relative_to(WIKI_DIR)).replace('\\', '/')
                    _mirror_wiki_file(rel, text.replace(old_text, new_text))
                    modified.append(rel)
                except Exception:
                    pass
    if FRIDAY_DIR.exists():
        for f in FRIDAY_DIR.glob('*.json'):
            try:
                text = wiki_read_text(f)
            except Exception:
                continue
            if old_text in text:
                try:
                    wiki_write_text(f, text.replace(old_text, new_text))
                    modified.append(f".friday/{f.name}")
                except Exception:
                    pass
    return json.dumps({"modified": modified, "count": len(modified)})


CLAUDE_TOOLS.append({
    "name": "propose_wiki_update",
    "description": "Propose an update to the user's personal wiki when you learn new information about them. The update is queued as PENDING and the user approves it from the Wiki workspace — it is NOT applied immediately. Use this whenever you learn a new fact about the user, their work, family, preferences, or projects that should outlive the current conversation.",
    "input_schema": {
        "type": "object",
        "properties": {
            "file": {"type": "string", "description": "Wiki file path relative to ~/wiki/, e.g., 'identity/core-profile.md'."},
            "section": {"type": "string", "description": "Optional section name within the file (e.g., 'birthplace'). Used to append under a header if no existing text is matched."},
            "new_value": {"type": "string", "description": "The new content to add or replace with."},
            "reason": {"type": "string", "description": "Why this update is being proposed (e.g., 'User correction during chat')."},
        },
        "required": ["file", "new_value", "reason"],
    },
})
CLAUDE_TOOLS.append({
    "name": "correct_wiki",
    "description": "Correct wrong information across the ENTIRE wiki at once. Use this when the user says you (or the wiki) got a fact wrong — replaces old_text with new_text in every wiki file plus ~/.friday JSONs. Applies immediately (no approval needed) because corrections are user-initiated.",
    "input_schema": {
        "type": "object",
        "properties": {
            "old_text": {"type": "string", "description": "Exact text to find and replace."},
            "new_text": {"type": "string", "description": "Replacement text."},
        },
        "required": ["old_text", "new_text"],
    },
})


CLAUDE_TOOLS.append({
    "name": "generate_image",
    "description": (
        "Generate a REAL image from a text prompt using Google's Gemini image "
        "models (Nano Banana Pro / Nano Banana 2) and save it to the user's "
        "creations folder. Use this whenever the user asks you to 'draw', "
        "'create/make/generate an image/picture/art of', 'paint', 'illustrate', "
        "or design a visual. You CAN make images — do not say you can't. The "
        "result file shows up in the Studio gallery; tell the user it's ready and "
        "give the title. A holographic progress orb appears while it renders."),
    "input_schema": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "Vivid description of the image to generate."},
            "model": {"type": "string", "description": "Image model: 'gemini-nano-banana-pro' (highest quality, default) or 'gemini-nano-banana-2' (faster). Optional."},
            "style": {"type": "string", "description": "Optional style preset: photorealistic, cinematic, digital-art, watercolor, oil-painting, anime, 3d-render, neon, minimalist, sketch — or free-text."},
            "aspect_ratio": {"type": "string", "description": "Optional aspect ratio: 1:1 (default), 3:4, 4:3, 9:16, 16:9."},
            "n": {"type": "integer", "description": "How many COPIES of the same prompt to render (1-8, default 1). Each gets its own random seed, so they vary. For DIFFERENT images use `prompts` instead."},
            "prompts": {"type": "array", "items": {"type": "string"},
                        "description": "Two or more DISTINCT prompts rendered as one batch, in a single GPU session. Use this whenever the user asks for several different images at once ('three different images of X, Y and Z') — do NOT call this tool repeatedly, which is slower and reloads the models between every render."},
        },
        "required": ["prompt"],
    },
})
CLAUDE_TOOLS.append({
    "name": "generate_video",
    "description": (
        "Generate a REAL video from a text prompt (optionally seeded by an image) "
        "using Google Veo, and save it to the user's creations folder. Use when "
        "the user asks you to 'make/create/generate a video/clip/animation of' "
        "something, or to 'animate' an existing creation. Video rendering takes "
        "roughly 1-3 minutes; a progress orb shows the estimate. For image-to-"
        "video, pass image_path (an absolute path or a filename already in the "
        "creations folder). You CAN make video — do not say you can't."),
    "input_schema": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "Description of the video / motion to generate."},
            "model": {"type": "string", "description": "Video model — 'veo' (default). Optional."},
            "aspect_ratio": {"type": "string", "description": "Optional: 16:9 (default) or 9:16."},
            "duration_seconds": {"type": "integer", "description": "Optional clip length in seconds (model-dependent, typically 4-8)."},
            "image_path": {"type": "string", "description": "Optional seed image for image-to-video: an absolute path or a creation filename (e.g. 'friday-image-20260621-120000-ab12.png')."},
        },
        "required": ["prompt"],
    },
})
CLAUDE_TOOLS.append({
    "name": "generate_music",
    "description": (
        "Write a DEMO PREVIEW of a track — a text description of the music, "
        "not audio. Use when the user asks you to 'make/write/compose a "
        "song/track/beat/score/jingle'. Say plainly that this produces a "
        "written preview rather than a playable file, and offer it on those "
        "terms; do not describe the result as a song the user can listen to. "
        "The installed google-genai exposes no batch music surface (only "
        "Lyria RealTime streaming), so no audio is rendered by this path. "
        "Higgsfield's account catalogue does list an audio model "
        "('sonilo_music'), but nothing has generated audio on this machine "
        "yet, so it is not offered here until it has. Accepts lyrics with "
        "[verse]/[chorus] tags and a mood-reference image, which shape the "
        "written preview."),
    "input_schema": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "Description of the music: genre, mood, instruments, tempo, references."},
            "model": {"type": "string", "description": "Music model: 'lyria-clip' (≤30s, default) or 'lyria-pro' (full song)."},
            "mode": {"type": "string", "description": "'instrumental' (default) or 'song' (with vocals — pass lyrics)."},
            "lyrics": {"type": "string", "description": "Optional custom lyrics. Use [verse]/[chorus]/[bridge] section tags. Enables vocal synthesis."},
            "duration_seconds": {"type": "integer", "description": "Optional length in seconds (clip model caps at 30)."},
            "language": {"type": "string", "description": "Optional vocal language code (default 'en')."},
            "negative_prompt": {"type": "string", "description": "Optional things to avoid, e.g. 'no drums'."},
            "seed_image_path": {"type": "string", "description": "Optional image (path or creation filename) to transfer mood from."},
        },
        "required": ["prompt"],
    },
})
CLAUDE_TOOLS.append({
    "name": "compose_timeline",
    "description": (
        "Assemble existing video clips and a music/audio track into a finished, "
        "exported production using FFmpeg — cuts/crossfades, music ducking under "
        "dialogue, and platform exports (YouTube 16:9, Instagram Reel / TikTok "
        "9:16, WebM, GIF preview, audio-only MP3). Use when the user asks you to "
        "'edit/assemble/stitch/cut these clips together', 'add music to this "
        "video', or 'export a reel/vertical version'. Each clip may be a "
        "creation filename, an absolute path, a path relative to either "
        "creations folder (subfolders fine, e.g. 'storybook/clips/clip1.mp4'), "
        "or an object {file, in, out} for per-clip trims; optionally add a "
        "music filename/path. The source clips' content hashes are signed into "
        "the production's provenance."),
    "input_schema": {
        "type": "object",
        "properties": {
            "clips": {"type": "array", "description": "Ordered list of clips: filenames/paths, or {file, in, out} objects for per-clip trims."},
            "music": {"type": "string", "description": "Optional music/audio creation filename to lay under the video."},
            "transition": {"type": "string", "description": "Transition between clips: 'cut' (default), 'crossfade', or 'fadeblack'."},
            "title": {"type": "string", "description": "Optional title-card text shown at the start."},
            "exports": {"type": "array", "description": "Export presets, e.g. ['mp4-1080p','mp4-vertical-9x16','gif-preview']. Default mp4-1080p.", "items": {"type": "string"}},
            "clip_seconds": {"type": "number", "description": "Optional per-clip length in seconds (default 6)."},
        },
        "required": ["clips"],
    },
})
CLAUDE_TOOLS.append({
    "name": "create_presentation",
    "description": (
        "Create a REAL, polished slide deck as a self-contained HTML file in the "
        "user's creations folder (opens in the Studio gallery; arrow keys / space "
        "navigate, N toggles speaker notes, printing exports to PDF). Use when "
        "the user asks you to 'make/build/create a presentation/slideshow/deck/"
        "slides about X'. You write the outline; a fixed template renders it — "
        "the result is always clean and works offline. You CAN make slide decks "
        "— do not say you can't."),
    "input_schema": {
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "What the deck is about — include audience and key points to hit if known."},
            "slides": {"type": "integer", "description": "Content slide count (3-16, default 8)."},
            "style": {"type": "string", "description": "Optional tone/style hints, e.g. 'investor pitch', 'technical deep-dive', 'playful'."},
        },
        "required": ["topic"],
    },
})
CLAUDE_TOOLS.append({
    "name": "create_website",
    "description": (
        "Create a REAL multi-page website as ONE self-contained HTML file (hash "
        "navigation between pages, responsive, works offline, deploys anywhere) "
        "saved to the user's creations folder and viewable in the Studio "
        "gallery. Use when the user asks you to 'make/build/create a website/"
        "site/landing page for X'. You write the content spec; a fixed template "
        "renders it. You CAN build websites — do not say you can't."),
    "input_schema": {
        "type": "object",
        "properties": {
            "brief": {"type": "string", "description": "What the site is for: product/subject, audience, pages wanted, key messages."},
            "pages": {"type": "integer", "description": "Page count (1-6, default 4). First page is the landing page."},
            "style": {"type": "string", "description": "Optional tone/style hints, e.g. 'startup landing', 'portfolio', 'documentation'."},
        },
        "required": ["brief"],
    },
})


def _tool_generate_image(inp):
    """Generate an image via Gemini (Nano Banana) and save it to creations."""
    from agent_friday.services.creative_engine import generate_image
    inp = inp or {}
    prompt = (inp.get("prompt") or "").strip()
    _prompts = inp.get("prompts") or []
    if isinstance(_prompts, str):          # a model may send one string
        _prompts = [_prompts]
    _prompts = [str(p).strip() for p in _prompts if str(p or "").strip()]
    if not prompt and not _prompts:
        return "generate_image error: 'prompt' or 'prompts' is required."
    res = generate_image(
        prompt or _prompts[0],
        model=inp.get("model"),
        style=inp.get("style"),
        aspect_ratio=inp.get("aspect_ratio") or "1:1",
        n=inp.get("n", 1),
        prompts=_prompts,
    )
    return _creative_result_summary(res, "image")


def _tool_generate_video(inp):
    """Generate a video via Google Veo and save it to creations."""
    from agent_friday.services.creative_engine import generate_video
    inp = inp or {}
    prompt = (inp.get("prompt") or "").strip()
    if not prompt:
        return "generate_video error: 'prompt' is required."
    res = generate_video(
        prompt,
        model=inp.get("model"),
        aspect_ratio=inp.get("aspect_ratio") or "16:9",
        duration_seconds=inp.get("duration_seconds"),
        image_path=inp.get("image_path"),
    )
    return _creative_result_summary(res, "video")


def _tool_generate_music(inp):
    """Generate music via Lyria 3 and save it to creations."""
    from agent_friday.services import music_engine
    inp = inp or {}
    prompt = (inp.get("prompt") or "").strip()
    if not prompt:
        return "generate_music error: 'prompt' is required."
    res = music_engine.generate_music(
        prompt,
        model=inp.get("model"),
        mode=inp.get("mode") or "instrumental",
        lyrics=inp.get("lyrics"),
        duration_seconds=inp.get("duration_seconds"),
        language=inp.get("language") or "en",
        negative_prompt=inp.get("negative_prompt"),
        seed_image_path=inp.get("seed_image_path"),
    )
    return _creative_result_summary(res, "music")


def _tool_compose_timeline(inp):
    """Assemble clips + music into an exported production via FFmpeg."""
    from agent_friday.services import timeline_engine
    inp = inp or {}
    clips = inp.get("clips") or []
    if not isinstance(clips, list) or not clips:
        return "compose_timeline error: 'clips' (a list of clip filenames) is required."
    transition = (inp.get("transition") or "cut").lower()
    clip_seconds = inp.get("clip_seconds") or 6
    video_clips = []
    for c in clips:
        if isinstance(c, dict):
            video_clips.append({
                "file": c.get("file") or "",
                "in": float(c.get("in") or 0.0),
                "out": float(c.get("out") or c.get("seconds") or clip_seconds),
                "transition_in": {"type": transition, "dur": 0.5}})
        else:
            video_clips.append({"file": c, "in": 0.0, "out": clip_seconds,
                                "transition_in": {"type": transition, "dur": 0.5}})
    tracks = [{"kind": "video", "clips": video_clips}]
    if inp.get("music"):
        tracks.append({"kind": "audio", "clips": [
            {"file": inp["music"], "role": "music", "gain_db": -4.0, "fade_out": 1.5}]})
    if inp.get("title"):
        tracks.append({"kind": "overlay", "clips": [
            {"text": inp["title"], "t": 0.5, "dur": 3.0, "style": "title-card"}]})
    timeline = {"fps": 30, "resolution": [1920, 1080], "tracks": tracks,
                "exports": inp.get("exports") or ["mp4-1080p"]}
    res = timeline_engine.compose(timeline)
    return _creative_result_summary(res, "production")


def _tool_create_presentation(inp):
    """Generate a self-contained HTML slide deck via the showcase engine."""
    from agent_friday.services.showcase_engine import generate_presentation
    inp = inp or {}
    topic = (inp.get("topic") or "").strip()
    if not topic:
        return "create_presentation error: 'topic' is required."
    res = generate_presentation(
        topic, slides=inp.get("slides"), style=inp.get("style"))
    if res.get("status") != "ok":
        return res.get("message") or "Presentation generation failed."
    return json.dumps({"status": "ok", "message": res.get("message"),
                       "files": res.get("files")}, default=str)


def _tool_create_website(inp):
    """Generate a self-contained hash-routed website via the showcase engine."""
    from agent_friday.services.showcase_engine import generate_website
    inp = inp or {}
    brief = (inp.get("brief") or "").strip()
    if not brief:
        return "create_website error: 'brief' is required."
    res = generate_website(
        brief, pages=inp.get("pages"), style=inp.get("style"))
    if res.get("status") != "ok":
        return res.get("message") or "Website generation failed."
    return json.dumps({"status": "ok", "message": res.get("message"),
                       "files": res.get("files")}, default=str)


def _creative_result_summary(res, kind):
    """Turn a creative_engine result envelope into a concise string for the model."""
    res = res or {}
    status = res.get("status")
    if status in ("ok", "demo"):
        files = res.get("files") or []
        names = ", ".join(f.get("filename", "") for f in files)
        urls = ", ".join(f.get("url", "") for f in files)
        extra = ""
        if kind in ("video", "music") and res.get("mode"):
            extra = f" ({res['mode']})"
        if status == "demo":
            msg = (res.get("message") or
                   f"Cloud {kind} is unavailable — wrote a demo preview.") + \
                  f" Saved to the gallery: {names}."
        else:
            msg = (f"Generated {len(files)} {kind}{'s' if len(files) != 1 else ''}{extra} "
                   f"with {res.get('model')}. Saved to the creations folder: {names}. "
                   f"It's now in the Studio gallery. Tell the user it's ready.")
        return json.dumps({
            "status": status,
            "message": msg,
            "files": files,
            "model": res.get("model"),
            "urls": urls,
        }, default=str)
    if status == "blocked":
        return f"[CONTENT SAFETY] {res.get('reason')}"
    if status == "unavailable":
        return res.get("message") or f"{kind} generation is unavailable (no Gemini key)."
    return res.get("message") or f"{kind} generation failed."


def _tool_epistemic_score(inp):
    """Score Friday's recent responses on epistemic quality (self-improvement)."""
    from agent_friday.services.introspection import epistemic_score
    return epistemic_score(limit=(inp or {}).get("limit", 20))


def _tool_personality_show(_inp):
    """Return Friday's current personality configuration (self-improvement)."""
    from agent_friday.services.introspection import personality_show
    return personality_show()


def _tool_personality_check_sycophancy(inp):
    """Flag sycophantic patterns in Friday's recent responses (self-improvement)."""
    from agent_friday.services.introspection import personality_check_sycophancy
    return personality_check_sycophancy(limit=(inp or {}).get("limit", 20))


CLAUDE_TOOL_HANDLERS = {
    "search_web": _tool_search_web,
    "browse_web": _tool_browse_web,
    "read_file": _tool_read_file,
    "search_files": _tool_search_files,
    "write_file": _tool_write_file,
    "write_clipboard": _tool_write_clipboard,
    "query_trust_graph": _tool_query_trust_graph,
    "query_calendar": _tool_query_calendar,
    "revert_workspace": _tool_revert_workspace,
    "list_workspace_history": _tool_list_workspace_history,
    "annotate_calendar_events": _tool_annotate_calendar_events,
    "create_calendar_event": _tool_create_calendar_event,
    "update_calendar_event": _tool_update_calendar_event,
    "find_calendar_events": _tool_find_calendar_events,
    "search_email": _tool_search_email,
    "search_drive": _tool_search_drive,
    "read_doc": _tool_read_doc,
    "list_tasks": _tool_list_tasks,
    "complete_task": _tool_complete_task,
    "create_task": _tool_create_task,
    "update_task": _tool_update_task,
    "delete_task": _tool_delete_task,
    "search_contacts": _tool_search_contacts,
    "read_wiki": _tool_read_wiki,
    "search_wiki": _tool_search_wiki,
    "search_news": _tool_search_news,
    "run_command": _tool_run_command,
    "open_url": _tool_open_url,
    "open_path": _tool_open_path,
    "navigate": _tool_navigate,
    "switch_model": _tool_switch_model,
    "draft_email": _tool_draft_email,
    "get_career_pipeline": _tool_get_career_pipeline,
    "get_briefing": _tool_get_briefing,
    "spawn_task": _tool_spawn_task,
    "propose_wiki_update": _tool_propose_wiki_update,
    "correct_wiki": _tool_correct_wiki,
    "learn_skill": _tool_learn_skill,
    "install_package": _tool_install_package,
    "epistemic_score": _tool_epistemic_score,
    "personality_show": _tool_personality_show,
    "personality_check_sycophancy": _tool_personality_check_sycophancy,
    "generate_image": _tool_generate_image,
    "generate_video": _tool_generate_video,
    "generate_music": _tool_generate_music,
    "compose_timeline": _tool_compose_timeline,
    "create_presentation": _tool_create_presentation,
    "create_website": _tool_create_website,
}


# ── Computer Control ─────────────────────────────────────────────
# pyautogui-based mouse/keyboard control. Requires explicit user permission.
# The grant persists across restarts (cc_permission file); the kill switch
# terminates immediately and is never persisted.

_CC_PERMISSION = threading.Event()   # Set = user granted permission
_CC_KILL = threading.Event()          # Set = kill switch activated
_CC_ACTION_TS: list = []              # timestamps for rate limiting
_CC_ACTION_LOCK = threading.Lock()
_CC_MAX_PER_SEC = 20                  # max actions per second (rate limit is a safety floor, not a ceiling)
_CC_PERM_FILE = FRIDAY_DIR / "cc_permission"   # persists the grant across restarts (kill is never persisted)
# Maps the coordinate space of the LAST screenshot we sent the model back to real
# screen pixels. We downscale screenshots for accuracy/payload, so the model's
# click coordinates live in the downscaled image space and must be scaled up.
_CC_LAST_SHOT = {"scale_x": 1.0, "scale_y": 1.0}

_HAS_PYAUTOGUI = False
_pag = None  # module handle

try:
    import pyautogui as _pag
    _pag.FAILSAFE = True   # moving mouse to top-left corner aborts any running call
    _pag.PAUSE = 0.05
    _HAS_PYAUTOGUI = True
    _log.info("pyautogui loaded — computer control available")
except ImportError:
    _log.info("pyautogui not installed — computer control disabled. Run: pip install pyautogui")


def _cc_persist(granted: bool):
    """Persist (or clear) the Computer Control grant so it survives a restart.

    The kill switch is intentionally NOT persisted — a fresh start clears a kill
    so the user isn't permanently locked out, but a prior grant is restored.
    """
    try:
        if granted:
            _CC_PERM_FILE.parent.mkdir(parents=True, exist_ok=True)
            _CC_PERM_FILE.write_text("granted", encoding="utf-8")
        elif _CC_PERM_FILE.exists():
            _CC_PERM_FILE.unlink()
    except Exception as _e:
        _log.warning("CC permission persist failed: %s", _e)


# Public-release hardening: Computer Control starts DISABLED on every launch.
# We do NOT auto-restore a previous runtime grant — this experimental, high-trust
# capability is opt-in per session — and we clear any stale persisted grant so the
# default is genuinely off (matches the Settings promise that permission is revoked
# on every server restart).
try:
    if _CC_PERM_FILE.exists():
        _CC_PERM_FILE.unlink()
except Exception:
    pass


def _cc_check():
    """Return (True, None) if CC is permitted, else (False, error_string)."""
    if not _HAS_PYAUTOGUI:
        return False, "pyautogui not installed. Run: pip install pyautogui"
    if _CC_KILL.is_set():
        return False, "Kill switch is active. Computer control suspended — re-enable in Settings."
    if not _CC_PERMISSION.is_set():
        return False, "Computer control permission not granted. Enable it in Settings > Computer Control."
    return True, None


def _cc_rate_ok():
    now = _time.time()
    with _CC_ACTION_LOCK:
        _CC_ACTION_TS[:] = [t for t in _CC_ACTION_TS if now - t < 1.0]
        if len(_CC_ACTION_TS) >= _CC_MAX_PER_SEC:
            return False
        _CC_ACTION_TS.append(now)
    return True


def _tool_move_mouse(inp):
    ok, err = _cc_check()
    if not ok:
        return err
    if not _cc_rate_ok():
        return "Rate limited: too many actions per second."
    # Coordinates arrive in the LAST screenshot's (downscaled) pixel space — map
    # them back to real screen pixels.
    x = int(round(int((inp or {}).get('x', 0)) * _CC_LAST_SHOT["scale_x"]))
    y = int(round(int((inp or {}).get('y', 0)) * _CC_LAST_SHOT["scale_y"]))
    try:
        _pag.moveTo(x, y, duration=0.25)
        _log_context("cc_action", {"action": "move_mouse", "x": x, "y": y})
        return f"Mouse moved to ({x}, {y})."
    except Exception as e:
        return f"move_mouse error: {e}"


def _tool_click(inp):
    ok, err = _cc_check()
    if not ok:
        return err
    if not _cc_rate_ok():
        return "Rate limited."
    # Map screenshot-space coords back to real screen pixels (see _CC_LAST_SHOT).
    x = int(round(int((inp or {}).get('x', 0)) * _CC_LAST_SHOT["scale_x"]))
    y = int(round(int((inp or {}).get('y', 0)) * _CC_LAST_SHOT["scale_y"]))
    button = (inp or {}).get('button', 'left')
    if button not in ('left', 'right', 'middle'):
        button = 'left'
    try:
        _pag.click(x, y, button=button)
        _log_context("cc_action", {"action": "click", "x": x, "y": y, "button": button})
        return f"Clicked {button} at ({x}, {y})."
    except Exception as e:
        return f"click error: {e}"


def _tool_type_text(inp):
    ok, err = _cc_check()
    if not ok:
        return err
    text = (inp or {}).get('text', '')
    if not text:
        return "No text provided."
    if len(text) > 2000:
        return "Text too long (max 2000 chars per call)."
    if not _cc_rate_ok():
        return "Rate limited."
    try:
        _pag.write(text, interval=0.03)
        _log_context("cc_action", {"action": "type_text", "chars": len(text)})
        return f"Typed {len(text)} characters."
    except Exception as e:
        return f"type_text error: {e}"


def _tool_press_key(inp):
    ok, err = _cc_check()
    if not ok:
        return err
    key = ((inp or {}).get('key') or '').strip()
    if not key:
        return "No key provided."
    if not _cc_rate_ok():
        return "Rate limited."
    try:
        _pag.press(key)
        _log_context("cc_action", {"action": "press_key", "key": key})
        return f"Pressed key: {key}."
    except Exception as e:
        return f"press_key error: {e}"


def _tool_screenshot(_inp):
    ok, err = _cc_check()
    if not ok:
        return err
    try:
        shot = _pag.screenshot()
        real_w, real_h = shot.size
        # Downscale to ~WXGA before sending to the model. Two reasons:
        #   1. Vision models localise UI elements more reliably below ~1366px wide.
        #   2. Keeps the base64 payload well under the API's per-image limit.
        # We record scale_x/scale_y so click()/move_mouse() map the model's
        # image-space coordinates back to real screen pixels.
        TARGET_W = 1366
        if real_w > TARGET_W:
            disp_w = TARGET_W
            disp_h = max(1, round(real_h * (TARGET_W / real_w)))
            shot_disp = shot.resize((disp_w, disp_h))
        else:
            disp_w, disp_h = real_w, real_h
            shot_disp = shot
        _CC_LAST_SHOT["scale_x"] = real_w / disp_w
        _CC_LAST_SHOT["scale_y"] = real_h / disp_h
        buf = io.BytesIO()
        shot_disp.save(buf, format='PNG')
        b64 = base64.b64encode(buf.getvalue()).decode()
        _log_context("cc_action", {"action": "screenshot", "size": f"{real_w}x{real_h}", "sent": f"{disp_w}x{disp_h}"})
        return json.dumps({
            "width": disp_w, "height": disp_h,
            "real_width": real_w, "real_height": real_h,
            "media_type": "image/png",
            "image_b64": b64,
            "note": (f"Screenshot is {disp_w}x{disp_h}px (top-left is 0,0). Give click/move "
                     "coordinates within this image — they are mapped to the real screen automatically."),
        })
    except Exception as e:
        return f"screenshot error: {e}"


def _tool_scroll(inp):
    ok, err = _cc_check()
    if not ok:
        return err
    if not _cc_rate_ok():
        return "Rate limited."
    direction = (inp or {}).get('direction', 'down')
    amount = max(1, min(20, int((inp or {}).get('amount', 3))))
    clicks = -amount if direction == 'down' else amount
    try:
        _pag.scroll(clicks)
        _log_context("cc_action", {"action": "scroll", "direction": direction, "amount": amount})
        return f"Scrolled {direction} {amount} step(s)."
    except Exception as e:
        return f"scroll error: {e}"


CLAUDE_TOOLS.extend([
    {
        "name": "move_mouse",
        "description": "Move the mouse cursor to screen coordinates. Requires computer control permission (user must enable in Settings > Computer Control). Take a screenshot first to locate elements.",
        "input_schema": {"type": "object", "properties": {
            "x": {"type": "integer", "description": "X pixels from left edge"},
            "y": {"type": "integer", "description": "Y pixels from top edge"},
        }, "required": ["x", "y"]},
    },
    {
        "name": "click",
        "description": "Click the mouse at screen coordinates. Requires computer control permission.",
        "input_schema": {"type": "object", "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "button": {"type": "string", "enum": ["left", "right", "middle"]},
        }, "required": ["x", "y"]},
    },
    {
        "name": "type_text",
        "description": "Type text via keyboard into the currently focused element. Requires computer control permission.",
        "input_schema": {"type": "object", "properties": {
            "text": {"type": "string"},
        }, "required": ["text"]},
    },
    {
        "name": "press_key",
        "description": "Press a keyboard key. Requires computer control permission. Key names: enter, tab, escape, backspace, delete, home, end, pageup, pagedown, up, down, left, right, f1-f12, ctrl, alt, shift, or combos like ctrl+c.",
        "input_schema": {"type": "object", "properties": {
            "key": {"type": "string"},
        }, "required": ["key"]},
    },
    {
        "name": "screenshot",
        "description": "Capture the current screen as a PNG. Returns dimensions and base64 image data. Use this before clicking to locate UI elements by their pixel position. Requires computer control permission.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "scroll",
        "description": "Scroll the mouse wheel up or down. Requires computer control permission.",
        "input_schema": {"type": "object", "properties": {
            "direction": {"type": "string", "enum": ["up", "down"]},
            "amount": {"type": "integer", "description": "Scroll steps (1-20, default 3)"},
        }, "required": ["direction"]},
    },
])

CLAUDE_TOOL_HANDLERS.update({
    "move_mouse": _tool_move_mouse,
    "click": _tool_click,
    "type_text": _tool_type_text,
    "press_key": _tool_press_key,
    "screenshot": _tool_screenshot,
    "scroll": _tool_scroll,
})


# ── Privilege Ring Mapping ─────────────────────────────────────
# Ring 0 READ   — local reads, no mutation, always allowed
# Ring 1 WRITE  — local state mutation, always allowed
# Ring 2 NETWORK — external calls, agent spawn; requires authenticated session
# Ring 3 FULL   — OS-level control (mouse, keyboard, screen); requires CC permission
TOOL_RINGS: dict[str, int] = {
    # Ring 0 — READ (local reads, no mutation, always allowed)
    "read_file":            0,
    "search_files":         0,   # read-only enumeration; no new reach over read_file
    "read_wiki":            0,
    "search_wiki":          0,
    "query_trust_graph":    0,
    "query_calendar":       0,
    "find_calendar_events": 0,   # search only, no mutation
    "list_workspace_history": 0,  # read-only history
    "get_career_pipeline":  0,
    "get_briefing":         0,
    "epistemic_score":      0,   # introspection — reads conversation memory
    "personality_show":     0,   # introspection — reads personality.json
    "personality_check_sycophancy": 0,  # introspection — reads conversation memory
    "navigate":             0,   # UI-only hint; client performs the move
    # Ring 1 — WRITE (local state mutation, always allowed)
    "write_file":           1,
    "write_clipboard":      1,
    "propose_wiki_update":  1,
    # Undo is Ring 1: it only ever moves local UI state to a state it
    # already held, and every undo is itself snapshotted.
    "revert_workspace":     1,
    # Calendar writes reach Google and change state the user shares with
    # other people, so Ring 2 with the rest of the network actions.
    "annotate_calendar_events": 2,
    "create_calendar_event":    2,
    "update_calendar_event":    2,
    "correct_wiki":         1,
    "learn_skill":          1,
    # Ring 2 — NETWORK (external calls; requires authenticated session)
    "search_web":           2,
    "search_news":          2,   # fetches the live RSS/Brave feed (network)
    "browse_web":           2,
    "search_email":         2,
    "search_drive":         2,
    "read_doc":             2,
    "list_tasks":           2,
    "complete_task":        2,   # writes to Google, same ring as calendar writes
    "create_task":          2,
    "update_task":          2,
    "delete_task":          2,   # irreversible — also gated by _ALWAYS_CONFIRM
    "search_contacts":      2,
    "draft_email":          2,
    "open_url":             2,
    "open_path":            2,
    "spawn_task":           2,
    "run_command":          2,
    "generate_image":       2,   # calls the Gemini image API (network)
    "generate_video":       2,   # calls the Google Veo API (network)
    "generate_music":       2,   # calls the Lyria 3 API (network)
    "compose_timeline":     1,   # local FFmpeg assembly — no network
    "create_presentation":  2,   # routed text model may be a cloud provider
    "create_website":       2,   # routed text model may be a cloud provider
    # Ring 3 — FULL OS CONTROL (requires CC permission)
    "install_package":      3,
    "move_mouse":           3,
    "click":                3,
    "type_text":            3,
    "press_key":            3,
    "screenshot":           3,
    "scroll":               3,
}


# ═══════════════════════════════════════════════════════════════════════════
#  CREATIVE PIPELINE TOOLS — Series Bible, multi-stage pipelines, take compare.
#  Let Friday manage creative projects and run pipelines from chat. Registered
#  late (after the registries above exist) via append/update, like MCP tools.
# ═══════════════════════════════════════════════════════════════════════════

def _tool_creative_project(inp):
    """Manage the active creative project's Series Bible (create / add cast /
    locations / continuity / list / activate)."""
    from agent_friday.services import creative_memory as cm
    inp = inp or {}
    action = (inp.get("action") or "").strip().lower()
    pid = (inp.get("project_id") or "").strip() or cm.get_active_project_id()
    try:
        if action == "create":
            b = cm.create_project(inp.get("name") or "Untitled Project",
                                  inp.get("type") or "general")
            return json.dumps({"status": "ok", "project_id": b["id"],
                               "message": f"Created project '{b['name']}' and made it active."})
        if action == "activate" and pid:
            cm.set_active_project(pid)
            return f"Activated project {pid}."
        if action in ("list", "list_projects"):
            return json.dumps({"status": "ok", "projects": cm.list_projects()}, default=str)
        if not pid:
            return "No active project. Create one first (action='create')."
        if action == "add_character":
            rec = cm.add_character(pid, inp.get("name") or "",
                                   visual_description=inp.get("visual_description") or "",
                                   voice_profile=inp.get("voice_profile") or "")
            return (f"Added/updated character {rec['name']}." if rec
                    else "Could not add character (name required).")
        if action == "add_location":
            rec = cm.add_location(pid, inp.get("name") or "",
                                  description=inp.get("description") or "")
            return (f"Added location {rec['name']}." if rec
                    else "Could not add location (name required).")
        if action == "add_continuity":
            e = cm.add_continuity(pid, inp.get("note") or "", scene=inp.get("scene") or "")
            return ("Logged continuity note." if e else "Note required.")
        if action in ("show", "get", "bible"):
            return json.dumps(cm.get_project(pid) or {}, default=str)[:4000]
        return f"Unknown action '{action}'. Try create/activate/add_character/add_location/add_continuity/show/list."
    except Exception as e:
        return f"creative_project error: {e}"


def _tool_start_creative_pipeline(inp):
    """Kick off a multi-stage creative pipeline (e.g. Research→Brief→Draft→Review)."""
    from agent_friday.services import creative_pipeline as cp
    from agent_friday.services import creative_memory as cm
    inp = inp or {}
    pipeline_id = (inp.get("pipeline_id") or "research-brief-draft-review").strip()
    pipe_input = inp.get("input")
    if not isinstance(pipe_input, dict):
        # Convenience: a bare topic/logline string.
        topic = inp.get("topic") or inp.get("input") or ""
        pipe_input = {"topic": topic, "logline": topic}
    project_id = (inp.get("project_id") or "").strip() or cm.get_active_project_id()
    run = cp.create_run(pipeline_id, pipe_input, project_id=project_id)
    if run.get("status") == "error":
        return json.dumps(run)
    cp.start_async(run["run_id"])
    fresh = cp.get_run(run["run_id"]) or run
    return json.dumps({
        "status": "ok", "run_id": run["run_id"], "state": fresh.get("state"),
        "milestones": fresh.get("milestones", []),
        "message": (f"Started pipeline '{fresh.get('name')}'. A progress orb is "
                    f"tracking it; it will pause at the first checkpoint for your "
                    f"review. Check status with the run_id."),
    }, default=str)


def _tool_compare_image_takes(inp):
    """Generate several image candidates and recommend the best (take comparison)."""
    from agent_friday.services import take_comparison as tc
    inp = inp or {}
    prompt = (inp.get("prompt") or "").strip()
    if not prompt:
        return "compare_image_takes error: 'prompt' is required."
    res = tc.compare_images(prompt, n=inp.get("n", 3), style=inp.get("style"),
                            aspect_ratio=inp.get("aspect_ratio") or "1:1",
                            intent=inp.get("intent") or prompt)
    if res.get("status") != "ok":
        return res.get("message") or res.get("reason") or f"take comparison {res.get('status')}"
    rec = res.get("recommended") or {}
    lines = [f"Generated {len(res.get('takes', []))} takes. "
             f"Recommended: take {rec.get('take')} "
             f"(score {rec.get('score')}) — {rec.get('filename')}."]
    for t in res.get("takes", []):
        if t.get("status") == "ok":
            lines.append(f"  • take {t['take']}: {t.get('filename')} "
                         f"(score {t.get('score')}) {t.get('critique') or ''}")
    return "\n".join(lines)


CLAUDE_TOOLS.extend([
    {
        "name": "creative_project",
        "description": (
            "Manage the user's creative project Series Bible — persistent memory "
            "for a video series, card deck, album, storybook, etc. Characters you "
            "add here (with a visual description) automatically propagate their "
            "look to every image/video you generate, so the same character stays "
            "consistent. Actions: create, activate, add_character, add_location, "
            "add_continuity, show, list."),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "create | activate | add_character | add_location | add_continuity | show | list"},
                "name": {"type": "string", "description": "Project/character/location name."},
                "type": {"type": "string", "description": "Project type (video-series, card, album, storybook, …) for action=create."},
                "visual_description": {"type": "string", "description": "Canonical look of a character (action=add_character)."},
                "voice_profile": {"type": "string", "description": "Voice/tone profile of a character (action=add_character)."},
                "description": {"type": "string", "description": "Location description (action=add_location)."},
                "note": {"type": "string", "description": "Continuity fact to log (action=add_continuity)."},
                "scene": {"type": "string", "description": "Optional scene label for a continuity note."},
                "project_id": {"type": "string", "description": "Target project id; defaults to the active project."},
            },
            "required": ["action"],
        },
    },
    {
        "name": "start_creative_pipeline",
        "description": (
            "Run a multi-stage creative pipeline that chains workspaces with typed "
            "hand-offs and shows milestone progress (e.g. 'research-brief-draft-"
            "review' or 'concept-storyboard-shots'). It pauses at checkpoints so "
            "the user can steer. Use when the user asks to take something from idea "
            "to finished piece in stages, or asks for a 'pipeline'/'workflow'."),
        "input_schema": {
            "type": "object",
            "properties": {
                "pipeline_id": {"type": "string", "description": "Pipeline template id (default 'research-brief-draft-review')."},
                "topic": {"type": "string", "description": "The topic/logline to seed the first stage (convenience for simple pipelines)."},
                "input": {"type": "object", "description": "Typed initial context object matching the pipeline's first-stage input schema."},
                "project_id": {"type": "string", "description": "Optional creative project to attach the run to."},
            },
            "required": [],
        },
    },
    {
        "name": "compare_image_takes",
        "description": (
            "Generate 2–4 image candidates for one prompt, have Friday score each, "
            "and recommend the best. Use for important visual decisions when the "
            "user wants options ('give me a few', 'show me some takes', 'pick the "
            "best one')."),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "What to generate."},
                "n": {"type": "integer", "description": "How many takes (2–4, default 3)."},
                "style": {"type": "string", "description": "Optional style preset."},
                "aspect_ratio": {"type": "string", "description": "Optional aspect ratio (default 1:1)."},
                "intent": {"type": "string", "description": "Optional explicit success criteria used to score takes."},
            },
            "required": ["prompt"],
        },
    },
])

CLAUDE_TOOL_HANDLERS.update({
    "creative_project": _tool_creative_project,
    "start_creative_pipeline": _tool_start_creative_pipeline,
    "compare_image_takes": _tool_compare_image_takes,
})

TOOL_RINGS.update({
    "creative_project":        1,   # local Series-Bible state mutation
    "start_creative_pipeline": 2,   # drives generation/LLM calls (network)
    "compare_image_takes":     2,   # calls the Gemini image API (network)
})

CLAUDE_TOOL_HANDLERS.update({
    "create_workflow": _tool_create_workflow,
    "run_workflow": _tool_run_workflow,
    "workflow_status": _tool_workflow_status,
})

TOOL_RINGS.update({
    "create_workflow": 1,   # writes a JSON file under ~/.friday/workflows
    "run_workflow": 1,      # spawns local background tasks
    "workflow_status": 0,   # read-only
})


# ═══════════════════════════════════════════════════════════════════════════
#  CONTENT PIPELINE TOOLS — social publishing from chat/voice (spec §10.2/§11).
#  Thin wrappers over services.content_pipeline / content_composer plus the
#  routes-hosted §6.4 optimal-time resolver, so voice and chat drive the same
#  pipeline with no new privilege surface. All four ride Ring 2 (spec §11 —
#  same governance as every network tool); the actual publish still runs the
#  publisher's moderation + egress gates, so nothing ships silently.
#  Imports stay lazy — the registrations below are data only.
# ═══════════════════════════════════════════════════════════════════════════

def _content_deeplink(tab: str, post_id=None) -> str:
    """useNavTarget deep link into the Content workspace (Queue/Compose)."""
    link = f"/?workspace=content&tab={tab}"
    return f"{link}&post={post_id}" if post_id else link


_WHEN_HOUR_WORDS = {"morning": 9, "noon": 12, "midday": 12, "afternoon": 15,
                    "evening": 18, "tonight": 20, "night": 20}
_WHEN_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                  "friday": 4, "saturday": 5, "sunday": 6}


def _content_parse_when(text, tz_name):
    """Parse a publish instant: ISO-8601 first, then a small natural-language
    vocabulary ('tomorrow morning', 'tonight', 'friday 3pm', 'in 2 hours'),
    interpreted in the post's timezone. Returns a UTC ISO string, or None so
    the caller falls back to the optimal-time resolver."""
    from datetime import timezone as _tzu
    from agent_friday.services import content_pipeline as _cp
    raw = str(text or "").strip()
    if not raw:
        return None
    iso = _cp._to_utc_iso(raw)
    if iso:
        return iso
    s = raw.lower()
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name or _cp._default_timezone())
    except Exception:
        tz = _tzu.utc
    now = datetime.now(tz)
    m = re.search(r"\bin\s+(\d+)\s*(minute|min|hour|hr|day)s?\b", s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = (timedelta(minutes=n) if unit in ("minute", "min")
                 else timedelta(hours=n) if unit in ("hour", "hr")
                 else timedelta(days=n))
        return _cp._to_utc_iso(now + delta)
    day_offset = None
    if "day after tomorrow" in s:
        day_offset = 2
    elif "tomorrow" in s:
        day_offset = 1
    elif "today" in s or "tonight" in s or "this " in s:
        day_offset = 0
    weekday = next((v for k, v in _WHEN_WEEKDAYS.items() if k in s), None)
    hour, minute = None, 0
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", s)
    if m and (m.group(2) or m.group(3) or re.search(r"\bat\s+\d", s)):
        hour = int(m.group(1)) % 12 if m.group(3) else int(m.group(1))
        if m.group(3) == "pm":
            hour += 12
        minute = int(m.group(2) or 0)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            hour, minute = None, 0
    if hour is None:
        for word, h in _WHEN_HOUR_WORDS.items():
            if word in s:
                hour = h
                break
    if day_offset is None and weekday is None and hour is None:
        return None                    # nothing recognized
    if hour is None:
        hour = 9                       # bare day word → morning
    cand = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if weekday is not None:
        ahead = (weekday - cand.weekday()) % 7
        if ahead == 0 and cand <= now:
            ahead = 7
        cand += timedelta(days=ahead)
    else:
        cand += timedelta(days=day_offset or 0)
        if cand <= now:                # 'this morning' already past → tomorrow
            cand += timedelta(days=1)
    return _cp._to_utc_iso(cand)


def _content_apply_schedule(post_id, when_text, optimal, tz_name):
    """Shared create/schedule rail: parse the instant (or resolve the optimal
    slot), store the ScheduleConfig, and report same-platform conflicts.
    Returns the schedule_post envelope + {publish_at, timezone, resolved,
    conflicts, warnings}. Never raises."""
    try:
        from agent_friday.services import content_pipeline as _cp
        # The §6.4 optimal-time resolver + conflict scan live with the routes.
        from agent_friday.routes import content_pipeline as _croutes
        got = _cp.get_post(post_id)
        if not got.get("ok"):
            return got
        post = got["post"]
        platforms = [t.get("platform") for t in (post.get("targets") or [])
                     if t.get("platform")]
        tz_name = (tz_name or (post.get("schedule") or {}).get("timezone")
                   or _cp._default_timezone())
        warnings = []
        publish_at = _content_parse_when(when_text, tz_name) if when_text else None
        resolved = "parsed" if publish_at else "optimal"
        cs = _croutes._content_settings()
        if not publish_at:
            if when_text and not optimal:
                warnings.append(f"could not parse '{when_text}' — picked the "
                                "next optimal slot instead")
            publish_at = _croutes._resolve_optimal(
                platforms, tz_name, cs["conflict_window_hours"])
        sched = _cp.new_schedule_config(publish_at=publish_at, tz=tz_name,
                                        optimal_time=(resolved == "optimal"))
        res = _cp.schedule_post(post_id, sched)
        if not res.get("ok"):
            return res
        res["publish_at"] = publish_at
        res["timezone"] = tz_name
        res["resolved"] = resolved
        res["conflicts"] = _croutes._find_conflicts(
            platforms, publish_at, cs["conflict_window_hours"],
            exclude_post=post_id)
        res["warnings"] = warnings
        return res
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _content_coerce_assets(raw):
    """Creation filenames (or dicts) → AssetRef list."""
    from agent_friday.services import content_pipeline as _cp
    out = []
    for a in (raw or []):
        if isinstance(a, dict):
            out.append(_cp.new_asset_ref(a.get("filename") or "",
                                         a.get("content_hash") or "",
                                         a.get("kind") or "image",
                                         a.get("alt_text") or ""))
        elif a:
            out.append(_cp.new_asset_ref(str(a)))
    return out


def _tool_content_create_post(inp):
    """§10.2 voice path: create draft → compose per platform → (optionally)
    schedule, in one call — answers with the Queue deep link."""
    from agent_friday.services import content_pipeline as _cp
    from agent_friday.services import content_composer as _cc
    inp = inp or {}
    body = (inp.get("body") or "").strip()
    if not body:
        return "content_create_post error: 'body' is required."
    platforms = [str(p).strip().lower() for p in (inp.get("platforms") or []) if p]
    if not platforms:
        return ("content_create_post error: 'platforms' is required, "
                "e.g. ['linkedin', 'bluesky'].")
    created = _cp.create_post(
        title=(inp.get("title") or "").strip(), body=body,
        assets=_content_coerce_assets(inp.get("assets")),
        platforms=platforms, tags=list(inp.get("tags") or []),
        source={"kind": "chat", "ref": ""})
    if not created.get("ok"):
        return f"content_create_post error: {created.get('error')}"
    post = created["post"]
    warnings = list(created.get("warnings") or [])
    composed = _cc.adapt(post, platforms=platforms)
    if composed.get("ok"):
        warnings += composed.get("warnings") or []
    else:
        warnings.append(f"compose failed: {composed.get('error')} — "
                        "targets keep the canonical body")
    when = str(inp.get("publish_at") or "").strip()
    optimal = bool(inp.get("optimal_time"))
    out = {"status": "ok", "post_id": post["id"], "post_status": "DRAFT",
           "platforms": platforms,
           "queue_link": _content_deeplink("queue", post["id"])}
    if when or optimal:
        sched = _content_apply_schedule(post["id"], when, optimal,
                                        inp.get("timezone"))
        if sched.get("ok"):
            out["post_status"] = "SCHEDULED"
            out["publish_at"] = sched.get("publish_at")
            out["timezone"] = sched.get("timezone")
            out["time_source"] = sched.get("resolved")
            warnings += sched.get("warnings") or []
            if sched.get("conflicts"):
                warnings.append(f"{len(sched['conflicts'])} same-platform "
                                "post(s) within the conflict window")
            out["message"] = (f"Scheduled for {sched.get('publish_at')} "
                              f"({out['time_source']}) — review or reschedule "
                              f"in the Queue: {out['queue_link']}")
        else:
            warnings.append(f"schedule failed: {sched.get('error')}")
            out["message"] = ("Draft created and composed, but not scheduled — "
                              + _content_deeplink("compose", post["id"]))
    else:
        out["message"] = ("Draft created and composed per platform. Schedule "
                          "with content_schedule_post, or review: "
                          + _content_deeplink("compose", post["id"]))
    if warnings:
        out["warnings"] = warnings
    return json.dumps(out, default=str)


def _tool_content_schedule_post(inp):
    """Schedule/reschedule an existing ContentPost; composes any target that
    has no adapted body yet, then resolves the instant (parsed or optimal)."""
    from agent_friday.services import content_pipeline as _cp
    from agent_friday.services import content_composer as _cc
    inp = inp or {}
    post_id = (inp.get("post_id") or "").strip()
    if not post_id:
        return "content_schedule_post error: 'post_id' is required."
    got = _cp.get_post(post_id)
    if not got.get("ok"):
        return f"content_schedule_post error: {got.get('error')}"
    post = got["post"]
    warnings = []
    if any(not (t.get("adapted_body") or "")
           for t in (post.get("targets") or [])
           if t.get("status") not in _cp.TARGET_TERMINAL):
        composed = _cc.adapt(post)
        if composed.get("ok"):
            warnings += composed.get("warnings") or []
        else:
            warnings.append(f"compose failed: {composed.get('error')}")
    when = str(inp.get("publish_at") or "").strip()
    res = _content_apply_schedule(
        post_id, when, bool(inp.get("optimal_time", not when)),
        inp.get("timezone"))
    if not res.get("ok"):
        return f"content_schedule_post error: {res.get('error')}"
    warnings += res.get("warnings") or []
    if res.get("conflicts"):
        warnings.append(f"{len(res['conflicts'])} same-platform post(s) "
                        "within the conflict window")
    out = {"status": "ok", "post_id": post_id, "post_status": "SCHEDULED",
           "publish_at": res.get("publish_at"),
           "timezone": res.get("timezone"),
           "time_source": res.get("resolved"),
           "queue_link": _content_deeplink("queue", post_id),
           "message": (f"Scheduled for {res.get('publish_at')} "
                       f"({res.get('resolved')}) — Queue: "
                       + _content_deeplink("queue", post_id))}
    if warnings:
        out["warnings"] = warnings
    return json.dumps(out, default=str)


def _tool_content_post_status(inp):
    """One post's per-platform delivery status, or (no post_id) the queue
    overview: upcoming targets, HELD posts awaiting release, recent history."""
    from agent_friday.services import content_pipeline as _cp
    inp = inp or {}
    post_id = (inp.get("post_id") or "").strip()
    if post_id:
        got = _cp.get_post(post_id)
        if not got.get("ok"):
            return f"content_post_status error: {got.get('error')}"
        p = got["post"]
        targets = [{"target_id": t.get("id"), "platform": t.get("platform"),
                    "format": t.get("format"), "status": t.get("status"),
                    "publish_at": t.get("publish_at"),
                    "post_url": t.get("post_url"), "error": t.get("error")}
                   for t in (p.get("targets") or [])]
        out = {"status": "ok", "post_id": post_id,
               "post_status": p.get("status"), "title": p.get("title") or "",
               "publish_at": (p.get("schedule") or {}).get("publish_at"),
               "timezone": (p.get("schedule") or {}).get("timezone"),
               "targets": targets,
               "queue_link": _content_deeplink("queue", post_id)}
        held = sum(1 for t in targets if t["status"] == "HELD")
        if held:
            out["held_note"] = (f"{held} target(s) HELD — the egress gate "
                                "flagged possibly-private content; the user "
                                "must review and release them in the Queue.")
        return json.dumps(out, default=str)
    upcoming, held = [], []
    for st in ("SCHEDULED", "PUBLISHING", "HELD"):
        for p in (_cp.list_posts(status=st, limit=100).get("posts") or []):
            for t in (p.get("targets") or []):
                row = {"post_id": p.get("id"), "title": p.get("title") or "",
                       "platform": t.get("platform"), "status": t.get("status"),
                       "publish_at": t.get("publish_at")}
                if t.get("status") == "HELD":
                    held.append(row)
                elif t.get("status") in ("PENDING", "PREPARING", "SENT"):
                    upcoming.append(row)
    upcoming.sort(key=lambda r: r.get("publish_at") or "9999")
    recent = _cp.read_publish_log(limit=10).get("entries") or []
    return json.dumps({"status": "ok", "upcoming": upcoming[:15], "held": held,
                       "recent_history": recent,
                       "queue_link": _content_deeplink("queue")}, default=str)


def _tool_content_repurpose(inp):
    """One piece → a spread of platform-native drafts (§9), each individually
    editable/schedulable. Source = body text or an existing post."""
    from agent_friday.services import content_pipeline as _cp
    from agent_friday.services import content_composer as _cc
    from agent_friday.routes import content_pipeline as _croutes  # §9.2 spreads
    inp = inp or {}
    body = (inp.get("body") or "").strip()
    title = (inp.get("title") or "").strip()
    tags = list(inp.get("tags") or [])
    assets = _content_coerce_assets(inp.get("assets"))
    src_ref = (inp.get("post_id") or "").strip()
    if not body and src_ref:
        got = _cp.get_post(src_ref)
        if not got.get("ok"):
            return f"content_repurpose error: {got.get('error')}"
        src = got["post"]
        body = (src.get("body") or "").strip()
        title = title or src.get("title") or ""
        assets = assets or list(src.get("assets") or [])
        tags = tags or list(src.get("tags") or [])
    if not body:
        return ("content_repurpose error: pass 'body' text or the 'post_id' "
                "of an existing content post.")
    spread = [str(p).strip().lower() for p in (inp.get("platforms") or []) if p]
    if not spread:
        kinds = [a.get("kind") for a in assets]
        dominant = ("video" if "video" in kinds else
                    "image" if "image" in kinds else
                    "audio" if "audio" in kinds else "text")
        spread = list(_croutes._DEFAULT_SPREADS[dominant])
    created = _cp.create_post(
        title=title, body=body, assets=assets, platforms=spread, tags=tags,
        source={"kind": "repurpose", "ref": src_ref,
                "src_kind": "post" if src_ref else "chat"})
    if not created.get("ok"):
        return f"content_repurpose error: {created.get('error')}"
    post = created["post"]
    warnings = list(created.get("warnings") or [])
    adapted = _cc.adapt(post, platforms=spread)
    if adapted.get("ok"):
        warnings += adapted.get("warnings") or []
    else:
        warnings.append(f"compose failed: {adapted.get('error')}")
    out = {"status": "ok", "post_id": post["id"], "spread": spread,
           "compose_link": _content_deeplink("compose", post["id"]),
           "message": (f"Repurposed into {len(spread)} platform-native drafts "
                       "— each is individually editable before scheduling. "
                       "Schedule with content_schedule_post when ready.")}
    if warnings:
        out["warnings"] = warnings
    return json.dumps(out, default=str)


CLAUDE_TOOLS.extend([
    {
        "name": "content_create_post",
        "description": (
            "Create a social-media post in the Content pipeline: saves a "
            "draft, adapts it per platform (voice, char limits, hashtags, "
            "threads), and optionally schedules it — one call covers 'post "
            "this to LinkedIn and Bluesky tomorrow morning'. publish_at takes "
            "ISO-8601 UTC or a natural phrase ('tomorrow morning', 'tonight', "
            "'friday 3pm'); or set optimal_time to let the best-times engine "
            "pick the slot. Nothing ships silently — every publish still "
            "passes moderation and the egress gate (private data → HELD for "
            "the user's review). Returns the post id and a Queue deep link."),
        "input_schema": {
            "type": "object",
            "properties": {
                "body": {"type": "string", "description": "The post text (markdown) — the canonical body the composer adapts per platform."},
                "platforms": {"type": "array", "items": {"type": "string"}, "description": "Target platforms: linkedin, twitter, instagram, youtube, bluesky, mastodon, reddit, substack, medium, tiktok, federation."},
                "title": {"type": "string", "description": "Optional working title (used by platforms with a title field)."},
                "publish_at": {"type": "string", "description": "When to publish: ISO-8601 UTC or a natural phrase like 'tomorrow morning'. Omit to leave a draft (or set optimal_time)."},
                "optimal_time": {"type": "boolean", "description": "Let the best-times engine pick the next optimal slot."},
                "timezone": {"type": "string", "description": "IANA timezone for interpreting publish_at (default: the user's settings timezone)."},
                "assets": {"type": "array", "items": {"type": "string"}, "description": "Optional creation filenames from the Studio gallery to attach."},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional tags (e.g. nsfw) — mapped to platform sensitivity flags."},
            },
            "required": ["body", "platforms"],
        },
    },
    {
        "name": "content_schedule_post",
        "description": (
            "Schedule or reschedule an existing content post by id. Composes "
            "any platform target that hasn't been adapted yet, then sets the "
            "publish time: publish_at (ISO-8601 UTC or a natural phrase) or "
            "optimal_time (default when no time is given — the best-times "
            "engine picks). Returns the resolved instant and a Queue deep "
            "link; warns about same-platform conflicts."),
        "input_schema": {
            "type": "object",
            "properties": {
                "post_id": {"type": "string", "description": "The ContentPost id (from content_create_post / content_post_status)."},
                "publish_at": {"type": "string", "description": "ISO-8601 UTC or a natural phrase ('tomorrow morning', 'in 2 hours')."},
                "optimal_time": {"type": "boolean", "description": "Pick the next optimal slot from the best-times engine."},
                "timezone": {"type": "string", "description": "IANA timezone for interpreting publish_at."},
            },
            "required": ["post_id"],
        },
    },
    {
        "name": "content_post_status",
        "description": (
            "Check the content pipeline. With post_id: that post's per-"
            "platform delivery status (PENDING/SENT/CONFIRMED/HELD/FAILED, "
            "post URLs, errors). Without post_id: the queue overview — "
            "upcoming scheduled targets, HELD posts awaiting the user's "
            "release, and recent publish history. HELD means the egress gate "
            "flagged possibly-private content; only the user can release it."),
        "input_schema": {
            "type": "object",
            "properties": {
                "post_id": {"type": "string", "description": "Optional ContentPost id for a single-post drilldown."},
            },
        },
    },
    {
        "name": "content_repurpose",
        "description": (
            "Turn one piece of content into a spread of platform-native "
            "drafts — e.g. a blog post becomes a LinkedIn post + X thread + "
            "Bluesky/Mastodon posts + newsletter section, each written for "
            "its platform (never the same caption pasted N times). Source: "
            "body text or the post_id of an existing content post. Creates "
            "DRAFTs only — review/edit, then schedule with "
            "content_schedule_post."),
        "input_schema": {
            "type": "object",
            "properties": {
                "body": {"type": "string", "description": "The source text to repurpose (or pass post_id instead)."},
                "post_id": {"type": "string", "description": "Existing ContentPost id to repurpose (body/title/assets are pulled from it)."},
                "platforms": {"type": "array", "items": {"type": "string"}, "description": "Custom spread; omit for the default spread by content kind."},
                "title": {"type": "string", "description": "Optional title override."},
                "assets": {"type": "array", "items": {"type": "string"}, "description": "Optional creation filenames to fan out with the spread."},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional tags."},
            },
        },
    },
])

CLAUDE_TOOL_HANDLERS.update({
    "content_create_post":   _tool_content_create_post,
    "content_schedule_post": _tool_content_schedule_post,
    "content_post_status":   _tool_content_post_status,
    "content_repurpose":     _tool_content_repurpose,
})

TOOL_RINGS.update({
    # Spec §11: all four ride Ring 2 — governance-gated like every network
    # tool (scheduling arms a real outbound publish; status reads the same
    # surface, and the spec keeps the whole set behind one gate).
    "content_create_post":   2,
    "content_schedule_post": 2,
    "content_post_status":   2,
    "content_repurpose":     2,
})


def _tool_knowledge_query(inp):
    """Structural knowledge-graph query — zero LLM calls, works offline."""
    question = ((inp or {}).get("question") or "").strip()
    if not question:
        return "knowledge_query needs a question."
    from agent_friday.services.knowledge_graph import structural_query
    result = structural_query.query(question)
    return json.dumps(result, ensure_ascii=False)


CLAUDE_TOOLS.append({
    "name": "knowledge_query",
    "description": (
        "Query Friday's knowledge graph (the wiki as a linked graph). "
        "Answers from graph structure alone — instant, offline, no LLM: "
        "ranked candidate pages, multi-hop paths ('how is X related to Y'), "
        "hub pages, and a should_read shortlist of the 2-3 pages worth "
        "opening with read_wiki for full detail. Use this BEFORE reading "
        "wiki pages speculatively."),
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {"type": "string",
                         "description": "Natural-language question about the knowledge base."},
        },
        "required": ["question"],
    },
})

def _tool_knowledge_related(inp):
    """Neighbours of a graph node (ego-graph) — structural, no LLM."""
    node_id = ((inp or {}).get("node") or "").strip()
    depth = min(max(int((inp or {}).get("depth") or 1), 1), 3)
    if not node_id:
        return "knowledge_related needs a node id or title."
    from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore
    store = KnowledgeGraphStore()
    ents = store.load("entities")
    node = next((e for e in ents if e["id"] == node_id
                 or e.get("title", "").lower() == node_id.lower()), None)
    if node is None:
        return f"No graph node matching '{node_id}'."
    rels = store.load("relationships")
    frontier, seen = {node["id"]}, {node["id"]}
    adj = {}
    for r in rels:
        adj.setdefault(r["source"], set()).add(r["target"])
        adj.setdefault(r["target"], set()).add(r["source"])
    for _ in range(depth):
        frontier = {nb for n in frontier for nb in adj.get(n, ())} - seen
        seen |= frontier
    related = [e for e in ents if e["id"] in seen and e["id"] != node["id"]]
    return json.dumps({"node": {"id": node["id"], "title": node["title"]},
                       "related": [{"id": e["id"], "title": e["title"],
                                    "type": e.get("type")}
                                   for e in related[:30]]}, ensure_ascii=False)


def _tool_knowledge_communities(inp):
    """Thematic map of the knowledge base: communities + LLM reports."""
    from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore
    store = KnowledgeGraphStore()
    comms = store.load("communities")
    reports = {r.get("community"): r for r in store.load("community_reports")}
    out = []
    for c in sorted(comms, key=lambda c: -c.get("size", 0))[:20]:
        rep = reports.get(c.get("community"))
        out.append({"id": c["id"], "title": c.get("title"),
                    "size": c.get("size"),
                    "summary": (rep or {}).get("summary", "")})
    return json.dumps({"communities": out}, ensure_ascii=False)


CLAUDE_TOOLS.extend([
    {
        "name": "knowledge_related",
        "description": (
            "List graph neighbours of a knowledge-graph node (wiki page or "
            "extracted entity) up to depth 3. Structural — instant, no LLM."),
        "input_schema": {
            "type": "object",
            "properties": {
                "node": {"type": "string",
                         "description": "Node id (e.g. page:research/graphrag) or exact title."},
                "depth": {"type": "integer", "description": "1-3 hops (default 1)."},
            },
            "required": ["node"],
        },
    },
    {
        "name": "knowledge_communities",
        "description": (
            "The thematic map of Friday's knowledge base: communities of "
            "related pages/entities with their LLM-written summaries. Use "
            "for 'what are the big areas of what I know' questions."),
        "input_schema": {"type": "object", "properties": {}},
    },
])

CLAUDE_TOOL_HANDLERS.update({
    "knowledge_query": _tool_knowledge_query,
    "knowledge_related": _tool_knowledge_related,
    "knowledge_communities": _tool_knowledge_communities,
})

TOOL_RINGS.update({
    "knowledge_query": 0,         # read-only, fully local
    "knowledge_related": 0,
    "knowledge_communities": 0,
})

# ══════════════════════════════════════════════════════════════
#  CAPABILITY PREFLIGHT — a tool whose dependency is missing is REMOVED
# ══════════════════════════════════════════════════════════════
#
# Registration above is unconditional: the ring-3 OS-control tools go into
# CLAUDE_TOOLS whether or not pyautogui imported, and _cc_check turns every
# call into "pyautogui not installed" at execution time. That is the
# present-but-broken shape — the model is handed a screenshot tool, tells the
# user it is taking a screenshot, and only then learns it cannot.
#
# The registry is the single source of truth for every surface (text chat, the
# local voice brain, and — since 2026-08-25 — the Gemini Live surface, which
# resolves its filesystem tools out of this same list). So dropping a tool HERE
# removes it from all of them at once, and the generated surface notes stop
# naming it in the same edit. Absent beats present-but-broken.
#
# services/capability_preflight.py owns the declared inventory and the reason
# each entry exists. Optional-by-design capabilities (Presidio, torch) never
# reach missing_tools() — they withhold nothing.
try:
    from agent_friday.services import capability_preflight as _cap_preflight
    _WITHHELD_TOOLS = _cap_preflight.missing_tools()
    if _WITHHELD_TOOLS:
        CLAUDE_TOOLS[:] = [t for t in CLAUDE_TOOLS
                           if t.get("name") not in _WITHHELD_TOOLS]
        for _wt in _WITHHELD_TOOLS:
            CLAUDE_TOOL_HANDLERS.pop(_wt, None)
            TOOL_RINGS.pop(_wt, None)
        print("  [CAPABILITY] withheld %d tool(s) with missing dependencies: %s"
              % (len(_WITHHELD_TOOLS), ", ".join(sorted(_WITHHELD_TOOLS))))
    for _line in _cap_preflight.report():
        print("  " + _line)
except Exception as _cpe:   # never let the preflight break the agent import
    _WITHHELD_TOOLS = frozenset()
    print("  [CAPABILITY] preflight skipped: %s" % _cpe)


# Self-QC + asset tools (inspect_image / inspect_audio / save_output). Added
# after the storybook E2E test showed the seat generating media it could not
# look at, listen to, or reliably save — see services/media_tools.py.
try:
    from agent_friday.services import media_tools as _media_tools
    _media_tools.register(CLAUDE_TOOLS, CLAUDE_TOOL_HANDLERS, TOOL_RINGS)
except Exception as _mte:  # never let optional deps break the agent import
    print(f"  [MEDIA-TOOLS] registration skipped: {_mte}")

# ElevenLabs speech (speak_text / list_voices). The seat could listen to audio
# and save a provider's output but could not produce speech — narration was a
# hole in the middle of the storybook pipeline. See services/elevenlabs_tools.py.
try:
    from agent_friday.services import elevenlabs_tools as _elevenlabs_tools
    _elevenlabs_tools.register(CLAUDE_TOOLS, CLAUDE_TOOL_HANDLERS, TOOL_RINGS)
except Exception as _ete:  # never let optional deps break the agent import
    print(f"  [ELEVENLABS] registration skipped: {_ete}")

# Interactive CLI sessions (spawn_interactive_session / send_to_session /
# read_session_output) — Ring 3, same tier as Computer Control. See
# services/interactive_sessions.py's module docstring for the full security
# posture (recursion guard, buffer cap, boot-time orphan reap).
try:
    from agent_friday.services import interactive_sessions as _interactive_sessions
    _interactive_sessions.register(CLAUDE_TOOLS, CLAUDE_TOOL_HANDLERS, TOOL_RINGS)
except Exception as _ise:  # never let optional deps break the agent import
    print(f"  [SESSIONS] registration skipped: {_ise}")


_GOVERNANCE_KEY: bytes | None = None


def _get_governance_key() -> bytes:
    """Return the HMAC signing key for BOM entries.

    Delegates to proof_of_integrity.get_governance_key() (OS keychain → file
    fallback → generate) so there is one canonical implementation.
    """
    global _GOVERNANCE_KEY
    if _GOVERNANCE_KEY is not None:
        return _GOVERNANCE_KEY
    try:
        from agent_friday.governance.proof_of_integrity import get_governance_key as _poi_gk
        _GOVERNANCE_KEY = _poi_gk()
        return _GOVERNANCE_KEY
    except Exception as _e:
        import logging as _log
        _log.getLogger(__name__).error("governance key unavailable: %s — BOM entries will not be signed", _e)
    import os as _os
    _GOVERNANCE_KEY = _os.urandom(32)  # ephemeral fallback, not persisted
    return _GOVERNANCE_KEY


# ── Sovereign Vault: encryption-at-rest ──────────────────────────────
# Transparent AES-256-GCM for sensitive files (finance, health, legal,
# family). The key is derived once from FRIDAY_PASSWORD via Argon2id — see
# vault_crypto.py. When no password is set (or the crypto deps are missing)
# the key is None and every helper falls back to plaintext, so behaviour is
# unchanged for the keyless local-dev case.
try:
    import agent_friday.privacy.vault_crypto as _vc
    _HAS_VAULT_CRYPTO = True
except Exception:
    _vc = None
    _HAS_VAULT_CRYPTO = False

_VAULT_KEY: bytes | None = None
_VAULT_KEY_READY = False
_VAULT_CONFIG_FILE = FRIDAY_DIR / "vault" / ".vault_config.json"


def _get_vault_key() -> bytes | None:
    """Derive (once) the AES-256 vault key from the resolved vault passphrase.

    Resolution order lives in ONE place — services/vault_passphrase.py — and is
    documented there. Returns the 32-byte key, or None when encryption is
    unavailable. On None, callers fall back to plaintext; vault encryption
    failure is logged at ERROR and surfaces in /api/health as a persistent
    warning banner.

    This docstring used to promise "tries the OS keychain before the
    environment variable so the passphrase never needs to appear in a shell
    script" while the code did the opposite — and since
    core._bootstrap_env_from_launch_scripts loads start.bat's SET lines into
    os.environ at import, "environment first" meant "start.bat first". The
    promise is now kept, with one deliberate refinement: an environment
    variable a HUMAN exported still outranks the keychain. Only one that came
    out of a launch script is demoted.
    """
    import logging as _logging
    _vlog = _logging.getLogger(__name__)

    global _VAULT_KEY, _VAULT_KEY_READY
    if _VAULT_KEY_READY:
        return _VAULT_KEY
    _VAULT_KEY_READY = True

    from agent_friday.services import vault_passphrase as _vp
    _passphrase = _vp.resolve()[0]

    if not _HAS_VAULT_CRYPTO or not _passphrase:
        if not _passphrase:
            _VAULT_ENCRYPTION_STATE["enabled"] = False
            _VAULT_ENCRYPTION_STATE["warning"] = (
                "Vault encryption is DISABLED — sensitive data is stored as plaintext at rest. "
                "Set FRIDAY_VAULT_PASSPHRASE or run: friday vault-setup"
            )
            _vlog.warning(
                "[vault] FRIDAY_VAULT_PASSPHRASE not set — sensitive data stored "
                "as PLAINTEXT at rest. Set the env var or run: friday vault-setup"
            )
        else:
            _VAULT_ENCRYPTION_STATE["enabled"] = False
            _VAULT_ENCRYPTION_STATE["error"] = "vault_crypto module unavailable"
            _vlog.error(
                "[vault] vault_crypto/cryptography unavailable — "
                "sensitive data stored as PLAINTEXT at rest."
            )
        _VAULT_KEY = None
        return None

    try:
        _VAULT_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        cfg = {}
        if _VAULT_CONFIG_FILE.exists():
            cfg = json.loads(_VAULT_CONFIG_FILE.read_text(encoding="utf-8"))
        salt_hex = cfg.get("salt_hex")
        if not salt_hex:
            salt_hex = os.urandom(16).hex()
            cfg.update({"salt_hex": salt_hex, "kdf": "argon2id", "cipher": "aes-256-gcm"})
            _tmp = _VAULT_CONFIG_FILE.with_name(_VAULT_CONFIG_FILE.name + ".tmp")
            _tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            _tmp.replace(_VAULT_CONFIG_FILE)
        _VAULT_KEY = _vc.derive_key(_passphrase, bytes.fromhex(salt_hex))
        _VAULT_ENCRYPTION_STATE["enabled"] = True
        _VAULT_ENCRYPTION_STATE["error"] = ""
        _VAULT_ENCRYPTION_STATE["warning"] = ""
        print("[vault] Encryption-at-rest ENABLED (AES-256-GCM · Argon2id).")
    except Exception as e:
        _VAULT_ENCRYPTION_STATE["enabled"] = False
        _VAULT_ENCRYPTION_STATE["error"] = str(e)
        _VAULT_ENCRYPTION_STATE["warning"] = (
            f"CRITICAL: Vault key derivation failed ({e}). "
            "Sensitive vault data may be unprotected. "
            "Check FRIDAY_VAULT_PASSPHRASE and the cryptography package installation."
        )
        _vlog.error(
            "[vault] CRITICAL: key derivation FAILED (%s) — "
            "falling back to PLAINTEXT.  This is a security failure.  "
            "Check FRIDAY_VAULT_PASSPHRASE and the cryptography package.",
            e,
        )
        _VAULT_KEY = None
    return _VAULT_KEY


def _vault_read_text(path) -> str:
    """Read a possibly-encrypted file as UTF-8 text.

    Decrypts when the file is a FRIDAYVAULT blob and a key is available;
    otherwise returns the bytes as text (handles plaintext + mixed states
    during rollover). Raises on an encrypted blob with no/incorrect key.
    """
    raw = Path(path).read_bytes()
    key = _get_vault_key()
    if _HAS_VAULT_CRYPTO and _vc.is_encrypted(raw):
        if key is None:
            raise RuntimeError("file is vault-encrypted but FRIDAY_PASSWORD is not set")
        return _vc.decrypt(raw, key).decode("utf-8")
    return raw.decode("utf-8")


def _vault_write_text(path, text: str) -> None:
    """Write text, encrypting at rest when a vault key is available. Atomic."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    key = _get_vault_key()
    if key is not None:
        data = _vc.encrypt(data, key)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(p)


# Sensitive directories whose file contents are encrypted at rest when a vault
# key is present. Scoped to the TIER_3 personal-data stores — NOT the wiki or
# the append-only audit logs (those are handled separately / kept plaintext).
def _sensitive_vault_dirs() -> list:
    dirs = [FRIDAY_DIR / "finance", FRIDAY_DIR / "health"]
    vault_root = FRIDAY_DIR / "vault"
    dirs += [vault_root / c for c in ("legal", "finances", "family")]
    # Opt-in encrypted wiki sections (settings.wiki_encrypted_sections) join
    # the same startup migration, so flipping the setting encrypts existing
    # files in place on next boot.
    try:
        from agent_friday.services.wiki_engine import _wiki_encrypted_section_dirs
        dirs += _wiki_encrypted_section_dirs()
    except Exception:
        pass
    return dirs


_VAULT_MIGRATE_SKIP = {".vault_config.json", ".governance-key",
                       "access-log.jsonl", "decision-bom.jsonl"}


def _migrate_vault_plaintext() -> None:
    """Encrypt any still-plaintext sensitive files in place (idempotent).

    Runs once at startup when a vault key is available. Verifies a decrypt
    round-trip before replacing each file; per-file try/except so a single
    failure never blocks boot. Files already encrypted are skipped.
    """
    key = _get_vault_key()
    if key is None or not _HAS_VAULT_CRYPTO:
        return
    migrated = 0
    for d in _sensitive_vault_dirs():
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if not p.is_file() or p.name in _VAULT_MIGRATE_SKIP or p.suffix == ".tmp":
                continue
            try:
                raw = p.read_bytes()
                if _vc.is_encrypted(raw):
                    continue
                blob = _vc.encrypt(raw, key)
                if _vc.decrypt(blob, key) != raw:   # prove recoverability first
                    continue
                tmp = p.with_name(p.name + ".tmp")
                tmp.write_bytes(blob)
                tmp.replace(p)
                migrated += 1
            except Exception as e:
                print(f"[vault] migrate skipped {p.name}: {e}")
    if migrated:
        print(f"[vault] encrypted {migrated} previously-plaintext sensitive file(s) at rest.")


def _governance_check(tool_name: str, args: dict, session_ctx: dict | None = None) -> tuple[bool, str]:
    """Policy gate executed before every tool call.

    Returns (allowed, reason). Appends a signed entry to decision-bom.jsonl
    regardless of outcome so every gate decision is auditable.

    session_ctx keys used:
      authenticated      — True if the HTTP session is logged-in
      is_background_task — True for spawned task threads (implicitly authenticated)
    """
    ring = TOOL_RINGS.get(tool_name, 2)   # unknown tools default to NETWORK ring
    ctx = session_ctx or {}

    # Scoped subagents: a scope-restricted background task gets its allow/deny
    # lists, ring ceiling, and step/time budgets enforced ahead of ring policy.
    # Unscoped tasks (no scope registered for the task_id) pass straight through.
    _scope_denial = None
    if ctx.get("task_id"):
        try:
            from agent_friday.services.subagents import scope_check
            _sc_ok, _sc_reason = scope_check(ctx["task_id"], tool_name, ring)
            if not _sc_ok:
                _scope_denial = _sc_reason
        except Exception:
            pass

    if _scope_denial is not None:
        allowed = False
        reason = _scope_denial
        policy = "cLaw:SubagentScope"
    elif ring <= 1:
        allowed = True
        reason = f"ring-{ring} always permitted"
        policy = "cLaw:Ring01-AlwaysAllow"
    elif ring == 2:
        is_auth = ctx.get("authenticated") or ctx.get("is_background_task")
        if is_auth:
            allowed = True
            reason = "ring-2 network op permitted (authenticated)"
            policy = "cLaw:Ring2-RequiresAuth"
        else:
            allowed = False
            reason = "ring-2 network op requires authenticated session"
            policy = "cLaw:Ring2-RequiresAuth"
    elif ring == 3:
        cc_ok, cc_err = _cc_check()
        if cc_ok:
            allowed = True
            reason = "ring-3 OS control permitted (CC enabled)"
            policy = "cLaw:Ring3-ExplicitApproval"
        else:
            allowed = False
            reason = f"ring-3 OS control denied: {cc_err}"
            policy = "cLaw:Ring3-ExplicitApproval"
    else:
        allowed = False
        reason = f"unknown ring level {ring}"
        policy = "cLaw:UnknownRing"

    # Build and sign the BOM entry — canonicalization via vault_crypto.sign_entry()
    # so there is one shared HMAC scheme instead of an inline duplicate.
    args_str = json.dumps(args or {}, sort_keys=True, default=str)
    args_hash = _hashlib.sha256(args_str.encode("utf-8")).hexdigest()
    ts = datetime.utcnow().isoformat() + "Z"
    entry: dict = {
        "timestamp": ts,
        "tool": tool_name,
        "ring": ring,
        "args_hash": args_hash,
        "policy": policy,
        "decision": "allow" if allowed else "deny",
        "reason": reason,
    }
    try:
        if _HAS_VAULT_CRYPTO and _vc is not None:
            entry = _vc.sign_entry(entry, _get_governance_key())
        else:
            canonical = json.dumps(entry, sort_keys=True).encode("utf-8")
            entry["hmac"] = _hmac.new(_get_governance_key(), canonical, _hashlib.sha256).hexdigest()
    except Exception as _sign_err:
        import logging as _log
        _log.getLogger(__name__).error("BOM signing failed: %s", _sign_err)

    try:
        DECISION_BOM_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(DECISION_BOM_FILE, "a", encoding="utf-8") as _f:
            _f.write(json.dumps(entry) + "\n")
    except Exception as _e:
        import logging as _log
        _log.getLogger(__name__).error("AUDIT WRITE FAILED: %s", _e)

    if not allowed:
        print(f"  [GOV] DENY  {tool_name} (ring={ring}): {reason}")

    return allowed, reason


# ── Action confirmation gate ─────────────────────────────────────────────────
# Trust, not surprise: before Friday takes a real-world action on the user's
# behalf — opening a URL, launching an app, switching the on-screen workspace,
# opening a folder, or creating a file — she must ASK first and wait for a yes.
# This is enforced mechanically here so it holds regardless of which model is
# driving the loop. Only model-INITIATED tool calls in an interactive chat are
# gated: scheduled/background work bypasses it (no human is waiting to confirm),
# and the deterministic direct-intent handlers (_maybe_handle_open_intent /
# _maybe_handle_navigate_intent) never reach this gate, so an explicit same-turn
# user command ("open news") still executes immediately — exactly the documented
# exception. The gate activates ONLY when a route opts in by stamping a
# session_id via prepare_confirmation_ctx(); everything else is unaffected.
# Actions that stop and ask before they run.
#
# 2026-08-17, Stephen: "Agent Friday was not able to open the images in their
# own Chrome tab or in their own viewer. She also was not able to open web
# pages. Agent Friday needs to be able to take these types of actions."
#
# `open_path` and `open_url` were in here, and that WAS the failure. The gate
# does not error — it denies, records a pending confirmation, and hands the
# model a message instructing it to "ask the user this exact yes/no question
# and then stop and wait for their reply". Every `ok=False` for open_path in
# the 2026-08-16 ledger is that denial, not a bug: eleven attempts, seven
# denied, four allowed only after he answered yes. The turn ends by design, so
# "open all nine of these" could never get past the first one.
#
# Opening a file he just asked for, or a page, on his own machine, at his own
# request, is trivially reversible and is not what a confirmation gate is for.
# It stays available as a setting for anyone who wants it; the default is that
# Friday can do the thing she was asked to do.
#
# `write_file` and `navigate` stay gated: one creates persistent state, the
# other moves the UI out from under him mid-task.
_ALWAYS_CONFIRM = {"write_file", "navigate", "delete_task", "spawn_interactive_session"}
_OPTIONAL_CONFIRM = {"open_url", "open_path"}


def _tools_requiring_confirmation() -> set:
    """The live gate set. Read per call so a settings change takes effect
    without a restart."""
    out = set(_ALWAYS_CONFIRM)
    try:
        from agent_friday.core import _load_settings
        if (_load_settings() or {}).get("confirm_before_opening"):
            out |= _OPTIONAL_CONFIRM
    except Exception:
        pass
    return out


# Kept as a module-level name because tests and callers import it. It now
# reflects only the unconditional half.
TOOL_REQUIRES_CONFIRMATION = _ALWAYS_CONFIRM

# Pending interactive confirmations, keyed by chat session id. A turn that calls
# a gated tool records the action here and asks the user; their next turn's
# affirmative grants it (see prepare_confirmation_ctx).
_PENDING_CONFIRMATIONS: dict[str, dict] = {}
_PENDING_LOCK = threading.Lock()

_AFFIRM_RE = re.compile(
    r"^\s*(?:yes|yep|yeah|yup|ya|sure|ok|okay|kk?|do it|go ahead|go for it|"
    r"please do|please|sounds good|do that|proceed|confirm(?:ed|s)?|affirmative|"
    r"absolutely|definitely|yes please|open it|open that|show me|let'?s do it|"
    r"go|make it so)\b",
    re.IGNORECASE,
)
_NEGATIVE_RE = re.compile(
    r"^\s*(?:no|nope|nah|don'?t|do not|stop|cancel|never ?mind|not now|skip|"
    r"leave it|hold off|wait|forget it)\b",
    re.IGNORECASE,
)


def _is_affirmative(message: str) -> bool:
    """True if `message` reads as the user approving a pending action."""
    return bool(_AFFIRM_RE.match(message or ""))


def _is_negative(message: str) -> bool:
    """True if `message` reads as the user declining a pending action."""
    return bool(_NEGATIVE_RE.match(message or ""))


def _confirmation_bypassed(session_ctx: dict | None) -> bool:
    """Scheduled cron / background tasks never wait for an interactive yes."""
    ctx = session_ctx or {}
    return bool(ctx.get("is_background_task") or ctx.get("scheduled")
                or ctx.get("confirm_bypass"))


def _record_pending_confirmation(session_id, name, tool_input):
    if not session_id:
        return
    with _PENDING_LOCK:
        _PENDING_CONFIRMATIONS[session_id] = {
            "tool": name, "input": tool_input, "ts": _time.time(),
        }


def prepare_confirmation_ctx(session_id, message, base_ctx=None):
    """Wire one interactive chat turn into the action-confirmation flow.

    Call this from a chat route BEFORE dispatching to the agent loop. It:
      • stamps `session_id` into the ctx so the gate can record pending actions
        and so confirmation is enforced (the gate is a no-op without it);
      • if an action is pending for this session and the user's message is an
        affirmative, sets `confirm_granted` so the re-issued tool call runs;
      • if the message is a refusal, clears the pending action.
    Returns the (new) ctx dict.
    """
    ctx = dict(base_ctx or {})
    ctx["session_id"] = session_id
    if not session_id:
        return ctx
    with _PENDING_LOCK:
        pending = _PENDING_CONFIRMATIONS.get(session_id)
    if pending:
        if _is_affirmative(message):
            ctx["confirm_granted"] = True
        elif _is_negative(message):
            with _PENDING_LOCK:
                _PENDING_CONFIRMATIONS.pop(session_id, None)
    return ctx


def _confirmation_question(name, tool_input):
    """A natural yes/no prompt for the gated `name` action."""
    inp = tool_input or {}
    if name == "open_url":
        tgt = inp.get("url") or "that link"
        return f"Would you like me to open {tgt} in your browser?"
    if name == "open_path":
        tgt = inp.get("path") or inp.get("target") or "that"
        return f"Would you like me to open {tgt} on your computer?"
    if name == "navigate":
        tgt = inp.get("workspace") or "that workspace"
        return f"I can switch you to the {tgt} workspace — shall I?"
    if name == "write_file":
        tgt = inp.get("path") or "a file"
        return f"Would you like me to create {tgt}?"
    return "Would you like me to go ahead with that?"


def _task_log_tool(session_ctx, name, args):
    """Write a tool call to the spawning task's log at EXECUTION time.

    The task log used to get its tool lines from `tool_trace` after the whole
    model call returned. For a 234-second heartbeat that meant four lines at
    the start, silence for the entire run, and everything else at the end —
    which is what "it still just says waiting for activity" describes. The
    lines existed; they simply arrived too late to be progress.
    """
    tid = (session_ctx or {}).get("task_id")
    if not tid:
        return
    try:
        detail = ""
        if isinstance(args, dict) and args:
            first = next(iter(args.items()))
            detail = "(%s=%s)" % (first[0], str(first[1])[:40])
        _task_log(tid, "→ tool: %s%s" % (name, detail))
    except Exception:
        pass


from agent_friday.services import tool_receipts as _receipts

#: Verb prefixes a model habitually invents in front of a tool's real name.
#: Observed: the seat called `mcp_higgsfield_get_balance` when the registered
#: tool is `mcp_higgsfield_balance`. The arguments and intent were right; only
#: the name was embellished.
_TOOL_VERB_NOISE = ("get_", "fetch_", "read_", "call_", "do_", "run_",
                    "list_", "show_", "check_", "query_")


def _tool_name_key(name):
    """Collapse a tool name to what it MEANS, for matching purposes.

    Drops the mcp_ prefix, any invented verb prefix, and separators, so
    `mcp_higgsfield_get_balance`, `higgsfield.balance` and `balance` all
    reduce to the same key.
    """
    s = str(name or "").strip().lower().replace(".", "_").replace("-", "_")
    if s.startswith("mcp_"):
        s = s[4:]
    parts = s.split("_")
    # Drop invented verbs wherever they sit — the observed miss was
    # `higgsfield_GET_balance`, i.e. the verb after the server name, not at
    # the front. Never drop the last token: `list_voices` collapsing to
    # `higgsfield` would be worse than not matching at all.
    verbs = {v.rstrip("_") for v in _TOOL_VERB_NOISE}
    kept = [p for i, p in enumerate(parts)
            if p not in verbs or i == len(parts) - 1]
    parts = kept or parts
    out, seen = [], set()
    for p in parts:                      # order-preserving dedupe
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return "".join(out)


def _resolve_tool_name(name):
    """(resolved_name | None, suggestions) for a tool name that did not match.

    Resolves ONLY when exactly one registered tool shares the collapsed key —
    an ambiguous guess would run a tool the model did not ask for, which is
    worse than failing. Otherwise returns near misses so the error can say
    what would have worked.
    """
    key = _tool_name_key(name)
    if not key:
        return None, []
    exact = [n for n in CLAUDE_TOOL_HANDLERS if _tool_name_key(n) == key]
    if len(exact) == 1:
        return exact[0], exact
    near = [n for n in CLAUDE_TOOL_HANDLERS
            if key and (key in _tool_name_key(n) or _tool_name_key(n) in key)]
    return None, sorted(near)[:6]


def _execute_tool(name, tool_input, pii_lookup=None, session_ctx=None):
    """Run a Claude tool through the lifecycle-hook chain.

    Every native and MCP tool call passes through here — the single choke point.
    The gate sequence (confirmation → governance → vault → sandbox → rate limit)
    is the PreToolUse chain; the audit log, PII scrub, and cost attribution are
    the PostToolUse chain. All are registered built-in hooks (see just below);
    skills can register additional hooks via services.tool_hooks.

    pii_lookup: if a dict, scrub PII into it instead of destructively redacting.
    session_ctx: ring-2/3 policy evaluation + hook attribution (workspace/run).
    """
    handler = CLAUDE_TOOL_HANDLERS.get(name)
    if not handler:
        resolved, suggestions = _resolve_tool_name(name)
        if resolved:
            print(f"  [tools] '{name}' is not registered; resolved to "
                  f"'{resolved}' (unambiguous match)")
            name, handler = resolved, CLAUDE_TOOL_HANDLERS[resolved]
        else:
            # A bare "Unknown tool: x" is a dead end: it says the call failed
            # but not what would work, and a dead end is where invented
            # results come from. Name the near misses and state plainly that
            # nothing ran, so the honest next move is obvious.
            hint = ("Closest registered tools: "
                    + ", ".join(suggestions)) if suggestions else \
                   "No similarly-named tool is registered."
            return (f"TOOL CALL FAILED — no tool named '{name}' exists, so "
                    f"nothing ran and no result was produced. {hint} "
                    f"Retry with an exact name from your tool list, or tell "
                    f"the user you could not do it. Do not describe an "
                    f"outcome: there isn't one.")

    ctx = _hooks.HookContext(
        tool_name=name,
        input=tool_input or {},
        session_ctx=session_ctx,
        pii_lookup=pii_lookup,
    )
    ctx.meta["t_start"] = _time.time()

    # ── PreToolUse chain — confirmation, governance, vault, sandbox, rate limit.
    # A DENY short-circuits; the deny message is what the model sees as the result.
    verdict = _hooks.run_pre_hooks(ctx)
    if verdict.action == "deny":
        _receipts.record(name, ok=False, denied=True, detail=verdict.reason)
        return verdict.reason

    try:
        result = handler(ctx.input)
        if not isinstance(result, str):
            result = json.dumps(result, default=str)
    except Exception as e:
        traceback.print_exc()
        _receipts.record(name, ok=False, detail=str(e))
        return f"Tool error ({name}): {e}"

    # Receipt written only after the handler actually returned. This is the
    # only place one is created, so a receipt cannot exist for a call that did
    # not happen — which is what makes an unbacked claim detectable later.
    _receipts.record(name, ok=True)

    # Cap result size to prevent token explosion in the model context window.
    # The voice path already caps at 8 KB; apply the same limit uniformly here.
    _TOOL_RESULT_MAX = 8192
    if isinstance(result, str) and len(result) > _TOOL_RESULT_MAX:
        result = result[:_TOOL_RESULT_MAX] + f"\n[truncated — {len(result)} chars total]"

    # ── A6: every date in a tool result carries a code-computed weekday, so
    # the model never derives one itself (Incident 2, F3). ──
    if isinstance(result, str):
        try:
            from agent_friday.services.clock import annotate_weekdays
            result = annotate_weekdays(result)
        except Exception:
            pass

    # ── PostToolUse chain — audit log, PII scrub, cost attribution. ──
    return _hooks.run_post_hooks(ctx, result)


# ═══════════════════════════════════════════════════════════════════════════
#  BUILT-IN LIFECYCLE HOOKS (Part B). Refactored out of _execute_tool's former
#  hard-coded gate sequence into named, reorderable, per-settings-toggleable
#  hooks. This is behaviour-preserving — same checks, same order — but the chain
#  is now extensible (skills can register their own) and visible in Settings.
#  Built-ins occupy priority 0–99; user/skill hooks default to 100 so they run
#  after the critical gates and can only tighten, never loosen, governance.
# ═══════════════════════════════════════════════════════════════════════════

def _creations_write_preapproved(name, inp) -> bool:
    """write_file into the creations folders is project work, not persistent
    system state — the storybook E2E showed the gate demanding a fresh yes
    every turn for a manifest inside the project's own folder, while the same
    bytes sailed through via shell curl. Writes whose resolved path lands
    under CREATIONS_DIR or DAILY_CREATIONS_DIR skip the ask; everything else
    keeps the ask-first contract unchanged."""
    if name != "write_file":
        return False
    try:
        from agent_friday import core as _core
        raw = (inp or {}).get("path") or ""
        if not str(raw).strip():
            return False
        p = Path(os.path.expanduser(str(raw))).resolve()
        for root in (getattr(_core, "CREATIONS_DIR", None),
                     getattr(_core, "DAILY_CREATIONS_DIR", None)):
            if not root:
                continue
            try:
                p.relative_to(Path(root).resolve())
                return True
            except ValueError:
                continue
    except Exception:
        return False
    return False


def _hook_confirmation_gate(ctx):
    """Ask-first permission gate (interactive chat only). Pre, priority 10."""
    name = ctx.tool_name
    session_ctx = ctx.session_ctx
    _sid = (session_ctx or {}).get("session_id")
    if _creations_write_preapproved(name, ctx.input):
        return _hooks.ALLOW
    if (name in _tools_requiring_confirmation() and _sid
            and not _confirmation_bypassed(session_ctx)):
        if (session_ctx or {}).get("confirm_granted"):
            # User approved on this turn — clear the marker and allow.
            with _PENDING_LOCK:
                _PENDING_CONFIRMATIONS.pop(_sid, None)
            return _hooks.ALLOW
        _record_pending_confirmation(_sid, name, ctx.input)
        _q = _confirmation_question(name, ctx.input)
        return _hooks.DENY(
            f"[CONFIRMATION REQUIRED] The '{name}' action needs the user's "
            f"approval before it runs, so it was NOT executed. Do NOT call "
            f"this tool again on this turn. Instead, ask the user this exact "
            f"yes/no question and then stop and wait for their reply: \"{_q}\""
        )
    return _hooks.ALLOW


def _hook_governance_rings(ctx):
    """Ring 0–3 cLaw governance (critical, fail-closed). Pre, priority 20."""
    allowed, reason = _governance_check(ctx.tool_name, ctx.input,
                                        session_ctx=ctx.session_ctx)
    if not allowed:
        return _hooks.DENY(f"[GOVERNANCE DENY] {reason}")
    return _hooks.ALLOW


def _hook_vault_zt(ctx):
    """Vault zero-trust: network/vault-tier tools need an authenticated (or
    background-task) session. Critical. Pre, priority 25.

    A strict subset of the governance ring-2 check above (which runs first and
    short-circuits), so this never independently changes an outcome — it is
    defence-in-depth and a first-class, visible governance seam.
    """
    ring = TOOL_RINGS.get(ctx.tool_name, 2)
    sc = ctx.session_ctx or {}
    authed = sc.get("authenticated") or sc.get("is_background_task")
    if ring == 2 and not authed:
        return _hooks.DENY(
            "[VAULT DENY] network/vault-tier tool requires an authenticated session")
    return _hooks.ALLOW


def _hook_sandbox_policy(ctx):
    """Filesystem/command sandbox confinement. Pre, priority 30."""
    ok, reason = _sandbox_policy(ctx.tool_name, ctx.input)
    if not ok:
        try:
            _log_context("sandbox_deny", {"name": ctx.tool_name, "reason": reason})
        except Exception:
            pass
        return _hooks.DENY(f"[SANDBOX DENY] {reason}")
    return _hooks.ALLOW


def _hook_rate_limiter(ctx):
    """Token-bucket cap on Ring-2/3 tool frequency. Pre, priority 40.

    Stops a runaway agent loop from hammering a network API or burning spend.
    Ring 0/1 (local reads/writes) are never limited.
    """
    ring = TOOL_RINGS.get(ctx.tool_name, 2)
    if ring < 2:
        return _hooks.ALLOW
    try:
        cfg = (_load_settings().get("rate_limiter") or {})
    except Exception:
        cfg = {}
    if cfg.get("enabled") is False:
        return _hooks.ALLOW
    per_min = cfg.get("ring3_per_min", 20) if ring >= 3 else cfg.get("ring2_per_min", 60)
    if not _hooks.rate_limit_check(f"ring{ring}", per_min):
        return _hooks.DENY(
            f"[RATE LIMIT] ring-{ring} tool calls exceeded {per_min}/min; "
            f"pause briefly before retrying.")
    return _hooks.ALLOW


def _hook_audit_log(ctx, result):
    """Structured tool-execution entry to the context log. Post, priority 90.

    Screenshots are base64 image payloads: log a placeholder, never the blob.
    """
    try:
        if ctx.tool_name == 'screenshot':
            _log_context("tool_call", {
                "name": ctx.tool_name, "input": ctx.input,
                "result_preview": "[screenshot image]",
            })
        else:
            _log_context("tool_call", {
                "name": ctx.tool_name,
                "input": ctx.input,
                "result_preview": result[:2000],
                "result_len": len(result),
                "workspace": ctx.workspace or None,
                "run_id": ctx.run_id,
            })
    except Exception:
        pass
    return result


def _hook_pii_scrub(ctx, result):
    """Scrub PII from tool results. Post, priority 95.

    Screenshots pass through untouched (a regex pass over base64 would be slow
    and could corrupt the image). Otherwise: scrub into pii_lookup for later
    rehydration when one is supplied, else destructively redact.
    """
    if ctx.tool_name == 'screenshot':
        return result
    if isinstance(ctx.pii_lookup, dict):
        scrubbed, sub = _scrub_pii(result)
        ctx.pii_lookup.update(sub)
        return scrubbed
    return _pii_redact(result)


def _hook_file_grant_registration(ctx, result):
    """WO-17 read-time feeder. Post, priority 96 — AFTER pii_scrub (95).

    Must run after the scrub, not before: registration has to match the
    EXACT string that later reaches the egress gate. read_file's raw
    extraction is scrubbed for PII first (phone/email/address → [PII:...]
    placeholders); registering the pre-scrub text left every paragraph that
    happened to contain a phone number or address permanently unmatched,
    which is how a granted CV's summary section stayed withheld after the
    grant was created (found live 2026-08-25, see _tool_read_file's note).
    """
    try:
        from agent_friday.services import file_grants as _fg
        path = (ctx.input or {}).get("path")
        if path:
            p = Path(path).expanduser().resolve()
            if p.is_file():
                _fg.on_file_read(p, result)
    except Exception:
        pass
    return result


def _hook_cost_attribution(ctx, result):
    """Attribute spend to the active workspace / scheduled run. Post, priority 80.

    The Part D cost meter records token usage at the model-call sites; this hook
    is the seam that makes per-workspace / per-schedule attribution available for
    tool-driven turns. It hands the call's attribution to the cost store when one
    is present (no-op until Part D is wired) and never raises.
    """
    try:
        from agent_friday.services import cost_meter as _cm
        note = getattr(_cm, "note_tool_attribution", None)
        if callable(note):
            note(ctx)
    except Exception:
        pass
    return result


def _register_builtin_tool_hooks():
    """Register the built-in hooks once, at import time."""
    _hooks.register_pre_hook(_hook_confirmation_gate, name="confirmation_gate",
                             priority=10)
    _hooks.register_pre_hook(_hook_governance_rings, name="governance_rings",
                             priority=20, critical=True)
    _hooks.register_pre_hook(_hook_vault_zt, name="vault_zt",
                             priority=25, critical=True)
    _hooks.register_pre_hook(_hook_sandbox_policy, name="sandbox_policy",
                             priority=30)
    _hooks.register_pre_hook(_hook_rate_limiter, name="rate_limiter",
                             priority=40)
    _hooks.register_post_hook(_hook_cost_attribution, name="cost_attribution",
                              priority=80)
    _hooks.register_post_hook(_hook_audit_log, name="audit_log", priority=90)
    _hooks.register_post_hook(_hook_pii_scrub, name="pii_scrub", priority=95)
    _hooks.register_post_hook(_hook_file_grant_registration,
                              name="file_grant_registration", priority=96,
                              tools={"read_file"})


_register_builtin_tool_hooks()


# ── MCP (Model Context Protocol) Client ────────────────────────────────────
# Friday speaks the same connector protocol Claude does: each MCP server is a
# subprocess exchanging newline-delimited JSON-RPC over stdio. mcp_client.py
# handles the transport; here we (1) load the server config, (2) register each
# discovered MCP tool into the SAME unified registry the native tools live in
# (CLAUDE_TOOLS / CLAUDE_TOOL_HANDLERS / TOOL_RINGS), and (3) forward calls.
#
# To the model there is no difference between a native tool and an MCP-backed
# one — _execute_tool dispatches both through CLAUDE_TOOL_HANDLERS, so the same
# governance gate, sandbox policy, and zero-trust vault check apply. MCP tools
# are named `mcp_<server>_<tool>` to avoid colliding with native tool names and
# default to Ring 2 (network — requires an authenticated session).
try:
    from agent_friday.mcp_client import MCPManager as _MCPManager
except Exception as _mcp_imp_err:  # noqa: BLE001 — degrade gracefully if absent
    _MCPManager = None
    print(f"  [mcp] client module unavailable: {_mcp_imp_err}")

MCP_SERVERS_FILE = FRIDAY_DIR / "mcp_servers.json"

_MCP_MANAGER = None                       # set by _mcp_boot()
_MCP_TOOL_MAP: dict[str, tuple] = {}      # registered tool name -> (server, raw tool)
_MCP_SERVER_TOOLS: dict[str, list] = {}   # server name -> [registered tool names]
_MCP_REG_LOCK = threading.Lock()


def _default_mcp_servers() -> dict:
    """Seed config for ~/.friday/mcp_servers.json.

    Paths are derived from the user's home (never hardcoded) so this stays
    portable and PII-free. The Gmail connector is enabled only when its built
    entry point is actually present on disk; the Calendar entry ships disabled
    with the npx invocation pre-filled so it's one flag away from running.
    """
    home = Path.home()
    servers: dict = {}

    gmail_dist = home / "Projects" / "gmail-mcp-multi" / "dist" / "index.js"
    servers["gmail"] = {
        "command": "node",
        "args": [str(gmail_dist)],
        "env": {},
        # Only auto-enable if the build exists; otherwise leave wired but off so
        # boot never fails trying to spawn a missing file.
        "enabled": gmail_dist.exists(),
        "note": "gmail-mcp-multi (search/read/send/labels). Needs OAuth creds in "
                "~/.gmail-mcp/ — run its `authenticate` tool or `npm run auth`.",
    }

    # Google Calendar — no local server is installed, so wire up the published
    # npx package disabled-by-default. Flip "enabled": true after dropping a
    # Google OAuth client JSON and pointing GOOGLE_OAUTH_CREDENTIALS at it.
    servers["calendar"] = {
        "command": "npx",
        "args": ["-y", "@cocal/google-calendar-mcp"],
        "env": {
            "GOOGLE_OAUTH_CREDENTIALS": str(home / ".friday" / "credentials.json"),
        },
        "enabled": False,
        "note": "@cocal/google-calendar-mcp via npx. Set enabled:true and ensure "
                "GOOGLE_OAUTH_CREDENTIALS points at a valid Google OAuth client.",
    }
    return {"servers": servers}


def _load_mcp_servers() -> dict:
    """Load ~/.friday/mcp_servers.json, seeding defaults on first run."""
    FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
    if not MCP_SERVERS_FILE.exists():
        seed = _default_mcp_servers()
        try:
            MCP_SERVERS_FILE.write_text(json.dumps(seed, indent=2), encoding="utf-8")
        except Exception:
            pass
        return seed
    try:
        # utf-8-sig, not utf-8: anything that edits this file from PowerShell
        # leaves a BOM, json.loads chokes on it, and the except below used to
        # swallow that into an empty config — every configured MCP server
        # silently vanished while the UI still listed them as enabled.
        data = json.loads(MCP_SERVERS_FILE.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            print(f"  [MCP] {MCP_SERVERS_FILE} is not a JSON object — "
                  f"no MCP servers loaded")
            return {"servers": {}}
        # Connector credentials are protected at rest. What is returned here is
        # STILL ENCRYPTED and is meant to be: this object is what
        # GET /api/mcp/servers hands to the browser. Only the spawn path
        # decrypts. Anything written before encryption shipped is upgraded on
        # the way past — once per process, and only when there is something to
        # upgrade.
        try:
            from agent_friday.services import connector_secrets as _cse
            data = _cse.migrate_config_file(MCP_SERVERS_FILE, data)
        except Exception as _e:
            print(f"  [MCP] credential migration skipped: {_e}")
        return data
    except Exception as e:
        # Loud, not silent: returning {} here disables every connector, and a
        # subsystem that produces nothing must say so rather than exit clean.
        print(f"  [MCP] FAILED to read {MCP_SERVERS_FILE}: {e} — "
              f"no MCP servers loaded (all connectors are OFF)")
        return {"servers": {}}


def _save_mcp_servers(cfg: dict) -> dict:
    """Persist the MCP server config (full replace of the servers map).

    Secret env values are encrypted on the way to disk. encrypt_config is
    idempotent, so a config that came back from the browser already encrypted
    passes through untouched rather than being wrapped a second time.
    """
    FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
    if "servers" not in cfg:
        cfg = {"servers": cfg}
    try:
        from agent_friday.services import connector_secrets as _cse
        cfg = _cse.encrypt_config(cfg)
    except Exception as _e:
        # Refuse rather than silently writing the token in the clear: this
        # function is the only thing standing between a pasted credential and
        # a readable file, and a caller that sees an error can say so.
        raise RuntimeError(
            "refusing to save connector config: credentials could not be "
            f"encrypted ({_e})") from _e
    MCP_SERVERS_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    try:
        from agent_friday.services import credential_store as _cs
        _cs.harden_permissions(MCP_SERVERS_FILE)
    except Exception:
        pass
    return cfg


def _mcp_sanitize(s: str) -> str:
    """Coerce a server/tool name into the [A-Za-z0-9_-] charset Anthropic and
    OpenAI tool names require."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(s))[:48]


def _mcp_is_remote(server_name: str) -> bool:
    """True when this MCP server lives off-machine (Streamable HTTP).

    Remote servers are cloud egress by construction. A stdio server is a local
    subprocess; what that subprocess then does with the payload is outside what
    this gate can see, and _mcp_gate_args says so rather than implying cover it
    does not provide.
    """
    mgr = _MCP_MANAGER
    if mgr is None:
        return True                      # fail-closed: unknown destination = cloud
    sp = (getattr(mgr, "servers", {}) or {}).get(server_name)
    if sp is None:
        return True
    return bool(getattr(sp, "url", None))


def _mcp_vault_conflict(value: str):
    """Return the vault directory a string points into, or None.

    Image bytes cannot be text-classified (routes/chat.py, qa_gates.py), so a
    file *path* is the only handle the gate has on an upload. A path under a
    sensitive vault dir is refused outright — the answer is the local pipeline
    or nothing.
    """
    if not value or len(value) > 4096 or chr(0) in value:
        return None
    try:
        cand = Path(value.strip().strip('"')).expanduser()
    except Exception:
        return None
    try:
        cand = cand.resolve(strict=False)
    except Exception:
        return None
    for d in _sensitive_vault_dirs():
        try:
            if cand == d or d in cand.parents:
                return str(d)
        except Exception:
            continue
    return None


def _mcp_gate_args(server_name: str, tool_name: str, args):
    """The single egress choke point for remote MCP tool calls.

    Every string anywhere in the argument tree goes through
    egress_gate.gate_text() with this server as the provider. Unknown provider
    names classify as cloud there, so this is fail-closed from day one without
    any registry work.

    If the gate CHANGES a string, the call is refused rather than submitted
    partially redacted: a half-gated prompt is a different request than the one
    that was asked for, and silently sending it would be substitution without
    disclosure. Returns (ok, explanation_or_None).
    """
    from agent_friday.services import egress_gate as _eg

    findings = []

    def _walk(node, path):
        if isinstance(node, str):
            hit = _mcp_vault_conflict(node)
            if hit:
                findings.append(f"{path}: refers to a file under {hit}, a "
                                f"vault-sensitive directory")
                return
            if not node.strip():
                return
            try:
                gated = _eg.gate_text(node, server_name, f"{tool_name}.{path}")
            except Exception as e:                       # a gate that cannot run
                findings.append(f"{path}: egress gate failed to run ({e})")
                return
            if gated != node:
                what = "dropped entirely" if not gated.strip() else "redacted"
                findings.append(f"{path}: sensitive content was {what} by the "
                                f"egress gate")
        elif isinstance(node, dict):
            for k, v in node.items():
                _walk(v, f"{path}.{k}" if path else str(k))
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                _walk(v, f"{path}[{i}]")

    _walk(args, "")
    if not findings:
        return True, None
    detail = "; ".join(findings[:6])
    return False, (
        f"[blocked] This call to '{tool_name}' was not sent to {server_name}. "
        f"{server_name} is a cloud service, and the egress gate found content "
        f"that must not leave this machine — {detail}. Nothing was sent and "
        f"nothing was charged. Rewrite the request without that material, or "
        f"use a local capability instead. I have not sent a redacted version, "
        f"because that would be a different request than the one you made."
    )


#: Documented defaults for Higgsfield generation, applied only when the model
#: names no model at all. Chosen on measured price (2026-08-19): the cheapest
#: option that does the job, per the standing "cheapest safe default" rule.
#: These are a stated policy, not a guess — and any use is logged, because a
#: silently-substituted model is exactly the kind of thing that should never
#: happen quietly.
_HF_DEFAULT_MODEL = {
    "generate_image": "nano_banana_2",   # 2 credits
    "generate_video": "veo3_1_lite",     # 8 credits, cheapest image-to-video
}


def _mcp_fit_envelope(server_name: str, tool_name: str, payload: dict) -> dict:
    """Reshape a flat tool payload into the envelope the server's schema wants.

    Higgsfield nests every generation argument under a single ``params``
    object whose schema is an ``anyOf`` of several branches. A model that
    sends ``{"model": ..., "prompt": ...}`` flat — which is the obvious shape,
    and what Friday's local seat actually sends — gets back
    ``params: Invalid input`` and no picture. The arguments were right; only
    the wrapper was missing.

    This fixes the wrapper and nothing else. It does not invent arguments,
    does not choose between anyOf branches on meaning, and does not touch a
    payload that already has the envelope. The one value it will supply is a
    model id, and only when none was given at all — see _HF_DEFAULT_MODEL.
    """
    if not isinstance(payload, dict):
        return payload
    full = f"mcp_{_mcp_sanitize(server_name)}_{_mcp_sanitize(tool_name)}"[:64]
    tool = next((t for t in CLAUDE_TOOLS if t.get("name") == full), None) or {}
    props = ((tool.get("input_schema") or {}).get("properties") or {})
    if "params" not in props or "params" in payload:
        return payload
    # Everything the model sent belongs inside params (nothing else is declared).
    outer = {k: v for k, v in payload.items() if k in props}
    inner = {k: v for k, v in payload.items() if k not in props}
    if not inner:
        return payload
    fitted = dict(outer)
    fitted["params"] = inner
    default = _HF_DEFAULT_MODEL.get(tool_name)
    if default and not inner.get("model"):
        inner["model"] = default
        print(f"  [mcp:{server_name}] no model given for {tool_name}; using the "
              f"documented default '{default}'")
    print(f"  [mcp:{server_name}] wrapped flat arguments into the 'params' "
          f"envelope for {tool_name}")
    return fitted


def _make_mcp_handler(server_name: str, tool_name: str):
    """Build a CLAUDE_TOOL_HANDLERS handler that forwards to the MCP server.

    Remote (HTTP) servers pass through the egress gate first. Local stdio
    servers are not gated here: the payload goes to a process on this machine,
    and pretending otherwise would claim a boundary this function does not
    enforce.
    """
    def _handler(inp):
        if _MCP_MANAGER is None:
            return "[mcp error] MCP manager not initialized"
        payload = _mcp_fit_envelope(server_name, tool_name, inp or {})
        if _mcp_is_remote(server_name):
            ok, explanation = _mcp_gate_args(server_name, tool_name, payload)
            if not ok:
                return explanation
        return _MCP_MANAGER.call(server_name, tool_name, payload)
    return _handler


_SCHEMA_COMBINATORS = ("anyOf", "oneOf", "allOf")


def _mcp_normalize_schema(schema: dict, tool_name: str = "") -> dict:
    """Make a third-party tool schema acceptable to the Anthropic tools API.

    Anthropic rejects `oneOf`, `allOf` and `anyOf` at the TOP level of an
    `input_schema` — and it rejects the whole REQUEST, not the one tool. So a
    single connector shipping such a schema takes every cloud turn down with
    a 400 that names a tool index and nothing else:

        tools.90.custom.input_schema: input_schema does not support
        oneOf, allOf, or anyOf at the top level

    Measured 2026-08-18: this is exactly what happened once the Higgsfield
    connector registered 86 tools. Cloud chat returned "[Friday offline]" for
    every message, in every conversation, and the cause was a schema written
    by a server we do not control.

    Dropping the offending tool would be the easy fix and the wrong one — it
    silently removes a capability. Instead the branches are merged into one
    object schema: properties are unioned, and a field stays `required` only
    if every branch required it (a field the caller can omit in some valid
    shape is not required). Nested combinators are left alone; the API only
    objects at the top level.
    """
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    if not any(k in schema for k in _SCHEMA_COMBINATORS):
        if not schema.get("type"):
            schema = dict(schema, type="object")
        return schema

    merged = {k: v for k, v in schema.items() if k not in _SCHEMA_COMBINATORS}
    props = dict(merged.get("properties") or {})
    required_sets = []
    base_required = merged.get("required")
    if isinstance(base_required, list):
        required_sets.append(set(base_required))

    for key in _SCHEMA_COMBINATORS:
        for branch in (schema.get(key) or []):
            if not isinstance(branch, dict):
                continue
            for name, spec in (branch.get("properties") or {}).items():
                props.setdefault(name, spec)
            br = branch.get("required")
            required_sets.append(set(br) if isinstance(br, list) else set())

    merged["type"] = "object"
    merged["properties"] = props
    keep = set.intersection(*required_sets) if required_sets else set()
    if keep:
        merged["required"] = sorted(keep)
    else:
        merged.pop("required", None)

    print(f"  [mcp] normalised a top-level combinator schema for {tool_name!r} "
          f"- Anthropic rejects oneOf/allOf/anyOf there")
    return merged


def _mcp_register_server_tools(server_name: str, tools: list) -> list:
    """Register a server's discovered tools into the unified tool registry.

    Called (from a background thread) the moment a server finishes its
    initialize/tools-list handshake, so tools light up as servers come ready
    rather than blocking boot. Returns the registered tool names.
    """
    registered: list[str] = []
    with _MCP_REG_LOCK:
        # Clear any stale registration for this server first (idempotent reload).
        _mcp_unregister_server_tools(server_name, _locked=True)
        for t in tools or []:
            raw = t.get("name")
            if not raw:
                continue
            full = f"mcp_{_mcp_sanitize(server_name)}_{_mcp_sanitize(raw)}"[:64]
            desc = t.get("description") or f"{raw} via the {server_name} connector"
            desc = f"[MCP·{server_name}] {desc}"[:1024]
            schema = _mcp_normalize_schema(
                t.get("inputSchema") or t.get("input_schema")
                or {"type": "object", "properties": {}}, full)
            # Replace any existing CLAUDE_TOOLS entry with the same name.
            CLAUDE_TOOLS[:] = [c for c in CLAUDE_TOOLS if c.get("name") != full]
            CLAUDE_TOOLS.append({"name": full, "description": desc,
                                 "input_schema": schema})
            CLAUDE_TOOL_HANDLERS[full] = _make_mcp_handler(server_name, raw)
            TOOL_RINGS[full] = 2  # network ring — requires authenticated session
            _MCP_TOOL_MAP[full] = (server_name, raw)
            registered.append(full)
        _MCP_SERVER_TOOLS[server_name] = registered
    if registered:
        print(f"  [mcp:{server_name}] registered {len(registered)} tool(s) "
              f"into the agent registry")
    return registered


def _mcp_unregister_server_tools(server_name: str, _locked: bool = False) -> None:
    """Remove a server's tools from the unified registry (used on reload)."""
    def _do():
        names = _MCP_SERVER_TOOLS.pop(server_name, [])
        if not names:
            return
        nameset = set(names)
        CLAUDE_TOOLS[:] = [c for c in CLAUDE_TOOLS if c.get("name") not in nameset]
        for n in names:
            CLAUDE_TOOL_HANDLERS.pop(n, None)
            TOOL_RINGS.pop(n, None)
            _MCP_TOOL_MAP.pop(n, None)
    if _locked:
        _do()
    else:
        with _MCP_REG_LOCK:
            _do()


def _mcp_boot() -> None:
    """Initialize the MCP manager and start every enabled server (async)."""
    global _MCP_MANAGER
    if _MCPManager is None:
        return
    try:
        cfg = _load_mcp_servers()
        # Extension security: scan every configured server before launch and
        # disable anything that trips a block-level finding (destructive or
        # download-and-execute command lines). Scanner failures never take
        # connectors down — the unscanned config passes through.
        try:
            from agent_friday.services.extension_security import gate_mcp_config
            cfg = gate_mcp_config(cfg)
        except Exception as _sec_err:
            print(f"  [mcp] extension security scan skipped: {_sec_err}")
        mgr = _MCPManager(log=lambda m: print(f"  {m}"))
        mgr.load_config(cfg)
        _MCP_MANAGER = mgr
        # Non-blocking: each server starts in its own thread; tools register via
        # the on_ready callback as each handshake completes.
        mgr.start_all(on_ready=_mcp_register_server_tools)
        enabled = [n for n, s in mgr.servers.items() if s.status != "disabled"]
        _log.info("MCP client: %d server(s) configured (%d enabled), connecting async…",
                  len(mgr.servers), len(enabled))
    except Exception as e:  # noqa: BLE001
        _log.warning("MCP boot failed: %s", e)


def _mcp_reload() -> dict:
    """Reload config from disk: tear down tools + servers, then restart all."""
    global _MCP_MANAGER
    if _MCPManager is None:
        return {"error": "MCP client module unavailable"}
    # Unregister every server's tools.
    for name in list(_MCP_SERVER_TOOLS.keys()):
        _mcp_unregister_server_tools(name)
    if _MCP_MANAGER is not None:
        try:
            _MCP_MANAGER.stop_all()
        except Exception:
            pass
    _mcp_boot()
    return {"ok": True}


def _screenshot_result_to_block(tool_use_id, result):
    """Convert a screenshot tool result (JSON with base64 image) into an Anthropic
    tool_result block carrying a real image so the model can SEE the screen.

    Returns None for error strings / unparseable results so the caller falls back
    to a plain-text tool_result.
    """
    try:
        data = json.loads(result)
    except Exception:
        return None
    b64 = data.get('image_b64')
    if not b64:
        return None
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": [
            {"type": "text", "text": data.get('note', 'Screenshot captured.')},
            {"type": "image", "source": {
                "type": "base64",
                "media_type": data.get('media_type', 'image/png'),
                "data": b64,
            }},
        ],
    }


def _tool_orb_meta(name):
    """Map a tool name to (category, icon, friendly_label) for the process orb."""
    n = (name or '').lower()
    if 'search_web' in n or 'browse_web' in n or n == 'search':
        return ('search', '🔍', name)
    if 'email' in n or 'draft_email' in n or 'slack' in n or 'message' in n or 'notif' in n:
        return ('communication', '✉', name)
    if 'wiki' in n or 'read_file' in n or 'write_file' in n or 'list_directory' in n:
        return ('monitoring', '📁', name)
    if 'command' in n or 'install_package' in n:
        return ('monitoring', '⚙', name)
    if 'calendar' in n or 'briefing' in n or 'pipeline' in n:
        return ('monitoring', '📅', name)
    if 'trust' in n:
        return ('monitoring', '🛡', name)
    return ('default', '⚡', name)


# ══════════════════════════════════════════════════════════════
#  B3 — Orb↔task↔ledger correlation + tier-redacted thread view
# ══════════════════════════════════════════════════════════════

# Sentinel prefixes that mean a tool call was DENIED by a governance gate
# (vs. an execution error, vs. success). Kept in sync with the gate messages
# emitted by _execute_tool's hook chain and the zero-trust vault gate.
_TOOL_DENY_SENTINELS = (
    "[VAULT-ZT DENY]", "[VAULT ACCESS DENIED]", "[CONFIRMATION REQUIRED]",
    "[GOVERNANCE DENY]", "[SANDBOX DENY]",
)
# "TOOL CALL FAILED" is the unknown-name message _execute_tool now returns.
# It MUST be listed here: _tool_call_status classifies anything unrecognised as
# 'ok', so a failure prefix missing from this tuple is a failed call reporting
# itself as a success — which is the exact defect the receipts work exists to
# remove. Changing a failure message means updating this tuple in the same edit.
_TOOL_ERROR_SENTINELS = ("Tool error (", "Unknown tool:", "TOOL CALL FAILED")


def _tool_call_status(result):
    """Classify a tool result string: 'ok' | 'deny' | 'error'."""
    r = result if isinstance(result, str) else ""
    if r.startswith(_TOOL_DENY_SENTINELS):
        return "deny"
    if r.startswith(_TOOL_ERROR_SENTINELS):
        return "error"
    return "ok"


def _tier_safe_summary(payload, limit=120, kind="args"):
    """Egress-tier-safe one-line summary of tool args/results for the orb
    thread view. Runs the text through the vault sensitivity classifier —
    TIER_2/TIER_3 content is withheld entirely (the process record is
    world-readable via /api/processes), TIER_1 is truncated to `limit` chars.
    """
    if payload is None:
        return ""
    try:
        text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    except Exception:
        text = str(payload)
    if not text:
        return ""
    tier = 1
    try:
        if VaultAccessControl is not None:
            tier = int(_get_vault_control().classify(text))
    except Exception:
        tier = 1
    if tier > 1:
        return f"[tier-{tier} {kind} withheld]"
    return " ".join(text.split())[:limit]


def _register_agent_orb(orb_label, orb_category, orb_icon, model, session_ctx=None):
    """Register the per-agent-loop process orb WITH correlation ids.

    The orb carries the model actually serving the loop plus the spawning
    task's id (when this loop runs inside a background task, _task_worker puts
    it in session_ctx), so the frontend thread panel and /api/tasks/<id> can
    correlate orb → task → ledger events exactly. Returns the orb pid, or
    None when registration failed (every caller treats None as "no orb").
    """
    orb_id = f"agent-{uuid.uuid4().hex[:8]}"
    try:
        process_register(
            orb_id,
            name="Friday",
            label=orb_label or "Thinking…",
            category=orb_category,
            icon=orb_icon,
            steps=[],
            model=model or ANTHROPIC_MODEL_DEFAULT,
            task_id=(session_ctx or {}).get("task_id"),
        )
    except Exception:
        return None
    return orb_id


def _orb_tool_trace(orb_id, name, args, result, duration_ms):
    """Append one completed tool call to the orb's thread view: a compact
    log line plus a timed step entry. Args/results are tier-redacted via
    _tier_safe_summary before touching the process record. Best-effort."""
    if not orb_id:
        return
    try:
        status = _tool_call_status(result)
        stamp = _time.strftime("%H:%M:%S")
        process_log(orb_id, f"[{stamp}] tool {name} → {status} ({int(duration_ms)}ms)")
        process_update(orb_id, step={
            "type": "tool",
            "name": name,
            "status": status,
            "args": _tier_safe_summary(args, kind="args"),
            "result": _tier_safe_summary(result, kind="result"),
            "duration_ms": int(duration_ms),
            "ts": _time.time(),
        })
    except Exception:
        pass


def _ledger_tool_call(name, result, duration_ms, orb_id, session_ctx):
    """B4: append a metadata-only tool_call event to the activity ledger."""
    try:
        from agent_friday.services import activity_ledger as _al
        _al.record(
            "tool_call",
            tool=name,
            ok=(_tool_call_status(result) == "ok"),
            duration_ms=int(duration_ms),
            orb_id=orb_id,
            task_id=(session_ctx or {}).get("task_id"),
        )
    except Exception:
        pass


def _ledger_model_invocation(model, provider, seat, duration_ms, tokens_in,
                             tokens_out, orb_id, session_ctx):
    """B4: append a metadata-only model_invocation event to the ledger."""
    try:
        from agent_friday.services import activity_ledger as _al
        _al.record(
            "model_invocation",
            model=model,
            provider=provider,
            seat=seat,
            duration_ms=int(duration_ms),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            orb_id=orb_id,
            task_id=(session_ctx or {}).get("task_id"),
            workspace=(session_ctx or {}).get("workspace"),
        )
    except Exception:
        pass


def _call_claude_agent(messages, system=None, model=None, max_tokens=16384, temperature=None, max_iters=999, pii_lookup=None, session_ctx=None, orb_label=None, orb_category='default', orb_icon='🧠'):
    """Tool-using Claude loop. Returns (final_text, tool_trace).

    pii_lookup: if a dict, tool results are scrubbed into it for rehydration.
    session_ctx: passed to _governance_check for ring-2/3 policy enforcement.
      Keys: authenticated (bool), is_background_task (bool).
    """
    client = get_anthropic_client()
    if client is None:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Set it via the setup wizard (Settings → Providers) or as an environment variable, then restart the server."
        )

    if pii_lookup is None:
        # Legacy path — destructively redact on the way out.
        safe_messages = []
        for m in messages:
            content = m.get('content')
            if isinstance(content, str):
                safe_messages.append({"role": m['role'], "content": _pii_redact(content)})
            else:
                safe_messages.append(m)
        safe_system = _pii_redact(system) if isinstance(system, str) else system
    else:
        # Caller already scrubbed — trust the inputs.
        safe_messages = list(messages)
        safe_system = system

    tool_trace = []
    convo = list(safe_messages)

    # ── Auto-compaction (Part C): summarize the middle of a long transcript
    # (head + tail preserved) before dispatch so a long session/task can't
    # overflow the context window. No-op below threshold. ──
    try:
        from agent_friday.services import compaction as _compaction
        convo = _compaction.maybe_compact(convo, model=model)
    except Exception:
        pass

    # ── Process orb registration — frontend renders an orb per active agent.
    # Registered FIRST so the behavioral monitor session below can carry the
    # orb id for exact orb↔trace correlation (B3). ──
    orb_id = _register_agent_orb(orb_label, orb_category, orb_icon,
                                 model or ANTHROPIC_MODEL_DEFAULT, session_ctx)

    # ── B4: model-invocation accounting for the activity ledger. ──
    _led_t0 = _time.time()
    _led_tok_in = 0
    _led_tok_out = 0

    # ── Behavioral monitor — open a governance session keyed to the user's
    # latest message, log every tool call, and score the loop on completion. ──
    _bmon = None
    _bmon_sid = None
    if _HAS_BEHAVIORAL_MONITOR:
        try:
            _bmon = get_behavioral_monitor()
            _bmon_user_msg = ""
            for _m in reversed(messages):
                if _m.get("role") == "user":
                    _c = _m.get("content")
                    if isinstance(_c, str):
                        _bmon_user_msg = _c
                        break
                    if isinstance(_c, list):
                        _txt = " ".join(
                            b.get("text", "") for b in _c
                            if isinstance(b, dict) and b.get("type") == "text"
                        )
                        if _txt.strip():
                            _bmon_user_msg = _txt
                            break
            _bmon_sid = _bmon.begin_session(_bmon_user_msg, meta={
                "is_background_task": bool((session_ctx or {}).get("is_background_task")),
                "provider": (session_ctx or {}).get("provider", "cloud"),
                # B3: exact correlation ids — the governance session, the
                # process orb and the spawning task now share a spine.
                "task_id": (session_ctx or {}).get("task_id"),
                "orb_id": orb_id,
            })
        except Exception:
            _bmon = None
            _bmon_sid = None

    def _bmon_log(_name, _input, _result):
        if _bmon is None or _bmon_sid is None:
            return
        try:
            _bmon.log_action(
                _bmon_sid, _name, _input,
                ring_level=TOOL_RINGS.get(_name, 2),
                result=_result,
            )
        except Exception:
            pass

    def _orb_safe(fn, *a, **kw):
        if not orb_id:
            return
        try:
            fn(*a, **kw)
        except Exception:
            pass

    # ── Per-task cloud ceiling (2026-08-26). ──
    # `max_iters` defaults to 999 and nothing else here bounds spend: at the
    # measured median of ~91,000 input tokens per iteration that is a
    # theoretical 90M tokens on one task. The incident that prompted this —
    # a crash-fallback re-sending a blown-context turn — billed ~1.43M. The
    # ceiling is charged in the shared egress chokepoint, so it also covers
    # any cloud call a TOOL makes from inside this loop, not just the loop's
    # own iterations. Entered here and released in the `finally` below.
    _budget = None
    try:
        from agent_friday.services import prompt_cache as _pc
        _budget = _pc.task_budget(label=orb_label or "agent task").__enter__()
    except Exception:
        _budget = None

    try:
        iter_count = 0
        for _ in range(max_iters):
            iter_count += 1
            # ── Operator filesystem controls ───────────────────────────
            # Drop ~/.friday/AGENT_STOP to kill a runaway agent immediately.
            _stop_path = FRIDAY_DIR / "AGENT_STOP"
            if _stop_path.exists():
                try:
                    _stop_path.unlink()
                except Exception:
                    pass
                _orb_safe(process_update, orb_id, status='error', label='Stopped', progress=1.0)
                return ("[Agent stopped by operator control: AGENT_STOP file detected.]", tool_trace)

            # Write instructions to ~/.friday/STEER.md to redirect mid-task.
            _steer_inject = None
            _steer_path = FRIDAY_DIR / "STEER.md"
            if _steer_path.exists():
                try:
                    _steer_inject = _steer_path.read_text(encoding='utf-8').strip()
                    _steer_path.unlink()
                except Exception:
                    pass

            # Update orb: reasoning step
            _orb_safe(process_update, orb_id,
                      label="Reasoning…" if iter_count == 1 else f"Reasoning (step {iter_count})",
                      progress=min(0.05 + (iter_count - 1) * 0.1, 0.9),
                      step={"type": "reason", "iter": iter_count, "ts": _time.time()})

            kwargs = {
                "model": model or ANTHROPIC_MODEL_DEFAULT,
                "max_tokens": max_tokens,
                "messages": convo,
                "tools": CLAUDE_TOOLS,
            }
            _sys = safe_system
            if _steer_inject:
                _sys = (_sys or '') + f"\n\n[OPERATOR STEER — FOLLOW THIS IMMEDIATELY]: {_steer_inject}"
            if _sys:
                kwargs["system"] = _sys
            # NOTE: `temperature` intentionally NOT forwarded — newer Claude
            # models (Opus 4.8+, Sonnet 4.6+) 400 on the deprecated param.
            # Kept in the signature for backward-compat; model defaults are used.

            # EGRESS GATE (fail-closed): this tool-loop is the PRIMARY cloud path
            # in Friday — /api/chat, channel messages, scheduled tasks and
            # orchestrator workers all funnel through here. It previously called
            # the Anthropic API directly, bypassing the gate that model_router's
            # _call_claude enforces, so the multi-layer sensitivity classifier
            # (financial/medical/legal/contextual-PII + vault content) NEVER ran on
            # the main path. Route every iteration's payload through the same
            # centralized _seal_or_block wrapper (R3) so the boundary holds here too.
            kwargs = _seal_or_block(kwargs, "anthropic")
            # Last line of defence for tool schemas. Normalising at MCP
            # registration fixes the known source, but ONE malformed schema
            # from any future path 400s the entire request — every tool, every
            # conversation — with an error that names only an index. This loop
            # is the primary cloud path in Friday; it should not be possible
            # for a third party's JSON to silence it.
            try:
                _tl = kwargs.get("tools")
                if isinstance(_tl, list):
                    kwargs["tools"] = [
                        dict(_t, input_schema=_mcp_normalize_schema(
                            _t.get("input_schema") or {}, _t.get("name") or "?"))
                        if isinstance(_t, dict) else _t
                        for _t in _tl
                    ]
            except Exception:
                pass
            # Prompt-cache breakpoints (2026-08-26), applied last — after the
            # gate and after schema normalisation — so nothing downstream can
            # drop them. This loop is where Friday's cloud bill actually lives:
            # every iteration re-sends the full tool tier (~14k tokens) plus the
            # entire accrued transcript, and the transcript is append-only here,
            # which is exactly the shape an incremental cache reads at 0.1x.
            # Modelled on 14 days of real calls in ~/.friday/costs.db: an 80%
            # cut to the input line, which is ~99% of the spend.
            try:
                from agent_friday.services import prompt_cache as _pc
                kwargs = _pc.apply_anthropic_cache(kwargs)
            except Exception:
                pass
            _t0 = _time.time()
            resp = client.messages.create(**kwargs)
            # B4: accumulate token counts for the activity-ledger record.
            try:
                _u = getattr(resp, "usage", None)
                _led_tok_in += int(getattr(_u, "input_tokens", 0) or 0)
                _led_tok_out += int(getattr(_u, "output_tokens", 0) or 0)
            except Exception:
                pass
            # Cost metering (Part D): the Anthropic tool loop used to discard
            # resp.usage — capture input+output tokens with run/workspace
            # attribution from session_ctx.
            try:
                from agent_friday.services import cost_meter as _cm
                _cm.meter("anthropic", kwargs.get("model"),
                          getattr(resp, "usage", None),
                          duration_ms=int((_time.time() - _t0) * 1000),
                          session_ctx=session_ctx,
                          kind=(session_ctx or {}).get("kind"))
            except Exception:
                pass

            # Collect text and tool_use blocks
            text_parts = []
            tool_uses = []
            for b in resp.content:
                btype = getattr(b, 'type', None)
                if btype == 'text':
                    text_parts.append(b.text)
                elif btype == 'tool_use':
                    tool_uses.append(b)

            if resp.stop_reason != 'tool_use' or not tool_uses:
                _orb_safe(process_update, orb_id, status='completed', progress=1.0, label='Done')
                # Badge truth (2026-08-14): record the model that ACTUALLY
                # generated this text — the badge layer reads this, never
                # the router's intent.
                try:
                    from agent_friday.services import attribution
                    attribution.record_generation(
                        model or ANTHROPIC_MODEL_DEFAULT,
                        provider="anthropic", seat="cloud")
                except Exception:
                    pass
                return ("".join(text_parts).strip(), tool_trace)

            # Promote orb category to whatever tool family is most active this round.
            try:
                cat, icon, _ = _tool_orb_meta(tool_uses[0].name)
                _orb_safe(process_update, orb_id, label=f"{tool_uses[0].name}…")
            except Exception:
                pass

            # Echo assistant turn (text + tool_use blocks) into the convo
            assistant_content = []
            for b in resp.content:
                btype = getattr(b, 'type', None)
                if btype == 'text':
                    assistant_content.append({"type": "text", "text": b.text})
                elif btype == 'tool_use':
                    assistant_content.append({
                        "type": "tool_use",
                        "id": b.id,
                        "name": b.name,
                        "input": b.input,
                    })
            convo.append({"role": "assistant", "content": assistant_content})

            # Execute tools and feed results back
            tool_results = []
            for tu in tool_uses:
                # B3: the step entry is appended AFTER execution (with status +
                # timing, tier-redacted args) by _orb_tool_trace — the raw tool
                # input no longer enters the world-readable process record.
                _orb_safe(process_update, orb_id, label=f"{tu.name}…")
                _t_tool = _time.time()

                # ── Zero-trust continuous vault authorization ──────────
                # Gate every tool call through vault check_action before
                # execution. If the provider can't see the data, deny.
                _vault_ctl = _get_vault_control() if VaultAccessControl else None
                if _vault_ctl is not None:
                    _zt_provider = (session_ctx or {}).get("provider", "cloud")
                    _zt_data = json.dumps(tu.input or {}, default=str)
                    _zt_allowed, _zt_detail, _zt_tier = _vault_ctl.check_action(
                        _zt_provider, tu.name, _zt_data,
                        access_log_path=str(FRIDAY_DIR / "vault" / "access-log.jsonl"),
                    )
                    if not _zt_allowed:
                        _zt_result = f"[VAULT-ZT DENY] {_zt_detail}"
                        tool_trace.append({"name": tu.name, "input": tu.input, "result": _zt_result})
                        _bmon_log(tu.name, tu.input, _zt_result)
                        _tool_ms = int((_time.time() - _t_tool) * 1000)
                        _orb_tool_trace(orb_id, tu.name, tu.input, _zt_result, _tool_ms)
                        _ledger_tool_call(tu.name, _zt_result, _tool_ms, orb_id, session_ctx)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "content": f"[VAULT ACCESS DENIED] This tool call references {_zt_detail} data. "
                                       f"Switch to a local model to access sensitive content.",
                        })
                        continue

                _task_log_tool(session_ctx, tu.name, tu.input)
                result = _execute_tool(tu.name, tu.input, pii_lookup=pii_lookup, session_ctx=session_ctx)
                _tool_ms = int((_time.time() - _t_tool) * 1000)
                _orb_tool_trace(orb_id, tu.name, tu.input, result, _tool_ms)
                _ledger_tool_call(tu.name, result, _tool_ms, orb_id, session_ctx)

                # Screenshot results carry a base64 image — hand it to the model as
                # an actual vision block so it can SEE the screen and pick coords.
                if tu.name == 'screenshot':
                    img_block = _screenshot_result_to_block(tu.id, result)
                    if img_block is not None:
                        tool_trace.append({"name": tu.name, "input": tu.input, "result": "[screenshot image returned to model]"})
                        _bmon_log(tu.name, tu.input, "[screenshot image returned to model]")
                        tool_results.append(img_block)
                        continue

                tool_trace.append({"name": tu.name, "input": tu.input, "result": result[:2000]})
                _bmon_log(tu.name, tu.input, result)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": result,
                })
            convo.append({"role": "user", "content": tool_results})

        _orb_safe(process_update, orb_id, status='error', label='Max iters', progress=1.0)
        return ("[Agent hit max tool iterations without completing.]", tool_trace)
    except Exception:
        _orb_safe(process_update, orb_id, status='error', label='Error', progress=1.0)
        raise
    finally:
        if _budget is not None:
            try:
                _budget.__exit__(None, None, None)
            except Exception:
                pass
        # ── B4: one model_invocation ledger event per agent-loop completion. ──
        _ledger_model_invocation(
            model or ANTHROPIC_MODEL_DEFAULT, "anthropic", "cloud",
            (_time.time() - _led_t0) * 1000, _led_tok_in, _led_tok_out,
            orb_id, session_ctx,
        )
        # ── Behavioral monitor — score this loop and fire response actions. ──
        if _bmon is not None and _bmon_sid is not None:
            try:
                _bmon.evaluate(_bmon_sid)
            except Exception:
                pass
        # The frontend keeps a "completing" orb for ~2s, then auto-purges via
        # /api/processes server-side TTL once status is completed/error.
        if orb_id:
            try:
                p = PROCESSES.get(orb_id)
                if p and p.get('status') == 'running':
                    process_update(orb_id, status='completed', progress=1.0)
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════
#  LOCAL MODEL INFERENCE (Ollama)
#  Mirror of _call_claude_agent's interface but routes through
#  Ollama. Only called when the model router selects a local model.
# ══════════════════════════════════════════════════════════════

def _oai_agentic_loop(convo, oai_tools, send_fn, *, provider, model,
                      pii_lookup=None, session_ctx=None, max_iters=50, orb=None,
                      meter_provider=None, orb_id=None):
    """Shared OpenAI-format agentic tool loop for every OpenAI-compatible
    provider — local Ollama (gemma4 et al.) AND cloud OpenAI/OpenRouter.

    Both endpoints speak the identical wire format: the assistant turn carries
    ``tool_calls``; each tool result goes back as a ``role: "tool"`` message.
    So the loop, the UNIFIED CLAUDE_TOOLS registry, the zero-trust vault gate
    and _execute_tool's governance rings live here ONCE instead of being copied
    into each provider. The only per-provider differences — how a single round
    trip is sent and how the orb is labelled — are injected via callbacks.

      convo          — the running message list (system + history); mutated in place
      oai_tools      — OpenAI function-tool schemas, or None for single-shot text
      send_fn(convo, oai_tools) -> raw OpenAI-format response dict (one round)
      provider       — "local" | "openai", used for loop semantics + vault default
      meter_provider — REGISTRY provider name ("openrouter", "groq", …) for
                       cost-ledger attribution; defaults to `provider` so
                       existing call sites are unchanged
      orb(**kw)      — optional process-orb updater (no-op if omitted)
      orb_id         — the caller's process-orb pid (B3): enables the enriched
                       thread view (process_log lines + timed steps) and exact
                       correlation ids on the activity-ledger events

    Returns (final_text, tool_trace). Tool-less calls do exactly one round.
    """
    _orb = orb or (lambda **kw: None)
    _meter_as = meter_provider or provider
    tool_trace = []
    # B4: model-invocation accounting — token totals accumulate across rounds
    # and a single ledger event is recorded at each completion path.
    _led_t0 = _time.time()
    _led_tok = {"in": 0, "out": 0}
    _led_seat = "local" if provider == "local" else "openai"

    def _led_done():
        _ledger_model_invocation(
            model, _meter_as, _led_seat, (_time.time() - _led_t0) * 1000,
            _led_tok["in"], _led_tok["out"], orb_id, session_ctx,
        )
    # Auto-compaction (Part C): condense a long transcript before the loop.
    try:
        from agent_friday.services import compaction as _compaction
        convo = _compaction.maybe_compact(convo, model=model)
    except Exception:
        pass
    loops = max_iters if oai_tools else 1
    _empty_retried = False
    for _ in range(loops):
        resp = send_fn(convo, oai_tools)

        usage = resp.get("usage", {}) or {}
        # Attribute spend to the model the provider ACTUALLY served when it
        # differs (OpenRouter's server-side fallback reports it in `model`,
        # surfaced by the transport as `_served_model`).
        _meter_model = resp.get("_served_model") or model
        # B4: accumulate token totals for the activity-ledger record.
        try:
            _led_tok["in"] += int(usage.get("prompt_tokens", 0) or 0)
            _led_tok["out"] += int(usage.get("completion_tokens", 0) or 0)
        except Exception:
            pass
        try:
            from agent_friday.routing.model_router import get_router
            get_router().cost_tracker.record(
                _meter_as, _meter_model,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
            )
        except Exception:
            pass
        # Cost metering (Part D): durable per-direction ledger with attribution.
        try:
            from agent_friday.services import cost_meter as _cm
            _cm.meter(_meter_as, _meter_model, usage, session_ctx=session_ctx)
        except Exception:
            pass

        choices = resp.get("choices", [])
        msg = (choices[0].get("message", {}) if choices else {}) or {}
        tool_calls = msg.get("tool_calls") or []

        # ── The gemma4 e-series speaks a channel format, not OpenAI shape ──
        #
        # It emits its calls inside the assistant's TEXT:
        #     <|tool_call>call:get_weather{city:Oslo}<tool_call|>
        # and `tool_calls` comes back empty. Ollama's daemon parsed that for
        # us, which is the single reason those seats could not be served as
        # processes we own without losing tool calling outright.
        #
        # Translated here rather than in a per-provider branch, so the loop
        # stays one loop: below this point nothing can tell which wire format
        # the model used.
        _chan_text = msg.get("content") or ""
        if oai_tools and not tool_calls and _chan_text:
            try:
                from agent_friday.services import channel_toolcalls as _chan
                _found, _rest = _chan.extract(_chan_text, oai_tools)
                if _found:
                    tool_calls = _found
                    msg = dict(msg, content=_rest, tool_calls=_found)
            except Exception:
                pass

        # No tools available, or the model is done calling them → final answer.
        if not oai_tools or not tool_calls:
            text = (msg.get("content") or "").strip()
            # An EMPTY completion is not an answer.
            #
            # Stephen watched gemma4:12b think for two minutes and then deliver
            # a blank message. That is worse than an error: an error says
            # something went wrong, a blank bubble says Friday had nothing to
            # say. One retry that tells the model what happened, then an honest
            # failure — never silence dressed up as a reply.
            if not text and not _empty_retried:
                _empty_retried = True
                convo.append({"role": "assistant", "content": ""})
                convo.append({"role": "user", "content":
                              "(Automated check — this is not from the user. "
                              "Your previous response was empty. Answer the "
                              "user's message directly, in words.)"})
                continue
            if not text:
                text = ("[Friday returned an empty response twice in a row. "
                        "That is a fault on this end, not an answer — please "
                        "try again, and switch seats if it repeats.]")
            # Even with nothing to call, channel markup must not reach the
            # transcript — the thought channel is a scratchpad, not an answer.
            if text and "channel" in text:
                try:
                    from agent_friday.services import channel_toolcalls as _chan
                    _c, text = _chan.extract(text, oai_tools)
                except Exception:
                    pass
            # Keep the DESCRIPTION. This used to overwrite it with
            # f'Done ({model})', which is why every finished orb in Stephen's
            # holographic desktop read the same thing — and read the model
            # twice, since the scene already appends its own model badge:
            #
            #     ⚡ Done (gemma4:12b)  🏠 gemma4
            #
            # The model was the whole identity and the task was nowhere. An
            # orb should say WHAT IT IS; "done" is already carried by the
            # status field and by the colour.
            _orb(status='completed', progress=1.0)
            _led_done()
            return text, tool_trace

        # Echo the assistant turn (must carry tool_calls verbatim).
        convo.append({
            "role": "assistant",
            "content": msg.get("content") or "",
            "tool_calls": tool_calls,
        })
        try:
            _first = (tool_calls[0].get("function") or {}).get("name") or "tool"
            _orb(label=f"{_first}…",
                 step={"type": "tool", "name": _first, "ts": _time.time()})
        except Exception:
            pass

        for tc in tool_calls:
            fn = tc.get("function") or {}
            tname = fn.get("name") or ""
            tcid = tc.get("id") or ""
            # Two wire shapes, and assuming one of them silently destroyed
            # every local tool call that took an argument.
            #
            # OpenAI's spec says `arguments` is a JSON STRING. Ollama's native
            # /api/chat returns it as an already-parsed OBJECT:
            #     {"function": {"name": "get_project",
            #                   "arguments": {"name": "gamma"}}}
            # `json.loads(dict)` raises TypeError, the except substituted {},
            # and the tool then ran with NO arguments. Measured 2026-08-15 on
            # a dependent 4-call chain: the model emitted `{"name": "gamma"}`
            # correctly every time, the executor received `{}` every time, and
            # the model — being told nothing was found — reported that the
            # tools had failed. It read as a model too weak to chain tool
            # calls. It was a type check.
            #
            # This surfaced when dispatch moved to /api/chat to make num_ctx
            # take effect (the OpenAI-compatible endpoint silently discards
            # `options`), which traded a working context for broken arguments.
            _raw = fn.get("arguments")
            if isinstance(_raw, dict):
                targs = _raw
            elif isinstance(_raw, str) and _raw.strip():
                try:
                    targs = json.loads(_raw)
                except Exception:
                    targs = {}
            else:
                targs = {}
            _t_tool = _time.time()

            # ── Zero-trust continuous vault authorization. ──
            # ONLY vault-tier (TIER_2/TIER_3) data is gated here; the provider
            # determines whether sensitive content may flow (local = allowed,
            # cloud = denied). Everything non-sensitive passes untouched, so
            # navigation / file ops / app launch / task spawn are available to
            # every model. _execute_tool then applies the cLaw governance rings.
            _vault_ctl = _get_vault_control() if VaultAccessControl else None
            if _vault_ctl is not None:
                _zt_provider = (session_ctx or {}).get("provider", provider)
                _zt_allowed, _zt_detail, _zt_tier = _vault_ctl.check_action(
                    _zt_provider, tname, json.dumps(targs, default=str),
                    access_log_path=str(FRIDAY_DIR / "vault" / "access-log.jsonl"),
                )
                if not _zt_allowed:
                    _zt_result = f"[VAULT-ZT DENY] {_zt_detail}"
                    tool_trace.append({"name": tname, "input": targs,
                                       "result": _zt_result})
                    _tool_ms = int((_time.time() - _t_tool) * 1000)
                    _orb_tool_trace(orb_id, tname, targs, _zt_result, _tool_ms)
                    _ledger_tool_call(tname, _zt_result, _tool_ms, orb_id, session_ctx)
                    convo.append({"role": "tool", "tool_call_id": tcid,
                                  "content": f"[VAULT ACCESS DENIED] references {_zt_detail} "
                                             f"data — switch to a local model to access it."})
                    continue

            _task_log_tool(session_ctx, tname, targs)
            result = _execute_tool(tname, targs, pii_lookup=pii_lookup,
                                   session_ctx=session_ctx)
            _tool_ms = int((_time.time() - _t_tool) * 1000)
            _orb_tool_trace(orb_id, tname, targs, result, _tool_ms)
            _ledger_tool_call(tname, result, _tool_ms, orb_id, session_ctx)
            # Screenshots return a base64 blob — useless as text here, and CC
            # already forces the Anthropic path, so degrade gracefully.
            if tname == 'screenshot':
                result = "[screenshot captured — vision is only available on the Anthropic path]"
            tool_trace.append({"name": tname, "input": targs, "result": result[:2000]})
            convo.append({"role": "tool", "tool_call_id": tcid, "content": result})

    _orb(status='error', label='Max iters', progress=1.0)
    _led_done()
    return "[Agent hit max tool iterations without completing.]", tool_trace


# ══════════════════════════════════════════════════════════════
#  TRAJECTORY COMPRESSION  (Hermes-inspired context management)
#  When the conversation history sent to Claude would exceed the
#  soft limit, compress older turns into a dense summary block
#  while keeping recent turns verbatim.
# ══════════════════════════════════════════════════════════════

_TRAJ_CHAR_LIMIT = 2_000_000   # ~500K tokens; Opus 4.8 has 1M ctx — only compress at this threshold
_TRAJ_KEEP_VERBATIM = 20       # keep last 20 turn-pairs (~40 messages) verbatim


def _start_kill_hotkey():
    """Background thread: listen for Ctrl+Shift+Q as a global kill switch."""
    try:
        from pynput import keyboard as _kb

        def _on_kill():
            _log.info("KILL HOTKEY Ctrl+Shift+Q — computer control terminated")
            _CC_PERMISSION.clear()
            _CC_KILL.set()
            _cc_persist(False)
            if _HAS_PYAUTOGUI:
                try:
                    _pag.moveTo(0, 0, duration=0.1)
                except Exception:
                    pass
            try:
                _log_context("cc_action", {"action": "kill_hotkey_ctrl_shift_q"})
            except Exception:
                pass

        hk = _kb.GlobalHotKeys({'<ctrl>+<shift>+q': _on_kill})
        hk.start()
        _log.info("Global kill hotkey active: Ctrl+Shift+Q")
    except ImportError:
        _log.info("pynput not installed — kill hotkey unavailable. Run: pip install pynput")
    except Exception as e:
        _log.warning("Kill hotkey listener failed: %s", e)


