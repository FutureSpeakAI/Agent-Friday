# Release process

## Versioning

Agent Friday's version is defined once, in `pyproject.toml`
(`project.version`, PEP 440: `1.0.1b1`); `package.json` mirrors it for the
Playwright tooling in npm's spelling (`1.0.1-beta.1`). Tags are the same
version in tag spelling, `v1.0.1-beta.1`, on `main`; the workflow normalises
one to the other and refuses a mismatch.

Releases are **ordered by build sequence, not by version number**: Beta 1.0.x is
numerically below the 5.x line it replaces. `BUILD_SEQUENCE` in
`src/agent_friday/release.py` is bumped by every release (a test holds it equal
to the sequence computed from the pyproject version), the setup program and the
update check both rank releases by it, and the release notes carry a
`Build sequence: N` line (the workflow adds it if missing).

## What a release contains

| Artifact | Built by | Supported |
|---|---|---|
| `AgentFriday-Setup-<tag>.exe` + `.sha256` (Windows setup program, Inno Setup 6, unsigned) | `packaging/windows/build-installer.ps1` | Yes, the primary distribution. |
| Source checkout (`pip install -e .`) | git | Yes, Windows, macOS, Linux (feature differences are in the README's platform section). |
| Wheel / `pip install agent-friday` | `python -m build` | Yes for the application code, CLI and the bundled seed skills; not the web UI (`index.html`, `static/`, `assets/` are not packaged). Not published to PyPI. |
| `AgentFriday.exe` (PyInstaller) |, | **Retired.** The last published binary predates current privacy fixes and should not be used. |

The full matrix, with what each path can and cannot do, is in
[Installation reference](../getting-started/installation.md).

## Cutting a release

1. Confirm `main` is green in CI and the full documented suite passes locally:
   `pytest tests/unit tests/api -q`.
2. Bump `project.version` in `pyproject.toml`, `version` in `package.json`,
   and `BUILD_SEQUENCE` and `RELEASE_TAG` in `src/agent_friday/release.py`.
3. Add the release's entry to `CHANGELOG.md` and rewrite `RELEASE_NOTES.md`
   for it. Move anything in `KNOWN_ISSUES.md` that the release resolves into
   the changelog entry.
4. Run the `installer` workflow on `main` (Actions → installer → Run workflow).
   It compiles the setup program with Inno Setup on a fresh Windows runner and
   proves it on two more: `fresh-install` installs silently (the cloud option,
   so nothing is downloaded), checks that both shortcuts exist, starts Friday,
   checks `/api/health` and first run (no login on localhost, the consent flow,
   scheduled jobs on a local seat, the phone off, agent.<name> once its hosts
   entry exists), uninstalls keeping the data, verifies the data is
   byte-identical, and reinstalls; `upgrade` runs once for each of the two previous lines, v5.14.3 and v1.0.0-beta.1:
   it installs that published release the way it ships (a zip for 5.x, the setup program for Beta 1.0), creates a vault, upgrades in
   place with the new exe, and requires the passphrase and data to survive, a
   backup to exist, and the shortcuts to land. The release waits for every leg. Both jobs upload their evidence.
   Certificate trust raises a Windows security dialog and is checked by hand on
   a real machine.
5. Commit, tag `v<version>`, and push the tag by name (never `--tags`). The
   tag must match `pyproject.toml`; the workflow refuses a mismatch.
6. The tag runs the same workflow. When both verification jobs pass, the
   release job (the only job with write permission) publishes a **pre-release**
   titled "Agent Friday Beta 1.0.1" carrying the exe, its SHA-256 and
   `RELEASE_NOTES.md`. It is public the moment it is created. Pushing the tag
   is the release decision.

To build locally instead, use a clean worktree at the tag:

```powershell
git worktree add ..\friday-release v<version>
cd ..\friday-release\packaging\windows
powershell -NoProfile -ExecutionPolicy Bypass -File .\build-installer.ps1
```

The build needs Inno Setup 6.3 or newer (`ISCC.exe`; GitHub's windows-latest
runners have it, or pass `-IsccPath`). It aborts rather than producing a
degraded artifact if the payload is incomplete, the wheelhouse is empty, or
anything credential-shaped survives into the payload. It refuses to continue if
any payload file is not tracked by git at the commit being built or any tracked
file has uncommitted changes, or if `release.py`'s build sequence disagrees
with the version in `pyproject.toml`. The compiled exe is not byte-reproducible,
so compare payload file lists and per-file hashes, not the exe's hash. `packaging\windows\tests\Test-Installer.ps1`
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
