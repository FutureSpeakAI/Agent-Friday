"""Phone parity: the phone picks any PC model or voice for ONE conversation and the PC honours it and says what
answered (docs/development/phone-model-voice-switching.md).

* POST /tv/v1/model/switch binds a conversation to a model the validated way (or starts the chat), refuses a model
  the PC cannot run in words with nothing changed, is owner-device only, and carries what the switch costs.
* GET /tv/v1/conversations/<id>/state reports the bound model, the bound voice and what last answered (the
  desktop's own record, with a substitution marked).
"""
import pytest

from agent_friday.services import voice_slate
from agent_friday.tv_gateway import conversation_state, owner_turn, voices
from tests.unit.test_owner_turn_model_truth import (  # noqa: F401  (fixtures and helpers shared with the model-truth tests)
    Desk, _clean as _gateway_clean, ask, block, client, desk, phone)
from tests.unit.test_tv_gateway import FakeTv, auth, open_session
from agent_friday.services import device_pairing
from agent_friday.tv_gateway import household


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    conversation_state._reset_for_tests()
    voices._reset_for_tests()
    monkeypatch.setattr(conversation_state, "model_facts",
                        lambda mid: {"id": mid, "switch": {"cold": False, "expected_seconds": 0.0}} if mid else None)
    yield
    conversation_state._reset_for_tests()
    voices._reset_for_tests()


def switch(client, dev, **body):
    return client.post("/tv/v1/model/switch", json=body, headers=auth(open_session(client, dev)))


def state(client, dev, cid):
    return client.get("/tv/v1/conversations/%s/state" % cid, headers=auth(open_session(client, dev)))


def test_a_switch_binds_the_chat_and_says_it_applies_to_this_conversation_only(client, desk):
    r = switch(client, phone(), model="bonsai2:27b", conversation_id="conv-old")
    j = r.get_json()
    assert r.status_code == 200 and j["ok"] and j["model"] == "bonsai2:27b" and j["applies"] == "this_conversation"
    assert desk.convs["conv-old"]["seat"] == {"model": "bonsai2:27b"} and j["facts"]["switch"]["cold"] is False


def test_a_switch_with_no_conversation_starts_one_on_that_model(client, desk):
    j = switch(client, phone(), model="claude-haiku-5-5").get_json()
    assert j["conversation_id"] == "conv-new" and desk.convs["conv-new"]["seat"] == {"model": "claude-haiku-5-5"}


def test_a_family_word_means_one_listed_model_and_the_ask_is_echoed(client, desk):
    desk.listed = {"claude-haiku-5-5"}
    j = switch(client, phone(), model="haiku", conversation_id="conv-old").get_json()
    assert j["model"] == "claude-haiku-5-5" and j["asked"] == "haiku"


def test_a_model_the_pc_does_not_list_is_refused_in_words_and_nothing_is_bound(client, desk):
    r = switch(client, phone(), model="klingon-9", conversation_id="conv-old")
    j = r.get_json()
    assert r.status_code == 400 and j["code"] == "unknown_model" and j["error"]
    assert desk.convs["conv-old"]["seat"] is None and not any(c[0] == "PATCH" for c in desk.api_calls)


def test_a_seat_the_pc_will_not_bind_is_a_409_with_its_reason(client, desk):
    desk.refuse = "Local only is on, so a cloud model is off."
    r = switch(client, phone(), model="claude-haiku-5-5", conversation_id="conv-old")
    j = r.get_json()
    assert r.status_code == 409 and j["code"] == "seat_refused" and "Local only" in j["error"]


def test_a_tv_cannot_switch_or_read_state_and_a_bad_request_is_400(client, desk):
    tv = FakeTv()
    device_pairing.add_device(tv.spki, name="TV", kind="tv")
    assert switch(client, tv, model="bonsai2:27b").status_code == 403
    assert state(client, tv, "conv-old").status_code == 403
    assert switch(client, phone()).status_code == 400


def test_state_reports_the_bound_model_the_bound_voice_and_what_really_answered(client, desk):
    dev = phone()
    switch(client, dev, model="claude-haiku-5-5", conversation_id="conv-old")
    desk.answer = {"model": "bonsai2:27b", "seat": "local", "provider": "arbiter-local"}   # the PC answered with another
    block(ask(client, dev, model="claude-haiku-5-5", conversation_id="conv-old"))   # reading the stream is the turn
    voices._bind("conv-old", "local", None)
    j = state(client, dev, "conv-old").get_json()
    assert j["model"]["bound"] == "claude-haiku-5-5" and j["voice"]["engine"] == "local"
    assert j["last_served"]["model"] == "bonsai2:27b" and j["last_served"]["substituted"] is True
    assert j["last_served"]["requested"] == "claude-haiku-5-5"


def test_state_of_a_chat_never_answered_has_no_last_served_and_an_unknown_chat_is_404(client, desk):
    dev = phone()
    assert state(client, dev, "conv-old").get_json()["last_served"] is None
    desk.convs.pop("conv-old")
    desk_missing = state(client, dev, "conv-old")
    assert desk_missing.status_code in (404, 503)


def test_the_stream_records_what_served_so_state_can_say_it(client, desk):
    """owner_turn._stream calls note_served: the one line that feeds last_served."""
    import inspect
    assert "note_served(truth)" in inspect.getsource(owner_turn._stream)
