"""DOCX heading styles become the section tree; text before the first heading
is not left outside it."""
from __future__ import annotations

from agent_friday.services.library import extract, structure
from tests.library_fixtures import make_docx


def test_heading_styles_become_nested_sections(tmp_path):
    p = tmp_path / "report.docx"
    p.write_bytes(make_docx([("Normal", "A cover note."), ("Heading1", "Findings"), ("Normal", "One."),
                             ("Heading2", "Method"), ("Normal", "Two."), ("Heading1", "Appendix"),
                             ("Normal", "Three.")]))
    res = extract.extract_document(p)
    secs = structure.build_sections(res["blocks"], res["title"])
    names = [(s["heading"], s["level"], s["parent"]) for s in secs]
    assert names == [("Opening", 1, None), ("Findings", 1, None), ("Method", 2, 1), ("Appendix", 1, None)]
    assert [b["text"] for b in secs[0]["blocks"]] == ["A cover note."]
    assert [b["text"] for b in secs[2]["blocks"]] == ["Two."]


def test_a_document_without_headings_gets_synthetic_sections(tmp_path):
    p = tmp_path / "plain.txt"
    p.write_text("\n\n".join(f"Paragraph {i} " + "word " * 120 for i in range(80)), encoding="utf-8")
    res = extract.extract_document(p)
    secs = structure.build_sections(res["blocks"], res["title"])
    assert len(secs) >= 2 and all(s["blocks"] for s in secs)
    assert all(s["parent"] is None for s in secs)
    assert sum(len(s["blocks"]) for s in secs) == len(res["blocks"])


def test_markdown_ignores_hash_lines_inside_code_fences(tmp_path):
    p = tmp_path / "n.md"
    p.write_text("# Real\n\ntext\n\n```\n# not a heading\n```\n", encoding="utf-8")
    res = extract.extract_document(p)
    assert [b["text"] for b in res["blocks"] if b["kind"] == "heading"] == ["Real"]
