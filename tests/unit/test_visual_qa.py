"""Visual QA (NS-24.4-1, NS-36.5-8; BD-20 V3): does a file Friday made LOOK
right? services/visual_qa renders PDFs locally (pdfium, which ships with
pdfplumber) and reads Office files' own layout, and reports fixed codes with
page numbers; the artifact manifest records them and a file whose looks
could not be checked is never passed. Every fixture is built here, at test
time, from raw bytes: no Office, no LibreOffice, no network."""
from __future__ import annotations

import json
import time
import zipfile

import pytest

from agent_friday.services import visual_qa as vq

HELVETICA = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"


def _pdf(path, pages, font=HELVETICA):
    """A PDF with one content stream per page (612 x 792 points) and one font, /F1."""
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", None, font]
    kids = []
    for stream in pages:
        s = stream.encode()
        objs.append(b"<< /Length %d >>\nstream\n" % len(s) + s + b"\nendstream")
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
                    b"/Resources << /Font << /F1 3 0 R >> >> >>" % len(objs))
        kids.append(len(objs))
    objs[1] = b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % k for k in kids) + b"] /Count %d >>" % len(kids)
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


def _text(x, y, words, colour="0 g", size=12):
    return "%s BT /F1 %d Tf %d %d Td (%s) Tj ET" % (colour, size, x, y, words)


BODY = _text(72, 720, "Quarterly summary") + " " + _text(72, 690, "Revenue rose in every region.")


def _codes(res):
    return {f["code"] for f in res["findings"]}


def _finding(res, code):
    return next(f for f in res["findings"] if f["code"] == code)


# ── PDF ────────────────────────────────────────────────────────────────────

def test_a_clean_pdf_is_rendered_and_passes(tmp_path):
    res = vq.check(_pdf(tmp_path / "clean.pdf", [BODY, BODY]))
    assert res["status"] == "checked" and res["method"] == "rendered" and res["renderer"] == "pdfium", res
    assert res["findings"] == [] and res["ok"]
    assert res["pages_checked"] == res["pages_total"] == 2
    assert vq.needs_attention(res) is None
    assert vq.summary(res) == "Looks: no problems found (2 page(s) rendered)."
    assert vq.check(tmp_path / "clean.pdf") == res                      # deterministic


def test_an_empty_page_is_an_error_with_its_page_number(tmp_path):
    res = vq.check(_pdf(tmp_path / "gap.pdf", [BODY, "", BODY]))
    f = _finding(res, "page_empty")
    assert f["pages"] == [2] and f["severity"] == "error" and f["unit"] == "page"
    assert not res["ok"] and res["status"] == "checked"
    assert "a page is empty (page 2)" in vq.messages(res, "error")


def test_text_off_the_page_edge_is_an_error(tmp_path):
    res = vq.check(_pdf(tmp_path / "edge.pdf", [BODY + " " + _text(560, 400, "Overflowing past the margin")]))
    f = _finding(res, "text_off_page")
    assert f["pages"] == [1] and f["severity"] == "error" and not res["ok"]
    # Codes and page numbers only: the file's words are never in the result.
    assert "Overflowing" not in json.dumps(res) and "Quarterly" not in json.dumps(res)


def test_a_font_that_is_not_embedded_is_reported(tmp_path):
    fancy = b"<< /Type /Font /Subtype /Type1 /BaseFont /FancyScriptPro >>"
    res = vq.check(_pdf(tmp_path / "font.pdf", [BODY], font=fancy))
    f = _finding(res, "font_missing")
    assert f["pages"] == [1] and f["severity"] == "warn"
    # The standard 14 are on every reader's machine: the clean PDF uses Helvetica.
    assert "font_missing" not in _codes(vq.check(_pdf(tmp_path / "std.pdf", [BODY])))


