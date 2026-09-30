"""Data mode: compute first, then talk only about what was computed.

`analyse_refs` loads each dataset on this computer with pandas and produces
numbered facts (F1…Fn). Each fact carries the sentence the writer sees, the
pandas expression that produced it, and the numbers in that sentence. The
writer is given the facts and never the rows.

`untraceable_numbers` is the check applied to every scripted line: each number
the line says (digits, percentages, "1.2 million", "forty-two") must match a
number in some computed fact, within the precision the line states it at. A
line that fails is cut before it is spoken.

Charts are small, self-describing SVGs (a <title> and a <desc> that lists the
plotted numbers), drawn here without a plotting library.
"""

from __future__ import annotations

import math
import re
from html import escape
from pathlib import Path

from agent_friday.services.podcast_sources import SourceError

MAX_ROWS = 2_000_000
MAX_NUMERIC = 6
MAX_CATEGORICAL = 3


# ── loading ─────────────────────────────────────────────────────────────────

def load_frame(path: Path):
    import pandas as pd
    ext = path.suffix.lower()
    try:
        if ext in (".csv", ".tsv", ".txt"):
            return pd.read_csv(path, sep=None, engine="python", nrows=MAX_ROWS)
        if ext in (".xlsx", ".xlsm", ".xls"):
            try:
                return pd.read_excel(path, nrows=MAX_ROWS)
            except ImportError as e:
                raise SourceError(
                    "%s is a spreadsheet, and reading spreadsheets needs the "
                    "'openpyxl' package, which is not installed. Save it as CSV, "
                    "or install openpyxl." % path.name) from e
        if ext == ".parquet":
            return pd.read_parquet(path)
        if ext == ".json":
            return pd.read_json(path)
    except SourceError:
        raise
    except Exception as e:
        raise SourceError("could not read %s as a table (%s)" % (path.name, str(e)[:120])) from e
    raise SourceError("%s is not a table Friday can read" % path.name)


def _dataset_path(ref: dict) -> Path:
    raw = str(ref.get("path") or ref.get("filename") or "").strip()
    if not raw:
        raise SourceError("a dataset needs a path")
    p = Path(raw).expanduser()
    if not p.is_absolute() and ref.get("filename"):
        from agent_friday.core import CREATIONS_DIR
        from agent_friday.paths import contained
        p = contained(CREATIONS_DIR, raw)
    p = p.resolve()
    if not p.is_file():
        raise SourceError("dataset not found: %s" % p.name)
    return p


# ── formatting ──────────────────────────────────────────────────────────────

def fmt(v) -> str:
    """How a number is shown to the writer, and so how it may be spoken."""
    try:
        v = float(v)
    except Exception:
        return str(v)
    if math.isnan(v) or math.isinf(v):
        return "n/a"
    if v == int(v) and abs(v) < 1e15:
        return "{:,}".format(int(v))
    if abs(v) >= 1000:
        s = "{:,.1f}".format(v)
        return s[:-2] if s.endswith(".0") else s
    if abs(v) >= 1:
        return ("%.2f" % v).rstrip("0").rstrip(".")
    return ("%.3g" % v)


def pct(part, whole) -> str:
    return "%.1f%%" % (100.0 * float(part) / float(whole)) if whole else "n/a"


# ── number parsing (shared by facts and by the line check) ─────────────────

