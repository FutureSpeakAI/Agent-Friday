"""Friday organizing the owner's wiki (services/item_actions).

  * A rename updates every link to the page, and undo puts the name and the
    links back.
  * A page that moves keeps its own relative links working.
  * Tags change in the page's own frontmatter; undo takes off only what the
    action added.
  * Archive keeps a page out of the graph; trash moves it out of the wiki into
    Friday's trash. Neither deletes anything.
  * An encrypted page stays encrypted: it cannot be moved into a plain
    section, and a plain page moved into an encrypted one is encrypted.
  * A title that fits two pages is not guessed: the choices come back.
  * Two pages or more are a batch on ONE card.
"""
from __future__ import annotations

import os

import pytest

from agent_friday.governance import action_gate as ag
from agent_friday.services import action_journal as journal
from agent_friday.services import approvals as ap
from agent_friday.services import item_actions as ia
from agent_friday.services import wiki_engine as we
from agent_friday.services.knowledge_graph.wiki_graph import list_wiki_pages

KEY = os.urandom(32)


@pytest.fixture(autouse=True)
def wiki(tmp_path, monkeypatch):
    from agent_friday.services import dissent_gate as dg
    root = tmp_path / "wiki"
    (root / "people").mkdir(parents=True)
    (root / "projects").mkdir()
    (root / "health").mkdir()
    monkeypatch.setattr(we, "WIKI_DIR", root)
    monkeypatch.setattr(we, "_wiki_mirror_dir", lambda: None)
    monkeypatch.setattr(we, "_wiki_encrypted_sections", lambda: {"health"})
    import agent_friday.services.agent as agent_mod
    monkeypatch.setattr(agent_mod, "_get_vault_key", lambda: KEY)
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(ag, "verify_claws", lambda: (True, "ok"))
    monkeypatch.setattr(journal, "_path", lambda: tmp_path / "receipts.jsonl")
    monkeypatch.setattr(journal, "home_trash", lambda rid: tmp_path / "friday-trash" / rid)
    journal.reset()
    (root / "people" / "Dana Smith.md").write_text(
        "---\ntitle: Dana Smith\ntags: [work]\n---\n# Dana Smith\nSee [go-live](../projects/Go Live.md).\n",
        encoding="utf-8")
    (root / "projects" / "Go Live.md").write_text(
        "# Go live\nOwner: [[Dana Smith]], and [[Dana Smith|Dana]] signs off. "
        "Details: [profile](../people/Dana Smith.md)\n", encoding="utf-8")
    (root / "projects" / "Budget.md").write_text("# Budget\nNo links here.\n", encoding="utf-8")
    (root / "people" / "Dana Brown.md").write_text("# Dana Brown\n", encoding="utf-8")
    we.wiki_write_text(root / "health" / "Dr Lee.md", "# Dr Lee\nprivate\n")
    yield root
    journal.reset()


def _text(root, rel):
    return (root / rel).read_text(encoding="utf-8")


def test_a_rename_updates_every_link_and_undo_puts_them_back(wiki):
    out = ia.organize_wiki("rename", pages=["people/Dana Smith.md"], new_name="Dana Smith (Acme)")
    assert out["status"] == "complete", out
    assert (wiki / "people" / "Dana Smith (Acme).md").exists()
    assert not (wiki / "people" / "Dana Smith.md").exists()
    go = _text(wiki, "projects/Go Live.md")
    assert "[[Dana Smith (Acme)]]" in go and "[[Dana Smith (Acme)|Dana]]" in go
    # a bracket cannot sit in a markdown link's address: it becomes a wikilink
    assert "[[Dana Smith (Acme)|profile]]" in go
    ia.undo(out["receipt_id"])
    assert (wiki / "people" / "Dana Smith.md").exists()
    back = _text(wiki, "projects/Go Live.md")
    assert back.count("[[Dana Smith") == 2 and "(Acme)" not in back
    assert "[profile](../people/Dana Smith.md)" in back


def test_a_rename_rewrites_markdown_links_to_the_new_name(wiki):
    out = ia.organize_wiki("rename", pages=["Go Live"], new_name="Launch Plan")
    assert out["status"] == "complete", out
    assert "[go-live](../projects/Launch Plan.md)" in _text(wiki, "people/Dana Smith.md")


def test_a_rename_onto_an_existing_page_is_refused(wiki):
    with pytest.raises(ia.Refused):
        ia.organize_wiki("rename", pages=["people/Dana Smith.md"], new_name="Budget")


