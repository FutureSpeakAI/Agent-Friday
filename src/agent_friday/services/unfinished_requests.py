"""An explicit "note this in my wiki" is never dropped silently.

The owner's request to keep something ("make a note of this in my wiki",
"add this to my wiki", "remember this") is a standing instruction until it is
carried out. When the turn that received it produced no reply, or produced a
reply without a successful wiki or memory write, the next turn's context names
the request so the model finishes it instead of asking for the facts again.

The request stays open until one of these happens, read from the stored
conversation rows:

  * a later reply's row records a successful note write (`meta.note_saved`,
    set from the turn's tool trace by `wrote_note`);
  * the owner says to drop it ("never mind", "forget it", "don't save it").

Detection is deliberately conservative: explicit phrases only. A message that
merely mentions the wiki, or asks "do you remember this?", opens nothing.
"""
from __future__ import annotations

import re

#: Explicit requests to keep something in the wiki or memory.
_REQUEST_PATTERNS = (
    # "make a note of this (in my wiki)", "take note of that"
    re.compile(r"\b(?:make|take)\s+(?:a\s+)?note\s+of\s+(?:this|that|these|it|all\s+(?:this|that))\b", re.I),
    # "add this to my wiki", "save that in the wiki", "put it in my memory"
    re.compile(r"\b(?:add|save|put|write|note|store|record|keep)\s+(?:this|that|these|it|all\s+(?:this|that))"
               r"(?:\s+down)?\s+(?:to|in|into|on)\s+(?:my|the|your)\s+(?:wiki|memory|notes)\b", re.I),
    # "note this in my wiki", "note it down in the wiki"
    re.compile(r"\bnote\s+(?:this|that|it)(?:\s+down)?\s+in\s+(?:my|the|your)\s+wiki\b", re.I),
    # "remember this", "please remember this for me" -- a statement, not a question
    re.compile(r"\bremember\s+(?:this|that)(?:\s+for\s+me)?\s*(?:[.!]|$)", re.I | re.M),
)

#: The owner withdrawing the request.
_DROP_PATTERNS = (
    re.compile(r"\b(?:never\s*mind|nevermind)\b", re.I),
    re.compile(r"\bforget\s+(?:it|that|about\s+it|about\s+that|the\s+(?:note|wiki\s+note))\b", re.I),
    re.compile(r"\b(?:drop|cancel|skip)\s+(?:it|that|the\s+(?:note|wiki\s+note))\b", re.I),
    re.compile(r"\b(?:do\s+not|don'?t)\s+(?:save|note|add|keep|record)\s+(?:it|that|this)\b", re.I),
)

#: Tools whose successful result is a wiki or memory write.
NOTE_WRITE_TOOLS = frozenset({"propose_wiki_update", "correct_wiki"})
_NOTE_WRITE_NAME = re.compile(
    r"(?:^|_)(?:remember|memory_(?:write|save|add|store)|wiki_(?:write|save|add|update)"
    r"|(?:write|save|add|update)_wiki)(?:_|$)", re.I)

#: A tool result that reports a failure or a refusal rather than a write.
_FAILED_RESULT = re.compile(
    r"^\s*(?:\[[A-Z ]+\]|tool error|error\b|[a-z_]+ error\b|not run\b|denied\b|refused\b|blocked\b)",
    re.I)

#: How much of the owner's words the note quotes.
_QUOTE_CHARS = 300
#: At most this many open requests are named in one turn.
_MAX_OPEN = 3


def is_note_request(text) -> bool:
    """True when the text explicitly asks for a wiki or memory note."""
    if not isinstance(text, str) or not text.strip():
        return False
    return any(p.search(text) for p in _REQUEST_PATTERNS)


def is_drop_request(text) -> bool:
    """True when the text explicitly withdraws a pending note request."""
    if not isinstance(text, str) or not text.strip():
        return False
    return any(p.search(text) for p in _DROP_PATTERNS)


def is_note_write_tool(name) -> bool:
    n = str(name or "")
    return n in NOTE_WRITE_TOOLS or bool(_NOTE_WRITE_NAME.search(n))


def wrote_note(tool_trace) -> bool:
    """True when the turn's tool trace holds a successful wiki/memory write."""
    for entry in tool_trace or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name") or entry.get("tool")
        if not is_note_write_tool(name):
            continue
        result = entry.get("result")
        text = result if isinstance(result, str) else str(result or "")
        if text.strip() and not _FAILED_RESULT.search(text):
            return True
    return False


def open_requests(rows, exclude_id=None) -> list[str]:
    """The owner's note requests still waiting, oldest first.

    `rows` are stored conversation messages in order. `exclude_id` is the row
    of the turn now running: its words are already in front of the model.
    """
    pending: list[str] = []
    for m in rows or []:
        if not isinstance(m, dict):
            continue
        if exclude_id is not None and m.get("id") == exclude_id:
            continue
        role = m.get("role")
        meta = m.get("meta") or {}
        if role == "user":
            text = m.get("text") or ""
            if is_drop_request(text) and not is_note_request(text):
                pending = []
            elif is_note_request(text):
                pending.append(text)
        elif role in ("friday", "assistant") and meta.get("note_saved"):
            pending = []
    return pending[-_MAX_OPEN:]


def _quote(text: str) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= _QUOTE_CHARS else t[:_QUOTE_CHARS].rstrip() + "..."


def note_for(pending) -> str:
    """The context note naming each open request, or '' when there is none."""
    if not pending:
        return ""
    lines = ['Unfinished request from the owner: "%s" -- the wiki note was not saved yet.'
             % _quote(t) for t in pending]
    return ("\n\n== UNFINISHED REQUEST ==\n" + "\n".join(lines)
            + "\nSave it now with a wiki or memory tool, or say plainly why it cannot be "
              "saved. Do not ask the owner to repeat facts that are already in this "
              "conversation.\n")


def context_block(conversation_id, exclude_id=None) -> str:
    """The note for this conversation's next turn ('' when nothing is open)."""
    from agent_friday.services import conversations as _conv
    try:
        rows = _conv.messages(conversation_id)
    except Exception:
        return ""
    return note_for(open_requests(rows, exclude_id=exclude_id))