def test_low_contrast_text_is_found_against_the_rendered_background(tmp_path):
    faint = _text(72, 650, "Light grey words on white", colour="0.9 g")
    res = vq.check(_pdf(tmp_path / "faint.pdf", [BODY + " " + faint]))
    f = _finding(res, "low_contrast")
    assert f["pages"] == [1] and f["severity"] == "error"           # about 1.3:1, all but unreadable
    # White text on a dark panel reads well: the background is what was rendered under it.
    panel = "0.1 g 60 450 400 120 re f " + _text(72, 500, "White words on a dark panel", colour="1 g")
    assert "low_contrast" not in _codes(vq.check(_pdf(tmp_path / "panel.pdf", [BODY + " " + panel])))
    assert round(vq.contrast_ratio((0, 0, 0), (255, 255, 255)), 1) == 21.0


def test_a_chart_spilling_off_one_edge_is_an_error_but_a_full_bleed_band_is_not(tmp_path):
    spill = "1 0 0 rg 450 100 250 120 re f"
    res = vq.check(_pdf(tmp_path / "spill.pdf", [BODY + " " + spill]))
    f = _finding(res, "content_off_page")                  # the pixels show it reaching the edge
    assert f["pages"] == [1] and f["severity"] == "error" and not res["ok"]
    band = "0.95 g -20 740 652 80 re f"                    # a header band bleeding off three edges
    assert "content_off_page" not in _codes(vq.check(_pdf(tmp_path / "band.pdf", [band + " " + BODY])))


def test_a_spill_the_pixels_do_not_show_is_only_a_warning(tmp_path, monkeypatch):
    white = "1 1 1 rg 450 100 250 120 re f"                # white on white: nothing visible is cut
    res = vq.check(_pdf(tmp_path / "white.pdf", [BODY + " " + white]))
    f = _finding(res, "content_off_page")
    assert f["severity"] == "warn" and res["ok"] and res["status"] == "checked", res
    # Without a renderer nothing can confirm it: a warning, never an error.
    monkeypatch.setattr(vq, "_renderer", lambda: None)
    nr = vq.check(_pdf(tmp_path / "spill.pdf", [BODY + " 1 0 0 rg 450 100 250 120 re f"]))
    assert _finding(nr, "content_off_page")["severity"] == "warn"


def test_text_drawn_invisibly_is_not_off_the_page(tmp_path):
    hidden = "BT /F1 12 Tf 3 Tr 560 400 Td (Overflowing past the margin) Tj ET"   # a scan's text layer
    res = vq.check(_pdf(tmp_path / "ocr.pdf", [BODY + " " + hidden]))
    assert res["status"] == "checked" and not _codes(res) & {"text_off_page", "text_near_edge"}, res


def test_caps_report_partial_and_never_pass(tmp_path, monkeypatch):
    p = _pdf(tmp_path / "long.pdf", [BODY, BODY, BODY])
    monkeypatch.setattr(vq, "MAX_PAGES", 2)
    res = vq.check(p)
    assert res["status"] == "partial" and res["pages_checked"] == 2 and res["pages_total"] == 3
    assert res["reason"] == "only the first 2 of 3 pages were looked at"
    assert vq.needs_attention(res) and "no problems found" not in vq.summary(res)
    monkeypatch.setattr(vq, "MAX_PAGES", 30)
    late = vq.check(p, budget_s=0)                          # the time budget is spent before page 1
    assert late["status"] == "partial" and late["pages_checked"] == 0
    assert late["reason"].startswith("time ran out")


def test_a_missing_renderer_reports_could_not_render_not_a_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(vq, "_renderer", lambda: None)
    res = vq.check(_pdf(tmp_path / "clean.pdf", [BODY]))
    assert res["status"] == "could_not_render" and res["reason"] == "no PDF renderer is installed here"
    assert vq.needs_attention(res) == "could not check how it looks here: no PDF renderer is installed here"
    assert "no problems found" not in vq.summary(res)
    # What needs no pixels is still read and still counts.
    off = vq.check(_pdf(tmp_path / "edge.pdf", [_text(560, 400, "Overflowing past the margin")]))
    assert off["status"] == "could_not_render" and "text_off_page" in _codes(off) and not off["ok"]


def test_kinds_without_a_visual_check_claim_nothing(tmp_path):
    p = tmp_path / "notes.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    res = vq.check(p)
    assert res["status"] == "not_applicable" and vq.compact(res) is None and vq.summary(res) == ""
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"not a pdf at all")
    assert vq.check(bad)["status"] == "could_not_render"


