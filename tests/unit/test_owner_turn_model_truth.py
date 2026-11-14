"""The model the owner picks on the phone is the model that runs, and every reply says which one did.

Stephen, 2026-11-13: "When I pick Bonsai2 on the phone, is Bonsai2 really answering? The replies seem too
fast." Traced: owner_turn read only the words and the conversation id from the phone's request; the
Messages body's `model` was dropped, so the choice never reached the desktop's per-chat seat, and the reply
said nothing about who generated it (message_start carried an empty model, the receipt an empty model), so
neither Stephen nor the phone's label could tell. The desktop's own turn record (`served_by.answered_by`)
had the truth and the gateway threw it away. The promises guarded here:

* (a) a `model` this PC lists as a seat is bound to the chat the way the desktop's picker binds one (the
  validated PATCH /api/conversations route); a new chat is created with it; a chat already on it is left
  alone (no write, so nothing reloads); a seat the PC refuses is a 409 with its reason and no turn runs on
  some other model;
* (b) the closing `message_delta` carries a `friday` block: the model the backend recorded as generating
  (not the requested id), its seat and provider, and the conversation id; an unknown is "unknown", never
  the requested id;
* (c) a fallback or notice the desktop raised rides along, and a served id that is not the requested one is
  marked `substituted`: never silent;
* a phone that sends no model, or one this PC does not list, runs on the chat's own seat, with the same
  block saying what served and what was asked.
"""
import json

import pytest

from agent_friday.services import device_pairing
from agent_friday.tv_gateway import app as gateway
from agent_friday.tv_gateway import household, owner_turn
from tests.unit.test_tv_gateway import FakeTv, auth, open_session


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr(household, "_path", lambda: tmp_path / "household.json")
    monkeypatch.setattr(gateway, "_BUCKET", gateway._Bucket())
    device_pairing._path().unlink(missing_ok=True)
    device_pairing._reset_for_tests()
    household.ensure_default_members()
    yield
    device_pairing._reset_for_tests()


@pytest.fixture
def client():
    return gateway.create_app().test_client()


class Desk:
    """The desktop, in process: its conversations, its seat list, and the turn it answers with."""

    def __init__(self):
        self.listed = {"bonsai2:27b", "claude-haiku-5-5"}
        self.convs = {"conv-old": {"id": "conv-old", "seat": None}}
        self.api_calls, self.turns = [], []
        self.refuse = None
        self.answer = {"model": "bonsai2:27b", "seat": "local", "provider": "arbiter-local"}
        self.extra = {}

    def api(self, method, path, body=None):
        self.api_calls.append((method, path, body))
        desk = self

        class R:
            def get_json(self, silent=True):
                return self.d
        r = R()
        if method == "POST" and path == "/api/conversations":
            desk.convs["conv-new"] = {"id": "conv-new", "seat": body.get("seat")}
            r.d = {"conversation": dict(desk.convs["conv-new"])}
        elif method == "GET":
            r.d = {"conversation": dict(desk.convs[path.rsplit("/", 1)[1]])}
        else:
            cid = path.rsplit("/", 1)[1]
            if desk.refuse:
                r.d = {"conversation": dict(desk.convs[cid]), "note": desk.refuse}
            else:
                desk.convs[cid]["seat"] = body["seat"]
                r.d = {"conversation": dict(desk.convs[cid])}
        return r

    def post(self, text, conversation_id):
        self.turns.append({"text": text, "conversation_id": conversation_id})
        a = self.answer
        payload = {"response": "ok", "model": a["model"], "seat": a["seat"],
                   "served_by": {"model": a["model"], "provider": "x",
                                 "answered_by": dict(a)}, "fallback_chain": [], "seat_notice": None}
        payload.update(self.extra)
        frames = [{"delta": "ok"}, {"done": True, "payload": payload}]
        body = "".join("data: " + json.dumps(f) + "\n\n" for f in frames).encode()

        class R:
            status_code = 200
            response = iter([body])
        return R()


@pytest.fixture
def desk(monkeypatch):
    d = Desk()
    monkeypatch.setattr(owner_turn, "_api", d.api, raising=False)
    monkeypatch.setattr(owner_turn, "_listed", lambda m: m in d.listed, raising=False)
    monkeypatch.setattr(owner_turn, "_post", d.post)
    return d


def phone():
    dev = FakeTv()
    device_pairing.add_device(dev.spki, name="Pixel", kind="phone")
    household.bind_device(dev.id, "stephen")
    return dev


def ask(client, dev, **extra):
    body = dict({"messages": [{"role": "user", "content": "Say only the word ok."}], "max_tokens": 100}, **extra)
    return client.post("/tv/v1/messages", json=body, headers=auth(open_session(client, dev)))


