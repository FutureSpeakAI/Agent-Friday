# Agent Friday — Windows installer

For Stephen. This is the maintainer's document; it is not what she sees.

---

## What this is for

Someone with a stock Windows 11 laptop and none of Python, git or Ollama
installed should be able to double-click one thing, answer a few plain
questions, and end up talking to Friday.

The path before this required Python (with the PATH checkbox ticked), git, a
virtual environment, `pip install -e .`, and Ollama — a developer workflow
wearing the word "installer".

---

## Building the artifact

```powershell
cd packaging\windows
powershell -NoProfile -ExecutionPolicy Bypass -File .\build-installer.ps1
```

Produces `dist\AgentFriday-Setup-<version>.zip`, about 21 MB. That zip is the
thing you send. She unzips it anywhere and double-clicks
**Install Agent Friday.cmd**. Nothing else is needed on her machine.

The build needs no Python of its own — it uses the embeddable interpreter it
just downloaded to build the wheels. It **aborts** rather than producing a
degraded artifact if the payload is incomplete, if the wheelhouse comes out
empty, or if anything credential-shaped survives into the payload.

Useful flags: `-NoBundlePython` (installer downloads Python on the target
instead), `-NoWheelhouse` (accept the source-build fallback deliberately).

## Running the tests

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\Test-Installer.ps1
```

82 assertions. Needs a real Python on PATH for the argv round-trip section,
which is the most important part of the file — it will tell you loudly if it
had to skip.

---

## The design rules, in order of how much they matter

**1. Never report success that has not been verified.**

Every step goes through `Invoke-Step`, which decides success from a `-Verify`
block and *discards* the action's own exit code and return value. Those are
recorded as evidence for diagnosis; they are never allowed to decide anything.
This is structural, not a convention — there is no code path that marks a step
complete without running `Verify`.

A dependency tier is verified by importing every module in the embedded
interpreter, not by reading pip's exit code. Python is verified by running the
interpreter and asking it its version, platform and `sys.path` — a Microsoft
Store stub would sail through a `Test-Path`. Ollama is verified by locating and
running the binary, not by trusting the installer's exit code.

**2. She never sees a stack trace, a path, or an exit code.**

Two output channels. `Say-*` is hers: plain English, one sentence, always with
something she can do. `Write-Log` is yours: everything, ugly, complete.

**3. No output ever names a secret — not its value, and not its name.**

`Protect-LogText` redacts values *and* generalises names to `<credential>`.
A log line reading "ANTHROPIC_API_KEY was not found" tells a reader which
credentials this machine expects to hold. That is the bug fixed in `c452f17`
and there is a test asserting it stays fixed.

**4. Everything is per-user.**

No administrator, no `HKLM`, no `Program Files`. If Windows shows a UAC prompt
during this install, something is wrong. An installer that needs administrator
to remove itself is not removable in practice.

**5. Anything survivable is survived loudly, in the report — never silently.**

---

## Layout

| Path | What |
|---|---|
| `Install Agent Friday.cmd` | Double-click entry point. Exists so nobody is told to run `Set-ExecutionPolicy`. |
| `install.ps1` | The twelve-step flow. |
| `uninstall.ps1` | Read its header before changing it. |
| `autostart.ps1` | The visible on/off switch. |
| `build-installer.ps1` | Produces the artifact. |
| `sources.json` | Pinned downloads and the honest notes about what is *not* pinned. |
| `healing.json` | Self-repair model, caps, rate table. |
| `lib/Common.ps1` | Step runner, logging, `Invoke-Native`. The spine. |
| `lib/Python.ps1` | Embeddable CPython. Three traps documented in its header. |
| `lib/Deps.ps1` | The three dependency tiers. |
| `lib/Ollama.ps1` | Install-and-verify cascade, graceful fallback. |
| `lib/Shortcuts.ps1` | Shortcuts, autostart, Add/Remove Programs. |
| `lib/Heal.ps1` | Bounded self-repair. Read the header before changing anything. |
| `tests/Test-Installer.ps1` | 82 assertions. |

Installed layout on her machine, all under `%LOCALAPPDATA%\AgentFriday`:
`app\` (the source tree), `python\` (private interpreter), `logs\`, `cache\`,
`tools\`, `install-manifest.json`, and four `.cmd` launchers.

---

## What was verified on Windows 11, and what was not

### Verified, on this machine, this session

- **CPython 3.12.10 embeddable**, SHA-256 pinned and checked. 3.12 rather than
  3.13 because presidio pulls spacy, which pulls blis/thinc/murmurhash, and
  those publish cp312 `win_amd64` wheels reliably while cp313 lags. 3.12.10 is
  the *last* 3.12 with an embeddable amd64 build — .11 and later are
  source-only. Confirmed by HEAD request.
- **Every dependency tier installs under `--only-binary=:all:` with zero source
  builds**, and every module imports in the embedded interpreter. That includes
  `torch 2.13.0+cpu` (the CPU wheel, not the multi-gigabyte CUDA one),
  chromadb, sentence-transformers, faster-whisper, piper, onnxruntime,
  presidio/spacy, pyttsx3 and headroom-ai.
- **The one honest exception**: `pyautogui`, `pyscreeze`, `pygetwindow`,
  `mouseinfo` and `pytweening` publish *no wheels at all*. Their sdists contain
  no C sources and no `ext_modules`, so no compiler is needed — but the build
  now turns them into `py3-none-any` wheels on the build machine so the
  installer stays literally wheels-only on hers.
- **A `._pth` file makes `PYTHONPATH` inert.** Verified directly. Two
  consequences: pip's PEP 517 build isolation cannot work under an embeddable
  interpreter, and `cli.py`'s `env["PYTHONPATH"]` hand-off to `server.py` and
  `setup_wizard.py` is a no-op there. We put the app source into the `._pth`
  instead, so no `src/` change is needed.
- **Embeddable Python has no `tkinter`, `venv` or `ensurepip`.** `sqlite3`,
  `ssl`, `ctypes`, `lzma`, `bz2`, `decimal` and `multiprocessing` are all
  present. Nothing in `agent_friday` imports tkinter; `mouseinfo` does, and
  `pyautogui` tolerates its absence.
- **Argument quoting**: ten hostile argument vectors round-tripped through a
  real process and compared against the resulting argv.
- **The build's credential scanner**, in both directions: it clears the four
  documented placeholder fixtures in this repo, and it aborts the build on a
  planted synthetic key of realistic length and entropy.
- **A full install from the built artifact**, unzipped and run the way she
  would run it. 4m33s for core + recommended; every step verified; the report
  came back "None. Every part of Friday that was meant to install, installed."
- **The installed Friday actually runs.** `agent_friday` imports from the
  installed tree, and the real double-clickable `Agent Friday.cmd` runs
  `friday doctor` to completion with exit 0, reporting every required and
  optional package present, plus `server.py found` and `index.html present`.
  That last pair is `cmd_status` confirming `PROJ_ROOT` resolved correctly
  under the embedded interpreter — which is the whole `._pth` design validated
  end to end, without a line of `src/` being touched.
- **A full uninstall**, isolated with `FRIDAY_HOME` so it could not touch the
  73 GB of real `~/.friday` data on this machine. Every result checked
  individually rather than trusting the summary line:

  | Checked | Result |
  |---|---|
  | Install root, desktop shortcut, Start Menu folder, Add/Remove entry, autostart | all removed |
  | Cache and checkpoint directories (7) | 7/7 removed, 0 of 7 cache files survived |
  | Her data directories (7) | 7/7 preserved, 7 of 7 files survived |
  | A data directory the deny-list has never heard of | preserved — the deny-list-not-allow-list choice does what it was meant to |
  | Windows Credential Manager | untouched, and the log records that it was deliberate |
  | The real `~/.friday` on this machine | 73.73 GB before and after |

### Sizes, measured rather than guessed

| Tier | site-packages on disk |
|---|---|
| core | ~500 MB |
| core + recommended | ~800 MB |
| plus memory | ~2.3 GB (torch alone is 490 MB) |

The artifact zip is 20.9 MB. Ollama and its models are on top of all of this —
budget about 3 GB more for `gemma3:4b`.

### NOT verified — say so rather than implying otherwise

- **This has never run on a clean machine.** Everything above was tested on a
  developer box that already has Ollama, several Pythons, and Friday's own
  `~/.friday` directory populated. The clean-machine test is the one that found
  the most important defect of the previous day's work, and it has not been run
  against this installer. **This is the single largest gap.**
- **The Ollama install path is untested end to end**, because Ollama was
  already present here and the code correctly declines to touch an existing
  install. Neither the winget path, nor the Inno switches, nor the NSIS
  switches, nor the manual-instruction fallback have been observed working.
  There is no documented silent-install contract for Ollama on Windows
  (upstream issue #7969 is literally titled "Administrative / silent install is
  borked"), which is *why* the code tries three ways and verifies after each —
  but "designed not to lie about it" is not the same as "known to work".
- **Self-repair has never made a live API call.** The menu, the schema, the
  validators, the refusal paths and the caps are all unit-tested. The
  Anthropic request itself, the tool-call parsing, and the cost accounting have
  not been exercised against the real API.
- **The setup wizard hand-off is untested**, because unattended mode skips it.
  Whether `ANTHROPIC_API_KEY` in the process environment actually pre-fills
  `step_brain` is an assumption from reading `setup_wizard.py:981-986`, not an
  observation.
- **The autostart shortcut has never survived a real sign-out/sign-in.**
- **Uninstall has been exercised, but never against an install that had pulled
  models or written a vault**, so the model-removal and vault-preservation
  branches are structurally tested rather than lived through.

---

## Self-repair: the bounding, and why each bound exists

Optional, consented, and armed only after she has both supplied a key and said
yes. A key without consent does nothing.

**Captured error text is data, never instruction.** It is delimited in
`<untrusted_tool_output>` tags and the system prompt says so in terms. Error
output from third-party tools is attacker-influenceable in principle — a
package name, a URL in a dependency chain, a server's error body. An installer
that acts on that text is a remote-code-execution path wearing a helpful face.

**The model does not hand us code.** One tool, a closed `remediation` enum,
every value mapping to a PowerShell function in `Heal.ps1`. No `eval`, no
`Invoke-Expression`, no shell-string composition anywhere in the remediation
path. An id that is not a menu key is refused and *recorded as refused*.

**Parameters are re-validated locally.** A JSON Schema enum is a request to the
model, not a guarantee. Package names must match the PEP 508 name grammar
(which excludes `@ git+https://…` direct references and `--index-url`
smuggling), versions a version regex, ports 1024–65535, model tags must be one
this install already planned to fetch. Paths are confined by *resolved* path,
so `..\..\Windows\System32` cannot escape and a sibling directory sharing the
root's string prefix is refused too.

