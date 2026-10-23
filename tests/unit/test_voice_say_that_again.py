"""Say that again (north star NS-33.3-2: voice supports repetition).

"Say that again" makes Friday say her last reply again, word for word, with
no model call: in a local voice call and on the phone. A question about the
past ("what did you say about the budget?") is not a repeat request.
"""
from __future__ import annotations

import pytest

from agent_friday.services import voice_repeat
from tests.unit import test_voice_session as tvs
from tests.unit.phone_fakes import phone_home, quiet  # noqa: F401


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    from agent_friday.services import off_record, voice_delivery
    monkeypatch.setattr(off_record, "_settings", lambda: {})
    monkeypatch.setattr(voice_delivery, "settings_snapshot", lambda: {})
    yield
    for s in tvs._OPEN:
        try:
            s.close()
            s._speaker.join(1.5)
        except Exception:
            pass
    del tvs._OPEN[:]


@pytest.mark.parametrize("said", [
    "say that again", "Say that again, please.", "Sorry, could you say that again?", "repeat that",
    "Can you repeat that?", "what did you say?", "What did you just say", "pardon?", "Pardon me?",
    "come again?", "I didn't catch that", "Sorry, I didn't hear that.", "Friday, repeat that please",
    "huh, what was that?", "say it again", "repeat the last thing you said", "once more",
    "Could you say that one more time?",
])
def test_repeat_requests(said):
    assert voice_repeat.is_repeat_request(said), said


@pytest.mark.parametrize("said", [
    "what did you say about the budget?", "what did I say yesterday?", "repeat the report every Monday",
    "say hello to Priya", "that again was wrong", "can you say that in French?",
    "what did we say about Tuesday's meeting", "send that again", "", "again",
    "please repeat that sentence to Mum in an email",
])
def test_not_repeat_requests(said):
    assert not voice_repeat.is_repeat_request(said), said


def test_a_local_call_says_its_last_reply_again_without_the_model():
    mouth = tvs._Mouth()
    s, frames = tvs._session(["Your train leaves at nine. Platform four."], mouth=mouth, stream=False)
    s.start()
    s.run_turn("when is my train")
    asked = []
    real = s.generate
    s.generate = lambda *a: asked.append(a) or real(*a)
    s.run_turn("Sorry, say that again?")
    assert asked == [], "a repeat must not ask the model"
    assert mouth.spoken == ["Your train leaves at nine.", "Platform four.",
                            "Your train leaves at nine.", "Platform four."]
    done = [f for f in frames if f["type"] == "voice_turn_done"]
    assert done[-1]["agent_text"] == "Your train leaves at nine. Platform four."


def test_a_repeat_before_anything_was_said_says_so():
    mouth = tvs._Mouth()
    s, _frames = tvs._session(["unused"], mouth=mouth, stream=False)
    s.start()
    s.generate = lambda *a: pytest.fail("a repeat must not ask the model")
    s.run_turn("repeat that")
    assert mouth.spoken == [voice_repeat.NOTHING_YET]


def test_a_question_about_the_past_still_goes_to_the_mind():
    mouth = tvs._Mouth()
    s, _frames = tvs._session(["You said the budget is fine."], mouth=mouth, stream=False)
    s.start()
    s.run_turn("what did you say about the budget?")
    assert mouth.spoken == ["You said the budget is fine."]

def test_a_repeat_is_not_stored_or_summarised_again():
    """The conversation keeps the first reply in the form it keeps it (a card
    read aloud as its neutral line; off-record words not at all): a repeat adds
    nothing, so a card's words can never reach storage through it."""
    mouth = tvs._Mouth()
    s, _frames = tvs._session(["Pay the gas bill of 84 pounds?"], mouth=mouth, stream=False)
    stored = []
    s.hooks["persist"] = lambda said, reply, cid: stored.append((said, reply)) or "Friday read a card aloud."
    s.start()
    s.run_turn("anything waiting")
    s.run_turn("say that again")
    assert stored == [("anything waiting", "Pay the gas bill of 84 pounds?")]
    assert s.turn_log == [("anything waiting", "Friday read a card aloud.")]
    assert mouth.spoken == ["Pay the gas bill of 84 pounds?"] * 2


def test_the_phone_says_its_last_line_again(phone_home, quiet):
    import array
    from agent_friday.phone import live_call as lc
    from tests.unit.test_phone_live_call import FakeWS, OneShotVAD

    class Engine:
        def __init__(self):
            self.said = []
            self.heard = iter(["what's the time", "sorry, say that again"])

        def transcribe(self, pcm):
            return next(self.heard)

        def synthesize(self, text):
            self.said.append(text)
            return array.array("h", [1000] * 2400).tobytes()

    eng = Engine()
    calls = []
    bridge = lc.CallBridge(FakeWS(), engine=eng, vad=OneShotVAD(), threaded=False,
                           agent=lambda h: calls.append(1) or "It's half past two.")
    bridge._turn(b"\x00" * 3200)
    bridge._turn(b"\x00" * 3200)
    assert calls == [1], "the repeat did not ask the agent"
    assert eng.said[-1] == eng.said[-2]
    assert bridge.history[-1]["who"] == "friday"
    assert bridge.history[-1]["text"] == bridge.history[-3]["text"]