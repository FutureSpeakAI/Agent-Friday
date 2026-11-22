"""Links in a briefing are enforced by code, not by the prompt.

Before a briefing is stored or shown, every item in its news, email, calendar and events sections must resolve to
a real link taken from the run's own tool output:

  news      the story's URL ([N3] cites a story by its id; services/news_links attaches the link afterwards),
  email     a Gmail link built from the email's REAL message id ([E2]), never from anything the model wrote,
  calendar  the calendar entry's own URL ([C1]), and
  events    the outing's, trip's or festival's own page ([V4]).

An item that cannot is dropped and counted; the briefing ends with "N items dropped: no source". A link or
address the model typed is removed unless it is exactly one the tools returned. This module never builds or guesses
an id or a URL: a missing or malformed id means the item is dropped.

`validate` is pure (text and sources in, text and counts out) so the one writer of briefing files can call it;
tests/unit/test_briefing_links.py holds each rule.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from agent_friday.services import news_links

NEWS, EMAIL, CALENDAR, EVENT = "news", "email", "calendar", "event"
#: Section kinds, as the headings name them, and the source kinds each accepts.
SECTION_NEWS, SECTION_EMAIL, SECTION_CALENDAR, SECTION_EVENTS = "news", "email", "calendar", "events"
ACCEPTS = {SECTION_NEWS: {NEWS}, SECTION_EMAIL: {EMAIL}, SECTION_CALENDAR: {CALENDAR, EVENT},
           SECTION_EVENTS: {CALENDAR, EVENT}}
_HEADING_KIND = (
    (SECTION_EMAIL, re.compile(r"\b(e-?mails?|inbox|unread)\b", re.I)),
    (SECTION_NEWS, re.compile(r"\b(news|headlines?|stories|story)\b", re.I)),
    (SECTION_CALENDAR, re.compile(r"\b(calendar|schedule)\b", re.I)),
    (SECTION_EVENTS, re.compile(r"\b(events?|outings?|trips?|festivals?|burns?|horizon|deadlines?|this week)\b", re.I)),
)
GMAIL_LINK = "https://mail.google.com/mail/u/0/#all/%s"
#: A Gmail message id as the API returns it: lowercase hex. Anything else is not an id.
_MESSAGE_ID = re.compile(r"^[0-9a-f]{10,40}$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_BULLET = re.compile(r"^(\s{0,3})(?:[-*+]|\d{1,3}[.)])\s+")
_CITE = re.compile(r"\[\s*([A-Z]\d{1,3}(?:\s*[,;]\s*[A-Z]\d{1,3})*)\s*\]")
_MD_LINK = re.compile(r"\[([^\]\n]{1,300})\]\((https?://[^)\s]+)\)")
_BARE_URL = re.compile(r"(?<![(\[])\bhttps?://[^\s)\]>\"']+")
LABELS = {EMAIL: "Open email", CALENDAR: "Calendar event", EVENT: "Event page"}


@dataclass(frozen=True)
class Source:
    """One real thing a tool returned: what a briefing item may cite."""
    id: str
    kind: str
    title: str = ""
    url: str = ""
    message_id: str = ""

    def link(self) -> Optional[str]:
        """The link this source supports, or None. Email: only from a real message id; the rest: their own URL."""
        if self.kind == EMAIL:
            return GMAIL_LINK % self.message_id if _MESSAGE_ID.fullmatch(self.message_id or "") else None
        return self.url if news_links.working(self.url) else None


@dataclass
class Validated:
    text: str
    dropped: dict = field(default_factory=dict)

    @property
    def dropped_total(self) -> int:
        return sum(self.dropped.values())


def collect(news=(), email=(), calendar=(), events=()) -> dict:
    """id -> Source, from what the tools returned. An entry with no id is not a source (an id is never made up here)."""
    out = {}
    for kind, rows in ((NEWS, news), (EMAIL, email), (CALENDAR, calendar), (EVENT, events)):
        for row in rows or ():
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
                continue
            out[row["id"]] = Source(id=row["id"], kind=kind, title=str(row.get("title") or row.get("subject") or ""),
                                    url=str(row.get("url") or ""), message_id=str(row.get("message_id") or ""))
    return out


def _section_kind(title: str) -> Optional[str]:
    return next((kind for kind, pattern in _HEADING_KIND if pattern.search(title)), None)


def _strip_typed_links(text: str, known: set) -> str:
    """Keep the words of a link the model typed and drop the address, unless it is exactly a known one."""
    text = _MD_LINK.sub(lambda m: m.group(0) if m.group(2) in known else m.group(1), text)
    return _BARE_URL.sub(lambda m: m.group(0) if m.group(0) in known else "", text)


def _resolve(item: str, kind: str, sources: dict, known: set) -> Optional[str]:
    """The item with its citations turned into links, or None when it has no verified source."""
    text = _strip_typed_links(item, known)
    verified = any(url in text for url in known)
    allowed = ACCEPTS[kind]

    def cite(match):
        nonlocal verified
        links, keep = [], []
        for sid in re.split(r"\s*[,;]\s*", match.group(1)):
            source = sources.get(sid)
            link = source.link() if source and source.kind in allowed else None
            if link is None:
                continue
            verified = True
            if source.kind == NEWS:
                keep.append(sid)                       # services/news_links attaches the publisher link afterwards
            else:
                links.append("[%s](%s)" % (LABELS[source.kind], link))
        if keep and not links:
            return "[%s]" % ", ".join(keep)
        return "(%s)" % ", ".join(links) if links else ""
    out = _CITE.sub(cite, text)
    if not verified:
        return None
    out = re.sub(r"[ \t]+\(", " (", out)
    return re.sub(r" +([.,;:])", r"\1", out)


def _count_line(total: int) -> str:
    return "_%d item%s dropped: no source_" % (total, "" if total == 1 else "s")


def validate(text: str, sources: dict) -> Validated:
    """The briefing with every unsourced item in its news, email, calendar and events sections dropped and counted."""
    known = {link for s in sources.values() if (link := s.link())}
    out, dropped = [], {}
    stack: list = []                                   # (heading level, section kind)
    item: Optional[list] = None                        # the lines of the item being collected
    item_kind: Optional[str] = None

    def close_item():
        nonlocal item
        if item is None:
            return
        resolved = _resolve("\n".join(item), item_kind, sources, known)
        if resolved is None:
            dropped[item_kind] = dropped.get(item_kind, 0) + 1
        else:
            out.extend(resolved.split("\n"))
        item = None

    for line in (text or "").split("\n"):
        heading = _HEADING.match(line)
        bullet = _BULLET.match(line)
        if item is not None and not heading and not bullet and line.startswith((" ", "\t")) and line.strip():
            item.append(line)
            continue
        close_item()
        if heading:
            level = len(heading.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, _section_kind(heading.group(2)) or (stack[-1][1] if stack else None)))
            out.append(line)
        elif bullet and stack and stack[-1][1]:
            item, item_kind = [line], stack[-1][1]
        else:
            out.append(line)
    close_item()
    result = "\n".join(out)
    total = sum(dropped.values())
    if total:
        result = result.rstrip("\n") + "\n\n" + _count_line(total) + "\n"
    return Validated(result, dropped)
