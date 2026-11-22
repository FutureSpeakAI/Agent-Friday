"""Links in a briefing are enforced by code, not by the prompt.

Before a briefing is stored or shown, every item that names something from the outside world must resolve to a real
link taken from the run's own tool output:

  news      the story's URL ([N3] cites a story by its id; services/news_links attaches the link afterwards),
  email     a Gmail link built from the email's REAL message id ([E2]), never from anything the model wrote,
  calendar  the calendar entry's own URL ([C1]), and
  events    the outing's, trip's or festival's own page ([V4]).

The rules, by what an item says rather than only where it sits:

* an item that cites an id must have EVERY id resolve to a source of a kind its section accepts; one unknown or
  unlinkable id ([C9]) drops the item;
* in a sourced section (a heading that names news, email, calendar, schedule, agenda, meetings or events) an item
  with no citation and no verified link is dropped; items elsewhere (tasks, an insight) are kept unless they cite;
* a heading naming two kinds ("Schedule & Inbox") accepts both;
* a link or address the model typed is removed unless it is exactly one the tools returned (`strip_unknown_links`
  runs again over the whole assembled briefing, so text added after `validate` cannot carry one either).

Every dropped item is counted; the briefing ends with "N items dropped: no source". This module never builds or
guesses an id or a URL. `validate` is pure (text and sources in, text and counts out) so the one writer of briefing
files can call it; tests/unit/test_briefing_links.py holds each rule.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from agent_friday.services import news_links

NEWS, EMAIL, CALENDAR, EVENT = "news", "email", "calendar", "event"
#: Section kinds, as the headings name them, and the source kinds each accepts.
SECTION_NEWS, SECTION_EMAIL, SECTION_CALENDAR, SECTION_EVENTS = "news", "email", "calendar", "events"
ACCEPTS = {SECTION_NEWS: {NEWS}, SECTION_EMAIL: {EMAIL}, SECTION_CALENDAR: {CALENDAR, EVENT},
           SECTION_EVENTS: {CALENDAR, EVENT}}
_HEADING_KIND = (
    (SECTION_EMAIL, re.compile(r"\b(e-?mails?|inbox|unread|mail)\b", re.I)),
    (SECTION_NEWS, re.compile(r"\b(news|headlines?|stories|story)\b", re.I)),
    (SECTION_CALENDAR, re.compile(r"\b(calendar|schedule|agenda|meetings?|appointments?)\b", re.I)),
    (SECTION_EVENTS, re.compile(r"\b(events?|outings?|trips?|festivals?|burns?|horizon)\b", re.I)),
)
#: /u/<address>/ opens the mailbox the message is in when several accounts are signed in (as gmail_send.draft_link);
#: /u/0/ (the first signed-in account) only when the run did not say which account the message came from.
GMAIL_LINK = "https://mail.google.com/mail/u/%s/#all/%s"
_ACCOUNT = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")
#: A Gmail message id as the API returns it: lowercase hex. Anything else is not an id.
_MESSAGE_ID = re.compile(r"^[0-9a-f]{10,40}$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_BULLET = re.compile(r"^(\s{0,3})(?:[-*+]|\d{1,3}[.)])\s+")
_CITE = re.compile(r"\[\s*([A-Z]\d{1,3}(?:\s*[,;]\s*[A-Z]\d{1,3})*)\s*\]")
_MD_LINK = re.compile(r"\[([^\]\n]{1,300})\]\((https?://[^)\s]+)\)")
#: Any address not written as a markdown link's target: in round brackets, angle brackets or bare.
_BARE_URL = re.compile(r"(?<!\]\()\bhttps?://[^\s)\]>\"']+")
_ANGLE_URL = re.compile(r"<(https?://[^\s>]+)>")
LABELS = {EMAIL: "Open email", CALENDAR: "Calendar event", EVENT: "Event page"}


@dataclass(frozen=True)
class Source:
    """One real thing a tool returned: what a briefing item may cite."""
    id: str
    kind: str
    title: str = ""
    url: str = ""
    message_id: str = ""
    account: str = ""

    def link(self) -> Optional[str]:
        """The link this source supports, or None. Email: only from a real message id (in its own account's mailbox
        when the account is known); the rest: their own URL."""
        if self.kind == EMAIL:
            if not _MESSAGE_ID.fullmatch(self.message_id or ""):
                return None
            return GMAIL_LINK % (self.account if _ACCOUNT.fullmatch(self.account or "") else "0", self.message_id)
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
                                    url=str(row.get("url") or ""), message_id=str(row.get("message_id") or ""),
                                    account=str(row.get("account") or ""))
    return out


def known_links(sources: dict) -> set:
    """Every link the run's sources support."""
    return {link for s in sources.values() if (link := s.link())}


