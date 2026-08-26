# Agent Friday v5.6.1

*2026-08-25 · FutureSpeak.AI*

**This release exists because the installer had never been run.**

Not "had not been run recently" — had never been run, by anyone, on any
machine, including the one it was written on. There was no `AgentFriday`
folder in `%LOCALAPPDATA%` anywhere to prove otherwise, and the self-repair
loop had executed exactly zero times.

So it was run, end to end, in an isolated profile. Four things broke. All four
are fixed below, and each entry says whether it was found by executing the
code or by reading it, because on this particular release that distinction is
the whole point.

Everything here is about the **first twenty minutes on a machine that is not
the author's**. There is no new feature.

---

## If you are installing on an 8 GB card

Short version: **you no longer need to know anything.** Run the installer and
answer the question it asks.

The longer version is worth reading once, because the old default was actively
wrong here and not obviously so.

An 8 GB card is not a smaller version of a 12 GB card. Windows takes 2.5 GiB
for the display and the residency layer reserves 1 GiB more, so an 8,188 MiB
card has **4.5 GiB** left — 44% of the card gone, against 29% on a 12 GiB one.

Left alone, the old installer downloaded a local model into that space. That
produced the *worse* of the two possible configurations, for a reason that is
genuinely counterintuitive:

- With **no local model**, a turn that touches your private notes takes the
  `redact` branch and routes to Claude. It works.
- With **a local model present**, `ModelRouter._route_vault` forces those
  turns on-device and does **not** fall back to the cloud. A seat that is
  cold, slow, or dead fails the turn outright.

Having zero local models is *safer* than having one that struggles. The
installer now knows that.

---

## What changed

### 1. The installer asks, once, instead of assuming

Immediately after the key — before twenty minutes of downloading, not after —
setup asks how you want Friday to think:

```
    1. Use your Claude key.
       Nothing extra to download.

    2. Also download a model that runs on this laptop.
       About 2.5 GB more now, and a longer wait.
```

The default comes from your actual card, read with `nvidia-smi` before Python
exists. Under ~5 GiB usable it recommends option 1 and says why. Above it, it
recommends option 2. Pressing Enter takes the recommendation, and either
answer can be changed later from Settings → Models.

`-SkipOllama` still works and still forces option 1. It is no longer something
anyone has to remember.

### 2. A local model, when you do add one, can call tools

`BRAIN_MODELS` gains **qwen3:4b**. On an 8 GiB card this replaces `gemma3:4b`,
and it is a strict improvement in both directions that matter: it is **2.5 GB
instead of 3.3 GB**, and Qwen3 ships native tool calling.

Until now the smallest supported card was also the only one whose local seat
could not call tools. The CPU-only path had the same problem for a different
reason — it picked by table order, so *every* machine without an NVIDIA card
got the one tool-incapable model. (An AMD card reads as no card at all:
`detect_gpus` shells `nvidia-smi` and nothing else.) It now breaks that tie
toward tool calling.

*Verified by executing the planner across four hardware profiles.* A 4060
lands on qwen3:4b, a 4070 still lands on qwen3:8b, and both CPU-only cases now
land on a tool-capable seat.

While there: the planner used to tell you `gemma3:4b` meant Friday "disables
tools for local turns". She does not. `services/agent.py:_via_ollama` passes
the tool registry with no capability check, and `find_pseudo_toolcalls`
catches a narrated call *after the fact* rather than preventing it. The
mitigation is real. The sentence describing it was not, and now matches.

### 3. One missing folder no longer costs every shortcut

*Found by running it.* `[Environment]::GetFolderPath()` returns an **empty
string** when the folder it names does not exist, and `Join-Path` refuses an
empty path. `Install-Shortcuts` resolved the Start Menu directory at the top
of the function, so one empty lookup threw before the first shortcut was
attempted — and took the desktop icon down with it.

Because the step is optional, this surfaced as one calm line: *"Skipped this
part — Friday will still work without it."* Eleven lines later the same screen
said **"There is an Agent Friday icon on the desktop. Double-click it,"** and
pointed at a Start menu folder that had also not been created.

An installer that finishes by naming two things that are not there has failed
in the only way that matters to the person reading it.

- Known folders now fall back to the `%USERPROFILE%` / `%APPDATA%` path and
  return empty rather than throwing.
- Each destination is resolved separately. A folder we cannot find costs the
  shortcuts that live in it and nothing else.
- **The closing screen now reads what was actually created** and says that —
  the desktop icon if it exists, otherwise the Start menu folder, otherwise
  the full path to `Agent Friday.cmd`. Same for the uninstaller.

