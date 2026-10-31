"""Spreadsheet integrity (NS-24.5-1, BD-20 V2): formulas, ranges, types,
named ranges, charts, links, locale and styles, for .xlsx and (where it
applies) .csv, each with a failing and a clean case. Every finding says its
severity, sheet and cell or range. Errors fail the check; warnings and notes
do not.

The workbooks are built here as the zip of XML an .xlsx is, with the standard
library: CI installs no spreadsheet library (openpyxl is not in pyproject or
requirements), and services/artifact_qa reads the package the same way, so
nothing needs skipping. Synthetic files only."""
from __future__ import annotations

import zipfile
from xml.sax.saxutils import escape

from agent_friday.services import artifact_qa as qa

S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" ' \
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
C = 'xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"'
RELS = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'
HYPERLINK = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
CT = '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
SHEET_FINDINGS = {"formulas", "bounds", "names", "ranges", "types", "charts", "links", "locale", "styles"}


# ── building a workbook ────────────────────────────────────────────────────

def n(ref, value, s=None):
    return '<c r="%s"%s><v>%s</v></c>' % (ref, ' s="%d"' % s if s is not None else "", value)


def t(ref, text):
    return '<c r="%s" t="inlineStr"><is><t>%s</t></is></c>' % (ref, escape(text))


def f(ref, formula, cached="0"):
    return '<c r="%s"><f>%s</f><v>%s</v></c>' % (ref, escape(formula), cached)


def row(r, *cells, hidden=False):
    return '<row r="%d"%s>%s</row>' % (r, ' hidden="1"' if hidden else "", "".join(cells))


def book(tmp_path, sheets, *, names="", styles=None, charts=(), parts=None, states=None,
         sheet_rels=None, cols=None, name="book.xlsx"):
    """`sheets`: {sheet name: rows xml}. `names`: definedName elements.
    `sheet_rels`: {sheet name: relationships xml}. `cols`: {sheet name: <cols> xml}."""
    states, sheet_rels, cols = states or {}, sheet_rels or {}, cols or {}
    sheet_els, rels, files = [], [], {}
    for i, (sname, rows) in enumerate(sheets.items(), 1):
        state = ' state="%s"' % states[sname] if sname in states else ""
        sheet_els.append('<sheet name="%s" sheetId="%d" r:id="rId%d"%s/>' % (escape(sname), i, i, state))
        rels.append('<Relationship Id="rId%d" Target="worksheets/sheet%d.xml"/>' % (i, i))
        files["xl/worksheets/sheet%d.xml" % i] = '<worksheet %s>%s<sheetData>%s</sheetData></worksheet>' % (
            S, cols.get(sname, ""), rows)
        if sname in sheet_rels:
            files["xl/worksheets/_rels/sheet%d.xml.rels" % i] = '<Relationships %s>%s</Relationships>' % (
                RELS, sheet_rels[sname])
    defined = "<definedNames>%s</definedNames>" % names if names else ""
    files["xl/workbook.xml"] = '<workbook %s><sheets>%s</sheets>%s</workbook>' % (S, "".join(sheet_els), defined)
    files["xl/_rels/workbook.xml.rels"] = '<Relationships %s>%s</Relationships>' % (RELS, "".join(rels))
    if styles is not None:
        files["xl/styles.xml"] = '<styleSheet %s>%s</styleSheet>' % (S, styles)
    for i, series in enumerate(charts, 1):
        files["xl/charts/chart%d.xml" % i] = (
            '<c:chartSpace %s><c:chart><c:title/><c:plotArea><c:barChart>%s</c:barChart></c:plotArea>'
            '</c:chart></c:chartSpace>' % (C, series))
    files.update(parts or {})
    p = tmp_path / name
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("[Content_Types].xml", CT)
        for part, body in files.items():
            z.writestr(part, body)
    return p


def series(ref, tag="c:val"):
    return '<c:ser><%s><c:numRef><c:f>%s</c:f></c:numRef></%s></c:ser>' % (tag, escape(ref), tag)


