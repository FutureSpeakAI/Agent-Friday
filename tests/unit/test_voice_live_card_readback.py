"""Gemini Live (/ws/live): a bare yes or no decides an approval card through
``answer_card`` only if Friday's output transcription shows the card's sealed
question was said whole and uncut since the card was raised.

The tool-call path runs for real (``routes.voice._live_card_tool`` over the
card watch the live loop feeds from output transcription and barges); the tool
runner and the card store are stubs. No session, no network.
"""
import asyncio

import pytest

from agent_friday.routes import voice as rv
from agent_friday.services import approvals as ap
from agent_friday.services import item_actions as ia
from agent_friday.services import laya_router as lr

CALL = "conv-live"
CARD = "apr_live"


class _Live:
    def __init__(self, monkeypatch, room=False):
        self.cards, self.ran = [], []
        self.session = {"conversation_id": CALL, "owner_text": "", "card_watch": rv._LiveCardWatch()}
        monkeypatch.setattr(ap, "list_approvals", lambda status=None, **kw: list(self.cards))
        monkeypatch.setattr(ap, "get_approval", lambda i: next(
            (c for c in self.cards if c["approval_id"] == i), None))
        monkeypatch.setattr(ia, "_quiet", lambda: False)
        from agent_friday.services import voice_engine
        monkeypatch.setattr(voice_engine, "_voice_room_mode", lambda: room)

        async def run(fname, fargs, send, session=None, limit=None, runner=None):
            self.ran.append((fname, dict(fargs)))
            if fname == "organize_email":
                self.cards.append({"approval_id": CARD, "status": "pending", "payload": {
                    "handler": ia.HANDLER, "conversation_id": CALL, "action": "archive", "count": 3,
                    "label": "", "preview": [{"subject": "Lunch plans", "sender": "Mara Quinn"}],
                    "lines": ["Lunch plans"]}})
                return "CARD_RAISED: nothing has changed yet. Read this back and ask: x (card_id=%s)" % CARD
            return "Recorded."
        monkeypatch.setattr(rv, "_voice_tool_with_limit", run)

    def tool(self, name, args=None):
        return asyncio.run(rv._live_card_tool(name, args or {}, lambda o: True, self.session))

    def raise_card(self):
        out = self.tool("organize_email", {"action": "archive"})
        assert rv._LIVE_CARD_ASK in out, "the sealed question rides in the tool result"
        return out.split(rv._LIVE_CARD_ASK, 1)[1]

    def yes(self, words="yes"):
        self.session["owner_text"] = words
        return self.tool("answer_card", {"card_id": CARD, "decision": "approve"})

    def approved(self):
        return [r for r in self.ran if r[0] == "answer_card"]


@pytest.fixture
def live(monkeypatch):
    return lambda **kw: _Live(monkeypatch, **kw)


def test_a_paraphrase_then_a_bare_yes_approves_nothing(live):
    lv = live()
    lv.raise_card()
    lv.session["card_watch"].heard("Done, I've sorted those.")
    out = lv.yes()
    assert lv.approved() == [] and out.startswith("NOT RECORDED")
    assert "confirm on screen" in out


def test_the_line_said_verbatim_then_a_bare_yes_approves_that_card(live):
    lv = live()
    line = lv.raise_card()
    for piece in (line[:30], line[30:]):          # transcription arrives in pieces
        lv.session["card_watch"].heard(piece)
    assert lv.yes() == "Recorded."
    assert lv.approved() == [("answer_card", {"card_id": CARD, "decision": "approve"})]


def test_a_line_cut_mid_way_by_a_barge_approves_nothing(live):
    lv = live()
    line = lv.raise_card()
    lv.session["card_watch"].heard(line[:30])
    lv.session["card_watch"].cut()
    lv.session["card_watch"].heard(line[30:])
    assert lv.yes().startswith("NOT RECORDED") and lv.approved() == []


def test_a_barge_after_the_whole_line_does_not_void_it(live):
    lv = live()
    lv.session["card_watch"].heard(lv.raise_card())
    lv.session["card_watch"].cut()
    assert lv.yes() == "Recorded."


def test_a_refused_bare_yes_hands_back_the_line_and_a_re_read_then_counts(live):
    lv = live()
    lv.raise_card()
    out = lv.yes()
    line = out.split("say these words exactly: ", 1)[1]
    lv.session["card_watch"].heard(line)
    assert lv.yes() == "Recorded."


def test_a_yes_that_names_the_action_is_left_to_the_existing_rules(live):
    lv = live()
    lv.raise_card()
    assert lv.yes("yes, archive them") == "Recorded."


def test_room_mode_line_names_no_sender_and_no_subject(live):
    lv = live(room=True)
    line = lv.raise_card()
    assert "Shall I archive 3 conversations?" in line
    assert "Mara" not in line and "Lunch" not in line