def test_the_isolated_check_gives_the_same_answer_and_never_passes_on_time_out(tmp_path, monkeypatch):
    import sys
    p = _pdf(tmp_path / "edge.pdf", [BODY + " " + _text(560, 400, "Overflowing past the margin")])
    direct, isolated = vq.check(p), vq.check_isolated(p)
    assert isolated["status"] == direct["status"] == "checked"
    assert isolated["findings"] == direct["findings"]
    late = vq.check_isolated(p, timeout=0.001)
    assert late["status"] == "could_not_render" and late["reason"].startswith("the visual check did not finish")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert vq.check_isolated(p)["status"] == "unavailable"


# ── PowerPoint, Word, Excel (from their own layout; no converter) ───────────

CT = '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
P = ('xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
     'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
     'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')


@pytest.fixture
def no_converter(monkeypatch):
    monkeypatch.setattr(vq, "find_converter", lambda: None)


def _zip(path, parts):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", CT)
        for name, body in parts.items():
            z.writestr(name, body)
    return path


def _sp(x, y, cx, cy, text="Quarterly summary", sz=2400):
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="2" name="t"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
            f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm></p:spPr>'
            f'<p:txBody><a:bodyPr/><a:p><a:r><a:rPr sz="{sz}"/><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp>')


def _pic(alt):
    return (f'<p:pic><p:nvPicPr><p:cNvPr id="3" name="pic" descr="{alt}"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr>'
            f'<p:spPr><a:xfrm><a:off x="1000000" y="3000000"/><a:ext cx="2000000" cy="1500000"/></a:xfrm>'
            f'</p:spPr></p:pic>')


def _pptx(tmp_path, *slides, name="deck.pptx", extra=None):
    parts = {"ppt/presentation.xml": f'<p:presentation {P}><p:sldSz cx="12192000" cy="6858000"/></p:presentation>'}
    for i, shapes in enumerate(slides, 1):
        parts[f"ppt/slides/slide{i}.xml"] = f'<p:sld {P}><p:cSld><p:spTree>{shapes}</p:spTree></p:cSld></p:sld>'
    parts.update(extra or {})
    return _zip(tmp_path / name, parts)


def _chart(descr="", title=None):
    t = f' title="{title}"' if title else ""
    return (f'<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="4" name="chart" descr="{descr}"{t}/>'
            f'<p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr>'
            f'<p:xfrm><a:off x="1000000" y="1000000"/><a:ext cx="4000000" cy="3000000"/></p:xfrm>'
            f'<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart"/>'
            f'</a:graphic></p:graphicFrame>')


def test_a_clean_deck_passes(tmp_path, no_converter):
    res = vq.check(_pptx(tmp_path, _sp(1000000, 1000000, 6000000, 1000000) + _pic("Revenue by region")))
    assert res["status"] == "checked" and res["method"] == "structural" and res["findings"] == [], res
    assert "no converter here" in vq.summary(res)


def test_a_slide_shape_off_the_slide_overflowing_text_and_missing_alt_text(tmp_path, no_converter):
    long_text = "word " * 60
    res = vq.check(_pptx(tmp_path, _sp(1000000, 1000000, 6000000, 1000000),
                         _sp(11000000, 500000, 3000000, 1000000) + _sp(500000, 2000000, 914400, 457200, long_text)
                         + _pic("")))
    off = _finding(res, "shape_off_slide")
    assert off["pages"] == [2] and off["unit"] == "slide" and off["severity"] == "error"
    assert _finding(res, "text_overflow")["pages"] == [2]
    assert _finding(res, "alt_text_missing")["pages"] == [2]
    assert not res["ok"] and "a shape runs off the slide (slide 2)" in vq.messages(res, "error")
    assert "word word" not in json.dumps(res)
    # The isolated child loads the module and its zip reader by file path.
    assert "shape_off_slide" in _codes(vq.check_isolated(tmp_path / "deck.pptx"))


