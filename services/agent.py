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
import core
from core import (
    _network_is_offline,
    ANTHROPIC_MODEL_DEFAULT,
    CREATIONS_DIR,
    DECISION_BOM_FILE,
    FRIDAY_DIR,
    FRIDAY_PASSWORD,
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
    process_register,
    process_update,
)  # noqa: E501
from services.model_router import (
    _call_ollama,
    _call_openai,
    _get_friday_system_prompt,
    _get_vault_control,
)  # noqa: E501
from services import tool_hooks as _hooks
from services.news_engine import (
    _fetch_news_items,
)  # noqa: E501
from services.wiki_engine import (
    _mirror_wiki_file,
    _propose_wiki_update,
    _safe_wiki_path,
    wiki_read_text,
    wiki_write_text,
)  # noqa: E501



def _generate_agent(messages, system=None, model=None, max_tokens=16384,
                    temperature=None, session_ctx=None, pii_lookup=None,
                    orb_label=None, orb_category='default', orb_icon='🧠',
                    workspace=None):
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
        from services.demo_mode import is_demo, demo_response
        if is_demo():
            return demo_response('generic'), []
    except Exception:
        pass

    # Per-workspace temperature profile (creative pipeline): derive a sampling
    # temperature from the active workspace when the caller didn't pin one.
    # Honored by Ollama/OpenAI primitives; newer Claude models ignore it.
    try:
        from services.model_router import resolve_workspace_temperature
        temperature = resolve_workspace_temperature(workspace, temperature)
    except Exception:
        pass

    settings = _load_settings()
    routing_cfg = settings.get('model_routing') or {}
    provider, routed_model = 'cloud', model
    route = {}
    try:
        from model_router import get_router
        route = get_router(routing_cfg).route(messages, task_context={
            "has_tools": True,
            "workspace": workspace or '',
            "cloud_model": model or settings.get('orchestrator_model') or ANTHROPIC_MODEL_DEFAULT,
        }) or {}
        provider = route.get('provider', 'cloud')
        routed_model = route.get('model') or model
    except Exception as _re:
        print(f"  [AGENT] routing failed, defaulting to cloud: {_re}")

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
        return _call_claude_agent(
            messages, system=system, model=use_model or model,
            max_tokens=max_tokens, temperature=temperature,
            pii_lookup=pii_lookup, session_ctx=session_ctx,
            orb_label=orb_label, orb_category=orb_category, orb_icon=orb_icon,
        )

    def _via_openai(use_model):
        # Full agentic tool loop with parity to _call_claude_agent.
        return _call_openai(
            messages, system=system, model=use_model,
            max_tokens=max_tokens, temperature=temperature,
            orb_label=orb_label, tools=CLAUDE_TOOLS,
            pii_lookup=pii_lookup, session_ctx=session_ctx,
        )

    def _via_ollama(use_model):
        # Local models run the FULL agentic tool loop now (native OpenAI-style
        # tool calling, e.g. gemma4) — same unified CLAUDE_TOOLS registry, vault
        # gate, and _execute_tool governance as the cloud paths. Returns
        # (text, tool_trace).
        return _call_ollama(
            messages, system=system, model=use_model,
            max_tokens=max_tokens, temperature=temperature,
            orb_label=orb_label, tools=CLAUDE_TOOLS,
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

    errors = []
    for name, fn, use_model in attempts:
        try:
            text, trace = fn(use_model)
            if text and text.strip():
                return text, (trace or [])
            errors.append(f"{name}: empty response")
        except Exception as e:
            errors.append(f"{name}: {e}")
    if vault_access:
        # Refuse rather than raise: the caller surfaces this as the reply, and
        # the request was deliberately kept off every cloud provider.
        return ("This request touches vault-protected data, so it was only "
                "tried on the local model — which failed ("
                + "; ".join(errors[-1:]) +
                "). It was NOT sent to a cloud provider. Check that Ollama "
                "is running, then retry."), []
    raise RuntimeError(
        "No model provider could run the agent (tried "
        + "; ".join(errors[-3:]) + "). Set ANTHROPIC_API_KEY (start.bat / "
        "launch_now.bat), configure an OpenAI-compatible endpoint in Settings, "
        "or run Ollama locally, then restart the server."
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
    {"name": "search_web", "description": "Search the web via DuckDuckGo for current information. Returns ranked snippets with URLs. Use for news, facts, people, companies, anything not in the local wiki.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "browse_web", "description": "Fetch a URL and return its full text content (HTML stripped). Use after search_web to read the full article/page. Ring 2.",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string", "description": "Full https:// URL to fetch"}}, "required": ["url"]}},
    {"name": "read_file", "description": "Read any file on the local filesystem. Supports absolute paths (C:\\...) or paths relative to home (~). Returns up to 500000 chars.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string", "description": "Absolute or home-relative path, e.g. ~/Projects/foo/bar.py or ~/wiki/notes.md"}}, "required": ["path"]}},
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
    {"name": "query_calendar", "description": "Check the user's Google Calendar (today's & tomorrow's events). Built-in Google integration. If the result says 'not connected', the integration just needs a one-time OAuth connection — offer to walk the user through it; do NOT say you lack calendar access.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "search_email", "description": "Search and read the user's recent Gmail (built-in read-only Google integration). If the result says 'not connected', the integration just needs a one-time OAuth connection — offer to set it up; do NOT say you can't access Gmail.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
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
     "input_schema": {"type": "object", "properties": {"path": {"type": "string", "description": "A folder/file path or friendly name (Downloads, Desktop, Projects, an absolute path, or an app name)."}}, "required": ["path"]}},
    {"name": "navigate", "description": "Switch the Friday desktop UI to one of its built-in workspaces, on-screen, for the user. Use this whenever the user asks to open, show, switch to, or go to a workspace by name — this drives the ACTUAL interface, so prefer it over just describing where something is. Workspaces: home, career, wiki, studio, trust, system, news, draft, code, finance, health, contacts, content, messages, calendar, family, futurespeak.",
     "input_schema": {"type": "object", "properties": {"workspace": {"type": "string", "description": "Workspace id or spoken name, e.g. 'studio', 'news', 'calendar', 'settings'."}}, "required": ["workspace"]}},
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
    q = (inp or {}).get('query', '')
    if not q:
        return "search_web error: 'query' is required."
    try:
        import requests as _req
        encoded = _req.utils.quote(q)
        resp = _req.get(
            f"https://html.duckduckgo.com/html/?q={encoded}",
            timeout=12,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) FridayAgent/1.0'},
        )
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, 'html.parser')
            results = []
            for r in soup.select('.result')[:8]:
                title_el = r.select_one('.result__title')
                snip_el = r.select_one('.result__snippet')
                url_el = r.select_one('.result__url')
                if title_el and snip_el:
                    results.append({
                        'title': title_el.get_text(strip=True),
                        'snippet': snip_el.get_text(strip=True),
                        'url': url_el.get_text(strip=True) if url_el else '',
                    })
            if results:
                lines = [f"Search results for '{q}':\n"]
                for i, r in enumerate(results, 1):
                    lines.append(f"{i}. {r['title']}\n   {r['snippet']}\n   {r['url']}")
                return '\n'.join(lines)[:100_000]
        except ImportError:
            pass
        # BS4 not available — return stripped text
        text = _html_to_text(resp.text)
        return f"Search results for '{q}' (raw):\n{text[:50_000]}"
    except ImportError:
        return (
            f"requests library not installed. Install it with: pip install requests\n"
            f"Query was: {q!r}"
        )
    except Exception as e:
        return f"Web search error: {e}. Query: {q!r}"


