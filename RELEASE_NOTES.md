# Agent Friday v5.6.5

*2026-08-29 · FutureSpeak.AI*

**If you have ever upgraded Agent Friday in place, this release is not
optional, and your install has probably been reporting a version it was not
running.**

Everything here is either that defect, the one escape hatch that should have
let you out of it, or a correction to something 5.6.4 told you that was not
true. No feature work.

---

## 1. In-place upgrades never delivered any code, and said they had

Re-running a newer installer over an existing install replaced **nothing**.
The installer printed "Friday is installed", wrote the new version number into
`install-manifest.json`, and exited 0 — while every file of Friday's own code
on disk stayed at the previous release.

`Invoke-Step` runs a step's `-Verify` block *before* its action, and skips the
action when verify passes. The `app.copy` verify asked only whether four files
existed: `cli.py`, `server.py`, `setup_wizard.py`, `index.html`. Any earlier
install satisfies that. So the copy short-circuited, every time, with one line
in the log to show for it:

```
app.copy : verify passed before action - already in place, nothing to do.
```

Measured on a real 5.6.3 → 5.6.4 upgrade run from the two published zips:

| | |
|---|---|
| Files updated | **0 of 489** |
| `install-manifest.json` | `5.6.4` |
| `pyproject.toml` on disk | `5.6.3` |
| `connector_secrets.py` (new in 5.6.4) | **absent** |
| `GET /api/mcp/servers` | returned connector tokens **in plaintext** |

That last row is the point. 5.6.4's headline security fix was that connector
credentials stop being handed to the browser. On an install that had upgraded
into "5.6.4", they were still handed to the browser, because the code that
stops it was never copied. Every other 5.6.4 fix was missing the same way.

**This shipped with the installer itself.** 5.6.0 through 5.6.4 all carry it
identically. No in-place upgrade this project has ever published delivered
code. If your install came from an upgrade rather than a fresh install, you
have been running the version you *first* installed.

### The fix

The `app.copy` verify now also requires the installed `pyproject.toml` version
to equal the version being installed. An older install reports an older
version and gets replaced. An install too old to have a readable
`pyproject.toml` reports nothing, mismatches, and also gets replaced — so this
repairs an upgrade from **any** prior release, not just from 5.6.4. The fast
path survives only for what it was for: re-running the *same* installer, where
skipping really is correct.

### What you should do

1. Download `AgentFriday-Setup-5.6.5.zip` below and run it over your existing
   install. It keeps everything under `~/.friday` — notes, wiki, settings,
   conversations, connected accounts.
2. Run `friday status` and confirm the version it reports is 5.6.5.
3. **If you connected Airtable, Gmail, GitHub or any other credentialed MCP
   server while on an install that only believed it was 5.6.4, treat those
   tokens as having been readable, and rotate them.** They sat in
   `~/.friday/mcp_servers.json` in the clear and were served by
   `GET /api/mcp/servers` on request.

A fresh install was never affected, and neither was anyone running from a git
checkout.

---

## 2. `friday update` pointed at a repository that does not exist

A packaged install deliberately ships no `.git`, so every installer user hits
the "not a git repository" branch of `friday update`. That branch printed
`https://github.com/FutureSpeakAI/friday-desktop`, which returns **404**. The
repository is `Agent-Friday`.

So the one command that could have told an affected user what to do sent them
to a dead link. It now prints the correct releases URL, shows the version the
installer recorded beside the version actually running, gives the three steps
that update a packaged install, and states plainly that an in-place upgrade
before 5.6.5 may never have applied.

---

## 3. The uninstaller addressed the author by name

Its closing line read `For Stephen: …LAST-UNINSTALL-REPORT.md`, on every
machine it ran on. It now reads `Details:`.

---

## Corrections to the 5.6.4 notes

Documentation only — no behaviour changed by any of these.

