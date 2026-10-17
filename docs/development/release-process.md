# Release process

## Versioning

Agent Friday's version is defined once, in `pyproject.toml`
(`project.version`, PEP 440: `1.2.0b1`); `package.json` mirrors it for the
Playwright tooling in npm's spelling (`1.2.0-beta.1`). Tags are the same
version in tag spelling, `v1.2.0-beta.1`, on `main`; the workflow normalises
one to the other and refuses a mismatch.

Releases are **ordered by build sequence, not by version number**: Beta 1.x is
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
| `release-manifest.json` | `scripts/release_manifest.py build`, run by the release job | Yes, published beside the exe. It is unsigned until an offline minisign key exists; see [Signing releases and public lists](release-signing.md). |

The setup program is **unsigned by decision**: the build job calls
`packaging/windows/sign-installer.ps1`, which signs only when every Artifact
Signing secret is set and otherwise prints that signing is not configured and
leaves the file alone. People verify the installer by its SHA-256, which the
release notes and the `.sha256` file both carry.

The full matrix, with what each path can and cannot do, is in
[Installation reference](../getting-started/installation.md).

## Cutting a release

1. **Make a release branch off `main`** named `release/v<version>`. Bring each
   item that ships in the release onto it as one squash commit with a neutral
   message that says what changed and the invariant it protects. Commit
   messages, branch names and comments never name a household member or their
   label for their agent; branch names and merge messages are as public as the
   code once pushed. Run the secret and personal-data scanner over the diff
   and the messages (`python .githooks/security_scan.py --tree`) before you
   push.
