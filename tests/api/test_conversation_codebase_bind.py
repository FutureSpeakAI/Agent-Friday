"""A chat binds to a codebase by one call (Chat Hub M3a, the Build switch):
`POST /api/conversations/<cid>/codebase {codebase}` makes the chat's panel the
Build panel for that codebase; `{codebase: null}` unbinds. Both sides are
written together (the conversation carries the codebase, the codebase names
the conversation), as `codebases.bind` always did.
"""
from __future__ import annotations

import pytest

from agent_friday.services import codebases as cb
from agent_friday.services import conversations as convs
from agent_friday.services import projects


@pytest.fixture(autouse=True)
def _roots(monkeypatch, tmp_path):
    monkeypatch.setattr(projects, "_root", lambda: tmp_path / "projects")
    monkeypatch.setattr(convs, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(cb, "_root", lambda: tmp_path / "codebases")
    yield


def test_a_chat_binds_to_a_codebase_and_unbinds(client):
    conv = client.post("/api/conversations", json={"title": "Field notes"}).get_json()["conversation"]
    rec = cb.create("Rent tracker", template="static")
    r = client.post(f"/api/conversations/{conv['id']}/codebase", json={"codebase": rec["id"]})
    assert r.status_code == 200, r.data[:300]
    assert r.get_json()["conversation"]["codebase"] == rec["id"]
    assert convs.load(conv["id"])["codebase"] == rec["id"]
    assert cb.load(rec["id"])["conversation_id"] == conv["id"]
    r = client.post(f"/api/conversations/{conv['id']}/codebase", json={"codebase": None})
    assert r.status_code == 200
    assert not convs.load(conv["id"]).get("codebase")
    assert not cb.load(rec["id"]).get("conversation_id")


def test_binding_to_nothing_or_to_an_unknown_codebase_is_refused(client):
    conv = client.post("/api/conversations", json={"title": "Plain"}).get_json()["conversation"]
    assert client.post(f"/api/conversations/{conv['id']}/codebase", json={"codebase": "cb-nope"}).status_code == 404
    assert client.post("/api/conversations/conv-nope/codebase", json={"codebase": "x"}).status_code == 404


def test_the_summary_names_the_bound_codebase_so_the_hub_can_show_it(client):
    conv = client.post("/api/conversations", json={"title": "Field notes"}).get_json()["conversation"]
    rec = cb.create("Rent tracker", template="static")
    client.post(f"/api/conversations/{conv['id']}/codebase", json={"codebase": rec["id"]})
    rows = client.get("/api/conversations").get_json()["conversations"]
    mine = next(c for c in rows if c["id"] == conv["id"])
    assert mine["codebase"] == rec["id"]
