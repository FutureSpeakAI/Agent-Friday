# Agent Friday v5.6.3

*2026-08-26 · FutureSpeak.AI*

**The first release built for someone who isn't the author.**

`5.6.0` through `5.6.2` fixed what broke when the installer was finally run
for the first time. This one fixes what broke when Friday was finally
*used* by someone on her own hardware, her own account, and her own
graphics card — a 5090, not the reference 4070. Four things, in the order
a new user hits them.

---

## What's new, if you're installing for the first time

### 1. The local model ladder scales with your hardware now

It used to top out at `gemma4:12b`. A 16 GiB card, a 4090, and a 5090 all
got handed the same 7.5 GB model, because "the largest that fits" had
nothing left to reach for once the table ran out of rows. That's not a fit
calculation — it's a missing ladder, and it reported a confident,
correct-looking answer every single time.

The ladder now runs:

```
     8 GiB  qwen3:4b     2.50 GB       16 GiB  qwen3:14b    9.28 GB
    10 GiB  qwen3:8b     5.23 GB       24 GiB  qwen3:32b   20.20 GB
    12 GiB  gemma4:12b   7.56 GB
```

`friday models` (and the installer) now shows you every rung your machine
could run, with the recommended default marked, instead of announcing one
decision and hiding the rest. **`qwen3:14b` and `qwen3:32b` are marked
UNMEASURED** — their fit is arithmetic, derived from the Ollama registry's
own manifests, not a timed run on real hardware. `gemma4:12b` remains the
best-evidenced row in the table: 49–54 tok/s, ~20.5s cold load, measured on
the reference card. **This marking is deliberate and should not be quietly
resolved by a future release** — when someone measures `qwen3:14b` or
`qwen3:32b` for real, that's when the marking changes, not before.

Tool calling is now a **hard gate**, not a preference: `gemma3:4b` cannot
be selected at any tier, on any card, because it cannot call tools and
Friday does not stop passing it the tool registry — it can only narrate a
call it never made. The flag itself is re-checked against the daemon's own
`capabilities` array after every install rather than trusted from a table,
so a wrong entry here is caught, not shipped quietly.

**A real arithmetic bug came out at the same time.** `vram_gib` was
hand-typed per model and already carried about 2 GiB of unstated overhead
padding. A separate overhead constant then took another 1 GiB off the
card, on top of that. Together, the two counts demanded a 13 GiB card for
`gemma4:12b` — a model measured to run fully resident in 11. **A 12 GiB
card was being refused the one model built for a 12 GiB card.** Overhead
is now counted exactly once, from a measurement, and `vram_gib` is
computed from the download size instead of typed by hand — it cannot
drift from it again.

`cli.BUNDLED_MODEL` and `ollama_manager.recommend_models` were both
independent, disagreeing model tables. Both now derive from the same
ladder as everything else, so `friday doctor` no longer recommends the one
model in the system that can't use Friday's tools.

### 2. API keys are manageable from Settings — for real, this time

View, replace, and remove any provider's key from the running app, no
launch script required. This closes a bug where a *replaced* key didn't
survive a restart: the setup wizard wrote keys to `start.bat` and the
encrypted credential store, Settings wrote only to the store, and Friday
re-bootstraps her environment from `start.bat` on every launch — so the
wizard's original key silently outlived every replacement typed into
Settings, while the panel kept reporting "connected." A saved key is now
treated as a deliberate, later instruction, and no longer shadowed by
ambient environment configuration.

`/test` now tells a rejected key apart from one that's simply out of
credit — the same distinction `Test-AnthropicKey` has drawn at install
time since `5.6.2`, now available for any key, any time, from Settings.

### 3. Google connects without a JSON file

Setting up Google used to mean creating your own Google Cloud project and
dropping a `credentials.json` file into a folder before anything would
work — a wall for anyone who isn't a developer. A guided, in-app
walkthrough replaces that as the default path.

**Say plainly what this does and does not include.** A one-click path is
also built, and takes precedence when it's available — but it ships
**inert**. The shared client ID and secret are empty until Stephen mints
them under his own Google Cloud project; shipping half a client (an ID
with no secret) would be worse than not shipping one, marching someone
through a warning screen toward an error. So today, everyone still uses
the walkthrough. When a client is minted, one-click activates with no
further release needed — nothing about *that* is inert, only the
credential is missing right now.

### 4. Cloud-only mode is honored everywhere, not just in chat

A keyless safety net was silently routing cloud-only turns to a local
model whenever no Anthropic key was present. Correct instinct on a
developer machine that always has a key close at hand; wrong on the first
machine that never did. Fixed across typed chat, agentic/tool turns,
briefings, and scheduled work — and the mirror-image bug, local-only turns
quietly falling back to the cloud when a local seat had a bad minute, is
fixed by the same rule, in one place, used by both directions.

---

## Also in this release

- **Installer documentation pass.** `README.md` and `INSTALLATION.md` no
  longer describe a single bundled model or a fixed 16 GB RAM floor as if
  that were the only path — both now describe the question the installer
  actually asks and the ladder it actually offers.
- **A Settings UI fix**: links that pointed at provider tabs which don't
  exist no longer do.

---

## How this was verified

Same discipline as `5.6.1` and `5.6.2`, because it keeps finding real
problems before a stranger does:

1. Built with `packaging\windows\build-installer.ps1` from a **clean,
   detached git worktree** at the `v5.6.3` tag.
2. The built zip was **opened and inspected** — not trusted from the build
   log — for the model ladder, the `UNMEASURED` markings, the key-test
   distinction, the Google walkthrough, and the cloud-only routing fix.
3. Published, then the **published asset was re-downloaded** and the same
   checks, plus a full hash comparison against the local build, were run
   again against the file a stranger would actually receive.

---

## Install

Download `AgentFriday-Setup-5.6.3.zip` below, unzip it anywhere, and
double-click **Install Agent Friday.cmd**. No Python, no git, no Ollama
needed first. Per-user throughout — no administrator, no `Program Files`,
no `HKLM`.

There is no `.exe` build in this release, or in `5.6.0`–`5.6.2`. **Older
`.exe` releases on this repository's release page — `v4.4.0` through
`v5.4.0` — predate this week's security work entirely and run only two of
Friday's four privacy layers even on the day each was built. Do not treat
any of them as current.** See `docs/INSTALLATION.md`.
