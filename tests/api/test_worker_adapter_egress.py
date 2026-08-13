"""A3 — the orchestrator's Ollama adapter cannot skip the egress gate (D2).

Before this change `worker_adapters/ollama_adapter.py` built a payload by hand
and POSTed it to a hardcoded localhost:11434 with no egress gate, no PII scrub
and no health recording — the single provider call site in the tree that
skipped the fail-closed contract every other site enforces.

The property under test is deliberately stronger than "the adapter calls
seal_outbound": it is that the GATE decides whether gating applies, from the
destination, so no future call site can opt out by omission.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import egress_gate
from agent_friday.services.worker_adapters import ollama_adapter


class _Task:
    def __init__(self, prompt="summarise this", budget_tokens=256,
                 deadline_seconds=30, task_id="t1"):
        self.prompt = prompt
        self.budget_tokens = budget_tokens
        self.deadline_seconds = deadline_seconds
        self.task_id = task_id
        self.context = {}


def test_adapter_routes_every_payload_through_the_gate(monkeypatch):
    """The gate is consulted on the way out — not bypassed."""
    seen = {}

    def _spy(payload, *, base_url, provider="ollama", log_path=None):
        seen["payload"] = payload
        seen["base_url"] = base_url
        seen["provider"] = provider
        return payload

    monkeypatch.setattr(egress_gate, "gate_worker_payload", _spy)

    sent = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"response": "ok", "prompt_eval_count": 3,
                               "eval_count": 4}).encode()

    def _urlopen(req, *a, **k):
        sent["data"] = json.loads(req.data.decode())
        return _Resp()

    monkeypatch.setattr(ollama_adapter.urllib.request, "urlopen", _urlopen)

    aid = "a1"
    ollama_adapter._JOBS[aid] = {"aid": aid, "task_id": "t1", "status": None,
                                 "_ollama_models": ["gemma4:e4b"]}
    ollama_adapter.OllamaAdapter()._run(aid, _Task())

    assert seen, "adapter reached the network WITHOUT consulting the gate"
    assert seen["base_url"] == ollama_adapter._OLLAMA_BASE
    assert seen["payload"]["prompt"] == "summarise this"
    assert sent["data"]["prompt"] == "summarise this"


def test_a_blocking_gate_stops_the_send(monkeypatch):
    """Fail-closed: if the gate refuses, nothing reaches the wire."""
    def _blocked(payload, *, base_url, provider="ollama", log_path=None):
        raise RuntimeError("egress gate is non-functional")

    monkeypatch.setattr(egress_gate, "gate_worker_payload", _blocked)

    reached = {"network": False}

    def _urlopen(req, *a, **k):
        reached["network"] = True
        raise AssertionError("payload left the device despite a blocking gate")

    monkeypatch.setattr(ollama_adapter.urllib.request, "urlopen", _urlopen)

    aid = "a2"
    ollama_adapter._JOBS[aid] = {"aid": aid, "task_id": "t2", "status": None,
                                 "_ollama_models": ["gemma4:e4b"]}
    ollama_adapter.OllamaAdapter()._run(aid, _Task(task_id="t2"))

    assert reached["network"] is False
    entry = ollama_adapter._JOBS[aid]
    assert entry["status"] == ollama_adapter.WorkerStatus.FAILED
    assert "egress gate blocked" in entry["error"]


# ── The gate's own decision logic ────────────────────────────────────────────
def test_gate_is_cheap_for_verified_on_device_traffic():
    """Local traffic is gated, and the gate correctly has nothing to do."""
    payload = {"model": "m", "prompt": "my SSN is 123-45-6789"}  # pragma: allowlist secret
    out = egress_gate.gate_worker_payload(
        payload, base_url="http://localhost:11434", provider="ollama")
    # Unchanged — and the same object, i.e. no classifier ran (D2: cheap).
    assert out is payload


def test_gate_seals_when_the_destination_is_not_on_device(monkeypatch):
    """A remote base_url must be sealed, not waved through as 'ollama'."""
    monkeypatch.setattr(egress_gate, "gate_operational", lambda: True)
    calls = {}

    def _seal(payload, provider, log_path=None):
        calls["sealed"] = provider
        return dict(payload)

    def _gate_text(text, provider, field="prompt", log_path=None):
        calls["texted"] = field
        return "[[REDACTED]]"

    monkeypatch.setattr(egress_gate, "seal_outbound", _seal)
    monkeypatch.setattr(egress_gate, "gate_text", _gate_text)

    out = egress_gate.gate_worker_payload(
        {"model": "m", "prompt": "my SSN is 123-45-6789"},  # pragma: allowlist secret
        base_url="https://ollama.example.com", provider="ollama")

    assert calls.get("sealed") == "ollama"
    assert calls.get("texted") == "prompt"
    assert out["prompt"] == "[[REDACTED]]"


def test_gate_fails_closed_when_it_cannot_verify_itself(monkeypatch):
    """A broken gate blocks a cloud-bound send instead of leaking it."""
    monkeypatch.setattr(egress_gate, "gate_operational", lambda: False)

    with pytest.raises(RuntimeError, match="non-functional"):
        egress_gate.gate_worker_payload(
            {"model": "m", "prompt": "secret"},
            base_url="https://ollama.example.com", provider="ollama")


def test_unverifiable_host_is_treated_as_cloud(monkeypatch):
    """If the host cannot be resolved/verified, fail-closed to cloud handling."""
    monkeypatch.setattr(
        "agent_friday.routing.provider_descriptors.is_private_host",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("dns exploded")))
    monkeypatch.setattr(egress_gate, "gate_operational", lambda: False)

    with pytest.raises(RuntimeError, match="non-functional"):
        egress_gate.gate_worker_payload(
            {"model": "m", "prompt": "secret"},
            base_url="http://localhost:11434", provider="ollama")
