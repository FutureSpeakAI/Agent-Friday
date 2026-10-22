"""Artifact checks for the release gate (north star §34.12-1): validity,
bounds, formulas, charts, fonts and accessibility of the files Friday makes,
read the way a reader's program reads them (services/artifact_qa). Synthetic
fixtures built here; no officecli, no network."""
from __future__ import annotations

import struct
import zipfile
import zlib

from agent_friday.services import artifact_qa as qa

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" ' \
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"'
CT = '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'


def _zip(path, parts):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", CT)
        for name, body in parts.items():
            z.writestr(name, body)
    return path


def _errors(res):
    return [f for f in res["findings"] if f["severity"] == "error"]


def _checks(res, severity=None):
    return {f["check"] for f in res["findings"] if severity is None or f["severity"] == severity}


# ── Word ───────────────────────────────────────────────────────────────────

def _docx(tmp_path, body, *, styles=None, fonts=None, name="doc.docx"):
    parts = {"word/document.xml": f'<w:document {W}><w:body>{body}</w:body></w:document>'}
    if styles is not None:
        parts["word/styles.xml"] = f'<w:styles {W}>{styles}</w:styles>'
    if fonts is not None:
        parts["word/fontTable.xml"] = f'<w:fonts {W}>{fonts}</w:fonts>'
    return _zip(tmp_path / name, parts)


def _para(text, style=None, font=None):
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    rpr = f'<w:rPr><w:rFonts w:ascii="{font}" w:hAnsi="{font}"/></w:rPr>' if font else ""
    return f'<w:p>{ppr}<w:r>{rpr}<w:t>{text}</w:t></w:r></w:p>'


def _picture(alt):
    return (f'<w:p><w:r><w:drawing><wp:inline><wp:docPr id="1" name="Picture 1" descr="{alt}"/>'
            f'</wp:inline></w:drawing></w:r></w:p>')


LANG = '<w:docDefaults><w:rPrDefault><w:rPr><w:lang w:val="en-GB"/></w:rPr></w:rPrDefault></w:docDefaults>'


def test_a_sound_word_document_passes(tmp_path):
    body = _para("Report", "Heading1") + _para("Findings", "Heading2", font="Calibri") + _picture("A bar chart")
    res = qa.check(_docx(tmp_path, body, styles=LANG))
    assert res["checked"] and res["ok"], res
    assert res["findings"] == []


def test_a_word_document_with_problems_says_each(tmp_path):
    body = (_para("Dear {{name}}", "Heading1") + _para("Detail", "Heading3", font="Fancy Script")
            + _picture(""))
    res = qa.check(_docx(tmp_path, body))
    assert not res["ok"]
    msgs = " | ".join(f["message"] for f in res["findings"])
    assert "placeholder" in msgs and "alt text" in msgs                  # errors
    assert "level 1 to 3" in msgs and "Fancy Script" in msgs              # warnings
    assert "language is not set" in msgs


def test_an_embedded_font_is_not_flagged(tmp_path):
    fonts = '<w:font w:name="Fancy Script"><w:embedRegular w:fontKey="{X}"/></w:font>'
    res = qa.check(_docx(tmp_path, _para("Hi", font="Fancy Script"), styles=LANG, fonts=fonts))
    assert "fonts" not in _checks(res)


def test_a_broken_package_is_invalid_not_a_crash(tmp_path):
    p = tmp_path / "broken.docx"
    p.write_bytes(b"this is not a zip file")
    res = qa.check(p)
    assert not res["ok"] and _checks(res) == {"validity"}
    q = _zip(tmp_path / "dtd.docx", {"word/document.xml": '<!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>'})
    assert not qa.check(q)["ok"]
    # A DTD after a long comment is refused too: the check is the parser's, not a peek.
    late = _zip(tmp_path / "late.docx", {"word/document.xml": "<!--" + "x" * 10000 + '-->'
                                         '<!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>'})
    res = qa.check(late)
    assert not res["ok"] and "DTD" in res["findings"][0]["message"]


def test_hostile_formulas_and_styles_are_checked_in_linear_time(tmp_path):
    import time as _t
    junk = "'" + "a" * 8000                          # backtracking bait, no closing quote or !
    cells = f'<c r="A1"><f>{junk}</f><v>0</v></c><c r="A2"><f>{"A" * 9000}</f><v>0</v></c>'
    t0 = _t.time()
    res = qa.check(_xlsx(tmp_path, cells))
    assert _t.time() - t0 < 2.0
    assert any("longer than Excel allows" in f["message"] for f in res["findings"])
    page = tmp_path / "p.html"
    page.write_text("<html lang='en'><title>t</title><style>" + "@font-face{" * 20000 + "url(" * 20000
                    + "</style></html>", encoding="utf-8")
    t0 = _t.time()
    qa.check(page)
    assert _t.time() - t0 < 2.0
    titled = tmp_path / "t.html"
    titled.write_text("<html><title>" + "a<b>" * 100000 + "</title></html>", encoding="utf-8")
    t0 = _t.time()
    qa.check(titled)
    assert _t.time() - t0 < 5.0


