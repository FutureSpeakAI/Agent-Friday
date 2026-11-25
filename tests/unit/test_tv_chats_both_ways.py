"""Chats run both ways through ONE store on this PC: the owner's phone, tablet and watch reach the PC's chats, and the
desktop shows every chat a device starts or continues.

What is guarded here:

* a chat a device starts (``POST /tv/v1/chats``) is a real PC conversation made by the desktop's own create route, in
  the desktop's own list, optionally inside a project; a project id is checked before it is used; starting one is an
  owner turn's power (an owner phone or tablet), never a reader's: ``pc_read`` promises the device changes nothing;
  a body is capped and shaped, and carries no words for the desktop's sidebar;
* a device chat is titled "New chat", so the desktop's first-message titling names it like every other chat;
* a turn sent with no chat id lands in Main, and the device is TOLD it landed there (it never learns a chat that
  does not exist on the PC);
* a turn from a device tells the open desktop chats to re-read (``conversation_changed``), and the desktop page
  listens for it in both chat surfaces;
* each row of the device's chat list carries its last message and its project's name;
* end to end on the desktop's real store: a chat started from the phone appears in GET /api/conversations, and a
  desktop chat reads back on the phone with its full history.
"""
import json
from pathlib import Path

import pytest

from agent_friday.services import device_pairing
import importlib

from agent_friday.tv_gateway import owner_turn, pc_read
from tests.unit import test_tv_pc_read as shared
from tests.unit.test_tv_gateway import auth, open_session
from tests.unit.test_tv_pc_read import get, paired

client = shared.client
_clean = shared._clean
ROOT = Path(__file__).resolve().parents[2]
MAX_BODY = 4 * 1024          # pc_chats.MAX_BODY


def post(client, dev, path, body=None, raw=None):
    data = raw if raw is not None else json.dumps(body or {})
    return client.post(path, data=data, content_type="application/json", headers=auth(open_session(client, dev)))


class Desk:
    """The desktop's own routes: the projects it knows, and the chats its create route makes."""

    def __init__(self):
        self.projects = {"proj-1": {"id": "proj-1", "name": "Taxes"}}
        self.created, self.gets = [], []
        self.chats = []
        self.msgs = {}

    def get(self, path, params=None):
        self.gets.append((path, dict(params or {})))
        if path.startswith("/api/projects/"):
            pid = path.rsplit("/", 1)[1]
            if pid in self.projects:
                return 200, json.dumps({"project": self.projects[pid], "conversations": []}).encode(), "application/json"
            return 404, b'{"status":"error"}', "application/json"
        if path == "/api/conversations":
            return 200, json.dumps({"status": "ok", "conversations": self.chats, "main_id": "conv-main",
                                    "projects": list(self.projects.values())}).encode(), "application/json"
        if path.endswith("/messages"):
            msgs = self.msgs.get(path.split("/")[3], [])
            lim = int((params or {}).get("limit") or 0)
            return 200, json.dumps({"messages": msgs[-lim:] if lim else msgs}).encode(), "application/json"
        return 404, b"{}", "application/json"

    def post(self, path, body):
        self.created.append((path, body))
        conv = {"id": "conv-%04d" % len(self.created), "title": body["title"], "project": body.get("project")}
        return 201, {"status": "ok", "conversation": conv}


@pytest.fixture
def desk(monkeypatch):
    d = Desk()
    monkeypatch.setattr(pc_read, "_desk", d.get)
    pc_chats = importlib.import_module("agent_friday.tv_gateway.pc_chats")     # the door this file is about
    monkeypatch.setattr(pc_chats, "_desk_post", d.post)
    return d


@pytest.fixture
def bus(monkeypatch):
    sent = []
    from agent_friday.services import notice_registry
    monkeypatch.setattr(notice_registry, "bus_broadcast", lambda eid, ev, kind="chat": sent.append((eid, ev, kind)) or 1)
    return sent


# ── a device starts a real PC chat ──────────────────────────────────────────────────────────────────────────

def test_a_device_starts_a_chat_through_the_desktops_own_create_route(client, desk, bus):
    r = post(client, paired(), "/tv/v1/chats", {})
    assert r.status_code == 201
    got = r.get_json()
    assert got["ok"] and got["conversation_id"] == "conv-0001"
    assert desk.created == [("/api/conversations", {"title": "New chat"})]
    # the desktop's open pages hear about it, by id only
    assert bus == [("E74", {"type": "conversation_changed", "domain": "conversations", "conversation_id": "conv-0001", "via": "device"}, "chat")]


