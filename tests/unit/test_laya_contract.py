"""services/laya: the one contract (swot spec section c.2, Phase 1).

``laya.ask(state, questions, caller, budget_ms, profile_id=None)`` is how every
Laya layer asks a question. The router's ``route`` is one preset of it. These
tests pin the six invariants, each with a case that fails when the invariant is
broken, and pin that the router's own behaviour did not move.

Nothing loads a model: rungs are faked at the preset seam, the encoder is faked
where an embedding is counted, and the real router runs only where it needs no
encoder (tier 0, or the encoder reported absent).
"""
from __future__ import annotations

import inspect
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_friday.services import laya, laya_router as lr

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    laya.reset()
    monkeypatch.setattr(laya, "log_path", lambda: tmp_path / "ask.jsonl")
    yield
    laya.reset()


@pytest.fixture
def preset(monkeypatch):
    """Register a throwaway preset for one test."""
    def add(name, fn):
        monkeypatch.setitem(laya.PRESETS, name, fn)
        return {"type": "choice", "preset": name}
    return add


def _ask(questions, text="what is on my calendar", budget_ms=500, **kw):
    return laya.ask({"text": text, "surface": "voice", "opts": dict(kw.pop("opts", {}))}, questions,
                    "test.caller", budget_ms, **kw)


# --- the shape ----------------------------------------------------------------

def test_ask_returns_the_contract_shape(preset):
    q = {"q": preset("fake", lambda ctx, spec: {"answer": {"value": "x", "p": 0.9, "set": ["x"], "abstain": False},
                                               "layer": "t1", "verdict": "act", "why": "fake"})}
    r = _ask(q)
    assert set(r) >= {"answers", "verdict", "layer", "versions", "latency_ms", "receipt_id"}
    assert r["answers"]["q"] == {"value": "x", "p": 0.9, "set": ["x"], "abstain": False}
    assert r["verdict"] == "act" and r["layer"] == "t1"
    assert set(r["versions"]) == {"base", "head", "personal", "calibration"}


def test_the_router_is_a_preset_and_keeps_its_signature():
    assert "route" in laya.PRESETS
    # route() is unchanged: the same parameters, in the same order, with the same defaults.
    sig = inspect.signature(lr.route)
    assert [(p.name, p.kind.name) for p in sig.parameters.values()] == [
        ("text", "POSITIONAL_OR_KEYWORD"), ("tools", "KEYWORD_ONLY"), ("budget_ms", "KEYWORD_ONLY"),
        ("surface", "KEYWORD_ONLY"), ("log", "KEYWORD_ONLY"), ("pending_card", "KEYWORD_ONLY"),
        ("last_route", "KEYWORD_ONLY")]
    assert sig.parameters["budget_ms"].default == lr.DEFAULT_BUDGET_MS
    assert sig.parameters["surface"].default == "voice" and sig.parameters["log"].default is True


TEXTS = ["what's on my calendar this afternoon?", "send an email to my sister saying I'll be late",
         "good morning", "Search the web for the Louvre's opening hours.", "don't check my email right now",
         "", "undo that"]


@pytest.mark.parametrize("text", TEXTS)
def test_route_through_ask_is_the_router_unchanged(text, monkeypatch):
    from agent_friday.services import laya2_encoder
    monkeypatch.setattr(laya2_encoder, "is_available", lambda: False)   # tier 0 and the degraded path only
    direct = lr.route(text, log=False)
    via = laya.route_turn(text, log=False)
    a, b = direct.as_dict(), via.as_dict()
    a.pop("elapsed_ms"), b.pop("elapsed_ms")
    assert a == b


def test_route_turn_passes_exactly_the_arguments_it_was_given(monkeypatch):
    seen = {}

    def fake(text, **kw):
        seen["text"], seen["kw"] = text, kw
        return lr.Route("no_tool", layer="rule")

    monkeypatch.setattr(lr, "route", fake)
    laya.route_turn("hello there", tools=["check_email"], surface="chat", log=False,
                    last_route={"tool": "check_email"})
    assert seen["text"] == "hello there"
    assert seen["kw"] == {"tools": ["check_email"], "surface": "chat", "log": False,
                          "last_route": {"tool": "check_email"}}
    with pytest.raises(TypeError):
        laya.route_turn("x", nonsense=1)
    # an argument another track adds to route() (trusted, quiet, head_mode...) is refused here until route() takes it
    assert laya._route_kw() == frozenset(n for n, p in inspect.signature(lr.route).parameters.items()
                                         if p.kind is inspect.Parameter.KEYWORD_ONLY)