def test_the_isolated_check_gives_the_same_answer_and_gives_up_on_time(tmp_path):
    p = tmp_path / "site.html"
    p.write_text("<html><title>" + "<b>a" * 50000 + "</title><img src=x></html>", encoding="utf-8")
    inline = qa.check(p)
    isolated = qa.check_isolated(p)
    assert isolated == inline and not isolated["ok"]
    late = qa.check_isolated(p, timeout=0.001)
    assert late["checked"] is False and "did not finish" in late["findings"][0]["message"]


def test_formula_references_are_read_exactly():
    refs = list(qa._formula_refs("SUM('My ''Q'' Sheet'!$A$1:B2)+Other!C3+\"Fake!D4\"+LOG10(5)"))
    assert refs == [("My 'Q' Sheet", "A", "1"), (None, "B", "2"), ("Other", "C", "3")]


# ── Excel ──────────────────────────────────────────────────────────────────

S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" ' \
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
C = 'xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"'


def _xlsx(tmp_path, cells, *, chart=None, name="book.xlsx"):
    parts = {
        "xl/workbook.xml": f'<workbook {S}><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/>'
                           f'<sheet name="Data Two" sheetId="2" r:id="rId2"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                                      'relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
                                      '<Relationship Id="rId2" Target="/xl/worksheets/sheet2.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": f'<worksheet {S}><sheetData><row r="1">{cells}</row></sheetData></worksheet>',
        "xl/worksheets/sheet2.xml": f'<worksheet {S}><sheetData><row r="1"><c r="A1"><v>3</v></c></row>'
                                    f'</sheetData></worksheet>',
    }
    if chart is not None:
        parts["xl/charts/chart1.xml"] = f'<c:chartSpace {C}><c:chart>{chart}</c:chart></c:chartSpace>'
    return _zip(tmp_path / name, parts)


GOOD_CHART = ('<c:title/><c:plotArea><c:barChart><c:ser><c:val><c:numRef><c:f>Sheet1!$A$1:$A$2</c:f>'
              '</c:numRef></c:val></c:ser></c:barChart></c:plotArea>')


def test_a_sound_workbook_passes(tmp_path):
    cells = ('<c r="A1"><v>1</v></c><c r="A2"><v>2</v></c><c r="B1"><f>SUM(A1:A2)</f><v>3</v></c>'
             "<c r=\"C1\"><f>'Data Two'!A1*2</f><v>6</v></c>")
    res = qa.check(_xlsx(tmp_path, cells, chart=GOOD_CHART))
    assert res["ok"] and res["findings"] == [], res


def test_formula_errors_missing_sheets_and_bounds_are_errors(tmp_path):
    cells = ('<c r="A1" t="e"><f>1/0</f><v>#DIV/0!</v></c>'
             '<c r="B1"><f>Missing!A1+1</f><v>0</v></c>'
             '<c r="C1"><f>#REF!+1</f><v>0</v></c>'
             '<c r="D1"><f>A0+1</f><v>0</v></c>')
    res = qa.check(_xlsx(tmp_path, cells))
    msgs = " | ".join(f["message"] for f in _errors(res))
    assert "Sheet1!A1 shows #DIV/0!" in msgs
    assert "sheet 'Missing' that does not exist" in msgs
    assert "deleted range" in msgs
    assert "outside any sheet" in msgs


def test_a_chart_with_no_data_is_an_error_and_no_title_a_warning(tmp_path):
    chart = '<c:autoTitleDeleted val="1"/><c:plotArea><c:barChart><c:ser/></c:barChart></c:plotArea>'
    res = qa.check(_xlsx(tmp_path, '<c r="A1"><v>1</v></c>', chart=chart))
    assert not res["ok"]
    assert "charts" in _checks(res, "error") and "accessibility" in _checks(res, "warn")


# ── PowerPoint ─────────────────────────────────────────────────────────────

P = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" ' \
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'


def _shape(x, y, cx, cy, *, title=False, font=None):
    ph = '<p:nvPr><p:ph type="title"/></p:nvPr>' if title else '<p:nvPr/>'
    run = f'<a:rPr><a:latin typeface="{font}"/></a:rPr>' if font else ""
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="2" name="s"/><p:cNvSpPr/>{ph}</p:nvSpPr>'
            f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm></p:spPr>'
            f'<p:txBody><a:p><a:r>{run}<a:t>Words</a:t></a:r></a:p></p:txBody></p:sp>')


def _pic(alt):
    return (f'<p:pic><p:nvPicPr><p:cNvPr id="3" name="pic" descr="{alt}"/></p:nvPicPr>'
            f'<p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="10" cy="10"/></a:xfrm></p:spPr></p:pic>')


