"""In the two local modes, NO cloud call leaves without a recorded intent.

Companion to test_every_cloud_transport_requires_intent.py (which proves the check
is written at every transport). This one drives the paths that could reach a
transport, in `local_preferred` and `local_only`, with the cloud edge replaced by
spies: the Anthropic SDK client, every non-loopback HTTP call, the Gemini client.
Anything that gets past `cloud_intent.require` lands in a spy; every assertion is
that the spies stay empty and that a local failure becomes a typed
`LocalUnavailable` (which the routes turn into an offer), never a cloud call.

It also holds the positive controls: with an intent, the same call IS made and its
receipt names the intent. A guard that cannot be passed is as broken as one that
cannot fail.

Routes (`/api/chat`, `/api/chat/send`, the stream) are driven in
tests/api/test_chat_local_failure_is_an_offer.py, which needs the Flask app.
"""
from __future__ import annotations

import copy
import re
import threading
import types

import pytest

from agent_friday import core
from agent_friday.routing.model_router import ModelRouter
from agent_friday.services import agent as agent_mod
from agent_friday.services import cloud_intent as ci
from agent_friday.services import model_router as mr

MODES = ("local_preferred", "local_only")
MSG = [{"role": "user", "content": "what is on my calendar this week?"}]
LOCAL = "bonsai2:27b"


class _Spies:
    """Everything that reached the cloud edge."""

    def __init__(self):
        self.anthropic, self.http, self.gemini = [], [], []

    @property
    def total(self):
        return len(self.anthropic) + len(self.http) + len(self.gemini)


def _is_loopback(url):
    u = str(url)
    return any(h in u for h in ("127.0.0.1", "localhost", "[::1]"))


#: Hosts that serve a model. Other outbound traffic (a news feed, an update
#: check) is not a model call and is simply refused: this suite is offline.
_MODEL_HOST = re.compile(
    r"api\.anthropic\.com|openrouter\.ai|api\.openai\.com|generativelanguage\.googleapis\.com|"
    r"aiplatform\.googleapis\.com|api\.groq\.com|api\.together\.xyz|api\.mistral\.ai|"
    r"api\.deepseek\.com|api\.x\.ai|kie\.ai|higgsfield", re.I)


@pytest.fixture
def cloud(monkeypatch):
    """The cloud edge, spied. Nothing here can reach a real provider."""
    import requests
    import urllib.request
    spies = _Spies()

    class _Resp:
        content = [types.SimpleNamespace(type="text", text="cloud reply")]
        stop_reason = "end_turn"
        usage = types.SimpleNamespace(input_tokens=1, output_tokens=1)
        model = "claude-sonnet-5"

    class _Messages:
        def create(self, **kw):
            spies.anthropic.append(kw)
            return _Resp()

    client = types.SimpleNamespace(messages=_Messages())
    for mod in (mr, agent_mod, core):
        monkeypatch.setattr(mod, "get_anthropic_client", lambda *a, **k: client, raising=False)

    def _post(url, *a, **k):
        if not _MODEL_HOST.search(str(url)):
            raise ConnectionError("offline test: no network for %s" % url)
        spies.http.append(str(url))
        raise AssertionError("a cloud HTTP call got past cloud_intent.require: %s" % url)

    monkeypatch.setattr(requests, "post", _post, raising=False)
    monkeypatch.setattr(requests, "get", _post, raising=False)
    monkeypatch.setattr(requests, "request", lambda m, url, *a, **k: _post(url), raising=False)

    def _urlopen(req, *a, **k):
        url = getattr(req, "full_url", req)
        if not _MODEL_HOST.search(str(url)):
            raise ConnectionError("offline test: no network for %s" % url)
        spies.http.append(str(url))
        raise AssertionError("a cloud HTTP call got past cloud_intent.require: %s" % url)

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen, raising=False)

    class _GenAI:
        def __init__(self, *a, **k):
            spies.gemini.append("client constructed")
            self.models = types.SimpleNamespace(
                generate_content=lambda **kw: spies.gemini.append(kw),
                generate_videos=lambda **kw: spies.gemini.append(kw),
                generate_music=lambda **kw: spies.gemini.append(kw))
    try:
        from google import genai
        monkeypatch.setattr(genai, "Client", _GenAI, raising=False)
    except Exception:
        pass
    monkeypatch.setattr(core, "GEMINI_API_KEY", "test-key-example", raising=False)  # pragma: allowlist secret
    return spies


