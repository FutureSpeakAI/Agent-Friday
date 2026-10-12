"""The Meetings workspace (MT-2): the notes pad, notes with spans, proposals, the calendar card, the archive.

Every model is a mock and no audio is recorded. The synthetic meetings are in
tests/fixtures/meeting_synthetic.json: invented names, places and numbers.

What these hold:
  * the pad takes bullets while a call records and after it;
  * a bullet is expanded by a LOCAL model only (inside the local-only guard, as `user_job` work), and
    every kept line cites real transcript lines and invents no name, number, date or place;
  * people, decisions and projects become proposals with provenance and nothing is written directly;
  * the summary reaches the calendar only through a card, and only to the meeting's own event;
  * the transcript and the notes are Library documents in a scope of their own, answered with citations,
    visible only while the owner has said yes, and never offered to a cloud model;
  * "open my last meeting" is a screen move, and a meeting row opens and ticks by pointing.
"""
from __future__ import annotations

import contextlib
import http.client
import json
import socket
import time
import types
import urllib.request
from pathlib import Path

import pytest

from agent_friday.services import local_only_guard
from agent_friday.services import meeting_archive as ma
from agent_friday.services import meeting_calendar as mcal
from agent_friday.services import meeting_capture as mc
from agent_friday.services import meeting_workspace as mw

ROOT = Path(__file__).resolve().parents[2]
FIX = json.loads((ROOT / "tests" / "fixtures" / "meeting_synthetic.json").read_text(encoding="utf-8"))["meetings"]
A = FIX[0]
CANARY = "CANARY-5d2e84b1-offsite-numbers"


def record(fx, **kw):
    segs = [{"t": 0, "kind": "consent", "text": "Recording started."}]
    segs += [{"t": t, "kind": "speech", "speaker": who, "text": text} for t, who, text in fx["lines"]]
    segs.insert(3, {"t": fx["lines"][1][0] + 1, "kind": "gap", "speaker": None, "text": "[This part could not be transcribed: x]"})
    rec = {"id": fx["id"], "title": fx["title"], "event_id": "", "started_at": "2026-10-09T10:00:00",
           "ended_at": "2026-10-09T10:05:00", "duration_s": 300, "state": "done", "stop_reason": "owner",
           "source": "microphone", "system_audio": False, "keep_audio": False, "segments": segs,
           "summary": {"status": "pending"}, "pad": [dict(b) for b in fx["pad"]], "tasks_created": [], "follow_ups": []}
    rec.update(kw)
    return rec


def done_summary(fx, **kw):
    return {"status": "done", "model": "notes-model", "local": True, "notes": ["Reviewed the plan."],
            "decisions": list(fx["decisions"]), "action_items": [{"task": t, "owner": None, "due": None} for t in fx["gold_actions"]]} | kw


class Src:
    kind = "microphone"
    error = ""

    def __init__(self, max_s):
        self.on = None

    def start(self, on_pcm):
        self.on = on_pcm

    def stop(self):
        pass


class Rig:
    """A manager with every outside thing replaced, and a record of what it was asked."""

    def __init__(self, tmp_path, monkeypatch, reply=None, seat="local-test-model", real_proposer=False):
        monkeypatch.setattr(mc, "_root", lambda: tmp_path / "meetings")
        self.calls, self.proposed, self.indexed, self.removed, self.work = [], [], [], [], []
        self.reply = reply or (lambda system, user: {"lines": []})

        def notes_call(system, user, model):
            # a cloud provider would be refused right here
            assert local_only_guard.is_active()
            with pytest.raises(local_only_guard.CloudRefused):
                local_only_guard.refuse_if_active("anthropic", "a-cloud-model")
            self.calls.append((system, user, model))
            return self.reply(system, user)

        @contextlib.contextmanager
        def fake_work(label, cls=None, needs=None, quiet=True):
            self.work.append((label, cls))
            yield
        from agent_friday.services import local_scheduler
        monkeypatch.setattr(local_scheduler, "work", fake_work)
        kw = {}
        if not real_proposer:
            kw["proposer"] = lambda c, mid, day, model: self.proposed.append((c, mid, day, model)) or len(c)
        self.m = mc.MeetingManager(
            source_factory=lambda max_s, **k: Src(max_s), transcriber=lambda pcm: "talk",
            seat_resolver=lambda: seat, notes_call=notes_call, asr_check=lambda: "", lock_detector=lambda: None,
            event_lookup=lambda: None, notifier=lambda *a: None,
            scheduler=lambda mid: types.SimpleNamespace(release=lambda: None),
            partials_factory=lambda: None, speech_gate=lambda pcm: True, ledger=lambda *a, **k: None,
            retention=lambda: "", archive_index=lambda m: self.indexed.append(m["id"]),
            archive_remove=lambda mid: self.removed.append(mid), tick_s=0.01, chunk_s=1.0, **kw)

    def put(self, rec):
        self.m._save(rec)
        return rec["id"]


