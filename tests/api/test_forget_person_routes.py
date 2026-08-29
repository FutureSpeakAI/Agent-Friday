"""The delete path has to be reachable by a normal user, not just by me.

A module nobody can call is not a feature. These pin the three routes the
Contacts screen uses:

    GET  /api/people/records/<name>   what is held, and where
    POST /api/people/forget           remove it, and stop it coming back
    GET  /api/people/forgotten        the tombstone list
    POST /api/people/forgotten        lift one

The receipt shape is asserted deliberately: the user reads it back to the
person who asked, so "what was removed" and "what was NOT touched" both have to
survive the round trip through JSON.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def person_on_disk(tmp_path, monkeypatch):
    import agent_friday.core as core

    home = tmp_path / ".friday"
    (home / "knowledge-graph").mkdir(parents=True)
    (home / "wiki" / "people").mkdir(parents=True)
    monkeypatch.setattr(core, "FRIDAY_DIR", home)

    people = {"people": {"dana_okafor": {
        "name": "Dana Okafor", "aliases": ["Dana"], "entity_type": "human",
        "scores": {"overall": 0.8}, "evidence": [{"note": "x"}]}}}
    (home / "people_graph.json").write_text(json.dumps(people), encoding="utf-8")
    (home / "trust_graph.json").write_text(json.dumps(people), encoding="utf-8")
    (home / "knowledge-graph" / "entities.json").write_text(json.dumps([
        {"id": "ent_dana", "title": "Dana Okafor", "type": "person",
         "description": "Colleague.", "tier": "B", "provenance": {}}]), encoding="utf-8")
    (home / "knowledge-graph" / "relationships.json").write_text("[]", encoding="utf-8")
    (home / "knowledge-graph" / "community_reports.json").write_text("[]", encoding="utf-8")
    (home / "wiki" / "people" / "dana-okafor.md").write_text(
        "# Dana Okafor\n\nNotes.\n", encoding="utf-8")
    return home


def test_records_route_reports_what_is_held(client, person_on_disk):
    r = client.get('/api/people/records/Dana Okafor')
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "ok"
    report = body["report"]
    assert report["found"] is True
    assert report["people_graph"]["present"] is True
    assert report["knowledge_graph"]["entities"] == 1


def test_forget_route_removes_and_returns_an_honest_receipt(client, person_on_disk):
    r = client.post('/api/people/forget', json={"name": "Dana Okafor"})
    assert r.status_code == 200
    receipt = r.get_json()["receipt"]

    assert receipt["removed"]["people_graph"] == 1
    assert receipt["removed"]["kg_entities"] == 1
    # The half that keeps the user honest with the person who asked.
    assert any("dana-okafor.md" in s for s in receipt["remaining_sources"])
    assert "your words" in receipt["note"] or "your notes" in receipt["note"]

    after = client.get('/api/people/records/Dana Okafor').get_json()["report"]
    assert after["people_graph"]["present"] is False
    assert after["knowledge_graph"]["entities"] == 0
    assert after["forgotten"] is True


def test_forget_route_requires_a_name(client, person_on_disk):
    assert client.post('/api/people/forget', json={}).status_code == 400


def test_forgotten_list_round_trips(client, person_on_disk):
    client.post('/api/people/forget', json={"name": "Dana Okafor"})

    listed = client.get('/api/people/forgotten').get_json()["forgotten"]
    assert [e["name"] for e in listed] == ["Dana Okafor"]

    r = client.post('/api/people/forgotten', json={"name": "Dana Okafor"})
    assert r.status_code == 200
    assert r.get_json()["result"]["lifted"] == 1
    assert client.get('/api/people/forgotten').get_json()["forgotten"] == []