def _tool_browse_web(inp):
    url = ((inp or {}).get('url') or '').strip()
    if not url:
        return "browse_web error: 'url' is required."
    if not (url.startswith('http://') or url.startswith('https://')):
        return f"browse_web error: URL must start with http:// or https://. Got: {url!r}"
    try:
        import requests as _req
        resp = _req.get(
            url, timeout=15,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) FridayAgent/1.0'},
            allow_redirects=True,
        )
        ct = resp.headers.get('content-type', '')
        if 'html' in ct or 'text' in ct or not ct:
            text = _html_to_text(resp.text)
        else:
            return f"Non-text content ({ct}) at {url} — can't extract text."
        _log_context("browse_web", {"url": url, "chars": len(text)})
        limit = 200_000
        return f"[{url}]\n{text[:limit]}" + (f"\n...[truncated — {len(text)} chars total]" if len(text) > limit else "")
    except ImportError:
        return "browse_web requires the requests library. Install: pip install requests"
    except Exception as e:
        return f"Browse error ({url}): {e}"


def _tool_read_file(inp):
    raw = (inp or {}).get('path', '')
    if not raw:
        return "read_file error: 'path' is required."
    try:
        p = Path(raw).expanduser().resolve()
    except Exception as e:
        return f"Invalid path {raw!r}: {e}"
    if not p.exists():
        return f"File not found: {p}"
    if not p.is_file():
        return f"Not a file: {p}"
    try:
        text = p.read_text(encoding='utf-8', errors='replace')
        _log_context("file_read", {"path": str(p), "bytes": len(text)})
        limit = 500_000
        return text[:limit] + (f"\n...[truncated — {len(text)} total chars]" if len(text) > limit else "")
    except Exception as e:
        return f"Read error: {e}"


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
            import skill_registry as _skreg
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
    from services.misc_engine import _load_trust_graph
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


