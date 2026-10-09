"""Data mode: numbers are computed on this computer first, and the hosts may
say only numbers that appear in the computed facts."""
from __future__ import annotations

import pytest

pytest.importorskip("pandas", reason="data mode reads tables with pandas (podcast extra)")

from agent_friday.services import podcast_data as pdm
from agent_friday.services import podcast_engine as pe
from agent_friday.services.podcast_sources import SourceError


CSV = """order_id,date,region,sales,units
1001,2026-01-05,North,1200.50,10
1002,2026-01-19,South,800,8
1003,2026-02-02,North,1500,12
1004,2026-02-16,West,400,3
1005,2026-03-02,South,950,9
1006,2026-03-16,North,2100,20
1007,2026-04-06,West,700,6
1008,2026-04-20,North,1800,15
"""


@pytest.fixture
def dataset(tmp_path):
    p = tmp_path / "sales.csv"
    p.write_text(CSV, encoding="utf-8")
    return p


def _analysis(dataset, tmp_path):
    return pdm.analyse_refs([{"kind": "dataset", "path": str(dataset)}], tmp_path / "charts")


def _fact(an, needle):
    return next(f for f in an["facts"] if needle in f["text"])


def test_facts_are_computed_numbered_and_carry_their_expression(dataset, tmp_path):
    an = _analysis(dataset, tmp_path)
    ids = [f["id"] for f in an["facts"]]
    assert ids == ["F%d" % i for i in range(1, len(ids) + 1)]
    assert _fact(an, "rows and").get("text") == "sales.csv has 8 rows and 5 columns."
    s = _fact(an, "sales: total")
    assert "total 9,450.5" in s["text"] and "highest 2,100" in s["text"]
    assert s["expr"].startswith("df['sales']")
    by_region = _fact(an, "Total sales by region")
    assert "North 6,600.5 (69.8% of all)" in by_region["text"]


def test_id_columns_are_not_averaged(dataset, tmp_path):
    an = _analysis(dataset, tmp_path)
    assert not any("order_id" in f["text"] and "average" in f["text"] for f in an["facts"])
    assert "order_id" not in an["summary"]["files"][0]["numeric"]


def test_a_time_trend_is_computed_and_charted(dataset, tmp_path):
    an = _analysis(dataset, tmp_path)
    trend = _fact(an, "Average sales per month")
    assert "1,000.2 in January 2026" in trend["text"] and "1,250 in April 2026" in trend["text"]
    assert "up 25.0%" in trend["text"]
    assert "Rows per month went from 2 in January 2026 to 2 in April 2026" in _fact(an, "Rows per")["text"]
    files = {c["file"] for c in an["charts"]}
    assert files and all((tmp_path / "charts" / f).is_file() for f in files)
    svg = (tmp_path / "charts" / an["charts"][0]["file"]).read_text(encoding="utf-8")
    assert "<title" in svg and "<desc" in svg and "<script" not in svg


def test_correlation_is_voiced_as_association_never_cause(dataset, tmp_path):
    an = _analysis(dataset, tmp_path)
    c = _fact(an, "moved together")
    assert "not a cause" in c["text"]


@pytest.mark.parametrize("line,ok", [
    ("Total sales came to 9,450.5.", True),
    ("That's about 9,451 in sales.", True),            # rounding what was computed
    ("North brought in 69.8 percent of it.", True),
    ("North brought in 70 percent of it.", True),       # stated at whole-percent precision
    ("Sales reached 12,000.", False),                   # invented
    ("North was 75 percent of sales.", False),          # invented share
    ("Sales grew thirty-three percent.", False),        # invented, spelled out
    ("There are three regions to talk about.", True),   # a count word, not a claim
    ("Two thousand one hundred was the best order.", True),
])
def test_every_spoken_number_must_be_in_the_facts(dataset, tmp_path, line, ok):
    an = _analysis(dataset, tmp_path)
    assert (pdm.untraceable_numbers(line, an["facts"]) == []) is ok


def test_an_invented_number_is_cut_before_it_is_spoken(dataset, tmp_path):
    an = _analysis(dataset, tmp_path)
    valid = {f["id"] for f in an["facts"]}
    kept, cut = pe.clean_lines([
        {"speaker": "a", "text": "Sales totalled 9,450.5.", "cites": ["F2"]},
        {"speaker": "b", "text": "And they doubled to 18,901 next year.", "cites": ["F2"]},
    ], valid, facts=an["facts"])
    assert [k["text"] for k in kept] == ["Sales totalled 9,450.5."]
    assert "18,901" in cut[0]["reason"]


