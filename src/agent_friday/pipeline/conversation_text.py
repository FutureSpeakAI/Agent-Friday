"""Conversation text is never rewritten by compression.

The text of the conversation (what the person typed, what Friday replied) goes back to the model exactly as written. Headroom's
text compressor strips function words and prefixes a note such as "[519 words compressed to 404 words ... hash=...]"; a model
that reads its own earlier replies in that style copies the style and the note into its next reply, and the person is shown a
reply that reads like a telegram. Only tool output and other bulky content (tool_result blocks, files, retrieved chunks) is
compressed.

`keep_conversation_text(original, compressed)` takes the message list as it was before compression and as the compressor
returned it and gives back the compressed list with every user and assistant text put back as written. A returned list that
does not line up with the original (a message dropped, merged or re-ordered, or a role changed) is not guessed at: the result is
None and the caller uses the original list uncompressed.

Pure: no I/O. Held by tests/unit/test_conversation_text.py (the rule over every small case) and
tests/unit/test_compressor_leaves_conversation_text_alone.py (through the compressor).
"""
from __future__ import annotations

from typing import Any, Optional

#: Roles whose plain text is conversation. A tool_result block inside a user message is tool output, not conversation.
CONVERSATION_ROLES = ("user", "assistant")


def keep_conversation_text(original: list, compressed: Any) -> Optional[list]:
    """`compressed` with every conversation text as in `original`, or None when the two lists do not line up."""
    if not isinstance(compressed, list) or not isinstance(original, list) or len(compressed) != len(original):
        return None
    out = []
    for before, after in zip(original, compressed):
        if not isinstance(before, dict) or not isinstance(after, dict) or before.get("role") != after.get("role"):
            return None
        out.append(_restore(before, after) if before.get("role") in CONVERSATION_ROLES else after)
    return out


def _restore(before: dict, after: dict) -> dict:
    was, now = before.get("content"), after.get("content")
    if isinstance(was, str):
        return dict(after, content=was)
    if isinstance(was, list) and isinstance(now, list) and len(was) == len(now):
        blocks = []
        for old, new in zip(was, now):
            is_text = isinstance(old, dict) and old.get("type") == "text"
            blocks.append(old if is_text and isinstance(new, dict) and new.get("type") == "text" else new)
        return dict(after, content=blocks)
    if isinstance(was, list):
        return dict(after, content=was)            # the blocks no longer line up: the message stays as it was
    return after
