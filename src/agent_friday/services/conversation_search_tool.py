"""search_past_conversations for TEXT chat (voice has its own handler in voice_engine).

Both read the same full-text index (services/conversation_fts, through
conversation_recall.search): real message snippets with dates, keys withheld,
off-record turns absent. The voice tool name is reused so Laya's router, the
tool groups and the ring table (2) already know it.

What a model may be handed depends on where it runs:

* a LOCAL model gets every match;
* a CLOUD model gets only the matches that same provider already received
  (conversation_provenance ``sent_to``), and a count of the rest, which stay on
  this PC. "Unknown" is not local (as in services/library/tools).
"""
from __future__ import annotations

TOOLS = [
    {"name": "search_past_conversations",
     "description": (
         "Search every earlier conversation (typed and spoken) for what you and the user said: full-text "
         "search, real excerpts with dates, best match first. Use it when they refer to something from before "
         "('what did we decide about...', 'when did I mention...'). since/until: inclusive YYYY-MM-DD."),
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "What to look for, in a few words."},
         "since": {"type": "string", "description": "Only on or after this date, YYYY-MM-DD."},
         "until": {"type": "string", "description": "Only on or before this date, YYYY-MM-DD."}},
         "required": ["query"]}},
]
RINGS = {"search_past_conversations": 2}


def _loop_is_local() -> bool:
    try:
        from agent_friday.services.library.tools import _loop_is_local as _lil
        return bool(_lil())
    except Exception:
        return False


def _provider() -> str:
    try:
        from agent_friday.services import agent
        from agent_friday.services import conversation_provenance as prov
        from agent_friday.services.local_only_guard import provider_name_of
        return prov.normalize_provider(provider_name_of(agent._CURRENT_PROVIDER.get()))
    except Exception:
        return "unknown"


def _format(hits) -> str:
    return "\n".join(f"- {h['date']} ({h['via']}, {'the user' if h['role'] == 'user' else 'you'}"
                     f"{', ' + h['title'] if h.get('title') else ''}): {h['text']}" for h in hits)


def _tool_search_conversations_typed(inp=None) -> str:
    inp = inp if isinstance(inp, dict) else {}
    query = str(inp.get("query") or "").strip()
    if not query:
        return "search_past_conversations needs something to look for."
    from agent_friday.services import conversation_recall as rc
    hits = rc.search(query, since=inp.get("since"), until=inp.get("until"), limit=12)
    if not hits:
        return f'Nothing in earlier conversations matches "{query}".'
    if _loop_is_local():
        return "From earlier conversations (best match first):\n" + _format(hits[:10])
    prov = _provider()
    direct = [h for h in hits if prov in (h.get("sent_to") or [])]
    rest = len(hits) - len(direct)
    parts = []
    if direct:
        parts.append("From earlier conversations this model already saw (best match first):\n"
                     + _format(direct[:8]))
    if rest:
        parts.append(f"{rest} more match(es) are in conversations that stayed on this PC; they are not "
                     "shown to a cloud model. A local model can read them: say so if the user wants that.")
    return "\n\n".join(parts)


HANDLERS = {"search_past_conversations": _tool_search_conversations_typed}


def register(claude_tools, handlers, rings):
    known = {t["name"] for t in claude_tools}
    for t in TOOLS:
        if t["name"] not in known:
            claude_tools.append(t)
    handlers.update(HANDLERS)
    rings.update(RINGS)