def _pptx(tmp_path, *slides, name="deck.pptx"):
    parts = {"ppt/presentation.xml": f'<p:presentation {P}><p:sldSz cx="1000" cy="500"/></p:presentation>'}
    for i, shapes in enumerate(slides, 1):
        parts[f"ppt/slides/slide{i}.xml"] = f'<p:sld {P}><p:cSld><p:spTree>{shapes}</p:spTree></p:cSld></p:sld>'
    return _zip(tmp_path / name, parts)


def test_a_sound_deck_passes(tmp_path):
    res = qa.check(_pptx(tmp_path, _shape(10, 10, 500, 100, title=True, font="Segoe UI") + _pic("Our logo")))
    assert res["ok"] and res["findings"] == [], res


def test_a_shape_off_the_slide_and_a_picture_without_alt_text_are_errors(tmp_path):
    res = qa.check(_pptx(tmp_path, _shape(10, 10, 500, 100, title=True),
                         _shape(900, 10, 300, 100, font="Fancy Script") + _pic("")))
    msgs = " | ".join(f["message"] for f in res["findings"])
    assert not res["ok"]
    assert "slide 2 has a shape that runs off the slide" in msgs
    assert "slide 2 has a picture with no alt text" in msgs
    assert "slide 2 has no title" in msgs and "Fancy Script" in msgs


def test_a_deck_with_no_slides_is_invalid(tmp_path):
    assert not qa.check(_pptx(tmp_path))["ok"]


# ── PDF ────────────────────────────────────────────────────────────────────

def _pdf(path, text=None, lang=None):
    stream = (f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET" if text else "0 0 m 10 10 l S").encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R" + (f" /Lang ({lang})".encode() if lang else b"") + b" >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    path.write_bytes(bytes(out))
    return path


def test_a_pdf_with_text_and_a_language_passes(tmp_path):
    res = qa.check(_pdf(tmp_path / "a.pdf", "Quarterly summary", lang="en-GB"))
    assert res["ok"] and res["findings"] == [], res


def test_a_pdf_without_a_text_layer_fails_and_a_missing_language_warns(tmp_path):
    res = qa.check(_pdf(tmp_path / "b.pdf"))
    assert not res["ok"]
    msgs = " | ".join(f["message"] for f in res["findings"])
    assert "no text layer" in msgs and "language is not set" in msgs
    p = tmp_path / "c.pdf"
    p.write_bytes(b"<html>not a pdf</html>")
    assert not qa.check(p)["ok"]


# ── web pages, CSV, images ─────────────────────────────────────────────────

def test_a_sound_page_passes(tmp_path):
    p = tmp_path / "site.html"
    p.write_text('<!doctype html><html lang="en"><head><title>Deck</title><style>body{font-family:'
                 '"Segoe UI",system-ui}</style></head><body><h1>A</h1><h2>B</h2><img src="a.png" alt="A">'
                 '<a href="https://example.com">a link is not a load</a></body></html>', encoding="utf-8")
    res = qa.check(p)
    assert res["ok"] and res["findings"] == [], res


def test_a_page_that_loads_from_the_internet_or_skips_alt_text_says_so(tmp_path):
    p = tmp_path / "site.html"
    p.write_text('<html><head><script src="https://cdn.example.com/x.js"></script><style>'
                 '@import url("https://fonts.example.com/css");h1{font-family:"Fancy Script"}</style></head>'
                 '<body><h1>A</h1><h3>C</h3><img src="b.png"></body></html>', encoding="utf-8")
    res = qa.check(p)
    msgs = " | ".join(f["message"] for f in res["findings"])
    assert not res["ok"] and "no alt text" in msgs
    assert "cdn.example.com" in msgs and "fonts.example.com" in msgs
    assert "language" in msgs and "no title" in msgs and "h1 to h3" in msgs and "Fancy Script" in msgs


def test_a_ragged_csv_and_a_fake_png_are_errors(tmp_path):
    c = tmp_path / "t.csv"
    c.write_text("a,b,c\n1,2,3\n4,5\n", encoding="utf-8")
    assert not qa.check(c)["ok"]
    good = tmp_path / "ok.png"
    ihdr = struct.pack(">IIBBBBB", 4, 3, 8, 2, 0, 0, 0)
    good.write_bytes(b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr
                     + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr)))
    assert qa.check(good)["ok"]
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"GIF89a....")
    assert not qa.check(bad)["ok"]


def test_an_unknown_kind_claims_nothing_and_summary_reads_plainly(tmp_path):
    p = tmp_path / "notes.xyz"
    p.write_text("x", encoding="utf-8")
    res = qa.check(p)
    assert res == {"ok": True, "kind": "xyz", "checked": False, "findings": []}
    assert qa.summary(res) == ""
    bad = qa.check(_pptx(tmp_path))
    assert qa.summary(bad).startswith("Problems: ")
