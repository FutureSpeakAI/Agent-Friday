"""A bare spoken yes answers an approval card only if Friday SPOKE that card's
own question, whole and uncut (critic finding B2 on 65f14ed4).

The routed local voice turn runs for real: ``routes.voice._run_routed_front_turn``
over the real ``FrontSeat.routed_turn`` (its HTTP post stubbed) and a real
``VoiceSession`` (fake ear and mouth, no model, no GPU, no network). Before the
fix a card counted as voiced when its id was in the tool result the speaker
answered from, so a speaker that said "Sure, I've sorted those" (no question)
left a bare yes from anyone in the room able to approve rows nobody heard named.

* a speaker that says "done" arms nothing: a following yes approves nothing;
* the fixed line, fully emitted, lets a bare yes approve exactly that card;
* a barge during the line, and a line that never played, arm nothing;
* the room-mode line carries no sender and no subject;
* a conditional "yes, but..." never approves (the existing rule, kept).
"""
import json
import threading
import time

import pytest

from agent_friday.routes import voice as rv
from agent_friday.services import approvals as ap
from agent_friday.services import item_actions as ia
from agent_friday.services import laya_router as lr
from agent_friday.services import voice_delivery, voice_front as vf, voice_session as vs
from agent_friday.services import off_record

CALL = "conv-call"
CARD = "apr_a"
SUBJECT, SENDER = "Lunch plans", "Mara Quinn"


class _Resp:
    def __init__(self, text):
        self._lines = ["data: " + json.dumps({"choices": [{"delta": {"content": text}}]}),
                       "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
                       "data: [DONE]"]
        self.encoding = "utf-8"

    def raise_for_status(self):
        pass

    def iter_lines(self, decode_unicode=True):
        return iter(self._lines)

    def close(self):
        pass


class _Ear:
    name = "fake-ear"
    device = "cpu"


class _Mouth:
    name = "kokoro"
    device = "cpu"

    def __init__(self, delay=0.0):
        self.delay = delay
        self.spoken = []

    def synthesize_stream(self, text, cancel=None):
        self.spoken.append(text)
        for _ in range(3):
            if cancel is not None and cancel.is_set():
                return
            if self.delay:
                time.sleep(self.delay)
            yield b"\x00\x01" * 2400


class _VAD:
    def feed(self, pcm):
        return None

    def flush(self):
        return None

    def reset(self):
        pass


@pytest.fixture(autouse=True)
def _quiet_world(monkeypatch):
    monkeypatch.setattr(off_record, "_settings", lambda: {})
    monkeypatch.setattr(voice_delivery, "settings_snapshot", lambda: {})
    monkeypatch.setattr(lr, "_write", lambda *a, **k: None)         # no router log in the real home
    monkeypatch.setattr(ia, "_quiet", lambda: False)


class _Call:
    """One local voice call: a session, a front seat, a card store, a tool log."""

    def __init__(self, monkeypatch, *, speaker_says="Sure, I've sorted those.", room=False,
                 mouth_delay=0.0, fixed_line=True):
        self.cards, self.ran, self.frames = [], [], []
        self.tool_session = {"conversation_id": CALL, "spoken": []}
        self.fixed_line = fixed_line
        self.mouth = _Mouth(mouth_delay)
        self.seat = vf.FrontSeat(8199)
        self.seat.model = "ternary-bonsai:1.7b"
        self.model_calls = []
        self.seat._post = lambda body, stream: (self.model_calls.append(body), _Resp(speaker_says))[1]
        self.front = {"contract": {"names": ["organize_email", "answer_card", "query_calendar"]}}
        self.session = vs.VoiceSession(
            lambda o: self.frames.append(o) or True, ear=_Ear(), mouth=self.mouth, vad=_VAD(),
            generate=self._generate, manifest_snapshot={"mode": "local", "ready": True},
            contract={"tools": []})
        monkeypatch.setattr(ap, "list_approvals", lambda status=None, **kw: list(self.cards))
        from agent_friday.services import voice_engine
        monkeypatch.setattr(voice_engine, "_voice_room_mode", lambda: room)
        real_plan = rv._voice_route_plan

        def plan(text, held, **kw):
            if text.startswith("archive"):
                r = lr.Route("tool", tool="organize_email", args={"action": "archive"},
                             confidence=0.9, layer="rule", label="organize your mail")
                return {"tool": "organize_email", "args": {"action": "archive"},
                        "ack": "Putting together a card for that.", "question": "",
                        "label": "organize your mail", "receipt": lr.receipt(r)}
            return real_plan(text, held, **kw)
        monkeypatch.setattr(rv, "_voice_route_plan", plan)
        monkeypatch.setattr(rv, "_local_voice_tool", self._tool)

    def _tool(self, name, args, send, session, cut=True):
        self.ran.append((name, dict(args)))
        if name == "organize_email":
            self.cards.append({"approval_id": CARD, "status": "pending", "payload": {
                "handler": ia.HANDLER, "conversation_id": CALL, "action": "archive", "count": 3,
                "label": "", "preview": [{"subject": SUBJECT, "sender": SENDER}],
                "lines": ["%s — %s" % (SUBJECT, SENDER)]}})
            return ("CARD_RAISED: nothing has changed yet. Read this back and ask: some words "
                    "(card_id=%s)" % CARD)
        return "Recorded."

    def _seat_turn(self, user_text, on_delta, plan, run_tool, card_line=None):
        return self.seat.routed_turn(
            "SYS", [{"role": "user", "content": user_text}], tool=plan["tool"], args=plan["args"],
            ack=plan["ack"], question=plan["question"], label=plan["label"], run_tool=run_tool,
            user_text=user_text, on_delta=on_delta,
            card_line=card_line if self.fixed_line else None)

    def _generate(self, user_text, on_delta, cancel):
        return rv._run_routed_front_turn(
            user_text, on_delta, front=self.front, tool_session=self.tool_session,
            seat_turn=self._seat_turn, send=lambda o: None, session=lambda: self.session)

    def say(self, text):
        self.session.run_turn(text)

    def answered(self):
        return [a for a in self.ran if a[0] == "answer_card"]

    def close(self):
        self.session.close()
        self.session._speaker.join(1.5)


