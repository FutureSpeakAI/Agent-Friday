"""The local brain's reasoning effort follows the turn's shape (spec 2.3).

A deep turn carries `xhigh`, an ordinary turn `medium`, a pinned setting wins
over any verdict, and a reflex-shaped turn carries `none` ONLY once the
policy flag `reflex_thinking_off` is on; shipped off, it carries `medium`,
which is what every local turn sent before the policy existed. The last tests
drive `_call_openai` to the transport and read the payload itself.

Mutation-checked: a policy that returns "none" for a reflex route regardless
of the flag fails `test_a_reflex_verdict_keeps_medium_until_the_flag_is_on`.
"""
from __future__ import annotations

from agent_friday.core import DEFAULT_SETTINGS
from agent_friday.services import reasoning_policy as rp

REFLEX = {"shape": "reflex_open", "route": "reflex", "effort": "none", "degraded": False}
ORDINARY = {"shape": "question_brain", "route": "brain", "effort": "medium", "degraded": False}
DEEP = {"shape": "deep_coding", "route": "brain", "effort": "xhigh", "degraded": False}
DEGRADED = {"shape": "brain", "route": "brain", "effort": "medium", "degraded": True}
AUTO = {"local_reasoning_effort": "auto"}


def test_the_settings_are_declared_so_the_loader_keeps_them():
    assert DEFAULT_SETTINGS["local_reasoning_effort"] == "auto"
    assert DEFAULT_SETTINGS["local_reasoning_effort_policy"] == {
        "reflex_thinking_off": False, "deep_xhigh": True}


def test_a_reflex_verdict_keeps_medium_until_the_flag_is_on():
    assert rp.reasoning_effort_for_turn(REFLEX, AUTO) == "medium"
    assert rp.reasoning_effort_for_turn(REFLEX, dict(DEFAULT_SETTINGS)) == "medium"
    on = dict(AUTO, local_reasoning_effort_policy={"reflex_thinking_off": True})
    assert rp.reasoning_effort_for_turn(REFLEX, on) == "none"


def test_an_ordinary_turn_is_medium():
    assert rp.reasoning_effort_for_turn(ORDINARY, AUTO) == "medium"
    assert rp.reasoning_effort_for_turn(None, AUTO) == "medium"
    assert rp.reasoning_effort_for_turn(DEGRADED, AUTO) == "medium"
    assert rp.reasoning_effort_for_turn(None, {}) == "medium"


def test_a_deep_turn_is_xhigh_unless_the_policy_says_otherwise():
    assert rp.reasoning_effort_for_turn(DEEP, AUTO) == "xhigh"
    assert rp.reasoning_effort_for_turn(
        {"shape": "deep_analysis", "route": "brain", "degraded": False}, AUTO) == "xhigh"
    off = dict(AUTO, local_reasoning_effort_policy={"deep_xhigh": False})
    assert rp.reasoning_effort_for_turn(DEEP, off) == "medium"


def test_a_pinned_setting_wins_over_every_verdict():
    for verdict in (REFLEX, ORDINARY, DEEP, DEGRADED, None):
        assert rp.reasoning_effort_for_turn(verdict, {"local_reasoning_effort": "xhigh"}) == "xhigh"
        assert rp.reasoning_effort_for_turn(verdict, {"local_reasoning_effort": "medium"}) == "medium"
        assert rp.reasoning_effort_for_turn(verdict, {"local_reasoning_effort": "off"}) == "none"
        assert rp.reasoning_effort_for_turn(verdict, {"local_reasoning_effort": "default"}) is None
    assert rp.reasoning_effort_for_turn(DEEP, {"local_reasoning_effort": "high"}) == "xhigh"


# ── the payload ─────────────────────────────────────────────────────────────

#: A seat we serve ourselves: local classification, local-capable adapter,
#: loopback address, no key. `_call_openai` re-verifies all three.
_SEAT = {
    "name": "test-seat",
    "base_url": "http://127.0.0.1:8090/v1",
    "auth": {"type": "none"},
    "classification": "local",
    "adapter": "openai-compatible",
    "features": {},
}


def _payload_for(monkeypatch, settings, **kw):
    """Drive _call_openai to the transport and read the first payload.

    `requests` is imported inside the sender, so the patch lands on the
    requests module; the live-seat lookup is silenced so the descriptor's
    address is used.
    """
    import requests
    from agent_friday.services import local_call, model_router as mr

    seen = {}

    def fake_post(url, **kwargs):
        if not seen:
            seen.update(kwargs.get("json") or {})
        raise RuntimeError("stop here: the payload is what this test reads")

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(local_call, "seat_endpoint", lambda m: None)
    monkeypatch.setattr(mr, "_load_settings", lambda *a, **k: dict(settings))
    try:
        mr._call_openai([{"role": "user", "content": "go"}], model="bonsai2:27b",
                        provider=dict(_SEAT), stream=False, **kw)
    except Exception:
        pass
    assert seen, "no payload was captured: the call never reached the transport"
    return seen


def test_the_payload_carries_medium_for_an_ordinary_turn(monkeypatch):
    p = _payload_for(monkeypatch, AUTO, session_ctx={"turn_shape": ORDINARY})
    assert p["reasoning_effort"] == "medium"
    assert "chat_template_kwargs" not in p


def test_the_payload_carries_xhigh_for_a_deep_turn(monkeypatch):
    p = _payload_for(monkeypatch, AUTO, session_ctx={"turn_shape": DEEP})
    assert p["reasoning_effort"] == "xhigh"


def test_the_payload_keeps_medium_for_a_reflex_turn_until_the_flag_is_on(monkeypatch):
    p = _payload_for(monkeypatch, AUTO, session_ctx={"turn_shape": REFLEX})
    assert p["reasoning_effort"] == "medium"
    on = dict(AUTO, local_reasoning_effort_policy={"reflex_thinking_off": True})
    p = _payload_for(monkeypatch, on, session_ctx={"turn_shape": REFLEX})
    assert p["reasoning_effort"] == "none"
    assert p["chat_template_kwargs"] == {"enable_thinking": False}


def test_the_turn_shape_kwarg_is_accepted_and_a_pinned_setting_wins(monkeypatch):
    p = _payload_for(monkeypatch, AUTO, turn_shape=DEEP)
    assert p["reasoning_effort"] == "xhigh"
    p = _payload_for(monkeypatch, {"local_reasoning_effort": "medium"}, turn_shape=DEEP)
    assert p["reasoning_effort"] == "medium"


def test_no_verdict_at_all_sends_medium_as_before(monkeypatch):
    p = _payload_for(monkeypatch, AUTO)
    assert p["reasoning_effort"] == "medium"