_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate(
    "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}
_SCALES = {"hundred": 100, "thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}
_MULT = {"thousand": 1e3, "k": 1e3, "million": 1e6, "m": 1e6, "mn": 1e6,
         "billion": 1e9, "bn": 1e9, "b": 1e9, "trillion": 1e12}

_DIGITS_RE = re.compile(
    r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*(%|percent\b|per cent\b)?"
    r"(?:\s*(thousand|million|billion|trillion|bn|mn|k|m)\b)?", re.I)
_WORDNUM_RE = re.compile(
    r"\b((?:(?:%s)(?:[\s-]+|\s+and\s+)?)+(?:point(?:\s+(?:%s))+)?)(\s*(?:%%|percent\b|per cent\b))?"
    % ("|".join(list(_UNITS) + list(_TENS) + list(_SCALES)),
       "|".join(list(_UNITS)[:10])), re.I)


def _words_value(phrase: str):
    words = re.split(r"[\s-]+", phrase.lower().strip())
    words = [w for w in words if w and w != "and"]
    total, cur, frac, in_frac, n = 0.0, 0.0, "", False, 0
    for w in words:
        if w == "point":
            in_frac = True
            continue
        if in_frac:
            if w in _UNITS and _UNITS[w] < 10:
                frac += str(_UNITS[w])
            continue
        n += 1
        if w in _UNITS:
            cur += _UNITS[w]
        elif w in _TENS:
            cur += _TENS[w]
        elif w == "hundred":
            cur = (cur or 1) * 100
        elif w in _SCALES:
            total += (cur or 1) * _SCALES[w]
            cur = 0
    value = total + cur
    if frac:
        value += float("0." + frac)
    return value, n, len(frac)


def numbers_in(text: str) -> list[tuple[float, float, str]]:
    """Every stated number as (value, tolerance, as written).

    Small counting words and digits (zero to ten, with no unit or percent) are
    left out: "two things", "chapter 3". Everything else is a claim.
    """
    out = []
    t = text or ""
    for m in _DIGITS_RE.finditer(t):
        whole, dec, per, mult = m.group(1), m.group(2) or "", m.group(3), m.group(4)
        v = float(whole.replace(",", "") + dec)
        places = len(dec) - 1 if dec else 0
        scale = _MULT.get((mult or "").lower(), 1.0)
        if mult and mult.lower() in ("m", "b", "k") and not dec and v > 999:
            scale = 1.0          # "2024 m" is not two trillion
        if not dec and not per and not mult and v <= 10:
            continue
        tol = 0.5 * (10 ** -places) * scale
        out.append((v * scale, tol, m.group(0).strip()))
    for m in _WORDNUM_RE.finditer(t):
        phrase = m.group(1).strip(" -")
        if not phrase:
            continue
        v, nwords, places = _words_value(phrase)
        per = m.group(2)
        if nwords <= 1 and not per and v <= 10 and not places:
            continue
        tol = 0.5 * (10 ** -places) if places else 0.5
        if v >= 1000:
            tol = max(tol, v * 0.005)
        out.append((v, tol, m.group(0).strip()))
    return out


_PLAIN_NUM_RE = re.compile(r"(?<![\w.,])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?")


def fact_values(text: str) -> list[float]:
    vals = [v for v, _t, _r in numbers_in(text)]
    # Small numbers inside a fact are real values there ("3 regions"). Read
    # whole, separators included: "1,075" is one number, never 1 and 75.
    for m in _PLAIN_NUM_RE.finditer(text or ""):
        vals.append(float(m.group(1).replace(",", "") + (m.group(2) or "")))
    return vals


def untraceable_numbers(text: str, facts: list[dict]) -> list[str]:
    """The numbers in `text` that no computed fact contains."""
    pool = []
    for f in facts or []:
        pool += [abs(v) for v in (f.get("values") or fact_values(f.get("text", "")))]
    bad = []
    for v, tol, raw in numbers_in(text):
        v = abs(v)
        if not any(abs(v - p) <= max(tol, abs(p) * 0.005) for p in pool):
            bad.append(raw)
    return bad


# ── analysis ────────────────────────────────────────────────────────────────

_ID_NAME = re.compile(r"(^|[_\s])(id|uuid|guid|key|index|zip|postcode|phone)$", re.I)


def _is_id_like(df, col) -> bool:
    import pandas as pd
    s = df[col]
    if _ID_NAME.search(str(col)):
        return True
    if pd.api.types.is_integer_dtype(s) and s.nunique(dropna=True) == len(s) and len(s) > 20:
        return True
    return False


def _datetime_cols(df):
    import pandas as pd
    out = []
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_datetime64_any_dtype(s):
            out.append(c)
            continue
        textual = pd.api.types.is_object_dtype(s) or pd.api.types.is_string_dtype(s)
        if textual and s.notna().sum() >= 3:
            sample = s.dropna().astype(str).head(200)
            if not sample.str.contains(r"\d{4}|\d{1,2}[/-]\d{1,2}", regex=True).mean() > 0.9:
                continue
            try:
                parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
            except Exception:
                continue
            if parsed.notna().mean() > 0.9:
                out.append(c)
    return out


class _Facts:
    def __init__(self, start: int = 1):
        self.items: list[dict] = []
        self.n = start

    def add(self, text: str, expr: str, **extra) -> str:
        fid = "F%d" % self.n
        self.n += 1
        self.items.append(dict({"id": fid, "text": text, "expr": expr,
                                "values": fact_values(text)}, **extra))
        return fid


def analyse_frame(df, name: str, facts: _Facts, chart_dir: Path | None,
                  charts: list) -> dict:
    import pandas as pd
    rows, cols = len(df), len(df.columns)
    facts.add("%s has %s rows and %s columns." % (name, fmt(rows), fmt(cols)),
              "len(df), len(df.columns)")

    dt_cols = _datetime_cols(df)
    num_cols = [c for c in df.columns
                if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])
                and c not in dt_cols and not _is_id_like(df, c) and df[c].notna().sum() > 0]
    num_cols.sort(key=lambda c: -df[c].notna().sum())
    num_cols = num_cols[:MAX_NUMERIC]
    cat_cols = [c for c in df.columns
                if c not in num_cols and c not in dt_cols and not _is_id_like(df, c)
                and 2 <= df[c].nunique(dropna=True) <= max(30, int(rows * 0.05))
                and not pd.api.types.is_float_dtype(df[c])]
    cat_cols = cat_cols[:MAX_CATEGORICAL]
    label_col = cat_cols[0] if cat_cols else (dt_cols[0] if dt_cols else None)

    for c in num_cols:
        s = df[c].dropna()
        facts.add("%s: total %s, average %s, median %s, lowest %s, highest %s (over %s rows with a value)."
                  % (c, fmt(s.sum()), fmt(s.mean()), fmt(s.median()), fmt(s.min()),
                     fmt(s.max()), fmt(len(s))),
                  "df[%r].sum()/mean()/median()/min()/max()" % c)
        if label_col is not None:
            top = df.loc[df[c].idxmax()]
            lab = top[label_col]
            if pd.notna(lab):
                if hasattr(lab, "strftime"):
                    lab = lab.strftime("%d %B %Y")
                facts.add("The highest %s, %s, is in the row where %s is %s."
                          % (c, fmt(top[c]), label_col, lab),
                          "df.loc[df[%r].idxmax(), %r]" % (c, label_col))

    main = num_cols[0] if num_cols else None
    for c in cat_cols:
        vc = df[c].value_counts(dropna=True).head(5)
        parts = ["%s %s (%s)" % (k, fmt(v), pct(v, rows)) for k, v in vc.items()]
        fid = facts.add("Most common values of %s, by number of rows: %s." % (c, "; ".join(parts)),
                        "df[%r].value_counts().head(5)" % c)
        if main is not None:
            g = df.groupby(c)[main].sum().sort_values(ascending=False).head(5)
            whole = df[main].sum()
            parts = ["%s %s (%s of all)" % (k, fmt(v), pct(v, whole)) for k, v in g.items()]
            gid = facts.add("Total %s by %s: %s." % (main, c, "; ".join(parts)),
                            "df.groupby(%r)[%r].sum().nlargest(5)" % (c, main))
            charts.append(_bar_chart(chart_dir, len(charts) + 1,
                                     "Total %s by %s" % (main, c),
                                     [(str(k), float(v)) for k, v in g.items()], [gid]))
        elif len(vc) > 1:
            charts.append(_bar_chart(chart_dir, len(charts) + 1, "Rows by %s" % c,
                                     [(str(k), float(v)) for k, v in vc.items()], [fid]))

    if dt_cols and main is not None:
        dc = dt_cols[0]
        d = df[[dc, main]].copy()
        d[dc] = pd.to_datetime(d[dc], errors="coerce", format="mixed")
        d = d.dropna()
        if len(d) >= 3:
            span = (d[dc].max() - d[dc].min()).days
            if span > 3 * 366:
                rule, label, lf = "YS", "year", "%Y"
            elif span > 90:
                rule, label, lf = "MS", "month", "%B %Y"
            else:
                rule, label, lf = "D", "day", "%d %B %Y"
            series = d.set_index(dc)[main].resample(rule).sum()
            series = series[series.index.notna()]
            if len(series) >= 2:
                first, last = series.iloc[0], series.iloc[-1]
                peak_at = series.idxmax()
                change = ("up %s" % pct(last - first, abs(first)) if last >= first
                          else "down %s" % pct(first - last, abs(first))) if first else "n/a"
                tid = facts.add(
                    "Total %s per %s went from %s in %s to %s in %s (%s). The highest %s was %s, with %s."
                    % (main, label, fmt(first), series.index[0].strftime(lf), fmt(last),
                       series.index[-1].strftime(lf), change, label, peak_at.strftime(lf),
                       fmt(series.max())),
                    "df.set_index(%r)[%r].resample(%r).sum()" % (dc, main, rule))
                facts.add("The data covers %s to %s."
                          % (d[dc].min().strftime("%d %B %Y"), d[dc].max().strftime("%d %B %Y")),
                          "df[%r].min(), df[%r].max()" % (dc, dc))
                charts.append(_line_chart(chart_dir, len(charts) + 1,
                                          "Total %s per %s" % (main, label),
                                          [(i.strftime(lf), float(v)) for i, v in series.items()],
                                          [tid]))

    if len(num_cols) >= 2:
        corr = df[num_cols].corr(numeric_only=True)
        pairs = []
        for i, a in enumerate(num_cols):
            for b in num_cols[i + 1:]:
                r = corr.loc[a, b]
                if pd.notna(r) and abs(r) >= 0.5:
                    pairs.append((abs(r), a, b, r))
        for _ar, a, b, r in sorted(pairs, reverse=True)[:3]:
            facts.add("%s and %s moved %s (correlation %.2f). That is an association, not a cause."
                      % (a, b, "together" if r > 0 else "in opposite directions", r),
                      "df[[%r, %r]].corr()" % (a, b))

    for c in df.columns:
        miss = int(df[c].isna().sum())
        if rows and miss / rows > 0.05:
            facts.add("%s is empty in %s rows (%s)." % (c, fmt(miss), pct(miss, rows)),
                      "df[%r].isna().sum()" % c)

    return {"name": name, "rows": rows, "columns": [str(c) for c in df.columns][:60],
            "numeric": [str(c) for c in num_cols], "categorical": [str(c) for c in cat_cols],
            "dates": [str(c) for c in dt_cols]}