def found(res, check, severity=None, text=""):
    return [x for x in res["findings"] if x["check"] == check
            and (severity is None or x["severity"] == severity) and text in x["message"]]


def assert_located(res):
    for x in res["findings"]:
        if x["check"] in SHEET_FINDINGS:
            assert "sheet" in x and "cell" in x, x
            assert x["severity"] in ("error", "warn", "info"), x


NUMBERS = row(1, t("A1", "Cost")) + row(2, n("A2", 100)) + row(3, n("A3", 200)) + row(4, n("A4", 300))


def test_a_clean_workbook_has_no_findings(tmp_path):
    rows = (row(1, t("A1", "Cost"), t("B1", "Total"))
            + row(2, n("A2", 100), f("B2", "SUM(A2:A4)", "600"))
            + row(3, n("A3", 200), f("B3", "Data!A2*TaxRate", "20"))
            + row(4, n("A4", 300), f("B4", "SUM({1,2;3,4})", "10")))
    res = qa.check(book(tmp_path, {"Data": rows}, names='<definedName name="TaxRate">Data!$A$2</definedName>',
                        charts=[series("Data!$A$2:$A$4")]))
    assert res["checked"] and res["ok"] and res["findings"] == [], res


# ── formulas ───────────────────────────────────────────────────────────────

def test_a_formula_that_does_not_parse_is_an_error(tmp_path):
    res = qa.check(book(tmp_path, {"Data": NUMBERS + row(5, f("A5", "SUM(A2:A4"))}))
    bad = found(res, "formulas", "error", "does not parse")
    assert bad and bad[0]["sheet"] == "Data" and bad[0]["cell"] == "A5" and not res["ok"]
    clean = qa.check(book(tmp_path, {"Data": NUMBERS + row(5, f("A5", "SUM(A2:A4)", "600"))}, name="ok.xlsx"))
    assert not found(clean, "formulas") and clean["ok"]
    assert_located(res)


def test_cached_errors_missing_sheets_and_cells_outside_the_grid_are_errors(tmp_path):
    rows = NUMBERS + row(5, '<c r="A5" t="e"><f>A2/0</f><v>#DIV/0!</v></c>', f("B5", "Gone!A1+1"),
                         f("C5", "A1048577+1"), '<c r="D5" t="e"><f>NOPE()</f><v>#NAME?</v></c>')
    res = qa.check(book(tmp_path, {"Data": rows}))
    msgs = " | ".join(x["message"] for x in res["findings"] if x["severity"] == "error")
    assert "Data!A5 shows #DIV/0!" in msgs and "Data!D5 shows #NAME?" in msgs
    assert "sheet 'Gone' that does not exist" in msgs and "outside any sheet" in msgs
    assert {x["cell"] for x in res["findings"]} >= {"A5", "B5", "C5", "D5"}
    assert_located(res)


def test_a_name_the_workbook_does_not_define_is_an_error(tmp_path):
    res = qa.check(book(tmp_path, {"Data": NUMBERS + row(5, f("A5", "A2*TaxRate"))}))
    bad = found(res, "names", "error", "'TaxRate'")
    assert bad and bad[0]["cell"] == "A5" and not res["ok"]
    defined = qa.check(book(tmp_path, {"Data": NUMBERS + row(5, f("A5", "A2*TaxRate"))}, name="ok.xlsx",
                            names='<definedName name="TaxRate">Data!$A$2</definedName>'))
    assert not found(defined, "names") and defined["ok"]


def test_a_3d_reference_over_a_range_of_sheets_is_clean(tmp_path):
    rows = NUMBERS + row(5, f("A5", "SUM(Data:Other!A2)", "100"), f("B5", "SUM('Data:Other'!A2:A4)", "600"))
    res = qa.check(book(tmp_path, {"Data": rows, "Other": NUMBERS}))
    assert not found(res, "names") and not found(res, "formulas"), res
    assert res["ok"], res
    assert qa._formula_scan("SUM(Sheet1:Sheet2!A1)")["names"] == []


