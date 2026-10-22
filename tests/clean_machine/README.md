# Clean-machine restore (CI only)

`.github/workflows/restore.yml` runs these on fresh GitHub-hosted VMs:

1. `seed_home.py` builds a realistic Friday data folder on machine A (a
   sealed vault, memory, settings, a skill, a workspace, a conversation,
   receipts in the action ledger, and credentials that must NOT travel), then
   `friday bundle create` makes a Friday Bundle of it.
2. On machine B, a different fresh VM that never saw A, `friday restore`
   restores it, and `check_restore.py` checks every file, the vault opening
   with the passphrase the bundle carried, the receipts verifying under the
   governance key the bundle carried, outward actions held until resumed, and
   that nothing that must not travel did.
3. A bundle with one byte changed is refused and writes nothing.

They refuse to run outside CI (`GITHUB_ACTIONS=true`), because they write to
the OS keychain of the machine they run on.
