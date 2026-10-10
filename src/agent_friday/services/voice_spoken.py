"""Spoken answers built in code, and the text clean-up every spoken line gets.

A small voice front (Ternary Bonsai 1.7B) is unreliable at structured lists:
handed three emails it listed them and then reasoned aloud for 646 words;
handed six events it narrated itself in the first person, used markdown
bullets and bold, and read out ISO dates; handed the news it looped. So a
routed read whose result is a list of records (calendar events, email
messages, files, news stories, web hits) is spoken from the records by
``structured_reply``, with no model call: short, second person, natural
local times ("3:40 PM"), "today" / "tomorrow" / a weekday instead of a date,
sender and subject for email (never a body), and "There are N more." past
the spoken few. The model still speaks free-text results (notes, past
conversations, the briefing).

Every field spoken from a record is cleaned (``_clean``): no markdown, no
control, bidi or zero-width characters, and no chat-template markers
(``<|`` / ``|>``, which the session's DeltaFilter would read as the start of
model markup and mute the rest of the turn).

``speakable`` strips markdown from anything about to be spoken, whoever
wrote it.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

#: Routed reads whose result is a list of records, spoken from code.
STRUCTURED_TOOLS = frozenset({"query_calendar", "check_email", "search_email", "search_files",
                              "search_news", "search_web"})
#: How many stories or web hits are spoken before "There are N more".
SPOKEN_STORIES = 3
#: Longest spoken story or hit, in words.
_STORY_WORDS = 30
#: How many records are spoken before "There are N more".
SPOKEN_ITEMS = 5
#: Longest title, subject or name spoken from a record.
_FIELD_CHARS = 80

_LATE_PREFIX = re.compile(r"(?s)^\s*\[LATE RESULT[^\]]*\]\s*")

# ── markdown and unspeakable characters out ──────────────────────────────────

_MD_LINK = re.compile(r"\[([^\]\n]{1,300})\]\((?:[^)\s]+)\)")
#: Bold and italics; a lone "_" is left alone (file names use it).
_MD_EMPH = re.compile(r"(\*\*|__|\*)(?=\S)(.+?)(?<=\S)\1")
#: List and heading markup at the start of a line. The clause chunker splits
#: a list into one clause per item, so it is stripped at the start of every
#: line, a one-line clause included; a dash inside a sentence stays.
_MD_LINE = re.compile(r"(?m)^[ \t]*(?:#{1,6}[ \t]+|[-*+\u2022][ \t]+|>[ \t]*)")
#: "1. " / "2) " opening a list item (followed by a capital letter).
_MD_NUMBERED = re.compile(r"(?m)^[ \t]*\d{1,2}[.)][ \t]+(?=[A-Z])")
_MD_LEFTOVER = re.compile(r"(?m)\*\*|__|`+|^[ \t]*[-*_]{3,}[ \t]*$")
#: Trademark, registered and copyright signs (NFKC would spell "TM").
_MARKS = re.compile("[\u2122\u00ae\u00a9]")
#: Chat-template markers and the characters that hide or reorder text.
_TEMPLATE = re.compile(r"<\||\|>")
_HIDDEN = re.compile("[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f"
                     "\u00ad\u061c\u180e\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff\ufff9-\ufffb]")


def speakable(text: str) -> str:
    """`text` without markdown: what a mouth should read aloud."""
    s = str(text or "")
    if not s:
        return s
    s = _MD_LINK.sub(r"\1", s)
    s = _MD_LINE.sub("", s)
    s = _MD_NUMBERED.sub("", s)
    for _ in range(2):
        s = _MD_EMPH.sub(r"\2", s)
    s = _MD_LEFTOVER.sub("", s)
    return re.sub(r"[ \t]{2,}", " ", s).strip(" \t") if s.strip() else ""


def _clean(v) -> str:
    """Record text made safe to speak: one line, no markdown, no template
    markers, no control / bidi / zero-width characters."""
    s = _MARKS.sub("", str(v or ""))
    s = unicodedata.normalize("NFKC", s)
    s = _HIDDEN.sub("", s)
    s = _TEMPLATE.sub(" ", s)
    s = re.sub(r"\s+", " ", s)
    return speakable(s).strip()


# ── records to sentences ─────────────────────────────────────────────────────

def _payload(result):
    text = _LATE_PREFIX.sub("", str(result or "")).strip()
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _field(v) -> str:
    """A record's text field, safe to speak and short."""
    s = _clean(v)
    if len(s) > _FIELD_CHARS:
        s = s[:_FIELD_CHARS].rsplit(" ", 1)[0] + "…"
    return s.rstrip(" .?!;:,")


