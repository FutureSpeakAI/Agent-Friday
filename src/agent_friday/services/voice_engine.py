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
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta
from pathlib import Path
from collections import deque as _deque
from functools import wraps

_log = logging.getLogger("friday.voice_engine")
from flask import (Flask, Blueprint, jsonify, request, send_from_directory,
                   send_file, session, redirect, url_for, Response, stream_with_context)
import agent_friday.core as core
from agent_friday.core import (
    CHAT_HISTORY,
    FRIDAY_DIR,
    HOME,
    TEMP_AUDIO_DIR,
    WIKI_PROFESSIONAL_DIR,
    _HAS_TRUST_GRAPHS,
    _load_settings,
    _log_context,
    _network_is_offline,
    _save_chat_history,
    get_source_trust_graph,
)  # noqa: E501
from agent_friday.services.agent import (
    _spawn_task,
    _tool_navigate,
    _tool_open_url,
    _tool_search_news,
    _tool_search_web,
    _tool_search_wiki,
)  # noqa: E501
from agent_friday.services.calendar_engine import (
    _collect_messages,
    _fetch_calendar_today,
    _google_section_error,
)  # noqa: E501
from agent_friday.services.model_router import (
    CAREER_OPS_DIR,
    _current_session_id,
    _index_chat_turn,
)  # noqa: E501
from agent_friday.services.news_engine import (
    _deep_dive_article,
    _voice_domain_of,
)  # noqa: E501



def _tool_get_article_deep_dive(inp):
    """Voice tool: deep-read + summarize one article. Returns a spoken-ready
    JSON string with summary, implications, and key quotes.

    The quick read: a spoken answer inside news_engine.DEEP_DIVE_QUICK_BUDGET_S,
    or the article's opening marked partial, never a minute of silence."""
    inp = inp or {}
    url = (inp.get("url") or "").strip()
    result, _status = _deep_dive_article(url, title=inp.get("title"),
                                         refresh=bool(inp.get("refresh")), quick=True)
    if result.get("status") != "ok":
        return json.dumps({"error": result.get("message") or "deep dive failed"})
    out = {
        "title": result.get("title"),
        "url": result.get("url"),
        "summary": result.get("summary"),
        "implications": result.get("implications"),
        "key_quotes": (result.get("key_quotes") or [])[:4],
    }
    if result.get("partial"):
        out["partial"] = True
        out["note"] = result.get("note")
    return json.dumps(out, default=str)


