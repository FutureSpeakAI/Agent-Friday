"""PDF forms: list a form's fields, fill them into a new file, render a page.

Invariants:

  * Filling never changes the source file. The filled copy is a NEW file in
    Friday's output folder (`output_dir()`), which governance treats as
    internal. Writing anywhere else, or over an existing file, is outward and
    waits for the owner (`classify`); writing over the source is refused.
  * Friday does not invent answers to legal or demographic questions. A field
    that looks like an SSN, a date of birth, race or ethnicity, gender,
    disability, veteran status or criminal history is filled only with a value
    the owner typed in their own message this turn. Signature and attestation
    fields are never filled here: a signature goes through `pdf_signing` and
    its approval card, and an attestation is the owner's to make.
  * Field text read out of a form is someone else's document content.
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any, Optional

INTERNAL = "internal"
OUTWARD = "outward"


class FormRefused(Exception):
    """The request cannot be carried out; the message says why."""


# ── Where output goes ───────────────────────────────────────────────────────

def output_dir() -> Path:
    """Friday's folder for filled and signed PDFs, inside the creations folder
    (an output folder in governance.action_gate)."""
    from agent_friday import core
    return Path(core.CREATIONS_DIR) / "forms"


def unique_output(name: str) -> Path:
    """A path in `output_dir()` that does not exist yet."""
    safe = re.sub(r"[^A-Za-z0-9._ -]+", "_", Path(name).name).strip(" .") or "document.pdf"
    if not safe.lower().endswith(".pdf"):
        safe += ".pdf"
    base = output_dir()
    cand = base / safe
    stem, n = cand.stem, 2
    while cand.exists():
        cand = base / f"{stem}-{n}.pdf"
        n += 1
    return cand


def open_pdf(path) -> Path:
    """Resolve `path` to an existing PDF file, or raise FormRefused."""
    if not path:
        raise FormRefused("a PDF path is required")
    try:
        p = Path(str(path)).expanduser().resolve()
    except Exception as e:
        raise FormRefused(f"invalid path {path!r}: {e}")
    if not p.is_file():
        raise FormRefused(f"file not found: {p}")
    try:
        with open(p, "rb") as f:
            head = f.read(1024)
    except Exception as e:
        raise FormRefused(f"cannot read {p.name}: {e}")
    if b"%PDF" not in head:
        raise FormRefused(f"{p.name} is not a PDF")
    return p


def page_count(src: Path) -> int:
    import pypdf
    return len(pypdf.PdfReader(str(src)).pages)


def page_size(src: Path, page: int) -> tuple:
    """(width, height) in PDF points of 1-based `page`."""
    import pypdf
    box = pypdf.PdfReader(str(src)).pages[page - 1].mediabox
    return float(box.width), float(box.height)


# ── Sensitive fields ────────────────────────────────────────────────────────

#: Fields Friday never fills on its own. Matched against the field's name and
#: its label (tooltip), split into words.
_SENSITIVE = [
    ("signature", re.compile(r"\b(?:signature|sign(?:ed)?(?: here)?|sig|initials?)\b")),
    ("attestation", re.compile(
        r"\b(?:attest\w*|certif(?:y|ies|ied|ication)|declar\w*|affirm\w*|perjury|"
        r"under penalty|i agree|agree(?:ment)? to|acknowledg\w*|oath|sworn|swear)\b")),
    ("ssn", re.compile(
        r"\b(?:ssn|social security|soc sec|taxpayer id\w*|tin|itin|national insurance|"
        r"tax id\w*)\b")),
    ("date_of_birth", re.compile(r"\b(?:dob|date of birth|birth ?date|born|birthday)\b")),
    ("race_ethnicity", re.compile(r"\b(?:race|racial|ethnic\w*|hispanic|latino|latina|latinx)\b")),
    ("gender", re.compile(r"\b(?:gender|sex|pronouns?)\b")),
    ("disability", re.compile(r"\b(?:disab\w*|handicap\w*|impairment)\b")),
    ("veteran", re.compile(r"\b(?:veteran|military service|armed forces|protected vet\w*)\b")),
    ("criminal_history", re.compile(
        r"\b(?:criminal|convict\w*|felon\w*|misdemeanou?r|arrest\w*|offen[cs]es?)\b")),
]

#: Never filled by the fill tool, whatever the owner typed.
_NEVER_FILLED = {"signature", "attestation"}

_QUESTIONS = {
    "signature": "This is a signature field. Signing goes through sign_pdf, which "
                 "always asks you on an approval card first.",
    "attestation": "This is a statement you certify or agree to. Please tick or "
                   "fill it yourself; Friday does not make legal attestations for you.",
    "ssn": "What should go here? Friday does not fill identity numbers unless you "
           "type the value yourself.",
    "date_of_birth": "What is the date of birth to enter? Friday does not fill it "
                     "unless you type it yourself.",
    "race_ethnicity": "How do you want to answer this (or leave it blank / decline)? "
                      "Friday does not answer demographic questions for you.",
    "gender": "How do you want to answer this (or leave it blank)? Friday does not "
              "answer demographic questions for you.",
    "disability": "How do you want to answer this (or leave it blank / decline)? "
                  "Friday does not answer it for you.",
    "veteran": "How do you want to answer this (or leave it blank / decline)? "
               "Friday does not answer it for you.",
    "criminal_history": "How do you want to answer this? Friday does not answer "
                        "legal-history questions for you.",
}


def _words(*parts) -> str:
    text = " ".join(str(p or "") for p in parts)
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)          # camelCase
    text = re.sub(r"[_\-.\[\]/:()#]+", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def sensitive_category(name: str, label: str = "") -> Optional[str]:
    """The kind of sensitive question a field asks, or None."""
    w = _words(name, label)
    for cat, rx in _SENSITIVE:
        if rx.search(w):
            return cat
    return None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def owner_supplied(value: Any, owner_text: Optional[str]) -> bool:
    """True when `value` appears in the owner's own words this turn."""
    if not owner_text or isinstance(value, bool) or value is None:
        return False
    v = _norm(value)
    if not v:
        return False
    if v in _norm(owner_text):
        return True
    # A number typed with different separators (spaces where the value has
    # dashes) is the same number.
    digits = re.sub(r"\D", "", v)
    only_number = len(digits) == len(re.sub(r"[\s\-/.]", "", v))
    if len(digits) < 4 or not only_number:
        return False
    runs = re.findall(r"\d[\d\s\-/.]*\d", owner_text)
    return any(re.sub(r"\D", "", r) == digits for r in runs)


