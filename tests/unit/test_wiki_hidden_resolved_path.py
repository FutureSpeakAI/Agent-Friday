"""A wiki path that RESOLVES to a hidden one is refused like a dotted one.

On Windows an 8.3 short name ("TRASH~1" for ".trash", "GIT~1" for ".git",
"DRAFT~1.MD" for ".draft.md") has no dot of its own, so a check on the
requested segments alone lets it through, and the write lands in the hidden
folder. The check runs on the resolved path as well. A Linux sandbox has no
short names; a link inside the wiki resolves the same way and stands in for one.
"""
from __future__ import annotations

import os

import pytest

from agent_friday.governance import action_gate as ag
from agent_friday.services import wiki_engine as we

pytestmark = pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")


@pytest.fixture(autouse=True)
def wiki(tmp_path, monkeypatch):
    root = tmp_path / "wiki"
    (root / "notes").mkdir(parents=True)
    (root / ".trash").mkdir()
    (root / ".git").mkdir()
    (root / ".git" / "config.md").write_text("[core]\n", encoding="utf-8")
    (root / "notes" / ".draft.md").write_text("# Draft\n", encoding="utf-8")
    try:
        os.symlink(root / ".trash", root / "TRASH~1", target_is_directory=True)
        os.symlink(root / ".git", root / "GIT~1", target_is_directory=True)
        os.symlink(root / "notes" / ".draft.md", root / "notes" / "DRAFT~1.md")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    monkeypatch.setattr(we, "WIKI_DIR", root)
    monkeypatch.setattr(we, "WIKI_PENDING_FILE", tmp_path / "wiki-pending.json")
    monkeypatch.setattr(we, "_wiki_mirror_dir", lambda: None)
    monkeypatch.setattr(we, "_wiki_encrypted_sections", lambda: set())
    yield root


@pytest.fixture
def client():
    from flask import Flask
    from agent_friday.routes import wiki as rw
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(rw.wiki_bp)
    with app.test_client() as c:
        yield c


@pytest.mark.parametrize("rel", ["TRASH~1/old-page.md", "GIT~1/config.md", "GIT~1/config",
                                 "notes/DRAFT~1.md", "notes/DRAFT~1"])
def test_a_name_that_resolves_to_a_hidden_path_is_refused(wiki, client, rel):
    assert we._safe_wiki_path(rel) is None
    r = client.post("/api/wiki/update", json={"file": rel, "new_value": "x", "reason": "r"})
    assert r.status_code == 400, r.get_json()
    r = client.put("/api/wiki/edit", json={"file": rel, "content": "x"})
    assert r.status_code == 400, r.get_json()
    tok = ag.DECIDED.set("card-h1")
    try:
        from agent_friday.services import agent
        out = agent._tool_propose_wiki_update({"file": rel, "new_value": "x"})
    finally:
        ag.DECIDED.reset(tok)
    assert "invalid wiki path" in out, out
    assert not (wiki / ".trash" / "old-page.md").exists()
    assert (wiki / ".git" / "config.md").read_text(encoding="utf-8") == "[core]\n"
    assert (wiki / "notes" / ".draft.md").read_text(encoding="utf-8") == "# Draft\n"


def test_an_ordinary_page_still_resolves(wiki):
    p = we._safe_wiki_path("notes/plain-page")
    assert p is not None and p.name == "plain-page.md"