def _tool_get_source_trust(inp):
    """Voice tool: trust profile for a news source/domain. Returns a JSON string
    with the composite score, a plain-language label, and the six dimension
    scores when the source has been observed."""
    inp = inp or {}
    domain = (inp.get("domain") or inp.get("source") or inp.get("url") or "").strip()
    if not domain:
        return json.dumps({"error": "Provide a domain, source name, or URL."})
    if not _HAS_TRUST_GRAPHS:
        return json.dumps({"error": "source trust graph unavailable"})
    try:
        g = get_source_trust_graph(friday_dir=FRIDAY_DIR)
        score = float(g.score_for(domain))
        label = ("highly trusted" if score >= 0.8 else
                 "generally reliable" if score >= 0.6 else
                 "mixed reliability" if score >= 0.4 else "low trust")
        out = {"domain": _voice_domain_of(domain) or domain,
               "trust_score": round(score, 3), "label": label}
        rec = g.get(domain)
        if rec:
            out["name"] = rec.get("name")
            out["observations"] = rec.get("observation_count") or rec.get("observations")
        try:
            out["dimensions"] = {k: round(float(v), 2)
                                 for k, v in (g.dimensions_for(domain) or {}).items()}
        except Exception:
            pass
        return json.dumps(out, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Short-lived reads for a live conversation ───────────────────────────────
#
# Calendar and mail are live Google API calls, measured at 3-7 s each. In a
# spoken turn that is dead air. A Live session warms them in the background
# the moment it opens (warm_voice_reads), and a tool call inside VOICE_READ_TTL_S
# of the last read answers from memory. Governance is unchanged: the tool call
# still goes through _execute_tool; only the Google round trip is reused.

VOICE_READ_TTL_S = 90
_VOICE_READS: dict = {}
_VOICE_READS_LOCK = threading.Lock()


def _voice_read(key, fetch, ttl=VOICE_READ_TTL_S):
    now = _time.time()
    with _VOICE_READS_LOCK:
        hit = _VOICE_READS.get(key)
    if hit is not None and now - hit[0] < ttl:
        return hit[1]
    value = fetch()
    with _VOICE_READS_LOCK:
        _VOICE_READS[key] = (_time.time(), value)
    return value


def _voice_mail():
    return _voice_read("mail", lambda: _collect_messages(limit=25))


def _voice_calendar():
    return _voice_read("calendar", _fetch_calendar_today)


def warm_voice_reads():
    """Fetch calendar, mail and news in the background. Never blocks."""
    def run():
        for key, fetch in (("calendar", _fetch_calendar_today),
                           ("mail", lambda: _collect_messages(limit=25))):
            try:
                _voice_read(key, fetch, ttl=0)
            except Exception as e:
                _log.info("voice warm-up: %s not fetched (%s)", key, e)
        try:
            from agent_friday.services.news_engine import warm_news_cache
            warm_news_cache(8)
        except Exception as e:
            _log.info("voice warm-up: news not fetched (%s)", e)
    threading.Thread(target=run, name="voice-warm", daemon=True).start()


def _tool_query_calendar(_inp):
    """Voice tool: today's + tomorrow's calendar as a spoken-ready JSON string.

    Powers global voice commands like 'what's next on my calendar?' from any
    workspace. Returns {connected, count, events:[{title,start,end,location,
    attendees}]} or a note when Google isn't linked."""
    events = _voice_calendar()
    err = _google_section_error(events)
    if err:
        return json.dumps({"connected": False, "note": err, "events": []})
    out = []
    for ev in (events or [])[:12]:
        out.append({
            "title": ev.get("title"),
            "start": ev.get("start_time"),
            "end": ev.get("end_time"),
            "location": ev.get("location") or "",
            "attendees": (ev.get("attendees") or [])[:6],
        })
    return json.dumps({"connected": True, "count": len(out), "events": out}, default=str)


def _tool_check_email(inp):
    """Voice tool: recent email with urgent/unread flags as a spoken-ready JSON.

    Powers 'any urgent emails?' from any workspace. Returns {connected, source,
    count, messages:[{from,subject,snippet,unread,urgent,lane,when}]}."""
    inp = inp or {}
    try:
        limit = max(1, min(25, int(inp.get("limit", 12))))
    except Exception:
        limit = 12
    try:
        cards, source = _voice_mail()
    except Exception as e:
        return json.dumps({"connected": False, "note": str(e), "messages": []})
    if source == "empty":
        return json.dumps({"connected": False,
                           "note": "No mailbox linked or message cache is empty.",
                           "messages": []})
    urgent_only = bool(inp.get("urgent_only"))
    out = []
    for c in (cards or []):
        is_urgent = bool(c.get("urgent") or c.get("priority") == "high"
                         or c.get("lane") in ("urgent", "priority"))
        if urgent_only and not is_urgent:
            continue
        out.append({
            "from": c.get("sender") or c.get("from") or "",
            "subject": c.get("subject") or c.get("title") or "",
            "snippet": (c.get("snippet") or c.get("preview") or "")[:160],
            "unread": bool(c.get("unread")),
            "urgent": is_urgent,
            "lane": c.get("lane") or "",
            "when": c.get("timestamp") or c.get("date") or "",
        })
        if len(out) >= limit:
            break
    return json.dumps({"connected": True, "source": source,
                       "count": len(out), "messages": out}, default=str)


# Tool surface exposed to the Live voice session. Each entry:
#   (name, description, {prop: (type, desc)}, [required])
# Kept as a plain spec so _build_voice_live_tools can render google.genai
# FunctionDeclarations without importing types at module load.
_VOICE_LIVE_TOOLS = [
    ("query_calendar",
     "Get the user's calendar — today's and tomorrow's events with times, "
     "locations, and attendees. Use whenever they ask 'what's next', 'what's on "
     "my calendar', 'am I free at…', or anything schedule-related. Works from any "
     "workspace. If the result has connected:false, the Calendar integration just "
     "needs a one-time connection — tell the user that and OFFER to help connect "
     "it; do NOT say you can't access their calendar.",
     {}, []),
    ("check_email",
     "Check the user's recent email and flag anything urgent or unread. Use when "
     "they ask 'any urgent emails', 'what's in my inbox', or 'did I hear back "
     "from…'. Set urgent_only to surface only the pressing items. Works from any "
     "workspace. If the result has connected:false, Gmail just needs a one-time "
     "connection — tell the user that and OFFER to help connect it; do NOT say you "
     "can't access their email.",
     {"urgent_only": ("boolean", "Only return urgent/priority messages."),
      "limit": ("integer", "Max messages (1-25, default 12).")}, []),
    ("search_news",
     "Search the live news feed (the same RSS feed the News workspace shows) for "
     "current stories matching a query. Returns ranked hits with title, snippet, "
     "source, trust rating, and URL. Use when the user asks for related coverage, "
     "'any other stories on X', or to ground a claim in current reporting. Omit "
     "the query for the day's top stories across every section. Stories you have "
     "already told in this conversation are left out; if it says out_of_stories, "
     "say so plainly instead of repeating an old story.",
     {"query": ("string", "Keywords across headline/snippet/source. Blank = top stories."),
      "limit": ("integer", "Max stories (1-25, default 8).")}, []),
    ("get_briefing",
     "Read Friday's own daily news briefing for today: the curated, ranked "
     "summary of the day's important stories across sections. Use it FIRST when "
     "the user asks for the news, the briefing, 'what's happening in the world' "
     "or a rundown of the day, then go through it story by story, in plain facts "
     "(who, what, where), without teasing.",
     {}, []),
    ("search_web",
     "Search the open web in real time for information that is NOT in the news "
     "feed — background, definitions, people, companies, or events the feed "
     "doesn't cover. Returns ranked snippets with URLs.",
     {"query": ("string", "What to search for.")}, ["query"]),
    ("open_url",
     "Open a URL in the user's browser. ASK PERMISSION FIRST: say a short spoken "
     "yes/no question ('Want me to open that in your browser?') and only call this "
     "tool with confirmed=true AFTER the user agrees. Only ever open a URL that "
     "came from real data (a news item, a source you looked up) — never a link you "
     "reconstructed from memory. ALWAYS prefer a URL that ends with a "
     "#:~:text=<exact%20passage> text fragment so the cited passage is "
     "highlighted on the page when it opens.",
     {"url": ("string", "Full https:// URL, ideally with a #:~:text= highlight fragment."),
      "title": ("string", "Short title of the page (for the chat citation chip)."),
      "confirmed": ("boolean", "Set true ONLY after the user has verbally agreed to open it.")}, ["url"]),
    ("get_source_trust",
     "Look up Friday's trust profile for a news source. Returns a composite trust "
     "score (0-1), a plain-language label, and dimension scores. Use when the "
     "user asks 'how reliable is that source', 'who reported this', or 'can we "
     "trust them'.",
     {"domain": ("string", "Source domain, name, or an article URL, e.g. 'reuters.com'.")}, ["domain"]),
    ("get_article_deep_dive",
     "Deep-read a single article and return a structured summary, what it means "
     "for the user, and key verbatim quotes. Use when the user asks to 'go deeper', "
     "'tell me more about that story', or 'what are the implications'.",
     {"url": ("string", "Full https:// URL of the article to deep-dive."),
      "title": ("string", "Article headline, if known.")}, ["url"]),
    ("search_wiki",
     "Keyword-search Friday's personal wiki for background context the user has "
     "saved. Returns up to a few hits with a path and excerpt.",
     {"query": ("string", "Keywords to match in the wiki."),
      "limit": ("integer", "Max hits (1-20, default 5).")}, ["query"]),
    ("navigate_workspace",
     "Switch the Friday desktop UI to a workspace on-screen for the user. If the "
     "user JUST asked to go there in their last message ('show me the calendar'), "
     "call it right away with confirmed=true. If YOU are proposing the move, ask "
     "first ('I can switch to the News workspace — shall I?') and only call with "
     "confirmed=true after they agree. Workspaces: {workspace_ids}.",
     {"workspace": ("string", "Workspace id or spoken name, e.g. 'news', 'settings'."),
      "confirmed": ("boolean", "True if the user asked for this workspace or has agreed to the switch.")}, ["workspace"]),
    ("spawn_task",
     "Start a long-running background task (a 'workflow') that keeps working "
     "while the conversation continues — deep research, multi-step analysis, "
     "drafting a long brief, or anything that takes more than about ten "
     "seconds. THIS IS THE ONLY WAY to do work in voice that outlives the "
     "current turn: if the user asks you to research, investigate, analyse, "
     "compile, monitor, or write something substantial, call this tool rather "
     "than describing what you are about to do. Progress appears in the user's "
     "Task Tray (bottom-right). Optionally chain a follow-up with on_complete.",
     {"name": ("string", "Short human-readable task title, e.g. 'Research the Zelda short film'."),
      "prompt": ("string", "The full instruction the background agent should execute."),
      "description": ("string", "Optional one-line subtitle shown in the Task Tray."),
      "on_complete_spawn": ("string", "Optional title of a follow-up task to auto-start when this one succeeds."),
      "on_complete_prompt": ("string", "Optional full instruction for that follow-up task.")},
     ["name", "prompt"]),
    # voice-system-clean-sheet.md §4.5 (D7): local brain, cloud mouth. The
    # ONE tool that lets Gemini Live reach the user's context honestly -- by
    # asking their local model, whose sealed answer is all Google ever sees.
    ("ask_friday",
     "Ask Friday's local model, which has full access to the user's notes, "
     "memory, knowledge graph, files, calendar and email. Use it for ANY "
     "question about the user's own context (their notes, their projects, what "
     "they wrote, what they decided, their wiki, their memory), and for anything "
     "that needs a tool you do not have. Announce it first ('Let me ask Friday.'), "
     "then call it, then speak the answer as given. The answer has already "
     "passed the user's privacy gate; if it says something was withheld, say "
     "so plainly rather than guessing.",
     {"question": ("string", "The question, in full, as Friday's local model should hear it.")},
     ["question"]),
]


def _tool_ask_friday(inp):
    """Dispatch the question to the LOCAL agent pipeline with the full contract
    (the same `_generate_agent` a local voice turn uses, on the resident
    brain seat, reply cap 300), then seal the answer for google-gemini.

    The seal is applied HERE, not only by the Live tool-call runner, so the
    withheld-whole guarantee (`_gate_voice_tool_result`: a withheld result is
    the marker, never a partial redaction) holds for every caller. The vault's
    TIER_2/3 content is read by the local model and never crosses.
    """
    from agent_friday.routes.voice import (  # route-owned prompt + gate
        _build_voice_system_prompt, _gate_voice_tool_result, _voice_reply_cap)
    from agent_friday.services.agent import _generate_agent
    question = str((inp or {}).get("question") or "").strip()
    if not question:
        return "ask_friday needs a question."
    settings = _load_settings() or {}
    try:
        from agent_friday.services import local_seats
        seat = local_seats.resolve("brain")
    except Exception:
        seat = None
    if not seat:
        return ("Friday's local model is not loaded right now, so the user's "
                "context cannot be reached from this session. Say so plainly.")
    system, _meta = _build_voice_system_prompt(settings)
    # The relay note and the volatile context ride in the USER turn: the
    # seat's template re-prefills the whole prompt on any system-message
    # change, so the system text stays the one the
    # local sessions and the proofs already have in cache.
    from agent_friday.routes.voice import _voice_user_message
    user = _voice_user_message(
        "You are answering a question RELAYED from a cloud voice session. "
        "Answer in one to three plain spoken sentences; the answer will be "
        "read aloud by another model. Do not mention the relay.\n\n"
        + question, settings, volatile=_meta.get("volatile"))
    try:
        text, _trace = _generate_agent(
            [{"role": "user", "content": user}], system=system, model=seat,
            max_tokens=_voice_reply_cap(settings),
            session_ctx={"authenticated": True, "provider": "local",
                         "is_voice": True, "surface": "voice-live-relay"},
            workspace=settings.get("active_workspace") or "")
    except Exception as e:
        _log.error("ask_friday failed: %s: %s", type(e).__name__, e, exc_info=True)
        return f"Friday's local model could not answer ({type(e).__name__})."
    return _gate_voice_tool_result((text or "").strip(), "ask_friday")


# ── Tools BORROWED VERBATIM from the text registry ────────────────────────
#
# The hand-written table above is the voice surface's original sin: it is a
# SECOND inventory, maintained by hand, of tools that already exist in
# `services.agent.CLAUDE_TOOLS`. Two lists drift, and drift is what the user
# hears as a lie -- the system prompt is assembled from the TEXT prompt, which
# advertises read_file / write_file / open_path / screenshot, while the Live
# API was handed nine declarations that contain none of them. Friday then did
# the only thing left to her: announced the action and nothing happened.
#
# These names are not redeclared here. They are RESOLVED out of CLAUDE_TOOLS at
# build time -- name, description and JSON schema verbatim -- so a change to the
# text registry reaches voice in the same edit, and a tool that is removed from
# the registry (because its dependency is missing, say) disappears from voice
# rather than lingering as a promise. Execution goes through `_execute_tool`,
# the single choke point, so ring policy, the vault gate, the sandbox, rate
# limiting, receipts and the audit log all apply exactly as they do in text.
#
# run_command is deliberately NOT here. Shell execution driven by a speech
# recogniser is a different risk class from reading a file, and nothing in the
# reported failure needs it.
_VOICE_SHARED_TOOLS = (
    "read_file",
    "search_files",
    "write_file",
    "open_path",
    "search_email",
    "screenshot",
    # The desktop and the live situation, the same in voice as in text. Both
    # answer inside the bridge's hard limit: navigate_to resolves within its
    # own budgets and check_situation reads memory.
    "navigate_to",
    "check_situation",
)


def _voice_shared_tool_specs():
    """Resolve _VOICE_SHARED_TOOLS out of the text registry.

    Returns a list of (name, description, input_schema) for the names that are
    ACTUALLY registered. A name that is absent -- never added, or dropped
    because its dependency is missing -- is silently skipped here and therefore
    never declared to Gemini and never named in the surface note. Absent beats
    present-but-broken: the model is told the truth either way.
    """
    try:
        from agent_friday.services.agent import CLAUDE_TOOLS, CLAUDE_TOOL_HANDLERS
    except Exception as e:  # pragma: no cover - import-time failure
        _log.error("voice shared tools unavailable (registry import failed): %s", e)
        return []
    by_name = {t.get("name"): t for t in CLAUDE_TOOLS if isinstance(t, dict)}
    out = []
    for name in _VOICE_SHARED_TOOLS:
        entry = by_name.get(name)
        if not entry or name not in CLAUDE_TOOL_HANDLERS:
            _log.warning("voice shared tool %r is not in the text registry - "
                         "not declaring it to the Live session", name)
            continue
        out.append((name, entry.get("description") or name,
                    entry.get("input_schema") or {"type": "object", "properties": {}}))
    return out


def _voice_tool_names():
    """Every tool name this voice session can actually call, native + shared.

    The ONE function both the Live declarations and the system-prompt surface
    note are built from, so the prompt can never name a tool the API was not
    given.
    """
    return [t[0] for t in _VOICE_LIVE_TOOLS] + [n for n, _d, _s in _voice_shared_tool_specs()]


def _navigate_tool_description(desc):
    """Fill the {workspace_ids} placeholder from the SAME alias table the
    navigation resolver uses. One source of truth — the hard-coded list this
    replaces had gone stale (no settings/marketplace), so Gemini told users it
    was 'opening settings' while the resolver sent them to System."""
    if "{workspace_ids}" not in desc:
        return desc
    try:
        from agent_friday.services.agent import _WORKSPACE_ALIASES
        ids = ", ".join(sorted(set(_WORKSPACE_ALIASES.values())))
    except Exception:
        ids = ("calendar, career, code, contacts, content, draft, family, "
               "finance, futurespeak, health, home, marketplace, messages, "
               "knowledge, news, settings, studio, system, trust")
    return desc.replace("{workspace_ids}", ids)


def _build_voice_live_tools(types, behavior=None):
    """Render _VOICE_LIVE_TOOLS as a google.genai Tool list for the Live config.

    `types` is google.genai.types (imported inside the live handler). Returns a
    single-element list holding one Tool with all function declarations, or []
    if the SDK shape is unavailable (caller then runs tool-free).

    `behavior` is an optional google.genai Behavior applied to EVERY
    declaration — "NON_BLOCKING" for the models that refuse BLOCKING function
    calls (see _model_requires_non_blocking_tools). It is passed as a plain
    string and resolved here so an SDK too old to define types.Behavior
    degrades to today's behaviour (no field set) instead of raising and
    dropping the whole tool surface, which is how a voice session ends up
    narrating actions it cannot take.
    """
    _behavior = None
    if behavior:
        _behavior = getattr(getattr(types, "Behavior", None), str(behavior), None)
        if _behavior is None:
            _log.warning("voice live tools: this google-genai has no "
                         "types.Behavior.%s - declaring tools WITHOUT a "
                         "behavior; a model that requires NON_BLOCKING will "
                         "refuse the connect and fall back", behavior)

    def _decl(**kw):
        if _behavior is not None:
            kw["behavior"] = _behavior
        return types.FunctionDeclaration(**kw)

    _type_map = {
        "string": types.Type.STRING,
        "integer": types.Type.INTEGER,
        "number": types.Type.NUMBER,
        "boolean": types.Type.BOOLEAN,
    }
    decls = []
    for name, desc, props, required in _VOICE_LIVE_TOOLS:
        schema_props = {
            pname: types.Schema(type=_type_map.get(ptype, types.Type.STRING),
                                description=pdesc)
            for pname, (ptype, pdesc) in props.items()
        }
        decls.append(_decl(
            name=name, description=_navigate_tool_description(desc),
            parameters=types.Schema(type=types.Type.OBJECT,
                                    properties=schema_props,
                                    required=list(required) or None),
        ))

    # Tools borrowed from the text registry, rendered from their OWN JSON
    # schema so the declaration Gemini receives is the declaration Claude
    # receives. A schema this renderer cannot express is skipped WITH A LOG and
    # therefore never declared -- it must not arrive as a silently truncated
    # tool the model then calls with arguments the handler does not understand.
    for name, desc, schema in _voice_shared_tool_specs():
        try:
            rendered = _json_schema_to_genai(types, schema, _type_map)
        except Exception as e:
            _log.error("voice shared tool %r: schema could not be rendered for "
                       "the Live API (%s) - NOT declaring it", name, e)
            continue
        decls.append(_decl(name=name, description=desc, parameters=rendered))

    return [types.Tool(function_declarations=decls)] if decls else []


def _json_schema_to_genai(types, schema, type_map):
    """Render one JSON-Schema object as a google.genai Schema.

    Deliberately narrow: object / string / integer / number / boolean / array
    of those, plus `enum` and `description`. Anything richer raises, and the
    caller drops the tool rather than declaring a lossy version of it.
    """
    if (schema or {}).get("type") not in (None, "object"):
        raise ValueError("top-level schema must be an object")
    props = {}
    for pname, pspec in ((schema or {}).get("properties") or {}).items():
        props[pname] = _json_schema_leaf(types, pspec, type_map, pname)
    return types.Schema(
        type=types.Type.OBJECT,
        properties=props or None,
        required=list((schema or {}).get("required") or []) or None,
    )


def _json_schema_leaf(types, spec, type_map, pname):
    spec = spec or {}
    jtype = spec.get("type") or "string"
    kwargs = {}
    if spec.get("description"):
        kwargs["description"] = spec["description"]
    if spec.get("enum"):
        kwargs["enum"] = [str(v) for v in spec["enum"]]
    if jtype == "array":
        return types.Schema(
            type=types.Type.ARRAY,
            items=_json_schema_leaf(types, spec.get("items") or {}, type_map,
                                    pname + "[]"),
            **kwargs)
    if jtype == "object":
        # Nested free-form objects have no faithful rendering here; refuse
        # rather than flatten it to a string the handler cannot parse.
        raise ValueError("nested object property %r is not supported" % pname)
    if jtype not in type_map:
        raise ValueError("unsupported type %r on property %r" % (jtype, pname))
    return types.Schema(type=type_map[jtype], **kwargs)


VOICE_BRIEFING_CHARS = 7000
_TOLD_STOPWORDS = {"that", "this", "with", "from", "have", "says", "said", "will",
                   "about", "after", "over", "into", "their", "they", "what",
                   "when", "were", "been", "more", "than", "report", "reports"}


def _trim_briefing(text):
    """A briefing trimmed to what one spoken rundown can use."""
    text = str(text or "")
    if len(text) > VOICE_BRIEFING_CHARS:
        text = text[:VOICE_BRIEFING_CHARS] + " [... the briefing continues; ask for more]"
    return text


def _title_told(title: str, spoken: str) -> bool:
    """Did Friday actually SAY this story, going by its distinctive words?

    Offering a story to the model is not telling it: it may speak two of five.
    A story counts as told when at least two of its significant title words,
    and at least a quarter of them, appear in what Friday said aloud.
    """
    words = {w for w in re.findall(r"[a-z0-9']+", (title or "").lower())
             if len(w) >= 4 and w not in _TOLD_STOPWORDS}
    if not words:
        return False
    spoken_l = (spoken or "").lower()
    hits = sum(1 for w in words if w in spoken_l)
    return hits >= 2 and hits / len(words) >= 0.25


def _news_args_for_session(args: dict, session) -> dict:
    """search_news arguments with this conversation's coverage attached."""
    if not isinstance(session, dict):
        return args
    offered = session.setdefault("news_offered", [])
    spoken = " ".join(session.get("spoken") or [])
    told = [t for t in offered if _title_told(t, spoken)]
    out = dict(args)
    out["_covered"] = told
    out["_offered"] = [t for t in offered if t not in told]
    return out


def _voice_tool_run(name, args, send_client, session=None):
    """Execute one Live tool call, emit any client-side side effect, and return a
    SHORT text/JSON result for the model to speak from. `send_client(obj)` pushes
    a WS frame to the browser (navigate action, citation chip). Never raises.

    `session` is the live call's own state (the voice bridge keeps one per
    connection): the stories offered so far and what Friday has said, so the
    news tools do not recycle the same stories."""
    name = (name or "").strip()
    args = dict(args or {})

    def _needs_confirm(_what):
        """The user hasn't agreed yet — tell the model to ask, and do nothing."""
        return (f"NOT DONE YET — you must get the user's spoken permission first. "
                f"Ask a short yes/no question about {_what}, then call this tool "
                f"again with confirmed=true only after they say yes.")

    # Every voice tool runs through agent._execute_tool, the one path to a
    # handler, so the governance check, provenance ledger, audit and PII hooks
    # apply to a spoken request exactly as to a typed one.
    from agent_friday.services.agent import _execute_tool

    def _governed(tool, fn, a):
        return _execute_tool(tool, a, handler=fn, session_ctx={
            "authenticated": True, "surface": "voice-live", "taint_key": "voice-live"})

    try:
        if name == "ask_friday":
            try:
                send_client({"type": "status", "text": "asking local model"})
                send_client({"type": "stage", "stage": "mind", "state": "busy",
                             "detail": "asking local model"})
            except Exception:
                pass
            try:
                return _governed("ask_friday", _tool_ask_friday, args)
            finally:
                try:
                    send_client({"type": "stage", "stage": "mind", "state": "idle",
                                 "detail": ""})
                except Exception:
                    pass
        if name in ("navigate_workspace", "navigate"):
            if not args.get("confirmed"):
                return _needs_confirm(f"switching to the {args.get('workspace') or 'that'} workspace")
            # Pause speech while the action runs; resume + report after.
            try:
                send_client({"type": "tts_pause"})
            except Exception:
                pass
            res = _governed("navigate", _tool_navigate, args)
            ok = isinstance(res, str) and res.startswith("NAV_OK:")
            if ok:
                wsid = res.split(":", 1)[1].split(" ", 1)[0].strip()
                try:
                    send_client({"type": "action",
                                 "actions": [{"type": "navigate", "workspace": wsid}]})
                except Exception:
                    pass
            try:
                send_client({"type": "tts_resume"})
            except Exception:
                pass
            if ok:
                return f"Done — I've opened the {wsid} workspace on screen. Tell the user it's up."
            return f"That didn't work: {res}. Tell the user, and offer another approach."
        if name == "open_url":
            url = (args.get("url") or "").strip()
            if not args.get("confirmed"):
                return _needs_confirm(f"opening {url or 'that link'} in the browser")
            try:
                send_client({"type": "tts_pause"})
            except Exception:
                pass
            res = _governed("open_url", _tool_open_url, args)
            # _tool_open_url validates the URL and returns an "I did NOT open"
            # message for dead/malformed links — surface that as a failure to report.
            opened = isinstance(res, str) and res.lower().startswith("opened")
            if opened and url.startswith("http"):
                try:
                    send_client({"type": "cite", "label": "Opened",
                                 "sources": [{"title": args.get("title") or url,
                                              "source": _voice_domain_of(url),
                                              "url": url}]})
                except Exception:
                    pass
            try:
                send_client({"type": "tts_resume"})
            except Exception:
                pass
            if opened:
                return f"Done — opened it in the browser. {res}"
            return (f"I did not open it because the link looks invalid. {res} "
                    f"Tell the user the link appears broken and offer to find the right source.")
        if name == "get_briefing":
            from agent_friday.services.agent import _tool_get_briefing
            return _trim_briefing(_governed("get_briefing", _tool_get_briefing, args))
        if name == "search_news":
            res = _governed("search_news", _tool_search_news,
                            _news_args_for_session(args, session))
            try:
                hits = (json.loads(res) or {}).get("hits", [])
                if isinstance(session, dict):
                    seen = session.setdefault("news_offered", [])
                    seen.extend(h.get("title") for h in hits
                                if h.get("title") and h.get("title") not in seen)
                chips = [{"title": h.get("title"), "source": h.get("source"),
                          "url": h.get("url")} for h in hits[:6] if h.get("url")]
                if chips:
                    send_client({"type": "cite", "label": "Related stories", "sources": chips})
            except Exception:
                pass
            return res
        if name == "search_web":
            return _governed("search_web", _tool_search_web, args)
        if name == "search_wiki":
            return _governed("search_wiki", _tool_search_wiki, args)
        if name == "spawn_task":
            # Voice's one durable-work primitive. The Live model is told (in the
            # shared system prompt) to delegate anything longer than a turn; before
            # this existed the prompt advertised spawn_task while the Live tool
            # surface omitted it, so Friday narrated "starting that now" and no
            # workflow was ever created.
            from agent_friday.services.agent import _tool_spawn_task
            _payload = {"name": args.get("name"),
                        "prompt": args.get("prompt"),
                        "description": args.get("description")}
            _oc_spawn = (args.get("on_complete_spawn") or "").strip()
            _oc_prompt = (args.get("on_complete_prompt") or "").strip()
            if _oc_spawn and _oc_prompt:
                _payload["on_complete"] = {"spawn": _oc_spawn, "prompt": _oc_prompt,
                                           "with_context": True}
            res = _governed("spawn_task", _tool_spawn_task, _payload)
            try:
                send_client({"type": "task_spawned",
                             "name": args.get("name") or "Background task"})
            except Exception:
                pass
            return res
        if name == "query_calendar":
            return _governed("query_calendar", _tool_query_calendar, args)
        if name == "check_email":
            return _governed("check_email", _tool_check_email, args)
        if name == "get_source_trust":
            return _governed("get_source_trust", _tool_get_source_trust, args)
        if name == "get_article_deep_dive":
            res = _governed("get_article_deep_dive", _tool_get_article_deep_dive, args)
            url = (args.get("url") or "").strip()
            if url.startswith("http"):
                try:
                    send_client({"type": "cite", "label": "Deep dive",
                                 "sources": [{"title": args.get("title") or url,
                                              "source": _voice_domain_of(url),
                                              "url": url}]})
                except Exception:
                    pass
            return res

        # ── Tools borrowed from the text registry ──────────────────────────
        # Executed through _execute_tool, the single choke point, so the
        # PreToolUse chain (confirmation -> governance/ring -> vault -> sandbox
        # -> rate limit) and the PostToolUse chain (audit, PII scrub, receipts)
        # run exactly as they do for a typed turn. The tool runs LOCALLY; only
        # its RESULT crosses to Google, and the caller in routes/voice.py gates
        # that result through egress_gate before it is sent back -- so a cloud
        # model can read a PDF in Downloads without the vault ever leaving.
        if name in dict((n, d) for n, d, _sc in _voice_shared_tool_specs()):
            return _execute_tool(name, args, session_ctx={
                # The live socket is authenticated before the session opens
                # (routes/voice.py rejects unauthenticated connects), which is
                # what ring 2 asks for. Ring 3 still consults the Computer
                # Control grant independently, so a screenshot with CC off
                # comes back as an honest deny, not a silent nothing.
                "authenticated": True,
                "surface": "voice-live",
                "taint_key": "voice-live",
            })
    except Exception as e:
        _log.error("Voice tool %r raised %s: %s", name, type(e).__name__, e, exc_info=True)
        return (f"I ran into a problem using the {name} tool: {type(e).__name__}. "
                f"Please try again, or ask me a different way.")
    return (f"TOOL CALL FAILED - there is no tool called '{name}' in voice mode, "
            f"so nothing ran and there is no result. Tell the user plainly that "
            f"you could not do it. Do not describe an outcome: there isn't one.")


# ═══════════════════════════════════════════════════════════════
#  TEXT-TO-SPEECH & AUDIO
# ═══════════════════════════════════════════════════════════════

def _local_tts_available():
    """True if any offline TTS engine is importable (pyttsx3 or Piper)."""
    try:
        import pyttsx3  # noqa: F401
        return True
    except Exception:
        pass
    try:
        from agent_friday.services.local_voice import deps_installed
        if deps_installed():
            return True
    except Exception:
        pass
    return False


def _synthesize_tts_wav_local(text):
    """Offline TTS via pyttsx3 (SAPI5 on Windows). Returns a WAV BytesIO or None.

    The fully-local fallback for spoken output when Gemini TTS is unreachable
    (offline, no key, or an API error). No network, no cloud — the audio is
    rendered on-device by the OS speech engine.
    """
    if not text or not str(text).strip():
        return None
    _has_pyttsx3 = True
    try:
        import pyttsx3
    except Exception:
        _has_pyttsx3 = False
    if not _has_pyttsx3:
        # Piper fallback when pyttsx3 is not installed
        try:
            from agent_friday.services.local_voice import get_local_voice_engine
            engine = get_local_voice_engine()
            if engine.available():
                if not engine._ready:
                    engine.ensure_ready()
                if engine._ready:
                    pcm = engine.synthesize(str(text))
                    if pcm:
                        import wave
                        buf = io.BytesIO()
                        with wave.open(buf, 'wb') as wf:
                            wf.setnchannels(1)
                            wf.setsampwidth(2)
                            wf.setframerate(24000)
                            wf.writeframes(pcm)
                        buf.seek(0)
                        return buf
        except Exception as e:
            print(f"  [voice] Piper TTS fallback failed: {e}")
        return None
    import tempfile
    tmp_path = None
    try:
        engine = pyttsx3.init()
        try:
            rate = (_load_settings() or {}).get("voice_local_rate")
            if rate:
                engine.setProperty("rate", int(rate))
        except Exception:
            pass
        fd, tmp_path = tempfile.mkstemp(suffix=".wav", dir=str(TEMP_AUDIO_DIR))
        os.close(fd)
        engine.save_to_file(str(text), tmp_path)
        engine.runAndWait()
        try:
            engine.stop()
        except Exception:
            pass
        data = Path(tmp_path).read_bytes()
        if not data:
            return None
        buf = io.BytesIO(data)
        buf.seek(0)
        return buf
    except Exception as e:
        print(f"  [voice] local TTS failed: {e}")
        return None
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


def _synthesize_tts_wav(text, voice=None, style='briefing', allow_local=True):
    """Synthesize `text` to a WAV (BytesIO at pos 0), Gemini TTS with local fallback.

    Shared by /api/voice/tts and the News Front-Page audio briefing. Offline-first:
    with no Gemini key or when the network monitor reports OFFLINE we render with
    the local pyttsx3 engine. If a live Gemini call fails for any other reason we
    also fall back to local TTS (when allow_local and settings.offline_voice_fallback)
    so spoken output degrades gracefully instead of erroring.

    PII gate: spoken replies are a cloud egress point of their own — a reply
    generated by a LOCAL model may legitimately contain vault values, and those
    must not transit Gemini TTS. Text containing PII is synthesized locally
    (full fidelity, nothing leaves the machine); if the local engine is
    unavailable, Gemini speaks the scrubbed text only.
    """
    try:
        _pii_lookup = core._scrub_pii(text)[1]
    except Exception:
        _pii_lookup = {}
    if _pii_lookup:
        _buf = _synthesize_tts_wav_local(text)
        if _buf is not None:
            return _buf
        scrubbed = core._scrub_pii(text)[0]
        text = core._PII_TAG_RE.sub("[redacted]", scrubbed)

    # Local-only is an absolute override, the same guarantee routes/chat.py's
    # vision path and routes/voice.py's engine selection enforce — it must
    # win regardless of key presence or network status. Checking only PII
    # content and connectivity here would let "read this aloud" and the News
    # audio briefing send spoken text to Gemini TTS with Local-Only Mode on,
    # so model_routing.mode is consulted before any cloud call.
    try:
        _local_only = str(((_load_settings() or {}).get('model_routing') or {})
                          .get('mode') or '').strip().lower() == 'local_only'
    except Exception:
        _local_only = False
    if _local_only:
        _buf = _synthesize_tts_wav_local(text)
        if _buf is not None:
            return _buf
        raise RuntimeError(
            "local-only mode is on and no local voice engine is ready — "
            "refusing to send spoken text to Gemini TTS")

    # local_preferred means "local first, cloud when it helps" — the same
    # idiom routes/chat.py:369 (vision) and routes/core_routes.py:1085
    # (file-upload analyze) already use (`mode in ('local_only',
    # 'local_preferred')`). TTS must honor it too rather than going straight
    # to Gemini whenever a key is present, which would contradict the mode's
    # own definition. Unlike local_only above, a local_preferred TTS failure
    # still falls through to Gemini below — "preferred," not absolute.
    try:
        _local_preferred = str(((_load_settings() or {}).get('model_routing') or {})
                               .get('mode') or '').strip().lower() == 'local_preferred'
    except Exception:
        _local_preferred = False
    try:
        _prefer_local = allow_local and (_local_preferred or (not core.GEMINI_API_KEY)
                                         or _network_is_offline())
    except Exception:
        _prefer_local = allow_local and (_local_preferred or not core.GEMINI_API_KEY)
    if _prefer_local:
        _buf = _synthesize_tts_wav_local(text)
        if _buf is not None:
            return _buf

    try:
        return _synthesize_tts_wav_gemini(text, voice=voice, style=style)
    except Exception as e:
        allow = allow_local
        try:
            allow = allow and bool((_load_settings() or {}).get("offline_voice_fallback", True))
        except Exception:
            pass
        if allow:
            _buf = _synthesize_tts_wav_local(text)
            if _buf is not None:
                print(f"  [voice] Gemini TTS failed ({e}); used local pyttsx3 fallback")
                return _buf
        raise


def _synthesize_tts_wav_gemini(text, voice=None, style='briefing'):
    """The Gemini-TTS synthesis path (cloud). Raises on any failure."""
    import wave
    from google import genai
    from google.genai import types

    # Gemini TTS is a CLOUD call — the spoken text leaves the device. If the
    # egress gate classifies it above PUBLIC, raise so the caller's local
    # (pyttsx3/Piper) fallback speaks it on-device instead of shipping it to
    # Google. Better a robotic local voice than a sovereignty leak.
    try:
        from agent_friday.services import egress_gate as _eg
        if _eg.gate_text(text, "gemini", "voice.tts") != text:
            raise RuntimeError("TTS text withheld by egress gate (sensitive) — "
                               "routing to local voice")
    except RuntimeError:
        raise
    except Exception:
        pass  # gate unavailable → don't block speech on an infra error

    client = genai.Client(api_key=core.GEMINI_API_KEY)  # pragma: allowlist secret
    if not voice:
        try:
            voice = (_load_settings() or {}).get('tts_voice') or 'Aoede'
        except Exception:
            voice = 'Aoede'

    # Custom user-defined style prompt takes priority over the built-in styles.
    custom_style = _get_voice_style_prompt()
    if custom_style:
        style_prefix = f"{custom_style}: "
    else:
        style_prefix = {
            'briefing': "Read this aloud in a warm, conversational news-anchor voice — natural pacing, light intonation, no robotic flatness: ",
            'chat': "Say this aloud in a calm, friendly tone, like a trusted assistant talking to a colleague: ",
            'plain': "Say this aloud: ",
        }.get(style, "Read this aloud in a warm, conversational voice: ")

    speech_kwargs = {
        "voice_config": types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
        )
    }
    language = _get_voice_language()
    if language:
        speech_kwargs["language_code"] = language

    response = client.models.generate_content(
        model="gemini-2.5-flash-preview-tts",
        contents=f"{style_prefix}{text}",
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(**speech_kwargs),
        )
    )

    # Cost metering: this is a real, billed Gemini call and must be metered
    # like the Live voice models (cost_meter.PRICING carries the TTS model id
    # for this). Never allowed to break speech: any failure here is swallowed.
    try:
        from agent_friday.services import cost_meter as _cm
        _um = getattr(response, "usage_metadata", None)
        _cm.meter("gemini", "gemini-2.5-flash-preview-tts", {
            "input_tokens": getattr(_um, "prompt_token_count", 0) or 0,
            "output_tokens": getattr(_um, "candidates_token_count", 0) or 0,
        }, kind="voice")
    except Exception:
        pass

    audio_data = response.candidates[0].content.parts[0].inline_data.data
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(audio_data)
    buf.seek(0)
    return buf