**Every repair is verified** by re-running the failed step's own `Verify`
block. Nothing proceeds past an unverified fix.

**Capped**: three attempts per step, twelve diagnosis calls per install, and a
25-minute wall clock.

Three deliberate refusals, so they read as decisions rather than omissions:

- **No alternate-index remediation.** `--index-url` is a supply-chain lever and
  is not on the menu at any price.
- **`prefer-binary-fallback` does not drop `--only-binary`.** The model needs
  somewhere to put "try a source build"; here that becomes a longer timeout and
  a fresh index read. There is a test asserting it never emits `--no-binary`.
- **`free_or_change_port` will not kill a process it does not recognise.** If
  something unrecognised holds the port, Friday moves instead.

Every heal — applied, refused or failed — lands in
`logs\LAST-INSTALL-REPORT.md` with a diagnosis, the parameters, whether the
re-check passed, a token count and an estimated cost. A self-repairing
installer that hides what it repaired makes the product worse while appearing
to make it better.

---

## Uninstall: the thing that would be easy to get catastrophically wrong

Friday's vault is encrypted at rest; the passphrase lives in Windows Credential
Manager as `agent-friday/vault-passphrase`.

An uninstaller that "tidied up" that credential while preserving the vault
would leave her a folder of AES-256-GCM ciphertext with the key thrown away.
Technically it preserved her data. Practically it destroyed it, silently, while
reporting success.

