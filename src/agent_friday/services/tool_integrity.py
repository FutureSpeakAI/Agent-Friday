"""Tool-call integrity — shared detection primitive for FR-1 (seat conformance
gate) and FR-2 (response validator).

2026-08-12: root-caused after gemma3:4b (no native Ollama tool-calling
capability) confabulated an entire "start my day" briefing — narrating
bracket-syntax pseudo-tool-calls like [query_calendar] and
[search_email(priority:high)] as prose, inventing a meeting, and minting a
fake Google Calendar URL. The wire-level tool-calling code already sends real
structured `tools=[...]` schemas (no bracket template exists anywhere in this
repo for a model to copy) — the leak is the model's own training-data habit
of roleplaying function calls in prose when it can't (or doesn't) use the
real tool-calling API. This module finds that prose leak wherever it lands.
"""
from __future__ import annotations

import re

# Matches a registry tool name written as prose/pseudo-syntax instead of a
# real structured call: `[tool_name]`, `[tool_name(args)]`, `tool_name(args)`,
# or `tool_name: {...}` — the shapes a model imitates when it has seen
# function-call transcripts in training data but isn't actually calling one.
_LEAK_TEMPLATE = re.compile(
    r'\[?\b({names})\b\s*(?:\([^)]*\)|:\s*\{{)?\]?'
)

_FENCE_RE = re.compile(r'```.*?```', re.DOTALL)
_INLINE_CODE_RE = re.compile(r'`[^`\n]+`')


def _strip_code(text: str) -> str:
    """Remove fenced and inline code spans so real code examples that
    legitimately mention a tool name aren't flagged as fabricated calls."""
    text = _FENCE_RE.sub(' ', text)
    text = _INLINE_CODE_RE.sub(' ', text)
    return text


def build_leak_pattern(tool_names):
    names = sorted({n for n in tool_names if n}, key=len, reverse=True)
    if not names:
        return None
    return re.compile(_LEAK_TEMPLATE.pattern.format(
        names='|'.join(re.escape(n) for n in names)))


def find_pseudo_toolcalls(text: str, tool_names) -> list[str]:
    """Return every registry tool name that appears as prose/pseudo-syntax in
    `text` (outside code fences), instead of as a real structured tool call.

    An empty/None text or empty tool_names always returns [].
    """
    if not text or not tool_names:
        return []
    pattern = build_leak_pattern(tool_names)
    if pattern is None:
        return []
    scanned = _strip_code(text)
    hits = []
    for m in pattern.finditer(scanned):
        hits.append(m.group(0).strip())
    return hits
