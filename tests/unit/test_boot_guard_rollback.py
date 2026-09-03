"""Unit tests for services/boot_guard.py — the boot invariant, actually held.

Written red-first against `30cb426`, where twelve of the eighteen tests here
failed (the split is accounted for below). The module's headline claim is *"a failed self-edit must never leave Friday unable to
start"*, and at that commit:

  * `_self_editable_paths()` covered `~/.friday/workspace_studio` and
    `~/.friday/settings.json` and nothing else, so the snapshot could not contain,
    and the auto-revert could not restore, anything capable of breaking a boot;
  * `check_self_edit()` and `check_scope()` had zero callers anywhere in `src/`;
  * `restore_known_good()`'s own note pointed the user at `STATE_DIR/"failed"`
    for the pre-rollback files, and nothing in the module ever wrote there;
  * `recent_notes()` had zero callers, so the trail was written and never read.

Evidence versus guard, counted rather than blurred (the rule this repo applies
to a growth loop's own tests, `docs/design/grow-button.md` §6.2: a test that
passed before the change proves nothing about the change). Of the 18 tests here,
**12 failed at `30cb426` and are evidence**; **6 passed and are regression
guards** on machinery this change rewrites — `test_self_editable_paths_still_
include_the_friday_state`, `test_restore_puts_back_a_source_file_that_was_
corrupted`, `test_restore_removes_a_file_the_bad_edit_added`, `test_a_successful_
boot_re_arms_the_restore`, `test_safe_mode_still_refuses_to_restore_anything`
and `test_a_changed_tree_is_recopied`. The first three pass pre-change only
because they call `snapshot_known_good(paths=...)` explicitly and so bypass
`_self_editable_paths()`, which is the defect; they are kept because the copy and
restore paths underneath them are being replaced wholesale, not because they
demonstrate F1.

The tests are grouped by the finding they pin (F1-F4 in
`docs/design/grow-button.md` §18.2). Each asserts a fact about the enforcement,
not about a setting — `security-boundary.md` §18.3's rule, which is what these
four defects each defeated.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_friday.services import boot_guard as bg


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Point every module-level path at a per-test directory.

    The root conftest already redirects HOME, but this module computes its paths
    at import time, so a redirect that happens later would not move them. These
    are the real files on a live machine; a test that wrote to them would destroy
    the user's known-good snapshot.
    """
    state = tmp_path / "boot_guard"
    monkeypatch.setattr(bg, "STATE_DIR", state)
    monkeypatch.setattr(bg, "ATTEMPT_FILE", state / "boot_attempt.json")
    monkeypatch.setattr(bg, "KNOWN_GOOD", state / "known_good")
    monkeypatch.setattr(bg, "NOTES_FILE", state / "rollback_notes.jsonl")
    monkeypatch.delenv("FRIDAY_SAFE_MODE", raising=False)
    yield


@pytest.fixture
def fake_tree(tmp_path):
    """A stand-in for the app's importable source: a package plus a UI file."""
    pkg = tmp_path / "app" / "agent_friday"
    (pkg / "services").mkdir(parents=True)
    (pkg / "__init__.py").write_text("VERSION = 1\n", encoding="utf-8")
    (pkg / "server.py").write_text("def main():\n    return 'good'\n", encoding="utf-8")
    (pkg / "services" / "thing.py").write_text("OK = True\n", encoding="utf-8")
    # Compiled churn that must never enter a snapshot.
    (pkg / "__pycache__").mkdir()
    (pkg / "__pycache__" / "server.cpython-313.pyc").write_bytes(b"\x00\x01stale")
    ui = tmp_path / "app" / "index.html"
    ui.write_text("<html>good</html>", encoding="utf-8")
    return pkg, ui


# ── F1: the snapshot must cover what can actually break a boot ───────────────

