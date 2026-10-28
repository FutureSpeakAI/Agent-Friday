"""Say that again (north star NS-33.3-2: voice supports repetition).

"Say that again", "repeat that", "what did you say?", "pardon?": Friday says
her last reply again, word for word, with no model call. The words are the
ones she actually said: after a barge, only what was heard.

Only the whole utterance counts. "What did you say about the budget?" is a
question about the past (laya_router sends it to the conversation search),
not a request to repeat, and is never matched here. Neither are a bare
"repeat it" or "once more": both are also answers ("OK, repeat it" to "shall I
repeat the reminder weekly?").
"""
from __future__ import annotations

import re

_LEAD = r"(?:(?:sorry|pardon|excuse me|huh|what|hmm|um|uh|friday|hey friday|ok|okay)[,.!?\s]+)*"
_PLEASE = r"(?:\s*,?\s*please)?"
_ASK = r"(?:(?:can|could|would|will) you\s+)?(?:please\s+)?"
_FORMS = (
    r"(?:say|repeat) (?:that|it|this|the last (?:thing|bit|part|sentence|line)(?: you said)?) (?:again|once more|one more time)",
    r"(?:say|repeat) again",
    r"repeat (?:that|yourself|what you (?:just )?said|the last (?:thing|bit|part|sentence|line)(?: you (?:just )?said)?)",
    r"what (?:did|was it) you (?:just )?(?:say|said)",
    r"what was that",
    r"come again",
    r"pardon(?: me)?",
    r"(?:i )?(?:didn't|did not) (?:catch|hear|get) (?:that|it|what you said)",
)
_RX = re.compile(r"^\s*" + _LEAD + _ASK + r"(?:" + "|".join(_FORMS) + r")" + _PLEASE + r"\s*[.!?]*\s*$",
                 re.IGNORECASE)

#: What Friday says when asked to repeat before she has said anything.
NOTHING_YET = "I haven't said anything yet."


def is_repeat_request(text: str) -> bool:
    """True when the whole utterance asks Friday to say her last reply again."""
    t = " ".join(str(text or "").replace("’", "'").split())
    return bool(t) and len(t) <= 80 and bool(_RX.match(t))


def repeat_reply(last_spoken: str) -> str:
    """The words to say for a repeat request."""
    last = (last_spoken or "").strip()
    return last or NOTHING_YET
