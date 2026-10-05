"""organize_media: favourite, tag or move Media cards. One at once, two or more on ONE card bound to the
cards that were ticked; every change has a receipt and Undo puts each card back as it was. The owner's
own bulk bar in Media (/api/media/bulk) stays card-free.
"""
from __future__ import annotations

import pytest

from agent_friday.governance import action_gate as ag
from agent_friday.services import action_journal as journal
from agent_friday.services import agent, approvals as ap, desktop_bus, item_actions as ia
from tests.screen_fixtures import Page, report


class FakeIndex:
    """media_index as organize_media sees it: get(id) and patch(...)."""

    def __init__(self):
        self.cards = {"c%d" % i: {"id": "c%d" % i, "title": "Card %d" % i, "favorite": False, "tags": ["old"], "project": ""}
                      for i in range(1, 5)}
        self.patches = []

    def get(self, cid):
        c = self.cards.get(cid)
        return dict(c) if c else None

    def patch(self, cid, favorite=None, tags=None, project=None, **kw):
        c = self.cards.get(cid)
        if c is None:
            return {"status": "not_found"}
        self.patches.append((cid, favorite, tags, project))
        if favorite is not None:
            c["favorite"] = favorite
        if tags is not None:
            c["tags"] = list(tags)
        if project is not None:
            c["project"] = project
        return {"status": "ok", "card": dict(c)}


@pytest.fixture
def world(tmp_path, monkeypatch):
    desktop_bus.reset()
    fake = FakeIndex()
    monkeypatch.setattr(ia, "_mi", lambda: fake)
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(ag, "verify_claws", lambda: (True, "ok"))
    monkeypatch.setattr(journal, "_path", lambda: tmp_path / "receipts.jsonl")
    journal.reset()
    yield fake
    journal.reset()
    desktop_bus.reset()


def _stage(selected=(), rev=4):
    items = [{"ref": "media:c%d" % i, "n": i, "facets": {"kind": "post", "status": "draft"}, "title": "Card %d" % i, "who": ""}
             for i in range(1, 5)]
    refs = ["media:c%d" % i for i in selected]
    return {"workspace": "media", "rev": rev, "items": items, "loaded": 4, "total_hint": 4,
            "selection": {"id": "sel_m", "refs": refs, "count": len(refs), "label": "", "source": "owner", "beyond_loaded": 0},
            "filters": [], "focus": None, "open": None, "cursor": None, "fields": [], "held": []}


def _card():
    return next(c for c in ap.list_approvals() if (c.get("payload") or {}).get("handler") == ia.HANDLER)


def test_one_card_changes_at_once_with_a_receipt_and_undo_puts_it_back(world):
    out = ia.organize_media("favourite", cards=["c1"])
    assert out["status"] == "complete" and not ap.list_approvals(), out
    assert world.cards["c1"]["favorite"] is True
    back = ia.undo(out["receipt_id"])
    assert back["status"] == "complete" and world.cards["c1"]["favorite"] is False


def test_tag_untag_and_project_keep_what_they_found(world):
    ia.organize_media("tag", cards=["c1"], value="harbor")
    assert world.cards["c1"]["tags"] == ["old", "harbor"]
    out = ia.organize_media("untag", cards=["c1"], value="OLD")
    assert world.cards["c1"]["tags"] == ["harbor"]
    ia.undo(out["receipt_id"])
    assert world.cards["c1"]["tags"] == ["old", "harbor"]
    p = ia.organize_media("project", cards=["c2"], value="Harbor")
    assert world.cards["c2"]["project"] == "Harbor"
    ia.undo(p["receipt_id"])
    assert world.cards["c2"]["project"] == ""


def test_two_or_more_cards_wait_for_one_card_and_approval_changes_exactly_those(world):
    out = ia.organize_media("favourite", cards=["c1", "c2", "c3"], owner_words="favourite those")
    assert out["status"] == "pending_approval" and out["count"] == 3
    assert not any(c["favorite"] for c in world.cards.values())
    card = _card()
    assert card["title"] == "Friday wants to favourite 3 cards"
    ap.decide(card["approval_id"], "approve")
    rec = ia.wait_for(card["approval_id"], 10)
    assert rec["status"] == "complete"
    assert [c["favorite"] for c in world.cards.values()] == [True, True, True, False]


def test_a_missing_card_or_an_unknown_action_is_refused_in_words(world):
    with pytest.raises(ia.Refused):
        ia.organize_media("favourite", cards=["nope"])
    with pytest.raises(ia.Refused):
        ia.organize_media("publish", cards=["c1"])
    with pytest.raises(ia.Refused):
        ia.organize_media("tag", cards=["c1"])
    with pytest.raises(ia.Refused):
        ia.organize_media("favourite", cards=[])


def test_selection_screen_binds_the_card_to_the_ticked_cards_and_holds_them(world, monkeypatch):
    report(_stage(selected=[1, 2, 3]))
    page = Page(monkeypatch)
    out = agent._tool_organize_media({"action": "tag", "value": "harbor", "selection": "screen"})
    assert out.startswith("CARD_RAISED"), out
    pay = _card()["payload"]
    assert [o["id"] for o in pay["ops"]] == ["c1", "c2", "c3"] and pay["refs"] == ["media:c1", "media:c2", "media:c3"]
    assert pay["stage_ws"] == "media" and pay["stage_rev"] == 4
    held = [a[0] for a in page.pushed if a[0].get("state") == "held"]
    assert held and held[0]["workspace"] == "media"
    report(_stage(selected=[1, 2, 3, 4], rev=8))                # ticking a fourth after the card changes nothing in it
    ap.decide(_card()["approval_id"], "approve")
    ia.wait_for(_card()["approval_id"], 10)
    assert "harbor" in world.cards["c3"]["tags"] and "harbor" not in world.cards["c4"]["tags"]
    assert [a[0]["state"] for a in page.pushed if a[0]["type"] == "held"][-1] == "done"


def test_the_card_in_front_of_them_is_this_when_nothing_is_ticked(world, monkeypatch):
    st = _stage()
    st["open"] = "media:c4"
    report(st)
    Page(monkeypatch)
    out = agent._tool_organize_media({"action": "favourite", "selection": "screen"})
    assert out.startswith("DONE") and world.cards["c4"]["favorite"] is True


def test_the_tool_is_judged_by_how_many_cards_like_the_other_organize_tools():
    assert ag.classify("organize_media", {"action": "favourite", "cards": ["c1"]})[0] == ag.INTERNAL
    assert ag.classify("organize_media", {"action": "favourite", "cards": ["c1", "c2"]})[0] == ag.OUTWARD
    assert "organize_media" in ag.SELF_GATED


def test_the_media_bar_the_owner_presses_is_not_a_tool_and_raises_no_card():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "src" / "agent_friday" / "routes" / "media.py").read_text(encoding="utf-8")
    body = src[src.index("def media_bulk"):src.index("def media_bulk") + 700]
    assert "approval" not in body.lower() and "organize_media" not in body
