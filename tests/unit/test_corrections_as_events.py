"""Owner corrections are first-class events (north star §29.9-1, §15.6-1,
§36.2-8): each owner correction on the memory card writes one event to the
correction log (services/corrections) with the claim, the correction, the
reason, the prior items, what it affected and what was repaired; the messages
it retires point back to the event; a restore is its own event linked to the
original; the old text stays in history and in the why path but not in recall;
the prompt cites a recent correction; the owner can keep a correction as a test
with their own golden. Friday's own changes write no event.

The real ConversationMemory over an in-memory collection with Chroma's
interface (CI installs no Chroma), as in test_card_edit_supersedes."""
from __future__ import annotations

import json
import re

import pytest

from agent_friday.services import conversation_fts as fts
from agent_friday.services import conversations as cv
from agent_friday.services import core_memory as cm
from agent_friday.services import corrections as co
from agent_friday.services import prompt_freeze
from agent_friday.services import user_model as um

FIELDS = {"id", "at", "by", "store", "action", "claim", "correction", "reason", "sources",
          "affected", "repair", "restores", "regression_test"}


def _words(text):
    return {w for w in re.findall(r"[a-z0-9]+", str(text).lower()) if len(w) > 2}


class FakeCollection:
    def __init__(self):
        self.rows = {}

    def count(self):
        return len(self.rows)

    def upsert(self, ids, documents, metadatas, embeddings=None):
        for i, d, m in zip(ids, documents, metadatas):
            self.rows[str(i)] = [d, dict(m)]

    def query(self, query_texts=None, query_embeddings=None, n_results=5, where=None, include=None):
        q = _words((query_texts or [""])[0])
        role = None
        if isinstance(where, dict):
            role = where.get("role") if isinstance(where.get("role"), str) else None
        scored = []
        for i, (d, m) in self.rows.items():
            if role and m.get("role") != role:
                continue
            scored.append((1.0 - len(q & _words(d)) / max(1, len(q)), i, d, m))
        scored.sort(key=lambda s: s[0])
        top = scored[:max(1, int(n_results))]
        return {"ids": [[s[1] for s in top]], "documents": [[s[2] for s in top]],
                "metadatas": [[dict(s[3]) for s in top]], "distances": [[s[0] for s in top]]}

    def get(self, ids=None, include=None, where=None, limit=None):
        keys = [str(i) for i in ids if str(i) in self.rows] if ids is not None else list(self.rows)
        return {"ids": keys, "documents": [self.rows[k][0] for k in keys],
                "metadatas": [dict(self.rows[k][1]) for k in keys]}

    def update(self, ids, metadatas=None, documents=None):
        for i, m in zip(ids, metadatas or []):
            if str(i) in self.rows:
                self.rows[str(i)][1] = dict(m)

    def delete(self, ids=None):
        for i in ids or []:
            self.rows.pop(str(i), None)


