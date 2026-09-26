"""An approved card runs the action it describes, exactly once.

Stephen, 2026-09-25, after an itinerary that never reached his calendar: five
`create_calendar_event` cards and two `write_file` cards were all approved by the
owner, and not one of them ran. Every card in that conversation ended as
`status: approved, consumed: false`.

WHY IT HAPPENED. `action_gate.authorize` has two gated verdicts and only one of
them executes:

  * `confirm` is BLOCKING. Outward, interactive, nothing from outside content:
    the chat gate asks yes/no inside the turn, the user answers, and the SAME
    call proceeds. This is the path the 5.14.1 clean-install walkthrough's
    governed write took, which is why that one worked.
  * `card` is DEFERRED. The tool call is denied with "[APPROVAL CARD RAISED]",
    the card is stored, and the design assumes the model will make the identical
    call again later so `_taint_card` can find the approved card and let it
    through. Nothing ever makes that second call -- the deny text explicitly
    tells the model "Do NOT call it again this turn" -- so the approval sat
    unconsumed and the action never happened.

So an approved card needs an EXECUTOR. These tests pin that: approving runs the
action once, marks the card consumed, reports the real outcome, and posts it back
into the conversation that asked -- including when the approval arrives long
after the turn has ended. And approving twice, from any number of surfaces, runs
it exactly once.
"""

import json
import threading