@pytest.fixture
def rig(tmp_path, monkeypatch):
    made = []

    def make(**kw):
        r = Rig(tmp_path, monkeypatch, **kw)
        made.append(r)
        return r
    yield make
    for r in made:
        try:
            r.m.stop()
        except mc.MeetingError:
            pass
        r.m.wait_idle(5)


# ── the numbered transcript ─────────────────────────────────────────────────

def test_spoken_lines_are_numbered_without_the_consent_line_or_gaps():
    lines = mw.spoken(record(A))
    assert [ln["i"] for ln in lines] == list(range(len(A["lines"])))
    assert lines[0]["text"].startswith("Okay, thanks") and lines[1]["speaker"] == "Them"
    assert mw.line_text(lines[1]).startswith("[1 00:00:20] Them: Marta says")
    chunks = mw.numbered_chunks(lines, 200)
    assert len(chunks) > 1 and "\n".join(chunks).count("[") == len(lines)


# ── the faithfulness check ──────────────────────────────────────────────────

def test_a_name_number_date_or_place_that_nobody_said_is_caught():
    transcript = "\n".join(ln["text"] for ln in mw.spoken(record(A)))
    assert mw.unknown_entities("The launch moves to the 21st after the Osaka review.", transcript) == ["21st", "osaka"]
    assert mw.unknown_entities("Devon is also leading the Berlin roadshow.", transcript) == ["berlin"]
    assert mw.unknown_entities("It costs $5,000.", transcript) == ["5000"]
    assert mw.unknown_entities("Write to ops@example.test.", transcript) == ["ops@example.test"]
    assert mw.unknown_entities("Harbor hosts the roadshow.", transcript) == ["harbor"], "a name that starts a sentence is caught"
    assert mw.unknown_entities("Send the plan to Priya.", transcript) == [], "a plain verb that starts a line is not a name"


def test_what_was_said_and_the_owners_own_bullet_are_not_inventions():
    transcript = "\n".join(ln["text"] for ln in mw.spoken(record(A)))
    assert mw.unknown_entities("Jonas's plan to cut Lisbon to thirty thousand was turned down.", transcript) == []
    # a name only the owner wrote is the owner's, not the model's
    assert mw.unknown_entities("Rowan owns the venue.", transcript, bullet="Rowan: venue") == []
    assert mw.unknown_entities("Rowan owns the venue.", transcript) == ["rowan"]


def test_the_faithfulness_check_can_fail():
    lines, pad = mw.spoken(record(A)), A["pad"]
    assert mw.check_polished([{"text": "Devon will ask Priya.", "bullet": 2, "spans": [{"i": 3, "t": 70}]}], lines, pad) == []
    probs = mw.check_polished([{"text": "Devon will ask Priya.", "bullet": 2, "spans": []},
                               {"text": "Devon will ask Priya.", "bullet": 2, "spans": [{"i": 99, "t": 0}]},
                               {"text": "Devon flew to Osaka.", "bullet": 2, "spans": [{"i": 3, "t": 70}]}], lines, pad)
    assert [p[1] for p in probs][0] == "no span" and any("bad span" in p[1] for p in probs) and any("osaka" in p[1] for p in probs)


# ── the notes pad ───────────────────────────────────────────────────────────

def test_a_bullet_jotted_while_recording_is_stamped_and_survives_the_stop(rig):
    r = rig()
    st = r.m.start(consent_ack=True, confirm=True, title="Pad test")
    mid = st["id"]
    item = r.m.pad_add(mid, "  budget   by Friday ")
    assert item["text"] == "budget by Friday" and item["t"] is not None and item["id"] == 1
    assert r.m.pad_add(mid, "second")["id"] == 2
    assert [b["text"] for b in r.m.pad_get(mid)] == ["budget by Friday", "second"]
    r.m.stop()
    assert r.m.wait_idle(10)
    assert [b["text"] for b in r.m.get(mid)["pad"]] == ["budget by Friday", "second"]


def test_the_pad_edits_a_finished_meeting_and_says_no_politely(rig, monkeypatch):
    r = rig()
    mid = r.put(record(A, pad=[]))
    a = r.m.pad_add(mid, "one")
    b = r.m.pad_add(mid, "two")
    assert (a["t"], b["id"]) == (None, 2)
    r.m.pad_remove(mid, a["id"])
    assert [x["text"] for x in r.m.pad_get(mid)] == ["two"]
    with pytest.raises(mc.MeetingError) as e:
        r.m.pad_remove(mid, 99)
    assert e.value.code == "not_found"
    with pytest.raises(mc.MeetingError) as e:
        r.m.pad_add(mid, "   ")
    assert e.value.code == "empty"
    monkeypatch.setattr(mw, "PAD_MAX_BULLETS", 1)
    with pytest.raises(mc.MeetingError) as e:
        r.m.pad_add(mid, "three")
    assert e.value.code == "pad_full"


# ── bullets into notes ──────────────────────────────────────────────────────

