"""The installer build must ship the committed tree, nothing else.

Audited 2026-09-06: the published 5.12.0 and 5.13.0 zips each carried ~290
files that existed only on the developer's machine (a second git repo with
its own .git/, seven gitignored token files, Claude memory, PowerShell
caches, local-only handoff docs). The copy loop snapshots the working tree
and the exclusion lists only catch what someone already named.

build-installer.ps1 now refuses to build unless every payload file is
tracked at HEAD and no tracked file is modified. Proven live: on the dirty
developer tree the build aborted at step 2/5 naming 268 strays; on a clean
worktree it passed. This test pins the guard's presence and its position
(before the credential scan, so a stray can never be "cleared" by a later
step) so it cannot be quietly removed.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "packaging" / "windows" / "build-installer.ps1"


def test_build_refuses_untracked_or_modified_payload_files():
    text = SCRIPT.read_text(encoding="utf-8", errors="replace")
    assert "Verifying the payload is the committed tree" in text
    assert re.search(r"git -C \$RepoRoot .*ls-files -z", text), "guard must enumerate tracked files with ls-files -z"
    assert "status --porcelain --untracked-files=no" in text, "guard must also refuse modified tracked files"
    assert "-FailedStep 'build.trackedtree'" in text, "guard must abort through the build report like every other step"
    guard_at = text.index("Verifying the payload is the committed tree")
    scan_at = text.index("$leakPatterns")
    zip_at = text.index("CreateFromDirectory")
    assert guard_at < scan_at < zip_at, "guard must run before the credential scan and long before the zip"


def test_guard_fails_when_git_is_missing_rather_than_skipping():
    text = SCRIPT.read_text(encoding="utf-8", errors="replace")
    block = text[text.index("Verifying the payload is the committed tree"):text.index("$leakPatterns")]
    assert "Get-Command git" in block and "exit 1" in block.split("git is not on PATH")[1][:900]
