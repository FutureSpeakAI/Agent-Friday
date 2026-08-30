# Known Issues

**As of 2026-08-29, for v5.6.6.**

This file lists what is broken, what is unverified, and what we do not know. It is
maintained because a defect you can read about is cheaper than one you discover, and
because a project that publishes its bug list is easier to trust than one that doesn't.

If something here is wrong or you hit something that isn't here, please open an issue.
We would rather add to this file than defend a claim.

---

## 0. If you upgraded using 5.6.5, your vault passphrase may be gone

**Fixed in 5.6.6. There is no fix for data already affected — read this in full.**

The vault passphrase is written to `start.bat` inside Friday's app folder and, unless
you separately ran `friday vault-setup`, **nowhere else**. The installer's `app.copy`
step deletes that folder and lays down a fresh copy, and `start.bat` is deliberately
never shipped, so it does not come back.

Before 5.6.5 this never bit, by accident — `app.copy` short-circuited on every upgrade
(issue 0a below) so the delete never ran. 5.6.5 correctly fixed that short-circuit, and
in doing so made this reachable. **Every in-place upgrade performed with 5.6.5 deletes
`start.bat`.**

`~/.friday/vault` is encrypted with AES-256-GCM under an Argon2id key derived from that
passphrase. The vault is never deleted — only the key is. The files remain on disk and
remain unreadable.

Measured on the published zips, 5.6.3 → 5.6.5: installer exit 0, code updated correctly,
`start.bat` gone, vault does not decrypt. The same run against 5.6.6: `start.bat`
preserved, vault decrypts.

**You are affected only if all three are true:** you upgraded in place with 5.6.5, you
had set a vault passphrase, and you had not stored it with `friday vault-setup`.

**What to do, in order:**

1. Run `friday vault-setup` — if you ever used it, your passphrase is in the OS keychain
   and nothing is lost.
2. Look for another launch script you kept (`launch_now.bat`, `friday_startup.bat`);
   they carry the same `SET FRIDAY_PASSWORD=` line. 5.6.6 preserves all of them.
3. Check wherever you saved it — the wizard displayed a generated passphrase once.
4. If none of those: **there is no recovery.** Argon2id + AES-256-GCM with no key cannot
   be reversed by us or by anyone. Your vault files are left alone and 5.6.6 will not
   overwrite them, so a passphrase found later still works. Friday runs normally
   meanwhile with the vault locked.

**Prevention, today:** run `friday vault-setup`. The OS keychain copy is the only one
the installer does not touch.

A second, independent route to the same loss — the setup wizard offering to generate a
new passphrase over an existing vault, defaulting to Yes, on every upgrade — is also
fixed in 5.6.6. See RELEASE_NOTES section 2.

**Still open by design:** the passphrase remains stored inside the app directory. 5.6.6
preserves the file across the copy; it does not relocate the credential. Options are in
`docs/design/vault-passphrase-location.md`, pending a decision.

---

## 0a. If you upgraded in place before 5.6.5, you were not running what you thought

**Fixed in 5.6.5. Read this if your install predates it.**

Installers 5.6.0 through 5.6.4 skipped the app-file copy entirely when an install
already existed — the `app.copy` step verified only that four files were *present*,
which any earlier install satisfies, and `Invoke-Step` runs that verify *before* the
action. So the installer wrote the new version into `install-manifest.json`, printed
"Friday is installed", exited 0, and changed no code. Measured on a real 5.6.3 → 5.6.4
run: 0 of 489 files updated.

Consequences, if this happened to you:

- Your version number was wrong. `install-manifest.json` said one thing;
  `pyproject.toml` and the running code said another.
- **No fix from any release you "upgraded" into was present.** For anyone who
  upgraded into 5.6.4 specifically, that includes its connector-credential
  encryption — so `~/.friday/mcp_servers.json` still held connector tokens in
  plaintext and `GET /api/mcp/servers` still served them to the browser.

**What to do:** install 5.6.6 over the top. It replaces the app files (from any prior
version) and keeps everything under `~/.friday`. Then rotate any MCP connector
credential — Airtable, Gmail, GitHub, Slack — that you entered while on an affected
install.

Fresh installs were never affected. Git checkouts were never affected.

---

## 0b. The unit-test suite fails 16 tests

Sixteen tests fail a whole-suite run at v5.6.5, all of them test defects rather than
product defects. The number has been stable across v5.6.3, v5.6.4 and v5.6.5; the
membership has not.

- **Deterministic and pre-existing (2).** Both
  `tests/test_egress_adversarial.py::test_tier2_keyword_batch` cases fail whether run
  alone or in the suite, identically at v5.6.3 and v5.6.4.
- **Order- and environment-dependent (14).** Five `test_residency_arbiter` (a
  `FakeLlama` double that predates `adopt_or_reap`), five `test_routing_resolver`,
  `test_local_image`, `test_model_plan`, `test_model_router`, `test_nemo_voice`.
  Every one of these passes when its own file is run alone. v5.6.3 fails a partly
  different set of the same size, which is what "order-dependent" looks like from
  outside.
- **Reads real hardware.** Two `test_ollama_manager` assertions check the *last* call
  made, and `chat_completion` legitimately issues a seat-release afterwards when the
  display is under its memory reserve — so they track free VRAM, not correctness.

The 5.6.4 release notes claimed seven and named `test_gate_harness_integrity`, which
in fact passes. Corrected in 5.6.5.

---

## 0c. A partial settings write still resets 18 of 20 blocks

`_save_settings` deep-merges two named blocks — `capability_routing` and
`model_routing`. Every other top-level block in `settings.json` (`cost_budget`,
`tool_hooks`, `qa_gates`, `context_pruning`, and the rest) is still replaced wholesale
by a partial write. 5.6.4 fixed the two with a known blast radius; the general case is
open.

Related: `_load_settings()` does not backfill sub-block defaults, so a `model_routing`
block written before 5.6.4 may hold only the keys that were explicitly saved. Readers
supply their own defaults, and the privacy-relevant one is safe — `vault_local_only`
defaults to ON when absent — but the file and `DEFAULT_SETTINGS` are two sources of
truth for the same setting.

---

## 0d. `cloud_only` does not override a local model you pick

