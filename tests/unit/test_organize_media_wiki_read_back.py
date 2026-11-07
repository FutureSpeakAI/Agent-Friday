"""Media-card and wiki-page changes count as done only once they are read back (NS-4.6-1: verify
effects against an authoritative source).

Before: organize_media and organize_wiki wrote "Done" because the patch or write call returned ok.
Now the card is read again (favourite, tag, project as asked) and the page is read again (tag in its
frontmatter; a move, rename or archive found at the new path and gone from the old; trash kept in
Friday's trash and gone from the wiki). The receipt marks each item verified; a change that cannot be
found is never called done: its undo stays, the receipt is partial, and the summary names it.
Fakes and synthetic pages in a temp folder.
"""
from __future__ import annotations

import pytest

from agent_friday.governance import action_gate as ag
from agent_friday.services import action_journal as journal
from agent_friday.services import approvals as ap
from agent_friday.services import item_actions as ia
from agent_friday.services import wiki_engine as we


class FakeIndex:
    """media_index as organize_media sees it. `lossy` makes patch say ok and change nothing."""

    def __init__(self):
        self.cards = {"c%d" % i: {"id": "c%d" % i, "title": "Card %d" % i, "favorite": False,
                                  "tags": ["old"], "project": ""} for i in range(1, 4)}
        self.lossy = set()

    def get(self, cid):
        c = self.cards.get(cid)
        return dict(c) if c else None

    def patch(self, cid, favorite=None, tags=None, project=None, **kw):
        c = self.cards.get(cid)
        if c is None:
            return {"status": "not_found"}
        if cid in self.lossy:
            return {"status": "ok", "card": dict(c)}
        if favorite is not None:
            c["favorite"] = favorite
        if tags is not None:
            c["tags"] = list(tags)
        if project is not None:
            c["project"] = project
        return {"status": "ok", "card": dict(c)}


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(ag, "verify_claws", lambda: (True, "ok"))
    monkeypatch.setattr(journal, "_path", lambda: tmp_path / "receipts.jsonl")
    monkeypatch.setattr(journal, "home_trash", lambda rid: tmp_path / "friday-trash" / rid)
    journal.reset()
    yield
    journal.reset()


@pytest.fixture
def media(monkeypatch):
    fake = FakeIndex()
    monkeypatch.setattr(ia, "_mi", lambda: fake)
    return fake


@pytest.fixture
def wiki(tmp_path, monkeypatch):
    root = tmp_path / "wiki"
    (root / "people").mkdir(parents=True)
    (root / "projects").mkdir()
    monkeypatch.setattr(we, "WIKI_DIR", root)
    monkeypatch.setattr(we, "_wiki_mirror_dir", lambda: None)
    monkeypatch.setattr(we, "_wiki_encrypted_sections", lambda: set())
    (root / "people" / "Dana Smith.md").write_text(
        "---\ntitle: Dana Smith\ntags: [work]\n---\n# Dana Smith\n", encoding="utf-8")
    (root / "projects" / "Budget.md").write_text("# Budget\nNo links here.\n", encoding="utf-8")
    return root


def _verified(out):
    return [i.get("verified") for i in journal.get(out["receipt_id"])["items"]]


# ── media ──

@pytest.mark.parametrize("action,kw", [("favourite", {}), ("tag", {"value": "trip"}),
                                       ("untag", {"value": "old"}), ("project", {"value": "Spain"})])
def test_a_card_change_found_on_the_card_is_read_back(media, action, kw):
    out = ia.organize_media(action, cards=["c1"], **kw)
    assert out["status"] == "complete", out
    assert _verified(out) == [True]


def test_a_card_change_that_did_not_land_is_not_called_done(media):
    media.lossy.add("c1")
    out = ia.organize_media("favourite", cards=["c1"])
    rec = journal.get(out["receipt_id"])
    assert rec["items"][0]["verified"] is False
    assert out["status"] != "complete", out
    assert not rec["summary"].startswith("Done"), rec["summary"]
    assert "confirm" in rec["summary"], rec["summary"]


def test_one_card_not_found_does_not_hide_the_ones_that_were(media):
    media.lossy.add("c2")
    out = ia.organize_media("favourite", cards=["c1", "c2"])
    ap.decide(out["approval_id"], "approve")
    rec = ia.wait_for(out["approval_id"], 10)
    got = {i["id"]: i.get("verified") for i in journal.get(rec["receipt_id"])["items"]}
    assert got == {"c1": True, "c2": False}, got
    assert rec["status"] == "partial", rec


# ── wiki ──

def test_a_tag_found_in_the_page_is_read_back(wiki):
    out = ia.organize_wiki("tag", pages=["people/Dana Smith.md"], tags=["vip"])
    assert out["status"] == "complete", out
    assert _verified(out) == [True]


def test_a_tag_that_did_not_reach_the_page_is_not_called_done(wiki, monkeypatch):
    monkeypatch.setattr(ia, "_write_page", lambda path, text: None)     # returns, writes nothing
    out = ia.organize_wiki("tag", pages=["people/Dana Smith.md"], tags=["vip"])
    rec = journal.get(out["receipt_id"])
    assert rec["items"][0]["verified"] is False
    assert out["status"] != "complete", out
    assert "confirm" in rec["summary"], rec["summary"]


def test_a_moved_page_is_read_back_at_its_new_path(wiki):
    out = ia.organize_wiki("move", pages=["projects/Budget.md"], to="people")
    assert out["status"] == "complete", out
    assert _verified(out) == [True]


def test_a_move_that_did_not_happen_is_not_called_done(wiki, monkeypatch):
    monkeypatch.setattr(ia, "_move_page", lambda src, dst: None)
    out = ia.organize_wiki("move", pages=["projects/Budget.md"], to="people")
    rec = journal.get(out["receipt_id"])
    assert rec["items"][0]["verified"] is False
    assert out["status"] != "complete", out
    assert "confirm" in rec["summary"], rec["summary"]


def test_trash_is_read_back_from_fridays_trash(wiki):
    out = ia.organize_wiki("trash", pages=["projects/Budget.md"])
    ap.decide(out["approval_id"], "approve")
    rec = ia.wait_for(out["approval_id"], 10)
    assert rec["status"] == "complete", rec
    assert _verified(rec) == [True]
