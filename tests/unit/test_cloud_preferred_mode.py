"""cloud_preferred: a cloud mode that may answer locally ONLY when the cloud is
out, and says so in fixed words (Laya in every mode addendum 2026-10-10, LM-D1).

* it is a cloud mode: `require` carries the implicit `cloud_mode` intent;
* `outage_local` is allowed in it and nowhere else, and the reply opens with
  "Answering locally: no connection to <provider>.";
* background text calls behave as cloud_only (no local legs in the ladder);
* cloud voice is allowed;
* it is not offered in any picker until the lead turns it on.
"""
from __future__ import annotations

import pytest

from agent_friday.services import cloud_intent as ci
from agent_friday.services import cloud_mode_policy as cmp
from agent_friday.services import laya_templates as lt


@pytest.fixture(autouse=True)
def _clean():
    ci.clear()
    ci.forget_recent()
    yield
    ci.clear()


@pytest.fixture
def mode(monkeypatch):
    def _set(name):
        monkeypatch.setattr(ci, "routing_mode", lambda: name)
        return name
    return _set


def test_it_is_a_cloud_mode_not_a_local_one():
    assert "cloud_preferred" in ci.CLOUD_MODES
    assert "cloud_preferred" not in ci.LOCAL_MODES
    assert ci.is_local_mode("cloud_preferred") is False
    assert ci.is_cloud_mode("cloud_preferred") is True
    assert cmp.is_local_mode("cloud_preferred") is False


def test_the_cloud_needs_no_intent_in_it(mode):
    mode("cloud_preferred")
    tok = ci.require("anthropic", "model call", "claude-sonnet-5")
    assert tok.intent == "cloud_mode" and tok.mode == "cloud_preferred"
    assert cmp.cloud_allowed("background job") is True


def test_a_more_specific_intent_is_the_better_receipt(mode):
    mode("cloud_preferred")
    with ci.intent_scope("user_explicit", source="chat_seat"):
        assert ci.require("anthropic", "model call").intent == "user_explicit"


@pytest.mark.parametrize("m,allowed", [
    ("cloud_preferred", True), ("cloud_only", False),
    ("local_preferred", False), ("local_only", False), ("smart", False)])
def test_only_cloud_preferred_may_answer_locally_on_an_outage(m, allowed):
    assert ci.outage_local_allowed(m) is allowed


def test_the_outage_answer_opens_with_the_fixed_words():
    line = ci.outage_notice("Anthropic")
    assert line.text == "Answering locally: no connection to Anthropic."
    assert line.laya is True and line.template == "outage_local"
    assert line.receipt() == {"laya": True, "template": "outage_local", "kind": "notice"}
    assert lt.OUTAGE_LOCAL.format(provider="X") == "Answering locally: no connection to X."


def test_an_unnamed_provider_is_still_named():
    assert ci.outage_notice("").text == "Answering locally: no connection to the cloud."


def test_background_ladder_has_no_local_legs_in_cloud_preferred():
    from agent_friday.services import model_router as mr

    def leg(name):
        return (name, lambda *a, **k: "", None)
    attempts = [leg("cloud"), leg("openai"), leg("local")]
    out = mr._mode_filtered_attempts(list(attempts), {"mode": "cloud_preferred"})
    assert [a[0] for a in out] == ["cloud", "openai"]
    # ... exactly what cloud_only does.
    same = mr._mode_filtered_attempts(list(attempts), {"mode": "cloud_only"})
    assert [a[0] for a in same] == [a[0] for a in out]


def test_the_first_call_goes_to_the_cloud_in_cloud_preferred(monkeypatch):
    import agent_friday.routing.ollama_manager as ollama_manager
    import agent_friday.services.model_store as model_store
    from agent_friday.routing.model_router import ModelRouter

    class _NoOllama:
        def is_available(self):
            return False

        def list_models(self):
            return []

    monkeypatch.setattr(ollama_manager, "get_manager", lambda *a, **kw: _NoOllama())
    monkeypatch.setattr(model_store, "available", lambda: {})
    r = ModelRouter(config={"mode": "cloud_preferred",
                            "default_cloud_model": "claude-sonnet-5"})
    assert r.mode == "cloud_preferred"
    d = r.route([{"role": "user", "content": "what is 2+2?"}])
    assert d["provider"] == "cloud" and d["is_local"] is False


def test_cloud_voice_is_not_blocked_by_the_mode(mode):
    # The /ws/live gate (routes/voice.py) refuses only local_only; the
    # checkpoint agrees: the owner's engine choice passes in cloud_preferred.
    mode("cloud_preferred")
    tok = ci.require_owner_engine("google-gemini", "Gemini Live voice call")
    assert tok.intent == "user_explicit" and tok.mode == "cloud_preferred"


def test_it_is_not_in_any_picker_until_the_lead_turns_it_on():
    assert ci.UI_HIDDEN_MODES == ("cloud_preferred",)