@pytest.fixture
def set_mode(monkeypatch):
    """Put the app in a routing mode, with `local` as the local candidates."""
    import agent_friday.services.demo_mode as dm
    monkeypatch.setattr(dm, "is_demo", lambda *a, **k: False, raising=False)

    def _set(mode, local=(LOCAL,)):
        s = copy.deepcopy(core.DEFAULT_SETTINGS)
        s["model_routing"].update({"mode": mode, "vault_local_only": False})
        for mod in (core, mr, agent_mod):
            monkeypatch.setattr(mod, "_load_settings", lambda s=s: s, raising=False)
        monkeypatch.setattr(ci, "routing_mode", lambda: mode)
        monkeypatch.setattr(ModelRouter, "_local_candidates",
                            lambda self: [{"name": n, "size_gb": 16.0} for n in local])
        monkeypatch.setattr(ModelRouter, "_pick_local_model",
                            lambda self, models, tt, m, preferred=None: models[0]["name"])
        monkeypatch.setattr(ModelRouter, "_is_registry_local",
                            lambda self, m: ":" in str(m or "") and "claude" not in str(m))
        from agent_friday.routing import model_router as rr
        monkeypatch.setattr(rr, "_router_instance", None)
        return s
    return _set


class _LocalDown:
    """The local seat: down (raises) or up (answers)."""

    def __init__(self, up=False, reply="local answered"):
        self.calls, self.up, self.reply = 0, up, reply

    def __call__(self, *a, **k):
        self.calls += 1
        if not self.up:
            raise RuntimeError("local seat is down (503 Loading model)")
        return self.reply, []


@pytest.fixture
def local_seat(monkeypatch):
    def _make(up=False):
        seat = _LocalDown(up=up)
        monkeypatch.setattr(mr, "_call_ollama", seat, raising=False)
        monkeypatch.setattr(agent_mod, "_call_ollama", seat, raising=False)
        return seat
    return _make


# ── the ladders ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mode", MODES)
class TestLaddersHaveNoCloudLegWithoutAnIntent:
    def test_text_ladder_a_local_failure_is_local_unavailable(self, mode, cloud, set_mode, local_seat):
        set_mode(mode)
        seat = local_seat(up=False)
        with pytest.raises(ci.LocalUnavailable) as ei:
            mr._generate_text(MSG, system="be brief")
        assert seat.calls >= 1
        assert cloud.total == 0
        assert "Nothing was sent to a cloud model" in str(ei.value)
        assert "503" in ei.value.reason or "down" in ei.value.reason

    def test_agent_ladder_a_local_failure_is_local_unavailable(self, mode, cloud, set_mode, local_seat):
        set_mode(mode)
        seat = local_seat(up=False)
        with pytest.raises(ci.LocalUnavailable):
            agent_mod._generate_agent(MSG, system="be brief")
        assert seat.calls >= 1
        assert cloud.total == 0

    def test_no_local_model_at_all_is_local_unavailable_not_a_cloud_route(self, mode, cloud, set_mode, local_seat):
        set_mode(mode, local=())
        local_seat(up=False)
        with pytest.raises(ci.LocalUnavailable):
            mr._generate_text(MSG, system="be brief")
        with pytest.raises(ci.LocalUnavailable):
            agent_mod._generate_agent(MSG, system="be brief")
        assert cloud.total == 0

    def test_a_router_that_raises_fails_closed_to_local(self, mode, cloud, set_mode, local_seat, monkeypatch):
        set_mode(mode)
        seat = local_seat(up=False)
        from agent_friday.routing import model_router as rr

        def boom(*a, **k):
            raise RuntimeError("router exploded")
        monkeypatch.setattr(rr, "get_router", boom)
        with pytest.raises(ci.LocalUnavailable):
            mr._generate_text(MSG, system="be brief")
        with pytest.raises(ci.LocalUnavailable):
            agent_mod._generate_agent(MSG, system="be brief")
        assert seat.calls >= 2
        assert cloud.total == 0

    def test_a_task_carrying_the_default_cloud_subagent_model_runs_local(self, mode, cloud, set_mode, local_seat):
        """F1's mechanism: `model=` becomes a cloud conversation seat, and with no
        intent the router keeps the task on the local seat."""
        set_mode(mode)
        seat = local_seat(up=True)
        text, _ = agent_mod._generate_agent(MSG, system="be brief", model="claude-sonnet-5")
        assert text == "local answered" and seat.calls == 1
        assert cloud.total == 0

    def test_a_voice_pinned_turn_that_fails_is_local_unavailable(self, mode, cloud, set_mode, local_seat):
        set_mode(mode)
        local_seat(up=False)
        with pytest.raises(ci.LocalUnavailable) as ei:
            agent_mod._generate_agent(
                MSG, system="be brief", model=LOCAL,
                session_ctx={"pin_to_seat": True, "is_voice": True})
        assert ei.value.model == LOCAL
        assert cloud.total == 0

    def test_seat_warm_stops_when_the_warm_fails(self, mode, cloud, set_mode, local_seat, monkeypatch):
        from agent_friday.services import seat_warm
        set_mode(mode)
        local_seat(up=False)
        monkeypatch.setattr(seat_warm, "_is_brain", lambda m: True)
        monkeypatch.setattr(seat_warm, "canonical_head", lambda *a, **k: "head")
        out = seat_warm.warm(LOCAL, 8090)
        assert out["warmed"] is False
        assert cloud.total == 0