`cloud_only` stops Friday choosing a local model on her own and stops her falling back
to one when a cloud call fails. It does **not** refuse a local model you select in the
picker — that runs on your machine. This is deliberate (`routing/model_router.py`:
"the model picker is authoritative"), and it fails safe, but the 5.6.4 notes described
it as a refusal. Corrected in 5.6.5.

---

## 1. The failure mode this project actually has

Everything below is downstream of one pattern, so it is worth stating before the list.

**Friday's dominant failure mode is confident wrongness, and her second is hiding her
own injuries.** Not crashes. Not wrong answers she flags as uncertain. Silent successes
that were not successes.

The evidence is not theoretical. Every one of these was found by *using* Friday, not by
reading her code, and each had been live for weeks:

- The server spawned its child with `stderr=DEVNULL`. Six overnight startup failures left
  no trace anywhere. The cause was a one-line import error that a two-second check would
  have caught.
- A whole API surface — the career pipeline — failed to register for **seven weeks and
  ~70 restarts**. It logged one warning per boot. Nobody read it. `/api/health` returned
  200 the entire time.
- Every voice conversation's wiki distillation was discarded for weeks. The task routed
  to a model that was not resident, got HTTP 404, and reported **"Task complete."**
- The system tray reported `FAILED TO START` on every *successful* start, because it
  waited 30 seconds against a measured 143-second boot. It recovered only because a
  watchdog later noticed the port was open.
- Local voice transcribed speech, emitted `status: thinking`, and never spoke. The
  readiness gate certified the microphone and the speaker and never asked whether the
  brain in between existed.
- A working MCP subsystem with ~99 registered tools reported itself dead for the life of
  every process, because an endpoint imported a value at module load instead of reading
  it live.

None of these looked broken. All of them reported success.

### The rule

> **Nothing in Friday may claim success it has not verified.**

A component that cannot verify its own success must say so. "I could not check" is an
acceptable answer. "Done" when nothing was done is not. This is the rule the codebase
most needs and the one the fixes below are all applications of.

### The specific bug class: comparisons that discard the meaning

Seven instances were found in a single night, in code written years apart by different
authors, including in tooling written that same night to investigate the others. They
look unrelated and are not:

| What was compared | What was thrown away | What it cost |
|---|---|---|
| `_has_model("gemma4:e2b")` matched any `gemma4:*` | the tag | `friday doctor` reports a model ready; the next call 404s |
| `split(":")[0]` in a benchmark harness | the tag | every measurement 404'd and reported `+0 MiB` as success |
| a creation filename `20260819-140523` | that it's a date | matched the credit-card detector; Friday's own output was withheld from her |
| `api_key=core.GEMINI_API_KEY` | that it's an identifier, not a literal | the secret scanner blocked a correct config read |
| a code comment containing `token:` | that it's prose | same scanner, same commit |
| tool descriptions containing "family", "contact" | that they're static documentation | the model was handed a tool list it could not read |
| a VRAM delta of `-653 MiB` | the sign | "SOMETHING LANDED ON THE GPU" when memory had been *freed* |

The shape is always the same: **a check compares a convenient projection of a value
instead of resolving what the value actually means.** A name's prefix instead of the
name. A number's magnitude instead of its sign. A string's shape instead of its role.

Every one produced a *confident* answer. None produced an error. That is what makes this
class expensive — it never announces itself, and the wrong answer is indistinguishable
from the right one until something downstream fails for an unrelated-looking reason.

**If you are reviewing this codebase, this is the thing to grep for.**

### The second class: an assertion loose enough to accept a failure it wasn't testing for

The first class is a check that throws away the part of the value carrying the
meaning. This one is its pair: a check so wide it cannot tell what it caught.

Both examples below are from tests written during this release, to verify fixes
for the bugs above. Both passed. Neither was evidence.

| The test | Why it passed | What it actually proved |
|---|---|---|
| A benchmark harness asserted a `+0 MiB` VRAM delta meant CPU-only placement held | Every model request had 404'd, so nothing ran on any processor | Nothing. A green light manufactured by a workload that never executed. |
| A test asserted a failed install `exited non-zero` | The subprocess crashed on `ModuleNotFoundError` before reaching the code under test | Nothing. It passed identically with the fix reverted. |

In both cases the assertion was true and meaningless. `abs(delta) < 200` is
satisfied by a run that did no work. `returncode != 0` is satisfied by a crash.
The narrow forms — "did the workload complete, *and* was the delta flat", "did
the command print its verdict, *and* was the code exactly 1" — catch both.

The rule:

> **A test that could not have failed for the reason you think it passed is not
> evidence.**

The cheapest way to check is to break the fix and re-run the test. If it still
passes, it was never testing the fix. That takes about a minute and it caught
both of these.

### The third class: evidence about a component that isn't in the path

> **A measurement of something the system does not use is not a measurement of
> the system.**

The sharpest instance of the week, and it survived the longest because every
individual step was done well.

Two small models — `embeddinggemma:300m` and `functiongemma:270m` — were
benchmarked against the live daemon. Real numbers came back: 57–328 ms to embed
a chunk, 358 ms for a function call, both returning genuine results rather than
timing out. A seating decision was built on them (cap the embedder, don't cap
the function seat), a thread-saturation profile was measured to support it, and
the installer was written to download both and describe what they do.

Nothing in `src/` loads either model.

The real embedder is `all-MiniLM-L6-v2`, reached through sentence-transformers.
`local_seats.py:48` sets `_MIN_USEFUL_GB = 1.5` *specifically to exclude*
`functiongemma` from seat selection, and says so in a comment. Both facts were
one grep away throughout.

Every check performed was a check on the *component*: does it exist, does it
respond, how fast is it, does it use the GPU. None was a check on the *system*:
does anything call this. The installer was about to have a first-time user
download 1.17 GB of weights and tell her they indexed her vault and handled her
tool calls.

The question that catches it is one line and it is not about performance:

```
grep -rn "<model-or-component>" --include=*.py src/ | grep -v "^src/.*/<the-thing-itself>"
```

If the only hits are comments and your own new code, you have measured
something that is not in the path.

### A note on the secret scanner: narrowed three times, bypassed zero

`.githooks/security_scan.py` blocked three commits during this release. All
three were false positives, and none was resolved with `--no-verify` or a
pragma:

