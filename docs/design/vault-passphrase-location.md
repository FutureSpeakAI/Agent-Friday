# Relocating the vault passphrase out of the app directory

**Date:** 2026-08-29
**Decision:** *made.* Stephen, 2026-08-29: *"yeah, that def needs to live in a
safe place that can never get overwritten."* The passphrase moves out of the app
directory.
**Scheduled:** the release **after** 5.6.6. Not in the hotfix — 5.6.6 ships
preservation plus the wizard fix to stop the bleeding tonight; a relocation with
migration consequences for every existing install is not a thing to rush.
**Status of this document:** analysis for the next session. **Nothing here is
implemented, and none of it should be implemented as part of 5.6.6.**

---

## 1. Why, in one paragraph

`setup_wizard._write_start_bat` writes the passphrase to `PROJ_ROOT/start.bat`.
For a packaged install `PROJ_ROOT` is `%LOCALAPPDATA%\AgentFriday\app` — the one
directory whose contract is *"this gets recursively deleted and rebuilt."*
`start.bat` is correctly excluded from the payload, so the copy cannot restore
it. `~/.friday/vault` is AES-256-GCM under an Argon2id key derived from that
passphrase and is never touched by the installer. Delete the folder and the
ciphertext survives while its only key does not. 5.6.6 carries the file across
the delete; the credential still lives in the condemned building.

---

## 2. Ground truth — what actually reads and writes it today

Verified by reading, 2026-08-29, at v5.6.6.

### Writers

| where | what it writes | file:line |
|---|---|---|
| `setup_wizard._write_start_bat` | `SET FRIDAY_PASSWORD=` into `app\start.bat` | `setup_wizard.py:1229` |
| `cli.cmd_vault_setup` | `keyring.set_password("agent-friday", "vault-passphrase")` | `cli.py:1091` |
| `routes/core_routes.py` | same keyring entry | `core_routes.py:639` |
| `routes/insights.py` | same keyring entry | `insights.py:309` |

### Readers — and they do not agree

| consumer | resolution order | reads keyring? |
|---|---|---|
| `services/agent.py::_get_vault_key` | `FRIDAY_VAULT_PASSPHRASE` env → **then** keyring | yes, as *fallback* |
| `services/credential_store.py::_vault_key` | `FRIDAY_PASSWORD` env → `core.FRIDAY_PASSWORD` | **no — never** |

Two findings fall out of that table, and both are load-bearing for the work:

**F1. `agent.py`'s comment contradicts its code.** The docstring says *"Tries the
OS keychain (keyring package) before the environment variable so the passphrase
never needs to appear in a shell script"* (`agent.py:4854`). The code sets
`_passphrase = FRIDAY_VAULT_PASSPHRASE` first and consults keyring only when that
is empty (`agent.py:4870-4876`). **The environment wins, so `start.bat` wins.**
The intended design is already written down; only the order is wrong. Relocation
is largely a matter of making the code do what the comment already claims.

**F2. `credential_store` never consults the keyring at all.** So a user whose
passphrase exists *only* in the keychain gets a working Sovereign Vault
(`agent.py` finds it) and a credential store that silently drops to DPAPI
(`_friday_password()` returns `""` → `_VAULT_KEY = None`). That is a degradation
rather than a loss — DPAPI is a legitimate tier-2 in that module's own
precedence — but it means **`friday vault-setup` alone does not fully restore an
install today.** Any relocation that does not unify these two resolvers will
half-work, in a way that is invisible until someone inspects which cipher a blob
was written with.

### One more consequence worth stating plainly

When no passphrase is found, `_get_vault_key()` returns `None` and **callers fall
back to plaintext** (`agent.py:4878-4893`) — with an ERROR log and a persistent
`/api/health` banner, but they do fall back. So losing the passphrase does not
merely lock the old data: new sensitive data starts being written unencrypted.
That is the correct fail-open choice for availability, and it raises the stakes
on "how does a user notice", which §6 picks up.

---

## 3. The requirement Stephen set, and what it has to mean

> *whatever location wins has to be somewhere the installer provably cannot
> delete — and "provably" should mean a test.*

This is the whole lesson. `start.bat` was not carelessly placed; it was placed
somewhere everyone believed was safe, and the belief was never checked against
what `app.copy` actually does. A convention that nobody tests is how we got here,
and a second convention would fail the same way.

Two tests, because they catch different failures:

**T1 — static, at build time.** The configured passphrase location must not be
inside `$InstallRoot`. A path-containment assertion in
`packaging/windows/tests/Test-Installer.ps1`, beside the one 5.6.6 added for the
preserve/exclude lists. Catches a bad path the moment it is written, in CI, with
no install required.

**T2 — dynamic, end to end.** The published-asset upgrade rehearsal built for
5.6.6 already does exactly this shape: install a real prior version into an
isolated root with a redirected profile, put a real passphrase in the real place,
encrypt a real artifact under it, upgrade with the real installer, then assert
the passphrase is intact and the ciphertext still decrypts. Point it at the new
location and it becomes the relocation's proof. The harness is
`upgrade-vault-test.ps1`; it should move into the repo rather than staying a
scratch file, since it is now the only thing standing between us and a repeat.

