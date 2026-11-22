"""A wiki edit whose old_value spans lines lands on a CRLF page, and the page keeps its own line endings.

read_wiki shows a page with its text as the model sees it: line breaks written as plain newlines by the model's
own old_value, and every date followed by a code-computed weekday tag (services/clock.annotate_weekdays, display
only). A page saved with CRLF endings never matched a two-line old_value, so the edit was refused as stale even
though it matched what the model read. The comparison now ignores the ending style and display-only weekday tags,
and the edit is written back in the page's own endings, touching nothing else. Synthetic pages only.
"""
from __future__ import annotations

import pytest

from agent_friday.services import clock
from agent_friday.services import wiki_engine as we

PAGE = "notes/synthetic-crlf.md"
LINES = ["# Synthetic Page", "", "## People", "- Alex: likes tea", "- Sam: likes coffee", "- Kim: likes juice",
         "", "## Dates", "- Dentist: 2026-11-24", "- Trip: 2026-12-01", ""]


@pytest.fixture(autouse=True)
def wiki(tmp_path, monkeypatch):
    root = tmp_path / "wiki"
    (root / "notes").mkdir(parents=True)
    monkeypatch.setattr(we, "WIKI_DIR", root)
    monkeypatch.setattr(we, "WIKI_PENDING_FILE", tmp_path / "wiki-pending.json")
    monkeypatch.setattr(we, "_wiki_mirror_dir", lambda: None)
    monkeypatch.setattr(we, "_wiki_encrypted_sections", lambda: set())
    yield root


def _put(root, text, eol):
    data = text.replace("\n", eol).encode("utf-8")
    (root / PAGE).write_bytes(data)
    return data


def _page(root):
    return (root / PAGE).read_bytes()


def _edit(old, new):
    return we.apply_wiki_proposal_result({"file": PAGE, "mode": "edit", "old_value": old, "new_value": new})


TEXT = "\n".join(LINES)
TWO_OLD = "- Sam: likes coffee\n- Kim: likes juice"
TWO_NEW = "- Sam: likes coffee\n- Kim: likes juice\n- Lee: likes water"


def test_a_two_line_edit_lands_on_a_crlf_page_and_the_page_stays_crlf(wiki):
    before = _put(wiki, TEXT, "\r\n")
    out = _edit(TWO_OLD, TWO_NEW)
    assert out["status"] == "applied", out
    after = _page(wiki)
    assert after == before.replace(b"- Kim: likes juice\r\n", b"- Kim: likes juice\r\n- Lee: likes water\r\n")
    assert b"\n" not in after.replace(b"\r\n", b""), "a bare newline was written into a CRLF page"


def test_the_plan_for_a_crlf_page_is_ready_not_stale(wiki):
    _put(wiki, TEXT, "\r\n")
    plan = we.plan_wiki_proposal({"file": PAGE, "mode": "edit", "old_value": TWO_OLD, "new_value": TWO_NEW})
    assert plan["status"] == "ready", plan


def test_a_one_line_edit_on_a_crlf_page_still_works_and_changes_nothing_else(wiki):
    before = _put(wiki, TEXT, "\r\n")
    assert _edit("- Alex: likes tea", "- Alex: likes milk")["status"] == "applied"
    assert _page(wiki) == before.replace(b"likes tea", b"likes milk")


def test_a_two_line_edit_on_an_lf_page_is_unchanged(wiki):
    before = _put(wiki, TEXT, "\n")
    assert _edit(TWO_OLD, TWO_NEW)["status"] == "applied"
    assert _page(wiki) == before.replace(b"- Kim: likes juice\n", b"- Kim: likes juice\n- Lee: likes water\n")


def test_a_page_with_mixed_endings_changes_only_the_edited_lines(wiki):
    before = (b"# Synthetic Page\n\n## People\r\n- Alex: likes tea\r\n- Sam: likes coffee\r\n"
              b"- Kim: likes juice\r\n\n## Dates\n- Dentist: 2026-11-24\n")
    (wiki / PAGE).write_bytes(before)
    assert _edit(TWO_OLD, "- Sam: likes soup\n- Kim: likes juice")["status"] == "applied"
    assert _page(wiki) == before.replace(b"likes coffee", b"likes soup")


def test_an_old_value_that_is_really_gone_is_still_stale_and_writes_nothing(wiki):
    before = _put(wiki, TEXT, "\r\n")
    out = _edit("- Sam: likes coffee\n- Kim: likes cake", "- Sam: x\n- Kim: y")
    assert out["status"] == "stale", out
    assert _page(wiki) == before


def test_an_old_value_that_occurs_twice_is_still_ambiguous(wiki):
    before = _put(wiki, TEXT + "\n- Sam: likes coffee\n- Kim: likes juice\n", "\r\n")
    assert _edit(TWO_OLD, "x\ny")["status"] == "ambiguous"
    assert _page(wiki) == before


def test_weekday_tags_the_reader_added_do_not_make_an_edit_stale_or_reach_the_page(wiki):
    before = _put(wiki, TEXT, "\r\n")
    shown = clock.annotate_weekdays(TEXT)                       # what read_wiki hands the model
    assert "2026-11-24 (Tuesday)" in shown
    old = "- Dentist: 2026-11-24 (Tuesday)\n- Trip: 2026-12-01 (Tuesday)"
    new = "- Dentist: 2026-11-25 (Wednesday)\n- Trip: 2026-12-01 (Tuesday)"
    assert _edit(old, new)["status"] == "applied"
    assert _page(wiki) == before.replace(b"2026-11-24", b"2026-11-25"), "a display tag was written into the page"


def test_a_weekday_the_page_itself_holds_is_left_alone(wiki):
    text = TEXT.replace("- Dentist: 2026-11-24", "- Dentist: 2026-11-24 (Tuesday)")
    before = _put(wiki, text, "\r\n")
    assert _edit("- Dentist: 2026-11-24 (Tuesday)\n- Trip: 2026-12-01",
                 "- Dentist: 2026-11-26 (Thursday)\n- Trip: 2026-12-01")["status"] == "applied"
    assert _page(wiki) == before.replace(b"2026-11-24 (Tuesday)", b"2026-11-26 (Thursday)")


def test_a_miss_on_an_unchanged_page_says_not_found_as_written_and_shows_the_nearest_text(wiki):
    _put(wiki, TEXT, "\r\n")
    out = _edit("- Sam: likes coffee\n- Kim: likes cake", "- Sam: x\n- Kim: y")
    assert out["status"] == "stale"
    assert "not found as written" in out["message"], out["message"]
    assert "changed" not in out["message"], "an unchanged page was reported as changed"
    assert "- Kim: likes juice" in out["message"], "the nearest text was not shown"


def test_the_page_is_called_changed_only_when_its_hash_differs_from_the_one_read(wiki):
    _put(wiki, TEXT, "\r\n")
    out = we.apply_wiki_proposal_result({"file": PAGE, "mode": "edit", "old_value": "- Alex: likes tea",
                                         "new_value": "- Alex: likes milk"}, before_sha256="0" * 64)
    assert out["status"] == "stale" and "changed" in out["message"].lower(), out
    assert b"likes tea" in _page(wiki), "a page that differs from the read was written anyway"