Honest about the trigger: the empty path came from a redirected `%APPDATA%` in
the test harness, which is not a state a normal laptop is in. A profile
mid-provision, a roaming profile, or an unfinished OneDrive Known Folder Move
produce the identical empty string on a real machine, and the blast radius was
every shortcut plus a closing screen that contradicted itself.

### 4. Self-repair works — and did not, the first time it ran

The Claude-powered repair loop (12 repairs, 25 minutes, a fixed 13-item menu,
validators that refuse anything off it) had never executed. It was run against
a deliberately induced failure. **It failed twice before it worked.**

**It was being asked to diagnose blind.** `Invoke-Step` sent the *action's*
output and the step's *static* description. When pip exits 0 and a module is
missing — the most likely way this install fails — the model received:

> "The command reported no error, but the check afterwards still failed: every
> core module imports in Friday's own interpreter"

No module is named in that sentence. The verifier had computed `MISSING
feedparser: ModuleNotFoundError` and written it to the log, and the log is not
what gets sent. Claude twice proposed repairing the interpreter's `.pth` file
— a good answer, because "pip succeeded, imports fail" *is* the classic `.pth`
symptom when you cannot see which import failed. It was answering the only
question it was asked.

A verify block can now hand its detail back. Same failure, same key, same
model:

| | repairs spent | chose | outcome |
|---|---|---|---|
| before | 2 | `repair_python_pth` ×2 | step **failed** |
| after | 1 | `install_missing_dependency [package=feedparser]` | **verified** |

`install_missing_dependency` was on the menu the whole time and was not
reachable by inference.

**And a truncated answer was charged as a repair.** The tool input is written
diagnosis-first, so at `max_tokens = 700` a long diagnosis ran out of budget
before the field naming the remediation. The partial input arrived with an
empty id, was refused as "not on the menu" — correct outcome, wrong reason —
and had already spent one of twelve. Truncation is now detected via
`stop_reason`, nothing runs, nothing is charged, and the log says the answer
was cut off. `max_tokens` raised to 1500.

*Verified by forcing `max_tokens = 40`: the guard fires and the counter stays
at zero.*

The menu gate itself was never the problem and is unchanged. It refused
everything it should have.

---

## What was rehearsed, and what was not

| | |
|---|---|
| **Rehearsed** | A cold install into an empty profile, start to finish: preflight, app copy, embedded Python, pip core / screen-control / voice+PDF+privacy / memory tiers, shortcuts, uninstaller registration, the closing report. 11 minutes. |
| **Rehearsed** | The self-repair loop against a real induced failure, with a real key: fail → diagnose → validate → remediate → re-verify → pass. Both before and after the fix. |
| **Rehearsed** | The truncation guard, by forcing a tiny token budget. |
| **Rehearsed** | The planner, executed across four hardware profiles. |
| **Not rehearsed** | Ollama install and the model pull. Deliberately skipped — the rehearsal machine has a live Ollama serving another Friday, and a pull would have written into its shared model store. |
| **Not rehearsed** | The interactive prompts, including the new question. `Read-Host -AsSecureString` reads the console directly and ignores redirected stdin, so a scripted run cannot answer it. The branch logic was executed; the typing was not. |
| **Not rehearsed** | `qwen3:4b` on an actual 8 GB card. Its `vram_gib` is derived from the artifact, not measured, and is marked as such. |
| **Not rehearsed** | The setup wizard, and first launch. |

---

## Known, unchanged

- **No residency seat is assigned on non-reference hardware.** Seed VRAM
  measurements are keyed by machine fingerprint and only the author's 4070 is
  present, so `interactive_brain` stays unfilled — silently, because the
  refusal is recorded inside the branch that never runs. Impact under
  cloud-first is nil; once you add a local model, every local turn pays a cold
  load. Unfixed.
- **`packaging/windows/assets/` is empty.** No `friday.ico` exists in the
  repo, so every shortcut gets the default icon. Cosmetic; noted so it is not
  rediscovered.
- **A deep install path costs the privacy tier.** spaCy's native modules hit
  the 260-character limit and `presidio_analyzer` fails to load. A normal
  `%LOCALAPPDATA%` path is nowhere near it. Also: a single failure in that
  tier reports voice and PDF as skipped too, even when both installed fine.

---

**Install:** download `AgentFriday-Setup-5.6.1.zip` below, unzip it anywhere,
and double-click **Install Agent Friday.cmd**. No Python, no git, no Ollama
needed first. Per-user throughout — no administrator, no `Program Files`, no
`HKLM`.

There is no `.exe` build in this release. The one that used to be published
ran **two** of the four privacy layers rather than all four, which is a
security difference and not a packaging one. See `docs/INSTALLATION.md`.
