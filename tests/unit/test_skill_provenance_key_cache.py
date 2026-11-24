"""The key that signs Friday's skills never outlives the reader it came from.

skill_registry caches the governance key for five minutes (load_skills runs every turn) and remembers a
failed read for thirty seconds. Neither noted where the key came from. A test that swapped the reader
left its synthetic key, or its failure, behind: a later skill signed with the left-over key and checked
after the five minutes with the real one reads as not Friday's, and a left-over failure refuses a reader
that works. The same shape bounced #1117/#1119 at b80 through approvals' seal key
(tests/unit/test_approval_seal_key_cache.py).

In the app the reader never changes, so nothing changes there. Both keys are synthetic; the OS keychain
is never read.
"""
from __future__ import annotations

import hashlib

import pytest

from agent_friday import skill_registry as registry
from agent_friday.governance import action_gate

EARLIER = hashlib.sha256(b"a key an earlier test swapped in").digest()
NOW = hashlib.sha256(b"the key this test reads").digest()


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(registry, "_KEY_CACHE", {"key": None, "at": 0.0, "failed_at": None})


def _an_earlier_test_swapped_the_reader(monkeypatch, reader):
    with monkeypatch.context() as m:
        m.setattr(action_gate, "_governance_key", reader)
        try:
            registry._provenance_key()
        except RuntimeError:
            pass


def test_a_key_from_a_swapped_reader_is_not_used_once_the_reader_changes(monkeypatch):
    _an_earlier_test_swapped_the_reader(monkeypatch, lambda: EARLIER)
    monkeypatch.setattr(action_gate, "_governance_key", lambda: NOW)
    assert registry._provenance_key() == NOW


def test_a_failure_under_a_swapped_reader_does_not_refuse_the_next_reader(monkeypatch):
    def _broken():
        raise RuntimeError("keychain locked")
    _an_earlier_test_swapped_the_reader(monkeypatch, _broken)
    monkeypatch.setattr(action_gate, "_governance_key", lambda: NOW)
    assert registry._provenance_key() == NOW


def test_a_line_signed_then_checked_after_the_cache_runs_out_still_verifies(monkeypatch):
    clock = {"t": 1000.0}
    import time as _time
    monkeypatch.setattr(_time, "monotonic", lambda: clock["t"])
    _an_earlier_test_swapped_the_reader(monkeypatch, lambda: EARLIER)
    monkeypatch.setattr(action_gate, "_governance_key", lambda: NOW)
    entry = {"ts": "2026-11-24T00:00:00Z", "action": "created", "actor": "friday"}
    entry["mac"] = registry._mac(entry, registry._provenance_key())
    clock["t"] += registry._KEY_TTL_S + 1
    assert registry._entry_signed(entry)


def test_an_unchanged_reader_is_still_read_once_per_cache_period(monkeypatch):
    reads = []
    monkeypatch.setattr(action_gate, "_governance_key", lambda: (reads.append(1), NOW)[1])
    for _ in range(5):
        assert registry._provenance_key() == NOW
    assert len(reads) == 1
