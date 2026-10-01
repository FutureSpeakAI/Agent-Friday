"""The Media routes: the gate on publish, and the shapes the page reads."""
from __future__ import annotations

import json
import time

import pytest

import agent_friday.core as core
from agent_friday.services import media_index as mi


@pytest.fixture
def home(tmp_path, monkeypatch):
    fd = tmp_path / ".friday"
    fd.mkdir()
    creations = tmp_path / "friday-creations"
    creations.mkdir()
    monkeypatch.setattr(core, "FRIDAY_DIR", fd)
    monkeypatch.setattr(core, "CREATIONS_DIR", creations)
    monkeypatch.setattr(core, "DAILY_CREATIONS_DIR", fd / "creations")
    from agent_friday.services import office_engine, content_pipeline as cp, creative_engine as ce, approvals
    (fd / "documents").mkdir()
    monkeypatch.setattr(office_engine, "DOCUMENTS_DIR", fd / "documents")
    (fd / "creations_meta").mkdir()
    monkeypatch.setattr(ce, "CREATIVE_META_DIR", fd / "creations_meta")
    monkeypatch.setattr(cp, "DB_PATH", fd / "content_pipeline.db")
    (fd / "content").mkdir()
    monkeypatch.setattr(cp, "PUBLISH_LOG", fd / "content" / "publish_log.jsonl")
    # Every store this test reads or writes lives under its own home: nothing from an
    # earlier test's home can leak in, and nothing leaks out.
    from agent_friday.services import misc_engine, provenance, approvals
    monkeypatch.setattr(misc_engine, "CONTENT_DIR", fd / "content")
    monkeypatch.setattr(misc_engine, "CONTENT_PIPELINE_FILE", fd / "content" / "pipeline.json")
    monkeypatch.setattr(provenance, "PROVENANCE_DIR", fd / "provenance", raising=False)
    monkeypatch.setattr(approvals, "APPROVALS_FILE", fd / "approvals.json", raising=False)
    monkeypatch.setattr(approvals, "APPROVALS_FILE", fd / "approvals.json", raising=False)
    (creations / "friday-text-ferry.md").write_text("# The ferry story\n\nThe 06:40 left on time.", encoding="utf-8")
    mi.reindex()
    return fd


def test_list_get_and_patch(client, home):
    r = client.get("/api/media?view=all")
    d = r.get_json()
    assert r.status_code == 200 and d["status"] == "ok" and d["counts"]["all"] == 1
    cid = d["cards"][0]["id"]
    g = client.get("/api/media/" + cid).get_json()
    assert g["status"] == "ok" and g["card"]["kind"] == "article" and g["body"].startswith("# The ferry story")
    p = client.patch("/api/media/" + cid, json={"status": "review", "project": "Harbour series"})
    assert p.status_code == 200 and p.get_json()["card"]["status"] == "review"
    assert client.get("/api/media?view=review").get_json()["counts"]["review"] == 1


def _draft(client):
    return client.post("/api/media", json={"kind": "draft", "title": "Reply to the board", "body": "Thanks for the figures."}).get_json()["card"]["id"]


def test_a_move_into_published_by_patch_is_refused(client, home):
    cid = _draft(client)
    p = client.patch("/api/media/" + cid, json={"status": "published"})
    assert p.status_code == 403
    assert client.get("/api/media/" + cid).get_json()["card"]["status"] == "idea"


def test_publish_raises_the_one_approval_card_and_nothing_goes_out_before_it(client, home):
    cid = _draft(client)
    r = client.post("/api/media/" + cid + "/publish")
    d = r.get_json()
    assert r.status_code == 202 and d["status"] == "pending", d
    from agent_friday.services import approvals
    recs = [a for a in approvals.list_approvals(kind="governed_action") if (a.get("payload") or {}).get("card") == cid]
    assert recs and recs[0]["status"] == "pending"
    c = client.get("/api/media/" + cid).get_json()["card"]
    assert c["status"] == "idea" and c["privacy"] == "private", "nothing happens before the owner approves"
    # approving the card is what publishes
    approvals.decide(recs[0]["approval_id"], "approve", decided_by="user")
    c = client.get("/api/media/" + cid).get_json()["card"]
    assert c["status"] == "published" and c["privacy"] == "published" and c["published_at"].startswith("This PC")
    # and a second ask for the same card is answered by the same, now used, card: no silent repeat
    r2 = client.post("/api/media/" + cid + "/publish")
    assert r2.get_json()["status"] in ("ok", "pending")


def test_new_card_body_and_turn_into(client, home):
    r = client.post("/api/media", json={"kind": "draft", "title": "Weekly note", "body": "A line."})
    assert r.status_code == 200
    cid = r.get_json()["card"]["id"]
    assert client.put("/api/media/" + cid + "/body", json={"text": "Two lines.\n\nMore."}).status_code == 200
    assert client.get("/api/media/" + cid).get_json()["body"] == "Two lines.\n\nMore."
    t = client.post("/api/media/" + cid + "/turn-into", json={"kind": "article"})
    assert t.status_code == 200 and t.get_json()["card"]["kind"] == "article"
    assert client.post("/api/media/" + cid + "/turn-into", json={"kind": "deck"}).status_code == 501


def test_the_calendar_route_and_the_route_manifest(client, home):
    r = client.get("/api/media/calendar?from=2026-09-28&to=2026-10-11")
    assert r.status_code == 200 and r.get_json()["status"] == "ok"
    from agent_friday import server
    assert "media" in server.ROUTE_MODULES
