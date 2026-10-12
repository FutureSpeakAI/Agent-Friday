"""The critic's conditions on 82e582f10 for the Meetings port (MT-2). Each test here fails on that commit.

H1 the knowledge graph never reads a meeting; H2 proposals hold no title or quoted line and leave with the
meeting; H3 no title to a reader that is not local and private; H4 a cloud-tagged Ollama loop is a cloud reader;
M1 a quote of a meeting is elided from cloud history whatever the cloud setting; M2 deleting a meeting scrubs
the chats that quoted it; M3 keeping a meeting's suggestion is a click on the screen; M4 forget-person redacts
the record; L1 the archive choice recommends neither answer; L3 a failed settings read removes nothing; L5 a
bullet Friday's tool adds is marked hers; L7 a Library of meetings is not "empty". Names, places and numbers
are synthetic (tests/fixtures/meeting_synthetic.json).
"""
from __future__ import annotations

import contextlib
import json
import types
from pathlib import Path

import pytest

from agent_friday.services import meeting_archive as ma
from agent_friday.services import meeting_capture as mc
from tests.library_fixtures import install_fake_encoder, isolate_library, release_library

ROOT = Path(__file__).resolve().parents[2]
FIX = json.loads((ROOT / "tests" / "fixtures" / "meeting_synthetic.json").read_text(encoding="utf-8"))["meetings"]
A, B = FIX[0], FIX[1]
QUESTION = "what did we decide about the Lisbon budget"


def record(fx, **kw):
    segs = [{"t": 0, "kind": "consent", "text": "Recording started."}]
    segs += [{"t": t, "kind": "speech", "speaker": who, "text": text} for t, who, text in fx["lines"]]
    rec = {"id": fx["id"], "title": fx["title"], "event_id": "", "started_at": "2026-10-09T10:00:00",
           "ended_at": "2026-10-09T10:05:00", "duration_s": 300, "state": "done", "stop_reason": "owner",
           "source": "microphone", "system_audio": False, "keep_audio": False, "segments": segs,
           "summary": {"status": "done", "model": "notes-model", "local": True, "notes": ["Reviewed the plan."],
                       "decisions": list(fx["decisions"]), "action_items": []},
           "pad": [], "tasks_created": [], "follow_ups": []}
    rec.update(kw)
    return rec


@pytest.fixture
def env(tmp_path, monkeypatch):
    fg, lstore = isolate_library(tmp_path, monkeypatch)
    install_fake_encoder(monkeypatch)
    from agent_friday.services.library import grants, shelf
    monkeypatch.setattr(shelf, "_vault_key", lambda: None)
    monkeypatch.setattr(shelf, "tier_of", lambda title, sample: 1)
    state = {"choice": "on", "tmp": tmp_path}
    monkeypatch.setattr(ma, "_settings", lambda: {"meeting_archive": state["choice"]})
    root = tmp_path / "fh" / "meetings"
    monkeypatch.setattr(mc, "_root", lambda: root)
    lstore.store_for("owner").encrypted = True
    grants._TRACKED_CACHE.clear()
    grants.forget_kept()
    for mid in (A["id"], B["id"]):
        (root / mid).mkdir(parents=True, exist_ok=True)
        (root / mid / "meeting.json.enc").write_bytes(b"x")
    yield state
    grants._TRACKED_CACHE.clear()
    grants.forget_kept()
    release_library(fg, lstore)


def _store():
    from agent_friday.services.library.store import store_for
    return store_for("owner")


def _count():
    return _store().one("SELECT count(*) n FROM documents")["n"]


def _tools(monkeypatch, *, local, surface=""):
    from agent_friday.services import agent
    from agent_friday.services.library import tools
    monkeypatch.setattr(tools, "_loop_is_local", lambda: local)
    monkeypatch.setattr(tools, "_settings", lambda: {"library_cloud_answers": True})
    tok = agent._CURRENT_SURFACE.set(surface)
    return tools, lambda: agent._CURRENT_SURFACE.reset(tok)


def _mgr(monkeypatch, *, seat=None, proposer=None, purge=None):
    from agent_friday.services import local_scheduler

    @contextlib.contextmanager
    def fake_work(label, cls=None, needs=None, quiet=True):
        yield
    monkeypatch.setattr(local_scheduler, "work", fake_work)
    return mc.MeetingManager(
        source_factory=lambda max_s, **k: None, transcriber=lambda pcm: "", seat_resolver=lambda: seat,
        notes_call=lambda s, u, model: {"people": [], "projects": []}, asr_check=lambda: "",
        lock_detector=lambda: None, event_lookup=lambda: None, notifier=lambda *a: None,
        scheduler=lambda mid: types.SimpleNamespace(release=lambda: None), partials_factory=lambda: None,
        ledger=lambda *a, **k: None, retention=lambda: "", archive_index=lambda m: None,
        archive_remove=lambda mid: None, proposer=proposer or (lambda c, mid, day, model: len(c)),
        proposal_purge=purge or (lambda mid: 0), tick_s=0.01, chunk_s=1.0)


