"""Friday organizing the owner's files (services/item_actions).

  * One local change runs now, writes a receipt, and undo puts it back.
  * Two or more changes are a batch: ONE card, nothing moves until it is
    approved, and approving it moves exactly what the card listed.
  * Nothing is deleted: trash moves a file into Friday's own trash, intact.
  * Nothing is overwritten, and a rename cannot turn a file into a program.
  * A change in the code projects, or into a folder a cloud client syncs,
    waits for a card even when it is one file.
"""
from __future__ import annotations

import pytest

from agent_friday.governance import action_gate as ag
from agent_friday.services import action_journal as journal
from agent_friday.services import approvals as ap
from agent_friday.services import item_actions as ia
from agent_friday.services import studio_files as sf


def _mk(p):
    p.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch, friday_dir):
    from agent_friday.services import dissent_gate as dg
    from agent_friday.services import file_grants as fg
    monkeypatch.setattr(fg, "_ledger_path", lambda: friday_dir / "privacy" / "file_grants.jsonl")
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(ag, "verify_claws", lambda: (True, "ok"))
    monkeypatch.setattr(journal, "_path", lambda: tmp_path / "receipts.jsonl")
    monkeypatch.setattr(journal, "home_trash", lambda rid: tmp_path / "friday-trash" / rid)
    journal.reset()
    fg._invalidate_cache()
    yield
    journal.reset()
    fg._invalidate_cache()


@pytest.fixture
def roots(tmp_path, monkeypatch):
    docs = _mk(tmp_path / "Documents")
    downloads = _mk(tmp_path / "Downloads")
    projects = _mk(tmp_path / "Projects")
    synced = _mk(tmp_path / "OneDrive" / "Desktop")
    monkeypatch.setattr(sf, "roots", lambda: {"documents": docs, "downloads": downloads,
                                               "projects": projects, "desktop": synced})
    (downloads / "w2.pdf").write_bytes(b"%PDF-1 w2")
    (downloads / "1099.pdf").write_bytes(b"%PDF-1 1099")
    (downloads / "notes.txt").write_text("keep me", encoding="utf-8")
    _mk(docs / "Taxes")
    (docs / "Taxes" / "w2.pdf").write_bytes(b"%PDF-1 an older w2")
    (docs / ".secret").write_text("x", encoding="utf-8")
    (projects / "app.py").write_text("print(1)\n", encoding="utf-8")
    return {"docs": docs, "downloads": downloads, "projects": projects, "desktop": synced}


def test_one_move_runs_now_with_a_receipt_and_undo_puts_it_back(roots):
    out = ia.organize_files("move", items=["Downloads/notes.txt"], to="Documents/Taxes")
    assert out["status"] == "complete", out
    assert (roots["docs"] / "Taxes" / "notes.txt").read_text(encoding="utf-8") == "keep me"
    assert not (roots["downloads"] / "notes.txt").exists()
    rec = journal.get(out["receipt_id"])
    assert rec["tool"] == "organize_files" and rec["items"][0]["ok"] is True
    back = ia.undo(out["receipt_id"])
    assert back["status"] == "complete", back
    assert (roots["downloads"] / "notes.txt").read_text(encoding="utf-8") == "keep me"
    assert not (roots["docs"] / "Taxes" / "notes.txt").exists()
    assert journal.get(out["receipt_id"])["undone"] is True


def test_a_batch_waits_for_one_card_and_approval_moves_exactly_that(roots):
    out = ia.organize_files("move", items=["Downloads/notes.txt", "Downloads/1099.pdf"],
                            to="Documents/Archive 2025")
    assert out["status"] == "pending_approval", out
    assert (roots["downloads"] / "notes.txt").exists() and (roots["downloads"] / "1099.pdf").exists()
    cards = [r for r in ap.list_approvals(kind="governed_action")
             if (r.get("payload") or {}).get("handler") == ia.HANDLER]
    assert len(cards) == 1, "a batch is ONE card"
    card = cards[0]
    lines = card["payload"]["lines"]
    assert any("notes.txt" in ln for ln in lines) and any("1099.pdf" in ln for ln in lines)
    # something new appears in Downloads before the approval: not in the batch
    (roots["downloads"] / "later.txt").write_text("new", encoding="utf-8")
    ap.decide(card["approval_id"], "approve")
    rec = ia.wait_for(card["approval_id"], 10)
    assert rec and rec["status"] == "complete", rec
    dest = roots["docs"] / "Archive 2025"
    assert sorted(p.name for p in dest.iterdir()) == ["1099.pdf", "notes.txt"]
    assert (roots["downloads"] / "later.txt").exists()
    used = ap.get_approval(card["approval_id"])
    assert used["consumed"] and used["used_detail"]["receipt_id"] == rec["receipt_id"]
    ia.undo(rec["receipt_id"])
    assert (roots["downloads"] / "notes.txt").exists() and (roots["downloads"] / "1099.pdf").exists()
    assert not dest.exists(), "the folder the batch made goes when it is empty again"


def test_a_long_batch_card_keeps_every_item(roots):
    for i in range(60):
        (roots["downloads"] / ("scan-%02d-with-a-long-descriptive-file-name.pdf" % i)).write_bytes(b"%PDF")
    items = ["Downloads/scan-%02d-with-a-long-descriptive-file-name.pdf" % i for i in range(60)]
    out = ia.organize_files("move", items=items, to="Documents/Scans")
    card = ap.get_approval(out["approval_id"])
    assert len(card["payload"]["lines"]) == 60, "the card lists every item it will move"
    assert len(card["action_description"]) < 200
    assert card["description"].rstrip().endswith("more"), "the text copy says how many it left out"


