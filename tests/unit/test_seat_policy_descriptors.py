"""Seat locality follows registered transports, including custom local names."""
from types import SimpleNamespace

import pytest

from agent_friday.services import memory_proposals, provider_registry, seat_policy


def descriptor(name="bonsai2-local", **overrides):
    return {"name": name, "type": "openai-compatible", "classification": "local",
            "base_url": "http://127.0.0.1:8090/v1", "models": ["bonsai2:27b"],
            "auth": {"type": "none"},
            **overrides}


def registry(monkeypatch, *providers, origin="file"):
    by_name = {p["name"]: p for p in providers}
    monkeypatch.setattr(provider_registry, "get_provider_registry",
                        lambda: SimpleNamespace(get_provider=by_name.get,
                                                provider_origin=lambda name: origin if name in by_name else "unknown"))


def test_custom_local_descriptor_is_accepted_by_settings_and_runtime(monkeypatch):
    from agent_friday.routes.core_routes import _check_local_only_seats
    from agent_friday.services import model_router

    registry(monkeypatch, descriptor())
    settings = {"capability_routing": {"memory_manager": {
        "provider": "bonsai2-local", "model": "bonsai2:27b"}}}
    assert _check_local_only_seats(settings) is None
    calls = []

    def local_call(messages, **kwargs):
        from agent_friday.services.local_only_guard import is_active
        assert is_active(), "the memory corpus must stay local throughout dispatch"
        calls.append((messages, kwargs))
        return "[]", []

    monkeypatch.setattr(model_router, "_call_ollama", local_call)
    monkeypatch.setattr(model_router, "_call_openai", local_call)
    assert memory_proposals._ask_seat("synthetic memory", "bonsai2:27b", "bonsai2-local") == "[]"
    assert calls[0][1]["model"] == "bonsai2:27b"
    assert calls[0][1]["tools"] is None
    assert calls[0][1]["provider"]["name"] == "bonsai2-local"


@pytest.mark.parametrize("provider", [
    descriptor(base_url="https://8.8.8.8/v1"),
    descriptor(type="anthropic"),
    descriptor(classification="cloud"),
    descriptor(name="ollama-local", base_url="https://8.8.8.8/v1"),
    descriptor(type="ollama", classification="cloud"),
])
def test_descriptors_cannot_earn_locality_from_the_name_alone(monkeypatch, provider):
    registry(monkeypatch, provider)
    assert not seat_policy.is_local_provider_name(provider["name"])
    with pytest.raises(memory_proposals.SeatUnavailable):
        memory_proposals._ask_seat("synthetic memory", "bonsai2:27b", provider["name"])


def test_unknown_local_sounding_names_remain_unverified(monkeypatch):
    registry(monkeypatch)
    assert not seat_policy.is_local_provider_name("unknown-local")


@pytest.mark.parametrize("provider", ["local", "arbiter-local", "llama-cpp-local"])
def test_internal_local_aliases_without_descriptors_remain_supported(monkeypatch, provider):
    registry(monkeypatch)
    assert seat_policy.is_local_provider_name(provider)


def test_custom_ollama_descriptor_uses_existing_inferred_locality(monkeypatch):
    registry(monkeypatch, descriptor("custom-engine", type="ollama", classification=""))
    assert seat_policy.is_local_provider_name("custom-engine")


def test_case_sensitive_custom_provider_id_is_resolved(monkeypatch):
    registry(monkeypatch, descriptor("Bonsai-Local"))
    assert seat_policy.is_local_provider_name(" Bonsai-Local ")


@pytest.mark.parametrize("model", ["fixture:cloud", "fixture:8b-cloud", "fixture-cloud",
                                    "fixture:CLOUD"])
def test_cloud_relay_tags_are_refused_at_save_and_before_inference(monkeypatch, model):
    from agent_friday.routes.core_routes import _check_local_only_seats
    from agent_friday.services import model_router

    registry(monkeypatch)
    calls = []
    monkeypatch.setattr(model_router, "_call_ollama", lambda *a, **k: calls.append(1))
    settings = {"capability_routing": {"memory_manager": {
        "provider": "ollama-local", "model": model}}}
    assert _check_local_only_seats(settings)["error"] == "local_only_seat"
    with pytest.raises(memory_proposals.SeatUnavailable):
        memory_proposals._ask_seat("synthetic memory", model, "ollama-local")
    assert calls == []


def test_a_cloud_sounding_local_model_is_not_a_relay_tag(monkeypatch):
    registry(monkeypatch)
    settings = {"memory_manager": {"provider": "ollama-local", "model": "cloudy-llama:7b"}}
    assert seat_policy.local_only_violations(settings) == []


