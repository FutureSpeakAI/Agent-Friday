# One harness, three targets — the self-patching installer as the first consumer

> **Status:** active
> **Last verified:** 2026-09-06
> **Implementation:** none
> **Supersedes / superseded by:** substrate for [`grow-button.md`](grow-button.md); inherits FA1–FA13 from [`friday-builds-agents.md`](friday-builds-agents.md)
> **Written:** 2026-08-29

## Implementation notes

- The subject — a coding-harness substrate that diffs installed-Friday against repo-Friday and generates the patch — does not exist; zero hits for `self_patch`/`patcher`/`worktree` in `src/` or `packaging/`. The build waits on the maintainer's explicit go.
- Three adjacent pieces predate this document and are the machinery it builds on, not its implementation: `packaging/windows/lib/Heal.ps1` is a fixed remediation menu that is by design "incapable of executing text supplied by the model"; `services/update_check.py` announces a newer release and applies nothing; `services/repo_sync.py` is a blunt `git pull`.
- File:line citations were read against the working tree at `f60ee0d`.

---

**Subject.** The maintainer, 2026-08-29: *"We will consider a way to build an updating system that
checks the users installed Friday versus the repo Friday and figures out how to add the
missing pieces or fix changed code. This will be like our self-healing installer, instead.
it's a self-patching installer."* And, correcting my framing of the harness as a per-feature
cost decision: *"I think the patcher needs a coding harness. So does the installer. So does
Friday."*

**The architectural claim this document is built on**, which is the maintainer's and is right: there
is **one coding-harness substrate** and **three consumers with different targets**. The
installer, the patcher, and Friday's own open-ended self-extension are not three features that
each happen to call a model. They are three targets pointed at one engine.

**Audience.** Two users, and no third:

1. **A new user installing fresh.** Non-technical. Has never seen this software. Their gate
   decisions must be answerable from what is on screen.
2. **The existing install on this machine**, upgrading in place. Which — as §4.3 establishes
   by reading the running process — is a *development checkout that is also the production
   install*, and that dual identity is the most interesting property in the whole problem.

**Method:** STORM — multi-perspective questioning, simulated disagreement at strength (§13),
cited synthesis. Ground truth was read and, where it could be, **measured**: §7's routing
conclusion comes from an executed retrospective merge simulation against this machine's real
drift, not from an estimate.

**Relationship to the other two documents.**

- [`grow-button.md`](grow-button.md) (2026-08-29) specced Friday's open-ended self-extension.
  It is now **consumer 3** of the substrate described here. Its ground truth (§2), its
  anti-vacuous mechanisms (§6), its vision scoping (§7) and its guard rules GB1–GB25 stand
  unchanged; §11 below states which of its procedural mechanisms the patcher gets for free and
  which it still needs.
- [`friday-builds-agents.md`](friday-builds-agents.md) supplies FA1–FA13. FA2 (explicitly
  constructed environment) and FA12 (exact pins) apply to all three consumers.

**Evidence registers.** **VERIFIED** — read at a cited file:line during this audit, 2026-08-29.
**MEASURED** — produced by a stated method, run. **INFERRED** — reasoned from verified facts.
**UNKNOWN** — not determined; the settling check is named. **PROPOSED** — design; does not exist.

---

## 0. The position, up front

**Four claims, in the order they matter.**

**1. The substrate is more than half-built, and it is built in PowerShell.** The installer's
`Invoke-Step` already implements the discipline the whole engine needs, and its header states
it better than I would:

> *"A step is NEVER reported as successful because its Action did not throw. It is reported as
> successful if, and only if, its `-Verify` block returns `$true` afterwards. The Action's own
> opinion of itself is discarded."*
> — **VERIFIED**, `packaging/windows/lib/Common.ps1:9-20`

That is the anti-vacuous-test principle, already load-bearing, already shipped. `boot_guard.py`
supplies the proven-bootable known-good state and the auto-revert. `Heal.ps1` supplies a
bounded model-in-the-loop with a closed action space. What is missing is not philosophy — it
is a **comparable object** (§5), a **router** (§7), and a Python home for the parts that
currently only exist as PowerShell.

**2. The pinned target is the whole prize, and its boundary is exactly the routing boundary.**
The maintainer's observation — that the patcher has an external definition of correct the agent cannot
rewrite — is correct and is the reason to build it first. But the guarantee has a precise edge:
**it holds for release-owned files and ends the moment a file is locally modified.** For a
locally-modified file the desired state is "release change *plus* local change", and nothing
external defines that. §3.2's table is the guarantee ledger; §7's routing tiers are the same
line drawn on the work.

**3. The routing answer, measured rather than asserted: most drift does not touch the harness,
and on the only real example available it touches it zero times.** This machine has 5 files
differing from `v5.6.4` out of 901 tracked (**MEASURED**). A typical patch release changes
12–65 files, 1.3%–7.2% of the tree (**MEASURED**, v5.6.0→v5.6.4). The harness can only be
needed where those two sets *intersect* and a deterministic 3-way merge fails. I ran that
scenario retrospectively — this machine's real local edits, installed at v5.6.3, upgrading to
v5.6.4 — and got **zero conflicts on both intersecting files, with the local change preserved
and the release change applied, line counts exactly additive** (**MEASURED**, §7.4). No model
was involved and none was needed.

That is not an argument against the harness. It is the argument for **routing**: the harness
must be present for the cases that need reasoning, and must not be on the path for the cases
that don't, because a model call on a file that `diff3` resolves deterministically is worse
than useless — it is a chance to be wrong about something that had a right answer.

**4. The one thing that must be built before anything else, and it needs no model at all:
`install-manifest.json` is an uninstall manifest, not an integrity manifest.** It records
version, install root, shortcuts, and which Ollama models the installer pulled. **It contains
no file list and no hashes** (**VERIFIED**, `install.ps1:819-841`), and the build script hashes
exactly one thing — the downloaded Python zip (**VERIFIED**, `build-installer.ps1:365`). So
today there is no way to ask "what does this install consist of, and has any of it changed?"
Everything in this document depends on that object existing. It is a `Get-FileHash` sweep at
build time and a JSON file in the payload.

---

## 1. What the maintainer asked, itemised

| | requirement | answered in |
|---|---|---|
| R1 | One harness substrate serving installer, patcher, and Friday | §3, §4 |
| R2 | Which guarantees are **structural** (pinned target) vs **manufactured** (open target) | **§3.2** — the centrepiece |
| R3 | What defines "the user's installed Friday" as a comparable object — manifest, hashes, version marker, or all three | §5 |
| R4 | How to tell a missing piece from a deliberate local divergence | §5.3 |
| R5 | What happens to local changes when you patch | §5.4, §7 |
| R6 | Use this machine's real drift as the worked example | §6 |
| R7 | Does every drift case invoke the harness — **as a routing question** | **§7** |
| R8 | The self-patching installer, concretely and in depth, as the first consumer | §5–§10 |
| R9 | The grow button as the later generalization, explicit about what is lost | §11 |

---

## 2. Ground truth — the substrate that already exists

### 2.1 `Invoke-Step` — verify-first, verify-after, heal-bounded

**VERIFIED**, `packaging/windows/lib/Common.ps1:496-620`. The step runner's sequence:

1. Run `-Verify` **first**. If it already passes, the step is a no-op. *(Re-running the
   installer must be safe — idempotence by construction.)*
2. Run `-Action`.
3. Run `-Verify` again. **Only `$true` from Verify counts as success.**
4. If Verify is false and healing is armed: diagnose, apply **one** bounded remediation, goto 3.
   Capped at `MaxHealAttempts = 3` per step.
5. Still failing: `-Optional` warns and continues; otherwise show `-HumanFailure` /
   `-HumanFix` and stop.

Two properties worth naming because the Python side of the substrate must inherit them:

- **The action's return value, exceptions and exit codes are recorded as evidence and are never
  used to decide success.** This is the same rule as the grow button's red-first gate, arrived
  at independently, three weeks earlier, in a different language.
- **Console output is split in two** — `Say-*` for the user (plain English, *"no paths, no exit
  codes, no stack traces, no jargon, ever"*) and `Write-Log` for the maintainer (everything). That
  split is exactly what §12's human gate needs and it already exists.

### 2.2 `Heal.ps1` — a bounded model, not a harness

**VERIFIED**, `packaging/windows/lib/Heal.ps1`. This is the closest thing in the repo to a
model in a repair loop, and the important fact is what it *is not*: it is not a coding harness.
It is a diagnosis call with a **closed enum** of remediations.

> *"There is exactly one tool, its `remediation` field is a closed enum… Nothing in the text
> can widen the action space… no shell string composition anywhere in the remediation path.
> If the model returns a remediation id that is not a key in `$script:Remediations`"* — it is
> refused. (`:26-35`)

And the rule for the menu itself (`:253-257`), which the substrate should adopt verbatim:

> *"ADDING AN ENTRY IS A SECURITY DECISION. A remediation must be: bounded (it does one named
> thing, not a class of things); idempotent-ish (safe to run twice); incapable of executing
> text supplied by the model. If you cannot write it that way, it does not belong on the menu."*

