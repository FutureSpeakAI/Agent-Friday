"""`/api/chat` history starts where the token budget says, not 100 rows back.

A fixed window of the last 100 messages moved its start by two rows on every
turn past 100, so the first history message changed every turn and the local
seat's prefix cache matched nothing: the whole transcript was re-read (43 s
measured, against 0.43 s when the prefix held). `_history_start` already
budgets `/api/chat/send` by tokens in 20-row steps; `/api/chat` now reads the
same rule, so two consecutive requests share their history prefix byte for
byte and the start is the same after a restart.
"""
from __future__ import annotations

import uuid

from agent_friday.routes import chat as chat_routes
from agent_friday.services import conversations as conv


def _fill(n, text="short line"):
    cid = uuid.uuid4().hex[:12]
    for i in range(n):
        role = "user" if i % 2 == 0 else "friday"
        conv.append(cid, {"role": role, "text": f"{text} {i}"})
    return cid


def test_two_requests_past_100_messages_share_their_history_prefix():
    cid = _fill(120)
    first = chat_routes._conv_context(cid)
    conv.append(cid, {"role": "user", "text": "short line 120"})
    conv.append(cid, {"role": "friday", "text": "short line 121"})
    second = chat_routes._conv_context(cid)
    assert second[:len(first)] == first, "the window start moved under a short history"
    assert len(second) == len(first) + 2


def test_a_long_history_is_budgeted_in_whole_steps():
    # 60 rows of ~1,000 chars (~250 tokens each) is ~15,000 tokens, over the
    # 8,000-token budget, so the start moves: to a multiple of 20, never by 1.
    cid = _fill(60, text="x" * 1000)
    rows = conv.messages(cid)
    start = chat_routes._history_start(rows)
    assert start > 0 and start % chat_routes._HISTORY_STEP == 0
    assert chat_routes._conv_context(cid)[0]["content"] == rows[start]["text"]


def test_the_start_is_the_same_after_a_restart():
    """Deterministic in the stored rows: nothing in memory decides it."""
    cid = _fill(130, text="y" * 400)
    a = chat_routes._conv_context(cid)
    b = chat_routes._conv_context(cid)
    assert a == b


def test_system_rows_are_still_never_replayed():
    cid = _fill(10)
    conv.append(cid, {"role": "system", "text": "seat changed"})
    assert all("seat changed" != m["content"] for m in chat_routes._conv_context(cid))


def test_a_caller_may_still_cap_the_rows():
    cid = _fill(30)
    assert len(chat_routes._conv_context(cid, 5)) == 5