2. **Prepare the release on that branch**, in its own commit:
   - bump `project.version` in `pyproject.toml`, `version` in `package.json`,
     and `RELEASE_TAG`, `BUILD_SEQUENCE` and (for a new line) `RELEASE_NAME` in
     `src/agent_friday/release.py`. `tests/unit/test_release_sequence.py` holds
     `BUILD_SEQUENCE` and `RELEASE_TAG` to the pyproject version, and
     `build-installer.ps1` refuses to build when they disagree;
   - add the previous published release as a leg of the `upgrade` matrix in
     `.github/workflows/installer.yml` (both the matrix default and the
     `previous` input's description), so the new installer is proven to
     upgrade from it. For a new release line, also change the literal
     `--title` in the release job;
   - add the release's block to `CHANGELOG.md`, rewrite `RELEASE_NOTES.md`
     (keep the `<SHA256 PLACEHOLDER ...>` marker, which the release job
     replaces, and the `Build sequence: N` line), and move anything in
     `KNOWN_ISSUES.md` that the release resolves into the changelog block.
3. **Open a pull request from the release branch and dry-run the installer
   on it.** Run the `installer` workflow by hand on the release branch
   (Actions > installer > Run workflow). It runs every job except the
   release job, which only runs for a tag:
   - `build` compiles the setup program with Inno Setup on a fresh Windows
     runner, runs the signing step (a no-op without a certificate) and checks
     the `.sha256` matches the file;
   - `fresh-install` runs `packaging/windows/tests/verify-clean-install.ps1`
     on another fresh runner: a silent install with the cloud option (nothing
     is downloaded), both shortcuts, the Apps entry, `/api/health` at this
     release, no provider key configured, the vault-first consent flow, the
     setup chat, "Set up later", no Traceback and no ERROR line in the first
     boot's logs beyond the allowed ones, scheduled jobs on a local seat, the
     phone off, `agent.<name>` once its hosts entry exists, an uninstall that
     keeps `~/.friday` byte for byte, and a reinstall over it. Trusting
     Friday's certificate raises a Windows dialog and is checked by hand on a
     real machine;
   - `upgrade` runs `packaging/windows/tests/rehearsal/upgrade-vault-test.ps1`
     once for each published release in the matrix: it installs that release
     the way it shipped (the zip and `Install Agent Friday.cmd` for 5.14.3,
     the setup program for every Beta), creates a vault, upgrades in place
     with the new exe, and requires the passphrase and the data to survive, a
     backup to exist first, and the shortcuts to land.

   Every job uploads its evidence. A red job is fixed on the release branch
   and the dry run is repeated.
4. **Merge.** When the pull request's checks are green, merge it into `main`
   with a rebase merge, so `main` carries the neutral commits one by one.
   Then wait for the push run of `tests.yml` and `codeql.yml` on the new
   `main` commit.
5. **Hard gate: every GitHub check is green on the exact commit to be
   tagged.** That is pytest on all four runners (ubuntu-latest and
   windows-latest, Python 3.11 and 3.12), the app specs, CodeQL (no open
   alert introduced by the release), the package build and the repository
   guards, plus a green `installer` dry run on that same tree.
   A check is never relaxed, skipped, re-labelled or marked optional to get
   there; a red check is fixed at its cause, and a rerun counts only for a
   failure shown to be the runner's, not the code's. Any later commit resets
   the gate.
6. Commit, tag `v<version>`, and push the tag by name (never `--tags`). The
   tag must match `pyproject.toml`; the workflow refuses a mismatch.
7. The tag runs the same workflow. When both verification jobs pass, the
   release job (the only job with write permission) publishes the **latest
   release** (not a pre-release, so the README's download link lands on it)
   titled "Agent Friday Beta 1.2" carrying the exe, its SHA-256 and
   `RELEASE_NOTES.md`. It is public the moment it is created. Pushing the tag
   is the release decision.

The release job runs only after `fresh-install` and every `upgrade` leg have
passed on the tag. Before it publishes it adds the `Build sequence: N` line to
the notes if it is missing, replaces the SHA-256 placeholder with the real
hash (and fails if any placeholder is left), and writes
`release-manifest.json`. It publishes the exe, its `.sha256` and the manifest.

After publishing, a fix that lands on `main` later (for example a CodeQL
finding raised on the merged release pull request) is not in the published
installer; it ships with the next release or patch release, which repeats the
steps above with the previous release added as an upgrade leg.

To build locally instead, use a clean worktree at the tag:

```powershell
git worktree add ..\friday-release v<version>
cd ..\friday-release\packaging\windows
powershell -NoProfile -ExecutionPolicy Bypass -File .\build-installer.ps1
```

The build needs Inno Setup 6.3 or newer (`ISCC.exe`; pass `-IsccPath` if it
is not in the default place; the workflow installs it with Chocolatey when the
runner lacks it). It aborts rather than producing a
degraded artifact if the payload is incomplete, the wheelhouse is empty, or
anything credential-shaped survives into the payload. It refuses to continue if
any payload file is not tracked by git at the commit being built or any tracked
file has uncommitted changes, or if `release.py`'s build sequence disagrees
with the version in `pyproject.toml`. The compiled exe is not byte-reproducible,
so compare payload file lists and per-file hashes, not the exe's hash. `packaging\windows\tests\Test-Installer.ps1`
runs the installer's own assertions. `verify-clean-install.ps1` refuses to run
outside GitHub Actions, because it installs into the current profile and
edits the hosts file.

## Branch protection

Recommended settings for `main` (Settings → Branches → Add rule):

- Require a pull request before merging, with at least one approval.
- Require status checks to pass, by the job names the workflows give them:
  `pytest (windows-latest, py3.11)`, `pytest (windows-latest, py3.12)`,
  `pytest (ubuntu-latest, py3.11)`, `pytest (ubuntu-latest, py3.12)`,
  `app specs (Settings walk, See & Touch)`, `repository guards`,
  `package build`, `analyze (python)` and `analyze (javascript)`.
- Require branches to be up to date before merging.
- Require linear history; block force pushes and branch deletion.
- Restrict who can push tags matching `v*` (Settings → Tags → protection
  rules), since a version tag starts a release.

## What never ships

`start.bat` and the other launch scripts, `.env` files, anything under
`~/.friday`, private keys, and root-level files whose name begins with an
underscore. The installer's exclusion list and the pre-commit scanner both
enforce this; the release checklist checks the built payload once more.