Bounds: `max_total_heals = 12` and `max_total_minutes = 25` across the whole install
(**VERIFIED**, `Heal.ps1:236-237`, `:113-114`) — a call budget *and* a wall clock, the same
pair the growth loop needs. A response truncated at `max_tokens` is detected via `stop_reason`
and **does not count against the repair budget** (`:848-865`), which is a real piece of
loop-engineering craft: an incomplete answer is not a failed attempt.

**INFERRED:** this is a third execution tier that my earlier framing missed entirely. Between
"no model" and "a model authors code" sits **"a model chooses from a fixed menu."** §7's router
uses all three.

### 2.3 `boot_guard.py` — the proven-bootable known-good state

**VERIFIED**, `services/boot_guard.py`, 301 lines, wired at `server.py:839-851`. Its opening
paragraph is the SRE requirement, already written:

> *"a Friday that cannot start cannot be asked to fix herself, so the recovery path must not
> depend on her running."*

What it has:

| | |
|---|---|
| **known-good = actually booted** | `mark_boot_succeeded()` is called late in boot, after the app is really up, and *"that is the only thing that promotes a state to known-good"* (`:144-154`) |
| **crash-during-boot detection** | `mark_boot_started()` counts an attempt never followed by a success as a failure (`:128-141`) |
| **auto-revert** | `MAX_FAILED_BOOTS = 2`, then `restore_known_good()` at `server.py:839` |
| **outside-the-app off switch** | `FRIDAY_SAFE_MODE=1` or a `SAFE_MODE` file — *"If the UI is broken you cannot use the UI to fix it"* (`:25-30`) |
| **boot-critical file set** | 5 files a self-edit may not touch (`:53-59`) |
| **forbidden settings keys** | `BLAST_RADIUS_FORBIDDEN` — routing, egress, vault, sensitivity, governance, rings, sandbox, credentials (`:64-70`) |
| **plain-language rollback trail** | `note()` — *"In his language, not the system's: what changed, when, at whose request, and how to undo it. A trail he cannot read is a trail that does not exist."* (`:96-101`) |
| **whole files, not diffs** | `snapshot_known_good` copies files, *"on the same principle that made the calendar repair possible: the receipt held the actual prior value, so restoring needed no reconstruction"* (`:162-172`) |

**Where it stops, and this is the gap to close:** `_self_editable_paths()` returns exactly two
paths — `~/.friday/workspace_studio` and `~/.friday/settings.json` (**VERIFIED**, `:223-226`).
**It does not snapshot source code.** So the auto-revert protects UI customisation JSON and
settings, and would not restore a single line of a bad patch.

Also: `check_blast_radius` is called (from `workspace_studio.py:200-203`), but **`check_self_edit`
and `check_scope` have no callers anywhere in `src/`** (**VERIFIED**, grep). Two gates, built,
never wired. `check_scope`'s own docstring — *"one request touching many files is usually a
misunderstanding, not an ambition"*, motivated by the nine-identical-images batch — is precisely
the guard a patcher needs, sitting unused.

### 2.4 `install-manifest.json` — the comparable object does not exist

**VERIFIED**, `install.ps1:819-841`. The manifest holds `schema_version`, `product`, `version`,
`installed_at`, `install_root`, `python_version`, `shortcuts`, `autostart_enabled`, an `ollama`
block, and `uninstall_reg_key`. Its own `_note` says what it is for: *"The uninstaller reads
this file so it removes exactly what was created and nothing else."*

No file list. No hashes. No commit sha.

The build script (`build-installer.ps1`) *does* prove the payload is complete before shipping —
motivated by an incident recorded in its own comment (`:214-218`) where a copy silently dropped
files and *"NOTHING NOTICED"* — but it checks required entry points and a file count floor
(`:241-251`), and it hashes only the downloaded Python zip against `sources.python.sha256`
(`:365`). It never hashes the payload it built.

**INFERRED:** producing a real release manifest is a `Get-FileHash -Algorithm SHA256` sweep over
`$Payload` at build time, written next to the payload and shipped inside it. It is perhaps
thirty lines, it needs no model, and **nothing in this document works without it.**

### 2.5 The Python side

| piece | status |
|---|---|
| spawn + converse with a long-running CLI | **HAVE** — `services/interactive_sessions.py`: Ring 3, per-spawn confirmation, `FRIDAY_SESSION_DEPTH` recursion guard, PID+start-time orphan reaping, bounded buffer that reports dropped bytes |
| run commands | **HAVE** — `run_command` at Ring 2; `git`, `python`, `pip`, `npm`, `pytest` on `_RUN_COMMAND_ALLOW` (`core/__init__.py:1201-1206`) |
| token ceiling at one chokepoint | **HAVE** — `prompt_cache.check_call_size` (`:215`) called from `model_router._seal_or_block` (`:88`), with a per-call cap and a `current_budget().charge()` hook |
| human approval queue with dissent + expiry | **HAVE** — `services/approvals.py` |
| a diff of whole files | **HAVE** — `difflib` already used in `routes/code.py:600-606` |
| worktree isolation | **MISSING** |
| a 3-way merge | **MISSING in Python** — see §7.5 |
| self-restart | **MISSING** — no `os.execv` or re-exec anywhere in `src/` |
| release manifest / install ledger | **MISSING** — §2.4, §5 |
| the router | **MISSING** — §7 |

---

## 3. The substrate, and the guarantee ledger

### 3.1 One engine, three targets

```
                      ┌──────────────────────────────────────────┐
                      │            H A R N E S S                 │
                      │                                          │
   target ───────────►│  0 admit + classify                      │
   (supplied by       │  1 criteria           ◄── from the TARGET│
    the consumer)     │  2 baseline + snapshot                   │
                      │  3 plan / route                          │
                      │  4 act (mechanical | menu | authored)    │
                      │  5 verify   ◄── VERIFY DECIDES, not the  │
                      │                  action's own report     │
                      │  6 vision (where a surface changed)       │
                      │  7 score → loop or gate                  │
                      │  8 human gate                            │
                      │  9 apply (parent process, allowlisted)   │
                      │ 10 prove (boot + health) or revert       │
                      │                                          │
                      │  state: a directory + append-only journal│
                      │  bounds: tokens · wall-clock · novelty   │
                      └──────────────────────────────────────────┘
                            ▲            ▲                ▲
                            │            │                │
                   ┌────────┴───┐  ┌─────┴──────┐  ┌──────┴─────┐
                   │ INSTALLER  │  │  PATCHER   │  │  FRIDAY    │
                   │ target:    │  │ target:    │  │ target:    │
                   │ "working   │  │ "the tree  │  │ "whatever  │
                   │  vN from   │  │  at tag N" │  │  he asked  │
                   │  nothing"  │  │            │  │  for"      │
                   └────────────┘  └────────────┘  └────────────┘
                    step/verify      byte equality    frozen
                    menu of steps    + release tests  criteria
                    EXTERNAL         EXTERNAL         MANUFACTURED
```

**A target is a small interface**, and this is what makes the three consumers one engine
rather than three:

```python
class Target:
    def desired(self) -> DesiredState:  ...  # what "correct" is
    def observe(self) -> ObservedState: ...  # what is actually here
    def drift(self) -> list[DriftItem]: ...  # the difference, enumerated
    def criteria(self) -> list[Criterion]: ...  # how we will know we fixed it
    def is_authoritative(self) -> bool:  ...  # can the agent rewrite desired()?
```

`is_authoritative()` returning **False** is the whole distinction. For the installer and the
patcher, `desired()` reads a signed artifact — an agent inside the loop has no path to change
what it returns. For Friday, `desired()` is a criteria file the loop itself produced, and every
mechanism in `grow-button.md` §6 exists to simulate the property the other two get for free.

### 3.2 Structural vs manufactured — the guarantee ledger

**This is the most useful thing this document produces.** Left column: what the pinned target
gives you as a property of the world. Right column: what has to be *built* to approximate it
when the target is open-ended.

