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

Each finding is {"check", "severity", "message"}; "error" means the file is
broken for its reader, "warn" means it works but falls short. `ok` is False
when there is any error. Office files are zip packages of XML, so they are
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


def _finding(check: str, severity: str, message: str) -> dict:
    return {"check": check, "severity": severity, "message": message[:300]}


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

def _check_xlsx(path: Path) -> List[dict]:
    pkg = _Pkg(path)
    wb = pkg.xml("xl/workbook.xml")
    if wb is None:
        return [_finding("validity", "error", "not an Excel workbook: xl/workbook.xml is missing")]
    out: List[dict] = []
    sheets = [s.get("name") or "" for s in wb.findall(".//s:sheets/s:sheet", NS)]
    names_lower = {n.lower() for n in sheets}
    if not sheets:
        out.append(_finding("validity", "error", "the workbook has no sheets"))
    # Sheet parts in workbook order through the relationships.
    rels = pkg.xml("xl/_rels/workbook.xml.rels")
    targets = {}
    if rels is not None:
        for rel in rels:
            targets[rel.get("Id")] = (rel.get("Target") or "").lstrip("/").replace("xl/", "", 1)
    cells = 0
    for s in wb.findall(".//s:sheets/s:sheet", NS):
        name = s.get("name") or "?"
        rid = s.get("{%s}id" % NS["r"])
        part = "xl/" + targets.get(rid, "") if rid in targets else None
        root = pkg.xml(part) if part else None
        if root is None:
            out.append(_finding("validity", "error", "sheet '%s' has no data part" % name))
            continue
        for c in root.iter("{%s}c" % NS["s"]):
            cells += 1
            ref = c.get("r") or "?"
            v = c.find("s:v", NS)
            f = c.find("s:f", NS)
            if c.get("t") == "e" or (v is not None and (v.text or "").strip() in _CELL_ERROR):
                out.append(_finding("formulas", "error", "%s!%s shows %s" % (name, ref, (v.text if v is not None else "an error"))))
            if f is not None and f.text:
                ftext = f.text
                if len(ftext) > MAX_FORMULA:
                    out.append(_finding("formulas", "error", "%s!%s has a formula longer than Excel allows" % (name, ref)))
                    continue
                if "#REF!" in ftext:
                    out.append(_finding("formulas", "error", "%s!%s refers to a deleted range" % (name, ref)))
                for other, col, row in _formula_refs(ftext):
                    if other and other != "REF" and other.lower() not in names_lower and "[" not in other:
                        out.append(_finding("formulas", "error", "%s!%s refers to a sheet '%s' that does not exist"
                                            % (name, ref, other[:60])))
                    if col and (_col_number(col) > _MAX_COL or int(row) > _MAX_ROW or int(row) < 1):
                        out.append(_finding("bounds", "error", "%s!%s refers to %s%s, outside any sheet" % (name, ref, col, row)))
            if len(out) >= MAX_FINDINGS:
                break
        if len(out) >= MAX_FINDINGS:
            break
    if sheets and not cells:
        out.append(_finding("validity", "error", "the workbook has no cells"))
    shared = pkg.xml("xl/sharedStrings.xml")
    if shared is not None:
        out += _placeholder_findings("".join(t.text or "" for t in shared.iter("{%s}t" % NS["s"])), "the workbook")
    out += _chart_findings(pkg, "xl/")
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
    return []


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
    findings = findings[:MAX_FINDINGS]
    return {"ok": not any(f["severity"] == "error" for f in findings), "kind": kind,
            "checked": True, "findings": findings}


#: How long one check may take in its own process before it is abandoned.
CHECK_TIMEOUT_S = 20


def check_isolated(path, timeout: float = CHECK_TIMEOUT_S) -> Dict:
    """`check`, run in a child Python with a hard time limit: the parsers it
    uses (pdfminer, html.parser, expat) are fed untrusted files and some of
    them slow down badly on hostile input, so the caller (the agent's
    manifest worker, the office delivery gate) can never be held by one.
    A check that does not finish is a warning, never a pass of the file's
    content. Never raises."""
    import json
    import subprocess
    p = Path(path)
    kind = p.suffix.lower().lstrip(".")
    if p.suffix.lower() not in _CHECKERS:
        return {"ok": True, "kind": kind, "checked": False, "findings": []}
    if getattr(sys, "frozen", False):
        # A frozen build's executable is the app, not Python: never start it.
        return {"ok": True, "kind": kind, "checked": False,
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
                        and f["severity"] in ("error", "warn") for f in fs)):
            return {"ok": res["ok"], "kind": kind, "checked": res["checked"],
                    "findings": [_finding(f["check"], f["severity"], f["message"]) for f in fs]}
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
