"""Compaction never carries a local seat's transcript to a cloud model.

A long run on a local seat (Bonsai2 on llama-server, a model on Ollama) is
compacted by summarising the middle of its transcript. That transcript holds
whatever the local seat was allowed to see -- vault reads, local-only
conversation, tool results that never left the machine. Summarising it on a
cloud model is the transcript leaving the machine.

The summary must be written by the seat itself, or not at all.
"""
from __future__ import annotations

import json
import types

import pytest

from agent_friday.services import agent as ag
from agent_friday.services import compaction
from agent_friday.services import model_router as mr

MARKER = "VAULT-MARKER-acct-4411"


@pytest.fixture
def cloud_calls(monkeypatch):
    """Record every request that reaches a cloud primitive."""
    seen = []

    # Both fakes begin exactly as the real primitives do: the local-only
    # guard refuses first (model_router._call_claude / _call_openai).
    from agent_friday.services.local_only_guard import refuse_if_active

    def _claude(messages, system=None, model=None, **kw):
        refuse_if_active("anthropic", str(model or ""))
        seen.append(("anthropic", model, json.dumps(messages, default=str)))
        return "cloud summary"

    def _openai(messages, system=None, model=None, provider=None, **kw):
        refuse_if_active(provider if provider else "openai", str(model or ""))
        seen.append(("openai", model, json.dumps(messages, default=str)))
        return ("cloud summary", [])

    monkeypatch.setattr(mr, "_call_claude", _claude)
    monkeypatch.setattr(mr, "_call_openai", _openai)
    monkeypatch.setattr(mr, "get_anthropic_client", lambda: object())
    return seen


@pytest.fixture
def settings(monkeypatch):
    """Stephen's shape: a cloud subagent model, local-preferred routing, and
    compaction on with a window small enough to fire."""
    s = {
        "subagent_model": "claude-opus-5-5",
        "model_routing": {"mode": "local_preferred", "vault_local_only": False},
        "compaction": {"enabled": True, "trigger_ratio": 0.5, "context_window": 2000,
                       "keep_head": 2, "keep_tail": 4, "summary_max_tokens": 200},
    }
    monkeypatch.setattr(compaction, "_load_settings", lambda: s)
    monkeypatch.setattr(mr, "_load_settings", lambda: s)
    return s


def _router(verdict):
    class _R:
        def route(self, messages, task_context=None):
            return {"provider": verdict, "model": task_context.get("cloud_model")}
    return lambda cfg=None: _R()


def _long_convo():
    convo = [{"role": "system", "content": "You are Friday."},
             {"role": "user", "content": "Reconcile the vault accounts."}]
    for i in range(12):
        convo.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{i}", "type": "function",
             "function": {"name": "vault_read", "arguments": json.dumps({"n": i})}}]})
        convo.append({"role": "tool", "tool_call_id": f"c{i}",
                      "content": f"{MARKER} row {i} " + ("balance 12.00 " * 60)})
    return convo


def _local_seat_send(calls):
    def send(convo, tools):
        calls.append([dict(m) for m in convo])
        last = convo[-1]
        if tools is None and "Summarize" in str(last.get("content")):
            return {"choices": [{"message": {"role": "assistant",
                                             "content": "local summary of rows 0-7"},
                                 "finish_reason": "stop"}]}
        return {"choices": [{"message": {"role": "assistant", "content": "Reconciled."},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2}}
    return send


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(ag, "_register_agent_orb", lambda *a, **k: None)
    from agent_friday.services import cost_meter as cm
    monkeypatch.setattr(cm, "meter", lambda *a, **k: 0.0)
    monkeypatch.setattr(cm, "record", lambda *a, **k: 0.0)


@pytest.mark.parametrize("verdict", ["cloud", "local"])
@pytest.mark.parametrize("dialect,seat", [("local", None), ("openai", "local")])
def test_local_seat_compaction_never_reaches_a_cloud_model(
        monkeypatch, settings, cloud_calls, verdict, dialect, seat):
    import agent_friday.routing.model_router as rmr
    monkeypatch.setattr(rmr, "get_router", _router(verdict))

    def _local_down(*a, **k):
        raise RuntimeError("seat busy")
    monkeypatch.setattr(mr, "_call_ollama", _local_down)

    sent = []
    ag._oai_agentic_loop(_long_convo(), None, _local_seat_send(sent),
                         provider=dialect, seat=seat, model="bonsai2:27b", session_ctx={})
    leaked = [c for c in cloud_calls if MARKER in c[2]]
    assert not leaked, "a local seat's transcript was summarised on %s" % (
        [(c[0], c[1]) for c in leaked],)


def test_the_local_seat_writes_its_own_summary(monkeypatch, settings, cloud_calls):
    import agent_friday.routing.model_router as rmr
    monkeypatch.setattr(rmr, "get_router", _router("cloud"))
    sent = []
    ag._oai_agentic_loop(_long_convo(), None, _local_seat_send(sent),
                         provider="openai", seat="local", model="bonsai2:27b", session_ctx={})
    asked = [c for c in sent if "Summarize" in str(c[-1].get("content"))]
    assert asked and MARKER in json.dumps(asked[0]), "the seat was not asked to summarise"
    answered = [c for c in sent if "Summarize" not in str(c[-1].get("content"))]
    assert "[Context Summary]" in json.dumps(answered[0]) and "local summary of rows" in json.dumps(answered[0])
    assert not cloud_calls


def test_default_summarizer_refuses_the_cloud(monkeypatch, settings, cloud_calls):
    """A caller that does not say where its transcript may go gets a local
    model or no summary -- never the cloud."""
    import agent_friday.routing.model_router as rmr
    monkeypatch.setattr(rmr, "get_router", _router("cloud"))
    monkeypatch.setattr(mr, "_call_ollama", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no local seat")))
    out = compaction._default_summarizer(f"USER: {MARKER}", 100)
    assert out == ""
    assert not [c for c in cloud_calls if MARKER in c[2]]


def test_chat_trajectory_compression_refuses_the_cloud(monkeypatch, settings, cloud_calls):
    import agent_friday.routing.model_router as rmr
    monkeypatch.setattr(rmr, "get_router", _router("cloud"))
    monkeypatch.setattr(mr, "_call_ollama", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no local seat")))
    msgs = []
    for i in range(60):
        msgs.append({"role": "user", "content": f"{MARKER} question {i} " + "x" * 50_000})
        msgs.append({"role": "assistant", "content": "answer " + "y" * 50_000})
    out = mr._compress_trajectory(list(msgs))
    assert not [c for c in cloud_calls if MARKER in c[2]]
    assert len(out) == len(msgs), "with no local summarizer the history is left whole, not truncated"


def test_the_real_claude_primitive_refuses_under_the_local_only_guard(monkeypatch):
    """The fakes above mirror this: the guard lives in the real primitive."""
    from agent_friday.services import local_only_guard as log
    created = []
    client = types.SimpleNamespace(messages=types.SimpleNamespace(
        create=lambda **kw: created.append(kw)))
    monkeypatch.setattr(mr, "get_anthropic_client", lambda: client)
    with log.local_only("context compaction"):
        with pytest.raises(log.CloudRefused):
            mr._call_claude([{"role": "user", "content": MARKER}], model="claude-opus-5-5")
    assert not created
