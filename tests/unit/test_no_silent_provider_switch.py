"""A6: Friday never moves from one cloud provider to another on her own.

Before this, both generation ladders (`model_router._generate_text`, behind
briefings, the digest, editorials and scheduled work, and `agent._generate_agent`,
behind subagents, tasks and /api/chat/send) answered an Anthropic failure by
calling the OpenAI-compatible provider (OpenRouter by default) with only a journal
line to show for it. Now the move happens only when the owner chose it for the call
or set an order in advance, and the failure is announced either way.

Providers are stubbed; no request leaves the test.
"""
from __future__ import annotations

import pytest

from agent_friday.services import model_router as mr
from agent_friday.services import provider_fallback as pf

MSG = [{"role": "user", "content": "write a haiku about espresso"}]


class _Leg:
    def __init__(self, exc=None, result="reply"):
        self.calls = 0
        self.exc, self.result = exc, result

    def __call__(self, *a, **k):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.result


@pytest.fixture(autouse=True)
def _anthropic_key_present(monkeypatch):
    """An Anthropic key exists: a failure is a failure, not the one-key configuration."""
    from agent_friday.services import one_key
    monkeypatch.setattr(one_key, "anthropic_ready", lambda: True)


@pytest.fixture
def notices(monkeypatch):
    pf._reset_for_tests()
    seen = []
    from agent_friday import notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: seen.append(kw) or {}, raising=False)
    yield seen
    pf._reset_for_tests()


def _text_ladder(monkeypatch, routing):
    monkeypatch.setattr(mr, "_load_settings", lambda *a, **k: {"model_routing": routing}, raising=False)
    monkeypatch.setattr(mr, "get_anthropic_client", lambda *a, **k: object(), raising=False)
    import agent_friday.services.demo_mode as dm
    monkeypatch.setattr(dm, "is_demo", lambda *a, **k: False, raising=False)
    claude = _Leg(exc=RuntimeError("529 overloaded"))
    oai = _Leg(result=("from the other provider", []))
    local = _Leg(exc=RuntimeError("no local seat"))
    monkeypatch.setattr(mr, "_call_claude", lambda m, **kw: claude(m, **kw))
    monkeypatch.setattr(mr, "_call_openai", lambda m, **kw: oai(m, **kw))
    monkeypatch.setattr(mr, "_call_ollama", lambda m, **kw: local(m, **kw))
    return claude, oai, local


# ── the ladder rule ───────────────────────────────────────────────────────────

def _legs(*names):
    return [(n, None, None) for n in names]


def test_by_default_the_other_cloud_leg_is_withheld():
    kept, withheld = pf.filter_ladder(_legs("cloud", "openai", "local"), {"mode": ""})
    assert [k[0] for k in kept] == ["cloud", "local"]
    assert [w[0] for w in withheld] == ["openai"]


def test_the_head_is_never_touched():
    kept, _ = pf.filter_ladder(_legs("openai", "cloud"), {})
    assert kept[0][0] == "openai" and [k[0] for k in kept] == ["openai"]


def test_the_owners_order_allows_only_its_own_direction():
    fwd = {"cloud_fallback_order": "anthropic_then_openai"}
    back = {"cloud_fallback_order": "openai_then_anthropic"}
    assert [k[0] for k in pf.filter_ladder(_legs("cloud", "openai"), fwd)[0]] == ["cloud", "openai"]
    assert [k[0] for k in pf.filter_ladder(_legs("cloud", "openai"), back)[0]] == ["cloud"]
    assert [k[0] for k in pf.filter_ladder(_legs("openai", "cloud"), back)[0]] == ["openai", "cloud"]


def test_an_unknown_order_means_ask():
    assert pf.stored_order({"cloud_fallback_order": "always_switch"}) == "ask"
    assert [k[0] for k in pf.filter_ladder(_legs("cloud", "openai"),
                                            {"cloud_fallback_order": "always_switch"})[0]] == ["cloud"]


