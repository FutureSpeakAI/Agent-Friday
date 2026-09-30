"""Data mode: numbers are computed on this computer first, and the hosts may
say only numbers that appear in the computed facts."""
from __future__ import annotations

import pytest

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
    trend = _fact(an, "per month")
    assert "January 2026" in trend["text"] and "April 2026" in trend["text"]
    assert "2,000.5" in trend["text"] and "2,500" in trend["text"]
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


def test_a_spreadsheet_without_its_reader_says_so(tmp_path, monkeypatch):
    p = tmp_path / "book.xlsx"
    p.write_bytes(b"PK\x03\x04not really")
    import pandas as pd

    def no_openpyxl(*a, **k):
        raise ImportError("Missing optional dependency 'openpyxl'")
    monkeypatch.setattr(pd, "read_excel", no_openpyxl)
    with pytest.raises(SourceError) as exc:
        pdm.load_frame(p)
    assert "openpyxl" in exc.value.user_message