# ═══════════════════════════════════════════════════════════════
#  NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════

try:
    import agent_friday.notifications_engine as _notif_engine
except Exception as _e:
    _notif_engine = None
    _log.warning("notifications_engine unavailable: %s", _e)


# ═══════════════════════════════════════════════════════════════
#  FRIDAY LIVE — Gemini Live API bridge over WebSocket
# ═══════════════════════════════════════════════════════════════

# gemini-3.8-live is Google's stable default Live model. Measured with Friday's
# own session config (tools/voice_bench/live_latency.py --friday): first audio
# 0.9 s against 2.2 s on the 2.5 native-audio model, and it answers a resumed
# session in ~0.55 s. It takes neither proactivity nor thinking_config; the
# bridge strips both per model (see the helpers below).
LIVE_MODEL = os.environ.get("FRIDAY_LIVE_MODEL", "gemini-3.8-live")
# Graceful-degradation chain. All three IDs below are verified by an actual
# bidiGenerateContent connect (open+close) against the live API —
# models.list presence alone is NOT sufficient proof, and an unverified ID in
# this chain recreates the exact 1008-misread-as-auth-failure incident this
# block exists to prevent. If you change any of these, re-verify with a real
# connect, not just models.list.
LIVE_MODEL_FALLBACK = "gemini-2.5-flash-native-audio-latest"
LIVE_MODEL_FALLBACK2 = "gemini-3.1-flash-live-preview"
# Verified the ONLY way this block accepts: a real
# bidiGenerateContent connect (open, one server message, close) against
# v1beta AND v1alpha, in the same run as a deliberately fake id
# ("gemini-3.8-live-does-not-exist") that failed 1008 — so the OK is
# evidence, not the probe rubber-stamping everything handed to it.
# gemini-3.8-live is the 2026-09-15 stable release: 131072 in / 65536 out,
# bidirectional audio, no shutdown date. It is now LIVE_MODEL, the default;
# the 2.5 native-audio models follow it in the chain (all re-verified by a
# real connect and a spoken answer on 2026-09-25, beside a fake id that
# failed 1008).
#
# Its sibling gemini-3.8-live-extended-thinking is deliberately NOT in this
# chain. It is real (models.get: bidiGenerateContent, same limits) but a
# bare connect fails 1007 "Thinking level must be specified for this model"
# — it requires thinking_config, which this chain's shared config does not
# carry. A fallback that cannot connect is worse than no fallback: it burns
# an attempt and reports a config error as if the previous model's failure
# continued. It is selectable in the catalogue instead, where
# _live_thinking_config_for() supplies the required level.
LIVE_MODEL_FALLBACK3 = "gemini-2.5-flash-native-audio-preview-09-2025"