def test_another_workbooks_defined_name_is_not_called_undefined(tmp_path):
    rows = NUMBERS + row(5, f("A5", "[1]!MyName*2", "4"), f("B5", "SUM([1]Prices!A1:A3)+OtherRate", "9"))
    res = qa.check(book(tmp_path, {"Data": rows}))
    assert not found(res, "names"), res
    assert not found(res, "formulas"), res
    # Still said: the values come from another workbook (a warning, not an error).
    assert found(res, "links", "warn", "another workbook") and res["ok"], res
    assert qa._formula_scan("[1]!MyName")["names"] == []


# ── named ranges ───────────────────────────────────────────────────────────

def test_named_ranges_to_deleted_ranges_missing_sheets_or_outside_the_grid_are_errors(tmp_path):
    names = ('<definedName name="Old">#REF!</definedName>'
             '<definedName name="Lost">Missing!$A$1:$A$3</definedName>'
             '<definedName name="Huge">Data!$A$1:$A$2000000</definedName>'
             '<definedName name="Mine" localSheetId="7">Data!$A$1</definedName>')
    res = qa.check(book(tmp_path, {"Data": NUMBERS}, names=names))
    errs = found(res, "ranges", "error")
    msgs = " | ".join(x["message"] for x in errs)
    assert "'Old' points at a deleted range" in msgs
    assert "'Lost' points at sheet 'Missing'" in msgs
    assert "'Huge' points at A2000000, outside any sheet" in msgs
    assert "'Mine' belongs to a sheet that does not exist" in msgs
    assert {x["cell"] for x in errs} >= {"Old", "Lost", "Huge", "Mine"} and not res["ok"]
    clean = qa.check(book(tmp_path, {"Data": NUMBERS}, name="ok.xlsx",
                          names='<definedName name="Costs">Data!$A$2:$A$4</definedName>'))
    assert not found(clean, "ranges") and clean["ok"]


# ── types ──────────────────────────────────────────────────────────────────

def test_numbers_stored_as_text_are_flagged(tmp_path):
    rows = row(1, t("A1", "Cost")) + row(2, n("A2", 100)) + row(3, t("A3", "1,234")) + row(4, n("A4", 300))
    res = qa.check(book(tmp_path, {"Data": rows}))
    w = found(res, "types", "warn", "stored as text (first at A3)")
    assert w and w[0]["sheet"] == "Data" and w[0]["cell"] == "A2:A4"
    assert res["ok"]                                  # a warning: the file still works
    assert not found(qa.check(book(tmp_path, {"Data": NUMBERS}, name="ok.xlsx")), "types")


def test_a_column_that_mixes_numbers_and_text_is_flagged(tmp_path):
    rows = (row(1, t("A1", "Value")) + row(2, n("A2", 1)) + row(3, t("A3", "apple")) + row(4, n("A4", 2))
            + row(5, t("A5", "pear")) + row(6, n("A6", 3)))
    res = qa.check(book(tmp_path, {"Data": rows}))
    assert found(res, "types", "warn", "mixes numbers (3) and text (2)")
    # One label among many numbers is below the threshold.
    few = (row(1, t("A1", "Value")) + "".join(row(i, n("A%d" % i, i)) for i in range(2, 12))
           + row(12, t("A12", "n/a")))
    assert not found(qa.check(book(tmp_path, {"Data": few}, name="ok.xlsx")), "types", text="mixes")


def test_dates_stored_as_text_are_flagged(tmp_path):
    rows = row(1, t("A1", "When")) + row(2, t("A2", "2024-01-31")) + row(3, t("A3", "2024-02-29")) \
        + row(4, t("A4", "2024-03-31"))
    res = qa.check(book(tmp_path, {"Data": rows}))
    assert found(res, "types", "warn", "3 date(s) stored as text (first at A2)")
    real = (row(1, t("A1", "When")) + row(2, n("A2", 45322, 1)) + row(3, n("A3", 45351, 1))
            + row(4, n("A4", 45382, 1)))
    styles = '<cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs>'
    clean = qa.check(book(tmp_path, {"Data": real}, styles=styles, name="ok.xlsx"))
    assert clean["findings"] == [], clean


