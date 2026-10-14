"""The re-review of 52de5e299 (Meetings, MT-2): R1, the kind alias, sealed suggestions, a word-bounded forget,
settling forget-person for a meeting still being made, the Friday bullet, and elision that survives a restart.
Each test fails on 52de5e299. Names and numbers are synthetic."""
from __future__ import annotations

import json
import sqlite3
import threading

import pytest

from agent_friday.services import meeting_archive as ma
from agent_friday.services import meeting_capture as mc
from test_meeting_archive_critic_fixes import (A, B, _mgr, _store, _tools, env, proposals_db,  # noqa: F401
                                               record)

CAND = {"text": "Priya came up in a meeting.", "category": "relationship", "span": {"i": 1, "t": 20}}


# ── R1 ────────────────────────────────────────────────────────────────────────

def test_deleting_a_meeting_never_runs_the_history_wide_sweep_and_leaves_unrelated_chats_alone(env, monkeypatch):
    from agent_friday.services import conversations
    from agent_friday.services.library import forget, sweep
    phrase = "let me know if you have any questions about it"
    fx = dict(A, lines=A["lines"] + [[200, "Them", "Fine, " + phrase + " later."]])
    ma.index_meeting(record(fx))
    doc = _store().find_document(str(ma.path_for(A["id"], "transcript")))

    def no_sweep(*a, **k):
        raise AssertionError("a meeting's words were swept out of every store")
    monkeypatch.setattr(sweep, "sweep_all", no_sweep)
    monkeypatch.setattr(forget, "cited_conversations", lambda st, doc_id: {"c1": {"m1"}} if doc_id == doc["id"] else {})
    chat = [{"role": "user", "content": "Please " + phrase + ", thanks."},
            {"role": "assistant", "content": "Forty thousand dollars for Lisbon [lib:%d#1]." % doc["id"]},
            {"role": "assistant", "content": "A different reply that says " + phrase + " too."}]
    seen = []

    def rewrite(cid, fn):
        seen.append(cid)
        for i, m in enumerate(chat):
            chat[i] = fn(dict(m))
        return 1
    monkeypatch.setattr(conversations, "rewrite", rewrite)
    assert ma.remove_meeting(A["id"]) == 2
    assert seen == ["c1"]
    assert phrase in chat[0]["content"], "the owner's own message is never rewritten"
    assert "[lib:%d#1]" % doc["id"] not in chat[1]["content"], "the reply that cited the meeting loses the footnote"


def test_the_off_choice_is_a_click_and_clears_the_archive_off_the_request_thread(env, monkeypatch):
    from flask import Flask
    from agent_friday.routes.meetings import meetings_bp
    from agent_friday.services import screen_click
    app = Flask(__name__)
    app.config.update(TESTING=True)
    app.register_blueprint(meetings_bp)
    c = app.test_client()
    saved = []
    monkeypatch.setattr(ma, "set_choice", lambda ch, **k: saved.append((ch, k.get("background"))) or {"choice": ch, "documents": 0})
    monkeypatch.setattr(screen_click, "screen_session", lambda request: False)
    assert c.post("/api/meetings/archive", json={"choice": "off"}).status_code == 403 and saved == []
    monkeypatch.setattr(screen_click, "screen_session", lambda request: True)
    assert c.post("/api/meetings/archive", json={"choice": "off"}).status_code == 200 and saved == [("off", True)]
    text = (ma.__file__ and open(ma.__file__, encoding="utf-8").read())
    assert "_clear_after_off" in text


def test_set_choice_off_returns_before_the_rows_are_removed_when_asked_to_run_in_the_background(env, monkeypatch):
    from agent_friday import core
    monkeypatch.setattr(core, "_save_settings", lambda d: env.update(choice=d["meeting_archive"]))
    done, release = threading.Event(), threading.Event()

    def slow(store=None):
        release.wait(5)
        done.set()
    monkeypatch.setattr(ma, "_clear_after_off", slow)
    out = ma.set_choice("off", background=True)
    assert out["choice"] == "off" and not done.is_set(), "the request did not wait for the cleanup"
    release.set()
    assert done.wait(5)


# ── H3: the kind alias ────────────────────────────────────────────────────────

def test_the_meeting_kind_aliases_are_named_only_to_a_reader_that_may_hear_them(env, monkeypatch):
    from agent_friday.services import agent, desktop_targets
    seen = []
    monkeypatch.setattr(desktop_targets, "open_on_desktop", lambda kind, **k: seen.append(k["name_items"]) or {"text": "ok"})
    for kind in ("call", "meetings", "Meeting"):
        _m, undo = _tools(monkeypatch, local=False)
        try:
            agent._tool_navigate_to({"kind": kind, "query": "x"})
        finally:
            undo()
        assert seen[-1] is False, kind


# ── H2 residual ───────────────────────────────────────────────────────────────

def _raw(mp):
    con = sqlite3.connect(str(mp.DB_PATH))
    try:
        return con.execute("SELECT text, evidence, status FROM proposed_facts").fetchall()
    finally:
        con.close()


