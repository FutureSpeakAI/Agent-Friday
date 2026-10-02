"""Media by voice and chat: "show my drafts", "what needs me", "find the deck
about Covista", "show me September's videos", "play the last podcast about AI
policy", "turn this into a podcast".

Four tools, registered into the agent's catalogue like the podcast tools (this module is media_card_tools; services/media_tools.py is the older save-and-look helper):

    media_show   changes the Media view on the owner's screen (a default view, a
                 status, a kind, a project, a search, a time such as "September"
                 or "this week") and lists what it shows
    media_cards  lists cards (for "what did I make this week") without moving
                 the screen
    media_play   finds one audio or video card and plays it on the owner's screen,
                 from where the searched words were said when there is a transcript
    media_turn   makes a new card from one ("turn the ferry story into a podcast")

The results are one line per item (the local seat's rule): the search runs in
the local full-text index, and nothing leaves this PC.

Publishing is not a tool of its own: "publish this" goes through the same
publish action as the button, which raises the one approval card, and the
card is read to the owner. The tools write only what Media itself writes.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from agent_friday.services import media_index as mi

VIEW_WORDS = {
    "today": "today", "progress": "progress", "in progress": "progress", "working": "progress",
    "needs me": "review", "needs you": "review", "review": "review", "in review": "review", "held": "review",
    "published": "published", "everything": "all", "all": "all",
}
KIND_WORDS = {
    "draft": "draft", "drafts": "draft", "article": "article", "articles": "article", "post": "post", "posts": "post",
    "episode": "episode", "episodes": "episode", "podcast": "episode", "podcasts": "episode", "image": "imageset",
    "images": "imageset", "picture": "imageset", "video": "video", "videos": "video", "audio": "audio", "music": "audio",
    "deck": "deck", "decks": "deck", "slides": "deck", "document": "deck", "documents": "deck", "chart": "chart",
    "charts": "chart", "page": "page", "pages": "page", "code": "code", "codebase": "code", "codebases": "code",
}


MONTHS = {m: i + 1 for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august",
                                            "september", "october", "november", "december"))}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})


def period(words: str, now: Optional[float] = None) -> Tuple[Optional[float], Optional[float]]:
    """A time in the owner's words as a [since, until) window of timestamps:
    today, yesterday, this week, last week, this month, last month, a month's
    name (the most recent one that has begun), or YYYY-MM. (None, None) when
    the words name no time."""
    import calendar
    import datetime as dt
    w = (words or "").strip().lower().replace("'s", "")
    if not w:
        return None, None
    t = dt.datetime.fromtimestamp(now) if now else dt.datetime.now()
    day = dt.datetime(t.year, t.month, t.day)

    def month_window(y: int, m: int):
        start = dt.datetime(y, m, 1)
        end = dt.datetime(y + (m == 12), 1 if m == 12 else m + 1, 1)
        return start.timestamp(), end.timestamp()
    if w in ("today",):
        return day.timestamp(), (day + dt.timedelta(days=1)).timestamp()
    if w in ("yesterday",):
        return (day - dt.timedelta(days=1)).timestamp(), day.timestamp()
    if w in ("this week", "week"):
        start = day - dt.timedelta(days=day.weekday())
        return start.timestamp(), (start + dt.timedelta(days=7)).timestamp()
    if w == "last week":
        start = day - dt.timedelta(days=day.weekday() + 7)
        return start.timestamp(), (start + dt.timedelta(days=7)).timestamp()
    if w in ("this month", "month"):
        return month_window(t.year, t.month)
    if w == "last month":
        y, m = (t.year - 1, 12) if t.month == 1 else (t.year, t.month - 1)
        return month_window(y, m)
    if w in ("this year", "year"):
        return dt.datetime(t.year, 1, 1).timestamp(), dt.datetime(t.year + 1, 1, 1).timestamp()
    m = re.match(r"^(\d{4})-(\d{2})$", w)
    if m:
        return month_window(int(m.group(1)), int(m.group(2)))
    parts = w.split()
    name = parts[0] if parts else ""
    if name in MONTHS:
        mo = MONTHS[name]
        year = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else (t.year if mo <= t.month else t.year - 1)
        return month_window(year, mo)
    return None, None


def _window(inp: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    return period(str(inp.get("when") or inp.get("period") or inp.get("month") or ""))


def _sentence(c: Dict[str, Any]) -> str:
    word = mi.KIND_WORD.get(c["kind"], c["kind"]).lower()
    st = mi.STATUS_WORD.get(c["status"], c["status"]).lower()
    held = ", held for you" if c.get("held") else ""
    where = f", at {c['published_at']}" if c.get("published_at") and c["status"] == "published" else ""
    return f"{c['title']}: {word}, {st}{held}{where}."


def _list(cards: List[Dict[str, Any]], limit: int = 8) -> str:
    if not cards:
        return "Nothing there."
    out = [_sentence(c) for c in cards[:limit]]
    if len(cards) > limit:
        out.append(f"And {len(cards) - limit} more.")
    return " ".join(out)


def _tool_media_show(inp: Dict[str, Any]) -> str:
    view = VIEW_WORDS.get(str(inp.get("view") or "").strip().lower())
    kind = KIND_WORDS.get(str(inp.get("kind") or "").strip().lower())
    status = str(inp.get("status") or "").strip().lower() or None
    if status and status not in mi.STATUSES:
        status = None
    project = inp.get("project")
    q = str(inp.get("query") or "").strip()
    target: Dict[str, Any] = {"workspace": "media", "view": "library"}
    if view:
        target["default_view"] = view
    if kind:
        target["kind"] = kind
    if status:
        target["status"] = status
    if project:
        target["project"] = str(project)
    if q:
        target["q"] = q
    if inp.get("board"):
        target["view"] = "board"
    if inp.get("calendar"):
        target["view"] = "calendar"
    since, until = _window(inp)
    res = mi.query(view=view or "all", q=q, kind=kind, project=project, status=status, limit=20, since=since, until=until,
                   sort="newest" if (since or until) else "next")
    shown = "no_desktop"
    try:
        from agent_friday.services import desktop_bus
        desktop_bus.send(dict(target, type="navigate"))
        shown = "shown"
    except Exception:
        shown = "no_desktop"
    return json.dumps({"status": shown, "count": res["total"], "cards": [
        {"id": c["id"], "title": c["title"], "kind": c["kind"], "status": c["status"], "held": c["held"], "published_at": c.get("published_at")} for c in res["cards"][:20]
    ], "say": _list(res["cards"])})


def _tool_media_cards(inp: Dict[str, Any]) -> str:
    view = VIEW_WORDS.get(str(inp.get("view") or "all").strip().lower(), "all")
    kind = KIND_WORDS.get(str(inp.get("kind") or "").strip().lower())
    q = str(inp.get("query") or "").strip()
    since, until = _window(inp)
    res = mi.query(view=view, q=q, kind=kind, limit=int(inp.get("limit") or 10), since=since, until=until,
                   sort="newest" if (since or until) else "next")
    return json.dumps({"count": res["total"], "cards": [
        {"id": c["id"], "title": c["title"], "kind": c["kind"], "status": c["status"], "when": c.get("when"), "published_at": c.get("published_at"), "sources": c.get("sources")} for c in res["cards"]
    ], "say": _list(res["cards"])})


def _find(inp: Dict[str, Any]) -> Dict[str, Any] | None:
    cid = str(inp.get("card") or inp.get("id") or "").strip()
    if cid:
        c = mi.get(cid)
        if c:
            return c
    q = str(inp.get("query") or inp.get("title") or "").strip()
    if not q:
        return None
    res = mi.query(view="all", q=q, limit=5)
    return res["cards"][0] if res["cards"] else None


AV = ("episode", "audio", "music", "video")


def _tool_media_play(inp: Dict[str, Any]) -> str:
    """"Play the last podcast about AI policy": the newest audio or video card
    matching the words, opened in Media's quick look and played; from where
    the words were said when the transcript knows."""
    q = str(inp.get("query") or "").strip()
    kind = KIND_WORDS.get(str(inp.get("kind") or "").strip().lower())
    if kind not in AV:
        kind = None
    since, until = _window(inp)
    c = None
    cid = str(inp.get("card") or "").strip()
    if cid:
        c = mi.get(cid)
    if c is None:
        res = mi.query(view="all", q=q, kind=kind, since=since, until=until, sort="newest", limit=20)
        cands = [x for x in res["cards"] if x["kind"] in AV] or []
        if not cands and q:
            # the words may be in a transcript the search ranked lower, or the kind word was wrong
            cands = [x for x in mi.query(view="all", q=q, sort="newest", limit=50)["cards"] if x["kind"] in AV]
        c = cands[0] if cands else None
    if c is None or c["kind"] not in AV:
        return json.dumps({"status": "not_found", "say": "I could not find anything to play for that."})
    at = None
    if q:
        try:
            from agent_friday.services import media_transcripts as mt
            at = mt.hit_time(c, q)
        except Exception:
            at = None
    target = {"type": "navigate", "workspace": "media", "view": "library", "card": c["id"], "play": True, "q": q}
    if at is not None:
        target["at"] = at
    shown = "no_desktop"
    try:
        from agent_friday.services import desktop_bus
        desktop_bus.send(target)
        shown = "playing"
    except Exception:
        shown = "no_desktop"
    where = f", from {int(at) // 60}:{int(at) % 60:02d} where you said it" if at is not None else ""
    return json.dumps({"status": shown, "card": {"id": c["id"], "title": c["title"], "kind": c["kind"], "duration": c.get("duration")},
                       "say": f"Playing {c['title']}{where}."})


def _tool_media_turn(inp: Dict[str, Any]) -> str:
    c = _find(inp)
    if not c:
        return json.dumps({"status": "not_found", "say": "I could not find that card."})
    into = str(inp.get("into") or "").strip().lower()
    kind = {"podcast": "episode", "episode": "episode", "post": "post", "page": "page", "site": "page", "article": "article",
            "draft": "draft", "slides": "deck", "deck": "deck", "read aloud": "audio", "audio": "audio", "video": "video"}.get(into)
    if not kind:
        return json.dumps({"status": "error", "say": "Say what to turn it into: a podcast, a post, a page, an article, or slides."})
    res = mi.turn_into(c["id"], kind)
    if res.get("status") != "ok":
        return json.dumps({"status": res.get("status"), "say": res.get("message") or "I could not make that."})
    new = res["card"]
    try:
        from agent_friday.services import desktop_bus
        desktop_bus.send({"type": "navigate", "workspace": "media", "card": new["id"]})
    except Exception:
        pass
    return json.dumps({"status": "ok", "card": {"id": new["id"], "title": new["title"], "kind": new["kind"], "status": new["status"]},
                       "say": f"Made a new {mi.KIND_WORD.get(new['kind'], new['kind']).lower()} from \"{c['title']}\": \"{new['title']}\", a draft."})


TOOLS = [
    {"name": "media_show",
     "description": ("Change what the Media workspace shows on the owner's screen and say what is there: a default view "
                     "(today, in progress, needs me, published, everything), a kind (drafts, posts, episodes, images, "
                     "video, decks, charts, pages, code), a project, a status, or a search. 'Show my drafts', 'what needs "
                     "me', 'what did I publish this week'. Read the answer back as sentences."),
     "input_schema": {"type": "object", "properties": {
         "view": {"type": "string", "description": "today | in progress | needs me | published | everything"},
         "kind": {"type": "string"}, "status": {"type": "string", "enum": list(mi.STATUSES)},
         "project": {"type": "string"}, "query": {"type": "string", "description": "Words to search for: in titles, prompts, slides, pages, documents and transcripts."},
         "when": {"type": "string", "description": "A time in the owner's words: today, this week, last month, September, 2026-09."},
         "board": {"type": "boolean", "description": "Show the Pipeline board instead of the Library."},
         "calendar": {"type": "boolean", "description": "Show the Calendar instead of the Library."}}}},
    {"name": "media_cards",
     "description": "List Media cards without moving the screen: what the owner made or is making, with status and where it went.",
     "input_schema": {"type": "object", "properties": {
         "view": {"type": "string"}, "kind": {"type": "string"}, "query": {"type": "string"},
         "when": {"type": "string", "description": "today, this week, last month, September, 2026-09"}, "limit": {"type": "integer"}}}},
    {"name": "media_play",
     "description": ("Play an audio or video card on the owner's screen: 'play the last podcast about AI policy', 'play the "
                     "quay video'. Finds the newest match (titles, prompts and transcripts) and starts it in Media's quick "
                     "look, from where the words were said when there is a transcript."),
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string"}, "kind": {"type": "string", "description": "podcast | audio | video"},
         "when": {"type": "string"}, "card": {"type": "string"}}}},
    {"name": "media_turn",
     "description": ("Turn a Media card into something else: a podcast (data mode for charts and sheets), a post, a page, "
                     "an article, or slides. Makes a new draft card linked to the original and opens it. Name the card by "
                     "its title or id."),
     "input_schema": {"type": "object", "properties": {
         "card": {"type": "string"}, "query": {"type": "string", "description": "The card's title, in the owner's words."},
         "into": {"type": "string", "description": "podcast | post | page | article | slides | read aloud"}},
         "required": ["into"]}},
]
#: Showing and listing read, and steer only the owner's own screen (ring 0/1);
#: turning makes a card on this computer (ring 1). Publishing is not a tool:
#: it is the publish action and its approval card.
RINGS = {"media_show": 1, "media_cards": 0, "media_play": 1, "media_turn": 1}
HANDLERS = {"media_show": _tool_media_show, "media_cards": _tool_media_cards, "media_play": _tool_media_play, "media_turn": _tool_media_turn}


def register(claude_tools, handlers, rings, workspace_tools=None):
    """Handlers and rings are always registered, so the tools run wherever they
    are named. Their schemas join the always-on catalogue only when no
    workspace registry is given; otherwise they are Media's own and travel with
    a turn in Media or on request (the catalogue's latency budget)."""
    target = workspace_tools.setdefault("media", []) if workspace_tools is not None else claude_tools
    known = {t["name"] for t in target}
    for t in TOOLS:
        if t["name"] not in known:
            target.append(t)
    handlers.update(HANDLERS)
    rings.update(RINGS)