def test_route_verdict_maps_the_decision(monkeypatch):
    for decision, verdict in (("tool", "act"), ("no_tool", "no_tool"), ("ask", "ask"), ("defer", "defer")):
        monkeypatch.setattr(lr, "route", lambda text, d=decision, **k: lr.Route(d, tool="check_email" if d == "tool" else None,
                                                                              layer="rule", confidence=0.9))
        res = laya.ask({"text": "x", "surface": "voice", "opts": {"log": False}}, laya.route_question(), "t", 500)
        assert res["verdict"] == verdict and res["layer"] == "rule"


# --- invariant 1: add-only ------------------------------------------------------

def test_a_preset_cannot_lower_an_answer_below_the_callers_floor(preset):
    low = preset("low", lambda ctx, spec: {"answer": {"value": "soft", "p": 0.9, "set": None, "abstain": False},
                                          "layer": "t2", "floor": "hard", "order": ["soft", "hard"]})
    r = _ask({"severity": low})
    assert r["answers"]["severity"]["value"] == "hard"
    rec = laya.receipt(r["receipt_id"])
    assert rec["questions"]["severity"]["clamped"] is True
    assert "only add" in rec["explain"]


def test_a_preset_that_raises_is_left_alone(preset):
    up = preset("up", lambda ctx, spec: {"answer": {"value": "hard", "p": 0.9, "set": None, "abstain": False},
                                        "layer": "t2", "floor": "soft", "order": ["soft", "hard"]})
    r = _ask({"severity": up})
    assert r["answers"]["severity"]["value"] == "hard"
    assert laya.receipt(r["receipt_id"])["questions"]["severity"]["clamped"] is False


@pytest.mark.parametrize("kw,lay", [("soft", "soft"), ("soft", "hard"), ("hard", "soft"), ("hard", "hard")])
def test_the_gate_opinion_is_never_below_the_keyword_scan(kw, lay, monkeypatch):
    from agent_friday.services import decisions, laya_backend
    monkeypatch.setattr(laya_backend, "is_ready", lambda: True)
    monkeypatch.setattr(laya_backend, "_answer_bounded", lambda state, also=(): (lay, 0.9, {"engine": "fake"}))
    monkeypatch.setattr(decisions, "_keyword_backend", lambda q, s, **k: (kw, None, {}))
    r = _ask({"action_severity": {"type": "choice", "preset": "gate_opinion", "options": ["soft", "hard"]}},
             text="Send the quarterly report to the board")
    got = r["answers"]["action_severity"]["value"]
    order = ["soft", "hard"]
    assert order.index(got) >= order.index(kw) and order.index(got) >= order.index(lay)
    assert r["layer"] == "t2"


def test_a_gate_opinion_with_laya_missing_is_degraded_and_keeps_the_keyword_answer(monkeypatch):
    from agent_friday.services import decisions, laya_backend
    monkeypatch.setattr(laya_backend, "is_ready", lambda: False)
    monkeypatch.setattr(laya_backend, "_loading", True, raising=False)
    monkeypatch.setattr(decisions, "_keyword_backend", lambda q, s, **k: ("hard", None, {}))
    r = _ask({"action_severity": {"type": "choice", "preset": "gate_opinion"}}, text="Send it")
    assert r["answers"]["action_severity"]["value"] == "hard"
    assert r["layer"] == "degraded"


# --- invariant 2: the mutation class is the governed registry's -----------------

def test_mutation_class_is_read_from_the_governed_tool_table():
    from agent_friday.services import call_verifier
    for tool in lr.TOOLS:
        ro = bool(call_verifier.annotations(tool).get("readOnlyHint"))
        assert laya.mutation_class(tool) == ("read" if ro else "write"), tool
    assert laya.mutation_class("undo_action") == "write"
    assert laya.mutation_class("query_calendar") == "read"
    assert laya.mutation_class("a_tool_the_table_has_never_heard_of") == "write"