def _tool_query_calendar(_inp):
    """Today's + tomorrow's events via the native Google Calendar integration.

    Uses the same real fetch path as the voice tools. When Google isn't linked,
    returns the 'needs connecting' note (never 'not installed') so Friday offers
    to set it up instead of claiming she lacks calendar access."""
    try:
        from services.calendar_engine import _fetch_calendar_today, _google_section_error
    except Exception:
        try:
            from calendar_engine import _fetch_calendar_today, _google_section_error  # type: ignore
        except Exception:
            return json.dumps({"connected": False, "events": [],
                               "note": _GOOGLE_NOT_CONNECTED_NOTE.format(
                                   what="Google Calendar", reads="your calendar")})
    try:
        events = _fetch_calendar_today()
    except Exception as e:
        return json.dumps({"connected": False, "events": [],
                           "note": f"Calendar fetch error: {e}"})
    if _google_section_error(events):
        return json.dumps({"connected": False, "events": [], "integration": "google_calendar",
                           "note": _GOOGLE_NOT_CONNECTED_NOTE.format(
                               what="Google Calendar", reads="your calendar")})
    out = []
    for ev in (events or [])[:20]:
        out.append({
            "title": ev.get("title"),
            "start": ev.get("start_time"),
            "end": ev.get("end_time"),
            "location": ev.get("location") or "",
            "attendees": (ev.get("attendees") or [])[:6],
        })
    return json.dumps({"connected": True, "count": len(out), "events": out}, default=str)


def _tool_search_email(inp):
    """Search the user's recent Gmail (native read-only Google integration).

    Pulls recent mail via the same _collect_messages path the voice tools use and
    filters by the query string. When Google isn't linked, returns the 'needs
    connecting' note (never 'not installed') so Friday offers setup."""
    q = ((inp or {}).get('query') or '').strip()
    try:
        from services.calendar_engine import _collect_messages
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
        _hdrs = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) FridayAgent/1.0'}
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


def _tool_open_url(inp):
    url = ((inp or {}).get('url') or '').strip()
    if not (url.startswith('http://') or url.startswith('https://')):
        return f"Refusing to open non-http(s) URL: {url!r}"
    ok, why = _validate_url(url)
    if not ok:
        return (f"I did NOT open that link — it appears invalid because {why}. "
                f"This often means the URL was guessed rather than taken from real "
                f"data. Tell the user the link looks broken and offer to search for "
                f"the correct source instead. URL: {url!r}")
    try:
        # Try Chrome first, fall back to default browser
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
    repo = Path(__file__).resolve().parent
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
    # A bare name that happens to live in HOME.
    try:
        cand = HOME / raw
        if cand.exists():
            return str(cand.resolve())
    except Exception:
        pass
    return None


def _perform_open(target):
    """Open an app or a resolved path. Returns a human-facing confirmation, or
    None if nothing concrete could be resolved."""
    if not target:
        return "open_path error: no path/target provided."
    app = _open_app(target)
    if app is not None:
        return app
    resolved = _resolve_open_target(target)
    if not resolved:
        return None
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
    target = ((inp or {}).get('path') or (inp or {}).get('target') or '').strip()
    result = _perform_open(target)
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
    'system': 'system', 'settings': 'system', 'system settings': 'system',
    'config': 'system', 'preferences': 'system', 'setting': 'system',
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
    'futurespeak': 'FutureSpeak',
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
    stripped = re.sub(r'\s+(workspace|tab|panel|page|screen|view|window|section)$', '', low).strip()
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