# Model IDs that fail a live connect with 1008 "not found" (which reads like
# an auth failure). validate_live_model() reports them as status "retired" so
# the auto-correction in _resolve_voice_engine can rewrite them — the marker
# heuristic below would otherwise vouch for them forever.
# NOTE: gemini-2.5-flash-preview-native-audio was introduced as a "verified"
# fallback in an earlier overhaul pass but does not exist upstream (verified
# 1008 on connect, absent from models.list) — never resurrect it.
_RETIRED_LIVE_MODELS = {
    "gemini-live-2.5-flash-preview",
    "gemini-2.5-flash-preview-native-audio",
}
LIVE_VOICE = os.environ.get("FRIDAY_LIVE_VOICE", "Aoede")


# ── Gemini API-key validation + multi-source resolution ──────────────────────
# Root-cause hardening for the "voice broken for days" incident: the server
# process env carried a rotated (revoked) key pinned by a stale launcher
# script, while the WORKING key sat in the Windows user environment the whole
# time. os.environ is frozen at process start, so "restart after updating the
# key" only helps when the right launcher was edited. These helpers validate
# candidates with a cheap cached REST probe and pick the first key Google
# actually accepts — self-healing across rotations without a restart.

_KEY_CHECK_CACHE = {}
_KEY_CHECK_TTL = 600.0  # seconds; per-key verdicts are cached
# The cache is indexed by a keyed digest of the API key under a per-process
# random secret, so an entry id is not a stable fingerprint of the key.
_KEY_CHECK_SALT = secrets.token_bytes(32)


