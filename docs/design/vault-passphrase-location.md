# Where the vault passphrase should live

**Date:** 2026-08-29 · **Status:** decision pending, Stephen's call.
**Not implemented.** 5.6.6 preserves `start.bat` across an upgrade. It does not
move the credential. This document exists so the move is decided deliberately
rather than as a reflex to the incident that prompted it.

---

## The property that caused the incident

The vault passphrase is written by `setup_wizard._write_start_bat` to
`PROJ_ROOT / "start.bat"`. For a packaged install `PROJ_ROOT` resolves to
`%LOCALAPPDATA%\AgentFriday\app` — **the one directory the installer deletes and
replaces on every upgrade.**

Two independent facts make that fatal rather than merely untidy:

1. `_persist` strips `vault_password` before writing settings, on purpose:
   *"it lives only in start.bat as a FRIDAY_PASSWORD env var so it is not
   committed or version-controlled"* (`setup_wizard.py:1197`). So `start.bat` is
   not a cache of the passphrase. It is the passphrase's **only** automatic home.
2. `start.bat` is in `Get-PayloadExcludes` — correctly, because it holds
   secrets and must never ship. So the copy that replaces the folder cannot
   restore it.

Delete the folder and the key is gone while `~/.friday/vault` — untouched by the
installer, by design — keeps every encrypted byte. Argon2id + AES-256-GCM with
no key is not recoverable.

5.6.6 closes the hole by carrying the file across the delete. **The design
property is unchanged: a credential lives inside a directory whose contract is
"this gets destroyed and rebuilt."** Every future change to `app.copy`, every new
step that touches `$AppDir`, and the self-patching installer in
[`self-patching-installer.md`](self-patching-installer.md) all have to keep
remembering that. That is the argument for moving it, and it is the only one
that matters — the rest is trade-offs.

---

## What already exists

- **`friday vault-setup`** stores the passphrase in the OS keychain via
  `keyring.set_password("agent-friday", "vault-passphrase")` (`cli.py:1091`).
  This is the copy that survives everything the installer does. It is **opt-in
  and separately invoked**, so most installs do not have it.
- **`_bootstrap_env_from_launch_scripts()`** (`core/__init__.py:753`) reads
  `start.bat`, `launch_now.bat`, `friday_startup.bat` at package import and
  force-overrides API keys from them. This is what makes `start.bat` load-bearing
  at boot, not just at setup.
- **5.6.6's `_existing_vault_password()`** already reads all three sources in
  priority order: environment → `start.bat` → keychain. **A migration has a
  reader already written**, which materially lowers the cost of options 1 and 2.

---

## Options

### 1. OS keychain as the primary home

Write to `keyring` at setup; read from it at boot; stop writing the passphrase
to `start.bat` at all.

- **For.** Survives the app folder being replaced. It is the mechanism already
  shipped, just promoted from opt-in to default. DPAPI-backed on Windows, so it
  is encrypted at rest per-user rather than sitting in a readable `.bat`. Removes
  a plaintext credential from disk entirely — which is a security improvement
  independent of the upgrade problem, and the same direction as 8977bac.
- **Against.** `keyring` is a dependency that can fail to import or fail at
  runtime on an unusual profile; the fallback path has to be designed, not
  assumed. A passphrase in a keychain is **less visible** to the user — the
  current `start.bat` is at least something they can open and read, and for a
  passphrase whose loss is unrecoverable, discoverability has real value. Roaming
  profiles and reimaged machines lose the keychain.
- **Migration.** On first 5.6.6+ boot with a passphrase found in `start.bat` and
  none in the keychain: copy it in, then rewrite `start.bat` without the
  `FRIDAY_PASSWORD` line. Reversible until that rewrite; the rewrite is the
  irreversible step and wants to be separately confirmed.

### 2. `~/.friday/` as the primary home

A file beside the vault it unlocks — the directory the installer already treats
as untouchable.

- **For.** Smallest change. No new dependency, no new failure mode. Consistent
  with everything else user-owned. Trivially inspectable, so no loss of
  discoverability.
- **Against.** **It is a plaintext credential on disk**, exactly as `start.bat`
  is — it has moved somewhere safer from the installer, not somewhere safer from
  anything else. Worse in one specific way: it sits *next to the ciphertext it
  decrypts*, so one folder read yields both halves. That is the arrangement the
  encryption was meant to prevent. Mitigable with DPAPI (`CryptProtectData`),
  which is roughly option 1 without the keychain's UI.
- **Migration.** Same shape as option 1.

### 3. Keychain primary, `~/.friday` fallback

Option 1, degrading to option 2 (DPAPI-wrapped) when `keyring` is unavailable.

- **For.** No single point of failure; both migration paths already have a
  reader.
- **Against.** Two homes means a precedence rule, and a precedence rule between
  two credential stores is exactly the shape of the bug already recorded in
  `credential_store.py:379` — *"A KEY SAVED IN SETTINGS BEATS ONE START.BAT PUT
  THERE"* — where two stores disagreed and the stale one won for months. Adding a
  third store to a system that already has this bug class is the risk.

### 4. Do nothing more

5.6.6's preservation holds. Recommend `friday vault-setup` in the docs.

- **For.** Zero migration risk. The incident is closed.
- **Against.** The preservation list is a hard-coded set of filenames that has to
  stay in sync with `Get-PayloadExcludes` by hand. Nothing enforces that. A file
  added to one and not the other reopens this silently — and "silently" is what
  made this cost a day.

---

## What is not in question

Whatever is chosen, two things should hold:

- **The wizard must never generate a passphrase over an existing vault.** Fixed
  in 5.6.6 and independent of where the credential lives.
- **The installer should never be able to delete the only copy of a credential.**
  Option 4 achieves this by convention; 1–3 achieve it structurally.

## The one measurement that would settle it

Nobody knows how many installs have a keychain copy, because `friday vault-setup`
is opt-in and nothing reports it. `friday status` does not check. If it did —
one line, "vault passphrase: stored in keychain / only in start.bat / not set" —
the answer would be visible on any machine before deciding, and it would tell an
affected 5.6.5 user, at a glance, whether they are recoverable. That is worth
doing regardless of which option wins, and it belongs with making `friday status`
the version-truth surface.