def test_expansion_runs_locally_as_user_job_work_and_keeps_only_traceable_lines(rig):
    r = rig(reply=lambda system, user: {"lines": A["model_lines"]})
    mid = r.put(record(A))
    out = r.m.expand_notes(mid)
    assert out["status"] == "done" and out["local"] is True and out["model"] == "local-test-model"
    assert [ln["text"] for ln in out["lines"]] == [A["model_lines"][i]["text"] for i in A["keep"]]
    for ln in out["lines"]:
        assert ln["spans"] and all(set(sp) == {"i", "t"} for sp in ln["spans"])
    assert out["left_out"] == len(A["model_lines"]) - len(A["keep"])
    assert r.work == [("Meeting notes", "user_job")]
    system, user, model = r.calls[0]
    assert system is mw.EXPAND_SYSTEM and model == "local-test-model"
    assert "[1 00:00:20] Them: Marta says" in user
    assert "b2 (jotted at 00:01:00): Devon: payment review with Priya" in user
    assert r.m.get(mid)["polished"]["lines"] == out["lines"]
    assert r.indexed == [mid], "the archive is refreshed with the new notes"


def test_expansion_says_why_when_it_cannot_run(rig):
    r = rig(seat=None, reply=lambda s, u: {"lines": []})
    mid = r.put(record(A))
    out = r.m.expand_notes(mid)
    assert out["status"] == "skipped" and out["reason"] == mc.NO_LOCAL_SEAT and r.calls == []
    r2 = rig(reply=lambda s, u: {"lines": []})
    assert r2.m.expand_notes(r2.put(record(A, pad=[])))["status"] == "skipped"
    r3 = rig(reply=lambda s, u: None)
    out = r3.m.expand_notes(r3.put(record(A)))
    assert out["status"] == "failed" and "lines" not in out
    assert r3.m.get(A["id"])["polished"]["status"] == "failed"


# ── action items become tasks through the existing approved path ────────────

def test_action_items_become_tasks_only_through_the_approved_create_tasks(rig):
    r = rig()
    todos = []
    r.m.todo_writer = lambda t: todos.append(t) or t
    mid = r.put(record(A, summary=done_summary(A)))
    out = r.m.create_tasks(mid, [0, 2])
    assert [c["index"] for c in out["created"]] == [0, 2]
    assert all(t["status"] == "approved" and t["source"] == "meeting:" + mid for t in todos)
    assert r.m.create_tasks(mid, [0])["skipped"] == [0], "an item already in Tasks is not added twice"


def test_an_action_item_that_traces_to_no_line_is_marked_to_check(rig):
    r = rig()
    s = done_summary(A, action_items=[{"task": "Confirm the payment review with Priya", "owner": None, "due": None},
                                     {"task": "Book a venue in Osaka", "owner": None, "due": None}])
    mid = r.put(record(A, summary=s))
    got = r.m.action_item_check(mid)
    assert [g["traced"] for g in got] == [True, False]
    assert got[0]["span"]["i"] in (2, 3) and got[1]["span"] is None


# ── proposals to memory ─────────────────────────────────────────────────────

@pytest.fixture
def proposals(tmp_path, monkeypatch):
    from agent_friday.services import memory_proposals as mp
    monkeypatch.setattr(mp, "FRIDAY_DIR", tmp_path / "fd")
    monkeypatch.setattr(mp, "DB_PATH", tmp_path / "fd" / "dreams.db")

    class Forbidden(BaseException):
        pass

    def boom(*a, **k):
        raise Forbidden("a meeting wrote straight into memory or the knowledge graph")
    from agent_friday.services import user_model
    from agent_friday.services.knowledge_graph import integration
    monkeypatch.setattr(user_model, "note_fact", boom)
    monkeypatch.setattr(integration, "ingest_fact", boom)
    return mp


def entities_reply(fx):
    def reply(system, user):
        assert system is mw.ENTITIES_SYSTEM
        return {"people": fx["people"], "projects": fx["projects"]}
    return reply


def test_people_decisions_and_projects_become_proposals_with_provenance_and_nothing_is_written(rig, proposals):
    r = rig(reply=entities_reply(A), real_proposer=True)
    mid = r.put(record(A, summary=done_summary(A)))
    out = r.m.propose_memory(mid)
    rows = proposals.pending()
    assert out["proposed"] == len(rows) == 5 and out["left_out"] == 1, "Rowan was never said, so is not proposed"
    assert {x["category"] for x in rows} == {"relationship", "project", "decision"}
    assert not any("Rowan" in x["text"] for x in rows)
    for x in rows:
        prov = x["provenance"]
        assert prov["meeting_id"] == mid and 0 <= prov["span"]["segment"] < len(A["lines"]) and prov["span"]["t"] >= 0
        assert prov["model"] == ("notes-model" if x["category"] == "decision" else "local-test-model")
        assert x["confidence"] == proposals.MODEL_CONFIDENCE
    assert r.m.get(mid)["memory_proposals"]["proposed"] == 5
    assert r.m.propose_memory(mid)["proposed"] == 0, "the same proposals are not made twice"


