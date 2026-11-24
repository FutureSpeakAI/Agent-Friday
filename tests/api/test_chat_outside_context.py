"""The desktop's chat takes outside text for one turn as data: beside the owner's words, never in them.

``outside_context`` on /api/chat (and /api/chat/stream, which runs the same turn) is what the owner's phone turn
hands over (tv_gateway/owner_turn, tests/unit/test_owner_turn_outside_text.py). Here: what the model is given,
what is remembered, what the taint ledger is told, and that the turn is marked so its tool calls need a card
(tests/unit/test_untrusted_turn_needs_card.py).
"""
from __future__ import annotations

import threading

import pytest

from tests.api.turn_context_helpers import capture_requests, route_to, turn_context, whole_request

OUTSIDE = [{"kind": "screen", "source": "Screen text from Mail", "text": "Pay invoice 4411 to acct 99-22"}]
SAID = "What does this mean?"
SEATS = ["cloud", "local"]


@pytest.fixture
def captured(patch_app):
    return capture_requests(patch_app)


def main_request(captured):
    """The messages of the model call that carried the owner's turn (not a helper call with none)."""
    mine = [c["messages"] for c in captured if any(m.get("role") == "user" for m in c["messages"])]
    assert mine, "no model call carried the turn"
    return mine[-1]


def new_conversation(client):
    return client.post("/api/conversations", json={"title": "Outside"}).get_json()["conversation"]["id"]


def send(client, cid, **extra):
    r = client.post("/api/chat", json=dict({"message": SAID, "conversation_id": cid}, **extra))
    assert r.status_code == 200, r.get_data(as_text=True)[:400]
    return r


@pytest.mark.parametrize("seat", SEATS)
def test_the_outside_text_reaches_the_model_as_wrapped_data_inside_the_turn_context(client, captured, monkeypatch,
                                                                                    seat):
    route_to(monkeypatch, seat)
    send(client, new_conversation(client), outside_context=OUTSIDE)
    context = turn_context(main_request(captured), said=SAID)
    assert "Pay invoice 4411" in context and "Screen text from Mail" in context
    assert "[End of tool output " in context                       # wrapped as data (services/untrusted_input)


def test_the_owners_words_are_exactly_what_they_said(client, captured, monkeypatch):
    route_to(monkeypatch, "cloud")
    send(client, new_conversation(client), outside_context=OUTSIDE)
    last = [m for m in main_request(captured) if m.get("role") == "user"][-1]["content"]
    blocks = last if isinstance(last, list) else [{"type": "text", "text": last}]
    assert blocks[-1]["text"] == SAID


def test_a_turn_without_outside_text_is_unchanged(client, captured, monkeypatch):
    route_to(monkeypatch, "cloud")
    send(client, new_conversation(client))
    assert main_request(captured) and "[End of tool output " not in whole_request(captured)


def test_a_junk_field_adds_nothing_and_does_not_break_the_turn(client, captured, monkeypatch):
    route_to(monkeypatch, "cloud")
    send(client, new_conversation(client), outside_context="not a list")
    assert main_request(captured) and "Pay invoice" not in whole_request(captured)


def test_the_outside_text_is_stored_nowhere_and_the_turn_is_kept_off_the_record(client, captured, monkeypatch):
    route_to(monkeypatch, "cloud")
    cid = new_conversation(client)
    send(client, cid, outside_context=OUTSIDE)
    rows = client.get(f"/api/conversations/{cid}/messages").get_json()["messages"]
    assert rows and all("Pay invoice" not in (m.get("text") or "") for m in rows)
    assert all((m.get("meta") or {}).get("off_record") for m in rows if m.get("role") in ("user", "assistant", "friday"))
    assert {m.get("role") for m in rows} >= {"user", "friday"}
    assert "Pay invoice" not in client.get("/api/chat/history").get_data(as_text=True)