def test_a_pending_meeting_suggestion_is_sealed_in_the_table_and_blanked_once_decided(env, monkeypatch, proposals_db):
    from agent_friday.services import user_model
    from agent_friday.services.knowledge_graph import integration
    monkeypatch.setattr(user_model, "note_fact", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(integration, "ingest_fact", lambda *a, **k: None)
    proposals_db.propose_from_meeting([CAND, dict(CAND, text="Sell the house to the Jensens.", category="project")],
                                      A["id"], "2026-10-09", "m")
    raw = _raw(proposals_db)
    assert raw and all("Priya" not in t and "Jensens" not in t for t, _e, _s in raw), "no meeting words in the plain table"
    shown = proposals_db.pending()
    assert {p["text"] for p in shown} == {CAND["text"], "Sell the house to the Jensens."}
    a, b = shown[0]["fact_id"], shown[1]["fact_id"]
    assert proposals_db.approve([a])["facts"][0]["text"] in {CAND["text"], "Sell the house to the Jensens."}
    proposals_db.reject([b])
    assert all(t == "" and e == "" for t, e, _s in _raw(proposals_db)), "a decided suggestion keeps no text"
    # the same suggestion is not made twice while it waits
    proposals_db.propose_from_meeting([CAND], B["id"], "2026-10-09", "m")
    proposals_db.propose_from_meeting([CAND], B["id"], "2026-10-09", "m")
    assert len([p for p in proposals_db.pending() if p["provenance"]["meeting_id"] == B["id"]]) == 1


def test_forgetting_tom_does_not_delete_a_suggestion_about_tomorrow(env, proposals_db):
    proposals_db.propose_from_meeting([dict(CAND, text="Tom came up in a meeting.")], A["id"], "2026-10-09", "m")
    proposals_db.propose_from_meeting([dict(CAND, text="Ship it tomorrow, for the custom build.", category="project")],
                                      A["id"], "2026-10-09", "m")
    assert proposals_db.people_find({"Tom"}, set()) == 1
    assert proposals_db.people_purge({"Tom"}, set()) == 1
    assert [p["text"] for p in proposals_db.pending()] == ["Ship it tomorrow, for the custom build."]


# ── M4: a meeting being made when forget-person ran ───────────────────────────

def test_a_meeting_being_transcribed_when_a_person_is_forgotten_is_redacted_when_it_settles(env, monkeypatch):
    from agent_friday.services import forget_person
    m = _mgr(monkeypatch)
    live = record(A, state="transcribing")
    m._save(live)
    monkeypatch.setattr(mc, "_manager", m)
    monkeypatch.setattr(forget_person, "forgotten_names", lambda: {"priya"})
    assert mc.people_find({"Priya"}, set()) == 0 and mc.people_purge({"Priya"}, set()) == 0, "left for the worker's own save"
    assert "Priya" in json.dumps(mc._load(A["id"]))
    live["state"] = "transcribed"
    m._save(live)
    assert "priya" not in json.dumps(mc._load(A["id"])).lower()


# ── L5 ────────────────────────────────────────────────────────────────────────

def test_a_bullet_friday_added_does_not_widen_what_the_notes_may_name_and_is_filed_as_hers(env):
    from agent_friday.services import meeting_workspace as mw
    m = record(A)
    lines = mw.spoken(m)
    # A name the transcript never says, which the entity check reads as a name when it opens a line
    # (test_meeting_workspace: "Rowan owns the venue." -> ["rowan"]).
    assert "rowan" not in " ".join(ln["text"] for ln in lines).lower()
    item = [{"bullet": 1, "text": "Rowan will send the contract.", "spans": [1]}]
    owner = [{"id": 1, "t": 5, "text": "Rowan to send the contract"}]
    hers = [{"id": 1, "t": 5, "text": "Rowan to send the contract", "by": "friday"}]
    kept, _left = mw.validate_expansion(item, lines, owner)
    assert len(kept) == 1
    kept, left = mw.validate_expansion(item, lines, hers)
    assert kept == [] and "rowan" in left[0]["why"]
    m2 = record(A, pad=hers, polished={"status": "done", "lines": [{"bullet": 1, "text": "The launch is set.", "spans": [{"i": 1, "t": 20}]}]})
    assert any(b["text"].startswith("From a bullet Friday added: ") for b in mw.notes_blocks(m2))


# ── M1: spoken answers and a restart ──────────────────────────────────────────

def test_a_reply_saved_after_a_turn_that_read_a_meeting_is_elided_even_with_no_footnote_and_after_a_restart(env):
    from agent_friday.services.library import cite, usage
    cite._MEETING_REPLIES = None
    reply = "They agreed to keep the budget where it was, and Jonas lost the argument."
    usage.mark_meeting("c-voice")
    assert cite.turn_used_library(None, "c-voice") is False       # a spoken turn: no trace, no footnote
    cite.remember_library_reply(reply)
    assert cite.quotes_a_meeting(reply) is True
    cite._MEETING_REPLIES = None                                   # a restart: only the sealed file remains
    msgs = [{"role": "assistant", "content": reply}]
    assert cite.elide_for_cloud(msgs, {"library_cloud_answers": True}) == 1 and msgs[0]["content"] == cite.ELIDED
    other = [{"role": "assistant", "content": "Plain reply with no footnote."}]
    assert cite.elide_for_cloud(other, {"library_cloud_answers": True}) == 0
