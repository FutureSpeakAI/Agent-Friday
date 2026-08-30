# Friday builds agents — the containment is the feature, and it is already half-built

> **Re-check 2026-08-29.** Written 2026-08-23; the repo has since shipped
> through **v5.7.0**. One fact in §3.2 changed and is corrected inline
> where it appears. The position, the threat model and the FA1-FA12
> requirements are **unchanged and still unimplemented** — this remains a
> design document describing a system that does not exist.
>
> Re-verified as **still true**: `python_script_adapter.py` still spawns with
> `env={**os.environ, "FRIDAY_WORKER": "1"}`, so every provider key still
> reaches model-authored code; and the vault is still plaintext at rest by
> default (`privacy/vault_crypto.py`, module docstring). **FA2 stands, and is
> still the largest security win in this document.**
>
> **What changed:** v5.7.0 moved the vault passphrase out of `start.bat` into
> the OS keychain and a DPAPI-wrapped file under `~/.friday/security`. Since
> `core._bootstrap_env_from_launch_scripts` was what copied that line into
> `os.environ`, `FRIDAY_PASSWORD` **is no longer automatically present in the
> inherited environment** — it reaches a worker only if a human exported it.
> The keys still flow; that one credential no longer does by default.

**Date:** 2026-08-23
**Branch:** `higgsfield-integration` @ `fbb52fb`. Doc-only.
**Status:** design/position. **No implementation code exists for this document and none is
proposed for immediate build.** Written to be read cold by a fresh-context session: every
fact needed is here or at a cited `file:line`.
**Subject:** Stephen's proposal that Friday should be able to *build agents* using
[NVIDIA-NeMo/labs-OO-Agents](https://github.com/NVIDIA-NeMo/labs-OO-Agents) (NOOA),
evaluated 2026-08-23 against the repository at `main` and the paper
[arXiv:2607.20709](https://arxiv.org/abs/2607.20709). The question is not "is NOOA good" —
it is what it would mean for Friday to author a program that later runs unattended on
Stephen's machine, and what has to be true first.
**Method:** STORM — multi-perspective questioning first, simulated disagreement second,
cited synthesis third (§5). The case *against* is argued at full strength and is not a
formality.
**Inherits:** [`switchyard-position.md`](switchyard-position.md) (the shape of an
adopt-the-ideas-not-the-dependency evaluation of an NVIDIA-NeMo research project, and
guard rules SW1–SW8), [`tool-index.md`](tool-index.md) (the registry measurement and the
seat ceiling), [`context-assembly.md`](context-assembly.md) (§1.2 the 32,768 cap, §3.1
deferred tool loading — **not amended here**), `docs/AUTONOMY_SPEC.md` and
`THREAT_MODEL.md` (the egress gate as final barrier).

**Branch note.** Three other sessions hold ~254 uncommitted files in this tree. This
document touches only this file. **No code was read into and no code was written.** All
source citations are reads.

**Evidence registers:**

- **VERIFIED** — the cited line, file, or command output was read during this document's
  audit runs (2026-08-23), against the working tree at `fbb52fb`+ and NOOA at `main`.
- **MEASURED** — a number produced by a stated method, with the method stated.
- **INFERRED** — a conclusion from verified facts, reasoning shown.
- **UNKNOWN** — not determined; the check that would settle it is named.
- **REPORTED** — asserted by Stephen or by an upstream author; not independently checked.

---

## 0. The position, up front

**Do not adopt NOOA now.** Not because the idea is wrong — the idea is right, and Friday
already half-invented it — but because the audit turned up something more urgent than the
proposal, and the proposal is what surfaced it:

> **Friday already runs model-authored work unattended, with *more* privilege than the
> same work typed into chat, and with no sandbox, no capability scope, and no human gate.**
> This is true today, at `fbb52fb`, with 18 enabled schedules on Stephen's machine
> (**VERIFIED**, §3.3). NOOA does not create this risk. It makes the existing risk
> load-bearing and therefore impossible to keep ignoring.

Four findings govern everything below.

1. **Friday has no sandbox.** `FRIDAY_SANDBOX_MODE` is an in-process string-policy gate in
   the Flask server, not an isolation boundary — and its own docstring is wrong about what
   it confines (**VERIFIED**, `core/__init__.py:1144-1160`, §3.1). Meanwhile
   `PythonScriptAdapter` executes arbitrary model-authored Python with
   `env={**os.environ, ...}` — every API key (and, until v5.7.0 moved it out of `start.bat`, the vault passphrase — see the re-check at the top) — under a wall-clock
   timeout and nothing else (**VERIFIED**, `services/worker_adapters/python_script_adapter.py:61-68`).
   NOOA's README says in bold that its guardrails "are defense-in-depth guardrails, **not a
   containment boundary**" and that the boundary must be OS-level. Friday's guardrails are
   weaker than NOOA's *and* Friday has no OS-level boundary to fall back on.

2. **The capability model this needs already exists in the tree, built and tested, and is
   simply not applied to the unattended paths.** `services/subagents.py:82-160` defines
   `BUILTIN_SCOPES` (ring caps, tool allow/deny lists, step budgets, time budgets), with
   `save_custom_scope()` for user-defined scopes and a `scope_check()` governance hook.
   `services/goals.py:738-765` applies it correctly and documents *exactly* why. Workflow
   chains and scheduled `agent_prompt` jobs pass `scope=None` (**VERIFIED**,
   `services/agent.py:2559-2565`, `services/scheduler.py:504-505`), and unscoped tasks
   always pass the check (`services/subagents.py:238-241`).

3. **Therefore the smallest useful first step is not a NOOA agent.** It is to apply the
   scope mechanism that exists to the unattended paths that exist (§6.1). That work is
   worth doing whether or not a single line of NOOA is ever installed, it is the
   prerequisite for *any* version of this proposal, and it is small.

4. **There is a version of this idea that does not depend on NOOA, and Friday shipped it
   four days ago.** `build_film.py` is 10.6 KB of Python Friday wrote, that three films now
   depend on, invoked by a skill through `run_command`. That is code-as-action, natively,
   with no framework (§2.1). What NOOA adds beyond it — `...`-body generation methods,
   pass-by-reference over live objects — is real and novel, and is also the part that
   demands the most of the model (§4.3).

**Licence: confirmed, Apache 2.0, no obstacle** (§1.3). Two of Stephen's premises need
correcting on the facts: NOOA is at **169 commits**, not 67 (§1.2), and the **959 MB
installer figure does not exist anywhere in this repository** (§4.2). Neither correction
changes the recommendation; both change the arithmetic.

---

## 1. What NOOA actually is

### 1.1 The programming model — accurately described

Stephen's summary is correct and I will not restate it at length. From the abstract
(**VERIFIED**, arXiv:2607.20709):

> "an agent is a Python object. Its methods are the actions the model can take, fields are
> its state, docstrings are its prompts, and its type annotations are contracts. A method
> whose code body consists of `...` is completed at runtime by an LLM-driven agent loop,
> while methods with normal bodies remain standard deterministic Python."

The README adds the mechanism that matters for §2.3 (**VERIFIED**):

> "**Code as action.** The model acts by writing Python in a Jupyter-style REPL with access
> to `self`, imports, and helpers — Python methods and type annotations supply the callable
> interfaces, reducing the need to write separate tool-schema definitions."

The paper claims six model-facing ideas combined on one surface for the first time: typed
I/O, pass-by-reference over live objects, code as action, programmable loop engineering,
explicit object state, and model-callable harness APIs for context and events. Evaluation
is on **SWE-bench Verified, Terminal-Bench 2.0, and ARC-AGI-3** (**VERIFIED**, abstract).
Note what that evaluation set is: frontier coding and reasoning benchmarks. It is evidence
that *strong* models use this interface well. §4.3 is about whether that transfers to
`gemma4:12b` on a 32,768 seat, and the honest answer is that nothing in the paper speaks to
it.

### 1.2 Maturity — correcting the premise

**VERIFIED** against the repository landing page, 2026-08-23:

| signal | value |
|---|---|
| commits on `main` | **169** (not 67) |
| stars / forks | 1.0k / 146 |
| open issues / PRs | 15 / 16 |
| latest pinned tag referenced in README | `v0.0.7` |
| distribution | published to PyPI as `nooa`, plus `nooa-cli`, `nooa-memory`, `nooa-bench` |
| self-description | "NOOA is **research software** … expect rough edges" |

169 commits is a bigger, faster-moving project than 67 — which cuts both ways. More
velocity means more capability and more churn. A `v0.0.x` version string on a project with
16 open PRs and four separately-versioned distributions is a project whose API will move
under you. `switchyard-position.md:171` recorded exactly this failure mode for the sibling
NeMo project — "three further API breaks landed after the 0.2.0 tag" — and rule **SW8**
there ("version pins are exact; an upgrade is a change with a test run behind it, not a
`pip install -U`") applies here unchanged and is carried forward as **FA12** (§3.9).

**UNKNOWN:** whether NOOA's test suite runs on Windows. Switchyard's did not (`switchyard-position.md:176-178`),
and that cost was priced. The check that settles it: read `.github/workflows/` on the NOOA
repo for a `windows-latest` job. Not done here; it is a gating check before any pilot
(§6.2).

### 1.3 Licence — confirmed, no obstacle

**VERIFIED.** Apache 2.0, stated in the README badge, in the README's own Licence section
("Apache 2.0. See LICENSE and THIRD_PARTY_NOTICES.md"), and by a `LICENSE` file at the repo
root. Same licence as Switchyard, already accepted in this project (`switchyard-position.md`
§0 — "benchmarked escalation prompt that is Apache-2.0 and directly reusable"). Attribution
and NOTICE obligations only; no copyleft, no field-of-use restriction, no patent trap
beyond the standard Apache grant/termination clause. **This is not a constraint on the
decision.**

One consequence worth stating positively: Apache 2.0 means the *ideas and the code* can be
lifted without taking the dependency. That is the option §6 recommends.

### 1.4 The safety note, quoted in full, because it is the whole spec

From the NOOA README, under a ⚠️ heading (**VERIFIED**, emphasis theirs):

> "NOOA validates generated code (AST checks) and applies module deny-lists before
> execution. **These are defense-in-depth guardrails, not a containment boundary.** They
> exist to keep generated code from freezing the event loop and to catch common mistakes
> early — not to stop code that is actively trying to escape. A static checker over Python
> cannot provide that guarantee: `open()` gives arbitrary file access, `importlib` can load
> modules straight from a path, and reflection reaches the rest. **The containment boundary
> is OS-level isolation** — always run agents that execute generated code inside a sandbox
> such as a container, VM, or NVIDIA OpenShell."

Three observations.

1. **This is the most honest paragraph in either project's documentation**, and it is
   written in the same register as `KNOWN_ISSUES.md` §1. Upstream is not overselling.
2. **NOOA's in-process guardrails are strictly stronger than Friday's** and NVIDIA still
   says they are insufficient. NOOA does AST validation and module deny-lists before
   execution; Friday does substring matching on a lowercased command string
   (`core/__init__.py:1191-1193`, **VERIFIED**) and has no AST check on generated code
   anywhere (**VERIFIED** — the only `compile(`/`exec(` hits under `src/` are `re.compile`).
3. **"Ensure you run NOOA agents in a sandboxed environment isolated from your primary
   filesystem"** is the exact instruction Friday cannot currently follow, on the exact
   machine that holds the vault, the keys, and Cassie's files.

That is the spec. Everything in §3 is an attempt to build the thing that sentence assumes
you already have.

---

## 2. Why this is plausible here rather than fantasy

### 2.1 `build_film.py` — code-as-action that already worked

**VERIFIED**, `~/.friday/skills/storybook-film/`:

- `SKILL.md`, 9,290 bytes — a procedure, `tool_chain` declared in front matter,
  `success_criteria` declared, triggers declared.
- `build_film.py`, 10,681 bytes — a program Friday wrote. Its docstring documents a
  `film.json` schema, a directory convention, and three "hard-won rules baked in
  (2026-08-19 storybook sessions)": clips are time-stretched rather than freeze-held, quiet
  layers are `loudnorm`'d at measured targets, narration rules the mix.

Read the SKILL.md's Phase 2 step 7 and note the shape (**VERIFIED**):

> "write_file a film.json manifest into the project folder (schema documented at the top of
> the build script), then run_command: `python …\build_film.py <project folder>`"

Friday writes *data* (the manifest) and invokes *code she previously wrote* (the
assembler). That is not "twenty chained tool calls"; it is a program with a typed-ish
interface and a caller. The delta to NOOA is smaller than it looks: replace `film.json`
with constructor arguments, replace the docstring-schema with type annotations, replace
`run_command python …` with a method call, and you have a NOOA agent minus the `...`
bodies.

**And the evidence that it worked is in the tree, dated, with failures recorded.**
`~/.friday/creations/film-lessons/lessons.md`, 44 lines, append-only, three dated entries
(**VERIFIED**):

| date | production | what it establishes |
|---|---|---|
| 2026-08-19 | "Princess Liberty and the Storm Dragon", 85s, 115.92 credits | supervised; user overrode the analytical voice pick |
| 2026-08-20 | "Liberty and the Masterless Storm", 198s, ~227 credits | supervised; user feedback *during* production drove the permanent `setpts` fix in `build_film.py` |
| 2026-08-20 | "Ember and the Big Dark", 47s, 3 pages | **"FIRST FULLY AUTONOMOUS RUN"** |

**INFERRED, and it is the single most useful precedent in this document:** the autonomous
run came *third*, after two supervised productions had written corrections into
`lessons.md`, and Friday's own judgment on that run ("she correctly chose DIRECT production
over `create_workflow` for a 3-pager") was assessed by a human and explicitly ratified —
"Valid judgment, keep it." Stephen has already run the supervised-then-unattended protocol
this document argues for in §3.8. He ran it on films. The proposal is to run it on
programs, where the blast radius is different.

Two lessons in that file are load-bearing for §3 and get cited again:

- *"A chain can 'complete' with zero output if the model provider dies and the error becomes
  the result text. Files-on-disk is the only real completion signal."*
- *"'his daughter' in the bible was redacted by the privacy gate when a cloud seat read the
  file — the step went blind and reported honestly."*

The first is `KNOWN_ISSUES.md` §1's dominant failure mode, observed in production, four
days ago. The second is the egress gate working — and is the reason §3.5 routes an agent's
privileged calls back through it rather than around it.

### 2.2 Chains are data; agents are code. An honest comparison.

**VERIFIED** against `services/agent.py:2461-2846` and the live chains in
`~/.friday/workflows/` (`engine-selftest.json`, `teen-storybook.json` 17 KB,
`teen-storybook-tail.json`).

A workflow chain is a JSON file: `{name, slug, description, seat, steps[], updated}` where
a step is `{name, prompt, with_context, seat, retries}` (`agent.py:2486-2519`). There is no
step *kind*. Every step is a free-text prompt that becomes a full autonomous agent run with
the entire tool registry — the tool description says so (`agent.py:2674-2680`, **VERIFIED**):

> "Each step is a full autonomous agent run with all your tools; steps run in order and each
> receives the previous step's result as context."

| | workflow chain (`create_workflow`) | agent-as-class (NOOA) |
|---|---|---|
| representation | JSON data | Python source |
| control flow | strictly linear index advance (`agent.py:2810-2831`) | anything Python does |
| branching | **none** | native `if` |
| looping | **none** | native `for`/`while` |
| state between steps | previous step's result text, truncated to 6,000 chars (`agent.py:2819-2822`) | object fields, typed, arbitrary |
| retries | 0–3 per step, prompt re-prefixed with the failure (`agent.py:2715-2746`) | your own `except`, or the framework's typed retry |
| failure detection | string-match against `_CHAIN_FAILURE_SIGNATURES` (`agent.py:2764-2776`) | exceptions, or a real assertion |
| durability | definition is durable; **run status is reconstructed from in-memory `TASKS` and lost on restart** (`agent.py:2568-2614`) | whatever you write |
| testability | run it and read the transcript | `pytest` |
| version control | a JSON blob diffs badly | a `.py` file diffs like code |
| privilege | full registry, ring 2, confirmation bypassed, **no scope** (§3.3) | whatever you grant it |
| who can read it | anyone | anyone |
| **failure floor** | a step that "completes" having done nothing | a program that runs correctly and does the wrong thing, forever, on a timer |

**What chains are genuinely better at**, and this is not a consolation prize:

- **Legibility to a non-programmer.** A chain is a list of English instructions. Stephen can
  read `teen-storybook.json` and know what it will attempt. He cannot read a 300-line
  generated Python class as quickly, and neither can a non-technical second user.
- **Failure containment by construction.** A step is bounded by the agent loop's own
  governance — every tool call funnels through `_execute_tool`, the ring check, the vault
  gate, the egress gate. Model-authored *code* running in a REPL bypasses that funnel
  entirely, because it calls Python functions, not tools. **This is the central technical
  point of the whole document**: Friday's entire safety apparatus is a tool-call
  interceptor, and code-as-action is precisely the pattern that does not make tool calls.
- **They already exist and are debugged**, at a project whose known-issues file is long for
  good reasons.

**What agents-as-classes are genuinely better at:**

- Anything requiring a loop, a conditional, or state — which is to say, anything a chain
  currently expresses by having a prompt ask a model to *simulate* a loop.
- Being tested before being trusted. This is the largest advantage and it is
  underappreciated: you cannot unit-test a prompt chain, and `KNOWN_ISSUES.md` §1 is a
  seven-item list of things that would have been caught by a test.
- Diffing. §3.7 is built on this.

**Do they coexist, or does one supersede the other?** They coexist, and the boundary is
sharp: **chains for orchestration a human should read; classes for logic a machine should
execute.** A chain step whose prompt is "run the agent I wrote" is a perfectly good
composition, and it keeps the human-readable layer human-readable. Superseding chains would
throw away the one property — legibility to Stephen — that the rest of this project's
design documents keep insisting on (`SEATS_AND_TRANSPARENCY_SPEC.md` B2, "no silent
changes"; `tool-index.md` §7.0, "never silently drop tools; disclose").

### 2.3 Does code-as-action change the tool-index calculus?

**Partly, in the direction Stephen hopes, and less than it first appears.**

The measured position (**MEASURED**, `tool-index.md` §1.2–1.3, method: static `ast`
extraction, estimator `len(json.dumps(tool))//4` per `services/tool_budget.py:40-45`,
calibrated to 1.4% against the independent AUDIT figure):

| configuration | tools | schema tokens |
|---|---:|---:|
| built-ins only | 64 | **10,755** |
| + GitHub | 90 | 14,041 |
| + Higgsfield + GitHub | 176 | ~46,800 |

Against a 32,768 seat with a 4,096 output reserve (28,672 usable) and ~5,100 of fixed
non-tool cost, the third row is a hard fail: `request (46288 tokens) exceeds the available
context size (32768 tokens)` (`tool_budget.py:5`, **VERIFIED**).

**Correction worth carrying:** the 32,768 is not a model limit. `gemma4:12b` declares
262,144 (`docs/contracts/roles-and-model-identity.md:191-193`). It is
`residency_arbiter.MAX_SEAT_NUM_CTX = 32768` (**VERIFIED**, `services/residency_arbiter.py:609`),
a VRAM policy cap whose comment reads: a seat "spawned at its architectural 262,144 left
448 MiB of 12,282 and took a monitor off the desktop." The seat cannot grow because the
monitor is load-bearing.

**The case that code-as-action helps.** 86 of those 176 tools are Higgsfield; 26 are
GitHub. Both are APIs with coherent surfaces that a Python module could expose as, say,
eight methods with docstrings and type annotations instead of 86 JSON schemas at 168
tokens each. One `run_python` tool plus a *narrative* interface description is plausibly a
tenth the tokens of the schema wall. This is real, and it is the same insight as
`tool-index.md`'s deferred loading, arrived at from the other end: **defer the schema, or
delete the schema.**

**Three reasons it changes the calculus less than it seems.**

1. **The interface still has to be described.** NOOA replaces JSON schemas with docstrings
   and type annotations, which are cheaper per capability but not free, and the model must
   still be told what exists. `tool-index.md` §3.1's deferred loading already gets the
   built-in registry down to a searchable index; the marginal win of code-as-action over
   *deferred* schemas is much smaller than over *eager* schemas.
2. **It does not compose with the existing design; it competes with it.** `context-assembly.md`
   §3.1 and `open_toolbox` are decided, and `fit_tools_to_seat` landed 2026-08-19.
   Code-as-action is a second answer to the same question. Running both means maintaining
   two capability-exposure mechanisms in a project that `KNOWN_ISSUES.md` says already
   loses track of which subsystems are alive.
3. **The failure mode is worse at the same token count.** `tool-index.md` §8 cites
   `services/tool_integrity.py:46` and `docs/audits/inference-discovery.md:113`:
   **prose-narrated fake tool calls** — local models emitting text that looks like a tool
   call without one occurring. A model that hallucinates a tool call fails visibly, because
   the tool did not run. A model that writes *plausible Python* against a live object can
   fail invisibly: the code runs, returns something, and reports success. That is
   `KNOWN_ISSUES.md` §1's dominant failure mode with a compiler in front of it.

**Net (INFERRED):** code-as-action is a legitimate long-term answer to the schema wall and
should be recorded as such, but it is not a reason to adopt NOOA *now*, and it does not
supersede `tool-index.md` or `context-assembly.md`. Those documents stand unamended. If the
registry keeps growing and deferred loading stops sufficing, this is the next idea to
reach for — and §7 Q2 names the measurement that would trigger it.

---

## 3. The hard part: containment

### 3.1 Friday has no sandbox — what `FRIDAY_SANDBOX_MODE` actually is

Stephen's brief says "Friday has sandbox capability already — find it and assess whether
it's sufficient." Found. **It is not a sandbox and it is not sufficient**, and the honest
version of this section is that the name is doing damage.

**VERIFIED**, `core/__init__.py:1140-1202`:

```python
FRIDAY_SANDBOX_MODE = os.environ.get("FRIDAY_SANDBOX_MODE", "confine").lower()
FRIDAY_SANDBOX_ROOT = (os.environ.get("FRIDAY_SANDBOX_ROOT", "") or str(HOME))
...
_SANDBOX_PATH_TOOLS = {"write_file": "path"}
```

`_sandbox_policy(name, args)` is a function called before a tool handler runs, in the Flask
server process, registered as a pre-hook at priority 30 (`services/agent.py:5141-5150`).
Assessment, point by point:

| property | reality |
|---|---|
| isolation boundary | **none** — same process, same user, same token |
| filesystem | only `write_file` is confined, and only to `HOME` |
| network egress | not restricted by this gate at all |
| environment / keys | fully inherited |
| resource limits | none |
| cross-platform | Windows/PowerShell-flavoured blocklist (`remove-item`, `reg delete`, `icacls`, `takeown`) |
| enforcement mechanism | **substring match on a lowercased command string** (`:1191-1193`); strict-mode allowlist inspects only the leading token (`:1194-1200`) |

**And its docstring is wrong about itself** (**VERIFIED**, `core/__init__.py:1144-1145`
versus `:1160`):

> `#   "confine" — DEFAULT. Path tools (write_file/read_file) must stay under`
> `#               FRIDAY_SANDBOX_ROOT; …`

but `_SANDBOX_PATH_TOOLS = {"write_file": "path"}` contains one entry, and the comment two
lines below concedes it: *"Only WRITES are path-confined by default."* So the mode
documentation claims reads are confined and the code confines writes only. `_tool_read_file`
(`services/agent.py:538-556`) resolves any absolute path and reads up to 500 KB with no root
check. **The whole filesystem is readable by any agent turn, including
`~/.friday/credentials.json` and `~/.friday/vault/`.**

This is `KNOWN_ISSUES.md` §1's named bug class — a comparison that discards the meaning —
sitting in the module named `sandbox`. It should be filed there on its own merits,
independent of this proposal.

Two structural bypasses, for completeness: `python` and `git` are both on
`_RUN_COMMAND_ALLOW` (`:1151-1156`), so `python -c "..."` passes even *strict* mode; and a
substring blocklist does not survive a shell that supports `;`.

### 3.2 The execution surfaces that already exist, priced

**VERIFIED.** Every one of these executes code today, at `fbb52fb`.

| # | site | what runs | boundary | env inheritance | limits |
|---|---|---|---|---|---|
| 1 | `services/worker_adapters/python_script_adapter.py:61-68` | **arbitrary Python; the prompt IS the source** | child process, same user | `env={**os.environ, "FRIDAY_WORKER": "1"}` — **every key.** *(Until v5.7.0 this also carried `FRIDAY_PASSWORD`, the vault KDF passphrase, because it was bootstrapped into `os.environ` from `start.bat`. It now lives in the keychain / a DPAPI file and is no longer inherited by default. The provider keys are unchanged.)* | wall-clock only |
| 2 | `services/agent.py:1242-1261` `run_command` | arbitrary PowerShell | child process, same user | full (no `env=`) | 300 s |
| 3 | `services/code_engine.py:56-57` | `claude --dangerously-skip-permissions` | new console, same user | full | **none** |
| 4 | `services/agent.py:632-667` `install_package` | `pip install <name>` | child process | full | 180 s |
| 5 | `services/mcp_client.py:122-146` | every MCP server, stdio | child process | `full_env = os.environ.copy()` | none on the process |
| 6 | `routes/compute.py:39-52` | reaches (1) via capability `analysis.run` | — | — | **no `@login_required`** |

Row 1 is the one that matters for this proposal, because it is what a "Friday runs the
agent she wrote" feature would reach for, and it is the worst of the six: model-authored
Python, all secrets, no filesystem confinement, no egress control, no memory or CPU cap.

Row 6 deserves a sentence because it is the sharpest thing the audit found and is not
otherwise in scope: `POST /api/federation/compute/request` has no auth decorator and maps
`analysis.run` straight to `AdapterType.PYTHON_SCRIPT` (`services/compute_provider.py:59`,
`:305-316`), admitted on a `requester_trust_score` **supplied by the requester in the
request body** (`:171`). Mitigation is that the server binds loopback by default
(`server.py:646`) and refuses a network bind without a key (`:649-670`) — so this is a
local-privilege surface, not an internet-facing one. It should be filed in
`KNOWN_ISSUES.md` regardless of what happens to this proposal.

### 3.3 Unattended already exists — and is *more* privileged than interactive

This is the finding that reorders the whole document.

**VERIFIED chain:** `run_workflow_chain` → `_spawn_task(...)` (`services/agent.py:2549-2565`)
→ `_task_worker` → `_generate_agent(..., session_ctx={"authenticated": True,
"is_background_task": True, "task_id": task_id})` (`services/agent.py:2192-2198`). The
scheduler's `agent_prompt` kind does the same (`services/scheduler.py:504-505`).

That single flag has three consequences:

1. **Ring 2 is granted unconditionally.** `_governance_check`: `is_auth = ctx.get("authenticated")
   or ctx.get("is_background_task")` (`services/agent.py:4687`). Ring 2 is the network tier —
   `run_command` and `spawn_task` live there.
2. **The confirmation gate is bypassed — twice over.** `_confirmation_bypassed()` returns
   True for `is_background_task` (`services/agent.py:4833-4837`, docstring: *"Scheduled cron
   / background tasks never wait for an interactive yes"*), and independently the gate is a
   no-op without a `session_id`, which background tasks never have (`:5097`).
3. **No scope is applied.** `_spawn_task` is called with no `scope=` from
   `run_workflow_chain` (`:2559-2565`), from `_retry_chain_step` (`:2736-2742`), and from
   the scheduler (`scheduler.py:504-505`). `scope_check()` returns `(True, "")` for
   unregistered task ids (`services/subagents.py:238-241`).

**Therefore a workflow step is strictly more privileged than the same words typed into
chat.** (**INFERRED** from the three verified facts above; the mechanism is not subtle.)

The contrast that proves the codebase knows better is `services/goals.py:738-765`
(**VERIFIED**), which passes `scope="goal-milestone"` and explains itself at length:

> "Defense in depth (V6 invariant 5 / AUTONOMY_SPEC A3 finding — 'any registered network
> tool can be invoked with zero re-classification against Q3 and zero human approval'): the
> spawned task is dispatched under the `goal-milestone` subagent scope … an auto-approved
> (or mis-classified) milestone's background task simply cannot reach an
> outward/spend/irreversible/shell tool, full stop."

The goals subsystem was hardened against precisely this. Workflows and schedules were not.

**And an approvals queue exists and is not consulted.** `services/approvals.py:95-110`
implements a policy table over `outward` / `irreversible` / `spend` / `external_message`
with 24-hour expiry and a decision endpoint (`routes/goals.py:202-210`). It is wired to
goals only. The scheduler's entire relationship to it is running `approvals.expire_stale`
on an hourly sweep (`scheduler.py:838-846`).

**Live state on Stephen's machine, 2026-08-23** (**VERIFIED**, `~/.friday/schedules.json`,
20 records): 18 enabled. Two are `agent_prompt` — `sch_heartbeat`, hourly, and
`sch_job_intelligence`, daily at 07:30. The heartbeat's prompt instructs *"observe-and-notify
only — take no real-world actions"* (`scheduler.py:891-903`). **That is a sentence in a
prompt, not a constraint in the dispatch path.** Nothing restricts its tools. It has ring 2,
no confirmation, and no scope, once an hour, forever.

Stephen's question — *"What does it mean for Friday to write code that runs later without a
human in the loop?"* — has a prior: Friday already runs *prompts* later without a human in
the loop, at higher privilege than chat. The answer to the question is that the missing
work is the same either way.

### 3.4 Where a Friday-authored agent runs

Given §3.1–§3.3, an agent Friday wrote must not run in the Flask process and must not run
via `PythonScriptAdapter` as it stands. The options on this hardware, priced honestly:

| option | boundary quality | cost | verdict |
|---|---|---|---|
| status quo (`PythonScriptAdapter`) | **none** | zero | **rejected**, per §3.2 row 1 |
| NOOA's AST checks + deny-lists alone | upstream says not a boundary | ~zero | **rejected by upstream's own README** |
| Docker Desktop container | good | a licensing question, a multi-hundred-MB install, a daemon, a Windows-Home story | **rejected on installer grounds (§4.2)** — this is the option that would actually cost 959 MB |
| Windows Sandbox (`WindowsSandbox.exe`) | very good, ephemeral | **Pro/Enterprise only** — excluded on Home, which is what a family install is likely to be; no persistence between runs | **rejected on availability** |
| WSL2 | good (separate kernel, separate FS namespace) | ships with Windows but needs enabling, a reboot, a distro download; GPU passthrough complications; a second Python | **viable, second choice** |
| **separate Windows user account + Job Object + restricted token** | good, and native | real engineering; no new runtime dependency; no new installer bytes | **recommended, §3.4.1** |

#### 3.4.1 The recommended boundary — a second local account

**Design (proposed, not built).** A `friday-agent` local account, created at install time or
on first agent authorship, with:

- **Its own profile**, and explicit `Deny` ACEs for that account's SID on `~\.friday`,
  `~\Projects`, `~\Documents`, and any path in the vault. The default `Users` ACL is not
  enough; the deny must be explicit, because Friday's own `read_file` is unconfined and an
  agent inheriting a normal user account would inherit that reach.
- **One writable path**: a per-agent scratch directory, passed in, owned by the agent
  account, and nothing else. `%TEMP%` is not acceptable — it is shared.
- **A Job Object** with `JOB_OBJECT_LIMIT_PROCESS_MEMORY`, `JOB_OBJECT_LIMIT_JOB_TIME`, and
  `JOB_OBJECT_LIMIT_ACTIVE_PROCESS`, plus `KILL_ON_JOB_CLOSE`. This is the piece that fixes
  §3.2's "wall-clock only" across the board, and it is worth building for `run_command` too.
- **An explicitly constructed environment**, not `os.environ`. The default must be empty
  plus `PATH`, `SYSTEMROOT`, `TEMP` pointed at the scratch dir. **No `ANTHROPIC_API_KEY`,
  no `GEMINI_API_KEY`, no `FRIDAY_PASSWORD`.** This single change is the largest security
  win available anywhere in this document and it applies to §3.2 rows 1, 2, 4 and 5 today.
- **A Windows Firewall rule scoped to the account SID** denying outbound by default. WFP
  supports per-user rules; this is the only mechanism in the table that gives a real egress
  boundary without a proxy.

**Honest cost.** This is several days of Windows-specific work that has to be right, on a
platform whose failure modes are ugly (a mis-set ACL locks Stephen out of his own
directory; `CreateProcessWithLogonW` needs a password or a service account). **INFERRED:**
it is more work than integrating NOOA. That asymmetry is the point — the framework is the
easy part and the containment is the product.

**UNKNOWN:** whether a second account survives Windows Home's account UX without confusing
a non-technical installer. The check that settles it: create one on the target machine and
see what appears on the login screen. `net user /add` with the `ACCOUNTDISABLE` flag and
logon-type restrictions is the likely answer, but it is unverified.

### 3.5 The broker — no keys, no direct egress, no ambient vault

Isolation alone makes an agent useless: a recruiter-email agent needs to read mail and write
a draft. The resolution is that **the agent gets capabilities, not credentials.**

**Proposed shape.** The sandboxed agent has no network access and no keys. It has one thing:
a handle to a local socket, authenticated by a per-agent, per-run capability token. Every
privileged action is a request to Friday, and Friday performs it — through the *existing*
funnel:

```
agent process (isolated, no keys, no egress)
    │  capability token + typed request
    ▼
Friday broker  ──► scope_check (services/subagents.py)
                ──► _governance_check / rings (services/agent.py:4652)
                ──► vault_access.check_action (privacy/vault_access.py:274)
                ──► egress_gate.seal_outbound (services/egress_gate.py)
                ──► approvals queue (services/approvals.py) when policy says so
```

Why this shape and not a thinner one:

- **It reuses the safety apparatus instead of routing around it.** §2.2's central point was
  that code-as-action bypasses the tool-call interceptor. The broker puts the interceptor
  back, at a process boundary, where it is enforceable rather than advisory.
- **It fixes the vault's missing per-caller scope as a side effect.** Today
  `vault_access.can_access(provider)` is a string membership test against
  `LOCAL_PROVIDERS = {"ollama", "local"}` (**VERIFIED**, `privacy/vault_access.py:29-39`,
  `:112-114`) — the *only* axis is provider identity. There is no principal, no capability,
  no path scoping; and the AES key is a process-global singleton
  (`services/agent.py:4497-4500`), so any in-process code that can import `_get_vault_key`
  has the raw key. An out-of-process agent asking through a broker is the first caller in
  this system that *has* an identity to scope on.
- **It fixes egress direction.** `egress_gate.seal_outbound` controls **what leaves, not
  where to** (**VERIFIED**, `services/egress_gate.py:1-21, 42-82`). There is no outbound
  domain allowlist anywhere in `src/` and zero `proxy` hits (`docs/INSTALLATION.md:315` is
  the only mention, and it is advice about HuggingFace downloads). An agent with no network
  and a broker has a destination policy for the first time, because the broker is the only
  route out.

**Cost, stated plainly:** the broker is a new IPC surface, and a new IPC surface on
loopback in a system where `login_required` auto-authenticates any loopback caller
(**VERIFIED**, `core/__init__.py:475-495`) is a liability unless the token is per-run,
short-lived, and *not* a cookie. **FA7** below makes that a rule.

### 3.6 Declared capabilities — bounded, not inherited

An agent's capabilities must be **declared in a manifest that the runtime enforces**, never
inferred from a docstring and never inherited from the session that authored it.

Proposed manifest, one file per agent, alongside the source:

```toml
# ~/.friday/agents/recruiter-triage/agent.toml
name          = "recruiter-triage"
authored_by   = "friday"
authored_on   = "2026-08-23T14:02:11Z"
source_sha256 = "…"            # binds this manifest to this exact source

[seat]
role          = "sidekick"      # residency_policy role; NOT interactive_brain
max_ctx       = 32768
cost_ceiling_musd = 500         # hard stop, enforced by the broker

[capabilities]
tools         = ["read_email", "draft_email", "read_file"]   # allow-list
max_ring      = 2
max_steps     = 40
time_budget_s = 900

[vault]
access        = "none"          # none | tier1 | tier1+2 ; never tier3 unattended

[egress]
posture       = "broker-only"   # no direct network; destinations below
destinations  = ["gmail.googleapis.com"]

[filesystem]
write         = ["~/.friday/agents/recruiter-triage/scratch"]
read          = []              # explicit; empty means nothing beyond scratch

[schedule]
trigger       = "manual"        # manual until promoted (§3.8)
```

Four rules about this object:

1. **`[capabilities].tools` maps onto `services/subagents.py`'s existing scope model** —
   `allowed_tools`, `denied_tools`, `max_ring`, `max_steps`, `time_budget_s` are the fields
   that are already there (**VERIFIED**, `subagents.py:82-116`), and `save_custom_scope()`
   already persists user-defined ones. This is not a new mechanism; it is a new caller for
   a built one.
2. **Nothing is inherited.** The default for every field is the empty/most-restrictive
   value. An agent authored during a session where Stephen happened to have Computer
   Control enabled does not get ring 3.
3. **`source_sha256` binds manifest to source.** If the source changes, the manifest is
   void and the agent reverts to unapproved (§3.7). This is the rule that stops an approved
   agent being edited into a different agent.
4. **The docstring is the prompt; the manifest is the contract.** NOOA's elegance is that
   the docstring *is* the prompt — but a prompt is not a permission. Anything security-
   relevant lives in the manifest, where it is data a gate can read, not prose a model
   wrote.

### 3.7 Review — a file, in a place, with a diff

Stephen's requirement, restated: *"An agent Friday wrote should land as a file Stephen can
read, in a place he can see, with a diff."*

**Proposed:**

- **Location:** `~/.friday/agents/<slug>/` containing `agent.py`, `agent.toml`, `README.md`
  (Friday's own plain-English account of what it does and why), and `scratch/`.
  Not in `~/Projects/friday-desktop` — that tree has three active sessions and 254
  uncommitted files, and an agent landing in it would be noise in someone's `git status`.
- **Version control:** `~/.friday/agents/` is **its own git repository**, initialised on
  first use. Friday commits every authored or edited agent on a branch named
  `friday/<slug>/<timestamp>`, never on `main`. Promotion to `main` is Stephen's merge.
  This gives `git diff` for free, gives an audit trail that survives a rewrite, and means
  the review UI is a diff view over a real repo rather than a bespoke serialisation.
- **The diff is mandatory and it is of the source, not of a summary.** `SEATS_AND_TRANSPARENCY_SPEC.md`
  B2 ("no silent changes") and `tool-index.md` §7.0b (the disclosure is an annotation on the
  thing it qualifies, not a status indicator elsewhere) both apply: the diff renders in the
  transcript beside the reply that produced it, with the approve control inline.
- **Three states, and they are visible:** `draft` (written, never run), `probationary`
  (approved for supervised runs only), `promoted` (approved for unattended runs). State
  lives in the manifest and is enforced by the broker, not by the UI.
- **Any edit demotes.** Source change → `source_sha256` mismatch → back to `draft`. Friday
  may propose the re-approval; she may not grant it.

**What Friday must write alongside the code**, because `KNOWN_ISSUES.md` §1 says a
component that cannot verify its own success must say so:

- a plain-English `README.md` — what it does, what it touches, what it will never do;
- a **test** she can run in the sandbox, whose passing is a precondition of leaving `draft`;
- an explicit "what I am unsure about" section. Not optional. The rule is not "Friday writes
  correct agents"; the rule is "Friday declares what she has not checked."

### 3.8 Does it run once with a human present before it runs unattended?

**Yes. Not once — a bounded number of supervised runs with an explicit promotion, and the
promotion is Stephen's, not Friday's.** The reasoning, since Stephen asked for it either
way:

**The case for going straight to unattended.** It is the honest counter-argument and it is
not weak. (a) Friday's `sch_heartbeat` already runs hourly, unscoped and unconfirmed, so
supervision here is a standard applied to the new thing and not the existing one — which is
inconsistency, not safety. (b) A supervised run proves the agent works *once*, on the state
of the world at that moment; the failure modes that matter for unattended code are the ones
that appear on the 40th run when an input is malformed. Supervision buys less assurance than
it looks like it does. (c) Every gate is a place a family install stalls, and §5's
product perspective is right that this project's binding constraint is time.

**The case for supervision, which wins.**

1. **The precedent is Stephen's own and it is four days old.** "Ember and the Big Dark" was
   the *first fully autonomous run*, and it came after two supervised productions whose
   corrections were written into `lessons.md` — including one, the `setpts` fix, that only
   surfaced because a human was watching *during* production (**VERIFIED**, §2.1). That is
   an empirical result from this exact system: supervision caught a defect that
   self-verification did not.
2. **The dominant failure mode is invisible success.** `KNOWN_ISSUES.md` §1 documents an API
   surface that failed to register for seven weeks and ~70 restarts while `/api/health`
   returned 200; a voice-wiki task that 404'd and reported "Task complete."; a chain that
   "completed" with zero output because the provider died and the error became the result
   text (`lessons.md`, 2026-08-20). Unattended model-authored code is a machine for
   producing exactly this, at 3am, with no transcript anyone reads.
3. **A supervised run is the only place the *manifest* gets tested.** The point of the first
   run is not "does the agent work" — the test in §3.7 answers that. It is "does the declared
   capability set actually suffice, and did it try to reach for something it did not
   declare?" A broker denial during a supervised run is a design signal. The same denial at
   3am is a silent failure.
4. **Answer (b) directly:** supervision is not claimed to prove correctness. It is claimed
   to establish a baseline against which the unattended runs are compared, which is the
   thing `lessons.md` does for films and nothing does for programs.

**The proposed promotion rule, concretely:** three consecutive supervised runs with no
broker denial, no unhandled exception, and a Stephen-visible result he did not have to
correct. Then promotion is offered — offered, in the transcript, with the run history
attached. Never automatic. And **promotion is per-agent, not per-feature**: approving the
recruiter agent says nothing about the next one.

Additionally, and this is the cheapest safety property in the document: **an unattended
agent's first action on every run is to write a run record, and its last is to close it.**
An open record older than its time budget is a hung agent, and the watchdog kills the Job
Object. `lessons.md`'s "files-on-disk is the only real completion signal" generalises to
"a completion record the agent did not write is not a completion."

### 3.9 Guard rules

Any implementation of this proposal, in any form, must satisfy all of these. They are
written to be checkable.

| | rule |
|---|---|
| **FA1** | A Friday-authored agent never executes in the Friday server process. Process boundary is mandatory; in-process AST validation is not a boundary and may not be presented as one. |
| **FA2** | The agent process receives an **explicitly constructed** environment. Never `os.environ`, never `{**os.environ}`. No provider key, no `FRIDAY_PASSWORD`, no `FRIDAY_SECRET_KEY` may appear in it. |
| **FA3** | The agent has exactly one writable path — its own scratch directory — and no read access to `~/.friday`, `~/Projects`, or `~/Documents`, enforced by ACL, not by policy string. |
| **FA4** | The agent has **no direct network egress**. Every outbound byte goes through the broker and therefore through `egress_gate.seal_outbound`. |
| **FA5** | Capabilities are **declared and enforced**, defaulting to empty, never inherited from the authoring session. The manifest is the contract; the docstring is only the prompt. |
| **FA6** | Every agent runs inside a Job Object with memory, CPU-time and wall-clock limits. Wall-clock alone is not a resource limit. |
| **FA7** | The broker token is per-agent, per-run, short-lived, and is **not** a session cookie. Loopback auto-authentication (`core/__init__.py:475-495`) must not authenticate it. |
| **FA8** | Vault tier 3 is never reachable unattended, by any agent, under any manifest. Tier 1–2 requires an explicit manifest declaration and is logged per access. |
| **FA9** | An agent lands as source in a git-tracked directory, on a branch, with a diff shown in the transcript beside the reply that produced it. No agent runs from a source Stephen has not had the opportunity to read. |
| **FA10** | Source change voids approval. `source_sha256` mismatch demotes to `draft` unconditionally. Friday may propose re-approval; she may not grant it. |
| **FA11** | Unattended promotion requires supervised runs and is granted by Stephen, per agent, never by default and never in bulk. |
| **FA12** | Version pins are exact. An upgrade of `nooa` or any transitive dependency is a change with a test run behind it, not `pip install -U`. (Carried from `switchyard-position.md` SW8.) |
| **FA13** | Every unattended run writes an open record before its first action and closes it at the end. An unclosed record past its budget is a failure, not a pending success. |

---

## 4. What it costs

### 4.1 Does the idea survive without NOOA?

**Yes, and most of it does.** Decompose what NOOA offers and mark what Friday already has:

| NOOA capability | Friday today | verdict without NOOA |
|---|---|---|
| agent state as typed object fields | — | plain Python classes; `dataclass`; free |
| methods as capabilities | `build_film.py` functions | already have it |
| docstrings as prompts | skill front matter, `SKILL.md` | already have it, less elegantly |
| type annotations as contracts | — | `pydantic` or plain annotations; ~free |
| **`...` bodies → LLM-driven at runtime** | — | **this is the genuinely novel part** |
| **pass-by-reference over live objects** | — | **also genuinely novel** |
| code-as-action REPL | `run_command python …` | crude version already in use |
| tracing of every LLM call and code execution | `~/.friday/vault/decision-bom.jsonl`, `activity_ledger.jsonl`, `trajectories.jsonl`, cost meter | **Friday's is arguably better and is already integrated with the vault and cost model** |
| typed I/O with auto-retry | chain-level retries (`agent.py:2715-2746`) | weaker, but present |
| MCP integration | `services/mcp_client.py`, live | already have it |
| memory subsystem (`nooa-memory`) | `~/.friday/memory`, knowledge graph, wiki, `learning.db` | **do not duplicate** |

**INFERRED:** a "Friday-authored agent" feature built on plain Python — a class, a manifest,
a broker, a sandbox — delivers most of the product value with zero new dependency and zero
installer bytes. What it forgoes is `...` bodies and live-object pass-by-reference. Those
are the two things NOOA is first to combine, and they are exactly the two things that demand
the most model capability (§4.3).

**Therefore the dependency question is not "is NOOA worth it" but "is it worth it *yet*",
and the answer turns on the seat, not the framework.**

The cost of depending on it, priced: a `v0.0.x` API across four distributions at 169
commits with 16 open PRs; a transitive tree that includes `litellm`, which this repo
deliberately removed four days ago (§4.2); a REPL that may need stdlib modules the
embeddable interpreter lacks; and an upstream that explicitly disclaims production
readiness. Against which: Apache 2.0, so anything worth copying can be copied.

### 4.2 The installer arithmetic — correcting 959 MB

**The figure 959 MB does not appear anywhere in this repository.** (**VERIFIED** by
exhaustive search across `.md`, `.ps1`, `.txt`, `.toml`, `.json`, `.spec`, plus
`git log -S"959 MB"` and `git log --grep 959`; the only hits are a PID in
`docs/audits/server-death-forensics.md:140`, a line range in `docs/audits/voice-mode.md:101`,
and a unix timestamp in a fixture.) Registered **REPORTED** and unsourced. The measured
figures that *do* exist:

| artefact | size | source |
|---|---:|---|
| `AgentFriday-Setup-5.5.0.zip` | **20.9 MiB** (21,935,914 bytes) | on disk; `packaging/windows/README.md:26` |
| core tier, site-packages on target | ~500 MB | `packaging/windows/README.md:169-178` |
| core + recommended | **~800 MB** | ibid.; `packaging/windows/install.ps1:43-48` |
| + memory tier | ~2.3 GB (torch alone 490 MB) | ibid. |
| `dist/AgentFriday.exe` (PyInstaller, not what ships) | 145 MiB | on disk |

**INFERRED:** 959 MB is most likely a remembered measurement of the *installed* footprint —
core + recommended (~800 MB) plus the embedded CPython and payload lands near 900 MB–1 GB —
rather than a download size. Either way, the number Stephen is defending is the **installed
site-packages footprint**, and that is the number NOOA would attack.

**What NOOA adds, specifically.** Commit `c82615d`, 2026-08-21 — two days ago — is titled
*"fix(packaging/windows): headroom-ai[all] was smuggling 2.3GB into the recommended tier."*
`packaging/windows/requirements/recommended.txt` names what `[all]` was dragging in
(**VERIFIED**):

> "torch (490 MB on disk), transformers, sentence-transformers, datasets, scikit-learn,
> scipy, pandas, pyarrow, opencv-python, rapidocr-onnxruntime, **litellm**, openai, fastapi,
> uvicorn, starlette, mcp, magika, trafilatura, and the whole tree-sitter-language-pack —
> about 2.3 GB, for an optional context-compression nicety."

**NOOA's model layer is LiteLLM** (`nooa.unifiedllm.registry.get_llm_client` fronts
LiteLLM-supported models, **VERIFIED** from the README). Adding `nooa` to `core.txt` or
`recommended.txt` would re-pull a chunk of what commit `c82615d` just removed, and would do
it inside a tier a user consents to as "voice and PDF reading." That is not a size argument;
it is the *same* argument the maintainer already made and won two days ago, and reversing it
by accident would be a poor outcome.

**Two hard constraints on any install path** (**VERIFIED**,
`packaging/windows/requirements/wheelhouse.txt`): the embeddable interpreter installs
**wheels only** — PEP 517 build isolation "does not work in an embeddable interpreter at
all" — so any sdist-only package in NOOA's tree needs a wheelhouse entry built on the build
machine; and the embeddable distribution lacks stdlib modules that ordinary CPython has
(the `mouseinfo`/tkinter precedent is recorded there). **A Jupyter-style REPL is a plausible
repeat of that failure mode.** Registered **UNKNOWN**; the check is `pip download nooa` into
the embeddable interpreter on the build machine and read what it pulls.

**The right home, if it ever lands:** the `voice_installer.py` pattern — lazy, consented,
disk-preflighted, fixed allow-listed targets, background job with a streamed log
(`services/voice_installer.py:8, 30-58`, whose own docstring says *"Fixed allowlisted
targets only — this is NOT an arbitrary-package installer"*), or a `pyproject` extra
excluded from `[all]` on the `voice-local-gpu` precedent (`pyproject.toml:47-52`). Not
`core.txt`. Not `recommended.txt`. **The 20.9 MiB artefact and the ~800 MB core+recommended
footprint are not negotiable for a family install.**

### 4.3 The seat question — the real blocker

NOOA's benchmark evidence is SWE-bench Verified, Terminal-Bench 2.0, ARC-AGI-3. Friday's
default reasoning, orchestrator and `sidekick_fast` seats are all `gemma4:12b` on
`llama-cpp-local`, capped at 32,768 (**VERIFIED**, `~/.friday/settings.json`
`capability_routing`; `residency_arbiter.py:609`).

Two facts in tension, both verified:

- **In favour:** `KNOWN_ISSUES.md` (2026-08-22) corrects an earlier, wronger version of
  itself and establishes that *"a local model WITH native tool calling uses tools fully
  offline, with no API key. `qwen3:8b` and `gemma4:12b` do."* The chat path passes the tool
  registry to local providers and executes what comes back
  (`agent.py:193` → `services/model_router.py:410` → `_oai_agentic_loop` at `agent.py:6225`,
  up to 50 iterations, under the same registry, vault gate and governance rings as cloud).
  Local Friday *acts*.
- **Against:** the same file's §1 records prose-narrated fake tool calls
  (`services/tool_integrity.py:46`), a 1-in-3 refusal rate on one measured suite
  (`docs/audits/model-suite-determination.md:65-93`), and a non-reproducible gate scoring
  10/10, 8/10, 8/10, 9/10 across runs (`docs/audits/residency-live-2026-08-15.md:64-77`).

**INFERRED:** a seat that narrates fake tool calls one turn in ten is a seat that will write
Python that looks right and does nothing, and the code-as-action pattern converts that from
a visible failure into a silent one. Writing an *agent* is harder than calling a tool, and
the agent gets written once and run forever — so a 10% authoring error rate is not a 10%
run failure rate, it is a permanent defect with a 10% chance per agent.

Which points at the routing answer: **authoring is a cloud-seat job; execution is whatever
the manifest says.** `researcher` is already bound to `claude-opus-5`
(`~/.friday/settings.json`). Friday should write agents with her strongest available seat,
under the egress gate, and run them on whatever the agent's own manifest declares. That is
consistent with `symphony-of-intelligence.md`'s thesis and costs nothing new.

**UNKNOWN, and it is the measurement that decides the whole proposal:** can `gemma4:12b` at
32,768 *drive* a NOOA agentic loop — write valid Python against a live object, in a REPL,
and recover from a traceback? Nothing in the paper, the README, or this repository speaks to
it. The check that settles it is §6.2's pilot, and it is cheap.

---

## 5. STORM — the disagreement, argued at strength

Five perspectives. Each is stated as its strongest self, not as a foil.

### 5.1 The security engineer

"Read §3.3 again and then tell me this is a feature request. You have eighteen enabled
schedules, two of which spawn a full-registry agent with ring 2, no confirmation and no
scope, and one of them says *'take no real-world actions'* in a **prompt**. You have an
unauthenticated HTTP endpoint that runs attacker-supplied Python with every key in the
environment. You have a module called `sandbox` whose docstring claims it confines reads and
whose code confines writes. The vault key is a process-global singleton and the vault is
plaintext at rest by default.

I am not against Friday writing agents. I am against calling this a *new* risk. The correct
sequence is: scope the unattended paths, fix `python_script_adapter`'s environment
inheritance, put `@login_required` on `routes/compute.py`, and *then* have the conversation
about NOOA. Every one of those is smaller than the integration. Do them in that order and
the NOOA question answers itself, because you will have built the containment and the
framework becomes a library choice rather than a security decision."

### 5.2 The product owner, and this is the case against

"There is a person installing this software this week who is not a developer. The
known-issues file is 32 KB and it is that long because this project is honest, which I
respect and which is also the point: you have a *long list of things that are broken now*
and you are proposing a subsystem whose failure mode is a program you wrote, that you
approved, running at 3am, doing something you did not intend, on a machine with your
daughter's files on it.

Look at the last fifteen commits. Every single one is packaging and install:
*'run prewarm even when no models need downloading'*, *'the build was shipping an empty
payload and reporting success'*, *'headroom-ai[all] was smuggling 2.3GB'*. That is a project
in the last mile of shipping to a real person. The correct answer to a fascinating new
framework in week one of a family install is **not now**, and 'not now' is not a euphemism
for 'no' — it is a schedule.

And be specific about the opportunity cost. §3.4.1 is several days of Windows security
engineering. §3.5 is a new IPC surface. §3.7 is a git repository, a review UI and a state
machine. That is two weeks minimum, on a codebase where a career-pipeline API failed to
register for seven weeks and nobody noticed. Two weeks of *that* attention is two weeks not
spent on the thing where the person is.

The strongest version of my objection is this: the document you are reading is itself the
deliverable. It found an unauthenticated code-execution endpoint and an unscoped hourly
agent. Ship those fixes. File the rest. Come back when there isn't someone waiting on an
installer."

### 5.3 The framework sceptic

"Count what NOOA actually gives you that you do not have. §4.1's table is nine rows of
'already have it' and two rows of 'genuinely novel'. You would take a `v0.0.x` dependency,
across four distributions, at 169 commits, with 16 open PRs and no verified Windows CI, to
get `...` bodies and pass-by-reference.

And you would take it into an embeddable interpreter that installs wheels only and lacks
stdlib modules — where the last package that assumed ordinary CPython needed a wheelhouse
entry hand-built on the build machine. A Jupyter-style REPL in that environment is not a
dependency, it is a research project.

Meanwhile the two novel features are the two that need a strong model, and your default seat
is a 12B at 32k that this codebase has caught narrating fake tool calls. You would be paying
the dependency cost for capabilities your hardware cannot exercise.

Apache 2.0 is the answer to my own objection, by the way — and it is a good one. Read their
loop engineering, take their trajectory prompts, copy their design. `switchyard-position.md`
already decided this exact question for the sibling project and it decided correctly: adopt
the ideas, not the dependency."

### 5.4 The architect

"I want to defend the proposal on grounds no one has raised, because the strongest argument
for it is not capability, it is **legibility**.

Chains are prompts. You cannot test a prompt, you cannot diff a prompt meaningfully, and you
cannot reason about what a prompt will do on input it has not seen. §2.2's table shows the
chain's failure floor is a step that completes having done nothing — which is
`KNOWN_ISSUES.md` §1's dominant failure mode, in the orchestration layer, structurally.
A class you can `pytest`. A class you can diff. A class fails with a traceback that names a
line.

`KNOWN_ISSUES.md`'s rule is *'nothing in Friday may claim success it has not verified.'*
Prompt chains structurally cannot obey that rule — their only success signal is a model
saying so. Programs can. The agent-as-class model is not primarily a capability upgrade; it
is the first mechanism in this system that makes the project's own central rule enforceable
in the orchestration layer.

I concede the sequencing entirely. Build the containment first. But do not file this as a
nice-to-have, because it is the answer to the failure mode the project's own bug list says
is its dominant one."

### 5.5 The synthesis

The security engineer and the product owner agree on the *sequence* while disagreeing about
the destination, and that agreement is the decision: **the containment work comes first, it
is small, and it is valuable independent of NOOA.** The framework sceptic is right that the
dependency is not worth taking today and right about why. The architect is right that the
idea is not a toy and should not be filed as one.

Nobody in the room argues for installing `nooa` this week. Everybody in the room argues for
scoping the unattended paths. That is the recommendation.

---

## 6. Recommendation

**Do not adopt NOOA now. Do the containment work it forced you to find, because it is
smaller than the integration, it is valuable on its own, and every version of this proposal
needs it.**

### 6.1 The smallest useful first step

**Apply the scope mechanism that already exists to the unattended paths that already exist.**

Concretely, and this is small enough to be one commit each:

1. `services/agent.py:2559-2565` and `:2736-2742` — pass a scope to `_spawn_task` from
   `run_workflow_chain` and `_retry_chain_step`. A new `workflow-step` builtin scope, modelled
   on `recipe-runner` (`subagents.py:109-116`), which already denies `run_command`,
   `install_package` and `spawn_task`.
2. `services/scheduler.py:504-505` — pass a scope for `agent_prompt` jobs. `sch_heartbeat`
   says *"observe-and-notify only"*; give it `readonly`, which is a scope that means it
   (`subagents.py:83-89`, `max_ring: 0`).
3. Where a chain or schedule genuinely needs more, let the *definition* declare a scope
   name — one new optional field in the chain JSON and the schedule record — resolved
   through `load_scope()`, defaulting to the restrictive builtin. `save_custom_scope()` is
   already there for the cases the builtins do not cover.

**Why this is the right first step even if NOOA is never adopted:** it closes the gap
`services/goals.py:738-765` already documents as a defence-in-depth requirement; it makes
`sch_heartbeat`'s prompt-level promise into an enforced one; it costs no installer bytes, no
new dependency and no new IPC surface; and it is the prerequisite for every later phase.
Nothing below can start until it lands.

**Three defects to file in `KNOWN_ISSUES.md` at the same time**, all found by this audit and
none contingent on the proposal:

- `routes/compute.py:39-52` — unauthenticated endpoint reaching arbitrary Python execution
  via `capability: "analysis.run"`, admitted on a requester-supplied trust score
  (`services/compute_provider.py:171`). Loopback-only by default; still wrong.
- `services/worker_adapters/python_script_adapter.py:67` — `env={**os.environ, ...}` hands
  every provider key and the vault passphrase to model-authored code. **FA2** is the fix and
  it is a one-line change plus a decision about what the allow-list contains.
- `core/__init__.py:1144` vs `:1160` — the `confine` mode docstring claims reads are
  path-confined; `_SANDBOX_PATH_TOOLS` contains only `write_file`. A comparison that
  discards the meaning, in the module named `sandbox`.

### 6.2 Phase 2 — the pilot that costs a day and answers §4.3

**Only after 6.1.** Install `nooa` pinned, in a scratch venv outside the installer, on the
build machine — **not** in the shipped tiers, **not** in the embeddable interpreter, and not
touching `~/.friday`. Write one trivial agent by hand (not by Friday) with one `...` body.
Run it three ways:

- against `claude-opus-5` (the `researcher` seat's model) — establishes the ceiling;
- against `gemma4:12b` at 32,768 via the local endpoint at `127.0.0.1:8090/v1` — **this is
  the measurement that decides everything**;
- with the REPL exercised, to see whether the embeddable-interpreter constraints in §4.2
  bite.

Also read `.github/workflows/` for a `windows-latest` job (§1.2's UNKNOWN), and
`pip download nooa` to see the real transitive tree against `recommended.txt`'s exclusion
list.

**Cost:** a day. **Output:** a paragraph appended to this document, and a go/no-go on the
seat question that is currently the largest UNKNOWN in it.

### 6.3 Phase 3 — the containment, if and only if Phase 2 says yes

§3.4.1 (second account, ACLs, Job Object, constructed environment), §3.5 (the broker),
§3.6 (the manifest), §3.7 (the git-tracked review), §3.8 (supervised promotion), all
subject to FA1–FA13. **Estimate: two weeks, honestly, and most of it is Windows security
engineering rather than agent work.** Do not start it in a week when someone is installing
the product.

Note that Phase 3 is worth building **even with plain Python classes and no NOOA at all**
(§4.1). The framework decision can be deferred to the very end, which is the correct place
for the decision with the most churn risk attached to it.

### 6.4 Triggers for revisiting

Testable, so nobody has to re-litigate this from memory:

- **T1** — NOOA reaches a `v0.1.x` or later with a Windows CI job. Re-read §1.2 and §4.2.
- **T2** — the live tool registry exceeds ~200 tools, or `fit_tools_to_seat` starts dropping
  connectors on a *typical* turn rather than a Higgsfield-loaded one. Then §2.3's
  code-as-action case becomes urgent rather than theoretical.
- **T3** — Friday's default local seat gains headroom (new card, or a model that fits 65,536
  under the display reserve). §4.3's blocker weakens.
- **T4** — a second person is using Friday daily and the "who reviews the agent" question
  acquires a second answer. §3.7's single-approver design needs revisiting before that, not
  after.

---

## 7. Open questions

Each answerable in a sentence, and each genuinely open.

**Q1 — the boundary.** §3.4 recommends a second Windows account over WSL2. WSL2 is a better
boundary and a worse install experience. Which way do you want that traded, given who is
installing this week?

**Q2 — the code-as-action trigger.** §2.3 says code-as-action does not supersede
`tool-index.md`. What measurement would change that — registry size, a measured drop rate,
or a specific connector (Higgsfield's 86) getting a hand-written Python facade instead of 86
schemas?

**Q3 — who may author.** §4.3 argues authoring is a cloud-seat job. Do you want agent
authoring to be *impossible* on a local seat, or possible-with-a-warning? The first is a
gate; the second is `tool-index.md` §7.0's disclose-don't-block principle, and they
genuinely conflict here.

**Q4 — the review surface.** §3.7 puts agents in their own git repo under `~/.friday/agents/`.
Is a git repo the right object for someone who is not a developer to review, or does the
diff need to render as something other than a diff for the second user?

**Q5 — the heartbeat.** `sch_heartbeat` runs hourly with ring 2 and no scope and a prompt
that asks it not to act. §6.1 proposes giving it `readonly`. Is there anything it currently
does that `readonly` would break — and if there is, is that thing something you knew it was
doing?

---

## 8. Sources

**NOOA (read 2026-08-23, at `main`).**

- [README](https://github.com/NVIDIA-NeMo/labs-OO-Agents) — the programming model, the
  installation tiers, the LiteLLM model layer, and §1.4's safety note quoted in full.
  Repository signals (169 commits, 1.0k stars, 146 forks, 15 issues, 16 PRs, `v0.0.7`) read
  from the same page.
- [arXiv:2607.20709](https://arxiv.org/abs/2607.20709), *NVIDIA-labs OO Agents: Native
  Python Object-Oriented Agents*, Furgale et al., submitted 22 Jul 2026 — abstract read via
  [the HuggingFace paper page](https://huggingface.co/papers/2607.20709); the arXiv abstract
  page itself timed out twice from this environment and the PDF was not read. The six
  model-facing ideas and the SWE-bench Verified / Terminal-Bench 2.0 / ARC-AGI-3 evaluation
  set are from the abstract. **Registered accordingly: the paper's containment discussion,
  if it has one, was not read.**
- [SECURITY.md](https://github.com/NVIDIA-NeMo/labs-OO-Agents/blob/main/SECURITY.md) — NVIDIA
  PSIRT process only; no threat model, no containment guidance beyond the README's.
- LICENSE / README licence section — Apache 2.0, §1.3.

**Friday (read 2026-08-23, working tree at `fbb52fb`+, `higgsfield-integration`).**

- `core/__init__.py:475-495` (loopback auto-auth), `:745-795` (`.bat` env bootstrap and
  `_FORCE_OVERRIDE`), `:1140-1202` (the sandbox policy gate, `_SANDBOX_PATH_TOOLS`, the
  docstring contradiction, the substring blocklist, `_RUN_COMMAND_ALLOW`).
- `services/agent.py` — `:538-556` unconfined `read_file`; `:1242-1261` `run_command`;
  `:2192-2198` the background `session_ctx`; `:2461-2846` the whole workflow-chain
  subsystem; `:4485-4649` vault key and migration; `:4652-4709` the ring model; `:4687`
  the `is_background_task` ring-2 grant; `:4833-4837` `_confirmation_bypassed`; `:5097` the
  session-id no-op; `:5141-5150` the sandbox pre-hook.
- `services/worker_adapters/python_script_adapter.py:48-68` — arbitrary Python,
  `env={**os.environ}`, the sharpest execution surface in the tree.
- `services/subagents.py:82-160` `BUILTIN_SCOPES` and `save_custom_scope`; `:230-250`
  `scope_check` and the unscoped-tasks-always-pass rule. **The mechanism §6.1 asks you to
  use.**
- `services/goals.py:738-765` — the correct pattern, applied, with its reasoning written
  out. **The proof the codebase already knows.**
- `services/scheduler.py:41-46, 126-153, 227-265, 485-537, 539-644, 706-855, 891-925,
  996-1035, 1131-1161` — the scheduler, the record shape, the two live `agent_prompt` jobs.
- `services/approvals.py:95-110` and `routes/goals.py:202-210` — the approvals queue that
  the unattended paths do not consult.
- `services/code_engine.py:44-68` and `routes/code.py:62-89` — `--dangerously-skip-permissions`.
- `routes/compute.py:39-52` and `services/compute_provider.py:59, 147-157, 171, 305-316` —
  the unauthenticated path to arbitrary Python.
- `privacy/vault_access.py:29-39, 112-114, 274-334` — access by provider identity, no
  per-caller scope; `privacy/vault_crypto.py:3-5, 41-70` — plaintext-at-rest by default.
- `services/egress_gate.py:1-21, 42-82, 85` — what leaves, never where to; `THREAT_MODEL.md:16,
  32-33, 75-79` — the gate as final barrier.
- `services/residency_arbiter.py:605-620` — `MAX_SEAT_NUM_CTX = 32768` and the display
  reserve; `services/residency_policy.py:44-60, 270-315` — roles and context ladder;
  `docs/contracts/roles-and-model-identity.md:181-193` — the 262,144 architectural window and
  the 47,309-token turn.
- `KNOWN_ISSUES.md` §1 (the dominant failure mode and its seven examples) and the
  `function_manager` / "local Friday cannot act" entry, corrected 2026-08-22 — the local
  tool-calling chain at `agent.py:193` → `services/model_router.py:410` → `agent.py:6225`.
- `packaging/windows/README.md:26, 169-178`; `packaging/windows/install.ps1:43-48`;
  `packaging/windows/requirements/recommended.txt` and `wheelhouse.txt`; commit `c82615d`;
  `packaging/windows/dist/BUILD-REPORT.md:27-56`; `pyproject.toml:18-99`;
  `services/voice_installer.py:8, 30-58`; `AgentFriday.spec:66-70` — §4.2 in full.
- `~/.friday/settings.json` (`capability_routing`), `~/.friday/seat_state.json`,
  `~/.friday/schedules.json` (20 records, 18 enabled) — live state, this machine.
- `~/.friday/skills/storybook-film/SKILL.md` and `build_film.py`;
  `~/.friday/creations/film-lessons/lessons.md` (44 lines, three dated entries) — §2.1 and
  §3.8's precedent.

**Inherited design documents.**

- [`switchyard-position.md`](switchyard-position.md) — the template for this evaluation, and
  SW7/SW8 carried forward as FA4/FA12. Its §0 conclusion — *"adopt the ideas, not the
  dependency"* — is reached here for the same reasons by a different route.
- [`tool-index.md`](tool-index.md) §1.2–1.3, §7.0, §7.0b, §8 — the 64-tool/10,755-token
  measurement, the 46,288-token failure, the disclose-don't-block decision, and the
  prose-narrated-fake-tool-call evidence. **Not amended here.**
- [`context-assembly.md`](context-assembly.md) §1.2, §3.1 — the 32,768 cap and deferred tool
  loading. **Not amended here; §2.3 defers to it.**
- `docs/SEATS_AND_TRANSPARENCY_SPEC.md` A7, B2 — the completion-receipt law and no-silent-changes,
  which FA9 and §3.7 apply to agent authorship.
- `docs/AUTONOMY_SPEC.md` A3 — quoted second-hand via `services/goals.py:738-765`.

**Reported, not verified.** Stephen's statement that a non-technical user is installing this
week (§5.2's premise; no reference to it exists in the repository), and the 959 MB installer
figure (§4.2, searched for and not found).
