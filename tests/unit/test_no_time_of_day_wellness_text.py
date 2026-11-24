"""No prompt or template the daily rundown is built from steers the model to remark on the hour or the owner's sleep.

The owner does not want bedtime or time-of-day comments in the rundown. What waits for the morning is the quiet-hours
setting, a program; a sentence in a prompt or in the profile about when the owner works or sleeps only invites the
remark. The rule is checked here over every template that reaches the briefing and the profile: the briefing job and
schedule prompts, the setup profile's notes for every answer the setup offers, and the voice rundown's wording."""
from __future__ import annotations

import itertools
import re

from agent_friday.services import scheduler, setup_chat_copy, setup_profile

#: Phrases that steer a remark about the hour, the owner's sleep or their working hours.
FORBIDDEN = re.compile(
    r"\b(bed ?time|go to bed|get some (sleep|rest)|good night.s sleep|sleep (schedule|well|tight)|late at night|"
    r"it.?s (getting )?late|time of day|time-of-day|off[- ]hours|office hours|works (early|late)|late-day|"
    r"night owl|early bird|wind down|stay up)\b",
    re.IGNORECASE)


def _hits(text: str):
    return [m.group(0) for m in FORBIDDEN.finditer(text or "")]


def test_the_checker_finds_the_phrases_it_exists_for():
    for text in ("Works late; don't treat evenings as off-hours.", "Works normal office hours.",
                 "keep late-day messages for the morning", "It's late, you should go to bed.",
                 "remark on the time of day"):
        assert _hits(text), text
    assert not _hits("Protect focus time: batch non-urgent updates.")


def test_no_setup_profile_note_steers_a_remark_about_the_hour_for_any_answer_the_setup_offers():
    options = {q[0]: list(q[2]) for q in setup_chat_copy.QUESTIONS}
    rhythm = options["rhythm"] + ["deep work before lunch, meetings after", "I work nights", "9 to 5", "early mornings"]
    for answer in rhythm:
        notes = setup_profile.deterministic_style({"rhythm": {"text": answer}})["notes"]
        assert not [h for n in notes for h in _hits(n)], (answer, notes)
    every = {qid: opts for qid, opts in options.items()}
    for combo in itertools.islice(itertools.product(*[opts[:2] for opts in every.values()]), 64):
        answers = {qid: {"text": text} for qid, text in zip(every, combo)}
        notes = setup_profile.deterministic_style(answers)["notes"]
        assert not [h for n in notes for h in _hits(n)], (combo, notes)


def test_the_work_rhythm_answer_is_not_handed_to_a_model():
    answers = {"rhythm": {"text": "I work late nights"}, "address": {"text": "casual"}}
    assert list(setup_profile._answers_for_model(answers).values()) == ["casual"]


def test_no_default_schedule_or_briefing_job_prompt_steers_a_remark_about_the_hour():
    prompts = [s.get("task", {}).get("prompt", "") for s in scheduler._DEFAULT_AGENT_SCHEDULES]
    prompts.append(scheduler._AFTERNOON_BRIEFING_JOB.instructions)
    assert any(prompts)
    for prompt in prompts:
        assert not _hits(prompt), (prompt[:80], _hits(prompt))


def test_the_voice_rundown_wording_has_no_time_of_day_remark():
    import inspect
    from agent_friday.routes import voice_context
    assert not _hits(inspect.getsource(voice_context))
