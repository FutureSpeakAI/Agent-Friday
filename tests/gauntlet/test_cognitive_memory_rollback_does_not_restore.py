"""Gauntlet finding F72 (2026-09-04, seam 13/SkillOpt investigation,
externally sourced): CognitiveMemory.memory_rollback() is named, and was
documented, as a point-in-time restore -- "Roll back all writes that
occurred after `timestamp`". It is not one. write_memory() keeps no
per-key history (a second write unconditionally overwrites the first's
on-disk file), so by the time rollback runs, the ONLY on-disk copy of a
rolled-back key is whatever the LATEST write left there -- never the
value the key held before the cutoff. Rollback moves that latest copy
into an internal `_rollback/<cutoff>/` directory that nothing else in
the codebase ever reads back from (confirmed by a repo-wide grep), so
the net effect for any key written both before AND after the cutoff is
that it disappears entirely: not the old value, not the new one, nothing.

This is a genuine, severe, externally-reported finding (the maintainer
commissioned an outside review of the public v5.10.0 repo; this specific
claim was verified by the external reviewer executing the scoring
functions, and re-verified here against current code before logging).
Reachable today via a real POST /api/memory/rollback route
(@login_required, routes/insights.py), though no UI currently calls it.

This probe is deliberately not named "test_..._is_fixed" -- it PINS the
current, corrected-documentation behavior (a purge, not a restore) as a
known, intentional fact for now, so a future change either fixes it
honestly (and this test is rewritten to match) or the gap stays visible
rather than being silently rediscovered. Whether Friday should have a
real point-in-time restore (which would require adding version history
to write_memory() -- a real architecture change to a security-adjacent
memory primitive) is a decision for the maintainer, not made here.
"""
from __future__ import annotations

import time

from agent_friday.cognitive_memory import CognitiveMemory


class TestMemoryRollbackDoesNotRestore:
    def test_the_reviewers_exact_reproduction_write_a_write_b_roll_back(self, tmp_path):
        """write A, record a cutoff, write B, roll back -- confirm the
        result is neither A nor B, it's gone. This is the exact scenario
        the external review specified to settle whether the finding held."""
        cm = CognitiveMemory(memory_dir=tmp_path)

        cm.write_memory("k", "A")
        time.sleep(0.01)
        cutoff = time.time()
        time.sleep(0.01)
        cm.write_memory("k", "B")

        before = cm.read_memory("k")
        assert before is not None and before["content"] == "B", (
            "setup assumption broken -- the second write should be the "
            "current value before rollback runs"
        )

        summary = cm.memory_rollback(cutoff)
        assert summary["rolled_back_keys"] == ["k"]

        after = cm.read_memory("k")
        assert after is None, (
            "memory_rollback() returned a value for 'k' after rolling back "
            "a key that was written both before and after the cutoff -- "
            "if this now returns 'A', a real restore mechanism was built "
            "and this test (and F72's disposition) needs updating, not "
            "just this assertion"
        )

    def test_a_key_written_only_after_the_cutoff_is_correctly_removed(self, tmp_path):
        """The one case rollback genuinely handles correctly: a key that
        did not exist before the cutoff. Removing it IS the correct
        pre-cutoff state (nonexistent), so this is not a defect -- named
        explicitly as the grounding/falsifiability case."""
        cm = CognitiveMemory(memory_dir=tmp_path)
        cutoff = time.time()
        time.sleep(0.01)
        cm.write_memory("new_key", "only ever existed after cutoff")

        cm.memory_rollback(cutoff)

        assert cm.read_memory("new_key") is None

    def test_a_key_never_touched_after_the_cutoff_is_left_alone(self, tmp_path):
        cm = CognitiveMemory(memory_dir=tmp_path)
        cm.write_memory("untouched", "still here")
        time.sleep(0.01)
        cutoff = time.time()

        cm.memory_rollback(cutoff)

        after = cm.read_memory("untouched")
        assert after is not None and after["content"] == "still here"

    def test_nothing_in_the_codebase_reads_back_from_the_rollback_directory(self):
        """Grounds the docstring's own claim: confirms (at write time of
        this test) that _rollback/ is write-only from the app's own
        perspective -- a human could still dig a file out manually, but
        the application never offers to."""
        import subprocess
        result = subprocess.run(
            ["git", "grep", "-n", "-l", "rollback_dir",
             "--", "src/agent_friday/"],
            capture_output=True, text=True, cwd=".",
        )
        hits = [line for line in result.stdout.splitlines()
                if "cognitive_memory.py" not in line]
        assert hits == [], (
            f"expected nothing outside cognitive_memory.py to reference "
            f"rollback_dir -- got {hits!r}. If something new reads from "
            f"_rollback/, F72's 'nothing restores from it' claim may no "
            f"longer hold and should be re-verified, not just this test "
            f"updated."
        )
