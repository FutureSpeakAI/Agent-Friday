"""A PDF served from the file browser carries no viewer, script or network
reference into the page: it is sandboxed like every other served document, and
the preview shows server-rendered page images instead of the browser's viewer."""
from __future__ import annotations

import pytest

from agent_friday.services import studio_files as sf
from tests.library_fixtures import make_pdf


@pytest.fixture
def pdf_root(tmp_path, monkeypatch):
    root = tmp_path / "Documents"
    root.mkdir()
    monkeypatch.setattr(sf, "roots", lambda: {"documents": root})
    (root / "memo.pdf").write_bytes(make_pdf([["# Memo", "First page text."], ["Second page text."]],
                                             javascript=True))
    (root / "notes.txt").write_text("hello", encoding="utf-8")
    return root


def test_pdf_raw_is_sandboxed_with_no_sources(client, pdf_root):
    r = client.get("/api/studio-files/raw?root=documents&path=memo.pdf")
    assert r.status_code == 200
    csp = r.headers.get("Content-Security-Policy", "")
    assert csp.startswith("sandbox")
    assert "default-src 'none'" in csp
    assert "allow-" not in csp


def test_pdf_page_is_an_image_and_names_the_page_count(client, pdf_root):
    r = client.get("/api/studio-files/pdfpage?root=documents&path=memo.pdf&n=2&w=400")
    assert r.status_code == 200
    assert r.mimetype in ("image/webp", "image/png")
    assert r.headers["X-Pdf-Pages"] == "2"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    info = client.get("/api/studio-files/pdfinfo?root=documents&path=memo.pdf").get_json()
    assert info["pages"] == 2


def test_pdf_page_refuses_other_files_and_missing_pages(client, pdf_root):
    assert client.get("/api/studio-files/pdfpage?root=documents&path=notes.txt&n=1").status_code == 403
    assert client.get("/api/studio-files/pdfpage?root=documents&path=memo.pdf&n=9").status_code == 422


def test_files3d_previews_pdfs_as_images_not_in_an_iframe():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "static" / "studio_files3d.js").read_text(encoding="utf-8")
    assert "h('iframe'" not in src
    assert "/api/studio-files/pdfpage" in src
