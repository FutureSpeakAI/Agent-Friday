"""Find the code plan in a model's reply to /api/code/plan.

The plan prompt asks for one JSON object with a `files` list. What comes back
is not always that clean:

- A local llama-server seat splits a thinking model's output into reasoning
  and answer, and its parser treats a literal `<tool_call>` as the end of the
  reasoning. A model that thinks about code which itself handles tool calls
  (a file that contains that tag) therefore hands back the rest of its
  thinking, the `</think>` marker and only then the answer, all as the reply.
- Models wrap the object in fences or add prose around it.

`extract_plan` returns the plan object, or None when the reply holds no
usable plan. It never guesses a shape: a reply without a JSON object that
carries a `files` list is not a plan.

Rules:
- A reply that is exactly one JSON object (fences allowed) is taken as it is.
- Otherwise the reply is scanned for top-level JSON objects with a `files`
  list, and the last one wins, because the answer follows the thinking.
- An object followed later in the reply by `</think>` was written while the
  model was still thinking (a draft), so it is never taken. A `</think>` inside
  the object, in a file's contents, does not count.
"""
from __future__ import annotations

import json
import re
from typing import Optional

#: The marker a thinking model writes when its reasoning ends.
THINK_END = "</think>"

#: Upper bounds on the scan, so a huge or hostile reply cannot stall a request.
#: A 16K-token answer is well under 100,000 characters.
_MAX_REPLY_CHARS = 400_000
_MAX_STARTS = 100_000

_FENCE_OPEN = re.compile(r"^```[a-zA-Z]*\n")
_FENCE_CLOSE = re.compile(r"\n```\s*$")


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = _FENCE_OPEN.sub("", t)
        t = _FENCE_CLOSE.sub("", t)
    return t


def _is_plan(obj) -> bool:
    return isinstance(obj, dict) and isinstance(obj.get("files"), list)


def _last_plan(text: str) -> Optional[dict]:
    """The last top-level JSON object in `text` that has a `files` list and is
    not followed by an end-of-thinking marker.

    Scans forward. A `{` that starts a complete object is skipped past as a
    whole, so braces inside it (in file contents, or in the plan's own file
    entries) are never visited and an inner object never stands in for the
    one around it. A `{` that does not start an object usually fails within a
    character or two, which keeps the scan close to linear.
    """
    decoder = json.JSONDecoder()
    found = None
    pos = 0
    starts = 0
    while starts < _MAX_STARTS:
        i = text.find("{", pos)
        if i < 0:
            break
        starts += 1
        try:
            obj, end = decoder.raw_decode(text, i)
        except (ValueError, RecursionError):
            pos = i + 1
            continue
        pos = end
        if _is_plan(obj) and text.find(THINK_END, end) < 0:
            found = obj
    return found


def extract_plan(raw) -> Optional[dict]:
    """The plan object in a model reply, or None. Pure."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw if len(raw) <= _MAX_REPLY_CHARS else raw[-_MAX_REPLY_CHARS:]
    whole = _strip_fences(text)
    try:
        obj = json.loads(whole)
    except (ValueError, RecursionError):
        obj = None
    if isinstance(obj, dict) and "files" in obj:
        return obj
    return _last_plan(text)
