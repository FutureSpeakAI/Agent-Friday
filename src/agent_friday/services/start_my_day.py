"""Start My Day speaks today's WRITTEN briefing, the same one the News workspace shows.

* today's stored briefing (written through briefing_files by the one generator, routes/news.generate_briefing, whose
  finishing step applies the daily-briefing-format skill, runs the section skills with their "Didn't run" lines,
  enforces the links and stamps "Generated <weekday date time>") is used when its stamp is under MAX_AGE old;
* otherwise the generator runs now, and the briefing it wrote is used;
* the spoken prompt is built from that text with its links taken out, says when it was generated and how old it is,
  and tells the model to say every "Didn't run" line and to add nothing that is not in it.

tests/unit/test_start_my_day.py and tests/api/test_start_my_day_route.py hold each rule.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from agent_friday.services import briefing_sections

#: A stored briefing older than this is made again when Start My Day is pressed (the same age the sections keep).
MAX_AGE: timedelta = briefing_sections.MAX_AGE
#: Held while Start My Day finds or makes today's briefing, so two presses never run the generator twice: the second
#: waits, then finds the briefing the first one wrote.
GENERATING = threading.Lock()


@dataclass(frozen=True)
class Briefing:
    path: Path
    content: str
    generated_at: datetime


def fresh(now: datetime, directory: Optional[Path] = None) -> Optional[Briefing]:
    """Today's newest stamped markdown briefing when it was generated within MAX_AGE, else None. A briefing with no
    stamp (written before briefings had one), an HTML one, or one from another day is never fresh."""
    from agent_friday.services import briefing_files
    best = None
    for path in briefing_files.dated(directory):
        if path.suffix != ".md" or path.stem[:10] != now.strftime("%Y-%m-%d"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        made = briefing_sections.generated_at(text)
        if made is None or not timedelta(0) <= now - made <= MAX_AGE:
            continue
        if best is None or made > best.generated_at:
            best = Briefing(path, text, made)
    return best


_MD_LINK = re.compile(r"\[([^\]\n]+)\]\(https?://[^)\s]+\)")
_BARE_URL = re.compile(r"<?\bhttps?://[^\s)>\]]+>?")
#: Link words that only say what the link opens; spoken, they are noise. A story's outlet name is kept.
_LINK_ONLY = ("Open email", "Calendar event", "Event page")
_SOURCES = re.compile(r"(?ms)^##\s+Sources\s*$.*?(?=^#{1,2}\s|\Z)")
_EMPTY_PARENS = re.compile(r"\s*\(\s*(?:,\s*)*\)")


def speakable(text: str) -> str:
    """The briefing as words to say: the Sources list goes, a link keeps its words (so a story's outlet is named)
    except words that only say what a link opens, a bare address goes, and citation ids go
    (services/news_links.for_speech)."""
    from agent_friday.services.news_links import for_speech
    out = _SOURCES.sub("", text or "")
    out = _MD_LINK.sub(lambda m: "" if m.group(1) in _LINK_ONLY else m.group(1), out)
    out = _BARE_URL.sub("", out)
    return for_speech(_EMPTY_PARENS.sub("", out))


def spoken_prompt(briefing: Briefing, now: datetime, tasks: str = "") -> str:
    """The opening voice turn: the written briefing without its links, when it was made, and how to read it."""
    when = briefing.generated_at.strftime("%A at %H:%M")
    age = briefing_sections.age_words(briefing.generated_at, now)
    return (
        "[START MY DAY: the user asked for their morning voice briefing.]\n\n"
        "Here is today's written briefing, generated %s (%s). It is the same briefing the News workspace is "
        "showing now.\n\n" % (when, age)
        + speakable(briefing.content)
        + (("\n\n" + tasks) if tasks else "")
        + "\n\nRead it aloud in its own order, conversationally and concisely. Say once, at the start, when it was "
          "generated. When a line says \"Didn't run\", say that section did not run and why, in a few words. Do not "
          "add any item that is not in the briefing. Speak naturally in short sentences, no markdown, and pause "
          "between sections so I can interrupt."
    )


def failed_prompt(reason: str, live_context: str, tasks: str = "") -> str:
    """When no written briefing could be made: say so first, then fall back to the live data, marked as unchecked."""
    return (
        "[START MY DAY: the user asked for their morning voice briefing.]\n\n"
        "Today's written briefing could not be made (%s). Say that first, in one sentence. Then give a short spoken "
        "summary from the live data below, and say its items are unchecked.\n\n" % reason
        + (live_context or "(no live data available)")
        + (("\n\n" + tasks) if tasks else "")
    )
