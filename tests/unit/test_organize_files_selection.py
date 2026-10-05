"""organize_files(selection="screen"): the card is bound to the files that were ticked in the Files browser
(the same exact-batch rule as mail, I1 for files), and the browser is told which tiles are held.
"""
from __future__ import annotations

import pytest

from agent_friday.governance import action_gate as ag
from agent_friday.services import action_journal as journal
from agent_friday.services import agent, approvals as ap, desktop_bus, item_actions as ia
from agent_friday.services import studio_files as sf
from tests.screen_fixtures import Page, report


def _mk(p):
    p.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture
def world(tmp_path, monkeypatch, friday_dir):
    from agent_friday.services import dissent_gate as dg
    from agent_friday.services import file_grants as fg
    desktop_bus.reset()
    monkeypatch.setattr(fg, "_ledger_path", lambda: friday_dir / "privacy" / "file_grants.jsonl")
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(ag, "verify_claws", lambda: (True, "ok"))
    monkeypatch.setattr(journal, "_path", lambda: tmp_path / "receipts.jsonl")
    monkeypatch.setattr(journal, "home_trash", lambda rid: tmp_path / "friday-trash" / rid)
    journal.reset()
    fg._invalidate_cache()
    downloads = _mk(tmp_path / "Downloads")
    docs = _mk(tmp_path / "Documents")
    monkeypatch.setattr(sf, "roots", lambda: {"documents": docs, "downloads": downloads})
    for name in ("a.pdf", "b.pdf", "c.pdf", "d.pdf"):
        (downloads / name).write_bytes(b"%PDF-1 " + name.encode())
    yield {"downloads": downloads, "docs": docs}
    journal.reset()
    fg._invalidate_cache()
    desktop_bus.reset()


def _fref(name, root="downloads"):
    return "file:%s:%s" % (root, name)


def _files_stage(selected=(), rev=3):
    items = [{"ref": _fref(n), "n": i + 1, "facets": {"kind": "pdf", "ext": ".pdf", "dir": False}, "title": n, "who": ""}
             for i, n in enumerate(("a.pdf", "b.pdf", "c.pdf", "d.pdf"))]
    refs = [_fref(n) for n in selected]
    return {"workspace": "files", "rev": rev, "items": items, "loaded": 4, "total_hint": 4,
            "selection": {"id": "sel_f", "refs": refs, "count": len(refs), "label": "", "source": "owner", "beyond_loaded": 0},
            "filters": [], "focus": None, "open": None, "cursor": None, "fields": [], "held": []}


def _org(**kw):
    return agent._tool_organize_files(dict({"selection": "screen"}, **kw))


def _card():
    return next(c for c in ap.list_approvals() if (c.get("payload") or {}).get("handler") == ia.HANDLER)


def test_the_card_covers_exactly_the_ticked_files_and_nothing_moves_yet(world, monkeypatch):
    report(_files_stage(selected=["a.pdf", "b.pdf", "c.pdf"]))
    page = Page(monkeypatch)
    out = _org(action="move", to="Documents")
    assert out.startswith("CARD_RAISED"), out
    pay = _card()["payload"]
    assert [o["path"] for o in pay["ops"]] == ["a.pdf", "b.pdf", "c.pdf"] and pay["refs"] == [_fref(n) for n in ("a.pdf", "b.pdf", "c.pdf")]
    assert pay["stage_ws"] == "files" and pay["stage_rev"] == 3 and pay["selection_id"] == "sel_f"
    assert (world["downloads"] / "a.pdf").exists() and not (world["docs"] / "a.pdf").exists()
    held = [a[0] for a in page.pushed if a[0].get("state") == "held"]
    assert held and held[0]["workspace"] == "files" and len(held[0]["refs"]) == 3


def test_files_added_or_ticked_after_the_card_are_not_in_it(world, monkeypatch):
    report(_files_stage(selected=["a.pdf", "b.pdf"]))
    Page(monkeypatch)
    _org(action="move", to="Documents")
    (world["downloads"] / "e.pdf").write_bytes(b"%PDF-1 e")
    report(_files_stage(selected=["a.pdf", "b.pdf", "c.pdf", "d.pdf"], rev=9))
    card = _card()
    ap.decide(card["approval_id"], "approve")
    rec = ia.wait_for(card["approval_id"], 10)
    assert rec["status"] == "complete"
    assert sorted(p.name for p in world["docs"].iterdir()) == ["a.pdf", "b.pdf"]
    assert (world["downloads"] / "c.pdf").exists() and (world["downloads"] / "d.pdf").exists()


def test_one_ticked_file_changes_at_once_with_a_receipt_and_the_page_hears_done(world, monkeypatch):
    report(_files_stage(selected=["d.pdf"]))
    page = Page(monkeypatch)
    out = _org(action="move", to="Documents")
    assert out.startswith("DONE"), out
    assert (world["docs"] / "d.pdf").exists() and not ap.list_approvals()
    done = [a[0] for a in page.pushed if a[0].get("state") == "done"]
    assert done and done[-1]["workspace"] == "files" and done[-1]["refs"] == [_fref("d.pdf")]


def test_the_page_hears_declined_and_the_ticks_stay(world, monkeypatch):
    report(_files_stage(selected=["a.pdf", "b.pdf"]))
    page = Page(monkeypatch)
    _org(action="trash")
    ap.decide(_card()["approval_id"], "deny")
    declined = [a[0] for a in page.pushed if a[0].get("state") == "declined"]
    assert declined and declined[-1]["workspace"] == "files" and len(declined[-1]["refs"]) == 2
    assert (world["downloads"] / "a.pdf").exists()


def test_a_stale_files_stage_refuses_and_does_not_search(world, monkeypatch):
    report(_files_stage(selected=["a.pdf", "b.pdf"]))
    desktop_bus._CLIENTS["pg1"]["stage_at"] -= 30
    Page(monkeypatch)
    out = _org(action="trash")
    assert out.startswith("NOT DONE") and "can't see your list" in out
    assert not ap.list_approvals() and (world["downloads"] / "a.pdf").exists()


def test_only_files_count_and_a_plain_call_is_unchanged(world, monkeypatch):
    st = _files_stage(selected=["a.pdf"])
    st["selection"]["refs"] = ["mail:acct_work:t1"]
    report(st)
    Page(monkeypatch)
    assert _org(action="trash").startswith("NOT DONE")
    out = agent._tool_organize_files({"action": "trash", "items": ["Downloads/b.pdf", "Downloads/c.pdf"]})
    assert out.startswith("CARD_RAISED")
    assert "refs" not in _card()["payload"]
