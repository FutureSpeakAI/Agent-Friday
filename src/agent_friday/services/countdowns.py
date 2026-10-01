"""The owner's countdowns: what is coming up in their own world, ranked by how
much it matters to them and how soon it is, each with the reason it is there.

Sources, all local reads (docs/design/active/unified-shell.md §10.1):

  * their calendar: the cached read the Calendar workspace makes, plus their
    quick-add events, for the next 60 days ("from your calendar");
  * commitments they made: to-dos they accepted that have a deadline, open
    follow-ups, and their goals and milestones with a due date;
  * their wiki and knowledge graph: birthdays, anniversaries and other dated
    lines on people and personal pages, rolled to their next date, a person
    more linked in the wiki counting for more ("from your wiki").

Generic holidays are not a source: one shows only when it is on the calendar
or in the wiki, and then as that entry. Nothing here calls a model. A source
that fails adds nothing and is named in the reply; the others still answer.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

TOP = 4                  # how many the start screen shows
MAX_LIMIT = 12
CALENDAR_DAYS = 60       # how far ahead the calendar is read
FAR_DAYS = 366           # how far ahead a commitment or a dated line counts
PERSON_SECTIONS = ("people", "family")
SELF_SECTION = "identity"
WIKI_SECTIONS = PERSON_SECTIONS + ("personal", SELF_SECTION)

#: How much an item matters before how soon it is; a routine meeting is 1.
MATTERS = {
    "meeting": 1.0, "appointment": 2.0, "deadline": 2.0, "trip": 2.5,
    "interview": 3.0, "birthday": 2.5, "anniversary": 3.0, "wedding": 3.0,
    "graduation": 2.5, "todo_high": 2.5, "todo_medium": 2.0, "todo_low": 1.5,
    "follow_up": 2.0, "goal": 2.5, "milestone": 2.0,
}
EMOJI = {
    "meeting": "📅", "appointment": "🩺", "deadline": "⏳", "trip": "✈️",
    "interview": "💼", "birthday": "🎂", "anniversary": "💍", "wedding": "💍",
    "graduation": "🎓", "todo": "✅", "follow_up": "💬", "goal": "🎯",
    "milestone": "🏁",
}
WHY_CALENDAR = "from your calendar"
WHY_WIKI = "from your wiki"
WHY_TODO = "a deadline you set"
WHY_GOAL = "your goal"
WHY_MILESTONE = "a step in your goal"

_PERSONAL = ("birthday", "anniversary", "wedding", "graduation")
_EVENT_WORDS = tuple((kind, re.compile(r"\b(?:%s)\b" % "|".join(map(re.escape, words))))
                     for kind, words in (
    ("birthday", ("birthday", "bday", "b-day")),
    ("anniversary", ("anniversary",)),
    ("wedding", ("wedding",)),
    ("graduation", ("graduation",)),
    ("trip", ("flight", "fly to", "trip", "hotel", "check-in", "airport", "vacation",
              "travel", "train to")),
    ("appointment", ("doctor", "dentist", "appointment", "appt", "therapy", "vet",
                     "checkup", "check-up", "haircut")),
    ("deadline", ("deadline", "due", "launch", "submit", "filing")),
))
_ACCEPTED_TODO = ("approved", "pending", "open", "in_progress")
_LIVE_GOAL = ("approved", "active")
_OPEN_MILESTONE = ("pending", "in_progress", "verifying", "escalated", "blocked")


# ── time ─────────────────────────────────────────────────────────────────────

def _as_local(value) -> datetime | date | None:
    """A timed start as local naive time, or an all-day date; None when unreadable.

    An aware time is converted to this computer's zone, not merely stripped."""
    if isinstance(value, datetime):
        return value.astimezone().replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if len(text) == 10:
            return date.fromisoformat(text)
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt


def short_when(now: datetime, start, all_day: bool) -> str:
    """"now", "in 25 min", "in 3 h", "today", "tomorrow", "in 12 days".

    The page says the same words, from ``at`` and ``date``, every minute."""
    if not all_day:
        mins = int((start - now).total_seconds() // 60)
        if mins < 1:
            return "now"
        if mins < 60:
            return "in %d min" % mins
        days = (start.date() - now.date()).days
        if days == 0:
            return "in %d h" % (mins // 60)
    else:
        days = (start - now.date()).days
    if days <= 0:
        return "today"
    if days == 1:
        return "tomorrow"
    return "in %d days" % days


def _item(now: datetime, *, id: str, label: str, start, kind: str, source: str,
          why: str, emoji: str, matters: float, horizon: int = FAR_DAYS) -> dict | None:
    """One countdown, or None when it is past or beyond the horizon."""
    start = _as_local(start)
    if start is None or not label:
        return None
    all_day = not isinstance(start, datetime)
    if all_day:
        days = (start - now.date()).days
        if days < 0:
            return None
        soon, minutes, at, day = float(days), None, None, start
    else:
        minutes = int((start - now).total_seconds() // 60)
        if minutes < 0:
            return None
        day = start.date()
        days = (day - now.date()).days
        soon, at = minutes / 1440.0, int(start.timestamp())
    if days > horizon:
        return None
    return {
        "id": id, "label": label[:80], "date": day.isoformat(), "at": at,
        "all_day": all_day, "days": days, "minutes": minutes,
        "kind": kind, "source": source, "why": why, "emoji": emoji,
        # how much it matters times how soon it is: today counts fully, a week
        # out about half, two months out about a tenth
        "score": round(matters / (1.0 + soon / 7.0), 4),
        "short": short_when(now, start, all_day),
    }


def _first_name(name: str) -> str:
    return (re.split(r"[\s(,]+", (name or "").strip()) or [""])[0]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


# ── the calendar ─────────────────────────────────────────────────────────────

def _event_kind(ev: dict) -> str:
    from agent_friday.services import calendar_engine as ce
    hay = "%s %s" % (ev.get("title") or "", ev.get("location") or "")
    hay = hay.lower()
    for kind, rx in _EVENT_WORDS:
        if rx.search(hay):
            return kind
    return "interview" if ce._classify_event(ev) == "career" else "meeting"


def _title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def from_calendar(now: datetime) -> list:
    from agent_friday.services import calendar_engine as ce
    start = datetime(now.year, now.month, now.day)
    end = start + timedelta(days=CALENDAR_DAYS + 1)
    events = [dict(e) for e in ce._fetch_calendar_range(start, end)]
    for le in ce._load_local_events():
        s = _as_local(le.get("start_time"))
        if s is not None and not isinstance(s, datetime):
            s = datetime(s.year, s.month, s.day)
        if s is not None and start <= s < end:
            events.append(dict(le, source=le.get("source") or "local"))
    # A title that repeats through the window (a weekly stand-up) is routine.
    repeats: dict = {}
    for ev in events:
        k = _title_key(ev.get("title"))
        repeats[k] = repeats.get(k, 0) + 1
    seen, out = set(), []
    for ev in events:
        key = ev.get("id") or ((ev.get("title") or "") + str(ev.get("start_time") or ""))
        if key in seen:
            continue
        seen.add(key)
        kind = _event_kind(ev)
        matters = MATTERS[kind]
        if kind == "meeting" and repeats.get(_title_key(ev.get("title")), 0) >= 3:
            matters *= 0.5
        if (ev.get("source") or "") != "google":
            matters *= 1.25          # the owner put it there by hand
        start_time = str(ev.get("start_time") or "")
        it = _item(now, id="cal:%s" % key, label=str(ev.get("title") or "").strip(),
                   start=start_time[:10] if ev.get("all_day") else start_time,
                   kind="personal" if kind in _PERSONAL else "event", source="calendar",
                   why=WHY_CALENDAR, emoji=EMOJI[kind], matters=matters,
                   horizon=CALENDAR_DAYS)
        if it:
            out.append(it)
    return out


# ── commitments ──────────────────────────────────────────────────────────────

def from_todos(now: datetime) -> list:
    from agent_friday.services import misc_engine
    out = []
    for t in misc_engine._load_todos() or []:
        if not isinstance(t, dict) or not t.get("deadline"):
            continue
        status = str(t.get("status") or "")
        # the owner's once accepted, or when they wrote it themselves
        if status not in _ACCEPTED_TODO and not (status == "proposed" and t.get("source") == "user"):
            continue
        prio = str(t.get("priority") or "medium").lower()
        it = _item(now, id="todo:%s" % t.get("id"), label=str(t.get("title") or "").strip(),
                   start=t.get("deadline"), kind="commitment", source="todo", why=WHY_TODO,
                   emoji=EMOJI["todo"],
                   matters=MATTERS.get("todo_" + prio, MATTERS["todo_medium"]))
        if it:
            out.append(it)
    return out


def from_follow_ups(now: datetime) -> list:
    from agent_friday.services import relationship_memory as rm
    out = []
    for f in rm.list_follow_ups("open") or []:
        who = _first_name(f.get("person") or "") or "them"
        due = _as_local(f.get("due"))
        if isinstance(due, datetime):
            due = due.date()           # a follow-up is due on a day
        note = str(f.get("note") or "").strip()
        label = note if 0 < len(note) <= 60 else "Follow up with %s" % who
        it = _item(now, id="fu:%s" % f.get("id"), label=label, start=due, kind="commitment",
                   source="follow_up", why="you said you'd get back to %s" % who,
                   emoji=EMOJI["follow_up"], matters=MATTERS["follow_up"])
        if it:
            out.append(it)
    return out


def from_goals(now: datetime) -> list:
    from agent_friday.services import goals
    out = []
    for g in goals.list_goals() or []:
        if g.get("status") not in _LIVE_GOAL:
            continue
        title = str(g.get("title") or "").strip()
        it = _item(now, id="goal:%s" % g.get("goal_id"), label=title, start=g.get("deadline"),
                   kind="commitment", source="goal", why=WHY_GOAL, emoji=EMOJI["goal"],
                   matters=MATTERS["goal"])
        if it:
            out.append(it)
        for m in g.get("milestones") or []:
            if m.get("status") not in _OPEN_MILESTONE:
                continue
            it = _item(now, id="ms:%s/%s" % (g.get("goal_id"), m.get("milestone_id")),
                       label=str(m.get("name") or "").strip(), start=m.get("due"),
                       kind="commitment", source="goal",
                       why="%s: %s" % (WHY_MILESTONE, title) if title else WHY_MILESTONE,
                       emoji=EMOJI["milestone"], matters=MATTERS["milestone"])
            if it:
                out.append(it)
    return out


# ── the wiki and the knowledge graph ─────────────────────────────────────────

_MONTHS = {m: i for i, names in enumerate((
    ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
    ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
    ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
    ("dec", "december")), 1) for m in names}
_MONTH = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
          r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")
_DATES = (
    ("ymd", re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")),
    ("mdy", re.compile(r"\b" + _MONTH + r"\s+(\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s+(\d{4})\b)?",
                       re.IGNORECASE)),
    ("dmy", re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+" + _MONTH + r"(?:,?\s+(\d{4})\b)?",
                       re.IGNORECASE)),
    ("us", re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}|\d{2}))?\b")),
)
_KEYWORD = re.compile(r"\b(birthday|bday|born|anniversary|wedding|graduation)\b", re.IGNORECASE)
_KEY_KIND = {"bday": "birthday", "born": "birthday"}
_FIELD_WORDS = {"birthday", "bday", "born", "anniversary", "wedding", "graduation",
                "date", "day", "on", "the", "is", "was"}
_MARKUP = re.compile(r"[*_`>#]+|^\s*(?:[-+]|\d+[.)])\s+")
_HEAD_WORDS = 6          # a longer lead-in is a sentence, not a date field


def _find_date(line: str):
    """(month, day, year or None, where the date starts and ends) for the first
    date on a line."""
    for form, rx in _DATES:
        m = rx.search(line)
        if not m:
            continue
        g = m.groups()
        try:
            if form == "ymd":
                y, mo, d = int(g[0]), int(g[1]), int(g[2])
            elif form == "mdy":
                mo, d, y = _MONTHS[g[0].lower()], int(g[1]), g[2] and int(g[2])
            elif form == "dmy":
                d, mo, y = int(g[0]), _MONTHS[g[1].lower()], g[2] and int(g[2])
            else:
                mo, d, y = int(g[0]), int(g[1]), g[2] and int(g[2])
                if y and y < 100:
                    y += 2000 if y < 50 else 1900
            date(2000, mo, d)          # a real day of the year (2000 was a leap year)
        except (KeyError, ValueError, TypeError):
            continue
        return mo, d, (y or None), m.start(), m.end()
    return None


def _on(year: int, month: int, day: int) -> date:
    """That day in that year; 29 February falls on the 28th in other years."""
    try:
        return date(year, month, day)
    except ValueError:
        return date(year, month, 28)


def _next_yearly(today: date, month: int, day: int) -> date:
    d = _on(today.year, month, day)
    return d if d >= today else _on(today.year + 1, month, day)


def _page_title(path: Path, text: str) -> str:
    m = re.search(r"^title:\s*[\"']?(.+?)[\"']?\s*$", text[:2000], re.MULTILINE)
    if m:
        return m.group(1).strip()
    m = re.search(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return path.stem.replace("-", " ").replace("_", " ").title()


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", _MARKUP.sub(" ", text)).strip(" :-–—,(")


def dated_lines(text: str):
    """Each dated line as (kind, lead, head, month, day, year): ``lead`` is what
    comes before the keyword, and ``head`` what the line calls the date, both
    without markup. "Birthday: March 14" has lead "" and head "Birthday";
    "March 14 - Sam's birthday" has head "Sam's birthday"."""
    for line in text.splitlines():
        km = _KEYWORD.search(line)
        if not km:
            continue
        found = _find_date(line)
        if not found:
            continue
        mo, d, y, start, end = found
        word = km.group(1).lower()
        if start < km.start():
            head = line[end:]
        else:
            head = line[:start]
            if ":" in head:
                head = head.rsplit(":", 1)[0]
        yield (_KEY_KIND.get(word, word), _clean(line[:km.start()]), _clean(head), mo, d, y)


def label_for(section: str, title: str, kind: str, lead: str, head: str) -> str | None:
    """What a dated line is called on the start screen, or None to leave it out.

    On a person's page only a field line counts ("Birthday: March 14" on
    Dana's page is "Dana's birthday"; "Kids: Sam (birthday May 3)" is not
    Dana's). On the owner's own pages it is theirs ("Your birthday"). On other
    personal pages the line names what it is ("Sam's birthday: May 3")."""
    what = "wedding anniversary" if kind == "anniversary" and "wedding" in head.lower() else kind
    if section in PERSON_SECTIONS or section == SELF_SECTION:
        if lead:
            return None
        return "Your " + what if section == SELF_SECTION else "%s's %s" % (_first_name(title), what)
    words = re.sub(r"[^a-z]+", " ", head.lower()).split()
    if (not _KEYWORD.search(head) or len(words) > _HEAD_WORDS
            or set(words) <= _FIELD_WORDS):
        return None
    return head[:1].upper() + head[1:]


def wiki_root() -> Path:
    from agent_friday import core
    return Path(core.WIKI_DIR)


_DEGREES: dict = {"key": None, "degrees": {}}


def degrees() -> dict:
    """How linked each wiki page is in the knowledge graph, by entity id."""
    from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore
    store = KnowledgeGraphStore()
    key: list = [str(store.base)]
    for p in (store._plain_path("entities"), store._sensitive_path("entities")):
        try:
            key.append(p.stat().st_mtime_ns)
        except OSError:
            key.append(None)
    if _DEGREES["key"] != key:
        _DEGREES["degrees"] = {e.get("id"): int(e.get("degree") or 0)
                               for e in store.load("entities") if isinstance(e, dict)}
        _DEGREES["key"] = key
    return _DEGREES["degrees"]


def forgotten() -> set:
    """Names the owner asked Friday to forget; nothing is shown about them."""
    try:
        from agent_friday.services import forget_person as fp
        return fp.forgotten_names()
    except Exception:
        return set()


def from_wiki(now: datetime) -> list:
    from agent_friday.services import wiki_engine
    from agent_friday.services.knowledge_graph.wiki_graph import _page_key
    root = wiki_root()
    today = now.date()
    gone = forgotten()
    links = None
    out = []
    for section in WIKI_SECTIONS:
        base = root / section
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.md")):
            if path.stem.startswith("_"):
                continue
            text = wiki_engine.wiki_read_text(path)
            if not _KEYWORD.search(text):
                continue
            rel = path.relative_to(root).as_posix()
            title = _page_title(path, text)
            if _norm(title) in gone:
                continue
            if links is None:
                try:
                    links = degrees()
                except Exception:
                    links = {}
            linked = min(int(links.get("page:" + _page_key(rel)) or 0), 20) / 20.0
            weight = 1.25 if section == SELF_SECTION else 0.75 + 0.5 * linked
            for n, (kind, lead, head, mo, d, y) in enumerate(dated_lines(text)):
                if kind == "wedding" and y and _on(y, mo, d) < today:
                    kind = "anniversary"          # a wedding that has happened
                    head = head or "wedding"
                if kind in ("birthday", "anniversary") or (kind == "wedding" and not y):
                    when = _next_yearly(today, mo, d)
                elif y:
                    when = _on(y, mo, d)          # a one-time date, shown while ahead
                else:
                    continue
                label = label_for(section, title, kind, lead, head)
                if not label:
                    continue
                subject = _norm(re.sub(r"['’]s\b.*$", "", label))
                if subject in gone:
                    continue
                it = _item(now, id="wiki:%s#%d" % (rel, n), label=label, start=when,
                           kind="personal", source="wiki", why=WHY_WIKI, emoji=EMOJI[kind],
                           matters=MATTERS[kind] * weight)
                if it:
                    out.append(it)
    return out


# ── together ─────────────────────────────────────────────────────────────────

#: (the name a failure is reported under, the reader in this module)
SOURCES = (
    ("calendar", "from_calendar"),
    ("to-dos", "from_todos"),
    ("follow-ups", "from_follow_ups"),
    ("goals", "from_goals"),
    ("wiki", "from_wiki"),
)


def _same(it: dict) -> tuple:
    words = re.sub(r"['’]s\b", "", it["label"].lower())
    return it["date"], re.sub(r"[^a-z0-9]+", "", words)


def countdowns(now: datetime | None = None, kind: str | None = None,
               limit: int = TOP) -> dict:
    """The owner's top countdowns in time order, and the sources that could not answer.

    ``kind`` narrows to "event", "commitment" or "personal" (the Family
    workspace asks for the personal kind)."""
    now = now or datetime.now()
    try:
        limit = max(1, min(int(limit), MAX_LIMIT))
    except (TypeError, ValueError):
        limit = TOP
    items, failed = [], []
    for name, reader in SOURCES:
        try:
            items.extend(globals()[reader](now))
        except Exception as exc:
            failed.append(name)
            log.info("countdowns: %s could not be read (%s)", name, type(exc).__name__)
    if kind:
        items = [it for it in items if it["kind"] == kind]
    best: dict = {}
    for it in items:
        k = _same(it)
        if k not in best or it["score"] > best[k]["score"]:
            best[k] = it
    ranked = sorted(best.values(), key=lambda it: (-it["score"], it["date"], it["label"]))[:limit]
    for i, it in enumerate(ranked, 1):
        it["rank"] = i
    ranked.sort(key=lambda it: (it["date"], it["at"] or 0, it["rank"]))
    return {"countdowns": ranked, "failed": failed}