def _evaluate_output(task_id, goal, output):
    """Grade task output with a fresh Claude call that has no build history."""
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
        resp = client.messages.create(
            model=ANTHROPIC_MODEL_DEFAULT,
            max_tokens=128,
            messages=[{"role": "user", "content": eval_prompt}],
        )
        return resp.content[0].text.strip() if resp.content else "GRADE: PARTIAL\nREASON: Evaluation unavailable."
    except Exception as e:
        return f"GRADE: PARTIAL\nREASON: Evaluation failed: {e}"


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


def _task_worker(task_id, name, prompt, description=''):
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
        system = _get_friday_system_prompt(prompt, workspace='task') + (
            "\n\n== BACKGROUND TASK MODE ==\n"
            "You are operating as an autonomous background task. Take initiative, "
            "use available tools, and produce a concrete, useful result the user can read.\n\n"
            "== RESEARCH DISCIPLINE ==\n"
            "When doing research tasks: after your first round of findings, identify which "
            "side of the question has WEAKER evidence. Run a second round explicitly targeting "
            "that weaker side to avoid confirmation bias. State both sides in your output."
        )
        # Stream a couple of milestone lines so the UI feels alive.
        _task_log(task_id, 'Calling Claude…')
        subagent_model = _load_settings().get("subagent_model") or ANTHROPIC_MODEL_DEFAULT
        _bg_label = (name or prompt or 'Task')[:24]
        # Route through the provider-agnostic agent dispatcher so a background
        # task (distill-to-wiki, deep research) never hard-fails with
        # "ANTHROPIC_API_KEY is not set" on a local/OpenAI setup.
        reply, tool_trace = _generate_agent(
            messages, system=system, max_tokens=16384, model=subagent_model,
            session_ctx={"authenticated": True, "is_background_task": True,
                         "task_id": task_id},
            orb_label=_bg_label, orb_category='monitoring', orb_icon='🛰',
            workspace='task',
        )
        for step in tool_trace or []:
            tn = step.get('name', '?')
            ti = step.get('input') or {}
            label = ti.get('query') or ti.get('path') or ti.get('command') or ti.get('url') or ''
            line = f'{tn}({str(label)[:60]})' if label else tn
            _task_log(task_id, '→ tool: ' + line)

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

        _task_log(task_id, 'Finalizing response')
        result_text = _summarize_task_outcome(name, reply, tool_trace, status=final_status)
        _task_set(task_id, status=final_status, result=result_text, ended=_time.time(),
                  verified=verified, verification_evidence=verification_summary)

        # ── Fresh-context evaluator ────────────────────────────────
        _task_log(task_id, 'Running quality evaluation…')
        evaluation = _evaluate_output(task_id, prompt, reply or '')
        if evaluation:
            _task_set(task_id, evaluation=evaluation)
            grade_line = next((l for l in evaluation.splitlines() if l.startswith('GRADE:')), '')
            if grade_line:
                _task_log(task_id, f'Eval: {grade_line}')

        _task_log(task_id, 'Done.')

        # ── Task chaining: spawn the next link if this task defines one ──
        try:
            _advance_task_chain(task_id, result_text)
        except Exception as ce:
            _task_log(task_id, f'Chain advance error: {ce}')
    except Exception as e:
        traceback.print_exc()
        _task_set(task_id, status='failed', result=f'[Error] {e}', ended=_time.time())
        _task_log(task_id, f'Error: {e}')


def _spawn_task(name, prompt, description='', on_complete=None,
                chain=None, chain_step=0):
    """Spawn a background task.

    on_complete: optional dict {"spawn": "<next step name>", "prompt": "<optional
        full instruction>", "with_context": true} — when this task finishes
        successfully, that follow-up is spawned. If with_context (default true),
        this task's result is fed in as context for the next.
    chain / chain_step: set when this task is one link of a named workflow chain
        stored in ~/.friday/workflows/. Completion advances to the next step.
    """
    task_id = str(uuid.uuid4())
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
        }
    _log_context("task_spawn", {
        "task_id": task_id,
        "name": name,
        "description": description,
        "prompt": prompt[:1000],
        "chain": chain,
        "chain_step": chain_step,
    })
    th = threading.Thread(target=_task_worker, args=(task_id, name, prompt, description), daemon=True)
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
        })
    stored = {
        'name': name.strip()[:120],
        'slug': _chain_slug(name),
        'description': (defn.get('description') or '').strip(),
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
    )


