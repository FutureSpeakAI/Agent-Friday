"""The phone's chat list over the gateway: every chat lives on the PC, readable and continuable from an owner device.

Stephen, 2026-11-15 (Mobile v3, order: chats, workspaces, jobs, media). BURN-DOWN's pc_read door gives the three
chat routes; this file guards what makes them usable from a phone:

* the list is PAGED (the desktop sends every chat and every project in one answer; a phone gets one page, a total
  and a next_offset, and never the projects), newest first, optionally only chats active after ``since``;
* ``q`` searches every chat's text through the desktop's own search and does not also return the list;
* a chat's messages can be read from a CURSOR (``after=<message id>``): the cheap live update, the phone polls with
  the last id it holds and gets only what is newer, in order, with ``has_more``; an id the chat no longer holds
  gets ``reset`` and the last page, so a deleted message never strands the phone;
* continuing a chat by ``conversation_id`` goes into the SAME PC chat (the id reaches the desktop unchanged) and the
  new message shows up on the next cursor read;
* only an owner-bound phone or tablet that holds ``pc_read`` is answered, for every one of these forms.
"""
import json

import pytest
from flask import Flask, Response, jsonify, request, stream_with_context

from agent_friday import core
from agent_friday.services import device_pairing
from agent_friday.tv_gateway import app as gateway
from agent_friday.tv_gateway import household, pc_read
from tests.unit.test_tv_gateway import auth, open_session
from tests.unit import test_tv_pc_read as shared
from tests.unit.test_tv_pc_read import get, paired

client = shared.client
_clean = shared._clean


