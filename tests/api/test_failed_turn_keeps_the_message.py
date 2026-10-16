"""A turn that fails keeps the owner's message.

The user's message is stored when the turn is accepted, before any model runs
(meta.status "received"). A completed turn completes that same row
("answered"); a turn that fails leaves it in the conversation as "unanswered",
and the next turn's model context carries it. A resend with the same client id
reuses the row instead of writing a second one.

An explicit request to note something in the wiki stays open across turns
until a turn records a successful wiki write: the next turn's context names
it ("Unfinished request from the owner: ...").

The model here is a fake `_call_claude_agent` that records what it was given
and raises or answers on demand; the conversation store is the real one.
"""
from __future__ import annotations

import copy
import json

import pytest

from agent_friday import core
from agent_friday.services.prompt_cache import VOLATILE_MARKER

FACTS = ("The spare key for the shed is under the blue flowerpot by Riverside Park gate. "
         "Make a note of this in my wiki.")
FOLLOW_UP = "Did you get all that?"
UNFINISHED = "Unfinished request from the owner"


def _fake_context(message, *a, **k):
    return ("== PERSONA ==\nI am Friday.\n" + VOLATILE_MARKER + "\nNow: 12:00\n", [])


class FakeModel:
    """Stands in for the cloud agent call: records each call, then raises or answers."""

    def __init__(self):
        self.calls = []
        self.plan = []
        self.hook = None

    def __call__(self, messages, system=None, **kwargs):
        self.calls.append({"messages": copy.deepcopy(messages), "system": system})
        if self.hook:
            self.hook()
        step = self.plan.pop(0) if self.plan else ("Okay.", [])
        if isinstance(step, BaseException):
            raise step
        return step

    def seen(self, i=-1):
        c = self.calls[i]
        return json.dumps(c["messages"], ensure_ascii=False) + "\n" + str(c["system"] or "")


@pytest.fixture
def model(monkeypatch, patch_app):
    settings = copy.deepcopy(core.DEFAULT_SETTINGS)
    settings["model_routing"] = dict(settings.get("model_routing") or {})
    settings["model_routing"].update({"mode": "cloud_only", "vault_local_only": False})
    settings["off_record"] = False
    patch_app("_load_settings", lambda *a, **k: settings)
    patch_app("_build_memory_context_block", lambda *a, **k: "")
    patch_app("_build_session_continuity_block", lambda *a, **k: "")
    patch_app("_build_emotional_tone_block", lambda *a, **k: "")
    patch_app("_build_context_prompt", _fake_context)
    patch_app("_index_chat_turn", lambda *a, **k: None)
    import agent_friday.services.demo_mode as dm
    monkeypatch.setattr(dm, "is_demo", lambda *a, **k: False)
    fake = FakeModel()
    patch_app("_call_claude_agent", fake)
    return fake


@pytest.fixture
def cid():
    from agent_friday.services import conversations as conv
    return conv.create()["id"]


def _rows(cid):
    from agent_friday.services import conversations as conv
    return [m for m in conv.messages(cid) if m.get("role") in ("user", "friday")]


def _users(cid, text):
    return [m for m in _rows(cid) if m["role"] == "user" and m.get("text") == text]


def _post(client, cid, text, turn_id):
    r = client.post("/api/chat", json={"message": text, "conversation_id": cid,
                                       "turn_id": turn_id})
    assert r.status_code == 200, r.get_data(as_text=True)[:400]
    return r.get_json()


def _stream(client, cid, text, turn_id):
    r = client.post("/api/chat/stream", json={"message": text, "conversation_id": cid,
                                              "turn_id": turn_id})
    assert r.status_code == 200
    frames = [json.loads(b[len("data: "):]) for b in r.get_data(as_text=True).split("\n\n")
              if b.startswith("data: ")]
    return frames


# ── (a) the message survives a failed turn ──────────────────────────────────

def test_a_failed_turn_leaves_the_message_in_the_conversation(client, model, cid):
    model.plan = [ConnectionError("connection reset mid-turn")]
    d = _post(client, cid, FACTS, "turn-a1")
    assert len(model.calls) == 1, "the model was never reached"
    assert str(d.get("response") or "").startswith("[Friday offline]")
    kept = _users(cid, FACTS)
    assert len(kept) == 1, _rows(cid)
    assert kept[0]["meta"].get("status") == "unanswered"


def test_a_failed_streamed_turn_leaves_the_message_and_says_it_was_accepted(
        client, model, cid):
    model.plan = [ConnectionError("connection reset mid-turn")]
    frames = _stream(client, cid, FACTS, "turn-s1")
    started = [f for f in frames if f.get("started")]
    assert started and started[0].get("accepted") is True
    kept = _users(cid, FACTS)
    assert len(kept) == 1 and kept[0]["meta"].get("status") == "unanswered"


