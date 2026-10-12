"""The `meeting` tool: find a recorded meeting, expand the notes pad, offer what to remember, jot a bullet.

One on-demand tool (not declared to the voice contract; voice reaches it through ask_friday or load_tools).
It is INTERNAL and ring 2: everything it does stays on this PC and is reviewable.

  find          list meetings by words from the title ("last" for the newest)
  expand_notes  turn the pad's bullets into notes, by a local model, each line citing a transcript span
  remember      offer the meeting's people, decisions and projects as proposals (nothing is kept until approved)
  jot           add a bullet to the notes pad (the recording in progress, or the named meeting)

Meeting titles and words never go to a cloud model through this tool: when the running loop is not known to
be local, `find` returns counts only, and the other actions return counts too. The recording itself is never
started from here (the owner presses start and confirms on screen).
"""
from __future__ import annotations

import json

ACTIONS = ("find", "expand_notes", "remember", "jot")

TOOLS = [{
    "name": "meeting",
    "description": ("The user's recorded meetings. action=find (query: words from a title, or 'last'), "
                    "expand_notes (turn their notes-pad bullets into notes with transcript citations), "
                    "remember (offer the meeting's people, decisions and projects for their review), "
                    "jot (text: add a bullet to the notes pad of the meeting being recorded, or meeting_id). "
                    "To search what was said use search_library with scope 'meetings'. It never starts a recording."),
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "query": {"type": "string", "description": "find / others: words from the meeting's title, or 'last'"},
        "meeting_id": {"type": "string"},
        "text": {"type": "string", "description": "jot: the bullet"}}, "required": ["action"]},
}]
RINGS = {"meeting": 2}


def _local() -> bool:
    try:
        from agent_friday.services.library import tools as lt
        return bool(lt._loop_is_local())
    except Exception:
        return False


def _manager():
    from agent_friday.services import meeting_capture as mc
    return mc.get_manager()


def _rows(query: str) -> list:
    from agent_friday.services import desktop_targets as dt
    from agent_friday.services import meeting_workspace as mw
    rows = mw.recorded()
    q = str(query or "").strip().lower()
    if not q:
        return rows
    words = [w for w in dt._tokens(q) if w not in dt._FILLER and w not in dt._MEETING_WORDS and w not in dt._LAST_WORDS]
    if not words:
        return rows[:1] if set(dt._tokens(q)) & dt._LAST_WORDS else rows
    return [r for r in rows if dt._score(words, (r.get("title") or "").lower()) >= 0.99]


def _pick(inp) -> tuple:
    """(meeting id, None) or (None, why)."""
    mid = str(inp.get("meeting_id") or "").strip()
    if mid:
        return mid, None
    rows = _rows(inp.get("query") or "last")
    if not rows:
        return None, "No recorded meeting matches that."
    if len(rows) > 1 and str(inp.get("query") or "").strip().lower() not in ("", "last"):
        return None, "%d meetings match; say which one." % len(rows)
    return rows[0]["id"], None


def meeting(inp: dict) -> str:
    from agent_friday.services import meeting_capture as mc
    inp = inp if isinstance(inp, dict) else {}
    action = str(inp.get("action") or "").strip()
    if action not in ACTIONS:
        return "meeting needs action: " + ", ".join(ACTIONS) + "."
    try:
        if action == "find":
            rows = _rows(inp.get("query"))
            if not _local():
                return json.dumps({"matches": len(rows), "note": "Titles stay on this PC. Open the Meetings workspace, "
                                   "or ask with search_library scope 'meetings'."})
            return json.dumps({"matches": len(rows), "meetings": [
                {"id": r["id"], "title": r.get("title"), "started_at": (r.get("started_at") or "")[:16],
                 "notes": r.get("summary_status")} for r in rows[:8]]})
        if action == "jot":
            text = str(inp.get("text") or "").strip()
            mid = str(inp.get("meeting_id") or "").strip()
            if not mid:
                st = _manager().status()
                mid = st.get("id") if st.get("recording") else ""
            if not text or not mid:
                return "jot needs the bullet's text and a meeting being recorded (or a meeting_id)."
            item = _manager().pad_add(mid, text)
            return json.dumps({"added": True, "bullet": item["id"]})
        mid, why = _pick(inp)
        if why:
            return why
        if action == "expand_notes":
            out = _manager().expand_notes(mid)
            return json.dumps({"status": out.get("status"), "lines": len(out.get("lines") or []),
                               "left_out": out.get("left_out", 0), "reason": out.get("reason", "")})
        out = _manager().propose_memory(mid)
        return json.dumps({"proposed": out.get("proposed", 0), "left_out": out.get("left_out", 0),
                           "note": out.get("note", ""), "kept": False,
                           "review": "They wait for the user's yes in the Meetings workspace."})
    except mc.MeetingError as e:
        return str(e.user_message)


HANDLERS = {"meeting": meeting}
NAMES = tuple(RINGS)


def register(claude_tools, handlers, rings):
    known = {t["name"] for t in claude_tools}
    for t in TOOLS:
        if t["name"] not in known:
            claude_tools.append(t)
    handlers.update(HANDLERS)
    rings.update(RINGS)
