"""Accessible charts (WCAG 2.2 1.1.1 and 1.4.1; north star NS-33.1-12).

The shared chart renderer (static/friday_chart.js: the artifact panel and every
published chart page) told series apart by colour alone and named its SVG only
by the title. Now:
  - each series also has its own line dash, marker shape and bar or slice
    pattern, and the legend swatch draws the same;
  - the SVG is named by a sentence that says what it shows (kind, series, where
    each is lowest and highest; a pie's parts with their shares);
  - the same data is offered as a table: a "Table" button in the panel, and a
    table under the chart on a published page.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "static" / "friday_chart.js"
node = shutil.which("node")

SPEC = {"title": "Calls", "columns": ["Month", "A", "B", "C"],
        "rows": [["Jan", 1, 4, 2], ["Feb", 3, 2, 5], ["Mar", 2, 6, 1]]}

PROBE = r"""
require(process.argv[2]);
const C = globalThis.FridayChart, spec = JSON.parse(process.argv[3]), out = {};
for (const t of ['bar', 'line', 'area', 'scatter', 'pie', 'donut']) {
  const r = C.renderSVG(Object.assign({}, spec, { type: t }), 480, 300);
  out[t] = { svg: r.svg, legend: r.legend, swatches: r.legend.map(l => C.legendSwatch(l)) };
}
out.describe = C.describe(Object.assign({}, spec, { type: 'line' }));
out.describePie = C.describe(Object.assign({}, spec, { type: 'pie' }));
out.table = C.tableHTML(spec);
out.gappy = C.tableHTML({ type: 'line', series: [{ name: 'x', data: [[1, 2], [2, 5]] }, { name: 'y', data: [[2, 1], [3, 9]] }] });
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def drawn():
    if not node:
        pytest.skip("node is not on PATH")
    with tempfile.TemporaryDirectory() as d:
        js = os.path.join(d, "probe.js")
        with open(js, "w", encoding="utf-8") as fh:
            fh.write(PROBE)
        out = subprocess.run([node, js, str(CHART), json.dumps(SPEC)], capture_output=True, text=True,
                             encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _distinct(values):
    return len(set(values)) == len(values)


def test_series_differ_by_more_than_colour(drawn):
    for kind, d in drawn.items():
        if not isinstance(d, dict):
            continue
        legend = d["legend"]
        assert len(legend) == 3, kind
        if kind in ("line", "area"):
            assert _distinct([l["dash"] for l in legend]) and _distinct([l["marker"] for l in legend]), kind
            assert d["svg"].count("stroke-dasharray") == 2, kind     # the first series is solid, the others dashed
        elif kind == "scatter":
            assert _distinct([l["marker"] for l in legend]), kind
            assert "<rect" in d["svg"] and "<polygon" in d["svg"], "square and diamond markers are drawn"
        else:
            assert _distinct([l["pattern"] for l in legend]), kind
            assert d["svg"].count("<pattern ") == 2 and "fill=\"url(#fc" in d["svg"], kind
        assert _distinct(d["swatches"]), kind + ": the legend draws the same differences"


def test_the_svg_says_what_the_chart_shows(drawn):
    assert drawn["describe"] == ("Line chart: Calls. 3 series over 3 Month values, from Jan to Mar. "
                                 "A: lowest 1 at Jan, highest 3 at Feb. B: lowest 2 at Feb, highest 6 at Mar. "
                                 "C: lowest 1 at Mar, highest 5 at Feb.")
    assert drawn["describePie"] == "Pie chart: Calls. 3 parts: Jan 17%, Feb 50%, Mar 33%."
    assert 'aria-label="Line chart: Calls. 3 series' in drawn["line"]["svg"]


def test_the_data_as_a_table(drawn):
    t = drawn["table"]
    assert t.startswith('<table class="fc-table"><caption>Calls: the data</caption>')
    assert '<th scope="col">Month</th><th scope="col">A</th>' in t
    assert '<tr><th scope="row">Feb</th><td class="num">3</td><td class="num">2</td><td class="num">5</td></tr>' in t
    # Series given as points that skip values line up by x, with empty cells where a series has none.
    g = drawn["gappy"]
    assert '<tr><th scope="row">1</th><td class="num">2</td><td class="num"></td></tr>' in g
    assert '<tr><th scope="row">3</th><td class="num"></td><td class="num">9</td></tr>' in g


def test_the_panel_offers_the_table_and_draws_the_swatches():
    s = (ROOT / "static" / "friday_artifacts.js").read_text(encoding="utf-8")
    assert "window.FridayChart.tableHTML(spec)" in s
    assert "'aria-pressed': asTable" in s and "'Show the data as a table'" in s
    assert "window.FridayChart.legendSwatch(l)" in s


def test_a_published_chart_carries_the_table_and_the_swatches():
    s = (ROOT / "src" / "agent_friday" / "services" / "publish_web.py").read_text(encoding="utf-8")
    assert "FridayChart.legendSwatch(l)" in s
    assert '<details class=\\"data\\"><summary>The data as a table</summary>%s</details>' in s
    from agent_friday.services import publish_web
    t = publish_web._data_table(["Month", "A"], [["Jan", 1], ["Feb", None]], "Calls")
    assert t == ('<table><caption>Calls</caption><thead><tr><th scope="col">Month</th><th scope="col">A</th></tr></thead>'
                 '<tbody><tr><th scope="row">Jan</th><td class="num">1</td></tr>'
                 '<tr><th scope="row">Feb</th><td class="num"></td></tr></tbody></table>')


def test_a_published_table_lines_series_up_by_x():
    """Series given as points that skip values: each number sits on its own x (the CSV the same)."""
    from agent_friday.services import publish_web
    cols, rows = publish_web._chart_rows({"series": [{"name": "x", "data": [[1, 2], [2, 5]]},
                                                     {"name": "y", "data": [{"x": 2, "y": 1}, {"x": 3, "y": 9}]}]})
    assert cols == ["x", "x", "y"]
    assert rows == [[1, 2, None], [2, 5, 1], [3, None, 9]]