def validate_gemini_key(key, timeout=5.0, force=False):
    """Does this Gemini API key authenticate? Returns (ok, detail).

    Probes GET /v1beta/models?pageSize=1 with the key in the x-goog-api-key
    header — the same auth path the google-genai SDK uses for BOTH REST and
    the Live WebSocket, so a pass here means the key itself is good and any
    remaining Live failure is model/config, not credentials. Network failures
    return (True, "unverifiable…") so being offline never brands a key bad.
    Never raises.
    """
    key = (key or "").strip()
    if not key:
        return False, "no key"
    if os.environ.get("FRIDAY_TESTING"):
        return True, "testing mode — not validated"
    cache_id = _hmac.new(_KEY_CHECK_SALT, key.encode(),
                         _hashlib.sha256).hexdigest()[:16]
    now = _time.time()
    if not force:
        hit = _KEY_CHECK_CACHE.get(cache_id)
        if hit and now - hit[0] < _KEY_CHECK_TTL:
            return hit[1], hit[2]
    try:
        import urllib.request as _rq
        import urllib.error as _er
        req = _rq.Request(
            "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1",
            headers={"x-goog-api-key": key})
        try:
            with _rq.urlopen(req, timeout=timeout) as resp:
                ok = 200 <= getattr(resp, "status", 200) < 300
                detail = f"HTTP {getattr(resp, 'status', 200)}"
        except _er.HTTPError as he:
            body = ""
            try:
                body = he.read(300).decode("utf-8", "replace")
            except Exception:
                pass
            ok = False
            detail = f"HTTP {he.code}: {' '.join(body.split())[:160]}"
    except Exception as e:
        # DNS down / offline / proxy — NOT a key verdict; don't cache.
        return True, f"unverifiable ({type(e).__name__}) — assuming ok"
    _KEY_CHECK_CACHE[cache_id] = (now, ok, detail)
    return ok, detail