# ── Listing ─────────────────────────────────────────────────────────────────

_FF_READ_ONLY = 1
_FF_REQUIRED = 1 << 1
_FF_RADIO = 1 << 15
_FF_PUSHBUTTON = 1 << 16
_FF_COMBO = 1 << 17


def _qualified_name(obj) -> str:
    parts, node, guard = [], obj, 0
    while node is not None and guard < 32:
        t = node.get("/T")
        if t is not None:
            parts.insert(0, str(t))
        parent = node.get("/Parent")
        node = parent.get_object() if parent is not None else None
        guard += 1
    return ".".join(parts)


def _field_pages(reader) -> dict:
    pages = {}
    for i, page in enumerate(reader.pages, start=1):
        for annot in page.get("/Annots") or []:
            try:
                obj = annot.get_object()
                if obj.get("/Subtype") != "/Widget":
                    continue
                name = _qualified_name(obj)
                if name:
                    pages.setdefault(name, i)
            except Exception:
                continue
    return pages


def _type_of(field: dict) -> str:
    ft = field.get("/FT")
    ff = int(field.get("/Ff") or 0)
    if ft == "/Tx":
        return "text"
    if ft == "/Btn":
        if ff & _FF_PUSHBUTTON:
            return "button"
        return "radio" if ff & _FF_RADIO else "checkbox"
    if ft == "/Ch":
        return "dropdown" if ff & _FF_COMBO else "list"
    if ft == "/Sig":
        return "signature"
    return str(ft or "unknown").lstrip("/")


def _options(field: dict, ftype: str) -> list:
    if ftype in ("dropdown", "list"):
        out = []
        for o in field.get("/Opt") or []:
            if isinstance(o, (list, tuple)) and len(o) >= 2:
                out.append(str(o[1]))
            else:
                out.append(str(o))
        return out
    if ftype in ("checkbox", "radio"):
        return [str(s).lstrip("/") for s in (field.get("/_States_") or [])
                if str(s) != "/Off"]
    return []


def _value(field: dict, ftype: str):
    v = field.get("/V")
    if ftype == "checkbox":
        return v is not None and str(v) not in ("/Off", "Off", "")
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        return [str(x).lstrip("/") for x in v]
    s = str(v)
    return s.lstrip("/") if ftype == "radio" else s


def list_fields(path) -> dict:
    """{"file", "pages", "fields": [{name, type, value, options, required,
    read_only, label, page, sensitive}]} for a PDF form."""
    import pypdf
    src = open_pdf(path)
    try:
        reader = pypdf.PdfReader(str(src))
        raw = reader.get_fields() or {}
        pages = _field_pages(reader)
        n_pages = len(reader.pages)
    except Exception as e:
        raise FormRefused(f"could not read the form in {src.name}: {e}")
    fields = []
    for name, f in raw.items():
        ftype = _type_of(f)
        if ftype == "button":
            continue
        ff = int(f.get("/Ff") or 0)
        label = str(f.get("/TU") or "")
        fields.append({
            "name": name, "type": ftype, "value": _value(f, ftype),
            "options": _options(f, ftype), "required": bool(ff & _FF_REQUIRED),
            "read_only": bool(ff & _FF_READ_ONLY), "label": label,
            "page": pages.get(name),
            "sensitive": ("signature" if ftype == "signature"
                          else sensitive_category(name, label)),
        })
    return {"file": str(src), "pages": n_pages, "fields": fields}


# ── Filling ─────────────────────────────────────────────────────────────────