def _join(items: list) -> str:
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return "%s and %s" % (items[0], items[1])
    return ", ".join(items[:-1]) + ", and " + items[-1]


def _more(n: int) -> str:
    """How many were not spoken. No offer to read them: a bare "yes" has no
    route back to this list (a repeat news request moves on by itself)."""
    if n <= 0:
        return ""
    return "There's 1 more." if n == 1 else "There are %d more." % n


def _total(data: dict, listed: int) -> int:
    """The tool's own total when it reports one larger than the list."""
    try:
        return max(listed, int(data.get("count") or 0))
    except (TypeError, ValueError):
        return listed


# ── calendar ─────────────────────────────────────────────────────────────────

def _when(dt: datetime) -> str:
    h = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return ("%d %s" % (h, ampm)) if dt.minute == 0 else ("%d:%02d %s" % (h, dt.minute, ampm))


def _parse(value):
    """(local naive datetime or None, all_day) from a calendar time string.
    A time with "Z" or an offset is converted to this machine's local time."""
    s = str(value or "").strip()
    if not s:
        return None, False
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        try:
            d = date.fromisoformat(s)
            return datetime(d.year, d.month, d.day), True
        except ValueError:
            return None, False
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None, False
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt, False


def _day_name(d: date, today: date) -> str:
    """"Today", "Tomorrow", "On Tuesday", "On October 20". Never
    "Yesterday": a past day is named by its date."""
    delta = (d - today).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Tomorrow"
    if 1 < delta < 7:
        return "On " + d.strftime("%A")
    return "On %s %d" % (d.strftime("%B"), d.day)


def _until(d: date, today: date) -> str:
    """"until tomorrow", "until Monday", "until October 20"."""
    delta = (d - today).days
    if delta == 1:
        return "until tomorrow"
    if 1 < delta < 7:
        return "until " + d.strftime("%A")
    return "until %s %d" % (d.strftime("%B"), d.day)


#: The owner asked about what already happened: finished events stay.
PAST_ASK = re.compile(
    r"(?i)\b(?:earlier(?: today)?|did i (?:have|miss)|what (?:was|were) on my calendar|"
    r"what i had|what did i have|was i|had i|have i had|already|missed|yesterday|"
    r"last (?:night|week)|in the past|"
    r"past (?:events?|meetings?|appointments?|week|hour|few hours)\b)"
    r"(?!\s*\d)")


def event_finished(start, end, now: datetime) -> bool:
    """Has the event with these start/end strings already ended at `now`
    (local)? An all-day event ends after its last day; a timed one at its
    end, or at its start when it has no end. Unparseable: not finished."""
    s, all_day = _parse(start)
    if s is None:
        return False
    e, _e_all_day = _parse(end)
    if all_day:
        last = (e.date() - timedelta(days=1)) if e is not None else s.date()
        return max(last, s.date()) < now.date()
    return (e if e is not None else s) <= now


def _incomplete(data: dict) -> bool:
    return bool(data.get("error") or data.get("degraded") or data.get("not_searched")
                or any(str((a or {}).get("status") or "") in ("error", "needs_reauth")
                       for a in (data.get("accounts") or []) if isinstance(a, dict)))


_PARTIAL = "Some of your accounts couldn't be read, so that may not be everything."


