"""An Ollama "-cloud" model is a cloud call, whatever daemon relays it.

Ollama serves tags such as `gpt-oss:120b-cloud` and `glm-4.6:cloud` by
forwarding the request to ollama.com. The local-only guard judged by provider
name, and the provider is the local daemon, so a local-only run could reach
the cloud through it; the Ollama transport never consulted the guard at all.
"""
from __future__ import annotations

import pytest

from agent_friday.services import local_only_guard as g


@pytest.mark.parametrize("tag", ["gpt-oss:120b-cloud", "glm-4.6:cloud", "qwen3-coder:480b-cloud",
                                 "GPT-OSS:20B-CLOUD", "deepseek-v3.1-cloud"])
def test_cloud_tags_are_cloud(tag):
    assert g.is_cloud_model_tag(tag) is True


@pytest.mark.parametrize("tag", ["bonsai2:27b", "gemma4:e4b", "qwen3:4b", "cloudy-llama:7b", ""])
def test_local_tags_are_not(tag):
    assert g.is_cloud_model_tag(tag) is False


def test_a_local_provider_serving_a_cloud_tag_is_refused():
    with g.local_only("Nightly digest"):
        with pytest.raises(g.CloudRefused):
            g.refuse_if_active("ollama-local", "gpt-oss:120b-cloud")
        g.refuse_if_active("ollama-local", "bonsai2:27b")      # a real local model is fine


def test_the_ollama_transport_consults_the_guard(monkeypatch):
    from agent_friday.services import model_router as mr
    from agent_friday.routing import ollama_manager as om

    class NoDaemon:
        def __getattr__(self, name):
            raise AssertionError("reached the Ollama daemon (%s)" % name)
    monkeypatch.setattr(om, "get_manager", lambda *a, **k: NoDaemon())
    with g.local_only("Nightly digest"):
        with pytest.raises(g.CloudRefused):
            mr._call_ollama([{"role": "user", "content": "hi"}], model="gpt-oss:120b-cloud")


def test_a_configured_default_cloud_tag_is_refused_too(monkeypatch):
    from agent_friday.services import model_router as mr
    from agent_friday.routing import ollama_manager as om

    class NoDaemon:
        def __getattr__(self, name):
            raise AssertionError("reached the Ollama daemon (%s)" % name)
    monkeypatch.setattr(om, "get_manager", lambda *a, **k: NoDaemon())
    monkeypatch.setattr(mr, "_load_settings",
                        lambda: {"model_routing": {"local_model": "glm-4.6:cloud"}})
    with g.local_only("Nightly digest"):
        with pytest.raises(g.CloudRefused):
            mr._call_ollama([{"role": "user", "content": "hi"}])
