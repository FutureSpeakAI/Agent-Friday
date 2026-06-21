"""Opt-in wiki encryption at rest.

`wiki_encrypted_sections` in settings encrypts those wiki sections with the
vault key (AES-256-GCM). OFF by default — the wiki stays hand-editable. All
wiki reads route through wiki_read_text, which transparently handles both
states, so search / context / the UI keep working over ciphertext files.
"""
from __future__ import annotations

import pytest

import vault_crypto as vc
import services.wiki_engine as we
import services.agent as agent_mod
from core import WIKI_DIR


@pytest.fixture
def health_encrypted(monkeypatch):
    monkeypatch.setattr(we, "_wiki_encrypted_sections", lambda: {"health"})
    (WIKI_DIR / "health").mkdir(parents=True, exist_ok=True)
    (WIKI_DIR / "family").mkdir(parents=True, exist_ok=True)
    yield
    for sub in ("health", "family"):
        d = WIKI_DIR / sub
        for f in d.glob("*.md"):
            f.unlink(missing_ok=True)


def test_opted_in_section_encrypts_on_disk(health_encrypted):
    p = WIKI_DIR / "health" / "meds.md"
    we.wiki_write_text(p, "# Meds\n\nLisinopril 10mg daily.\n")
    raw = p.read_bytes()
    assert vc.is_encrypted(raw), "opted-in section must be ciphertext on disk"
    assert b"Lisinopril" not in raw
    assert "Lisinopril 10mg" in we.wiki_read_text(p)


def test_unlisted_section_stays_plaintext(health_encrypted):
    p = WIKI_DIR / "family" / "birthdays.md"
    we.wiki_write_text(p, "# Birthdays\n\nSam: June 12\n")
    raw = p.read_bytes()
    assert not vc.is_encrypted(raw)
    assert b"June 12" in raw
    assert "June 12" in we.wiki_read_text(p)


def test_read_tolerates_both_states(health_encrypted):
    enc = WIKI_DIR / "health" / "enc.md"
    plain = WIKI_DIR / "health" / "plain.md"
    we.wiki_write_text(enc, "secret condition")
    plain.write_text("legacy plaintext note", encoding="utf-8")  # pre-migration state
    assert we.wiki_read_text(enc) == "secret condition"
    assert we.wiki_read_text(plain) == "legacy plaintext note"


def test_no_key_degrades_to_plaintext_write(health_encrypted, monkeypatch):
    monkeypatch.setattr(agent_mod, "_get_vault_key", lambda: None)
    p = WIKI_DIR / "health" / "nokey.md"
    we.wiki_write_text(p, "written without a key")
    assert not vc.is_encrypted(p.read_bytes())
    assert we.wiki_read_text(p) == "written without a key"


def test_startup_migration_encrypts_existing_files(health_encrypted):
    p = WIKI_DIR / "health" / "legacy.md"
    p.write_text("legacy plaintext medical history", encoding="utf-8")
    assert not vc.is_encrypted(p.read_bytes())

    agent_mod._migrate_vault_plaintext()

    assert vc.is_encrypted(p.read_bytes()), "migration must encrypt opted-in wiki files"
    assert we.wiki_read_text(p) == "legacy plaintext medical history"


def test_wiki_read_route_serves_plaintext_over_ciphertext(health_encrypted, client):
    p = WIKI_DIR / "health" / "viaroute.md"
    we.wiki_write_text(p, "# Via Route\n\nciphertext on disk, plaintext over API\n")
    assert vc.is_encrypted(p.read_bytes())

    resp = client.get("/api/wiki/health/viaroute.md")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "plaintext over API" in body.get("content", "")


def test_wiki_search_finds_text_in_encrypted_files(health_encrypted, client):
    p = WIKI_DIR / "health" / "searchable.md"
    we.wiki_write_text(p, "The xylophone protocol is working.")
    resp = client.post("/api/wiki/search", json={"query": "xylophone"})
    assert resp.status_code == 200
    hits = resp.get_json().get("results", [])
    assert any("searchable" in (h.get("file") or h.get("path") or "")
               for h in hits), f"encrypted file missing from search: {hits}"