import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    """An approvals store and conversation root isolated to this test."""
    from agent_friday.services import approvals as ap
    from agent_friday.services import conversations as convs
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(ap, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(convs, "_root", lambda: tmp_path / "conversations",
                        raising=False)
    return ap


@pytest.fixture
def ran(monkeypatch):
    """Capture every tool execution the executor performs."""
    calls = []
    from agent_friday.services import approval_executor as ex

    def fake_exec(name, args, **kw):
        calls.append((name, args))
        return "created: ok"

    monkeypatch.setattr(ex, "_run_tool", fake_exec)
    return calls


def _card(ap, *, tool="create_calendar_event", inp=None, cid=None):
    return ap.create_approval(
        kind="tainted_action", subject_type="tool_action",
        subject_id="taint:test:%s" % (tool,),
        title="Create calendar event",
        action_description="%s %s" % (tool, json.dumps(inp or {})),
        description="a detail came from something Friday read",
        force_gate=True,
        payload={"tool": tool, "input": inp or {"title": "Dinner"},
                 **({"conversation_id": cid} if cid else {})},
        requested_by="taint_gate")


# ── the headline ───────────────────────────────────────────────────────────

def test_approving_a_card_runs_the_action(store, ran):
    """The failure Stephen hit: approve, and nothing happens."""
    from agent_friday.services import approval_executor as ex
    ex.register()
    rec = _card(store)
    store.decide(rec["approval_id"], "approve", decided_by="owner")
    assert len(ran) == 1, (
        "the approved action did not run (this is the reported bug): %r" % (ran,))
    assert ran[0][0] == "create_calendar_event"


def test_the_card_is_consumed_after_it_runs(store, ran):
    from agent_friday.services import approval_executor as ex
    ex.register()
    rec = _card(store)
    store.decide(rec["approval_id"], "approve", decided_by="owner")
    after = store.get_approval(rec["approval_id"])
    assert after.get("consumed") is True, (
        "approved but not consumed -- exactly the state every card in the "
        "reported conversation was left in")


def test_approving_twice_runs_once(store, ran):
    from agent_friday.services import approval_executor as ex
    ex.register()
    rec = _card(store)
    store.decide(rec["approval_id"], "approve", decided_by="owner")
    store.decide(rec["approval_id"], "approve", decided_by="owner")
    assert len(ran) == 1, "a second approval ran the action again: %r" % (ran,)


def test_two_surfaces_approving_at_once_run_once(store, ran):
    """Cross-tab: the Workflows session's approval popups mean two tabs can
    approve the same card in the same instant. One decision, one action."""
    from agent_friday.services import approval_executor as ex
    ex.register()
    rec = _card(store)
    start = threading.Barrier(4)

    def approve():
        start.wait()
        try:
            store.decide(rec["approval_id"], "approve", decided_by="owner")
        except Exception:
            pass

    ts = [threading.Thread(target=approve) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(ran) == 1, (
        "four simultaneous approvals ran the action %d times" % len(ran))


def test_a_denied_card_never_runs(store, ran):
    from agent_friday.services import approval_executor as ex
    ex.register()
    rec = _card(store)
    store.decide(rec["approval_id"], "deny", decided_by="owner")
    assert ran == [], "a declined action ran anyway"


def test_an_approval_after_the_turn_has_moved_on_still_runs(store, ran):
    """The reported case: the owner approved in System -> Approvals minutes
    later, with no turn in flight. There is nothing to resume, so the executor
    is the only thing that can act."""
    from agent_friday.services import approval_executor as ex
    ex.register()
    rec = _card(store)
    # no session, no turn, nothing in flight
    store.decide(rec["approval_id"], "approve", decided_by="owner",
                 note="approved from the System workspace")
    assert len(ran) == 1


# ── the result has to come back ────────────────────────────────────────────

def test_the_result_is_posted_into_the_originating_conversation(store, ran):
    from agent_friday.services import approval_executor as ex
    from agent_friday.services import conversations as convs
    ex.register()
    conv = convs.create(title="Weekend itinerary")
    cid = conv["id"] if isinstance(conv, dict) else conv
    rec = _card(store, cid=cid)
    store.decide(rec["approval_id"], "approve", decided_by="owner")
    msgs = convs.messages(cid)
    assert msgs, "nothing was posted back into the conversation"
    text = " ".join((m.get("text") or "") for m in msgs)
    assert "create_calendar_event" in text or "Dinner" in text, text[:200]


def test_a_failure_is_reported_not_swallowed(store, monkeypatch):
    """The executor must report the REAL outcome, so a card that was approved
    and then failed does not read as done."""
    from agent_friday.services import approval_executor as ex
    from agent_friday.services import conversations as convs
    ex.register()

    def boom(name, args, **kw):
        raise RuntimeError("calendar refused it")

    monkeypatch.setattr(ex, "_run_tool", boom)
    conv = convs.create(title="Weekend itinerary")
    cid = conv["id"] if isinstance(conv, dict) else conv
    rec = _card(store, cid=cid)
    store.decide(rec["approval_id"], "approve", decided_by="owner")
    after = store.get_approval(rec["approval_id"])
    detail = json.dumps(after.get("used_detail") or {})
    assert "calendar refused it" in detail or after.get("consumed") is True
    text = " ".join((m.get("text") or "") for m in convs.messages(cid))
    assert "refused" in text.lower() or "did not" in text.lower(), text[:200]


# ── the blocking path must keep working ────────────────────────────────────

def test_the_blocking_confirm_path_is_untouched():
    """`confirm` executes inside the turn and must not be turned into a card.

    This is the distinction that explains why the 5.14.1 walkthrough's governed
    write worked while the itinerary's did not, and it is the half that already
    works.
    """
    from agent_friday.governance import action_gate as gate
    v = gate.authorize("write_file", {"path": "notes.md", "content": "x"},
                       {"session_id": "s1", "authenticated": True},
                       tainted=False)
    assert v.action in ("allow", "confirm"), (
        "an interactive, untainted outward call should be asked in chat, not "
        "deferred to a card: %r" % (v,))


def test_a_tainted_call_still_raises_a_card():
    from agent_friday.governance import action_gate as gate
    v = gate.authorize("create_calendar_event", {"title": "x"},
                       {"session_id": "s1", "authenticated": True},
                       tainted=True)
    assert v.action == "card", v


# ── the bypass itself, through the real checkpoint ─────────────────────────

def _approve_quietly(ap, aid):
    """Approve without firing the decision hook.

    These tests exercise the checkpoint's card check in isolation. Going through
    `decide` would run the executor, which consumes the card -- correct
    behaviour, and it would leave nothing here to test.
    """
    import time as _t
    return ap._patch(aid, status="approved", decided_at=_t.time(),
                     decided_by="owner")


def test_the_card_keyed_bypass_allows_exactly_the_approved_call(store):
    """The executor reaches the tool through the ordinary choke point, so the
    governance hook has to let an approved card's own call through -- once."""
    from agent_friday.services import agent as ag
    rec = _card(store, inp={"title": "Dinner", "start": "2026-09-26T17:30"})
    aid = rec["approval_id"]
    _approve_quietly(store, aid)
    ok, why = ag._approved_card_allows(aid, "create_calendar_event",
                                       {"title": "Dinner",
                                        "start": "2026-09-26T17:30"})
    assert ok, why
    assert store.get_approval(aid).get("consumed") is True
    # ...and never twice
    ok2, why2 = ag._approved_card_allows(aid, "create_calendar_event",
                                         {"title": "Dinner",
                                          "start": "2026-09-26T17:30"})
    assert not ok2 and "already been used" in why2, why2


def test_the_bypass_refuses_a_different_tool_or_different_details(store):
    """A card authorises what it SAYS. Anything else is a different action."""
    from agent_friday.services import agent as ag
    rec = _card(store, inp={"title": "Dinner"})
    aid = rec["approval_id"]
    _approve_quietly(store, aid)
    ok, why = ag._approved_card_allows(aid, "send_email", {"title": "Dinner"})
    assert not ok and "authorises" in why, why
    ok, why = ag._approved_card_allows(aid, "create_calendar_event",
                                       {"title": "Something else entirely"})
    assert not ok and "differ" in why, why


def test_the_bypass_refuses_an_unapproved_card(store):
    from agent_friday.services import agent as ag
    rec = _card(store, inp={"title": "Dinner"})          # still pending
    ok, why = ag._approved_card_allows(rec["approval_id"],
                                       "create_calendar_event",
                                       {"title": "Dinner"})
    assert not ok and "not approved" in why, why


def test_argument_key_order_does_not_defeat_the_match(store):
    from agent_friday.services import agent as ag
    rec = _card(store, inp={"a": 1, "b": 2})
    _approve_quietly(store, rec["approval_id"])
    ok, why = ag._approved_card_allows(rec["approval_id"],
                                       "create_calendar_event", {"b": 2, "a": 1})
    assert ok, why


# ── the owner has to SEE that it happened ──────────────────────────────────

def test_the_result_is_pushed_to_open_chat_pages(store, ran, monkeypatch):
    """Appending to the store is the record; the push is what makes it visible.

    The chat window fetches a transcript when it opens or switches and never
    polls, so an approval decided minutes later would otherwise need a reload
    before the owner could tell whether his approved action had happened.
    """
    from agent_friday.services import approval_executor as ex
    from agent_friday.services import conversations as convs
    from agent_friday.services import desktop_bus as bus
    ex.register()
    conv = convs.create(title="Weekend itinerary")
    cid = conv["id"] if isinstance(conv, dict) else conv

    q = bus.subscribe("chat-testpage", "chat")
    try:
        rec = _card(store, cid=cid)
        store.decide(rec["approval_id"], "approve", decided_by="owner")
        events = []
        while not q.empty():
            events.append(q.get_nowait())
    finally:
        bus.unsubscribe("chat-testpage", q)

    kinds = [e.get("type") for e in events]
    assert "approval_result" in kinds, kinds
    evt = next(e for e in events if e.get("type") == "approval_result")
    assert evt.get("conversation_id") == cid


def test_the_push_carries_no_content(store, ran):
    """The event says WHICH conversation moved, never what was written: the
    transcript is re-read from the store, and an SSE frame is not the place to
    put whatever the tool touched."""
    from agent_friday.services import approval_executor as ex
    from agent_friday.services import conversations as convs
    from agent_friday.services import desktop_bus as bus
    ex.register()
    conv = convs.create(title="x")
    cid = conv["id"] if isinstance(conv, dict) else conv
    q = bus.subscribe("chat-testpage2", "chat")
    try:
        rec = _card(store, inp={"title": "Dinner at a named place"}, cid=cid)
        store.decide(rec["approval_id"], "approve", decided_by="owner")
        evts = []
        while not q.empty():
            evts.append(q.get_nowait())
    finally:
        bus.unsubscribe("chat-testpage2", q)
    blob = json.dumps(evts)
    assert "named place" not in blob, blob[:200]


def test_a_broadcast_reaches_every_chat_page_and_skips_other_kinds():
    from agent_friday.services import desktop_bus as bus
    a = bus.subscribe("chatA", "chat")
    b = bus.subscribe("chatB", "chat")
    d = bus.subscribe("deskC", "desktop")
    try:
        n = bus.broadcast({"type": "approval_result", "conversation_id": "c1"},
                          kind="chat")
        assert n == 2, n
        assert not a.empty() and not b.empty()
        assert d.empty(), "a desktop page got a chat broadcast"
    finally:
        bus.unsubscribe("chatA", a)
        bus.unsubscribe("chatB", b)
        bus.unsubscribe("deskC", d)


def test_a_full_queue_does_not_break_the_run(monkeypatch):
    """The action has already happened when this is called. Losing the
    notification must not raise back into the executor."""
    import queue as _q
    from agent_friday.services import desktop_bus as bus
    full = bus.subscribe("chatFull", "chat")
    try:
        while True:
            full.put_nowait({"filler": 1})
    except _q.Full:
        pass
    try:
        assert bus.broadcast({"type": "approval_result"}, kind="chat") == 0
    finally:
        bus.unsubscribe("chatFull", full)
