"""File text extraction — WO-14.1.

read_file used to decode every file as UTF-8 with errors='replace' and hand
the result straight to the model. For a PDF or .docx that is not text at all
— it is a compressed/binary container — so the "text" was mojibake, and the
model narrated a confident summary over it (voice session 2026-08-25, item
#20: "four pages, senior AI leadership" over 8,000 chars of raw PDF bytes).

extract_text() is the single place that decides whether a file's bytes can
become real text, and never falls back to raw-bytes-as-text for a format it
recognizes as binary. On failure it returns an honest, specific reason
instead of the file's raw contents.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

# Bounded, same spirit as the read_file 500_000-char cap: a resume or a
# report needs a handful of pages, not an entire book scanned page-by-page.
_MAX_PDF_PAGES = 50

# >10% of decoded characters being the UTF-8 replacement char means the file
# is not text — read_text() succeeded but the content is noise, not prose.
_BINARY_RATIO_THRESHOLD = 0.10


@dataclass
class ExtractionResult:
    text: str | None
    error: str | None
    truncated: bool = False


def _extract_pdf(data: bytes) -> ExtractionResult:
    try:
        import pdfplumber
    except ImportError:
        return ExtractionResult(
            None,
            "This is a PDF and the text-extraction library (pdfplumber) is "
            "not installed, so I could not read it. I did not return its raw "
            "bytes as a substitute."
        )
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = pdf.pages[:_MAX_PDF_PAGES]
            text = "\n\n".join((page.extract_text() or "") for page in pages)
            truncated = len(pdf.pages) > _MAX_PDF_PAGES
    except Exception as e:
        return ExtractionResult(None, f"This is a PDF but I could not open it to extract text: {e}")
    if not text.strip():
        return ExtractionResult(
            None,
            "This is a PDF with no extractable text layer — most likely a "
            "scanned image with no OCR text underneath. I could not read its "
            "contents; I did not guess at them."
        )
    return ExtractionResult(text, None, truncated=truncated)


_DOCX_TAG_RE = re.compile(r"<[^>]+>")
_DOCX_PARA_END_RE = re.compile(r"</w:p>")


def _extract_docx(data: bytes) -> ExtractionResult:
    """Plain zipfile + XML read — no new dependency. A .docx is a zip archive
    containing word/document.xml; stripping tags and treating </w:p> as a
    paragraph break is enough to get real, readable prose out of it."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    except Exception as e:
        return ExtractionResult(None, f"This is a .docx file but I could not open it to extract text: {e}")
    xml = _DOCX_PARA_END_RE.sub("\n\n", xml)
    text = _DOCX_TAG_RE.sub("", xml).strip()
    # Collapse the XML entity noise word processors leave behind.
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    if not text.strip():
        return ExtractionResult(None, "This is a .docx file with no extractable text.")
    return ExtractionResult(text, None)


def _binary_ratio(text: str) -> float:
    if not text:
        return 0.0
    return text.count("�") / len(text)


def extract_text(path: Path) -> ExtractionResult:
    """Extract readable text from `path`.

    Never returns raw bytes decoded with errors='replace' for a format known
    to be binary (PDF, docx) — either real extracted text, or an honest error
    naming what was tried and why it failed. Plain text files are still
    decoded directly, but checked for binary noise first.
    """
    ext = path.suffix.lower().lstrip(".")
    if ext == "pdf":
        try:
            data = path.read_bytes()
        except Exception as e:
            return ExtractionResult(None, f"Read error: {e}")
        return _extract_pdf(data)
    if ext == "docx":
        try:
            data = path.read_bytes()
        except Exception as e:
            return ExtractionResult(None, f"Read error: {e}")
        return _extract_docx(data)

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return ExtractionResult(None, f"Read error: {e}")
    ratio = _binary_ratio(text)
    if ratio > _BINARY_RATIO_THRESHOLD:
        pct = round(ratio * 100)
        return ExtractionResult(
            None,
            f"This looks like a binary file, not text ({pct}% of the decoded "
            f"content is not valid UTF-8). I did not return the raw bytes."
        )
    return ExtractionResult(text, None)
