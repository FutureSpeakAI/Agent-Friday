"""``POST /api/wiki/update`` applied directly holds the Knowledge approve rule.

No page of the owner's UI calls it (the editor saves through
``PUT /api/wiki/edit``), so a direct call is a script or tool: an append or
overwrite of a page that exists applies only with the ``before_sha256`` of
the page the caller showed. Without it nothing is written and the answer is
409 ``stale`` with the current hash. Edits and new pages are unchanged, and
the owner's editor save still writes.
"""
from __future__ import annotations

import hashlib

import pytest

from agent_friday.services import wiki_engine as we

PAGE = "notes/sample-page.md"
BODY = "# Sample Page\n\n## Preferences\n- Colour: blue\n"


@pytest.fixture(autouse=True)
def wiki(tmp_path, monkeypatch):
    root = tmp_path / "wiki"
    (root / "notes").mkdir(parents=True)
    monkeypatch.setattr(we, "WIKI_DIR", root)
    monkeypatch.setattr(we, "WIKI_PENDING_FILE", tmp_path / "wiki-pending.json")
    monkeypatch.setattr(we, "_wiki_mirror_dir", lambda: None)
    monkeypatch.setattr(we, "_wiki_encrypted_sections", lambda: set())
    (root / PAGE).write_text(BODY, encoding="utf-8")
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


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _text(wiki):
    return (wiki / PAGE).read_text(encoding="utf-8")


@pytest.mark.parametrize("mode,new_value", [("append", "- Tea: green"),
                                            ("replace_file", "# Gone\n")])
def test_a_direct_append_or_overwrite_without_the_hash_writes_nothing(wiki, client, mode, new_value):
    r = client.post("/api/wiki/update", json={"file": PAGE, "section": "Preferences",
                                              "new_value": new_value, "mode": mode, "reason": "r"})
    body = r.get_json()
    assert r.status_code == 409 and body["status"] == "stale", body
    assert body["before_sha256"] == _sha(BODY)
    assert _text(wiki) == BODY


@pytest.mark.parametrize("mode,new_value", [("append", "- Tea: green"),
                                            ("replace_file", "# Gone\n")])
def test_with_the_hash_of_the_page_shown_it_applies(wiki, client, mode, new_value):
    r = client.post("/api/wiki/update", json={"file": PAGE, "section": "Preferences",
                                              "new_value": new_value, "mode": mode, "reason": "r",
                                              "before_sha256": _sha(BODY)})
    assert r.status_code == 200 and r.get_json()["result"] == "applied", r.get_json()
    assert _text(wiki) != BODY


def test_an_edit_and_a_new_page_need_no_hash(wiki, client):
    r = client.post("/api/wiki/update", json={"file": PAGE, "old_value": "- Colour: blue",
                                              "new_value": "- Colour: green", "reason": "r"})
    assert r.status_code == 200, r.get_json()
    r = client.post("/api/wiki/update", json={"file": "notes/new-page.md",
                                              "new_value": "- First", "reason": "r"})
    assert r.status_code == 200, r.get_json()
    assert (wiki / "notes" / "new-page.md").is_file()


def test_the_owners_editor_save_still_writes(wiki, client):
    r = client.put("/api/wiki/edit", json={"file": PAGE, "content": BODY + "- Tea: green\n"})
    assert r.status_code == 200, r.get_json()
    assert _text(wiki) == BODY + "- Tea: green\n"


def test_the_owners_ui_never_calls_the_direct_update():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    for rel in ("index.html", "ui_parts/app.html"):
        assert "/api/wiki/update" not in (root / rel).read_text(encoding="utf-8"), rel
