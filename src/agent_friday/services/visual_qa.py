"""Visual QA (NS-24.4-1, NS-36.5-8): does a file Friday made LOOK right?

services/artifact_qa reads a file the way a reader's program parses it
(does it open, do its formulas work). This module looks at the layout the
reader will see, and reports problems as fixed codes with page (or slide, or
sheet) numbers. A finding never carries the file's words.

  PDF      pages are rendered locally with pdfium (pypdfium2, which ships
           with pdfplumber, a core dependency) and read with pdfplumber:
             page_empty / page_nearly_empty   a blank or all-but-blank page
             text_off_page                    text outside the page
             text_near_edge                   text inside a printer's margin
             text_overlap                     text drawn over other text
             content_off_page                 a chart, table or picture
                                              spilling off one edge
             font_missing                     a font neither embedded nor
                                              one of the PDF standard 14
             image_distorted                  a picture shown at another
                                              aspect ratio than its pixels
             low_contrast                     text colour against the
                                              rendered background (WCAG)
             large_gap                        a big blank band between blocks
  Word, PowerPoint, Excel
           checked from the file's own layout (always), and, when LibreOffice
           is installed here, converted to PDF in a temporary folder and
           rendered as above, plus font_substituted (a font the file asks for
           that is not in the rendered output):
             .pptx  shape_off_slide, text_overflow (estimated), alt_text_missing,
                    image_distorted
             .docx  table_too_wide, graphic_too_wide, long_unbroken_text,
                    image_distorted
             .xlsx  cell_text_cut, cell_shows_hashes

Every result says what it did ("status"):
  checked           every check ran on every page
  partial           a cap was hit (pages, time): the pages after it were not
                    looked at, so the file is never "passed"
  could_not_render  the PDF renderer is missing here or failed: no pixel
                    check ran (what could be read without pixels is still
                    reported); never a pass
  unavailable       a frozen build, which cannot start the isolated check
  not_applicable    a kind this module does not look at

Bounded (bytes, pages, a time budget, a pixel cap per page, a converter
timeout, an isolated child with a hard limit), deterministic (no sampling at
random, pages in order) and fail-safe: `check` never raises.
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import posixpath
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

#: Files bigger than this are not looked at.
MAX_BYTES = 100 * 1024 * 1024
#: Only the first pages are looked at; a longer file is "partial".
MAX_PAGES = 30
#: The page loop stops after this long; the rest is "partial".
TIME_BUDGET_S = 12.0
#: A rendered page has at most this many pixels.
MAX_RENDER_PIXELS = 1_600_000
RENDER_SCALE = 1.25
#: Per page: characters read for edges and contrast, words for overlaps,
#: drawn objects for spills.
MAX_CHARS = 20_000
MAX_CONTRAST_SAMPLES = 160
MAX_WORDS = 2_000
MAX_OBJECTS = 4_000
#: Office files: cells, paragraphs and media read at most.
MAX_CELLS = 200_000
MAX_PARAGRAPHS = 20_000
MAX_MEDIA_HEAD = 512 * 1024
#: LibreOffice gets this long to convert one file.
CONVERT_TIMEOUT_S = 15
#: The isolated child is abandoned after this long.
ISOLATED_TIMEOUT_S = 45
#: Page numbers listed per finding, findings per result.
MAX_LISTED_PAGES = 20
MAX_FINDINGS = 30

#: Text closer than this (points) to the page edge may be cut by a printer.
EDGE_PT = 12.0
#: WCAG 2 contrast: 4.5:1 for body text, 3:1 for large text (18pt, or 14pt
#: bold). Below SEVERE_CONTRAST the text is all but unreadable: an error.
CONTRAST_NORMAL = 4.5
CONTRAST_LARGE = 3.0
SEVERE_CONTRAST = 2.0
#: A page is flagged when at least this many characters fall short.
LOW_CONTRAST_MIN_CHARS = 3
#: A picture is distorted when its shown and pixel aspect ratios differ by more.
DISTORTION = 0.05
#: A blank band taller than this share of the page, with content above and below.
GAP_SHARE = 0.25

VISUAL_KINDS = frozenset((".pdf", ".docx", ".pptx", ".xlsx", ".xlsm"))
STATUSES = ("checked", "partial", "could_not_render", "unavailable", "not_applicable")
UNITS = ("page", "slide", "sheet")

#: Every code: its usual severity and the plain words for it. A few codes
#: are raised to "error" when the case is severe (said where it happens).
CODES: Dict[str, Tuple[str, str]] = {
    "page_empty": ("error", "a page is empty"),
    "page_nearly_empty": ("warn", "a page is nearly empty"),
    "text_off_page": ("error", "text runs off the edge of the page"),
    "text_near_edge": ("warn", "text sits so close to the edge that a printer may cut it off"),
    "text_overlap": ("warn", "text is drawn over other text"),
    "content_off_page": ("error", "a chart, table or picture spills off the page"),
    "font_missing": ("warn", "a font is not embedded, so the reader may see a different font"),
    "font_substituted": ("warn", "a font the file asks for was replaced by another when it was rendered"),
    "image_distorted": ("warn", "a picture is stretched out of shape"),
    "low_contrast": ("warn", "text is hard to read against its background"),
    "large_gap": ("warn", "a large blank gap breaks up the page"),
    "shape_off_slide": ("error", "a shape runs off the slide"),
    "text_overflow": ("warn", "text probably overflows its box"),
    "alt_text_missing": ("error", "a picture or chart has no alt text"),
    "table_too_wide": ("warn", "a table is wider than the page's text area"),
    "graphic_too_wide": ("warn", "a picture or chart is wider than the page's text area"),
    "long_unbroken_text": ("warn", "a long unbroken run of text is wider than the page"),
    "cell_text_cut": ("warn", "text in a cell is cut off by the next cell"),
    "cell_shows_hashes": ("warn", "a column is too narrow for its dates, so they show as ####"),
}
_RANK = {"error": 0, "warn": 1}

#: The PDF standard 14 families every reader has.
_STANDARD_14 = ("courier", "helvetica", "times", "symbol", "zapfdingbats")
EMU_PER_PT = 12700
TWIPS_PER_PT = 20


# ── results ─────────────────────────────────────────────────────────────────

class _Capped(Exception):
    """A native check reached one of its caps (the message says which)."""


class _Found:
    """Findings gathered per (code, unit): pages and how often."""

    def __init__(self):
        self.items: Dict[tuple, dict] = {}

    def add(self, code: str, page: Optional[int] = None, *, unit: Optional[str] = None,
            severity: Optional[str] = None) -> None:
        sev = severity or CODES[code][0]
        it = self.items.setdefault((code, unit), {"code": code, "severity": sev, "unit": unit,
                                                  "pages": set(), "count": 0})
        if _RANK[sev] < _RANK[it["severity"]]:
            it["severity"] = sev
        it["count"] += 1
        if page is not None:
            it["pages"].add(int(page))

    def codes(self) -> Set[str]:
        return {c for c, _u in self.items}

    def findings(self) -> List[dict]:
        out = []
        for it in sorted(self.items.values(), key=lambda i: (_RANK[i["severity"]], i["code"], i["unit"] or "")):
            out.append({"code": it["code"], "severity": it["severity"], "unit": it["unit"],
                        "pages": sorted(it["pages"])[:MAX_LISTED_PAGES], "count": int(it["count"])})
        return out[:MAX_FINDINGS]


def _result(kind: str, status: str, *, found: Optional[_Found] = None, method: Optional[str] = None,
            renderer: Optional[str] = None, converter: Optional[str] = None, pages_checked: int = 0,
            pages_total: Optional[int] = None, reason: Optional[str] = None) -> dict:
    findings = found.findings() if found else []
    return {"status": status, "kind": kind, "method": method, "renderer": renderer, "converter": converter,
            "pages_checked": int(pages_checked), "pages_total": pages_total, "reason": reason,
            "ok": not any(f["severity"] == "error" for f in findings), "findings": findings}


def _qa():
    """services/artifact_qa: its zip reader refuses DTDs and oversized parts.
    In the isolated child (loaded by file path, no package) it is loaded the
    same way, from beside this file."""
    mod = sys.modules.get("agent_friday.services.artifact_qa")
    if mod is not None:
        return mod
    if __package__:
        from agent_friday.services import artifact_qa
        return artifact_qa
    mod = sys.modules.get("artifact_qa")
    if mod is None:
        spec = importlib.util.spec_from_file_location("artifact_qa", str(Path(__file__).with_name("artifact_qa.py")))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules["artifact_qa"] = mod
    return mod


# ── renderers and converters ────────────────────────────────────────────────

def _renderer():
    """pypdfium2, or None when it is not installed here."""
    try:
        import pypdfium2
        return pypdfium2
    except Exception:
        return None


def find_converter() -> Optional[str]:
    """LibreOffice's soffice when this machine has it, else None."""
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    candidates = []
    for env in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env)
        if base:
            candidates.append(Path(base) / "LibreOffice" / "program" / "soffice.exe")
    candidates.append(Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"))
    for c in candidates:
        try:
            if c.is_file():
                return str(c)
        except OSError:
            continue
    return None


def _convert(exe: str, src: Path, work: Path, timeout: float = CONVERT_TIMEOUT_S) -> Optional[Path]:
    """`src` converted to PDF inside `work` (a temporary folder), or None. The
    converter reads a copy under a fixed name, with a profile of its own."""
    inp = work / ("input" + src.suffix.lower())
    shutil.copyfile(str(src), str(inp))
    out_dir = work / "out"
    out_dir.mkdir()
    argv = [exe, "--headless", "--norestore", "--nolockcheck", "--nodefault",
            "-env:UserInstallation=" + (work / "profile").as_uri(),
            "--convert-to", "pdf", "--outdir", str(out_dir), str(inp)]
    try:
        subprocess.run(argv, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return None
    pdf = out_dir / "input.pdf"
    return pdf if pdf.is_file() and pdf.stat().st_size > 0 else None


# ── PDF ─────────────────────────────────────────────────────────────────────

def _name(v) -> str:
    """A PDF name or string as plain text."""
    n = getattr(v, "name", v)
    if isinstance(n, bytes):
        n = n.decode("latin-1", "replace")
    return str(n or "").strip("/'")


def _font_family(base: str) -> str:
    """'ABCDEF+Calibri-Bold' -> 'calibri' (for comparing names)."""
    plain = base.split("+", 1)[-1]
    plain = re.split(r"[-,]", plain, maxsplit=1)[0]
    return re.sub(r"[^a-z0-9]", "", plain.lower()).replace("mt", "").replace("ps", "")


def _page_fonts(page) -> List[Tuple[str, bool]]:
    """(base font name, embedded) for each font the page's resources name."""
    from pdfminer.pdftypes import resolve1
    out = []
    try:
        fonts = resolve1((page.page_obj.resources or {}).get("Font")) or {}
        items = list(fonts.values())[:200]
    except Exception:
        return out
    for font in items:
        try:
            fd = resolve1(font) or {}
            base = _name(resolve1(fd.get("BaseFont")))
            subtype = _name(resolve1(fd.get("Subtype")))
            if subtype == "Type3":
                continue
            desc = resolve1(fd.get("FontDescriptor")) or {}
            if subtype == "Type0":
                kids = resolve1(fd.get("DescendantFonts")) or []
                kid = resolve1(kids[0]) if kids else {}
                desc = resolve1((kid or {}).get("FontDescriptor")) or {}
            embedded = any(k in desc for k in ("FontFile", "FontFile2", "FontFile3"))
            out.append((base, embedded))
        except Exception:
            continue
    return out


def _rgb(color) -> Optional[Tuple[int, int, int]]:
    """A pdfplumber colour (gray, RGB or CMYK, 0..1) as 0..255 RGB."""
    if not isinstance(color, (tuple, list)) or not color:
        return None
    try:
        v = [float(x) for x in color]
    except (TypeError, ValueError):
        return None
    if len(v) == 1:
        g = round(255 * min(1, max(0, v[0])))
        return (g, g, g)
    if len(v) == 3:
        return tuple(round(255 * min(1, max(0, x))) for x in v)
    if len(v) == 4:
        c, m, y, k = (min(1, max(0, x)) for x in v)
        return (round(255 * (1 - c) * (1 - k)), round(255 * (1 - m) * (1 - k)), round(255 * (1 - y) * (1 - k)))
    return None


def _luminance(rgb) -> float:
    def ch(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb[:3]
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast_ratio(a, b) -> float:
    """WCAG 2 contrast ratio of two RGB colours (1.0 to 21.0)."""
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _spills(o: dict, box: Tuple[float, float, float, float]) -> bool:
    """A drawn object partly on the page that runs off one edge. One that
    runs off both opposite edges, or spans the page, is a deliberate bleed."""
    bx0, btop, bx1, bbottom = box
    w, h = bx1 - bx0, bbottom - btop
    try:
        x0, x1, top, bottom = float(o["x0"]), float(o["x1"]), float(o["top"]), float(o["bottom"])
    except (KeyError, TypeError, ValueError):
        return False
    if not (x1 > bx0 and x0 < bx1 and bottom > btop and top < bbottom):
        return False                                     # wholly off the page: never seen
    tx, ty = max(3.0, 0.01 * w), max(3.0, 0.01 * h)
    left, right = bx0 - x0 > tx, x1 - bx1 > tx
    up, down = btop - top > ty, bottom - bbottom > ty
    if (left and right) or (up and down) or (x1 - x0) >= 0.95 * w or (bottom - top) >= 0.95 * h:
        return False
    return left or right or up or down


def _distorted(shown_w: float, shown_h: float, px_w: float, px_h: float) -> bool:
    if min(shown_w, shown_h) <= 0 or min(px_w, px_h) <= 0:
        return False
    shown, pixels = shown_w / shown_h, px_w / px_h
    off = abs(shown / pixels - 1)
    turned = abs(shown * pixels - 1)                     # rotated a quarter turn
    return off > DISTORTION and turned > DISTORTION


def _structure(page, n: int, found: _Found, fonts: Dict[str, bool]) -> dict:
    """The checks that need no pixels. Returns what the pixel pass reuses."""
    box = tuple(float(v) for v in page.bbox)
    bx0, btop, bx1, bbottom = box
    chars = [c for c in page.chars[:MAX_CHARS] if (c.get("text") or "").strip()]
    off = near = False
    for c in chars:
        try:
            x0, x1, top, bottom = float(c["x0"]), float(c["x1"]), float(c["top"]), float(c["bottom"])
        except (KeyError, TypeError, ValueError):
            continue
        if x0 < bx0 - 1 or x1 > bx1 + 1 or top < btop - 1 or bottom > bbottom + 1:
            off = True
        elif x0 < bx0 + EDGE_PT or x1 > bx1 - EDGE_PT or top < btop + EDGE_PT or bottom > bbottom - EDGE_PT:
            near = True
    if off:
        found.add("text_off_page", n, unit="page")
    elif near:
        found.add("text_near_edge", n, unit="page")
    # Text over text: words from different lines whose boxes overlap.
    try:
        words = page.extract_words()[:MAX_WORDS]
    except Exception:
        words = []
    grid: Dict[tuple, list] = {}
    overlap = False
    for i, wd in enumerate(words):
        try:
            x0, x1, top, bottom = float(wd["x0"]), float(wd["x1"]), float(wd["top"]), float(wd["bottom"])
        except (KeyError, TypeError, ValueError):
            continue
        cells = {(gx, gy) for gx in range(int(x0 // 60), int(x1 // 60) + 1)
                 for gy in range(int(top // 60), int(bottom // 60) + 1)}
        for cell in cells:
            for j in grid.get(cell, ()):
                other = words[j]
                if other.get("text") == wd.get("text") or abs(float(other["top"]) - top) < 1.0:
                    continue                             # a fake-bold copy, or the same line
                ix = min(x1, float(other["x1"])) - max(x0, float(other["x0"]))
                iy = min(bottom, float(other["bottom"])) - max(top, float(other["top"]))
                if ix > 0 and iy > 0:
                    a = (x1 - x0) * (bottom - top)
                    b = (float(other["x1"]) - float(other["x0"])) * (float(other["bottom"]) - float(other["top"]))
                    if ix * iy > 0.3 * max(1e-6, min(a, b)):
                        overlap = True
                        break
            if overlap:
                break
            grid.setdefault(cell, []).append(i)
        if overlap:
            break
    if overlap:
        found.add("text_overlap", n, unit="page")
    # Charts, tables and pictures spilling off one edge.
    objects = (list(page.rects) + list(page.lines) + list(page.curves))[:MAX_OBJECTS]
    images = list(page.images)[:MAX_OBJECTS]
    if any(_spills(o, box) for o in objects + images):
        found.add("content_off_page", n, unit="page")
    for im in images:
        try:
            sw, sh = im.get("srcsize") or (0, 0)
            w, h = float(im["x1"]) - float(im["x0"]), float(im["bottom"]) - float(im["top"])
        except (KeyError, TypeError, ValueError):
            continue
        if min(sw, sh) >= 16 and min(w, h) >= 18 and _distorted(w, h, sw, sh):
            found.add("image_distorted", n, unit="page")
            break
    missing = False
    for base, embedded in _page_fonts(page):
        fonts[base] = fonts.get(base, False) or embedded
        if not embedded and not base.split("+")[-1].lower().startswith(_STANDARD_14):
            missing = True
    if missing:
        found.add("font_missing", n, unit="page")
    return {"box": box, "chars": chars, "images": len(images), "objects": len(objects)}


def _render(doc, index: int):
    page = doc[index]
    try:
        w, h = page.get_size()
        if w <= 0 or h <= 0:
            return None
        scale = min(RENDER_SCALE, math.sqrt(MAX_RENDER_PIXELS / float(w * h)))
        bitmap = page.render(scale=scale)
        try:
            return bitmap.to_pil().convert("RGB")
        finally:
            try:
                bitmap.close()
            except Exception:
                pass
    finally:
        try:
            page.close()
        except Exception:
            pass


def _pixels(img, info: dict, n: int, total: int, found: _Found) -> None:
    """Empty pages, big gaps and contrast, from the rendered page."""
    from PIL import Image
    gray = img.convert("L")
    W, H = gray.size
    hist = gray.histogram()
    bg = max(range(256), key=lambda v: hist[v])
    mask = gray.point([255 if abs(v - bg) > 32 else 0 for v in range(256)])
    ink = mask.histogram()[255] / float(max(1, W * H))
    chars = info["chars"]
    if ink < 0.0002:
        found.add("page_empty", n, unit="page")
        return
    if ink < 0.003 and len(chars) <= 10 and not info["images"]:
        found.add("page_nearly_empty", n, unit="page")
    # A big blank band between blocks (not on a cover or the last page, and
    # not the space above a footer: there must be real content below it).
    if 1 < n < total:
        try:
            rows = list(mask.resize((1, H), getattr(Image, "Resampling", Image).BOX).getdata())
        except Exception:
            rows = []
        inked = [i for i, v in enumerate(rows) if v > 0.25]
        if inked:
            first, last = inked[0], inked[-1]
            best, gap_end = 0, None
            prev = first
            for i in inked[1:]:
                if i - prev > best:
                    best, gap_end = i - prev, i
                prev = i
            if gap_end is not None and best > GAP_SHARE * H:
                above = (gap_end - best) - first
                below = last - gap_end
                if above > 0.05 * H and below > 0.05 * H:
                    found.add("large_gap", n, unit="page")
    # Contrast: each sampled character's fill colour against the most common
    # colour of the rendered pixels under it (the background).
    bx0, btop, bx1, bbottom = info["box"]
    pw, ph = max(1e-6, bx1 - bx0), max(1e-6, bbottom - btop)
    sx, sy = W / pw, H / ph
    step = max(1, len(chars) // MAX_CONTRAST_SAMPLES)
    low, worst = 0, 21.0
    for c in chars[::step][:MAX_CONTRAST_SAMPLES]:
        fg = _rgb(c.get("non_stroking_color"))
        if fg is None:
            continue
        try:
            x0, x1 = (float(c["x0"]) - bx0) * sx, (float(c["x1"]) - bx0) * sx
            y0, y1 = (float(c["top"]) - btop) * sy, (float(c["bottom"]) - btop) * sy
            size = float(c.get("size") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        x0, x1 = max(0, int(x0)), min(W, int(math.ceil(x1)))
        y0, y1 = max(0, int(y0)), min(H, int(math.ceil(y1)))
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        colors = img.crop((x0, y0, x1, y1)).getcolors((x1 - x0) * (y1 - y0))
        if not colors:
            continue
        back = max(colors, key=lambda cc: cc[0])[1]
        ratio = contrast_ratio(fg, back)
        bold = "bold" in str(c.get("fontname") or "").lower()
        need = CONTRAST_LARGE if size >= 18 or (bold and size >= 14) else CONTRAST_NORMAL
        if ratio < need:
            low += 1
            worst = min(worst, ratio)
    if low >= LOW_CONTRAST_MIN_CHARS or (low and len(chars) < LOW_CONTRAST_MIN_CHARS):
        found.add("low_contrast", n, unit="page", severity="error" if worst < SEVERE_CONTRAST else "warn")


def _check_pdf(path: Path, deadline: float, found: _Found, fonts: Dict[str, bool], *,
               kind: str = "pdf") -> dict:
    with open(path, "rb") as fh:
        if not fh.read(1024).lstrip().startswith(b"%PDF-"):
            return _result(kind, "could_not_render", found=found, reason="the file is not a PDF")
    try:
        import pdfplumber
    except Exception:
        return _result(kind, "could_not_render", found=found, reason="no PDF reader is installed here")
    pdfium = _renderer()
    with pdfplumber.open(str(path)) as pdf:
        pages = pdf.pages
        total = len(pages)
        if not total:
            return _result(kind, "could_not_render", found=found, pages_total=0,
                           reason="the PDF has no pages to look at")
        doc, render_reason = None, None
        if pdfium is None:
            render_reason = "no PDF renderer is installed here"
        else:
            try:
                doc = pdfium.PdfDocument(str(path))
            except Exception:
                render_reason = "the PDF renderer could not open the file"
        checked = rendered = 0
        timed_out = False
        try:
            for i in range(min(total, MAX_PAGES)):
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                page = pages[i]
                info = _structure(page, i + 1, found, fonts)
                if doc is not None:
                    try:
                        img = _render(doc, i)
                        if img is not None:
                            _pixels(img, info, i + 1, total, found)
                            rendered += 1
                    except Exception:
                        img = None
                    if img is None and render_reason is None:
                        render_reason = "page %d could not be rendered" % (i + 1)
                elif not info["chars"] and not info["images"] and not info["objects"]:
                    found.add("page_empty", i + 1, unit="page")      # nothing drawn at all
                checked += 1
                try:
                    page.close()
                except Exception:
                    pass
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass
    common = {"found": found, "method": "rendered", "renderer": "pdfium" if pdfium else None,
              "pages_checked": checked, "pages_total": total}
    if render_reason:
        return _result(kind, "could_not_render", reason=render_reason, **common)
    if checked < total:
        why = ("time ran out after %d of %d pages" % (checked, total) if timed_out
               else "only the first %d of %d pages were looked at" % (checked, total))
        return _result(kind, "partial", reason=why, **common)
    return _result(kind, "checked", **common)


# ── Office files, from their own layout ─────────────────────────────────────

_NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
_R_EMBED = "{%s}embed" % _NS["r"]
_R_ID = "{%s}id" % _NS["r"]


def _int(v, default=0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def image_size(data: bytes) -> Optional[Tuple[int, int]]:
    """(width, height) in pixels from a PNG, GIF or JPEG header, or None."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR" and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        return struct.unpack("<HH", data[6:10])
    if data[:3] == b"\xff\xd8\xff":
        i, n = 2, len(data)
        while i + 9 < n:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01, 0xFF) or 0xD0 <= marker <= 0xD7:
                i += 2 if marker != 0xFF else 1
                continue
            seg = struct.unpack(">H", data[i + 2:i + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return (w, h)
            i += 2 + seg
    return None


def _rels(pkg, part: str) -> Dict[str, str]:
    """{relationship id: package path} for one part's internal targets."""
    folder, _, name = part.rpartition("/")
    root = pkg.xml("%s/_rels/%s.rels" % (folder, name))
    out = {}
    for rel in (list(root) if root is not None else []):
        if (rel.get("TargetMode") or "") == "External":
            continue
        target = (rel.get("Target") or "").replace("\\", "/")
        full = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(folder, target))
        out[rel.get("Id") or ""] = full
    return out


def _media_size(pkg, name: Optional[str]) -> Optional[Tuple[int, int]]:
    if not name or name not in pkg.names:
        return None
    try:
        with pkg.z.open(name) as fh:
            return image_size(fh.read(MAX_MEDIA_HEAD))
    except Exception:
        return None


def _picture_distorted(pkg, rels: Dict[str, str], blip_fill, cx: int, cy: int) -> bool:
    """A picture shown at cx x cy (EMU) whose (cropped) pixels have another shape."""
    if blip_fill is None:
        return False
    blip = blip_fill.find("a:blip", _NS)
    size = _media_size(pkg, rels.get(blip.get(_R_EMBED) or "")) if blip is not None else None
    if not size:
        return False
    w, h = float(size[0]), float(size[1])
    crop = blip_fill.find("a:srcRect", _NS)
    if crop is not None:
        w *= 1 - (_int(crop.get("l")) + _int(crop.get("r"))) / 100000.0
        h *= 1 - (_int(crop.get("t")) + _int(crop.get("b"))) / 100000.0
    if min(size) < 16:
        return False
    return _distorted(float(cx), float(cy), w, h)


def _decorative(cnvpr) -> bool:
    return any(el.tag.endswith("}decorative") and el.get("val") in ("1", "true") for el in cnvpr.iter())


def _pptx_native(path: Path, found: _Found) -> Set[str]:
    qa = _qa()
    pkg = qa._Pkg(path)
    pres = pkg.xml("ppt/presentation.xml")
    if pres is None:
        return set()
    size = pres.find("p:sldSz", _NS)
    sw = _int(size.get("cx"), 12192000) if size is not None else 12192000
    sh = _int(size.get("cy"), 6858000) if size is not None else 6858000
    tol_x, tol_y = 0.005 * sw, 0.005 * sh
    slides = sorted(pkg.matching(r"ppt/slides/slide\d+\.xml"), key=lambda n: int(re.findall(r"\d+", n)[-1]))
    fonts: Set[str] = set()
    a = "{%s}" % _NS["a"]
    for name in slides[:500]:
        root = pkg.xml(name)
        if root is None:
            continue
        n = int(re.findall(r"\d+", name)[-1])
        rels = _rels(pkg, name)
        tree = root.find("p:cSld/p:spTree", _NS)
        for shape in (list(tree) if tree is not None else []):
            x = None
            for sub in ("p:spPr/a:xfrm", "p:grpSpPr/a:xfrm", "p:xfrm"):
                x = shape.find(sub, _NS)
                if x is not None:
                    break
            off = x.find("a:off", _NS) if x is not None else None
            ext = x.find("a:ext", _NS) if x is not None else None
            if off is not None and ext is not None:
                ox, oy, cx, cy = (_int(off.get("x")), _int(off.get("y")), _int(ext.get("cx")), _int(ext.get("cy")))
                if ox < -tol_x or oy < -tol_y or ox + cx > sw + tol_x or oy + cy > sh + tol_y:
                    found.add("shape_off_slide", n, unit="slide")
                tag = shape.tag.rsplit("}", 1)[-1]
                if tag == "sp" and _text_overflows(shape, cx, cy):
                    found.add("text_overflow", n, unit="slide")
                if tag == "pic" and _picture_distorted(pkg, rels, shape.find("p:blipFill", _NS), cx, cy):
                    found.add("image_distorted", n, unit="slide")
        for pic in root.iter("{%s}pic" % _NS["p"]):
            pr = pic.find("p:nvPicPr/p:cNvPr", _NS)
            if pr is not None and not (pr.get("descr") or "").strip() and not _decorative(pr):
                found.add("alt_text_missing", n, unit="slide")
        for frame in root.iter("{%s}graphicFrame" % _NS["p"]):
            data = frame.find("a:graphic/a:graphicData", _NS)
            pr = frame.find("p:nvGraphicFramePr/p:cNvPr", _NS)
            if data is not None and (data.get("uri") or "").endswith("/chart") and pr is not None \
                    and not (pr.get("descr") or "").strip() and not _decorative(pr):
                found.add("alt_text_missing", n, unit="slide")
        for latin in root.iter(a + "latin"):
            face = (latin.get("typeface") or "").strip()
            if face and not face.startswith("+"):
                fonts.add(face)
    if len(slides) > 500:
        raise _Capped("only the first 500 slides were looked at")
    return fonts


def _text_overflows(sp, cx: int, cy: int) -> bool:
    """An estimate: does the shape's text need more room than the box has?
    Average glyph width 0.5em, line height 1.2em, 18pt when no size is set.
    Boxes that shrink text or grow to fit it are never flagged."""
    body = sp.find("p:txBody", _NS)
    if body is None:
        return False
    pr = body.find("a:bodyPr", _NS)
    if pr is not None:
        if pr.find("a:normAutofit", _NS) is not None or pr.find("a:spAutoFit", _NS) is not None:
            return False
        if (pr.get("vert") or "horz") != "horz":
            return False
    def g(k, d):
        return _int(pr.get(k), d) if pr is not None else d
    avail_w = (cx - g("lIns", 91440) - g("rIns", 91440)) / float(EMU_PER_PT)
    avail_h = (cy - g("tIns", 45720) - g("bIns", 45720)) / float(EMU_PER_PT)
    no_wrap = pr is not None and pr.get("wrap") == "none"
    a = "{%s}" % _NS["a"]
    height, any_text = 0.0, False
    for para in body.findall("a:p", _NS)[:500]:
        size, segments = None, [0]
        for child in para:
            tag = child.tag
            if tag in (a + "r", a + "fld"):
                rpr = child.find("a:rPr", _NS)
                if size is None and rpr is not None and rpr.get("sz"):
                    size = _int(rpr.get("sz"), 1800) / 100.0
                t = child.find("a:t", _NS)
                segments[-1] += len((t.text or "") if t is not None else "")
            elif tag == a + "br":
                segments.append(0)
        if size is None:
            end = para.find("a:endParaRPr", _NS)
            size = _int(end.get("sz"), 1800) / 100.0 if end is not None and end.get("sz") else 18.0
        size = max(1.0, size)
        lines = 0
        for chars in segments:
            any_text = any_text or chars > 0
            need = chars * 0.5 * size
            if avail_w <= 1:
                if chars:
                    return True
                lines += 1
            elif no_wrap:
                if need > avail_w * 1.1:
                    return True
                lines += 1
            else:
                lines += max(1, int(math.ceil(need / avail_w)))
        height += lines * size * 1.2
    return any_text and height > avail_h * 1.15 + 2


def _docx_native(path: Path, found: _Found) -> Set[str]:
    qa = _qa()
    pkg = qa._Pkg(path)
    doc = pkg.xml("word/document.xml")
    if doc is None:
        return set()
    w = "{%s}" % _NS["w"]
    body = doc.find("w:body", _NS)
    if body is None:
        return set()
    sect = body.find("w:sectPr", _NS)
    pg = sect.find("w:pgSz", _NS) if sect is not None else None
    mar = sect.find("w:pgMar", _NS) if sect is not None else None
    page_w = _int(pg.get(w + "w"), 12240) if pg is not None else 12240
    left = _int(mar.get(w + "left"), 1440) if mar is not None else 1440
    right = _int(mar.get(w + "right"), 1440) if mar is not None else 1440
    text_w = max(1, page_w - left - right)                         # twips
    styles = pkg.xml("word/styles.xml")
    size_pt = 11.0
    if styles is not None:
        sz = styles.find("w:docDefaults/w:rPrDefault/w:rPr/w:sz", _NS)
        if sz is not None and sz.get(w + "val"):
            size_pt = max(1.0, _int(sz.get(w + "val"), 22) / 2.0)
    # Tables: the grid's width (and indent) against the text area and the page.
    for tbl in body.findall("w:tbl", _NS)[:2000]:
        cols = sum(_int(gc.get(w + "w")) for gc in tbl.findall("w:tblGrid/w:gridCol", _NS))
        ind = tbl.find("w:tblPr/w:tblInd", _NS)
        indent = _int(ind.get(w + "w")) if ind is not None and (ind.get(w + "type") in (None, "dxa")) else 0
        if cols and left + indent + cols > page_w:
            found.add("table_too_wide", severity="error")
        elif cols and indent + cols > text_w * 1.05:
            found.add("table_too_wide")
    rels = _rels(pkg, "word/document.xml")
    text_emu = text_w * 635                                         # twips -> EMU
    for holder in list(doc.iter("{%s}inline" % _NS["wp"])) + list(doc.iter("{%s}anchor" % _NS["wp"])):
        ext = holder.find("wp:extent", _NS)
        if ext is None:
            continue
        cx, cy = _int(ext.get("cx")), _int(ext.get("cy"))
        if holder.tag.endswith("}inline") and cx > text_emu * 1.02:
            found.add("graphic_too_wide")
        pic = holder.find(".//pic:pic", _NS)
        if pic is not None and _picture_distorted(pkg, rels, pic.find("pic:blipFill", _NS), cx, cy):
            found.add("image_distorted")
    # Long unbroken runs (a URL, a hash) wider than the text area.
    width_pt = text_w / float(TWIPS_PER_PT)
    paragraphs = body.findall("w:p", _NS)
    for para in paragraphs[:MAX_PARAGRAPHS]:
        text = "".join(t.text or "" for t in para.iter(w + "t"))
        if not text:
            continue
        size = size_pt
        sz = para.find(".//w:rPr/w:sz", _NS)
        if sz is not None and sz.get(w + "val"):
            size = max(1.0, _int(sz.get(w + "val"), 22) / 2.0)
        longest = max((len(tok) for tok in text.split()), default=0)
        if longest * 0.5 * size > width_pt:
            found.add("long_unbroken_text")
    fonts: Set[str] = set()
    for root in (doc, styles):
        if root is None:
            continue
        for rf in root.iter(w + "rFonts"):
            for attr in ("ascii", "hAnsi"):
                v = (rf.get(w + attr) or "").strip()
                if v:
                    fonts.add(v)
    if len(paragraphs) > MAX_PARAGRAPHS:
        raise _Capped("only the first %d paragraphs were looked at" % MAX_PARAGRAPHS)
    return fonts


#: Widths (in digit widths) of dates in the built-in formats.
_DATE_WIDTHS = {14: 10, 15: 9, 16: 6, 17: 6, 18: 8, 19: 11, 20: 5, 21: 8, 22: 15, 45: 5, 46: 8, 47: 7}
_DATE_BUILTIN = set(range(14, 23)) | set(range(27, 37)) | {45, 46, 47} | set(range(50, 59))


def _date_width(fmt_id: int, code: Optional[str], value: str) -> float:
    """How wide (in digit widths) a date shows in its format."""
    if code is None:
        if fmt_id == 14:
            try:
                import datetime
                d = datetime.date(1899, 12, 30) + datetime.timedelta(days=int(float(value)))
                return len(str(d.month)) + len(str(d.day)) + 4 + 2 * 0.6
            except (ValueError, OverflowError):
                return 10
        return _DATE_WIDTHS.get(fmt_id, 10)
    plain = re.sub(r'"[^"]*"|\\.|\[[^\]]*\]|_.|\*.', "", code.split(";", 1)[0])
    plain = plain.replace("mmmm", "mmmmmmmmm").replace("dddd", "ddddddddd")
    return sum(0.6 if ch in "/-.:, " else 1.0 for ch in plain)


def _xlsx_native(path: Path, found: _Found) -> Set[str]:
    qa = _qa()
    pkg = qa._Pkg(path)
    wb = pkg.xml("xl/workbook.xml")
    if wb is None:
        return set()
    s = "{%s}" % _NS["s"]
    targets = {}
    rels = pkg.xml("xl/_rels/workbook.xml.rels")
    for rel in (list(rels) if rels is not None else []):
        targets[rel.get("Id")] = (rel.get("Target") or "").lstrip("/").replace("xl/", "", 1)
    # Cell styles: number format and whether the text wraps.
    custom, xfs = {}, []
    styles = pkg.xml("xl/styles.xml")
    if styles is not None:
        for nf in styles.iter(s + "numFmt"):
            custom[_int(nf.get("numFmtId"), -1)] = nf.get("formatCode") or ""
        cell_xfs = styles.find("s:cellXfs", _NS)
        for xf in (cell_xfs.findall("s:xf", _NS) if cell_xfs is not None else []):
            al = xf.find("s:alignment", _NS)
            xfs.append((_int(xf.get("numFmtId")), al is not None and al.get("wrapText") in ("1", "true")
                        or (al is not None and al.get("shrinkToFit") in ("1", "true"))))
    shared: List[int] = []
    sroot = pkg.xml("xl/sharedStrings.xml")
    if sroot is not None:
        for si in sroot.findall("s:si", _NS):
            shared.append(sum(len(t.text or "") for t in si.iter(s + "t")))
    budget = MAX_CELLS
    for index, sheet in enumerate(wb.findall(".//s:sheets/s:sheet", _NS), 1):
        rid = sheet.get(_R_ID)
        part = "xl/" + targets[rid] if rid in targets else None
        root = pkg.xml(part) if part else None
        if root is None:
            continue
        fmt = root.find("s:sheetFormatPr", _NS)
        default_w = 8.43
        try:
            if fmt is not None and fmt.get("defaultColWidth"):
                default_w = max(0.0, min(255.0, float(fmt.get("defaultColWidth"))))
        except ValueError:
            pass
        widths: Dict[int, float] = {}
        for col in list(root.iter(s + "col"))[:1000]:
            lo, hi = _int(col.get("min"), 0), _int(col.get("max"), 0)
            try:
                width = float(col.get("width"))
            except (TypeError, ValueError):
                continue
            if col.get("hidden") in ("1", "true"):
                continue
            for c in range(max(1, lo), min(hi, lo + 300) + 1):
                widths[c] = width
        merged = []
        for mc in list(root.iter(s + "mergeCell"))[:2000]:
            ends = [qa._split_ref(x) for x in (mc.get("ref") or "").split(":")]
            if len(ends) == 2 and all(ends):
                merged.append((ends[0][0], ends[0][1], ends[1][0], ends[1][1]))

        def in_merge(c, r):
            return any(c1 <= c <= c2 and r1 <= r <= r2 for c1, r1, c2, r2 in merged)
        for row in root.iter(s + "row"):
            cells = {}
            for c in row.findall("s:c", _NS):
                budget -= 1
                pos = qa._split_ref(c.get("r") or "")
                v = c.find("s:v", _NS)
                t = c.get("t") or "n"
                if pos is None:
                    continue
                if t == "inlineStr":
                    length = sum(len(x.text or "") for x in c.iter(s + "t"))
                    cells[pos[0]] = ("text", length, _int(c.get("s")), "")
                elif v is None or not (v.text or "").strip():
                    continue
                elif t == "s":
                    try:
                        length = shared[int(v.text)]
                    except (ValueError, IndexError):
                        length = 0
                    cells[pos[0]] = ("text", length, _int(c.get("s")), "")
                elif t == "str":
                    cells[pos[0]] = ("text", len(v.text or ""), _int(c.get("s")), "")
                elif t == "n":
                    cells[pos[0]] = ("num", 0, _int(c.get("s")), v.text or "")
                else:
                    cells[pos[0]] = ("other", 0, _int(c.get("s")), "")
            r = _int(row.get("r"), 0)
            for col, (kind, length, style, value) in cells.items():
                width = widths.get(col, default_w)
                fmt_id, wraps = xfs[style] if 0 <= style < len(xfs) else (0, False)
                if in_merge(col, r):
                    continue
                if kind == "text" and not wraps and length * 0.9 > width * 1.1 + 1 and (col + 1) in cells:
                    found.add("cell_text_cut", index, unit="sheet")
                elif kind == "num":
                    code = custom.get(fmt_id)
                    is_date = (fmt_id in _DATE_BUILTIN and code is None) or (
                        code is not None and qa._format_category(fmt_id, code) == "date")
                    if is_date and _date_width(fmt_id, code, value) > width + 0.01:
                        found.add("cell_shows_hashes", index, unit="sheet")
            if budget <= 0:
                raise _Capped("only the first %d cells were looked at" % MAX_CELLS)
    return set()


_NATIVE = {".docx": _docx_native, ".pptx": _pptx_native, ".xlsx": _xlsx_native, ".xlsm": _xlsx_native}


def _check_office(path: Path, kind: str, deadline: float) -> dict:
    found = _Found()
    try:
        requested = _NATIVE[path.suffix.lower()](path, found)
        capped = None
    except _Capped as c:
        requested, capped = set(), str(c)
    if capped:
        # Part of the file was not read: whatever was found stands, and the
        # file is never passed on what was not looked at.
        return _result(kind, "partial", found=found, method="structural", reason=capped)
    exe = find_converter()
    if exe:
        with tempfile.TemporaryDirectory(prefix="friday-vqa-") as td:
            pdf = _convert(exe, path, Path(td), min(CONVERT_TIMEOUT_S, max(1.0, deadline - time.monotonic())))
            if pdf is not None:
                fonts: Dict[str, bool] = {}
                res = _check_pdf(pdf, deadline + TIME_BUDGET_S, found, fonts, kind=kind)
                if res["status"] in ("checked", "partial"):
                    shown = {_font_family(f) for f in fonts}
                    if any(_font_family(f) and _font_family(f) not in shown for f in requested):
                        found.add("font_substituted")
                    return _result(kind, res["status"], found=found, method="converted", renderer=res["renderer"],
                                   converter="libreoffice", pages_checked=res["pages_checked"],
                                   pages_total=res["pages_total"], reason=res["reason"])
    # No converter (or it could not render this file): the layout checks
    # read from the file itself are the visual check.
    return _result(kind, "checked", found=found, method="structural",
                   converter="libreoffice" if exe else None)


# ── entry points ────────────────────────────────────────────────────────────

def check(path, *, budget_s: float = TIME_BUDGET_S) -> dict:
    """The visual check of one file. Never raises. See the module docstring
    for the result's shape and what each status means."""
    p = Path(path)
    kind = p.suffix.lower().lstrip(".")
    if p.suffix.lower() not in VISUAL_KINDS:
        return _result(kind, "not_applicable")
    found = _Found()
    try:
        if not p.is_file():
            return _result(kind, "could_not_render", reason="the file is not there")
        if p.stat().st_size > MAX_BYTES:
            return _result(kind, "could_not_render", reason="the file is too large to look at")
        deadline = time.monotonic() + max(0.0, float(budget_s))
        if p.suffix.lower() == ".pdf":
            return _check_pdf(p, deadline, found, {})
        return _check_office(p, kind, deadline)
    except Exception as e:
        return _result(kind, "could_not_render", found=found,
                       reason="the file could not be read for a visual check (%s)" % type(e).__name__)


def _validated(res, kind: str) -> Optional[dict]:
    """The child's answer, accepted only in exactly the expected shape."""
    if not isinstance(res, dict) or res.get("status") not in STATUSES:
        return None
    fs = res.get("findings")
    if not isinstance(fs, list) or len(fs) > MAX_FINDINGS:
        return None
    found = _Found()
    for f in fs:
        if not (isinstance(f, dict) and f.get("code") in CODES and f.get("severity") in _RANK
                and (f.get("unit") is None or f.get("unit") in UNITS)
                and isinstance(f.get("pages"), list) and len(f["pages"]) <= MAX_LISTED_PAGES
                and all(isinstance(n, int) and 0 < n < 10 ** 6 for n in f["pages"])
                and isinstance(f.get("count"), int)):
            return None
        for n in f["pages"] or [None]:
            found.add(f["code"], n, unit=f["unit"], severity=f["severity"])
        found.items[(f["code"], f["unit"])]["count"] = max(0, min(f["count"], 10 ** 6))
    def text(k, allowed=None):
        v = res.get(k)
        return v if isinstance(v, str) and len(v) <= 200 and (allowed is None or v in allowed) else None

    def num(k):
        v = res.get(k)
        return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 10 ** 7 else None
    return _result(kind, res["status"], found=found, method=text("method", ("rendered", "converted", "structural")),
                   renderer=text("renderer", ("pdfium",)), converter=text("converter", ("libreoffice",)),
                   pages_checked=num("pages_checked") or 0, pages_total=num("pages_total"),
                   reason=text("reason"))


def check_isolated(path, timeout: float = ISOLATED_TIMEOUT_S) -> dict:
    """`check`, run in a child Python with a hard time limit (the renderer
    and parsers read untrusted files). A child that does not finish, or
    answers in any other shape, is "could_not_render", never a pass. A frozen
    build cannot start a child: "unavailable". Never raises."""
    p = Path(path)
    kind = p.suffix.lower().lstrip(".")
    if p.suffix.lower() not in VISUAL_KINDS:
        return _result(kind, "not_applicable")
    if getattr(sys, "frozen", False):
        return _result(kind, "unavailable", reason="the visual check cannot run in this build")
    code = ("import importlib.util,json,sys\n"
            "s=importlib.util.spec_from_file_location('visual_qa',sys.argv[1])\n"
            "m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\n"
            "sys.stdout.write(json.dumps(m.check(sys.argv[2])))\n")
    try:
        out = subprocess.run([sys.executable, "-I", "-c", code, str(Path(__file__).resolve()), str(p)],
                             capture_output=True, timeout=timeout, text=True, stdin=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        res = _validated(json.loads(out.stdout or "{}"), kind)
        if res is not None:
            return res
        why = "the visual check gave no usable answer"
    except subprocess.TimeoutExpired:
        why = "the visual check did not finish within %d seconds" % timeout
    except Exception as e:
        why = "the visual check could not run (%s)" % type(e).__name__
    return _result(kind, "could_not_render", reason=why)


# ── words ───────────────────────────────────────────────────────────────────

def _where(f: dict) -> str:
    pages = f.get("pages") or []
    unit = f.get("unit")
    if not pages or not unit:
        return ""
    listed = ", ".join(str(n) for n in pages[:5])
    more = " and more" if len(pages) > 5 or f.get("count", 0) > len(pages) and len(pages) >= MAX_LISTED_PAGES else ""
    return "%s %s%s" % (unit if len(pages) == 1 else unit + "s", listed, more)


def describe(f: dict) -> str:
    """One finding in plain words, from fixed wording: never the file's text."""
    words = CODES.get(f.get("code"), ("warn", "a layout problem"))[1]
    where = _where(f)
    return "%s (%s)" % (words, where) if where else words


def messages(res: Optional[dict], severity: str) -> List[str]:
    return [describe(f) for f in ((res or {}).get("findings") or []) if f.get("severity") == severity]


def needs_attention(res: Optional[dict]) -> Optional[str]:
    """Why a visual result leaves the file unverified (it was not fully
    looked at), or None when it does not."""
    st = (res or {}).get("status")
    if st == "could_not_render":
        return "could not check how it looks here: " + ((res or {}).get("reason") or "no renderer")
    if st == "partial":
        return "how it looks was only partly checked: " + ((res or {}).get("reason") or "a limit was reached")
    return None


def summary(res: Optional[dict]) -> str:
    """One plain line for a card or a tool result ("" when not applicable)."""
    if not res or res.get("status") in (None, "not_applicable"):
        return ""
    st = res["status"]
    if st == "unavailable":
        return "Looks: not checked (the visual check cannot run in this build)."
    parts = []
    errors, warns = messages(res, "error"), messages(res, "warn")
    if errors:
        parts.append("Looks wrong: " + "; ".join(errors[:5]) + ".")
    if warns:
        parts.append("Looks worth fixing: " + "; ".join(warns[:5]) + ".")
    attention = needs_attention(res)
    if attention:
        parts.insert(0, "Looks: " + attention + ".")
    elif not parts:
        how = {"rendered": "%d page(s) rendered" % res.get("pages_checked", 0),
               "converted": "converted and %d page(s) rendered" % res.get("pages_checked", 0),
               "structural": "checked from the file's layout; no converter here to render it"}.get(
                   res.get("method"), "checked")
        parts.append("Looks: no problems found (%s)." % how)
    return " ".join(parts)


def compact(res: Optional[dict]) -> Optional[dict]:
    """What the artifact manifest keeps: codes, page numbers, counts, the
    status and the fixed-wording reason and summary. None when not applicable."""
    if not res or res.get("status") in (None, "not_applicable"):
        return None
    keep = ("status", "method", "renderer", "converter", "pages_checked", "pages_total", "reason", "ok")
    out = {k: res.get(k) for k in keep}
    out["findings"] = [dict(f) for f in (res.get("findings") or [])][:MAX_FINDINGS]
    out["summary"] = summary(res)
    return out
