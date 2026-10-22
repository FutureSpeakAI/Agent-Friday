"""Every routing decision carries a structured record, and the reply shows it (north star 12.3).

Before this change ``ModelRouter.route`` returned provider, model, task type and a reason;
the journal's "alternatives" were just the other provider names; nothing said what a turn
would cost, how much was sent, or what happens if the choice fails; and the page showed
the model badge with no reason behind it.

Now ``route`` adds ``record`` (services/route_record): seat, reason, alternatives not
chosen each with why, expected cost with its basis, context size, the fallback policy;
``attribution`` keeps the turn's records and returns the one that matches the model that
answered, with latency; the chat routes put it on the reply as ``route``; the badge has a
"why" that opens it (static/js/friday_route_why.js).
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MSGS = [{"role": "user", "content": "x" * 4000}]


def _rr():
    from agent_friday.services import route_record
    return route_record


def test_a_cloud_choice_names_what_it_passed_over_and_what_it_costs():
    rec = _rr().build({"provider": "cloud", "model": "claude-sonnet-5", "task_type": "chat",
                       "reason": "Routing mode is cloud_only", "is_local": False},
                      messages=MSGS, routing_cfg={"mode": "cloud_only"})
    assert rec["seat"] == "cloud" and rec["model"] == "claude-sonnet-5"
    assert rec["reason"] == "Routing mode is cloud_only"
    seats = {a["seat"] for a in rec["alternatives"]}
    assert seats == {"local", "cloud"}
    local = [a for a in rec["alternatives"] if a["seat"] == "local"][0]
    assert "cloud only" in local["why"]
    other = [a for a in rec["alternatives"] if a["seat"] == "cloud"][0]
    assert other["provider"] == "openai" and "silent" in other["why"]
    assert rec["context"]["prompt_tokens_estimate"] == 1000
    assert rec["expected_cost"]["usd"] == pytest.approx(0.006)
    assert "1,000" in rec["expected_cost"]["basis"]
    assert rec["fallback_policy"]["mode"] == "cloud_only"
    assert "you are asked" in rec["fallback_policy"]["cloud_to_cloud"]
    assert "not used" in rec["fallback_policy"]["cloud_to_local"]


def test_an_owner_cloud_order_is_reported_as_the_policy():
    rec = _rr().build({"provider": "cloud", "model": "claude-sonnet-5", "is_local": False},
                      messages=MSGS, routing_cfg={"mode": "cloud_only",
                                                  "cloud_fallback_order": "anthropic_then_openai"})
    other = [a for a in rec["alternatives"] if a["seat"] == "cloud"][0]
    assert "next in your cloud order" in other["why"]
    assert rec["fallback_policy"]["cloud_fallback_order"] == ["anthropic", "openai"]


def test_a_local_choice_says_why_the_cloud_was_withheld_and_costs_nothing():
    rec = _rr().build({"provider": "local", "model": "gemma4:e4b", "is_local": True,
                       "reason": "local_preferred mode: no cloud intent for this call, so it stays on a local seat",
                       "cloud_withheld": {"model": "claude-sonnet-5", "reason": "tool use"}},
                      messages=MSGS, routing_cfg={"mode": "local_preferred"})
    assert rec["seat"] == "local"
    cloud = [a for a in rec["alternatives"] if a["seat"] == "cloud"][0]
    assert cloud["model"] == "claude-sonnet-5" and "no cloud intent" in cloud["why"]
    assert rec["expected_cost"]["usd"] == 0.0
    assert "your yes" in rec["fallback_policy"]["local_to_cloud"] or \
        "recorded choice" in rec["fallback_policy"]["local_to_cloud"]


def test_a_vault_turn_says_it_stays_on_this_machine():
    rec = _rr().build({"provider": "local", "model": "gemma4:e4b", "is_local": True,
                       "vault_access": True}, routing_cfg={"mode": "cloud_only"})
    assert "vault" in rec["alternatives"][0]["why"]
    assert "never leaves" in rec["fallback_policy"]["vault"]


def test_a_refusal_is_a_record_too():
    rec = _rr().build({"provider": "local", "model": None, "refuse": True, "local_unavailable": True,
                       "warning": "No local model is answering right now"},
                      routing_cfg={"mode": "local_only"})
    assert rec["seat"] == "none" and rec["refused"] is True
    assert rec["expected_cost"]["usd"] == 0.0
    assert {a["seat"] for a in rec["alternatives"]} == {"local", "cloud"}


def test_computer_control_override_names_the_local_seat_it_set_aside():
    rec = _rr().build({"provider": "cloud", "model": "claude-sonnet-5", "is_local": False,
                       "override": "computer_control", "overridden_local_model": "gemma4:e4b"},
                      routing_cfg={"mode": "local_preferred"})
    assert "Computer Control" in rec["reason"]
    local = [a for a in rec["alternatives"] if a["seat"] == "local"][0]
    assert local["model"] == "gemma4:e4b" and "screen" in local["why"]


def test_an_unknown_price_is_said_not_invented():
    rec = _rr().build({"provider": "openai", "model": "some/unknown-model", "is_local": False,
                       "provider_name": "openrouter"}, messages=MSGS, routing_cfg={"mode": "cloud_only"})
    assert rec["expected_cost"]["usd"] is None
    assert "no price on file" in rec["expected_cost"]["basis"]
    other = [a for a in rec["alternatives"] if a["seat"] == "cloud"][0]
    assert other["provider"] == "anthropic"


def test_an_anthropic_decision_through_the_registry_does_not_list_anthropic_as_unused():
    rec = _rr().build({"provider": "cloud", "provider_name": "anthropic", "model": "claude-sonnet-5",
                       "is_local": False}, routing_cfg={"mode": "cloud_only"})
    other = [a for a in rec["alternatives"] if a["seat"] == "cloud"][0]
    assert other["provider"] == "openai"


def test_error_text_never_reaches_the_record_raw():
    from agent_friday.user_errors import ExceptionText
    rec = _rr().build({"provider": "cloud", "model": "m", "is_local": False,
                       "reason": ExceptionText("Traceback ... internal detail at C:\\Users\\x")},
                      routing_cfg={"mode": "cloud_only"})
    assert rec["reason"] == "(error)"


def test_the_chat_record_wins_and_is_replaced_not_duplicated():
    from agent_friday.services import attribution as a
    a.reset()
    a.note_route({"model": "claude-sonnet-5", "source": "chat", "reason": "first"})
    a.note_route({"model": "claude-sonnet-5", "source": "router", "reason": "nested helper call"})
    a.note_route({"model": "claude-sonnet-5", "source": "chat", "reason": "final"})
    a.record_generation("claude-sonnet-5", provider="anthropic", seat="cloud")
    rec = a.route_record()
    assert rec["reason"] == "final" and rec["source"] == "chat"
    a.reset()
    a.note_route({"model": "x", "source": "router"})
    assert a.route_record()["verified"] is False
    a.reset()


def test_build_never_raises_on_nonsense():
    assert _rr().build(None)["seat"] in ("cloud", "local", "none")
    assert _rr().build({"provider": object()}, messages=[1, None, {"content": 5}])


def test_attribution_returns_the_record_that_matches_who_answered():
    from agent_friday.services import attribution as a
    a.reset()
    assert a.route_record() is None
    a.note_route({"model": "gemma4:e4b", "seat": "local"})
    a.note_route({"model": "claude-sonnet-5", "seat": "cloud"})
    time.sleep(0.06)        # Windows' monotonic clock ticks in ~16 ms
    a.record_generation("gemma4:e4b", provider="ollama", seat="local")
    rec = a.route_record()
    assert rec["model"] == "gemma4:e4b" and "substituted" not in rec
    assert isinstance(rec["latency_ms"], int) and rec["latency_ms"] >= 30
    a.record_generation("other-model", provider="x", seat="cloud")
    rec = a.route_record()
    assert rec["model"] == "claude-sonnet-5"            # the last decision
    assert rec["answered_by"] == "other-model" and rec["substituted"] is True
    a.reset()
    assert a.route_record() is None


def test_the_router_attaches_the_record_and_notes_it(monkeypatch):
    from agent_friday.routing.model_router import ModelRouter
    from agent_friday.services import attribution as a
    r = ModelRouter({"mode": "cloud_only"})
    monkeypatch.setattr(ModelRouter, "_decide",
                        lambda self, m, c=None: {"provider": "cloud", "model": "claude-sonnet-5",
                                                 "reason": "Routing mode is cloud_only", "is_local": False})
    a.reset()
    out = r.route(MSGS, {})
    assert out["record"]["seat"] == "cloud" and out["record"]["source"] == "router"
    assert a.route_record()["model"] == "claude-sonnet-5"


def test_the_chat_routes_put_the_record_on_the_reply():
    src = (REPO / "src" / "agent_friday" / "routes" / "chat.py").read_text(encoding="utf-8")
    assert src.count("_attr.route_record()") + src.count("_attr2.route_record()") == 2
    assert src.count("friday_msg['route'] = _route_rec_out") == 2
    assert src.count('"route": _route_rec_out') == 2
    # The chat route's final decision (after its per-turn overrides) is recorded too.
    assert 'source="chat"' in src


@pytest.mark.parametrize("page", ["index.html", "ui_parts/app.html"])
def test_the_badge_has_a_why(page):
    src = (REPO / page).read_text(encoding="utf-8")
    assert "window.FridayRouteWhy" in src and "route: m.route" in src.replace("route:m.route", "route: m.route")
    assert "d.route" in src


@pytest.mark.parametrize("page", ["index.html", "ui_parts/head.html"])
def test_the_why_script_is_loaded(page):
    assert '/static/js/friday_route_why.js' in (REPO / page).read_text(encoding="utf-8")


def test_the_why_shows_every_part_of_the_record():
    js = (REPO / "static" / "js" / "friday_route_why.js").read_text(encoding="utf-8")
    for key in ("r.reason", "r.alternatives", "r.expected_cost", "r.context", "r.fallback_policy",
                "r.latency_ms", "r.answered_by"):
        assert key in js, key
    assert "fetch(" not in js        # it shows what the reply carries; it calls nothing