@pytest.fixture
def proposals_db(tmp_path, monkeypatch):
    from agent_friday.services import memory_proposals as mp
    monkeypatch.setattr(mp, "FRIDAY_DIR", tmp_path / "fd")
    monkeypatch.setattr(mp, "DB_PATH", tmp_path / "fd" / "dreams.db")
    return mp


CAND = {"text": "Priya came up in a meeting.", "category": "relationship", "span": {"i": 1, "t": 20}}


# ── H1 ────────────────────────────────────────────────────────────────────────

def test_the_knowledge_graph_never_reads_a_meeting_and_drops_what_it_took(env, monkeypatch):
    from agent_friday.services.knowledge_graph import indexer
    ma.index_meeting(record(A))
    gone = []
    monkeypatch.setattr(indexer, "library_learning_on", lambda: True)
    monkeypatch.setattr(indexer, "purge_library_documents", lambda ids: gone.append(sorted(ids)) or {})
    assert list(indexer._library_chunks()) == [], "no passage of a meeting is offered to the graph"
    assert gone and len(gone[0]) == 2, "whatever an earlier pass took from the meeting's documents is purged"


# ── H2 ────────────────────────────────────────────────────────────────────────

def test_a_proposal_carries_no_title_and_no_quoted_line():
    from agent_friday.services import meeting_workspace as mw
    m = record(A)
    cands, _left = mw.proposal_candidates(
        m, {"people": [{"name": "Priya", "line": None, "role": "finance"}], "projects": []}, mw.spoken(m))
    assert cands
    for c in cands:
        assert A["title"] not in c["text"] and A["title"] not in c["evidence"]
        assert all(t not in c["evidence"] for _t, _w, t in A["lines"]), "no transcript sentence is copied out"


def test_deleting_a_meeting_deletes_its_proposals_and_none_is_written_after_it(env, monkeypatch, proposals_db):
    m = _mgr(monkeypatch, purge=proposals_db.purge_meeting)
    m._save(record(A))
    proposals_db.propose_from_meeting([CAND], A["id"], "2026-10-09", "m")
    proposals_db.propose_from_meeting([dict(CAND, text="Another thing came up.")], B["id"], "2026-10-09", "m")
    assert len(proposals_db.pending()) == 2
    m.delete(A["id"])
    assert [p["provenance"]["meeting_id"] for p in proposals_db.pending()] == [B["id"]]


def test_a_suggestion_still_being_made_when_the_meeting_is_deleted_leaves_nothing(env, monkeypatch, proposals_db):
    holder = {}

    def proposer(cands, mid, day, model):
        holder["m"].delete(mid)                                   # the owner deletes it while the model ran
        return proposals_db.propose_from_meeting(cands, mid, day, model)
    holder["m"] = m = _mgr(monkeypatch, proposer=proposer, purge=proposals_db.purge_meeting)
    m._save(record(B))
    out = m.propose_memory(B["id"])
    assert proposals_db.pending() == [] and out["proposed"] == 0


def test_saying_no_and_forget_person_clear_meeting_proposals(env, monkeypatch, proposals_db):
    from agent_friday import core
    proposals_db.propose_from_meeting([CAND], A["id"], "2026-10-09", "m")
    assert proposals_db.people_find({"Priya"}, set()) == 1
    assert proposals_db.people_purge({"Priya"}, set()) == 1 and proposals_db.pending() == []
    proposals_db.propose_from_meeting([CAND], A["id"], "2026-10-09", "m")
    monkeypatch.setattr(core, "_save_settings", lambda d: env.update(choice=d["meeting_archive"]))
    ma.set_choice("off")
    assert proposals_db.pending() == []
    from agent_friday.services import forget_registry
    forget_registry._DEFAULTS_LOADED = False
    forget_registry._STORES.clear()
    forget_registry._ensure_defaults()
    assert {"memory_proposals", "meetings"} <= set(forget_registry.registered())


# ── M3 ────────────────────────────────────────────────────────────────────────

def test_keeping_a_meeting_suggestion_needs_a_click_on_the_screen(env, monkeypatch, proposals_db):
    from flask import Flask
    from agent_friday.routes.memory_proposals import memory_proposals_bp
    from agent_friday.services import screen_click
    app = Flask(__name__)
    app.config.update(TESTING=True)
    app.secret_key = "test"                 # login_required keeps a session on loopback
    app.register_blueprint(memory_proposals_bp)
    c = app.test_client()
    proposals_db.propose_from_meeting([CAND], A["id"], "2026-10-09", "m")
    fid = proposals_db.pending()[0]["fact_id"]
    assert proposals_db.any_from_meeting([fid]) is True
    called = []
    monkeypatch.setattr(proposals_db, "approve", lambda ids: called.append(ids) or {"ok": True, "approved": len(ids)})
    monkeypatch.setattr(screen_click, "screen_session", lambda request: False)
    assert c.post("/api/memory/proposals/approve", json={"fact_ids": [fid]}).status_code == 403 and called == []
    monkeypatch.setattr(screen_click, "screen_session", lambda request: True)
    assert c.post("/api/memory/proposals/approve", json={"fact_ids": [fid]}).status_code == 200 and called == [[fid]]


