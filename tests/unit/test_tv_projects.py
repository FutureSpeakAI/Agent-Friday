"""The desktop's projects on the owner's phone and tablet.

Same data as the desktop, no device copy: the gateway reads the desktop's own /api/projects routes. Guarded here:

* the list is the desktop's project list (archived ones stay off, as in its sidebar), without seats or codebases;
* a project opens with its chats (rows like /tv/v1/chats: last message, newest first) and its files by name and size, never
  a host path; its instructions are capped;
* an unknown project is a 404 the device can say; a bad id never reaches the desktop;
* only an owner phone or tablet holding ``pc_read`` is answered;
* end to end on the desktop's real routes and stores: a project made on the desktop, a chat filed in it and a file added
  to it all show on the device, and a chat started from the device inside it lands in it.
"""
import importlib
import json

import pytest

from agent_friday.tv_gateway import pc_read
from tests.unit import test_tv_pc_read as shared
from tests.unit.test_tv_gateway import auth, open_session
from tests.unit.test_tv_pc_read import get, paired

client = shared.client
_clean = shared._clean


@pytest.fixture
def real_desk(tmp_path, monkeypatch):
    """The desktop's own conversation and project routes on temp stores, answering the gateway's in-process calls."""
    from flask import Flask
    from agent_friday.routes import conversations as routes
    from agent_friday.services import conversations, projects
    monkeypatch.setattr(conversations, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(projects, "_root", lambda: tmp_path / "projects")
    desk = Flask("desk")
    desk.register_blueprint(routes.conversations_bp)
    dc = desk.test_client()
    calls = []

    def desk_get(path, params=None):
        calls.append(path)
        r = dc.get(path, query_string=params or {})
        return r.status_code, r.get_data(), r.headers.get("Content-Type", "application/json")

    def desk_post(path, body):
        r = dc.post(path, json=body)
        return r.status_code, r.get_json(silent=True) or {}
    monkeypatch.setattr(pc_read, "_desk", desk_get)
    pc_chats = importlib.import_module("agent_friday.tv_gateway.pc_chats")
    monkeypatch.setattr(pc_chats, "_desk_post", desk_post)
    from agent_friday.services import notice_registry
    monkeypatch.setattr(notice_registry, "bus_broadcast", lambda *a, **k: 0)
    return dc, calls


def test_the_project_list_is_the_desktops(client, real_desk):
    dc, _ = real_desk
    a = dc.post("/api/projects", json={"name": "Taxes 2026", "instructions": "Keep receipts."}).get_json()["project"]
    b = dc.post("/api/projects", json={"name": "Old stuff"}).get_json()["project"]
    dc.patch("/api/projects/" + b["id"], json={"archived": True})
    r = get(client, paired(), "/tv/v1/projects")
    assert r.status_code == 200
    rows = r.get_json()["projects"]
    assert [p["name"] for p in rows] == ["Taxes 2026"] and rows[0]["id"] == a["id"]
    assert "seat" not in rows[0] and "codebases" not in rows[0]


def test_a_project_opens_with_its_chats_and_files(client, real_desk):
    dc, _ = real_desk
    p = dc.post("/api/projects", json={"name": "Garden", "instructions": "x" * 5000}).get_json()["project"]
    c = dc.post("/api/conversations", json={"title": "Beds", "project": p["id"]}).get_json()["conversation"]
    dc.post("/api/conversations", json={"title": "Not in it"})
    dc.post("/api/projects/%s/files" % p["id"], json={"name": "plan.md", "content": "# Beds\n"})
    r = get(client, paired("tablet"), "/tv/v1/projects/" + p["id"])
    assert r.status_code == 200
    got = r.get_json()
    assert got["project"]["name"] == "Garden" and len(got["project"]["instructions"]) == 2000
    assert [x["id"] for x in got["conversations"]] == [c["id"]] and "last" in got["conversations"][0]
    assert got["files"] == [{"name": "plan.md", "bytes": 7, "added_at": got["files"][0]["added_at"]}]
    assert "C:\\" not in json.dumps(got) and "/Users/" not in json.dumps(got)


def test_a_chat_started_on_the_device_in_a_project_lands_in_it(client, real_desk):
    dc, _ = real_desk
    p = dc.post("/api/projects", json={"name": "Garden"}).get_json()["project"]
    dev = paired()
    r = client.post("/tv/v1/chats", data=json.dumps({"project": p["id"]}), content_type="application/json",
                    headers=auth(open_session(client, dev)))
    assert r.status_code == 201
    cid = r.get_json()["conversation_id"]
    on_desktop = dc.get("/api/projects/" + p["id"]).get_json()["conversations"]
    assert [c["id"] for c in on_desktop] == [cid]
    assert [c["id"] for c in get(client, dev, "/tv/v1/projects/" + p["id"]).get_json()["conversations"]] == [cid]


def test_unknown_and_bad_ids(client, real_desk):
    _, calls = real_desk
    dev = paired()
    r = get(client, dev, "/tv/v1/projects/proj-nope")
    assert r.status_code == 404 and r.get_json()["code"] == "unknown_project"
    calls.clear()
    assert get(client, dev, "/tv/v1/projects/.hidden").status_code == 400 and calls == []


@pytest.mark.parametrize("path", ["/tv/v1/projects", "/tv/v1/projects/proj-1"])
@pytest.mark.parametrize("kind,member,right", [("watch", "stephen", True), ("tv", "family", True), ("phone", "stephen", False)])
def test_only_an_owner_phone_or_tablet_with_pc_read_reads_projects(client, real_desk, path, kind, member, right):
    from agent_friday.services import device_pairing
    _, calls = real_desk
    dev = paired(kind, member, right=False)
    if right:
        device_pairing.set_right(dev.id, "pc_read", True)
    assert get(client, dev, path).status_code == 403 and calls == []


def test_a_files_read_that_fails_is_a_refusal_not_an_empty_list(client, real_desk, monkeypatch):
    dc, _ = real_desk
    pid = dc.post("/api/projects", json={"name": "Garden"}).get_json()["project"]["id"]
    real = pc_read._desk

    def flaky(path, params=None):
        if path.endswith("/files"):
            raise TimeoutError("files")
        return real(path, params)
    monkeypatch.setattr(pc_read, "_desk", flaky)
    r = get(client, paired(), "/tv/v1/projects/%s" % pid)
    assert r.status_code == 503 and r.get_json()["code"] == "pc_unavailable"