class Desk:
    """The desktop's own GET routes, answering per path."""

    def __init__(self):
        self.calls, self.chats, self.msgs, self.hits = [], [], {}, []

    def __call__(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if path == "/api/conversations":
            return 200, json.dumps({"status": "ok", "conversations": self.chats, "main_id": "main",
                                    "projects": [{"id": "p1", "name": "secret project"}]}).encode(), "application/json"
        if path == "/api/conversations/search":
            return 200, json.dumps({"status": "ok", "results": self.hits}).encode(), "application/json"
        if path.endswith("/messages"):
            cid = path.split("/")[3]
            msgs = self.msgs.get(cid, [])
            limit = int((params or {}).get("limit") or 0) or None
            return 200, json.dumps({"status": "ok", "messages": msgs[-limit:] if limit else msgs}).encode(), "application/json"
        return 404, b'{"status":"error"}', "application/json"


@pytest.fixture
def desk(monkeypatch):
    d = Desk()
    monkeypatch.setattr(pc_read, "_desk", d)
    return d


def chats(n):
    # newest first, as the desktop sorts them
    return [{"id": "c%d" % i, "title": "Chat %d" % i, "last_active_at": 1000.0 + (n - i)} for i in range(n)]


def msgs(n):
    return [{"id": "m%d" % i, "role": "user" if i % 2 else "assistant", "content": "text %d" % i} for i in range(1, n + 1)]


# ── the list is paged ────────────────────────────────────────────────────────

def test_the_list_is_one_page_with_a_total_and_a_next_offset_and_no_projects(client, desk):
    desk.chats = chats(75)
    r = get(client, paired(), "/tv/v1/chats").get_json()
    assert [c["id"] for c in r["conversations"]] == ["c%d" % i for i in range(30)]
    assert r["total"] == 75 and r["offset"] == 0 and r["next_offset"] == 30 and "projects" not in r


def test_the_last_page_has_no_next_offset_and_a_huge_limit_is_capped(client, desk):
    desk.chats = chats(75)
    dev = paired()
    last = get(client, dev, "/tv/v1/chats?offset=60").get_json()
    assert len(last["conversations"]) == 15 and last["next_offset"] is None
    big = get(client, dev, "/tv/v1/chats?limit=99999").get_json()
    assert len(big["conversations"]) == 75 and big["next_offset"] is None          # 75 < the cap of 100
    desk.chats = chats(150)
    assert len(get(client, dev, "/tv/v1/chats?limit=99999").get_json()["conversations"]) == 100


def test_since_keeps_only_chats_active_after_it(client, desk):
    desk.chats = chats(10)                        # last_active_at 1010 .. 1001
    r = get(client, paired(), "/tv/v1/chats?since=1007").get_json()
    assert [c["id"] for c in r["conversations"]] == ["c0", "c1", "c2"] and r["total"] == 3


def test_a_junk_since_or_offset_is_ignored_not_an_error(client, desk):
    desk.chats = chats(5)
    r = get(client, paired(), "/tv/v1/chats?since=soon&offset=x&limit=-4")
    assert r.status_code == 200 and r.get_json()["total"] == 5


def test_archived_is_passed_on_and_nothing_else_is(client, desk):
    desk.chats = chats(2)
    get(client, paired(), "/tv/v1/chats?archived=1&path=/etc/passwd")
    assert desk.calls == [("/api/conversations", {"archived": "1"})]


# ── search ───────────────────────────────────────────────────────────────────

def test_q_searches_every_chat_through_the_desktops_search_and_returns_its_hits(client, desk):
    desk.hits = [{"conversation_id": "c3", "snippet": "the tax thing"}]
    r = get(client, paired(), "/tv/v1/chats?q=tax%20thing").get_json()
    assert r["results"] == desk.hits and desk.calls == [("/api/conversations/search", {"q": "tax thing"})]


def test_a_blank_q_is_the_list_and_a_long_q_is_clipped(client, desk):
    desk.chats = chats(3)
    dev = paired()
    assert "conversations" in get(client, dev, "/tv/v1/chats?q=%20%20").get_json()
    get(client, dev, "/tv/v1/chats?q=" + "x" * 900)
    assert len(desk.calls[-1][1]["q"]) == 200


# ── the cursor: the cheap live update ───────────────────────────────────────

def test_after_returns_only_the_newer_messages_in_order_with_has_more(client, desk):
    desk.msgs["c1"] = msgs(10)
    r = get(client, paired(), "/tv/v1/chats/c1/messages?after=m3&limit=4").get_json()
    assert [m["id"] for m in r["messages"]] == ["m4", "m5", "m6", "m7"] and r["has_more"] is True and "reset" not in r


def test_after_the_last_message_is_an_empty_answer_not_an_error(client, desk):
    desk.msgs["c1"] = msgs(10)
    r = get(client, paired(), "/tv/v1/chats/c1/messages?after=m10").get_json()
    assert r["messages"] == [] and r["has_more"] is False


def test_an_after_id_the_chat_does_not_hold_resets_to_the_last_page(client, desk):
    desk.msgs["c1"] = msgs(10)
    r = get(client, paired(), "/tv/v1/chats/c1/messages?after=gone&limit=3").get_json()
    assert r["reset"] is True and [m["id"] for m in r["messages"]] == ["m8", "m9", "m10"] and r["has_more"] is False


def test_without_after_it_is_the_tail_and_the_cap_still_applies(client, desk):
    desk.msgs["c1"] = msgs(10)
    r = get(client, paired(), "/tv/v1/chats/c1/messages?limit=3").get_json()
    assert [m["id"] for m in r["messages"]] == ["m8", "m9", "m10"] and "has_more" not in r
    assert desk.calls[-1] == ("/api/conversations/c1/messages", {"limit": 3})


@pytest.mark.parametrize("after", [".hidden", "../x", "a;b", "a b"])
def test_a_bad_after_id_is_refused_before_the_desktop_is_asked(client, desk, after):
    r = get(client, paired(), "/tv/v1/chats/c1/messages?after=" + after)
    assert r.status_code == 400 and desk.calls == []


def test_a_desktop_404_for_a_missing_chat_is_passed_on_not_turned_into_a_500(client, monkeypatch):
    monkeypatch.setattr(pc_read, "_desk", lambda path, params=None: (404, b'{"status":"error","error":"no such conversation"}',
                                                                     "application/json"))
    for path in ("/tv/v1/chats/nope/messages", "/tv/v1/chats/nope/messages?after=m1"):
        assert get(client, paired(), path).status_code == 404


# ── only an owner device with the right, for every form ─────────────────────

FORMS = ["/tv/v1/chats?q=tax", "/tv/v1/chats?offset=30", "/tv/v1/chats?since=5", "/tv/v1/chats/c1/messages?after=m1"]


@pytest.mark.parametrize("path", FORMS)
@pytest.mark.parametrize("kind,member,right", [("watch", "stephen", True), ("tv", "family", True), ("phone", "family", True),
                                               ("phone", None, True), ("phone", "stephen", False)])
def test_no_form_answers_a_device_that_is_not_an_owner_phone_or_tablet_with_the_right(client, desk, path, kind, member, right):
    dev = paired(kind, member, right=False)
    if right:
        device_pairing.set_right(dev.id, "pc_read", True)
    assert get(client, dev, path).status_code == 403 and desk.calls == []


def test_a_desktop_failure_is_a_plain_503_for_the_list_and_the_cursor(client, monkeypatch):
    def boom(path, params=None):
        raise RuntimeError("secret detail")
    monkeypatch.setattr(pc_read, "_desk", boom)
    dev = paired()
    for path in ("/tv/v1/chats", "/tv/v1/chats/c1/messages?after=m1"):
        r = get(client, dev, path)
        assert r.status_code == 503 and "secret detail" not in r.get_data(as_text=True)


# ── continuing a chat by conversation_id, then reading it back ──────────────

def _live_desktop(monkeypatch):
    """A real Flask app standing in for the desktop: the chat stream appends to one in-memory chat and the
    messages route reads it, so a phone turn and a phone read go through the real gateway code both ways."""
    store = {"c1": msgs(2)}
    seen = []
    inner = Flask("fake_desktop")
    inner.secret_key = "k" * 32

    @inner.post("/api/chat/stream")
    def stream():
        body = request.get_json(silent=True) or {}
        seen.append(body)
        cid = body.get("conversation_id") or "fresh"
        store.setdefault(cid, []).append({"id": "m%d" % (len(store[cid]) + 1), "role": "user", "content": body["message"]})
        store[cid].append({"id": "m%d" % (len(store[cid]) + 1), "role": "assistant", "content": "an answer"})

        def gen():
            yield ": open\n\n"
            yield "data: " + json.dumps({"done": True, "payload": {"response": "an answer", "model": "m", "seat": "local",
                                                                     "conversation_id": cid}}) + "\n\n"
        return Response(stream_with_context(gen()), mimetype="text/event-stream")

    @inner.get("/api/conversations/<cid>/messages")
    def messages(cid):
        return jsonify({"status": "ok", "messages": store.get(cid, [])})

    monkeypatch.setattr(core, "app", inner)
    monkeypatch.setattr(core, "_current_api_token", lambda: "t")
    return store, seen


def test_continuing_by_conversation_id_lands_in_the_same_pc_chat_and_the_cursor_sees_it(client, monkeypatch):
    store, seen = _live_desktop(monkeypatch)
    dev = paired()
    tok = open_session(client, dev)
    r = client.post("/tv/v1/messages", headers=auth(tok), json={
        "conversation_id": "c1", "messages": [{"role": "user", "content": "carry on from before"}]})
    assert r.status_code == 200
    assert seen == [{"message": "carry on from before", "conversation_id": "c1"}]      # the SAME chat, not a new one
    after = client.get("/tv/v1/chats/c1/messages?after=m2", headers=auth(tok)).get_json()
    assert [m["content"] for m in after["messages"]] == ["carry on from before", "an answer"]
    assert list(store) == ["c1"]                                                      # no stray second chat was made


def test_a_turn_with_no_conversation_id_sends_none_so_it_never_lands_in_an_existing_chat(client, monkeypatch):
    store, seen = _live_desktop(monkeypatch)
    client.post("/tv/v1/messages", headers=auth(open_session(client, paired())),
                json={"messages": [{"role": "user", "content": "hello"}]})
    assert seen == [{"message": "hello"}] and len(store["c1"]) == 2        # the existing chat is untouched


def test_a_page_too_big_for_a_phone_is_refused_not_sent(client, desk, monkeypatch):
    monkeypatch.setattr(pc_read, "MAX_JSON", 2000)
    desk.chats = [{"id": "c%d" % i, "title": "t" * 200, "last_active_at": 1.0} for i in range(30)]
    r = get(client, paired(), "/tv/v1/chats")
    assert r.status_code == 413 and r.get_json()["code"] == "too_big"
    desk.msgs["c1"] = [{"id": "m%d" % i, "content": "x" * 200} for i in range(1, 30)]
    assert get(client, paired(), "/tv/v1/chats/c1/messages?after=m1").status_code == 413
