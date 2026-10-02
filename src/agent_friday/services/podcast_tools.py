"""Podcast tools for chat and voice: make one, list them, play and steer
playback, and answer "what's the source for that?".

Registered into the chat registry by `register()` (the media_tools pattern) and
exposed to voice through `voice_engine._VOICE_SHARED_TOOLS`, so the same four
tools work typed, in chat and out loud.

A voice session may be a cloud model. Everything these tools return about a
PRIVATE episode is therefore passed through `_private_summary`, the single
place a private episode's content becomes something a cloud session may hear:
the local model's short summary, with personal details removed. Titles of
private episodes and names of private sources are PII-scrubbed the same way.
"""

from __future__ import annotations

import json
import threading
import time

from agent_friday.services import podcast_engine as pe


# ── the private-summary seam ────────────────────────────────────────────────

NEUTRAL = "A private episode made from your own material."


def _scrub(text: str) -> str:
    """Private text as a cloud session may see it: the voice handoff's own
    scrub and floor (local_context.prepare). People become relationships,
    identifiers become numbered placeholders, and anything on the never-send
    floor withholds the text entirely."""
    if not (text or "").strip():
        return ""
    try:
        from agent_friday.services import local_context
        return local_context.prepare(text).get("text") or "[withheld]"
    except Exception:
        return "[withheld]"


def _private_summary(text: str, *, sentences: int = 2) -> str:
    """A short, PII-free summary of private text, written by the LOCAL model.

    The local model marks every person the way the voice handoff asks
    ({{person: Name | relationship}}), and the result goes through the same
    scrub and floor (`_scrub`). Never calls a cloud model. With no local model,
    or when the floor refuses, returns a neutral line and none of the text.
    """
    text = (text or "").strip()
    if not text:
        return ""
    try:
        out, _model = pe._llm_json(
            "You summarise the owner's private material for a listener who must "
            "not hear personal details. Every time you mention a person, write "
            "them as {{person: Name | how they relate to the owner}}, for example "
            "{{person: Sam | their brother}}. Leave out contact details, "
            "addresses, account numbers and health details. Return JSON only.",
            "Summarise in at most %d short sentences what this is about, in "
            "general terms.\n\n%s\n\nReturn {\"summary\": \"...\"}."
            % (sentences, text[:12000]), max_tokens=400)
        summary = str(out.get("summary") or "").strip()
    except Exception:
        summary = ""
    safe = _scrub(summary) if summary else ""
    return safe if safe and safe != "[withheld]" else NEUTRAL


def _safe_title(ep: dict) -> str:
    t = ep.get("title") or ep.get("show") or "Episode"
    return _scrub(t) if ep.get("privacy") == "private" else t


def _brief(ep: dict) -> dict:
    s = pe.summary(ep)
    s["title"] = _safe_title(ep)
    if ep.get("privacy") == "private":
        s["about"] = ep.get("about_public") or NEUTRAL
    else:
        s["about"] = ep.get("about") or ""
    s["chapters"] = [{"title": (_scrub(c["title"]) if ep.get("privacy") == "private" else c["title"]),
                      "start": c.get("start")} for c in s.get("chapters") or []]
    return s


# ── now playing (reported by the desktop player) ────────────────────────────

_NOW = {"episode_id": "", "t": 0.0, "playing": False, "at": 0.0}
_NOW_LOCK = threading.Lock()


def set_now_playing(episode_id: str, t: float, playing: bool) -> None:
    with _NOW_LOCK:
        _NOW.update(episode_id=str(episode_id or ""), t=float(t or 0.0),
                    playing=bool(playing), at=time.time())


def now_playing() -> dict:
    with _NOW_LOCK:
        d = dict(_NOW)
    if d["playing"] and d["at"]:
        d["t"] += time.time() - d["at"]
    return d


def line_at(ep: dict, t: float) -> tuple[int, dict | None]:
    """The line being spoken at `t` seconds (or the last one before it)."""
    best, idx = None, -1
    for i, ln in enumerate(ep.get("lines") or []):
        if ln.get("start") is not None and ln["start"] <= t + 0.05:
            best, idx = ln, i
        else:
            break
    return idx, best