def _read_windows_user_env_key():
    """Live-read the Gemini key from the per-user Windows registry env.

    Catches the classic rotation trap: the user updates the key with setx /
    System Properties (or one launcher of several), but the running process
    env still holds the old value. Returns '' on non-Windows or when unset.
    """
    if os.name != "nt":
        return ""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as hk:
            for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
                try:
                    val, _typ = winreg.QueryValueEx(hk, name)
                except OSError:
                    continue
                val = str(val or "").strip()
                if val:
                    return val
    except Exception:
        pass
    return ""


def resolve_gemini_key(update_core=True):
    """Pick the freshest WORKING Gemini key across every known source.

    Candidate order: process env GEMINI_API_KEY → process env GOOGLE_API_KEY →
    settings.json gemini_api_key → Windows user-registry env → the value the
    server booted with. First candidate that passes validate_gemini_key wins;
    if none validates, the first non-empty candidate is returned with
    valid=False so callers can degrade truthfully. With update_core=True the
    winning key is installed into core.GEMINI_API_KEY (and the cached genai
    client reset) so voice, TTS and creative endpoints all self-heal.
    Returns {"key","source","valid","detail"}.
    """
    # Hermetic tests: the suite's seam is core.GEMINI_API_KEY (monkeypatched);
    # never read the host env/registry or touch the network under test.
    if os.environ.get("FRIDAY_TESTING"):
        _tkey = (core.GEMINI_API_KEY or "").strip()
        return {"key": _tkey, "source": "server startup value",
                "valid": bool(_tkey), "detail": "testing mode — not validated"}

    candidates = []

    def _add(source, key):
        key = (key or "").strip()
        if key and all(key != k for _, k in candidates):
            candidates.append((source, key))

    _add("process env GEMINI_API_KEY (launcher script)",
         os.environ.get("GEMINI_API_KEY", ""))
    _add("process env GOOGLE_API_KEY (launcher script)",
         os.environ.get("GOOGLE_API_KEY", ""))
    try:
        _add("settings.json (Settings → Accounts & Keys)",
             (_load_settings() or {}).get("gemini_api_key", ""))  # pragma: allowlist secret
    except Exception:
        pass
    _add("Windows user environment (registry)", _read_windows_user_env_key())
    _add("server startup value", core.GEMINI_API_KEY)

    picked = {"key": "", "source": "none", "valid": False,
              "detail": "no Gemini API key configured in any source"}
    first_bad = None
    for source, key in candidates:
        ok, detail = validate_gemini_key(key)
        if ok:
            picked = {"key": key, "source": source, "valid": True,
                      "detail": detail}
            break
        if first_bad is None:
            first_bad = {"key": key, "source": source, "valid": False,
                         "detail": detail}
    else:
        if first_bad is not None:
            picked = first_bad
    if update_core and picked["key"] and picked["key"] != core.GEMINI_API_KEY:
        from agent_friday.routing.provider_descriptors import key_presence
        _old = key_presence(core.GEMINI_API_KEY)
        core.GEMINI_API_KEY = picked["key"]  # pragma: allowlist secret
        try:
            core._genai_client = None  # rebuild lazily with the fresh key
        except Exception:
            pass
        _log.info("GEMINI_API_KEY replaced from %s (previous key %s, valid=%s)",
                  picked["source"], _old, picked["valid"])
    return picked


def _get_live_model():
    """Return the currently configured voice/live model from settings, falling back to LIVE_MODEL."""
    return _load_settings().get("voice_model") or LIVE_MODEL


# Known-good Gemini Live / native-audio model IDs, plus the substrings that mark
# a plausible Live model family. A stale/renamed model ID is the #1 cause of the
# opaque "voice is broken" report (a 1007/1008 that looks like an auth failure),
# so we validate the configured id up front and surface it, rather than letting
# the user discover it mid-call.
_KNOWN_LIVE_MODELS = {LIVE_MODEL, LIVE_MODEL_FALLBACK, LIVE_MODEL_FALLBACK2}
_LIVE_MODEL_MARKERS = ("-live", "live-", "native-audio")


