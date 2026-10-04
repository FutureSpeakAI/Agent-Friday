"""Structure-preserving text extraction for the Library.

Runs inside the limited child process (worker task "extract"). Every format
produces the same thing: an ordered list of blocks, each with its text, kind,
page (or media time) and, for PDFs, its bounding box in PDF points. Headings
are blocks with a `level`. Nothing here executes, renders or fetches anything
from a document: links stay text, scripts are dropped, XML is parsed with
entities refused, archives are measured before they are opened.
"""
from __future__ import annotations

import csv
import io
import re
import statistics
import time
import zipfile
from html.parser import HTMLParser
from pathlib import Path

from agent_friday.services.library import caps
from agent_friday.services.library.caps import CapExceeded
from agent_friday.services.library.textclean import clean_text, safe_title


class Unsupported(CapExceeded):
    """The file kind is not one the Library reads (shown as 'skipped')."""


CODE_EXTS = {"py", "js", "ts", "tsx", "jsx", "c", "h", "cpp", "hpp", "cs", "java", "go", "rs", "rb",
             "php", "sh", "ps1", "sql", "css", "json", "yaml", "yml", "toml", "ini", "xml"}
TEXT_EXTS = {"txt", "log", "rst", "tex"}
TRANSCRIPT_EXTS = {"vtt", "srt"}
MEDIA_EXTS = {"mp3", "wav", "m4a", "ogg", "flac", "aac", "opus", "mp4", "webm", "mov", "m4v", "mkv"}
MD_EXTS = {"md", "markdown"}
HTML_EXTS = {"html", "htm", "xhtml"}
KINDS = {"pdf": {"pdf"}, "docx": {"docx"}, "markdown": MD_EXTS, "text": TEXT_EXTS, "code": CODE_EXTS,
         "html": HTML_EXTS, "csv": {"csv", "tsv"}, "xlsx": {"xlsx"},
         "transcript": TRANSCRIPT_EXTS, "media": MEDIA_EXTS}
EXT_TO_KIND = {e: k for k, es in KINDS.items() for e in es}


def kind_for(path: str | Path) -> str | None:
    return EXT_TO_KIND.get(Path(path).suffix.lower().lstrip("."))


def _block(kind: str, text: str, page: int | None = None, bbox=None, level: int = 0, **extra) -> dict:
    b = {"kind": kind, "text": text, "page": page, "bbox": bbox, "level": level}
    b.update(extra)
    return b


class _Budget:
    """Running totals checked against the per-document caps."""

    def __init__(self):
        self.blocks = 0
        self.chars = 0

    def add(self, text: str) -> None:
        self.blocks += 1
        self.chars += len(text.encode("utf-8"))
        if self.blocks > caps.MAX_BLOCKS:
            raise CapExceeded("too many paragraphs")
        if self.chars > caps.MAX_TEXT_BYTES:
            raise CapExceeded("too much text")


# ── PDF ──────────────────────────────────────────────────────────────────────

def _ocr_engine():
    try:
        from rapidocr_onnxruntime import RapidOCR
        return RapidOCR()
    except Exception:
        return None


def _group_lines(words: list[dict], tol: float = 3.0) -> list[dict]:
    """Words -> lines (top-to-bottom, left-to-right)."""
    words = sorted(words, key=lambda w: (round(w["top"]), w["x0"]))
    lines: list[dict] = []
    for w in words:
        if lines and abs(w["top"] - lines[-1]["top"]) <= tol:
            ln = lines[-1]
            ln["words"].append(w)
            ln["x1"] = max(ln["x1"], w["x1"])
            ln["bottom"] = max(ln["bottom"], w["bottom"])
        else:
            lines.append({"top": w["top"], "bottom": w["bottom"], "x0": w["x0"], "x1": w["x1"],
                          "words": [w]})
    out = []
    for ln in lines:
        ws = sorted(ln["words"], key=lambda w: w["x0"])
        text = ws[0]["text"]
        for a, b in zip(ws, ws[1:]):
            text += (" " if b["x0"] - a["x1"] > 0.5 else "") + b["text"]
        size = max((w.get("size") or 0) for w in ws)
        bold = any("bold" in (w.get("fontname") or "").lower() for w in ws)
        ln.update(text=text, size=size or (ln["bottom"] - ln["top"]), bold=bold, x0=ws[0]["x0"])
        out.append(ln)
    return out