def test_a_chat_can_be_started_inside_a_project_the_pc_has(client, desk, bus):
    r = post(client, paired("tablet"), "/tv/v1/chats", {"project": "proj-1"})
    assert r.status_code == 201
    assert desk.created == [("/api/conversations", {"title": "New chat", "project": "proj-1"})]


@pytest.mark.parametrize("project,status,code", [("proj-nope", 404, "unknown_project"), ("../etc", 400, "bad_id"),
                                                 (7, 400, "bad_id")])
def test_a_project_is_checked_before_it_is_used(client, desk, bus, project, status, code):
    r = post(client, paired(), "/tv/v1/chats", {"project": project})
    assert r.status_code == status and r.get_json()["code"] == code and desk.created == []


def test_a_body_is_capped_and_shaped(client, desk, bus):
    dev = paired()
    assert post(client, dev, "/tv/v1/chats", raw="{" + " " * MAX_BODY + "}").status_code == 413
    assert post(client, dev, "/tv/v1/chats", raw="[1,2]").status_code == 400
    assert post(client, dev, "/tv/v1/chats", {"title": {"x": 1}}).status_code == 400
    # a device never writes words into the desktop's sidebar: a title is refused, not stored
    assert post(client, dev, "/tv/v1/chats", {"title": "\u202eevil"}).status_code == 400
    assert desk.created == []


@pytest.mark.parametrize("kind,member", [("watch", "stephen"), ("tv", "family"), ("tv", "stephen"), ("phone", "family"),
                                         ("tablet", None)])
def test_starting_a_chat_is_an_owner_turns_power_not_a_readers(client, desk, bus, kind, member):
    dev = paired(kind, member, right=False)
    device_pairing.set_right(dev.id, "pc_read", True)          # reading is no licence to write
    assert post(client, dev, "/tv/v1/chats", {}).status_code == 403 and desk.created == []


def test_an_owner_phone_starts_a_chat_without_the_read_right_as_its_first_turn_would(client, desk, bus):
    from agent_friday.tv_gateway import pairing
    r = post(client, paired("phone", "stephen", right=False), "/tv/v1/chats", {})
    assert r.status_code == 201 and pairing._OFFERED == {}       # and no read card is raised for it


# ── the list a device reads ─────────────────────────────────────────────────────────────────────────────────

def test_a_chat_row_carries_its_last_message_and_project_name(client, desk, monkeypatch):
    desk.chats = [{"id": "conv-a", "title": "Taxes 2026", "project": "proj-1", "last_active_at": 5.0},
                  {"id": "conv-b", "title": "Loose", "project": None, "last_active_at": 4.0}]
    desk.msgs = {"conv-a": [{"id": "m1", "role": "user", "text": "hi", "ts": 1.0},
                            {"id": "m2", "role": "friday", "text": "Filed it  under\n C:\\Users\\x\\taxes.pdf", "ts": 2.0}]}
    from agent_friday.services import conversations
    monkeypatch.setattr(conversations, "last_message", lambda cid: (desk.msgs.get(cid) or [None])[-1])
    rows = get(client, paired(), "/tv/v1/chats").get_json()["conversations"]
    a, b = rows
    assert a["project_name"] == "Taxes" and a["last"]["role"] == "friday" and a["last"]["at"] == 2.0
    assert a["last"]["text"].startswith("Filed it under") and "Users" not in a["last"]["text"]
    assert b["last"] is None and b["project_name"] is None


# ── turns land in a real PC chat, and the device is told which ──────────────────────────────────────────────

def test_a_turn_without_an_id_reports_the_chat_it_landed_in():
    assert owner_turn.landed_in({"conversation_id": "conv-main"}, None) == "conv-main"
    assert owner_turn.landed_in({"conversation_id": "conv-1a2b"}, "conv-1a2b") == "conv-1a2b"
    assert owner_turn.landed_in({}, "conv-x") == "conv-x"
    assert owner_turn.landed_in({"conversation_id": "../etc"}, "conv-x") == "conv-x"     # never an unchecked id
    assert owner_turn.landed_in(None, None) is None


def test_a_device_chat_is_named_by_its_first_message():
    # the desktop titles a chat from its first message only while it is called "New chat" (conversations.append)
    src = (ROOT / "src/agent_friday/tv_gateway/owner_turn.py").read_text(encoding="utf-8")
    assert '"Phone chat"' not in src and '{"title": "New chat"}' in src