def validate_live_model(model_id: str | None = None) -> dict:
    """Validate a configured Gemini Live model id.

    Returns {"ok": bool, "model": str, "status": "ok"|"retired"|"unknown"|"empty",
    "detail": str}. "retired" means the id is on the known-dead list — it will
    1008 on connect and should be auto-corrected. "unknown" (not a hard error)
    means the id doesn't match a known-good model or the Live-family naming
    pattern — likely a typo or a model that has been renamed/retired. The call
    chain still tries unknowns and falls back (LIVE_MODEL_FALLBACK/2), so that
    case is advisory, surfaced in Settings→Voice.
    """
    model = (model_id if model_id is not None else _get_live_model()) or ""
    model = model.strip()
    if not model:
        return {"ok": False, "model": model, "status": "empty",
                "detail": "No voice model configured; using the built-in default."}
    # Retired check MUST precede the marker heuristic: retired IDs look exactly
    # like live-family names, so the markers would happily vouch for them.
    if model in _RETIRED_LIVE_MODELS:
        return {"ok": False, "model": model, "status": "retired",
                "detail": (f"'{model}' has been retired by Google — connecting to it "
                           "fails with a 1008 close that looks like an auth error but "
                           f"isn't. Known-good: {', '.join(sorted(_KNOWN_LIVE_MODELS))}.")}
    if model in _KNOWN_LIVE_MODELS or any(m in model for m in _LIVE_MODEL_MARKERS):
        return {"ok": True, "model": model, "status": "ok", "detail": ""}
    return {"ok": False, "model": model, "status": "unknown",
            "detail": (f"'{model}' isn't a recognized Gemini Live model — voice "
                       "may fail to connect (this looks like an auth error but "
                       f"isn't). Known-good: {', '.join(sorted(_KNOWN_LIVE_MODELS))}.")}


def _get_live_voice():
    """Return the currently configured Live API voice from settings.

    Resolution order: settings.tts_voice → FRIDAY_LIVE_VOICE env var → "Aoede".
    The Live API binds voice at session-config time, so changes take effect on
    the next WebSocket connection — not mid-stream.
    """
    return (_load_settings() or {}).get("tts_voice") or LIVE_VOICE


def _get_voice_language():
    """Return the configured BCP-47 language code, or '' to use the server default."""
    return ((_load_settings() or {}).get("voice_language") or "").strip()


def _get_voice_style_prompt():
    """Return the user's custom speaking-style instruction, or '' for built-in styles."""
    return ((_load_settings() or {}).get("voice_style_prompt") or "").strip()


def _model_supports_affective_dialog(model_name: str) -> bool:
    """Affective dialog is only available on Gemini 2.5 Flash Live models.

    Supported: gemini-2.5-flash-native-audio-preview, gemini-live-2.5-flash-preview,
    and any model with 'native-audio' in the name. Standard Live models
    (e.g. gemini-3.1-flash-live-preview, gemini-2.0-flash-live-001) return
    1011 if enable_affective_dialog is sent.
    """
    mn = (model_name or "").lower()
    if _is_gemini_38_live(mn):
        # 3.8 Live is not a 2.5 native-audio model and must not be treated as
        # one. Probed: the server ACCEPTS enable_affective_dialog on
        # 3.8 (it does not 1011), so this is not a crash guard — it is a
        # correctness one. The field steers a 2.5-era emotion model that 3.8
        # does not have, and the general rule of this file is that a flag is
        # sent only where it is known to do the thing it names.
        return False
    if "native-audio" in mn:
        return True
    if "2.5-flash" in mn and ("live" in mn or "preview" in mn):
        return True
    return False


# ── Gemini 3.8 Live family (released 2026-09-15) ────────────────────────────
# Three config facts about this family, each established by a real connect
# rather than by reading the model card, and each one of which
# turns a working voice session into a 1007 if it is got wrong:
#
#   1. `proactivity` is GONE from the setup message. Not "defaults to true",
#      not "false is rejected" — the field itself no longer exists. Both
#      proactive_audio=True and =False fail with
#      1007 'Invalid JSON payload received. Unknown name "proactivity" at
#      'setup': Cannot find field.' Proactive audio is on permanently; there
#      is nothing to send.
#   2. gemini-3.8-live REJECTS thinking_config ("Thinking level is not
#      supported for this model") while gemini-3.8-live-extended-thinking
#      REQUIRES it ("Thinking level must be specified for this model") and
#      rejects MINIMAL specifically. Exactly one of the pair takes the field,
#      and the other one hard-fails on it.
#   3. Extended-thinking rejects BLOCKING function calls outright
#      ("BLOCKING function calls are not supported for this model"); plain
#      3.8-live accepts either. So NON_BLOCKING is not a preference on
#      extended-thinking, it is the only mode that connects with tools.
def _is_gemini_38_live(model_name: str) -> bool:
    """Is this a Gemini 3.8-generation Live model?

    Matched on the '3.8' + 'live' pair rather than an exact id list so the
    next 3.8 live variant Google ships inherits the config rules above
    instead of silently getting 2.5-era ones. Deliberately narrow: it does
    NOT match gemini-3.8-flash (a text model, never used here).
    """
    mn = (model_name or "").lower()
    return "3.8" in mn and "live" in mn


def _model_supports_proactivity_config(model_name: str) -> bool:
    """May `proactivity` be sent in this model's setup message at all?

    False for the 3.8 family (see fact 1 above). Sending it there is a hard
    connect failure, which the voice bridge surfaces to the user as a dead
    socket, so this is checked before the field is ever attached.
    """
    return not _is_gemini_38_live(model_name)


def _model_requires_thinking_config(model_name: str) -> bool:
    """Does a bare connect fail without thinking_config? (fact 2)"""
    return "extended-thinking" in (model_name or "").lower()


def _model_rejects_thinking_config(model_name: str) -> bool:
    """Does this model 1007 if thinking_config IS sent? (fact 2)"""
    mn = (model_name or "").lower()
    return _is_gemini_38_live(mn) and "extended-thinking" not in mn


#: Levels gemini-3.8-live-extended-thinking accepts. MINIMAL is rejected by
#: name ("Thinking level MINIMAL is not supported for this model"), so it is
#: absent here rather than merely undocumented.
LIVE_THINKING_LEVELS = ("LOW", "MEDIUM", "HIGH")
DEFAULT_LIVE_THINKING_LEVEL = "LOW"


def resolve_live_thinking_level(requested=None) -> str:
    """Normalise a user-supplied thinking level to one the model accepts.

    Anything unrecognised — including MINIMAL, which reads valid and is not —
    becomes the default rather than being forwarded to fail the connect.
    """
    lvl = str(requested or "").strip().upper()
    return lvl if lvl in LIVE_THINKING_LEVELS else DEFAULT_LIVE_THINKING_LEVEL


def _model_requires_non_blocking_tools(model_name: str) -> bool:
    """Must function declarations be NON_BLOCKING for this model? (fact 3)"""
    return "extended-thinking" in (model_name or "").lower()

LIVE_SYSTEM_TEMPLATE = """You are Agent Friday, a sovereign personal AI assistant.
You are having a live voice conversation — natural spoken dialogue, not text chat.
Match your response length to what the user asks for: brief for quick questions, thorough and
comprehensive when they ask you to explain or go into detail. The user controls the length, not a
blanket rule. Deliver longer answers in short, clear sentences with natural pauses so they can
follow and interrupt. If they don't hear you the first time, repeat it simpler.

You can see through the user's phone camera. If you notice something interesting or relevant, mention it naturally.
Don't narrate what's on screen unless asked — only speak up when it matters.

Personality: knowledgeable, direct collaborator. No sycophancy. Independent thinker. Clear communication.
Trust the user's judgment; push back when you genuinely disagree, but don't lecture.

=== DAILY CONTEXT ===
{context_summary}
=== END CONTEXT ===
"""


def _strip_html(raw: str) -> str:
    raw = re.sub(r'<script\b[^>]*>.*?</script>', ' ', raw, flags=re.S | re.I)
    raw = re.sub(r'<style\b[^>]*>.*?</style>', ' ', raw, flags=re.S | re.I)
    raw = re.sub(r'<[^>]+>', ' ', raw)
    raw = re.sub(r'&nbsp;', ' ', raw)
    raw = re.sub(r'&amp;', '&', raw)
    raw = re.sub(r'&lt;', '<', raw)
    raw = re.sub(r'&gt;', '>', raw)
    raw = re.sub(r'\s+', ' ', raw)
    return raw.strip()


# In-process TTL cache for _load_live_context(). It is called on EVERY chat
# turn and every voice turn to build the `== TODAY'S CONTEXT ==` block, and it
# does five file reads, a sorted directory listing and two JSON parses each
# time — for content whose inputs (the latest briefing, the career tracker, the
# trust graph, personality) change on the order of hours, not turns. Same
# reasoning as the 45s cache on the Gmail merge in services/message_triage.py:
# short enough to still feel live, long enough that a burst of turns pays for
# it once. Monotonic clock so a system time change cannot freeze it.
_LIVE_CTX_TTL_S = 60.0
_live_ctx_lock = threading.Lock()
_live_ctx_cache = {"text": None, "at": 0.0}


