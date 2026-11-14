"""The owner's phone turn is delivered: no HTTP 500, progress while the model thinks, the final answer only.

Stephen, 2026-11-13 19:53: the desktop answered "who is Jere Jay ...?" correctly (bonsai2:27b, 43 s) and the
phone never got it. The gateway log shows `POST /tv/v1/messages -> 500` at 19:51:30, 19:53:08 and 20:08:08,
each at the START of the turn, with "Popped wrong app context" in the desktop's own stream: owner_turn read
the desktop's lazy Flask stream from inside the gateway's request, so the two Flask contexts interleaved,
and the gateway failed while the desktop finished the turn on its own. The promises guarded here:

* the desktop turn runs on its own thread and the gateway reads plain dicts, so a real streaming Flask app
  called from inside a gateway request answers 200 with the answer (this test uses real Flask on both
  sides; it fails with the old lazy read);
* while the model is silent the phone gets `ping` events (status "thinking", elapsed seconds), and a tool
  frame becomes a `ping` that says what is happening in a few words and never carries the tool's arguments;
* the phone gets the FINAL answer only: the desktop's deltas are not relayed, and a reasoning scratchpad
  (<think>...</think>) never reaches the phone;
* a turn that fails before any answer is an `error` event, not a silent end.
"""
import json
import time

import pytest
from flask import Flask, Response, stream_with_context

from agent_friday import core
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


def desktop(frames, delay=0.0, status=200):
    """A real streaming Flask app standing in for the desktop's /api/chat/stream (stream_with_context, like the real one)."""
    inner = Flask("fake_desktop")
    inner.secret_key = "k" * 32

    @inner.post("/api/chat/stream")
    def stream():
        def gen():
            yield ": open\n\n"
            for f in frames:
                if delay:
                    time.sleep(delay)
                yield "data: " + json.dumps(f) + "\n\n"
        return Response(stream_with_context(gen()), mimetype="text/event-stream", status=status)
    return inner


@pytest.fixture
def wire(monkeypatch):
    def install(frames, **kw):
        monkeypatch.setattr(core, "app", desktop(frames, **kw))
        monkeypatch.setattr(core, "_current_api_token", lambda: "t")
    return install


def phone():
    dev = FakeTv()
    device_pairing.add_device(dev.spki, name="Pixel", kind="phone")
    household.bind_device(dev.id, "stephen")
    return dev


def ask(client, dev, **extra):
    body = dict({"messages": [{"role": "user", "content": "who is Jere Jay?"}]}, **extra)
    return client.post("/tv/v1/messages", json=body, headers=auth(open_session(client, dev)))


def events(r):
    return [json.loads(line[6:]) for part in r.get_data(as_text=True).split("\n\n")
            for line in part.split("\n") if line.startswith("data: ")]


def spoken(r):
    return "".join(e["delta"]["text"] for e in events(r) if e.get("type") == "content_block_delta")


DONE = {"done": True, "payload": {"response": "Jere Jay is your father-in-law.", "model": "bonsai2:27b", "seat": "local"}}


def test_a_real_streaming_desktop_called_from_inside_a_gateway_request_answers_200(client, wire):
    wire([{"started": True}, {"delta": "Jere "}, DONE])
    r = ask(client, phone())
    assert r.status_code == 200 and spoken(r) == "Jere Jay is your father-in-law."
    assert events(r)[-1]["type"] == "message_stop"


def test_the_phone_hears_the_turn_is_alive_while_the_model_is_silent(client, wire, monkeypatch):
    monkeypatch.setattr(owner_turn, "STATUS_EVERY_S", 0.05)
    wire([{"started": True}, DONE], delay=0.3)
    ev = events(ask(client, phone()))
    pings = [e for e in ev if e["type"] == "ping"]
    assert len(pings) >= 3 and all(p["friday"]["status"] == "thinking" for p in pings)
    first_content = next(i for i, e in enumerate(ev) if e["type"] == "content_block_delta")
    assert ev.index(pings[0]) < first_content                       # alive before the first word


def test_a_tool_frame_becomes_a_few_words_never_the_arguments(client, wire):
    wire([{"started": True}, {"tool": {"name": "Searching the wiki", "args": {"q": "SECRET QUERY"}}}, DONE])
    r = ask(client, phone())
    working = [e for e in events(r) if e["type"] == "ping" and e["friday"]["status"] == "working"]
    assert working and working[0]["friday"]["detail"] == "Searching the wiki"
    assert "SECRET QUERY" not in r.get_data(as_text=True)


def test_only_the_final_answer_reaches_the_phone_never_the_scratchpad(client, wire):
    wire([{"delta": "<think>so that's clear. Also, the trust data says"}, {"delta": " ...</think>Draft."},
          {"done": True, "payload": {"response": "<think>hmm, reasoning here</think>Jere Jay is your father-in-law."}}])
    r = ask(client, phone())
    assert spoken(r) == "Jere Jay is your father-in-law."
    assert "reasoning here" not in r.get_data_as_text() if hasattr(r, "get_data_as_text") else "reasoning here" not in r.get_data(as_text=True)
    assert "trust data" not in r.get_data(as_text=True) and "Draft." not in r.get_data(as_text=True)


def test_a_turn_that_fails_before_any_answer_is_an_error_event(client, wire):
    wire([{"started": True}, {"error": "the model did not answer"}])
    ev = events(ask(client, phone()))
    assert ev[-1]["type"] == "error" and "did not answer" in ev[-1]["error"]["message"]


def test_a_desktop_that_refuses_the_turn_is_refused_before_the_stream(client, wire):
    wire([], status=503)
    assert ask(client, phone()).status_code == 503


def test_answer_only_handles_a_dangling_scratchpad():
    assert owner_turn.answer_only("<think>endless") == ""
    assert owner_turn.answer_only("thinking...</think>The answer.") == "The answer."
    assert owner_turn.answer_only("Plain.") == "Plain."