class TestSnapshotCoverage:

    def test_self_editable_paths_include_the_importable_source(self):
        """RED at 30cb426: returned only two paths under ~/.friday.

        `BOOT_CRITICAL` names five files under `src/agent_friday/`. A snapshot
        that cannot contain any of them cannot restore any of them, so the
        module's stated promise was not delivered by the paths it protected.
        """
        paths = [str(p).replace("\\", "/") for p in bg._self_editable_paths()]
        pkg_root = str(Path(bg.__file__).resolve().parent.parent).replace("\\", "/")
        assert any(p == pkg_root for p in paths), (
            "the importable package root is not snapshotted, so BOOT_CRITICAL "
            "files cannot be restored: %r" % paths)

    def test_self_editable_paths_still_include_the_friday_state(self):
        """The original two paths are additions to, not replacements of, the set."""
        names = {Path(p).name for p in bg._self_editable_paths()}
        assert "workspace_studio" in names
        assert "settings.json" in names

    def test_restore_puts_back_a_source_file_that_was_corrupted(self, fake_tree):
        """GUARD (passed at 30cb426) on the copy/restore machinery being rewritten.

        It passes pre-change only because it hands `snapshot_known_good` the
        paths explicitly and so never asks `_self_editable_paths()` what is
        covered — which is F1 itself. It is evidence that copy-and-restore works,
        never that the right things are copied; `test_self_editable_paths_
        include_the_importable_source` is the test that carries that claim.
        """
        pkg, ui = fake_tree
        bg.snapshot_known_good([pkg, ui])

        (pkg / "server.py").write_text("def main(:\n", encoding="utf-8")   # syntax error
        ui.write_text("<html>broken", encoding="utf-8")

        res = bg.restore_known_good()

        assert res.get("ok") is True, res
        assert (pkg / "server.py").read_text(encoding="utf-8") == \
            "def main():\n    return 'good'\n"
        assert ui.read_text(encoding="utf-8") == "<html>good</html>"

    def test_restore_removes_a_file_the_bad_edit_added(self, fake_tree):
        """Restoring a directory means restoring its shape, not merging into it.

        A self-edit that ADDS a broken module is as capable of stopping a boot as
        one that edits an existing file, and a copy-over-the-top restore leaves it.
        """
        pkg, ui = fake_tree
        bg.snapshot_known_good([pkg, ui])
        (pkg / "services" / "poison.py").write_text("raise SystemExit(1)\n", encoding="utf-8")

        bg.restore_known_good()

        assert not (pkg / "services" / "poison.py").exists()

    def test_snapshot_excludes_compiled_churn(self, fake_tree):
        """RED at 30cb426: the snapshot copied the tree wholesale.

        Harmless while the covered set was two JSON paths. Once the package root
        is covered, compiled artefacts are churn that differs per interpreter and
        would make every boot look like a change. MEASURED: excluding them takes
        the store from `du`'s 25 MB to 7.4 MB.
        """
        pkg, ui = fake_tree
        bg.snapshot_known_good([pkg, ui])
        stored = list(bg.KNOWN_GOOD.rglob("*.pyc"))
        assert stored == [], "compiled artefacts were snapshotted: %r" % stored

    def test_two_snapshot_paths_with_the_same_basename_do_not_collide(self, tmp_path):
        """RED at 30cb426: `dest = KNOWN_GOOD / p.name` keyed by basename alone.

        Not yet reachable at that commit because the two covered paths had
        distinct names — which is exactly why widening the set without fixing the
        key would have silently made the second path overwrite the first.
        """
        a = tmp_path / "one" / "settings.json"
        b = tmp_path / "two" / "settings.json"
        a.parent.mkdir(parents=True)
        b.parent.mkdir(parents=True)
        a.write_text("A", encoding="utf-8")
        b.write_text("B", encoding="utf-8")

        bg.snapshot_known_good([a, b])
        a.write_text("corrupt", encoding="utf-8")
        b.write_text("corrupt", encoding="utf-8")
        bg.restore_known_good()

        assert a.read_text(encoding="utf-8") == "A"
        assert b.read_text(encoding="utf-8") == "B"


