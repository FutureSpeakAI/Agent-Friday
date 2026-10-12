"""The learning hooks and the rule that a learned phrasing never reaches the screen rungs.

State-only tests: no vault, no encoder, no tools/tool_head.
"""
import pytest

from agent_friday.services import laya_personal as lp
from agent_friday.services import laya_router as lr
from agent_friday.services import laya_router_learn as ll


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    ll.reset()
    monkeypatch.setattr(ll, "_settings", lambda: {})
    yield
    ll.reset()


@pytest.fixture
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(lp, "on_executed", lambda *a, **k: seen.append(("executed", a, k)))
    monkeypatch.setattr(lp, "on_undo", lambda *a, **k: seen.append(("undo", a, k)))
    return seen


# ── the router's hooks tell the personal layer ───────────────────────────────────

def test_an_executed_tool_is_passed_to_the_personal_layer_with_its_conversation(calls):
    ll.note_executed("search_email", {"query": "x"}, ok=True, conversation_id="c1")
    assert len(calls) == 1 and calls[0][0] == "executed"
    assert calls[0][1][0] == "search_email" and calls[0][2]["conversation_id"] == "c1" and calls[0][2]["ok"] is True
    # red when: note_executed does not call laya_personal.on_executed


def test_a_failure_in_the_personal_layer_is_never_the_callers(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(lp, "on_executed", boom)
    assert ll.note_executed("search_email", {}, ok=True, conversation_id="c1") is False


def test_an_undo_is_passed_on_with_its_taint_and_a_tainted_one_is_not(calls):
    ll.note_undo(conversation_id="c1", tainted=lambda: False)
    assert [c[0] for c in calls] == ["undo"] and calls[0][2]["conversation_id"] == "c1"
    calls.clear()
    ll.note_undo(conversation_id="c1", tainted=True)
    ll.note_undo(conversation_id="c1", tainted=lambda: True)
    ll.note_undo(conversation_id="c1", tainted=lambda: 1 / 0)
    assert calls == []
    # red when: an undo that came from a turn which read untrusted content reaches the personal layer


def test_the_personal_undo_itself_refuses_a_tainted_turn(monkeypatch):
    monkeypatch.setattr(lp, "current_profile", lambda: "owner")
    monkeypatch.setattr(lp, "_active", lambda p: True)
    kept = []
    monkeypatch.setattr(lp, "_capture", lambda *a, **k: kept.append(a))
    now = 1000.0
    monkeypatch.setattr(lp, "now_fn", lambda: now)
    with lp._lock:
        lp._held.clear()
        for _ in range(3):
            lp._held.append({"at": now - 5, "text": "t", "decision": "tool", "tool": "check_email", "confidence": 0.9,
                             "layer": "rule", "cands": [], "surface": "voice", "conversation_id": "c1", "resolved": False})
    lp.on_undo(conversation_id=None)
    lp.on_undo(conversation_id="c1", tainted=True)
    lp.on_undo(conversation_id="c1", tainted=lambda: True)
    assert kept == []
    lp.on_undo(conversation_id="c1", tainted=lambda: False)
    assert len(kept) == 1
    with lp._lock:
        lp._held.clear()


# ── a turn the screen decided is never held for learning ─────────────────────────

@pytest.mark.parametrize("layer,held", [("point_act", 0), ("resolver", 0), ("rule", 1), ("laya2", 1)])
def test_note_routed_holds_a_turn_unless_the_screen_decided_it(monkeypatch, layer, held):
    monkeypatch.setattr(lp, "current_profile", lambda: "owner")
    monkeypatch.setattr(lp, "_active", lambda p: True)
    monkeypatch.setattr(lp, "_room_mode", lambda s: False)
    monkeypatch.setattr(lp, "_kick_first_notice", lambda p: None)
    monkeypatch.setattr(lp, "top_sensitivity", lambda t: None)
    with lp._lock:
        lp._held.clear()
    lp.note_routed("archive these two", lr.Route("defer", layer=layer, confidence=0.9), surface="voice",
                   conversation_id="c1")
    with lp._lock:
        n = len(lp._held)
        lp._held.clear()
    assert n == held
    # red when: a point-and-act or resolver turn is held, so a deeper-mind choice attaches to a screen-dependent phrasing


# ── a learned phrasing never reaches a rung ──────────────────────────────────────

def test_the_rungs_run_without_the_profile_and_decide_the_same_with_one(monkeypatch):
    seen = []

    def rung(text):
        seen.append((lr.PROFILE_CTX.get(), getattr(lr._PROFILE, "pid", None)))
        return lr.Route("tool", tool="navigate_to", args={"workspace": "messages"}, layer="point_act", confidence=0.92)

    def no_personal(*a, **k):
        raise AssertionError("a rung must never read the personal layer")
    for name in ("personal_protos", "act_threshold", "graduation_ok", "has_live"):
        monkeypatch.setattr(lp, name, no_personal)
    monkeypatch.setattr(lr, "point_act_route", lambda text, **k: rung(text))
    base = lr.route("the second one", trusted=True, stage=object(), pointed=None, log=False)
    tok = lr.PROFILE_CTX.set("owner")
    try:
        with_profile = lr.route("the second one", trusted=True, stage=object(), pointed=None, log=False)
    finally:
        lr.PROFILE_CTX.reset(tok)
    assert seen and all(s == (None, None) for s in seen)
    assert (base.decision, base.tool, base.layer) == (with_profile.decision, with_profile.tool, with_profile.layer)
    # red when: a rung runs on a thread that carries the profile, or reads a learned phrasing


def test_a_learned_defer_on_a_turn_the_base_would_ask_about_is_the_bases_ask(monkeypatch):
    """The graduation guard's second half (laya_router._no_new_action) holds on the merged tree: a personal
    defer where the shipped base says ask or no_tool is the base's decision."""
    base_ask = lr.Route("ask", confidence=0.5, layer="laya2", question="Which one?")
    monkeypatch.setattr(lr, "_prototypes", lambda: (["a"], [[1.0]]))
    monkeypatch.setattr(lr, "_rank", lambda vec, labels, protos: [("a", 1.0, 1.0), ("b", 0.0, 0.0)])
    monkeypatch.setattr(lr, "_decide", lambda ranked, text, thr=lr.ACT_THRESHOLD: base_ask)
    got = lr._no_new_action("open that", lr.Route("defer", layer="laya2", confidence=0.9), [1.0])
    assert got is base_ask
    assert lr._no_new_action("open that", lr.Route("defer", layer="laya2", confidence=0.9), None).decision == "ask"


# ── the execute hook through agent._execute_tool, with the real on_executed ─────────

import types


@pytest.fixture
def rig(monkeypatch):
    """The real laya_personal.on_executed / on_delegated behind the real agent._execute_tool; only the
    sealed store write (_capture) and the pre-hooks are replaced."""
    from agent_friday.services import agent
    kept, asked = [], []
    monkeypatch.setattr(lp, "current_profile", lambda: "owner")
    monkeypatch.setattr(lp, "_active", lambda p: True)
    monkeypatch.setattr(lp, "_capture", lambda profile, kind, ent, **k: kept.append((kind, ent["text"], k.get("label"))))
    monkeypatch.setattr(agent._hooks, "run_pre_hooks", lambda ctx: types.SimpleNamespace(action="allow", reason=""))
    from agent_friday.services import slice_head
    monkeypatch.setattr(slice_head, "is_tainted", lambda key: asked.append(key) or False)
    with lp._lock:
        lp._held.clear()
        lp._held.append({"at": lp.now_fn(), "text": "sort out the thing with the lease", "decision": "defer",
                         "tool": None, "confidence": 0.9, "layer": "rule", "cands": [], "surface": "voice",
                         "conversation_id": "c1", "resolved": False})
    yield types.SimpleNamespace(agent=agent, kept=kept, asked=asked)
    with lp._lock:
        lp._held.clear()


def _run(agent, ctx, name="query_calendar"):
    return agent._execute_tool(name, {}, session_ctx=ctx, handler=lambda inp: "ok")


def test_a_voice_tool_in_the_conversation_does_not_resolve_a_deferred_turn(rig):
    """The voice dispatcher sets the conversation outside _execute_tool; Laya's own routed tool on the next
    turn, or a cloud voice tool, must not become the deeper mind's choice."""
    from agent_friday.services import laya_router_learn as ll
    ll.note_delegated("t1", conversation_id="c1", request=LEASE)       # the turn really is waiting for task t1
    tok = rig.agent._CURRENT_CONVERSATION.set("c1")
    try:
        # each call its own turn, so the call verifier's repeat cache cannot answer one without running it
        _run(rig.agent, {"authenticated": True, "turn_id": "v1"})
        _run(rig.agent, {"authenticated": True, "conversation_id": "c1", "turn_id": "v2"})
        _run(rig.agent, {"authenticated": True, "conversation_id": "c1", "chosen_by": "laya_router:rule", "turn_id": "v3"})
        # Laya's own routed tool, even carrying the delegated task's id, is not the deeper mind's choice
        _run(rig.agent, {"authenticated": True, "conversation_id": "c1", "chosen_by": "laya_router:rule",
                         "is_background_task": True, "task_id": "t1", "turn_id": "v4"})
    finally:
        rig.agent._CURRENT_CONVERSATION.reset(tok)
    assert rig.kept == []
    # positive control: the delegated task's own call still makes the row
    _run(rig.agent, {"authenticated": True, "is_background_task": True, "task_id": "t1", "conversation_id": "c1",
                     "turn_id": "v5"})
    assert rig.kept == [("shadow", LEASE, "query_calendar")]
    # red on f943324a8: the first call is read with the outer conversation after the reset and makes a row


def test_only_the_delegated_tasks_own_call_makes_the_shadow_row_and_reads_its_own_taint(rig):
    from agent_friday.services import laya_router_learn as ll
    from agent_friday.services import taint
    ll.note_delegated("t1", conversation_id="c1", request=LEASE)
    other = {"authenticated": True, "is_background_task": True, "task_id": "t2", "conversation_id": "c1"}
    mine = {"authenticated": True, "is_background_task": True, "task_id": "t1", "conversation_id": "c1"}
    _run(rig.agent, other)
    assert rig.kept == []
    assert "search_contacts" not in lr.TOOLS
    _run(rig.agent, mine, name="search_contacts")        # a tool the router cannot choose does not label it
    assert rig.kept == []
    _run(rig.agent, mine)
    assert rig.kept == [("shadow", "sort out the thing with the lease", "query_calendar")]
    assert taint.ledger_key(mine) in rig.asked and "default" not in rig.asked


def test_a_tainted_task_call_and_a_crew_or_phone_call_make_no_row(rig, monkeypatch):
    from agent_friday.services import laya_router_learn as ll
    from agent_friday.services import slice_head
    ll.note_delegated("t1", conversation_id="c1", request=LEASE)
    base = {"authenticated": True, "is_background_task": True, "task_id": "t1", "conversation_id": "c1"}
    # Each call is its own turn of the task: the same read repeated inside one turn is answered from the call
    # verifier's repeat cache (services/call_verifier) and never runs, so it would reach no hook at all.
    _run(rig.agent, dict(base, crew_agent_id="a1", turn_id="r1"))
    _run(rig.agent, dict(base, origin="phone", turn_id="r2"))
    monkeypatch.setattr(slice_head, "is_tainted", lambda key: True)
    _run(rig.agent, dict(base, turn_id="r3"))
    assert rig.kept == []
    monkeypatch.setattr(slice_head, "is_tainted", lambda key: False)
    _run(rig.agent, dict(base, turn_id="r4"))
    assert len(rig.kept) == 1


def test_a_deferral_with_no_delegated_task_never_makes_a_row(rig):
    from agent_friday.services import laya_router_learn as ll
    mine = {"authenticated": True, "is_background_task": True, "task_id": "t1", "conversation_id": "c1"}
    _run(rig.agent, dict(mine, turn_id="d1"))
    assert rig.kept == []
    # positive control: once the turn is handed to t1 by its own words, the same call (a new turn) makes the row
    ll.note_delegated("t1", conversation_id="c1", request="some other words")      # other words tie nothing
    _run(rig.agent, dict(mine, turn_id="d2"))
    assert rig.kept == []
    ll.note_delegated("t1", conversation_id="c1", request=LEASE)
    _run(rig.agent, dict(mine, turn_id="d3"))
    assert rig.kept == [("shadow", LEASE, "query_calendar")]


LEASE = "sort out the thing with the lease"


def test_a_hand_over_ties_the_deferral_whose_words_it_carries_never_a_newer_stale_one(rig):
    from agent_friday.services import laya_router_learn as ll
    with lp._lock:                                                       # a later deferral that was never delegated
        lp._held.append({"at": lp.now_fn(), "text": "book the dentist", "decision": "defer", "tool": None,
                         "confidence": 0.9, "layer": "rule", "cands": [], "surface": "voice",
                         "conversation_id": "c1", "resolved": False})
    ll.note_delegated("t9", conversation_id="c1", request="something the model made up")
    ll.note_delegated("t9", conversation_id="c1",
                      request=LEASE + "\n\n[Resolved on the owner's screen by rules, not by you: archive 2 row(s)]")
    with lp._lock:
        tied = {e["text"]: e.get("task_id") for e in lp._held}
    assert tied == {LEASE: "t9", "book the dentist": None}
    # red when: the newest untied deferral of the conversation is tied instead of the one that was handed over


def test_a_typed_deferral_is_never_tied(rig):
    from agent_friday.services import laya_router_learn as ll
    with lp._lock:
        lp._held[0]["surface"] = "chat"
    ll.note_delegated("t9", conversation_id="c1", request=LEASE)
    with lp._lock:
        assert lp._held[0].get("task_id") is None