def test_approving_twice_runs_once(roots):
    out = ia.organize_files("trash", items=["Downloads/notes.txt", "Downloads/1099.pdf"])
    aid = out["approval_id"]
    ap.decide(aid, "approve")
    first = ia.wait_for(aid, 10)
    ia._on_decision(ap.get_approval(aid))          # a replayed decision
    assert len([r for r in journal.recent(10) if r["tool"] == "organize_files"]) == 1
    assert first["status"] == "complete"


def test_a_declined_batch_changes_nothing(roots):
    out = ia.organize_files("trash", items=["Downloads/notes.txt", "Downloads/1099.pdf"])
    ap.decide(out["approval_id"], "deny")
    assert (roots["downloads"] / "notes.txt").exists() and (roots["downloads"] / "1099.pdf").exists()
    assert journal.recent(5) == []


def test_trash_keeps_the_file_intact_in_fridays_trash(roots, tmp_path):
    out = ia.organize_files("trash", items=["Downloads/w2.pdf"])
    assert out["status"] == "complete", out
    assert not (roots["downloads"] / "w2.pdf").exists()
    kept = list((tmp_path / "friday-trash").rglob("w2.pdf"))
    assert len(kept) == 1 and kept[0].read_bytes() == b"%PDF-1 w2"
    ia.undo(out["receipt_id"])
    assert (roots["downloads"] / "w2.pdf").read_bytes() == b"%PDF-1 w2"


def test_nothing_is_overwritten_and_the_rest_of_the_batch_goes_on(roots):
    ops = ia.plan_files("move", ["Downloads/1099.pdf"], "Documents/Taxes")
    ops[0:0] = [{"op": "move", "root": "downloads", "path": "w2.pdf", "name": "w2.pdf",
                 "dir": False, "to_root": "documents", "to_path": "Taxes/w2.pdf", "make": []}]
    rec = ia.run_files(ops)
    assert rec["status"] == "partial"
    assert (roots["docs"] / "Taxes" / "w2.pdf").read_bytes() == b"%PDF-1 an older w2"
    assert (roots["downloads"] / "w2.pdf").exists()
    assert (roots["docs"] / "Taxes" / "1099.pdf").exists()


def test_a_move_onto_a_taken_name_is_refused_when_planned(roots):
    with pytest.raises(ia.Refused):
        ia.organize_files("rename", items=["Downloads/1099.pdf"], new_name="w2.pdf")


def test_a_rename_cannot_make_a_program(roots):
    with pytest.raises(ia.Refused) as e:
        ia.organize_files("rename", items=["Downloads/notes.txt"], new_name="notes.bat")
    assert "kind of file" in e.value.user_message
    out = ia.organize_files("rename", items=["Downloads/notes.txt"], new_name="shopping list")
    assert out["status"] == "complete"
    assert (roots["downloads"] / "shopping list.txt").read_text(encoding="utf-8") == "keep me"


def test_private_and_hidden_files_are_refused(roots):
    with pytest.raises(ia.Refused):
        ia.organize_files("trash", items=["Documents/.secret"])
    with pytest.raises(ia.Refused):
        ia.organize_files("move", items=["Downloads/w2.pdf"], to="Documents/.hidden")


def test_a_path_outside_the_folders_is_refused(roots, tmp_path):
    (tmp_path / "elsewhere.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ia.Refused):
        ia.organize_files("trash", items=[str(tmp_path / "elsewhere.txt")])


def test_full_paths_and_root_names_both_work(roots):
    assert ia.file_ref(str(roots["downloads"] / "w2.pdf")) == ("downloads", "w2.pdf")
    assert ia.file_ref("Documents/Taxes/w2.pdf") == ("documents", "Taxes/w2.pdf")
    assert ia.file_ref("documents:Taxes/w2.pdf") == ("documents", "Taxes/w2.pdf")


def test_what_waits_for_a_card(roots):
    one = {"action": "move", "items": ["Downloads/w2.pdf"], "to": "Documents"}
    assert ia.classify_files(one)[0] == "internal"
    two = {"action": "move", "items": ["Downloads/w2.pdf", "Downloads/1099.pdf"], "to": "Documents"}
    assert ia.classify_files(two)[0] == "outward"
    code = {"action": "trash", "items": ["Projects/app.py"]}
    assert ia.classify_files(code)[0] == "outward"
    cloud = {"action": "move", "items": ["Downloads/w2.pdf"], "to": "Desktop"}
    assert ia.classify_files(cloud)[0] == "outward"
    assert ia.classify_files({"action": "move", "items": ["nowhere/x"]})[0] == "outward"


def test_one_file_into_a_synced_folder_raises_a_card(roots):
    out = ia.organize_files("move", items=["Downloads/w2.pdf"], to="Desktop")
    assert out["status"] == "pending_approval"
    assert (roots["downloads"] / "w2.pdf").exists()


def test_sorting_into_several_folders_is_one_card(roots):
    out = ia.organize_files("move", moves=["Downloads/w2.pdf => Documents/Taxes/2025",
                                           "Downloads/notes.txt => Documents/Notes"])
    assert out["status"] == "pending_approval"
    ap.decide(out["approval_id"], "approve")
    rec = ia.wait_for(out["approval_id"], 10)
    assert rec["status"] == "complete", rec
    assert (roots["docs"] / "Taxes" / "2025" / "w2.pdf").exists()
    assert (roots["docs"] / "Notes" / "notes.txt").exists()


def test_the_gate_classifies_by_argument():
    klass, _why = ag.classify("organize_files", {"action": "move", "items": ["a", "b"], "to": "c"})
    assert klass == ag.OUTWARD
    assert "organize_files" in ag.SELF_GATED and "organize_files" in ag.BY_ARGUMENT