def _split_columns(words: list[dict], page_w: float) -> list[list[dict]]:
    """One list of words per column. A page is two columns when a clear gutter
    runs through the middle and almost no word straddles it."""
    if len(words) < 40:
        return [words]
    iv = sorted((w["x0"], w["x1"]) for w in words)
    merged = [list(iv[0])]
    for a, b in iv[1:]:
        if a <= merged[-1][1] + 0.1:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    best = None
    for (_, e1), (s2, _) in zip(merged, merged[1:]):
        mid = (e1 + s2) / 2
        if 0.3 * page_w <= mid <= 0.7 * page_w and s2 - e1 >= 14 and (best is None or s2 - e1 > best[1] - best[0]):
            best = (e1, s2)
    if not best:
        return [words]
    mid = (best[0] + best[1]) / 2
    left = [w for w in words if (w["x0"] + w["x1"]) / 2 < mid]
    right = [w for w in words if (w["x0"] + w["x1"]) / 2 >= mid]
    return [left, right] if left and right else [words]


def _paragraphs(lines: list[dict], body: float) -> list[dict]:
    """Lines -> paragraph groups. A new group starts at a vertical gap, a size
    change or a hanging heading line."""
    groups: list[list[dict]] = []
    for ln in lines:
        if groups:
            prev = groups[-1][-1]
            gap = ln["top"] - prev["bottom"]
            same_size = abs(ln["size"] - prev["size"]) <= 0.6
            if gap <= max(0.9 * ln["size"], 4.0) and same_size and not _is_heading_line(prev, body) \
                    and ln["x0"] - prev["x0"] < 60:
                groups[-1].append(ln)
                continue
        groups.append([ln])
    return groups


def _is_heading_line(ln: dict, body: float) -> bool:
    t = ln["text"].strip()
    if not t or len(t) > 140:
        return False
    if ln["size"] >= body * 1.18:
        return True
    return ln["bold"] and len(t) <= 90 and ln["size"] >= body * 0.98 and not t.endswith((".", ",", ";"))


def _norm_margin(t: str) -> str:
    return re.sub(r"\d+", "#", t.strip().lower())