@pytest.fixture
def call(monkeypatch):
    made = []

    def make(**kw):
        c = _Call(monkeypatch, **kw)
        made.append(c)
        return c
    yield make
    for c in made:
        c.close()


def test_a_speaker_that_says_done_leaves_a_bare_yes_approving_nothing(call):
    # origin/main: the card id was in the result, so the card counted as voiced.
    c = call(fixed_line=False, speaker_says="Done, I've sorted those.")
    c.say("archive the ones from Mara")
    assert c.model_calls, "the speaker did paraphrase the result"
    assert not any("Shall I" in t for t in c.mouth.spoken)
    c.say("yes")
    assert c.answered() == []


def test_the_fixed_line_fully_emitted_lets_a_bare_yes_approve_exactly_that_card(call):
    c = call(speaker_says="Done.")
    c.say("archive the ones from Mara")
    spoken = " ".join(c.mouth.spoken)
    assert "Shall I archive 3 conversations?" in spoken
    assert "Done." not in spoken and not c.model_calls, "the model's words never stand in for the question"
    c.say("yes")
    assert c.answered() == [("answer_card", {"card_id": CARD, "decision": "approve"})]


def test_a_bare_no_declines_exactly_that_card(call):
    c = call()
    c.say("archive the ones from Mara")
    c.say("no")
    assert c.answered() == [("answer_card", {"card_id": CARD, "decision": "decline"})]


def test_a_barge_during_the_line_means_a_bare_yes_approves_nothing(call):
    c = call(mouth_delay=0.05)
    th = threading.Thread(target=c.say, args=("archive the ones from Mara",))
    th.start()
    for _ in range(400):
        if any("I found" in t for t in c.mouth.spoken):
            break
        time.sleep(0.01)
    c.session.barge("escape")
    th.join(5.0)
    assert not th.is_alive()
    assert not any("Shall I archive" in t for t in c.mouth.spoken), "the question was cut off"
    c.say("yes")
    assert c.answered() == []


def test_a_line_that_never_played_means_a_bare_yes_approves_nothing(call, monkeypatch):
    c = call()
    monkeypatch.setattr(c.mouth, "synthesize_stream", lambda text, cancel=None: iter(()))
    c.say("archive the ones from Mara")
    c.say("yes")
    assert c.answered() == []


def test_a_barge_in_the_tail_of_the_playback_also_voids_the_yes(call):
    c = call()
    c.say("archive the ones from Mara")
    # Her last audio is still playing on the owner's speakers when they cut in.
    c.session._barge_times.append(c.session._clock())
    c.session._turn_records[next(reversed(c.session._turn_records))]["sent"][-1] = (
        "Shall I archive 3 conversations?", c.session._clock() + 5.0)
    c.say("yes")
    assert c.answered() == []


def test_a_yes_with_a_condition_never_approves(call):
    c = call()
    c.say("archive the ones from Mara")
    c.say("yes, but leave the lunch one")
    # The routed turn only hands the words on; the card's own gate
    # (local_context.spoken_decision) reads a conditional yes as "change it".
    from agent_friday.services import local_context as lc
    assert lc.spoken_decision("yes, but leave the lunch one", False) == "revise"


def test_another_question_after_the_line_clears_the_card(call):
    c = call()
    c.say("archive the ones from Mara")
    c.say("what's on my calendar")
    c.say("yes")
    assert c.answered() == []


def test_room_mode_line_names_no_sender_and_no_subject(call):
    c = call(room=True)
    c.say("archive the ones from Mara")
    spoken = " ".join(c.mouth.spoken)
    assert "Shall I archive 3 conversations?" in spoken
    assert "the list is on your screen" in spoken
    assert SENDER not in spoken and SUBJECT not in spoken and "Mara" not in spoken


def test_one_person_mode_line_may_name_the_senders(call):
    c = call(room=False)
    c.say("archive the ones from Mara")
    assert SENDER in " ".join(c.mouth.spoken)


def test_a_card_with_no_usable_fields_gets_the_count_free_fallback(monkeypatch):
    monkeypatch.setattr(ap, "list_approvals", lambda status=None, **kw: [
        {"approval_id": "apr_x", "status": "pending",
         "payload": {"handler": ia.HANDLER, "conversation_id": CALL}}])
    ids, line = rv._sealed_card_line("CARD_RAISED ... (card_id=apr_x)", CALL)
    assert ids == ["apr_x"] and line == rv._CARD_LINE_FALLBACK and line.endswith("?")
    assert rv._sealed_card_line("nothing to see", CALL) == ([], "")