# ── F1b: a restore must not be able to destroy the tree it is repairing ──────

class TestRestoreSafety:

    def test_an_incomplete_snapshot_is_refused_rather_than_restored(self, fake_tree):
        """RED at 30cb426: restore rmtree'd the live path, then copied.

        `snapshot_known_good` did `rmtree(dest)` then `copytree`, so a crash
        mid-snapshot left a partial known-good; restore would then have deleted
        the live source and replaced it with the fragment. Widening the covered
        set to include `src/` makes that failure fatal rather than cosmetic.
        """
        pkg, ui = fake_tree
        bg.snapshot_known_good([pkg, ui])

        manifest = json.loads((bg.STATE_DIR / "known_good.json").read_text(encoding="utf-8"))
        manifest["complete"] = False
        (bg.STATE_DIR / "known_good.json").write_text(json.dumps(manifest), encoding="utf-8")

        res = bg.restore_known_good()

        assert res.get("ok") is False, "an incomplete snapshot was restored anyway"
        assert (pkg / "server.py").exists(), "the live tree was destroyed"

    def test_a_truncated_snapshot_is_detected_before_anything_is_replaced(self, fake_tree):
        """Integrity is checked against the manifest, not assumed from presence."""
        pkg, ui = fake_tree
        bg.snapshot_known_good([pkg, ui])

        for stored in bg.KNOWN_GOOD.rglob("thing.py"):
            stored.unlink()

        res = bg.restore_known_good()

        assert res.get("ok") is False, res
        assert (pkg / "services" / "thing.py").exists(), "the live tree was destroyed"


# ── F4a: the failed/ directory the note has always promised ─────────────────

class TestFailedDirectory:

    def test_restore_moves_the_pre_restore_state_into_failed(self, fake_tree):
        """RED at 30cb426: the note said the files were in STATE_DIR/'failed'.

        Nothing in the module ever wrote there, so the one instruction the
        rollback trail gave a user was to look in a directory that did not exist.
        """
        pkg, ui = fake_tree
        bg.snapshot_known_good([pkg, ui])
        (pkg / "server.py").write_text("def main(:\n", encoding="utf-8")

        bg.restore_known_good()

        failed = bg.STATE_DIR / "failed"
        assert failed.exists(), "the failed/ directory the note promises was never created"
        kept = [p for p in failed.rglob("server.py")]
        assert kept, "the broken file was discarded rather than preserved for inspection"
        assert kept[0].read_text(encoding="utf-8") == "def main(:\n", (
            "failed/ holds something other than the state that was replaced")

    def test_the_note_points_at_a_path_that_now_exists(self, fake_tree):
        """The trail's claim is checked against the filesystem, not read for tone."""
        pkg, ui = fake_tree
        bg.snapshot_known_good([pkg, ui])
        (pkg / "server.py").write_text("broken", encoding="utf-8")
        bg.restore_known_good()

        notes = bg.recent_notes(5)
        assert notes, "the restore wrote no note"
        undo = notes[0].get("undo") or ""
        assert undo, "the note carries no undo pointer"
        pointed = Path(undo.split("are in ")[-1].strip())
        assert pointed.exists(), "the note points at %s, which does not exist" % pointed


# ── F4b: a trail nobody reads is a trail that does not exist ────────────────

class TestNotesAreReachable:

    def test_status_surfaces_the_rollback_trail(self, fake_tree):
        """RED at 30cb426: `recent_notes()` had zero callers and `status()` no key."""
        pkg, ui = fake_tree
        bg.snapshot_known_good([pkg, ui])
        (pkg / "server.py").write_text("broken", encoding="utf-8")
        bg.restore_known_good()

        st = bg.status()
        assert "recent_notes" in st, "status() does not expose the trail: %r" % sorted(st)
        assert st["recent_notes"], "status() exposes an empty trail after a restore"


# ── The restore must not loop, and must not overstate what it fixed ─────────