# ── H3 ────────────────────────────────────────────────────────────────────────

def test_the_library_stage_shows_a_meeting_document_without_its_title():
    from agent_friday.services import screen_stage as ss
    st = ss.bound_stage({"workspace": "library", "rev": 1, "items": [
        {"ref": "lib:1", "n": 1, "title": "Meeting transcript: Divorce lawyer call (2026-10-09)", "facets": {}},
        {"ref": "lib:2", "n": 2, "title": "Meeting notes: Layoffs plan (2026-10-09)", "facets": {}},
        {"ref": "lib:3", "n": 3, "title": "Quarterly budget", "facets": {}}],
        "selection": {"refs": ["lib:1"], "label": "Meeting notes: Layoffs plan (2026-10-09)"}}, now=1.0)
    assert [i["title"] for i in st["items"]] == ["", "", "Quarterly budget"]
    assert st["selection"]["label"] == ""


def test_navigate_to_names_a_meeting_only_to_a_reader_that_may_hear_it(env, monkeypatch):
    from agent_friday.services import agent, desktop_targets
    seen = []
    monkeypatch.setattr(desktop_targets, "open_on_desktop",
                        lambda kind, **k: seen.append((kind, k["name_items"])) or {"text": "ok"})
    for local, surface, named in ((True, "", True), (False, "", False), (True, "voice-live-relay", False)):
        _tools_mod, undo = _tools(monkeypatch, local=local, surface=surface)
        try:
            agent._tool_navigate_to({"kind": "meeting", "query": "the launch call"})
        finally:
            undo()
        assert seen[-1] == ("meeting", named), (local, surface)
    _tools_mod, undo = _tools(monkeypatch, local=False)
    try:
        agent._tool_navigate_to({"kind": "email", "query": "x"})
    finally:
        undo()
    assert seen[-1] == ("email", True), "the other kinds are unchanged"


def test_meeting_find_gives_a_relayed_answer_counts_only(env, monkeypatch):
    from agent_friday.services import meeting_tool as mt, meeting_workspace as mw
    monkeypatch.setattr(mw, "recorded", lambda: [{"id": A["id"], "title": "Launch planning", "state": "done",
                                                  "started_at": "2026-10-09T10:00:00", "summary_status": "done"}])
    _tools_mod, undo = _tools(monkeypatch, local=True, surface="voice-live-relay")
    try:
        out = mt.meeting({"action": "find", "query": "launch"})
    finally:
        undo()
    assert "Launch planning" not in out and json.loads(out)["matches"] == 1


# ── H4 ────────────────────────────────────────────────────────────────────────

def _with_loop(provider, model):
    from agent_friday.services import agent
    a, b = agent._CURRENT_PROVIDER.set(provider), agent._CURRENT_MODEL.set(model)
    return lambda: (agent._CURRENT_PROVIDER.reset(a), agent._CURRENT_MODEL.reset(b))


def test_a_cloud_tagged_ollama_loop_is_not_local_and_hears_no_meeting(env, monkeypatch):
    from agent_friday.services.library import tools
    ma.index_meeting(record(A))
    monkeypatch.setattr(tools, "_settings", lambda: {})
    for model, heard in (("qwen3:8b", True), ("glm-4.6:cloud", False), ("gpt-oss:120b-cloud", False)):
        undo = _with_loop("ollama", model)
        try:
            assert tools._loop_is_local() is heard, model
            out = tools.search_library({"question": QUESTION, "scope": "meetings"})
        finally:
            undo()
        assert ("forty thousand" in out) is heard, model


def test_the_ask_box_never_writes_a_meeting_answer_with_a_cloud_tagged_seat(env, monkeypatch):
    from agent_friday.services import local_call
    from agent_friday.services.library import answer, search
    ma.index_meeting(record(A))
    ev = search.run(QUESTION, principal="owner", scope="meetings")["evidence"]
    assert ev
    sent = []
    monkeypatch.setattr(local_call, "call", lambda *a, **k: sent.append(a) or "an answer [1]")
    out = answer.write(QUESTION, ev, model="glm-4.6:cloud")
    assert out["ok"] is False and "cloud" in out["reason"] and sent == []
    assert answer.write(QUESTION, ev, model="qwen3:8b")["ok"] is True and sent


