"""Tool results as a local seat reads them: the same data, less boilerplate.

A local seat re-reads every tool result on every later round of a turn, inside
a window of a few thousand tokens, at a few hundred tokens a second. Much of a
typical result is formatting rather than data: pretty-printing indentation,
escaped non-ASCII, keys whose value is null or an empty string, trailing
spaces and runs of blank lines.

`compact` removes only that. It never truncates, never drops a value that
carries information (zero, false, an empty list that says "no hits" are all
kept). Text that is not JSON, including text that only looks like it, keeps
every word and its line structure; only trailing spaces and blank-line runs
go. A non-string result is returned unchanged.
"""
from __future__ import annotations

import json
import re

_BLANK_RUN = re.compile(r"\n{3,}")


def _prune(obj):
    """Drop dict entries whose value is None or "", recursively."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            v = _prune(v)
            if v is None or (isinstance(v, str) and v == ""):
                continue
            out[k] = v
        return out
    if isinstance(obj, list):
        return [_prune(v) for v in obj]
    return obj


def compact(result):
    """`result` with formatting boilerplate removed and every datum kept."""
    if not isinstance(result, str) or not result:
        return result
    s = result.strip()
    if s[:1] in ("{", "["):
        try:
            obj = json.loads(s)
        except (ValueError, TypeError):
            pass
        else:
            try:
                return json.dumps(_prune(obj), ensure_ascii=False,
                                  separators=(",", ":"), default=str)
            except (TypeError, ValueError):
                return result
    text = "\n".join(line.rstrip() for line in result.splitlines())
    return _BLANK_RUN.sub("\n\n", text).strip("\n")
