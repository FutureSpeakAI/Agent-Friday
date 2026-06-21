"""Unit tests for cognitive_memory.py — hash-chained tamper-evident memory ledger.

Key invariants under test:
  - write_memory / read_memory round-trip
  - verify_chain returns True for a clean chain, False after tampering
  - quarantine: a quarantined entry is excluded from normal reads
  - rollback: writes after a cutoff timestamp are moved, not deleted
  - health() reflects live system state

All tests use temp dirs — no ~/.friday files are touched.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from cognitive_memory import CognitiveMemory


# ── Fixture ────────────────────────────────────────────────────────────────────

@pytest.fixture
def cm(tmp_path):
    """A CognitiveMemory instance rooted in an isolated temp dir."""
    return CognitiveMemory(memory_dir=tmp_path)


# ── write / read round-trip ────────────────────────────────────────────────────

class TestWriteReadRoundtrip:
    def test_write_returns_ledger_entry(self, cm):
        entry = cm.write_memory("test/key1", "hello world", source_id="unit-test")
        assert isinstance(entry, dict)
        assert entry["op"] == "write"
        assert entry["key"] == "test/key1"

    def test_read_returns_written_content(self, cm):
        cm.write_memory("my_memory", "synthetic content", source_id="unit-test")
        data = cm.read_memory("my_memory")
        assert data is not None
        assert data["content"] == "synthetic content"
        assert data["key"] == "my_memory"

    def test_read_nonexistent_returns_none(self, cm):
        assert cm.read_memory("does_not_exist") is None

    def test_write_stores_correct_source_id(self, cm):
        cm.write_memory("src_test", "data", source_id="fake-agent-42")
        data = cm.read_memory("src_test")
        assert data["source_id"] == "fake-agent-42"

    def test_write_stores_content_hash(self, cm):
        import hashlib
        cm.write_memory("hash_check", "fixed content")
        data = cm.read_memory("hash_check")
        expected = hashlib.sha256("fixed content".encode("utf-8")).hexdigest()
        assert data["content_hash"] == expected

    def test_write_metadata_persisted(self, cm):
        cm.write_memory("meta_key", "content", metadata={"importance": "high", "tag": "test"})
        data = cm.read_memory("meta_key")
        assert data["metadata"]["importance"] == "high"

    def test_overwrite_replaces_content(self, cm):
        cm.write_memory("overwrite_me", "first version")
        cm.write_memory("overwrite_me", "second version")
        data = cm.read_memory("overwrite_me")
        assert data["content"] == "second version"

    def test_key_with_slash_safe(self, cm):
        """Keys with path separators must be stored safely (/ → _)."""
        cm.write_memory("dir/subkey", "nested content")
        data = cm.read_memory("dir/subkey")
        assert data is not None
        assert data["content"] == "nested content"

    def test_quarantined_flag_defaults_false(self, cm):
        cm.write_memory("qflag_test", "not quarantined")
        data = cm.read_memory("qflag_test")
        assert data["quarantined"] is False


# ── verify_chain ───────────────────────────────────────────────────────────────

class TestVerifyChain:
    def test_empty_ledger_is_valid(self, cm):
        result = cm.verify_chain()
        assert result["valid"] is True
        assert result["entries"] == 0
        assert result["break_at"] is None

    def test_single_entry_chain_valid(self, cm):
        cm.write_memory("entry1", "some data")
        result = cm.verify_chain()
        assert result["valid"] is True
        assert result["entries"] >= 1
        assert result["break_at"] is None

    def test_multiple_entries_chain_valid(self, cm):
        for i in range(5):
            cm.write_memory(f"entry_{i}", f"content_{i}")
        result = cm.verify_chain()
        assert result["valid"] is True
        assert result["entries"] == 5

    def test_tamper_breaks_chain(self, cm):
        """Mutating entry_hash in a ledger entry must invalidate the subsequent link.

        verify_chain checks that entry[i].prev_hash == entry[i-1].entry_hash.
        Flipping entry_hash on entry 0 means entry 1's prev_hash no longer matches.
        """
        cm.write_memory("tamper_target", "original content")
        cm.write_memory("second_entry", "more content")

        lines = cm.ledger_path.read_text(encoding="utf-8").splitlines()
        # Corrupt the entry_hash of the first entry — entry 1's prev_hash will mismatch.
        first_entry = json.loads(lines[0])
        first_entry["entry_hash"] = "000000000000000000000000000000000000000000000000000000000000dead"
        lines[0] = json.dumps(first_entry)
        cm.ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = cm.verify_chain()
        assert result["valid"] is False

    def test_tamper_reports_break_index(self, cm):
        """break_at must point to a specific entry index."""
        for i in range(3):
            cm.write_memory(f"k{i}", f"v{i}")

        lines = cm.ledger_path.read_text(encoding="utf-8").splitlines()
        # Tamper entry 1 (second entry)
        entry = json.loads(lines[1])
        entry["prev_hash"] = "deadbeef" * 8
        lines[1] = json.dumps(entry)
        cm.ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = cm.verify_chain()
        assert result["valid"] is False
        assert result["break_at"] is not None

    def test_prepend_fake_entry_breaks_chain(self, cm):
        """Inserting a fake entry at the front must break the chain."""
        cm.write_memory("real_entry", "real content")

        original = cm.ledger_path.read_text(encoding="utf-8")
        fake_line = json.dumps({
            "op": "write", "key": "injected", "content_hash": "aaa",
            "source_id": "attacker", "ts": 0.0, "ts_iso": "1970-01-01T00:00:00Z",
            "metadata": {}, "prev_hash": "genesis", "entry_hash": "fake_hash",
        })
        cm.ledger_path.write_text(fake_line + "\n" + original, encoding="utf-8")

        result = cm.verify_chain()
        assert result["valid"] is False


# ── quarantine ─────────────────────────────────────────────────────────────────

class TestQuarantine:
    def test_quarantine_by_key_hides_from_read(self, cm):
        cm.write_memory("q_target", "sensitive data", source_id="good-source")
        cm.memory_quarantine(specific_key="q_target", reason="test")
        assert cm.read_memory("q_target") is None

    def test_quarantine_with_include_flag_returns_data(self, cm):
        cm.write_memory("q_visible", "sensitive data", source_id="good-source")
        cm.memory_quarantine(specific_key="q_visible")
        data = cm.read_memory("q_visible", include_quarantined=True)
        assert data is not None
        assert data["quarantined"] is True

    def test_quarantine_by_source_id(self, cm):
        cm.write_memory("from_bad", "data 1", source_id="untrusted-bot")
        cm.write_memory("also_from_bad", "data 2", source_id="untrusted-bot")
        cm.write_memory("from_good", "data 3", source_id="trusted-system")
        cm.memory_quarantine(source_id="untrusted-bot")
        assert cm.read_memory("from_bad") is None
        assert cm.read_memory("also_from_bad") is None
        assert cm.read_memory("from_good") is not None

    def test_quarantine_returns_count(self, cm):
        cm.write_memory("q1", "a", source_id="taint")
        cm.write_memory("q2", "b", source_id="taint")
        result = cm.memory_quarantine(source_id="taint")
        assert result["count"] == 2
        assert set(result["quarantined_keys"]) == {"q1", "q2"}

    def test_quarantine_appends_ledger_entry(self, cm):
        cm.write_memory("q_ledger", "content")
        before = len(cm.get_ledger())
        cm.memory_quarantine(specific_key="q_ledger")
        after = len(cm.get_ledger())
        assert after == before + 1

    def test_double_quarantine_not_counted_twice(self, cm):
        cm.write_memory("q_dbl", "content")
        cm.memory_quarantine(specific_key="q_dbl")
        result2 = cm.memory_quarantine(specific_key="q_dbl")
        # Already quarantined → second call reports 0 newly quarantined
        assert result2["count"] == 0


# ── rollback ───────────────────────────────────────────────────────────────────

class TestRollback:
    def test_rollback_removes_post_cutoff_from_reads(self, cm):
        cm.write_memory("before_cut", "early content")
        cutoff = time.time()
        time.sleep(0.01)  # ensure ts > cutoff
        cm.write_memory("after_cut", "late content")

        result = cm.memory_rollback(cutoff)
        assert "after_cut" in result["rolled_back_keys"]
        assert cm.read_memory("after_cut") is None

    def test_rollback_preserves_pre_cutoff(self, cm):
        cm.write_memory("keep_me", "old content")
        cutoff = time.time()
        time.sleep(0.01)
        cm.write_memory("toss_me", "new content")
        cm.memory_rollback(cutoff)
        assert cm.read_memory("keep_me") is not None

    def test_rollback_moves_not_deletes(self, cm, tmp_path):
        cm.write_memory("rb_target", "roll this back")
        cutoff = time.time()
        time.sleep(0.01)
        cm.write_memory("rb_target", "later version")
        cm.memory_rollback(cutoff)
        # The _rollback subdirectory must exist
        rollback_dirs = list((tmp_path / "_rollback").iterdir())
        assert len(rollback_dirs) == 1
        moved_files = list(rollback_dirs[0].iterdir())
        assert len(moved_files) >= 1

    def test_rollback_returns_summary(self, cm):
        cutoff = time.time()
        time.sleep(0.01)
        cm.write_memory("rb_summary", "data")
        result = cm.memory_rollback(cutoff)
        assert "rolled_back_keys" in result
        assert "cutoff_ts" in result
        assert result["count"] >= 1

    def test_rollback_appends_ledger_entry(self, cm):
        cutoff = time.time()
        before = len(cm.get_ledger())
        cm.memory_rollback(cutoff)
        after = len(cm.get_ledger())
        assert after == before + 1


# ── health ─────────────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_shape(self, cm):
        cm.write_memory("h1", "data1")
        h = cm.health()
        for key in ("total_memories", "quarantined", "active",
                    "ledger_entries", "chain_valid", "chain_break_at"):
            assert key in h

    def test_health_counts_quarantined(self, cm):
        cm.write_memory("healthy", "data")
        cm.write_memory("sick", "data", source_id="bad")
        cm.memory_quarantine(specific_key="sick")
        h = cm.health()
        assert h["quarantined"] >= 1
        assert h["active"] >= 1

    def test_health_chain_valid_true_for_clean(self, cm):
        cm.write_memory("clean1", "good")
        cm.write_memory("clean2", "also good")
        h = cm.health()
        assert h["chain_valid"] is True


# ── get_ledger ─────────────────────────────────────────────────────────────────

class TestGetLedger:
    def test_get_ledger_returns_list(self, cm):
        cm.write_memory("led1", "data")
        entries = cm.get_ledger()
        assert isinstance(entries, list)
        assert len(entries) >= 1

    def test_get_ledger_since_filter(self, cm):
        # Derive the cutoff from the entries' OWN stored timestamps instead of
        # sampling time.time() between writes: on coarse clocks (Windows CI
        # runners tick at ~15.6ms) the sampled cutoff can tie the first
        # entry's ts and the >= filter then includes it ? a pure flake.
        cm.write_memory("before_filter", "early")
        time.sleep(0.05)
        cm.write_memory("after_filter", "late")
        ts = {e["key"]: e["ts"] for e in cm.get_ledger()
              if e["key"] in ("before_filter", "after_filter")}
        assert ts["after_filter"] > ts["before_filter"], \
            "clock too coarse to distinguish the writes"
        cutoff = (ts["before_filter"] + ts["after_filter"]) / 2
        entries = cm.get_ledger(since=cutoff)
        keys = [e["key"] for e in entries]
        assert "after_filter" in keys
        assert "before_filter" not in keys

    def test_get_ledger_limit(self, cm):
        for i in range(10):
            cm.write_memory(f"limit_{i}", "data")
        entries = cm.get_ledger(limit=3)
        assert len(entries) <= 3


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