| What it flagged | Why it was wrong | The fix |
|---|---|---|
| `api_key=core.GEMINI_API_KEY` | a config read is the *correct* pattern | exempt dotted identifiers, with a carve-out so no known key shape is ever exempted |
| a comment reading ``token: `list_voices` `` | prose, not an assignment | exempt values containing code punctuation |
| `(venv) PS C:\Users\you\Agent-Friday>` | `you` **is** the placeholder | an explicit `PLACEHOLDER_USERNAMES` set |

The rule each time was the same: **a scanner that flags correct code teaches
people it cries wolf, and a scanner people bypass protects nothing.** Every
narrowing was verified in both directions — the real `C:\Users\<realname>\` leak
this rule caught earlier still blocks, and so do the live Google key and the
vault password.

The third case is worth its own note because a pragma was the obvious move and
the wrong one: `# pragma: allowlist secret` would have rendered visibly inside a
markdown code block that a first-time user reads while following a tutorial. A
suppression that damages the artifact is not a suppression, it is a defect with
a comment on it.

Each narrowing is an explicit list rather than a heuristic. "Short name" or
"common word" would be a guess, and guessing is the failure mode this file
exists to document.

**A fourth instance, and the neatest one.** The commit fixing the third case was
itself blocked — by the comment explaining the fix, which cited the real account
name as its worked example. The rule read its own documentation and correctly
identified a username. So the tally is now: a filename that looked like a credit
card, a config read that looked like a hardcoded key, a prose sentence that
looked like an assignment, and a comment *about* a leak that looked like a leak.

Every one is a check unable to distinguish **using** a thing from **writing
about** one. That is not a fixable property of any single rule — a scanner
cannot read intent — so the practical form is: expect false positives in
documentation and comments, keep the examples generic, and narrow the rule
rather than exempt the file. Blanket-exempting comments would be wrong for the
obvious reason that a real key in a comment is still a real key.

### Why these classes are worth naming together

They are the same root failure at different layers. One is a comparison that
discards meaning; the other is an assertion too broad to detect that discarding
happened. A codebase with the first and without a guard against the second
produces confident wrong answers *and* a green test suite, which is how a defect
survives seven weeks and ~70 restarts.

Both recurred *inside the fixes for themselves* during this release — the
installer's model planner reproduced defect H3, the benchmark harness shipped
the name-shape bug twice, and while fixing a command that exited 0 on failure we
found `sys.exit(False)`, which is also 0. Treat them as live, not historical.

They kept doing it on 2026-08-23, four times in one session, and the fourth is
worth writing down as a rule because the tell is specific.

Verifying that `--mmproj` had fixed local vision, the harness asked for a
description with `max_tokens: 24` and got **empty strings back in ~10 s**. That
reads exactly like a projector that loaded and produced nothing — a capability
failure. It was the token budget. gemma4 declares `thinking`, the trace consumed
the whole allowance, and the answer never started. Raising the budget to 400
produced *"Red background, circle shape."* and *"Blue background, circle
shape."* from the same server, same image sizes, same flags.

> **An empty reply plus `finish_reason: length` (or Ollama's
> `done_reason: length`) is a BUDGET symptom, not a blind model.** Read the
> finish reason before concluding anything about capability.

### Gating the content is not gating the decision to send

Three times now, in three files, written by careful people, and the third one
was found only by accident — so it is a class and not three incidents.

The shape is always the same. Someone notices that a payload bound for a cloud
provider might carry sensitive material. They do the thoughtful thing and
protect the payload: run it through the egress gate, scrub the PII, redact what
the classifier recognises. Then the code sends it. **Nobody asks whether the
call should happen at all.**

| where | what protected the payload | what was never asked |
|---|---|---|
| `routes/chat.py` screen capture | nothing — bytes are unclassifiable, so the comment concluded there was "nothing to gate" | should an image go to Gemini when the user chose Local only? |
| `/api/analyze` uploads | `gate_text` on the PDF and text branches | should an upload leave at all in Local only? |
| `agent.py::_evaluate_output` | `_seal_or_block(..., "anthropic")` — genuinely classifies and redacts | should a task that *just refused the cloud for its own work* be graded by a cloud call? |

> **A redactor answers "what may leave?". It never answers "should this call
> happen?"** Those are different questions with different inputs: the first
> reads the text, the second reads the user's policy. Code that only asks the
> first is not gated, however careful the redaction is.

The evaluator case is the sharpest because the protection was real and the leak
was still there. On 2026-08-24 a vault-protected task refused the cloud, told
the user *"It was NOT sent to a cloud provider"*, and then `_evaluate_output`
called Anthropic with that task's goal and up to 4,000 characters of its output.
Nothing consulted `_vault_local_only()`. It surfaced only because the API key
was out of credit; with a working key it would have gone silently.

Two things fall out of it that are worth stating separately. That evaluator runs
at the end of **every** background task, so it is a per-task cloud call nobody
had counted. And it returned `GRADE: PARTIAL` when it *failed to run*, so a step
that produced nothing at all was scored PARTIAL — the grader's own error
becoming a judgement of the work, in a field people read as one. It now returns
`GRADE: UNAVAILABLE` and says the output has not been assessed.

**The check to apply anywhere a cloud call is made:** find the user's policy
input, not the payload's. If the code path cannot name which setting permits
this specific call, it is not gated.

### A true observation does not license the conclusion attached to it

The fifth instance that day, and the cleanest statement of the shape, because
here the observation was not merely true — it was rigorous, and the conclusion
was still wrong.

A stale `.git/index.lock` was found, zero bytes, 99 minutes old. Before removing
it the checks were: no `git.exe` running anywhere on the machine, no
`MERGE_HEAD`/`rebase-*`/`CHERRY_PICK_HEAD`, and an exclusive open
(`FileShare.None`) that succeeded, proving no process held a handle. All correct.
Both parties then concluded **"a git process crashed."** Wrong. Ninety minutes
later three more locks appeared at once — `index.lock`, `HEAD.lock`,
`objects/maintenance.lock`, seconds apart, all empty — and the commit they
belonged to had landed perfectly (`6ac2386`, on top of `fbb52fb`, reflog clean).

Nothing had crashed. The agent FUSE mount permits `create` on `.git/` and denies
`unlink` with `EPERM`, so git writes its lock, does the work, and cannot clean
up. Verified directly rather than inferred: `touch .git/<probe>` succeeds,
`rm .git/<probe>` returns *"Operation not permitted"*, and the same file deletes
instantly from the Windows host. **Every commit through that mount strands its
locks.** An incident was actually a recurring class.

