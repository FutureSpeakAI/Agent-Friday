"""The skills a briefing applies, and saying so out loud when one is not there.

Start My Day applies the `daily-briefing-format` skill and builds its events section with the `events-horizon`
skill. A skill can be installed, waiting (saved through learn_skill and queued for testing, not approved yet),
missing, or unreadable. The briefing never falls back silently: when a skill is not installed, the briefing starts
with a visible line that names the skill and why, written by code and not left to the model.

tests/unit/test_briefing_skills.py holds each state and the visible line.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

_log = logging.getLogger(__name__)

FORMAT_SKILL = "daily-briefing-format"
EVENTS_SKILL = "events-horizon"
INSTALLED, WAITING, MISSING, UNREADABLE = "installed", "waiting", "missing", "unreadable"
_WHY = {WAITING: "is saved but not approved yet (it is queued for testing)",
        MISSING: "is not installed",
        UNREADABLE: "could not be read"}


@dataclass(frozen=True)
class BriefingSkill:
    name: str
    state: str
    text: str = ""

    @property
    def installed(self) -> bool:
        return self.state == INSTALLED

    @property
    def notice(self) -> str:
        """The visible line for a skill that is not installed; empty for one that is."""
        if self.installed:
            return ""
        return "> ⚠ The %s skill %s, so this briefing was made without it." % (self.name, _WHY[self.state])


def _queued(name: str) -> bool:
    from agent_friday.services import trials
    for candidate in trials.candidates("queued"):
        subject = candidate.get("subject") or {}
        if subject.get("kind") == "skill" and subject.get("id") == name:
            return True
    return False


def load(name: str) -> BriefingSkill:
    """The skill by name: installed with its instructions, waiting for its trial, missing, or unreadable."""
    try:
        from agent_friday import skill_registry
        skill = skill_registry.get_skill(name)
    except Exception:
        _log.exception("could not read the %s skill", name)
        return BriefingSkill(name, UNREADABLE)
    if skill is not None:
        text = (getattr(skill, "body", "") or "").strip()
        return BriefingSkill(name, INSTALLED, text) if text else BriefingSkill(name, UNREADABLE)
    try:
        return BriefingSkill(name, WAITING if _queued(name) else MISSING)
    except Exception:
        _log.exception("could not read the saved-for-testing list")
        return BriefingSkill(name, MISSING)


def notices(skills) -> str:
    """The visible lines for every skill that is not installed, one per line, or ""."""
    return "\n".join(s.notice for s in skills if not s.installed)


def instructions(skill: BriefingSkill, heading: str) -> str:
    """The skill's instructions as a prompt block, or "" when it is not installed."""
    return "%s\n%s" % (heading, skill.text) if skill.installed else ""