def test_an_answer_cannot_claim_a_tool_does_not_mutate(preset):
    liar = preset("liar", lambda ctx, spec: {
        "answer": {"value": "tool", "p": 0.99, "set": None, "abstain": False, "tool": "undo_action", "mutates": False,
                   "readOnlyHint": True},
        "layer": "t1", "tool": "undo_action", "mutates": False})
    r = _ask({"q": liar}, opts={"mutates": False, "readOnlyHint": True})
    a = r["answers"]["q"]
    assert a["mutation_class"] == "write" and "mutates" not in a


def test_the_contract_never_reads_a_manifest_for_mutation():
    src = inspect.getsource(laya)
    assert "laya_manifests" not in src and "workspace_registry" not in src
    assert "call_verifier.annotations" in inspect.getsource(laya.mutation_class)


# --- invariant 3: never blocks ---------------------------------------------------

def test_a_hanging_rung_returns_degraded_within_the_budget(preset):
    gate = threading.Event()
    hang = preset("hang", lambda ctx, spec: (gate.wait(30), {"answer": {"value": "late"}})[1])
    t0 = time.perf_counter()
    try:
        r = _ask({"q": hang}, budget_ms=150)
        took = (time.perf_counter() - t0) * 1000
        assert r["layer"] == "degraded" and r["answers"]["q"]["value"] is None and r["answers"]["q"]["abstain"]
        assert took < 1000, took
        assert laya.receipt(r["receipt_id"])["degraded"] is True
    finally:
        gate.set()


def test_a_crashing_or_unknown_rung_is_degraded_not_raised(preset):
    def boom(ctx, spec):
        raise RuntimeError("encoder exploded")
    r = _ask({"a": preset("boom", boom), "b": {"type": "choice", "preset": "no_such_preset"}})
    assert r["layer"] == "degraded"
    assert r["answers"]["a"]["value"] is None and r["answers"]["b"]["value"] is None


def test_one_slow_question_does_not_take_a_fast_one_with_it(preset):
    gate = threading.Event()
    fast = preset("fast", lambda ctx, spec: {"answer": {"value": "ok", "p": 1.0, "set": None, "abstain": False}, "layer": "rule"})
    hang = preset("hang2", lambda ctx, spec: (gate.wait(30), None)[1])
    try:
        r = _ask({"fast": fast, "slow": hang}, budget_ms=150)
        assert r["answers"]["fast"]["value"] == "ok" and r["answers"]["slow"]["value"] is None
    finally:
        gate.set()