def _extract_pdf(path: Path) -> dict:
    import pdfplumber

    bud = _Budget()
    page_lines: list[tuple[int, float, float, list[list[dict]], list[dict]]] = []
    sizes: list[float] = []
    title = None
    ocr = None
    ocr_deadline = time.monotonic() + caps.OCR_SECONDS_PER_DOC
    ocr_pages = 0
    with pdfplumber.open(str(path)) as pdf:
        n = len(pdf.pages)
        if n > caps.MAX_PAGES:
            raise CapExceeded("too many pages")
        try:
            title = (pdf.metadata or {}).get("Title")
        except Exception:
            title = None
        for i, page in enumerate(pdf.pages, 1):
            pw, ph = float(page.width), float(page.height)
            tables = []
            try:
                for t in page.find_tables()[:6]:
                    rows = t.extract()
                    if rows and len(rows) >= 2:
                        tables.append({"bbox": [float(v) for v in t.bbox], "rows": rows})
            except Exception:
                tables = []
            try:
                words = page.extract_words(extra_attrs=["size", "fontname"], keep_blank_chars=False)
            except Exception:
                words = []
            if tables:
                def _in_table(w):
                    cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
                    return any(t["bbox"][0] <= cx <= t["bbox"][2] and t["bbox"][1] <= cy <= t["bbox"][3]
                               for t in tables)
                words = [w for w in words if not _in_table(w)]
            cols = _split_columns(words, pw)
            col_lines = [_group_lines(c) for c in cols]
            if len(words) < 3 and not tables:
                # No text layer: read the page image locally, within the budget.
                if ocr is None:
                    ocr = _ocr_engine() or False
                if ocr and time.monotonic() < ocr_deadline:
                    col_lines = [_ocr_page_lines(path, i, ocr)]
                    ocr_pages += 1
                    for ln in col_lines[0]:
                        ln["ocr"] = True
            for c in col_lines:
                sizes += [ln["size"] for ln in c for _ in range(max(1, len(ln["text"]) // 20))]
            page_lines.append((i, pw, ph, col_lines, tables))
    body = statistics.median(sizes) if sizes else 11.0
    # Running heads and page numbers: short lines in the top or bottom margin
    # whose text repeats on many pages.
    margin_count: dict[str, int] = {}
    for _, pw, ph, cols, _t in page_lines:
        for c in cols:
            for ln in c:
                if ln["top"] < ph * 0.07 or ln["bottom"] > ph * 0.93:
                    margin_count[_norm_margin(ln["text"])] = margin_count.get(_norm_margin(ln["text"]), 0) + 1
    need = max(3, int(0.4 * len(page_lines)))
    drop = {k for k, v in margin_count.items() if v >= need}

    def is_margin_noise(ln, ph):
        if not (ln["top"] < ph * 0.07 or ln["bottom"] > ph * 0.93):
            return False
        t = ln["text"].strip()
        return _norm_margin(t) in drop or re.fullmatch(r"(page\s*)?\d{1,4}(\s*of\s*\d{1,4})?", t.lower()) is not None

    # Heading levels by size rank.
    cand_sizes = sorted({round(ln["size"], 1) for _, _, ph, cols, _t in page_lines for c in cols for ln in c
                         if _is_heading_line(ln, body) and ln["size"] >= body * 1.18}, reverse=True)[:4]
    blocks: list[dict] = []
    for i, pw, ph, cols, tables in page_lines:
        items: list[tuple[float, dict]] = []
        for c in cols:
            c = [ln for ln in c if not is_margin_noise(ln, ph)]
            for grp in _paragraphs(c, body):
                first = grp[0]
                text = clean_text(" ".join(g["text"] for g in grp))
                if not text:
                    continue
                bbox = [round(min(g["x0"] for g in grp), 1), round(min(g["top"] for g in grp), 1),
                        round(max(g["x1"] for g in grp), 1), round(max(g["bottom"] for g in grp), 1)]
                if len(grp) == 1 and _is_heading_line(first, body):
                    sz = round(first["size"], 1)
                    lvl = (cand_sizes.index(sz) + 1) if sz in cand_sizes else min(len(cand_sizes) + 1, 4)
                    b = _block("heading", text, i, bbox, lvl)
                else:
                    b = _block("ocr" if first.get("ocr") else "para", text, i, bbox)
                items.append((bbox[1], b))
        for t in tables:
            rows = [[clean_text(str(c or "")) for c in r] for r in t["rows"]]
            text = "\n".join(" | ".join(r) for r in rows)
            items.append((t["bbox"][1], _block("table", text, i, [round(v, 1) for v in t["bbox"]],
                                                header=" | ".join(rows[0]))))
        # Columns keep their own order; tables slot in by vertical position.
        ordered = []
        col_items = [it for it in items if it[1]["kind"] != "table"]
        tab_items = sorted((it for it in items if it[1]["kind"] == "table"), key=lambda x: x[0])
        for it in col_items:
            while tab_items and tab_items[0][0] <= it[0] and len(cols) == 1:
                ordered.append(tab_items.pop(0)[1])
            ordered.append(it[1])
        ordered += [t[1] for t in tab_items]
        for b in ordered:
            bud.add(b["text"])
            blocks.append(b)
    if not title or len(title.strip()) < 3:
        title = next((b["text"] for b in blocks if b["kind"] == "heading" and b["level"] == 1), None)
    return {"title": title, "pages": n, "blocks": blocks, "ocr_pages": ocr_pages}


def _ocr_page_lines(path: Path, page_no: int, engine) -> list[dict]:
    import numpy as np
    import pypdfium2 as pdfium

    scale = 2.0
    pdf = pdfium.PdfDocument(str(path))
    try:
        pil = pdf[page_no - 1].render(scale=scale).to_pil().convert("RGB")
    finally:
        pdf.close()
    result, _ = engine(np.array(pil))
    lines = []
    for box, text, _score in (result or []):
        xs = [p[0] / scale for p in box]
        ys = [p[1] / scale for p in box]
        h = max(ys) - min(ys)
        lines.append({"top": min(ys), "bottom": max(ys), "x0": min(xs), "x1": max(xs),
                      "text": clean_text(text), "size": h, "bold": False, "words": []})
    lines.sort(key=lambda ln: (round(ln["top"] / 4), ln["x0"]))
    return lines


# ── DOCX ─────────────────────────────────────────────────────────────────────

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_UNSAFE_XML = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.I)


def _safe_xml(raw: bytes):
    """Parse XML with no DTD and no entities: refuse a document that has one."""
    if _UNSAFE_XML.search(raw):
        raise CapExceeded("contains unsafe XML")
    import xml.etree.ElementTree as ET
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        raise CapExceeded("is not valid") from None


def _zip_guard(z: zipfile.ZipFile) -> None:
    infos = z.infolist()
    if len(infos) > caps.MAX_ZIP_MEMBERS:
        raise CapExceeded("looks like a zip bomb")
    if sum(i.file_size for i in infos) > caps.MAX_ZIP_UNCOMPRESSED:
        raise CapExceeded("looks like a zip bomb")


def _read_member(z: zipfile.ZipFile, name: str, limit: int = caps.MAX_ZIP_UNCOMPRESSED) -> bytes:
    with z.open(name) as f:
        raw = f.read(limit + 1)
    if len(raw) > limit:
        raise CapExceeded("looks like a zip bomb")
    return raw


def _extract_docx(path: Path) -> dict:
    bud = _Budget()
    with zipfile.ZipFile(str(path)) as z:
        _zip_guard(z)
        try:
            root = _safe_xml(_read_member(z, "word/document.xml"))
        except KeyError:
            raise CapExceeded("is not a Word document") from None
    body = root.find(_W + "body")
    blocks: list[dict] = []
    title = None

    def para_text(p) -> str:
        out = []
        for el in p.iter():
            if el.tag == _W + "t" and el.text:
                out.append(el.text)
            elif el.tag in (_W + "tab", _W + "br"):
                out.append(" ")
        return clean_text("".join(out))

    for el in (body if body is not None else []):
        if el.tag == _W + "p":
            text = para_text(el)
            if not text:
                continue
            ppr = el.find(_W + "pPr")
            style = ""
            lvl = 0
            if ppr is not None:
                ps = ppr.find(_W + "pStyle")
                style = (ps.get(_W + "val") if ps is not None else "") or ""
                ol = ppr.find(_W + "outlineLvl")
                if ol is not None and (ol.get(_W + "val") or "").isdigit():
                    lvl = int(ol.get(_W + "val")) + 1
            m = re.match(r"(?i)heading\s*(\d)", style)
            if m:
                lvl = int(m.group(1))
            elif style.lower() == "title":
                lvl = 1
                title = title or text
            if lvl and len(text) <= 300:
                b = _block("heading", text, None, None, min(lvl, 6))
            elif style.lower().startswith("list"):
                b = _block("list", text)
            else:
                b = _block("para", text)
            bud.add(text)
            blocks.append(b)
        elif el.tag == _W + "tbl":
            rows = []
            for tr in el.iter(_W + "tr"):
                rows.append([para_text(tc) for tc in tr.findall(_W + "tc")])
            rows = [r for r in rows if any(r)]
            if rows:
                text = "\n".join(" | ".join(r) for r in rows)
                bud.add(text)
                blocks.append(_block("table", text, header=" | ".join(rows[0])))
    if not title:
        title = next((b["text"] for b in blocks if b["kind"] == "heading" and b["level"] == 1), None)
    return {"title": title, "pages": None, "blocks": blocks}


# ── Markdown, text, code ─────────────────────────────────────────────────────

def _decode(raw: bytes) -> str:
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", "replace")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1", "replace")


def _read_text_file(path: Path) -> str:
    with open(path, "rb") as f:
        raw = f.read(caps.MAX_TEXT_BYTES + 1)
    if len(raw) > caps.MAX_TEXT_BYTES:
        raise CapExceeded("too much text")
    if b"\x00" in raw[:4096] and raw[:2] not in (b"\xff\xfe", b"\xfe\xff"):
        raise CapExceeded("is not text")
    return _decode(raw)


def _extract_markdown(path: Path, *, headings: bool = True, code: bool = False) -> dict:
    bud = _Budget()
    text = _read_text_file(path)
    blocks: list[dict] = []
    buf: list[str] = []
    fence = False

    def flush(kind="para"):
        if buf:
            t = clean_text("\n".join(buf))
            if t:
                bud.add(t)
                blocks.append(_block("code" if code else kind, t))
            buf.clear()

    lines = text.splitlines()
    for idx, ln in enumerate(lines):
        if headings and ln.lstrip().startswith(("```", "~~~")):
            fence = not fence
            buf.append(ln)
            continue
        if fence:
            buf.append(ln)
            continue
        m = re.match(r"^(#{1,6})\s+(.*\S)\s*#*\s*$", ln) if headings else None
        if m:
            flush()
            t = clean_text(m.group(2))
            bud.add(t)
            blocks.append(_block("heading", t, level=len(m.group(1))))
            continue
        if headings and idx + 1 < len(lines) and ln.strip() and re.fullmatch(r"=+|-{3,}", lines[idx + 1].strip() or "x") \
                and not buf:
            flush()
            t = clean_text(ln)
            bud.add(t)
            blocks.append(_block("heading", t, level=1 if lines[idx + 1].strip().startswith("=") else 2))
            lines[idx + 1] = ""
            continue
        if not ln.strip():
            flush("list" if buf and re.match(r"^\s*([-*+]|\d+\.)\s", buf[0]) else "para")
        else:
            buf.append(ln)
    flush()
    title = next((b["text"] for b in blocks if b["kind"] == "heading" and b["level"] == 1), None)
    return {"title": title, "pages": None, "blocks": blocks}


# ── HTML (text only) ─────────────────────────────────────────────────────────

class _HTMLText(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "iframe", "object", "embed", "svg", "head"}
    BLOCK = {"p", "div", "li", "br", "tr", "section", "article", "blockquote", "pre", "ul", "ol",
             "table", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.title = ""
        self._in_title = False
        self.cur: list[str] = []
        self.heading = 0
        self.out: list[tuple[int, str]] = []

    def _flush(self):
        t = clean_text(" ".join(self.cur))
        if t:
            self.out.append((self.heading, t))
        self.cur = []

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP and tag != "head":
            self.skip += 1
        if tag in self.BLOCK:
            self._flush()
            self.heading = int(tag[1]) if tag[0] == "h" and tag[1:].isdigit() else 0

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP and tag != "head" and self.skip:
            self.skip -= 1
        if tag in self.BLOCK:
            self._flush()
            self.heading = 0

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip:
            self.cur.append(data)


def _extract_html(path: Path) -> dict:
    bud = _Budget()
    p = _HTMLText()
    p.feed(_read_text_file(path))
    p._flush()
    blocks = []
    for lvl, t in p.out:
        bud.add(t)
        blocks.append(_block("heading", t, level=lvl) if lvl and len(t) <= 300 else _block("para", t))
    return {"title": clean_text(p.title) or None, "pages": None, "blocks": blocks}


# ── CSV and XLSX ─────────────────────────────────────────────────────────────

_ROWS_PER_BLOCK = 25
_BLOCK_CHARS = 800


def _row_blocks(rows: list[list[str]], bud: _Budget, label: str | None = None) -> list[dict]:
    rows = [[clean_text(c) for c in r] for r in rows if any((c or "").strip() for c in r)]
    if not rows:
        return []
    header = " | ".join(rows[0])
    out, cur, size = [], [], 0
    for r in rows[1:] or []:
        line = " | ".join(r)
        if cur and (len(cur) >= _ROWS_PER_BLOCK or size + len(line) > _BLOCK_CHARS):
            t = "\n".join([header] + cur)
            bud.add(t)
            out.append(_block("table", t, header=header))
            cur, size = [], 0
        cur.append(line)
        size += len(line)
    if cur or len(rows) == 1:
        t = "\n".join([header] + cur)
        bud.add(t)
        out.append(_block("table", t, header=header))
    return out


def _extract_csv(path: Path) -> dict:
    bud = _Budget()
    text = _read_text_file(path)
    delim = "\t" if path.suffix.lower() == ".tsv" else ","
    csv.field_size_limit(1_000_000)
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    return {"title": None, "pages": None, "blocks": _row_blocks(rows, bud)}


def _col_index(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def _extract_xlsx(path: Path) -> dict:
    bud = _Budget()
    with zipfile.ZipFile(str(path)) as z:
        _zip_guard(z)
        names = set(z.namelist())
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = _safe_xml(_read_member(z, "xl/sharedStrings.xml"))
            ns = root.tag.split("}")[0] + "}"
            for si in root.findall(ns + "si"):
                shared.append("".join(t.text or "" for t in si.iter(ns + "t")))
        sheets = sorted(n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
        if not sheets:
            raise CapExceeded("is not a spreadsheet")
        root = _safe_xml(_read_member(z, sheets[0]))
    ns = root.tag.split("}")[0] + "}"
    rows: list[list[str]] = []
    for row in root.iter(ns + "row"):
        cells: dict[int, str] = {}
        for c in row.findall(ns + "c"):
            ref = c.get("r") or ""
            if not ref:
                continue
            t = c.get("t")
            v = c.find(ns + "v")
            f = c.find(ns + "f")
            if t == "s" and v is not None and (v.text or "").isdigit():
                val = shared[int(v.text)] if int(v.text) < len(shared) else ""
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(ns + "t"))
            elif v is not None and v.text is not None:
                val = v.text
            elif f is not None and f.text:
                val = "=" + f.text          # a formula is shown as text, never evaluated
            else:
                val = ""
            cells[_col_index(ref)] = val
        if cells:
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
        if len(rows) > 200_000:
            raise CapExceeded("too many rows")
    return {"title": None, "pages": None, "blocks": _row_blocks(rows, bud)}


# ── transcripts: timed segments ─────────────────────────────────────────────

_CUE_TIME = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})\s*-->\s*(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")
_TAGS = re.compile(r"<[^>]{0,80}>")
SEGMENT_SECONDS = 75.0


def _secs(h, m, s, ms) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")[:3]) / 1000.0


def parse_cues(text: str) -> list[tuple[float, float, str]]:
    """(start, end, text) for each cue of a WebVTT or SRT file. Markup inside a
    cue is dropped; nothing in it is ever followed or rendered."""
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        for i, ln in enumerate(lines):
            m = _CUE_TIME.search(ln)
            if m:
                g = m.groups()
                body = clean_text(_TAGS.sub("", " ".join(lines[i + 1:])))
                if body:
                    cues.append((_secs(*g[:4]), _secs(*g[4:]), body))
                break
    return cues


def _extract_transcript(path: Path) -> dict:
    bud = _Budget()
    cues = parse_cues(_read_text_file(path))
    if not cues:
        raise CapExceeded("has no timed text")
    blocks, cur = [], None
    for a, b, t in cues:
        if cur and (a - cur["t0"] >= SEGMENT_SECONDS or len(cur["text"]) + len(t) > 700):
            blocks.append(cur)
            cur = None
        if cur is None:
            cur = _block("segment", t, None, None, t0=a, t1=b)
        else:
            cur["text"] += " " + t
            cur["t1"] = b
    if cur:
        blocks.append(cur)
    for b in blocks:
        bud.add(b["text"])
    return {"title": None, "pages": None, "blocks": blocks}


def _extract_media(path: Path) -> dict:
    """Audio and video are read through a transcript: a caption file beside the
    file here (in the limited child), or the Media transcriber's cache, which
    the caller asks in its own process (`media_cache_result`). Friday has one
    transcriber; the Library does not run a second."""
    for ext in (".vtt", ".srt"):
        side = path.with_suffix(ext)
        if side.is_file():
            return _extract_transcript(side)
    raise Unsupported("needs a transcript first")


def media_cache_result(path: Path) -> dict | None:
    """The extraction result for a recording from the Media transcriber's cache,
    in the shape `extract_document` returns, or None."""
    res = _media_cache_transcript(path)
    if not res:
        return None
    for i, b in enumerate(res["blocks"]):
        b["ord"] = i
    res.update(kind="media", ext=path.suffix.lower().lstrip("."), title=safe_title(path.stem))
    return res


def _media_cache_transcript(path: Path) -> dict | None:
    """Segments from the Media transcriber's cache, when that module is present
    and has already transcribed this file. Absent module or no cache: None."""
    try:
        from agent_friday.services import media_transcripts as mt
    except Exception:
        return None
    seg_fn = getattr(mt, "segments_for_path", None)
    segs = seg_fn(path) if seg_fn else None
    if not segs:
        return None
    bud = _Budget()
    blocks, cur = [], None
    for s in segs:
        a, b, t = float(s["start"]), float(s["end"]), clean_text(str(s.get("text") or ""))
        if not t:
            continue
        if cur and (a - cur["t0"] >= SEGMENT_SECONDS or len(cur["text"]) + len(t) > 700):
            blocks.append(cur)
            cur = None
        if cur is None:
            cur = _block("segment", t, None, None, t0=a, t1=b)
        else:
            cur["text"] += " " + t
            cur["t1"] = b
    if cur:
        blocks.append(cur)
    for b in blocks:
        bud.add(b["text"])
    return {"title": None, "pages": None, "blocks": blocks} if blocks else None


# ── entry ────────────────────────────────────────────────────────────────────

def extract_document(path: str | Path) -> dict:
    """{kind, title, pages, blocks:[...]} or raises CapExceeded / Unsupported."""
    p = Path(path)
    size = p.stat().st_size
    if size > caps.MAX_FILE_BYTES:
        raise CapExceeded("too large")
    kind = kind_for(p)
    if kind is None:
        raise Unsupported("this kind of file isn't read yet")
    ext = p.suffix.lower().lstrip(".")
    if kind == "pdf":
        res = _extract_pdf(p)
    elif kind == "docx":
        res = _extract_docx(p)
    elif kind == "markdown":
        res = _extract_markdown(p)
    elif kind == "code":
        res = _extract_markdown(p, headings=False, code=True)
    elif kind == "text":
        res = _extract_markdown(p, headings=False)
    elif kind == "html":
        res = _extract_html(p)
    elif kind == "csv":
        res = _extract_csv(p)
    elif kind == "xlsx":
        res = _extract_xlsx(p)
    elif kind == "transcript":
        res = _extract_transcript(p)
    elif kind == "media":
        res = _extract_media(p)
    else:  # pragma: no cover
        raise Unsupported("this kind of file isn't read yet")
    for i, b in enumerate(res["blocks"]):
        b["ord"] = i
    res["kind"] = kind
    res["ext"] = ext
    res["title"] = safe_title(res.get("title") or p.stem)
    if not res["blocks"]:
        raise CapExceeded("has no readable text")
    return res


def _task(args: dict) -> dict:
    return extract_document(args["path"])


# The worker adds these to its task table when it loads this module.
TASKS = {"extract": _task}