def test_the_message_is_stored_before_the_model_runs(client, model, cid):
    seen = {}
    model.hook = lambda: seen.setdefault("rows", copy.deepcopy(_users(cid, FACTS)))
    _post(client, cid, FACTS, "turn-b0")
    assert seen["rows"] and seen["rows"][0]["meta"].get("status") == "received"
    rows = _users(cid, FACTS)
    assert len(rows) == 1 and rows[0]["meta"].get("status") == "answered"
    assert [m["role"] for m in _rows(cid)] == ["user", "friday"]


# ── (b) the next turn's model sees it ───────────────────────────────────────

def test_the_next_turn_carries_the_unanswered_message(client, model, cid):
    model.plan = [ConnectionError("connection reset mid-turn"), ("Yes.", [])]
    _post(client, cid, FACTS, "turn-b1")
    _post(client, cid, FOLLOW_UP, "turn-b2")
    assert len(model.calls) == 2
    assert "spare key for the shed" in model.seen(-1), (
        "the next turn's model context does not contain the lost message")
    roles = [m["role"] for m in model.calls[-1]["messages"]]
    assert all(a != b for a, b in zip(roles, roles[1:])), roles
    assert len(_users(cid, FACTS)) == 1 and len(_users(cid, FOLLOW_UP)) == 1


# ── (c) a resend with the same client id is not a second message ────────────

def test_a_resend_with_the_same_client_id_reuses_the_row(client, model, cid):
    model.plan = [ConnectionError("connection reset mid-turn"), ("Got it.", [])]
    _post(client, cid, FACTS, "turn-c1")
    _post(client, cid, FACTS, "turn-c1")
    kept = _users(cid, FACTS)
    assert len(kept) == 1, [m.get("id") for m in kept]
    assert kept[0]["meta"].get("status") == "answered"
    # The model saw the message once: the history leaves out the running row.
    assert model.seen(-1).count("spare key for the shed") == 1
    rows = _rows(cid)
    assert [m["role"] for m in rows] == ["user", "friday"]


def test_a_streamed_resend_with_the_same_client_id_reuses_the_row(client, model, cid):
    model.plan = [ConnectionError("connection reset mid-turn"), ("Got it.", [])]
    _stream(client, cid, FACTS, "turn-c2")
    _stream(client, cid, FACTS, "turn-c2")
    kept = _users(cid, FACTS)
    assert len(kept) == 1 and kept[0]["meta"].get("status") == "answered"


# ── (d) an explicit wiki request is not dropped ─────────────────────────────

WIKI_OK = [{"name": "propose_wiki_update", "input": {"file": "home.md", "new_value": "x"},
            "result": "Wiki update proposed (id=p1) -- awaiting your approval in Knowledge."}]


def test_an_unfinished_wiki_request_is_named_until_a_write_succeeds(client, model, cid):
    model.plan = [ConnectionError("connection reset mid-turn"),
                  ("Okay.", []),          # answered, but nothing written
                  ("Okay.", WIKI_OK),     # the write
                  ("Okay.", [])]
    _post(client, cid, FACTS, "turn-d1")
    _post(client, cid, FOLLOW_UP, "turn-d2")
    assert UNFINISHED in model.seen(-1)
    assert "the wiki note was not saved yet" in model.seen(-1)
    _post(client, cid, "And the gate code is the usual one.", "turn-d3")
    assert UNFINISHED in model.seen(-1), "a reply without a write closed the request"
    _post(client, cid, "Thanks.", "turn-d4")
    assert UNFINISHED not in model.seen(-1), "a successful write did not close the request"


def test_a_failed_wiki_write_keeps_the_request_open(client, model, cid):
    bad = [{"name": "propose_wiki_update", "input": {},
            "result": "propose_wiki_update error: 'file' and 'new_value' are required."}]
    model.plan = [("Okay.", bad), ("Okay.", [])]
    _post(client, cid, FACTS, "turn-e1")
    _post(client, cid, FOLLOW_UP, "turn-e2")
    assert UNFINISHED in model.seen(-1)


def test_the_owner_can_drop_the_request(client, model, cid):
    model.plan = [ConnectionError("down"), ("Okay.", []), ("Okay.", []), ("Okay.", [])]
    _post(client, cid, FACTS, "turn-f1")
    _post(client, cid, FOLLOW_UP, "turn-f2")
    assert UNFINISHED in model.seen(-1)
    _post(client, cid, "Never mind, forget it.", "turn-f3")
    _post(client, cid, FOLLOW_UP, "turn-f4")
    assert UNFINISHED not in model.seen(-1)