def test_approving_a_meeting_proposal_keeps_where_it_came_from(rig, proposals, monkeypatch):
    r = rig(reply=entities_reply(A), real_proposer=True)
    mid = r.put(record(A, summary=done_summary(A)))
    r.m.propose_memory(mid)
    seen = []
    from agent_friday.services import user_model
    from agent_friday.services.knowledge_graph import integration
    monkeypatch.setattr(user_model, "note_fact", lambda cat, text, **k: seen.append((cat, k["source"])) or {"ok": True})
    monkeypatch.setattr(integration, "ingest_fact", lambda text, **k: seen.append(("kg", k["source_key"])))
    first = proposals.pending()[0]
    assert proposals.approve([first["fact_id"]])["approved"] == 1
    assert seen == [(first["category"], "meeting:" + mid), ("kg", "meeting:" + mid)]


def test_without_a_local_model_only_decisions_are_considered(rig, proposals):
    r = rig(seat=None, real_proposer=True)
    mid = r.put(record(A, summary=done_summary(A)))
    out = r.m.propose_memory(mid)
    assert out["proposed"] == 2 and "No local model" in out["note"]
    assert {x["category"] for x in proposals.pending()} == {"decision"}


# ── the summary on the calendar event ───────────────────────────────────────

def test_attaching_the_summary_waits_for_a_card_and_writes_nothing_before_it(monkeypatch):
    from agent_friday.governance import action_gate
    writes, seen = [], {}

    def fake_write(**kw):
        writes.append(kw)
        return {"ok": True, "dry_run": bool(kw.get("dry_run")),
                "changed": [{"id": "ev1", "title": "Launch planning", "would_set": {}}], "skipped": []}

    def fake_authorize(action, detail, **kw):
        seen.update(action=action, detail=detail, kw=kw)
        return action_gate.Verdict("card", action_gate.OUTWARD, "waiting")
    monkeypatch.setattr(mcal, "_write", fake_write)
    monkeypatch.setattr(action_gate, "authorize_external", fake_authorize)
    monkeypatch.setattr(mcal, "_card_id", lambda d: "ap_1")
    out = mcal.request(record(A, event_id="ev1", summary=done_summary(A)))
    assert out["status"] == "pending_approval" and out["approval_id"] == "ap_1"
    assert seen["action"] == "annotate_calendar_events"
    assert seen["detail"]["event_id"] == "ev1" and seen["detail"]["handler"] == mcal.HANDLER
    note = seen["detail"]["note"]
    assert "Launch on the 14th" in note and "Marta says" not in note, "the summary goes, never the transcript"
    assert "Nothing is added unless you approve" in seen["kw"]["description"] and note in seen["kw"]["description"]
    assert [w.get("dry_run") for w in writes] == [True], "before the card is decided there is only a preview"


def test_the_approved_card_writes_to_that_one_event_and_nothing_else_does(monkeypatch):
    from agent_friday.governance import action_gate
    writes = []
    monkeypatch.setattr(mcal, "_write", lambda **kw: writes.append(kw) or {"ok": True})
    monkeypatch.setattr(mcal, "_notify", lambda *a, **k: None)
    monkeypatch.setattr(action_gate, "authorize_external", lambda a, d, **kw: action_gate.Verdict("allow", action_gate.OUTWARD, "approved"))
    detail = {"handler": mcal.HANDLER, "meeting_id": "x", "event_id": "ev1", "query": "Launch planning", "note": "n"}
    mcal._on_decision({"payload": detail, "status": "approved", "approval_id": "ap"})
    assert writes == [{"query": "Launch planning", "note": "n", "event_id": "ev1", "apply_to_series": False}]
    mcal._on_decision({"payload": {"handler": "something_else"}, "status": "approved"})
    mcal._on_decision({"payload": detail, "status": "denied"})
    assert len(writes) == 1
    monkeypatch.setattr(action_gate, "authorize_external", lambda a, d, **kw: action_gate.Verdict("card", action_gate.OUTWARD, "no"))
    mcal._on_decision({"payload": detail, "status": "approved", "approval_id": "ap"})
    assert len(writes) == 1, "a check that does not allow it writes nothing"


def test_there_is_nothing_to_attach_without_a_summary_or_an_event(monkeypatch):
    monkeypatch.setattr(mcal, "_write", lambda **kw: pytest.fail("the calendar was touched"))
    assert mcal.request(record(A, event_id="ev1"))["status"] == "nothing"
    assert mcal.request(record(A, summary=done_summary(A)))["status"] == "unlinked"


