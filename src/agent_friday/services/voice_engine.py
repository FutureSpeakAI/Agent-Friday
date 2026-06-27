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
    JSON string with summary, implications, and key quotes."""
    inp = inp or {}
    url = (inp.get("url") or "").strip()
    result, _status = _deep_dive_article(url, title=inp.get("title"),
                                         refresh=bool(inp.get("refresh")))
    if result.get("status") != "ok":
        return json.dumps({"error": result.get("message") or "deep dive failed"})
    return json.dumps({
        "title": result.get("title"),
        "url": result.get("url"),
        "summary": result.get("summary"),
        "implications": result.get("implications"),
        "key_quotes": (result.get("key_quotes") or [])[:4],
    }, default=str)


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


def _tool_query_calendar(_inp):
    """Voice tool: today's + tomorrow's calendar as a spoken-ready JSON string.

    Powers global voice commands like 'what's next on my calendar?' from any
    workspace. Returns {connected, count, events:[{title,start,end,location,
    attendees}]} or a note when Google isn't linked."""
    events = _fetch_calendar_today()
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
        cards, source = _collect_messages(limit=limit)
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
     "the query for the top current stories.",
     {"query": ("string", "Keywords across headline/snippet/source. Blank = top stories."),
      "limit": ("integer", "Max stories (1-25, default 8).")}, []),
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
     "confirmed=true after they agree. Workspaces: home, career, wiki, studio, "
     "trust, system, news, draft, code, finance, health, contacts, content, "
     "messages, calendar, family, futurespeak.",
     {"workspace": ("string", "Workspace id or spoken name, e.g. 'news', 'calendar'."),
      "confirmed": ("boolean", "True if the user asked for this workspace or has agreed to the switch.")}, ["workspace"]),
]


def _build_voice_live_tools(types):
    """Render _VOICE_LIVE_TOOLS as a google.genai Tool list for the Live config.

    `types` is google.genai.types (imported inside the live handler). Returns a
    single-element list holding one Tool with all function declarations, or []
    if the SDK shape is unavailable (caller then runs tool-free)."""
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
        decls.append(types.FunctionDeclaration(
            name=name, description=desc,
            parameters=types.Schema(type=types.Type.OBJECT,
                                    properties=schema_props,
                                    required=list(required) or None),
        ))
    return [types.Tool(function_declarations=decls)] if decls else []


def _voice_tool_run(name, args, send_client):
    """Execute one Live tool call, emit any client-side side effect, and return a
    SHORT text/JSON result for the model to speak from. `send_client(obj)` pushes
    a WS frame to the browser (navigate action, citation chip). Never raises."""
    name = (name or "").strip()
    args = dict(args or {})

    def _needs_confirm(_what):
        """The user hasn't agreed yet — tell the model to ask, and do nothing."""
        return (f"NOT DONE YET — you must get the user's spoken permission first. "
                f"Ask a short yes/no question about {_what}, then call this tool "
                f"again with confirmed=true only after they say yes.")

    try:
        if name in ("navigate_workspace", "navigate"):
            if not args.get("confirmed"):
                return _needs_confirm(f"switching to the {args.get('workspace') or 'that'} workspace")
            # Pause speech while the action runs; resume + report after.
            try:
                send_client({"type": "tts_pause"})
            except Exception:
                pass
            res = _tool_navigate(args)
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
            res = _tool_open_url(args)
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
        if name == "search_news":
            res = _tool_search_news(args)
            try:
                hits = (json.loads(res) or {}).get("hits", [])
                chips = [{"title": h.get("title"), "source": h.get("source"),
                          "url": h.get("url")} for h in hits[:6] if h.get("url")]
                if chips:
                    send_client({"type": "cite", "label": "Related stories", "sources": chips})
            except Exception:
                pass
            return res
        if name == "search_web":
            return _tool_search_web(args)
        if name == "search_wiki":
            return _tool_search_wiki(args)
        if name == "query_calendar":
            return _tool_query_calendar(args)
        if name == "check_email":
            return _tool_check_email(args)
        if name == "get_source_trust":
            return _tool_get_source_trust(args)
        if name == "get_article_deep_dive":
            res = _tool_get_article_deep_dive(args)
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
    except Exception as e:
        return f"(tool {name} error: {e})"
    return f"(unknown tool {name})"


# ═══════════════════════════════════════════════════════════════
#  TEXT-TO-SPEECH & AUDIO
# ═══════════════════════════════════════════════════════════════

def _local_tts_available():
    """True if the offline TTS engine (pyttsx3 + a system voice) is importable."""
    try:
        import pyttsx3  # noqa: F401
        return True
    except Exception:
        return False


def _synthesize_tts_wav_local(text):
    """Offline TTS via pyttsx3 (SAPI5 on Windows). Returns a WAV BytesIO or None.

    The fully-local fallback for spoken output when Gemini TTS is unreachable
    (offline, no key, or an API error). No network, no cloud — the audio is
    rendered on-device by the OS speech engine.
    """
    if not text or not str(text).strip():
        return None
    try:
        import pyttsx3
    except Exception:
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

    try:
        _prefer_local = allow_local and ((not core.GEMINI_API_KEY) or _network_is_offline())
    except Exception:
        _prefer_local = allow_local and not core.GEMINI_API_KEY
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
    print(f"  [FRIDAY] WARNING: notifications_engine unavailable: {_e}")


# ═══════════════════════════════════════════════════════════════
#  FRIDAY LIVE — Gemini Live API bridge over WebSocket
# ═══════════════════════════════════════════════════════════════

LIVE_MODEL = os.environ.get("FRIDAY_LIVE_MODEL", "gemini-3.1-flash-live-preview")
# Graceful-degradation chain if the primary (3.1 Flash Live) is unavailable.
# 3.1 Flash Live is now the primary — it's the known-working model on the AI
# Studio AQ. key tier and handles barge-in via server-side VAD natively. It does
# NOT support affective dialog / proactive audio (those are 2.5-only); the
# config builder strips them automatically for non-native-audio models. The 2.5
# models stay as fallbacks for resilience if 3.1 ever fails to connect mid-call.
LIVE_MODEL_FALLBACK = "gemini-live-2.5-flash-preview"
LIVE_MODEL_FALLBACK2 = "gemini-2.5-flash-native-audio-preview-12-2025"
LIVE_VOICE = os.environ.get("FRIDAY_LIVE_VOICE", "Aoede")


def _get_live_model():
    """Return the currently configured voice/live model from settings, falling back to LIVE_MODEL."""
    return _load_settings().get("voice_model") or LIVE_MODEL


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
    if "native-audio" in mn:
        return True
    if "2.5-flash" in mn and ("live" in mn or "preview" in mn):
        return True
    return False

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


def _load_live_context() -> str:
    """Build a concise context summary string for the Friday Live system prompt."""
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


def _persist_voice_turn(user_text, agent_text):
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
    if user_text:
        CHAT_HISTORY.append({
            'id': str(uuid.uuid4()),
            'timestamp': now_iso,
            'role': 'user',
            'text': user_text,
            'pinned': False,
            'via': 'voice',
        })
    if agent_text:
        CHAT_HISTORY.append({
            'id': str(uuid.uuid4()),
            'timestamp': now_iso,
            'role': 'friday',
            'text': agent_text,
            'pinned': False,
            'via': 'voice',
        })
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
        "his family, his projects, or his preferences — something worth remembering "
        "across sessions — call `propose_wiki_update` to queue it for his approval. "
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


