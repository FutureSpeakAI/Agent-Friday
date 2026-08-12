"""FR-3 — provenance: only executed-tool-result URLs are clickable
(toolcall-integrity-v5).

2026-08-12: the "start my day" confabulation minted a fake Google Calendar
URL and the client rendered it as a normal clickable [web:...] link —
indistinguishable from a real one. `fridayCitationize` (ui_parts/app.html,
index.html) linkifies ANY [web:URL] token in a reply with no cross-check
against what the turn's tools actually touched. This module is the
server-side ground-truth check: a [web:URL] citation is only left clickable
if that URL is backed by something an executed tool this turn actually
returned or was given; otherwise it's rewritten to a client-recognized
"unverified" marker that renders inert (see fridayCitationize's
[unverified-web:...] handling).

Deliberately NOT named provenance.py / ~/.friday/provenance — that name and
directory are already used by services/provenance.py, an unrelated C2PA
content-ownership/media-licensing system.
"""
from __future__ import annotations

import logging
import re

WEB_CITATION_RE = re.compile(r"\[web:(https?://[^\]\s]+)\]")
_URL_RE = re.compile(r"https?://[^\s\]\)\"'<>]+")

_provenance_logger = logging.getLogger("friday.provenance")

# Citation forms that assert a sourced fact — used by warn_if_ungrounded_claim
# to detect a reply that cites something despite zero executed tools.
_ASSERTION_CITATION_RE = re.compile(r"\[(?:web|wiki|news):[^\]]+\]")


def _strip_trailing_punct(url: str) -> str:
    return url.rstrip(').,;:!?"\'')


def extract_executed_urls(tool_trace) -> set[str]:
    """URLs an executed tool this turn actually touched or returned — the
    ground truth a [web:...] citation is checked against. Covers both the
    tool's input (open_url/browse_web's target) and its result text (a
    search tool's returned links)."""
    urls = set()
    for entry in (tool_trace or []):
        if not isinstance(entry, dict):
            continue
        tool_input = entry.get('input') or {}
        if isinstance(tool_input, dict):
            u = tool_input.get('url')
            if isinstance(u, str) and u.startswith(('http://', 'https://')):
                urls.add(_strip_trailing_punct(u))
        result = entry.get('result')
        if isinstance(result, str):
            for m in _URL_RE.finditer(result):
                urls.add(_strip_trailing_punct(m.group(0)))
    return urls


def mark_unverified_citations(reply: str, tool_trace) -> tuple[str, list[str]]:
    """Rewrite every [web:URL] citation whose URL wasn't touched by an
    executed tool this turn into [unverified-web:URL] — a form the client
    renders as an inert, visually-flagged marker instead of a clickable link.

    Returns (rewritten_reply, list_of_flagged_urls).
    """
    if not reply or '[web:' not in reply:
        return reply, []
    executed = extract_executed_urls(tool_trace)
    flagged = []

    def _check(m):
        url = m.group(1)
        if _strip_trailing_punct(url) in executed:
            return m.group(0)
        flagged.append(url)
        return f"[unverified-web:{url}]"

    rewritten = WEB_CITATION_RE.sub(_check, reply)
    if flagged:
        _provenance_logger.warning(
            "reply cited %d web URL(s) not backed by an executed tool result "
            "this turn — rendered inert: %s", len(flagged), flagged)
    return rewritten, flagged


def warn_if_ungrounded_claim(reply: str, tool_trace) -> bool:
    """Log a warning when a turn cites a sourced fact ([web:]/[wiki:]/[news:])
    while zero tools actually executed. Returns True if it warned.

    This is a precise, low-noise signal (a real citation token, not fuzzy
    keyword matching) for exactly the "asserts calendar/email/search facts
    with zero executed tools" pattern from the 2026-08-12 confabulation.
    """
    if tool_trace:
        return False
    if not reply or not _ASSERTION_CITATION_RE.search(reply):
        return False
    _provenance_logger.warning(
        "turn cited a source with zero executed tools — reply: %.300s", reply)
    return True