def test_a_chart_without_alt_text_is_a_warning_and_a_title_counts_as_alt_text(tmp_path, no_converter):
    res = vq.check(_pptx(tmp_path, _chart(), name="chart.pptx"))
    f = _finding(res, "alt_text_missing")
    assert f["severity"] == "warn" and f["pages"] == [1] and res["ok"], res
    titled = _chart(title="Revenue by region") + _pic("").replace('descr=""', 'title="Revenue by region"')
    assert "alt_text_missing" not in _codes(vq.check(_pptx(tmp_path, titled, name="titled.pptx")))
    # A picture with neither is still an error.
    assert _finding(vq.check(_pptx(tmp_path, _pic(""), name="pic.pptx")), "alt_text_missing")["severity"] == "error"


def test_a_band_across_the_whole_slide_is_a_deliberate_bleed(tmp_path, no_converter):
    band = _sp(-200000, 0, 12592000, 1000000)               # off the left and the right edge
    assert "shape_off_slide" not in _codes(vq.check(_pptx(tmp_path, band, name="band.pptx")))
    one = _sp(-200000, 0, 6000000, 1000000)                 # off the left edge only
    assert "shape_off_slide" in _codes(vq.check(_pptx(tmp_path, one, name="one.pptx")))


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
SECT = '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:left="1440" w:right="1440"/></w:sectPr>'


def _docx(tmp_path, body, name="doc.docx"):
    return _zip(tmp_path / name, {"word/document.xml": f'<w:document {W}><w:body>{body}{SECT}</w:body></w:document>'})


def _table(*widths):
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
    cells = "".join('<w:tc><w:p><w:r><w:t>cell</w:t></w:r></w:p></w:tc>' for _ in widths)
    return f'<w:tbl><w:tblGrid>{grid}</w:tblGrid><w:tr>{cells}</w:tr></w:tbl>'


def _para(text):
    return f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p>'


def test_a_word_table_wider_than_the_page_and_a_long_unbroken_line(tmp_path, no_converter):
    clean = vq.check(_docx(tmp_path, _para("A short paragraph.") + _table(3000, 3000), "clean.docx"))
    assert clean["status"] == "checked" and clean["findings"] == [], clean
    res = vq.check(_docx(tmp_path, _para("https://example.com/" + "a" * 200) + _table(8000, 8000)))
    wide = _finding(res, "table_too_wide")
    assert wide["severity"] == "error" and wide["pages"] == [] and wide["unit"] is None
    assert "long_unbroken_text" in _codes(res) and not res["ok"]


LANDSCAPE = ('<w:p><w:pPr><w:sectPr><w:pgSz w:w="15840" w:h="12240" w:orient="landscape"/>'
             '<w:pgMar w:left="1440" w:right="1440"/></w:sectPr></w:pPr></w:p>')


def test_a_table_is_judged_against_the_section_it_is_in(tmp_path, no_converter):
    # In a landscape section the table fits; the last section is portrait.
    fits = vq.check(_docx(tmp_path, _table(6000, 6000) + LANDSCAPE + _para("Portrait from here."), "fits.docx"))
    assert "table_too_wide" not in _codes(fits), fits
    # The same table in the portrait section runs off its page.
    wide = vq.check(_docx(tmp_path, LANDSCAPE + _table(6000, 6000), "wide.docx"))
    assert _finding(wide, "table_too_wide")["severity"] == "error"
    # A section whose page size is not given: only a warning.
    unknown = _zip(tmp_path / "unknown.docx", {"word/document.xml": f'<w:document {W}><w:body>'
                                                                    f'{_table(8000, 8000)}<w:sectPr/></w:body>'
                                                                    f'</w:document>'})
    assert _finding(vq.check(unknown), "table_too_wide")["severity"] == "warn"


S ='xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


