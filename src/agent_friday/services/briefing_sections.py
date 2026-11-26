"""The skills a briefing runs as sections, and what it says when one does not run.

A briefing's sections are a list in settings (`briefing_sections`): each entry names a skill, the heading it gets,
and the days it applies. The generator runs each applicable entry as its own model call, with that skill's
instructions and the numbered calendar windows (services/briefing_events), checks the result with the link
validator (services/briefing_links), and assembles it under its heading in code. A section whose skill is not
installed, awaits approval, cannot be read, errors, or returns nothing shows one line: "Didn't run: <skill> <why>".
It never silently disappears.

The default list holds the events section every day and the Weekend Family Outing section on Friday to Sunday,
which is the daily-briefing-format skill's own rule written as data. Section results are kept with the time they
were made (`SECTIONS_FILE`), so Start My Day can use today's and say how old they are, and regenerate when they
are too old.

tests/unit/test_briefing_sections.py holds each rule.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from agent_friday.services import briefing_events, briefing_links, briefing_skills

_log = logging.getLogger(__name__)

SETTING = "briefing_sections"
SECTIONS_FILE = "briefing_sections.json"
MAX_AGE = timedelta(hours=6)
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULT_SECTIONS = (
    {"skill": "events-horizon", "heading": "Events", "days": "daily", "enabled": True},
    {"skill": "family-outing", "heading": "Weekend Family Outing", "days": "fri-sun", "enabled": True},
)
RAN, DID_NOT_RUN = "ran", "didnt_run"
_REASONS = {briefing_skills.WAITING: "awaiting approval", briefing_skills.MISSING: "is not installed",
            briefing_skills.UNREADABLE: "could not be read"}


@dataclass(frozen=True)
class Spec:
    skill: str
    heading: str
    days: str = "daily"
    enabled: bool = True


@dataclass(frozen=True)
class Result:
    skill: str
    heading: str
    state: str                      # RAN or DID_NOT_RUN
    markdown: str = ""
    reason: str = ""
    dropped: int = 0
    links: tuple = ()               # the verified links the markdown carries (the final link pass keeps only these)


_DAY_WORDS = {"daily": range(7), "weekdays": range(5), "weekends": (5, 6)}


def _days(spec_days: str) -> Optional[set]:
    """The weekday numbers (Monday 0) a days spec covers: "daily", "weekdays", "weekends", "fri-sun", "mon,wed", or
    a single day. None when any part of it cannot be read (the section then says it did not run, and why)."""
    text = str(spec_days or "daily").strip().lower()
    if text in _DAY_WORDS:
        return set(_DAY_WORDS[text])
    out = set()
    for part in text.split(","):
        part = part.strip()
        low, dash, high = part.partition("-")
        if dash and low in DAY_NAMES and high in DAY_NAMES:
            a, b = DAY_NAMES.index(low), DAY_NAMES.index(high)
            out |= {d % 7 for d in range(a, b + 1 if b >= a else b + 8)}
        elif part in DAY_NAMES:
            out.add(DAY_NAMES.index(part))
        else:
            return None
    return out or None


def applies(spec: Spec, now: datetime) -> bool:
    """On, and today is one of its days. A section whose days cannot be read applies (and says it did not run)."""
    days = _days(spec.days)
    return spec.enabled and (days is None or now.weekday() in days)


def configured(settings: Optional[dict] = None) -> list:
    """The section list from settings, or the default. An entry that is not well formed is skipped, not guessed at."""
    if settings is None:
        try:
            from agent_friday.core import _load_settings
            settings = _load_settings() or {}
        except Exception:
            _log.exception("settings could not be read: the default briefing sections are used")
            settings = {}
    raw = settings.get(SETTING)
    if not isinstance(raw, list):
        raw = list(DEFAULT_SECTIONS)
    out = []
    for entry in raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("skill"), str) or not entry["skill"].strip():
            continue
        skill = entry["skill"].strip()
        heading = " ".join(str(entry.get("heading") or skill).split()).lstrip("#").strip() or skill   # one line
        out.append(Spec(skill=skill, heading=heading[:80],
                        days=str(entry.get("days") or "daily"), enabled=entry.get("enabled") is not False))
    return out


def overview(now: datetime, settings: Optional[dict] = None) -> list:
    """What the Customize Briefing panel lists: every configured section with its skill's state now (installed,
    waiting, missing, unreadable) and whether it applies today."""
    out = []
    for spec in configured(settings):
        out.append({"skill": spec.skill, "heading": spec.heading, "days": spec.days, "enabled": spec.enabled,
                    "state": briefing_skills.load(spec.skill).state, "today": applies(spec, now)})
    return out


def set_enabled(skill: str, enabled: bool) -> list:
    """Turn one configured section on or off, keeping the rest of the list as it is, and save the whole list
    (the default list is written out the first time). Raises KeyError for a skill that is not in the list."""
    from agent_friday.core import _load_settings, _save_settings
    specs = configured(_load_settings() or {})
    if not any(spec.skill == skill for spec in specs):
        raise KeyError(skill)
    rows = [{"skill": s.skill, "heading": s.heading, "days": s.days,
             "enabled": bool(enabled) if s.skill == skill else s.enabled} for s in specs]
    _save_settings({SETTING: rows})
    return rows


def didnt_run_line(skill: str, reason: str) -> str:
    return "_Didn't run: %s %s._" % (skill, reason)


def _prompt(spec: Spec, skill: briefing_skills.BriefingSkill, now: datetime, context: str) -> str:
    return "\n".join([
        "Write the \"%s\" section of today's briefing, following the %s skill below." % (spec.heading, skill.name),
        "Today is %s %s." % (_WEEKDAYS[now.weekday()], now.strftime("%Y-%m-%d")),
        "Write markdown with no level-1 or level-2 heading (the heading is added for you).",
        "Cite each calendar entry you use by its id in square brackets, like [C1]. Never write a web address; "
        "links are added for you, and an item with no id is dropped.",
        "", context, "", "THE SKILL:", skill.text])


def run_one(spec: Spec, now: datetime, context: str, sources: dict, generate: Callable[[str], str]) -> Result:
    skill = briefing_skills.load(spec.skill)
    if not skill.installed:
        return Result(spec.skill, spec.heading, DID_NOT_RUN, reason=_REASONS[skill.state])
    try:
        text = generate(_prompt(spec, skill, now, context))
    except Exception as exc:
        _log.warning("section %s failed: %s", spec.skill, type(exc).__name__)
        return Result(spec.skill, spec.heading, DID_NOT_RUN, reason="failed (%s)" % type(exc).__name__)
    if not isinstance(text, str) or not text.strip():
        return Result(spec.skill, spec.heading, DID_NOT_RUN, reason="returned nothing")
    # Checked under a fixed events heading, so a custom heading cannot switch the checking off. A heading the model
    # wrote is made bold text first: as a heading it would end the events section and nothing under it would be
    # checked. The count line the validator appends is cut here and written once by `render`.
    flat = _HEADING_LINE.sub(lambda m: "**%s**" % m.group(1), text.strip())
    checked = briefing_links.validate("## Events\n%s" % flat, sources)
    body = checked.text.split("\n", 1)[1] if "\n" in checked.text else ""
    if checked.dropped_total:
        body = body[:body.rindex(briefing_links._count_line(checked.dropped_total))]
    links = tuple(sorted(link for link in briefing_links.known_links(sources) if link in body))
    return Result(spec.skill, spec.heading, RAN, markdown=body.strip(), dropped=checked.dropped_total, links=links)


_HEADING_LINE = re.compile(r"(?m)^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")


def _todays(now: datetime, specs) -> list:
    return [spec for spec in (configured() if specs is None else specs) if applies(spec, now)]


def run_all(now: datetime, calendar_rows, generate: Callable[[str], str], specs=None, first_index: int = 1) -> list:
    """Run every applicable section. Each gets the same numbered calendar windows; the ids it may cite are the
    entries the tools returned, and nothing else. A section whose days cannot be read says so instead of running."""
    context = briefing_events.build(now, calendar_rows, first_index=first_index)
    sources = briefing_links.collect(calendar=context.calendar)
    out = []
    for spec in _todays(now, specs):
        if _days(spec.days) is None:
            out.append(Result(spec.skill, spec.heading, DID_NOT_RUN,
                              reason="has days that could not be read (%s)" % spec.days[:40]))
        else:
            out.append(run_one(spec, now, context.text, sources, generate))
    return out


def fingerprint(now: datetime, specs=None) -> str:
    """What today's sections depend on besides the calendar: the sections that apply and each skill's state. Stored
    sections made under another fingerprint are made again (a section switched off, a skill just approved)."""
    return json.dumps([[s.skill, s.heading, s.days, briefing_skills.load(s.skill).state]
                       for s in _todays(now, specs)], sort_keys=True)


def render(results) -> str:
    """The sections as markdown, in order: a heading and its body, or a heading and the one-line 'Didn't run'."""
    blocks = []
    for r in results:
        body = r.markdown if r.state == RAN else didnt_run_line(r.skill, r.reason)
        if r.state == RAN and r.dropped:
            body += "\n\n" + briefing_links._count_line(r.dropped)
        blocks.append("## %s\n%s" % (r.heading, body or "_Nothing to show._"))
    return "\n\n".join(blocks)


