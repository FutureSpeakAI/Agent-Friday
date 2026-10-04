"""Every cap holds and every failure is named, never a silent skip."""
from __future__ import annotations

import zipfile

import pytest

from agent_friday.services.library import caps, extract
from tests.library_fixtures import make_docx


def test_docx_with_an_entity_declaration_is_refused(tmp_path):
    p = tmp_path / "xxe.docx"
    p.write_bytes(make_docx([("Normal", "hello")],
                            doctype='<!DOCTYPE d [<!ENTITY x SYSTEM "file:///c:/windows/win.ini">]>'))
    with pytest.raises(caps.CapExceeded, match="unsafe XML"):
        extract.extract_document(p)


def test_docx_zip_bomb_is_refused_before_it_is_opened(tmp_path, monkeypatch):
    p = tmp_path / "bomb.docx"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", "<a/>")
        z.writestr("word/filler.bin", b"\0" * (4 * 1024 * 1024))
    monkeypatch.setattr(caps, "MAX_ZIP_UNCOMPRESSED", 1024 * 1024)
    with pytest.raises(caps.CapExceeded, match="zip bomb"):
        extract.extract_document(p)


def test_a_docx_with_too_many_members_is_refused(tmp_path, monkeypatch):
    p = tmp_path / "many.docx"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("word/document.xml", "<a/>")
        for i in range(30):
            z.writestr(f"m{i}.txt", "x")
    monkeypatch.setattr(caps, "MAX_ZIP_MEMBERS", 10)
    with pytest.raises(caps.CapExceeded, match="zip bomb"):
        extract.extract_document(p)


def test_a_file_over_the_size_cap_is_too_large(tmp_path, monkeypatch):
    p = tmp_path / "big.txt"
    p.write_text("word " * 100, encoding="utf-8")
    monkeypatch.setattr(caps, "MAX_FILE_BYTES", 100)
    with pytest.raises(caps.CapExceeded, match="too large"):
        extract.extract_document(p)


def test_text_over_the_text_cap_is_refused(tmp_path, monkeypatch):
    p = tmp_path / "long.txt"
    p.write_text("para\n\n" * 2000, encoding="utf-8")
    monkeypatch.setattr(caps, "MAX_TEXT_BYTES", 2000)
    with pytest.raises(caps.CapExceeded, match="too much text"):
        extract.extract_document(p)


def test_too_many_blocks_are_refused(tmp_path, monkeypatch):
    p = tmp_path / "many.txt"
    p.write_text("p\n\n" * 50, encoding="utf-8")
    monkeypatch.setattr(caps, "MAX_BLOCKS", 10)
    with pytest.raises(caps.CapExceeded, match="too many paragraphs"):
        extract.extract_document(p)


def test_an_unsupported_kind_is_named_not_swallowed(tmp_path):
    p = tmp_path / "a.exe"
    p.write_bytes(b"MZ")
    with pytest.raises(extract.Unsupported, match="isn't read yet"):
        extract.extract_document(p)


def test_html_is_text_only_scripts_and_resources_dropped(tmp_path):
    p = tmp_path / "page.html"
    p.write_text('<html><head><title>T</title><script>steal()</script></head><body><h1>Head</h1>'
                 '<p>Visible <img src="http://attacker.example/x.png"> text</p>'
                 '<iframe src="http://attacker.example"></iframe><style>a{background:url(http://x)}</style>'
                 '</body></html>', encoding="utf-8")
    res = extract.extract_document(p)
    blob = " ".join(b["text"] for b in res["blocks"])
    assert "Visible" in blob and "steal" not in blob and "attacker" not in blob and "url(" not in blob


def test_control_and_bidi_characters_are_stripped_from_text(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("safe\u202etxt.exe hidden\x07 words\u200b here", encoding="utf-8")
    t = extract.extract_document(p)["blocks"][0]["text"]
    assert "\u202e" not in t and "\x07" not in t and "\u200b" not in t
