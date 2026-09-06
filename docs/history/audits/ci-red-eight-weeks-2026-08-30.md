# CI was red for eight weeks and ran no tests for the last two

**2026-08-30.** Fixed in PR #4 (`9d96d23`). This note exists because the fix is
the small part; the reason nobody noticed is the part worth keeping.

## What happened

`tests` was red on **every push to `main` from 2026-07-04 to 2026-08-30**. The
last green run was `563c26e`. For the final stretch it was worse than red: from
2026-08-26 it executed **zero tests** on all four pytest legs.

`tests/unit/test_tray_restart_debounce.py` (added 2026-08-13, `8f53f92`)
imports `friday_tray`, which does `import pystray` at module scope. `pystray`
was never in the workflow's hand-curated `pip install` list, so **collection
aborted** before a single test ran. `pystray` is a genuine shipped dependency
(`packaging/windows/requirements/core.txt`, `pyproject.toml` under
`sys_platform == 'win32'`), so installed copies always had it. Only CI didn't.

Between 07-07 and 08-26 nothing was pushed to `main` at all. Branch work was
never gated either: across 100 runs the trigger events are only `push`,
`dynamic` (Dependency Graph) and `pull_request`, and there has been exactly
**one** `pull_request` event in the repo's history — PR #4, on 2026-08-30.

## Every release since 5.2.0 shipped past it

All four of 2026-08-29's tags sit on commits whose CI run had already failed:

| tag | commit | CI verdict at that SHA |
|---|---|---|
| v5.6.4 | `f60ee0d` | failure |
| v5.6.5 | `ca7cb7a` | failure |
| v5.6.6 | `15e9faa` | failure |
| v5.7.0 | `0f64fc7` | failure |

Nothing in the release path consults CI. `tests.yml` is the **only** workflow
in the repo — there is no build or release workflow — and the installer zip is
built locally from a clean worktree at the tag.

## The gap was enforcement, not signal

The pipeline was not silent. It failed loudly on every push and sent an email
every time. Nobody was missing information; nothing was mechanically stopped.

`main` is unprotected (`gh api .../branches/main/protection` → 404). Everything
is pushed straight to it, tags are cut from whatever is there, and the artifact
is built off-CI. At no point does a red run block anything.

**A checklist item saying "check CI" would not have helped** — five releases in
one day is exactly the situation where a checklist item gets skipped. The fix
has to be mechanical.

## It was never red over trivia

Each round of unblocking exposed another real defect, all on Windows, the
platform users run. This matters for how seriously the next red build is taken:

- **`cli.py` `cmd_model()`** — `2fbe1bf` (2026-08-17) deleted the two
  `_pick_model` calls assigning `new_orch`/`new_sub` while leaving their
  headings and every use. `friday model` raised `NameError` as soon as you
  answered the creative prompt. Shipped in v5.6.4 through v5.7.0.
- **`web_fetch.py`** — `_fetch_via_firecrawl` called `_log.info()` in a module
  that never imported `logging`. When Firecrawl failed, the code written to
  route around the outage was the code that crashed.
- **`core/__init__.py` `_save_settings`** — built its temp file as
  `SETTINGS_FILE.with_suffix('.tmp')`: **one shared name for every writer**.
  Friday saves settings from background threads as well as request handlers, so
  two concurrent saves wrote the same temp path and each called `replace()` on
  it. A writer could replace using a temp file another was still filling —
  persisting a mixed `settings.json`, which is exactly the corruption the
  comment above it says the atomic write prevents. The same call also raised
  `PermissionError`/WinError 5, turning a settings save into a 500. This is the
  most consequential find: `settings.json` has already caused one silent
  `model_routing` reset.
- **`hardware_profile.measure_disk_read_mib_s`** — timed a read with
  `time.time()`, whose resolution on Windows is ~15.6 ms. A cached 1 MiB read
  finishes inside that, so elapsed came out exactly `0.0`, hit the `el <= 0`
  guard, and returned `"unavailable"`. The measurement failed silently on the
  **fastest** disks, and the model-load-time estimate fell back to a guess.
- Earlier in the streak (2026-07), the same ruff gate was flagging `base64`
  undefined in `creative_engine.py` — also real, since fixed by other means.

Nine failures were fixed in total: five code, four test. No skips were added to
reach green and no `importorskip` was used. Two assertions came back stronger
than what they replaced — the `/api/health` test now catches a regression to a
hardcoded `"ok"` (the bug decision D1 existed to fix), which the old test
structurally could not, and the TIER_2 tests now pin the 2026-08-19
strong/common split, which nothing tested before.

## Green does not mean covered

CI gates the import smoke test, `tests/unit`, `tests/api`, `tests/security`,
the egress adversarial suite, the judgment gate, and ruff's fatal subset
(`E9,F63,F7,F82`).

It gates **none** of:

- `packaging/windows/` — the whole installer path, **including the upgrade
  rehearsal harness added 2026-08-29**
- `tests/honesty`, `integration`, `conformance`, `regression`, `smoke`,
  `persona`, `probes`, `edge_cases`, `app`
- the Playwright UI specs
- `index.html`, the UI source of truth

Both of the worst defects of that week — the upgrade that copied nothing
(v5.6.5) and the upgrade that destroyed the vault passphrase (v5.6.6) — lived
precisely in the untested region. Neither would have been caught by a green
pipeline.

## Recommendations (repo settings — not applied)

Both require repo-admin changes and are deliberately left for a decision.

1. **Branch protection on `main` requiring the four `pytest` legs and `ruff`.**
   This is the one that matters. It makes red mechanically unable to reach
   `main`, so any tag cut from `main` is green by construction — which closes
   the entire failure described here without anyone having to remember
   anything. It also means work arrives by PR, where `pull_request` already
   runs the same gate.

2. **A release workflow triggered on tag push that refuses to build or publish
   unless `tests` succeeded for that exact SHA.** Branch protection alone still
   permits a tag on an older commit, and today's build happens locally, wholly
   outside CI. Without this the release path remains ungated even when `main`
   is green.

A third, cheaper than it sounds: **extend CI to run the packaging rehearsal
harness**, so the region that produced the two worst recent defects stops being
invisible to every automated check.

## Platform assumption, now recorded

Not a defect, but it had never been written down. With `keyring` absent, the
DPAPI-wrapped file is the **only** durable home for the vault passphrase, and
`CryptProtectData` is Windows-only — so `vault_passphrase.store()` writes
nothing at all on a non-Windows host. It costs users nothing today, because
Friday is a Windows desktop app (tray, DPAPI, PowerShell, `packaging/windows`
is the only packaging path), but it is an assumption rather than a law. The
module's imports are clean, so a Linux collection succeeds; the two tests that
assert the file's behaviour are now guarded on `sys.platform` and say why.