def _calendar(data: dict, now: datetime, asked: str = "") -> str | None:
    events = [e for e in (data.get("events") or []) if isinstance(e, dict)]
    if not events and not data.get("finished") and data.get("window_complete") is not False:
        return None       # a truly empty calendar: voice_front's fixed sentence
    today = now.date()
    keep_past = bool(PAST_ASK.search(asked or ""))
    days: dict = {}
    undated = []
    for e in events:
        title = _field(e.get("title")) or "something untitled"
        start, all_day = _parse(e.get("start"))
        if start is None:
            undated.append((title, ""))
            continue
        end, _end_all_day = _parse(e.get("end"))
        if all_day:
            first = start.date()
            # Google's all-day end date is exclusive; one day when missing.
            last = (end.date() - timedelta(days=1)) if end is not None else first
            last = max(last, first)
            if last < today and not keep_past:
                continue
            key = first if (keep_past or first >= today) else today
            phrase = "%s all day" % title
            if last > key:
                phrase += ", %s" % _until(last, key)
            days.setdefault(key, []).append((datetime.min, phrase))
            continue
        finish = end if end is not None else start
        if finish <= now and not keep_past:
            continue                                  # already over
        if start.date() < today <= finish.date() and not keep_past:
            # Began on an earlier day and still running (overnight, multi-day).
            key = today
            phrase = ("%s until %s" % (title, _when(finish)) if finish.date() == today
                      else "%s, %s" % (title, _until(finish.date(), today)))
            days.setdefault(key, []).append((datetime.min.replace(microsecond=1), phrase))
            continue
        days.setdefault(start.date(), []).append((start, "%s at %s" % (title, _when(start))))
    kept = sum(len(v) for v in days.values()) + len(undated)
    total = kept + max(0, _total(data, len(events)) - len(events))
    if not kept:
        if data.get("window_complete") is False:
            # The fetch stopped at its limit: what is left was never read.
            return "Everything I could see on your calendar has already happened, " \
                   "and I couldn't see the rest of today just now."
        return "Nothing more on your calendar today or tomorrow." + (
            " " + _PARTIAL if _incomplete(data) else "")
    spoken, left, told = [], SPOKEN_ITEMS, 0
    for key in sorted(days):
        if left <= 0:
            break
        items = [p for _t, p in sorted(days[key], key=lambda x: x[0])][:left]
        left -= len(items)
        told += len(items)
        spoken.append("%s you have %s." % (_day_name(key, today), _join(items)))
    if left > 0 and undated:
        extra = [t for t, _p in undated[:left]]
        told += len(extra)
        spoken.append("You also have %s." % _join(extra))
    out = " ".join(spoken + [_more(total - told)]).strip()
    if _incomplete(data):
        out += " " + _PARTIAL
    return out


# ── email ────────────────────────────────────────────────────────────────────

def _assistant_names() -> set:
    names = {"friday", "agent friday", "agent friday tm"}
    try:
        from agent_friday.services.podcast_engine import her_name
        n = _clean(her_name() or "").strip().lower()
        if n:
            names |= {n, "agent " + n}
    except Exception:
        pass
    return names


def _sender(v) -> str:
    """Who sent it: the display name, unless it is empty or the assistant's
    own name (a mail "from Friday" must not sound like Friday speaking), in
    which case the address's domain."""
    raw = _clean(v)
    m = re.match(r'^\s*"?([^"<]*?)"?\s*<([^>]+)>\s*$', raw)
    name, addr = (m.group(1).strip(), m.group(2).strip()) if m else ("", "")
    if not m:
        if "@" in raw and " " not in raw:
            addr = raw
        else:
            name = raw
    name = _field(name)
    if name and name.lower() not in _assistant_names() and "@" not in name:
        return name
    domain = addr.rsplit("@", 1)[-1].strip(" .>") if "@" in addr else ""
    return ("someone at %s" % _field(domain)) if domain else "someone"


def _mail_item(m: dict, mark_urgent: bool = True) -> str:
    subj = _field(m.get("subject"))
    s = "from %s" % _sender(m.get("from"))
    if subj:
        s += " about %s" % subj
    if mark_urgent and m.get("urgent"):
        s += ", marked urgent"
    return s


def _plural(n, one: str, many: str) -> str:
    return one if n == 1 else many


def _unread(data: dict, msgs: list):
    """(how many unread, whether that is only a lower bound)."""
    if "unread_total" in data:
        try:
            n = int(data.get("unread_total") or 0)
        except (TypeError, ValueError):
            n = 0
        try:
            lower = int(data.get("fetched") or 0) >= int(data.get("fetch_limit") or 10 ** 9)
        except (TypeError, ValueError):
            lower = False
        return n, lower
    n = sum(1 for m in msgs if m.get("unread"))
    return n, n >= 12


