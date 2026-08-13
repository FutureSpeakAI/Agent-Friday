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
#
# 2026-08-13 (Incident 2, F4): the syntax parts used to be optional, so a bare
# English word that happened to be a tool name ("I could click a button and
# navigate there") counted as a leak — two live false positives cost ~90s of
# corrective-retry dead air each. A match now REQUIRES call syntax: brackets,
# parens directly after the name (no space — "click (the blue one)" is
# prose), or a `name: {` JSON-ish form. A bare word is never a leak.
_LEAK_TEMPLATE = re.compile(
    r'(?:\[({names})\s*(?:\([^)]*\))?\]'   # [name] / [name(args)]
    r'|\b({names})\([^)]*\)'               # name(args) — paren touches name
    r'|\b({names})\s*:\s*\{{)'             # name: {json...}
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


# ── B5: retry-scope isolation (Incident 2, F5) ──
# The FR-2 corrective note is validator plumbing, not conversation. A retry
# seat that sees the unexplained extra turn tends to apologize for it or tag
# its reply (the live "[user correction]" leak), and that apology used to be
# persisted into visible history and replayed as future-turn context. These
# patterns strip correction-referencing artifacts from the FRONT of a retry
# reply; apologies about the answer's *topic* are untouched.
_RETRY_ARTIFACT_PATTERNS = [
    # Bracketed meta tags: "[user correction]", "[correction]", "[system]" …
    re.compile(r'^\s*\[(?:user[ _-]?correction|correction|corrected'
               r'|system(?:\s+correction)?)\]:?\s*', re.IGNORECASE),
    # A leading sentence that apologizes for / references the correction
    # mechanism ("previous reply", "fabricated tool-call", "the correction").
    # The sentence ends at ., !, ? or a spaced dash — lazily, so the rest of
    # the reply survives.
    re.compile(r'^\s*(?:my\s+)?apolog(?:ies|y|ise|ize)[^.!?\n]*?'
               r'(?:previous|earlier|correction|fabricat|tool[- ]call)'
               r'[^.!?\n]*?(?:[.!?]|\s[—–-]\s)\s*', re.IGNORECASE),
    re.compile(r'^\s*(?:sorry|you(?:\'re| are) right)[^.!?\n]*?'
               r'(?:previous repl|earlier repl|correction|fabricat'
               r'|tool[- ]call)[^.!?\n]*?(?:[.!?]|\s[—–-]\s)\s*',
               re.IGNORECASE),
]


def scrub_retry_artifacts(text: str) -> str:
    """Strip validator-correction artifacts from the front of a retry reply.

    Applied to every corrective-retry output before it can be returned,
    persisted, or spoken — the user must never see evidence of the retry
    plumbing (Incident 2's "[user correction]" leak, F5).
    """
    if not text:
        return text
    prev = None
    while prev != text:
        prev = text
        for pat in _RETRY_ARTIFACT_PATTERNS:
            text = pat.sub('', text, count=1)
    return text.lstrip()