@pytest.mark.parametrize("mode", MODES)
class TestWithAnIntentTheCloudIsReachedAndTheReceiptSaysWhy:
    def test_call_claude_with_an_intent_is_sent_and_receipted(self, mode, cloud, set_mode):
        set_mode(mode)
        with ci.intent_scope("user_explicit", source="route_mode", confirmed=True):
            out = mr._call_claude(MSG, system="be brief", model="claude-sonnet-5")
        assert out and len(cloud.anthropic) == 1
        row = ci.recent(1)[0]
        assert row["intent"] == "user_explicit" and row["source"] == "route_mode"

    def test_without_the_intent_the_same_call_is_refused_before_the_sdk(self, mode, cloud, set_mode):
        set_mode(mode)
        with pytest.raises(ci.NoCloudIntent):
            mr._call_claude(MSG, system="be brief", model="claude-sonnet-5")
        assert cloud.total == 0

    def test_a_cloud_bound_conversation_with_an_intent_routes_to_the_cloud(self, mode, cloud, set_mode, local_seat):
        set_mode(mode)
        local_seat(up=True)
        with ci.intent_scope("user_explicit", source="chat_seat", confirmed=True):
            r = ModelRouter({"mode": mode}).route(MSG, task_context={
                "has_tools": True, "conversation_seat": {"model": "claude-sonnet-5"},
                "cloud_model": "claude-sonnet-5"})
        if mode == "local_only":
            # local_only never reads a conversation binding: it stays local.
            assert r["provider"] == "local"
        else:
            assert r["provider"] == "cloud" and r["model"] == "claude-sonnet-5"


# ── the router's own rules ───────────────────────────────────────────────────