| # | guarantee | patcher — **STRUCTURAL** | Friday — **MANUFACTURED**, and the mechanism |
|---|---|---|---|
| G1 | **definition of correct** | the tag's bytes, hashed at build, shipped in the payload. The agent has no write path to it | criteria authored **before** any external content is fetched, hashed, frozen; a mismatch aborts (**GB3**) |
| G2 | **who authored the criterion** | the release engineer, weeks ago, in a different process | a different seat from the implementer — *the weakest link in the whole design*, because two calls to one model family share failure modes |
| G3 | **oracle for "did it work"** | byte equality with the target, plus the release's own suite, which shipped green | red-first gate (a test must be observed to FAIL on the baseline) + no-op detector (revert the implementation, the suite must go red) (**GB4/GB5**) |
| G4 | **immunity to a vacuous test** | **total.** The agent cannot make itself pass by changing the target; the target is a hash | procedural and partial. Every mechanism in `grow-button.md` §6, and the honest position is that it is *mitigation*, not immunity |
| G5 | **scope** | enumerable **before** the first model call: exactly the drift set. Countable, displayable, finite | a declared manifest that the applier enforces and the loop may not widen mid-run (**GB6**) |
| G6 | **termination** | the drift set only shrinks. "Done" = zero entries | token ceiling + wall clock + novelty bound + surrender, because the work is unbounded a priori (**GB8/GB9**) |
| G7 | **regression detection** | the release suite *is* the definition of the release's behaviour | inherited, but weakly: existing tests catch regressions and say nothing about the new thing |
| G8 | **what "done" looks like to a human** | *"these 27 files will match version 5.6.5"* — checkable by anyone, including someone who cannot read code | *"here is a thing that works"* — requires an artifact demo, which is why `grow-button.md` §9.2 puts a live artifact on the card |
| G9 | **provenance of the desired state** | a signed release artifact, built from a tagged commit on a clean tree | model output, reviewed |
| G10 | **reversibility** | **symmetric.** The previous release is *also* a pinned artifact; rollback is "sync to the old tag" — the same mechanism running backwards | snapshots of declared stores, undeclared-write detection, forward-only labelling for format changes (**GB13/GB14/GB17**) |
| G11 | **cost predictability** | the diff is measurable before spending anything. You can price the job first | unknown until it converges or surrenders |
| G12 | **failure is legible** | a file either matches its hash or does not | a judgement about whether the thing does what was meant |

**The edge of the guarantee, stated precisely, because everything downstream depends on it.**

G1–G12 hold for a file the release owns and the machine has not modified. For a **locally
modified** file the desired state is *"the release's change, plus the local change, reconciled"*
— and **no external artifact defines that.** The tag knows nothing about the local edit; the
local edit knows nothing about the release.

So: **the patcher is structurally safe exactly as far as the file is unmodified, and at the
first genuinely conflicting local modification it becomes Friday's problem wearing the patcher's
hat.** That sentence is the design. It is also the routing boundary in §7, and it is why the two
tracks must share a substrate rather than being built twice.

---

## 4. Ground truth — this machine, measured

### 4.1 The repository state

**MEASURED**, 2026-08-29:

| | |
|---|---|
| tracked files at `v5.6.4` | **901** |
| working tree differs from `v5.6.4` in | **5 files** (+213 / −8 lines) |
| untracked, non-ignored | **114** |
| local-only commits (on a branch, on no remote) | **311** |
| local `main` | **39 behind** `origin/main` |
| `v5.6.4` | annotated tag → `f60ee0d` = `origin/main` = current `HEAD` |
| git worktrees | 10+, under `.claude/worktrees/` and `~/Projects/friday-desktop-*` |

The tag is well-formed and points where it should. That matters: the pinned target exists and
is trustworthy today.

### 4.2 The five modified files, and what they actually are

**VERIFIED** by reading each diff:

| file | Δ | what it is |
|---|---|---|
| `index.html` | +22/−1 | voice sessions address the **open** thread — `conversation_id` on the WS connect + a live re-address on thread switch. Fixes a reported symptom ("the two-conversations-merging surprise") |
| `core/__init__.py` | +38/−2 | `VIBE_STATE_FILE` + `_persist_vibe_terminals()` / `_read_vibe_terminals_state()`, atomic write |
| `routes/code.py` | +22 | boot-reconcile thread + `_persist_vibe_terminals()` at every mutation site |
| `services/code_engine.py` | +115 | `adopt_or_reap_vibe_terminals()` — adopt live `Friday-Vibe-<id>` windows, reap orphans |
| `tests/unit/test_local_image.py` | +24/−4 | a test repair whose new docstring documents **a vacuous test**: a `monkeypatch.setattr(..., raising=False)` on a symbol that *did not exist* — *"the name was invented — and `raising=False` turned the mistake into a silent no-op instead of an AttributeError naming it"* |

Plus one untracked file, `tests/api/test_voice_thread_targeting.py` — **the test for the
`index.html` change.** A naive "restore from the tag" deletes it and nothing notices.

Three things are worth stopping on.

**All five are ahead of the tag, and all five are correct.** None is corruption, none is a
failed patch, none is stale. A patcher that treats "differs from the tag" as "needs restoring"
destroys a day's work and a shipped bug fix.

**The `code_engine`/`core`/`routes` trio is the repair for a defect documented in
[`grow-button.md`](grow-button.md) §2.3** — the vibe terminal that recorded a log path nothing
wrote to and therefore reported `running` forever. That document read the working tree and
described the *fixed* behaviour as current. Against the tag, `adopt_or_reap_vibe_terminals` and
`_persist_vibe_terminals` **do not exist** (**VERIFIED**, `git show v5.6.4:...code_engine.py`
contains neither symbol). Same repository, two answers, depending on which object you asked.
That is the drift problem in one sentence, and it caught me.

**The test diff is an in-repo specimen of the exact failure the harness must not commit.** A
test that silently no-ops is what `grow-button.md` §6.2's red-first gate exists to catch, and
here is one, found by a human, in this tree, this week.

### 4.3 What is running

**MEASURED**, `Win32_Process`:

```
27120  2026-08-29 10:04:53  <repo>\venv\Scripts\python.exe
                            <repo>\server.py
27644  2026-08-29 10:04:50  ...\pythonw.exe  ...\friday_tray.py
```

**There is no install at `%LOCALAPPDATA%\AgentFriday`** (**VERIFIED** — the path does not
exist). The production Friday on this machine **is the git checkout**. Two consequences:

1. **This machine gets git for free** — a real 3-way merge base, real provenance, real history.
   A new user's install gets none of that (§5.5).
2. **The dev tree and the production tree are the same bytes**, so an uncommitted experiment is
   also what is serving. That is exactly the condition the patcher must be safe under, and it
   is not an edge case here — it is the normal state.

### 4.4 The three drift symptoms the maintainer named, checked

**"Changes written but not restarted into."** Partly. The five edits are dated 08-26 10:49
and 08-28 18:12/18:32; the server started 08-29 10:04:53, so it *did* import them. The general
condition is nonetheless real and is unmonitored: **nothing in this codebase compares the code
in the running process to the code on disk.** `boot_guard` tracks whether a boot *succeeded*,
never whether the process is current. There is no self-restart (**VERIFIED**, no `os.execv`
anywhere in `src/`), so the gap between "written" and "running" is bounded only by when someone
happens to restart. **PROPOSED:** the substrate records the source fingerprint at boot and
exposes `stale_since` — a one-line answer to "is what I am running what is on disk?", which
nothing can answer today.

