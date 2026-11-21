"""A cloud model picked on the phone answers everything a local one does, and a pick is never refused twice.

Stephen, 2026-11-20 07:18, on the phone: a local turn with the knowledge graph answered fine; he then picked
Claude Opus 5.5 and asked a follow-up, and the PC said "this computer would not use that model for this chat"
on that message, and again on the next, with no answer ever. The reply badge before it read "Bonsai 2 · 27B ·
null", and the model list the PC "can answer now with" had no Opus 5.5 though the PC lists it.

Traced on live: the phone held a chat id the PC no longer had. With no model the turn resolves to Main and
answers; with a pick, bind_seat read the PC's 404 as an empty record and turned it into the generic refusal,
on every message. The promises guarded here (the flow is the screenshot's):

* (1) a chat the PC no longer has is not a reason to refuse a pick: a fresh chat is started on the model, the
  turn runs there, the reply names the new chat (so the phone keeps it) and says, once, that a new chat was
  started;
* (2) the PC's own HTTP failure on the bind (a 500) is a 503 "try again" with its own code, never the 409
  "would not use that model for this chat" the phone can do nothing about;
* (3) the same refusal is never given twice: a seat the PC refuses with a reason is a 409 once; the next message
  with the same pick on the same chat from the same phone is ANSWERED on the chat's own seat, marked
  substituted, with the PC's reason as the notice (a visible fallback, never a loop; never a quiet other model);
* (4) a cloud model bound from the phone carries a one-time disclosure in the reply: what leaves the PC and for
  whom; a chat already on that model carries none;
* (5) the friday block never carries a JSON null: a phone's JSON reader prints it as the word "null".
"""
import json

import pytest

from agent_friday.tv_gateway import owner_turn
from tests.unit.test_owner_turn_model_truth import Desk, ask, block, events, phone  # noqa: F401
from tests.unit.test_owner_turn_model_truth import _clean  # noqa: F401  (the autouse fixture)
from agent_friday.tv_gateway import app as gateway


@pytest.fixture
def gw():
    return gateway.create_app().test_client()

OPUS = "claude-opus-5-5"
STALE = "conv-gone"                      # the phone's remembered chat; the PC no longer has it


class R:
    def __init__(self, status, d):
        self.status_code, self.d = status, d

    def get_json(self, silent=True):
        return self.d


class LiveDesk(Desk):
    """The desktop as it is on live: a conversation it no longer has answers 404 on GET and PATCH."""

    def __init__(self):
        super().__init__()
        self.listed = {"bonsai2:27b", OPUS, "claude-sonnet-5-5"}
        self.fail_patch = None           # an HTTP status the PATCH answers with instead of binding
        self.minted = 0                  # every chat this desktop starts gets its own id

    def api(self, method, path, body=None):
        self.api_calls.append((method, path, body))
        cid = path.rsplit("/", 1)[1] if method != "POST" else None
        if cid is not None and cid not in self.convs:
            return R(404, {"status": "error", "error": "no such conversation: %s" % cid})
        if method == "PATCH" and self.fail_patch:
            return R(self.fail_patch, {"status": "error", "error": "the seat store is unavailable"})
        if method == "POST":
            self.minted += 1
            new = "conv-new-%d" % self.minted
            self.convs[new] = {"id": new, "seat": body.get("seat")}
            return R(201, {"conversation": dict(self.convs[new])})
        r = super().api(method, path, body)
        return R(200, r.d)


@pytest.fixture
def desk(monkeypatch):
    d = LiveDesk()
    monkeypatch.setattr(owner_turn, "_api", d.api, raising=False)
    monkeypatch.setattr(owner_turn, "_slate", lambda dev: [
        {"id": m, "name": {OPUS: "Claude Opus 5.5", "bonsai2:27b": "Ternary Bonsai 2 27B",
                           "claude-sonnet-5-5": "Claude Sonnet 5.5"}[m],
         "available": True, "reason": None, "kind": "local" if ":" in m else "cloud",
         "provider": "arbiter-local" if ":" in m else "anthropic",
         "provider_name": "Local" if ":" in m else "Anthropic"} for m in sorted(d.listed)])
    monkeypatch.setattr(owner_turn, "_post", d.post)
    # absent on the code before the memo: red by assertion, not by setup
    reset = getattr(owner_turn, "_reset_for_tests", lambda: None)
    reset()
    yield d
    reset()


def cloud_answer(desk):
    desk.answer = {"model": OPUS, "seat": "cloud", "provider": "anthropic"}


# ---- (1) the screenshot: a stale chat + a cloud pick ----------------------------------------------------------