def test_annotating_by_event_id_touches_that_event_only(monkeypatch):
    from agent_friday.services import calendar_write as cw
    patched = []

    class Req:
        def __init__(self, body):
            self.body = body

        def execute(self):
            return {}

    class Events:
        def patch(self, calendarId, eventId, body):
            patched.append(eventId)
            return Req(body)
    svc = types.SimpleNamespace(events=lambda: Events())
    ev = lambda i: {"id": i, "recurring_event_id": None, "title": "Launch planning", "start": "2026-10-09", "location": "", "description": ""}
    monkeypatch.setattr(cw, "write_ready", lambda: (True, ""))
    monkeypatch.setattr(cw, "resolve_write_account", lambda a: ("acct", None))
    monkeypatch.setattr(cw, "find_events", lambda q, **k: {"events": [ev("e1"), ev("e2")], "series": [ev("s1")]})
    monkeypatch.setattr(cw, "_service", lambda aid: (svc, None))
    monkeypatch.setattr(cw, "_gate_calendar_field", lambda text, field: (text, ""))
    monkeypatch.setattr(cw, "_drop_calendar_cache", lambda: None)
    out = cw.annotate_events("Launch planning", note="Decisions: x", event_id="e1")
    assert patched == ["e1"] and out["matched"] == 1
    assert cw.annotate_events("Launch planning", note="x", event_id="nope")["error"].startswith("that calendar event was not found")
    assert patched == ["e1"]
    patched.clear()
    assert cw.annotate_events("Launch planning", note="x", apply_to_series=False)["matched"] == 2, "without an id the old behaviour stands"


# ── the archive ─────────────────────────────────────────────────────────────

@pytest.fixture
def lib(tmp_path, monkeypatch):
    from tests.library_fixtures import install_fake_encoder, isolate_library, release_library
    fg, lstore = isolate_library(tmp_path, monkeypatch)
    install_fake_encoder(monkeypatch)
    from agent_friday.services.library import shelf
    monkeypatch.setattr(shelf, "_vault_key", lambda: None)
    monkeypatch.setattr(shelf, "tier_of", lambda title, sample: 1)
    state = {"choice": "on"}
    monkeypatch.setattr(ma, "_settings", lambda: {"meeting_archive": state["choice"]})
    monkeypatch.setattr(mc, "_root", lambda: tmp_path / "fh" / "meetings")
    # these tests index meetings that were never written to disk; the live check of the meeting's record has its
    # own tests (test_meeting_archive_consent.py)
    monkeypatch.setattr(ma, "meeting_exists", lambda mid: True)
    # the test environment may have no sqlcipher: stand in for an encrypted, keyed index (the other branch is tested below)
    lstore.store_for("owner").encrypted = True
    yield state
    release_library(fg, lstore)


def full(fx=A):
    rec = record(fx, summary=done_summary(fx))
    rec["polished"] = {"status": "done", "lines": [{"bullet": 1, "text": "The launch is set for the 14th.", "spans": [{"i": 1, "t": 20}]}]}
    return rec


def test_the_archive_shape_and_the_owners_yes(lib):
    p = ma.path_for(A["id"], "notes")
    assert ma.is_archive_path(p) and ma.covers(p)
    assert not ma.is_archive_path("C:/Users/x/Documents/meetings/notes.md")
    assert not ma.is_archive_path(p.parent / "other.md")
    lib["choice"] = ""
    assert not ma.covers(p), "nothing is visible until the owner has chosen"
    lib["choice"] = "off"
    assert not ma.covers(p)


def test_a_meeting_becomes_two_library_documents_in_a_scope_of_its_own(lib):
    from agent_friday.services.library.store import store_for
    assert ma.index_meeting(full()) == {"indexed": 2, "unchanged": 0, "removed": 0}
    st = store_for("owner")
    rows = st.q("SELECT id, path, scope, title, state FROM documents ORDER BY id")
    assert {r["scope"] for r in rows} == {ma.SCOPE} and all(r["state"] == "indexed" for r in rows)
    assert all(ma.is_archive_doc(r) for r in rows)
    assert [r["title"] for r in rows] == ["Meeting transcript: Launch planning (2026-10-09)", "Meeting notes: Launch planning (2026-10-09)"]
    blocks = st.q("SELECT t_start, t_end, text FROM blocks WHERE doc_id=? ORDER BY ord", (rows[0]["id"],))
    assert [b["t_start"] for b in blocks] == [float(t) for t, _w, _x in A["lines"]]
    assert blocks[0]["text"].startswith("[00:00:05] You: Okay")
    notes = " ".join(b["text"] for b in st.q("SELECT text FROM blocks WHERE doc_id=?", (rows[1]["id"],)))
    assert "We decided: Launch on the 14th" in notes and "transcript line 1 at 00:00:20" in notes
    assert ma.index_meeting(full()) == {"indexed": 0, "unchanged": 2, "removed": 0}, "an unchanged meeting is not read again"


def test_nothing_is_indexed_until_the_owner_says_yes(lib):
    from agent_friday.services.library.store import store_for
    lib["choice"] = ""
    assert ma.index_meeting(full())["skipped"] == "not chosen"
    assert store_for("owner").one("SELECT count(*) n FROM documents")["n"] == 0
    lib["choice"] = "on"
    assert ma.index_meeting(dict(full(), state="recording"))["skipped"] == "still being recorded"