# ── M1, M2 ────────────────────────────────────────────────────────────────────

def test_a_reply_that_quotes_a_meeting_is_elided_even_when_cloud_answers_are_on(env):
    from agent_friday.services.library import cite
    ma.index_meeting(record(A))
    d = _store().find_document(str(ma.path_for(A["id"], "notes")))
    quoting = {"role": "assistant", "content": "The budget stays at forty thousand [lib:%d#1]." % d["id"]}
    other = {"role": "assistant", "content": "It is on page four [lib:99999#1]."}
    msgs = [dict(quoting), dict(other)]
    assert cite.elide_for_cloud(msgs, {"library_cloud_answers": True}) == 1
    assert msgs[0]["content"] == cite.ELIDED and msgs[1]["content"] == other["content"]
    assert cite.elide_for_cloud([dict(quoting), dict(other)], {}) == 2


def test_deleting_a_meeting_or_saying_no_scrubs_the_chats_that_quoted_it(env, monkeypatch):
    from agent_friday.services.library import forget
    ma.index_meeting(record(A))
    ids = {r["id"] for r in _store().q("SELECT id FROM documents")}
    scrubbed = []
    monkeypatch.setattr(forget, "scrub_copies", lambda principal, doc_id: scrubbed.append(doc_id) or 0)
    assert ma.remove_meeting(A["id"]) == 2 and set(scrubbed) == ids
    scrubbed.clear()
    ma.index_meeting(record(B))
    assert ma.remove_all() == 2 and len(scrubbed) == 2


# ── M4 ────────────────────────────────────────────────────────────────────────

def test_forget_person_redacts_the_name_from_the_meeting_record(env, monkeypatch):
    m = _mgr(monkeypatch)
    m._save(record(A, pad=[{"id": 1, "t": 5, "text": "Priya: review"}]))
    monkeypatch.setattr(mc, "_manager", m)
    assert mc.people_find({"Priya"}, set()) == 1
    assert mc.people_purge({"Priya"}, set()) == 1
    got = json.dumps(m.get(A["id"]))
    assert "priya" not in got.lower() and "[removed]" in got
    assert mc.people_find({"Priya"}, set()) == 0


# ── L1, L3, L5, L7 ────────────────────────────────────────────────────────────

def test_the_archive_choice_recommends_neither_answer(env):
    assert all(o["recommended"] is False for o in ma.status()["options"])
    page = (ROOT / "static" / "meetings_ws.js").read_text(encoding="utf-8").split("function Archive")[1]
    assert "(Recommended)" not in page


def test_a_failed_settings_read_refuses_reads_and_the_sweep_wipes_nothing(env, monkeypatch):
    from agent_friday.services.library import runtime
    ma.index_meeting(record(A))
    monkeypatch.setattr(ma, "settings_readable", lambda: False)
    monkeypatch.setattr(ma, "_settings", lambda: {})
    assert not ma.covers(ma.path_for(A["id"], "notes"))
    assert runtime.purge_uncovered("owner") == 0 and _count() == 2, "unknown is not no"
    monkeypatch.setattr(ma, "settings_readable", lambda: True)
    assert runtime.purge_uncovered("owner") == 2 and _count() == 0, "a read answer that is not yes still clears it"


def test_the_settings_reader_reports_a_failed_read(env, monkeypatch):
    from agent_friday import core
    p = env["tmp"] / "settings.json"
    monkeypatch.setattr(core, "SETTINGS_FILE", p)
    ma._FILE.update(key=None, val={}, failed=False)
    assert ma._read_settings_file() == {} and ma._FILE["failed"] is False, "no file yet is not a failed read"
    p.write_text("{broken", encoding="utf-8")
    assert ma._read_settings_file() == {} and ma._FILE["failed"] is True
    p.write_text(json.dumps({"meeting_archive": "on"}), encoding="utf-8")
    assert ma._read_settings_file() == {"meeting_archive": "on"} and ma._FILE["failed"] is False


def test_a_bullet_friday_adds_is_marked_hers(env, monkeypatch):
    from agent_friday.services import meeting_tool as mt
    m = _mgr(monkeypatch)
    m._save(record(A, pad=[]))
    assert "by" not in m.pad_add(A["id"], "mine")
    monkeypatch.setattr(mt, "_manager", lambda: m)
    assert json.loads(mt.meeting({"action": "jot", "meeting_id": A["id"], "text": "from a tool"}))["added"] is True
    assert [b.get("by") for b in m.pad_get(A["id"])][-1] == "friday"


def test_library_status_does_not_call_a_library_of_meetings_empty(env, monkeypatch):
    ma.index_meeting(record(A))
    tools, undo = _tools(monkeypatch, local=True)
    try:
        assert "empty" not in tools.library_status({}).lower()
    finally:
        undo()