# ── charts ─────────────────────────────────────────────────────────────────

def test_chart_series_over_empty_or_missing_ranges_are_errors(tmp_path):
    with_categories = ('<c:ser><c:cat><c:strRef><c:f>Nowhere!$A$1:$A$2</c:f></c:strRef></c:cat>'
                       '<c:val><c:numRef><c:f>Data!$A$2:$A$4</c:f></c:numRef></c:val></c:ser>')
    res = qa.check(book(tmp_path, {"Data": NUMBERS},
                        charts=[series("Data!$C$1:$C$5") + with_categories]))
    msgs = " | ".join(x["message"] for x in found(res, "charts", "error"))
    assert "series 1 takes its values from Data!$C$1:$C$5, which is empty" in msgs
    assert "series 2 takes its categories from sheet 'Nowhere', which does not exist" in msgs
    assert not res["ok"]
    assert_located(res)
    clean = qa.check(book(tmp_path, {"Data": NUMBERS}, charts=[series("Data!$A$2:$A$4")], name="ok.xlsx"))
    assert not found(clean, "charts") and clean["ok"]


# ── links ──────────────────────────────────────────────────────────────────

def test_links_to_other_workbooks_and_the_internet_are_flagged(tmp_path):
    parts = {"xl/externalLinks/externalLink1.xml": '<externalLink %s/>' % S,
             "xl/externalLinks/_rels/externalLink1.xml.rels":
                 '<Relationships %s><Relationship Id="rId1" Target="file:///C:/shared/prices.xlsx" '
                 'TargetMode="External"/></Relationships>' % RELS}
    rels = {"Data": '<Relationship Id="rId9" Type="%s" Target="https://example.com/report" '
                    'TargetMode="External"/>' % HYPERLINK}
    res = qa.check(book(tmp_path, {"Data": NUMBERS + row(5, f("A5", "[1]Prices!A1*2", "4"))},
                        parts=parts, sheet_rels=rels))
    assert found(res, "links", "warn", "another workbook (prices.xlsx)")
    cell = found(res, "links", "warn", "Data!A5 takes values from another workbook")
    assert cell and cell[0]["cell"] == "A5"
    assert found(res, "links", "info", "links to example.com")
    # A sheet in another workbook is that workbook's: not reported as missing here.
    assert not found(res, "formulas", text="Prices")
    assert res["ok"]
    assert not found(qa.check(book(tmp_path, {"Data": NUMBERS}, name="ok.xlsx")), "links")


# ── locale ─────────────────────────────────────────────────────────────────

def test_regional_function_names_and_separators_are_errors(tmp_path):
    rows = NUMBERS + row(5, f("A5", "SUMME(A2:A4)"), f("B5", "SUM(A2;A4)"))
    res = qa.check(book(tmp_path, {"Data": rows}))
    assert found(res, "locale", "error", "calls SUMME")[0]["cell"] == "A5"
    assert found(res, "locale", "error", "separates arguments with ';'")[0]["cell"] == "B5"
    assert not res["ok"]
    clean = qa.check(book(tmp_path, {"Data": NUMBERS + row(5, f("A5", "SUM(A2,A4)"), f("B5", "SUM({1,2;3,4})"))},
                          name="ok.xlsx"))
    assert not found(clean, "locale") and clean["ok"]


def test_decimal_commas_mixed_with_decimal_points_are_flagged(tmp_path):
    rows = row(1, t("A1", "Rate")) + row(2, t("A2", "1,5")) + row(3, t("A3", "2.5")) + row(4, t("A4", "3,25"))
    res = qa.check(book(tmp_path, {"Data": rows}))
    w = found(res, "locale", "warn", "with a point (A3) and with a comma (A2)")
    assert w and w[0]["sheet"] == "Data"
    same = row(1, t("A1", "Rate")) + row(2, t("A2", "1.5")) + row(3, t("A3", "2.5")) + row(4, t("A4", "3.25"))
    assert not found(qa.check(book(tmp_path, {"Data": same}, name="ok.xlsx")), "locale")


# ── styles and hidden data ─────────────────────────────────────────────────