def strip_unknown_links(text: str, known: Iterable[str]) -> str:
    """Keep the words of a link the model typed and drop the address, unless it is exactly a known one; drop a bare,
    bracketed or angle-bracketed address that is not known. Safe to run on a whole assembled briefing."""
    known = set(known)
    text = _MD_LINK.sub(lambda m: m.group(0) if m.group(2) in known else m.group(1), text or "")
    text = _ANGLE_URL.sub(lambda m: m.group(0) if m.group(1) in known else "", text)
    text = _BARE_URL.sub(lambda m: m.group(0) if m.group(0) in known else "", text)
    return re.sub(r"\(\s*\)", "", text)


def _section_kinds(title: str) -> set:
    return {kind for kind, pattern in _HEADING_KIND if pattern.search(title)}


def _accepts(kinds: set) -> set:
    return set().union(*(ACCEPTS[k] for k in kinds)) if kinds else {NEWS, EMAIL, CALENDAR, EVENT}


def _resolve(item: str, kinds: set, sources: dict, known: set) -> Optional[str]:
    """The item with its citations turned into links, or None when it may not stand (see the module rules)."""
    text = strip_unknown_links(item, known)
    verified = any(url in text for url in known)
    allowed = _accepts(kinds)
    cited = bad = False

    def cite(match):
        nonlocal verified, cited, bad
        cited = True
        links, keep = [], []
        for sid in re.split(r"\s*[,;]\s*", match.group(1)):
            source = sources.get(sid)
            link = source.link() if source and source.kind in allowed else None
            if link is None:
                bad = True                         # an id that resolves to nothing linkable here sinks the item
                continue
            verified = True
            if source.kind == NEWS:
                keep.append(sid)                   # services/news_links attaches the publisher link afterwards
            else:
                links.append("[%s](%s)" % (LABELS[source.kind], link))
        if keep and not links:
            return "[%s]" % ", ".join(keep)
        return "(%s)" % ", ".join(links) if links else ""
    out = _CITE.sub(cite, text)
    if bad or (kinds and not verified):
        return None
    out = re.sub(r"[ \t]+\(", " (", out)
    return re.sub(r" +([.,;:])", r"\1", out)


def _count_line(total: int) -> str:
    return "_%d item%s dropped: no source_" % (total, "" if total == 1 else "s")


def validate(text: str, sources: dict) -> Validated:
    """The briefing with every item that may not stand dropped and counted (see the module rules)."""
    known = known_links(sources)
    out, dropped = [], {}
    stack: list = []                                   # (heading level, the section's kinds)
    item: Optional[list] = None                        # the lines of the item being collected
    item_kinds: set = set()

    def close_item():
        nonlocal item
        if item is None:
            return
        resolved = _resolve("\n".join(item), item_kinds, sources, known)
        if resolved is None:
            label = "/".join(sorted(item_kinds)) or "other"
            dropped[label] = dropped.get(label, 0) + 1
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
        kinds = stack[-1][1] if stack else set()
        if heading:
            level = len(heading.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, _section_kinds(heading.group(2)) or (stack[-1][1] if stack else set())))
            out.append(line)
        elif bullet or (line.strip() and (_CITE.search(line) or _MD_LINK.search(line) or _BARE_URL.search(line))):
            # a list item, or a line of prose that cites or links: checked as an item
            if kinds or _CITE.search(line) or _MD_LINK.search(line) or _BARE_URL.search(line):
                item, item_kinds = [line], kinds
            else:
                out.append(line)                       # a task or an insight outside the sourced sections
        else:
            out.append(line)
    close_item()
    result = "\n".join(out)
    total = sum(dropped.values())
    if total:
        result = result.rstrip("\n") + "\n\n" + _count_line(total) + "\n"
    return Validated(result, dropped)
