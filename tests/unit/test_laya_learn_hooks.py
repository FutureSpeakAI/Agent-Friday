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
