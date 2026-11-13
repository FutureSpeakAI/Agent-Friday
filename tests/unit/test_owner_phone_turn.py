"""Stephen's phone is paired as the OWNER, so /tv/v1/messages answers with the owner's Friday.

Found on the first real pairing (2026-11-13): the phone, bound to Stephen, asked "who am I?" and was
told "I don't have your personal file ... I'm running on the household computer for the TV": the
gateway's turn path was the TV's for every device. The promises guarded here:

* a personal device (phone, tablet, watch) the PC bound to the owner gets the owner's turn: the same
  chat turn the desktop runs (routes/chat.chat_stream, with the knowledge graph, wiki and vault, and the
  owner's seat), reached in process with this PC's own session token; the household TV prompt is not used;
* a TV, or anything bound to the family member or to nobody, is unchanged: household prompt, no owner context;
* the only way in is the binding the owner made at the PC: not a header, a field in the body, or a kind
  claimed at turn time; a device of kind tv bound to the owner (a mis-bind) is still not the owner's turn;
* the reply is in the Messages stream format the phone already reads.
"""
import json

import pytest

from agent_friday.services import device_pairing
from agent_friday.tv_gateway import app as gateway
from agent_friday.tv_gateway import household, owner_turn, turn
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


class OwnerChat:
    """Stands in for the in-process owner chat turn (core.app's /api/chat/stream)."""

    def __init__(self, pieces=("Griff Jay is ", "in your graph."), status=200):
        self.calls, self.pieces, self.status = [], pieces, status

    def __call__(self, text, conversation_id):
        self.calls.append({"text": text, "conversation_id": conversation_id})
        frames = [{"started": True}] + [{"delta": p} for p in self.pieces] + [
            {"done": True, "payload": {"response": "".join(self.pieces)}}]
        body = "".join("data: " + json.dumps(f) + "\n\n" for f in frames).encode()
        outer = self

        class R:
            status_code = outer.status
            response = iter([body[:25], body[25:]])        # frames split across chunks, like a real stream
        return R()


@pytest.fixture
def owner_chat(monkeypatch):
    fake = OwnerChat()
    monkeypatch.setattr(owner_turn, "_post", fake)
    return fake


@pytest.fixture
def household_path(monkeypatch):
    """Records any turn that took the household/TV path."""
    seen = []

    def fake_run(dev, body, principal):
        seen.append(principal)
        return iter([turn._event({"type": "message_start", "message": {}}), turn._event({"type": "message_stop"})])
    real_run = turn.run

    def dispatch(dev, body, principal):
        if owner_turn.is_owner_device(dev):
            return real_run(dev, body, principal)
        return fake_run(dev, body, principal)
    monkeypatch.setattr(turn, "run", dispatch)
    return seen


def paired(kind, member):
    dev = FakeTv()
    device_pairing.add_device(dev.spki, name="A device", kind=kind)
    if member:
        household.bind_device(dev.id, member)
    return dev


def ask(client, dev, text="Who is Griff Jay?", **extra):
    body = dict({"messages": [{"role": "user", "content": text}], "system": "You are Friday on the TV."}, **extra)
    return client.post("/tv/v1/messages", json=body, headers=auth(open_session(client, dev)))


def texts(r):
    out = []
    for part in r.get_data(as_text=True).split("\n\n"):
        for line in part.split("\n"):
            if line.startswith("data: "):
                e = json.loads(line[6:])
                if e.get("type") == "content_block_delta":
                    out.append(e["delta"]["text"])
    return "".join(out)


@pytest.mark.parametrize("kind", ["phone", "tablet", "watch"])
def test_an_owners_personal_device_gets_the_owners_friday(client, owner_chat, kind):
    dev = paired(kind, "stephen")
    r = ask(client, dev)
    assert r.status_code == 200
    assert owner_chat.calls == [{"text": "Who is Griff Jay?", "conversation_id": None}]
    assert texts(r) == "Griff Jay is in your graph."
    assert "message_start" in r.get_data(as_text=True) and "message_stop" in r.get_data(as_text=True)


def test_the_tvs_system_prompt_never_reaches_the_owners_turn(client, owner_chat):
    ask(client, paired("phone", "stephen"), system="You are the household TV. You know nothing personal.")
    assert owner_chat.calls and "household TV" not in json.dumps(owner_chat.calls)


@pytest.mark.parametrize("kind,member", [("tv", "family"), ("phone", "family"), ("phone", None), ("tablet", None)])
def test_a_tv_or_a_family_or_unbound_device_never_gets_the_owners_friday(client, owner_chat, household_path, kind, member):
    r = ask(client, paired(kind, member))
    assert r.status_code == 200 and owner_chat.calls == [] and household_path


def test_a_tv_misbound_to_the_owner_is_still_not_the_owners_turn(client, owner_chat, household_path):
    ask(client, paired("tv", "stephen"))
    assert owner_chat.calls == [] and household_path


def test_a_turn_cannot_claim_to_be_the_owner(client, owner_chat, household_path):
    dev = paired("tv", "family")
    ask(client, dev, member="stephen", kind="phone", principal="owner", conversation_id="main")
    r = client.post("/tv/v1/messages", json={"messages": [{"role": "user", "content": "hi"}]},
                    headers=dict(auth(open_session(client, dev)), **{"X-Friday-Principal": "owner", "X-Friday-Member": "stephen"}))
    assert owner_chat.calls == [] and r.status_code in (200, 503)


def test_the_conversation_is_the_owners_when_the_phone_names_one(client, owner_chat):
    ask(client, paired("phone", "stephen"), conversation_id="  thread-7 ")
    assert owner_chat.calls[0]["conversation_id"] == "thread-7"


def test_the_newest_user_message_is_what_is_asked(client, owner_chat):
    dev = paired("phone", "stephen")
    messages = [{"role": "user", "content": "old"}, {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
                {"role": "user", "content": [{"type": "text", "text": "Who is Griff Jay?"}]}]
    r = client.post("/tv/v1/messages", json={"messages": messages}, headers=auth(open_session(client, dev)))
    assert r.status_code == 200 and owner_chat.calls[0]["text"] == "Who is Griff Jay?"


def test_an_empty_turn_is_refused_before_any_event(client, owner_chat):
    dev = paired("phone", "stephen")
    r = client.post("/tv/v1/messages", json={"messages": [{"role": "user", "content": "  "}]},
                    headers=auth(open_session(client, dev)))
    assert r.status_code == 400 and owner_chat.calls == []


def test_a_turn_the_owners_app_will_not_take_is_a_503_the_phone_can_act_on(client, monkeypatch):
    monkeypatch.setattr(owner_turn, "_post", OwnerChat(status=500))
    assert ask(client, paired("phone", "stephen")).status_code == 503


def test_the_owner_turn_reaches_the_real_chat_route_with_this_pcs_own_token(monkeypatch):
    """No fake of the transport: the request goes to core.app's /api/chat/stream with the PC's token."""
    from agent_friday import core
    seen = {}

    class C:
        def post(self, path, json=None, headers=None, buffered=None):
            seen.update(path=path, body=json, token=(headers or {}).get("X-Friday-Token"), buffered=buffered)
            return "response"
    monkeypatch.setattr(core.app, "test_client", lambda: C())
    assert owner_turn._post("Who is Griff Jay?", None) == "response"
    assert seen["path"] == "/api/chat/stream" and seen["body"] == {"message": "Who is Griff Jay?"}
    assert seen["token"] == core._current_api_token() and seen["buffered"] is False