class TestRestoreDoesNotLoop:

    def test_a_second_restore_is_skipped_until_a_boot_succeeds(self, fake_tree):
        """RED at 30cb426: restore fired on every boot where failing_to_boot().

        Widening the set to `src/` makes an unhelpful restore expensive and
        repetitive rather than harmless, so a restore that has not yet been
        vindicated by a successful boot must not be repeated.
        """
        pkg, ui = fake_tree
        bg.snapshot_known_good([pkg, ui])
        (pkg / "server.py").write_text("broken", encoding="utf-8")

        first = bg.restore_known_good()
        assert first.get("ok") is True

        (pkg / "server.py").write_text("broken again", encoding="utf-8")
        second = bg.restore_known_good()

        assert second.get("ok") is not True, second
        assert second.get("skipped"), "a repeat restore was not reported as skipped"
        assert (pkg / "server.py").read_text(encoding="utf-8") == "broken again"

    def test_a_successful_boot_re_arms_the_restore(self, fake_tree):
        pkg, ui = fake_tree
        bg.snapshot_known_good([pkg, ui])
        (pkg / "server.py").write_text("broken", encoding="utf-8")
        bg.restore_known_good()

        bg.mark_boot_succeeded()

        (pkg / "server.py").write_text("broken later", encoding="utf-8")
        again = bg.restore_known_good()
        assert again.get("ok") is True, again

    def test_restore_clears_the_failure_count_so_the_next_boot_is_a_clean_chance(
            self, fake_tree):
        pkg, ui = fake_tree
        bg.snapshot_known_good([pkg, ui])
        bg._write(bg.ATTEMPT_FILE, {"consecutive_failures": 4,
                                    "last_start_completed": False})
        assert bg.failing_to_boot() is True

        bg.restore_known_good()

        assert bg.failing_to_boot() is False

    def test_restore_says_a_source_restore_lands_on_the_next_start(self, fake_tree):
        """This process imported its modules before the restore ran.

        Overwriting the files on disk cannot change the code already in memory,
        so the honest report is that the restored state takes effect on the next
        start. Saying otherwise is the invisible-success failure one layer down.
        """
        pkg, ui = fake_tree
        bg.snapshot_known_good([pkg, ui])
        (pkg / "server.py").write_text("broken", encoding="utf-8")

        res = bg.restore_known_good()

        assert "next start" in (res.get("takes_effect") or "").lower(), res

    def test_safe_mode_still_refuses_to_restore_anything(self, fake_tree, monkeypatch):
        """The outside-the-app off switch keeps precedence over every change here."""
        pkg, ui = fake_tree
        bg.snapshot_known_good([pkg, ui])
        (pkg / "server.py").write_text("broken", encoding="utf-8")
        monkeypatch.setenv("FRIDAY_SAFE_MODE", "1")

        res = bg.restore_known_good()

        assert res.get("ok") is False
        assert (pkg / "server.py").read_text(encoding="utf-8") == "broken"


# ── An unchanged tree should not be re-copied on every boot ─────────────────

class TestSnapshotIsCheap:

    def test_an_unchanged_tree_is_not_recopied(self, fake_tree):
        pkg, ui = fake_tree
        first = bg.snapshot_known_good([pkg, ui])
        assert first.get("saved")

        second = bg.snapshot_known_good([pkg, ui])
        assert second.get("unchanged") is True, (
            "a 25MB tree is copied on every successful boot: %r" % second)

    def test_a_changed_tree_is_recopied(self, fake_tree):
        pkg, ui = fake_tree
        bg.snapshot_known_good([pkg, ui])
        (pkg / "services" / "thing.py").write_text("OK = False\n", encoding="utf-8")

        second = bg.snapshot_known_good([pkg, ui])
        assert second.get("unchanged") is not True
        bg.restore_known_good()
        assert (pkg / "services" / "thing.py").read_text(encoding="utf-8") == "OK = False\n"
