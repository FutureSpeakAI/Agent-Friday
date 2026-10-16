"""Wiki appends land under the section the page already has, and a new page
whose content opens with its own title is not given a second one.

Field reports:

  * An append to a section the page already had made a second heading of the
    same name: the section was named "## Preferences" (or the text opened with
    "## Preferences"), which was not matched against the page's
    "## Preferences". The existing heading is matched with or without its
    ``#``, at any level, ignoring case, spacing and a trailing colon, and the
    text goes at the END of that section (before the next heading of the same
    or a higher level). A heading is added only when the page has none.
  * Creating a page whose content already starts with an H1 showed the title
    twice. The content's own H1 is the title.

The preview the owner approves is the plan that is applied, so what it shows
is what lands.
"""
from __future__ import annotations

import logging

import pytest

from agent_friday.governance import action_gate as ag
from agent_friday.services import wiki_engine as we

PAGE = "notes/sample-page.md"
BODY = ("# Sample Page\n\n"
        "## Preferences\n- Colour: blue\n\n"
        "### Drinks\n- Coffee: black\n\n"
        "## Other\n- Pets: one cat\n")


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


def _tool(inp):
    from agent_friday.services import agent
    return agent._tool_propose_wiki_update(inp)


def _decided(card_id, inp):
    tok = ag.DECIDED.set(card_id)
    try:
        return _tool(inp)
    finally:
        ag.DECIDED.reset(tok)


def _text(wiki, rel=PAGE):
    return (wiki / rel).read_text(encoding="utf-8")


def _pending():
    return [p for p in we._load_pending_wiki() if p.get("status") == "pending"]


#: The page with "- Tea: green" at the end of the Preferences section: after
#: its subsection, before "## Other".
WANT = BODY.replace("- Coffee: black\n", "- Coffee: black\n- Tea: green\n")


# ── #9: an append goes under the heading the page already has ──────────────

@pytest.mark.parametrize("section", [
    "## Preferences",          # with the ## prefix
    "preferences",             # without it, other case
    "  Preferences  ",         # stray spaces
    "### PREFERENCES:",        # another level, a trailing colon
    "## Preferences ##",       # a closing run of #
])
def test_an_append_lands_at_the_end_of_the_existing_section(wiki, section):
    out = _decided("card-s1", {"file": PAGE, "section": section, "new_value": "- Tea: green",
                               "mode": "append", "reason": "r"})
    text = _text(wiki)
    assert text == WANT, (out, text)
    assert text.count("Preferences") == 1, text


@pytest.mark.parametrize("section", ["Preferences", ""])
def test_text_that_opens_with_the_heading_is_not_given_a_second_one(wiki, section):
    """The text itself opens with "## Preferences": with the section named, or
    with no section at all, it goes under the page's own Preferences."""
    out = _decided("card-s2", {"file": PAGE, "section": section,
                               "new_value": "## Preferences\n- Tea: green", "reason": "r"})
    text = _text(wiki)
    assert text == WANT, (out, text)


def test_a_heading_in_a_code_block_is_not_the_section(wiki):
    page = ("# Setup\n\n```sh\n# Preferences\necho hi\n```\n\n"
            "## Preferences\n- Colour: blue\n")
    (wiki / PAGE).write_text(page, encoding="utf-8")
    _decided("card-s3", {"file": PAGE, "section": "Preferences", "new_value": "- Tea: green",
                         "reason": "r"})
    assert _text(wiki) == page + "- Tea: green\n", _text(wiki)


def test_a_new_heading_only_when_the_page_has_none(wiki):
    _decided("card-s4", {"file": PAGE, "section": "## Travel", "new_value": "- Window seat",
                         "reason": "r"})
    assert _text(wiki) == BODY + "\n## Travel\n- Window seat\n", _text(wiki)


def test_the_preview_shows_exactly_what_is_applied(wiki, client):
    out = _tool({"file": PAGE, "section": "## Preferences", "new_value": "- Tea: green",
                 "mode": "append", "reason": "r"})
    assert "proposed" in out, out
    [item] = _pending()
    p = client.get(f"/api/wiki/pending/{item['id']}/preview").get_json()
    added = [ln for ln in p["diff"].splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    assert added == ["+- Tea: green"], p["diff"]           # no second heading in the preview
    assert p["lines_removed"] == 0
    assert "existing section" in p["headline"] and "## Preferences" in p["headline"], p["headline"]
    r = client.post(f"/api/wiki/pending/{item['id']}/approve",
                    json={"before_sha256": p["before_sha256"]})
    assert r.status_code == 200 and r.get_json()["status"] == "applied", r.get_json()
    assert _text(wiki) == WANT                              # what was shown is what landed


def test_an_append_already_in_the_section_is_not_added_again(wiki):
    inp = {"file": PAGE, "section": "Preferences",
           "new_value": "## Preferences\n- Tea: green", "reason": "r"}
    _decided("card-s5", inp)
    out = _decided("card-s6", inp)
    assert _text(wiki) == WANT, (out, _text(wiki))
    assert "not needed" in out.lower(), out


# ── #13: a page whose content opens with an H1 gets one title ──────────────

def test_a_new_page_that_opens_with_its_title_has_it_once(wiki):
    rel = "notes/garden-plan.md"
    _decided("card-t1", {"file": rel, "new_value": "# Garden Plan\n\n- Tomatoes\n",
                         "reason": "r"})
    assert _text(wiki, rel) == "# Garden Plan\n\n- Tomatoes\n", _text(wiki, rel)


def test_a_section_named_like_the_title_is_not_a_second_title(wiki):
    """Profile setup sends a full page (with its H1) and a section named like
    the page: the page is written once, with one title."""
    rel = "identity/core-profile.md"
    _decided("card-t2", {"file": rel, "section": "Core profile",
                         "new_value": "# Core Profile\n\n- **Name:** Sample Person\n",
                         "reason": "r"})
    text = _text(wiki, rel)
    assert text == "# Core Profile\n\n- **Name:** Sample Person\n", text


def test_a_different_h1_is_kept_and_logged(wiki, caplog):
    rel = "notes/tea-list.md"
    with caplog.at_level(logging.INFO, logger="friday.wiki"):
        _decided("card-t3", {"file": rel, "new_value": "# Teas I Like\n\n- Green\n",
                             "reason": "r"})
    text = _text(wiki, rel)
    assert text == "# Teas I Like\n\n- Green\n", text
    assert text.count("\n# ") + text.startswith("# ") == 1
    assert any("Teas I Like" in r.getMessage() and "Tea List" in r.getMessage()
               for r in caplog.records), [r.getMessage() for r in caplog.records]


def test_a_new_page_without_an_h1_still_gets_its_title(wiki):
    rel = "notes/reading-list.md"
    _decided("card-t4", {"file": rel, "section": "Books", "new_value": "## Books\n- One",
                         "reason": "r"})
    assert _text(wiki, rel) == "# Reading List\n\n## Books\n- One\n", _text(wiki, rel)


def test_setup_drafts_have_one_title_each(wiki, client, monkeypatch):
    from agent_friday.routes import wiki as rw
    monkeypatch.setattr(rw, "WIKI_DIR", wiki, raising=False)
    r = client.post("/api/wiki/setup-research", json={"full_name": "Sample Person"})
    assert r.status_code == 200, r.get_json()
    for item in _pending():
        p = client.get(f"/api/wiki/pending/{item['id']}/preview").get_json()
        h1 = [ln for ln in p["diff"].splitlines() if ln.startswith("+# ")]
        assert len(h1) == 1, p["diff"]