def test_a_question_about_meetings_is_a_library_search_in_the_meetings_scope(lib, tmp_path, monkeypatch):
    from agent_friday.services.library import grants, indexer, search, tools
    from agent_friday.services.library.store import store_for
    from tests.library_fixtures import write_docs
    root = tmp_path / "Lib"
    write_docs(root, {"plan.txt": "The Lisbon budget is whatever the vendor quotes in the contract."})
    grants.add_scope("owner", str(root))
    indexer.sweep_scope(store_for("owner"), root, allowed=lambda p: grants.allowed("owner", p))
    ma.index_meeting(full())
    q = "what did we decide about the Lisbon budget"
    scoped = search.run(q, principal="owner", scope="meetings")
    ev = scoped["evidence"]
    assert ev and all(e["ref"].startswith("lib:") and e["doc"].startswith("Meeting ") for e in ev)
    assert any("forty thousand" in e["text"] for e in ev)
    assert search.MEETINGS_SCOPE == ma.SCOPE
    assert any(e["doc"] == "plan" for e in search.run(q, principal="owner")["evidence"]), "the plain Library is unchanged"
    # the same question through the tool the model and the voice use
    monkeypatch.setattr(tools, "_loop_is_local", lambda: True)
    monkeypatch.setattr(tools, "_settings", lambda: {})
    out = tools.search_library({"question": q, "scope": "meetings"})
    assert "lib:" in out and "forty thousand" in out and "plan.txt" not in out
    lib["choice"] = "off"
    assert search.run(q, principal="owner", scope="meetings")["evidence"] == []


def test_a_meetings_words_are_never_offered_to_a_cloud_model_even_with_grants(lib, monkeypatch):
    from agent_friday.services import file_grants as fg
    from agent_friday.services.library import search, tools
    ma.index_meeting(full())
    ev = search.run("what did we decide about the Lisbon budget", principal="owner", scope="meetings")["evidence"]
    assert ev
    monkeypatch.setattr(fg, "check_grant", lambda p: types.SimpleNamespace(state="active"))
    monkeypatch.setattr(fg, "on_file_read", lambda *a, **k: None)
    kept, note = tools._cloud_evidence(ev, "owner", {"library_cloud_answers": True})
    assert kept == [] and "withheld" in note
    monkeypatch.setattr(tools, "_loop_is_local", lambda: False)
    monkeypatch.setattr(tools, "_settings", lambda: {"library_cloud_answers": True})
    out = tools.search_library({"question": "what did we decide about the Lisbon budget", "scope": "meetings"})
    assert "forty thousand" not in out


def test_a_citation_to_a_meeting_stays_valid_while_it_is_searchable(lib):
    from agent_friday.services.library import cite, search
    ma.index_meeting(full())
    e = search.run("launch planning payment review", principal="owner", scope="meetings")["evidence"][0]
    assert cite._still_valid("owner", e["doc_id"], e["block_id"]) is True
    lib["choice"] = "off"
    assert cite._still_valid("owner", e["doc_id"], e["block_id"]) is False


def test_deleting_a_meeting_or_turning_the_archive_off_removes_its_documents(lib, rig, monkeypatch):
    from agent_friday.services.library.store import store_for
    st = store_for("owner")
    ma.index_meeting(full())
    ma.index_meeting(full(FIX[1]))
    assert st.one("SELECT count(*) n FROM documents")["n"] == 4
    assert ma.remove_meeting(A["id"]) == 2 and st.one("SELECT count(*) n FROM documents")["n"] == 2
    saved = []
    from agent_friday import core
    monkeypatch.setattr(core, "_save_settings", lambda d: saved.append(d))
    out = ma.set_choice("off")
    assert saved == [{"meeting_archive": "off"}] and out["documents"] == 0
    with pytest.raises(ValueError):
        ma.set_choice("maybe")
    r = rig()
    mid = r.put(record(A))
    r.m.delete(mid)
    assert r.removed and set(r.removed) == {mid}, "deleting a meeting clears its archive documents in the same step"


def test_finishing_the_notes_refreshes_the_archive(rig):
    r = rig(reply=lambda s, u: {"notes": ["a point"], "decisions": [], "action_items": []})
    mid = r.put(record(A))
    assert r.m.write_notes(mid)["status"] == "done"
    assert r.indexed == [mid]


# ── Laya: the router, the resolver, pointing ────────────────────────────────

def test_open_my_last_meeting_goes_to_the_meeting_not_the_calendar():
    from agent_friday.services import laya_router as lr
    for text in ("open my last meeting", "show me the latest call", "pull up the most recent recording"):
        r = lr._tier0(text)
        assert (r.decision, r.tool) == ("tool", "navigate_to"), text
        assert r.args["kind"] == "meeting" and r.args["id"] == "last" and r.args["query"]
    assert lr._tier0("what meetings do I have today").tool == "query_calendar"
    assert lr._tier0("open the calendar").args == {"kind": "workspace", "query": "calendar"}
    assert lr._tier0("open meetings").args == {"kind": "workspace", "query": "meetings"}
    r = lr._tier0("open my last email")
    assert r is None or r.tool != "navigate_to"