# -- stored with the time they were made ---------------------------------------------------------

def _path() -> Path:
    from agent_friday.paths import friday_home
    return friday_home() / SECTIONS_FILE


def store(now: datetime, results, made_for: str = "") -> None:
    data = {"generated_at": now.isoformat(), "fingerprint": made_for,
            "results": [dict(r.__dict__, links=list(r.links)) for r in results]}
    target = _path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".sections-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(data, ensure_ascii=False, indent=1))
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def stored(now: datetime, made_for: Optional[str] = None):
    """(generated_at, results) when sections made today, within MAX_AGE (and, when `made_for` is given, under that
    fingerprint) are stored; else None."""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        made = datetime.fromisoformat(data["generated_at"])
        results = [Result(**dict(row, links=tuple(row.get("links") or ()))) for row in data["results"]]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if made.date() != now.date() or now - made > MAX_AGE or now < made:
        return None
    if made_for is not None and data.get("fingerprint") != made_for:
        return None
    return made, results


def age_words(made: datetime, now: datetime) -> str:
    minutes = max(0, int((now - made).total_seconds() // 60))
    return "just now" if minutes < 2 else "%d minutes ago" % minutes if minutes < 90 else "%d hours ago" % round(minutes / 60)


#: English day names written by code, so the stamp reads back the same under any system locale.
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_STAMP = re.compile(r"_Generated [A-Za-z]+ (\d{4}-\d{2}-\d{2} \d{2}:\d{2})_")


def stamp(now: datetime) -> str:
    return "_Generated %s %s_" % (_WEEKDAYS[now.weekday()], now.strftime("%Y-%m-%d %H:%M"))


def with_stamp(text: str, now: datetime) -> str:
    """The briefing with its one generation line: under its level-1 heading when it has one, else first. A stamp
    the model typed is removed, so the time shown is always the time code wrote it."""
    lines = [line for line in (text or "").split("\n") if not _STAMP.fullmatch(line.strip())]
    at = 1 if lines and lines[0].startswith("# ") else 0
    return "\n".join(lines[:at] + [stamp(now), ""] + lines[at:])


def generated_at(text: str) -> Optional[datetime]:
    """When a stored briefing was generated, from its stamp; None for one with no stamp (made before it had one)."""
    found = _STAMP.search(text or "")
    if not found:
        return None
    try:
        return datetime.strptime(found.group(1), "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def made_line(made: datetime, now: datetime) -> str:
    """Sections kept from an earlier run say when they were made; sections made in this run need no line."""
    if now - made < timedelta(minutes=2):
        return ""
    return "_Sections made %s (%s)_" % (made.strftime("%H:%M"), age_words(made, now))


def current(now: datetime, calendar_rows, generate: Callable[[str], str], specs=None, first_index: int = 1):
    """Today's sections and when they were made: the stored ones when they are fresh, else made now and stored."""
    made_for = fingerprint(now, specs)
    fresh = stored(now, made_for)
    if fresh is not None:
        return fresh
    results = run_all(now, calendar_rows, generate, specs, first_index)
    store(now, results, made_for)
    return now, results