# ── tools ───────────────────────────────────────────────────────────────────

def _latest_ready(routine: str = "") -> dict | None:
    """The newest READY episode, by when it was finished, across every show;
    "news" is any News routine's; a named show narrows it to that show."""
    eps = [e for e in pe.list_episodes(limit=500) if e.get("status") == "ready"]
    routine = (routine or "").strip()
    if routine == "news":
        eps = [e for e in eps if (e.get("attached") or {}).get("routine")]
    elif routine and routine != "any":
        eps = [e for e in eps if (e.get("attached") or {}).get("routine") == routine]
    eps.sort(key=lambda e: e.get("finished_at") or e.get("updated_at") or e.get("created_at") or 0, reverse=True)
    return eps[0] if eps else None


def _tool_make_podcast(inp):
    inp = inp or {}
    refs = [r for r in (inp.get("sources") or []) if isinstance(r, dict)]
    topic = str(inp.get("topic") or "").strip()
    if topic and not refs:
        from agent_friday.services import podcast_sources
        refs = podcast_sources.find_topic(topic)
        if not refs:
            return json.dumps({"status": "not_found",
                               "message": "I couldn't find wiki pages about that. "
                                          "Name a file, page or dataset and I'll use it."})
    if not refs:
        return json.dumps({"status": "error",
                           "message": "Give me at least one source: a file, wiki page, "
                                      "knowledge entry, conversation, creation, dataset, "
                                      "web page or some text."})
    # "This conversation": pinned now, while the chat or voice call that asked
    # is the current one. The episode is rendered later, on another thread.
    for r in refs:
        if r.get("kind") == "conversation" and not r.get("id"):
            try:
                from agent_friday.services.agent import _CURRENT_CONVERSATION
                r["id"] = _CURRENT_CONVERSATION.get() or ""
            except Exception:
                r["id"] = ""
            if not r["id"]:
                return json.dumps({"status": "error",
                                   "message": "I couldn't tell which conversation you mean. "
                                              "Ask from inside the conversation, or name it."})
    try:
        ep = pe.create(refs, title=str(inp.get("title") or ""),
                       length=str(inp.get("length") or "standard"),
                       mode=str(inp.get("mode") or "auto"),
                       instructions=str(inp.get("instructions") or ""),
                       voice_engine=str(inp.get("voice") or "local"),
                       origin="user")
    except pe.PodcastRefused as e:
        return json.dumps({"status": "refused", "message": str(e)})
    mins = {"short": 5, "standard": 10, "long": 30}[ep["length"]]
    return json.dumps({
        "status": "queued", "episode_id": ep["id"], "privacy": ep["privacy"],
        "mode": ep["mode"], "sources": len(refs),
        "message": ("Queued a %s-minute %sepisode from %d source%s. It's written by the "
                    "local model and spoken on this computer; you'll get a notice when "
                    "it's ready.%s"
                    % (mins, "data " if ep["mode"] == "data" else "", len(refs),
                       "" if len(refs) == 1 else "s",
                       " It's private, so it stays on this PC." if ep["privacy"] == "private" else "")),
    })


def _tool_podcast_format(inp):
    """Read or set who is on each show: Friday alone (solo) or two hosts (duo)."""
    inp = inp or {}
    routine = str(inp.get("routine") or "").strip()
    fmt = str(inp.get("format") or "").strip().lower()
    if fmt:
        try:
            pe.set_format(routine if routine != "any" else "", fmt)
        except pe.PodcastRefused as e:
            return json.dumps({"status": "error", "message": str(e)})
    return json.dumps({"status": "ok", "formats": pe.formats(),
                       "message": ("%s is now %s." % (routine or "any", fmt)) if fmt else ""})