def _xlsx(tmp_path, cols="", name="book.xlsx", extra=""):
    rows = ('<row r="1"><c r="A1" t="inlineStr"><is><t>' + "a long label for a narrow column" + '</t></is></c>'
            '<c r="B1"><v>5</v></c></row><row r="2"><c r="A2" s="1"><v>45000.5</v></c></row>')
    return _zip(tmp_path / name, {
        "xl/workbook.xml": f'<workbook {S} {R}><sheets><sheet name="Data" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                                      'relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
                                      '</Relationships>',
        "xl/styles.xml": f'<styleSheet {S}><cellXfs><xf numFmtId="0"/><xf numFmtId="22"/></cellXfs></styleSheet>',
        "xl/worksheets/sheet1.xml": f'<worksheet {S}>{cols}<sheetData>{rows}</sheetData>{extra}</worksheet>',
    })


def test_a_sheet_with_cut_off_text_and_dates_too_wide_for_their_column(tmp_path, no_converter):
    res = vq.check(_xlsx(tmp_path))
    assert _finding(res, "cell_text_cut")["pages"] == [1] and _finding(res, "cell_text_cut")["unit"] == "sheet"
    assert _finding(res, "cell_shows_hashes")["pages"] == [1]
    wide = '<cols><col min="1" max="1" width="40" customWidth="1"/></cols>'
    assert vq.check(_xlsx(tmp_path, wide, "wide.xlsx"))["findings"] == []


def test_merged_cells_are_read_within_caps_and_a_deadline(tmp_path, no_converter, monkeypatch):
    p = _xlsx(tmp_path, name="merged.xlsx", extra='<mergeCells count="1"><mergeCell ref="A1:B1"/></mergeCells>')
    res = vq.check(p)
    assert res["status"] == "checked" and "cell_text_cut" not in _codes(res)   # the label spans its merge
    monkeypatch.setattr(vq, "MAX_MERGE_WORK", 1)
    capped = vq.check(p)
    assert capped["status"] == "partial" and capped["reason"] == "only part of the merged cells were looked at"
    monkeypatch.setattr(vq, "MAX_MERGE_WORK", 2_000_000)
    monkeypatch.setattr(vq, "MAX_MERGES", 0)
    assert vq.check(p)["reason"] == "a sheet has more than 0 merged ranges"
    monkeypatch.setattr(vq, "MAX_MERGES", 2000)
    late = vq.check(p, budget_s=0)
    assert late["status"] == "partial" and late["reason"].startswith("time ran out")


# ── child processes, the converter and its profile ──────────────────────────

GRANDCHILD = ("import sys,time\n"
              "while True:\n"
              "    with open(sys.argv[1],'w') as f: f.write(repr(time.time()))\n"
              "    time.sleep(0.1)\n")
CHILD = ("import os,subprocess,sys,time\n"
         "subprocess.Popen([sys.executable,'-I','-c',sys.argv[1],sys.argv[2]])\n"
         "while not os.path.exists(sys.argv[2]): time.sleep(0.05)\n"
         "time.sleep(120)\n")


def test_a_time_out_kills_the_child_and_its_grandchild(tmp_path):
    import subprocess
    import sys
    beat = tmp_path / "beat.txt"
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        vq._run_contained([sys.executable, "-I", "-c", CHILD, GRANDCHILD, str(beat)], 15)
    assert time.monotonic() - started < 40
    assert beat.exists()                                    # the grandchild had started...
    time.sleep(1.0)
    first = beat.read_text()
    time.sleep(1.5)
    assert beat.read_text() == first                        # ...and is not running any more


def test_children_get_a_minimal_environment_without_keys(monkeypatch):
    import sys
    import uuid
    name = "_".join(("VQA", "PLANTED", "API", "KEY"))
    planted = "planted-" + uuid.uuid4().hex
    monkeypatch.setenv(name, planted)
    env = vq._child_env()
    assert name not in {k.upper() for k in env} and "PATH" in {k.upper() for k in env}
    code = "import json,os,sys; sys.stdout.write(json.dumps(dict(os.environ)))"
    rc, out = vq._run_contained([sys.executable, "-I", "-c", code], 60)
    text = out.decode("utf-8")
    assert rc == 0 and name not in {k.upper() for k in json.loads(text)}
    assert planted not in text