def _offline(data: dict) -> bool:
    return data.get("connected") is False or str(data.get("source") or "") == "cache"


def _check_email(data: dict) -> str:
    msgs = [m for m in (data.get("messages") or []) if isinstance(m, dict)]
    n_unread, lower = _unread(data, msgs)
    count = ("at least %d" % n_unread) if lower else str(n_unread)
    if data.get("urgent_only"):
        urgent = msgs
        if not urgent:
            out = "Nothing urgent."
            if n_unread:
                out += " You have %s unread %s." % (count, _plural(n_unread, "email", "emails"))
        else:
            shown = urgent[:SPOKEN_ITEMS]
            total = _total(data, len(urgent))
            out = "You have %d urgent %s: %s." % (
                total, _plural(total, "email", "emails"),
                _join([_mail_item(m, False) for m in shown]))
            out = (out + " " + _more(total - len(shown))).strip()
        return out
    unread = [m for m in msgs if m.get("unread")]
    if not unread:
        if n_unread:
            # Unread mail exists past the listed newest messages.
            return "You have %s unread %s; the newest ones are read." % (
                count, _plural(n_unread, "email", "emails"))
        if not msgs:
            return "Nothing new in your email."
        return "Nothing unread. Your latest email is %s." % _mail_item(msgs[0])
    shown = unread[:SPOKEN_ITEMS]
    n = max(n_unread, len(unread))
    out = "You have %s unread %s: %s." % (
        ("at least %d" % n) if lower else str(n), _plural(n, "email", "emails"),
        _join([_mail_item(m) for m in shown]))
    return (out + " " + _more(n - len(shown))).strip()


def _email(data: dict, tool: str) -> str | None:
    msgs = [m for m in (data.get("messages") or []) if isinstance(m, dict)]
    if tool == "check_email":
        if not msgs and _offline(data):
            return "My offline copy has nothing new, and I couldn't reach your mail just now."
        if not msgs and not data.get("urgent_only"):
            return None
        out = _check_email(data)
    else:
        if not msgs:
            return None
        shown = msgs[:SPOKEN_ITEMS]
        total = _total(data, len(msgs))
        out = "I found %d %s: %s." % (total, _plural(total, "email", "emails"),
                                      _join([_mail_item(m) for m in shown]))
        out = (out + " " + _more(total - len(shown))).strip()
    if _offline(data):
        out += " That's from my offline copy, so it may be out of date."
    elif _incomplete(data):
        out += " " + _PARTIAL
    return out


# ── files ────────────────────────────────────────────────────────────────────

def _files(data: dict) -> str | None:
    rows = [r for r in (data.get("results") or []) if isinstance(r, dict)]
    if not rows:
        return None
    shown = rows[:SPOKEN_ITEMS]
    names = [_field(r.get("name") or str(r.get("path") or "").replace("\\", "/").rsplit("/", 1)[-1])
             or "an unnamed file" for r in shown]
    total = _total(data, len(rows))
    out = "I found %d %s: %s." % (total, _plural(total, "file", "files"), _join(names))
    return (out + " " + _more(total - len(shown))).strip()


# ── news and the web ─────────────────────────────────────────────────────────

#: Words a "." follows without ending a sentence.
_ABBREV = frozenset({"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "mt", "ft", "no",
                     "vs", "etc", "inc", "ltd", "co", "corp", "gov", "gen", "sen", "rep",
                     "lt", "col", "capt", "sgt", "jan", "feb", "mar", "apr", "jun", "jul",
                     "aug", "sep", "sept", "oct", "nov", "dec", "approx", "est", "dept"})


def _words(text: str, n: int = _STORY_WORDS) -> str:
    w = text.split()
    return " ".join(w[:n]) if len(w) > n else text


def _first_sentence(text: str) -> str:
    """The first sentence: it ends at ". ", "! " or "? " followed by a
    capital, unless the word before the dot is an abbreviation ("Dr.") or a
    single letter or an initialism ("U.S.")."""
    for m in re.finditer(r"([.!?])\s+(?=[A-Z\"'(])", text):
        if m.group(1) == ".":
            before = re.search(r"(\S+)$", text[:m.start()])
            word = (before.group(1) if before else "").strip("\"'(").lower()
            if (word in _ABBREV or re.fullmatch(r"[a-z]", word)
                    or re.fullmatch(r"(?:[a-z]\.)+[a-z]", word)):
                continue
        return text[:m.start() + 1]
    return text