def test_a_device_turn_tells_open_desktop_chats_to_reread(monkeypatch, bus):
    payload = {"response": "ok", "conversation_id": "conv-main"}
    body = "".join("data: " + json.dumps(f) + "\n\n" for f in ({"delta": "ok"}, {"done": True, "payload": payload}))

    class R:
        status_code = 200
        response = iter([body.encode()])
    monkeypatch.setattr(owner_turn, "_call_desktop", lambda text, cid: R())
    turn = owner_turn._DesktopTurn("hello", None)
    frames = [f for f in turn.frames(0.05) if f is not None]
    assert frames[-1]["done"]
    for _ in range(100):                    # the thread announces after its last frame
        if bus:
            break
        import time
        time.sleep(0.01)
    assert bus == [("E74", {"type": "conversation_changed", "domain": "conversations", "conversation_id": "conv-main", "via": "device"}, "chat")]


def test_a_failed_turn_announces_nothing(monkeypatch, bus):
    class R:
        status_code = 503
        response = iter([])
    monkeypatch.setattr(owner_turn, "_call_desktop", lambda text, cid: R())
    turn = owner_turn._DesktopTurn("hello", "conv-1")
    list(f for f in turn.frames(0.05) if f is not None)
    assert bus == []


def test_open_desktop_chats_reread_on_conversation_changed():
    page = (ROOT / "index.html").read_text(encoding="utf-8")
    # the undocked ConversationWindow and the docked chat in App both re-read on it, and the docked one refreshes the list
    assert page.count("m.type !== 'conversation_changed'") == 1
    assert page.count("ev.type !== 'conversation_changed'") == 1
    assert "if (ev.type === 'conversation_changed') refreshConvs();" in page


# ── end to end on the desktop's real chat store ─────────────────────────────────────────────────────────────