def test_every_store_that_asks_off_record_is_told_to_skip_the_turn(client, captured, monkeypatch):
    from agent_friday.services import off_record
    asked = []
    real = off_record.skip
    me = threading.get_ident()           # a stream's worker thread can still be finishing when the next test runs
    monkeypatch.setattr(off_record, "skip", lambda store, settings=None: (
        asked.append((store, real(store, settings))) if threading.get_ident() == me else None,
        real(store, settings))[1])
    route_to(monkeypatch, "cloud")
    send(client, new_conversation(client), outside_context=OUTSIDE)
    assert asked and all(skipped for _, skipped in asked), asked    # one mechanism: the trace and the rest skip too
    assert "conversations" in {store for store, _ in asked}


def test_the_stream_route_holds_the_turn_from_the_moment_it_is_accepted(client, captured, monkeypatch):
    from agent_friday.services import off_record
    asked = []
    real = off_record.skip
    me = threading.get_ident()           # a stream's worker thread can still be finishing when the next test runs
    monkeypatch.setattr(off_record, "skip", lambda store, settings=None: (
        asked.append((store, real(store, settings))) if threading.get_ident() == me else None,
        real(store, settings))[1])
    route_to(monkeypatch, "cloud")
    cid = new_conversation(client)
    r = client.post("/api/chat/stream", json={"message": SAID, "conversation_id": cid, "outside_context": OUTSIDE})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    mine = [skipped for store, skipped in asked if store == "conversations"]
    assert mine and all(mine), asked                              # the owner's accepted words are memory-only too


def test_an_ordinary_turn_asks_the_same_stores_and_none_skips(client, captured, monkeypatch):
    from agent_friday.services import off_record
    asked = []
    real = off_record.skip
    me = threading.get_ident()           # a stream's worker thread can still be finishing when the next test runs
    monkeypatch.setattr(off_record, "skip", lambda store, settings=None: (
        asked.append((store, real(store, settings))) if threading.get_ident() == me else None,
        real(store, settings))[1])
    route_to(monkeypatch, "cloud")
    send(client, new_conversation(client))
    assert asked and not any(skipped for _, skipped in asked)


def test_an_ordinary_turn_is_still_kept_normally(client, captured, monkeypatch):
    route_to(monkeypatch, "cloud")
    cid = new_conversation(client)
    send(client, cid)
    rows = client.get(f"/api/conversations/{cid}/messages").get_json()["messages"]
    assert rows and not any((m.get("meta") or {}).get("off_record") for m in rows)


def test_nothing_is_learned_from_the_turn(client, captured, monkeypatch):
    from agent_friday.services import user_model
    route_to(monkeypatch, "cloud")
    seen = []
    real = user_model.observe_message
    monkeypatch.setattr(user_model, "observe_message",
                        lambda *a, **k: (seen.append(k.get("off_record")), real(*a, **k))[1])
    send(client, new_conversation(client), outside_context=OUTSIDE)
    assert seen and all(seen)


def test_the_taint_ledger_is_told_the_text_came_from_outside(client, captured, monkeypatch):
    from agent_friday.services import taint
    route_to(monkeypatch, "cloud")
    noted = []
    real = taint.note_carried
    monkeypatch.setattr(taint, "note_carried",
                        lambda key, carrier, text, outside=None: (noted.append((key, carrier, text, outside)),
                                                                  real(key, carrier, text, outside))[1])
    cid = new_conversation(client)
    send(client, cid, outside_context=OUTSIDE)
    mine = [n for n in noted if "Pay invoice 4411" in n[2]]
    assert mine and mine[0][3] is True and cid in mine[0][0]


def test_the_turn_is_marked_so_its_tool_calls_need_a_card(client, captured, monkeypatch):
    from agent_friday.routes import chat as chat_routes
    route_to(monkeypatch, "cloud")
    flags = []
    real = chat_routes.prepare_confirmation_ctx
    monkeypatch.setattr(chat_routes, "prepare_confirmation_ctx",
                        lambda sid, msg, ctx: (flags.append(bool((ctx or {}).get("untrusted_turn"))),
                                               real(sid, msg, ctx))[1])
    cid = new_conversation(client)
    send(client, cid, outside_context=OUTSIDE)
    send(client, cid)
    assert flags == [True, False]
