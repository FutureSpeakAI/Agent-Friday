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
4. Run the `installer` workflow on `main` (Actions → installer → Run workflow).
   It builds the zip on a fresh Windows runner and proves it on two more:
   `fresh-install` installs, starts Friday, checks first run (no login on
   localhost, the consent flow, scheduled jobs on a local seat, the phone
   off, agent.<name> once its hosts entry exists) and uninstalls;
   `upgrade` installs the previous published release, creates a vault,
   upgrades in place and requires the passphrase and data to survive. Both
   upload their `RESULT.json` as evidence. Certificate trust raises a Windows
   security dialog and is checked by hand on a real machine.
5. Commit, tag `v<version>`, and push the tag by name (never `--tags`). The
   tag must match `pyproject.toml`; the workflow refuses a mismatch.
6. The tag runs the same workflow. When both verification jobs pass it
   creates a **draft** release carrying the zip, its SHA-256 and
   `RELEASE_NOTES.md`. A draft is visible only to maintainers.
7. Review the draft, then publish it. Publishing is the public act; nothing
   in CI does it.

To build locally instead, use a clean worktree at the tag:

```powershell
git worktree add ..\friday-release v<version>
cd ..\friday-release\packaging\windows
powershell -NoProfile -ExecutionPolicy Bypass -File .\build-installer.ps1
```

The build aborts rather than producing a degraded artifact if the payload is
incomplete, the wheelhouse is empty, or anything credential-shaped survives
into the payload. It refuses to continue if any payload file is not tracked by
git at the commit being built or any tracked file has uncommitted changes. The
outer zip is not byte-reproducible (entry timestamps), so compare payload file
lists and per-file hashes, not the zip's hash. `packaging\windows\tests\Test-Installer.ps1`
runs the installer's own assertions.

## Branch protection

Recommended settings for `main` (Settings → Branches → Add rule):

- Require a pull request before merging, with at least one approval.
- Require status checks to pass: `pytest (windows-latest, py3.12)`,
  `pytest (ubuntu-latest, py3.12)`, `repository guards`, `package build`,
  and `codeql`.
- Require branches to be up to date before merging.
- Require linear history; block force pushes and branch deletion.
- Restrict who can push tags matching `v*` (Settings → Tags → protection
  rules), since a version tag starts a release.

## What never ships

`start.bat` and the other launch scripts, `.env` files, anything under
`~/.friday`, private keys, and root-level files whose name begins with an
underscore. The installer's exclusion list and the pre-commit scanner both
enforce this; the release checklist checks the built payload once more.