**So the credential and the vault live or die together.** Keep the notes, keep
the key. Remove the notes, remove the key. There is no branch in `uninstall.ps1`
that produces a third outcome, and if you find one, that is the bug.

Removed in every case: the install folder including its private Python, every
shortcut, the autostart entry, the Add/Remove Programs key, the multi-gigabyte
model checkpoints and caches under `~/.friday`, the `all-MiniLM-L6-v2` weights
in the Hugging Face cache, and only the Ollama model tags this install recorded
pulling.

Kept by default and stated explicitly with the path: her notes, wiki,
conversation memory, skills, settings and creations. Ollama itself is only
offered for removal if `install-manifest.json` records that this installer put
it there.

The cache list is a **deny-list, not an allow-list**, so a future version that
adds a data folder is preserved by default rather than deleted by default.
That is the right way round for a mistake to happen.

---

## Traps found the hard way

Kept here because every one of them looked like working code.

| Trap | What it did |
|---|---|
| `ProcessStartInfo.ArgumentList` | .NET Core 2.1 API; absent on the .NET Framework that PS 5.1 runs on. |
| `Register-ObjectEvent -Action` | Does **not** preserve stdout line order. Scrambled a 3-line probe and made the Python verifier report a healthy install as a failed download. |
| `Start-Process -PassThru` | `.ExitCode` reads `$null` unless you touch `.Handle` first. |
| `Get-ChildItem -LiteralPath -Include` | `-Include` is silently ignored. Deleted every file in the payload; the build then reported success and shipped 12.5 MB of empty folders. |
| `return @()` | Collapses to `$null`, so the caller's `.Count` throws under StrictMode — at the last line of a *successful* install. |
| `foreach ($c in …)` at script scope | Clobbers `$script:C`; PowerShell variable names are case-insensitive. |
| `$PSScriptRoot` in `param()` defaults | Empty under 5.1. |
| `ConvertFrom-Json` | Emits a JSON array as one object instead of enumerating it. |
| PowerShell's `,` operator | Binds *tighter* than `+`, so `@('a' + 'b', 'c')` is not what you wrote. |
| `python.exe` on a stock Win11 | A zero-byte Store app-execution stub. Checked by file size, not by name. |

---

## Deliberately not done

- **Nothing in `src/` was modified.** Not one file. The `._pth` approach exists
  specifically so that `cli.py`'s inert `PYTHONPATH` hand-off did not have to
  be touched while another session owned that tree.
- **No code signing.** The `.cmd` and `.ps1` files are unsigned, so
  SmartScreen may warn on first run. Worth solving before this goes to anyone
  who is not family.
- **`get-pip.py` is not hash-pinned** — upstream regenerates it at a stable URL
  with no published hash. The installer records that it was *not* verified
  rather than implying it was. `sources.json` explains how to pin it properly
  if you want that.
