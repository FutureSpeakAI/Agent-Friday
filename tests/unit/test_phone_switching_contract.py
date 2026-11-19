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
    missing = state(client, dev, "conv-gone")
    assert missing.status_code == 404 and missing.get_json()["code"] == "unknown_conversation"


def test_switching_a_chat_the_pc_does_not_have_is_404_not_a_refused_model(client, desk):
    r = switch(client, phone(), model="bonsai2:27b", conversation_id="conv-gone")
    assert r.status_code == 404 and r.get_json()["code"] == "unknown_conversation"
    assert not any(c[0] == "PATCH" for c in desk.api_calls)


def test_a_chat_id_that_is_not_a_plain_token_never_reaches_an_internal_url(client, desk):
    dev = phone()
    for bad in ("x?y=1", "a/b", "..", "conv old"):
        assert switch(client, dev, model="bonsai2:27b", conversation_id=bad).status_code == 400
        assert ask(client, dev, model="bonsai2:27b", conversation_id=bad).status_code == 400
    assert state(client, dev, "x%3Fy%3D1").status_code == 404
    assert desk.api_calls == []


def test_a_turn_with_no_model_is_still_flagged_when_another_model_answers_than_the_chat_is_bound_to(client, desk):
    """The documented flow: switch once, then send turns with only the conversation id."""
    dev = phone()
    switch(client, dev, model="claude-haiku-5-5", conversation_id="conv-old")
    desk.answer = {"model": "bonsai2:27b", "seat": "local", "provider": "arbiter-local"}
    block(ask(client, dev, conversation_id="conv-old"))
    last = state(client, dev, "conv-old").get_json()["last_served"]
    assert last["model"] == "bonsai2:27b" and last["requested"] is None
    assert last["expected"] == "claude-haiku-5-5" and last["substituted"] is True


def test_the_real_model_facts_pick_the_slate_row_fields(monkeypatch):
    from agent_friday.services import model_slate
    monkeypatch.undo()
    monkeypatch.setattr(model_slate, "find", lambda mid: {"id": mid, "label": "L", "local": True, "secret": "x",
                                                           "switch": {"cold": True}, "measured": None})
    got = conversation_state.model_facts("m1")
    assert got["id"] == "m1" and got["label"] == "L" and got["switch"] == {"cold": True} and "secret" not in got


def test_a_chat_that_gets_its_model_from_its_project_is_flagged_and_reported_the_same_way(client, desk):
    dev = phone()
    desk.convs["conv-old"]["effective_seat"] = {"model": "claude-haiku-5-5"}
    desk.answer = {"model": "bonsai2:27b", "seat": "local", "provider": "arbiter-local"}
    block(ask(client, dev, conversation_id="conv-old"))
    j = state(client, dev, "conv-old").get_json()
    assert j["model"]["bound"] == "claude-haiku-5-5" and j["last_served"]["substituted"] is True


def test_a_desktop_that_cannot_answer_is_503_not_no_such_chat(client, desk):
    dev = phone()
    desk.api_status = 500
    r = state(client, dev, "conv-old")
    assert r.status_code == 503 and r.get_json()["code"] == "chat_unavailable"
    r = switch(client, dev, model="bonsai2:27b", conversation_id="conv-old")
    assert r.status_code == 503 and r.get_json()["code"] == "chat_unavailable"


def test_a_turn_with_no_model_and_a_bad_chat_id_is_refused_before_any_lookup(client, desk):
    r = ask(client, phone(), conversation_id="a/b")
    assert r.status_code == 400 and desk.api_calls == []


def test_a_switch_to_the_projects_model_is_still_pinned_to_the_chat_itself(client, desk):
    desk.convs["conv-old"]["effective_seat"] = {"model": "claude-haiku-5-5"}      # inherited, no seat of its own
    switch(client, phone(), model="claude-haiku-5-5", conversation_id="conv-old")
    assert desk.convs["conv-old"]["seat"] == {"model": "claude-haiku-5-5"}


def test_a_desktop_that_fails_on_the_write_is_503_not_a_refused_model(client, desk, monkeypatch):
    real = desk.api

    def api(method, path, body=None):
        r = real(method, path, body)
        if method == "PATCH":
            r.status_code, r.d = 500, {}
        return r
    monkeypatch.setattr(owner_turn, "_api", api)
    r = switch(client, phone(), model="claude-haiku-5-5", conversation_id="conv-old")
    assert r.status_code == 503 and r.get_json()["code"] == "chat_unavailable"


