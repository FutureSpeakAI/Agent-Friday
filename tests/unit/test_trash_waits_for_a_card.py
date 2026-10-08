"""The owner's rule (2026-10): anything that sends, deletes, trashes, archives, spends, or changes
settings or permissions waits for one card per batch. Trashing one file or trashing or archiving one
wiki page is no exception, though each goes to Friday's own trash and can be undone; moving,
renaming or tagging one item still changes at once."""
from __future__ import annotations

import pytest

from agent_friday.governance import action_gate as ag
from agent_friday.services import item_actions as ia


@pytest.mark.parametrize("action", ["trash", "archive"])
def test_one_wiki_page_taken_away_waits_for_a_card(action):
    klass, why = ia.classify_wiki({"action": action, "pages": ["people/dana.md"]})
    assert klass == "outward" and "card" in why


def test_one_file_trashed_waits_for_a_card():
    klass, why = ia.classify_files({"action": "trash", "items": ["Documents/old.txt"]})
    assert klass == "outward" and "card" in why


@pytest.mark.parametrize("call", [
    ("organize_files", {"action": "rename", "items": ["Documents/a.txt"], "new_name": "b.txt"}),
    ("organize_files", {"action": "move", "items": ["Documents/a.txt"], "to": "Documents/Old"}),
    ("organize_wiki", {"action": "rename", "pages": ["people/dana.md"], "new_name": "dana-k"}),
    ("organize_wiki", {"action": "tag", "pages": ["people/dana.md"], "tags": ["family"]}),
])
def test_one_item_moved_renamed_or_tagged_still_changes_at_once(call):
    fn = ia.classify_files if call[0] == "organize_files" else ia.classify_wiki
    assert fn(call[1])[0] == "internal"


def test_the_gate_reads_the_same_rule():
    for tool, args in (("organize_files", {"action": "trash", "items": ["Documents/old.txt"]}),
                       ("organize_wiki", {"action": "archive", "pages": ["people/dana.md"]})):
        klass, _why = ag.classify(tool, args)
        assert klass == ag.OUTWARD, tool


@pytest.mark.parametrize("handler,kw,plan", [
    (ia.organize_files, {"action": "trash", "items": ["Documents/old.txt"]}, "plan_files"),
    (ia.organize_wiki, {"action": "trash", "pages": ["people/dana.md"]}, "plan_wiki"),
    (ia.organize_wiki, {"action": "archive", "pages": ["people/dana.md"]}, "plan_wiki"),
])
def test_the_handler_raises_the_card_for_one_item(monkeypatch, handler, kw, plan):
    seen = {}
    kw = dict(kw)
    action = kw.pop("action")
    monkeypatch.setattr(ia, plan, lambda *a, **k: [{"op": action, "item": "x"}])
    monkeypatch.setattr(ia, "_local", lambda domain, op, ops, bulk=False, **k: seen.setdefault("bulk", bulk) or {})
    handler(action, **kw)
    assert seen["bulk"] is True, "one item taken away is carded, not run"