def events(r):
    return [json.loads(line[6:]) for part in r.get_data(as_text=True).split("\n\n")
            for line in part.split("\n") if line.startswith("data: ")]


def block(r):
    return next(e for e in events(r) if e.get("type") == "message_delta")["friday"]


# ---- (a) the choice survives -------------------------------------------------------------------------------

def test_a_picked_model_starts_a_chat_bound_to_it_and_the_turn_runs_in_that_chat(client, desk):
    r = ask(client, phone(), model="claude-haiku-5-5")
    assert r.status_code == 200
    assert desk.api_calls == [("POST", "/api/conversations", {"title": "Phone chat", "seat": {"model": "claude-haiku-5-5"}})]
    assert desk.turns[0]["conversation_id"] == "conv-new" and block(r)["conversation_id"] == "conv-new"


def test_a_picked_model_rebinds_an_existing_chat_through_the_validated_route(client, desk):
    ask(client, phone(), model="bonsai2:27b", conversation_id="conv-old")
    assert desk.api_calls[-1] == ("PATCH", "/api/conversations/conv-old", {"seat": {"model": "bonsai2:27b"}})
    assert desk.turns == [{"text": "Say only the word ok.", "conversation_id": "conv-old"}]


def test_a_chat_already_on_the_model_is_not_written_again(client, desk):
    desk.convs["conv-old"]["seat"] = {"model": "bonsai2:27b"}
    ask(client, phone(), model="bonsai2:27b", conversation_id="conv-old")
    assert [c[0] for c in desk.api_calls] == ["GET"]            # nothing changed, so nothing can reload
    assert len(desk.turns) == 1


def test_a_seat_the_pc_refuses_is_a_409_with_its_reason_and_no_turn_runs(client, desk):
    desk.refuse = "bonsai2:27b is loading; one local model at a time on this machine"
    r = ask(client, phone(), model="bonsai2:27b", conversation_id="conv-old")
    assert r.status_code == 409 and "one local model at a time" in r.get_data(as_text=True)
    assert desk.turns == []


def test_no_model_or_an_unlisted_one_binds_nothing(client, desk):
    ask(client, phone())
    ask(client, phone(), model="some-placeholder")
    assert desk.api_calls == [] and len(desk.turns) == 2


# ---- (b) the reply carries the backend's id ----------------------------------------------------------------

def test_the_reply_carries_the_model_the_backend_recorded_not_the_requested_one(client, desk):
    desk.answer = {"model": "claude-haiku-5-5", "seat": "cloud", "provider": "anthropic"}
    b = block(ask(client, phone(), model="bonsai2:27b"))
    assert b["model"] == "claude-haiku-5-5" and b["seat"] == "cloud" and b["local"] is False
    assert b["requested"] == "bonsai2:27b" and b["substituted"] is True


def test_a_matching_reply_is_not_marked_substituted(client, desk):
    b = block(ask(client, phone(), model="bonsai2:27b"))
    assert b["model"] == "bonsai2:27b" and b["local"] is True and b["substituted"] is False and b["provider"] == "arbiter-local"


def test_an_unknown_server_is_unknown_never_the_requested_id(client, desk):
    desk.post = lambda text, cid: type("R", (), {"status_code": 200, "response": iter([
        b'data: {"delta": "ok"}\n\ndata: {"done": true, "payload": {"response": "ok"}}\n\n'])})()
    owner_turn._post = desk.post
    b = block(ask(client, phone(), model="bonsai2:27b"))
    assert b["model"] == "" and b["unknown"] is True and b["substituted"] is False


# ---- (c) a fallback or notice is shown ---------------------------------------------------------------------

def test_a_fallback_and_a_notice_ride_along(client, desk):
    desk.extra = {"fallback_chain": [{"from": "bonsai2:27b", "to": "claude-haiku-5-5", "why": "local seat down"}],
                  "seat_notice": "bonsai2:27b did not answer; claude-haiku-5-5 did"}
    desk.answer = {"model": "claude-haiku-5-5", "seat": "cloud", "provider": "anthropic"}
    b = block(ask(client, phone(), model="bonsai2:27b"))
    assert b["fallback"][0]["why"] == "local seat down" and "did not answer" in b["notice"] and b["substituted"] is True


def test_the_message_delta_still_ends_the_turn_the_way_the_phone_reads_it(client, desk):
    ev = events(ask(client, phone(), model="bonsai2:27b"))
    d = next(e for e in ev if e["type"] == "message_delta")
    assert d["delta"] == {"stop_reason": "end_turn"} and ev[-1]["type"] == "message_stop"
