"""Unit tests for security-boundary.md §20: the retrieval ledger.

The gap: vault access-log rows stop entirely when prompt gating is off
(today's posture). This ledger writes one row per named, tier-tagged prompt
section at assembly time — regardless of gating posture — so an ungated
cloud prompt still produces a record of what it carried.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent_friday.services import retrieval_ledger as rl


def _read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class TestDeriveSectionName:
    def test_extracts_header_between_double_equals(self):
        assert rl.derive_section_name("\n== TRUST NETWORK ==\nsome text", 0) == "trust_network"

    def test_handles_header_with_trailing_colon_detail(self):
        name = rl.derive_section_name("\n== ACTIVE WORKSPACE: Draft ==\ndata", 3)
        assert name.startswith("active_workspace")

    def test_falls_back_to_positional_when_no_header(self):
        assert rl.derive_section_name("just plain text, no header", 5) == "section_5"

    def test_falls_back_on_empty_text(self):
        assert rl.derive_section_name("", 2) == "section_2"


class TestRecordAssembly:
    def test_writes_one_row_per_nonempty_section(self, tmp_path):
        log_path = tmp_path / "retrieval-log.jsonl"
        sections = [
            (1, "\n== TODAY'S CONTEXT ==\nsome public stuff"),
            (2, "\n== TRUST NETWORK ==\nprivate stuff"),
            (2, ""),  # empty sections must not produce a row
        ]
        n = rl.record_assembly(
            sections, turn_id="t1", destination_class="anthropic",
            gated=True, policy_source="settings", log_path=log_path,
        )
        assert n == 2
        rows = _read_rows(log_path)
        assert len(rows) == 2

    def test_row_shape_has_names_tiers_sizes_never_content(self, tmp_path):
        log_path = tmp_path / "retrieval-log.jsonl"
        secret = "a very specific private detail nobody else should read"
        sections = [(2, f"\n== TRUST NETWORK ==\n{secret}")]
        rl.record_assembly(
            sections, turn_id="t2", destination_class="gemini",
            gated=False, policy_source="default", log_path=log_path,
        )
        row = _read_rows(log_path)[0]
        assert row["section"] == "trust_network"
        assert row["tier"] == 2
        assert row["chars"] == len(sections[0][1])
        assert row["gated"] is False
        assert row["policy_source"] == "default"
        assert row["destination_class"] == "gemini"
        assert row["turn_id"] == "t2"
        assert row["action_hint"] == "redact"
        # the content record is the context log, not this ledger
        raw = log_path.read_text(encoding="utf-8")
        assert secret not in raw

    def test_action_hint_maps_tier_to_gate_verdict(self, tmp_path):
        log_path = tmp_path / "retrieval-log.jsonl"
        sections = [
            (1, "== A ==\npublic"),
            (2, "== B ==\nprivate"),
            (3, "== C ==\nsensitive"),
        ]
        rl.record_assembly(sections, turn_id="t3", destination_class="local",
                           gated=False, policy_source="default", log_path=log_path)
        rows = {r["section"]: r["action_hint"] for r in _read_rows(log_path)}
        assert rows["a"] == "allow"
        assert rows["b"] == "redact"
        assert rows["c"] == "drop"

    def test_local_assemblies_are_recorded_too(self, tmp_path):
        """§20: local assemblies are recorded too, cheaply, so the ledger
        can answer 'what does a local turn see that a cloud turn doesn't'."""
        log_path = tmp_path / "retrieval-log.jsonl"
        n = rl.record_assembly(
            [(1, "== X ==\nhello")], turn_id="t4", destination_class="local",
            gated=False, policy_source="default", log_path=log_path,
        )
        assert n == 1
        assert _read_rows(log_path)[0]["destination_class"] == "local"

    def test_never_raises_on_write_failure(self, tmp_path):
        bad_path = tmp_path / "nonexistent_dir_with_no_perms" / ".." / "\x00bad"
        # Use a path guaranteed to fail to open rather than relying on OS perms.
        n = rl.record_assembly(
            [(1, "== X ==\nhello")], turn_id="t5", destination_class="local",
            gated=False, policy_source="default", log_path=bad_path,
        )
        assert n == 0

    def test_empty_sections_write_nothing(self, tmp_path):
        log_path = tmp_path / "retrieval-log.jsonl"
        n = rl.record_assembly([], turn_id="t6", destination_class="local",
                               gated=False, policy_source="default", log_path=log_path)
        assert n == 0
        assert not log_path.exists()


def test_new_turn_id_is_stable_length_and_varies():
    a, b = rl.new_turn_id(), rl.new_turn_id()
    assert a != b
    assert len(a) == 12