def _resolve_target(src: Path, output_path) -> Path:
    if not output_path:
        return unique_output(f"{src.stem}-filled.pdf")
    raw = str(output_path)
    p = Path(raw).expanduser()
    if p.name == raw:                   # a bare file name goes in the output folder
        p = output_dir() / p.name
    if p.suffix.lower() != ".pdf":
        p = p.with_suffix(".pdf")
    return p.resolve()


def classify(args: dict) -> tuple:
    """(class, why) for a fill_pdf_form call. See the module invariants."""
    a = args or {}
    if not a.get("output_path"):
        return INTERNAL, "it writes a new file into Friday's output folder"
    try:
        src = Path(str(a.get("path") or "")).expanduser().resolve()
        target = _resolve_target(src, a.get("output_path"))
    except Exception as e:
        return OUTWARD, f"the output path could not be resolved ({e})"
    if a.get("path") and target == src:
        return "forbidden", "it would overwrite the original form"
    if target.exists():
        return OUTWARD, "it would replace a file that already exists"
    from agent_friday.governance import action_gate
    return action_gate.classify_write(str(target))


def _coerce(field: dict, value):
    """The value PyPDFForm expects for this field type."""
    ftype = field["type"]
    if ftype == "checkbox":
        if isinstance(value, bool):
            return value
        return _norm(value) in ("true", "yes", "y", "1", "on", "x", "checked") or \
            _norm(value) in [o.lower() for o in field["options"]]
    if ftype in ("dropdown", "list", "radio"):
        opts = field["options"]
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < len(opts):
            return value
        low = [o.lower() for o in opts]
        if _norm(value) in low:
            return low.index(_norm(value))
        raise FormRefused(f"'{value}' is not one of the options for {field['name']}: {opts}")
    return "" if value is None else str(value)


def fill_form(path, values: dict, *, owner_text: Optional[str] = None,
              output_path=None) -> dict:
    """Fill `values` into a copy of the form at `path`.

    Returns {"output", "filled", "needs_owner", "unknown", "invalid"}. No file is
    written when nothing may be filled.
    """
    if not isinstance(values, dict) or not values:
        raise FormRefused("'values' must map field names to values")
    src = open_pdf(path)
    info = list_fields(src)
    by_name = {f["name"]: f for f in info["fields"]}
    allowed, needs_owner, unknown, invalid = {}, [], [], []
    for name, value in values.items():
        f = by_name.get(name)
        if f is None:
            unknown.append(name)
            continue
        if f["read_only"]:
            invalid.append({"field": name, "reason": "the field is read-only"})
            continue
        cat = f["sensitive"]
        if cat and (cat in _NEVER_FILLED or not owner_supplied(value, owner_text)):
            needs_owner.append({"field": name, "category": cat,
                                "question": _QUESTIONS.get(cat, "Please answer this yourself.")})
            continue
        try:
            allowed[name] = _coerce(f, value)
        except FormRefused as e:
            invalid.append({"field": name, "reason": str(e)})
    out = {"source": str(src), "output": None, "filled": sorted(allowed),
           "needs_owner": needs_owner, "unknown": unknown, "invalid": invalid}
    if not allowed:
        return out
    target = _resolve_target(src, output_path)
    if target == src:
        raise FormRefused("the filled form is never written over the original")
    from PyPDFForm import PdfWrapper
    try:
        data = PdfWrapper(str(src)).fill(allowed).read()
    except Exception as e:
        raise FormRefused(f"could not fill {src.name}: {e}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    out["output"] = str(target)
    return out


# ── Drawing and rendering ───────────────────────────────────────────────────

def stamp_image(src: Path, image_png: bytes, page: int, box) -> bytes:
    """The PDF bytes of `src` with `image_png` drawn in `box` (x, y, w, h in
    points from the page's bottom-left) on 1-based `page`. Form fields stay."""
    from PyPDFForm import PdfWrapper, RawElements
    x, y, w, h = (float(v) for v in box)
    return PdfWrapper(str(src)).draw([RawElements.RawImage(
        image=image_png, page_number=int(page), x=x, y=y, width=w, height=h)]).read()


def render_page_png(src: Path, page: int, box=None, width_px: int = 700) -> bytes:
    """A PNG of 1-based `page`, with `box` (x, y, w, h in points) outlined."""
    import pypdfium2 as pdfium
    from PIL import ImageDraw
    doc = pdfium.PdfDocument(str(src))
    try:
        pg = doc[page - 1]
        try:
            pw, ph = pg.get_size()
            scale = max(0.2, min(4.0, width_px / float(pw or 612)))
            img = pg.render(scale=scale).to_pil().convert("RGB")
        finally:
            pg.close()
    finally:
        doc.close()
    if box:
        x, y, w, h = (float(v) for v in box)
        left, top = x * scale, (ph - y - h) * scale
        right, bottom = (x + w) * scale, (ph - y) * scale
        d = ImageDraw.Draw(img)
        for i in range(3):
            d.rectangle((left - i, top - i, right + i, bottom + i), outline=(220, 20, 20))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