def test_the_screenshot_flow_a_cloud_pick_in_a_chat_the_pc_lost_is_answered_in_a_fresh_chat_not_refused(gw, desk):
    dev = phone()
    # 07:16 the local turn with no model: the PC answered it (it resolved the stale id to Main)
    r1 = ask(gw, dev, conversation_id=STALE)
    assert r1.status_code == 200 and block(r1)["model"] == "bonsai2:27b"
    # 07:17 he picks Opus 5.5 and asks the follow-up
    cloud_answer(desk)
    r2 = ask(gw, dev, model=OPUS, conversation_id=STALE)
    assert r2.status_code == 200, r2.get_data(as_text=True)
    b = block(r2)
    assert b["model"] == OPUS and b["substituted"] is False
    # the turn ran in a chat the PC really has, bound to his pick, and the reply names it so the phone keeps it
    new = b["conversation_id"]
    assert new == "conv-new-1" and desk.convs[new]["seat"] == {"model": OPUS}
    assert desk.turns[-1]["conversation_id"] == new
    # and he is told, in the reply, that a new chat was started (never silently)
    assert "new chat" in b["notice"].lower()


def test_the_generic_refusal_is_never_the_answer_to_a_chat_the_pc_lost(gw, desk):
    r = ask(gw, phone(), model=OPUS, conversation_id=STALE)
    assert "would not use that model" not in r.get_data(as_text=True)


def test_the_next_message_after_a_lost_chat_runs_in_the_new_chat_with_no_second_notice(gw, desk):
    dev = phone()
    cloud_answer(desk)
    first = block(ask(gw, dev, model=OPUS, conversation_id=STALE))
    second = block(ask(gw, dev, model=OPUS, conversation_id=first["conversation_id"]))
    assert second["conversation_id"] == first["conversation_id"] and second["substituted"] is False
    assert "notice" not in second                         # the chat is already on his pick: nothing to say


# ---- (2) the PC's own failure is said as such --------------------------------------------------------------

def test_a_bind_the_pc_fails_on_is_a_503_try_again_with_its_own_code_not_the_409_refusal(gw, desk):
    desk.fail_patch = 500
    r = ask(gw, phone(), model=OPUS, conversation_id="conv-old")
    body = r.get_json()
    assert r.status_code == 503 and body["code"] == "chat_unavailable", body     # the code the phone already speaks
    assert "could not change that chat" in body["error"] and "would not use that model" not in body["error"]
    assert desk.turns == []


# ---- (3) never the same refusal twice ------------------------------------------------------------------------

def test_a_seat_refusal_is_given_once_then_the_pick_falls_back_visibly_on_the_chats_own_seat(gw, desk):
    dev = phone()
    desk.refuse = "bonsai2:27b is already serving on this machine and there is only room for one local model at a time"
    r1 = ask(gw, dev, model="claude-sonnet-5-5", conversation_id="conv-old")
    assert r1.status_code == 409 and desk.turns == []           # once, loudly, with the reason
    r2 = ask(gw, dev, model="claude-sonnet-5-5", conversation_id="conv-old")
    assert r2.status_code == 200, r2.get_data(as_text=True)      # the same message again is answered
    b = block(r2)
    assert b["model"] == "bonsai2:27b" and b["requested"] == "claude-sonnet-5-5" and b["substituted"] is True
    assert "one local model at a time" in b["notice"] and "bonsai2:27b" in b["notice"]
    assert desk.turns[-1]["conversation_id"] == "conv-old"


def test_a_refusal_remembered_for_one_phone_and_chat_is_not_spent_for_another(gw, desk):
    dev, other = phone(), phone()
    desk.refuse = "no room"
    assert ask(gw, dev, model="claude-sonnet-5-5", conversation_id="conv-old").status_code == 409
    desk.convs["conv-two"] = {"id": "conv-two", "seat": None}
    assert ask(gw, dev, model="claude-sonnet-5-5", conversation_id="conv-two").status_code == 409   # another chat
    assert ask(gw, other, model="claude-sonnet-5-5", conversation_id="conv-old").status_code == 409  # another phone
    same = ask(gw, dev, model="claude-sonnet-5-5", conversation_id="conv-old")
    assert same.status_code == 200                                         # the same: answered


def test_a_pick_the_pc_then_binds_forgets_the_earlier_refusal(gw, desk):
    dev = phone()
    desk.refuse = "no room"
    assert ask(gw, dev, model="claude-sonnet-5-5", conversation_id="conv-old").status_code == 409
    desk.refuse = None
    cloud_answer(desk)
    desk.answer["model"] = "claude-sonnet-5-5"
    b = block(ask(gw, dev, model="claude-sonnet-5-5", conversation_id="conv-old"))
    assert b["substituted"] is False and desk.convs["conv-old"]["seat"] == {"model": "claude-sonnet-5-5"}
    # the PC drops the seat and refuses the same pick for the same reason again: a 409 again, not a fallback
    desk.convs["conv-old"]["seat"] = None
    desk.refuse = "no room"
    assert ask(gw, dev, model="claude-sonnet-5-5", conversation_id="conv-old").status_code == 409


