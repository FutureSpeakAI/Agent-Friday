# Agent Friday v5.6.2

*2026-08-26 · FutureSpeak.AI*

**5.6.1 shipped without the one check it was written to add.**

`Test-AnthropicKey` — the code that verifies a pasted API key actually works,
before self-repair is armed on it — was finished and committed on 2026-08-26,
a few hours after the `v5.6.1` tag was cut and the zip built from it. The tag
does not move when new commits land, so the published `AgentFriday-Setup-5.6.1.zip`
promises a working self-repair loop and ships the exact gap that promise was
meant to close: a malformed, revoked, or out-of-credit key still installs
clean, arms self-repair, and only reveals it doesn't work twenty minutes
later, at the first failure, to someone with no way to know why.

This release closes that gap and republishes. Nothing else changes.

> **If you already have a `5.6.1` install:** the fix is small enough not to
> need a reinstall. Replace `packaging/windows/install.ps1` and
> `packaging/windows/lib/Heal.ps1`, or just download `5.6.2` and run it again
> over the same folder — it's idempotent. If you have not installed yet, use
> `5.6.2` and skip the gap entirely.

---

## What's actually new

### The key is checked when it's pasted, not twenty minutes later

`Test-AnthropicKey` (`packaging/windows/lib/Heal.ps1`) makes one request at
`max_tokens = 1` — a fraction of a cent — to the same endpoint and model
self-repair will actually use, not a free metadata check. A key with no
credit authenticates fine and only fails when asked to think, which is the
case worth catching before twenty minutes of install have passed.

| Response | Verdict | What happens |
|---|---|---|
| `200` | `ok` | self-repair arms normally |
| `401` / `403` | `rejected` | told plainly; offered another key |
| `400` naming credit | `no_credit` | told plainly; not armed |
| `429` | `unknown` | key authenticated, account is busy — not a reason to refuse |
| anything else | `unknown` | network, 5xx, timeout, our own bad request — warned, and setup carries on |

**It fails open, deliberately.** An optional pre-flight must never be the
thing that stops an install. A `400` that isn't about credit is our request
being wrong, not her key, and blocking on it would punish her for our bug.

*Exercised live* against a real key (ok), a bogus key (rejected), and an
unreachable endpoint (unknown). The credit branch matches Anthropic's real
error text but was **not** exercised against a genuinely exhausted account —
noted rather than claimed. Confirmed zero `sk-ant-` strings reach the log
across all of it; the response body is inspected in memory only.

### The borrowed-key problem is written down, not solved

Setup now says, at the moment a key is entered, what was previously true and
unstated: the wizard stores whatever key it's given and uses it on that
machine until replaced, with no record of whose account it is. Revoking it
stops every install using it — there is no way to revoke one machine without
revoking all of them, and no list of which machines there are.

`KNOWN_ISSUES.md` §3b writes this up properly, including the practical
answer: if you're setting someone else up, give them their own key if you
can. If you give them yours, plan on it staying on their machine until one of
you replaces it. Saying the limitation out loud is cheap. Key management is
not, and this release does not attempt it — it makes the limitation
discoverable by reading rather than by being locked out.

---

## What did *not* change, and was checked rather than assumed

`v5.6.1` also shipped two other changes from the same work: the installer
asking about local models instead of assuming one, and the small-card default
moving to `qwen3:4b`. Before cutting this release, both were checked directly
against the `v5.6.1` tag's tree — not inferred from the commit graph — and
**both are already in the published artifact.** This release does not touch
either. Full detail on both is in the `v5.6.1` notes below.

---

## How this was built and verified

Same discipline as `5.6.1`, because that release is the reason it exists:

1. Built with `packaging\windows\build-installer.ps1` from a **clean git
   worktree** at the `v5.6.2` tag — not the working tree, so nothing
   uncommitted or stray can reach the artifact.
2. The built zip was **opened and inspected** for `Test-AnthropicKey`, the
   Step 2b question, and `qwen3:4b`, rather than trusted from the build log.
3. Published, then the **published asset was re-downloaded** and re-checked
   against the same three things and against the local build's hash. This is
   the exact step that caught `5.6.1`'s bundled-changelog defect, and it ran
   again here for the same reason.

---

## Install

Download `AgentFriday-Setup-5.6.2.zip` below, unzip it anywhere, and
double-click **Install Agent Friday.cmd**. No Python, no git, no Ollama
needed first. Per-user throughout — no administrator, no `Program Files`, no
`HKLM`.

There is no `.exe` build in this release. The one that used to be published
ran **two** of the four privacy layers rather than all four, which is a
security difference and not a packaging one. See `docs/INSTALLATION.md`.

---

## Everything from v5.6.1, unchanged and still true

**This release exists because the installer had never been run.**

Not "had not been run recently" — had never been run, by anyone, on any
machine, including the one it was written on. There was no `AgentFriday`
folder in `%LOCALAPPDATA%` anywhere to prove otherwise, and the self-repair
loop had executed exactly zero times.

So it was run, end to end, in an isolated profile. Four things broke. All four
are fixed, and each entry says whether it was found by executing the code or
by reading it, because on that release the distinction was the whole point.

### Why v5.6.1 existed

1. **The installer asks about local models instead of assuming.** One
   question, straight after the key, with a default read off your actual
   graphics card via `nvidia-smi` — before Python exists and before twenty
   minutes of downloading. Under ~5 GiB usable VRAM it recommends your Claude
   key only, because on a small card "no local model" is the *safer*
   configuration: `ModelRouter._route_vault` pins vault turns on-device with
   no cloud fallback when a local model is present, so a struggling seat
   fails those turns outright, while having none routes them to Claude via
   the `redact` branch and works.
2. **The small-card default can call tools.** `BRAIN_MODELS` gains
   `qwen3:4b` — 2.5 GB against `gemma3:4b`'s 3.3 GB, smaller *and*
   tool-capable. An 8 GiB card used to land on the one model in the table
   that couldn't call tools; the CPU-only path had the same problem for a
   different reason (it picked by table order). Both now land on
   `qwen3:4b`.
3. **`Write-Log 'PLAN'` killed the install at step 3 of 16.** An invalid log
   level aborted the run under `Set-StrictMode`, on a step nobody had ever
   reached until the first real rehearsal. Now covered by a test.
4. **One missing known folder cost all four shortcuts.**
   `[Environment]::GetFolderPath()` returns an empty string when the folder
   doesn't exist, and the shortcut step threw before the first shortcut was
   attempted. Known folders now fall back and resolve independently.
5. **Self-repair was diagnosing blind, and paying for truncated answers.**
   The healer received the step's static description instead of the module
   that actually failed to import, and a long diagnosis could run out of
   token budget before naming a remediation and get charged anyway. Both
   fixed; verified by forcing each failure directly.

> **If you downloaded a `5.6.1` zip before that release was published:** it
> shipped a bundled `CHANGELOG.md` that still read *"Current version:
> 5.6.0"*. The installer code, planner, and payload were identical — only
> that file was wrong. Superseded by the published asset, which is correct,
> and now by `5.6.2`.

**Known, unchanged since v5.6.1:**

- No residency seat is assigned on non-reference hardware — cosmetic under
  cloud-first, a cold load on every local turn once a local model is added.
- `packaging/windows/assets/` is empty; every shortcut gets the default icon.
- A deep install path can cost the privacy tier (spaCy's 260-character
  native-module limit). A normal `%LOCALAPPDATA%` path is nowhere near it.