def analyse_refs(refs: list[dict], chart_dir: Path | None) -> dict:
    from agent_friday.services.podcast_sources import is_dataset
    facts, charts, files = _Facts(), [], []
    for ref in refs:
        if not is_dataset(ref):
            continue
        p = _dataset_path(ref)
        df = load_frame(p)
        if df is None or df.empty:
            raise SourceError("%s has no rows" % p.name)
        files.append(analyse_frame(df, p.name, facts, chart_dir, charts))
    if not files:
        raise SourceError("no dataset was given")
    return {"facts": facts.items, "charts": [c for c in charts if c],
            "summary": {"files": files}}


# ── charts ──────────────────────────────────────────────────────────────────

_W, _H = 640, 360


def _svg(title: str, desc: str, body: str) -> str:
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" role="img" '
            'aria-labelledby="t d" font-family="system-ui, sans-serif" font-size="13">'
            '<title id="t">%s</title><desc id="d">%s</desc>'
            '<rect width="100%%" height="100%%" fill="#ffffff"/>'
            '<text x="16" y="26" font-size="16" font-weight="600" fill="#1f2328">%s</text>%s</svg>'
            % (_W, _H, escape(title), escape(desc), escape(title), body))


def _save_chart(chart_dir, n, svg, title, fact_ids):
    if chart_dir is None:
        return {"id": "C%d" % n, "title": title, "facts": fact_ids, "file": None}
    chart_dir = Path(chart_dir)
    chart_dir.mkdir(parents=True, exist_ok=True)
    name = "chart-%d.svg" % n
    (chart_dir / name).write_text(svg, encoding="utf-8")
    return {"id": "C%d" % n, "title": title, "facts": fact_ids, "file": name}


