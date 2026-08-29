# Upgrade rehearsal

Tests that install a **real published installer** into an isolated root and then
upgrade it, to prove that an in-place upgrade delivers new code without
destroying anything the user already had.

They exist because two releases in a row shipped a defect that every other kind
of test missed, and both were only ever going to be caught by actually
performing an upgrade:

- **5.6.0–5.6.4** copied no files at all on an upgrade, then wrote the new
  version number and exited 0. Fixed in 5.6.5.
- **5.6.5** fixed that — correctly — and by making the copy step run for the
  first time, made it delete `app\start.bat`, which is the **only** automatic
  home of the vault passphrase. `~/.friday/vault` kept its ciphertext and lost
  its key. Fixed in 5.6.6.

Both reported success. Unit tests passed throughout. Only an upgrade shows it.

> These are **not** part of `pytest` or `Test-Installer.ps1`. Each full run takes
> 15–25 minutes and downloads an embedded Python plus a dependency tier. Run them
> when touching `app.copy`, the setup wizard's vault step, the install manifest,
> or anything that decides whether an install step is needed.

---

## `upgrade-vault-test.ps1` — the main harness

Installs a published base zip, mints a passphrase through the wizard's **own**
`_write_start_bat`, encrypts a real artifact under the key it derives, upgrades
with a second zip, then answers three questions:

1. did the app code actually update?
2. did `start.bat` survive?
3. does the vault still decrypt?

```powershell
# Prove the defect against a published build:
.\upgrade-vault-test.ps1 -BaseZip ...\AgentFriday-Setup-5.6.3.zip `
                         -UpgradeZip ...\AgentFriday-Setup-5.6.5.zip `
                         -Root C:\temp\rehearsal-565 -Label FAIL-565

# Prove the fix, and prove a fresh install still works:
.\upgrade-vault-test.ps1 -BaseZip ...\5.6.3.zip -UpgradeZip ...\5.6.6.zip -Root ... -Label FIX
.\upgrade-vault-test.ps1 -BaseZip ...\5.6.6.zip -UpgradeZip ...\5.6.6.zip -Root ... -Label FRESH
```

Get the zips with `gh release download v5.6.3 -p '*.zip' -D <dir>`. Results land
in `<Root>\RESULT.json`; installer output in `<Root>\run-{base,upgrade}.log`.

Measured 2026-08-29 on the published assets:

| | → 5.6.5 | → 5.6.6 |
|---|---|---|
| installer exit | 0 | 0 |
| code updated | yes | yes |
| `start.bat` survived | **no** | yes |
| vault decrypts | **no** | yes |

### Read this before running it

- **It writes to real machine-wide locations.** `[Environment]::GetFolderPath`
  ignores a redirected `USERPROFILE`, so the install creates a **real desktop
  shortcut**, a **real Start Menu folder**, and
  `HKCU:\...\Uninstall\AgentFriday` pointing into your temp root. Remove all
  three afterwards, and check each target is a test path before deleting.
- **It asserts its own isolation.** Before it writes a passphrase or a vault it
  checks that Python resolves `Path.home()` to the redirected profile, and stops
  if it does not. That check is the only thing standing between a test run and
  the developer's real `~/.friday`. **Do not weaken it.**
- `-Unattended` is deliberate: it skips the setup wizard, isolating the single
  question of whether `app.copy` destroys the file. The wizard's own behaviour is
  covered by the two scripts below, because it cannot be driven headlessly.
- The base install downloads ~800 MB. Both installs share nothing.

---

## `wizard-enter-only.py` — the wizard half, across the fix

Runs against whatever `setup_wizard.py` is checked out, so it shows the
behaviour **changing**:

- **≤5.6.5** — returns a new random passphrase; the vault then fails to decrypt.
- **≥5.6.6** — returns nothing and stops to explain.

Every prompt is answered with Enter, because the input that destroyed vaults was
the *default*, not a mistake. Exits 1 when it detects data loss.

```
python packaging/windows/tests/rehearsal/wizard-enter-only.py
```

To see the failure, check out a pre-5.6.6 `setup_wizard.py` and run it again:

```
git show v5.6.5:src/agent_friday/setup_wizard.py > src/agent_friday/setup_wizard.py
```

## `wizard-scenarios.py` — the two upgrade landings (5.6.6+)

`start.bat` preserved → keeps the original passphrase, verified against real
ciphertext. `start.bat` already destroyed → returns nothing rather than minting a
replacement. Exits 1 if either mints a passphrase over an existing vault.

```
python packaging/windows/tests/rehearsal/wizard-scenarios.py
```

Both scripts build a genuine vault — real Argon2id key, real AES-256-GCM
ciphertext, via the product's own `vault_crypto` — in a fresh temp directory.
Nothing touches `~/.friday`.

---

## What is still only covered by convention

`tests/unit/test_vault_passphrase_rerun.py` pins the wizard's branches, and
`Test-Installer.ps1` asserts that the files `app.copy` preserves are exactly the
secret-bearing set the payload excludes. Neither of those is an upgrade. **The
only thing that proves an upgrade is an upgrade**, which is the whole reason
this directory exists.

Not yet covered, and worth adding when the vault passphrase relocates out of the
app directory (see `docs/design/vault-passphrase-location.md`): a static
assertion that the passphrase's location is not inside `$InstallRoot`, and a run
of this harness pointed at the new location. Neither alone is sufficient — a
static check cannot prove no installer step reaches the path, and a single run
only exercises the paths it happens to take.