def test_stale_temporary_folders_are_swept(tmp_path, monkeypatch):
    import os
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    old, new, other = tmp_path / "friday-vqa-old", tmp_path / "friday-vqa-new", tmp_path / "someone-else"
    for d in (old, new, other):
        d.mkdir()
        (d / "f.txt").write_text("x", encoding="utf-8")
    hours_ago = time.time() - 2 * 3600
    for d in (old, other):
        os.utime(d, (hours_ago, hours_ago))
    assert vq._sweep_stale_temp(force=True) == 1
    assert not old.exists() and new.exists() and other.exists()


def test_the_converter_profile_is_locked_down_before_its_first_launch(tmp_path):
    import xml.etree.ElementTree as ET
    xcu = vq._prepare_profile(tmp_path / "profile")
    text = xcu.read_text(encoding="utf-8")
    ET.fromstring(text.encode("utf-8"))                     # well-formed
    for needle in ('<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value>',
                   '<prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value>',
                   '<prop oor:name="BlockUntrustedRefererLinks" oor:op="fuse"><value>true</value>',
                   'Office.Writer/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>0</value>',
                   'Office.Calc/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>1</value>'):
        assert needle in text, needle
    # Left alone while intact; put back when a setting is lost.
    before = xcu.read_bytes()
    vq._prepare_profile(tmp_path / "profile")
    assert xcu.read_bytes() == before
    xcu.write_text(text.replace("<value>3</value>", "<value>0</value>"), encoding="utf-8")
    assert not vq._locked_down(xcu.read_text(encoding="utf-8"))
    vq._prepare_profile(tmp_path / "profile")
    assert vq._locked_down(xcu.read_text(encoding="utf-8"))


@pytest.fixture
def converter(tmp_path, monkeypatch):
    """A converter is "installed" (this Python stands in for soffice); its
    temporary folders go to tmp/ so the test can see they are removed."""
    import sys
    import tempfile
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp))
    monkeypatch.setattr(vq, "find_converter", lambda: sys.executable)
    return tmp


def _fake_soffice(monkeypatch, code, *args):
    import sys
    calls = []

    def argv(exe, profile, out_dir, inp):
        calls.append(str(profile))
        return [sys.executable, "-I", "-c", code, str(out_dir), *args]
    monkeypatch.setattr(vq, "_soffice_argv", argv)
    return calls


def test_a_converted_file_is_rendered_with_the_locked_down_profile(tmp_path, converter, monkeypatch):
    made = _pdf(tmp_path / "made.pdf", [BODY])
    _fake_soffice(monkeypatch, "import shutil,sys; shutil.copyfile(sys.argv[2], sys.argv[1] + '/input.pdf')",
                  str(made))
    profile = tmp_path / "profile"
    res = vq.check(_docx(tmp_path, _para("A short paragraph.")), profile_dir=profile)
    assert res["status"] == "checked" and res["method"] == "converted" and res["pages_checked"] == 1, res
    assert vq._locked_down((profile / "user" / "registrymodifications.xcu").read_text(encoding="utf-8"))
    assert [d for d in converter.iterdir() if d.name.startswith("friday-vqa-")] == []


def test_a_failed_conversion_is_partial_never_checked(tmp_path, converter, monkeypatch):
    calls = _fake_soffice(monkeypatch, "pass")                   # runs, makes nothing
    p = _docx(tmp_path, _para("A short paragraph."))
    res = vq.check(p, profile_dir=tmp_path / "profile")
    assert len(calls) == 1
    assert res["status"] == "partial" and res["method"] == "structural" and res["converter"] == "libreoffice"
    assert res["reason"] == "layout checked, not rendered: the converter could not convert the file"
    assert res["ok"] and res["findings"] == []                    # nothing wrong found, but not a pass
    assert vq.needs_attention(res).startswith("how it looks was only partly checked")
    # With no converter at all, the layout check is the whole check.
    monkeypatch.setattr(vq, "find_converter", lambda: None)
    assert vq.check(p)["status"] == "checked"