def _advance_task_chain(task_id, result_text):
    """Called when a task finishes. If it's a chain link, spawn the next step;
    otherwise honor a one-off on_complete spec. The completed task's result is
    threaded forward as context when requested."""
    with TASKS_LOCK:
        t = dict(TASKS.get(task_id) or {})
    result_text = (result_text or '').strip()

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
            "n": {"type": "integer", "description": "How many images to generate (1-4, default 1)."},
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
        "Generate REAL music from a text prompt using Google's Lyria 3 and save "
        "it to the user's creations folder. Use whenever the user asks you to "
        "'make/write/compose a song/track/beat/score/jingle', set something to "
        "music, or score a video. You CAN make music — do not say you can't. "
        "Supports instrumental or vocal songs (pass lyrics, with [verse]/[chorus] "
        "section tags), a mood-reference image, and multi-language vocals. "
        "Clips are ≤30s ('lyria-clip'); full songs use 'lyria-pro'. If cloud "
        "music isn't available, Friday writes a demo preview describing the "
        "track. A progress orb shows while it renders."),
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
        "video', or 'export a reel/vertical version'. Pass the creation "
        "filenames of the clips (in order) and optionally a music filename. The "
        "source clips' content hashes are signed into the production's "
        "provenance."),
    "input_schema": {
        "type": "object",
        "properties": {
            "clips": {"type": "array", "description": "Ordered list of video clip creation filenames (or absolute paths) to stitch.", "items": {"type": "string"}},
            "music": {"type": "string", "description": "Optional music/audio creation filename to lay under the video."},
            "transition": {"type": "string", "description": "Transition between clips: 'cut' (default), 'crossfade', or 'fadeblack'."},
            "title": {"type": "string", "description": "Optional title-card text shown at the start."},
            "exports": {"type": "array", "description": "Export presets, e.g. ['mp4-1080p','mp4-vertical-9x16','gif-preview']. Default mp4-1080p.", "items": {"type": "string"}},
            "clip_seconds": {"type": "number", "description": "Optional per-clip length in seconds (default 6)."},
        },
        "required": ["clips"],
    },
})


def _tool_generate_image(inp):
    """Generate an image via Gemini (Nano Banana) and save it to creations."""
    from services.creative_engine import generate_image
    inp = inp or {}
    prompt = (inp.get("prompt") or "").strip()
    if not prompt:
        return "generate_image error: 'prompt' is required."
    res = generate_image(
        prompt,
        model=inp.get("model"),
        style=inp.get("style"),
        aspect_ratio=inp.get("aspect_ratio") or "1:1",
        n=inp.get("n", 1),
    )
    return _creative_result_summary(res, "image")


def _tool_generate_video(inp):
    """Generate a video via Google Veo and save it to creations."""
    from services.creative_engine import generate_video
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
    from services import music_engine
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
    from services import timeline_engine
    inp = inp or {}
    clips = inp.get("clips") or []
    if not isinstance(clips, list) or not clips:
        return "compose_timeline error: 'clips' (a list of clip filenames) is required."
    transition = (inp.get("transition") or "cut").lower()
    clip_seconds = inp.get("clip_seconds") or 6
    video_clips = [{"file": c, "in": 0.0, "out": clip_seconds,
                    "transition_in": {"type": transition, "dur": 0.5}}
                   for c in clips]
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
    from services.introspection import epistemic_score
    return epistemic_score(limit=(inp or {}).get("limit", 20))


def _tool_personality_show(_inp):
    """Return Friday's current personality configuration (self-improvement)."""
    from services.introspection import personality_show
    return personality_show()


def _tool_personality_check_sycophancy(inp):
    """Flag sycophantic patterns in Friday's recent responses (self-improvement)."""
    from services.introspection import personality_check_sycophancy
    return personality_check_sycophancy(limit=(inp or {}).get("limit", 20))