- **The known-issue test count was wrong: seven claimed, sixteen measured.**
  `test_gate_harness_integrity`, named there as failing, passes. The five
  `test_residency_arbiter` and `test_local_image` do fail. Also failing and
  unnamed: five `test_routing_resolver`, one `test_model_router`, one
  `test_model_plan`, one `test_nemo_voice`, two `test_egress_adversarial`.

  The distinction 5.6.4 missed is **isolation versus whole-suite**. Running
  their own files alone, only the two `test_egress_adversarial` cases fail,
  identically at `v5.6.3` and `v5.6.4` — those two are deterministic and
  genuinely pre-existing. The other thirteen pass in isolation and fail only
  in a whole-suite run: order- and environment-dependent, not attributable to
  a release. `v5.6.3` fails the same *number* with a partly different
  *membership*. The count is stable; the roster is not. Still test defects,
  not product defects.

- **`cloud_only` does not refuse a local model you pick.** 5.6.4 said it did.
  `_is_local_choice` guards a task override and a bound seat, but not the
  turn's chosen model — which is the path the picker uses. The behaviour is
  deliberate and long-standing, and it fails safe (on-device is the more
  contained destination), so the sentence is corrected rather than the code.

  **What `cloud_only` actually promises:** Friday will not pick a local model
  on her own, and will not fall back to one when a cloud call fails. It does
  not override a local model you choose yourself — that runs on your machine.

- **The pricing fix was filed under Performance. It is a Fixed-section defect
  with a bill attached.** Opus 5 — Friday's default — was metered at $15/$75
  per MTok against a real $5/$25, so it was **charged at three times its real
  price**. Fable 5 was billed as the cheapest model in the lineup when it is
  the most expensive. `claude-haiku-4-5` metered at exactly $0. The Cost &
  Usage panel and both budget tripwires inherited all three.

- **"A partial settings write no longer resets its siblings" is narrower than
  it sounds.** The deep merge covers two blocks out of the 20 a live
  `settings.json` carries. The rest are still replaced wholesale.

- **A privacy fix shipped in 5.6.4 undocumented** — the sensitivity
  classifier's Layer 4 was POSTing a model tag that was never installed, so it
  404'd on every call and returned "no opinion" indistinguishably from a
  working layer. Now recorded in the 5.6.4 changelog entry.

---

## Known issues

Sixteen unit tests fail a whole-suite run on this line. Two
(`test_egress_adversarial::test_tier2_keyword_batch`) are deterministic and
pre-existing. The other fourteen pass when their own file is run alone and
fail only under the full suite — they are order- and environment-dependent.
All are test defects, not product defects. See the Corrections section above
for the breakdown, and `KNOWN_ISSUES.md` for the standing list.

The two `test_ollama_manager` assertions noted in 5.6.4 assert on the *last*
call made; `chat_completion` legitimately issues a seat-release afterwards
when the display is under its memory reserve, so they track free VRAM rather
than correctness.

---

## How this was verified

1. Built with `packaging\windows\build-installer.ps1` from a **clean git
   worktree** at the `v5.6.5` tag.
2. **A fresh install** from the built zip, on an isolated root and profile.
3. **A real in-place upgrade**, 5.6.3 → 5.6.5, from the *published* 5.6.3 zip,
   then every file in the resulting install byte-compared against the 5.6.5
   tag. This is the same procedure that proved the defect, run again to prove
   the fix.
4. Published, then the **published asset re-downloaded** and its SHA-256
   compared against the local build.

---

## Install

Download `AgentFriday-Setup-5.6.5.zip` below, unzip it anywhere, and
double-click **Install Agent Friday.cmd**. No Python, no git, no Ollama needed
first. Per-user throughout — no administrator, no `Program Files`, no `HKLM`.

Running it over an existing install is now a real upgrade, and keeps your data.

There is no `.exe` build in this release, or in `5.6.0`–`5.6.4`. **Older
`.exe` releases on this repository's release page — `v4.5.0` through `v5.4.0`
— predate this month's security work entirely. Do not treat any of them as
current.** See `docs/INSTALLATION.md`.