> **An exclusive-open test proves that nobody holds a file. It proves nothing
> whatsoever about why the file exists.** "No holder" is compatible with a
> crash, with a process that finished and could not clean up, and with a file
> nothing ever held. Establishing the first does not select among the rest.

This is the same defect as the image-bytes comment in `routes/chat.py`, which
reasoned that image bytes cannot be text-classified by the egress gate,
*"so there is nothing to gate here."* True premise, and the conclusion does not
follow from it: the bytes were unclassifiable, the decision to send them was
always gateable. §1's original bug class is a comparison that discards meaning;
this is its sibling — a correct measurement carrying a conclusion it cannot
support. Both survive review because the evidence beneath them is real, and
reviewers check the evidence.

The habit that catches it is one question: **what else would produce exactly
this observation?** For the lock, the answer was available in seconds and nobody
asked it.

**Operationally, until the mount changes:** git operations run from the Windows
host clean up after themselves; git operations run through the FUSE mount do
not. Any session committing through the mount will strand locks on every commit,
and they must be cleared from the host.

The codebase already knew: `routing/ollama_manager.py:287` records the identical
effect — `num_predict=10` against gemma4:12b returning `response=''` with
`done_reason='length'`. The knowledge was one file away from the harness that
needed it, which is the actual lesson. `services/local_vision.py` now reports
which kind of empty it got, so the next person does not have to know this.

The other three that day: an assertion about shared-model VRAM that failed for a
reason unrelated to the fix it was testing (`sidekick` displacing the e2b, not
charge-once); a golden-diff script whose output labels were inverted, read for a
moment as "the golden already has this key"; and a regenerated golden set whose
1,151-line diff was CRLF, not content, and nearly got committed that way.

### An install that could never succeed reports as an install that was interrupted

Stephen clicked the optional GPU voice download on 2026-08-24. It ran for a
while, then Windows put up a dialog about a missing symbol in `c10_cuda.dll`
and his GPU stack was broken — torch had moved, torchaudio had not.

The obvious reading is "the download was interrupted, try again". That reading
is wrong, and retrying would have broken it a second time. The target was
`nemo_toolkit[asr,tts]`. The `tts` extra depends on `pyopenjtalk`, a Japanese
text-to-speech frontend that publishes no Windows wheel. pip therefore tried to
build it from source and stopped at

    CMake Error: CMAKE_C_COMPILER not set, after EnableLanguage

cmake is on this machine; MSVC is not. So the tier was **uninstallable as
specified on any stock Windows box** — not slow, not flaky, not unlucky.
It had never worked and could not work.

Two things make this worth naming as a class rather than logging as a bug:

**A dependency that must compile is a different risk class from one that
downloads.** Every other package in that command was a wheel: fetch, unzip,
done. One entry in one extra silently changed the operation from "download
files" to "require a C toolchain", and nothing in the settings UI, the
installer, or the pin list distinguished the two. The install list was read as
homogeneous when it was not.

**pip is not transactional.** It had already replaced torch before it reached
the package that could not build, and it does not roll back. So a failure at
step N leaves the machine in a state no version of the code ever intended —
which is why the symptom (`c10_cuda.dll`) pointed at torch, a package that was
entirely innocent, and why the first diagnosis of it was wrong.

The general form: **a failure partway through a non-transactional install
produces a state that looks like a different bug than the one that occurred.**
Read the resolver output, not the crash.

What this cost: a confident and incorrect ABI diagnosis from me, which was
caught only because Stephen said *verify by loading the GPU speech stack, not
by checking a version string*. Loading it showed torch, torchaudio,
sentence_transformers and silero_vad all imported fine with CUDA available.
`nemo` was simply absent. The version strings had told a story the import
statements contradicted.

Fixed in `efc7d01`: the extra is dropped (NeMo is wanted for ASR; TTS already
comes from the Tier-1 Piper path on CPU), the torchaudio pin is corrected to a
version that exists, and the whole set is dry-run before anything installs.

### The corollary for the interface: many warnings at once is wallpaper

The rule above says nothing may claim success it has not verified. This is its
mirror, and it is a rule about the interface rather than the code:

> **A warning that fires alongside nine others is not a warning. It is
> wallpaper, and wallpaper is worse than silence, because it trains the reader
> to ignore the one that matters.**

The evidence for this is already in §1 and was read as being about logging. It
is not. The career pipeline was dead for seven weeks and ~70 restarts and
*"logged one warning per boot. Nobody read it."* The tray reported
`FAILED TO START` on every successful start. In both cases the message was
present, accurate in its own terms, and ignored — because it was indistinguishable
from the background.

The same reasoning is why the tool-disclosure line lives in the conversation and
fires rarely rather than sitting permanently in the interface.

**The test, applied before a warning is allowed to exist:**

1. **Can the person reading it do something that clears it?** If nothing the
   user can do makes it go away, it is not a warning. It is either an
   instruction to a developer that leaked into the interface — which belongs in
   the log — or it is information wearing the wrong colour.
2. **Does it say what is wrong, why that matters in plain language, and what to
   do?** If it cannot say what to do, question whether it should exist at all.
3. **Is it a fault, an unmade choice, or a fact?** Only the first may look like
   a warning. An optional seat with no model assigned is an available option,
   not a fault. A fact about the configuration is not yellow.

**Worked example, 2026-08-23.** Settings → Intelligence rendered **thirteen**
identical amber boxes under "What will not fit right now". Ten of them were one
arithmetic bug: `hardware_profile.live_display_mib()` summed a WDDM counter that
is not bounded by physical VRAM (Chrome alone reported 25,808 MiB on a 12,282 MiB
card), the baseline exceeded the whole card, every GPU budget floored to zero,
and every seat refused — on a page that said "10.1 GB free of 12 GB" two lines
above. Four of the boxes then claimed "no model assigned" for roles that *did*
have models assigned, because the seats had failed to place.

Every one of the ten failed test 1: nothing the user could do cleared any of
them. Recolouring or grouping them would have shipped a cosmetic fix over a live
planning fault. **When a warning cannot be cleared by its reader, suspect the
warning before you restyle it.**

