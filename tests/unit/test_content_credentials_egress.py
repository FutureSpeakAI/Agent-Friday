"""Unit tests for security-boundary.md §19 row 9: `content_credentials.py`
sends a content HASH (never content) to freetsa.org for RFC-3161
timestamping — no gating is needed (there is no prose to classify), but the
send and its timing were invisible in the one file supposed to enumerate
everything that left the machine. Disposition per the spec: a ledger row per
timestamp request, via the same `record_binary_egress` primitive every other
non-text egress uses.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent_friday.services import content_credentials as cc
from agent_friday.services import egress_gate


def test_successful_timestamp_writes_a_ledger_row(monkeypatch):
    calls = []
    monkeypatch.setattr(
        egress_gate, "record_binary_egress",
        lambda provider, field, **kw: calls.append((provider, field, kw)) or {})
    monkeypatch.setattr(cc, "_request_tsa_token",
                        lambda content_hash: {"type": "rfc3161", "tsa": cc._TSA_URL,
                                              "token": "deadbeef"})

    out = cc.timestamp_rfc3161("sha256:" + "ab" * 32)

    assert out["type"] == "rfc3161"
    assert len(calls) == 1
    provider, field, kw = calls[0]
    assert "freetsa" in provider
    assert kw["action"] == "allow"


def test_failed_timestamp_still_writes_a_ledger_row(monkeypatch):
    """A network failure falls back to local-ledger — the ATTEMPT still
    happened and still belongs in the record."""
    calls = []
    monkeypatch.setattr(
        egress_gate, "record_binary_egress",
        lambda provider, field, **kw: calls.append((provider, field, kw)) or {})
    monkeypatch.setattr(cc, "_request_tsa_token",
                        lambda content_hash: (_ for _ in ()).throw(RuntimeError("timeout")))

    out = cc.timestamp_rfc3161("sha256:" + "cd" * 32)

    assert out["type"] == "local-ledger"
    assert len(calls) == 1
    assert calls[0][2]["action"] == "block" or calls[0][2]["action"] == "fail"


def test_ledger_row_never_raises(monkeypatch):
    monkeypatch.setattr(
        egress_gate, "record_binary_egress",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    monkeypatch.setattr(cc, "_request_tsa_token",
                        lambda content_hash: {"type": "rfc3161", "tsa": cc._TSA_URL,
                                              "token": "x"})
    cc.timestamp_rfc3161("sha256:" + "ef" * 32)  # must not raise