def test_the_router_hanging_is_the_routers_own_degraded_route(monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(lr, "route", lambda text, **k: (gate.wait(30), None)[1])
    monkeypatch.setattr(laya, "RULE_ALLOWANCE_MS", 100)
    t0 = time.perf_counter()
    try:
        r = laya.route_turn("what's on my calendar", log=False, budget_ms=50)
        assert (time.perf_counter() - t0) < 1.5
        assert r.decision == "no_tool" and r.layer == "degraded"
    finally:
        gate.set()


def test_stuck_threads_are_capped(preset, monkeypatch):
    gate = threading.Event()
    hang = preset("hang3", lambda ctx, spec: (gate.wait(30), None)[1])
    monkeypatch.setattr(laya, "MAX_STUCK", 2)
    try:
        for _ in range(2):
            _ask({"q": hang}, budget_ms=30)
        t0 = time.perf_counter()
        r = _ask({"q": hang}, budget_ms=5000)          # saturated: answers at once, starts nothing
        assert (time.perf_counter() - t0) < 1.0 and r["layer"] == "degraded"
    finally:
        gate.set()


# --- invariant 4: every decision has a receipt -----------------------------------

def test_every_ask_has_a_receipt_with_versions_and_no_words(preset):
    words = "please wire the garden fund to the plumber"
    ok = preset("ok", lambda ctx, spec: {"answer": {"value": "x", "p": 0.5, "set": None, "abstain": False},
                                         "layer": "rule", "why": "a sealed pattern"})
    r = _ask({"q": ok}, text=words)
    rec = laya.receipt(r["receipt_id"])
    assert rec["versions"] == r["versions"] and set(rec["versions"]) == {"base", "head", "personal", "calibration"}
    assert rec["caller"] == "test.caller" and rec["layer"] == "rule"
    assert words not in json.dumps(rec) and "garden" not in json.dumps(rec)
    assert "sealed rule" in rec["explain"] and "R0" in rec["explain"]


def test_a_degraded_ask_also_has_a_receipt_that_says_so(preset):
    def boom(ctx, spec):
        raise ValueError("x")
    r = _ask({"q": preset("boom2", boom)})
    rec = laya.receipt(r["receipt_id"])
    assert rec is not None and rec["degraded"] and "did not answer" in rec["explain"]


def test_receipts_are_appended_to_the_log_and_never_rewritten(preset, tmp_path):
    ok = preset("ok2", lambda ctx, spec: {"answer": {"value": "x"}, "layer": "rule"})
    a = _ask({"q": ok})
    b = _ask({"q": ok})
    lines = (tmp_path / "ask.jsonl").read_text(encoding="utf-8").splitlines()
    ids = [json.loads(l)["receipt_id"] for l in lines]
    assert ids == [a["receipt_id"], b["receipt_id"]]


def test_a_caller_that_says_not_to_log_writes_nothing_but_still_gets_a_receipt(preset, tmp_path):
    ok = preset("ok3", lambda ctx, spec: {"answer": {"value": "x"}, "layer": "rule"})
    r = _ask({"q": ok}, opts={"log": False})
    assert not (tmp_path / "ask.jsonl").exists()
    assert laya.receipt(r["receipt_id"]) is not None


# --- profile_id ---------------------------------------------------------------------

def test_no_profile_means_base_only(preset):
    ok = preset("ok4", lambda ctx, spec: {"answer": {"value": "x"}, "layer": "t1"})
    r = _ask({"q": ok})
    assert r["versions"]["personal"] == "none" and laya.receipt(r["receipt_id"])["profile_id"] is None
    r2 = _ask({"q": ok}, profile_id="p1")
    assert laya.receipt(r2["receipt_id"])["profile_id"] == "p1"
    assert r2["layer"] != "t1+personal"            # no personal layer exists yet


# --- one embedding per turn ----------------------------------------------------------

def test_every_preset_shares_the_turns_one_embedding(preset, monkeypatch):
    import hashlib
    import numpy as np
    from agent_friday.services import laya2_encoder, turn_vector
    calls = []

    def fake_embed(texts, max_len=256):
        calls.append(list(texts))
        out = []
        for t in texts:
            v = np.random.default_rng(int(hashlib.sha256(t.encode()).hexdigest()[:8], 16)).standard_normal(384)
            out.append((v / np.linalg.norm(v)).tolist())
        return out

    monkeypatch.setattr(laya2_encoder, "is_available", lambda: True)
    monkeypatch.setattr(laya2_encoder, "embed", fake_embed)
    turn_vector.reset()
    seen = []

    def needs_vector(ctx, spec):
        seen.append(ctx.vector())
        return {"answer": {"value": "x"}, "layer": "t1"}

    q = {"a": preset("v1", needs_vector), "b": preset("v2", needs_vector), "c": preset("v3", needs_vector)}
    text = "move the garden notes to the shared folder"
    try:
        _ask(q, text=text)
        _ask(q, text=text)
        assert len([c for c in calls if c == [text]]) == 1
        assert len(seen) == 6 and all(v == seen[0] for v in seen)
    finally:
        turn_vector.reset()
        laya2_encoder.reset()


# --- the callers use the contract ----------------------------------------------------

def test_the_local_voice_turn_routes_through_laya(monkeypatch):
    import agent_friday.routes.voice as rv
    calls = []

    def spy(text, **kw):
        calls.append(kw)
        return lr.Route("no_tool", layer="rule")

    monkeypatch.setattr(laya, "route_turn", spy)
    plan = rv._voice_route_plan("good morning", {"check_email"})
    assert len(calls) == 1 and calls[0]["surface"] == "voice"
    assert plan["tool"] is None


def test_the_chat_turn_routes_through_laya(monkeypatch):
    ctr = pytest.importorskip("agent_friday.services.chat_tool_routing")      # arrives with the chat-routing track
    calls = []

    def spy(text, **kw):
        calls.append(kw)
        return lr.Route("no_tool", layer="rule")

    monkeypatch.setattr(laya, "route_turn", spy)
    monkeypatch.setattr(lr, "ready", lambda: True)
    tr = ctr.decide("good morning", {}, settings={ctr.SETTING: "laya"}, held=["check_email"], local=True,
                    trusted=True)
    assert tr is not None and len(calls) == 1 and calls[0]["surface"] == "chat"


def test_the_live_reflex_shadow_routes_through_laya(monkeypatch):
    live_laya = pytest.importorskip("agent_friday.services.live_laya")        # arrives with the live-reflex track
    calls = []

    def spy(text, **kw):
        calls.append((text, kw))
        return lr.Route("no_tool", layer="rule")

    monkeypatch.setattr(laya, "route_turn", spy)
    me = SimpleNamespace(route_fn=None, held={"check_email"}, trusted=False)
    live_laya.LiveLaya._route(me, "good morning")
    assert calls and calls[0][1]["surface"] == live_laya.SURFACE and calls[0][1]["caller"] == "voice.live"


def test_point_and_act_routes_through_laya(monkeypatch):
    point_act = pytest.importorskip("agent_friday.services.point_act")        # arrives with the deep-actions track
    reflex_chat = pytest.importorskip("agent_friday.services.reflex_chat")
    calls = []
    monkeypatch.setattr(point_act, "looks_like", lambda m: True)
    monkeypatch.setattr(reflex_chat, "trusted_surface", lambda *a, **k: True)
    monkeypatch.setattr(laya, "point_act_turn", lambda m, **k: calls.append((m, k)))
    out = reflex_chat._point_act_turn("open the second one", {}, session_id="s", conversation_id="c",
                                      turn_id="t", authenticated=True, local=True)
    assert out is None and calls == [("open the second one", {"quiet": False})]


def test_the_triage_heads_read_through_laya(monkeypatch):
    th = pytest.importorskip("agent_friday.services.triage_heads")            # arrives with the triage track
    th.set_embedder(th.hashed_embedder())
    th.reset_cache()
    seen = []
    real = laya.ask

    def spy(state, questions, caller, budget_ms, profile_id=None):
        seen.append(caller)
        return real(state, questions, caller, budget_ms, profile_id)

    monkeypatch.setattr(laya, "ask", spy)
    try:
        th.head("mail").prototypes(block=True)
        label, p = th.mail_label(th.MAIL_SEEDS["career"][0], "")
        assert label == "career" and p > 0
        assert "triage.mail" in seen
    finally:
        th.set_embedder(None)
        th.reset_cache()


def test_without_the_point_act_rung_the_preset_degrades_and_the_caller_takes_its_own_path(monkeypatch):
    monkeypatch.delattr(lr, "point_act_route", raising=False)
    assert laya.point_act_turn("open the second one", log=False) is None


def test_without_the_triage_heads_a_triage_read_degrades(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "agent_friday.services.triage_heads", None)    # import raises ImportError
    assert laya.triage_read("mail", ["hello"]) is None


def test_the_point_act_preset_is_the_routers_point_act(monkeypatch):
    if not hasattr(lr, "point_act_route"):
        pytest.skip("point-and-act arrives with the deep-actions track")
    want = lr.Route("tool", tool="screen_select", args={"refs": ["mail:1"]}, layer="rule", confidence=0.9)
    monkeypatch.setattr(lr, "point_act_route", lambda text, stage, pointed, quiet: want)
    assert laya.point_act_turn("open the second one", log=False) is want
    monkeypatch.setattr(lr, "point_act_route", lambda text, stage, pointed, quiet: None)
    assert laya.point_act_turn("hello", log=False) is None


def test_the_resolver_preset_returns_the_resolution(monkeypatch):
    from agent_friday.services import laya_resolver as res
    want = res.Resolution("command", "Opening it.", reason="the only match")
    monkeypatch.setattr(res, "resolve", lambda text, sources=None: want)
    r = _ask({"resolve": {"type": "choice", "preset": "resolve"}}, text="open the budget note")
    assert r["native"]["resolve"] is want and r["answers"]["resolve"]["value"] == "command"


# --- wiring that must not drift --------------------------------------------------------

def test_the_scope_is_documented():
    doc = (ROOT / "docs" / "user-guide" / "laya.md").read_text(encoding="utf-8")
    assert "How Laya decides" in doc and "receipt" in doc.split("How Laya decides", 1)[1]


# --- per-call restrictions follow the call onto the worker ------------------------

def test_a_preset_on_the_worker_sees_the_callers_local_only_run(preset):
    from agent_friday.services import local_only_guard as g
    seen = {}

    def probe(ctx, spec):
        seen["mode"], seen["active"], seen["label"] = g.routing_mode(), g.is_active(), g.label()
        return {"answer": {"value": "x"}, "layer": "rule"}

    q = {"q": preset("probe", probe)}
    with g.local_only("a scheduled briefing"):
        _ask(q)
    assert seen == {"mode": "local_only", "active": True, "label": "a scheduled briefing"}
    seen.clear()
    _ask(q)                                            # outside the run the worker is not local-only
    assert seen["active"] is False


def test_a_cloud_pin_and_the_turn_id_follow_the_call(preset):
    from agent_friday import core
    from agent_friday.services import local_only_guard as g
    seen = {}
    q = {"q": preset("probe2", lambda ctx, spec: (seen.update(pin=getattr(g._state, "pin", None),
                                                              turn=getattr(core._TURN_LOCAL, "turn_id", None)),
                                                  {"answer": {"value": "x"}, "layer": "rule"})[1])}
    g._state.pin, g._state.pin_label = "some-model", "a job"
    core._TURN_LOCAL.turn_id = "turn-42"
    try:
        _ask(q)
    finally:
        g._state.pin, g._state.pin_label = "", ""
        core._TURN_LOCAL.turn_id = None
    assert seen == {"pin": "some-model", "turn": "turn-42"}


def test_context_variables_follow_the_call_too(preset):
    from agent_friday.services import cloud_intent
    seen = {}
    q = {"q": preset("probe3", lambda ctx, spec: (seen.update(owner=cloud_intent._OWNER_TURN.get()),
                                                  {"answer": {"value": "x"}, "layer": "rule"})[1])}
    tok = cloud_intent._OWNER_TURN.set(True)
    try:
        _ask(q)
    finally:
        cloud_intent._OWNER_TURN.reset(tok)
    assert seen == {"owner": True}


def test_the_worker_carries_every_thread_local_a_rung_could_read():
    src = inspect.getsource(laya._carry_snapshot) + inspect.getsource(laya._carry_apply)
    for needle in ("local_only_guard", "_TURN_LOCAL", "reasoning_trace", "turn_budget"):
        assert needle in src


# --- a cold first ask never changes the router's decision ------------------------------

def test_a_cold_first_route_turn_still_returns_the_routers_tool_decision(monkeypatch):
    """The first spoken turn after start finds the calibration table and the
    version ids cold. Their first-use cost is shadow bookkeeping and must not
    spend the decision's deadline: "pause the music" is still the router's
    tool, not a degraded no_tool the model would answer."""
    from agent_friday.services import laya_calibration as lc
    lc.set_table(None)                                   # cold: nothing loaded yet
    monkeypatch.setattr(lc, "load_table", lambda: (time.sleep(1.0), {"version": "x", "heads": {}})[1])
    monkeypatch.setattr(laya, "RULE_ALLOWANCE_MS", 0)
    monkeypatch.setattr(lr, "route", lambda text, **k: lr.Route("tool", tool="check_email", layer="laya2",
                                                                confidence=0.9))
    r = laya.route_turn("check my email", budget_ms=150, log=False)
    assert r.decision == "tool" and r.tool == "check_email" and r.layer == "laya2"   # not a degraded no_tool


def test_warm_loads_the_table_and_versions_off_the_request_path():
    from agent_friday.services import laya_calibration as lc
    lc.set_table(None)
    assert not lc.is_warm()
    laya.warm()
    deadline = time.time() + 5
    while time.time() < deadline and not lc.is_warm():
        time.sleep(0.02)
    assert lc.is_warm()
