"""Artifact checks (north star §34.12-1): is a file Friday made sound?

One function, `check(path)`, for the documents Friday makes: Word (.docx),
Excel (.xlsx), PowerPoint (.pptx), PDF, web pages (.html), CSV and images.
It reads the file the way a reader's program would and reports what is
wrong, without a model and without OfficeCLI, so it runs anywhere (CI
included):

  validity       the file opens as what its name says
  bounds         shapes on the slide; cell references inside the sheet;
                 page sizes a printer can use
  formulas       cached errors (#REF!, #DIV/0!...), references to sheets
                 that do not exist
  charts         every chart has at least one series with data
  fonts          fonts that are neither embedded nor common, so the reader's
                 machine may substitute them
  accessibility  pictures without alt text, missing titles, heading levels
                 that skip, no document language, a PDF with no text layer,
                 a page that loads anything from the internet

Spreadsheets (.xlsx, and .csv where it applies) also get the integrity
checks of NS-24.5-1: formulas that do not parse, names a formula uses that
the workbook does not define, named ranges that point at missing sheets or
deleted ranges, numbers and dates stored as text, columns that mix types,
chart series over missing or empty ranges, links to other workbooks and the
internet, decimal commas mixed with decimal points, function names from a
non-English Excel, number formats that change within a column, and hidden
sheets, rows and columns that hold data.

Each finding is {"check", "severity", "message"}, and a spreadsheet finding
also has "sheet" and "cell" (a cell, range, column or rows). "error" means
the file is broken for its reader, "warn" means it works but falls short,
"info" is worth knowing (hidden data, a web link). `ok` is False when there
is any error. Findings name cells, never their contents; they may name
sheets (and sheet names a formula refers to) and the host name of a
hyperlink or external link, so a finding is not free of what the file says
about itself. Office files are zip packages of XML, so they are
read with the standard library; PDFs with pdfplumber (already a core
dependency). Never raises: a file that cannot be read is itself a finding.
"""
from __future__ import annotations

import csv
import io
import re
import struct
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Optional
from xml.etree import ElementTree as ET

#: Bigger packages are not opened (a zip bomb is not a document).
MAX_BYTES = 200 * 1024 * 1024
MAX_PART_BYTES = 16 * 1024 * 1024
MAX_ENTRIES = 10_000
MAX_FINDINGS = 40
#: Excel's own limit on a formula; anything longer is not checked.
MAX_FORMULA = 8192
#: Web pages and CSS are read up to this much (regexes stay linear in it).
MAX_TEXT = 2 * 1024 * 1024
#: PDFs: only the first pages and this much text are read.
MAX_PDF_PAGES = 20
MAX_PDF_TEXT = 200_000

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}

#: Fonts a reader is all but certain to have (Office, Windows, macOS, the
#: common Linux metric-compatible families). Anything else should be embedded.
COMMON_FONTS = frozenset(f.lower() for f in (
    "Calibri", "Calibri Light", "Cambria", "Cambria Math", "Candara", "Consolas", "Constantia",
    "Corbel", "Aptos", "Aptos Display", "Aptos Narrow", "Arial", "Arial Black", "Arial Narrow",
    "Times New Roman", "Courier New", "Georgia", "Verdana", "Tahoma", "Trebuchet MS",
    "Segoe UI", "Segoe UI Semibold", "Segoe UI Light", "Segoe UI Symbol", "Segoe UI Emoji",
    "Helvetica", "Helvetica Neue", "Garamond", "Book Antiqua", "Century Gothic", "Lucida Console",
    "Lucida Sans Unicode", "Palatino Linotype", "Symbol", "Wingdings", "Impact", "Comic Sans MS",
    "Franklin Gothic Medium", "Gill Sans MT", "Liberation Sans", "Liberation Serif",
    "Liberation Mono", "DejaVu Sans", "DejaVu Serif", "DejaVu Sans Mono", "Noto Sans",
    "Noto Serif", "Roboto", "Open Sans", "Source Sans Pro", "Inter", "system-ui", "sans-serif",
    "serif", "monospace"))
#: Theme references (+mj-lt, +mn-ea...) resolve to the theme's fonts.
_THEME_FONT = re.compile(r"^\+(mj|mn)-")

_PLACEHOLDER = re.compile(r"\{\{[^}]{0,80}\}\}|\blorem ipsum\b|\[(insert|placeholder|todo)[^\]]{0,80}\]|"
                          r"\bTODO\b|\bXXX\b", re.I)
_CELL_ERROR = ("#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A", "#NUM!", "#NULL!", "#SPILL!", "#CALC!")
_CELL = re.compile(r"\$?([A-Z]{1,4})\$?(\d{1,8})")
_MAX_ROW, _MAX_COL = 1_048_576, 16_384


def _formula_refs(text: str):
    """(sheet or None, column letters, row digits) for each cell reference in
    a formula. A single linear scan (no backtracking regex over attacker
    text): strings are skipped, 'quoted sheet'! and Sheet! prefixes read."""
    i, n = 0, len(text)
    word = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.$")
    while i < n:
        ch = text[i]
        if ch == '"':                                   # a string literal ("" escapes)
            i += 1
            while i < n:
                if text[i] == '"':
                    if i + 1 < n and text[i + 1] == '"':
                        i += 2
                        continue
                    break
                i += 1
            i += 1
            continue
        sheet = None
        if ch == "'":                                   # 'Sheet name'!A1
            j, name = i + 1, []
            while j < n:
                if text[j] == "'":
                    if j + 1 < n and text[j + 1] == "'":
                        name.append("'")
                        j += 2
                        continue
                    break
                name.append(text[j])
                j += 1
            if j + 1 < n and text[j + 1] == "!":
                sheet, i = "".join(name), j + 2
            else:
                i = j + 1
                continue
        start = i
        while i < n and text[i] in word:
            i += 1
        token = text[start:i]
        if sheet is None and i < n and text[i] == "!" and token:
            sheet, i = token, i + 1
            start = i
            while i < n and text[i] in word:
                i += 1
            token = text[start:i]
        if not token:
            i = max(i, start + 1)
            continue
        if i < n and text[i] == "(":                    # a function name
            continue
        m = _CELL.fullmatch(token)
        if m:
            yield sheet, m.group(1), m.group(2)
        elif sheet is not None:
            yield sheet, "", ""


def _finding(check: str, severity: str, message: str, **where) -> dict:
    """One finding. Spreadsheet findings also say where: `sheet` and `cell`
    (a cell, a range, a column or a row span), each None when it does not
    apply; both keys are present whenever either is given."""
    out = {"check": check, "severity": severity, "message": message[:300]}
    if where:
        for k in ("sheet", "cell"):
            v = where.get(k)
            out[k] = None if v is None else str(v)[:120]
    return out