---

## 2. Fixed in this release

Listed because several were long-lived and you may have hit them.

- **Server could not start** — a module-level use-before-definition in `agent.py`.
  Now guarded by a ~3.5s import check wired into launch, pre-commit and CI.
- **Silent startup failures** — the tray now captures child stdout/stderr to
  `~/.friday/server_stderr.log` and distinguishes "failed to start (exit N)" from
  "stopped". `_wait_for_health` widened 30s → 300s against a measured 143s cold start.
- **Blueprint registration failures were invisible** — now tiered. A required module
  failing exits loudly; an optional one starts but announces in `friday.log`, a
  high-priority notification, and `~/.friday/startup-report.json`. `GET /api/startup-report`
  serves it. Proved by fault injection, not the happy path.
- **Background tasks reported success on provider errors** — any task whose reply is a
  provider failure is now marked `failed` and notified as failed.
- **Tool results were never appended on the normal path** in `_call_claude_agent` — a
  block had been spliced into the middle of a loop, leaving the append inside an `except`.
- **Creation filenames tripped the PII detector** — timestamps moved to ISO form, so
  Friday can see her own output again.
- **Tool descriptions were stripped before reaching the model** — now scoped to MCP
  tools only; first-party descriptions are static documentation and are never gated.
- **`/api/mcp/status` reported a working subsystem as dead.**
- **`friday doctor` reported a model installed when a sibling tag was installed.**
- **The one-line installer cloned a repository that does not exist** (`friday-desktop.git`
  → `Agent-Friday.git`), and pointed at `setup_wizard.py` in the wrong directory. Both
  fatal, in both the PowerShell and shell installers. Nobody had run them end to end.
- **The installer regenerated `index.html` from a dead mirror**, deleting 17 components
  that exist only in the served file and silently downgrading to CDN Babel.
- **TIER-2 sensitivity over-triggering** — ordinary words like "family" now require a
  possessive or personal frame.
- **Local voice had no brain** — the local session now pins a resident seat and refuses
  to start if none exists.

---

## 3. Still broken

### Tooling that is silently inert

Three of these found on 2026-08-23/24 while building the role-consumer test. Each is
the house failure mode applied to a *checker* rather than to a feature: something that
looks like it is guarding you, and is not.

**The pre-commit hook cannot run from a Linux-side session, and fails closed when it
tries.** `.githooks/pre-commit` picks its import-smoke interpreter with
`[ -x "$HERE/../venv/Scripts/python.exe" ]`. Under WSL, a container, or an agent
sandbox that mount-maps the repo, that test passes — the file exists and the mount
marks everything executable — and then exec fails with `Exec format error` because it
is a Windows PE binary. The hook reports `import smoke test FAILED` and blocks the
commit, having never run the check. The security scanner after it never runs at all.
Fix is to test the interpreter actually runs (`"$IMPORTPY" -c pass`) rather than
trusting the `-x` bit.

**Three source files are UTF-8 BOM-prefixed, which Python imports fine and `ast.parse`
rejects.** `services/creative_engine.py`, `routes/core_routes.py` and
`services/capability_router.py` begin with U+FEFF. Any tool that reads source as text
and parses it — a linter, a codemod, an AST-based check — dies on line 1 with
`invalid non-printable character U+FEFF` while the interpreter itself is perfectly
happy. Read with `utf-8-sig`, or strip the BOMs. The cost is an afternoon of
debugging a parser error that points at code containing nothing wrong.

**`services/role_consumers.py` has a known blind spot: mirrored settings keys.** Its
AST check looks for the *capability* name in the consumer module. When a consumer
reads the seat through its legacy flat mirror instead — `voice_model` rather than
`capability_routing.voice` — no literal appears and the seat reads as consumed by
nothing. This produced a false "dead" verdict on the `voice` seat, which was live the
whole time. Mirrored seats are now declared explicitly and the mirror link is verified
against `core._CAP_FLAT_MAP`, but the general lesson stands for any similar check:
**a consumer reading an aliased name looks identical to no consumer at all.** If you
add an alias layer anywhere, assume every static check downstream of it now lies.

### Blocking for a packaged release

**FIXED (PR-3, packaging).** `data/job_tracker_schema.py` and `skills/application_engine`,
`skills/job_scanner` moved from repo-root directories into the installed package proper, at
`agent_friday/seed/data` and `agent_friday/seed/skills` — see `agent_friday/seed/__init__.py`.
`routes/jobs.py` now imports them as plain `agent_friday.seed.*` submodules; no sys.path
surgery, no repo-root dependency. `pyproject.toml` gained a `[tool.setuptools.package-data]`
entry for the non-code `config.yaml`/`SKILL.md` files (the `.py` modules ship automatically
as part of the discovered package). A first-run step (`ensure_seed_skills_installed()`,
called from `cli.cmd_start()` and `setup_wizard.main()`) also copies the bundled skills into
`friday_home()/"skills"` so they appear in the user-facing Skills registry. Proven with a
real non-editable install into a fresh venv from this tree, not asserted from the diff — see
the PR description for the actual command output. `AgentFriday.spec` (the PyInstaller build)
was updated too: its `datas` list silently drops any entry whose source no longer exists,
so the old `('skills', 'skills')` line would have gone missing from frozen builds with zero
warning the moment repo-root `skills/` was deleted, had it not moved with everything else.
`packaging/windows/build-installer.ps1` needed no change — it stages the whole repo tree
including `src/`, so the new location was already covered.

Original text, for history:

> **The career pipeline cannot work in a pip install.** `pyproject.toml` packages only
> `src/`, but `routes/jobs.py` imports `data.job_tracker_schema`, `skills.application_engine`
> and `skills.job_scanner` — all top-level directories at the repo root, none of which ship
> in the wheel. A user who runs `pip install agent-friday` gets four `/api/pipeline/*`
> endpoints that cannot function under any circumstances, because the modules are not on
> their disk.
>
> They will at least now be *told* — the blueprint policy announces "Career pipeline
> unavailable" rather than logging a warning nobody reads. But being reliably informed that
> a shipped feature is permanently broken is not the same as shipping a working feature.
>
> **Status: source installs work, pip installs do not.** Fixing it properly means deciding
> whether bundled skills are Python package internals or first-class installed content, and
> that is a product decision about what the skills system *is*, not a packaging typo. It is
> deliberately not being answered inside a bug fix.