def test_a_meeting_is_found_by_id_by_last_and_by_the_words_of_its_title(monkeypatch):
    from agent_friday.services import desktop_targets as dt
    rows = [{"id": "e5e5e5e5e5e5", "title": "Live one", "state": "recording"},
            {"id": A["id"], "title": "Launch planning", "state": "done"},
            {"id": FIX[1]["id"], "title": "Hiring sync", "state": "done"}]
    monkeypatch.setattr(mc, "get_manager", lambda: types.SimpleNamespace(list=lambda: rows))
    assert dt.resolve("meeting", id="last")["target"] == {"workspace": "meetings", "meeting_id": A["id"]}
    assert dt.resolve("meeting", query="my latest meeting")["target"]["meeting_id"] == A["id"]
    assert dt.resolve("meeting", id=FIX[1]["id"])["target"]["meeting_id"] == FIX[1]["id"]
    assert dt.resolve("meeting", query="the hiring call")["target"]["meeting_id"] == FIX[1]["id"]
    assert dt.resolve("meeting", query="the second meeting")["target"]["meeting_id"] == FIX[1]["id"], "the second one on screen"
    assert dt.resolve("meeting", query="the fifth meeting")["ok"] is False
    bad = dt.resolve("meeting", query="the budget call")
    assert bad["ok"] is False and "no meeting matches" in bad["reason"]
    assert dt.resolve("meeting", id="ffffffffffff")["ok"] is False
    assert "meeting" in dt.KINDS and dt.UNNAMED["meeting"] == "the meeting"


def test_start_recording_by_voice_still_only_opens_the_start_card(monkeypatch):
    from agent_friday.services import desktop_targets as dt

    def no_manager():
        raise AssertionError("opening the card must not touch the recorder")
    monkeypatch.setattr(mc, "get_manager", no_manager)
    for phrase in ("start recording", "record this meeting", "take notes"):
        r = dt.resolve("meeting", query=phrase)
        assert r["ok"] and r["target"] == {"workspace": "meetings", "start": "confirm"}
        assert "nothing is recorded until you confirm" in r["label"]


def _stage(n=3):
    from agent_friday.services import screen_stage as ss
    ids = [A["id"], FIX[1]["id"], FIX[2]["id"], FIX[3]["id"]][:n]
    return ss.bound_stage({"workspace": "meetings", "rev": 1, "items": [
        {"ref": "meeting:" + i, "n": k + 1, "title": "Meeting %d" % (k + 1),
         "facets": {"kind": "meeting", "state": "done", "transcript": "never published"}}
        for k, i in enumerate(ids)]}, now=time.time()), ids


def test_a_meeting_row_opens_by_pointing():
    from agent_friday.services import point_act as pa
    st, ids = _stage()
    r = pa.plan("open the second one", stage=st, pointed=None, now=time.time())
    assert (r.decision, r.tool, r.args) == ("tool", "navigate_to", {"kind": "meeting", "id": ids[1]})
    assert all(it["facets"] == {"kind": "meeting"} and it["title"] == "" for it in st["items"]), "a kind and a position, no title"


def test_meeting_rows_can_be_ticked_by_pointing():
    from agent_friday.services import point_act as pa
    st, ids = _stage()
    r = pa.plan("select the first two", stage=st, pointed=None, now=time.time())
    assert (r.decision, r.tool) == ("tool", "screen_select")
    assert r.args["workspace"] == "meetings" and r.args["match"]["refs"] == ["meeting:" + i for i in ids[:2]]


def test_calendar_no_longer_claims_the_word_meetings():
    from agent_friday.services import workspace_registry as wr
    assert "meetings" not in wr.get("calendar")["aliases"] and "meetings" in wr.ids()
    assert wr.resolve("meetings") == wr.resolve("my meetings") == "meetings"
    assert wr.resolve("calendar") == wr.resolve("schedule") == "calendar"
    assert "meetings: { prefix: 'meeting:', selectable: true, counts_only: true }" in (ROOT / "static" / "workspace_stages.js").read_text(encoding="utf-8")


# ── nothing leaves this PC ──────────────────────────────────────────────────

def test_the_workspace_flows_send_nothing_anywhere(rig, proposals, lib, monkeypatch):
    attempts = []

    def trip(*a, **k):
        attempts.append(a[:2])
        raise AssertionError("a meeting tried to reach the network")
    monkeypatch.setattr(socket.socket, "connect", trip)
    monkeypatch.setattr(socket.socket, "connect_ex", trip)
    monkeypatch.setattr(socket, "create_connection", trip)
    monkeypatch.setattr(urllib.request, "urlopen", trip)
    monkeypatch.setattr(http.client.HTTPConnection, "request", trip)
    monkeypatch.setattr(http.client.HTTPSConnection, "request", trip)
    fx = dict(A, lines=A["lines"] + [[170, "Them", "Send the %s to everyone." % CANARY]])
    seen = []

    def reply(system, user):
        seen.append(user)
        if system is mw.EXPAND_SYSTEM:
            return {"lines": [{"bullet": "b1", "text": "The launch is set for the 14th.", "spans": [1]}]}
        return {"people": fx["people"], "projects": fx["projects"]}
    r = rig(reply=reply, real_proposer=True)
    mid = r.put(record(fx, summary=done_summary(fx)))
    r.m.pad_add(mid, "launch 14th")
    assert r.m.expand_notes(mid)["status"] == "done"
    assert r.m.propose_memory(mid)["proposed"] > 0
    assert ma.index_meeting(r.m.get(mid))["indexed"] == 2
    assert any(CANARY in u for u in seen), "the model saw the transcript (the canary is the tracer)"
    assert attempts == []
    out = mcal.request(record(fx, event_id=""))          # no event: nothing to write, no network
    assert out["status"] in ("nothing", "unlinked")


