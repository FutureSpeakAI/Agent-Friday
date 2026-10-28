"""The forget and show-everywhere routes (routes/memory_forget, NS-15.7-1).

The owner's alone: every route needs a signed-in caller (login_required) and
the owner principal (an observer is refused before the view runs). The three
forgets need ``confirm: true``; inputs are bounded; a Library document is held
to the Library's own page-and-token rule, and forgetting a conversation or
stopping any other source to the same press in Friday's page. The routes hand the request to
services/forget_kinds and return its receipt (tested in
tests/unit/test_forget_kinds.py); here the service is replaced so only the
wiring is under test.

Red on the base: none of these routes exists (404), and there is no
routes/memory_forget to import.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def calls(monkeypatch):
    from agent_friday.services import forget_kinds as fk
    seen = []

    def rec(name):
        def f(*a, **k):
            seen.append((name, a, k))
            return {"ok": True, "kind": name, "stores": [{"store": "x", "removed": 1, "kind": "things",
                                                          "not_removable": [], "retained": []}]}
        return f
    for name in ("forget_fact", "forget_conversation", "stop_using_source", "show_everywhere"):
        monkeypatch.setattr(fk, name, rec(name))
    return seen


def test_forget_fact_needs_confirmation_and_a_bounded_statement(client, calls):
    r = client.post("/api/memory/forget-fact", json={"text": "The user prefers tea."})
    assert r.status_code == 400 and "confirmation" in r.get_json()["error"]
    r = client.post("/api/memory/forget-fact", json={"text": "x" * 401, "confirm": True})
    assert r.status_code == 400
    r = client.post("/api/memory/forget-fact", json={"fact_id": "../../etc", "confirm": True})
    assert r.status_code == 400
    r = client.post("/api/memory/forget-fact", json={"confirm": True})
    assert r.status_code == 400
    assert calls == []
    r = client.post("/api/memory/forget-fact", json={"text": "The user prefers tea.", "confirm": True})
    assert r.status_code == 200 and r.get_json()["ok"] and r.get_json()["stores"][0]["removed"] == 1
    assert calls[-1][0] == "forget_fact" and calls[-1][1] == ("The user prefers tea.",)
    r = client.post("/api/memory/forget-fact", json={"fact_id": "abc123", "confirm": True})
    assert r.status_code == 200 and calls[-1][2]["fact_id"] == "abc123"


def test_forget_fact_takes_only_a_bounded_list_of_confirmed_turn_ids(client, calls):
    for bad in ("m-1", ["../x"], [1], ["m"] * 201):
        r = client.post("/api/memory/forget-fact", json={"text": "tea", "turn_ids": bad, "confirm": True})
        assert r.status_code == 400, bad
    assert calls == []
    r = client.post("/api/memory/forget-fact", json={"text": "tea", "turn_ids": ["m-1", "a1b2"], "confirm": True})
    assert r.status_code == 200 and calls[-1][2]["turn_ids"] == ["m-1", "a1b2"]
    r = client.post("/api/memory/forget-fact", json={"text": "tea", "confirm": True})
    assert r.status_code == 200 and calls[-1][2]["turn_ids"] == []


@pytest.mark.parametrize("path, body", [
    ("/api/memory/forget-conversation", {"conversation_id": "conv-1", "confirm": True}),
    ("/api/memory/stop-using-source", {"source": "meeting:m-1", "confirm": True}),
    ("/api/memory/stop-using-source", {"source": "web:example.com", "confirm": True}),
    ("/api/memory/stop-using-source", {"source": "sender:dana@example.com", "confirm": True}),
])
def test_forgetting_a_conversation_or_stopping_a_source_needs_a_press_in_friday_s_page(client, calls, path, body):
    """Like the Library path: the owner's press in Friday's own page on this PC
    (screen_click.screen_session), answered otherwise with the coded 403 the
    page retries after fetching its desk key."""
    from agent_friday.services import desk_press
    r = client.post(path, json=body)
    assert r.status_code == 403 and r.get_json()["code"] == desk_press.REFUSAL_CODE
    assert calls == []


def test_forget_conversation_and_stop_using_source(client, calls, monkeypatch):
    from agent_friday.services import screen_click
    monkeypatch.setattr(screen_click, "screen_session", lambda request: True)
    assert client.post("/api/memory/forget-conversation", json={"conversation_id": "conv-1"}).status_code == 400
    assert client.post("/api/memory/forget-conversation",
                       json={"conversation_id": "../x", "confirm": True}).status_code == 400
    r = client.post("/api/memory/forget-conversation", json={"conversation_id": "conv-1", "confirm": True})
    assert r.status_code == 200 and calls[-1][:2] == ("forget_conversation", ("conv-1",))

    assert client.post("/api/memory/stop-using-source", json={"source": "meeting:m-1"}).status_code == 400
    assert client.post("/api/memory/stop-using-source",
                       json={"source": "x" * 301, "confirm": True}).status_code == 400
    r = client.post("/api/memory/stop-using-source", json={"source": "meeting:m-1", "confirm": True})
    assert r.status_code == 200 and calls[-1][:2] == ("stop_using_source", ("meeting:m-1",))


def test_a_library_document_needs_friday_s_own_page(client, calls):
    n = len(calls)
    r = client.post("/api/memory/stop-using-source", json={"source": "library:3", "confirm": True})
    assert r.status_code == 403, "a Library forget answered a request without the page's token"
    assert len(calls) == n


def test_show_everywhere_is_a_bounded_get(client, calls):
    assert client.get("/api/memory/show-everywhere").status_code == 400
    assert client.get("/api/memory/show-everywhere?q=" + "x" * 401).status_code == 400
    r = client.get("/api/memory/show-everywhere?q=green%20tea")
    assert r.status_code == 200 and calls[-1][:2] == ("show_everywhere", ("green tea",))
    assert client.post("/api/memory/show-everywhere", json={"q": "x"}).status_code == 405


def test_only_the_owner_principal_reaches_the_views(app, calls):
    from flask import g
    from agent_friday.routes import memory_forget as mf
    with app.test_request_context("/api/memory/show-everywhere?q=tea"):
        g.friday_principal = "observer"
        resp = mf._owner_only()
        assert resp is not None and resp[1] == 403
    with app.test_request_context("/api/memory/show-everywhere?q=tea"):
        g.friday_principal = "user"
        assert mf._owner_only() is None
    assert mf.OWNER_PRINCIPALS == frozenset({"user"})
    # The blueprint runs the check before every one of its views.
    assert mf._owner_only in mf.memory_forget_bp.before_request_funcs.get(None, [])
    assert calls == []


def test_an_observer_token_is_refused(client, calls):
    from agent_friday.services import observer_access as obs
    hdr = {obs.HEADER: client.post("/api/tasks/observer-token").get_json()["token"]}
    assert client.get("/api/memory/show-everywhere?q=tea", headers=hdr).status_code == 403
    assert client.post("/api/memory/forget-fact", headers=hdr,
                       json={"text": "tea", "confirm": True}).status_code == 403
    assert calls == []


@pytest.mark.parametrize("view, path, method, body", [
    ("forget_fact", "/api/memory/forget-fact", "POST", {"text": "tea", "confirm": True}),
    ("forget_conversation", "/api/memory/forget-conversation", "POST", {"conversation_id": "c", "confirm": True}),
    ("stop_using_source", "/api/memory/stop-using-source", "POST", {"source": "meeting:m", "confirm": True}),
    ("show_everywhere", "/api/memory/show-everywhere?q=tea", "GET", None),
])
def test_every_route_needs_a_signed_in_caller(app, calls, monkeypatch, view, path, method, body):
    from agent_friday import core
    from agent_friday.routes import memory_forget as mf
    monkeypatch.setattr(core, "_loopback_trusted", lambda: False)
    monkeypatch.setattr(core, "_HTTP_AUTH_KEY", "")
    with app.test_request_context(path, method=method, json=body):
        out = getattr(mf, view)()
    status = out[1] if isinstance(out, tuple) else out.status_code
    assert status in (401, 403), "%s answered a caller who is not signed in" % path
    assert calls == []