def _col_number(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n


class _Pkg:
    """A zip package read with limits."""

    def __init__(self, path: Path):
        self.z = zipfile.ZipFile(path)
        infos = self.z.infolist()
        if len(infos) > MAX_ENTRIES:
            raise ValueError("the package has %d parts, more than Friday opens" % len(infos))
        total = sum(i.file_size for i in infos)
        if total > MAX_BYTES:
            raise ValueError("the package unpacks to %d bytes, more than Friday opens" % total)
        self.names = set(self.z.namelist())

    def xml(self, name: str) -> Optional[ET.Element]:
        if name not in self.names:
            return None
        info = self.z.getinfo(name)
        if info.file_size > MAX_PART_BYTES:
            raise ValueError("%s is too large to check" % name)
        return _parse_xml(self.z.read(name), name)

    def matching(self, pattern: str) -> List[str]:
        rx = re.compile(pattern)
        return sorted(n for n in self.names if rx.fullmatch(n))


class _Refused(ValueError):
    pass


def _parse_xml(data: bytes, name: str) -> ET.Element:
    """Parse one document part. A part that declares a DTD (the door to entity
    expansion and external entities) is refused wherever the declaration is."""
    from xml.parsers import expat

    def _no_dtd(*_a):
        raise _Refused("%s declares a DTD, which a document part never needs" % name)
    # A first pass with expat alone (no tree built) refuses any DTD before
    # ElementTree, whose C parser exposes no such hook, ever sees the part.
    scan = expat.ParserCreate()
    scan.StartDoctypeDeclHandler = _no_dtd
    scan.EntityDeclHandler = _no_dtd
    try:
        scan.Parse(data, True)
    except expat.ExpatError as e:
        raise ET.ParseError(str(e))
    return ET.fromstring(data)


def _fonts_findings(used: set, embedded: set, where: str) -> List[dict]:
    out = []
    for font in sorted(used):
        f = font.strip()
        if not f or _THEME_FONT.match(f) or f.lower() in COMMON_FONTS or f.lower() in embedded:
            continue
        out.append(_finding("fonts", "warn", "%s uses the font '%s', which is not embedded and may "
                                            "not be on the reader's machine" % (where, f)))
    return out


def _placeholder_findings(text: str, where: str) -> List[dict]:
    m = _PLACEHOLDER.search(text or "")
    return [_finding("validity", "error", "%s still has placeholder text: %r" % (where, m.group(0)))] if m else []


def _chart_findings(pkg: _Pkg, prefix: str) -> List[dict]:
    out = []
    for name in pkg.matching(re.escape(prefix) + r"charts/chart\d+\.xml"):
        root = pkg.xml(name)
        label = name.rsplit("/", 1)[-1]
        if root is None:
            continue
        series = root.findall(".//c:ser", NS)
        if not series:
            out.append(_finding("charts", "error", "%s has no data series" % label))
            continue
        for i, ser in enumerate(series, 1):
            vals = ser.find("c:val", NS)
            if vals is None:
                vals = ser.find("c:yVal", NS)
            pts = vals.findall(".//c:pt", NS) if vals is not None else []
            ref = vals.find(".//c:f", NS) if vals is not None else None
            if vals is None or (not pts and (ref is None or not (ref.text or "").strip())):
                out.append(_finding("charts", "error", "%s series %d has no values" % (label, i)))
            elif ref is not None and "#REF!" in (ref.text or ""):
                out.append(_finding("charts", "error", "%s series %d points at a deleted range" % (label, i)))
        has_title = root.find(".//c:title", NS) is not None
        auto_deleted = root.find(".//c:autoTitleDeleted", NS)
        if not has_title and (auto_deleted is None or auto_deleted.get("val") in ("1", "true")):
            out.append(_finding("accessibility", "warn", "%s has no title" % label))
    return out


# ── Word ───────────────────────────────────────────────────────────────────

def _check_docx(path: Path) -> List[dict]:
    pkg = _Pkg(path)
    doc = pkg.xml("word/document.xml")
    if doc is None:
        return [_finding("validity", "error", "not a Word document: word/document.xml is missing")]
    out: List[dict] = []
    w = "{%s}" % NS["w"]
    texts = [t.text or "" for t in doc.iter(w + "t")]
    body = "".join(texts)
    if not body.strip() and doc.find(".//wp:docPr", NS) is None:
        out.append(_finding("validity", "error", "the document is empty"))
    out += _placeholder_findings(body, "the document")
    # Headings: Title / Heading N in order, no skipped level.
    last = 0
    for st in doc.iter(w + "pStyle"):
        val = (st.get(w + "val") or "")
        m = re.fullmatch(r"(?i)heading\s*(\d)", val)
        if not m:
            continue
        level = int(m.group(1))
        if last and level > last + 1:
            out.append(_finding("accessibility", "warn", "a heading jumps from level %d to %d" % (last, level)))
        last = level
    for pr in doc.findall(".//wp:docPr", NS):
        if not (pr.get("descr") or pr.get("title") or "").strip():
            out.append(_finding("accessibility", "error", "a picture has no alt text (%s)" % (pr.get("name") or "unnamed")))
    # Language: a w:lang anywhere in the document defaults or runs.
    styles = pkg.xml("word/styles.xml")
    has_lang = any(e.tag == w + "lang" for root in (doc, styles) if root is not None for e in root.iter())
    if not has_lang:
        out.append(_finding("accessibility", "warn", "the document's language is not set"))
    used = set()
    for root in (doc, styles):
        if root is None:
            continue
        for rf in root.iter(w + "rFonts"):
            for attr in ("ascii", "hAnsi", "cs", "eastAsia"):
                v = rf.get(w + attr)
                if v:
                    used.add(v)
    embedded = set()
    table = pkg.xml("word/fontTable.xml")
    if table is not None:
        for font in table.findall("w:font", NS):
            if any(child.tag.startswith(w + "embed") for child in font):
                embedded.add((font.get(w + "name") or "").lower())
    out += _fonts_findings(used, embedded, "the document")
    out += _chart_findings(pkg, "word/")
    return out


# ── Excel ──────────────────────────────────────────────────────────────────

#: Spreadsheet integrity (NS-24.5-1). A column's types are judged once it
#: has this many data cells; numbers and text in one column are "mixed" when
#: the smaller share is at least MIXED_TYPE_SHARE of at least
#: MIXED_TYPE_MIN_CELLS cells. Per sheet, at most MAX_TYPE_CELLS cells are read
#: for the column checks (errors and formulas are read in every cell).
MIN_COLUMN_CELLS = 3
MIXED_TYPE_SHARE = 0.2
MIXED_TYPE_MIN_CELLS = 5
MAX_TYPE_CELLS = 200_000
#: Cell text longer than this is never a number or a date.
_MAX_TYPED_TEXT = 64

#: Error literals a formula may contain.
_ERROR_LITERALS = ("#NULL!", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A",
                   "#GETTING_DATA", "#SPILL!", "#CALC!")
#: Function names from non-English Excel. A file stores English names, so
#: one of these in a stored formula is #NAME? on every machine.
_LOCALIZED_FUNCTIONS = frozenset((
    # German
    "SUMME", "WENN", "MITTELWERT", "ANZAHL", "ANZAHL2", "SVERWEIS", "WVERWEIS", "RUNDEN",
    "SUMMEWENN", "ZÄHLENWENN", "HEUTE", "JETZT", "VERKETTEN", "WENNFEHLER", "UND", "ODER",
    # French
    "SOMME", "MOYENNE", "RECHERCHEV", "RECHERCHEH", "NB", "NBVAL", "NB.SI", "SOMME.SI", "ARRONDI",
    "AUJOURDHUI", "MAINTENANT", "CONCATENER", "SIERREUR", "ET", "OU",
    # Spanish
    "SUMA", "PROMEDIO", "BUSCARV", "BUSCARH", "CONTAR", "CONTARA", "CONTAR.SI", "SUMAR.SI",
    "REDONDEAR", "HOY", "AHORA", "CONCATENAR", "SI.ERROR",
    # Italian
    "SOMMA", "MEDIA", "CERCA.VERT", "CERCA.ORIZZ", "CONTA.NUMERI", "CONTA.SE", "SOMMA.SE",
    "ARROTONDA", "OGGI", "ADESSO", "SE.ERRORE",
    # Dutch
    "SOM", "ALS", "GEMIDDELDE", "VERT.ZOEKEN", "HORIZ.ZOEKEN", "AANTAL", "AFRONDEN", "VANDAAG",
    "NU", "ALS.FOUT",
    # Portuguese
    "SOMA", "SE", "MÉDIA", "PROCV", "PROCH", "CONT.NÚM", "CONT.SE", "SOMASE", "ARRED", "HOJE",
    "AGORA", "SEERRO",
    # Swedish
    "SUMMA", "OM", "MEDEL", "LETARAD", "LETAKOLUMN", "ANTAL", "AVRUNDA", "IDAG",
))
_EXTERNAL_REF = re.compile(r"\[\d+\][^\[\]\"!]{0,100}!|\[\d+\]!")
_RANGE_REF = re.compile(r"(?:'((?:[^']|''){1,100})'|([A-Za-z0-9_.]{1,100}))!"
                        r"\$?([A-Z]{1,3})\$?(\d{1,7})(?::\$?([A-Z]{1,3})\$?(\d{1,7}))?")
_COL_ONLY = re.compile(r"\$?[A-Za-z]{1,3}|\$?\d+")
_NUM_COMMA = re.compile(r"[-+]?\d{1,3}(?:\.\d{3})+,\d+|[-+]?\d+,\d{1,2}|[-+]?\d+,\d{4,}")
_NUM_POINT = re.compile(r"[-+]?[$€£¥]?\d+\.\d+%?|[-+]?[$€£¥]?\(?\d{1,3}(?:,\d{3})+(?:\.\d+)?\)?%?")
_NUM_INT = re.compile(r"[-+]?[$€£¥]?(?:[1-9]\d*|0)%?")
_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]{0,6}\.?"
_DATE_FORMS = (
    ("iso", re.compile(r"\d{4}-\d{1,2}-\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?")),
    ("slash", re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}")),
    ("dot", re.compile(r"\d{1,2}\.\d{1,2}\.\d{2,4}")),
    ("dash", re.compile(r"\d{1,2}-\d{1,2}-\d{4}")),
    ("words", re.compile(_MONTHS + r" \d{1,2},? \d{4}|\d{1,2} " + _MONTHS + r" \d{4}", re.I)),
)
#: Built-in number formats (ECMA-376 18.8.30) by what they show.
_BUILTIN_FORMATS = {0: "general", 1: "number", 2: "number", 3: "number", 4: "number",
                    5: "currency", 6: "currency", 7: "currency", 8: "currency",
                    9: "percent", 10: "percent", 11: "scientific", 12: "fraction", 13: "fraction",
                    37: "number", 38: "number", 39: "number", 40: "number",
                    41: "currency", 42: "currency", 43: "currency", 44: "currency",
                    48: "scientific", 49: "text"}
