"""Outside text from the owner's phone: what is on its screen, kept apart from the owner's own words.

The phone's assist sheet can send the text on its screen (or text shared to it) with a turn. That text was
written by someone else (a web page, an email, a chat) and may be written to look like an instruction, so it is
data for that turn only:

* ``parse`` checks the phone's ``untrusted`` list (or the desktop's ``outside_context``) and refuses what is
  malformed or too big in plain words. It never cuts text down: a half-sent page is worse than a refusal.
* ``block`` wraps each item with services/untrusted_input.wrap, with where it came from, ready to ride in the
  turn's context beside, never inside, the owner's message.
* ``plain`` is the same text unwrapped, for the taint ledger (a tool argument that came from it is flagged).

The turn that carries it is not the owner's command (its tool calls need a card), and nothing is learned from it
(routes/chat). See tests/unit/test_phone_outside_text.py.
"""
from __future__ import annotations

from typing import Any, List

MAX_ITEMS = 8
MAX_ITEM_CHARS = 6000
MAX_TOTAL_CHARS = 24000
MAX_LABEL_CHARS = 80
MAX_ID_CHARS = 40
KINDS = ("screen", "shared")

WHY = "Text from an app on the owner's phone arrived with this turn; the owner did not write it."
LABEL = "text from an app on the owner's phone"


class Refused(ValueError):
    """The list was refused; ``message`` is safe to show the phone."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def parse(raw: Any) -> List[dict]:
    """The normalised items, or [] for no field. Raises Refused for anything malformed or too big."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise Refused("untrusted_bad", "the screen text was not sent as a list")
    if len(raw) > MAX_ITEMS:
        raise Refused("untrusted_too_big", "too many pieces of screen text (at most %d)" % MAX_ITEMS)
    items, total = [], 0
    for entry in raw:
        if not isinstance(entry, dict):
            raise Refused("untrusted_bad", "a piece of screen text was not an object")
        kind, source, text = entry.get("kind"), entry.get("source"), entry.get("text")
        if kind not in KINDS:
            raise Refused("untrusted_bad", "a piece of screen text has no known kind (screen or shared)")
        if not isinstance(source, str) or not isinstance(text, str) or not text.strip():
            raise Refused("untrusted_bad", "a piece of screen text needs a source and some text")
        if len(text) > MAX_ITEM_CHARS:
            raise Refused("untrusted_too_big", "a piece of screen text is over %d characters" % MAX_ITEM_CHARS)
        total += len(text)
        if total > MAX_TOTAL_CHARS:
            raise Refused("untrusted_too_big", "the screen text is over %d characters in all" % MAX_TOTAL_CHARS)
        ident = entry.get("id")
        items.append({"kind": kind, "source": " ".join(source.split())[:MAX_LABEL_CHARS], "text": text,
                      "id": ident[:MAX_ID_CHARS] if isinstance(ident, str) else ""})
    return items


def from_chat_body(body: Any) -> List[dict]:
    """The items on a desktop chat request (``outside_context``); a missing or bad field is no items, never an
    error: the field can only make a turn more careful."""
    try:
        return parse((body or {}).get("outside_context")) if isinstance(body, dict) else []
    except Refused:
        return []


def block(items: List[dict]) -> str:
    """Each item wrapped as data and named, separated by blank lines."""
    from agent_friday.services.untrusted_input import wrap
    parts = []
    for item in items:
        what = "Screen text" if item["kind"] == "screen" else "Text shared to Friday"
        parts.append(wrap("%s, from %s:\n%s" % (what, item["source"], item["text"])))
    return "\n\n".join(parts)


def plain(items: List[dict]) -> str:
    return "\n\n".join(item["text"] for item in items)