def test_the_data_writer_is_told_to_do_no_arithmetic():
    ep = {"show": "S", "hosts": pe.DEFAULTS["hosts"], "mode": "data"}
    prompt = pe._system_prompt(ep)
    assert "Do no arithmetic of your own" in prompt and "never \"caused\"" in prompt


def test_the_writer_sees_facts_not_rows(dataset, tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(core, "_load_settings", lambda: {})
    ep = pe.create([{"kind": "dataset", "path": str(dataset)}], origin="routine")
    assert ep["mode"] == "data" and ep["privacy"] == "private"
    ep = pe.load(ep["id"])
    docs = pe._gather(ep)
    assert all(d["sid"].startswith("F") for d in docs)
    block = pe._source_block(docs)
    assert "1001" not in block and "2026-01-19" not in block   # no raw rows
    assert ep["charts"]


def test_a_file_that_is_not_really_a_spreadsheet_says_so(tmp_path, monkeypatch):
    p = tmp_path / "book.xlsx"
    p.write_bytes(b"PK\x03\x04not really")
    import pandas as pd

    def no_openpyxl(*a, **k):
        raise ImportError("Missing optional dependency 'openpyxl'")
    monkeypatch.setattr(pd, "read_excel", no_openpyxl)
    with pytest.raises(SourceError) as exc:
        pdm.load_frame(p)
    assert "book.xlsx" in exc.value.user_message and "spreadsheet" in exc.value.user_message


WAITS = """request_id,opened,neighborhood,days_to_close
1,2026-01-05,Eastside,9
2,2026-01-06,Eastside,7
3,2026-01-07,Downtown,2
4,2026-01-08,Downtown,3
5,2026-01-09,Downtown,4
6,2026-01-10,Downtown,3
7,2026-05-05,Riverside,1
8,2026-05-06,Downtown,2
"""


@pytest.fixture
def waits(tmp_path):
    p = tmp_path / "waits.csv"
    p.write_text(WAITS, encoding="utf-8")
    return pdm.analyse_refs([{"kind": "dataset", "path": str(p)}], tmp_path / "charts")


def test_which_group_waits_longest_is_answered_by_the_average_not_the_total(waits):
    """Downtown has the biggest TOTAL (14 days over 5 requests), Eastside the
    longest AVERAGE wait (8 days over 2). A total mixes volume with duration."""
    avg = _fact(waits, "Average days to close by neighborhood")
    assert avg["text"].index("Eastside 8") < avg["text"].index("Downtown 2.8")
    assert "(2 rows)" in avg["text"] and "(5 rows)" in avg["text"]
    assert any("Average days to close by neighborhood" == c["title"] for c in waits["charts"])


def test_volume_over_time_is_its_own_fact(waits):
    rows = _fact(waits, "Rows per")
    assert "6 in January 2026" in rows["text"] and "2 in May 2026" in rows["text"]
    avg = _fact(waits, "Average days to close per")
    assert "4.67 in January 2026" in avg["text"] and "1.5 in May 2026" in avg["text"]


def test_column_names_are_written_as_words(waits):
    assert not any("days_to_close" in f["text"] for f in waits["facts"])


def test_the_top_group_comparison_is_computed_not_eyeballed(waits):
    avg = _fact(waits, "Average days to close by neighborhood")
    assert "Eastside's average is 2.86 times the next highest (Downtown)" in avg["text"]


@pytest.mark.parametrize("line,ok", [
    ("Eastside's wait is roughly double the rest.", False),     # a ratio nobody computed
    ("That is nearly half of all requests.", False),
    ("Eastside waits twice as long.", False),
    ("Eastside's average is 2.86 times the next highest.", True),  # the computed ratio
    ("That is a gap of six months.", False),                    # derived, with a unit
    ("We have three chapters today.", True),                    # a count word, not a claim
])
def test_ratios_and_small_numbers_with_units_must_be_computed(waits, line, ok):
    assert (pdm.untraceable_numbers(line, waits["facts"]) == []) is ok


def test_numbers_that_are_names_are_not_claims(tmp_path):
    """"311" in city_311.csv is the service's name; "Route 66" is a category."""
    p = tmp_path / "city_311.csv"
    p.write_text("opened,route,days\n2026-01-01,Route 66,3\n2026-01-02,Route 9,4\n"
                 "2026-01-03,Route 66,5\n", encoding="utf-8")
    an = pdm.analyse_refs([{"kind": "dataset", "path": str(p)}], tmp_path / "c")
    assert pdm.untraceable_numbers("Every 311 request on Route 66.", an["facts"]) == []
    assert pdm.untraceable_numbers("It took 312 days.", an["facts"]) == ["312"]
    # The name list is not a source a line can cite.
    assert all(f["id"].startswith("F") for f in an["facts"] if not f.get("names_only"))


def test_the_names_entry_is_never_a_source_or_a_stored_fact(tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(core, "_load_settings", lambda: {})
    p = tmp_path / "city_311.csv"
    p.write_text("opened,route,days\n2026-01-01,Route 66,3\n2026-01-02,Route 9,4\n", encoding="utf-8")
    ep = pe.load(pe.create([{"kind": "dataset", "path": str(p)}], origin="routine")["id"])
    docs = pe._gather(ep)
    assert all(d["sid"].startswith("F") for d in docs)
    assert any(f.get("names_only") for f in ep["_facts"])


def test_charts_draw_in_the_brand_palette_when_the_brand_module_is_present(tmp_path, monkeypatch):
    """Colours come from agent_friday.brand (docs/brand/BRAND.md), not a copy."""
    import sys
    import types
    fake = types.SimpleNamespace(SURFACE="#0a0e1a", CYAN="#00d4ff", TEXT="rgba(255,255,255,0.86)",
                                 TEXT_DIM="rgba(255,255,255,0.46)", GLASS_EDGE="rgba(255,255,255,0.06)")
    monkeypatch.setitem(sys.modules, "agent_friday.brand", fake)
    import agent_friday
    monkeypatch.setattr(agent_friday, "brand", fake, raising=False)
    c = pdm._bar_chart(tmp_path, 1, "T", [("a", 2.0), ("b", 1.0)], ["F1"])
    svg = (tmp_path / c["file"]).read_text(encoding="utf-8")
    assert 'fill="#0a0e1a"' in svg and 'fill="#00d4ff"' in svg and "#2f6feb" not in svg


def _xlsx(path, rows, date_col=None):
    """A minimal real .xlsx, written by hand: shared strings, numbers, and an
    Excel-serial date column styled as a date (numFmt 14)."""
    import zipfile
    from xml.sax.saxutils import escape as x
    strings, cells_xml = [], []

    def col(i):
        return "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[i]
    for r, row in enumerate(rows, 1):
        cs = []
        for c, v in enumerate(row):
            ref = "%s%d" % (col(c), r)
            if isinstance(v, str):
                if v not in strings:
                    strings.append(v)
                cs.append('<c r="%s" t="s"><v>%d</v></c>' % (ref, strings.index(v)))
            else:
                style = ' s="1"' if (date_col is not None and c == date_col and r > 1) else ""
                cs.append('<c r="%s"%s><v>%s</v></c>' % (ref, style, v))
        cells_xml.append('<row r="%d">%s</row>' % (r, "".join(cs)))
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rns = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("xl/workbook.xml", '<workbook %s %s><sheets><sheet name="Data" sheetId="1" r:id="rId1"/></sheets></workbook>' % (ns, rns))
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/sharedStrings.xml", '<sst %s>%s</sst>' % (ns, "".join("<si><t>%s</t></si>" % x(s) for s in strings)))
        z.writestr("xl/styles.xml", '<styleSheet %s><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>' % ns)
        z.writestr("xl/worksheets/sheet1.xml", '<worksheet %s><sheetData>%s</sheetData></worksheet>' % (ns, "".join(cells_xml)))


def test_a_spreadsheet_is_read_on_this_computer_without_extra_packages(tmp_path, monkeypatch):
    import pandas as pd

    def no_openpyxl(*a, **k):
        raise ImportError("Missing optional dependency 'openpyxl'")
    monkeypatch.setattr(pd, "read_excel", no_openpyxl)
    p = tmp_path / "sales.xlsx"
    # 46023 is 2026-01-01 as an Excel serial date.
    _xlsx(p, [["opened", "region", "sales"], [46023, "North", 100], [46054, "South", 250.5],
              [46082, "North", 300]], date_col=0)
    df = pdm.load_frame(p)
    assert list(df.columns) == ["opened", "region", "sales"]
    assert df["region"].tolist() == ["North", "South", "North"]
    assert df["sales"].tolist() == [100, 250.5, 300]
    assert str(df["opened"].iloc[0].date()) == "2026-01-01"
    an = pdm.analyse_refs([{"kind": "dataset", "path": str(p)}], tmp_path / "c")
    assert "total 650.5" in _fact(an, "sales: total")["text"]