def _bar_chart(chart_dir, n, title, items, fact_ids):
    if not items:
        return None
    top = max(abs(v) for _k, v in items) or 1.0
    left, right, y0, bh = 170, 90, 50, min(44, (_H - 70) // len(items))
    body = []
    for i, (k, v) in enumerate(items):
        y = y0 + i * bh
        w = (_W - left - right) * abs(v) / top
        body.append('<text x="%d" y="%d" text-anchor="end" fill="#1f2328">%s</text>'
                    % (left - 8, y + bh * 0.62, escape(k[:24])))
        body.append('<rect x="%d" y="%d" width="%.1f" height="%d" rx="3" fill="#2f6feb"/>'
                    % (left, y + 4, w, bh - 10))
        body.append('<text x="%.1f" y="%d" fill="#1f2328">%s</text>'
                    % (left + w + 6, y + bh * 0.62, escape(fmt(v))))
    desc = "; ".join("%s: %s" % (k, fmt(v)) for k, v in items)
    return _save_chart(chart_dir, n, _svg(title, desc, "".join(body)), title, fact_ids)


def _line_chart(chart_dir, n, title, points, fact_ids):
    if len(points) < 2:
        return None
    vals = [v for _k, v in points]
    lo, hi = min(vals + [0.0]), max(vals)
    span = (hi - lo) or 1.0
    left, right, top, bottom = 70, 24, 50, 50
    pw, ph = _W - left - right, _H - top - bottom

    def xy(i, v):
        return (left + pw * i / (len(points) - 1), top + ph * (1 - (v - lo) / span))
    path = " ".join(("M" if i == 0 else "L") + "%.1f,%.1f" % xy(i, v)
                    for i, (_k, v) in enumerate(points))
    body = ['<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#d0d7de"/>'
            % (left, top + ph, left + pw, top + ph),
            '<path d="%s" fill="none" stroke="#2f6feb" stroke-width="2.5"/>' % path,
            '<text x="%d" y="%d" text-anchor="end" fill="#57606a">%s</text>'
            % (left - 6, top + 4, escape(fmt(hi))),
            '<text x="%d" y="%d" text-anchor="end" fill="#57606a">%s</text>'
            % (left - 6, top + ph + 4, escape(fmt(lo))),
            '<text x="%d" y="%d" fill="#57606a">%s</text>'
            % (left, _H - 18, escape(points[0][0])),
            '<text x="%d" y="%d" text-anchor="end" fill="#57606a">%s</text>'
            % (left + pw, _H - 18, escape(points[-1][0]))]
    desc = "; ".join("%s: %s" % (k, fmt(v)) for k, v in points[:60])
    return _save_chart(chart_dir, n, _svg(title, desc, "".join(body)), title, fact_ids)