### Local Friday converses and remembers. She does not act.

**`function_manager` is a role in the contract that nothing consults.**

It exists in `residency_policy.py` with a residency class (`RESIDENT`, commented
*"sits inside the tool loop"*), a context budget, and a slot in `ROLE_RESIDENCY`.
It has a default settings entry at `core/__init__.py:1661` — with an empty model
string. It has a UI label, "Tool calling", in `routes/intelligence.py:85`.

It appears **nowhere** in `agent.py`, `routes/chat.py`, `routing/`,
`local_seats.py` or `local_call.py`. `local_seats._ROLE_TO_CAPABILITY` cannot
even resolve a `"function"` role — it knows `brain`, `judge`, `sidekick`,
`extractor`, `heavy`.

**What this does and does not mean** — the scope is narrower than it first
appears, and an earlier version of this entry got it wrong.

Friday's chat path DOES pass the tool registry to local providers and DOES
execute what comes back. Verified by reading the chain 2026-08-22:
``_via_ollama`` (agent.py:193) sends ``tools=``; ``_call_ollama``
(services/model_router.py:410) converts to OpenAI tool schema and runs
``_oai_agentic_loop``; that loop (agent.py:6225) reads ``msg["tool_calls"]``
and calls ``_execute_tool`` under the same registry, vault gate and governance
rings as the cloud path, feeding results back as ``role: "tool"`` messages for
up to 50 iterations. There is even a fallback that parses tool calls out of
prose for models that emit them textually.

> **So a local model WITH native tool calling uses tools fully offline, with no
> API key.** `qwen3:8b` and `gemma4:12b` do. `gemma3:4b` does not — and that is
> a property of the model, not of Friday.

`function_manager` would let a model *without* native tool calling delegate the
decision to a small specialist like `functiongemma`. That path is unbuilt, so
`gemma3:4b` cannot be rescued into tool use. The gap is real; it is just much
narrower than "local Friday cannot act".

**Unbuilt, not parked — checked 2026-08-23.** A recommendation to build the
delegation path behind an off-by-default flag was made and endorsed; it was
never implemented, and the endorsement was later remembered as a completion.
The search that settles it: `function_manager` appears in `src/*.py` on all 22
local branches, all remote refs, four `.claude` worktrees and an empty stash
list, and in only six files on every one of them —
`services/residency_policy.py` (declares the role),
`core/__init__.py:1661` (default entry, empty model),
`services/seat_binding.py` (maps it), `routes/intelligence.py:85` (UI label),
plus comments in `services/model_plan.py` and `setup_brain.py`. **No consumer
and no flag anywhere.** `local_seats._MIN_USEFUL_GB = 1.5` exists specifically
to keep `functiongemma:270m` out of seat selection.

Its *purpose* has also changed and should not be restated from the original
framing. The seat was conceived as an ENABLER for models that cannot emit tool
calls. `gemma4:12b` demonstrably can — 10/10 emission against the production
registry on 2026-08-23, median 2.2 s, and it consumes a tool result and reasons
about it without re-calling. So the seat is no longer load-bearing. As an
OPTIMISATION — a 270M model triaging whether a tool is needed before a 12B runs
— it is still worth having, and `functiongemma:270m` emits tool calls in ~358 ms
on CPU, so the model side was never the obstacle.

Note for anyone reading old comments: `agent.py:93` describes `_call_ollama` as
"single-shot, no tool loop". That is stale and contradicted by the function's
own docstring and by its code.

Wiring it is real work and is not scheduled. `functiongemma:270m` — the model
the role was presumably intended to use — genuinely does emit tool calls, and
does so in 358 ms on CPU, so the model side is not the obstacle.

### Other open defects

- **Friday does not search when asked to search.** Measured 2026-08-24: the prompt
  *"Search the web for the latest on the EU AI Act and tell me what changed, citing your
  sources"* returned a confident five-sentence answer with an **empty `tool_trace`** and
  two `[web:...]` citations for pages it had never fetched. The provenance layer caught
  the citations and rendered them inert, which is the layer working — but it is treating
  a symptom. This is upstream of the whole Source Production Mode effort and probably
  matters more than the citation guard: a citation check cannot fix an answer that was
  never researched. Cause not yet established; the seat is cloud on the `smart` route, so
  it is not the local-tool-calling gap of §3.
- **`print()` from the chat path may not reach the log under the tray launch.** The
  `[CITE]` diagnostics added on 2026-08-24 appear on stdout when the server is run in a
  console and were **absent from `~/.friday/server_stderr.log`** on a tray-launched
  (pythonw) run, while `[PROVENANCE]` and `[INTEGRITY]` lines from the same function were
  present. Not chased to a cause. The consequence is the one §1 opens with: a diagnostic
  you believe is being recorded and is not. Prefer the structured logger or the response
  payload over `print()` for anything you intend to rely on later.
- **The `egress_mode` setting is read by nothing.** Settings → Privacy → EGRESS GATE
  (Audit/Enforce) is a dead control. The direction is safe — the gate always enforces —
  but a privacy toggle that does nothing is a credibility problem regardless.
- **Two safety gates have no callers**: `boot_guard.check_self_edit()` and
  `check_scope()`.
- **The model picker shows a hardcoded list of three models** while hundreds may be
  available.
- **Settings keys absent from `DEFAULT_SETTINGS` are silently discarded on save**, and
  the API returns success. Hit three times so far.
- **The sampling progress bar never advances** during image generation.
- **Cancel loses a race** — cancelling between the orb registering and the arbiter
  granting a lease says "nothing to interrupt", and the job completes anyway.
- **Chain retry has a race and a false-complete.** The retry counter is written after the
  worker thread starts, so a fast-failing step can retry past its budget; and an exhausted
  retry logs but never flips status, so a step that shipped nothing reports `completed`.
- **`ui_parts/app.html` is a hand-maintained mirror of `index.html` that nothing builds
  from.** `index.html` is the source of truth — it is a strict superset, containing 17
  components the mirror lacks. The mirror is kept in sync by hand and will drift.
- **Chain seat overrides are advisory**, not enforced against the capability router.

### Seat contention