**"A caching change that sat inert for a day."** Confirmed as a real interval, and the
measurable version is sharper than the recollection. Caching landed at `0c47906`
(2026-08-26 11:09, *"cache the prefix Friday re-sends, and cap what one task may spend"*). The
test it broke was repaired at `5a4a26d` (2026-08-28 18:44, *"repair the assertion prompt caching
broke when it landed"*). **2 days 7 hours** in which the tree contained a change whose own test
disagreed with it. A patcher whose criteria include "the release suite passes" would have caught
that on the first scan after the change landed.

**"A pricing commit that may exist only on a local branch."** **This one is not true, and
saying so is the point.** `2d8e2b3` *"fix(cost): charge what Anthropic charges, in all three
places that guess"* (2026-08-28 17:58) is on `origin/main`, the 5.6.4 backport branch
and the current branch (**VERIFIED**, `git branch -a --contains`). The *class* is real and
large — **311 commits exist on a local branch and no remote**, including the entire
conversations/concurrency MC1–MC9 series from 08-18 and `091dde5` (judgment-seat reachability)
— but this specific commit is not one of them. Which is itself the argument for the feature:
**the drift is already past the point where the person who caused it can recall it accurately.**

---

## 5. The comparable object — defining "the user's installed Friday"

The maintainer's question: *is it a file manifest with hashes, a version marker, both, and what about
files a user or a prior agent legitimately modified locally?*

**Answer: three artifacts, not one, and the third is the one that makes the second usable.**

### 5.1 Why the obvious answers each fail alone

- **A version marker alone** answers "which release did you install" and nothing about whether
  the bytes still match it. It cannot distinguish a clean 5.6.3 from a 5.6.3 with a corrupted
  `index.html`. It is what exists today.
- **Hashes alone** answer "does this file match the release" and nothing about *why* it doesn't.
  A hash mismatch is identical whether the file is corrupt, half-written, hand-edited, or
  carrying a fix that is going into the next release. **Every interesting decision lives in the
  difference between those, and a hash cannot see it.**
- **Both together** get you to "27 files differ from 5.6.3" and stop. You still cannot patch
  safely, because you cannot tell a missing piece from a deliberate divergence.

### 5.2 The three artifacts

**1. Release manifest** — `release-manifest.json`, produced at build, shipped inside the
payload, immutable. **PROPOSED**, does not exist (§2.4).

```jsonc
{
  "schema_version": 1,
  "product": "Agent Friday",
  "version": "5.6.4",
  "commit": "f60ee0d6cd4fa9fc53307e37d8c9421d84bb8bd8",
  "tag": "v5.6.4",
  "built_at": "2026-08-29T...",
  "built_from_clean_tree": true,
  "files": [
    { "path": "index.html",
      "sha256": "…", "bytes": 1531158,
      "class": "app",           // app | ui | test | doc | asset | vendor | config-template
      "boot_critical": false,
      "mergeable": true },      // false ⇒ never 3-way merged (binaries, generated)
    …901 entries…
  ],
  "manifest_sha256": "…"        // of the files array, canonical-ordered
}
```

`class` and `mergeable` are not decoration — §7's router keys on them. A binary asset is never
merged; a `config-template` is never overwritten; a `doc` conflict is not worth a model call.

**2. Install ledger** — `~/.friday/install-ledger.jsonl`, owned by the machine, never shipped,
**append-only**. This is the artifact that answers R4, and it does not exist in any form today.

```jsonc
{"t":"2026-08-14T…","event":"installed","version":"5.6.2","by":"installer","files":901}
{"t":"2026-08-26T10:49:14","event":"local_modify","path":"src/.../code_engine.py",
 "from_sha":"…","to_sha":"…","by":"unknown","detected_by":"scan"}
{"t":"2026-08-28T18:03:51","event":"patched","path":"index.html","from":"5.6.3","to":"5.6.4",
 "how":"merge3","conflicts":0,"local_change_preserved":true}
{"t":"…","event":"growth_applied","slug":"trello-connector","paths":["…"]}
```

**Append-only and written at the moment of the event, not at patch time.** A ledger populated
only when the patcher runs cannot distinguish a deliberate edit from corruption, because both
are simply "different from the manifest, first noticed now". The ledger's value is entirely in
being *early*.

**3. Observed tree** — computed at scan time. Walk the app directory, hash every file. 901
files; **UNKNOWN** what that costs on a cold cache on a slow disk, and it is worth measuring
before deciding whether the scan is on the boot path or on a schedule (§16 Q1).

### 5.3 Provenance classes — telling a missing piece from a deliberate divergence

Every path resolves to exactly one class. This is R4's answer, and the routing in §7 keys on it.

| class | test | meaning | default |
|---|---|---|---|
| **PRISTINE** | observed == manifest(installed) | untouched | nothing |
| **MISSING** | in manifest, absent on disk | a missing piece, unambiguously | restore, no model |
| **STALE** | observed == manifest(*some older release*) | simply behind — matches a real prior release byte-for-byte | restore to target, no model |
| **DIVERGED-KNOWN** | observed ∉ any manifest **and** the ledger has an event | deliberate: a local edit, a prior merge, an applied growth | **preserve**; merge if the target also changed it |
| **DIVERGED-UNKNOWN** | observed ∉ any manifest **and** the ledger is silent | could be a legitimate edit the ledger missed, a partial write, disk corruption, or tampering | **never silently overwritten.** Quarantine a copy, then treat as DIVERGED-KNOWN for merging, and say so on the card |
| **EXTRA** | on disk, in no manifest | user file, growth output, or junk | **never deleted** by a patch. Listed |
| **UNREADABLE** | locked / permission denied | in use, or a permissions problem | report; retry after restart |

The distinction that does the work is **DIVERGED-KNOWN vs DIVERGED-UNKNOWN**, and it is
entirely a function of whether the ledger was being maintained. On a git checkout the answer is
free and better — git *is* the ledger, with authorship, timestamps and a real merge base. On a
payload install the ledger has to be manufactured by a periodic scan, and a first scan on a tree
that has been drifting for months will produce a pile of DIVERGED-UNKNOWN with no way to
adjudicate it.

**PROPOSED, and it matters for the new-user story:** run the first scan **at install time, on a
tree the installer just wrote**, so the ledger starts from a known-pristine baseline. A ledger
that begins at install has no unexplained entries by construction. One that begins later never
fully recovers.

### 5.4 What happens to local changes when you patch (R5)

The rule, in one line: **a patch never discards a local change without a human saying so, and
never silently.**

| provenance × target | action |
|---|---|
| PRISTINE / STALE / MISSING, target changed it | restore. Mechanical |
| DIVERGED-*, target **did not** change it | **leave it alone.** Not a defect — a file the release does not touch has no drift |
| DIVERGED-*, target changed it, 3-way merge clean | **merge.** Mechanical, verified by the merge algorithm itself producing no conflict |
| DIVERGED-*, target changed it, merge conflicts | escalate: §7 tiers T3/T4 |
| EXTRA | leave. Listed on the report |
| any class, file is `boot_critical` | never touched by an automatic patch. §12 |

The second row is the one people get wrong, and it is quantitatively the most common: on this
machine, **3 of the 5 locally-modified files were untouched by either of the last two releases**
(**MEASURED**, §7.4). They need no decision at all.

### 5.5 New user vs this machine

| | new user (payload install) | this machine (git checkout) |
|---|---|---|
| release manifest | shipped in payload | `git ls-tree` at the tag — free and exact |
| ledger | must be built; starts at install | git history — authorship, dates, and a true merge base |
| merge base | last release's manifest + a blob store (§7.5) | `git merge-base`, exact |
| local commits | impossible — there is no vcs | **311 of them** |
| what "modified locally" means | a hash that matches nothing shipped | a diff against a ref, with a message explaining it |
| rollback | re-sync to the prior release manifest | `git revert` / `git checkout` |
| what re-running the installer does | `Remove-Item -Recurse` on `app\`, re-copy (**VERIFIED**, `install.ps1:434-439`) — **destroys all divergence with no diff** | n/a |

That last cell is the reason the patcher exists at all. For a payload install, today's only
update path is a full wipe-and-replace, and the wipe is silent. A new user who let Friday grow
herself a Trello connector loses it on the next update and is told nothing.

---

## 6. The worked example, end to end

Applying §5's machinery to this machine, as it actually is, upgrading `v5.6.4 → v5.6.5`:

| path | provenance | target touched it? | route |
|---|---|---|---|
| 896 files | PRISTINE | mostly no | **T0** — nothing |
| ~25 files (typical release) | PRISTINE | yes | **T1** — copy from payload |
| `services/code_engine.py` | DIVERGED-KNOWN (git: uncommitted, 08-26) | *no* in 5.6.3→5.6.4 | **T0** — leave alone |
| `routes/code.py` | DIVERGED-KNOWN | *no* | **T0** — leave alone |
| `tests/unit/test_local_image.py` | DIVERGED-KNOWN | *no* | **T0** — leave alone |
| `index.html` | DIVERGED-KNOWN | **yes**, every release | **T2** — 3-way merge |
| `core/__init__.py` | DIVERGED-KNOWN | **yes**, every release | **T2** — 3-way merge |
| `tests/api/test_voice_thread_targeting.py` | EXTRA (untracked) | n/a | **leave, list** |
| 113 other untracked | EXTRA | n/a | **leave, list** |

**Model calls required: zero** — if the two T2 merges are clean. §7.4 measured whether they are.

---

## 7. Routing — does every drift case invoke the harness?

**No. Availability and routing are different questions, and the routing is the cost model.**

### 7.1 The tiers

| tier | what it is | who decides | model? | cost |
|---|---|---|---|---|
| **T0** | no action — matches, or the target didn't touch it | hash comparison | no | ~0 |
| **T1** | mechanical restore — copy the target's bytes | provenance table | no | I/O |
| **T2** | mechanical merge — 3-way diff3, base = installed release | the merge algorithm; **a clean merge is verified, not judged** | no | ms |
| **T3** | bounded choice — conflict, resolvable from a **closed menu** (take-local / take-release / both / defer), per hunk | a human, or a model constrained to the enum (`Heal.ps1`'s exact pattern) | **diagnosis only** — no code authored | one small call |
| **T4** | **harness** — a genuine semantic merge: both sides changed the same behaviour and reconciling requires understanding both | a model authors a merge in a worktree; the release suite + local tests judge | yes, full loop | the expensive case |
| **T5** | refuse and escalate — boot-critical, secrets at rest, a schema/format migration, or a conflict T4 could not close | a human | no | a conversation |

### 7.2 The routing rules

```
for each path in (manifest ∪ observed):
    if provenance == PRISTINE and target_unchanged:            → T0
    if provenance in (MISSING, STALE):                          → T1
    if provenance is DIVERGED-* and target_unchanged:           → T0   # the common case
    if not manifest_entry.mergeable (binary/generated):         → T3   # never auto-merged
    if path is boot_critical:                                   → T5
    if path touches secrets-at-rest or a store format:          → T5
    merged, conflicts = diff3(base=installed, ours=observed, theirs=target)
    if conflicts == 0:                                          → T2
    if every conflict hunk is whole-hunk-disjoint:              → T3
    else:                                                       → T4
```

Two properties of this router are non-negotiable:

- **The router is mechanical.** It is not a model deciding how hard a case is. A model in the
  routing position could send a T4 case down the T1 path by misjudging it, and T1 overwrites.
- **The router may only escalate, never de-escalate.** A case classified T4 cannot be demoted to
  T2 by anything inside the loop. Escalation is free; de-escalation is a hole.

### 7.3 Why T3 exists as a separate tier

Because `Heal.ps1` already proves it works, and because it is dramatically cheaper and safer
than T4 for the shape of conflict that is actually common: two changes near each other where the
*resolution* is a choice among a few options rather than new code. A model choosing an enum
member cannot author a backdoor. A model authoring a merge can.

The menu inherits `Heal.ps1:253-257` verbatim: bounded, idempotent-ish, incapable of executing
model-supplied text.

### 7.4 The measurement

**MEASURED**, 2026-08-29, executed rather than estimated.

*Sizing.* 901 tracked files at `v5.6.4`. Files changed per patch release: **13** (5.6.0→1),
**12** (→2), **65** (→3), **27** (→4) — **1.3%–7.2%** of the tree. On this machine, **5** files
are locally modified. The harness can only be reached where those sets intersect: of the 5
locally-modified files, **2** (`index.html`, `core/__init__.py`) appear in the 5.6.2→5.6.3 and
5.6.3→5.6.4 deltas; **3** appear in neither.

*The simulation.* I reconstructed the exact scenario — an install at `v5.6.3` carrying this
machine's real local edits, upgrading to `v5.6.4`:

```
base   = git show v5.6.3:<path>                 # the installed release
ours   = base + <this machine's real local diff>   # applied with `patch --fuzz=3`
theirs = git show v5.6.4:<path>                 # the new release
git merge-file ours base theirs
```

Both local patches applied to the older release cleanly (`index.html` hunks at offset −22).
Then:

| file | conflicts | local change present | release change present | lines |
|---|---|---|---|---|
| `index.html` | **0** | yes (`conversation_id=` on WS connect) | yes | — |
| `core/__init__.py` | **0** | yes (`VIBE_STATE_FILE`, 5 refs) | yes (the field-by-field settings-merge comment) | base 2574 + release 12 + local 36 = **2622 exactly** |

**Zero conflicts. Both changes preserved. Line counts exactly additive. No conflict markers. No
model.**

*The conclusion, stated with its limits.* On the only real drift example available, a full
version upgrade over genuine, non-trivial local modification routes **entirely to T0/T1/T2 and
invokes the harness zero times.** One sample is not a distribution, and the sample is favourable
— the local edits are additive, in distinct regions, in a 42,628-line file where collision is
unlikely. **INFERRED:** the T4 rate is low but not zero, and the cases that will reach it are
the ones where a release *refactors* a region a local change also edits — a rename, a signature
change, a moved block. Those are exactly the cases where a mechanical merge would be *wrong
while appearing clean*, which is §13.2's objection and §7.6's answer.

**So: the harness must be present, and it must not be on the path.** Present, because T4 is
real and unbounded when it happens. Off the path, because ~99% of drift has a right answer that
a hash comparison and `diff3` produce deterministically, and routing that through a model buys
nothing and risks something.

### 7.5 The merge implementation, and the git question

T2 is load-bearing and needs a real 3-way merge. On this machine `git merge-file` exists. On a
payload install, **git is not installed** — `install.ps1` installs Python, pip, requirements,
optionally Ollama and PyAutoGui; there is no git step (**VERIFIED**).

Two options, priced:

| | ship git | pure-Python diff3 |
|---|---|---|
| correctness | battle-tested | must be proven |
| install cost | ~50 MB (MinGit), a new download + hash to maintain in `sources.json` | zero |
| merge base | still needs the prior release's bytes (see below) | same |
| risk | another supply-chain dependency | **a subtly wrong merge that reports no conflict** — the worst failure mode in this document |

**Recommendation: pure-Python diff3, with a mandatory differential test.** The repo already uses
`difflib`. But it does not ship until it has been run against `git merge-file` over a corpus of
several hundred real three-way cases generated from this repo's own history, and **agrees on
every one, including the conflict cases.** A merge engine that disagrees with git about where a
conflict is has not been tested; it has been hoped for. Until that corpus passes, T2 falls back
to T3 and asks.

**The merge base.** `diff3` needs the bytes of the *installed release*, not just its hashes. So
the payload must either carry the prior release's files (doubling payload size — no) or the
install must keep a content-addressed blob store of the release it installed:
`~/.friday/patch-store/<sha256>` for every file the release wrote. 901 files, mostly small, one
1.5 MB `index.html` — **UNKNOWN** total, plausibly 30–60 MB, and it can be pruned to only files
that have diverged once the first scan has run. This is the cheapest path to a real merge base
without shipping git, and it is ~200 lines.

### 7.6 What the router must never do

- **Never treat "the merge produced no conflict" as "the merge is correct."** `diff3` is
  syntactic. A release that renames a function and a local edit that calls the old name merge
  cleanly into code that does not run. **Therefore every T2 result is followed by a mechanical
  check the merge cannot fake: syntax parse (`ast.parse` for Python, a real parse for JS), then
  the release's own test suite, then boot.** A T2 file whose merge parses but whose suite fails
  is re-routed to T4 — the one case where the router escalates *after* acting.
- **Never widen a class.** A T1 restore writes exactly the manifest's bytes for exactly that
  path. It does not "clean up while it's there."
- **Never delete an EXTRA file.** Not one, not ever, not on the grounds that it looks like junk.
- **Never run on a tree it has not fully scanned.** A partial scan produces a partial drift set,
  and a patch computed from a partial drift set is not a patch — it is a guess.

---

## 8. The patch loop

Stages, as the substrate's `Target` for the patcher. Stage numbering matches §3.1.

```
 0  ADMIT     read release-manifest for target version. Refuse if absent/unsigned.
              Refuse if the app is running from a tree the patcher cannot lock.
 1  CRITERIA  ← from the TARGET, not from a model. Four, all mechanical:
                 C1 every non-DIVERGED path matches the target hash
                 C2 every merged file parses
                 C3 the release's own test suite passes
                 C4 the app boots and answers /api/health
 2  BASELINE  full scan → provenance table. Snapshot every file about to change,
              plus every declared store, into the growth-style directory.
              Record: is the release suite green BEFORE we start? If not, STOP.
 3  ROUTE     the mechanical router, §7.2. Produces the work list, priced,
              displayable, finite. THIS IS SHOWN BEFORE ANYTHING IS WRITTEN.
 4  ACT       T1 copies · T2 merges · T3 menu choices · T4 harness iterations
 5  VERIFY    C1–C3. The action's own report is discarded (Common.ps1's rule).
 6  VISION    only if a UI-class file changed. §9.
 7  SCORE     all criteria met → gate. T4 items that failed → loop or surrender.
 8  GATE      human go/no-go. §12.
 9  APPLY     parent process. Write the marker file FIRST (§8.2). Copy. Restart.
