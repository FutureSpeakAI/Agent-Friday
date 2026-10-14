"""The keyless safety net never takes a cloud-tagged Ollama model.

``routes/chat.py``'s keyless safety net picks an on-device Ollama model when
the router chose the cloud and there is no key. A cloud tag (``glm-4.6:cloud``)
is relayed to ollama.com, so it is never that model, in any routing mode: the
cloud path is an explicit offer or the normal cloud route. Should a cloud tag
ever be prepared or answer, its prompt is built AS CLOUD (vault withheld, PII
scrubbed) by the model passed to ``_prep_for``, and the response never labels
it local. And ``_prep_for``'s docstring must be its docstring: code above it
made it a dead string.
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


CLOUD_TAG = "glm-4.6:cloud"
MODES = ["smart", "local_preferred", "local_only", "cloud_preferred", "cloud_only"]


def _keyless_turn(client, monkeypatch, mode):
    """One keyless /api/chat turn in `mode` with only a cloud tag in Ollama.

    Returns (prepared, ollama_models, body): the (provider, model) of every
    prompt the route prepared, the model of every _call_ollama, and the JSON."""
    s = dict(DEFAULT_SETTINGS)
    s["model_routing"] = dict(s.get("model_routing") or {})
    s["model_routing"]["mode"] = mode
    monkeypatch.setattr(chat_mod, "_load_settings", lambda: s, raising=False)
    monkeypatch.setattr(chat_mod, "get_anthropic_client", lambda *a, **k: None, raising=False)
    import agent_friday.routing.ollama_manager as ollama_manager
    monkeypatch.setattr(ollama_manager, "get_manager", lambda *a, **k: _FakeOllama(), raising=False)
    import agent_friday.services.demo_mode as _dm
    monkeypatch.setattr(_dm, "is_demo", lambda *a, **k: False, raising=False)

    called = []

    def ollama(*a, **k):
        called.append(k.get("model"))
        return "reply", []
    monkeypatch.setattr(chat_mod, "_call_ollama", ollama, raising=False)

    prepared = []

    def build(message, workspace, vision, *a, **kw):
        prepared.append((kw.get("provider"), kw.get("model")))
        return "SYSTEM", []
    monkeypatch.setattr(chat_mod, "_build_context_prompt", build)

    resp = client.post("/api/chat", json={"message": "hello Friday"})
    return prepared, called, (resp.get_json(silent=True) or {})


@pytest.mark.parametrize("mode", MODES)
def test_the_keyless_safety_net_never_picks_a_cloud_tag(client, monkeypatch, mode):
    prepared, called, body = _keyless_turn(client, monkeypatch, mode)
    assert CLOUD_TAG not in called, (mode, called)
    assert not [c for c in prepared if c[1] == CLOUD_TAG], (mode, prepared)
    # Should a cloud tag ever be prepared or answer, it is cloud, never local.
    from agent_friday.services.local_only_guard import is_cloud_model_tag
    assert all(p == "cloud" for p, m in prepared if is_cloud_model_tag(m)), prepared
    served = body.get("served_by") or {}
    if is_cloud_model_tag(served.get("model") or body.get("model") or ""):
        assert not served.get("local") and body.get("seat") != "local", body


@pytest.mark.parametrize("mode", ["smart", "local_preferred", "local_only"])
def test_a_local_mode_never_falls_back_to_a_cloud_tag(client, monkeypatch, mode):
    # A local mode with no recorded intent: a cloud tag is relayed off this PC,
    # so it is never the keyless fallback (cloud_intent), not even prepared.
    prepared, called, _body = _keyless_turn(client, monkeypatch, mode)
    assert not [c for c in prepared if c[1] == CLOUD_TAG], prepared
    assert CLOUD_TAG not in called, called
