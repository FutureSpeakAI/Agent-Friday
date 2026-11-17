"""The model slate: one list for the desktop picker and every remote device, and a serve check that
fails loudly (never a quiet other model)."""
from __future__ import annotations

import pytest

from agent_friday.services import model_slate as ms

CAT = {
    "roles": {"orchestrator": [{"id": "claude-opus-5-5"}, {"id": "bonsai2:27b"}]},
    "models": [
        {"id": "claude-opus-5-5", "provider": "anthropic", "provider_label": "Anthropic",
         "roles": ["orchestrator"], "local": False, "available": True, "needs_key": True,
         "curated": True},
        {"id": "gpt-5", "provider": "openai", "provider_label": "OpenAI",
         "roles": ["orchestrator"], "local": False, "available": False, "needs_key": True},
        {"id": "x/model", "provider": "openrouter", "provider_label": "OpenRouter",
         "roles": ["orchestrator"], "local": False, "available": True, "needs_key": True},
        {"id": "bonsai2:27b", "provider": "arbiter-local", "provider_label": "On this PC",
         "roles": ["orchestrator"], "local": True, "available": True, "needs_key": False,
         "curated": True},
        {"id": "gemma4:e4b", "provider": "arbiter-local", "provider_label": "On this PC",
         "roles": ["orchestrator"], "local": True, "available": True, "needs_key": False},
        {"id": "flux-1", "provider": "x", "roles": ["creative"], "local": True, "available": True},
    ],
    "providers": [],
}


@pytest.fixture
def pc(monkeypatch):
    monkeypatch.setattr(ms, "_benchmark", lambda mid: None)
    state = {"mode": "local_preferred", "live": {"bonsai2:27b"}, "busy": None}
    monkeypatch.setattr(ms, "_catalog", lambda: CAT)
    monkeypatch.setattr(ms, "_settings", lambda: {"orchestrator_model": "bonsai2:27b",
                                                  "model_routing": {"mode": state["mode"]}})
    monkeypatch.setattr(ms, "_live_local", lambda: state["live"])
    monkeypatch.setattr(ms, "_local_busy_reason", lambda mid: state["busy"])
    return state


def _by_id(s):
    return {r["id"]: r for r in s["models"]}


def test_the_slate_lists_every_provider_the_picker_does_with_the_facts(pc):
    s = ms.slate()
    rows = _by_id(s)
    assert set(rows) == {"claude-opus-5-5", "gpt-5", "x/model", "bonsai2:27b", "gemma4:e4b"}
    assert "flux-1" not in rows  # not a chat model
    opus, gpt, bonsai, gemma = (rows[k] for k in ("claude-opus-5-5", "gpt-5", "bonsai2:27b",
                                                  "gemma4:e4b"))
    assert opus["cloud"] and opus["key_present"] and opus["can_serve"] and opus["warm"] is None
    assert gpt["key_present"] is False and gpt["can_serve"] is False
    assert gpt["blocked"]["code"] == ms.NEEDS_KEY and "OpenAI key" in gpt["blocked"]["reason"]
    assert bonsai["local"] and bonsai["warm"] is True and bonsai["selected"] and bonsai["key_present"]
    assert gemma["warm"] is False  # cold: installed, not serving
    assert s["selected"] == "bonsai2:27b" and s["routing_mode"] == "local_preferred"
    assert [r["id"] for r in s["models"][:2]] == ["claude-opus-5-5", "bonsai2:27b"]  # picker first


def test_warm_is_unknown_not_guessed_when_the_seats_cannot_be_surveyed(pc):
    pc["live"] = None
    assert _by_id(ms.slate())["bonsai2:27b"]["warm"] is None


def test_every_listed_id_with_a_key_is_servable_including_openrouter(pc):
    for mid in ("claude-opus-5-5", "x/model", "bonsai2:27b", "gemma4:e4b"):
        assert ms.require_servable(mid)["id"] == mid


@pytest.mark.parametrize("mid,code", [
    ("claude-opus-9", ms.UNKNOWN_MODEL),
    ("flux-1", ms.NOT_A_CHAT_MODEL),
    ("gpt-5", ms.NEEDS_KEY),
])
def test_a_pick_that_cannot_run_is_refused_with_a_reason(pc, mid, code):
    with pytest.raises(ms.ModelNotServable) as e:
        ms.require_servable(mid)
    assert e.value.code == code and e.value.reason and e.value.model_id == mid


def test_a_cloud_pick_in_local_only_mode_says_so(pc):
    pc["mode"] = "local_only"
    with pytest.raises(ms.ModelNotServable) as e:
        ms.require_servable("claude-opus-5-5")
    assert e.value.code == ms.LOCAL_ONLY_MODE and "Local only" in e.value.reason
    assert ms.require_servable("bonsai2:27b")  # local still serves
    assert _by_id(ms.slate())["claude-opus-5-5"]["can_serve"] is False


def test_a_second_local_model_while_another_serves_is_refused(pc):
    pc["busy"] = "bonsai2:27b is already serving on this machine and there is only room for one."
    with pytest.raises(ms.ModelNotServable) as e:
        ms.require_servable("gemma4:e4b")
    assert e.value.code == ms.LOCAL_BUSY
    assert ms.require_servable("claude-opus-5-5")  # a cloud pick never contends


def test_the_available_copy_of_an_id_wins(monkeypatch, pc):
    dup = {"roles": {}, "models": [
        {"id": "m", "provider": "a", "roles": ["orchestrator"], "available": False, "needs_key": True},
        {"id": "m", "provider": "b", "roles": ["orchestrator"], "available": True, "needs_key": True}]}
    monkeypatch.setattr(ms, "_catalog", lambda: dup)
    assert ms.require_servable("m")["provider"] == "b"
