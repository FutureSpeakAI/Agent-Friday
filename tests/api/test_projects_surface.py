"""The HTTP surface for projects.

The house rule these assert, stated in routes/conversations.py: the response
always carries the object as it now IS, with a `note` when what was asked for
did not happen. A refusal that reports success is the bug this codebase keeps
having to fix, and a sidebar that shows a chat in a folder the server never
filed it into is that bug wearing a new hat.
"""
from __future__ import annotations

import pytest

from agent_friday.services import conversations as C
from agent_friday.services import projects as P


@pytest.fixture()
def store(tmp_path, monkeypatch):
    # FRIDAY_DIR is imported into each module at import time, so it is patched
    # per module rather than through the environment. See test_projects.py.
    monkeypatch.setattr(C, "FRIDAY_DIR", str(tmp_path))
    monkeypatch.setattr(P, "FRIDAY_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture()
def client(store):
    from flask import Flask
    from agent_friday.routes.conversations import conversations_bp
    app = Flask(__name__)
    app.register_blueprint(conversations_bp)
    return app.test_client()


def _json(resp):
    return resp.get_json()


def test_create_and_list_a_project(client):
    r = client.post("/api/projects", json={"name": "INNEX",
                                           "seat": {"model": "bonsai2:27b"}})
    assert r.status_code == 201
    p = _json(r)["project"]
    assert p["name"] == "INNEX"
    assert p["seat"] == {"model": "bonsai2:27b"}
    assert p["conversations"] == 0

    listing = _json(client.get("/api/projects"))["projects"]
    assert [x["id"] for x in listing] == [p["id"]]


def test_the_chat_list_carries_the_projects(client):
    """One request draws the whole sidebar. Two would render the folders a
    beat after their contents."""
    p = _json(client.post("/api/projects", json={"name": "INNEX"}))["project"]
    client.post("/api/conversations", json={"title": "a", "project": p["id"]})
    d = _json(client.get("/api/conversations"))
    assert "projects" in d and len(d["projects"]) == 1
    assert d["projects"][0]["conversations"] == 1


def test_a_new_chat_opens_inside_the_folder_you_were_looking_at(client):
    p = _json(client.post("/api/projects", json={"name": "INNEX"}))["project"]
    c = _json(client.post("/api/conversations",
                          json={"title": "x", "project": p["id"]}))["conversation"]
    assert c["project"] == p["id"]


def test_a_summary_reports_the_seat_a_turn_will_actually_use(client):
    """`seat` is the binding, `effective_seat` is what will answer. A row that
    showed only the empty binding would be lying by omission about a chat that
    is going to run on its project's model."""
    p = _json(client.post("/api/projects",
                          json={"name": "INNEX",
                                "seat": {"model": "bonsai2:27b"}}))["project"]
    c = _json(client.post("/api/conversations",
                          json={"title": "x", "project": p["id"]}))["conversation"]
    assert c["seat"] is None
    assert c["effective_seat"] == {"model": "bonsai2:27b"}


def test_filing_into_a_project_that_does_not_exist_is_refused_out_loud(client):
    """A chat pointing at a ghost folder is invisible in every project view
    and belongs to nothing. The refusal has to say so rather than 200-ing."""
    c = _json(client.post("/api/conversations", json={"title": "x"}))["conversation"]
    d = _json(client.patch("/api/conversations/" + c["id"],
                           json={"project": "proj-nope"}))
    assert d["conversation"]["project"] is None
    assert d.get("note"), "a refusal reported success"
    assert "proj-nope" in d["note"]


def test_a_chat_can_be_taken_back_out_of_a_project(client):
    p = _json(client.post("/api/projects", json={"name": "INNEX"}))["project"]
    c = _json(client.post("/api/conversations",
                          json={"title": "x", "project": p["id"]}))["conversation"]
    d = _json(client.patch("/api/conversations/" + c["id"], json={"project": None}))
    assert d["conversation"]["project"] is None
    assert not d.get("note"), "unfiling is always allowed and needs no excuse"


def test_pinning_sends_a_boolean_and_stores_a_time(client):
    """The client says whether; the store says when, so pins order themselves
    without a second field."""
    c = _json(client.post("/api/conversations", json={"title": "x"}))["conversation"]
    assert c["pinned_at"] is None
    on = _json(client.patch("/api/conversations/" + c["id"],
                            json={"pinned_at": True}))["conversation"]
    assert isinstance(on["pinned_at"], (int, float)) and on["pinned_at"] > 0
    off = _json(client.patch("/api/conversations/" + c["id"],
                             json={"pinned_at": False}))["conversation"]
    assert off["pinned_at"] is None


def test_the_message_pin_list_is_not_reachable_over_http(client):
    """`pinned` holds message ids and is managed by clear/prune. Letting the
    sidebar PATCH it would let a UI control quietly decide which messages
    survive the next prune."""
    c = _json(client.post("/api/conversations", json={"title": "x"}))["conversation"]
    client.patch("/api/conversations/" + c["id"], json={"pinned": ["forged"]})
    assert C.load(c["id"])["pinned"] == []


def test_deleting_a_project_keeps_the_chats_and_says_how_many(client):
    p = _json(client.post("/api/projects", json={"name": "Doomed"}))["project"]
    ids = [_json(client.post("/api/conversations",
                             json={"title": "k%d" % i,
                                   "project": p["id"]}))["conversation"]["id"]
           for i in range(2)]
    d = _json(client.delete("/api/projects/" + p["id"]))
    assert d["detached"] == 2
    assert "kept" in (d.get("note") or "")
    for cid in ids:
        got = _json(client.get("/api/conversations/" + cid))["conversation"]
        assert got["project"] is None


def test_deleting_an_empty_project_does_not_claim_it_moved_anything(client):
    p = _json(client.post("/api/projects", json={"name": "Empty"}))["project"]
    d = _json(client.delete("/api/projects/" + p["id"]))
    assert d["detached"] == 0
    assert not d.get("note")


def test_unknown_project_is_a_404_not_a_silent_ok(client):
    assert client.get("/api/projects/proj-nope").status_code == 404
    assert client.patch("/api/projects/proj-nope", json={"name": "x"}).status_code == 404
    assert client.delete("/api/projects/proj-nope").status_code == 404


def test_a_project_page_lists_its_own_chats_only(client):
    a = _json(client.post("/api/projects", json={"name": "A"}))["project"]
    b = _json(client.post("/api/projects", json={"name": "B"}))["project"]
    client.post("/api/conversations", json={"title": "in-a", "project": a["id"]})
    client.post("/api/conversations", json={"title": "in-b", "project": b["id"]})
    client.post("/api/conversations", json={"title": "loose"})
    d = _json(client.get("/api/projects/" + a["id"]))
    assert [c["title"] for c in d["conversations"]] == ["in-a"]
    assert d["project"]["conversations"] == 1


def test_only_listed_fields_are_writable_on_a_project(client):
    p = _json(client.post("/api/projects", json={"name": "A"}))["project"]
    client.patch("/api/projects/" + p["id"],
                 json={"id": "proj-hijack", "created_at": 0})
    assert P.load(p["id"])["id"] == p["id"]
    assert P.load(p["id"])["created_at"] != 0
