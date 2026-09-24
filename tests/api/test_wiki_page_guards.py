"""Reading and writing wiki pages through the HTTP routes:

  * any page is readable by its path, nested or at the wiki root, and
    nothing outside the wiki is readable through either page route;
  * an encrypted page cannot be overwritten while the vault is locked (what
    an editor showed was the placeholder, not the page);
  * creating a page never replaces one that is already there.
"""
import os

import pytest


@pytest.fixture
def wiki_dir(server_module):
    return server_module.WIKI_DIR


# ── reading a page by its path ───────────────────────────────────────────

def test_nested_and_root_pages_are_readable_by_path(client, wiki_dir):
    nested = wiki_dir / "kwtest" / "deep" / "page.md"
    root = wiki_dir / "kwtest-root.md"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_bytes(b"# Nested\n")
    root.write_bytes(b"# Root\n")
    try:
        d = client.get("/api/wiki/page?path=kwtest/deep/page.md").get_json()
        assert (d["status"], d["content"], d["section"], d["filename"], d["locked"]) == \
            ("ok", "# Nested\n", "kwtest/deep", "page.md", False)
        d = client.get("/api/wiki/page?path=kwtest-root.md").get_json()
        assert (d["status"], d["content"], d["section"], d["filename"]) == \
            ("ok", "# Root\n", "", "kwtest-root.md")
    finally:
        nested.unlink(missing_ok=True)
        root.unlink(missing_ok=True)


def test_a_missing_page_is_404(client):
    assert client.get("/api/wiki/page?path=kwtest/no-such-page.md").status_code == 404


def test_nothing_beside_the_wiki_is_readable(client, wiki_dir):
    """`..` must not climb out of the wiki through either page route: the
    folder around it holds the soul file and settings exports."""
    beside = wiki_dir.parent / "kw-beside-the-wiki.md"
    beside.write_bytes(b"BESIDE-THE-WIKI-MARKER\n")
    try:
        for url in ("/api/wiki/%2e%2e/kw-beside-the-wiki.md",
                    "/api/wiki/../kw-beside-the-wiki.md",
                    "/api/wiki/page?path=../kw-beside-the-wiki.md",
                    "/api/wiki/page?path=kwtest/../../kw-beside-the-wiki.md"):
            r = client.get(url)
            assert r.status_code in (400, 404), (url, r.status_code)
            assert "BESIDE-THE-WIKI-MARKER" not in r.get_data(as_text=True), url
    finally:
        beside.unlink(missing_ok=True)


# ── the locked vault ─────────────────────────────────────────────────────

def test_an_encrypted_page_cannot_be_overwritten_while_the_vault_is_locked(
        client, wiki_dir, monkeypatch):
    import agent_friday.privacy.vault_crypto as vc
    import agent_friday.services.agent as agent
    monkeypatch.setattr(agent, "_get_vault_key", lambda: None)     # locked
    page = wiki_dir / "kwtest" / "sealed.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    blob = vc.encrypt(b"# The real page\n", os.urandom(32))
    page.write_bytes(blob)
    try:
        # What an editor showed for this page is the placeholder; saving it
        # back (edited or not) would replace the page.
        r = client.put("/api/wiki/edit", json={
            "file": "kwtest/sealed.md",
            "content": "[vault-encrypted file — set FRIDAY_PASSWORD to read it]\nedited"})
        assert r.status_code == 409 and r.get_json()["locked"] is True
        assert page.read_bytes() == blob, "the ciphertext was overwritten"
        # The reader says why, so the page is shown as locked, not as text.
        d = client.get("/api/wiki/page?path=kwtest/sealed.md").get_json()
        assert d["locked"] is True and d["content"].startswith("[vault-encrypted file")
    finally:
        page.unlink(missing_ok=True)


# ── creating a page ──────────────────────────────────────────────────────

def test_creating_a_page_never_replaces_one(client, wiki_dir):
    page = wiki_dir / "kwtest" / "taken.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_bytes(b"# Taken\n\nwritten earlier\n")
    try:
        r = client.put("/api/wiki/edit", json={"file": "kwtest/taken.md",
                                               "content": "# Taken\n\n", "create": True})
        assert r.status_code == 409 and r.get_json()["exists"] is True
        assert page.read_bytes() == b"# Taken\n\nwritten earlier\n"
        r = client.put("/api/wiki/edit", json={"file": "kwtest/fresh.md",
                                               "content": "# Fresh\n\n", "create": True})
        assert r.status_code == 200
        assert (wiki_dir / "kwtest" / "fresh.md").read_bytes() == b"# Fresh\n\n"
    finally:
        page.unlink(missing_ok=True)
        (wiki_dir / "kwtest" / "fresh.md").unlink(missing_ok=True)
