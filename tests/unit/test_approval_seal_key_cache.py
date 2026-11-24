"""The key that seals an approval never outlives the reader it came from.

approvals caches the governance key for a minute (each read is an OS keychain call). The cache did not
note where the key came from. A test that swapped the key reader (test_step_records gives
get_governance_key a synthetic key) and raised a goal card left that synthetic key in the cache. A
later test sealed its card with it, the minute ran out between the approval and the spend, the spend
read the real key, and a good card was refused as "not sealed for what it now says". That bounced
#1117 and #1119 at b80 (test_voice_organize::test_a_finished_batch_tells_the_live_call_in_counts,
about 55 s after test_step_records).

In the app the reader never changes, so this changes nothing there: the cached key is the key.
Synthetic cards in a scratch store; both keys are synthetic, the OS keychain is never read.
"""
from __future__ import annotations

import hashlib

import pytest

from agent_friday.governance import proof_of_integrity as poi
from agent_friday.services import approvals

EARLIER = hashlib.sha256(b"a key an earlier test swapped in").digest()
NOW = hashlib.sha256(b"the key this test reads").digest()


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(approvals, "_HOOKS", {})
    monkeypatch.setattr(approvals, "_KEY_CACHE", {"key": None, "at": 0.0})


def _an_earlier_test_swapped_the_reader(monkeypatch):
    """What test_step_records does: a synthetic key reader for one test, then undone."""
    with monkeypatch.context() as m:
        m.setattr(poi, "get_governance_key", lambda: EARLIER)
        assert approvals._seal_key() == EARLIER


def test_a_key_from_a_swapped_reader_is_not_used_once_the_reader_changes(monkeypatch):
    _an_earlier_test_swapped_the_reader(monkeypatch)
    monkeypatch.setattr(poi, "get_governance_key", lambda: NOW)
    assert approvals._seal_key() == NOW


def test_a_card_approved_then_spent_across_the_cache_minute_still_spends(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(approvals.time, "monotonic", lambda: clock["t"])
    _an_earlier_test_swapped_the_reader(monkeypatch)
    monkeypatch.setattr(poi, "get_governance_key", lambda: NOW)
    aid = approvals.create_approval(kind="tool_confirm", subject_type="tool", subject_id="key-cache-1",
                                    title="Run the synthetic action", action_description="run it",
                                    force_gate=True, payload={"tool": "synthetic", "input": {"a": 1}})["approval_id"]
    approvals.decide(aid, "approve")
    clock["t"] += 61                                    # the cache minute runs out before the spend
    assert approvals.spend_once(aid, "synthetic") is True


def test_the_key_is_still_read_once_a_minute_from_an_unchanged_reader(monkeypatch):
    reads = []
    monkeypatch.setattr(poi, "get_governance_key", lambda: (reads.append(1), NOW)[1])
    for _ in range(5):
        assert approvals._seal_key() == NOW
    assert len(reads) == 1
