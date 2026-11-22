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


def _days(spec_days: str) -> set:
    """The weekday numbers (Monday 0) a days spec covers: "daily", "fri-sun", "mon,wed", or a single day."""
    text = str(spec_days or "daily").strip().lower()
    if text == "daily":
        return set(range(7))
    out = set()
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            low, _, high = part.partition("-")
            if low in DAY_NAMES and high in DAY_NAMES:
                a, b = DAY_NAMES.index(low), DAY_NAMES.index(high)
                out |= {d % 7 for d in range(a, b + 1 if b >= a else b + 8)}
        elif part in DAY_NAMES:
            out.add(DAY_NAMES.index(part))
    return out


def applies(spec: Spec, now: datetime) -> bool:
    return spec.enabled and now.weekday() in _days(spec.days)


def configured(settings: Optional[dict] = None) -> list:
    """The section list from settings, or the default. An entry that is not well formed is skipped, not guessed at."""
    if settings is None:
        try:
            from agent_friday.core import _load_settings
            settings = _load_settings() or {}
        except Exception:
            settings = {}
    raw = settings.get(SETTING)
    if not isinstance(raw, list):
        raw = list(DEFAULT_SECTIONS)
    out = []
    for entry in raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("skill"), str) or not entry["skill"].strip():
            continue
        skill = entry["skill"].strip()
        out.append(Spec(skill=skill, heading=str(entry.get("heading") or skill)[:80],
                        days=str(entry.get("days") or "daily"), enabled=entry.get("enabled") is not False))
    return out


def didnt_run_line(skill: str, reason: str) -> str:
    return "_Didn't run: %s %s._" % (skill, reason)


def _prompt(spec: Spec, skill: briefing_skills.BriefingSkill, now: datetime, context: str) -> str:
    return "\n".join([
        "Write the \"%s\" section of today's briefing, following the %s skill below." % (spec.heading, skill.name),
        "Today is %s." % now.strftime("%A %Y-%m-%d"),
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
    # Checked under a fixed events heading, so a custom heading cannot switch the checking off; the count line the
    # validator appends is cut here and written once by `render`.
    checked = briefing_links.validate("## Events\n%s" % text.strip(), sources)
    body = checked.text.split("\n", 1)[1] if "\n" in checked.text else ""
    if checked.dropped_total:
        body = body[:body.rindex(briefing_links._count_line(checked.dropped_total))]
    return Result(spec.skill, spec.heading, RAN, markdown=body.strip(), dropped=checked.dropped_total)


def run_all(now: datetime, calendar_rows, generate: Callable[[str], str], specs=None, first_index: int = 1) -> list:
    """Run every applicable section. Each gets the same numbered calendar windows; the ids it may cite are the
    entries the tools returned, and nothing else."""
    context = briefing_events.build(now, calendar_rows, first_index=first_index)
    sources = briefing_links.collect(calendar=context.calendar)
    return [run_one(spec, now, context.text, sources, generate)
            for spec in (configured() if specs is None else specs) if applies(spec, now)]


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


def store(now: datetime, results) -> None:
    data = {"generated_at": now.isoformat(), "results": [r.__dict__ for r in results]}
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


def stored(now: datetime):
    """(generated_at, results) when sections made today, within MAX_AGE, are stored; else None."""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        made = datetime.fromisoformat(data["generated_at"])
        results = [Result(**row) for row in data["results"]]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if made.date() != now.date() or now - made > MAX_AGE or now < made:
        return None
    return made, results


def age_words(made: datetime, now: datetime) -> str:
    minutes = max(0, int((now - made).total_seconds() // 60))
    return "just now" if minutes < 2 else "%d minutes ago" % minutes if minutes < 90 else "%d hours ago" % round(minutes / 60)


def stamp(now: datetime) -> str:
    return "_Generated %s_" % now.strftime("%A %Y-%m-%d %H:%M")


def current(now: datetime, calendar_rows, generate: Callable[[str], str], specs=None, first_index: int = 1):
    """Today's sections and when they were made: the stored ones when they are fresh, else made now and stored."""
    fresh = stored(now)
    if fresh is not None:
        return fresh
    results = run_all(now, calendar_rows, generate, specs, first_index)
    store(now, results)
    return now, results