CLAUDE_TOOL_HANDLERS = {
    "search_web": _tool_search_web,
    "browse_web": _tool_browse_web,
    "read_file": _tool_read_file,
    "write_file": _tool_write_file,
    "write_clipboard": _tool_write_clipboard,
    "query_trust_graph": _tool_query_trust_graph,
    "query_calendar": _tool_query_calendar,
    "search_email": _tool_search_email,
    "read_wiki": _tool_read_wiki,
    "search_wiki": _tool_search_wiki,
    "search_news": _tool_search_news,
    "run_command": _tool_run_command,
    "open_url": _tool_open_url,
    "open_path": _tool_open_path,
    "navigate": _tool_navigate,
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
    print("  [FRIDAY] pyautogui loaded — computer control available")
except ImportError:
    print("  [FRIDAY] pyautogui not installed — computer control disabled. Run: pip install pyautogui")


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
        print(f"  [FRIDAY] CC permission persist failed: {_e}")


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
    "read_wiki":            0,
    "search_wiki":          0,
    "query_trust_graph":    0,
    "query_calendar":       0,
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
    "correct_wiki":         1,
    "learn_skill":          1,
    # Ring 2 — NETWORK (external calls; requires authenticated session)
    "search_web":           2,
    "search_news":          2,   # fetches the live RSS/Brave feed (network)
    "browse_web":           2,
    "search_email":         2,
    "draft_email":          2,
    "open_url":             2,
    "open_path":            2,
    "spawn_task":           2,
    "run_command":          2,
    "generate_image":       2,   # calls the Gemini image API (network)
    "generate_video":       2,   # calls the Google Veo API (network)
    "generate_music":       2,   # calls the Lyria 3 API (network)
    "compose_timeline":     1,   # local FFmpeg assembly — no network
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
    from services import creative_memory as cm
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
    from services import creative_pipeline as cp
    from services import creative_memory as cm
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
    from services import take_comparison as tc
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

_GOVERNANCE_KEY: bytes | None = None


def _get_governance_key() -> bytes:
    """Return the HMAC signing key for BOM entries, generating once per run."""
    global _GOVERNANCE_KEY
    if _GOVERNANCE_KEY is not None:
        return _GOVERNANCE_KEY
    key_file = FRIDAY_DIR / "vault" / ".governance-key"
    if key_file.exists():
        try:
            _GOVERNANCE_KEY = key_file.read_bytes()
            return _GOVERNANCE_KEY
        except Exception:
            pass
    import os as _os
    key = _os.urandom(32)
    try:
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(key)
    except Exception:
        pass
    _GOVERNANCE_KEY = key
    return key


# ── Sovereign Vault: encryption-at-rest ──────────────────────────────
# Transparent AES-256-GCM for sensitive files (finance, health, legal,
# family). The key is derived once from FRIDAY_PASSWORD via Argon2id — see
# vault_crypto.py. When no password is set (or the crypto deps are missing)
# the key is None and every helper falls back to plaintext, so behaviour is
# unchanged for the keyless local-dev case.
try:
    import vault_crypto as _vc
    _HAS_VAULT_CRYPTO = True
except Exception:
    _vc = None
    _HAS_VAULT_CRYPTO = False

_VAULT_KEY: bytes | None = None
_VAULT_KEY_READY = False
_VAULT_CONFIG_FILE = FRIDAY_DIR / "vault" / ".vault_config.json"


def _get_vault_key() -> bytes | None:
    """Derive (once) the AES-256 vault key from FRIDAY_PASSWORD.

    Returns the 32-byte key, or None when encryption is disabled/unavailable
    (no password set, or vault_crypto/cryptography missing). On None, callers
    transparently read and write plaintext.
    """
    global _VAULT_KEY, _VAULT_KEY_READY
    if _VAULT_KEY_READY:
        return _VAULT_KEY
    _VAULT_KEY_READY = True
    if not _HAS_VAULT_CRYPTO or not FRIDAY_PASSWORD:
        if not FRIDAY_PASSWORD:
            print("[vault] FRIDAY_PASSWORD not set — sensitive data stored as PLAINTEXT at rest.")
        elif not _HAS_VAULT_CRYPTO:
            print("[vault] vault_crypto unavailable — sensitive data stored as PLAINTEXT at rest.")
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
        _VAULT_KEY = _vc.derive_key(FRIDAY_PASSWORD, bytes.fromhex(salt_hex))
        print("[vault] Encryption-at-rest ENABLED (AES-256-GCM · Argon2id).")
    except Exception as e:
        print(f"[vault] key derivation failed ({e}) — falling back to plaintext.")
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
        from services.wiki_engine import _wiki_encrypted_section_dirs
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
            from services.subagents import scope_check
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

    # Build and sign the BOM entry
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
    canonical = json.dumps(entry, sort_keys=True).encode("utf-8")
    entry["hmac"] = _hmac.new(_get_governance_key(), canonical, _hashlib.sha256).hexdigest()

    try:
        DECISION_BOM_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(DECISION_BOM_FILE, "a", encoding="utf-8") as _f:
            _f.write(json.dumps(entry) + "\n")
    except Exception as _e:
        print(f"  [GOV] BOM write failed: {_e}")

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
TOOL_REQUIRES_CONFIRMATION = {"open_url", "open_path", "navigate", "write_file"}

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
        return f"Unknown tool: {name}"

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
        return verdict.reason

    try:
        result = handler(ctx.input)
        if not isinstance(result, str):
            result = json.dumps(result, default=str)
    except Exception as e:
        traceback.print_exc()
        return f"Tool error ({name}): {e}"

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

def _hook_confirmation_gate(ctx):
    """Ask-first permission gate (interactive chat only). Pre, priority 10."""
    name = ctx.tool_name
    session_ctx = ctx.session_ctx
    _sid = (session_ctx or {}).get("session_id")
    if (name in TOOL_REQUIRES_CONFIRMATION and _sid
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


def _hook_cost_attribution(ctx, result):
    """Attribute spend to the active workspace / scheduled run. Post, priority 80.

    The Part D cost meter records token usage at the model-call sites; this hook
    is the seam that makes per-workspace / per-schedule attribution available for
    tool-driven turns. It hands the call's attribution to the cost store when one
    is present (no-op until Part D is wired) and never raises.
    """
    try:
        from services import cost_meter as _cm
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
    from mcp_client import MCPManager as _MCPManager
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
        data = json.loads(MCP_SERVERS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"servers": {}}
        return data
    except Exception:
        return {"servers": {}}


def _save_mcp_servers(cfg: dict) -> dict:
    """Persist the MCP server config (full replace of the servers map)."""
    FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
    if "servers" not in cfg:
        cfg = {"servers": cfg}
    MCP_SERVERS_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return cfg


def _mcp_sanitize(s: str) -> str:
    """Coerce a server/tool name into the [A-Za-z0-9_-] charset Anthropic and
    OpenAI tool names require."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(s))[:48]


def _make_mcp_handler(server_name: str, tool_name: str):
    """Build a CLAUDE_TOOL_HANDLERS handler that forwards to the MCP server."""
    def _handler(inp):
        if _MCP_MANAGER is None:
            return "[mcp error] MCP manager not initialized"
        return _MCP_MANAGER.call(server_name, tool_name, inp or {})
    return _handler


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
            schema = (t.get("inputSchema") or t.get("input_schema")
                      or {"type": "object", "properties": {}})
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
            from services.extension_security import gate_mcp_config
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
        print(f"  [FRIDAY] MCP client: {len(mgr.servers)} server(s) configured "
              f"({len(enabled)} enabled), connecting async…")
    except Exception as e:  # noqa: BLE001
        print(f"  [mcp] boot failed: {e}")


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


def _call_claude_agent(messages, system=None, model=None, max_tokens=16384, temperature=None, max_iters=999, pii_lookup=None, session_ctx=None, orb_label=None, orb_category='default', orb_icon='🧠'):
    """Tool-using Claude loop. Returns (final_text, tool_trace).

    pii_lookup: if a dict, tool results are scrubbed into it for rehydration.
    session_ctx: passed to _governance_check for ring-2/3 policy enforcement.
      Keys: authenticated (bool), is_background_task (bool).
    """
    client = get_anthropic_client()
    if client is None:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to start.bat / launch_now.bat and restart the server."
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
        from services import compaction as _compaction
        convo = _compaction.maybe_compact(convo, model=model)
    except Exception:
        pass

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

    # ── Process orb registration — frontend renders an orb per active agent. ──
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
        )
    except Exception:
        orb_id = None

    def _orb_safe(fn, *a, **kw):
        if not orb_id:
            return
        try:
            fn(*a, **kw)
        except Exception:
            pass

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

            _t0 = _time.time()
            resp = client.messages.create(**kwargs)
            # Cost metering (Part D): the Anthropic tool loop used to discard
            # resp.usage — capture input+output tokens with run/workspace
            # attribution from session_ctx.
            try:
                from services import cost_meter as _cm
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
                _orb_safe(process_update, orb_id, label=f"{tu.name}…",
                          step={"type": "tool", "name": tu.name, "input": tu.input, "ts": _time.time()})

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
                        tool_trace.append({"name": tu.name, "input": tu.input, "result": f"[VAULT-ZT DENY] {_zt_detail}"})
                        _bmon_log(tu.name, tu.input, f"[VAULT-ZT DENY] {_zt_detail}")
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "content": f"[VAULT ACCESS DENIED] This tool call references {_zt_detail} data. "
                                       f"Switch to a local model to access sensitive content.",
                        })
                        continue

                result = _execute_tool(tu.name, tu.input, pii_lookup=pii_lookup, session_ctx=session_ctx)

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
                      pii_lookup=None, session_ctx=None, max_iters=50, orb=None):
    """Shared OpenAI-format agentic tool loop for every OpenAI-compatible
    provider — local Ollama (gemma4 et al.) AND cloud OpenAI/OpenRouter.

    Both endpoints speak the identical wire format: the assistant turn carries
    ``tool_calls``; each tool result goes back as a ``role: "tool"`` message.
    So the loop, the UNIFIED CLAUDE_TOOLS registry, the zero-trust vault gate
    and _execute_tool's governance rings live here ONCE instead of being copied
    into each provider. The only per-provider differences — how a single round
    trip is sent and how the orb is labelled — are injected via callbacks.

      convo       — the running message list (system + history); mutated in place
      oai_tools   — OpenAI function-tool schemas, or None for single-shot text
      send_fn(convo, oai_tools) -> raw OpenAI-format response dict (one round)
      provider    — "local" | "openai", used for cost tracking + vault default
      orb(**kw)   — optional process-orb updater (no-op if omitted)

    Returns (final_text, tool_trace). Tool-less calls do exactly one round.
    """
    _orb = orb or (lambda **kw: None)
    tool_trace = []
    # Auto-compaction (Part C): condense a long transcript before the loop.
    try:
        from services import compaction as _compaction
        convo = _compaction.maybe_compact(convo, model=model)
    except Exception:
        pass
    loops = max_iters if oai_tools else 1
    for _ in range(loops):
        resp = send_fn(convo, oai_tools)

        usage = resp.get("usage", {}) or {}
        try:
            from model_router import get_router
            get_router().cost_tracker.record(
                provider, model,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
            )
        except Exception:
            pass
        # Cost metering (Part D): durable per-direction ledger with attribution.
        try:
            from services import cost_meter as _cm
            _cm.meter(provider, model, usage, session_ctx=session_ctx)
        except Exception:
            pass

        choices = resp.get("choices", [])
        msg = (choices[0].get("message", {}) if choices else {}) or {}
        tool_calls = msg.get("tool_calls") or []

        # No tools available, or the model is done calling them → final answer.
        if not oai_tools or not tool_calls:
            text = (msg.get("content") or "").strip()
            _orb(status='completed', progress=1.0, label=f'Done ({model})')
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
            try:
                targs = json.loads(fn.get("arguments") or "{}")
            except Exception:
                targs = {}

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
                    tool_trace.append({"name": tname, "input": targs,
                                       "result": f"[VAULT-ZT DENY] {_zt_detail}"})
                    convo.append({"role": "tool", "tool_call_id": tcid,
                                  "content": f"[VAULT ACCESS DENIED] references {_zt_detail} "
                                             f"data — switch to a local model to access it."})
                    continue

            result = _execute_tool(tname, targs, pii_lookup=pii_lookup,
                                   session_ctx=session_ctx)
            # Screenshots return a base64 blob — useless as text here, and CC
            # already forces the Anthropic path, so degrade gracefully.
            if tname == 'screenshot':
                result = "[screenshot captured — vision is only available on the Anthropic path]"
            tool_trace.append({"name": tname, "input": targs, "result": result[:2000]})
            convo.append({"role": "tool", "tool_call_id": tcid, "content": result})

    _orb(status='error', label='Max iters', progress=1.0)
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
            print("  [FRIDAY] KILL HOTKEY Ctrl+Shift+Q — computer control terminated")
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
        print("  [FRIDAY] Global kill hotkey active: Ctrl+Shift+Q")
    except ImportError:
        print("  [FRIDAY] pynput not installed — kill hotkey unavailable. Run: pip install pynput")
    except Exception as e:
        print(f"  [FRIDAY] Kill hotkey listener failed: {e}")