def test_a_stage_summary_for_a_cloud_bound_reader_carries_no_meeting_title():
    """The stage cannot tell a local reader from a cloud one, so meetings are counts-only for everyone."""
    from agent_friday.services import screen_stage as ss
    assert "meetings" in ss.PRIVATE_WORKSPACES
    st, _ids = _stage()
    for quiet in (True, False):
        text = ss.summary(st, quiet=quiet) + ss.on_screen_block(st)
        assert "meeting" in text.lower()
        assert "Meeting 1" not in text and "Meeting 2" not in text
    assert st["selection"]["label"] == ""


def test_the_archive_is_offered_only_when_the_library_index_is_encrypted(lib, monkeypatch):
    from agent_friday import core
    from agent_friday.services.library.store import store_for
    st = store_for("owner")
    saved = []
    monkeypatch.setattr(core, "_save_settings", lambda d: saved.append(d))
    # encrypted and keyed: available, and the choice is saved
    assert ma.status()["available"] is True and ma.unavailable() == ""
    lib["choice"] = ""
    assert "refused" not in ma.set_choice("on") and saved == [{"meeting_archive": "on"}]
    # a plain index: nothing is offered, saved or indexed
    saved.clear()
    st.encrypted = False
    out = ma.status()
    assert out["available"] is False and out["unavailable_reason"] == ma.NOT_ENCRYPTED
    assert ma.NOT_ENCRYPTED == "Not available on this PC: the Library index isn't encrypted."
    lib["choice"] = ""
    assert ma.set_choice("on")["refused"] == ma.NOT_ENCRYPTED and saved == []
    lib["choice"] = "on"                       # even if it was chosen before the index lost its encryption
    assert ma.index_meeting(full())["skipped"] == ma.NOT_ENCRYPTED
    assert st.one("SELECT count(*) n FROM documents")["n"] == 0
    st.encrypted = True
    assert ma.index_meeting(full())["indexed"] == 2


def test_the_meeting_tool_is_on_demand_internal_and_local(monkeypatch):
    from agent_friday.governance import action_gate as ag
    from agent_friday.services import agent, meeting_tool as mt, voice_engine as ve
    from agent_friday.services.library import tools as lt
    assert "meeting" in agent.ON_DEMAND_TOOLS and "meeting" in agent.CLAUDE_TOOL_HANDLERS
    assert agent.TOOL_RINGS["meeting"] == 2 and ag.classify("meeting", {})[0] == ag.INTERNAL
    assert '"meeting": [' in (ROOT / "src" / "agent_friday" / "resources" / "tool_groups.json").read_text(encoding="utf-8")
    assert "meeting" not in {t[0] for t in ve._VOICE_LIVE_TOOLS} | set(ve._VOICE_SHARED_TOOLS), "not on the voice contract"
    rows = [{"id": A["id"], "title": "Launch planning", "started_at": "2026-10-09T10:00:00", "summary_status": "done", "state": "done"}]
    monkeypatch.setattr(mw, "recorded", lambda: rows)
    monkeypatch.setattr(lt, "_loop_is_local", lambda: False)
    out = json.loads(mt.meeting({"action": "find", "query": "launch"}))
    assert out == {"matches": 1, "note": out["note"]} and "Launch planning" not in json.dumps(out), "a cloud loop hears counts only"
    monkeypatch.setattr(lt, "_loop_is_local", lambda: True)
    assert json.loads(mt.meeting({"action": "find", "query": "last"}))["meetings"][0]["title"] == "Launch planning"
    calls = []
    fake = types.SimpleNamespace(status=lambda: {"recording": True, "id": A["id"]},
                                 pad_add=lambda mid, t: calls.append(("jot", mid, t)) or {"id": 1},
                                 expand_notes=lambda mid: calls.append(("expand", mid)) or {"status": "done", "lines": [1, 2], "left_out": 1},
                                 propose_memory=lambda mid: calls.append(("remember", mid)) or {"proposed": 3})
    monkeypatch.setattr(mt, "_manager", lambda: fake)
    assert json.loads(mt.meeting({"action": "jot", "text": "budget by Friday"})) == {"added": True, "bullet": 1}
    assert json.loads(mt.meeting({"action": "expand_notes", "query": "last"}))["lines"] == 2
    assert json.loads(mt.meeting({"action": "remember", "meeting_id": A["id"]}))["kept"] is False
    assert [c[0] for c in calls] == ["jot", "expand", "remember"]
    assert "start" not in mt.ACTIONS and mt.meeting({"action": "start"}).startswith("meeting needs action")
