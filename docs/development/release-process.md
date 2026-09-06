# Release process

## Versioning

Agent Friday uses semantic versions. The version is defined once, in
`pyproject.toml` (`project.version`); `package.json` mirrors it for the
Playwright tooling. Tags are `v<major>.<minor>.<patch>` on `main`.

## What a release contains

| Artifact | Built by | Supported |
|---|---|---|
| `AgentFriday-Setup-<version>.zip` (Windows installer) | `packaging/windows/build-installer.ps1` | Yes — the primary distribution. |
| Source checkout (`pip install -e .`) | git | Yes — Windows, macOS, Linux (feature differences are in the README's platform section). |
| Wheel / `pip install agent-friday` | `python -m build` | Yes for the application code, CLI and the bundled seed skills; not the web UI (`index.html`, `static/`, `assets/` are not packaged). Not published to PyPI. |
| `AgentFriday.exe` (PyInstaller) | `AgentFriday.spec` | **Not supported.** The recipe is kept for reference; the last published binary predates current privacy fixes and should not be used. |

The full matrix, with what each path can and cannot do, is in
[Installation](../getting-started/installation.md).

## Cutting a release

1. Confirm `main` is green in CI and the full documented suite passes locally:
   `pytest tests/unit tests/api -q`.
2. Bump `project.version` in `pyproject.toml` and `version` in `package.json`.
3. Add the release's entry to `CHANGELOG.md` and rewrite `RELEASE_NOTES.md`
   for it. Move anything in `KNOWN_ISSUES.md` that the release resolves into
   the changelog entry.
4. Commit, tag `v<version>`, and push the tag by name (never `--tags`).
5. Build the Windows installer from a **clean worktree at the tag**:

   ```powershell
   cd packaging\windows
   powershell -NoProfile -ExecutionPolicy Bypass -File .\build-installer.ps1
   ```

   The build aborts rather than producing a degraded artifact if the payload
   is incomplete, the wheelhouse is empty, or anything credential-shaped
   survives into the payload. It excludes launch scripts, `.env`, key files,
   tests, packaging sources, and root-level scratch files by pattern.

   "Clean worktree" is enforced, not advised: the build refuses to continue
   if any payload file is not tracked by git at the commit being built or
   any tracked file has uncommitted changes, and names the strays. A
   long-lived working tree will fail this on purpose; use
   `git worktree add <dir> v<version>` and build from there. The outer zip
   is not byte-reproducible (entry timestamps), so compare payload file
   lists and per-file hashes, not the zip's hash.
6. Run the installer tests: `packaging\windows\tests\Test-Installer.ps1`.
7. Create the GitHub release for the tag and attach the zip.

## What never ships

`start.bat` and the other launch scripts, `.env` files, anything under
`~/.friday`, private keys, and root-level files whose name begins with an
underscore. The installer's exclusion list and the pre-commit scanner both
enforce this; the release checklist checks the built payload once more.
