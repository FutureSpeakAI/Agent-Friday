"""WO-14.1 — read_file must extract real text from PDF/docx, never mojibake.

THE REPORTED FAILURE (voice session 2026-08-25, item #20): read_file had no
PDF extraction. `p.read_text(encoding='utf-8', errors='replace')` on a PDF's
raw bytes produced mojibake, and the model narrated a confident summary
("four pages, senior AI leadership") over garbage it never actually read.

Every test below is written to FAIL against the old behaviour
(`path.read_text(encoding='utf-8', errors='replace')`) — proven by
test_old_behaviour_would_have_produced_mojibake, which runs that exact old
code against the same fixture and asserts it is NOT usable text.
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from agent_friday.services.file_extraction import extract_text


# ── Fixture builders (no new dependency: pdfplumber is already required,
#    and we deliberately do NOT depend on a PDF-writing library — a minimal
#    hand-built single-page PDF with a byte-accurate xref table is enough) ──

def _make_minimal_pdf(text: str) -> bytes:
    import zlib
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 612 792] /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    # Flate-compressed content stream, same as a real-world PDF producer —
    # the whole point of the "old behaviour" comparison test is that a PDF's
    # bytes are genuinely binary, not human-readable ASCII.
    raw_stream = ("BT /F1 24 Tf 72 700 Td (%s) Tj ET" % text).encode("latin-1")
    stream_content = zlib.compress(raw_stream)
    objects.append(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(stream_content)
                    + stream_content + b"\nendstream")

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += ("%d 0 obj\n" % i).encode() + obj + b"\nendobj\n"
    xref_offset = len(out)
    out += ("xref\n0 %d\n" % (len(objects) + 1)).encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += ("%010d 00000 n \n" % off).encode()
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n"
            % (len(objects) + 1))
    out += ("%d\n" % xref_offset).encode()
    out += b"%%EOF"
    return bytes(out)


def _make_minimal_docx(paragraphs: list[str]) -> bytes:
    body = "".join(
        f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types/>')
        z.writestr("word/document.xml", document_xml)
    return buf.getvalue()


# ── PDF extraction ──────────────────────────────────────────────────────────

class TestPdfExtraction:
    def test_extracts_real_text(self, tmp_path):
        p = tmp_path / "cv.pdf"
        p.write_bytes(_make_minimal_pdf("Senior AI Leadership Resume"))

        result = extract_text(p)

        assert result.error is None
        assert "Senior AI Leadership Resume" in result.text

    def test_old_behaviour_would_have_produced_mojibake(self, tmp_path):
        """Proves the fix addresses a real defect: the OLD code path (plain
        errors='replace' UTF-8 decode of PDF bytes) does NOT recover the
        embedded text, and produces the replacement character instead."""
        p = tmp_path / "cv.pdf"
        raw = _make_minimal_pdf("Senior AI Leadership Resume")
        p.write_bytes(raw)

        old_behaviour = p.read_text(encoding="utf-8", errors="replace")

        assert "Senior AI Leadership Resume" not in old_behaviour
        assert "�" in old_behaviour or "%PDF" in old_behaviour[:10]

    def test_scanned_pdf_with_no_text_layer_is_honest(self, tmp_path, monkeypatch):
        """A PDF that opens but has no text (a scan) must say so, not return
        empty content silently or fabricate a page count."""
        import agent_friday.services.file_extraction as fe

        class _FakePage:
            def extract_text(self):
                return None

        class _FakePdf:
            pages = [_FakePage()]
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        class _FakePdfplumber:
            @staticmethod
            def open(_buf):
                return _FakePdf()

        monkeypatch.setitem(sys.modules, "pdfplumber", _FakePdfplumber)
        p = tmp_path / "scan.pdf"
        p.write_bytes(b"%PDF-1.4\n%%EOF")

        result = extract_text(p)

        assert result.text is None
        assert "scan" in result.error.lower() or "no extractable text" in result.error.lower()

    def test_missing_pdfplumber_gives_honest_error_not_raw_bytes(self, tmp_path, monkeypatch):
        import agent_friday.services.file_extraction as fe
        monkeypatch.setitem(sys.modules, "pdfplumber", None)  # import raises

        p = tmp_path / "cv.pdf"
        p.write_bytes(_make_minimal_pdf("should not leak"))

        result = extract_text(p)

        assert result.text is None
        assert "should not leak" not in (result.error or "")
        assert "pdfplumber" in result.error.lower() or "not installed" in result.error.lower()


# ── DOCX extraction ──────────────────────────────────────────────────────────

class TestDocxExtraction:
    def test_extracts_real_text_no_dependency_needed(self, tmp_path):
        p = tmp_path / "cover_letter.docx"
        p.write_bytes(_make_minimal_docx(["Dear hiring manager,", "Sanofi pivot notes."]))

        result = extract_text(p)

        assert result.error is None
        assert "Dear hiring manager" in result.text
        assert "Sanofi pivot notes" in result.text

    def test_old_behaviour_would_have_produced_mojibake(self, tmp_path):
        p = tmp_path / "cover_letter.docx"
        raw = _make_minimal_docx(["Dear hiring manager,"])
        p.write_bytes(raw)

        old_behaviour = p.read_text(encoding="utf-8", errors="replace")

        assert "Dear hiring manager" not in old_behaviour

    def test_corrupt_docx_is_honest_not_a_crash(self, tmp_path):
        p = tmp_path / "broken.docx"
        p.write_bytes(b"not a zip file at all")

        result = extract_text(p)

        assert result.text is None
        assert result.error


# ── Binary detection for everything else ──────────────────────────────────────

class TestBinaryDetection:
    def test_plain_text_passes_through(self, tmp_path):
        p = tmp_path / "notes.txt"
        p.write_text("Ordinary prose, nothing binary here.", encoding="utf-8")

        result = extract_text(p)

        assert result.error is None
        assert result.text == "Ordinary prose, nothing binary here."

    def test_binary_file_is_reported_not_returned_as_mojibake(self, tmp_path):
        p = tmp_path / "image.dat"
        # Genuinely non-UTF-8 binary noise.
        p.write_bytes(bytes(range(256)) * 10)

        result = extract_text(p)

        assert result.text is None
        assert "binary" in result.error.lower()

    def test_old_behaviour_would_have_returned_the_raw_replace_string(self, tmp_path):
        p = tmp_path / "image.dat"
        p.write_bytes(bytes(range(256)) * 10)

        old_behaviour = p.read_text(encoding="utf-8", errors="replace")

        assert "�" in old_behaviour   # exactly the mojibake this WO fixes


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
