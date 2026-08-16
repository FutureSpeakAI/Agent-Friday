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

# Image artifacts are files too. The registry's _ARTIFACT list carried no image
# extensions, so "saved as friday_local_00005_.png" — a completion claim naming
# a specific file that did exist — matched nothing at all.
_IMAGE_ARTIFACT = r"\S+\.(?:png|jpe?g|webp|gif|svg|mp4|mov|wav|mp3)\b"

# A PROMISE is not a completion, and nothing looked for one until 2026-08-16.
# Both existing detectors ask "did she claim to have DONE something without a
# receipt". Neither asks "did she say she was ABOUT to do something and then end
# the turn without doing it", which is what actually happened, twice:
#
#     "Give me one second to find it. I'll try to pull up the image for you now."
#     "I'll do a quick search of your creations folder... Give me one second."
#
# Both times the turn ended there and no tool ran. From Stephen's side that is
# indistinguishable from a lie, and he had to catch it himself.
_PROMISE_RE = re.compile(
    r"\b(?:"
    r"I'll (?:go(?: ahead and)? )?(?:try to |now )?(?:search|look|check|find|pull"
    r"|open|grab|fetch|scan|dig|run|do|take a look|see)"
    r"|let me (?:go |just |quickly )?(?:search|look|check|find|pull|open|grab"
    r"|fetch|scan|dig|run|see|take a look)"
    r"|give me (?:one |a )?(?:second|moment|sec|minute)"
    r"|I'm (?:going to|about to|now) (?:search|look|check|find|pull|open|grab|run)"
    r"|(?:one|just a) (?:second|moment|sec) while I"
    r"|hang on while I|bear with me while I"
    r")\b", re.IGNORECASE)

# Navigation and diagnostic claims. "I've moved you over to the Studio" and
# "Diagnostics complete — Connectivity verified" both assert an action or a
# result that only a tool could have produced.
_NAVIGATED_RE = re.compile(
    _FIRST_PERSON + r"(?:moved|switched|taken|brought|navigated|flipped)\s+you"
    r"[^.!?\n]{0,40}?\b(?:to|over)\b", re.IGNORECASE)
_DIAGNOSTIC_RE = re.compile(
    r"\b(?:diagnostics?|system check|self[- ]?test|status check)\s+"
    r"(?:complete|done|finished|passed)\b"
    r"|\b(?:verified and responding|confirmed access"
    r"|all systems (?:go|nominal))\b",
    re.IGNORECASE)


def find_unkept_promises(reply: str, tool_trace) -> list[str]:
    """Promises of imminent action in a turn where NO tool ran.

    Deliberately narrow: it fires only when the trace is COMPLETELY empty. A
    turn that called three tools and also said "let me check" is doing exactly
    what it said it would; a turn that promised and called nothing is not.
    """
    if not reply or tool_trace:
        return []
    return [m.group(0).strip()
            for m in _PROMISE_RE.finditer(_strip_code(reply))]


COMPLETION_CLAIM_REGISTRY = [
    {
        "id": "saved-image",
        "pattern": re.compile(
            _FIRST_PERSON + r"(?:created|generated|saved|rendered|made)"
            r"[^.!?\n]{0,120}?" + _IMAGE_ARTIFACT, re.IGNORECASE),
        "tools": ("image", "creat", "generat", "render", "draw"),
    },
    {
        "id": "navigated-user",
        "pattern": _NAVIGATED_RE,
        "tools": ("navigate", "open", "switch", "workspace", "browse"),
    },
    {
        "id": "ran-diagnostics",
        "pattern": _DIAGNOSTIC_RE,
        "tools": ("health", "status", "diagnos", "check", "calendar", "email",
                  "wiki", "list"),
    },
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
