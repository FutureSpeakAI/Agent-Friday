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

def _scrub(text: str) -> str:
    from agent_friday.core import _PII_TAG_RE, _scrub_pii
    return _PII_TAG_RE.sub("[redacted]", _scrub_pii(text or "")[0])


def _private_summary(text: str, *, sentences: int = 2) -> str:
    """A short, PII-free summary of private text, written by the LOCAL model.

    Never calls a cloud model. If no local model is serving, returns a neutral
    line rather than any of the text.
    """
    text = (text or "").strip()
    if not text:
        return ""
    try:
        out, _model = pe._llm_json(
            "You summarise the owner's private material for a listener who must "
            "not hear personal details. Never include names of people, contact "
            "details, addresses, account numbers, health or family details. "
            "Return JSON only.",
            "Summarise in at most %d short sentences what this is about, in "
            "general terms.\n\n%s\n\nReturn {\"summary\": \"...\"}."
            % (sentences, text[:12000]), max_tokens=400)
        summary = str(out.get("summary") or "").strip()
    except Exception:
        summary = ""
    if not summary:
        return "A private episode made from your own material."
    return _scrub(summary)


def _safe_title(ep: dict) -> str:
    t = ep.get("title") or ep.get("show") or "Episode"
    return _scrub(t) if ep.get("privacy") == "private" else t


def _brief(ep: dict) -> dict:
    s = pe.summary(ep)
    s["title"] = _safe_title(ep)
    if ep.get("privacy") == "private":
        s["about"] = ep.get("about_public") or "A private episode made from your own material."
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
    for ep in pe.list_episodes(routine=routine, limit=50):
        if ep.get("status") == "ready":
            return ep
    return None


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
         "Make a podcast episode (an audio overview: two hosts talking it through, "
         "with chapters, a transcript and a source for every claim) from ANY sources: "
         "files (PDF, Word, text), wiki pages, knowledge-graph entries, a chat "
         "conversation, Studio creations, datasets (CSV/XLSX: data mode computes the "
         "numbers first and the hosts may only say computed numbers), web pages or "
         "pasted text. Or pass `topic` to use the owner's wiki notes on it. Written by "
         "the local model and spoken on this computer; returns at once and the episode "
         "follows in minutes."),
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
    {"name": "podcast_list",
     "description": "List podcast episodes, newest first: title, status, length, chapters, privacy. Filter by routine (front_page, briefing, weekly, editorial).",
     "input_schema": {"type": "object", "properties": {
         "routine": {"type": "string", "enum": ["", "front_page", "briefing", "weekly", "editorial"]},
         "limit": {"type": "integer"}}}},
    {"name": "podcast_play",
     "description": (
         "Play or control a podcast episode on the owner's screen: play (an episode id, or the "
         "latest episode of a routine such as today's briefing), pause, resume, stop, "
         "next_chapter, previous_chapter, or seek to a second."),
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": list(PLAY_OPS)},
         "episode_id": {"type": "string"},
         "routine": {"type": "string", "enum": ["", "front_page", "briefing", "weekly", "editorial"]},
         "seconds": {"type": "number"}}, "required": ["action"]}},
    {"name": "podcast_source",
     "description": (
         "Answer \"what's the source for that?\" while a podcast plays: the line being spoken "
         "now and the sources (outlet and link, or the computed fact) it cites."),
     "input_schema": {"type": "object", "properties": {
         "episode_id": {"type": "string"},
         "seconds": {"type": "number", "description": "A position other than now."}}}},
]

#: make_podcast writes an episode on this computer (ring 1). Listing and
#: source lookup only read (ring 0). Playing steers the owner's own screen,
#: like navigate_to (ring 1).
RINGS = {"make_podcast": 1, "podcast_list": 0, "podcast_play": 1, "podcast_source": 0}

HANDLERS = {
    "make_podcast": _tool_make_podcast,
    "podcast_list": _tool_podcast_list,
    "podcast_play": _tool_podcast_play,
    "podcast_source": _tool_podcast_source,
}


def register(claude_tools, handlers, rings):
    known = {t["name"] for t in claude_tools}
    for t in TOOLS:
        if t["name"] not in known:
            claude_tools.append(t)
    handlers.update(HANDLERS)
    rings.update(RINGS)