The GPU is effectively full: a resident 12b holds ~10.2 GB of a 12 GB card, leaving
~618 MiB. Any image generation or second GPU seat will fail to allocate. The mitigation
being adopted is moving the embedder and function seats to CPU (see §5), which does not
evict anything but does not create headroom on the card either.

---

## 3b. One key per machine, and no way to tell whose it is

Setup asks for a Claude key, and the setup wizard stores whatever key it is given
**encrypted on that machine**, where it is used until somebody replaces it. As of
v5.6.2 the key is verified before self-repair is armed, so a dead key is caught at
the moment it is pasted rather than twenty minutes later — but that is the only
thing that changed. (This landed after the `v5.6.1` tag was cut and did not reach
that published artifact; see `v5.6.2` in `CHANGELOG.md`.)

**There is no notion of a borrowed key.** Nothing records whose account a key
belongs to, and nothing distinguishes "my key" from "a key I was given". The
consequences are real for the common case of one person setting another person up:

- Everything that install does is billed to whoever owns the key, and shares that
  account's rate limits. Heavy use at one end slows the other.
- Revoking the key stops every install using it. **There is no way to revoke one
  machine's access** without revoking it for all of them, and no list of which
  machines are using it.
- The owner cannot see which install spent what. Usage from all of them is
  indistinguishable at the account level.

The installer now says this out loud at the point the key is entered, because
saying it is cheap and key management is not. That is the whole mitigation.

**If you are setting someone else up:** giving them their own key is the only
clean answer today. If you give them yours, plan on it staying on their machine
until one of you replaces it.

---

## 4. Unverified — we do not know

Listed separately from "broken" on purpose. These are not claims that things work.

- **No clean-machine install has been performed.** Everything below "the installers now
  point at the right repository" is untested on a machine that has never seen this code.
  The clone URL fix in particular is verified only by checking that the corrected URL
  resolves and the old one does not.
- **Local voice end to end is unverified.** The fix pins a resident brain and the code
  compiles, but no one has yet spoken to Friday, received a spoken answer, and confirmed
  from the egress log that nothing left the machine. Until that happens, treat local
  voice as unproven rather than working.
- **CPU-only inference throughput is unmeasured for any generation model.**
- **8 GB VRAM is unverified.** The hardware fixture claiming to represent it carries
  measurements copied from a 12 GB card. Nobody has run it.
- **The KV-cache slope that the entire seat-planning model rests on is INFERRED**, from
  two anchors taken on two different backends. The code's own note calls it "the weakest
  number here and everything downstream leans on it." A directly measured slope on one
  model was an order of magnitude away from it.
- **Whether all Telegram/Discord sends route through the sealing manager**, or whether
  the bridge modules are also called directly.
- **Which ffmpeg build `imageio-ffmpeg` ships** — LGPL or GPL. This has licensing
  consequences for any binary release.
- **Whether earlier server deaths shared the cause found in this one.** The import error
  explains the failures after it landed. Deaths before it had a different cause that this
  investigation does not reach, and stderr was discarded in both cases, so the two failure
  modes are indistinguishable in the surviving evidence.

---

## 5. Things that leave your machine

Stated here because a sovereignty product owes you the enumeration, and because none of
this was documented before.

There is **no telemetry, analytics, crash reporting, phone-home, update check, or license
check** in this codebase. That was verified by exhaustive grep and it is a real result.

But four things do leave on a default install with zero keys configured:

1. **A TCP connect to `dns.google:443` every 30 seconds, forever** (fallbacks `8.8.8.8`,
   `1.1.1.1`). No payload — but it reveals your IP and continuous uptime to Google and
   Cloudflare roughly 2,880 times a day.
2. **HTTP GETs to ~39 news feeds every 5 minutes, forever, by default.** No user data in
   the request; the pattern is the signal.
3. **`fonts.googleapis.com`** on every launch, from `index.html`.
4. **Three MediaPipe bundles from `cdn.jsdelivr.net`**, none carrying SRI hashes.

Items 3 and 4 are the largest inconsistency in the product: a "nothing leaves your
machine" application that contacts Google on startup. Self-hosting them is cheap and is
not yet done.

The egress gate itself is strong, and narrower than the README implies. It covers
Anthropic, OpenAI-compatible providers including OpenRouter, and Gemini. It does **not**
cover: web search queries (sent to Brave or DuckDuckGo), Firecrawl, ElevenLabs TTS text,
images and audio sent to Gemini, Google Calendar event content, or a content hash sent to
`freetsa.org`. Those are real third parties receiving user text, and they are outside the
guarantee as written.

### The screen capture ignores "Local only", and that one IS undisclosed

The line above ("images and audio sent to Gemini") covers the *fact* of the send. It does
not cover **when** it happens, and that is the part nobody has been told.

`routes/chat.py:296` fires the vision path on **any** attached image:

```python
screenshot_b64 = data.get('image') or data.get('screenshot') or None
if screenshot_b64 and (include_vision or data.get('image') is not None):
    ...  gclient.models.generate_content(model='gemini-2.5-flash', ...)
```

It runs at line 296. `model_routing` is not read until line 421. There is no mode check,
no vault gate, and no egress-gate call on this path — the block's own comment says image
bytes cannot be text-classified, which is true, and then concludes "there is nothing to
gate here", which does not follow. The *decision to send at all* is gateable even when
the bytes are not classifiable.

So: with routing set to **Local only** — whose help text in `routes/intelligence.py` reads
*"Never leaves the machine. If a local model cannot answer, I say so rather than using the
cloud"* — attaching an image sends a **screenshot of the user's desktop** to Google.
A screenshot is not a bounded payload. It contains whatever was on screen: the vault, a
password manager, a terminal, another person's message.

This is a user-facing control that states a guarantee the code does not keep. It is a
worse failure than an undocumented egress, because the user has actively chosen the
setting that promises it will not happen.

