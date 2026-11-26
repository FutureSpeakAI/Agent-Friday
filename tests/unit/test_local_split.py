"""The owner's split (Dispatch 2026-11-26, Stephen): quick turns on FridayWeaver 2, private turns on Bonsai.

Pinned here:
  * off by default; an unknown value is off; on but with FridayWeaver not installed routes as before;
  * a private turn (vault flag, vault tool, sensitive words) goes to Bonsai, local, in EVERY routing mode including
    cloud_only, and is never turned into a cloud route, not even when Bonsai cannot load;
  * a quick turn (confident reflex/conversation shape, short) goes to FridayWeaver 2 when it is serving;
  * a long or unconfident turn routes as usual;
  * while Bonsai holds the card for a private conversation, quick turns go to Bonsai instead of swapping;
  * the private wait says so on screen (the tool narration sink), and a failed load is a local refusal with no
    cloud offer.
"""
from __future__ import annotations

import pytest

from agent_friday.routing.model_router import ModelRouter, TaskType
from agent_friday.services import local_split as ls

ON = {"local_turn_split": ls.MODE_SPLIT}
QUICK = {"shape": "reflex_open", "shape_conf": 0.9, "margin": 0.4, "mutation": "state_changing", "oos": False}


@pytest.fixture
def world(monkeypatch):
    state = {"installed": {ls.QUICK_MODEL, ls.PRIVATE_MODEL}, "serving": {ls.QUICK_MODEL}}
    monkeypatch.setattr(ls, "_installed", lambda m: m in state["installed"])
    monkeypatch.setattr(ls, "_serving", lambda m: m in state["serving"])
    return state


def _route(text, mode="local_preferred", settings=ON, **ctx):
    r = ModelRouter(config={"mode": mode})
    return ls.route_for_turn(r, [{"role": "user", "content": text}], ctx, settings=settings)


def test_off_by_default_and_unknown_is_off():
    assert ls.mode({}) == ls.MODE_OFF
    assert ls.mode({"local_turn_split": "sideways"}) == ls.MODE_OFF
    assert ls.DEFAULT_MODE == ls.MODE_OFF


def test_off_routes_as_usual(world):
    assert _route("open spotify", settings={}, turn_shape=QUICK) is None


def test_on_without_fridayweaver_installed_routes_as_usual(world):
    world["installed"].discard(ls.QUICK_MODEL)
    assert not ls.active(ON)
    assert _route("open spotify", turn_shape=QUICK) is None
    assert ls.seat_override(ON) == {}


def test_seat_override_pins_fridayweaver_as_quick_reflexes(world):
    assert ls.seat_override(ON) == {"sidekick": ls.QUICK_MODEL}


@pytest.mark.parametrize("mode", ["local_only", "local_preferred", "cloud_only"])
def test_private_turn_goes_to_bonsai_in_every_mode(world, mode):
    r = _route("what's in my vault about my passport number", mode=mode, vault_access=True)
    assert r["provider"] == "local" and r["model"] == ls.PRIVATE_MODEL
    assert r["is_local"] and r["vault_access"] and r["local_split"] == "private"
    assert r["task_type"] == TaskType.VAULT_ACCESS


def test_vault_tool_makes_it_private(world):
    r = _route("look that up", tool_names=["vault_read"], turn_shape=QUICK)
    assert r["model"] == ls.PRIVATE_MODEL


def test_explicit_private_flag(world):
    assert _route("hi", private=True, turn_shape=QUICK)["model"] == ls.PRIVATE_MODEL


def test_quick_turn_goes_to_fridayweaver(world):
    r = _route("open spotify", turn_shape=QUICK)
    assert r["model"] == ls.QUICK_MODEL and r["is_local"] and r["local_split"] == "quick"


def test_long_or_unconfident_turn_routes_as_usual(world):
    assert _route("x " * 200, turn_shape=QUICK) is None
    assert _route("open spotify", turn_shape=dict(QUICK, shape_conf=0.3)) is None
    assert _route("open spotify", turn_shape=dict(QUICK, shape="deep_analysis")) is None
    assert _route("open spotify", turn_shape=dict(QUICK, oos=True)) is None
    assert _route("open spotify", turn_shape={"degraded": True}) is None
    assert _route("open spotify", turn_shape=None) is None


def test_agent_and_voice_paths_classify_when_no_verdict_was_passed(world, monkeypatch):
    from agent_friday.services import reflex_turn
    seen = []
    monkeypatch.setattr(reflex_turn, "classify", lambda text, **k: (seen.append(text), QUICK)[1])
    assert _route("open spotify")["model"] == ls.QUICK_MODEL
    assert seen == ["open spotify"]