def _sentence(text: str, whole: bool = False) -> str:
    """Cleaned, without URLs, at most _STORY_WORDS words; ``whole`` keeps a
    title whole (no sentence split) when it fits."""
    t = re.sub(r"https?://\S+|www\.\S+", "", _clean(text)).strip()
    if not t:
        return ""
    if not (whole and len(t.split()) <= _STORY_WORDS):
        t = _first_sentence(t)
    return _words(t).rstrip(" ,;:-").rstrip(".")


def _end(t: str) -> str:
    return t if t[-1:] in ".!?" else t + "."


def _domain(url) -> str:
    try:
        host = urlparse(str(url or "")).hostname or ""
    except ValueError:
        host = ""
    host = host[4:] if host.startswith("www.") else host
    return _field(host)


def _stories(items: list, lead: str, total: int, web: bool = False) -> str | None:
    """Up to SPOKEN_STORIES items, each its title (else its summary's first
    sentence), attributed: a news outlet by name, a web hit by its site, so a
    headline never sounds like Friday's own claim. Never a URL."""
    said = []
    for it in items:
        text = (_sentence(it.get("title") or "", whole=True)
                or _sentence(it.get("snippet") or it.get("summary") or ""))
        if not text:
            continue
        # The headline keeps its own case: lowercasing its first word would
        # turn a name ("Dr.", "Apple") into a common word.
        if web:
            site = _domain(it.get("url"))
            said.append(_end("One site, %s, says %s" % (site, text) if site
                             else "One result says %s" % text))
        else:
            outlet = _field(it.get("source") or "")
            said.append(_end("%s says %s" % (outlet, text) if outlet
                             else text[0].upper() + text[1:]))
        if len(said) == SPOKEN_STORIES:
            break
    if not said:
        return None
    return (lead + " " + " ".join(said) + " " + _more(total - len(said))).strip()


_WEB_ROW = re.compile(r"(?m)^\s*\d+\.\s+(?P<title>[^\n]*)\n[ \t]+(?P<snippet>[^\n]*)"
                      r"(?:\n[ \t]+(?P<url>\S+))?")


def _news(data: dict) -> str | None:
    hits = [h for h in (data.get("hits") or []) if isinstance(h, dict)
            and (h.get("title") or h.get("snippet") or h.get("summary"))]
    return _stories(hits, "Here's the news:", _total(data, len(hits))) if hits else None


def _web(result) -> str | None:
    data = _payload(result)
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        rows = [r for r in data["results"] if isinstance(r, dict)]
        total = _total(data, len(rows))
    else:
        text = _LATE_PREFIX.sub("", str(result or ""))
        if not text.lstrip().startswith("Search results for"):
            return None
        rows = [{"title": m.group("title"), "snippet": m.group("snippet"), "url": m.group("url")}
                for m in _WEB_ROW.finditer(text)]
        total = len(rows)
    rows = [{"title": r.get("title"), "snippet": r.get("snippet") or r.get("description"),
             "url": r.get("url")} for r in rows]
    return _stories(rows, "Here's what I found on the web:", total, web=True) if rows else None


def structured_reply(result, tool: str, now: datetime | None = None,
                     asked: str = "") -> str | None:
    """The spoken answer for a structured routed read, or None (not a
    structured tool, nothing to list, or a shape it does not know: the
    speaker answers instead). ``asked`` is what the owner said (the calendar
    keeps finished events only when they asked about the past)."""
    if tool not in STRUCTURED_TOOLS:
        return None
    if tool == "search_web":
        try:
            return _web(result)
        except Exception:
            return None
    data = _payload(result)
    if not isinstance(data, dict):
        return None
    try:
        if tool == "query_calendar":
            return _calendar(data, now or datetime.now(), asked)
        if tool in ("check_email", "search_email"):
            return _email(data, tool)
        if tool == "search_files":
            return _files(data)
        if tool == "search_news":
            return _news(data)
    except Exception:
        return None
    return None


__all__ = ["STRUCTURED_TOOLS", "SPOKEN_ITEMS", "speakable", "structured_reply"]
