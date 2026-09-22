"""Reclassifying a message must TEACH, not just move it.

message_triage promises, in its own docstring, that correcting one newsletter
"fixes every future newsletter from that sender". record_signal() implemented
it, /api/messages/learn exposed it, and nothing ever called either: the only
reclassify path in either front-end posts to /api/messages/classify, which
wrote a lane override and stopped. sender_signals.json was 38 bytes with zero
senders after months of real use.

These tests pin the loop closed. The first one is the regression: it fails
against the old route.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import message_triage as mt


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    sig = tmp_path / "sender_signals.json"
    monkeypatch.setattr(mt, "SIGNALS_FILE", str(sig))
    yield


def _signals():
    return mt._load_signals().get("senders") or {}


def _lane():
    ids = mt.lane_ids()
    return ids[0] if ids else "career"


def test_reclassify_records_a_sender_signal(client, monkeypatch):
    """THE REGRESSION. Old route: 0 signals. New route: 1."""
    lane = _lane()
    monkeypatch.setattr(
        "agent_friday.routes.messages._load_cached_messages",
        lambda: [{"id": "m1", "sender": "Recruiter <jobs@example.com>",
                  "subject": "role", "snippet": "hi"}])
    monkeypatch.setattr("agent_friday.routes.messages._cache_messages",
                        lambda rows: None)
    assert _signals() == {}
    r = client.post("/api/messages/classify", json={"id": "m1", "lane": lane})
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "ok"
    assert body["learned"]["ok"] is True
    assert body["learned"]["sender"] == "jobs@example.com"
    assert _signals()["jobs@example.com"]["lanes"][lane] == 1


def test_it_reports_how_close_the_sender_is_to_authority(client, monkeypatch):
    """The countdown is what makes correcting feel like training.

    Also pins that one correction is NOT authoritative - a person who usually
    writes about one thing and once wrote about another should nudge the
    classifier, not rewrite it.
    """
    lane = _lane()
    monkeypatch.setattr(
        "agent_friday.routes.messages._load_cached_messages",
        lambda: [{"id": "m1", "sender": "a@example.com"}])
    monkeypatch.setattr("agent_friday.routes.messages._cache_messages",
                        lambda rows: None)
    first = client.post("/api/messages/classify",
                        json={"id": "m1", "lane": lane}).get_json()["learned"]
    assert first["authoritative"] is False
    assert first["needed_for_authority"] == mt.LEARN_CONFIDENT_AT - 1

    for _ in range(mt.LEARN_CONFIDENT_AT - 1):
        last = client.post("/api/messages/classify",
                           json={"id": "m1", "lane": lane}).get_json()["learned"]
    assert last["authoritative"] is True
    assert last["needed_for_authority"] == 0


def test_learn_can_be_turned_off_per_call(client, monkeypatch):
    """A caller that only wants to move this one message can say so."""
    lane = _lane()
    monkeypatch.setattr(
        "agent_friday.routes.messages._load_cached_messages",
        lambda: [{"id": "m1", "sender": "b@example.com"}])
    monkeypatch.setattr("agent_friday.routes.messages._cache_messages",
                        lambda rows: None)
    body = client.post("/api/messages/classify",
                       json={"id": "m1", "lane": lane, "learn": False}).get_json()
    assert body["status"] == "ok"
    assert body["learned"] is None
    assert _signals() == {}


def test_a_message_not_in_the_cache_still_teaches(client, monkeypatch):
    """Live-only mail is most of the inbox on a fresh fetch.

    Without the sender being remembered in message state, correcting anything
    that had not been cached yet would silently teach nothing - which is the
    same class of bug as the one being fixed, one layer down.
    """
    lane = _lane()
    monkeypatch.setattr("agent_friday.routes.messages._load_cached_messages",
                        lambda: [])
    monkeypatch.setattr("agent_friday.routes.messages._cache_messages",
                        lambda rows: None)
    state = {"m9": {"sender": "live@example.com"}}
    monkeypatch.setattr("agent_friday.routes.messages._load_message_state",
                        lambda: state)
    monkeypatch.setattr("agent_friday.routes.messages._save_message_state",
                        lambda s: state.update(s))
    body = client.post("/api/messages/classify",
                       json={"id": "m9", "lane": lane}).get_json()
    assert body["learned"]["sender"] == "live@example.com"


def test_a_failure_to_learn_still_moves_the_message(client, monkeypatch):
    """The correction is the user's instruction; teaching is a side effect.

    If the signal store is unwritable the move must still happen, or a full
    disk turns the inbox read-only.
    """
    lane = _lane()
    monkeypatch.setattr(
        "agent_friday.routes.messages._load_cached_messages",
        lambda: [{"id": "m1", "sender": "c@example.com"}])
    monkeypatch.setattr("agent_friday.routes.messages._cache_messages",
                        lambda rows: None)

    def _boom(*a, **k):
        raise OSError("signal store unwritable")
    monkeypatch.setattr(mt, "record_signal", _boom)
    body = client.post("/api/messages/classify",
                       json={"id": "m1", "lane": lane}).get_json()
    assert body["status"] == "ok"
    assert body["lane"] == lane
    assert body["learned"]["ok"] is False


def test_an_invalid_lane_is_still_rejected(client):
    r = client.post("/api/messages/classify",
                    json={"id": "m1", "lane": "not-a-lane"})
    assert r.status_code == 400
    assert _signals() == {}