def test_a_moved_page_keeps_its_own_links_working(wiki):
    out = ia.organize_wiki("move", pages=["people/Dana Smith.md"], to="people/clients")
    assert out["status"] == "complete", out
    moved = _text(wiki, "people/clients/Dana Smith.md")
    assert "(../../projects/Go Live.md)" in moved
    assert "(../people/clients/Dana Smith.md)" in _text(wiki, "projects/Go Live.md")
    ia.undo(out["receipt_id"])
    assert "(../projects/Go Live.md)" in _text(wiki, "people/Dana Smith.md")
    assert "(../people/Dana Smith.md)" in _text(wiki, "projects/Go Live.md")


def test_tags_change_in_the_frontmatter_and_undo_removes_only_what_was_added(wiki):
    out = ia.organize_wiki("tag", pages=["people/Dana Smith.md"], tags=["work", "acme"])
    assert out["status"] == "complete"
    assert "tags: [work, acme]" in _text(wiki, "people/Dana Smith.md")
    ia.undo(out["receipt_id"])
    assert "tags: [work]" in _text(wiki, "people/Dana Smith.md")


def test_a_tag_on_a_page_without_frontmatter_adds_it(wiki):
    ia.organize_wiki("tag", pages=["projects/Budget.md"], tags=["finance"])
    body = _text(wiki, "projects/Budget.md")
    assert body.startswith("---\ntags: [finance]\n---\n")
    assert "# Budget" in body


def test_archive_keeps_a_page_out_of_the_graph_and_undo_brings_it_back(wiki):
    out = ia.organize_wiki("archive", pages=["projects/Budget.md"])
    assert out["status"] == "complete"
    assert (wiki / "_archived" / "projects" / "Budget.md").exists()
    assert all(p.name != "Budget.md" for p in list_wiki_pages(wiki))
    ia.undo(out["receipt_id"])
    assert (wiki / "projects" / "Budget.md").exists()


def test_trash_moves_a_page_out_of_the_wiki_intact(wiki, tmp_path):
    before = (wiki / "health" / "Dr Lee.md").read_bytes()
    out = ia.organize_wiki("trash", pages=["health/Dr Lee.md"])
    assert out["status"] == "complete"
    assert not (wiki / "health" / "Dr Lee.md").exists()
    kept = list((tmp_path / "friday-trash").rglob("Dr Lee.md"))
    assert len(kept) == 1 and kept[0].read_bytes() == before, "the ciphertext is kept as it was"
    ia.undo(out["receipt_id"])
    assert (wiki / "health" / "Dr Lee.md").read_bytes() == before


def test_an_encrypted_page_cannot_move_into_a_plain_section(wiki):
    import agent_friday.privacy.vault_crypto as vc
    assert vc.is_encrypted((wiki / "health" / "Dr Lee.md").read_bytes())
    with pytest.raises(ia.Refused) as e:
        ia.organize_wiki("move", pages=["health/Dr Lee.md"], to="people")
    assert "encrypted" in e.value.user_message


def test_a_plain_page_moved_into_an_encrypted_section_is_encrypted(wiki):
    import agent_friday.privacy.vault_crypto as vc
    out = ia.organize_wiki("move", pages=["projects/Budget.md"], to="health")
    assert out["status"] == "complete"
    raw = (wiki / "health" / "Budget.md").read_bytes()
    assert vc.is_encrypted(raw)
    assert we.wiki_read_text(wiki / "health" / "Budget.md").startswith("# Budget")


def test_archiving_an_encrypted_page_keeps_it_encrypted(wiki):
    import agent_friday.privacy.vault_crypto as vc
    ia.organize_wiki("archive", pages=["health/Dr Lee.md"])
    assert vc.is_encrypted((wiki / "_archived" / "health" / "Dr Lee.md").read_bytes())


def test_a_title_that_fits_two_pages_returns_the_choices(wiki):
    with pytest.raises(ia.Refused) as e:
        ia.organize_wiki("archive", pages=["Dana"])
    msg = e.value.user_message
    assert "Dana Smith" in msg and "Dana Brown" in msg and "Which one" in msg
    assert ia.wiki_ref("dana smith") == "people/Dana Smith.md"


def test_two_pages_are_one_card(wiki):
    out = ia.organize_wiki("tag", pages=["projects/Budget.md", "projects/Go Live.md"], tags=["q4"])
    assert out["status"] == "pending_approval"
    assert "tags" not in _text(wiki, "projects/Budget.md")
    ap.decide(out["approval_id"], "approve")
    rec = ia.wait_for(out["approval_id"], 10)
    assert rec["status"] == "complete", rec
    assert "tags: [q4]" in _text(wiki, "projects/Budget.md")
    assert "tags: [q4]" in _text(wiki, "projects/Go Live.md")


def test_a_bad_tag_is_refused(wiki):
    with pytest.raises(ia.Refused):
        ia.organize_wiki("tag", pages=["projects/Budget.md"], tags=["two words, comma"])
