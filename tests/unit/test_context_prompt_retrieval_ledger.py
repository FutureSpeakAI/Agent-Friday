"""Unit tests for security-boundary.md §20 wiring: `_build_context_prompt`
must call the retrieval ledger on EVERY assembly — gated or not — so an
ungated cloud prompt (today's posture) still leaves a record of what it
carried, closing the §1.4 gap (vault access-log rows stop entirely when
prompt gating is off).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent_friday.services import model_router as mr
from agent_friday.services import retrieval_ledger as rl


def test_ungated_assembly_still_writes_a_retrieval_ledger_row(monkeypatch):
    """This is exactly today's posture: vault_local_only false ->
    vault_control=None -> _build_context_prompt assembles ungated. Before
    this wiring, that path produced NO record anywhere (§1.4's gap)."""
    calls = []
    monkeypatch.setattr(
        rl, "record_assembly",
        lambda sections, **kw: calls.append((sections, kw)) or len(sections))

    mr._build_context_prompt("hello there", workspace="chat", vault_control=None)

    assert calls, (
        "an ungated assembly must still call the retrieval ledger — this "
        "is the whole point of §20"
    )
    sections, kw = calls[0]
    assert kw["gated"] is False
    assert kw["destination_class"]
    assert kw["policy_source"]
    assert isinstance(kw["turn_id"], str) and kw["turn_id"]


def test_gated_assembly_also_writes_a_retrieval_ledger_row(monkeypatch):
    calls = []
    monkeypatch.setattr(
        rl, "record_assembly",
        lambda sections, **kw: calls.append((sections, kw)) or len(sections))

    class _FakeVaultControl:
        def classify(self, text, default=2):
            return default

        def assemble_prompt(self, sections, provider, fallback="redact"):
            return "\n".join(t for _, t in sections if t)

    mr._build_context_prompt("hello there", workspace="chat",
                             vault_control=_FakeVaultControl(), provider="anthropic")

    assert calls
    sections, kw = calls[0]
    assert kw["gated"] is True
    assert kw["destination_class"] in ("anthropic",)


def test_ledger_failure_does_not_break_prompt_assembly(monkeypatch):
    monkeypatch.setattr(
        rl, "record_assembly",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    # Must not raise — assembly must survive a ledger failure.
    prompt, _sources = mr._build_context_prompt("hello there", workspace="chat",
                                                 vault_control=None)
    assert prompt


def test_local_provider_assembly_records_destination_local(monkeypatch):
    calls = []
    monkeypatch.setattr(
        rl, "record_assembly",
        lambda sections, **kw: calls.append((sections, kw)) or len(sections))

    mr._build_context_prompt("hello there", workspace="chat",
                             vault_control=None, provider="ollama")

    assert calls
    _sections, kw = calls[0]
    assert kw["destination_class"] == "local"