**T1 alone is not enough.** It proves the path is outside a directory; it does
not prove no installer step reaches it — a future `Remove-Item` on `~/.friday`,
a "clean reinstall" option, or an uninstaller that over-collects would all pass
T1 and fail T2. **T2 alone is not enough either:** it only exercises the paths a
test run happens to take. Both.

---

## 4. The options

Each is judged on Stephen's three questions: **what happens to an existing
passphrase during the move**, **what happens if the move is interrupted**, and
**how a user recovers if the new location is lost.**

---

### Option A — OS keyring as primary (Windows Credential Manager via `keyring`)

Read from the keychain first, write there at setup, stop writing the passphrase
to `start.bat`.

**Closest to done.** The writer exists in three places, the reader exists in
`agent.py`, and 5.6.6's `_existing_vault_password()` already reads all three
sources in priority order. The work is: flip `agent.py`'s order to match its own
comment (F1), teach `credential_store` to use the same resolver (F2), and stop
`_write_start_bat` emitting the `FRIDAY_PASSWORD` line.

- **Existing passphrase during the move.** On first boot after upgrade: if a
  passphrase is found in env/`start.bat` and the keychain has none, copy it in,
  verify by reading it back, *then* rewrite `start.bat` without the line. Nothing
  is removed until the new copy is confirmed present.
- **If the move is interrupted.** Safe in every ordering, because the copy is
  additive and the removal is last. Interrupted before the write: nothing
  changed. Between write and verify: both copies exist; next boot re-verifies and
  proceeds. Between verify and rewrite: both copies exist and the old one is
  still authoritative under today's precedence — the next boot finishes the job.
  **The only irreversible step is the `start.bat` rewrite, and it is the last
  one.** That ordering is the whole safety argument and should be a comment in
  the code, not folklore.
- **If the new location is lost.** The keychain is per-user and per-machine. It
  is lost on: a reimaged machine, a new Windows profile, a restored-from-backup
  home directory without the credential store, and some roaming-profile setups.
  Recovery is only from whatever the user separately kept — which today is
  nothing. **This is Option A's real weakness and it is not small**, because the
  passphrase becomes *less* visible than a `.bat` file the user could open and
  read. Mitigation is §5's recovery code, and A should not ship without it.
- **Other risks.** `keyring` is an optional dependency (`cli.py:1067` tells the
  user to `pip install 'agent-friday[keyring]'`), so the import can fail. A
  primary store that might not be importable needs a defined fallback — which is
  Option D.

---

### Option B — DPAPI-encrypted file under `~/.friday`

`~/.friday/vault/.passphrase` (or similar), wrapped with `CryptProtectData`.

The machinery exists: `credential_store._dpapi()` already calls
`CryptProtectData`/`CryptUnprotectData` through ctypes with no extra dependency,
and its blobs are self-describing (`FRIDAYDPAPI\x01`) so a reader always knows
how a blob was written.

- **Existing passphrase during the move.** Same additive shape as A: write the
  wrapped file, verify by unwrapping, then rewrite `start.bat`.
- **If the move is interrupted.** Same argument, with one addition — the file
  write must be atomic (`.tmp` + `replace`, the pattern already used for
  `.vault_config.json` at `agent.py:4903-4906`), or an interrupted write leaves a
  truncated blob that unwraps to garbage. A half-written credential is worse than
  an absent one because it looks present.
- **If the new location is lost.** DPAPI is bound to the OS login account, so it
  is lost in the same cases as the keychain **plus** a Windows password reset
  performed by an administrator rather than by the user. Recovery: none, without
  §5.
- **Against.** No `keyring` dependency and no UI surface, but it is Windows-only
  in the same way, and `~/.friday` is a directory users copy between machines
  expecting it to work — a DPAPI blob silently will not, and the failure looks
  like "my vault is empty" rather than "this file cannot be read here." That
  failure mode deserves an explicit message.

---

### Option C — plaintext file under `~/.friday`

Named to be rejected, because it is the obvious move and it is wrong.

It solves the installer problem and nothing else: the key would sit **in the same
directory as the ciphertext it decrypts**, so a single folder read yields both
halves. That is precisely the arrangement encryption-at-rest exists to prevent,
and it is a regression against `start.bat`, which at least lived somewhere else.
Migration and interruption are trivial; that is not enough. **Do not ship this.**

---

### Option D — keyring primary, DPAPI file fallback (A, then B)

Use the keychain when `keyring` imports and works; otherwise the DPAPI file.

- **For.** No single point of failure, both readers already exist, and it removes
  Option A's "what if the optional dependency is absent" hole.
- **Against, and this is the specific risk.** Two stores means a precedence rule,
  and this codebase has already been bitten by exactly that: *"A KEY SAVED IN
  SETTINGS BEATS ONE START.BAT PUT THERE"* (`credential_store.py:379`) documents
  months of a stale store winning over a fresh one, with the UI reporting
  "connected" the whole time. Adding a third credential store to a system with a
  live precedence bug is how that bug gets a sibling.