def _tool_podcast_list(inp):
    inp = inp or {}
    try:
        limit = max(1, min(25, int(inp.get("limit") or 8)))
    except (TypeError, ValueError):
        limit = 8
    eps = pe.list_episodes(routine=str(inp.get("routine") or ""), limit=limit)
    return json.dumps({"status": "ok", "episodes": [_brief(e) for e in eps]})


PLAY_OPS = ("play", "pause", "resume", "stop", "next_chapter", "previous_chapter", "seek")


def _tool_podcast_play(inp):
    """Play or steer an episode on the desktop player."""
    inp = inp or {}
    op = str(inp.get("action") or "play").strip()
    if op not in PLAY_OPS:
        return "podcast_play error: action must be one of " + ", ".join(PLAY_OPS)
    eid = str(inp.get("episode_id") or "").strip()
    if op == "play" and not eid:
        ep = _latest_ready(str(inp.get("routine") or ""))
        if not ep:
            return json.dumps({"status": "not_found",
                               "message": "There's no finished episode for that yet."})
        eid = ep["id"]
    if not eid:
        eid = now_playing().get("episode_id") or ""
    action = {"type": "podcast", "op": op, "episode_id": eid}
    if op == "seek":
        try:
            action["t"] = max(0.0, float(inp.get("seconds") or 0))
        except (TypeError, ValueError):
            return "podcast_play error: seconds must be a number"
    try:
        from agent_friday.services import desktop_bus
        sent = desktop_bus.send([action], timeout=4.0)
    except Exception as e:
        sent = {"delivered": False, "reason": str(e)}
    if not sent.get("delivered"):
        return json.dumps({"status": "no_desktop",
                           "message": "I couldn't reach the Friday window to play it: %s."
                                      % sent.get("reason", "no page is open")})
    ep = pe.load(eid) if eid else None
    return json.dumps({"status": "ok", "action": op, "episode_id": eid,
                       "title": _safe_title(ep) if ep else ""})


def _tool_podcast_source(inp):
    """The sources behind the line playing now (or at a given second)."""
    inp = inp or {}
    now = now_playing()
    eid = str(inp.get("episode_id") or now.get("episode_id") or "").strip()
    ep = pe.load(eid) if eid else None
    if not ep:
        return json.dumps({"status": "not_found", "message": "Nothing is playing."})
    t = inp.get("seconds")
    try:
        t = float(t) if t is not None else float(now.get("t") or 0.0)
    except (TypeError, ValueError):
        t = 0.0
    idx, line = line_at(ep, t)
    if line is None:
        return json.dumps({"status": "not_found", "message": "The episode hasn't started."})
    # A host's line with no citation of its own leans on the line before it.
    j = idx
    while j > 0 and not (ep["lines"][j].get("cites")):
        j -= 1
    cites = ep["lines"][j].get("cites") or []
    by_id = {s["id"]: s for s in ep.get("sources") or []}
    by_id.update({f["id"]: {"id": f["id"], "title": f["text"], "kind": "fact",
                            "url": "", "private": True, "expr": f.get("expr")}
                  for f in ep.get("facts") or []})
    private = ep.get("privacy") == "private"
    out = []
    for c in cites:
        s = by_id.get(c)
        if not s:
            continue
        if s.get("kind") == "fact":
            out.append({"id": c, "fact": s["title"], "computed_by": s.get("expr") or ""})
        elif s.get("private"):
            out.append({"id": c, "kind": s.get("kind"), "title": _scrub(s.get("title") or "")})
        else:
            out.append({"id": c, "title": s.get("title"), "url": s.get("url") or ""})
    speaker = ep["hosts"][line["speaker"]]["name"]
    return json.dumps({
        "status": "ok", "episode_id": eid, "at_seconds": round(t, 1),
        "line": _scrub(line["text"]) if private else line["text"],
        "speaker": speaker, "sources": out,
        "message": "" if out else "That line is the hosts' own connective talk; it cites nothing.",
    })