def test_a_choice_for_this_call_allows_that_move_only():
    with pf.chosen("openai"):
        assert [k[0] for k in pf.filter_ladder(_legs("cloud", "openai"), {})[0]] == ["cloud", "openai"]
    assert [k[0] for k in pf.filter_ladder(_legs("cloud", "openai"), {})[0]] == ["cloud"]
    with pf.chosen("not-a-provider"):
        assert [k[0] for k in pf.filter_ladder(_legs("cloud", "openai"), {})[0]] == ["cloud"]


def test_local_legs_are_not_this_rules_business():
    kept, withheld = pf.filter_ladder(_legs("local", "cloud", "openai"), {})
    assert [k[0] for k in kept] == ["local", "cloud"] and [w[0] for w in withheld] == ["openai"]


# ── the text ladder (briefings, digest, editorials, scheduled work) ───────────

def test_an_anthropic_failure_is_said_and_offered_not_switched(monkeypatch, notices):
    claude, oai, _local = _text_ladder(monkeypatch, {"mode": "cloud_only"})
    with pytest.raises(pf.ProviderSwitchWithheld) as e:
        mr._generate_text_untraced(MSG)
    assert claude.calls >= 1
    assert oai.calls == 0, "the other cloud provider was called without the owner's say-so"
    offer = e.value.offer()
    assert offer["kind"] == "cloud_provider_offer" and offer["alternative"] == "openai"
    assert [c["id"] for c in offer["choices"]] == ["use_alternative", "retry_same"]
    assert not any(c.get("selected") for c in offer["choices"])
    assert notices and "did not switch" in notices[-1]["title"]


def test_the_owners_order_is_followed_and_still_announced(monkeypatch, notices):
    claude, oai, _local = _text_ladder(
        monkeypatch, {"mode": "cloud_only", "cloud_fallback_order": "anthropic_then_openai"})
    out = mr._generate_text_untraced(MSG)
    assert out == "from the other provider" and oai.calls == 1
    assert notices and "this answer came from" in notices[-1]["title"], "an allowed move is still visible"


def test_a_choice_for_this_call_is_followed(monkeypatch, notices):
    _claude, oai, _local = _text_ladder(monkeypatch, {"mode": "cloud_only"})
    with pf.chosen("openai"):
        assert mr._generate_text_untraced(MSG) == "from the other provider"
    assert oai.calls == 1


# ── the agent ladder (subagents, tasks, /api/chat/send) ───────────────────────

def test_the_agent_ladder_withholds_the_switch_too(monkeypatch, notices):
    import agent_friday.services.agent as agent_mod
    import agent_friday.routing.ollama_manager as om
    import agent_friday.services.demo_mode as dm

    class _NoOllama:
        base_url = "http://localhost:11434"

        def is_available(self):
            return False

        def list_models(self):
            return []

    monkeypatch.setattr(om, "get_manager", lambda *a, **k: _NoOllama())
    monkeypatch.setattr(dm, "is_demo", lambda *a, **k: False, raising=False)
    monkeypatch.setattr(agent_mod, "_load_settings", lambda: {"model_routing": {"mode": "cloud_only"}},
                        raising=False)
    monkeypatch.setattr(agent_mod, "get_anthropic_client", lambda *a, **k: object(), raising=False)
    cloud = _Leg(exc=RuntimeError("anthropic down"))
    oai = _Leg(result=("other provider", []))
    monkeypatch.setattr(agent_mod, "_call_claude_agent", cloud)
    monkeypatch.setattr(agent_mod, "_call_openai", oai)
    monkeypatch.setattr(agent_mod, "_call_ollama", _Leg(exc=RuntimeError("no ollama")))
    with pytest.raises(pf.ProviderSwitchWithheld):
        agent_mod._generate_agent(MSG, system="sys")
    assert cloud.calls == 1 and oai.calls == 0


# ── the order is the owner's, set by a press ──────────────────────────────────