def test_a_converter_that_hangs_is_killed_and_its_folder_removed(tmp_path, converter, monkeypatch):
    _fake_soffice(monkeypatch, "import time; time.sleep(120)")
    monkeypatch.setattr(vq, "CONVERT_TIMEOUT_S", 2)
    started = time.monotonic()
    res = vq.check(_pptx(tmp_path, _sp(1000000, 1000000, 6000000, 1000000)), profile_dir=tmp_path / "profile")
    assert time.monotonic() - started < 30
    assert res["status"] == "partial"
    assert res["reason"] == "layout checked, not rendered: the converter did not finish within 2 seconds"
    assert [d for d in converter.iterdir() if d.name.startswith("friday-vqa-")] == []


EXTERNAL_IMAGE = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                  '<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                  'relationships/%s" Target="http://example.invalid/pixel.png" TargetMode="External"/>'
                  '</Relationships>')


def test_a_file_linking_to_outside_content_is_never_converted(tmp_path, converter, monkeypatch):
    calls = _fake_soffice(monkeypatch, "pass")
    slide = _sp(1000000, 1000000, 6000000, 1000000)
    linked = _pptx(tmp_path, slide, name="linked.pptx",
                   extra={"ppt/slides/_rels/slide1.xml.rels": EXTERNAL_IMAGE % "image"})
    res = vq.check(linked, profile_dir=tmp_path / "profile")
    assert calls == []
    assert res["status"] == "partial"
    assert res["reason"] == "layout checked, not rendered: the file links to outside content, so it was not converted"
    # An outside hyperlink fetches nothing when converting: that file is converted.
    link = _pptx(tmp_path, slide, name="link.pptx",
                 extra={"ppt/slides/_rels/slide1.xml.rels": EXTERNAL_IMAGE % "hyperlink"})
    vq.check(link, profile_dir=tmp_path / "profile")
    assert len(calls) == 1


def test_a_macro_enabled_file_is_never_converted(tmp_path, converter, monkeypatch):
    import sys
    calls = _fake_soffice(monkeypatch, "pass")
    xlsm = _xlsx(tmp_path, name="book.xlsm")
    res = vq.check(xlsm, profile_dir=tmp_path / "profile")
    assert calls == [] and res["status"] == "partial"
    assert res["reason"] == "layout checked, not rendered: macro-enabled files are never converted"
    work = tmp_path / "work"
    work.mkdir()
    assert vq._convert(sys.executable, xlsm, work, 5, tmp_path / "profile") == (
        None, "macro-enabled files are never converted")
    # A .xlsx carrying a macro project is not converted either.
    hidden = _xlsx(tmp_path, name="hidden.xlsx")
    with zipfile.ZipFile(hidden, "a") as z:
        z.writestr("xl/vbaProject.bin", b"not really a macro project")
    res = vq.check(hidden, profile_dir=tmp_path / "profile")
    assert calls == [] and res["status"] == "partial" and "carries macros" in res["reason"]


# ── the manifest and the verification state ────────────────────────────────

KEY = b"synthetic-governance-key-for-tests-0000000000000000"


@pytest.fixture
def home(tmp_path, monkeypatch):
    import agent_friday.paths as paths
    from agent_friday.governance import action_gate as ag
    from agent_friday.governance import proof_of_integrity as poi
    from agent_friday.services import off_record
    monkeypatch.setattr(paths, "friday_home", lambda: tmp_path)
    monkeypatch.setattr(ag, "friday_home", lambda: tmp_path)
    monkeypatch.setattr(poi, "get_governance_key", lambda: KEY)
    monkeypatch.setattr(off_record, "active", lambda *a, **k: False)
    yield tmp_path


def _record(p, task_id):
    from agent_friday.services import artifact_manifest as am
    lines = am.record_call("write_file", {"path": str(p), "content": "x"}, "Wrote 1 chars to %s" % p,
                           task_id=task_id, started_at=time.time() - 1)
    assert len(lines) == 1, lines
    return lines[0]


