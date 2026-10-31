"""Read the JSON object a model was asked to answer with.

Many features ask a model for "ONLY a JSON object" and then have to find it in
whatever came back. Two things get in the way:

- A local llama-server seat splits a thinking model's output into reasoning
  and answer, and its parser treats a literal `<tool_call>` as the end of the
  reasoning. A model that thinks about anything containing that tag (code that
  handles tool calls, a document quoting one, a skill file) hands back the rest
  of its thinking, the `</think>` marker and only then the answer, all as the
  reply. The thinking often holds draft objects of its own.
- Models wrap the object in fences or put prose, with braces, around it.

Taking the span from the first `{` to the last `}` fails on both. The rules
here:

- A reply that is exactly one JSON object (fences allowed) is taken as it is.
- Otherwise the reply is scanned for top-level JSON objects, and the last one
  the caller accepts wins, because the answer follows the thinking.
- An object followed later in the reply by `</think>` was written while the
  model was still thinking (a draft), so it is never taken. A `</think>` inside
  the object, in one of its strings, does not count.

`last_object` never invents a shape: when nothing acceptable is there it
returns None, and the caller keeps its own fallback.
"""
from __future__ import annotations

import json
import re
from typing import Callable, Iterable, List, Optional, Tuple

#: The marker a thinking model writes when its reasoning ends.
THINK_END = "</think>"
#: The tag that ends the reasoning early on a llama-server seat.
TOOL_CALL = "<tool_call>"

#: Upper bounds on the scan, so a huge or hostile reply cannot stall a request.
#: A 16K-token answer is well under 100,000 characters.
MAX_REPLY_CHARS = 400_000
MAX_STARTS = 20_000

_OBJECT_START = re.compile(r'\{\s*["}]')

_FENCE_OPEN = re.compile(r"^```[a-zA-Z]*\s*\n")
_FENCE_CLOSE = re.compile(r"\n?```\s*$")


def strip_fences(text: str) -> str:
    """`text` without one surrounding Markdown code fence."""
    t = text.strip()
    if t.startswith("```"):
        t = _FENCE_OPEN.sub("", t, count=1)
        t = _FENCE_CLOSE.sub("", t, count=1)
    return t.strip()


def bounded(raw: str) -> str:
    """The part of a reply the readers look at: its last MAX_REPLY_CHARS."""
    return raw if len(raw) <= MAX_REPLY_CHARS else raw[-MAX_REPLY_CHARS:]


def scan_all(text: str, accept: Callable[[dict], bool]) -> List[Tuple[int, dict]]:
    """Every top-level JSON object in `text` that `accept` takes and that is
    not followed by an end-of-thinking marker, as (start, object), in order.

    Scans forward. A `{` that starts a complete object is skipped past as a
    whole, so braces inside it are never visited and an inner object never
    stands in for the one around it. Only a `{` followed by a key or `}` is
    tried, so stray braces in prose and code cost no decode.
    """
    decoder = json.JSONDecoder()
    found: List[Tuple[int, dict]] = []
    pos = 0
    starts = 0
    while starts < MAX_STARTS:
        m = _OBJECT_START.search(text, pos)
        if m is None:
            break
        i = m.start()
        starts += 1
        try:
            obj, end = decoder.raw_decode(text, i)
        except (ValueError, RecursionError):
            pos = i + 1
            continue
        pos = end
        if isinstance(obj, dict) and accept(obj) and text.find(THINK_END, end) < 0:
            found.append((i, obj))
    return found


def scan_last(text: str, accept: Callable[[dict], bool]) -> Optional[dict]:
    """The last object `scan_all` finds, or None."""
    found = scan_all(text, accept)
    return found[-1][1] if found else None


def _acceptor(keys: Optional[Iterable[str]],
              accept: Optional[Callable[[dict], bool]]) -> Callable[[dict], bool]:
    wanted = tuple(keys or ())

    def _ok(obj: dict) -> bool:
        if wanted and not any(k in obj for k in wanted):
            return False
        if accept is not None:
            try:
                return bool(accept(obj))
            except Exception:
                return False
        return True

    return _ok


def last_object(raw, *, keys: Optional[Iterable[str]] = None,
                accept: Optional[Callable[[dict], bool]] = None,
                unique: bool = False) -> Optional[dict]:
    """The JSON object a model answered with, or None. Pure.

    keys:   when given, an object must carry at least one of them.
    accept: when given, an object must also pass this check.
    unique: for readers that must fail closed. Outside a whole-JSON reply,
            None unless exactly one acceptable object is found, and None when
            a `<tool_call>` precedes it with no `</think>` in between (thinking
            cut off before it ended, so the object may be a draft).
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    ok = _acceptor(keys, accept)
    text = bounded(raw)
    try:
        whole = json.loads(strip_fences(text))
    except (ValueError, RecursionError):
        whole = None
    if isinstance(whole, dict) and ok(whole):
        return whole
    if not unique:
        return scan_last(text, ok)
    found = scan_all(text, ok)
    if len(found) != 1:
        return None
    start, obj = found[0]
    tc = text.find(TOOL_CALL)
    if 0 <= tc < start and text.find(THINK_END, tc, start) < 0:
        return None
    return obj
