"""Regression: "cloud only" must actually keep the turn off this machine.

Janet's laptop, 2026-08-26 — the first time Friday was installed by someone
who did not write her. She set the routing mode to cloud only and every turn
was still answered by a local model. It was not a save that failed; it was a
save nothing read.

The cause is the keyless safety net in `routes/chat.py`. It was written for a
machine that HAS an Anthropic key and might momentarily lose it:

    if (not _routed_local) and _provider == 'cloud' \
            and get_anthropic_client() is None:
        ... route to Ollama instead ...

Its comment justifies itself with "only triggers when the alternative is a
guaranteed failure, so it can't regress a working setup". That premise holds
for the author and fails for a new user: on a fresh install there is no
Anthropic key at all, so the net is not a net, it is the permanent route. And
it never consulted `model_routing.mode`, so no setting could switch it off.

Note the asymmetry this pins shut. The mirror-image case was already fixed —
`local_only` refuses to fall back to the cloud, loudly, with an explanation
("LOCAL ONLY MEANS LOCAL ONLY", chat.py). `cloud_only` had no such guard.
Same disease, opposite direction, and only the direction the author travels
had been treated.

  1. cloud_only + no key  → say so; Ollama NEVER called
  2. cloud_only + no key  → the message names the fix, not a file path
  3. smart + no key       → the safety net still works (no over-correction)
"""
from __future__ import annotations

import pytest

import agent_friday.routes.chat as chat_mod
from agent_friday.core import DEFAULT_SETTINGS

pytestmark = pytest.mark.real_provider_paths


class _FakeOllama:
    """A healthy local daemon with a model on disk — the tempting option."""

    base_url = "http://localhost:11434"

    def is_available(self):
        return True

    def list_models(self):
        return [{"name": "gemma3:4b", "size": 2_500_000_000}]


class _Recorder:
    def __init__(self, result=("local reply", [])):
        self.calls = 0
        self._result = result

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return self._result


def _settings(mode):
    s = dict(DEFAULT_SETTINGS)
    s["model_routing"] = dict(s.get("model_routing") or {})
    s["model_routing"]["mode"] = mode
    return s


def _arrange(monkeypatch, mode):
    """A fresh install: no Anthropic key, a healthy Ollama, `mode` selected."""
    monkeypatch.setattr(chat_mod, "_load_settings", lambda: _settings(mode),
                        raising=False)
    monkeypatch.setattr(chat_mod, "get_anthropic_client", lambda *a, **k: None,
                        raising=False)
    import agent_friday.routing.ollama_manager as ollama_manager
    monkeypatch.setattr(ollama_manager, "get_manager",
                        lambda *a, **k: _FakeOllama(), raising=False)
    import agent_friday.services.demo_mode as _dm
    monkeypatch.setattr(_dm, "is_demo", lambda *a, **k: False, raising=False)

    local = _Recorder()
    monkeypatch.setattr(chat_mod, "_call_ollama", local, raising=False)
    return local


class TestCloudOnlyIsHonoured:
    def test_cloud_only_never_silently_routes_to_a_local_model(
            self, client, monkeypatch):
        local = _arrange(monkeypatch, "cloud_only")

        resp = client.post("/api/chat", json={"message": "hello Friday"})

        assert resp.status_code == 200
        assert local.calls == 0, (
            "cloud_only was set and a local model answered anyway — this is "
            "the exact defect Janet reported"
        )
        assert resp.get_json().get("cloud_only_no_key") is True

    def test_the_refusal_tells_her_what_to_do(self, client, monkeypatch):
        _arrange(monkeypatch, "cloud_only")

        text = client.post(
            "/api/chat", json={"message": "hello Friday"}).get_json()["response"]

        low = text.lower()
        # It must name the action, not a file path or an env var. A message
        # that says ANTHROPIC_API_KEY or names settings.json is the same
        # "assumes its operator is its author" failure in message form.
        assert "settings" in low and "key" in low
        assert "cloud only" in low or "cloud-only" in low
        assert ".json" not in low
        assert "ANTHROPIC_API_KEY" not in text

    def test_smart_mode_still_falls_back_to_local(self, client, monkeypatch):
        """The net exists for a reason — do not remove it, scope it."""
        local = _arrange(monkeypatch, "smart")

        client.post("/api/chat", json={"message": "hello Friday"})

        assert local.calls == 1, (
            "scoping the safety net to non-cloud_only modes must not disable "
            "it for the modes that legitimately want a local fallback"
        )
