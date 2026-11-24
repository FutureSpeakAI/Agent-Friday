"""A date inside a path or a file name gets no weekday tag; a date in prose still does.

Every tool result passes through clock.annotate_weekdays, so the model never works a weekday out itself. It also
tagged dates inside paths: the cut-off notice told the model to read
".../tool-output/2026-11-22 (Sunday)/browse_web-1.txt", a file that does not exist, so a cut result could not be
paged back on Linux and macOS (CI on #1118). The same broke any dated folder or file name a tool returns.
"""
from __future__ import annotations

import pytest

from agent_friday.services.clock import annotate_weekdays


@pytest.mark.parametrize("text", [
    "/home/u/.friday/tool-output/2026-11-22/browse_web-1.txt",
    r"C:\Users\u\.friday\tool-output\2026-11-22\browse_web-1.txt",
    "notes/2026-11-22.md",
    "backup_2026-11-22.zip",
    "see ~/journal/2026-11-22/entry for it",
    "file:///srv/reports/11/22/2026/summary.txt",
])
def test_a_date_inside_a_path_or_file_name_is_left_as_it_is(text):
    assert annotate_weekdays(text) == text


@pytest.mark.parametrize("text, want", [
    ("The meeting is on 2026-11-22.", "The meeting is on 2026-11-22 (Sunday)."),
    ("due 2026-11-23, then 11/24/2026", "due 2026-11-23 (Monday), then 11/24/2026 (Tuesday)"),
    ("(2026-11-22)", "(2026-11-22 (Sunday))"),
])
def test_a_date_in_prose_still_carries_its_weekday(text, want):
    assert annotate_weekdays(text) == want


def test_a_cut_off_notice_names_a_path_that_exists_after_annotation(tmp_path):
    folder = tmp_path / "tool-output" / "2026-11-22"
    folder.mkdir(parents=True)
    f = folder / "browse_web-1.txt"
    f.write_text("x", encoding="utf-8")
    notice = annotate_weekdays("[cut: the rest is in %s; read it with read_file]" % f.as_posix())
    assert f.as_posix() in notice