def test_a_plain_settings_write_cannot_set_the_order(tmp_path, monkeypatch):
    import agent_friday.core as core
    before = core._load_settings().get("model_routing", {}).get("cloud_fallback_order", "ask")
    core._save_settings({"model_routing": {"cloud_fallback_order": "anthropic_then_openai"}})
    after = core._load_settings().get("model_routing", {}).get("cloud_fallback_order", "ask")
    assert after == before
    core._save_settings({"model_routing": {"cloud_fallback_order": "anthropic_then_openai"}},
                        owner_fallback_order_change=True)
    try:
        assert core._load_settings()["model_routing"]["cloud_fallback_order"] == "anthropic_then_openai"
    finally:
        core._save_settings({"model_routing": {"cloud_fallback_order": "ask"}},
                            owner_fallback_order_change=True)


def test_the_default_is_ask():
    import agent_friday.core as core
    assert core.DEFAULT_SETTINGS["model_routing"]["cloud_fallback_order"] == "ask"


@pytest.mark.parametrize("exc,expected", [
    (RuntimeError("Error code: 529 - overloaded"), True),
    (RuntimeError("HTTP 429 Too Many Requests"), True),
    (TimeoutError("timed out"), False),
    (KeyError("tool_input"), False),
    (ValueError("list index out of range"), False),
])
def test_only_a_provider_failure_is_offered(exc, expected):
    assert pf.looks_like_provider_failure(exc) is expected


# ── security review round 1 ───────────────────────────────────────────────────

def test_with_no_anthropic_key_the_other_provider_is_configuration_not_a_switch(monkeypatch):
    from agent_friday.services import one_key
    monkeypatch.setattr(one_key, "anthropic_ready", lambda: False)
    kept, withheld = pf.filter_ladder(_legs("cloud", "openai"), {})
    assert [k[0] for k in kept] == ["cloud", "openai"] and not withheld


def test_a_guest_key_turn_never_moves_to_another_provider():
    kept, withheld = pf.filter_ladder(_legs("cloud", "openai"),
                                      {"cloud_fallback_order": "anthropic_then_openai"}, locked=True)
    assert [k[0] for k in kept] == ["cloud"] and [w[0] for w in withheld] == ["openai"]


def test_a_provider_marked_down_is_not_skipped_silently(monkeypatch, notices):
    """The owner's order allows the move, and health ordering makes it before Anthropic is
    even tried: it is still announced."""
    claude, oai, _local = _text_ladder(
        monkeypatch, {"mode": "cloud_only", "cloud_fallback_order": "anthropic_then_openai"})
    monkeypatch.setattr(mr, "_health_order", lambda attempts, *a, **k: sorted(
        attempts, key=lambda leg: 0 if leg[0] == "openai" else 1))
    assert mr._generate_text_untraced(MSG) == "from the other provider"
    assert claude.calls == 0 and notices and "this answer came from" in notices[-1]["title"]


def test_no_raw_error_text_reaches_the_offer_or_the_notice(monkeypatch, notices):
    _text_ladder(monkeypatch, {"mode": "cloud_only"})
    secretish = "Incorrect API key provided: sk-proj-abcd...wxyz at https://api.example.invalid/v1 req_123"
    monkeypatch.setattr(mr, "_call_claude", lambda m, **kw: (_ for _ in ()).throw(RuntimeError(secretish)))
    with pytest.raises(pf.ProviderSwitchWithheld) as e:
        mr._generate_text_untraced(MSG)
    shown = e.value.text() + str(e.value.offer()) + str(notices)
    for leak in ("sk-proj", "api.example.invalid", "req_123"):
        assert leak not in shown, leak


def test_only_sdk_or_status_failures_are_offered():
    class LocalUnavailable(Exception):
        pass
    assert not pf.looks_like_provider_failure(LocalUnavailable("connection refused"))
    assert not pf.looks_like_provider_failure(ConnectionError("tool fetch failed: connection reset"))
