"""Credentials are a property of saving (media-workspace.md §4.11, P7).

Every tool that writes a file Friday made signs it right after the write, so
the Media Library never shows a thing Friday made as Unsigned unless a tool
genuinely could not. Each save site is exercised with its real writer and the
tool behind it stubbed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import agent_friday.core as core
from agent_friday.services import provenance


def _signed(path) -> bool:
    m = provenance.manifest_for_file(str(path))
    return bool(m) and (m.get("artifact") or {}).get("content_hash") == provenance.hash_file(str(path))


@pytest.fixture
def home(tmp_path, monkeypatch):
    fd = tmp_path / ".friday"
    fd.mkdir()
    creations = tmp_path / "friday-creations"
    creations.mkdir()
    monkeypatch.setattr(core, "FRIDAY_DIR", fd)
    monkeypatch.setattr(core, "CREATIONS_DIR", creations)
    monkeypatch.setattr(provenance, "PROVENANCE_DIR", fd / "provenance", raising=False)
    return tmp_path


def test_sign_saved_never_raises_and_signs_a_real_file(home):
    p = home / "friday-creations" / "note.md"
    p.write_text("The 06:40 left on time.", encoding="utf-8")
    provenance.sign_saved(p, "test_tool", "text", sources=[{"kind": "prompt", "text": "a note"}], prompt_len=5)
    assert _signed(p)
    provenance.sign_saved(home / "does-not-exist.md", "test_tool", "text")   # quietly nothing


def test_a_showcase_page_is_signed_when_saved(home, monkeypatch):
    from agent_friday.services import showcase_engine as se
    monkeypatch.setattr(se, "CREATIONS_DIR", home / "friday-creations")
    monkeypatch.setattr("agent_friday.services.creations._notify_creation", lambda *a, **k: None, raising=False)
    se._save_creation("friday-site-test.html", "<html><body>A page</body></html>", "studio")
    assert _signed(home / "friday-creations" / "friday-site-test.html")


def test_an_office_write_verb_re_signs_the_document(home, monkeypatch):
    from agent_friday.services import office_engine as oe
    docs = home / ".friday" / "documents"
    docs.mkdir(parents=True)
    monkeypatch.setattr(oe, "DOCUMENTS_DIR", docs)
    monkeypatch.setattr(oe, "_record_made", lambda paths: None)

    def fake_run(argv, *, timeout=0):
        target = Path(argv[1])
        if argv[0] == "create":
            target.write_bytes(b"PK deck v1")
        elif argv[0] == "add":
            target.write_bytes(target.read_bytes() + b" v2")
        return 0, "", ""      # a read verb such as view leaves the file alone

    monkeypatch.setattr(oe, "run", fake_run)
    r = oe.run_command(["create", "deck.pptx"])
    assert r["ok"], r
    deck = docs / "deck.pptx"
    assert _signed(deck), "created: signed"
    r = oe.run_command(["add", "deck.pptx", "/", "--type", "slide"])
    assert r["ok"] and _signed(deck), "changed: the credential follows the new bytes"
    r = oe.run_command(["view", "deck.pptx"])
    assert r["ok"] and _signed(deck), "a read verb leaves the credential alone"


def test_a_filled_pdf_form_is_signed(home, monkeypatch):
    from agent_friday.services import pdf_forms as pf
    src = home / "permit.pdf"
    src.write_bytes(b"%PDF-1.4 fake form")

    class FakeWrapper:
        def __init__(self, path):
            self.path = path

        def fill(self, values):
            return self

        def read(self):
            return b"%PDF-1.4 filled"

    import sys, types
    fake = types.ModuleType("PyPDFForm")
    fake.PdfWrapper = FakeWrapper
    monkeypatch.setitem(sys.modules, "PyPDFForm", fake)
    monkeypatch.setattr(pf, "list_fields", lambda p: {"file": str(p), "pages": 1, "fields": [{"name": "name", "type": "text", "value": "", "options": [], "required": False, "read_only": False, "label": "Name", "page": 1, "sensitive": False}]})
    out = pf.fill_form(str(src), {"name": "A"}, owner_text="A")
    assert _signed(Path(out["output"]))
