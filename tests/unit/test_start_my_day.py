"""Start My Day speaks today's written briefing: which stored briefing counts as fresh, what the spoken prompt says
about when it was generated and about sections that did not run, and that no link or address is read aloud.
Synthetic briefings only."""
from datetime import datetime, timedelta

import pytest

from agent_friday.services import briefing_sections as bsec
from agent_friday.services import start_my_day as smd

NOW = datetime(2026, 11, 22, 9, 30)
BODY = ("# Briefing\n{stamp}\n\n## Today's Calendar\n- Planning at ten. ([Calendar event](https://calendar.example.test/e/1))\n"
        "## Email\n- Dana wants to talk. ([Open email](https://mail.google.com/mail/u/0/#all/18f3a1b2c3d4e5f6))\n"
        "- See https://example.test/raw for the agenda.\n"
        "## Events\n_Didn't run: events-horizon awaiting approval._\n")


def write(folder, name, made):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(BODY.format(stamp=bsec.stamp(made) if made else ""), encoding="utf-8")


@pytest.mark.parametrize("made,fresh", [
    (NOW - timedelta(minutes=5), True),
    (NOW - smd.MAX_AGE, True),
    (NOW - smd.MAX_AGE - timedelta(minutes=1), False),
    (NOW + timedelta(minutes=5), False),             # a stamp from the future is not trusted
    (None, False),                                   # a briefing written before briefings had a stamp
])
def test_only_a_stamped_briefing_from_the_last_six_hours_is_fresh(tmp_path, made, fresh):
    write(tmp_path, "2026-11-22.md", made)
    got = smd.fresh(NOW, tmp_path)
    assert (got is not None) is fresh
    if fresh:
        assert got.path.name == "2026-11-22.md" and got.generated_at == made.replace(second=0, microsecond=0)


def test_yesterdays_briefing_and_an_html_one_are_never_fresh(tmp_path):
    write(tmp_path, "2026-11-21.md", NOW - timedelta(hours=1))           # a stamp that is recent, on another day's file
    write(tmp_path, "2026-11-22.html", NOW - timedelta(minutes=5))
    assert smd.fresh(NOW, tmp_path) is None


def test_the_newest_stamp_of_the_day_wins(tmp_path):
    write(tmp_path, "2026-11-22.md", NOW - timedelta(hours=3))
    write(tmp_path, "2026-11-22-pm.md", NOW - timedelta(minutes=10))
    assert smd.fresh(NOW, tmp_path).path.name == "2026-11-22-pm.md"


def test_the_spoken_prompt_says_when_it_was_generated_and_reads_no_link_or_address(tmp_path):
    write(tmp_path, "2026-11-22.md", NOW - timedelta(minutes=30))
    prompt = smd.spoken_prompt(smd.fresh(NOW, tmp_path), NOW, tasks="## Tasks\n- File the report")
    assert "generated Sunday at 09:00 (30 minutes ago)" in prompt
    assert "http" not in prompt and "mail.google.com" not in prompt and "Open email" not in prompt
    assert "Planning at ten." in prompt and "Dana wants to talk." in prompt and "File the report" in prompt
    assert "Didn't run: events-horizon awaiting approval" in prompt
    assert 'When a line says "Didn\'t run"' in prompt and "Do not add any item that is not in the briefing" in prompt


def test_when_no_briefing_could_be_made_the_prompt_says_so_first():
    prompt = smd.failed_prompt("no provider is set up", "## Live News\n- A story")
    head = prompt.split("\n\n")[1]
    assert "could not be made (no provider is set up)" in head and "unchecked" in head
    assert "A story" in prompt


def test_speech_keeps_a_storys_outlet_and_skips_the_sources_list():
    text = ("# Briefing\n## News\n- The council passed the budget. ([Examplewire](https://examplewire.test/a))\n"
            "## Sources\n- [Council passes the budget](https://examplewire.test/a) — Examplewire\n"
            "## Events\n- Quiz night. ([Calendar event](https://calendar.example.test/e/1))\n")
    said = smd.speakable(text)
    assert "(Examplewire)" in said and "## Sources" not in said and "Council passes the budget" not in said
    assert "Quiz night." in said and "Calendar event" not in said and "http" not in said
