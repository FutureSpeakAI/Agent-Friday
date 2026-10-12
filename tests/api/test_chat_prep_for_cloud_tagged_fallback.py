"""The chat route builds a prompt as CLOUD for the model it is preparing it for.

``routes/chat.py::_prep_for`` classifies a prompt by the model it is built for.
The keyless safety net picks a local Ollama model and prepares the prompt for it;
when the only model the daemon lists is a cloud-tagged one (``glm-4.6:cloud``),
that prompt must be built as cloud (vault tiers withheld, PII scrubbed), by the
model passed to ``_prep_for`` and not by whatever the route record happens to
hold. And ``_prep_for``'s docstring must be its docstring: code above it made
it a dead string.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import agent_friday.routes.chat as chat_mod
from agent_friday.core import DEFAULT_SETTINGS

CHAT_PY = Path(chat_mod.__file__)


class _FakeOllama:
    base_url = "http://localhost:11434"

    def is_available(self):
        return True

    def list_models(self):
        return [{"name": "glm-4.6:cloud", "size": 1}]


def _prep_for_node():
    tree = ast.parse(CHAT_PY.read_text(encoding="utf-8-sig"))
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "_prep_for")


def test_prep_for_keeps_its_docstring():
    doc = ast.get_docstring(_prep_for_node())
    assert doc and doc.startswith("The system prompt for `provider`")


def test_the_safety_net_prepares_for_the_model_it_picked():
    calls = [n for n in ast.walk(ast.parse(CHAT_PY.read_text(encoding="utf-8-sig")))
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_prep_for"
             and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value == "local"]
    assert calls, "expected the local fallback to prepare a local prompt"
    assert all(any(k.arg == "model" for k in c.keywords) for c in calls)


def _keyless_turn(client, monkeypatch, mode):
    """One keyless /api/chat turn in `mode` with only a cloud tag in Ollama:
    the (provider, model) of every prompt the route prepared."""
    s = dict(DEFAULT_SETTINGS)
    s["model_routing"] = dict(s.get("model_routing") or {})
    s["model_routing"]["mode"] = mode
    monkeypatch.setattr(chat_mod, "_load_settings", lambda: s, raising=False)
    monkeypatch.setattr(chat_mod, "get_anthropic_client", lambda *a, **k: None, raising=False)
    import agent_friday.routing.ollama_manager as ollama_manager
    monkeypatch.setattr(ollama_manager, "get_manager", lambda *a, **k: _FakeOllama(), raising=False)
    import agent_friday.services.demo_mode as _dm
    monkeypatch.setattr(_dm, "is_demo", lambda *a, **k: False, raising=False)
    monkeypatch.setattr(chat_mod, "_call_ollama", lambda *a, **k: ("reply", []), raising=False)

    seen = []

    def build(message, workspace, vision, *a, **kw):
        seen.append((kw.get("provider"), kw.get("model")))
        return "SYSTEM", []
    monkeypatch.setattr(chat_mod, "_build_context_prompt", build)

    client.post("/api/chat", json={"message": "hello Friday"})
    return seen


def test_a_cloud_tagged_local_fallback_is_prepared_as_cloud(client, monkeypatch):
    # A cloud mode: the cloud is already open, so with no key and no real local
    # model the safety net may take the cloud tag, and it is prepared AS CLOUD.
    seen = _keyless_turn(client, monkeypatch, "cloud_preferred")
    fallback = [c for c in seen if c[1] == "glm-4.6:cloud"]
    assert fallback, "the fallback never prepared a prompt for the cloud-tagged model: %r" % (seen,)
    assert all(p == "cloud" for p, _m in fallback), seen


@pytest.mark.parametrize("mode", ["smart", "local_preferred", "local_only"])
def test_a_local_mode_never_falls_back_to_a_cloud_tag(client, monkeypatch, mode):
    # A local mode with no recorded intent: a cloud tag is relayed off this PC,
    # so it is never the keyless fallback (cloud_intent), not even prepared.
    seen = _keyless_turn(client, monkeypatch, mode)
    assert not [c for c in seen if c[1] == "glm-4.6:cloud"], seen
