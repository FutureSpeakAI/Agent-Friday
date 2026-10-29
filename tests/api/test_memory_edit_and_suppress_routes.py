"""The owner's routes for NS-15.5-1: edit a suggestion and keep their wording,
"Don't suggest things like this", and the Suppressed list.

Red on the base (0054889d2): /api/core-memory/review ignored `text` (the
suggested wording was kept), /api/core-memory/suppress, /suppressed and
/suppressed/remove and /api/memory/proposals/suppress did not exist (404), and
/api/memory/proposals/approve ignored `text`.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from agent_friday.services import core_memory as cm
from agent_friday.services import memory_proposals as mp
from agent_friday.services import prompt_freeze
from agent_friday.services import user_model as um


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "_dir", lambda: tmp_path / "core_memory")
    monkeypatch.setattr(cm, "_MIGRATION_CHECKED", {str(tmp_path / "core_memory"): True})
    monkeypatch.setattr(mp, "DB_PATH", tmp_path / "proposals.db")
    monkeypatch.setattr(um, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(um, "DB_PATH", tmp_path / "user_model.db")
    from agent_friday.services.knowledge_graph import integration as kgi
    monkeypatch.setattr(kgi, "ingest_fact", lambda text, **kw: "fact_x")
    prompt_freeze.reset()
    yield
    prompt_freeze.reset()


def _post(client, path, body):
    r = client.post(path, json=body)
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    return r.get_json()


def test_edit_and_keep_through_the_card_route(client):
    q = cm.queue("user", "add", text="Prefers long detailed answers.", source="dream:2026-10-01")
    d = _post(client, "/api/core-memory/review",
              {"id": q["id"], "keep": True, "text": "Prefers short answers, bullet points first."})
    assert d["ok"] and d.get("edited"), d
    assert cm.entries("user") == ["Prefers short answers, bullet points first."]
    # /decide is the same route; a non-string text is refused.
    q = cm.queue("user", "add", text="Plays chess on Sundays.", source="review")
    assert _post(client, "/api/core-memory/decide", {"id": q["id"], "keep": True, "text": 5})["ok"] is False
    assert _post(client, "/api/core-memory/decide", {"id": q["id"], "keep": True,
                                                     "text": "x" * (cm.ENTRY_MAX + 1)})["ok"] is False
    assert len(cm.pending()) == 1


def test_suppress_list_and_remove_through_the_card_routes(client):
    q = cm.queue("user", "add", text="Prefers terse answers in dark mode.", source="review")
    d = _post(client, "/api/core-memory/suppress", {"id": q["id"]})
    assert d["ok"] and d.get("suppressed"), d
    assert cm.pending() == []
    r = cm.queue("user", "add", text="Likes dark mode with terse answers.", source="review")
    assert r.get("suppressed") and cm.pending() == []
    listed = client.get("/api/core-memory/suppressed").get_json()
    assert listed["ok"] and len(listed["rules"]) == 1 and listed["dropped_total"] == 1
    assert "Prefers terse answers in dark mode." not in json.dumps(listed)
    rid = listed["rules"][0]["id"]
    assert _post(client, "/api/core-memory/suppressed/remove", {"id": rid})["ok"]
    assert client.get("/api/core-memory/suppressed").get_json()["rules"] == []
    r = cm.queue("user", "add", text="Likes dark mode with terse answers.", source="review")
    assert r["ok"] and not r.get("suppressed") and len(cm.pending()) == 1


def _propose(facts, day="2026-08-23"):
    seat = {"assigned": True, "model": "gemma4:12b", "provider": "ollama-local", "reason": ""}
    turns = [{"role": "user", "text": f"turn {i} about the week"} for i in range(5)]
    with patch.object(mp, "seat", return_value=seat), \
         patch.object(mp, "_ask_seat", return_value=json.dumps(facts)), \
         patch("agent_friday.services.memory_dreaming._pull_turns", return_value=(turns, False)):
        return mp.propose(day)


def test_proposal_edit_and_keep_and_suppress_through_the_routes(client):
    _propose([{"category": "preference", "text": "The user likes long reports.", "evidence": "x"},
              {"category": "preference", "text": "The user prefers terse answers in dark mode.",
               "evidence": "y"}])
    by_text = {p["text"]: p["fact_id"] for p in mp.pending()}
    fid = by_text["The user likes long reports."]
    d = _post(client, "/api/memory/proposals/approve",
              {"fact_ids": [fid], "text": "Likes a one-page summary first."})
    assert d["ok"] and d["approved"] == 1 and d["facts"][0]["text"] == "Likes a one-page summary first.", d
    assert cm.entries("user") == ["Likes a one-page summary first."]
    # An edit names exactly one suggestion.
    assert _post(client, "/api/memory/proposals/approve",
                 {"fact_ids": ["a", "b"], "text": "anything"})["ok"] is False
    d = _post(client, "/api/memory/proposals/suppress",
              {"fact_id": by_text["The user prefers terse answers in dark mode."]})
    assert d["ok"] and d.get("suppressed"), d
    assert mp.pending() == []
    out = _propose([{"category": "preference", "text": "User prefers dark mode and terse answers.",
                     "evidence": "z"}], day="2026-08-24")
    assert out["stored"] == 0 and out.get("dropped_suppressed") == 1