def invalidate_live_context() -> None:
    """Drop the cached TODAY'S CONTEXT block — call after writing a briefing."""
    with _live_ctx_lock:
        _live_ctx_cache["at"] = 0.0


def _load_live_context() -> str:
    """Build a concise context summary string for the Friday Live system prompt.

    Cached for _LIVE_CTX_TTL_S. The empty string is cached like any other
    result: "we looked and there is nothing to say today" is an answer, and
    treating it as a miss would make the cheapest case the most expensive one.
    """
    now = _time.monotonic()
    with _live_ctx_lock:
        if (_live_ctx_cache["text"] is not None
                and (now - _live_ctx_cache["at"]) < _LIVE_CTX_TTL_S):
            return _live_ctx_cache["text"]
    text = _build_live_context()
    with _live_ctx_lock:
        _live_ctx_cache["text"] = text
        _live_ctx_cache["at"] = _time.monotonic()
    return text


def _build_live_context() -> str:
    """Uncached body of _load_live_context()."""
    parts = [f"TODAY: {date.today().isoformat()}"]

    # Latest briefing (plain-text excerpt)
    try:
        briefings_dir = HOME / ".friday" / "wiki" / "briefings"
        if briefings_dir.exists():
            candidates = sorted(
                (p for p in briefings_dir.iterdir() if p.suffix in ('.html', '.md')),
                reverse=True,
            )
            if candidates:
                latest = candidates[0]
                raw = latest.read_text(encoding='utf-8', errors='ignore')
                text = _strip_html(raw) if latest.suffix == '.html' else raw
                parts.append(f"LATEST BRIEFING ({latest.name}):\n{text[:1800]}")
    except Exception as e:
        parts.append(f"(briefing load failed: {e})")

    # Career pipeline
    try:
        tracker_candidates = [WIKI_PROFESSIONAL_DIR / 'application-log.md', CAREER_OPS_DIR / 'applications.md']
        tracker = next((p for p in tracker_candidates if p.exists()), None)
        if tracker:
            raw = tracker.read_text(encoding='utf-8', errors='ignore')
            parts.append(f"CAREER PIPELINE (top):\n{raw[:1200]}")
    except Exception:
        pass

    # Upcoming countdowns (<=90 days)
    try:
        today_d = date.today()
        events = [
            {"label": "Summer Solstice", "date": "2026-06-21"},
            {"label": "Independence Day", "date": "2026-07-04"},
            {"label": "New Year", "date": "2027-01-01"},
        ]
        cd = []
        for ev in events:
            d = date.fromisoformat(ev['date'])
            delta = (d - today_d).days
            if 0 <= delta <= 90:
                cd.append(f"- {ev['label']}: {delta} days away ({ev['date']})")
        if cd:
            parts.append("UPCOMING:\n" + "\n".join(cd))
    except Exception:
        pass

    # Trust graph — top names
    try:
        tfile = FRIDAY_DIR / "trust_graph.json"
        if tfile.exists():
            data = json.loads(tfile.read_text(encoding='utf-8'))
            people = data.get('people') or {}
            items = []
            for name, info in people.items():
                score = 0
                role = ''
                if isinstance(info, dict):
                    score = info.get('score') or info.get('trust_score') or 0
                    role = info.get('role') or info.get('relation') or info.get('relationship') or ''
                try:
                    score = float(score)
                except Exception:
                    score = 0.0
                items.append((name, score, role))
            items.sort(key=lambda x: x[1], reverse=True)
            top = items[:8]
            if top:
                lines = [f"- {n}" + (f" ({r})" if r else '') for n, _s, r in top]
                parts.append("TRUST CIRCLE (top 8):\n" + "\n".join(lines))
    except Exception:
        pass

    # Personality snapshot
    try:
        pfile = FRIDAY_DIR / "personality.json"
        if pfile.exists():
            data = json.loads(pfile.read_text(encoding='utf-8'))
            parts.append(f"PERSONALITY: {json.dumps(data)[:500]}")
    except Exception:
        pass

    return "\n\n".join(parts)


def _persist_voice_turn(user_text, agent_text, conversation_id=None):
    """Log a completed voice turn to the context log and chat history.

    Voice turns are saved as event types `voice_user` and `voice_agent` so
    they show up in the context-log search alongside text chats, and as
    role=user/friday entries in CHAT_HISTORY with `via:'voice'` so the chat
    panel can render them when the user comes back.
    """
    settings = _load_settings()
    off_record = bool(settings.get('off_record'))
    if not off_record:
        if user_text:
            _log_context("voice_user", {"text": user_text})
        if agent_text:
            _log_context("voice_agent", {"text": agent_text})
    now_iso = datetime.now().isoformat()
    # Voice turns used to land in the global CHAT_HISTORY list with NO
    # conversation key at all, while text chat writes to a real conversation and
    # only mirrors here. GET /api/chat/history returns this flat list unfiltered,
    # so a voice session and an unrelated text thread rendered interleaved in one
    # window. Address voice to a real conversation and stamp the mirror rows so
    # history can be filtered per thread.
    #
    # `conversation_id` is the thread OPEN on screen, carried from the client on
    # the voice socket. Speaking is a way of typing into the conversation you are
    # looking at, so a voice turn belongs where the user is. Passing None here --
    # a caller with genuinely no open thread, e.g. the scheduler or a channel --
    # still falls back to Main, but resolve() makes that an explicit fallback for
    # callers that have nothing rather than the destination for everyone. The old
    # unconditional resolve(None) is why talking while a non-Main thread was open
    # filed the exchange out of sight.
    try:
        from agent_friday.services import conversations as _conv
        _cid = _conv.resolve(conversation_id)
    except Exception:
        _conv, _cid = None, None
    user_msg = friday_msg = None
    if user_text:
        user_msg = {
            'id': str(uuid.uuid4()),
            'timestamp': now_iso,
            'role': 'user',
            'text': user_text,
            'pinned': False,
            'via': 'voice',
            'conversation_id': _cid,
        }
        CHAT_HISTORY.append(user_msg)
    if agent_text:
        friday_msg = {
            'id': str(uuid.uuid4()),
            'timestamp': now_iso,
            'role': 'friday',
            'text': agent_text,
            'pinned': False,
            'via': 'voice',
            'conversation_id': _cid,
        }
        CHAT_HISTORY.append(friday_msg)
    if _conv is not None and _cid:
        for _m in (user_msg, friday_msg):
            if not _m:
                continue
            try:
                _conv.append(_cid, {"id": _m['id'], "role": _m['role'],
                                    "text": _m['text'], "pinned": False,
                                    "meta": {"kind": "turn", "via": "voice"}})
            except Exception as _ce:
                print(f'  [voice] could not persist turn to {_cid}: {_ce}')
        try:
            _conv.prune(_cid)
        except Exception:
            pass
    try:
        cutoff = (datetime.now() - timedelta(days=30)).isoformat()
        CHAT_HISTORY[:] = [m for m in CHAT_HISTORY if m.get('pinned') or m.get('timestamp', '') >= cutoff][-500:]
        _save_chat_history(CHAT_HISTORY)
    except Exception as e:
        print(f'  [voice] chat history save failed: {e}')

    # Persistent conversation memory + emotional arc — index the voice exchange
    # into ChromaDB (the same store as text chat) so future sessions can recall
    # it, and fold the user's transcript into the cross-session emotional arc.
    # Skip when off-record; best-effort in a daemon thread so it never blocks
    # the voice turn.
    if not off_record and (user_text or agent_text):
        try:
            threading.Thread(
                target=_index_chat_turn,
                args=(user_text, agent_text, _current_session_id()),
                daemon=True,
            ).start()
        except Exception as _ve:
            print(f'  [voice] memory indexing skipped: {_ve}')


def _spawn_voice_distill(turn_log):
    """Ask Claude to review a voice session and propose any wiki updates.

    Fire-and-forget — runs as a background task so the WS handler can return
    immediately. Claude has access to the `propose_wiki_update` tool, so any
    new fact it spots will land in the pending-approvals queue rather than
    being applied immediately.
    """
    if not turn_log:
        return
    convo = []
    for u, a in turn_log:
        if u:
            convo.append(f"User (voice): {u}")
        if a:
            convo.append(f"Friday (voice): {a}")
    transcript = "\n".join(convo)[:8000]
    prompt = (
        "Review the following voice conversation between the user and Friday. "
        "If the user mentioned anything new and durable about themselves, their work, "
        "their family, their projects, or their preferences — something worth remembering "
        "across sessions — call `propose_wiki_update` to queue it for their approval. "
        "Pick a sensible file under ~/wiki/ (e.g. identity/core-profile.md, "
        "professional/job-search.md, family/notes.md). If nothing new came up, "
        "reply with a one-line note and do nothing.\n\n"
        "=== TRANSCRIPT ===\n" + transcript
    )
    _spawn_task(
        name='Voice session: distill to wiki',
        prompt=prompt,
        description='Looking for anything wiki-worthy in the voice session…',
    )