def test_a_device_that_keeps_sending_the_lost_chat_id_is_answered_in_the_one_fresh_chat(gw, desk):
    dev = phone()
    cloud_answer(desk)
    first = block(ask(gw, dev, model=OPUS, conversation_id=STALE))
    again = block(ask(gw, dev, model=OPUS, conversation_id=STALE))
    assert again["conversation_id"] == first["conversation_id"] == "conv-new-1" and "notice" not in again
    assert desk.minted == 1


def test_a_refusal_in_a_lost_or_new_chat_is_not_repeated_when_the_phone_retries_into_the_chat_it_was_handed(gw, desk):
    desk.refuse = "no room"
    for sent in (STALE, None):              # a chat the PC lost, and a first message with no chat id at all
        dev = phone()
        turns = len(desk.turns)
        first = ask(gw, dev, model=OPUS, conversation_id=sent) if sent else ask(gw, dev, model=OPUS)
        body = first.get_json()
        assert first.status_code == 409 and len(desk.turns) == turns        # refused, no turn ran
        handed = body["conversation_id"]                    # the chat this refusal made, so a retry reuses it
        if sent:
            assert "new chat" in body["error"].lower()      # the lost chat is said once, with the refusal itself
        made = desk.minted
        again = ask(gw, dev, model=OPUS, conversation_id=handed)
        assert again.status_code == 200, again.get_data(as_text=True)     # answered, not refused a second time
        b = block(again)
        assert b["substituted"] is True and "no room" in b["notice"] and b["conversation_id"] == handed
        assert desk.minted == made                          # no second chat
        assert "new chat" not in b["notice"].lower()        # and the lost chat is not announced twice


def test_a_conversation_id_this_pc_would_never_mint_never_touches_a_path(gw, desk, monkeypatch):
    seen = []
    real = desk.api
    monkeypatch.setattr(owner_turn, "_api", lambda m, p, b=None: (seen.append(p), real(m, p, b))[1])
    r = ask(gw, phone(), model="bonsai2:27b", conversation_id="../../etc/passwd")
    assert r.status_code == 400 and r.get_json()["code"] == "bad_request"           # refused before any path is built
    assert all("etc" not in p and ".." not in p for p in seen)
    assert owner_turn.valid_cid("conv-39be9e7c") and not owner_turn.valid_cid("a/b")


def test_a_turn_that_comes_back_empty_still_carries_the_binds_notice(gw, desk):
    dev = phone()
    desk.post = lambda text, cid: type("R", (), {"status_code": 200, "response": iter([
        b'data: {"done": true, "payload": {"response": ""}}\n\n'])})()
    owner_turn._post = desk.post
    ev = events(ask(gw, dev, model=OPUS, conversation_id=STALE))
    err = next(e for e in ev if e.get("type") == "error")
    assert "new chat" in err["friday"]["notice"].lower() and "Anthropic" in err["friday"]["notice"]


# ---- (4) a cloud pick says what leaves the PC, once ---------------------------------------------------------

def test_binding_a_cloud_model_from_the_phone_discloses_once_what_leaves_the_pc_and_for_whom(gw, desk):
    dev = phone()
    cloud_answer(desk)
    b = block(ask(gw, dev, model=OPUS, conversation_id="conv-old"))
    n = b["notice"]
    assert "Anthropic" in n and "Claude Opus 5.5" in n and "leave" in n.lower() and "PC" in n
    again = block(ask(gw, dev, model=OPUS, conversation_id="conv-old"))
    assert "notice" not in again


def test_binding_a_local_model_discloses_nothing(gw, desk):
    b = block(ask(gw, phone(), model="bonsai2:27b", conversation_id="conv-old"))
    assert "notice" not in b


# ---- (5) no JSON null in the block -------------------------------------------------------------------------

def test_the_friday_block_carries_no_json_null_for_a_phone_to_print_as_the_word_null(gw, desk):
    dev = phone()
    for extra in ({}, {"model": "bonsai2:27b", "conversation_id": "conv-old"}):
        r = ask(gw, dev, **extra)                        # each stream read before the next request opens
        d = next(e for e in events(r) if e.get("type") == "message_delta")
        nulls = [k for k, v in d["friday"].items() if v is None]
        assert nulls == [], json.dumps(d["friday"])
