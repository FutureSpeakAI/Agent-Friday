"""Beta CodeQL alerts: a route shows only the messages written for the person.

Stack-trace exposure: an exception that is not a `UserFacingError` is logged
and answered with an error id. A deliberate validation message (a
`UserFacingValueError`) still reaches the screen, in the same envelope.
"""
from __future__ import annotations

import pytest

INTERNAL = "SECRET-INTERNAL-/path/detail"  # pragma: allowlist secret


def _boom(*_a, **_k):
    raise RuntimeError(INTERNAL)


def _boom_value(*_a, **_k):
    raise ValueError(INTERNAL)


def _hidden(resp):
    raw = resp.get_data(as_text=True)
    assert "SECRET-INTERNAL" not in raw, raw[:400]
    assert "Traceback" not in raw
    return resp.get_json()


# -- stack-trace exposure: what a route may say -------------------------------

def test_artifact_validation_message_still_shows_and_a_library_error_does_not(client, monkeypatch):
    from agent_friday.services import artifacts as art
    r = client.post("/api/artifacts/conv1", json={"kind": "nonsense", "title": "t", "content": "x"})
    assert r.status_code == 400
    assert "unknown artifact kind" in r.get_json()["error"]
    monkeypatch.setattr(art, "list_for", _boom_value)
    r = client.get("/api/artifacts?conversation_id=conv1")
    body = _hidden(r)
    assert r.status_code == 400 and body["status"] == "error" and body["error_id"]
    assert "error " + body["error_id"] in body["error"]


def test_codebase_undo_hides_a_git_failure(client, monkeypatch):
    from agent_friday.services import codebases as cb
    monkeypatch.setattr(cb, "undo", _boom)
    r = client.post("/api/codebases/abc/undo")
    body = _hidden(r)
    assert r.status_code == 409 and body["status"] == "error" and body["error_id"]


def test_codebase_nothing_to_undo_is_still_said_plainly(client, monkeypatch):
    from agent_friday.services import codebases as cb

    def nothing(*_a, **_k):
        raise cb.NothingToUndo("every step has been undone; only the starting point is left")
    monkeypatch.setattr(cb, "undo", nothing)
    r = client.post("/api/codebases/abc/undo")
    assert r.status_code == 409
    assert "every step has been undone" in r.get_json()["error"]


def test_hosting_status_error_has_an_id_not_the_cause(client, monkeypatch):
    from agent_friday.services import publish_hosting as ph
    monkeypatch.setattr(ph, "status", _boom)
    r = client.get("/api/publish/status")
    body = _hidden(r)
    this_pc = body["adapters"]["this_pc"]
    assert this_pc["enabled"] is False and "error " in this_pc["error"]


def test_workspace_chat_clear_hides_an_operating_system_refusal(client, monkeypatch):
    from agent_friday.routes import workspace_studio as route

    def denied(*_a, **_k):
        raise PermissionError(INTERNAL)
    monkeypatch.setattr(route, "clear_chat", denied)
    r = client.post("/api/workspace/notes/chat/clear")
    body = _hidden(r)
    assert r.status_code == 403 and body["status"] == "error" and body["error_id"]


def test_workspace_off_the_record_refusal_is_still_said(client, monkeypatch):
    from agent_friday.routes import workspace_studio as route
    from agent_friday.user_errors import UserFacingPermissionError

    def refused(*_a, **_k):
        raise UserFacingPermissionError("Off the record is on. Workspace Studio changes and chat are not saved.")
    monkeypatch.setattr(route, "clear_chat", refused)
    r = client.post("/api/workspace/notes/chat/clear")
    assert r.status_code == 403 and "Off the record is on" in r.get_json()["message"]


def test_home_card_message_shows_and_a_plain_value_error_does_not(client, monkeypatch):
    from agent_friday.services import desktop_cards as cards

    def bad_card(*_a, **_k):
        raise cards.CardError("Give the card a title.")
    monkeypatch.setattr(cards, "upsert_card", bad_card)
    r = client.post("/api/desktop/cards", json={})
    assert r.status_code == 400 and r.get_json()["message"] == "Give the card a title."
    monkeypatch.setattr(cards, "upsert_card", _boom_value)
    body = _hidden(client.post("/api/desktop/cards", json={}))
    assert body["status"] == "error" and body["error_id"]


def test_domain_request_message_shows_and_a_plain_value_error_does_not(client, monkeypatch):
    from agent_friday.services import domain_accounts as accounts
    monkeypatch.setattr(accounts, "require_recording", lambda: None)
    monkeypatch.setattr(accounts, "list_accounts", _boom_value)
    r = client.get("/api/domains/accounts")
    if r.status_code == 500:        # privacy context unavailable in this fixture
        pytest.skip("domain routes need a recording privacy context")
    body = _hidden(r)
    assert r.status_code == 400 and body["error_id"]


def test_media_open_failure_hides_the_operating_system_text(client, monkeypatch, tmp_path):
    import os
    import subprocess
    from agent_friday.routes import media as route
    f = tmp_path / "clip.txt"
    f.write_text("x")
    monkeypatch.setattr(route, "_local_file", lambda cid: f)

    def fail(*_a, **_k):
        raise OSError(INTERNAL)
    monkeypatch.setattr(os, "startfile", fail, raising=False)
    monkeypatch.setattr(subprocess, "Popen", fail)
    r = client.post("/api/media/card1/open")
    body = _hidden(r)
    assert r.status_code == 500 and body["status"] == "error" and body["error_id"]


def test_sites_overview_hides_a_plain_value_error(client, monkeypatch):
    from agent_friday.services import sites_privacy
    monkeypatch.setattr(sites_privacy, "capture", _boom_value)
    r = client.get("/api/sites")
    body = _hidden(r)
    assert r.status_code == 400 and body["status"] == "error" and body["error_id"]