- **Only acceptable with a single resolver.** One function, one documented order,
  every consumer calling it — no module deciding for itself, which is the F1/F2
  situation today. If D is chosen, unifying the resolvers is not cleanup to do
  afterwards; it is the precondition.

---

### Option E — no stored passphrase: bind the vault key to the OS account

Genuinely different, and worth putting on the table rather than assuming a
passphrase must exist. Drop the user passphrase entirely; generate a random vault
key and DPAPI-wrap it.

- **For.** **It deletes the entire failure class.** There is nothing for a user
  to forget, nothing to write down, nothing to lose — and no wizard step that can
  destroy anything, which is the other half of the defect 5.6.6 just fixed.
- **Against.** It changes the threat model, and not by a little. Today the vault
  resists someone who has read access to the disk *and* is logged in as the user,
  because they still lack the passphrase. Under E, anything running in the user's
  session can decrypt the vault — including Friday's own tool loop, which runs
  `claude --dangerously-skip-permissions` in Vibe Code. It also makes the vault
  unmovable between machines by design.
- **Verdict.** Not a drop-in replacement — it is a product decision about what
  the vault protects against, and it should be decided as one rather than adopted
  because it is convenient. Reasonable as an *option offered* alongside a
  passphrase ("protect with my Windows login" vs "protect with a passphrase"),
  which is how comparable products put it.

---

## 5. The thing every option needs: a recovery code

All four viable options answer "what if the new location is lost" with *nothing*,
because today there is no second copy by construction. Moving the passphrase
somewhere safer from the installer makes it **less** visible to the user, so the
loss case gets more likely, not less, unless something is done about it.

The standard answer: at setup, show a recovery code once and require the user to
confirm they have stored it. Either it *is* the passphrase (shown, then stored
for them), or it is an independent key-encryption key the vault key is also
wrapped under. This is orthogonal to A/B/D — it works with any of them — and it
is the only thing that makes "lost the machine" survivable.

**It should ship in the same release as the relocation**, not after. A relocation
without it trades a visible fragile store for an invisible fragile store.

---

## 6. Work required regardless of which option wins

1. **One resolver.** A single `vault_passphrase()` with one documented precedence
   order, used by `agent.py` and `credential_store.py` alike. F1 and F2 are two
   modules disagreeing about the same secret; relocation on top of that
   disagreement will half-land. **Do this first, before moving anything** — it is
   independently correct, it is testable on its own, and it shrinks the
   relocation to a change of one order.
2. **Fix F1's comment-versus-code mismatch** as part of that, and say in the
   comment which order is intended and why.
3. **T1 + T2 from §3.** T1 is cheap and belongs in the same commit as the new
   path. T2 means moving `upgrade-vault-test.ps1` into the repo.
4. **`friday status` should report where the passphrase lives** — "in the OS
   keychain / only in start.bat / not set". Nobody currently knows how many
   installs have a keychain copy, because `vault-setup` is opt-in and nothing
   reports it. That number decides how much migration code is worth writing, and
   it is the same line that tells a 5.6.5 casualty whether they are recoverable.
   It belongs with making `friday status` the version-truth surface.
5. **Decide what the migration does about a user who has no passphrase at all**
   (vault disabled, data at rest in plaintext today). Relocation is a natural
   moment to offer encryption to people who never enabled it — and a bad moment
   to enable it silently.

---

## 7. Open questions for the next session

- **Q1.** Is Option E offered as a choice, or is a passphrase mandatory? This is
  a threat-model question and it is Stephen's. Everything else follows from it.
- **Q2.** Recovery code in the same release, or does relocation ship without one?
  §5 argues same release; the counter-argument is scope.
- **Q3.** Does `start.bat` keep the *API keys* after the passphrase leaves? They
  have an encrypted second home (`~/.friday/providers/keys/`) that the passphrase
  does not, so the answer can differ — but `_bootstrap_env_from_launch_scripts`
  force-overrides those keys from `start.bat` at import (`core/__init__.py:762`),
  which is its own stale-value problem and out of scope here.
- **Q4.** What does an existing install with a passphrase in *two* places do when
  they disagree? Today `start.bat` wins by accident of ordering. After
  unification it should be a deliberate, documented answer — and probably an
  explicit warning, since disagreement means one of them will not open the vault.

---

## 8. Provisional shape, for the next session to argue with

Not a decision. Stated so there is something concrete to attack rather than a
blank page:

**Option D (keyring primary, DPAPI file fallback), preceded by the single
resolver from §6.1, shipped together with §5's recovery code, and proved by both
T1 and T2.** It reuses machinery that already exists in the codebase, it has no
new dependency in the fallback path, and the precedence risk that makes D
dangerous is exactly what §6.1 removes first. Option E is the better answer if
Q1 comes back as "a passphrase is a burden users should not carry" — and that is
a real position, not a lesser one.