def test_the_expected_model_is_read_before_the_turn_runs_not_after(client, desk, monkeypatch):
    dev = phone()
    switch(client, dev, model="claude-haiku-5-5", conversation_id="conv-old")
    real_post = desk.post

    def post(text, conversation_id):
        desk.convs["conv-old"]["seat"] = {"model": "bonsai2:27b"}       # the owner switches at the desk mid-turn
        return real_post(text, conversation_id)
    monkeypatch.setattr(owner_turn, "_post", post)
    desk.answer = {"model": "bonsai2:27b", "seat": "local", "provider": "arbiter-local"}
    block(ask(client, dev, conversation_id="conv-old"))
    last = state(client, dev, "conv-old").get_json()["last_served"]
    assert last["expected"] == "claude-haiku-5-5" and last["substituted"] is True


def test_a_pc_still_checking_its_local_models_is_a_retryable_409_not_a_refusal(client, desk, monkeypatch):
    real = desk.api

    def api(method, path, body=None):
        r = real(method, path, body)
        if method == "PATCH":
            desk.convs["conv-old"]["seat"] = None          # the desktop did not bind it yet
            r.d = {"conversation": dict(desk.convs["conv-old"]), "seat_pending": True,
                   "note": "still checking which local model is serving, try again in a moment"}
        return r
    monkeypatch.setattr(owner_turn, "_api", api)
    r = switch(client, phone(), model="bonsai2:27b", conversation_id="conv-old")
    j = r.get_json()
    assert r.status_code == 409 and j["code"] == "seat_pending" and j["retry"] is True and "try again" in j["error"]


def test_a_chat_deleted_between_the_look_and_the_write_is_404(client, desk, monkeypatch):
    real = desk.api

    def api(method, path, body=None):
        r = real(method, path, body)
        if method == "PATCH":
            r.status_code, r.d = 404, {}
        return r
    monkeypatch.setattr(owner_turn, "_api", api)
    r = switch(client, phone(), model="bonsai2:27b", conversation_id="conv-old")
    assert r.status_code == 404 and r.get_json()["code"] == "unknown_conversation"


def test_a_switch_that_starts_a_new_chat_gets_the_same_refusals_as_one_on_an_existing_chat(client, desk):
    desk.refuse = "There is only room for one local model at a time."
    r = switch(client, phone(), model="bonsai2:27b")                  # no conversation_id
    j = r.get_json()
    assert r.status_code == 409 and j["code"] == "seat_refused" and "one local model" in j["error"]
    assert not any(c[0] == "POST" and (c[2] or {}).get("seat") for c in desk.api_calls)   # never created with a seat


def test_a_refused_new_chat_hands_back_its_id_so_a_retry_reuses_it_not_a_second_empty_chat(client, desk):
    dev = phone()
    desk.refuse = "There is only room for one local model at a time."
    j = switch(client, dev, model="bonsai2:27b").get_json()
    assert j["code"] == "seat_refused" and j["conversation_id"] == "conv-new"
    desk.refuse = None
    posts = sum(1 for c in desk.api_calls if c[0] == "POST")
    ok = switch(client, dev, model="bonsai2:27b", conversation_id=j["conversation_id"]).get_json()
    assert ok["ok"] and ok["conversation_id"] == "conv-new"
    assert sum(1 for c in desk.api_calls if c[0] == "POST") == posts          # no second chat created


def test_a_failed_write_after_creating_a_chat_still_hands_back_its_id(client, desk, monkeypatch):
    real = desk.api

    def api(method, path, body=None):
        r = real(method, path, body)
        if method == "PATCH":
            r.status_code, r.d = 500, {}
        return r
    monkeypatch.setattr(owner_turn, "_api", api)
    j = switch(client, phone(), model="bonsai2:27b").get_json()
    assert j["code"] == "chat_unavailable" and j["conversation_id"] == "conv-new"


def test_a_voice_turn_whose_default_model_is_refused_runs_in_the_chat_it_created_not_a_second_one(client, desk):
    desk.refuse = "still checking"
    r = ask(client, phone(), voice=True, conversation_id=None)
    assert r.status_code == 200
    assert sum(1 for c in desk.api_calls if c[0] == "POST") == 1          # one chat, not a spare plus another
    assert desk.turns[0]["conversation_id"] == "conv-new"