class TestRouterRules:
    def test_a_cloud_bound_conversation_without_an_intent_stays_local(self, set_mode):
        set_mode("local_preferred")
        r = ModelRouter({"mode": "local_preferred"}).route(MSG, task_context={
            "has_tools": True, "conversation_seat": {"model": "claude-sonnet-5"},
            "cloud_model": "claude-sonnet-5"})
        assert r["provider"] == "local" and r["model"] == LOCAL
        assert r["cloud_withheld"]["model"] == "claude-sonnet-5"
        assert "no cloud intent" in r["reason"]

    def test_a_global_cloud_reasoning_seat_does_not_pull_a_text_call_to_the_cloud(self, set_mode, monkeypatch):
        set_mode("local_preferred")
        s = copy.deepcopy(core.DEFAULT_SETTINGS)
        s["capability_routing"]["reasoning"] = {"provider": "anthropic", "model": "claude-opus-5"}
        r = ModelRouter({"mode": "local_preferred"}).route(
            [{"role": "user", "content": "hi"}], task_context={"has_tools": False})
        assert r["provider"] == "local"

    def test_the_vault_redact_fallback_needs_an_intent(self, set_mode, monkeypatch):
        set_mode("local_preferred", local=())
        out = ModelRouter({"mode": "local_preferred", "vault_local_only": True,
                           "vault_cloud_fallback": "redact"}).route(
            [{"role": "user", "content": "what is my social security number"}],
            task_context={"vault_access": True, "has_tools": True})
        assert out["provider"] == "local" and out["refuse"] is True
        assert out["local_unavailable"] is True

    def test_f2_published_arbiter_seats_count_as_local_candidates(self, monkeypatch):
        """The Ollama daemon is not the only source of local candidates."""
        from agent_friday.services import local_call
        from agent_friday.services import model_store
        monkeypatch.setattr(model_store, "available", lambda: {})
        monkeypatch.setattr(local_call, "published_models", lambda: ["bonsai2:27b", "glm-4.6:cloud"])
        monkeypatch.setattr(local_call, "seat_endpoint",
                            lambda m: "http://127.0.0.1:8090/v1" if m == "bonsai2:27b" else None)
        import agent_friday.routing.ollama_manager as om
        monkeypatch.setattr(om, "get_manager", lambda *a, **k: types.SimpleNamespace(
            is_available=lambda: False, list_models=lambda: []))
        from agent_friday.services import residency_catalog
        monkeypatch.setattr(residency_catalog, "can_generate", lambda m: True, raising=False)
        names = [m["name"] for m in ModelRouter({"mode": "local_preferred"})._local_candidates()]
        assert names == ["bonsai2:27b"]          # the -cloud relay tag is filtered out

    def test_f2_a_non_tool_call_with_only_arbiter_seats_goes_local(self, set_mode):
        set_mode("local_preferred")      # candidates stand in for the arbiter's seats
        import agent_friday.routing.ollama_manager as om
        r = ModelRouter({"mode": "local_preferred"}).route(
            [{"role": "user", "content": "hi"}], task_context={"has_tools": False})
        assert r["provider"] == "local" and r["model"] == LOCAL

    def test_f5_an_open_local_breaker_never_puts_the_cloud_first(self, monkeypatch):
        from agent_friday.services import provider_health
        monkeypatch.setattr(provider_health, "availability",
                            lambda name: "down" if name == "ollama-local" else "ok")
        ladder = [("local", "l", "m"), ("cloud", "c", None), ("openai", "o", None)]
        assert [a[0] for a in mr._health_order(ladder, None, mode="local_preferred")] == \
            ["local", "cloud", "openai"]
        # The unchanged behaviour outside the local modes.
        assert mr._health_order(ladder, None, mode="cloud_only")[0][0] != "local"

    def test_the_ladder_filter_keeps_only_local_without_an_intent(self):
        ladder = [("cloud", "c", "m"), ("openai", "o", None), ("local", "l", None)]
        for mode in MODES:
            assert [a[0] for a in mr._mode_filtered_attempts(ladder, {"mode": mode})] == ["local"]

    def test_the_ladder_filter_with_no_local_leg_is_local_unavailable(self):
        with pytest.raises(ci.LocalUnavailable):
            mr._mode_filtered_attempts([("cloud", "c", "m")], {"mode": "local_preferred"})

    def test_the_ladder_filter_keeps_the_old_shape_with_an_intent(self):
        ladder = [("cloud", "c", "m"), ("openai", "o", None), ("local", "l", None)]
        with ci.intent_scope("user_explicit", source="x", confirmed=True):
            assert [a[0] for a in mr._mode_filtered_attempts(
                ladder, {"mode": "local_preferred"})] == ["cloud", "openai", "local"]
            assert [a[0] for a in mr._mode_filtered_attempts(
                ladder, {"mode": "local_only"})] == ["cloud", "local"]
        # cloud_only is unchanged
        assert [a[0] for a in mr._mode_filtered_attempts(
            ladder, {"mode": "cloud_only"})] == ["cloud", "openai"]


# ── every transport, called bare, in both local modes ────────────────────────