_BUILTIN_FORMATS.update({i: "date" for i in list(range(14, 23)) + list(range(27, 37))
                         + list(range(45, 48)) + list(range(50, 59))})


def _col_letters(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _split_ref(ref: str):
    """(column number, row number) of an A1 cell reference, or None."""
    m = _CELL.fullmatch(ref or "")
    if not m:
        return None
    return _col_number(m.group(1)), int(m.group(2))


def _formula_scan(text: str) -> dict:
    """One linear pass over a formula: does it parse (brackets and quotes
    balance), the functions it calls, the bare names it uses, and whether it
    separates arguments with ';' (a regional setting, never valid in a file).
    Strings, quoted sheet names, [external or table] parts and error literals
    are skipped, never searched with a backtracking regex."""
    i, n = 0, len(text)
    depth = brace = 0
    funcs: List[str] = []
    names: List[str] = []
    semicolon = False
    parses = True

    def is_word(ch):
        return ch.isalnum() or ch in "_.$\\"

    def skip_ref(j):                     # the reference after Sheet!
        while j < n and (is_word(text[j]) or text[j] in ":#"):
            j += 1
        return j

    def _sheet_range_end(j):             # A:B! -> index of the '!', else None
        k = j
        while k < n and is_word(text[k]):
            k += 1
        return k if k > j and k < n and text[k] == "!" else None

    while i < n:
        ch = text[i]
        if ch == '"' or ch == "'":
            j, closed = i + 1, False
            while j < n:
                if text[j] == ch:
                    if j + 1 < n and text[j + 1] == ch:
                        j += 2
                        continue
                    closed = True
                    break
                j += 1
            if not closed:
                parses = False
                break
            i = j + 1
            if ch == "'" and i < n and text[i] == "!":
                i = skip_ref(i + 1)
            continue
        if ch == "#":
            lit = next((e for e in _ERROR_LITERALS if text.startswith(e, i)
                        or text[i:i + len(e)].upper() == e), None)
            i += len(lit) if lit else 1
            continue
        if ch == "[":
            level, j = 0, i
            while j < n:
                if text[j] == "[":
                    level += 1
                elif text[j] == "]":
                    level -= 1
                    if level == 0:
                        break
                j += 1
            if level:
                parses = False
                break
            i = j + 1
            if i < n and text[i] == "!":         # [1]!MyName: another workbook's name
                i = skip_ref(i + 1)
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                parses = False
                break
        elif ch == "{":
            brace += 1
        elif ch == "}":
            brace -= 1
            if brace < 0:
                parses = False
                break
        elif ch == ";" and brace == 0:
            semicolon = True
        elif ch.isdigit() or ch == ".":
            while i < n and is_word(text[i]):
                i += 1
            continue
        elif is_word(ch):
            start = i
            while i < n and is_word(text[i]):
                i += 1
            token = text[start:i]
            nxt = text[i] if i < n else ""
            if nxt == "(":
                funcs.append(token)
            elif nxt == "!":
                i = skip_ref(i + 1)
            elif nxt == ":" and _sheet_range_end(i + 1) is not None:
                # Sheet1:Sheet2!A1, a 3D reference over a range of sheets.
                i = skip_ref(_sheet_range_end(i + 1) + 1)
            elif nxt != "[":
                names.append(token)
            continue
        i += 1
    if depth or brace:
        parses = False
    return {"parses": parses, "funcs": funcs, "names": names, "semicolon": semicolon}


def _function_base(name: str) -> str:
    up = name.upper()
    for prefix in ("_XLFN._XLWS.", "_XLFN.", "_XLWS."):
        if up.startswith(prefix):
            return up[len(prefix):]
    return up


def _format_category(fmt_id: int, code: Optional[str]) -> str:
    if code is None:
        return _BUILTIN_FORMATS.get(fmt_id, "general")
    code = code[:255]
    if re.search(r"[$€£¥]|\[\$", code):
        return "currency"
    plain = re.sub(r'"[^"]*"|\\.|\[[^\]]*\]', "", code).lower()
    if "%" in plain:
        return "percent"
    if plain.strip() == "@":
        return "text"
    if plain.strip() in ("", "general"):
        return "general"
    if re.search(r"[ymdhs]", plain.replace("general", "")):
        return "date"
    if "e+" in plain or "e-" in plain:
        return "scientific"
    if re.search(r"[0#?]", plain):
        return "number"
    return "general"


def _style_categories(pkg: "_Pkg") -> List[str]:
    """The number-format category of each cell style (xf index)."""
    root = pkg.xml("xl/styles.xml")
    if root is None:
        return []
    custom = {}
    for nf in root.iter("{%s}numFmt" % NS["s"]):
        try:
            custom[int(nf.get("numFmtId"))] = nf.get("formatCode") or ""
        except (TypeError, ValueError):
            continue
    xfs = root.find("s:cellXfs", NS)
    out = []
    for xf in (xfs.findall("s:xf", NS) if xfs is not None else []):
        try:
            fid = int(xf.get("numFmtId") or 0)
        except ValueError:
            fid = 0
        out.append(_format_category(fid, custom.get(fid)))
    return out


def _text_kind(text: str):
    """("textnum", decimal style or None) | ("datetext", form) | ("text", None)
    for a cell's text. Leading zeros ("007") are an identifier, not a number."""
    v = (text or "").strip()
    if not v or len(v) > _MAX_TYPED_TEXT:
        return ("text", None)
    if _NUM_COMMA.fullmatch(v):
        return ("textnum", "comma")
    if _NUM_POINT.fullmatch(v):
        return ("textnum", "point" if "." in v else None)
    if _NUM_INT.fullmatch(v):
        return ("textnum", None)
    for form, rx in _DATE_FORMS:
        if rx.fullmatch(v):
            return ("datetext", form)
    return ("text", None)


class _Column:
    __slots__ = ("num", "textnum", "text", "datetext", "first", "last", "first_textnum",
                 "first_datetext", "first_comma", "first_point", "cats", "date_forms")

    def __init__(self):
        self.num = self.textnum = self.text = self.datetext = 0
        self.first = self.last = None
        self.first_textnum = self.first_datetext = self.first_comma = self.first_point = None
        self.cats = {}
        self.date_forms = {}

    def add(self, ref: str, row: int, kind: str, detail) -> None:
        if self.first is None:
            self.first = (row, ref)
        self.last = (row, ref)
        if kind == "num":
            self.num += 1
            self.cats[detail] = self.cats.get(detail, 0) + 1
        elif kind == "textnum":
            self.textnum += 1
            self.first_textnum = self.first_textnum or ref
            if detail == "comma":
                self.first_comma = self.first_comma or ref
            elif detail == "point":
                self.first_point = self.first_point or ref
        elif kind == "datetext":
            self.datetext += 1
            self.first_datetext = self.first_datetext or ref
            self.date_forms[detail] = self.date_forms.get(detail, 0) + 1
        else:
            self.text += 1

    def findings(self, sheet: Optional[str], col: str, *, typed_cells: bool) -> List[dict]:
        """The column's type, locale and style findings. `typed_cells`: the
        file has real numbers (a workbook); in a CSV every value is text, so
        "stored as text" does not apply there."""
        out: List[dict] = []
        total = self.num + self.textnum + self.text + self.datetext
        if total < MIN_COLUMN_CELLS:
            return out
        span = "%s:%s" % (self.first[1], self.last[1]) if self.first and self.last else col
        where = {"sheet": sheet, "cell": span}
        label = ("%s column %s" % (sheet, col)) if sheet else ("column %s" % col)
        numeric = self.num + self.textnum
        if typed_cells and self.textnum and numeric * 2 >= total:
            out.append(_finding("types", "warn", "%s has %d number(s) stored as text (first at %s); they do "
                                "not add up or sort as numbers" % (label, self.textnum, self.first_textnum), **where))
        if total >= MIXED_TYPE_MIN_CELLS and numeric and self.text \
                and min(numeric, self.text) >= MIXED_TYPE_SHARE * total:
            out.append(_finding("types", "warn", "%s mixes numbers (%d) and text (%d)"
                                % (label, numeric, self.text), **where))
        if typed_cells and self.datetext:
            out.append(_finding("types", "warn", "%s has %d date(s) stored as text (first at %s); they do not "
                                "sort or calculate as dates" % (label, self.datetext, self.first_datetext), **where))
        if not typed_cells and len(self.date_forms) > 1:
            out.append(_finding("types", "warn", "%s writes dates in %d different ways (%s)"
                                % (label, len(self.date_forms), ", ".join(sorted(self.date_forms))), **where))
        if self.first_comma and self.first_point:
            out.append(_finding("locale", "warn", "%s writes decimals both with a point (%s) and with a comma "
                                "(%s); one of them is read wrong in any one locale"
                                % (label, self.first_point, self.first_comma), **where))
        cats = set(self.cats)
        if len(cats - {"general"}) >= 2 or ("general" in cats and cats & {"date", "percent", "currency"}):
            out.append(_finding("styles", "warn", "%s shows its numbers in different formats (%s)"
                                % (label, ", ".join(sorted(cats))), **where))
        return out


def _rels_targets(pkg: "_Pkg", part: str) -> List[dict]:
    """The relationships of one part: [{"type", "target", "external"}]."""
    folder, _, name = part.rpartition("/")
    rels = pkg.xml("%s/_rels/%s.rels" % (folder, name))
    out = []
    for rel in (list(rels) if rels is not None else []):
        out.append({"type": rel.get("Type") or "", "target": rel.get("Target") or "",
                    "external": (rel.get("TargetMode") or "") == "External"})
    return out


def _link_findings(target: str, sheet: str) -> List[dict]:
    import urllib.parse
    low = target.strip().lower()
    if low.startswith(("http://", "https://", "mailto:")):
        host = urllib.parse.urlsplit(target).hostname or ("an email address" if low.startswith("mailto:") else "?")
        return [_finding("links", "info", "%s links to %s" % (sheet, host[:80]), sheet=sheet, cell=None)]
    return [_finding("links", "warn", "%s links to another file (%s), which may not be there on the reader's "
                                      "machine" % (sheet, target.replace("\\", "/").rsplit("/", 1)[-1][:80]),
                     sheet=sheet, cell=None)]


def _check_xlsx(path: Path) -> List[dict]:
    pkg = _Pkg(path)
    wb = pkg.xml("xl/workbook.xml")
    if wb is None:
        return [_finding("validity", "error", "not an Excel workbook: xl/workbook.xml is missing")]
    out: List[dict] = []
    sheet_els = wb.findall(".//s:sheets/s:sheet", NS)
    sheets = [s.get("name") or "" for s in sheet_els]
    names_lower = {n.lower() for n in sheets}
    if not sheets:
        out.append(_finding("validity", "error", "the workbook has no sheets"))
    # Sheet parts in workbook order through the relationships.
    rels = pkg.xml("xl/_rels/workbook.xml.rels")
    targets = {}
    if rels is not None:
        for rel in rels:
            targets[rel.get("Id")] = (rel.get("Target") or "").lstrip("/").replace("xl/", "", 1)
    shared_root = pkg.xml("xl/sharedStrings.xml")
    shared: List[str] = []
    if shared_root is not None:
        for si in shared_root.findall("s:si", NS):
            shared.append("".join(t.text or "" for t in si.iter("{%s}t" % NS["s"])))
    styles = _style_categories(pkg)
    # Defined names: the workbook's own, and each one checked (ranges).
    global_names, local_names = set(), {}
    for dn in wb.findall(".//s:definedNames/s:definedName", NS):
        dname = (dn.get("name") or "").strip()
        text = (dn.text or "").strip()[:MAX_FORMULA]
        local = dn.get("localSheetId")
        scope = None
        if local is not None:
            try:
                idx = int(local)
            except ValueError:
                idx = -1
            local_names.setdefault(idx, set()).add(dname.upper())
            if 0 <= idx < len(sheets):
                scope = sheets[idx]
            else:
                out.append(_finding("ranges", "error", "the named range '%s' belongs to a sheet that does not exist"
                                    % dname[:60], sheet=None, cell=dname))
        else:
            global_names.add(dname.upper())
        builtin = dname.startswith("_xlnm.")
        where = {"sheet": scope, "cell": dname}
        if not text:
            out.append(_finding("ranges", "error", "the named range '%s' is empty" % dname[:60], **where))
            continue
        if "#REF!" in text:
            out.append(_finding("ranges", "warn" if builtin else "error",
                                "the named range '%s' points at a deleted range" % dname[:60], **where))
        external = bool(_EXTERNAL_REF.search(text))
        if external:
            out.append(_finding("links", "warn", "the named range '%s' takes its values from another workbook"
                                % dname[:60], **where))
        for other, col, row in _formula_refs(text):
            if other and other != "REF" and "[" not in other and not external and other.lower() not in names_lower:
                out.append(_finding("ranges", "error", "the named range '%s' points at sheet '%s', which does not "
                                    "exist" % (dname[:60], other[:60]), **where))
            if col and (_col_number(col) > _MAX_COL or int(row) > _MAX_ROW or int(row) < 1):
                out.append(_finding("ranges", "error", "the named range '%s' points at %s%s, outside any sheet"
                                    % (dname[:60], col, row), **where))
    tables = set()
    for tpart in pkg.matching(r"xl/tables/table\d+\.xml"):
        troot = pkg.xml(tpart)
        if troot is not None:
            tables.update(v.upper() for v in (troot.get("name"), troot.get("displayName")) if v)
    # Links to other workbooks and outside data.
    for xpart in pkg.matching(r"xl/externalLinks/externalLink\d+\.xml"):
        linked = [r["target"] for r in _rels_targets(pkg, xpart) if r["target"]]
        what = linked[0].replace("\\", "/").rsplit("/", 1)[-1][:80] if linked else "a file"
        out.append(_finding("links", "warn", "the workbook takes values from another workbook (%s); they go "
                            "stale, or break when that file moves" % what, sheet=None, cell=None))
    if "xl/connections.xml" in pkg.names:
        out.append(_finding("links", "warn", "the workbook has data connections that refresh from outside "
                                             "sources", sheet=None, cell=None))
    cells = 0
    filled: Dict[str, Optional[set]] = {}      # sheet -> {(col, row)} with data; None when not fully read
    s_ns = "{%s}" % NS["s"]
    for index, s in enumerate(sheet_els):
        name = s.get("name") or "?"
        rid = s.get("{%s}id" % NS["r"])
        part = "xl/" + targets.get(rid, "") if rid in targets else None
        root = pkg.xml(part) if part else None
        if root is None:
            out.append(_finding("validity", "error", "sheet '%s' has no data part" % name, sheet=name, cell=None))
            continue
        known = global_names | local_names.get(index, set()) | tables
        hidden_cols = set()
        for col_el in root.iter(s_ns + "col"):
            if col_el.get("hidden") in ("1", "true"):
                try:
                    lo, hi = int(col_el.get("min")), int(col_el.get("max"))
                except (TypeError, ValueError):
                    continue
                hidden_cols.update(range(lo, min(hi, lo + _MAX_COL) + 1))
        here: Optional[set] = set()
        typed = []                              # (col, row, ref, kind, detail) of value cells
        hidden_rows_with_data, hidden_cols_with_data = [], set()
        for row_el in root.iter(s_ns + "row"):
            row_hidden = row_el.get("hidden") in ("1", "true")
            row_has_data = False
            for c in row_el.findall("s:c", NS):
                cells += 1
                ref = c.get("r") or "?"
                where = {"sheet": name, "cell": ref}
                v = c.find("s:v", NS)
                f = c.find("s:f", NS)
                t = c.get("t") or "n"
                vtext = (v.text or "") if v is not None else ""
                if t == "inlineStr":
                    vtext = "".join(x.text or "" for x in c.iter(s_ns + "t"))
                has_data = bool(vtext.strip()) or (f is not None and bool(f.text))
                pos = _split_ref(ref)
                if has_data:
                    row_has_data = True
                    if pos and here is not None:
                        if len(here) < MAX_TYPE_CELLS:
                            here.add(pos)
                        else:
                            here = None
                    if pos and pos[0] in hidden_cols:
                        hidden_cols_with_data.add(pos[0])
                if t == "e" or (v is not None and vtext.strip() in _CELL_ERROR):
                    out.append(_finding("formulas", "error", "%s!%s shows %s"
                                        % (name, ref, (v.text if v is not None else "an error")), **where))
                if f is not None and f.text:
                    out += _formula_findings(f.text, name, ref, names_lower, known)
                elif pos and has_data and t not in ("b", "e") and len(typed) < MAX_TYPE_CELLS:
                    if t == "s":
                        try:
                            kind, detail = _text_kind(shared[int(vtext)])
                        except (ValueError, IndexError):
                            kind, detail = "text", None
                    elif t in ("inlineStr", "str"):
                        kind, detail = _text_kind(vtext)
                    elif t == "d":
                        kind, detail = "num", "date"
                    elif t == "n":
                        try:
                            style = styles[int(c.get("s") or 0)] if styles else "general"
                        except (ValueError, IndexError):
                            style = "general"
                        kind, detail = "num", style
                    else:
                        kind, detail = "text", None
                    typed.append((pos[0], pos[1], ref, kind, detail))
                if len(out) >= MAX_FINDINGS:
                    break
            if row_hidden and row_has_data:
                try:
                    hidden_rows_with_data.append(int(row_el.get("r")))
                except (TypeError, ValueError):
                    pass
            if len(out) >= MAX_FINDINGS:
                break
        filled[name.lower()] = here
        if len(out) >= MAX_FINDINGS:
            break
        if (here is None or here) and (s.get("state") or "visible") in ("hidden", "veryHidden"):
            out.append(_finding("styles", "info", "sheet '%s' is hidden and holds data" % name, sheet=name, cell=None))
        if hidden_rows_with_data:
            lo, hi = min(hidden_rows_with_data), max(hidden_rows_with_data)
            out.append(_finding("styles", "info", "%s has %d hidden row(s) that hold data (%s)"
                                % (name, len(hidden_rows_with_data), "%d:%d" % (lo, hi)),
                                sheet=name, cell="%d:%d" % (lo, hi)))
        if hidden_cols_with_data:
            cols = sorted(hidden_cols_with_data)
            span = "%s:%s" % (_col_letters(cols[0]), _col_letters(cols[-1]))
            out.append(_finding("styles", "info", "%s has %d hidden column(s) that hold data (%s)"
                                % (name, len(cols), span), sheet=name, cell=span))
        out += _column_findings(typed, name)
        # Links in the sheet: hyperlinks to the internet (info) or to other files (warn).
        seen_links = set()
        for r in _rels_targets(pkg, part):
            if r["external"] and r["type"].endswith("/hyperlink") and r["target"] not in seen_links \
                    and len(seen_links) < 5:
                seen_links.add(r["target"])
                out += _link_findings(r["target"], name)
    if sheets and not cells:
        out.append(_finding("validity", "error", "the workbook has no cells"))
    if shared:
        out += _placeholder_findings("".join(shared), "the workbook")
    out += _chart_findings(pkg, "xl/")
    out += _chart_range_findings(pkg, filled, names_lower)
    return out


def _formula_findings(ftext: str, name: str, ref: str, names_lower: set, known: set) -> List[dict]:
    """Everything wrong with one stored formula."""
    where = {"sheet": name, "cell": ref}
    if len(ftext) > MAX_FORMULA:
        return [_finding("formulas", "error", "%s!%s has a formula longer than Excel allows" % (name, ref), **where)]
    out = []
    if "#REF!" in ftext:
        out.append(_finding("formulas", "error", "%s!%s refers to a deleted range" % (name, ref), **where))
    # A sheet in another workbook ([1]Prices!A1) is that workbook's: not judged here.
    external = bool(_EXTERNAL_REF.search(ftext))
    for other, col, row in _formula_refs(ftext):
        # 'First:Last'!A1 is a 3D reference over a range of sheets: each end must exist.
        ends = other.split(":") if other and ":" in other else [other]
        missing = [s for s in ends if s and s.lower() not in names_lower]
        if other and other != "REF" and missing and "[" not in other and not external:
            out.append(_finding("formulas", "error", "%s!%s refers to a sheet '%s' that does not exist"
                                % (name, ref, missing[0][:60]), **where))
        if col and (_col_number(col) > _MAX_COL or int(row) > _MAX_ROW or int(row) < 1):
            out.append(_finding("bounds", "error", "%s!%s refers to %s%s, outside any sheet" % (name, ref, col, row),
                                **where))
    scan = _formula_scan(ftext)
    if not scan["parses"]:
        out.append(_finding("formulas", "error", "%s!%s has a formula that does not parse (its brackets or "
                                                 "quotes do not balance)" % (name, ref), **where))
        return out
    if scan["semicolon"]:
        out.append(_finding("locale", "error", "%s!%s separates arguments with ';', a regional setting; a file "
                                               "stores ',' so the formula breaks" % (name, ref), **where))
    bases = [_function_base(fn) for fn in scan["funcs"]]
    for fn in dict.fromkeys(bases):
        if fn in _LOCALIZED_FUNCTIONS or not fn.isascii():
            out.append(_finding("locale", "error", "%s!%s calls %s, a function name from a non-English Excel; a "
                                                   "file stores English names, so it shows #NAME?" % (name, ref, fn[:40]),
                                **where))
        elif fn == "WEBSERVICE":
            out.append(_finding("links", "warn", "%s!%s fetches data from the internet (WEBSERVICE)" % (name, ref),
                                **where))
    if external:
        out.append(_finding("links", "warn", "%s!%s takes values from another workbook" % (name, ref), **where))
    # A formula that reads another workbook may use that workbook's names
    # ([1]!MyName): they are not this workbook's to define, so not judged.
    if not external and not {"LET", "LAMBDA"} & set(bases):
        for token in dict.fromkeys(scan["names"]):
            up = token.upper()
            if up in ("TRUE", "FALSE") or up.startswith("_XLPM.") or _CELL.fullmatch(up.replace("$", "")) \
                    or _COL_ONLY.fullmatch(token) or up in known:
                continue
            out.append(_finding("names", "error", "%s!%s uses the name '%s', which the workbook does not define"
                                % (name, ref, token[:60]), **where))
    return out


def _column_findings(typed, sheet: Optional[str], *, typed_cells: bool = True,
                     header: bool = True) -> List[dict]:
    """Type, locale and style findings per column. With `header`, a first
    row that is all text is a header and is left out."""
    if not typed:
        return []
    first_row = min(r for _c, r, _ref, _k, _d in typed)
    kinds = [k for _c, r, _ref, k, _d in typed if r == first_row]
    skip_header = header and all(k == "text" for k in kinds) and any(r != first_row for _c, r, _ref, _k, _d in typed)
    columns: Dict[int, _Column] = {}
    for col, row, ref, kind, detail in sorted(typed, key=lambda x: (x[0], x[1])):
        if skip_header and row == first_row:
            continue
        columns.setdefault(col, _Column()).add(ref, row, kind, detail)
    out = []
    for col in sorted(columns):
        out += columns[col].findings(sheet, _col_letters(col), typed_cells=typed_cells)
        if len(out) >= MAX_FINDINGS:
            break
    return out


def _chart_range_findings(pkg: "_Pkg", filled: Dict[str, Optional[set]], names_lower: set) -> List[dict]:
    """Each chart series' ranges: on a sheet that exists, inside the grid,
    and not empty (judged only for a sheet that was read in full)."""
    out = []
    parts = {"c:val": "values", "c:yVal": "values", "c:xVal": "x values", "c:cat": "categories"}
    for name in pkg.matching(r"xl/charts/chart\d+\.xml"):
        root = pkg.xml(name)
        if root is None:
            continue
        label = name.rsplit("/", 1)[-1]
        for i, ser in enumerate(root.findall(".//c:ser", NS), 1):
            for tag, what in parts.items():
                el = ser.find(tag, NS)
                ref_el = el.find(".//c:f", NS) if el is not None else None
                text = (ref_el.text or "").strip()[:2048] if ref_el is not None else ""
                if not text or "#REF!" in text:
                    continue
                where = {"sheet": None, "cell": text[:120]}
                if _EXTERNAL_REF.search(text):
                    out.append(_finding("links", "warn", "%s series %d takes its %s from another workbook"
                                        % (label, i, what), **where))
                    continue
                for m in _RANGE_REF.finditer(text):
                    sheet = (m.group(1) or "").replace("''", "'") or m.group(2)
                    where = {"sheet": sheet, "cell": text[:120]}
                    if sheet.lower() not in names_lower:
                        out.append(_finding("charts", "error", "%s series %d takes its %s from sheet '%s', which does "
                                            "not exist" % (label, i, what, sheet[:60]), **where))
                        continue
                    c1, r1 = _col_number(m.group(3)), int(m.group(4))
                    c2 = _col_number(m.group(5)) if m.group(5) else c1
                    r2 = int(m.group(6)) if m.group(6) else r1
                    if max(c1, c2) > _MAX_COL or max(r1, r2) > _MAX_ROW or min(r1, r2) < 1:
                        out.append(_finding("charts", "error", "%s series %d takes its %s from outside any sheet"
                                            % (label, i, what), **where))
                        continue
                    data = filled.get(sheet.lower())
                    if data is None:
                        continue                # too big to index, or not read: not judged
                    lo_c, hi_c, lo_r, hi_r = min(c1, c2), max(c1, c2), min(r1, r2), max(r1, r2)
                    if not any(lo_c <= c <= hi_c and lo_r <= r <= hi_r for c, r in data):
                        out.append(_finding("charts", "error", "%s series %d takes its %s from %s, which is empty"
                                            % (label, i, what, text[:80]), **where))
            if len(out) >= MAX_FINDINGS:
                return out
    return out


# ── PowerPoint ─────────────────────────────────────────────────────────────

def _check_pptx(path: Path) -> List[dict]:
    pkg = _Pkg(path)
    pres = pkg.xml("ppt/presentation.xml")
    if pres is None:
        return [_finding("validity", "error", "not a PowerPoint deck: ppt/presentation.xml is missing")]
    out: List[dict] = []
    size = pres.find("p:sldSz", NS)
    sw = int(size.get("cx")) if size is not None else 12192000
    sh = int(size.get("cy")) if size is not None else 6858000
    slides = sorted(pkg.matching(r"ppt/slides/slide\d+\.xml"), key=lambda n: int(re.findall(r"\d+", n)[-1]))
    if not slides:
        out.append(_finding("validity", "error", "the deck has no slides"))
    used = set()
    for name in slides:
        root = pkg.xml(name)
        n = re.findall(r"\d+", name)[-1]
        label = "slide %s" % n
        if root is None:
            continue
        text = "".join(t.text or "" for t in root.iter("{%s}t" % NS["a"]))
        out += _placeholder_findings(text, label)
        types = {ph.get("type") for ph in root.iter("{%s}ph" % NS["p"])}
        if not types & {"title", "ctrTitle"}:
            out.append(_finding("accessibility", "warn", "%s has no title" % label))
        # Top-level shapes only: a group's children are placed in the group's
        # own coordinate space, and the group's frame is what lands on the slide.
        tree = root.find("p:cSld/p:spTree", NS)
        frames = []
        for shape in (list(tree) if tree is not None else []):
            for sub in ("p:spPr/a:xfrm", "p:grpSpPr/a:xfrm", "p:xfrm"):
                x = shape.find(sub, NS)
                if x is not None:
                    frames.append(x)
        for xfrm in frames:
            off, ext = xfrm.find("a:off", NS), xfrm.find("a:ext", NS)
            if off is None or ext is None:
                continue
            try:
                x, y = int(off.get("x")), int(off.get("y"))
                cx, cy = int(ext.get("cx")), int(ext.get("cy"))
            except (TypeError, ValueError):
                continue
            if x < 0 or y < 0 or x + cx > sw or y + cy > sh:
                out.append(_finding("bounds", "error", "%s has a shape that runs off the slide" % label))
                break
        for pic in root.iter("{%s}pic" % NS["p"]):
            pr = pic.find("p:nvPicPr/p:cNvPr", NS)
            if pr is not None and not (pr.get("descr") or "").strip():
                out.append(_finding("accessibility", "error", "%s has a picture with no alt text" % label))
        for latin in root.iter("{%s}latin" % NS["a"]):
            if latin.get("typeface"):
                used.add(latin.get("typeface"))
    embedded = set()
    for font in pres.iter("{%s}embeddedFont" % NS["p"]):
        f = font.find("p:font", NS)
        if f is not None and f.get("typeface"):
            embedded.add(f.get("typeface").lower())
    out += _fonts_findings(used, embedded, "the deck")
    out += _chart_findings(pkg, "ppt/")
    return out


# ── PDF ────────────────────────────────────────────────────────────────────

_STANDARD_14 = ("courier", "helvetica", "times", "symbol", "zapfdingbats")


def _check_pdf(path: Path) -> List[dict]:
    with open(path, "rb") as fh:
        if not fh.read(1024).lstrip().startswith(b"%PDF-"):
            return [_finding("validity", "error", "not a PDF: the file does not start with %PDF-")]
    try:
        import pdfplumber
    except Exception:
        return [_finding("validity", "warn", "the PDF could not be checked here (pdfplumber is missing)")]
    out: List[dict] = []
    with pdfplumber.open(str(path)) as pdf:
        pages = pdf.pages
        if not pages:
            return [_finding("validity", "error", "the PDF has no pages")]
        text = ""
        # The first pages and a bounded amount of text are enough to judge;
        # a pathological document cannot keep the check busy for long.
        for i, page in enumerate(pages[:MAX_PDF_PAGES], 1):
            w, h = float(page.width), float(page.height)
            if not (72 <= w <= 14400 and 72 <= h <= 14400):
                out.append(_finding("bounds", "error", "page %d is %.0f x %.0f points, not a printable size" % (i, w, h)))
            if len(text) < MAX_PDF_TEXT:
                text += (page.extract_text() or "")[:MAX_PDF_TEXT]
            try:
                fonts = (page.page_obj.resources or {}).get("Font") or {}
                from pdfminer.pdftypes import resolve1
                for font in resolve1(fonts).values():
                    fd = resolve1(font)
                    base = str(resolve1(fd.get("BaseFont")) or "").strip("/'")
                    desc = resolve1(fd.get("FontDescriptor")) or {}
                    plain = base.split("+")[-1].lower()
                    embedded = any(k in desc for k in ("FontFile", "FontFile2", "FontFile3"))
                    if not embedded and not plain.startswith(_STANDARD_14) and fd.get("Subtype") != "/Type3" \
                            and str(resolve1(fd.get("Subtype")) or "").strip("/'") != "Type0":
                        out.append(_finding("fonts", "warn", "page %d uses the font '%s', not embedded" % (i, base.split("+")[-1])))
            except Exception:
                pass
        if not text.strip():
            out.append(_finding("accessibility", "error", "the PDF has no text layer, so a screen reader "
                                                         "or a search cannot read it"))
        out += _placeholder_findings(text, "the PDF")
        try:
            from pdfminer.pdftypes import resolve1
            catalog = pdf.doc.catalog
            if not resolve1(catalog.get("Lang")):
                out.append(_finding("accessibility", "warn", "the PDF's language is not set"))
        except Exception:
            pass
    # One finding per font is enough.
    seen, unique = set(), []
    for f in out:
        if f["message"] not in seen:
            seen.add(f["message"])
            unique.append(f)
    return unique


# ── web pages ──────────────────────────────────────────────────────────────

_REMOTE = re.compile(r"^\s*(https?:)?//", re.I)


class _Html(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lang = None
        self._title: List[str] = []
        self._title_len = 0
        self._in_title = False
        self._in_style = False
        self.headings: List[int] = []
        self.imgs_without_alt = 0
        self.remote: List[str] = []
        self.text: List[str] = []
        self.styles: List[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang", "").strip() or None
        elif tag == "title":
            self._in_title = True
        elif tag == "style":
            self._in_style = True
        elif re.fullmatch(r"h[1-6]", tag):
            self.headings.append(int(tag[1]))
        elif tag == "img" and "alt" not in a:
            self.imgs_without_alt += 1
        for attr in ("src", "href", "data", "poster"):
            v = a.get(attr, "")
            if v and _REMOTE.match(v) and not (tag == "a" and attr == "href"):
                self.remote.append(v[:120])
        if tag == "link" and "stylesheet" in a.get("rel", "").lower() and _REMOTE.match(a.get("href", "")):
            pass  # counted above

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "style":
            self._in_style = False

    def handle_data(self, data):
        if self._in_title:
            if self._title_len < 4096:
                self._title.append(data)
                self._title_len += len(data)
        elif self._in_style:
            self.styles.append(data)
        else:
            self.text.append(data)


def _check_html(path: Path) -> List[dict]:
    with open(path, "rb") as fh:
        raw = fh.read(MAX_TEXT).decode("utf-8", errors="replace")
    p = _Html()
    try:
        p.feed(raw)
        p.close()
    except Exception as e:
        return [_finding("validity", "error", "the page could not be read (%s)" % e)]
    out: List[dict] = []
    if not p.lang:
        out.append(_finding("accessibility", "warn", "the page does not say its language (<html lang>)"))
    if not "".join(p._title).strip():
        out.append(_finding("accessibility", "warn", "the page has no title"))
    if p.imgs_without_alt:
        out.append(_finding("accessibility", "error", "%d picture(s) have no alt text" % p.imgs_without_alt))
    last = 0
    for level in p.headings:
        if last and level > last + 1:
            out.append(_finding("accessibility", "warn", "a heading jumps from h%d to h%d" % (last, level)))
            break
        last = level
    css = "\n".join(p.styles)[:MAX_TEXT]
    low = css.lower()
    # Each url( and @import is examined in a short window after it: linear in
    # the stylesheet, no backtracking over attacker text.
    for key in ("url(", "@import"):
        start = 0
        while len(p.remote) < 50:
            k = low.find(key, start)
            if k < 0:
                break
            start = k + len(key)
            window = css[start:start + 300].lstrip(" \t\r\n(\"'")
            if _REMOTE.match(window):
                p.remote.append(re.split(r"[)\"'\s;]", window, maxsplit=1)[0][:120])
    for url in sorted(set(p.remote))[:5]:
        out.append(_finding("validity", "warn", "the page loads %s from the internet, so it changes or "
                                                "breaks offline and tells that site it was opened" % url))
    fams = set()
    for m in re.finditer(r"font-family\s*:\s*([^;}{]+)", css, re.I):
        for fam in m.group(1).split(","):
            fams.add(fam.strip().strip("'\""))
    faces = set()
    start = 0
    while True:
        k = low.find("@font-face", start)
        if k < 0:
            break
        end = low.find("}", k)
        block = css[k:end if end >= 0 else len(css)]
        start = (end if end >= 0 else len(css)) + 1
        m = re.search(r"font-family\s*:\s*([^;}]+)", block, re.I)
        if m:
            faces.add(m.group(1).strip().strip("'\"").lower())
    out += _fonts_findings(fams, faces, "the page")
    out += _placeholder_findings(" ".join(p.text), "the page")
    return out


# ── CSV and images ─────────────────────────────────────────────────────────

def _check_csv(path: Path) -> List[dict]:
    with open(path, "rb") as fh:
        raw = fh.read(MAX_PART_BYTES).decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(raw)))
    if not rows:
        return [_finding("validity", "error", "the file is empty")]
    width = len(rows[0])
    bad = [i for i, r in enumerate(rows, 1) if r and len(r) != width]
    if bad:
        return [_finding("bounds", "error", "row %d has %d columns; the header has %d"
                         % (bad[0], len(rows[bad[0] - 1]), width))]
    return _csv_integrity(rows)


#: CSV rows read for the column checks.
MAX_CSV_ROWS = 50_000


def _csv_integrity(rows) -> List[dict]:
    """The spreadsheet checks that apply to a CSV (NS-24.5-1): every value is
    text there, so the questions are whether a column mixes numbers and text,
    writes decimals both ways or dates several ways, and whether a cell will
    run as a formula when a spreadsheet opens the file."""
    out: List[dict] = []
    typed = []
    injected: Dict[int, List[str]] = {}
    for r_index, row in enumerate(rows[1:MAX_CSV_ROWS], 2):
        for c_index, raw in enumerate(row, 1):
            v = (raw or "").strip()
            if not v:
                continue
            ref = "%s%d" % (_col_letters(c_index), r_index)
            if v[0] in "=@" or (v[0] in "+-" and len(v) > 1 and not (v[1].isdigit() or v[1] == ".")):
                injected.setdefault(c_index, []).append(ref)
            kind, detail = _text_kind(v)
            typed.append((c_index, r_index, ref, kind, detail))
    for col, refs in sorted(injected.items()):
        out.append(_finding("formulas", "warn", "column %s has %d cell(s) that a spreadsheet will run as a formula "
                            "(first at %s)" % (_col_letters(col), len(refs), refs[0]),
                            sheet=None, cell=refs[0]))
    # The header row was left out above; every value is text, so nothing is
    # "a number stored as text" here.
    out += _column_findings(typed, None, typed_cells=False, header=False)
    return out[:MAX_FINDINGS]


def _check_image(path: Path) -> List[dict]:
    head = path.read_bytes()[:64]
    suffix = path.suffix.lower()
    if suffix == ".png":
        if not head.startswith(b"\x89PNG\r\n\x1a\n") or head[12:16] != b"IHDR":
            return [_finding("validity", "error", "not a PNG image")]
        w, h = struct.unpack(">II", head[16:24])
        if not (0 < w <= 30000 and 0 < h <= 30000):
            return [_finding("bounds", "error", "the image is %d x %d pixels" % (w, h))]
    elif suffix in (".jpg", ".jpeg") and not head.startswith(b"\xff\xd8\xff"):
        return [_finding("validity", "error", "not a JPEG image")]
    elif suffix == ".gif" and not head.startswith((b"GIF87a", b"GIF89a")):
        return [_finding("validity", "error", "not a GIF image")]
    elif suffix == ".webp" and not (head.startswith(b"RIFF") and head[8:12] == b"WEBP"):
        return [_finding("validity", "error", "not a WebP image")]
    return []


_CHECKERS = {".docx": _check_docx, ".xlsx": _check_xlsx, ".xlsm": _check_xlsx, ".pptx": _check_pptx,
             ".pdf": _check_pdf, ".html": _check_html, ".htm": _check_html, ".csv": _check_csv,
             ".png": _check_image, ".jpg": _check_image, ".jpeg": _check_image, ".gif": _check_image,
             ".webp": _check_image}
SUPPORTED = frozenset(_CHECKERS)


def check(path) -> Dict:
    """{"ok", "kind", "checked", "findings"} for one file. Never raises.
    `checked` is False for a kind this module does not know (nothing claimed)."""
    p = Path(path)
    kind = p.suffix.lower().lstrip(".")
    fn = _CHECKERS.get(p.suffix.lower())
    if fn is None:
        return {"ok": True, "kind": kind, "checked": False, "findings": []}
    try:
        if not p.is_file():
            findings = [_finding("validity", "error", "the file does not exist")]
        elif p.stat().st_size == 0:
            findings = [_finding("validity", "error", "the file is empty")]
        elif p.stat().st_size > MAX_BYTES:
            findings = [_finding("validity", "warn", "the file is too large to check")]
        else:
            findings = fn(p)
    except zipfile.BadZipFile:
        findings = [_finding("validity", "error", "the file is not a valid %s package" % kind)]
    except _Refused as e:
        findings = [_finding("validity", "error", str(e))]
    except ET.ParseError as e:
        findings = [_finding("validity", "error", "a part of the file is not well-formed XML (%s)" % e)]
    except Exception as e:
        findings = [_finding("validity", "error", "the file could not be read (%s)" % type(e).__name__)]
    # Errors first (in the order found), so a cap never drops one for a note.
    findings = sorted(findings, key=lambda f: _SEVERITY_RANK.get(f["severity"], 3))[:MAX_FINDINGS]
    return {"ok": not any(f["severity"] == "error" for f in findings), "kind": kind,
            "checked": True, "findings": findings}


_SEVERITY_RANK = {"error": 0, "warn": 1, "info": 2}


#: How long one check may take in its own process before it is abandoned.
CHECK_TIMEOUT_S = 20


def check_isolated(path, timeout: float = CHECK_TIMEOUT_S) -> Dict:
    """`check`, run in a child Python with a hard time limit: the parsers it
    uses (pdfminer, html.parser, expat) are fed untrusted files and some of
    them slow down badly on hostile input, so the caller (the agent's
    manifest worker, the office delivery gate) can never be held by one.
    A check that does not finish is a warning, never a pass of the file's
    content. Never raises.

    In a frozen (packaged) build `sys.executable` is the app itself, not a
    Python, so no child is started and the result is `checked: False` with
    the warning "the check cannot run in this build". Callers must not read
    that as a pass: the manifest records it as verification_state
    "unverified" with the reason "checks unavailable in this build"
    (artifact_manifest.UNAVAILABLE_REASON)."""
    import json
    import subprocess
    p = Path(path)
    kind = p.suffix.lower().lstrip(".")
    if p.suffix.lower() not in _CHECKERS:
        return {"ok": True, "kind": kind, "checked": False, "findings": []}
    if getattr(sys, "frozen", False):
        # A frozen build's executable is the app, not Python: never start it.
        return {"ok": True, "kind": kind, "checked": False, "unavailable": True,
                "findings": [_finding("validity", "warn", "the check cannot run in this build")]}
    code = ("import importlib.util,json,sys\n"
            "s=importlib.util.spec_from_file_location('artifact_qa',sys.argv[1])\n"
            "m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\n"
            "sys.stdout.write(json.dumps(m.check(sys.argv[2])))\n")
    try:
        out = subprocess.run([sys.executable, "-I", "-c", code, str(Path(__file__).resolve()), str(p)],
                             capture_output=True, timeout=timeout, text=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        res = json.loads(out.stdout or "{}")
        # The child's answer is data, accepted only in exactly the expected shape.
        fs = res.get("findings") if isinstance(res, dict) else None
        if (isinstance(res, dict) and isinstance(res.get("ok"), bool) and isinstance(res.get("checked"), bool)
                and isinstance(fs, list) and len(fs) <= MAX_FINDINGS
                and all(isinstance(f, dict) and all(isinstance(f.get(k), str) for k in ("check", "severity", "message"))
                        and f["severity"] in _SEVERITY_RANK
                        and all(f.get(k) is None or isinstance(f.get(k), str) for k in ("sheet", "cell"))
                        for f in fs)):
            return {"ok": res["ok"], "kind": kind, "checked": res["checked"],
                    "findings": [_finding(f["check"], f["severity"], f["message"],
                                          **({k: f.get(k) for k in ("sheet", "cell")}
                                             if ("sheet" in f or "cell" in f) else {}))
                                 for f in fs]}
        raise ValueError("no result")
    except subprocess.TimeoutExpired:
        why = "the check did not finish within %d seconds, so the file was not checked" % timeout
    except Exception as e:
        why = "the check could not run (%s)" % type(e).__name__
    return {"ok": True, "kind": p.suffix.lower().lstrip("."), "checked": False,
            "findings": [_finding("validity", "warn", why)]}


def summary(result: Dict) -> str:
    """One plain line for a tool result or a card."""
    if not result.get("checked"):
        return ""
    errors = [f["message"] for f in result["findings"] if f["severity"] == "error"]
    warns = [f["message"] for f in result["findings"] if f["severity"] == "warn"]
    if not errors and not warns:
        return "Checked: no problems found."
    parts = []
    if errors:
        parts.append("Problems: " + "; ".join(errors[:5]))
    if warns:
        parts.append("Worth fixing: " + "; ".join(warns[:5]))
    return " ".join(parts)