@pytest.mark.real_provider_paths
def test_pinned_openai_descriptor_bypasses_other_providers_of_the_same_model(monkeypatch):
    from agent_friday.services import model_router, residency_arbiter, model_seat_gate
    from agent_friday.routing import ollama_manager
    from agent_friday.services.local_only_guard import local_only

    chosen = descriptor()
    calls = []
    registry(monkeypatch, chosen)
    monkeypatch.setattr(model_router, "_load_settings", lambda: {})
    monkeypatch.setattr(model_router, "_call_openai", lambda *a, **kw:
                        (calls.append(kw) or "[]", []))
    def no_discovery(*a, **k):
        pytest.fail("an explicit provider must not be replaced by model-name discovery")
    monkeypatch.setattr(residency_arbiter, "owned_provider", no_discovery)
    monkeypatch.setattr(model_seat_gate, "_local_openai_descriptor", no_discovery)
    monkeypatch.setattr(ollama_manager, "get_manager", no_discovery)
    with local_only("Memory keeper"):
        text, _ = model_router._call_ollama(
            [{"role": "user", "content": "synthetic memory"}],
            model="bonsai2:27b", provider=chosen)
    assert text == "[]"
    assert calls[0]["provider"] is chosen
    assert calls[0]["pin_provider_endpoint"] is True


@pytest.mark.real_provider_paths
def test_pinned_ollama_descriptor_uses_its_own_daemon(monkeypatch):
    from agent_friday.services import agent, model_router, pause_forecast, residency_arbiter
    from agent_friday.routing import ollama_manager
    from agent_friday.services.local_only_guard import local_only

    chosen = descriptor("custom-ollama", type="ollama", base_url="http://127.0.0.1:11500")
    registry(monkeypatch, chosen)
    calls = []
    class Manager:
        def __init__(self, base_url):
            self.base_url = base_url
        def is_available(self):
            return True
        def chat_completion(self, messages, **kwargs):
            calls.append((self.base_url, kwargs))
            return {"choices": [{"message": {"content": "[]"}}]}
    def no_default(*a, **k):
        pytest.fail("the pinned daemon must not use default manager or owned-provider discovery")
    monkeypatch.setattr(ollama_manager, "get_manager", no_default)
    monkeypatch.setattr(ollama_manager, "OllamaManager", Manager)
    monkeypatch.setattr(residency_arbiter, "owned_provider", no_default)
    monkeypatch.setattr(model_router, "_load_settings", lambda: {})
    monkeypatch.setattr(model_router, "_served_ctx", lambda _: 4096)
    monkeypatch.setattr(pause_forecast, "_load_estimate", lambda *a: (0, None))
    def loop(messages, tools, send, **kw):
        response = send(messages, tools)
        return response["choices"][0]["message"]["content"], []
    monkeypatch.setattr(agent, "_oai_agentic_loop", loop)
    with local_only("Memory keeper"):
        text, _ = model_router._call_ollama(
            [{"role": "user", "content": "synthetic memory"}],
            model="bonsai2:27b", provider=chosen)
    assert text == "[]"
    assert calls[0][0] == "http://127.0.0.1:11500"
    assert calls[0][1]["model"] == "bonsai2:27b"


@pytest.mark.real_provider_paths
@pytest.mark.parametrize("alias", ["ollama-local", "OLLAMA-LOCAL"])
def test_builtin_ollama_alias_preserves_configured_daemon_discovery(monkeypatch, alias):
    from agent_friday.services import model_router
    from agent_friday.routing import ollama_manager

    registry(monkeypatch, descriptor("ollama-local", type="ollama",
                                    base_url="http://localhost:11434"), origin="builtin")
    monkeypatch.setattr(model_router, "_load_settings", lambda: {
        "model_routing": {"ollama_url": "http://127.0.0.1:11500"}})
    seen = []
    def configured_manager(url):
        seen.append(url)
        raise RuntimeError("fixture ends before inference")
    monkeypatch.setattr(ollama_manager, "get_manager", configured_manager)
    with pytest.raises(memory_proposals.SeatUnavailable):
        memory_proposals._ask_seat("synthetic memory", "bonsai2:27b", alias)
    assert seen == ["http://127.0.0.1:11500"]


@pytest.mark.real_provider_paths
def test_explicit_openai_endpoint_is_not_replaced_by_another_seat(monkeypatch):
    import requests
    from agent_friday.services import agent, model_router, local_call
    from agent_friday.services.local_only_guard import local_only

    chosen = descriptor()
    registry(monkeypatch, chosen)
    monkeypatch.setattr(model_router, "_load_settings", lambda: {})
    monkeypatch.setattr(model_router, "_served_ctx", lambda _: 4096)
    monkeypatch.setattr(local_call, "seat_endpoint", lambda _: "http://127.0.0.1:8099/v1")
    sent = []
    class Response:
        status_code = 200
        headers = {"Content-Type": "application/json"}
        def raise_for_status(self):
            pass
        def json(self):
            return {"choices": [{"message": {"content": "[]"}}]}
    def post(url, **kw):
        sent.append(url)
        return Response()
    monkeypatch.setattr(requests, "post", post)
    def loop(messages, tools, send, **kw):
        response = send(messages, tools)
        return response["choices"][0]["message"]["content"], []
    monkeypatch.setattr(agent, "_oai_agentic_loop", loop)
    with local_only("Memory keeper"):
        text, _ = model_router._call_ollama(
            [{"role": "user", "content": "synthetic memory"}],
            model="bonsai2:27b", provider=chosen)
    assert text == "[]"
    assert sent == ["http://127.0.0.1:8090/v1/chat/completions"]
