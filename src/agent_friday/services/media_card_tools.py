"""Media by voice and chat: "show my drafts", "what needs me", "turn this into a
podcast", "publish this".

Three tools, registered into the agent's catalogue like the podcast tools (this module is media_card_tools; services/media_tools.py is the older save-and-look helper):

    media_show   changes the Media view on the owner's screen (a default view, a
                 status, a kind, a project, a search) and lists what it shows
    media_cards  lists cards (for "what did I make this week") without moving
                 the screen
    media_turn   makes a new card from one ("turn the ferry story into a podcast")

Publishing is not a tool of its own: "publish this" goes through the same
publish action as the button, which raises the one approval card, and the
card is read to the owner. The tools write only what Media itself writes.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

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
    res = mi.query(view=view or "all", q=q, kind=kind, project=project, status=status, limit=20)
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
    res = mi.query(view=view, q=q, kind=kind, limit=int(inp.get("limit") or 10))
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
         "project": {"type": "string"}, "query": {"type": "string"},
         "board": {"type": "boolean", "description": "Show the Pipeline board instead of the Library."},
         "calendar": {"type": "boolean", "description": "Show the Calendar instead of the Library."}}}},
    {"name": "media_cards",
     "description": "List Media cards without moving the screen: what the owner made or is making, with status and where it went.",
     "input_schema": {"type": "object", "properties": {
         "view": {"type": "string"}, "kind": {"type": "string"}, "query": {"type": "string"}, "limit": {"type": "integer"}}}},
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
RINGS = {"media_show": 1, "media_cards": 0, "media_turn": 1}
HANDLERS = {"media_show": _tool_media_show, "media_cards": _tool_media_cards, "media_turn": _tool_media_turn}


def register(claude_tools, handlers, rings):
    known = {t["name"] for t in claude_tools}
    for t in TOOLS:
        if t["name"] not in known:
            claude_tools.append(t)
    handlers.update(HANDLERS)
    rings.update(RINGS)