@pytest.fixture
def world(tmp_path, monkeypatch):
    from agent_friday.conversation_memory import ConversationMemory
    from agent_friday.services import model_router
    monkeypatch.setattr(cm, "_dir", lambda: tmp_path / "core_memory")
    monkeypatch.setattr(cm, "_MIGRATION_CHECKED", {})
    monkeypatch.setattr(co, "_dir", lambda: tmp_path / "corrections")
    monkeypatch.setattr(um, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(um, "DB_PATH", tmp_path / "user_model.db")
    monkeypatch.setattr(cv, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(fts, "_BUILT", {})
    monkeypatch.setattr(fts, "BACKGROUND_BUILD", False)
    mem = ConversationMemory(persist_dir=tmp_path / "conv_mem")
    mem._collection = FakeCollection()
    monkeypatch.setattr(mem, "_assert_embedding_space", lambda: None)
    monkeypatch.setattr(mem, "_shared_query_ok", lambda: False)
    monkeypatch.setattr(model_router, "_get_conversation_memory", lambda: mem)
    prompt_freeze.reset()
    yield mem
    prompt_freeze.reset()


def _said(mem, cid, text, role="user"):
    m = cv.append(cid, {"role": role, "text": text, "meta": {"kind": "turn", "via": "chat"}})
    mid = m["id"] if isinstance(m, dict) and m.get("id") else \
        [x for x in cv._stored_messages(cid) if x.get("text") == text][-1]["id"]
    mem.index(text, role, session_id="2026-09-01", msg_id=mid)
    return mid


def _recalled_ids(mem, q):
    return {h["id"] for h in mem.search(q, n=10)}


def _raw(tmp_path):
    p = tmp_path / "corrections" / "corrections.jsonl"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def test_a_card_correction_writes_one_event_with_every_field(world, tmp_path):
    mem = world
    cid = cv.create("Home")["id"]
    old = _said(mem, cid, "I live at 14 Elm Street in Harwich, by the harbour.")
    fts.ensure_built()
    cm.add("user", "Lives at 14 Elm Street, Harwich.", actor="owner")
    res = cm.owner_set("user", 0, "Lives at 3 Quay Lane, Harwich.", reason="I moved in August")
    assert res["ok"] and res["superseded_turns"] == 1 and res.get("correction_id")
    evs = co.events()
    assert len(evs) == 1, "one owner correction is one event"
    ev = evs[0]
    assert FIELDS <= set(ev), FIELDS - set(ev)
    assert ev["id"] == res["correction_id"] and ev["by"] == "owner"
    assert ev["store"] == "card" and ev["action"] == "corrected"
    assert ev["claim"] == "Lives at 14 Elm Street, Harwich."
    assert ev["correction"] == "Lives at 3 Quay Lane, Harwich."
    assert ev["reason"] == "I moved in August"
    assert "card:user#0" in ev["sources"] and "msg:" + old in ev["sources"]
    assert "msg:" + old in ev["affected"]
    assert ev["repair"]["superseded_turns"] == 1
    assert ev["regression_test"] is False and ev["restores"] == []
    raw = _raw(tmp_path)
    assert raw and "Elm Street" not in raw and "Quay Lane" not in raw, "the claim sits in the log only sealed"


def test_superseded_rows_link_to_the_event_and_the_original_stays_in_history(world):
    mem = world
    cid = cv.create("Home")["id"]
    old = _said(mem, cid, "I live at 14 Elm Street in Harwich, by the harbour.")
    fts.ensure_built()
    cm.add("user", "Lives at 14 Elm Street, Harwich.", actor="owner")
    eid = cm.owner_set("user", 0, "Lives at 3 Quay Lane, Harwich.")["correction_id"]
    assert mem._collection.rows[old][1]["superseded_by"] == eid
    assert mem._collection.rows[old][1]["superseded_reason"] == cm.CARD_CORRECTED
    assert old not in _recalled_ids(mem, "where do I live elm street harwich"), "not in recall"
    hist = [r for r in mem.get_session("2026-09-01") if "Elm Street" in (r.get("text") or "")]
    assert hist and hist[0]["superseded"] and hist[0]["superseded_by"] == eid, "history shows it, linked"
    why = co.for_item("msg:" + old)
    assert [e["id"] for e in why] == [eid] and why[0]["claim"] == "Lives at 14 Elm Street, Harwich."


def test_a_delete_marked_wrong_is_an_event_and_a_plain_delete_is_not(world):
    mem = world
    cid = cv.create("Diet")["id"]
    old = _said(mem, cid, "Please remember I am allergic to peanuts and cashews.")
    fts.ensure_built()
    cm.add("user", "Dentist is Dr Okafor at Riverside Dental Clinic.", actor="owner")
    cm.add("user", "Allergic to peanuts and cashews.", actor="owner")
    assert cm.owner_delete("user", 0)["ok"]
    assert co.events() == [], "a plain delete leaves no copy of the line anywhere"
    res = cm.owner_delete("user", 0, wrong=True)
    ev = co.get(res["correction_id"])
    assert ev["action"] == "marked_wrong" and ev["store"] == "card"
    assert ev["claim"] == "Allergic to peanuts and cashews." and ev["correction"] == co.REMOVED
    assert ev["reason"] == cm.CARD_WRONG and "msg:" + old in ev["affected"]
    assert mem._collection.rows[old][1]["superseded_by"] == ev["id"]
    journal = [json.loads(ln) for ln in (cm._journal_path()).read_text(encoding="utf-8").splitlines()]
    assert any(r.get("correction_id") == ev["id"] for r in journal), "the card's change journal names the event"


def test_a_restore_is_its_own_event_linked_to_the_original(world):
    mem = world
    cid = cv.create("Diet")["id"]
    old = _said(mem, cid, "Please remember I am allergic to peanuts and cashews.")
    fts.ensure_built()
    cm.add("user", "Allergic to peanuts and cashews.", actor="owner")
    res = cm.owner_delete("user", 0, wrong=True)
    assert cm.restore_superseded(res["superseded_ids"]) == 1
    assert old in _recalled_ids(mem, "allergic peanuts cashews")
    evs = co.events()
    assert [e["action"] for e in evs] == ["restored", "marked_wrong"], "newest first"
    restored, orig = evs
    assert FIELDS <= set(restored)
    assert restored["restores"] == [orig["id"]] and restored["by"] == "owner"
    assert "msg:" + old in restored["affected"] and restored["repair"]["restored_turns"] == 1
    assert orig["restored_by"] == [restored["id"]]
    assert cm.restore_superseded(res["superseded_ids"]) == 0
    assert len(co.events()) == 2, "a restore that puts nothing back records nothing"


def test_friday_s_own_changes_and_non_corrections_write_no_event(world):
    mem = world
    cid = cv.create("Boat")["id"]
    _said(mem, cid, "My sailing boat Wren is moored at Harwich marina.")
    assert cm.add("user", "Sailing boat Wren moored at Harwich marina.", actor="friday")["ok"]
    changed = cm.replace("user", "Sailing boat", "Sailing boat Wren moored at Ipswich marina.", actor="friday")
    assert cm.undo(changed["change_id"])["ok"]                 # the owner undoes Friday's change
    cm.add("friday", "Answers in short paragraphs when asked for a summary.", actor="friday")
    cm.remove("friday", "short paragraphs", actor="friday")
    cm.add("user", "Walks the dog every morning.", actor="owner")
    cm.owner_set("user", len(cm.entries("user")) - 1, "Walks the dog every morning before work.")  # added detail
    q = cm.queue("user", "replace", old="Walks the dog", text="Walks the dog at noon each day.", source="review")
    cm.decide(q["id"], True)                                    # the owner keeps Friday's suggestion
    assert co.events() == []
    assert co.record("card", "corrected", claim="x", correction="y", by="friday") == ""
    assert co.events() == []


def test_the_prompt_cites_a_recent_correction(world):
    cm.add("user", "Lives at 14 Elm Street, Harwich.", actor="owner")
    cm.add("user", "Keeps bees in the back garden.", actor="owner")
    cm.owner_set("user", 0, "Lives at 3 Quay Lane, Harwich.")
    block = cm.render_prompt()
    assert re.search(r"- Lives at 3 Quay Lane, Harwich\. \[correction:\d{4}-\d{2}-\d{2}\]", block), block
    assert "- Keeps bees in the back garden.\n" in block + "\n" and "bees in the back garden. [correction" not in block
    assert cm.usage("user")["used"] == len("Lives at 3 Quay Lane, Harwich.\nKeeps bees in the back garden.")


def test_keep_as_a_test_stores_the_owner_s_golden_sealed(world, tmp_path):
    cm.add("user", "Allergic to peanuts and cashews.", actor="owner")
    cm.add("user", "Dentist is Dr Okafor at Riverside Dental Clinic.", actor="owner")
    eid = cm.owner_set("user", 1, "Dentist is Dr Adeyemi at Harbour Dental.")["correction_id"]
    assert co.events()[0]["regression_test"] is False and co.events()[0]["golden"] is None
    out = co.set_test(eid, True, "Who is my dentist?", "Dr Adeyemi at Harbour Dental")
    assert out["ok"] and out["regression_test"] is True
    ev = co.get(eid)
    assert ev["regression_test"] is True
    assert ev["golden"] == {"question": "Who is my dentist?", "expected": "Dr Adeyemi at Harbour Dental"}
    assert "Who is my dentist" not in _raw(tmp_path), "the golden is sealed too"
    assert co.set_test(eid, False)["ok"]
    assert co.get(eid)["regression_test"] is False and co.get(eid)["golden"] is None
    assert co.set_test("corr-000000000000", True)["ok"] is False
    rows = [json.loads(ln) for ln in _raw(tmp_path).splitlines()]
    assert [r["kind"] for r in rows] == ["event", "test_flag", "test_flag"], "append only"


def test_forgetting_a_person_removes_the_corrections_that_name_them(world):
    from agent_friday.services import forget_registry as fr
    cm.add("user", "Dentist is Dr Okafor at Riverside Dental Clinic.", actor="owner")
    cm.owner_set("user", 0, "Dentist is Dr Adeyemi at Harbour Dental.")
    assert "corrections" in fr.registered()
    assert co.people_find({"okafor"}, set()) == 1
    assert co.people_purge({"okafor"}, set()) == 1
    assert co.events() == []


# ── review fixes ──────────────────────────────────────────────────────────────
def _lock_open(monkeypatch):
    """The keystore cannot open anything (it is locked)."""
    from agent_friday.services import credential_store as cs

    def _locked(_blob):
        raise RuntimeError("keystore locked")
    monkeypatch.setattr(cs, "unprotect", _locked)


def test_reason_and_naming_ids_are_sealed_and_the_why_path_still_opens_them(world, tmp_path):
    eid = co.record("wiki", "corrected", claim="old", correction="new", reason="Dr Okafor retired",
                    sources=["wiki:people/Dr Okafor.md#Clinic", "msg:m-1", "card:user#2"],
                    affected=["wiki:people/Dr Okafor.md", "friday:okafor_notes.json", "msg:m-1"],
                    restores=["corr-0123456789ab"])
    raw = _raw(tmp_path)
    assert "Okafor" not in raw and "okafor" not in raw, "the reason, the page path and the section are sealed"
    row = json.loads(raw.splitlines()[0])
    assert "msg:m-1" in row["sources"] and "card:user#2" in row["sources"], "link ids stay plain"
    assert row["restores"] == ["corr-0123456789ab"]
    ev = co.get(eid)
    assert ev["reason"] == "Dr Okafor retired"
    assert ev["sources"] == ["wiki:people/Dr Okafor.md#Clinic", "msg:m-1", "card:user#2"]
    assert "friday:okafor_notes.json" in ev["affected"]
    assert [e["id"] for e in co.for_item("wiki:people/Dr Okafor.md")] == [eid], "why opens the sealed id"
    assert [e["id"] for e in co.for_item("msg:m-1")] == [eid]
    shut = co.events(open_text=False)[0]
    assert "reason" not in shut and shut["sources"] == ["msg:m-1", "card:user#2"]


def test_forget_person_searches_every_field_of_a_row(world):
    co.record("wiki", "corrected", claim="the clinic moved", correction="it moved again",
              reason="Adeyemi told me")
    co.record("wiki", "corrected", claim="the dentist", correction="a new dentist",
              sources=["wiki:people/Adeyemi.md#Clinic"])
    co.record("card", "corrected", claim="bees in the garden", correction="bees on the roof")
    assert co.people_find({"adeyemi"}, set()) == 2
    assert co.people_purge({"adeyemi"}, set()) == 2
    assert [e["claim"] for e in co.events()] == ["bees in the garden"]


def test_forget_person_with_a_locked_keystore_says_it_could_not_check(world, monkeypatch, tmp_path):
    co.record("card", "corrected", claim="Dentist is Dr Okafor.", correction="Dentist is Dr Adeyemi.")
    before = _raw(tmp_path)
    _lock_open(monkeypatch)
    with pytest.raises(co.CorrectionsLocked):
        co.people_find({"okafor"}, set())             # never a silent 0
    with pytest.raises(co.CorrectionsLocked):
        co.people_purge({"okafor"}, set())
    assert _raw(tmp_path) == before, "what could not be checked is not touched"
    # forget_registry turns the raise into "could not check" (-1 and the reason), as for memory_proposals.
    assert co.people_find(set(), set()) == 0, "no names to look for is still a plain 0"


def test_keep_as_a_test_with_a_locked_keystore_keeps_nothing_and_says_so(world, monkeypatch, tmp_path):
    from agent_friday.services import credential_store as cs
    from agent_friday.services.keystore import KeystoreLocked
    eid = co.record("card", "corrected", claim="Dentist is Dr Okafor.", correction="Dentist is Dr Adeyemi.")

    def _locked(_data):
        raise KeystoreLocked("locked")
    monkeypatch.setattr(cs, "protect", _locked)
    out = co.set_test(eid, True, "Who is my dentist?", "Dr Adeyemi")
    assert out["ok"] is False and out["locked"] is True
    assert [json.loads(ln)["kind"] for ln in _raw(tmp_path).splitlines()] == ["event"], "no flag row"


def test_the_owner_can_delete_one_correction_record(world, tmp_path):
    cm.add("user", "Dentist is Dr Okafor at Riverside Dental Clinic.", actor="owner")
    cm.add("user", "Keeps bees in the back garden.", actor="owner")
    gone = cm.owner_set("user", 0, "Dentist is Dr Adeyemi at Harbour Dental.")["correction_id"]
    kept = cm.owner_set("user", 1, "Keeps hens in the back garden.")["correction_id"]
    assert co.set_test(gone, True, "Who is my dentist?", "Dr Adeyemi")["ok"]
    out = co.delete(gone)
    assert out["ok"] and out["removed"] == 2, "the event and its flag row"
    assert [e["id"] for e in co.events()] == [kept]
    assert "Okafor" not in _raw(tmp_path) and gone not in _raw(tmp_path), "removed, not tombstoned"
    assert co.delete(gone)["ok"] is False


def test_a_plain_owner_delete_removes_the_card_corrections_that_claim_the_line(world):
    line = "Dentist is Dr Okafor at Riverside Dental Clinic."
    old = co.record("card", "corrected", claim=line, correction="Dentist is Dr Adeyemi.")
    wiki = co.record("wiki", "corrected", claim=line, correction="x")
    cm.add("user", line, actor="owner")
    cm.add("user", "Allergic to peanuts and cashews.", actor="owner")
    assert cm.owner_delete("user", 0)["ok"]
    assert [e["id"] for e in co.events()] == [wiki], "a full delete leaves no card trace; the wiki's is its own"
    older = co.record("card", "corrected", claim="Allergic to peanuts and cashews.", correction="y")
    res = cm.owner_delete("user", 0, wrong=True)                # a correction keeps its history
    assert {older, res["correction_id"], wiki} == {e["id"] for e in co.events()}
    assert old not in {e["id"] for e in co.events()}


def test_a_wrong_delete_names_its_event_in_the_journal_row_it_writes(world, monkeypatch):
    rows = []
    real = cm._journal
    monkeypatch.setattr(cm, "_journal", lambda row: rows.append(dict(row)) or real(row))
    writes = []
    real_write = cm._write_jsonl
    monkeypatch.setattr(cm, "_write_jsonl",
                        lambda p, r: writes.append((p, [dict(x) for x in r])) or real_write(p, r))
    cm.add("user", "Allergic to peanuts and cashews.", actor="owner")
    rows.clear()
    writes.clear()
    res = cm.owner_delete("user", 0, wrong=True)
    assert rows and rows[-1]["op"] == "remove" and rows[-1]["correction_id"] == res["correction_id"]
    # The remove row names its event from its first write (never added by a later rewrite). The
    # journal may still be rewritten after it: the deleted line's own earlier rows, matched by
    # their keyed line_ref (M11), leave the journal with the line.
    removes = [[x for x in rows_ if x.get("op") == "remove"] for p, rows_ in writes if p == cm._journal_path()]
    assert removes and all(rm and rm[-1].get("correction_id") == res["correction_id"] for rm in removes)


@pytest.mark.parametrize("how", ["edit", "wrong"])
def test_a_failed_record_leaves_nothing_pointing_at_a_missing_event(world, monkeypatch, how):
    mem = world
    cid = cv.create("Home")["id"]
    old = _said(mem, cid, "I live at 14 Elm Street in Harwich, by the harbour.")
    fts.ensure_built()
    cm.add("user", "Lives at 14 Elm Street, Harwich.", actor="owner")

    def _boom(_row):
        raise OSError("disk full")
    monkeypatch.setattr(co, "_append", _boom)
    res = cm.owner_set("user", 0, "Lives at 3 Quay Lane, Harwich.") if how == "edit" \
        else cm.owner_delete("user", 0, wrong=True)
    assert res["ok"] and "correction_id" not in res and res["superseded_turns"] == 1
    journal = [json.loads(ln) for ln in cm._journal_path().read_text(encoding="utf-8").splitlines()]
    # Every add/replace row carries its keyed line_ref (M11, "why do you know this"); only the event link goes.
    assert not any(r.get("correction_id") for r in journal)
    meta = mem._collection.rows[old][1]
    assert meta["superseded"] == "1" and meta["superseded_by"] == "", "still retired, no dangling link"
    assert co.events() == []


# ── edit and keep (live's NS-15.5-1) is a correction too ──────────────────────
def test_edit_and_keep_of_a_card_suggestion_is_a_correction_event(world, tmp_path):
    mem = world
    cid = cv.create("Office")["id"]
    said = _said(mem, cid, "I work from the Leeds office most weeks of the year.")
    fts.ensure_built()
    q = cm.queue("user", "add", text="Prefers long detailed answers.", source="dream:2026-10-01")
    res = cm.decide(q["id"], True, text="Prefers short answers, bullet points first.")
    assert res["ok"] and res.get("edited") and res.get("correction_id"), res
    evs = co.events()
    assert [e["id"] for e in evs] == [res["correction_id"]]
    ev = evs[0]
    assert ev["store"] == "card" and ev["action"] == "corrected" and ev["by"] == "owner"
    assert ev["correction"] == "Prefers short answers, bullet points first."
    assert ev["claim"] == "", "the suggested wording is kept nowhere, not even sealed"
    assert ev["repair"].get("suggestion_edited") == 1 and ev["repair"].get("superseded_turns") == 0
    assert "card:user#0" in ev["affected"] and ("suggestion:" + q["id"]) in ev["sources"]
    assert "long detailed" not in _raw(tmp_path) and "Prefers short" not in _raw(tmp_path)
    # Approving a suggestion never retires what was said.
    assert mem._collection.rows[said][1].get("superseded") != "1"
    journal = [json.loads(ln) for ln in cm._journal_path().read_text(encoding="utf-8").splitlines()]
    assert journal[-1].get("via") == "edited_suggestion"
    assert journal[-1].get("correction_id") == res["correction_id"] and journal[-1].get("line_ref")
    assert re.search(r"- Prefers short answers, bullet points first\. \[correction:\d{4}-\d{2}-\d{2}\]",
                     cm.render_prompt())


def test_edit_and_keep_of_a_replace_suggestion_is_a_correction_event(world):
    cm.add("user", "Works from the Leeds office.", actor="owner")
    q = cm.queue("user", "replace", old="Leeds", text="Works from the York office.", source="review")
    res = cm.decide(q["id"], True, text="Works from home on Fridays, York office otherwise.")
    assert res["ok"] and res.get("correction_id"), res
    ev = co.get(res["correction_id"])
    assert ev["correction"] == "Works from home on Fridays, York office otherwise." and ev["claim"] == ""
    assert ev["repair"].get("suggestion_edited") == 1
    # A plain Keep, or an "edit" that leaves the wording as suggested, records nothing.
    q2 = cm.queue("user", "add", text="Plays chess on Sundays.", source="review")
    assert "correction_id" not in cm.decide(q2["id"], True, text="Plays chess on Sundays.")
    assert [e["id"] for e in co.events()] == [res["correction_id"]]


def test_a_proposal_edited_then_kept_names_the_proposal(world):
    # memory_proposals.approve passes the fact id when the owner edited it.
    res = cm.add("user", "Likes a one-page summary first.", actor="owner", dedupe=True,
                 via="edited_suggestion", suggestion_edit=["fact:f-123"])
    assert res["ok"] and res.get("correction_id")
    ev = co.get(res["correction_id"])
    assert "fact:f-123" in ev["sources"] and ev["claim"] == ""
    # Without an edit the owner's add is no correction.
    assert "correction_id" not in cm.add("user", "Keeps bees.", actor="owner")
    assert len(co.events()) == 1


def test_an_edit_and_keep_whose_record_fails_leaves_no_dangling_link(world, monkeypatch):
    def _boom(_row):
        raise OSError("disk full")
    monkeypatch.setattr(co, "_append", _boom)
    q = cm.queue("user", "add", text="Prefers tea.", source="review")
    res = cm.decide(q["id"], True, text="Prefers green tea, no sugar.")
    assert res["ok"] and "correction_id" not in res
    assert cm.entries("user") == ["Prefers green tea, no sugar."]
    journal = [json.loads(ln) for ln in cm._journal_path().read_text(encoding="utf-8").splitlines()]
    # Every add/replace row carries its keyed line_ref (M11, "why do you know this"); only the event link goes.
    assert not any(r.get("correction_id") for r in journal)
    assert co.events() == []
