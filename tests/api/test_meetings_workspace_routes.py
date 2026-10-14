"""The Meetings workspace's API: the pad, notes with spans, proposals, the calendar card, the archive.

The blueprint is mounted on a bare Flask app (the routes touch only the meeting manager), and every model is a
mock. Remote callers are refused exactly as they are for the rest of /api/meetings."""
from __future__ import annotations

import json
import threading
import types
from pathlib import Path

import pytest
from flask import Flask

from agent_friday.routes.meetings import meetings_bp
from agent_friday.services import meeting_archive as ma
from agent_friday.services import meeting_calendar as mcal
from agent_friday.services import meeting_capture as mc

ROOT = Path(__file__).resolve().parents[2]
A = json.loads((ROOT / "tests" / "fixtures" / "meeting_synthetic.json").read_text(encoding="utf-8"))["meetings"][0]


def record(**kw):
    segs = [{"t": 0, "kind": "consent", "text": "Recording started."}]
    segs += [{"t": t, "kind": "speech", "speaker": w, "text": x} for t, w, x in A["lines"]]
    rec = {"id": A["id"], "title": A["title"], "event_id": "", "started_at": "2026-10-09T10:00:00", "ended_at": "2026-10-09T10:05:00",
           "duration_s": 300, "state": "done", "stop_reason": "owner", "source": "microphone", "system_audio": False,
           "keep_audio": False, "segments": segs, "summary": {"status": "pending"}, "pad": [], "tasks_created": [], "follow_ups": []}
    rec.update(kw)
    return rec


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "_root", lambda: tmp_path / "meetings")
    proposed = []

    def reply(system, user):
        from agent_friday.services import meeting_workspace as mw
        if system is mw.EXPAND_SYSTEM:
            return {"lines": A["model_lines"]}
        return {"people": A["people"], "projects": A["projects"]}
    m = mc.MeetingManager(
        source_factory=lambda max_s, **k: None, transcriber=lambda pcm: "", seat_resolver=lambda: "local-test-model",
        notes_call=lambda s, u, model: reply(s, u), asr_check=lambda: "", lock_detector=lambda: None,
        event_lookup=lambda: None, notifier=lambda *a: None, scheduler=lambda mid: None, partials_factory=lambda: None,
        ledger=lambda *a, **k: None, retention=lambda: "", archive_index=lambda m_: None, archive_remove=lambda mid: None,
        proposer=lambda c, mid, day, model: proposed.append((c, mid, model)) or len(c), tick_s=0.05)
    m.proposed = proposed
    monkeypatch.setattr(mc, "_manager", m)
    m._save(record(summary={"status": "done", "model": "notes-model", "notes": [], "decisions": A["decisions"],
                            "action_items": [{"task": t, "owner": None, "due": None} for t in A["gold_actions"]] +
                                            [{"task": "Book a venue in Osaka", "owner": None, "due": None}]},
                   pad=[dict(b) for b in A["pad"]]))
    yield m


@pytest.fixture
def client(mgr):
    app = Flask(__name__)
    app.config.update(TESTING=True)
    app.register_blueprint(meetings_bp)
    return app.test_client()


def test_the_pad_takes_bullets_and_gives_them_back(client):
    mid = A["id"]
    r = client.post("/api/meetings/%s/pad" % mid, json={"text": "  venue   contract "})
    assert r.status_code == 200 and r.get_json()["item"]["text"] == "venue contract"
    pad = client.get("/api/meetings/%s/pad" % mid).get_json()["pad"]
    assert [b["text"] for b in pad][-1] == "venue contract" and len(pad) == len(A["pad"]) + 1
    assert client.delete("/api/meetings/%s/pad/%d" % (mid, pad[-1]["id"])).status_code == 200
    assert client.delete("/api/meetings/%s/pad/999" % mid).status_code == 404
    assert client.post("/api/meetings/%s/pad" % mid, json={"text": "  "}).get_json()["code"] == "empty"
    assert client.get("/api/meetings/%s/pad" % ("f" * 12)).status_code == 404


def test_the_transcript_is_numbered_for_the_citations(client):
    lines = client.get("/api/meetings/%s/lines" % A["id"]).get_json()["lines"]
    assert [ln["i"] for ln in lines] == list(range(len(A["lines"])))
    assert lines[1]["clock"] == "00:00:20" and lines[1]["speaker"] == "Them"