@pytest.fixture
def real_store(tmp_path, monkeypatch):
    """The desktop's own conversation routes (routes/conversations.py) on its own store, in a temp folder; the gateway's in-process
    calls go to them exactly as they go to the running desktop."""
    from flask import Flask
    from agent_friday.routes import conversations as routes
    from agent_friday.services import conversations, projects
    monkeypatch.setattr(conversations, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(projects, "_root", lambda: tmp_path / "projects")
    desk = Flask("desk")
    desk.register_blueprint(routes.conversations_bp)
    dc = desk.test_client()

    def desk_get(path, params=None):
        r = dc.get(path, query_string=params or {})
        return r.status_code, r.get_data(), r.headers.get("Content-Type", "application/json")

    def desk_post(path, body):
        r = dc.post(path, json=body)
        return r.status_code, r.get_json(silent=True) or {}
    monkeypatch.setattr(pc_read, "_desk", desk_get)
    monkeypatch.setattr(importlib.import_module("agent_friday.tv_gateway.pc_chats"), "_desk_post", desk_post)
    monkeypatch.setattr(conversations, "desk_client", dc, raising=False)
    return conversations


def test_a_phone_started_chat_appears_on_the_desktop_list(client, real_store, bus):
    r = post(client, paired(), "/tv/v1/chats", {})
    assert r.status_code == 201
    cid = r.get_json()["conversation_id"]
    listed = real_store.desk_client.get("/api/conversations").get_json()       # the list the desktop sidebar draws
    assert cid in [c["id"] for c in listed["conversations"]]
    # and a later turn's first words name it, as the desktop names its own
    real_store.append(cid, {"id": "u1", "role": "user", "text": "Plan the garden beds", "meta": {"kind": "turn"}})
    assert real_store.load(cid)["title"] == "Plan the garden beds"


def test_a_desktop_chat_resumes_on_the_phone_with_full_history(client, real_store):
    conv = real_store.create(title="Desk thread")
    for i in range(1, 8):
        real_store.append(conv["id"], {"id": "m%d" % i, "role": "user" if i % 2 else "friday", "text": "line %d" % i,
                                       "meta": {"kind": "turn"}})
    dev = paired()
    listed = get(client, dev, "/tv/v1/chats").get_json()["conversations"]
    row = next(c for c in listed if c["id"] == conv["id"])
    assert row["title"] == "Desk thread" and row["last"]["text"] == "line 7"
    msgs = get(client, dev, "/tv/v1/chats/%s/messages" % conv["id"]).get_json()["messages"]
    assert [m["text"] for m in msgs] == ["line %d" % i for i in range(1, 8)]


def test_the_last_message_is_read_from_the_tail(real_store, monkeypatch):
    conv = real_store.create(title="Long")
    for i in range(400):
        real_store.append(conv["id"], {"id": "m%d" % i, "role": "user", "text": "x" * 500 + str(i), "meta": {"kind": "turn"}})
    calls, whole = [], real_store._stored_messages
    monkeypatch.setattr(real_store, "_stored_messages", lambda cid: calls.append(cid) or [])
    assert real_store.last_message(conv["id"])["id"] == "m399" and calls == []       # the tail only, never the transcript
    huge = {"id": "big", "role": "friday", "text": "y" * (real_store.TAIL_BYTES * 2), "meta": {"kind": "turn"}}
    monkeypatch.setattr(real_store, "_stored_messages", whole)
    real_store.append(conv["id"], huge)
    assert real_store.last_message(conv["id"])["id"] == "big"                         # a last line longer than the tail
    assert real_store.last_message(real_store.create(title="Empty")["id"]) is None


def test_a_device_turn_never_rereads_under_a_desktop_stream():
    page = (ROOT / "index.html").read_text(encoding="utf-8")
    # undocked window: held while its own turn loads, re-read when it ends
    assert "if (m.type === 'conversation_changed' && loadingRef.current) { rereadAfterTurn.current = true; return; }" in page
    assert "if (!loading && rereadAfterTurn.current && cid) { rereadAfterTurn.current = false; openConvQuiet(cid); }" in page
    # docked chat: held while a turn is in flight, re-read when chatLoad clears
    assert "if (ev.type === 'conversation_changed' && dockLoading.current) { dockRereadAfterTurn.current = convId; return; }" in page
    assert "dockLoading.current = chatLoad;" in page
    # the behaviour itself (a device turn shows after a desktop turn has ended) is tests/app/specs/device_turn_rereads_dock.spec.ts
    assert "if (!chatLoad && dockRereadAfterTurn.current && dockRereadAfterTurn.current === convId) {" in page


def test_the_read_card_names_projects():
    from agent_friday.tv_gateway import pairing
    title, text = pairing.RIGHT_CARDS["pc_read"]
    assert "projects" in text and "cannot change anything" in text


def test_a_started_chat_answers_with_its_id_and_nothing_of_the_pcs(client, desk, bus):
    r = post(client, paired(), "/tv/v1/chats", {})
    assert r.status_code == 201 and set(r.get_json()) == {"ok", "conversation_id"}     # no seat, no summary


def test_starting_inside_a_project_needs_the_read_right(client, desk, bus):
    # a project id that exists or not is project knowledge, which the pc_read card governs
    dev = paired("phone", "stephen", right=False)
    r = post(client, dev, "/tv/v1/chats", {"project": "proj-1"})
    assert r.status_code == 403 and desk.created == []
    assert post(client, dev, "/tv/v1/chats", {}).status_code == 201          # an empty chat is still an owner turn's power


def test_a_device_cannot_flood_the_desktops_list(client, desk, bus):
    pc_chats = importlib.import_module("agent_friday.tv_gateway.pc_chats")
    dev = paired()
    codes = [post(client, dev, "/tv/v1/chats", {}).status_code for _ in range(pc_chats.STARTS_PER_WINDOW + 1)]
    assert codes[:-1] == [201] * pc_chats.STARTS_PER_WINDOW and codes[-1] == 429
    assert len(desk.created) == pc_chats.STARTS_PER_WINDOW
    assert post(client, paired("tablet"), "/tv/v1/chats", {}).status_code == 201        # per device, not for everyone


def test_a_failed_start_does_not_use_up_the_devices_starts(client, desk, bus, monkeypatch):
    pc_chats = importlib.import_module("agent_friday.tv_gateway.pc_chats")
    dev = paired()

    def down(path, body=None):
        raise RuntimeError("desktop busy")
    monkeypatch.setattr(pc_chats, "_desk_post", down)
    assert {post(client, dev, "/tv/v1/chats", {}).status_code for _ in range(pc_chats.STARTS_PER_WINDOW + 2)} == {503}
    monkeypatch.setattr(pc_chats, "_desk_post", desk.post)
    assert post(client, dev, "/tv/v1/chats", {}).status_code == 201
