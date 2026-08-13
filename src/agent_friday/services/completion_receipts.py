"""A7 — completion-receipt law (Incident 2, F1; seats-and-transparency).

2026-08-13: gemma4:latest, silently seated by a local_only routing flip,
claimed "I created daily_context_check.md in your Wiki" — the file exists
nowhere (every wiki location and the wiki-pending queue was checked). FR-2
only caught pseudo-tool-call *syntax*; a plain-prose completion claim walked
straight through.

The law: an assistant reply that asserts a COMPLETED side-effecting action
(created / wrote / saved / sent / scheduled / ...) must be backed by a
successful tool receipt from a matching tool in the same turn. No receipt →
the claim is fabrication and is handled exactly like a pseudo-tool-call leak
(strip, corrective retry, honest failure) by validate_toolcall_integrity.

Receipts are the turn's `tool_trace` entries ({name, input, result}). A
receipt EXISTING is not enough — deny/hold/error results are recorded in the
same shape with sentinel-prefixed result strings (see FAILURE_SENTINELS),
generalizing the predicate routes/chat.py already used for navigate actions.
"""
from __future__ import annotations

import re

from agent_friday.services.tool_integrity import _strip_code

# Result-string prefixes that mean the tool did NOT successfully do the thing.
# Sources: agent.py vault-ZT deny (4733/4897), confirmation hold (4101),
# governance deny (4114), sandbox deny (4143), handler exception (4065),
# unknown tool (4043), plus the vault-access denial text itself.
FAILURE_SENTINELS = (
    "[VAULT-ZT DENY]",
    "[VAULT ACCESS DENIED]",
    "[CONFIRMATION REQUIRED]",
    "[GOVERNANCE DENY]",
    "[SANDBOX DENY]",
    "[EGRESS-GATE:",
    "Tool error (",
    "Unknown tool:",
)


def receipt_ok(entry) -> bool:
    """True when a tool_trace entry records a tool call that actually ran and
    was not denied, held, or errored."""
    if not isinstance(entry, dict) or not entry.get("name"):
        return False
    result = entry.get("result")
    if isinstance(result, str):
        stripped = result.lstrip()
        for sentinel in FAILURE_SENTINELS:
            if stripped.startswith(sentinel):
                return False
    return True


# ── The claim registry (registry-tunable per the spec) ──
# Each entry: a compiled pattern for a first-person completed-action claim,
# and the tool-name substrings whose successful receipt satisfies it. The
# patterns demand BOTH a completion verb and evidence of an externalized
# artifact (a file name with an extension, or an artifact noun) so that
# "I've created a draft below" (content delivered inline, no side effect)
# never trips it — see _INLINE_DELIVERY_RE.
_ARTIFACT = (
    r"(?:\S+\.(?:md|txt|json|ya?ml|py|docx?|xlsx?|csv|pdf|html?)\b"
    r"|\b(?:file|page|note|entry|document|doc|skill|record|task|reminder"
    r"|wiki|list|spreadsheet)\b)"
)

_FIRST_PERSON = r"\bI(?:'ve| have| just|'d)?(?: just)?(?: successfully)?\s+"

COMPLETION_CLAIM_REGISTRY = [
    {
        "id": "wrote-artifact",
        "pattern": re.compile(
            _FIRST_PERSON + r"(?:created|wrote|saved|stored|added|updated)"
            r"[^.!?\n]{0,120}?" + _ARTIFACT,
            re.IGNORECASE),
        "tools": ("write", "create", "save", "learn", "wiki", "update",
                  "add", "edit", "note", "task", "reminder"),
    },
    {
        "id": "sent-message",
        "pattern": re.compile(
            _FIRST_PERSON + r"(?:sent|emailed|forwarded|replied to)\b",
            re.IGNORECASE),
        "tools": ("send", "email", "reply", "forward", "message"),
    },
    {
        "id": "scheduled",
        "pattern": re.compile(
            _FIRST_PERSON + r"(?:scheduled|booked|rescheduled)\b"
            r"|" + _FIRST_PERSON + r"added[^.!?\n]{0,120}?"
            r"\b(?:calendar|event|meeting|appointment)\b",
            re.IGNORECASE),
        "tools": ("calendar", "event", "schedule", "book"),
    },
    {
        "id": "deleted",
        "pattern": re.compile(
            _FIRST_PERSON + r"(?:deleted|removed)[^.!?\n]{0,120}?" + _ARTIFACT,
            re.IGNORECASE),
        "tools": ("delete", "remove", "write", "edit"),
    },
]

# A claim whose sentence says the content is right here in the reply is
# delivery, not a side effect: "I've created a draft below", "here's the
# note I wrote for you:".
_INLINE_DELIVERY_RE = re.compile(r"\b(?:below|above|here(?:'s| is| are)?|"
                                 r"inline|in this (?:message|reply))\b",
                                 re.IGNORECASE)


def _claim_satisfied(entry_tools, tool_trace) -> bool:
    for receipt in (tool_trace or []):
        if not receipt_ok(receipt):
            continue
        name = str(receipt.get("name", "")).lower()
        if any(t in name for t in entry_tools):
            return True
    return False


def find_unreceipted_completion_claims(reply: str, tool_trace,
                                       registry=None) -> list[str]:
    """Return the matched claim texts in `reply` (outside code fences) that
    assert a completed side-effecting action with no matching successful
    tool receipt in `tool_trace`. Empty list == reply is honest."""
    if not reply:
        return []
    scanned = _strip_code(reply)
    violations = []
    for entry in (registry or COMPLETION_CLAIM_REGISTRY):
        for m in entry["pattern"].finditer(scanned):
            # The sentence around the match — for the inline-delivery guard.
            start = scanned.rfind("\n", 0, m.start()) + 1
            end_candidates = [i for i in (
                scanned.find(".", m.end()), scanned.find("!", m.end()),
                scanned.find("?", m.end()), scanned.find("\n", m.end()))
                if i != -1]
            end = min(end_candidates) if end_candidates else len(scanned)
            sentence = scanned[start:end]
            if _INLINE_DELIVERY_RE.search(sentence):
                continue
            if not _claim_satisfied(entry["tools"], tool_trace):
                violations.append(m.group(0).strip())
    return violations
