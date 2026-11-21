"""Opt-in wiki encryption at rest.

`wiki_encrypted_sections` in settings encrypts those wiki sections with the
vault key (AES-256-GCM). OFF by default — the wiki stays hand-editable. All
wiki reads route through wiki_read_text, which transparently handles both
states, so search / context / the UI keep working over ciphertext files.
"""
from __future__ import annotations

import pytest

import agent_friday.privacy.vault_crypto as vc
import agent_friday.services.wiki_engine as we
import agent_friday.services.agent as agent_mod
from agent_friday.core import WIKI_DIR


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


def test_no_key_refuses_the_write_instead_of_storing_plain_text(health_encrypted, monkeypatch):
    # threat assessment fix #2: a section the owner locked is never written as plain text because a key is missing
    monkeypatch.setattr(agent_mod, "_get_vault_key", lambda: None)
    p = WIKI_DIR / "health" / "nokey.md"
    with pytest.raises(agent_mod.VaultKeyMissing):
        we.wiki_write_text(p, "written without a key")
    assert not p.exists()


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


def test_the_page_editor_save_says_it_did_not_save_when_the_locked_section_has_no_key(health_encrypted, client, monkeypatch):
    # follow-up from the #1051 critic: the refusal reached the editor as a generic 500; it is the owner's plain line, 409
    monkeypatch.setattr(agent_mod, "_get_vault_key", lambda: None)
    r = client.put("/api/wiki/edit", json={"file": "health/nokey-editor.md", "content": "# a private note\n"})
    assert r.status_code == 409, r.get_data(as_text=True)[:300]
    body = r.get_json()
    assert body["status"] == "error" and "plain text" in body["message"] and "Not saved" in body["message"]
    assert not (WIKI_DIR / "health" / "nokey-editor.md").exists()


def test_a_correction_names_the_locked_page_it_could_not_change(health_encrypted, client, monkeypatch):
    # follow-up from the #1051 critic: a correction skipped a page of a locked section (no key) and still answered a bare
    # "ok", so the owner was not told one page kept the old claim
    old = "QZX-old-claim-7731"
    locked = WIKI_DIR / "health" / "locked-skip.md"
    other = WIKI_DIR / "family" / "plain-ok.md"
    locked.write_text("the " + old + " is here", encoding="utf-8")   # a plain page not yet migrated
    other.write_text("also " + old, encoding="utf-8")
    monkeypatch.setattr(agent_mod, "_get_vault_key", lambda: None)
    r = client.post("/api/wiki/correct", json={"old_text": old, "new_text": "QZX-new-claim"})
    body = r.get_json()
    assert r.status_code == 200 and body["status"] == "ok"
    assert [m["path"] for m in body["modified"]] == ["family/plain-ok.md"]
    assert body["skipped"] == [{"scope": "wiki", "path": "health/locked-skip.md", "reason": "locked"}]
    assert "1 page" in body["message"] and "not changed" in body["message"]
    assert locked.read_text(encoding="utf-8") == "the " + old + " is here", "the locked page was left as it was"
