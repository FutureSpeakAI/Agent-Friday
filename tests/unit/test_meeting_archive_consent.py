"""The meetings archive is searchable only while the owner says yes, and nowhere else.

A meeting's transcript and notes are Library documents in a scope of their own. This file holds the consent
around them: the answer is the owner's, read on every ask (never kept), for a meeting that still exists;
saying no or deleting a meeting takes it out of search at once; the Library's own per-file consent, the
folder check and the drafting of wiki notes (KW-2) never treat a meeting as a file they may read; and no
cloud model, cloud voice call or relayed answer is handed a meeting's words. Meetings, names and numbers are
synthetic (tests/fixtures/meeting_synthetic.json).
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
    """A scratch Library over an encrypted-looking index, the owner's answer in a dict, and meetings that
    exist as records on disk (the archive reads the record's presence, nothing else of it)."""
    fg, lstore = isolate_library(tmp_path, monkeypatch)
    install_fake_encoder(monkeypatch)
    from agent_friday.services.library import grants, shelf
    monkeypatch.setattr(shelf, "_vault_key", lambda: None)
    monkeypatch.setattr(shelf, "tier_of", lambda title, sample: 1)
    state = {"choice": "on"}
    monkeypatch.setattr(ma, "_settings", lambda: {"meeting_archive": state["choice"]})
    root = tmp_path / "fh" / "meetings"
    monkeypatch.setattr(mc, "_root", lambda: root)
    lstore.store_for("owner").encrypted = True
    grants._TRACKED_CACHE.clear()
    grants.forget_kept()
    state["root"], state["tmp"] = root, tmp_path

    def exists(*ids):
        for mid in ids:
            (root / mid).mkdir(parents=True, exist_ok=True)
            (root / mid / "meeting.json.enc").write_bytes(b"x")
    state["exists"] = exists
    exists(A["id"], B["id"])
    yield state
    grants._TRACKED_CACHE.clear()
    grants.forget_kept()
    release_library(fg, lstore)


def _store():
    from agent_friday.services.library.store import store_for
    return store_for("owner")


def _count():
    return _store().one("SELECT count(*) n FROM documents")["n"]


# ── off by default; the answer is the owner's, read on every ask ─────────────

def test_the_archive_is_off_until_the_owner_chooses_and_nothing_is_preselected(env):
    from agent_friday import core
    assert core.DEFAULT_SETTINGS["meeting_archive"] == ""
    env["choice"] = ""
    path = ma.path_for(A["id"], "notes")
    assert ma.choice() == "" and not ma.visible() and not ma.covers(path)
    assert ma.index_meeting(record(A))["skipped"] == "not chosen" and _count() == 0
    out = ma.status()
    assert out["choice"] == "" and out["documents"] == 0


def test_a_yes_covers_the_owners_archive_documents_of_meetings_that_exist_and_nothing_else(env):
    from agent_friday.services.library import grants
    notes = ma.path_for(A["id"], "notes")
    assert grants.allowed("owner", notes) and grants.allowed("owner", ma.path_for(B["id"], "transcript"))
    assert not grants.allowed("someone-else", notes), "only the owner's Library holds a meeting"
    ghost = ma.path_for("c3c3c3c3c3c3", "notes")                       # no record on disk: not a meeting
    assert ma.is_archive_path(ghost) and not grants.allowed("owner", ghost)
    look = env["tmp"] / "Docs" / "meetings" / A["id"] / "library" / "notes.md"
    look.parent.mkdir(parents=True)
    look.write_text("an ordinary file that only looks like one", encoding="utf-8")
    assert ma.is_archive_path(look) and not ma.owns(look)
    assert not grants.allowed("owner", look), "an ordinary file is judged by the ordinary consent, which has none"
    env["choice"] = "off"
    assert not grants.allowed("owner", notes)


def test_every_way_of_asking_gives_the_same_answer_and_none_keeps_it(env, monkeypatch):
    from agent_friday.services.library import grants
    monkeypatch.setattr(grants, "_RECHECK_S", 0.0)
    path = ma.path_for(A["id"], "notes")
    held = grants.allower("owner")
    held.prime([path] * 40)
    for choice, want in (("on", True), ("off", False), ("", False), ("on", True)):
        env["choice"] = choice
        assert grants.allowed("owner", path) is want
        assert held(path) is want
        assert grants._allowed_uncached("owner", path) is want
        assert grants.allower("owner")(path) is want
    # a record deleted behind the Library's back is a meeting that is gone, at once
    (env["root"] / A["id"] / "meeting.json.enc").unlink()
    assert held(path) is False and grants.allowed("owner", path) is False


def test_a_never_send_mark_or_a_suspended_ledger_hides_a_meeting(env, monkeypatch):
    from agent_friday.services import file_grants as fg
    from agent_friday.services.library import grants
    notes, talk = ma.path_for(A["id"], "notes"), ma.path_for(A["id"], "transcript")
    assert grants.allowed("owner", notes) and grants.allowed("owner", talk)
    fg.create_deny_mark(str(notes), "file")
    assert not grants.allowed("owner", notes) and grants.allowed("owner", talk)
    monkeypatch.setattr(grants, "suspended", lambda: True)
    assert not grants.allowed("owner", talk), "an unverifiable ledger trusts no yes"
    assert grants.scope_covered("owner", talk) is False


def test_the_settings_write_cannot_say_yes_for_the_owner():
    text = (ROOT / "src" / "agent_friday" / "routes" / "core_routes.py").read_text(encoding="utf-8")
    block = text[text.index("def api_settings"):]
    block = block[:block.index("seat_error = ")]
    assert "meeting_archive" in block and "k != 'meeting_archive'" in block


def test_choosing_yes_or_no_is_a_click_on_the_screen(env, monkeypatch):
    from flask import Flask
    from agent_friday.routes.meetings import meetings_bp
    from agent_friday.services import screen_click
    app = Flask(__name__)
    app.config.update(TESTING=True)
    app.register_blueprint(meetings_bp)
    client = app.test_client()
    saved = []
    monkeypatch.setattr(ma, "set_choice", lambda c, **k: saved.append(c) or {"choice": c, "documents": 0})
    monkeypatch.setattr(ma, "reindex_all", lambda manager, **k: None)
    monkeypatch.setattr(screen_click, "screen_session", lambda request: False)
    r = client.post("/api/meetings/archive", json={"choice": "on"})
    assert r.status_code == 403 and r.get_json()["code"] == "screen_only" and saved == []
    assert client.post("/api/meetings/archive", json={"choice": "off"}).status_code == 403 and saved == []
    monkeypatch.setattr(screen_click, "screen_session", lambda request: True)
    assert client.post("/api/meetings/archive", json={"choice": "on"}).status_code == 200 and saved == ["on"]


# ── no, deletion and forget take a meeting out of search at once ──────────────

def test_saying_no_removes_every_archive_document_and_search_finds_nothing_even_before_the_purge(env, monkeypatch):
    from agent_friday import core
    from agent_friday.services.library import runtime, search
    assert ma.index_meeting(record(A))["indexed"] == 2 and ma.index_meeting(record(B))["indexed"] == 2
    assert search.run(QUESTION, principal="owner", scope="meetings")["evidence"]
    env["choice"] = "off"                                              # the answer changed; nothing was purged yet
    assert _count() == 4
    assert search.run(QUESTION, principal="owner", scope="meetings")["evidence"] == []
    assert search.run(QUESTION, principal="owner")["evidence"] == []
    assert runtime.purge_uncovered("owner") == 4 and _count() == 0, "the sweep takes the rest out of the index"
    env["choice"] = "on"
    assert ma.index_meeting(record(A))["indexed"] == 2
    monkeypatch.setattr(core, "_save_settings", lambda d: env.update(choice=d["meeting_archive"]))
    out = ma.set_choice("off")
    assert out["documents"] == 0 and _count() == 0 and env["choice"] == "off"


def test_deleting_a_meeting_removes_it_from_search_at_once_and_its_documents_with_it(env):
    from agent_friday.services.library import search
    mgr = mc.MeetingManager(source_factory=lambda max_s, **k: None, transcriber=lambda pcm: "", seat_resolver=lambda: None,
                            asr_check=lambda: "", lock_detector=lambda: None, event_lookup=lambda: None,
                            notifier=lambda *a: None, scheduler=lambda mid: types.SimpleNamespace(release=lambda: None),
                            partials_factory=lambda: None, ledger=lambda *a, **k: None, retention=lambda: "",
                            archive_index=lambda m: ma.index_meeting(m), archive_remove=ma.remove_meeting,
                            tick_s=0.01, chunk_s=1.0)
    mgr._save(record(A))
    mgr._save(record(B))
    assert ma.index_meeting(mgr.get(A["id"]))["indexed"] == 2 and ma.index_meeting(mgr.get(B["id"]))["indexed"] == 2
    assert search.run(QUESTION, principal="owner", scope="meetings")["evidence"]
    mgr.delete(A["id"])
    assert _store().one("SELECT count(*) n FROM documents WHERE path LIKE ?", ("%" + A["id"] + "%",))["n"] == 0
    assert _count() == 2, "the other meeting is untouched"
    # even if the removal had failed, the record is gone, so the documents are not an answer
    ma.index_meeting(mgr.get(B["id"]), force=True)
    (env["root"] / B["id"] / "meeting.json.enc").unlink()
    assert _count() == 2
    assert search.run(QUESTION, principal="owner", scope="meetings")["evidence"] == []


def test_deleting_the_audio_leaves_the_archive_and_the_notes_alone(env):
    assert ma.index_meeting(record(A, keep_audio=True))["indexed"] == 2
    mgr = mc.MeetingManager(source_factory=lambda max_s, **k: None, transcriber=lambda pcm: "", seat_resolver=lambda: None,
                            asr_check=lambda: "", lock_detector=lambda: None, event_lookup=lambda: None,
                            notifier=lambda *a: None, scheduler=lambda mid: types.SimpleNamespace(release=lambda: None),
                            partials_factory=lambda: None, ledger=lambda *a, **k: None, retention=lambda: "",
                            archive_index=lambda m: None, archive_remove=ma.remove_meeting, tick_s=0.01, chunk_s=1.0)
    mgr._save(record(A, keep_audio=True))
    mgr.delete_audio(A["id"])
    assert _count() == 2


def test_forgetting_a_meeting_document_keeps_it_forgotten_when_the_meeting_is_read_in_again(env):
    from agent_friday.services.library import forget
    ma.index_meeting(record(A))
    notes = _store().find_document(str(ma.path_for(A["id"], "notes")))
    assert forget.forget_document("owner", notes["id"])["ok"] is True
    assert _store().find_document(str(ma.path_for(A["id"], "notes"))) is None
    again = ma.index_meeting(record(A), force=True)
    assert again["indexed"] == 1, "the transcript is read again; the forgotten notes are not"
    assert _store().find_document(str(ma.path_for(A["id"], "notes"))) is None
    forget.unforget("owner", str(ma.path_for(A["id"], "notes")))
    assert ma.index_meeting(record(A), force=True)["indexed"] == 2


def test_a_person_the_owner_forgot_is_purged_and_is_not_read_back_from_the_meeting(env, monkeypatch):
    from agent_friday.services import forget_person
    from agent_friday.services.library import forget
    ma.index_meeting(record(A))
    assert forget.people_find({"Priya"}, set()) > 0
    forget.people_purge({"Priya"}, set())
    assert forget.people_find({"Priya"}, set()) == 0
    monkeypatch.setattr(forget_person, "forgotten_names", lambda: {"priya"})
    ma.index_meeting(record(A, summary=dict(record(A)["summary"], notes=["Priya will confirm the review."])), force=True)
    assert forget.people_find({"Priya"}, set()) == 0, "the record still names her; the archive no longer does"


# ── KW-2: the per-file consent, the folder check and wiki drafting ────────────

def test_a_meeting_is_never_drafted_into_wiki_staging_even_when_every_other_check_says_yes(env, monkeypatch):
    from agent_friday.services.library import grants, staging
    monkeypatch.setattr(staging, "_learning_off", lambda: False)
    ma.index_meeting(record(A))
    st = _store()
    for r in st.q("SELECT id FROM documents"):                         # a public tier, so only the archive rule is in the way
        st.set_doc_meta(r["id"], doc_type="note", type_score=0.5, sensitivity=1, dup_of=None, dup_kind=None,
                        dup_score=None, degraded=False, versions={"triage": 1})
    docs = st.q("SELECT * FROM documents WHERE state='indexed'")
    assert len(docs) == 2
    monkeypatch.setattr(grants, "allowed", lambda principal, path: True)    # consent says yes to everything
    assert staging.eligible(st) == [] and staging.eligible(st, ma.SCOPE) == []
    for d in docs:
        assert staging._still_ok(st, d, "owner") is False
    called = []
    ask = lambda *a: called.append(a) or {"facts": []}     # noqa: E731
    assert staging.draft_document(st, docs[0], "m", ask) == []
    assert staging.run_pass("owner", ma.SCOPE, complete=ask, model="m")["done"] == 0
    assert staging.run_pass("owner", "/not-a-folder", complete=ask, model="m")["done"] == 0
    assert called == [], "the model is never asked about a meeting"
    assert staging.list_items() == []


def test_a_meeting_is_not_a_folder_a_consent_can_cover(env):
    from agent_friday.services.library import grants
    assert grants.scope_covered("owner", ma.SCOPE) is False
    assert grants.scope_covered("owner", ma.path_for(A["id"], "notes")) is False
    assert grants.scope_covered("owner", ma.path_for(A["id"], "notes").parent) is False


# ── no cloud model, cloud voice or relayed answer gets a meeting's words ──────

def _tools(monkeypatch, *, local, surface=""):
    from agent_friday.services import agent
    from agent_friday.services.library import tools
    monkeypatch.setattr(tools, "_loop_is_local", lambda: local)
    monkeypatch.setattr(tools, "_settings", lambda: {"library_cloud_answers": True})
    tok = agent._CURRENT_SURFACE.set(surface)
    return tools, lambda: agent._CURRENT_SURFACE.reset(tok)


def test_a_local_loop_speaking_here_hears_the_meeting_and_nothing_else_does(env, monkeypatch):
    ma.index_meeting(record(A))
    cases = ((True, "", True), (False, "", False), (True, "voice-live-relay", False), (False, "voice-live-relay", False))
    for local, surface, heard in cases:
        tools, undo = _tools(monkeypatch, local=local, surface=surface)
        try:
            out = tools.search_library({"question": QUESTION, "scope": "meetings"})
        finally:
            undo()
        assert ("forty thousand" in out) is heard, (local, surface)
        if not heard:
            assert tools.MEETINGS_STAY in out and "lib:" not in out, (local, surface)


def test_library_show_does_not_name_a_meeting_to_a_cloud_reader(env, monkeypatch):
    from agent_friday.services.library import ui
    ma.index_meeting(record(A))
    doc = _store().find_document(str(ma.path_for(A["id"], "notes")))
    tools, undo = _tools(monkeypatch, local=False)
    try:
        assert ui._told("owner", doc["id"], doc["title"]) == "a meeting"
    finally:
        undo()
    tools, undo = _tools(monkeypatch, local=True)
    try:
        assert "Launch planning" in ui._told("owner", doc["id"], doc["title"])
    finally:
        undo()


def test_library_status_names_no_meeting_to_a_cloud_reader(env, monkeypatch):
    from agent_friday.services.library import grants
    ma.index_meeting(record(A))
    st = _store()
    st.x("UPDATE documents SET state=? WHERE scope=?", ("failed:x", ma.SCOPE))
    (env["tmp"] / "Somewhere").mkdir()
    grants.add_scope("owner", str(env["tmp"] / "Somewhere"))
    tools, undo = _tools(monkeypatch, local=False)
    try:
        assert "Launch planning" not in tools.library_status({})
    finally:
        undo()
    tools, undo = _tools(monkeypatch, local=True)
    try:
        assert "Launch planning" in tools.library_status({})
    finally:
        undo()


# ── the notes models: local only, a cloud-tagged seat is never one ────────────

def _manager(monkeypatch, seat, calls):
    from agent_friday.services import local_scheduler

    @contextlib.contextmanager
    def fake_work(label, cls=None, needs=None, quiet=True):
        yield
    monkeypatch.setattr(local_scheduler, "work", fake_work)

    def notes_call(system, user, model):
        calls.append(model)
        return {"lines": [], "people": [], "projects": []}
    return mc.MeetingManager(source_factory=lambda max_s, **k: None, transcriber=lambda pcm: "", seat_resolver=lambda: seat,
                             notes_call=notes_call, asr_check=lambda: "", lock_detector=lambda: None,
                             event_lookup=lambda: None, notifier=lambda *a: None,
                             scheduler=lambda mid: types.SimpleNamespace(release=lambda: None),
                             partials_factory=lambda: None, ledger=lambda *a, **k: None, retention=lambda: "",
                             archive_index=lambda m: None, archive_remove=lambda mid: None,
                             proposer=lambda c, mid, day, model: len(c), tick_s=0.01, chunk_s=1.0)


@pytest.mark.parametrize("seat", ["glm-4.6:cloud", "gpt-oss:120b-cloud"])
def test_a_cloud_tagged_seat_never_reads_a_transcript_for_the_pad_or_the_proposals(env, monkeypatch, seat):
    calls = []
    m = _manager(monkeypatch, seat, calls)
    m._save(record(A, pad=[{"id": 1, "t": 30, "text": "launch date 14th"}]))
    out = m.expand_notes(A["id"])
    assert out["status"] == "skipped" and out["reason"] == mc.NO_LOCAL_SEAT_CLOUD
    got = m.propose_memory(A["id"])
    assert "cloud model" in got["note"]
    assert calls == [], "the transcript was not handed to the model"


def test_a_local_seat_runs_the_pad_inside_one_visible_job_of_the_notes_kind(env, monkeypatch):
    src = (ROOT / "src" / "agent_friday" / "services" / "meeting_capture.py").read_text(encoding="utf-8")
    for fn in ("def expand_notes", "def propose_memory"):
        body = src[src.index(fn):]
        body = body[:body.index("\n    def ", 10)]
        assert 'self._job("Meeting notes", "meeting_notes"' in body and 'local_only("Meeting notes")' in body, fn
        assert "_notes_seat()" in body, fn


# ── every job is visible; the encoder is on the CPU ───────────────────────────

def test_reading_meetings_into_the_archive_is_a_visible_job_that_waits_for_a_recording(env, monkeypatch):
    seen = {"holds": 0, "ids": []}

    def hold():
        seen["holds"] += 1
        return "paused: a meeting is being recorded" if seen["holds"] < 3 else ""
    monkeypatch.setattr(ma, "_sleep", lambda s: None)
    mgr = types.SimpleNamespace(list=lambda: [{"id": A["id"], "state": "done"}, {"id": B["id"], "state": "recording"}],
                                get=lambda mid: seen["ids"].append(mid) or record(A))
    out = ma.reindex_all(mgr, hold=hold)
    assert out == {"meetings": 1, "indexed": 2} and seen["holds"] == 3 and seen["ids"] == [A["id"]]
    src = (ROOT / "src" / "agent_friday" / "services" / "meeting_archive.py").read_text(encoding="utf-8")
    assert src.count("with visible_job(") == 4 and src.count('kind="meeting_archive"') == 4
    assert "cuda" not in src.lower() and "device" not in src.lower(), "the archive loads no encoder of its own"


def test_the_owner_saying_no_while_the_archive_waits_ends_the_wait_and_reads_nothing(env, monkeypatch):
    monkeypatch.setattr(ma, "_sleep", lambda s: env.update(choice="off"))
    mgr = types.SimpleNamespace(list=lambda: [{"id": A["id"], "state": "done"}], get=lambda mid: record(A))
    out = ma.reindex_all(mgr, hold=lambda: "paused: a meeting is being recorded")
    assert out == {"meetings": 0, "indexed": 0} and _count() == 0
