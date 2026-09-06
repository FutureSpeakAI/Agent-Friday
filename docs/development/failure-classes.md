# Failure classes worth naming

> **Status:** engineering guidance · **Written:** 2026-08 · **Last revised:** 2026-09-06

This note records the failure classes this codebase has actually produced, stated
as rules a contributor can apply. It began life inside `KNOWN_ISSUES.md`; it lives
here because it is guidance, not a list of open defects. The examples are real
and dated where the date matters; the rules are present tense.


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

## The rule

> **Nothing in Friday may claim success it has not verified.**

A component that cannot verify its own success must say so. "I could not check" is an
acceptable answer. "Done" when nothing was done is not. This is the rule the codebase
most needs and the one the fixes below are all applications of.

## The specific bug class: comparisons that discard the meaning

Seven instances were found in one pass, in code written years apart by different
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

## The second class: an assertion loose enough to accept a failure it wasn't testing for

The first class is a check that throws away the part of the value carrying the
meaning. This one is its pair: a check so wide it cannot tell what it caught.

Both examples below are from tests written to verify fixes, to verify fixes
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

## The third class: evidence about a component that isn't in the path

> **A measurement of something the system does not use is not a measurement of
> the system.**

The sharpest instance, and it survived the longest because every
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

## A note on the secret scanner: narrowed three times, bypassed zero

`.githooks/security_scan.py` blocked three commits in one release cycle. All
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

## Why these classes are worth naming together

They are the same root failure at different layers. One is a comparison that
discards meaning; the other is an assertion too broad to detect that discarding
happened. A codebase with the first and without a guard against the second
produces confident wrong answers *and* a green test suite, which is how a defect
survives seven weeks and ~70 restarts.

Both recurred *inside the fixes for themselves* in one release cycle — the
installer's model planner reproduced defect H3, the benchmark harness shipped
the name-shape bug twice, and while fixing a command that exited 0 on failure we
found `sys.exit(False)`, which is also 0. Treat them as live, not historical.

The pattern recurs; one instance is worth writing down as a rule because the
tell is specific.

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

## Gating the content is not gating the decision to send

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

## A true observation does not license the conclusion attached to it

The cleanest statement of the shape, because
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

Three more instances of the same shape: an assertion about shared-model VRAM that failed for a
reason unrelated to the fix it was testing (`sidekick` displacing the e2b, not
charge-once); a golden-diff script whose output labels were inverted, read for a
moment as "the golden already has this key"; and a regenerated golden set whose
1,151-line diff was CRLF, not content, and nearly got committed that way.

## An install that could never succeed reports as an install that was interrupted

A user clicked the optional GPU voice download. It ran for a
while, then Windows put up a dialog about a missing symbol in `c10_cuda.dll`
and the GPU stack was broken — torch had moved, torchaudio had not.

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

What this cost: a confident and incorrect ABI diagnosis, caught only by the
rule *verify by loading the GPU speech stack, not by checking a version string*. Loading it showed torch, torchaudio,
sentence_transformers and silero_vad all imported fine with CUDA available.
`nemo` was simply absent. The version strings had told a story the import
statements contradicted.

The fix: the extra is dropped (NeMo is wanted for ASR; TTS already
comes from the Tier-1 Piper path on CPU), the torchaudio pin is corrected to a
version that exists, and the whole set is dry-run before anything installs.

## The corollary for the interface: many warnings at once is wallpaper

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

**Worked example.** Settings → Intelligence rendered **thirteen**
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
