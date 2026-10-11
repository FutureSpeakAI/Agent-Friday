"""A local model that could not answer becomes an OFFER, never a cloud call.

Routing modes (addendum 2.1 rule 4): an interactive turn gets "try again locally /
a shorter version / send it to Claude", the first one labelled as recommended and
nothing preselected; a spoken turn gets the same offer in one short line, and a
bare spoken yes answers it only under the voice card rules.
"""
from __future__ import annotations

import pytest

from agent_friday.services import cloud_intent as ci
from agent_friday.services import local_offer as lo


@pytest.fixture(autouse=True)
def _clean():
    for cid in ("c1", "c2"):
        lo.forget(cid)
    ci.clear()
    yield
    ci.clear()


class TestTheOffer:
    def test_three_choices_in_order_with_the_local_retry_first(self):
        o = lo.build(RuntimeError("timed out"), model="bonsai2:27b", mode="local_preferred")
        assert [c["id"] for c in o["choices"]] == ["retry_local", "shorter", "send_claude"]
        assert [c["label"] for c in o["choices"]] == [
            "Try again locally", "Try a shorter version", "Send to Claude"]

    def test_the_recommended_one_is_labelled_and_nothing_is_preselected(self):
        o = lo.build("it timed out", mode="local_preferred")
        assert [c["id"] for c in o["choices"] if c["recommended"]] == ["retry_local"]
        for c in o["choices"]:
            assert not c.get("selected") and not c.get("default") and not c.get("checked")
        assert "selected" not in o and "default_choice" not in o

    def test_it_says_nothing_was_sent_and_what_failed(self):
        o = lo.build(RuntimeError("HTTP 400 context length exceeded"), model="bonsai2:27b",
                     mode="local_preferred")
        assert "bonsai2:27b" in o["text"] and "Nothing was sent" in o["text"]
        assert "too big" in o["reason"]
        assert o["served"] == {"model": "bonsai2:27b", "local": True, "answered": False}

    @pytest.mark.parametrize("cause,expect", [
        (RuntimeError("Read timed out"), "timed out"),
        (RuntimeError("HTTP 503 Loading model"), "isn't answering"),
        (RuntimeError("n_ctx exceeded"), "too big"),
        (RuntimeError("something odd"), "hit an error"),
    ])
    def test_the_reason_is_plain_words(self, cause, expect):
        assert expect in lo.short_reason(cause)

    def test_under_local_only_the_cloud_choice_names_the_mode(self):
        o = lo.build("it timed out", mode="local_only")
        assert o["confirm_local_only"] is True
        assert o["choices"][2]["label"] == "Send this one turn to Claude"

    def test_the_local_only_confirm_names_the_mode_and_offers_to_keep_it_local(self):
        o = lo.build(mode="local_only", kind="local_only_confirm")
        assert "local-only mode" in o["text"] and "Nothing has been sent" in o["text"]
        assert [c["id"] for c in o["choices"]] == ["send_claude", "retry_local"]
        assert [c["label"] for c in o["choices"]][1] == "Keep it local"

    def test_the_spoken_line_is_short_and_asks_one_question(self):
        o = lo.build("it timed out", mode="local_preferred")
        assert len(o["spoken"]) < 160 and o["spoken"].count("?") == 1
        assert "Claude" in o["spoken"] and "try again" in o["spoken"].lower()

    def test_every_offer_has_its_own_id(self):
        assert lo.build("x")["id"] != lo.build("x")["id"]


class TestTheSpokenReply:
    @pytest.mark.parametrize("words,choice", [
        ("yes", "send_claude"), ("Yeah.", "send_claude"), ("sure", "send_claude"),
        ("yes please", "send_claude"), ("ask Claude", "send_claude"),
        ("send it to Claude", "send_claude"), ("go ahead", "send_claude"),
        ("try again", "retry_local"), ("retry", "retry_local"), ("Try that again.", "retry_local"),
        ("no", "decline"), ("not now", "decline"), ("never mind", "decline"), ("cancel", "decline"),
    ])
    def test_plain_replies(self, words, choice):
        assert lo.classify_reply(words) == choice

    @pytest.mark.parametrize("words", [
        "yes but only send the first paragraph",
        "yes, but leave out the calendar",
        "sure if it's cheap",
        "what would it cost",
        "ask Claude about the weather in Paris",
        "", None, "x" * 200,
    ])
    def test_a_conditional_yes_or_anything_else_is_ordinary_conversation(self, words):
        assert lo.classify_reply(words) is None


class TestTheVoiceRules:
    def _offered(self, cid="c1", message="move my dentist appointment"):
        offer = lo.build("it timed out", mode="local_preferred")
        lo.remember(cid, offer, message)
        return offer

    def test_a_yes_decides_the_offer_voiced_in_the_previous_turn(self):
        offer = self._offered()
        choice, message = lo.take_reply("c1", "yes", [offer["id"]])
        assert choice == "send_claude" and message == "move my dentist appointment"

    def test_a_retry_returns_the_original_words(self):
        offer = self._offered()
        assert lo.take_reply("c1", "try again", [offer["id"]]) == (
            "retry_local", "move my dentist appointment")

    def test_a_yes_when_nothing_was_voiced_last_turn_is_not_an_answer(self):
        self._offered()
        assert lo.take_reply("c1", "yes", []) == (None, "")
        assert lo.pending("c1", []) is None

    def test_an_offer_voiced_some_other_turn_does_not_qualify(self):
        self._offered()
        assert lo.take_reply("c1", "yes", ["some-other-offer"]) == (None, "")

    def test_another_calls_offer_is_never_answered_by_this_calls_yes(self):
        offer = self._offered("c1")
        assert lo.take_reply("c2", "yes", [offer["id"]]) == (None, "")

    def test_a_conditional_yes_lapses_the_offer_and_sends_nothing(self):
        offer = self._offered()
        choice, _ = lo.take_reply("c1", "yes, but only the first part", [offer["id"]])
        assert choice is None
        assert lo.pending("c1", [offer["id"]]) is None      # asked once, not again

    def test_the_offer_is_consumed_by_its_answer(self):
        offer = self._offered()
        lo.take_reply("c1", "yes", [offer["id"]])
        assert lo.take_reply("c1", "yes", [offer["id"]]) == (None, "")

    def test_a_stale_offer_does_not_qualify(self, monkeypatch):
        offer = self._offered()
        monkeypatch.setattr(lo, "_OFFER_TTL_S", -1)
        assert lo.pending("c1", [offer["id"]]) is None


class TestTheOneCloudTurnTheOwnerAskedFor:
    def test_it_runs_under_a_user_explicit_confirmed_intent_on_a_cloud_seat(self, monkeypatch):
        from agent_friday.services import agent as agent_mod
        seen = {}

        def fake(messages, **kw):
            cur = ci.current()
            seen.update(intent=cur.intent, source=cur.source, confirmed=cur.confirmed,
                        seat=kw.get("conversation_seat"))
            return "claude answered", []
        monkeypatch.setattr(agent_mod, "_generate_agent", fake)
        out = lo.run_cloud_once([{"role": "user", "content": "hi"}], source="voice_offer")
        assert out == ("claude answered", [])
        assert seen["intent"] == "user_explicit" and seen["confirmed"] is True
        assert seen["source"] == "voice_offer"
        assert str(seen["seat"]["model"]).startswith("claude")
        assert ci.current() is None                       # one turn only

    def test_a_local_unavailable_is_a_runtime_error_so_old_callers_still_stop(self):
        assert issubclass(ci.LocalUnavailable, RuntimeError)
        assert issubclass(ci.NoCloudIntent, RuntimeError)
