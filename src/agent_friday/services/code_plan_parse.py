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

The scan is services/reply_json's, shared with every other reader of a model's
JSON answer.
"""
from __future__ import annotations

import json
from typing import Optional

from agent_friday.services import reply_json as _rj

#: The marker a thinking model writes when its reasoning ends.
THINK_END = _rj.THINK_END


def _is_plan(obj) -> bool:
    """A JSON object whose `files` is a list of file entries (objects)."""
    if not isinstance(obj, dict):
        return False
    files = obj.get("files")
    return isinstance(files, list) and all(isinstance(f, dict) for f in files)


def extract_plan(raw) -> Optional[dict]:
    """The plan object in a model reply, or None. Pure."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = _rj.bounded(raw)
    try:
        obj = json.loads(_rj.strip_fences(text))
    except (ValueError, RecursionError):
        obj = None
    if isinstance(obj, dict) and "files" in obj:
        return obj
    return _rj.scan_last(text, _is_plan)
