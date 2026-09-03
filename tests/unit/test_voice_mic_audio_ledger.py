"""Unit tests for security-boundary.md §19 row 2: Live microphone audio left
NO ledger row of any kind (contrast routes/chat.py:341, which at least
records binary egress for an uploaded image). Audio cannot be
text-classified, so the fix is not gating — it is making the send itself
part of the record, via the SAME `record_binary_egress` primitive every
other binary path in the codebase already uses.

`_record_mic_audio_egress` is the extracted, directly-testable seam. It must
call `egress_gate.record_binary_egress` with provider "google-gemini",
field "mic_audio", and the byte count on close — never silently no-op.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent_friday.routes import voice as v
from agent_friday.services import egress_gate


def test_session_open_writes_a_ledger_row(monkeypatch):
    calls = []
    monkeypatch.setattr(
        egress_gate, "record_binary_egress",
        lambda provider, field, **kw: calls.append((provider, field, kw)) or {})
    v._record_mic_audio_egress("open")
    assert len(calls) == 1
    provider, field, kw = calls[0]
    assert provider == "google-gemini"
    assert field == "mic_audio"
    assert kw["action"] == "allow"
    assert "open" in kw["reason"].lower()


def test_session_close_writes_a_ledger_row_with_byte_count(monkeypatch):
    calls = []
    monkeypatch.setattr(
        egress_gate, "record_binary_egress",
        lambda provider, field, **kw: calls.append((provider, field, kw)) or {})
    v._record_mic_audio_egress("close", byte_len=48000)
    assert len(calls) == 1
    provider, field, kw = calls[0]
    assert provider == "google-gemini"
    assert field == "mic_audio"
    assert kw["byte_len"] == 48000
    assert "clos" in kw["reason"].lower()


def test_gate_module_unreachable_does_not_raise(monkeypatch):
    """Ledger failure must never take down the voice session — it is a
    record, not enforcement — but it must not silently pretend to have
    written a row either; that distinction is what the reason strings above
    are for."""
    monkeypatch.setattr(
        egress_gate, "record_binary_egress",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    v._record_mic_audio_egress("open")  # must not raise