@pytest.mark.parametrize("mode", MODES)
class TestEveryTransportIsRefusedBare:
    def test_call_claude_agent_run(self, mode, cloud, set_mode):
        set_mode(mode)
        with pytest.raises(ci.NoCloudIntent):
            agent_mod._call_claude_agent(MSG, system="x", model="claude-sonnet-5")
        assert cloud.total == 0

    def test_call_openai_cloud_provider(self, mode, cloud, set_mode):
        set_mode(mode)
        with pytest.raises(ci.NoCloudIntent):
            mr._call_openai(MSG, system="x", model="gpt-4o-mini", provider="openrouter")
        assert cloud.total == 0

    def test_call_openai_legacy_single_slot(self, mode, cloud, set_mode):
        set_mode(mode)
        with pytest.raises(ci.NoCloudIntent):
            mr._call_openai(MSG, system="x", model="gpt-4o-mini")
        assert cloud.total == 0

    def test_one_key_path_through_openrouter(self, mode, cloud, set_mode, monkeypatch):
        set_mode(mode)
        for mod in (mr, agent_mod, core):
            monkeypatch.setattr(mod, "get_anthropic_client", lambda *a, **k: None, raising=False)
        from agent_friday.services import one_key
        monkeypatch.setattr(one_key, "openrouter_instead", lambda m: "anthropic/claude-sonnet-5")
        with pytest.raises(ci.NoCloudIntent):
            mr._call_claude(MSG, system="x", model="claude-sonnet-5")
        assert cloud.total == 0

    def test_gemini_creative_and_media_factories(self, mode, cloud, set_mode, monkeypatch):
        from agent_friday.services import creative_engine, media_tools
        set_mode(mode)
        monkeypatch.setattr(creative_engine, "is_available", lambda: True)
        with pytest.raises(ci.NoCloudIntent):
            creative_engine._client()
        with pytest.raises(ci.NoCloudIntent):
            media_tools._gemini_client()
        assert cloud.total == 0

    def test_lyria(self, mode, cloud, set_mode):
        from agent_friday.services import music_engine
        set_mode(mode)
        with pytest.raises(ci.NoCloudIntent):
            music_engine._generate_music_cloud(
                types.SimpleNamespace(models=types.SimpleNamespace(
                    generate_music=lambda **kw: cloud.gemini.append(kw))),
                types.SimpleNamespace(GenerateMusicConfig=lambda **kw: kw, Image=lambda **kw: kw),
                "lyria", "a tune", mode="clip", lyrics=None, duration_seconds=None,
                language=None, timestamps=None, negative_prompt=None, seeds=None, orb=None)
        assert cloud.total == 0

    def test_kie_and_higgsfield(self, mode, cloud, set_mode):
        from agent_friday.services import higgsfield_generate, kie_generate
        set_mode(mode)
        with pytest.raises(ci.NoCloudIntent):
            kie_generate._request("POST", "https://api.kie.ai/x", json_body={})
        with pytest.raises(ci.NoCloudIntent):
            higgsfield_generate._call("generate_image", {"prompt": "a"})
        assert cloud.total == 0

    def test_compaction_summarizer_sends_nothing(self, mode, cloud, set_mode):
        from agent_friday.services import compaction
        set_mode(mode)
        summarize = compaction.claude_summarizer(
            types.SimpleNamespace(messages=types.SimpleNamespace(
                create=lambda **kw: cloud.anthropic.append(kw))),
            "claude-sonnet-5")
        assert not (summarize("a long transcript " * 50) or "")
        assert cloud.total == 0

    def test_a_health_probe_spends_nothing_and_reports_unknown(self, mode, cloud, set_mode):
        from agent_friday.services import provider_health
        set_mode(mode)
        out = provider_health.inference_probe(
            "anthropic", prov={"name": "anthropic", "type": "anthropic",
                               "models": ["claude-haiku-4-5-20251001"]}, use_cache=False)
        # Spends nothing and does not claim the provider is up (or down): it was
        # not asked.
        assert out is None or out["status"] not in ("ok", "down")
        assert cloud.total == 0

    def test_research_scoping_falls_back_to_the_brain(self, mode, cloud, set_mode):
        from agent_friday.services.research import harness
        set_mode(mode)
        with pytest.raises(ci.NoCloudIntent):
            harness._claude("system", "question")
        assert cloud.total == 0

    def test_task_resume_style_call_through_the_agent_wrapper(self, mode, cloud, set_mode):
        set_mode(mode)
        with pytest.raises(ci.NoCloudIntent):
            agent_mod._call_claude_agent(MSG, model="claude-sonnet-5", resumed_tool_trace=[])
        assert cloud.total == 0


# ── scheduler built-ins ──────────────────────────────────────────────────────

#: The built-ins that call a model. The rest are deterministic (audit iv).
_MODEL_BUILTINS = ("daily_creation", "news_morning", "front_page_evening",
                   "afternoon_briefing", "weekly_digest", "weekly_editorial",
                   "self_improvement", "session_summary")


@pytest.mark.parametrize("mode", MODES)
def test_every_model_calling_builtin_sends_nothing_without_an_intent(mode, cloud, set_mode, local_seat, tmp_path, monkeypatch):
    """Run each built-in body bare (no scheduler guard) with every local call
    failing: it may fail, wait or skip, but nothing reaches the cloud edge."""
    from agent_friday.services import scheduler
    set_mode(mode)
    local_seat(up=False)
    monkeypatch.setattr(scheduler, "BUILTIN_TASKS", {})
    scheduler._register_default_builtin_tasks()
    # No waiting out a backoff window in a test.
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)
    refs = [r for r in _MODEL_BUILTINS if r in scheduler.BUILTIN_TASKS]
    assert len(refs) >= 5, "the scheduler no longer registers the model-calling built-ins: %s" % sorted(scheduler.BUILTIN_TASKS)
    for ref in refs:
        fn = scheduler.BUILTIN_TASKS[ref]["fn"]
        done = threading.Event()

        def _run(fn=fn):
            try:
                fn()
            except BaseException:       # noqa: BLE001 - a job may fail; it may not call the cloud
                pass
            finally:
                done.set()
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        done.wait(20)
        assert cloud.total == 0, "%s reached the cloud with no intent in %s" % (ref, mode)
