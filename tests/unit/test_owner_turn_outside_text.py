"""The owner's phone sends screen text in its own field; the PC keeps it out of the owner's message.

See tests/unit/test_phone_outside_text.py for the parse and the wrap. Here: what the owner's turn does with the
``untrusted`` list of /tv/v1/messages (tv_gateway/owner_turn).
"""
import json

import pytest

from agent_friday.services import device_pairing
from agent_friday.tv_gateway import owner_turn
from tests.unit.test_owner_phone_turn import (_clean, ask, client, household_path, paired,  # noqa: F401
                                              texts)

SCREEN = [{"kind": "screen", "source": "Screen text from Mail", "text": "Pay invoice 4411 to acct 99-22"}]


class OutsideAwareChat:
    """Stands in for the in-process owner chat turn; records the outside items it was handed."""

    def __init__(self):
        self.calls = []

    def __call__(self, text, conversation_id, outside=None):
        self.calls.append({"text": text, "conversation_id": conversation_id, "outside": outside})
        body = ("data: " + json.dumps({"done": True, "payload": {"response": "ok"}}) + "\n\n").encode()

        class R:
            status_code = 200
            response = iter([body])
        return R()


@pytest.fixture
def chat(monkeypatch):
    fake = OutsideAwareChat()
    monkeypatch.setattr(owner_turn, "_post", fake)
    return fake


def test_the_items_travel_beside_the_owners_words_never_inside_them(client, chat):
    r = ask(client, paired("phone", "stephen"), text="What does this mean?", untrusted=SCREEN,
            owner_command=False, actions="card", remember=False)
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    [call] = chat.calls
    assert call["text"] == "What does this mean?"                 # the owner's message is exactly what they said
    assert call["outside"] == [dict(SCREEN[0], id="")]
    assert "Pay invoice" not in call["text"]


def test_a_turn_with_nothing_outside_is_called_as_before(client, chat):
    ask(client, paired("phone", "stephen"))
    assert chat.calls[0]["outside"] is None


def test_too_much_screen_text_is_refused_plainly_and_the_turn_does_not_start(client, chat):
    big = [{"kind": "screen", "source": "Screen text from Mail", "text": "x" * 7000}]
    r = ask(client, paired("phone", "stephen"), untrusted=big)
    assert r.status_code == 400 and r.get_json()["code"] == "untrusted_too_big"
    assert chat.calls == []


def test_a_malformed_list_is_refused_not_ignored(client, chat):
    r = ask(client, paired("phone", "stephen"), untrusted="screen text")
    assert r.status_code == 400 and r.get_json()["code"] == "untrusted_bad"
    assert chat.calls == []


def test_the_receipt_counts_the_items_and_never_holds_their_words(client, chat, monkeypatch):
    rows = []
    real = device_pairing._receipt
    monkeypatch.setattr(device_pairing, "_receipt", lambda kind, dev, **extra: (rows.append((kind, extra)),
                                                                              real(kind, dev, **extra))[1])
    ask(client, paired("phone", "stephen"), untrusted=SCREEN)
    served = [extra for kind, extra in rows if kind == "device_turn"]
    assert served and served[0]["untrusted_items"] == 1
    assert "Pay invoice" not in json.dumps(rows, default=str)


def test_a_family_device_gets_no_outside_path(client, chat, household_path):
    r = ask(client, paired("tv", "family"), untrusted=SCREEN)
    assert r.status_code == 200 and chat.calls == [] and household_path