def test_a_clean_pdf_is_recorded_as_passed_with_its_visual_check(home):
    line = _record(_pdf(home / "report.pdf", [BODY]), "task-v-clean")
    vis = line["qa"]["visual"]
    assert vis["status"] == "checked" and vis["ok"] and vis["findings"] == []
    assert vis["summary"].startswith("Looks: no problems found")
    assert line["verification_state"] == "qa_passed" and line["verification_reason"] is None
    # A kind with no visual check carries none, and its record is as before.
    csv = home / "notes.csv"
    csv.write_text("a,b\n1,2\n", encoding="utf-8")
    plain = _record(csv, "task-v-clean")
    assert "visual" not in plain["qa"] and plain["verification_state"] == "qa_passed"


def test_a_visual_problem_fails_the_file_and_leaves_the_task_unverified(home):
    from agent_friday.services import artifact_manifest as am
    p = _pdf(home / "edge.pdf", [BODY + " " + _text(560, 400, "Overflowing past the margin")])
    line = _record(p, "task-v-bad")
    assert line["qa"]["ok"] is True                                      # the structure is fine...
    assert line["verification_state"] == "qa_failed"                     # ...but it does not look right
    vis = line["qa"]["visual"]
    assert {"code": "text_off_page", "severity": "error", "unit": "page", "pages": [1], "count": 1} in vis["findings"]
    assert "Overflowing" not in json.dumps(line) and "Quarterly" not in json.dumps(line)
    failures = am.qa_failures("task-v-bad")
    assert [f["path"] for f in failures] == [str(p.resolve())]
    assert failures[0]["looks"] == ["text runs off the edge of the page (page 1)"]
    assert "text runs off the edge of the page (page 1)" in failures[0]["errors"]
    assert am.lookup(p)["qa"]["visual"] == vis


def test_could_not_render_is_unverified_with_its_reason_never_passed(home, monkeypatch):
    from agent_friday.services import artifact_manifest as am
    monkeypatch.setattr(vq, "_renderer", lambda: None)
    monkeypatch.setattr(vq, "check_isolated", lambda p, **k: vq.check(p))
    p = _pdf(home / "report.pdf", [BODY])
    line = _record(p, "task-v-norender")
    assert line["qa"]["checked"] and line["qa"]["ok"]
    assert line["qa"]["visual"]["status"] == "could_not_render"
    assert line["verification_state"] == "unverified"
    assert line["verification_reason"] == "could not check how it looks here: no PDF renderer is installed here"
    assert am.qa_failures("task-v-norender") == []
    assert am.qa_unverified("task-v-norender") == [{"path": str(p.resolve()), "reason": line["verification_reason"]}]
    # A partial look is never a pass either.
    monkeypatch.setattr(vq, "_renderer", lambda: __import__("pypdfium2"))
    monkeypatch.setattr(vq, "MAX_PAGES", 1)
    two = _record(_pdf(home / "two.pdf", [BODY, BODY]), "task-v-partial")
    assert two["verification_state"] == "unverified" and two["qa"]["visual"]["status"] == "partial"
    assert am.qa_unverified("task-v-partial")[0]["reason"].startswith("how it looks was only partly checked")


def test_a_build_that_cannot_run_the_visual_check_keeps_v2s_rule(home, monkeypatch):
    from agent_friday.services import artifact_manifest as am
    monkeypatch.setattr(vq, "check_isolated", lambda p, **k: vq._result("pdf", "unavailable",
                                                                         reason="the visual check cannot run "
                                                                                "in this build"))
    line = _record(_pdf(home / "report.pdf", [BODY]), "task-v-frozen")
    assert line["qa"]["visual"]["status"] == "unavailable"
    assert line["verification_state"] == "qa_passed"
    assert am.qa_unverified("task-v-frozen") == []


def test_the_studio_manifest_bar_shows_how_it_looks(tmp_path):
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    for name in ("index.html", "ui_parts/app.html"):
        src = (root / name).read_text(encoding="utf-8")
        start = src.index("function ManifestBar")
        # up to the next top-level function: in the compiled index.html the
        # destructured props close with "\n}" before the body starts
        end = src.find("\nfunction ", start + 1)
        bar = src[start:end if end > 0 else len(src)]
        assert "qa.visual" in bar and "'Looks'" in bar, name
        assert "vis.ok === false" in bar.replace("vis.ok===false", "vis.ok === false"), name
