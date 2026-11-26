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


def test_prepare_turn_loads_bonsai_silently(world):
    route = _route("my vault", vault_access=True)
    out = ls.prepare_turn(route, ensure=lambda: {"ok": True, "waited_s": 38.0})
    assert out["model"] == ls.PRIVATE_MODEL and not out.get("refuse") and not out.get("warning")
    assert out["private_seat_wait_s"] == 38.0
    assert not hasattr(ls, "WAIT_NOTE")          # no "please wait" text exists to show


def test_failed_load_is_a_local_refusal_never_cloud(world):
    route = _route("my vault", mode="cloud_only", vault_access=True)
    out = ls.prepare_turn(route, ensure=lambda: {"ok": False, "error": "Waited 300 s for the lease"})
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


def test_ensure_grants_silently(world, monkeypatch):
    arb = _Arb(grant={"ok": True})

    def grant(kind, **kw):
        world["serving"] = {ls.PRIVATE_MODEL}
        arb.calls.append(("grant", kind, kw))
        return {"ok": True}
    arb.grant = grant
    out = ls.ensure_private_seat(arbiter=arb)
    assert out["ok"]
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


# ── a conversation that went private stays on Bonsai ──

def test_rest_of_a_private_conversation_stays_on_bonsai(world):
    ls._PRIVATE_CONVS.clear()
    assert _route("read my vault note", vault_access=True, conversation_id="c1")["local_split"] == "private"
    r = _route("open spotify", turn_shape=QUICK, conversation_id="c1")
    assert r["model"] == ls.PRIVATE_MODEL and r["local_split"] == "private_conversation" and r["vault_access"]
    # another conversation is not affected
    assert _route("open spotify", turn_shape=QUICK, conversation_id="c2")["model"] == ls.QUICK_MODEL


def test_private_conversation_idles_out(world, monkeypatch):
    ls._PRIVATE_CONVS.clear()
    ls.note_private("c3")
    ls._PRIVATE_CONVS["c3"] -= ls.PRIVATE_IDLE_S + 1
    assert not ls.in_private_conversation("c3")


def test_private_conversation_turn_also_loads_bonsai(world):
    route = {"provider": "local", "model": ls.PRIVATE_MODEL, "local_split": "private_conversation"}
    called = []
    ls.prepare_turn(route, ensure=lambda: (called.append(1), {"ok": True, "waited_s": 0})[1])
    assert called == [1]


# ── Laya on the draft (typing, or a partial voice transcript) ──

class _DraftArb:
    def __init__(self):
        self.lease, self.calls = None, []

    def grant(self, kind, **kw):
        self.calls.append(("grant", kind))
        self.lease = {"kind": kind}
        return {"ok": True}

    def renew(self, kind, ttl):
        self.calls.append(("renew", kind))
        return {"ok": True}

    def release(self, kind=None):
        self.calls.append(("release", kind))
        self.lease = None
        return {"ok": True}


@pytest.fixture
def draft(world, monkeypatch):
    ls._PRIVATE_CONVS.clear()
    ls._DRAFT_LAST.clear()
    ls._PREWARM.__init__()
    monkeypatch.setattr(ls, "DRAFT_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(ls, "_settings", lambda s=None: ON if s is None else s)
    world["serving"] = {ls.QUICK_MODEL}
    return _DraftArb()


def _wait_prewarm():
    t = ls._PREWARM.thread
    if t is not None:
        t.join(5)


def test_private_draft_prewarms_bonsai(draft):
    out = ls.observe_draft("what's my passport number in the vault", "c9", arbiter=draft)
    _wait_prewarm()
    assert out["prep"] == "prewarm_bonsai" and ("grant", "private_turn") in draft.calls


def test_quick_draft_keeps_fridayweaver(draft):
    out = ls.observe_draft("open the weather app please", "c9", arbiter=draft)
    assert out["prep"] == "keep" and draft.calls == []


def test_draft_that_turns_away_cancels_the_prewarm(draft, monkeypatch):
    ls.observe_draft("what's my passport number in the vault", "c9", arbiter=draft)
    _wait_prewarm()
    monkeypatch.setattr(ls, "DRAFT_CANCEL_AFTER_S", 0.0)
    out = ls.observe_draft("actually, open the weather app", "c9", arbiter=draft)
    assert out["prep"] == "cancelled_prewarm"
    assert draft.calls[-1] == ("release", "private_turn")


def test_cancel_never_drops_bonsai_once_a_private_turn_used_it(draft, monkeypatch):
    ls.observe_draft("what's my passport number in the vault", "c9", arbiter=draft)
    _wait_prewarm()
    ls._PREWARM.mark_used()                       # the owner sent it; the turn is on Bonsai now
    monkeypatch.setattr(ls, "DRAFT_CANCEL_AFTER_S", 0.0)
    ls.observe_draft("thanks, now open the weather app", "c9", arbiter=draft)
    assert ("release", "private_turn") not in draft.calls


def test_draft_does_nothing_when_split_is_off(world, monkeypatch):
    monkeypatch.setattr(ls, "_settings", lambda s=None: {} if s is None else s)
    assert ls.observe_draft("what's my passport number in the vault", "c9")["prep"] == "none"


def test_draft_text_is_never_logged(draft, caplog, capsys):
    import logging
    caplog.set_level(logging.DEBUG)
    secret = "my passport number is X1234567 in the vault"
    ls.observe_draft(secret, "c9", arbiter=draft)
    _wait_prewarm()
    out = capsys.readouterr()
    assert "X1234567" not in caplog.text and "X1234567" not in out.out and "X1234567" not in out.err
    assert "X1234567" not in repr(ls.draft_stats()) and "X1234567" not in repr(ls._PREWARM.__dict__)


def test_draft_check_is_fast(draft):
    for i in range(20):
        ls.observe_draft("open the weather app number %d" % i, "c%d" % i, arbiter=draft)
    st = ls.draft_stats()
    assert st["checks"] >= 20 and st["check_ms_p95"] < 50


def test_draft_endpoint_never_echoes_text(draft, monkeypatch):
    from flask import Flask
    from agent_friday.routes import residency as rr
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(rr.residency_bp)
    monkeypatch.setattr(ls, "_arbiter", lambda a=None: draft)
    r = app.test_client().post("/api/split/draft", json={"text": "my passport X7654321 in the vault",
                                                         "conversation_id": "c5"})
    _wait_prewarm()
    assert r.status_code == 200 and "X7654321" not in r.get_data(as_text=True)
    assert r.get_json()["prep"] in ("prewarm_bonsai", "bonsai_ready_or_loading")


def test_async_observer_keeps_only_the_newest_draft(draft, monkeypatch):
    seen = []
    monkeypatch.setattr(ls, "observe_draft", lambda t, c=None, **k: seen.append(t))
    for i in range(50):
        ls.observe_draft_async("draft %d" % i, "c6")
    t = ls._ASYNC["thread"]
    if t is not None:
        t.join(5)
    assert seen and seen[-1] == "draft 49" and len(seen) < 50


def test_background_work_is_not_split(world):
    assert _route("read my vault note", vault_access=True, is_background_task=True) is None
    assert _route("open spotify", turn_shape=QUICK, scheduled=True) is None


def test_a_local_voice_call_lease_fails_fast_not_minutes(world):
    arb = _Arb(lease={"kind": "voice_call"})
    out = ls.ensure_private_seat(arbiter=arb)
    assert not out["ok"] and arb.calls == []
