"""A PDF block keeps its page and its box, so a footnote can land on a paragraph."""
from __future__ import annotations

from agent_friday.services.library import extract
from tests.library_fixtures import make_pdf


def test_blocks_carry_page_and_a_box_inside_that_page(tmp_path):
    p = tmp_path / "deposition.pdf"
    p.write_bytes(make_pdf([["# Direct examination", "The witness arrived at nine."],
                            ["# Cross examination", "The lease was signed in May.", "Rent was due monthly."],
                            ["Closing remarks on page three."]]))
    res = extract.extract_document(p)
    assert res["pages"] == 3 and res["kind"] == "pdf"
    by_text = {b["text"]: b for b in res["blocks"]}
    lease = by_text["The lease was signed in May."]
    assert lease["page"] == 2
    x0, y0, x1, y1 = lease["bbox"]
    assert 0 <= x0 < x1 <= 612 and 0 <= y0 < y1 <= 792
    assert by_text["Closing remarks on page three."]["page"] == 3
    assert by_text["Cross examination"]["kind"] == "heading"
    assert by_text["Cross examination"]["page"] == 2


def test_separate_paragraphs_stay_separate_blocks(tmp_path):
    p = tmp_path / "a.pdf"
    p.write_bytes(make_pdf([["First paragraph.", "Second paragraph."]]))
    texts = [b["text"] for b in extract.extract_document(p)["blocks"]]
    assert texts == ["First paragraph.", "Second paragraph."]


def test_a_pdf_with_javascript_reads_as_plain_text(tmp_path):
    p = tmp_path / "evil.pdf"
    p.write_bytes(make_pdf([["Plain words."]], javascript=True))
    res = extract.extract_document(p)
    assert [b["text"] for b in res["blocks"]] == ["Plain words."]
    assert "alert" not in str(res)
