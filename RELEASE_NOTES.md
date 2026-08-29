# Agent Friday v5.6.6

*2026-08-29 · FutureSpeak.AI*

**If you upgraded to 5.6.5 today, your vault passphrase may have been deleted.
Read section 1 before you do anything else. If you have not upgraded yet,
upgrade to 5.6.6 and skip 5.6.5 entirely.**

5.6.5 fixed in-place upgrades, which had never delivered any code. That fix was
correct and it stays. But making the app-file copy actually *run* on an upgrade
made a second, older defect reachable for the first time: the copy deletes the
file your vault passphrase lives in.

This release is that fix, the wizard half of the same problem, and two smaller
defects of the identical shape. No feature work.

---

## 1. An upgrade could destroy your vault passphrase

### What happened

Your vault passphrase is written to `start.bat` inside Friday's own app folder,
and — unless you separately ran `friday vault-setup` — **nowhere else**. That is
deliberate: the wizard refuses to put it in a settings file, so it is not
committed or synced anywhere.

`app.copy` deletes the entire app folder and lays down a fresh copy. `start.bat`
is deliberately excluded from what ships, so it does not come back.

Before 5.6.5 this never bit, by accident: `app.copy` short-circuited on every
upgrade and the delete never ran. 5.6.5 fixed that short-circuit. From 5.6.5,
every in-place upgrade deletes `start.bat`.

Your data in `~/.friday/vault` is encrypted with AES-256-GCM under a key derived
from that passphrase with Argon2id. **The vault is never deleted. Only the key
is.** The files are still on your disk and they are still unreadable.

### Measured, on the two published zips

Published 5.6.3 installed, a real passphrase minted through the wizard's own
writer, a real note encrypted under it, then upgraded with published 5.6.5:

| | 5.6.3 → **5.6.5** | 5.6.3 → **5.6.6** |
|---|---|---|
| Installer exit code | 0 | 0 |
| Code actually updated | yes (5.6.3 → 5.6.5) | yes (5.6.3 → 5.6.6) |
| `start.bat` survived | **no** | **yes** |
| Passphrase survived | **no** | **yes** |
| Vault decrypts afterwards | **no** | **yes** |

Both runs reported success. One of them left the vault permanently unreadable.

### Are you affected

Only if **all** of these are true:

1. You upgraded in place by re-running the installer, using **5.6.5**, and
2. you had set a vault passphrase, and
3. you had not stored it via `friday vault-setup`.

Upgrades using 5.6.4 or earlier did not delete it — those installers copied
nothing at all, which is the defect 5.6.5 fixed. Fresh installs are unaffected.

### If it happened to you

**In order:**

1. **Check the OS keychain.** If you ever ran `friday vault-setup`, your
   passphrase is there and nothing is lost:
   ```
   friday vault-setup
   ```
   It will tell you if one is already stored.

2. **Check for another copy.** If you ever launched Friday from a shortcut you
   made yourself, or kept a `launch_now.bat` or `friday_startup.bat`, those hold
   the same `SET FRIDAY_PASSWORD=` line. 5.6.6 preserves all of them from now on.

3. **Check wherever you saved it.** If you accepted the wizard's generated
   passphrase, it was shown on screen once and written to `start.bat`. A password
   manager, a note, a screenshot.

4. **If none of those:** there is **no recovery**. This is not a lock we can pick
   — that is the property the encryption was chosen for. Argon2id + AES-256-GCM
   with no key is not recoverable by us, by you, or by anyone else.

   Your vault files stay where they are. Nothing deletes them, and 5.6.6 will not
   overwrite them. If the passphrase turns up later it will still work. Friday
   runs normally in the meantime; the vault stays locked.

We are sorry. This was ours, it was silent, and it looked like success.

---

## 2. The setup wizard could mint a new passphrase over an existing vault

A second, independent way to lose the same data.

`step_vault_password` opened with *"Generate a random passphrase for me?"*
defaulting to **Yes**, and never checked whether a vault already existed. The
installer runs the wizard on **every** run, including every upgrade. So pressing
Enter through an upgrade generated a fresh passphrase over a vault encrypted
under the old one.

Measured against 5.6.5, existing vault, every prompt answered with Enter:

```
original passphrase : the-users-original-passphrase
wizard returned     : gFwCZBGllhg2rcpVrdC7xgnHcbOYK5K4
vault decrypts      : False  (IntegrityError)
```

Every other step in that wizard already takes what is on disk and leaves a
settled answer alone. This one now does too:

- **Vault exists, passphrase found** (environment, `start.bat`, or the OS
  keychain) — it is kept, and checked against your actual encrypted data before
  being accepted. No prompt that can destroy anything.
- **Vault exists, passphrase not found** — the wizard **stops and explains**. You
  can type it, leave it unset (the default), or deliberately start a new vault —
  and that last one requires typing the word `abandon`. Pressing Enter never
  abandons anything.
- **No vault yet** — unchanged. A fresh install still gets a generated
  passphrase, which is the right default when there is nothing to lose.

---

## 3. Add/Remove Programs kept showing the old version

`Register-Uninstaller` writes `DisplayVersion`. `Test-UninstallerRegistered`
never read it back — it checked only that *an* entry existed pointing at a real
file. `Invoke-Step` runs verify before the action, so on every upgrade the
previous install's entry satisfied it, registration was skipped, and Windows went
on reporting the old version indefinitely.

Same defect as `app.copy`'s, one surface over — and this is a surface people
check to find out what they are running. The check now compares the version.

---

## 4. The install manifest recorded intentions, not outcomes

`install-manifest.json` is what the uninstaller reads to know what to remove. It
was written from what the installer *set out to do*. Because `Invoke-Step` skips
steps whose verify already passes, several of those things did not happen and the
file said they did. Three ways it was wrong, all now **measured** after the fact:

- **`version`** — recorded the version being installed even when `app.copy`
  short-circuited and the disk still held the old release. That is the paper
  trail of the 5.6.5 bug. Now read back from the installed `pyproject.toml`, with
  `installer_version` beside it. **If those two disagree, the copy did not take.**
- **`shortcuts`** — empty on every upgrade, because `Install-Shortcuts` did not
  re-run. The uninstaller therefore left four shortcuts on the machine after
  reporting a clean removal. Now enumerated from what exists.
- **`autostart_enabled`** — recorded your *answer*, not the state. Answering
  "No" on a machine that already started Friday at sign-in wrote `false` and
  changed nothing, so Friday kept starting and the uninstaller didn't know to
  remove the entry. **Answering No now actually turns it off.**

Manifest `schema_version` is now `2`.

---

## Still open, and it is Stephen's call

The passphrase living inside the app folder is what made section 1 possible.
Preserving the file across the copy fixes the symptom; the credential is still
stored in the one directory the installer deliberately destroys. Moving it —
to the OS keychain by default, or to `~/.friday` — is a real change with
migration consequences for every existing install, and it is not being made in
a hotfix. The options are written up in
`docs/design/vault-passphrase-location.md`.

Until then: **run `friday vault-setup`.** It puts your passphrase in the OS
keychain, which nothing in the installer touches.

---

## Upgrading

Download `AgentFriday-Setup-5.6.6.zip`, unzip anywhere, double-click
**Install Agent Friday.cmd**. Your notes, settings and connected accounts are
kept; the installer replaces Friday's own files and nothing under `~/.friday`.

If you are on 5.6.4 or earlier, your `start.bat` was never deleted — 5.6.6 will
preserve it. If you are on 5.6.5, read section 1 first.