STYLES = ('<numFmts count="1"><numFmt numFmtId="164" formatCode="&quot;$&quot;#,##0.00"/></numFmts>'
          '<cellXfs count="3"><xf numFmtId="0"/><xf numFmtId="9"/><xf numFmtId="164"/></cellXfs>')


def test_number_formats_that_change_within_a_column_are_flagged(tmp_path):
    rows = row(1, t("A1", "Price")) + row(2, n("A2", 0.5, 1)) + row(3, n("A3", 10, 2)) + row(4, n("A4", 12, 2))
    res = qa.check(book(tmp_path, {"Data": rows}, styles=STYLES))
    w = found(res, "styles", "warn", "different formats (currency, percent)")
    assert w and w[0]["cell"] == "A2:A4" and res["ok"]
    same = row(1, t("A1", "Price")) + row(2, n("A2", 5, 2)) + row(3, n("A3", 10, 2)) + row(4, n("A4", 12, 2))
    assert not found(qa.check(book(tmp_path, {"Data": same}, styles=STYLES, name="ok.xlsx")), "styles")


def test_hidden_sheets_rows_and_columns_with_data_are_noted(tmp_path):
    rows = (row(1, t("A1", "Cost"), t("B1", "Note")) + row(2, n("A2", 100), t("B2", "x"))
            + row(3, n("A3", 200), hidden=True) + row(4, n("A4", 300)))
    res = qa.check(book(tmp_path, {"Data": rows, "Scratch": row(1, n("A1", 1))}, states={"Scratch": "hidden"},
                        cols={"Data": '<cols><col min="2" max="2" hidden="1"/></cols>'}))
    assert found(res, "styles", "info", "sheet 'Scratch' is hidden and holds data")
    assert found(res, "styles", "info", "1 hidden row(s)")[0]["cell"] == "3:3"
    assert found(res, "styles", "info", "1 hidden column(s)")[0]["cell"] == "B:B"
    assert res["ok"]                                  # notes never fail a file
    assert_located(res)
    shown = qa.check(book(tmp_path, {"Data": NUMBERS, "Scratch": row(1, n("A1", 1))}, name="ok.xlsx"))
    assert not found(shown, "styles")


def test_the_isolated_check_keeps_where_each_finding_is(tmp_path):
    p = book(tmp_path, {"Data": NUMBERS + row(5, f("A5", "SUMME(A2:A4)")), "Scratch": row(1, n("A1", 1))},
             states={"Scratch": "hidden"})
    inline = qa.check(p)
    assert qa.check_isolated(p) == inline
    assert [x["severity"] for x in inline["findings"]] == sorted(
        (x["severity"] for x in inline["findings"]), key=["error", "warn", "info"].index)


# ── CSV ────────────────────────────────────────────────────────────────────

def _csv(tmp_path, text, name="data.csv"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return qa.check(p)


def test_csv_columns_are_checked_where_it_applies(tmp_path):
    res = _csv(tmp_path, "item,price,when,cmd,v\n"
                         'a,1.5,2024-01-31,ok,1\n'
                         'b,"2,5",31/01/2024,=2+3,2\n'
                         'c,3.25,2024-02-01,ok,x\n'
                         'd,4.5,2024-02-02,+44 20 7946 0000,y\n'
                         'e,5.5,2024-02-03,-5,3\n')
    assert found(res, "locale", "warn", "column B writes decimals both")
    assert found(res, "types", "warn", "column C writes dates in 2 different ways")
    inj = found(res, "formulas", "warn", "column D has 1 cell(s) that a spreadsheet will run as a formula")
    assert inj and inj[0]["cell"] == "D3"
    assert found(res, "types", "warn", "column E mixes numbers (3) and text (2)")
    assert not found(res, "types", text="stored as text")      # every CSV value is text
    assert res["ok"]
    assert_located(res)
    clean = _csv(tmp_path, "item,price,when\na,1.5,2024-01-31\nb,2.25,2024-02-01\nc,3,2024-03-01\n", "ok.csv")
    assert clean["ok"] and clean["findings"] == [], clean
