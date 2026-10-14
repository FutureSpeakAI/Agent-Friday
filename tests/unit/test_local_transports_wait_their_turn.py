"""The local transports take their turn in the queue instead of sitting
invisibly in an HTTP call: local_call.call, the router's local legs, the
Ollama manager. Written against the queue alone: fake HTTP, no model."""
import threading

import pytest
import requests
from turn_queue_helpers import fast, wait_until

from agent_friday.services import local_call
from agent_friday.services import turn_queue as tq


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    fast(monkeypatch)
    if hasattr(tq, "HELD"):
        tq.HELD.set(())
    yield
    tq.reset_for_tests()


class Resp:
    status_code = 200

    def json(self):
        return {"message": {"content": "ok"}}


def test_local_call_holds_a_ticket_while_the_model_answers(monkeypatch):
    monkeypatch.setattr(local_call, "seat_endpoint", lambda m: None)
    seen = []

    def fake_post(url, json=None, timeout=None):
        seen.append([(t.cls, t.kind) for t in tq.queue_for("test-seat").running_tickets()])
        return Resp()
    monkeypatch.setattr(requests, "post", fake_post)
    assert local_call.call("s", "u", "test-seat") == "ok"
    assert seen == [[("background", "local_call.call")]]
    assert tq.queue_for("test-seat").running_tickets() == []


def test_local_call_waits_behind_a_chat_turn_on_the_same_seat_and_a_cancelled_call_is_never_sent(monkeypatch):
    monkeypatch.setattr(local_call, "seat_endpoint", lambda m: None)
    sent = []
    monkeypatch.setattr(requests, "post", lambda url, json=None, timeout=None: sent.append(url) or Resp())
    turn = tq.acquire("test-seat", "chat", "turn-1")
    out = []
    th = threading.Thread(target=lambda: out.append(local_call.call("s", "u", "test-seat")), daemon=True)
    th.start()
    waiting_now = lambda: tq.queue_for("test-seat").waiting_tickets()          # noqa: E731
    assert wait_until(lambda: len(waiting_now()) == 1), "it queued"
    assert sent == [], "it did not reach the model beside the chat turn"
    waiting = waiting_now()[0]
    assert getattr(waiting, "kind", None) == "local_call.call"
    assert tq.cancel(waiting.id)
    th.join(5)
    assert out == [""], "local_call.call never raises: a cancelled call comes back empty"
    assert sent == []
    turn.release()


def test_the_router_and_manager_legs_carry_the_admission_marker():
    from agent_friday.routing.ollama_manager import OllamaManager
    from agent_friday.services import model_router as mr
    assert mr._call_ollama.__local_scheduler_admitted__ == "model_router.ollama"
    assert mr._call_openai.__local_scheduler_admitted__ == "model_router.openai"
    assert OllamaManager.chat_completion.__local_scheduler_admitted__ == "ollama_manager.chat"


def test_only_a_local_openai_leg_is_admitted():
    from agent_friday.services import model_router as mr
    local = {"provider": {"name": "x", "adapter": "openai-compatible", "classification": "local",
                          "base_url": "http://127.0.0.1:8090/v1"}}
    cloud = {"provider": {"name": "y", "adapter": "openai-compatible", "classification": "cloud",
                          "base_url": "https://api.example.com/v1"}}
    assert mr._openai_leg_is_local(local) is True
    assert mr._openai_leg_is_local(cloud) is False
    assert mr._openai_leg_is_local({"provider": None}) is False


def test_a_failing_call_that_is_not_local_is_made_once():
    """The decorator decides locality, then calls through. A cloud leg that raises
    is the fallback chain's business: it must not be sent a second time."""
    from agent_friday.services import local_scheduler as ls
    calls = []

    @ls.admitted("test.leg", local=lambda bound: False)
    def cloud_leg(model="x"):
        calls.append(model)
        raise RuntimeError("503")
    with pytest.raises(RuntimeError):
        cloud_leg()
    assert calls == ["x"]

    @ls.admitted("test.leg", local=lambda bound: (_ for _ in ()).throw(ValueError("cannot tell")))
    def unsure_leg(model="x"):
        calls.append("unsure")
        raise RuntimeError("503")
    with pytest.raises(RuntimeError):
        unsure_leg()
    assert calls == ["x", "unsure"], "when it cannot tell, it goes through unadmitted, once"