def test_quick_turn_while_bonsai_holds_the_card_uses_bonsai(world):
    world["serving"] = {ls.PRIVATE_MODEL}
    assert _route("open spotify", turn_shape=QUICK)["model"] == ls.PRIVATE_MODEL


def test_quick_seat_down_routes_as_usual(world):
    world["serving"] = set()
    assert _route("open spotify", turn_shape=QUICK) is None


def test_prepare_turn_waits_and_says_so(world):
    route = _route("my vault", vault_access=True)
    notes = []
    out = ls.prepare_turn(route, ensure=lambda narrate: (narrate(ls.WAIT_NOTE), {"ok": True, "waited_s": 38.0})[1],
                          narrate=notes.append)
    assert notes == [ls.WAIT_NOTE]
    assert out["model"] == ls.PRIVATE_MODEL and not out.get("refuse")
    assert out["private_seat_wait_s"] == 38.0


def test_failed_load_is_a_local_refusal_never_cloud(world):
    route = _route("my vault", mode="cloud_only", vault_access=True)
    out = ls.prepare_turn(route, ensure=lambda narrate: {"ok": False, "error": "Waited 240 s for the lease"},
                          narrate=lambda n: None)
    assert out["refuse"] is True
    assert out["provider"] == "local" and out["model"] == ls.PRIVATE_MODEL
    assert not out.get("offer_cloud_switch") and not out.get("local_unavailable")
    assert "didn't send it anywhere else" in out["warning"]


def test_prepare_turn_leaves_other_routes_alone():
    r = {"provider": "cloud", "model": "x"}
    assert ls.prepare_turn(r, ensure=lambda n: pytest.fail("must not load Bonsai")) is r


class _Arb:
    def __init__(self, lease=None, grant=None):
        self.lease, self._grant, self.calls = lease, grant, []

    def renew(self, kind, ttl):
        self.calls.append(("renew", kind, ttl))
        return {"ok": True}

    def grant(self, kind, **kw):
        self.calls.append(("grant", kind, kw))
        return self._grant


def test_ensure_renews_a_held_private_lease(world):
    world["serving"] = {ls.PRIVATE_MODEL}
    arb = _Arb(lease={"kind": "private_turn"})
    assert ls.ensure_private_seat(arbiter=arb)["ok"]
    assert arb.calls == [("renew", "private_turn", ls.PRIVATE_IDLE_S)]


def test_ensure_grants_and_narrates(world, monkeypatch):
    arb = _Arb(grant={"ok": True})
    seen = []

    def grant(kind, **kw):
        world["serving"] = {ls.PRIVATE_MODEL}
        arb.calls.append(("grant", kind, kw))
        return {"ok": True}
    arb.grant = grant
    out = ls.ensure_private_seat(seen.append, arbiter=arb)
    assert out["ok"] and seen == [ls.WAIT_NOTE]
    assert arb.calls[0][0:2] == ("grant", "private_turn")
    assert arb.calls[0][2]["role"] == ls.PRIVATE_MODEL


def test_ensure_reports_a_refused_grant(world):
    arb = _Arb(grant={"ok": False, "error": "no room"})
    out = ls.ensure_private_seat(arbiter=arb)
    assert not out["ok"] and out["error"] == "no room"


# ── wired into the router: every chat, agent and voice turn goes through ModelRouter._decide ──

def test_router_decide_sends_private_to_bonsai_even_in_cloud_only(world, monkeypatch):
    monkeypatch.setattr(ls, "_settings", lambda s=None: ON if s is None else s)
    r = ModelRouter(config={"mode": "cloud_only"})
    out = r.route([{"role": "user", "content": "read my vault note"}], task_context={"vault_access": True})
    assert out["provider"] == "local" and out["model"] == ls.PRIVATE_MODEL and out["local_split"] == "private"


def test_router_decide_sends_quick_to_fridayweaver(world, monkeypatch):
    monkeypatch.setattr(ls, "_settings", lambda s=None: ON if s is None else s)
    r = ModelRouter(config={"mode": "local_preferred"})
    out = r.route([{"role": "user", "content": "open spotify"}],
                  task_context={"has_tools": True, "turn_shape": QUICK, "user_text": "open spotify"})
    assert out["model"] == ls.QUICK_MODEL and out["is_local"]


def test_router_decide_unchanged_when_off(world, monkeypatch):
    monkeypatch.setattr(ls, "_settings", lambda s=None: {} if s is None else s)
    r = ModelRouter(config={"mode": "local_preferred"})
    out = r.route([{"role": "user", "content": "open spotify"}],
                  task_context={"has_tools": True, "turn_shape": QUICK})
    assert out.get("local_split") is None
