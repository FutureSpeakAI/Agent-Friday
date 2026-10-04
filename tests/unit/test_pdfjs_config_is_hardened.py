"""The Reader's pdf.js is vendored, pinned, and configured so a PDF cannot make it
evaluate code, follow a link, or fetch anything from outside Friday's own files."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VENDOR = ROOT / "static" / "vendor" / "pdfjs-6.4.299"
READER = (ROOT / "static" / "library_reader.js").read_text(encoding="utf-8")


def _manifest() -> dict:
    return json.loads((VENDOR / "VERSION.json").read_text(encoding="utf-8"))


def test_the_vendored_files_match_the_pinned_hashes():
    m = _manifest()
    assert m["package"] == "pdfjs-dist" and m["license"] == "Apache-2.0" and m["integrity"].startswith("sha512-")
    assert m["files"], "an empty manifest pins nothing"
    for rel, want in m["files"].items():
        got = hashlib.sha256((VENDOR / rel).read_bytes()).hexdigest()
        assert got == want, rel
    present = {p.relative_to(VENDOR).as_posix() for p in VENDOR.rglob("*") if p.is_file() and p.name != "VERSION.json"}
    assert present == set(m["files"]), "a vendored file that is not in the manifest is unpinned"
    assert (VENDOR / "LICENSE").is_file()


def test_the_version_is_past_the_font_evaluation_fixes():
    v = tuple(int(x) for x in _manifest()["version"].split("."))
    assert v >= (4, 2, 67)
    assert _manifest()["version"] in READER and VENDOR.name.endswith(_manifest()["version"])


def test_the_reader_turns_off_eval_forms_scripts_and_system_fonts():
    assert "isEvalSupported: false" in READER
    assert "enableXfa: false" in READER
    assert "useSystemFonts: false" in READER
    assert not re.search(r"enableScripting\s*:\s*true|isEvalSupported\s*:\s*true|enableXfa\s*:\s*true", READER)


def test_every_pdfjs_resource_comes_from_the_vendored_copy():
    for key in ("cMapUrl", "standardFontDataUrl", "wasmUrl", "iccUrl", "workerSrc"):
        line = next((ln for ln in READER.splitlines() if key in ln), "")
        assert "PDFJS_BASE" in line, key
    assert "PDFJS_BASE = '/static/vendor/pdfjs-" in READER
    assert not re.search(r"https?://", READER), "the Reader names no outside address"
    assert "cdn" not in READER.lower()


def test_no_annotation_layer_is_built_so_no_document_link_is_ever_followed():
    for banned in ("AnnotationLayer", "annotationMode", "LinkService", "externalLinkTarget", "linkService"):
        assert banned not in READER, banned


def test_the_original_is_fetched_only_through_the_sandboxed_raw_route():
    assert "'/api/library/raw/' + doc" in READER


def test_the_page_image_stays_as_the_fallback():
    assert "usePage(" in READER and "setV2Failed(true)" in READER