**The local alternative now exists.** As of 2026-08-23 `residency_arbiter._spawn` passes
`--mmproj` when the extracted projector is present, so `gemma4:12b` describes an image
on-device — verified end to end, not by inspecting the command line: two images, two
colours, correct answers both times ("Red background, circle shape." / "Blue background,
circle shape."), `finish_reason: stop`. What is missing is the wiring in `routes/chat.py`
to prefer that seat over Gemini when the mode says local.

---

## 6. Platform reality

**Agent Friday runs on Windows 10/11 with an NVIDIA GPU.**

macOS and Linux can run the server, the web UI, cloud providers, and local chat through
Ollama. They cannot run the system tray, the residency layer (llama-server seats),
GPU-aware seat planning, or OS-protected credential storage.

- On non-Windows, **credentials fall back to plaintext** unless `FRIDAY_PASSWORD` is set.
- **Apple Silicon is explicitly refused** by the residency planner — no MLX or Metal
  backend exists in the tree.
- **AMD GPUs are invisible on every platform** — `nvidia-smi` is the only probe.
- On Linux, no llama-server seat can load at all (the engine candidates are `.exe`), so
  you are Ollama-only.

**Minimum: 16 GB system RAM.** Not a recommendation — at 8 GB, Friday's own budget rule
resolves to zero available memory and refuses every model seat. Earlier documentation
claimed 8 GB; the code always disagreed.

---

## 7. Security posture

- **Keys on disk.** The setup wizard has historically written API keys and the vault
  passphrase as plaintext `SET` lines into launch scripts, and those plaintext values
  *override* the encrypted store. Documentation claiming keys are "never stored as
  plaintext" was false as written. Treat any launch script as containing live secrets.
- **`FRIDAY_SECRET_KEY` — STALE, corrected 2026-08-30 (PR-5, credentials).** This section
  used to claim it "ships as a known default string." That is no longer what the code
  does, and grepping `core/__init__.py::_load_or_create_secret` shows no evidence it ever
  fell back to a hardcoded constant either — it reads `FRIDAY_SECRET_KEY` from the
  environment if set, and otherwise generates a random 32-byte secret with
  `secrets.token_hex(32)` and persists it (mode 0600, atomic write) so sessions survive a
  restart. The persisted-random path has its own gap worth knowing: it writes to
  `Path(os.path.expanduser("~")) / ".friday" / "secret_key"` — the *real* home directory,
  computed inline rather than through `agent_friday.paths.friday_home()` (PR-1) or
  `core.FRIDAY_DIR` — so it does **not** honor a `FRIDAY_HOME` override. A test or a kiosk
  image that sets `FRIDAY_HOME` to redirect Friday's state still gets its Flask session
  secret written under the invoking user's actual home directory. Not a forgeable-session
  risk (the value is still random, never a known constant), but a state-isolation leak:
  anything relying on `FRIDAY_HOME` to fully sandbox a test run or a sealed image should
  not assume `~/.friday/secret_key` stays inside that sandbox.
- **Linux OS-mode credential storage has no durable secret store yet (PR-5, credentials).**
  `services/vault_passphrase.py::store()` and `services/credential_store.py::protect()`
  now fail closed under `FRIDAY_OS_MODE=1`: if neither a vault key (`FRIDAY_PASSWORD`) nor
  DPAPI (Windows-only) can protect a secret, and — for the vault passphrase specifically —
  neither `keyring` nor DPAPI can persist it, both now raise `RuntimeError` instead of
  silently writing plaintext or silently reporting an empty/success-looking result. That
  is strictly better than before (a lost or unencrypted secret was previously invisible),
  but it does not, by itself, give Friday Linux anywhere durable to put a credential:
    - `keyring` is an *optional* extra in `pyproject.toml` (`keyring = ["keyring>=24.0"]`)
      and is **not** in Friday Linux's planned venv extras list
      (`[voice-local-lite,local,compression,federation,google,compose,provenance]`, per the
      OS-mode spec). It is not installed by default and is not installed in this repo's own
      dev venv or in CI (`.github/workflows/tests.yml` never installs it).
    - Even if `keyring` is added as a dependency, `keyring`'s Linux backend needs a Secret
      Service provider (e.g. `gnome-keyring` or `libsecret`) present in the OS image. No
      such package is in Friday Linux's planned system package list.
    - DPAPI is Windows-only (`os.name == "nt"`) and has no Linux equivalent in this
      codebase today.
  **Net effect:** on a Friday Linux kiosk image as currently planned, calling
  `vault_passphrase.store()` for a value that isn't handed to it via
  `FRIDAY_VAULT_PASSPHRASE`/`FRIDAY_PASSWORD` will now raise loudly and correctly instead
  of silently losing the secret — which is progress — but there is still no way for it to
  *succeed* at persisting a passphrase durably. Whoever builds Friday Linux's next
  credentials milestone needs one of: (a) add `keyring` to the deployment's dependency
  extras **and** ship a Secret Service provider in the system image, or (b) add a
  different durable Linux secret store this module can write to, or (c) accept that
  Friday Linux credentials/vault-passphrase persistence is environment-variable-only
  (`FRIDAY_VAULT_PASSPHRASE` set by whatever provisions the kiosk) until (a) or (b) lands,
  and document that as the supported path rather than leaving it to fail-closed by
  default.
- **The vault crypto tests are excluded from CI**, along with the MCP client, integration,
  regression and edge-case suites.
- **`web_fetch.py` and `web_safety.py` — the SSRF guard — have zero tests.**
- **`calendar_write.py` writes to a live Google Calendar, including deletions, with zero
  tests.**
- **Dependencies are unpinned.** Every requirement is `>=`, there is no lockfile, and CI
  installs unpinned. You cannot assert what you ship if you cannot assert what versions
  you ship.

---

## 8. Licensing, unresolved

- **`mutagen` is GPL-2.0** and is currently reachable in the `[all]` extra. Statically
  bundling it into a distributed MIT binary triggers GPL obligations on the combined work.
  It is not in the PyInstaller excludes list.
- **`pynput` and `pystray` are LGPL-3.0**, and `pynput` is in `hiddenimports` — i.e.
  deliberately frozen in. LGPL §4 requires relinkability, which a onefile build removes.
- **Model licenses are not surfaced anywhere.** Gemma models carry use restrictions and
  a downstream pass-through obligation, and Gemma is the *default* local model. Llama
  models carry the 700M-MAU clause. Stable Diffusion 3.5 carries a revenue-conditioned
  license with an attribution requirement — a fact that currently lives only in a Python
  dict comment.
- **There is no `NOTICE` file.** Vendored JavaScript in `static/vendor/` — three.js,
  React, marked, highlight.js — is checked in with zero attribution.

These block a **binary** release. A source release can proceed.

---

*If this file seems long, that is the point. It is shorter than the list of things that
work.*