10  PROVE     boot + health + suite. Else auto-revert from the snapshot.
```

### 8.1 What the pinned target buys at stage 1

Criteria C1–C4 are **computed from the target**, not authored. There is no criteria-freezing
ceremony, no different-seat requirement, no hash-check on the criteria file — because the
criteria are a function of an artifact the loop cannot write to. `grow-button.md` §6.1 exists
entirely to approximate this, and here it is free.

C3 deserves a note: **the release's suite is the release's own definition of itself, and it
shipped green.** A patcher that leaves the suite red has failed by the release's own standard,
not by a standard the loop invented. That is G3/G4 in §3.2 made concrete.

### 8.2 Restart, boot proof, and the revert that does not depend on the app

This is the SRE requirement and `boot_guard.py` is 80% of it (§2.3). The missing 20%:

1. **`_self_editable_paths()` must include the patch's file set.** Today it covers two
   `~/.friday` paths and no source. Without this, the auto-revert cannot restore a patch.
2. **The marker is written before the restart, and it is read by the launcher, not the app.**
   `~/.friday/boot_guard/pending_patch.json` naming the snapshot and the target version. The
   tray/launcher — which is a separate process (`friday_tray.py`, PID 27644 on this machine,
   started 3 seconds before the server) — checks it. If the server fails to come up, the
   *launcher* restores. `boot_guard`'s own `restore_known_good()` runs inside the server at
   `server.py:839`, which works for a server that starts and is useless for one that does not.
3. **"Booted" is not "working."** `mark_boot_succeeded()` is already called late, after the app
   is serving — good. The patch confirmation should additionally require the release suite green
   *after* the restart, because a patch can boot fine and have broken something a test covers.
4. **Safe mode already exists and must gate the patcher too.** `FRIDAY_SAFE_MODE=1` disables
   self-modification from outside the app. A patcher that runs in safe mode has defeated the one
   switch that works when nothing else does.

### 8.3 Bounds

Inherited from `Heal.ps1`'s pair and `prompt_cache`'s ceiling:

| bound | value | note |
|---|---|---|
| model calls | 12 per patch run | `Heal.ps1`'s `max_total_heals`, same number, same reason |
| wall clock | 25 min | `max_total_minutes` |
| tokens | at `_seal_or_block` via `current_budget()` | the one chokepoint; a new call site cannot bypass it |
| T4 items | ≤ 3 per run | more than three genuine semantic merges means the install has drifted past what a patch should attempt |
| novelty | 2 repeats of a failure signature | `check_call_size`'s *"a retry that has not shrunk cannot succeed and must not be paid for twice"*, generalised |

A truncated model response does not count against the call budget (`Heal.ps1:848-865`).

---

## 9. Vision, in the patcher

Narrower than in the grow button, and correspondingly stronger.

**What is looked at:** only if a `ui`-class file changed. Three screens the patch did not intend
to alter (home, chat, settings) plus any screen the release notes name. The comparison is
**before/after on the same machine** — screenshots taken at stage 2, before anything is written,
against the same screens after stage 10. That is a far better signal than the grow button can
get, because the "before" is the user's own working app rather than a stated intent.

**What it may veto:** the closed set from `grow-button.md` §7.2 — blank screen, empty control,
undecoded image, clipped/overlapping text, duplicated element, content cut off at an edge. Two
passes must describe the same problem (`tests/app/vision.ts::judgeConfirmed`).

**What it may never do:** approve. A clean vision pass is the absence of evidence of breakage,
never evidence of correctness. C1–C4 turn things green.

**Judge unreachable.** For the patcher this is a **STOP on UI-class changes and a pass on
everything else** — a patch that touched no `ui` file does not need a judge. That is a real
softening relative to `grow-button.md` §7.2 and it is justified: the patcher's correctness
argument rests on hashes and the release suite, and vision is a backstop, not a pillar.

---

## 10. The installer as a consumer

The installer's target is *"get this machine to a working v-N from nothing"*, and it is already
the most mature consumer. What changes when it sits on the shared substrate:

- **The step menu becomes the target's drift set.** `Invoke-Step`'s list of steps is already a
  desired-state description; formalising it as a `Target` costs little and makes install and
  patch the same operation with different starting points. A repair install *is* a patch whose
  observed state is "partially installed."
- **The remediation menu becomes the T3 tier.** Shared, not duplicated.
- **It writes the ledger's first entry** (§5.3) — the pristine baseline that makes every later
  provenance decision possible.
- **It gains T4 where it has nothing today.** `Heal.ps1` can pick from a menu; it cannot fix
  something not on the menu. A genuinely novel install failure currently ends in
  `-HumanFailure` text. With the harness available, a bounded T4 attempt — *in a scratch
  directory, against a criterion the installer supplied, with the same 12-call budget* — is a
  strict improvement over "run the installer again."
- **What it must not gain:** the ability to author code into the payload it just wrote. An
  installer T4 fixes the *machine* (a missing wheel, a broken PATH, a permissions problem), not
  the *product*. Patching the payload during install means the machine now runs a version that
  matches no manifest, which destroys §5 before it starts. **This is a guard rule (SP9).**

---

## 11. Friday as a consumer — what is lost, and what replaces it

When the target stops being pinned, **G1–G12 in §3.2 all degrade at once**, and it is worth
being blunt about the shape of the loss: it is not that Friday's version is *riskier by a
margin*; it is that **every guarantee changes category from "property of the world" to
"mechanism we built."** A built mechanism can have a bug. A hash cannot.

| lost | replaced by | residual risk, honestly |
|---|---|---|
| G1 external target | frozen, hashed criteria authored before research | the criteria can be *wrong* — they cannot be *tampered with*. Wrongness is unaddressed and is why an unstatable request must be refused |
| G2 independent author | a different seat / provider | weak. Same-family models share failure modes |
| G3/G4 vacuous-test immunity | red-first gate + no-op detector + ban on editing existing tests | mitigation, not immunity. The gates are mechanical, which is the best available |
| G5 enumerable scope | declared manifest, enforced by a non-model applier | good — this one transfers almost fully |
| G6 termination | token/clock/novelty bounds + surrender | good, and the bounds are at an existing chokepoint |
| G8 legible "done" | a live artifact on the gate card | the single most important UI decision in the grow button |
| G10 symmetric rollback | store snapshots, undeclared-write detection, forward-only labelling | the genuinely hard one. §8.3 of `grow-button.md` |
| G11 cost predictability | budget, spent up front as a ceiling | you buy a bound, not an estimate |

**The load-bearing consequence for phasing:** building the patcher first is not merely a smaller
first step. It exercises stages 0–10 of the substrate, the router, the snapshot/restore path,
the boot-proof, the gate card and the journal **under conditions where a bug is visible** —
because the target is a hash and a wrong answer is provably wrong. Every one of those parts is
shared with Friday's consumer, where a bug would be a judgement call nobody can adjudicate.

---

## 12. The human gate

`grow-button.md` §9 stands. The patcher's card is much easier and should look different, because
G8 is structural here.

**What a new user sees, before anything is written** (stage 3 output, not stage 8 — the drift
set is knowable *before* the work, which the grow button can never manage):

> **Friday can update herself to 5.6.5.**
>
> 27 files will be updated to the new version.
> 2 files you (or Friday) changed will be **kept and combined** with the update.
> 3 files you changed are **untouched** by this update and will be left alone.
> 114 files that are yours are not part of the update and will not be touched.
>
> *Nothing is changed until you say go. If it doesn't start afterwards, it puts itself back.*
>
> **[ Update ]  [ Not now ]  [ Show me the details ]**

That paragraph is checkable by someone who cannot read code, which is the whole point of G8.
`Say-*`/`Write-Log` (§2.1) already encodes the two-audience split.

**Escalations that never reach that card:** boot-critical files, anything touching secrets at
rest, a store-format migration, a T5, and any T4 whose criteria did not all go green. Those go
to a "needs a person" state with the journal attached.

**Cooling off.** `grow-button.md` §9.5's watch window applies, with an important simplification:
for a patch, "revert" means *re-sync to the previous release manifest* — the same mechanism
running backwards (G10). The button is real and its implementation is the one already built.

---

## 13. STORM — the disagreement

### 13.1 The security engineer

*"The pinned target genuinely removes my biggest objection to the grow button — no
attacker-controlled documentation, no open-ended goal, no model deciding what correct means. I'll
grant that. Three things replace it.*

***The manifest is now the crown jewel.*** *Everything downstream trusts it. Ship it inside the
payload and hash the payload; if the manifest can be swapped, the patcher becomes a delivery
mechanism for arbitrary files with a green checkmark. And the patcher must never fetch a
manifest over the network at patch time — it reads the one in the payload it is installing, and
the payload's own integrity is the release's problem, solved once.*

***The blob store is a write primitive I did not previously have.*** *`~/.friday/patch-store/`
holds the bytes the patcher will write. It is content-addressed, so tampering means finding a
SHA-256 collision — fine. But `~/.friday` is user-writable and survives reinstall. Verify the
blob's hash at read time, every time, not at write time.*

***T2 is the tier that worries me, not T4.*** *T4 has a human gate and a model whose output gets
tested. T2 writes files with no human in the loop on the strength of 'diff3 said no conflict.'
Your §7.6 syntax-and-suite check is the right instinct — make it non-optional, and make the suite
check cover the merged file specifically, not just 'the suite passed.'*

*Also file this: `check_self_edit` and `check_scope` in `boot_guard.py` have no callers. You have
two written gates that enforce nothing."*

**Adopted:** manifest-in-payload with no network fetch at patch time; hash-on-read for the blob
store; the T2 post-check made a hard requirement (SP6); the unwired-gates finding filed.

### 13.2 The SRE who owns rollback

*"Better than the grow-button version because the rollback target is a real artifact. Four holes.*

***A clean merge is not a correct merge, and this is the failure I would bet on.*** *Your own
example is benign — additive changes in distant regions. The one that will hurt: the release
renames a symbol and moves a block; the local change calls the old name from a region the release
did not touch. `diff3` reports zero conflicts and produces code that imports something that no
longer exists. It parses fine. It fails at import, at boot, after the restart. Your boot proof
catches it — good — but only if the auto-revert actually covers source files, which today it does
not.*

***The scan must be atomic with respect to a running app.*** *You are hashing 901 files of a tree
that is being served from, on a machine where the dev tree and the prod tree are the same bytes.
Someone saves a file mid-scan and your provenance table is a lie. Stop the app, or scan twice and
require agreement, or take the hit and scan while stopped. Do not pretend a live scan is a
snapshot.*

***`index.html` is 1.5 MB and 42,628 lines and it is in every release delta.*** *It is your
highest-collision file by a wide margin and it is the one whose breakage is least visible to a
test suite. Treat UI-class merges as a distinct risk class with vision mandatory, not optional.*

***What happens when the patch is interrupted?*** *Power loss between file 12 and file 13. Your
snapshot exists, but nothing knows a patch was in flight. Write the marker before the first byte
and clear it after the boot proof — that also gives the launcher its trigger."*

**Adopted, all four.** The rename-and-move case becomes the named motivating example for §7.6.
Scan atomicity becomes an open question with three priced options (§16 Q2). UI-class merges get
mandatory vision. Marker-before-first-byte folded into §8.2.

### 13.3 The new user

*"I don't know what a manifest is and I'm not going to learn.*

*What I need to know is: will it break, and can I get back. Your paragraph is fine — '27 files
will be updated, 2 things you changed will be kept, if it doesn't start it puts itself back.'
That last clause is the only sentence I actually care about, and if it turns out not to be true
even once, I will never press the button again.*

*One thing bothers me. You say '2 files you or Friday changed will be kept and combined.' I never
changed a file. I don't know what a file is in this context. If Friday changed something, say
Friday changed it and say what it was in words — 'Friday added a Trello connection for you on the
14th; the update keeps it.' If you genuinely don't know who changed it, say that too, because
'something on your computer changed and I don't know why' is information I can act on.*

*And: don't ask me at a bad moment. If I opened Friday to do a thing, let me do the thing."*

**Adopted:** the card names the *change*, not the file, and reads the ledger's `by` field to
attribute it; DIVERGED-UNKNOWN is stated plainly rather than smoothed over; the patch is offered
at a boundary, never as a modal on launch.

### 13.4 The hostile party

*"You closed my best door — I can't feed you documentation any more, because the target is a hash.
So:*

*I want the **prior-release blob store**, because that is a directory of bytes your patcher will
write into the app with no human gate at T2. You verify hashes at read time now, so that is
probably shut.*

*Failing that, I want **DIVERGED-UNKNOWN treated as routine**. It is the class that means 'a file
changed and nobody knows why', and the moment it appears often enough to be annoying, someone will
make it auto-merge. The whole safety of your provenance model is that this class is rare and
noisy. If your first scan on an existing install produces 200 of them, you will have to soften it,
and I will be in one of them.*

*And I want the **T2/T4 boundary moved by convenience**. Every time a T4 escalation is
inconvenient, someone will widen what counts as a clean merge. Your rule that the router may only
escalate is the only thing stopping that, and it is one line of code away from being untrue."*

**Adopted:** DIVERGED-UNKNOWN's rarity is a *design requirement*, which is why the ledger must
start at install (§5.3) — an install-time baseline makes the class rare by construction. The
escalate-only property becomes a guard rule with a test.

### 13.5 Synthesis

All four converge on the same ranking, and it is not the ranking the feature's name suggests:

1. **The manifest and the ledger** — nothing works without them, and neither needs a model.
2. **The T2 post-check (parse + suite + boot)** — because a clean merge that is wrong is the
   most likely serious failure, and it is invisible without it.
3. **The boot proof and an auto-revert that covers source** — because "if it doesn't start it
   puts itself back" is the only sentence the user cares about, and today it is not true of code.
4. Everything else, including the harness.

**The harness is fourth.** That is not an argument against the maintainer's claim that all three
consumers need one — they do, and building it once is right. It is a statement about order: the
substrate's *value* is in verify-first, provenance, and reversibility, and the model is the part
that runs least often.

---

## 14. Guard rules

`SP` for self-patching. `GB*` from `grow-button.md` and `FA*` from `friday-builds-agents.md`
apply unchanged to the harness consumer.

| | rule |
|---|---|
| **SP1** | A step is successful if and only if its verify passes afterwards. An action's return value, exit code and absence of exceptions are evidence, never a verdict. (`Common.ps1`'s one rule, promoted to substrate.) |
| **SP2** | The release manifest ships inside the payload, is hashed with it, and is never fetched over the network at patch time. |
| **SP3** | The router is mechanical, not a model. It may escalate a case but may never de-escalate one, and that property carries a test. |
| **SP4** | No file is overwritten whose provenance is DIVERGED-* unless a 3-way merge succeeded or a human chose. DIVERGED-UNKNOWN is quarantined before anything touches it. |
| **SP5** | EXTRA files are never deleted by a patch. Not one. |
| **SP6** | Every T2 merge is followed by parse + the release suite + boot. A merge that reports no conflict is not thereby correct. |
| **SP7** | The patcher never touches `boot_guard.BOOT_CRITICAL`, secrets at rest, or a store format automatically. Those are T5. |
| **SP8** | A pending-patch marker is written before the first byte and cleared only after the boot proof. The launcher, not the app, acts on it. |
| **SP9** | An installer T4 may fix the machine. It may never author changes into the payload — an install must produce a tree that matches its manifest exactly. |
| **SP10** | `_self_editable_paths()` covers the patch's file set, or the auto-revert is a fiction. |
| **SP11** | `FRIDAY_SAFE_MODE` disables the patcher exactly as it disables self-modification. |
| **SP12** | The install ledger's first entry is written by the installer, on a tree it just wrote, so the baseline is pristine by construction. |
| **SP13** | The blob store is content-addressed and verified **at read time**, every read. |
| **SP14** | A patch does not begin on a tree whose release suite is already red, or whose scan did not complete. |
| **SP15** | A pure-Python diff3 does not ship until it agrees with `git merge-file` on a corpus of several hundred real cases from this repo's history, conflicts included. |
| **SP16** | The scan is atomic with respect to writers, by whatever means §16 Q2 settles on. A live scan is not a snapshot and may not be presented as one. |
| **SP17** | UI-class merges require vision. Other classes do not. |
| **SP18** | The gate card names changes in human terms with attribution from the ledger, states DIVERGED-UNKNOWN plainly, and is shown before anything is written. |

---

## 15. Phasing

**Phase 0 — the comparable object. No model, no loop, no risk.**
`release-manifest.json` at build. The install ledger, written first by the installer. The scan
and the provenance table. A read-only `friday doctor`-style report: *"you are at 5.6.4; 5 files
differ; here they are and here is what I think each one is."* **This alone would have prevented
the confusion in §4.2 where two readings of one repository gave different answers**, and it ships
without a single line of the harness.

**Phase 1 — T0/T1/T2, mechanical only.** The blob store, the diff3 (with SP15's corpus), the
router, the snapshot, the marker, the boot proof, `_self_editable_paths` extended to source, the
gate card. **No model call anywhere in this phase.** On the measured evidence (§7.4) this is a
complete patcher for the drift that actually exists.

**Phase 2 — T3.** The remediation menu, shared with `Heal.ps1`. First model call, bounded to an
enum, cannot author code.

**Phase 3 — T4, the harness.** Worktree isolation, the build/verify/vision/iterate loop, the
journal, the budget at `_seal_or_block`, surrender. Judged by the release suite — the friendliest
possible first target for the loop, because correctness is external.

**Phase 4 — the installer moves onto the substrate.** Its steps become a `Target`; its menu
becomes T3; it gains a bounded T4 for machine problems (SP9).

**Phase 5 — Friday as consumer 3.** `grow-button.md`, with §11's manufactured guarantees, on a
substrate that has already been exercised for months against a target that could prove it wrong.

---

## 16. Open questions

- **Q1.** What does a full 901-file SHA-256 scan cost on a cold cache on a spinning disk? Decides
  whether the scan is on the boot path, on a schedule, or on demand. **UNKNOWN.** Settled by
  measuring it on the slowest machine available.
- **Q2.** Scan atomicity (§13.2). Three options: stop the app and scan; scan twice and require
  agreement; or hash-with-mtime-recheck. All three are cheap; none is obviously right; a live
  scan being treated as a snapshot is the failure. **Unresolved.**
- **Q3.** Ship MinGit (~50 MB, a new pinned download) or write diff3 in Python (zero bytes, real
  correctness risk, SP15's corpus)? §7.5 recommends Python. **Unresolved**, and it is the
  single largest fork in the build.
- **Q4.** How large is the blob store in practice, and can it be pruned to diverged files only
  after the first scan? **UNKNOWN.** Measure on this repo's payload.
- **Q5.** What happens to a payload install that has *already* drifted before the ledger exists?
  Its first scan yields DIVERGED-UNKNOWN for everything changed, which §13.4 identifies as the
  class an attacker wants normalised. Options: treat a first-ever scan as establishing a baseline
  (loses the ability to detect prior tampering — but there was never any ability to detect it);
  or refuse to patch until a human adjudicates (unusable). **Leaning toward the former, stated
  plainly on the card.** Unresolved.
- **Q6.** Does the patcher restart the app itself, or wait for the next natural restart? Itself is
  better (the change takes effect and is proven) and worse (it interrupts). §13.3 says do not
  interrupt. **Unresolved.**
- **Q7.** What does the patcher do about the 311 local-only commits and 10+ worktrees on this
  machine? They are outside its model entirely — it reasons about a *tree*, not a *history*. Is
  the dev-machine consumer a different Target that reasons in git terms (`git merge`, real
  ancestry) rather than manifests? **INFERRED: probably yes, and it is strictly easier.**
  Unresolved whether that is one Target with two backends or two Targets.
- **Q8.** Where does the T4 model come from on a local-only install? Same question as
  `grow-button.md` Q7 and it has the same weight: a 4b local model authoring a semantic merge into
  the trusted core is a different proposition from Opus. Should T4 require a cloud seat, and say
  so? **Unresolved.**
- **Q9.** Should the patcher also detect **stale process** (§4.4) — code on disk newer than the
  code running — and offer a restart? It is nearly free once the manifest exists and it addresses
  a real observed symptom. Not specced above because it is arguably a separate feature.

---

## 17. Where I think the framing still has a problem

**17.1 "The self-patching installer has an external definition of correct" is true, and it stops
being true exactly where the feature is interesting.** For a pristine tree the guarantee is total
and the work is a file copy. For a diverged tree — the only case where a patcher is more useful
than a reinstall — the desired state is a *reconciliation* that no external artifact defines. So
the pinned target does not remove the vacuous-test problem from the system; it **confines it to
the intersection of local drift and release change**, which on the evidence is small. That is a
large, real win and it should be claimed as what it is: a reduction in surface, not an
elimination. Claiming elimination would set up the exact over-trust that lets a wrong T2 merge
through.

**17.2 The most valuable part of this feature involves no model, and the name hides that.**
"Self-patching installer" implies the interesting part is the patching. Measured, the interesting
part is *knowing what you have* — and §4.2 is the proof: two readings of this repository, hours
apart, gave different answers about whether a function exists, because one read the worktree and
one read the tag. A read-only doctor report (Phase 0) is most of the value and none of the risk.

**17.3 "Like our self-healing installer, instead" undersells the difference in stakes.** The
healing installer repairs a machine that is not yet working, where the worst case is that the
install fails and the user runs it again. The patcher modifies a working install that holds a
vault, connector tokens and a user's accumulated state, where the worst case is a Friday that
does not start and a user who cannot fix it. The *mechanism* is a good analogy. The *risk
posture* is not, and the parts that must be stronger are the boot proof and the revert, not the
repair.

**17.4 One harness, three consumers is right — and the third consumer will pull on the substrate
in a direction the first two never do.** The installer and patcher want the loop to be *small,
bounded, and mostly skipped*. Friday's consumer wants it to be *capable, iterative, and patient*.
Those are opposing pressures on the same code, and the way this goes wrong is that the harness
gets built for the patcher's needs, then generalised under deadline for Friday's, and the
generalisation quietly relaxes something the patcher was relying on. The defence is that the
guarantees in §3.2 are **declared per-target and asserted at stage 0**, not assumed: a Target
whose `is_authoritative()` is False must present its manufactured substitutes for G1–G6 or the
loop refuses to start. Otherwise the ledger in §3.2 is a table in a document rather than a
property of the system.

**17.5 A small one, but it is the one I would bet money on.** The failure that actually ships is
not a bad model call. It is `diff3` returning zero conflicts on a rename-and-move, the merged file
parsing cleanly, the suite not covering that path, the app booting, the boot proof passing, and a
feature being quietly broken for a week. Every mechanism in this document is aimed at the model,
and the model is not the risk. **SP6 is the rule that matters most and it is the least
interesting one on the list.**

---

## 18. Sources

**Read, this machine, 2026-08-29, tree at `f60ee0d`+:**

- `packaging/windows/lib/Common.ps1` — the one rule (`:9-20`), `Invoke-Step` (`:496-620`),
  `Say-*`/`Write-Log` split (`:22-26`).
- `packaging/windows/lib/Heal.ps1` — closed action space (`:26-35`), menu criteria (`:253-257`),
  `Initialize-Healing` (`:86-114`), truncation handling (`:848-865`), tool schema (`:679-698`).
- `packaging/windows/healing.json` — `max_total_heals`, `max_total_minutes`, model choice.
- `packaging/windows/install.ps1` — `InstallRoot` (`:34`), `AppDir` (`:76`), wipe-and-recopy
  (`:434-439`), `$Version` (`:92`), manifest (`:819-841`).
- `packaging/windows/build-installer.ps1` — payload completeness check (`:214-251`), credential
  scan (`:289-343`), the single `Assert-FileHash` (`:365`).
- `src/agent_friday/services/boot_guard.py` — whole file; `BOOT_CRITICAL` (`:53-59`),
  `BLAST_RADIUS_FORBIDDEN` (`:64-70`), `mark_boot_succeeded` (`:144-154`),
  `snapshot_known_good` (`:162-190`), `_self_editable_paths` (`:223-226`),
  `check_self_edit` (`:231`), `check_scope` (`:268`). Wired at `server.py:839-851`;
  `check_blast_radius` called from `workspace_studio.py:200-203`.
- `src/agent_friday/services/model_router.py:88` `_seal_or_block`;
  `services/prompt_cache.py:215` `check_call_size`.
- `src/agent_friday/core/__init__.py:1201-1206` `_RUN_COMMAND_ALLOW`.
- `src/agent_friday/services/interactive_sessions.py` — security posture docstring.
- `tests/app/vision.ts` — `judge` / `judgeConfirmed`; `tests/app/liveness.ts`.

**Measured, 2026-08-29:**

- `git ls-tree -r v5.6.4 --name-only | wc -l` → 901.
- `git diff --name-only v5.6.4 | wc -l` → 5; `git ls-files --others --exclude-standard | wc -l`
  → 114; `git log --branches --not --remotes | wc -l` → 311.
- `git diff --name-only vN vN+1 | wc -l` across v5.6.0…v5.6.4 → 13, 12, 65, 27.
- Intersection of the 5 modified files with each upgrade delta → 2 (`index.html`,
  `core/__init__.py`).
- Three-way merge simulation: `patch -p1 --fuzz=3` of the real local diffs onto `v5.6.3`
  (clean, offset −22 on `index.html`), then `git merge-file ours base theirs` → **exit 0, zero
  conflicts, both files**; `core/__init__.py` 2574 + 12 + 36 = 2622 lines, exactly additive;
  local and release markers both present; no conflict markers in either output.
- `Win32_Process` → server PID 27120 from `~/Projects/friday-desktop/server.py`, started
  2026-08-29 10:04:53; tray PID 27644 at 10:04:50. No `%LOCALAPPDATA%\AgentFriday`.
- `git log` timings: caching `0c47906` 2026-08-26 11:09 → test repair `5a4a26d` 2026-08-28 18:44
  (2 d 7 h). Pricing `2d8e2b3` present on `origin/main`.

**Documents:** `docs/design/grow-button.md`, `docs/design/friday-builds-agents.md`,
`docs/design/switchyard-position.md`, `docs/audits/caching-audit-2026-08-26.md`,
`KNOWN_ISSUES.md` §1.