TOOLS = [
    {"name": "make_podcast",
     "description": (
         "Make a two-host podcast episode (chapters, transcript, a source per claim) "
         "from any sources: files, wiki pages, graph entries, a conversation, "
         "creations, datasets (CSV/XLSX: data mode, only computed numbers), web "
         "pages or text; or `topic` for the owner's wiki notes. Made locally; "
         "returns at once. Say in one sentence that it started and a notice will "
         "follow; do NOT guess its content."),
     "input_schema": {"type": "object", "properties": {
         "sources": {"type": "array", "description": (
             "Each item: {kind: file|wiki|kg_node|conversation|creation|dataset|text|url|news_run, "
             "plus path (file, dataset, wiki), id (kg_node, conversation), filename (creation), "
             "url, text, or routine+run_id (news_run)}."),
             "items": {"type": "object"}},
         "topic": {"type": "string", "description": "Use the owner's wiki notes on this topic."},
         "length": {"type": "string", "enum": ["short", "standard", "long"],
                    "description": "short ~5 min, standard ~10 min, long ~30 min"},
         "mode": {"type": "string", "enum": ["auto", "conversation", "data"]},
         "title": {"type": "string"},
         "instructions": {"type": "string", "description": "Angle or emphasis the owner asked for."},
         "voice": {"type": "string", "enum": ["local", "cloud"],
                   "description": "local (default). cloud only if the owner asks and has switched cloud voices on."},
     }}},
    {"name": "podcast_format",
     "description": ("Who is on each show: solo (Friday alone) or duo (two hosts). No "
                     "format: read all, with the recommended one. With routine and format: set it."),
     "input_schema": {"type": "object", "properties": {
         "routine": {"type": "string", "enum": ["briefing", "front_page", "editorial", "weekly", "any"]},
         "format": {"type": "string", "enum": ["solo", "duo"]}}}},
    {"name": "podcast_list",
     "description": "List podcast episodes, newest first: title, status, length, chapters, privacy. Filter by routine (front_page, briefing, weekly, editorial). Read them back as sentences, not a table.",
     "input_schema": {"type": "object", "properties": {
         "routine": {"type": "string", "enum": ["front_page", "briefing", "weekly", "editorial"]},
         "limit": {"type": "integer"}}}},
    {"name": "podcast_play",
     "description": (
         "Control a podcast on the owner's screen: play (an episode id, or the latest: the "
         "newest finished episode of any show), pause, resume, stop, next_chapter, "
         "previous_chapter, seek. On no_desktop, ask them to open Friday's window."),
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": list(PLAY_OPS)},
         "episode_id": {"type": "string"},
         "routine": {"type": "string", "enum": ["front_page", "briefing", "weekly", "editorial", "news"],
                     "description": ("Set only when the user names a show (\"the Briefing\"); "
                                     "\"news\" for any News show; leave out for the latest of all.")},
         "seconds": {"type": "number"}}, "required": ["action"]}},
    {"name": "podcast_source",
     "description": (
         "\"What's the source for that?\" during a podcast: the current line and what it "
         "cites (outlet and link, or the computed fact). Name the outlet; if it cites "
         "nothing, say it was the hosts' own talk."),
     "input_schema": {"type": "object", "properties": {
         "episode_id": {"type": "string"},
         "seconds": {"type": "number", "description": "A position other than now."}}}},
]

#: make_podcast writes an episode on this computer (ring 1). Listing and
#: source lookup only read (ring 0). Playing steers the owner's own screen,
#: like navigate_to (ring 1). podcast_format changes the owner's own podcast
#: setting on this computer (ring 1).
RINGS = {"make_podcast": 1, "podcast_list": 0, "podcast_play": 1, "podcast_source": 0,
         "podcast_format": 1}

HANDLERS = {
    "make_podcast": _tool_make_podcast,
    "podcast_list": _tool_podcast_list,
    "podcast_play": _tool_podcast_play,
    "podcast_source": _tool_podcast_source,
    "podcast_format": _tool_podcast_format,
}


def register(claude_tools, handlers, rings):
    known = {t["name"] for t in claude_tools}
    for t in TOOLS:
        if t["name"] not in known:
            claude_tools.append(t)
    handlers.update(HANDLERS)
    rings.update(RINGS)