def test_polishing_returns_lines_that_each_cite_a_span(client):
    out = client.post("/api/meetings/%s/polish" % A["id"]).get_json()
    assert out["status"] == "ok" and out["polished"]["status"] == "done"
    lines = out["polished"]["lines"]
    assert len(lines) == len(A["keep"]) and all(ln["spans"] for ln in lines)
    assert out["polished"]["left_out"] == len(A["model_lines"]) - len(A["keep"])
    assert client.get("/api/meetings/%s" % A["id"]).get_json()["meeting"]["polished"]["lines"] == lines


def test_action_items_say_which_trace_to_the_transcript(client):
    items = client.get("/api/meetings/%s/actions" % A["id"]).get_json()["items"]
    assert [i["traced"] for i in items] == [True, True, True, False]
    assert items[3]["task"] == "Book a venue in Osaka" and items[0]["span"]["i"] >= 0


def test_memory_proposals_are_made_and_listed_per_meeting(client, mgr, monkeypatch):
    from agent_friday.services import memory_proposals as mp
    out = client.post("/api/meetings/%s/memory" % A["id"]).get_json()
    assert out["status"] == "ok" and out["proposed"] == 5 and out["left_out"] == 1
    assert {c["category"] for call in mgr.proposed for c in call[0]} == {"relationship", "project", "decision"}
    monkeypatch.setattr(mp, "pending", lambda **k: [
        {"fact_id": "f1", "text": "x", "provenance": {"meeting_id": A["id"], "span": {"segment": 1, "t": 20}, "model": "m"}},
        {"fact_id": "f2", "text": "y", "provenance": {"meeting_id": "0" * 12}},
        {"fact_id": "f3", "text": "z"}])
    got = client.get("/api/meetings/%s/memory" % A["id"]).get_json()["proposals"]
    assert [p["fact_id"] for p in got] == ["f1"]


def test_the_calendar_route_returns_a_card_not_a_write(client, monkeypatch):
    monkeypatch.setattr(mcal, "request", lambda m, **k: {"status": "pending_approval", "approval_id": "ap_1", "message": "Waiting."})
    r = client.post("/api/meetings/%s/calendar" % A["id"])
    assert r.status_code == 200 and r.get_json()["approval_id"] == "ap_1" and r.get_json()["ok"] is True
    monkeypatch.setattr(mcal, "request", lambda m, **k: {"status": "error", "message": "I couldn't reach your calendar just now."})
    r = client.post("/api/meetings/%s/calendar" % A["id"])
    assert r.status_code == 409 and r.get_json()["ok"] is False


def test_the_archive_choice_is_the_owners_and_indexing_runs_off_the_request(client, monkeypatch):
    from agent_friday.services import screen_click
    monkeypatch.setattr(screen_click, "screen_session", lambda request: True)       # the page's own click
    monkeypatch.setattr(ma, "status", lambda **k: {"choice": "", "documents": 0, "scope": "meetings", "index_encrypted": True})
    assert client.get("/api/meetings/archive").get_json()["choice"] == ""
    assert client.post("/api/meetings/archive", json={"choice": "maybe"}).status_code == 400
    done, saved = threading.Event(), []
    monkeypatch.setattr(ma, "set_choice", lambda c, **k: saved.append(c) or {"choice": c, "documents": 0})
    monkeypatch.setattr(ma, "reindex_all", lambda manager, **k: done.set())
    out = client.post("/api/meetings/archive", json={"choice": "on"}).get_json()
    assert out["status"] == "ok" and saved == ["on"] and done.wait(5)
    done.clear()
    client.post("/api/meetings/archive", json={"choice": "off"})
    assert saved == ["on", "off"] and not done.wait(0.3), "turning it off indexes nothing"


def test_a_remote_caller_gets_none_of_it(client):
    for method, path in (("get", "/api/meetings/%s/pad" % A["id"]), ("post", "/api/meetings/%s/polish" % A["id"]),
                         ("get", "/api/meetings/archive"), ("post", "/api/meetings/%s/calendar" % A["id"])):
        r = getattr(client, method)(path, environ_overrides={"REMOTE_ADDR": "203.0.113.9"})
        assert r.status_code == 403, path


def test_choosing_the_archive_on_a_plain_index_is_refused_in_plain_words(client, monkeypatch):
    from agent_friday.services import screen_click
    monkeypatch.setattr(screen_click, "screen_session", lambda request: True)
    monkeypatch.setattr(ma, "set_choice", lambda c, **k: {"choice": "", "refused": ma.NOT_ENCRYPTED})
    r = client.post("/api/meetings/archive", json={"choice": "on"})
    assert r.status_code == 409 and r.get_json()["error"] == "Not available on this PC: the Library index isn't encrypted."
