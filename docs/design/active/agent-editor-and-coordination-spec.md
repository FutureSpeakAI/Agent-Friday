# The agent editor, the workspace, and the coordination layer — contracts first, org chart second

> **Status:** active
> **Last verified:** 2026-09-08
> **Implementation:** none
> **Supersedes / superseded by:** extends [`friday-crew-spec.md`](friday-crew-spec.md) (the desk) and [`friday-builds-agents.md`](friday-builds-agents.md) (the agent-as-class); supersedes neither
> **Written:** 2026-09-08

## Implementation notes

Nothing here is built. This document adds four things to the two existing agent specs and takes nothing away: (1) a single **agent object** that unifies the crew "desk" and the builds-agents "agent-as-class" under one manifest schema; (2) an **irreversibility boundary, a budget and a termination contract** as declared fields rather than conventions in a memory file; (3) a **resource arbiter of record** that treats scarcity, not messaging, as the coordination problem; (4) **orchestration as a per-project binding**, so the same agent can lead one project and take direction in another.

Read at 2026-09-08 against the working tree: `docs/design/active/friday-crew-spec.md`, `docs/design/active/friday-builds-agents.md`, `docs/design/active/autonomy-execution-spec.md`, `docs/design/active/v6-wholeness-spec.md`, `docs/design/implemented/seats-and-transparency-spec.md`, `docs/design/implemented/conversations-and-concurrency.md`, `docs/reference/roles-and-model-identity.md`, `docs/security/threat-model.md`, `docs/design/historical/open-source-adoption-review.md`. Code claims are inherited from those documents' citations and were **not** independently re-verified line by line in this pass; treat a `file.py:LINE` reference below as naming the right file and claim, not necessarily the right line.

**One correction to the working brief, stated up front because the rest depends on it.** The brief instructed this spec to reuse "Friday's existing Household Identity spec" and its vocabulary of principals, shared episodes and discretion. **There is no Household Identity spec.** `friday-crew-spec.md` §1.1 already makes this correction verbatim: *"There is no Household identity spec. 'Household' appears nowhere as a design document; the principal/discretion model is V6 Phase 9, unbuilt, blocked on values questions Q1/Q2."* The principal model exists only as **V6 Phase 9** in `v6-wholeness-spec.md` — specified, unbuilt, with `services/principals.py` and `services/person_gate.py` named but not written, and `FRIDAY_DIR = ~/.friday` still flat-global with no `user_id` anywhere. The instruction behind the brief is nonetheless correct and is obeyed: **this spec invents no second identity model.** It scopes agent memory strictly agent-wise, exactly as the crew spec requires ("a desk partition answers 'which agent wrote this,' never 'which person owns this'"), and leaves a named socket for desk partitions to nest inside person partitions when P9 lands. See §7.

Second correction: the brief refers to "a prior evaluation [that] found LocalAI could not express identity-plus-scope." A repository-wide grep for `LocalAI` in Markdown returns nothing. The adopt/defer corpus that exists is `open-source-adoption-review.md` and `switchyard-position.md`, neither of which evaluates LocalAI. The Buzz verdict in §9 therefore rests on this pass's own reasoning, not on a prior finding, and should be read as such.

---

**Companions:** [`friday-crew-spec.md`](friday-crew-spec.md) · [`friday-builds-agents.md`](friday-builds-agents.md) · [`autonomy-execution-spec.md`](autonomy-execution-spec.md) · [`v6-wholeness-spec.md`](v6-wholeness-spec.md) · [`conversations-and-concurrency.md`](../implemented/conversations-and-concurrency.md) · [`roles-and-model-identity.md`](../../reference/roles-and-model-identity.md)

**Method:** STORM — five perspectives interrogated in §8, each stated as its strongest self, then synthesised.

---

**Evidence registers:**
- **VERIFIED** — read in a design document or the working tree during this pass (2026-09-08), with the file named.
- **MEASURED** — a number taken off this machine, cited to the audit that took it.
- **INFERRED** — a conclusion from verified facts, with the reasoning shown.
- **UNKNOWN** — not determined; the check that would settle it is named.
- **REPORTED** — an external claim about a third-party product, with the source named. Not verified by running the product.

---

## 0. The position, in one paragraph

The coordination centre's first job is **arbitrating scarce resources, not routing messages.** A CPU scoring job run alongside a training run has cut that training to roughly a fifteenth of its speed, because nothing on this machine arbitrated between two processes that wanted the same silicon. No amount of agent-to-agent messaging would have prevented that; a lease would have. Everything else follows from taking that seriously. An agent is not a persona with a system prompt; an agent is **a scope, a budget, an irreversibility boundary, a termination condition and a score**, with a persona painted on the front so a human can talk to it. Orchestration is not a rank an agent holds; it is a **role a project assigns**, so the same agent leads one project and takes direction in another. The org chart is an excellent renderer and a poor schema: build typed contracts, budgets, permissions and evaluation as the substrate, then draw the org chart on top of it — but concede what the org-chart position gets right, which is that every unit of work needs a named **decomposer** and a named **escalation addressee**, because a purely contractual system with a gap in it deadlocks silently, and silence is the failure mode we already have.

---

## 1. What already exists, so this spec builds rather than invents

**VERIFIED** from the two existing agent specs. Both carry `Implementation: none`. This spec is the third document in that family and must not fork their vocabulary.

| Primitive | Where | State |
|---|---|---|
| **Desk** — a named persistent role-agent = charter + seat + scope + memory partition + byline | `friday-crew-spec.md` §6.2, `~/.friday/crew/<name>/manifest.json` | specified |
| **Agent-as-class** — a Python class with `~/.friday/agents/<slug>/agent.toml`, states `draft`/`probationary`/`promoted` | `friday-builds-agents.md` | specified |
| **Handoff** — signed task card `{handoff_id, from_desk, to_desk, task_card, scope, budget_mψ, deadline, correlation, nonce, ts, sig}` | crew §6.5 | specified |
| **Scope** — `allowed_tools`, `max_ring`, `max_steps`, `time_budget_s` | `services/subagents.py:32-63` | built |
| **Rings 0–3** — `_governance_check()` before every tool call | `services/agent.py` | built |
| **Approvals / Q3 policy table** — "any outward/irreversible act, any spend, any external message" is gated | `services/approvals.py`, autonomy spec §4 | built (policy data) |
| **Goal budgets** — `budget_cap_mψ`, hard stop | `services/goals.py`, `services/budget_enforcer.py` | built |
| **Dual gate** — 10/10 structural + 12/12 honesty before a model may hold a seat | `services/model_seat_gate.py`, `services/honesty_battery.py` | built; **not enforced at dispatch** (crew defect X1) |
| **Completion-receipt law (A7)** — a completion claim without a matching tool receipt is fabrication and is stripped | `services/completion_receipts.py` | built |
| **Byline + model badge (B1/B2/MC10)** — every message names the model that answered *this* turn | seats spec | built |
| **Per-resource admission (MC3)** — no global turn gate; contention resolves in ≤5 s to progress or a stated choice | concurrency spec | built for conversations |
| **Residency arbiter** — `POST /api/residency/preview`, `assignment_cost()`, VRAM classes | `services/residency_policy.py` | built, **advice not a gate** |
| **Second computer (G3)** — executor as a federation peer, isolation tiers S1/S2/S3, Ring 3S | crew §6, §7 | specified |
| **Principals (V6 P9)** — owner/guest, `person_gate.py`, minor mode | `v6-wholeness-spec.md` | specified, blocked on values Q1/Q2 |

**INFERRED.** Friday already has almost every primitive this spec needs *in isolation*. What is missing is not a library and not a framework: it is **one manifest that binds them together per agent, one arbiter that owns scarcity, and one project object that assigns roles.** That is the whole build.

---

## 2. Goals

- **G1 — The agent editor.** A surface (usable by the maintainer and by Friday acting on the maintainer's instruction) that creates, configures and versions named agents: seat, system prompt/charter, scope, budget, irreversibility boundary, termination and escalation, evaluation reference, memory scoping, byline. Every field is enforced at dispatch or it does not appear in the editor.
- **G2 — The agent workspace.** A place to see agents: what each is doing, what it has spent, what it is waiting on, what it last produced, and — the one Friday most needs — **whether it has stopped without saying so.**
- **G3 — The coordination layer.** Multi-project concurrency with a real arbiter over scarce resources, and orchestration assigned per project rather than held per agent.
- **G4 — The org view.** Render the contract graph as an org chart because that is what a human reads at a glance. The chart is a projection, never the source of truth.

## 3. Non-goals

- **Multi-user / principals.** Untouched. Agent partitions stay agent-scoped; Q1/Q2 remain the maintainer's to decide (crew §10).
- **Selling or renting compute.** The mψ ledger stays internal infrastructure (V6 §1.1).
- **Adopting or forking Buzz.** See §9.
- **A leadership tier of agents with standing authority.** See §8, P3 and the synthesis.
- **Deleting anything.** Retirement is quarantine (crew D11).
- **A second daemon.** One process, one tray icon, one PyInstaller onefile (`open-source-adoption-review.md` §1). Any design needing a background service is scored down for that alone.

---

## 4. The agent object

One schema. The crew `manifest.json` is the base; `agent.toml` from builds-agents becomes a serialisation of the same object for code-bearing agents. Proposed location `~/.friday/agents/<slug>/agent.json`, with `~/.friday/crew/<name>/` retained as an alias until the two are merged.

```jsonc
{
  "schema": 1,
  "slug": "career-scout",
  "name": "Career Scout",
  "role_title": "Opportunity researcher",
  "status": "draft | probationary | active | suspended | retired",

  "identity": {
    "agent_id": "...",                 // stable, survives rename
    "subkey_pub": "ed25519:...",       // HKDF from ~/.friday/vault/.attestation-key-ed25519
    "created": "2026-09-08T...",
    "charter_version": 4,
    "source_sha256": "...",            // FA10: change voids approval, demotes to draft
    "exportable": false                // §9 — portable identity, off by default
  },

  "seat": {
    "capability": "subagent",          // or provider+model
    "fallbacks": ["claude-sonnet-5"],
    "max_ctx": 32768,
    "require_dual_green": true         // enforced at dispatch, not at settings-save (X1)
  },

  "charter": {
    "charter_md": "charter.md",        // taste, user-editable absolutely
    "persona_md": "persona.md",
    "constitution": "inherited"        // cLaws, honesty battery, receipts, egress tiers — not editable
  },

  "scope": {
    "allowed_tools": ["web_search", "read_file"],
    "max_ring": 1,
    "max_steps": 40,
    "time_budget_s": 1800,
    "egress_posture": "broker-only",
    "vault_access": "none | tier1 | tier1+2",
    "filesystem": { "read": ["..."], "write": ["scratch/"] }
  },

  "budget": {
    "tokens": 400000,
    "usd_micro": 250000,
    "wall_clock_s": 3600,
    "gpu": { "vram_mib": 0, "exclusive": false },
    "cpu_cores": 1,
    "period": "per_run | per_day",
    "on_exhaustion": "halt_and_escalate"   // never "continue"
  },

  "irreversibility": {
    "default": "approve",
    "classes": {
      "spend":              "approve",
      "outward_message":    "approve",
      "external_mutation":  "approve",
      "file_delete":        "refuse",
      "credential_use":     "approve",
      "code_execution":     "receipt",
      "schedule_change":    "approve",
      "self_modification":  "refuse"
    }
  },

  "termination": {
    "success":   { "kind": "scored", "eval": "career-scout-golden", "min": 0.7 },
    "failure":   { "kind": "steps_without_progress", "n": 5 },
    "deadline_s": 3600,
    "heartbeat_s": 120,
    "on_stall":  "escalate"
  },

  "escalation": {
    "addressee": "conv:conv-main",     // a conversation, an agent, or the maintainer
    "within_s": 300,
    "payload": ["last_receipt", "blocking_resource", "proposed_options"],
    "if_addressee_silent_s": 1800,
    "then": "halt_and_notify_maintainer"
  },

  "evaluation": {
    "batteries": ["core", "source_honesty"],
    "golden_tasks": "fixtures/",
    "red_axes":    ["sycophancy", "law1_refusal", "fabricated_completion"],
    "yellow_axes": ["voice"],
    "min_scored_runs_before_learning": 20
  },

  "learning": {
    "enabled": false,                  // hard-gated on evaluation, see §4.5
    "may_modify": ["routines"],        // never "charter" without Q5 being answered
    "promotion": "maintainer_approval"
  },

  "memory": {
    "partition": "agent:career-scout",
    "shared_reads": ["kg_core"],
    "cross_agent_reads": "brokered",   // never direct
    "person_partition": null           // reserved for V6 P9
  },

  "byline": { "label": "Scout", "icon": "...", "color": "..." }
}
```

### 4.1 Budget with hard ceilings

Four dimensions, because the incident that motivates this had nothing to do with money: **tokens, dollars, wall clock, and machine** (VRAM, exclusivity, CPU cores). `on_exhaustion` has exactly two legal values, `halt_and_escalate` and `halt_and_notify`; "continue" is not expressible. An unset ceiling inherits the conservative default, never an unbounded one (crew D12). Per-run budgets nest inside the crew-wide daily autonomous-spend ceiling that the autonomy spec already specifies.

The machine dimension is the new one and is the reason §5 exists.

### 4.2 The irreversibility boundary as configuration

Today the rule *"reversible operations are the agent's to execute; irreversible ones need the owner"* lives in a memory file and in one agent's head. Encoding it per-agent turns a convention into a field with three legal dispositions:

- **`execute`** — do it, receipt it.
- **`receipt`** — do it, receipt it, and surface it in the workspace as a notable action.
- **`approve`** — do not do it; raise an approval card and wait.
- **`refuse`** — do not do it and do not ask; the agent lacks the standing to propose it.

The class list is deliberately short and closed. It maps onto the Q3 policy table already in `services/approvals.py`, so this is a per-agent overlay on an existing global policy, not a second policy engine. **Precedence is fixed and not per-agent configurable:** vault law and the judgment gate outrank the agent's boundary; an agent's boundary may only ever be *stricter* than the global policy, never looser. An agent cannot grant itself `execute` on a class the global table gates. (This is the same rule that stops a role package granting itself Ring 3.)

The right boundary genuinely differs by agent, which is the whole argument for making it a field: a research agent that only reads should have `outward_message: refuse`, not `approve`, because asking is itself noise; a drafting agent should have `outward_message: approve`; an agent that maintains a scratch directory should have `file_delete: execute` scoped to that directory.

### 4.3 Termination conditions

The dominant observed failure was not agents doing wrong things. It was agents **not knowing they had stopped** — sessions idling politely while a GPU sat cold. So termination is specified as four independent predicates, any of which fires:

1. **Success** — a scored predicate, not a self-assessment (§4.5).
2. **Failure** — N steps without progress, where progress is defined as a new receipt of a kind the task declared.
3. **Deadline** — wall clock.
4. **Stall** — no receipt within `heartbeat_s`.

The stall predicate is the important one and is the cheapest thing in this entire document to build. It requires an open run record with a last-receipt timestamp and a tick that reads it. `friday-builds-agents.md` FA13 already specifies open/close run records; the concurrency spec already reconciles `running` → `queued` at boot. What is missing is a liveness tick that treats *quiet* as a state transition rather than as the absence of one. **Law: silence is never success.**

### 4.4 The escalation contract

Escalation without an addressee is a log line. Each agent names *who* it escalates to, *within what time*, *with what payload*, and *what happens if the addressee is also silent*. The addressee may be a conversation, another agent, or the maintainer; the chain must terminate at the maintainer within a bounded number of hops (proposed cap: 2). Escalation payloads carry the last receipt, the blocking resource (from the arbiter, §5), and proposed options — because "I am stuck" is not actionable and "I am stuck because project *training* holds the exclusive GPU lease until 04:10; I can wait, run CPU-only at ~8× the time, or use a cloud seat for $0.40" is.

### 4.5 A scored definition of doing the job well — and the learning-loop gate

An unscored model is worthless; an unscored agent is the same thing with a larger blast radius. Friday already has the machinery: `honesty_battery` (12 checks), `model_seat_gate` (10 structural), `persona_eval`, `qa_gates.evaluate_text` at threshold 0.7, per-role fixture categories, and the desk trust table with decayed weighted means. What is missing is the binding: **an agent's `evaluation` block is mandatory at creation, and an agent with no golden tasks may be created as `draft` but may never reach `active`.**

The hard rule, stated as a law because it is the one that most invites erosion:

> **Learning loops require the evaluation harness to exist first.** `learning.enabled` may not be set true unless `evaluation.golden_tasks` is non-empty and the agent has ≥ `min_scored_runs_before_learning` scored runs on record. An agent that modifies itself without evaluation does not improve; it drifts, and the drift is invisible precisely because the thing that would have noticed is the thing that was skipped.

This directly closes the crew spec's admitted gap that `learning_loop.promote()` is fully automatic.

### 4.6 Provenance

Every artifact an agent produces carries `{agent_id, charter_version, seat_model, seat_provider, config_hash, run_id, ts}`. This is not new policy; it is the seats-and-transparency badge law (B1/MC10) plus the completion-receipt law (A7) plus the crew byline, applied to artifacts rather than to chat turns. The user always knows which model is serving them; the corollary is that the user always knows which agent, under which version of its configuration, produced any given file. `config_hash` matters because "Scout wrote this" is useless if Scout's charter changed twice since.

---

## 5. The arbiter — scarcity is the coordination problem

**MEASURED, inherited:** the brain at optimum holds ~11.6 GB of 12.28 GB VRAM — **370 MiB free**. Never-co-resident is a hard constraint. The residency arbiter exists and returns `fits`, `overflow_mib`, `would_evict` — and is explicitly **advice, not a gate**: "a model the user selects wins."

That design is right for a human choosing a seat and wrong for two agents on two projects. So:

**Proposal — the arbiter becomes a lease broker for a closed set of scarce classes**, while remaining advisory for human-initiated seat choices:

| Class | Unit | Exclusive? |
|---|---|---|
| `gpu_vram` | MiB | shareable up to the reserve |
| `gpu_exclusive` | whole device | yes — training holds this |
| `cpu_cores` | cores | shareable; reserve honours `nproc - 1` |
| `llama_seat:<model>` | one in-flight request | serialised per seat process (MC3) |
| `egress_budget` | requests/minute per destination | shareable |
| `dollars` | micro-USD/day | shareable against the daily ceiling |

Five rules:

- **AR1 — No unmetered agent work.** Any work an agent starts acquires a lease first. A denied lease is a *decision returned in ≤5 s* with options, never a block (MC3 extended from conversations to projects).
- **AR2 — Foreign holds are declarable.** Friday cannot preempt a process it did not start — a training run launched from a terminal is outside its authority, and pretending otherwise is how you get an agent that kills your training. So the arbiter accepts a **foreign hold**: a declared, human-registered claim on a class ("training holds `gpu_exclusive` and 1 core until further notice"). Friday's contribution is to *refuse to add contention*, not to arbitrate the human's own processes. **This one rule, alone, is what would have prevented the scoring-job incident.**
- **AR3 — Priority is a project property, not an agent property.** Two projects contending resolve by declared project priority, then by lease age. An agent inherits its project's priority for the duration of the binding.
- **AR4 — Preemption is opt-in per project and never silent.** A project may declare itself preemptible. Preemption emits a receipt, an escalation to the preempted agent's addressee, and a resumable checkpoint reference. Non-preemptible work under contention queues and reports its queue position and estimate — the same `waiting: gemma4:12b busy (1 ahead, ~40s)` pattern the concurrency spec already ships.
- **AR5 — Detection beats politeness.** The arbiter samples actual utilisation and flags divergence between held leases and observed load. A lease held on an idle device is a stall signal; observed load with no lease is an undeclared foreign hold and prompts the user to declare it.

**UNKNOWN:** whether measured GPU/CPU sampling on Windows can be done cheaply enough to run on a tick without itself becoming a load. The check that settles it: sample `nvidia-smi --query-gpu` and per-process CPU at 10 s intervals for an hour during a training run and measure the delta. Do this before AR5 is designed, not after.

---

## 6. Orchestration as an assigned role

**The claim to build on:** orchestration is a **role a project assigns**, not a property an agent has. Concretely, a project object:

```jsonc
// ~/.friday/projects/<project-id>/project.json
{
  "id": "friday-v6",
  "title": "Friday v6",
  "priority": 60,                      // 0-100, arbitration input
  "preemptible": false,
  "orchestrator": "agent:chief-of-staff",   // may be "human:maintainer" or null
  "members": ["agent:scout", "agent:scribe", "agent:reviewer"],
  "budget": { "usd_micro_per_day": 500000, "gpu_exclusive_allowed": false },
  "escalation_addressee": "conv:conv-main",
  "contracts": ["research→brief", "brief→draft", "draft→review"],
  "status": "active | paused | archived"
}
```

Consequences that fall out of this, all of them wanted:

- The same agent is `orchestrator` in project A and a plain `member` in project B. Its manifest never changes; the binding does.
- **Friday need not be the orchestrator.** `orchestrator` may name any agent, or the maintainer, or be null (a flat project where work is pulled rather than assigned). Friday's default coordinator role, per the crew spec, carries **no elevated authority** — same gates, cannot seat models, cannot approve, cannot waive. That property must survive: an orchestrator agent is a *decomposer and router*, not a privilege tier.
- **Several projects run at once**, each with its own priority, budget, orchestrator and escalation addressee, contending through §5.
- Budgets nest: run ⊂ agent ⊂ project ⊂ daily household ceiling.

### 6.1 The typed contract between units

A contract is the unit of delegated work — the crew spec's signed handoff, given a declared type:

```
Contract {
  contract_id, project_id, from, to,
  input_type, output_type,             // named, checkable shapes
  success_criteria,                    // the scored predicate
  scope, budget, deadline,
  irreversibility_ceiling,             // may only narrow the agent's own
  nonce, ts, sig                       // Ed25519 desk subkey
}
```
Receipts on **accept / complete / refuse**, with refusal a first-class legitimate outcome. Contracts, not chat history, cross the boundary — this is the crew spec's "task cards, not chat history" rule, and it is the structural answer to telephone-game degradation.

### 6.2 The org chart as a projection

Given projects, bindings and contracts, the org chart is computed, not authored: nodes are agents, edges are contract flows, the root of each tree is the project's orchestrator, and colour encodes state (working, waiting on a lease, escalated, stalled, over budget). Rearranging the chart is not a legal operation; **editing a binding is**, and the chart redraws. This is the whole of G4 and it is a view layer.

---

## 7. Memory scoping — the hardest question, answered narrowly on purpose

Four scopes, and one deferral.

| Scope | Meaning | Rule |
|---|---|---|
| **private** | `agent:<slug>` partition | the agent reads and writes its own, always |
| **shared_reads** | declared list, e.g. `kg_core` | read-only, declared in the manifest, visible in the editor |
| **project** | `project:<id>` | members read and write; membership is the grant; leaving the project revokes the read |
| **brokered** | another agent's private partition | never direct; the orchestrator brokers, and **on ambiguity, withhold** |

Mechanism is already there: `cognitive_memory` `source_id="agent:<slug>"` plus Chroma metadata filters, the same pattern the crew spec specifies for desks.

**Who may read whose history** is answered as: nobody, by default, except through a broker that leaves a receipt. Cross-agent reads are a real exfiltration path (§8, P2) and deserve to be as loud as a cross-device egress.

**The deferral, stated explicitly rather than fudged.** Agents are arguably just another kind of principal, and the brief is right that Friday should not grow a second identity model. But the first identity model **does not exist yet** — V6 P9 is unbuilt and blocked on values questions Q1 (how Friday knows which person she is talking to) and Q2 (minor oversight policy) that are the maintainer's to answer. So this spec does the only honest thing available: it keeps every agent partition **agent-scoped, never person-scoped**, adds a reserved, always-null `memory.person_partition` field, and states the nesting rule now so that P9 lands as a prefix rather than a rewrite:

> When P9 lands, `agent:<slug>` partitions nest inside `person:<id>` partitions, and `person_gate.py` adjudicates before the agent broker is consulted. Until then, no agent partition may carry person-identifying scope, and no feature in this spec may create an accidental second-user path.

**INFERRED:** this is a cost. It means "the owner's agents" and "a guest's agents" are indistinguishable today, and the multi-project workspace is single-principal. That is the right cost to pay, because the alternative is deciding Q1 and Q2 implicitly through an agent feature, which is exactly what the autonomy spec forbids.

---

## 8. STORM interrogation

Each perspective is stated as its strongest self, not as a foil.

### P1 — The operator who has run multi-agent systems in production

"Your failure list is the right list and it is incomplete. Here is what actually kills these systems.

**Silent stall is number one and you have it already** — sessions idling politely while the GPU sits cold. Good, you're specifying a heartbeat. But note *why* it happens: an agent's last action succeeded, so nothing is in an error state; it is simply waiting for a turn that will never come. Your stall detector must be driven by *elapsed time since last receipt*, not by any error signal, or it will never fire.

**Number two is retry storms.** An agent whose termination predicate is 'success or 5 steps without progress' will, under a transient failure, burn its entire budget in a tight loop and then escalate — and if three agents share a rate-limited destination, they synchronise. Budgets bound the damage, which is why I like the four-dimensional budget, but you also need *backoff* to be a scope field, not a per-agent implementation detail.

**Number three is fan-out.** The moment an orchestrator can create contracts, someone writes a decomposer that emits forty of them. Cap contracts-per-run in scope and make exceeding it an escalation.

**Number four is idempotency.** Your contract has a `nonce`; use it. A retried contract that sends the same email twice is worse than one that fails.

**Number five is that your success predicate will be scored by a model.** `qa_gates.evaluate_text` at threshold 0.7 is a model judging a model. That is fine for drafts and unacceptable for anything with a side effect. Success criteria for side-effecting work must be *checkable* — a file exists, a row changed, a receipt matches — and only style work should be model-scored.

One thing you have got right that most teams don't: refusal as a first-class outcome. Systems where 'refuse' isn't expressible produce agents that fabricate rather than decline. Your A7 receipt law is worth more than your entire orchestration design."

### P2 — The security engineer

"You are about to hand credentials and tool access to configurable, possibly self-modifying processes, in an app whose own threat model admits the shipped binary's egress classifier is two layers of pattern matching. Sequence matters: **do not ship the editor before the enforcement.**

**The confused deputy is the whole game.** An agent with `max_ring: 1` that can ask an orchestrator to do something at Ring 2 has Ring 2. So: an orchestrator's contract may only ever *narrow* scope, never widen it, and the executing agent's own scope is checked at execution — never the requester's. Your `irreversibility_ceiling` on the contract is the right shape; make the rule explicit that the effective boundary is `min(agent, contract, global)` and that `min` is computed at the tool call, not at dispatch.

**Scope inheritance is where this always breaks.** FA5 already says capabilities are declared and enforced, default empty, never inherited. Spawned agents must start empty and be granted explicitly. If your editor has a 'clone this agent' button — and it will — cloning must not clone the boundary. Clone the charter, reset the boundary to default.

**Shared memory is an injection channel.** The moment agent A writes to `project:X` and agent B reads it as context, B is executing text A produced. If A ingested a web page, that page is now in B's prompt. Two mitigations, both cheap: mark every memory record with its provenance and trust class, and apply the existing `untrusted_input` discipline — untrusted content informs, never instructs — to *cross-agent reads*, not just to web content. The autonomy spec's Invariant 12, 'reach never widens authority', generalises exactly: **a contract never widens authority.**

**Budgets are a security control, not an accounting one.** A compromised or merely confused agent with an unbounded token budget is a denial-of-wallet. `on_exhaustion: halt_and_escalate` is correct precisely because it is the safe default under compromise.

**Signed handoffs need replay protection you actually check.** You have a nonce and a signature. Note the existing defect: `governance/proof_of_integrity.py:284` — with PyNaCl absent, checks are `None` and an unsigned manifest verifies `valid: True`. If contracts are verified by the same path, an attacker's best move is to make PyNaCl unavailable. Fail closed on a missing verifier, always.

**Exportable identity is an exfiltration primitive.** §9 proposes making the agent subkey portable so it can participate in an external network. Understand what that means: an identity that can act on Friday's behalf, off Friday's machine, outside Friday's egress gate. Default false is right. I would go further: make it require a maintainer approval card per agent, per export, with the destination named.

**Finally — the irreversibility boundary must be enforced at the tool call, in `_governance_check()`, not in the prompt.** A boundary that lives in a system prompt is a suggestion. This codebase already has a documented case of a constraint that was 'a sentence in a prompt, not a constraint in the dispatch path'."

### P3 — Defending the org chart as architecture, not just interface

"The skeptical position — that human org charts solve human problems agents don't have — is elegant and about two-thirds right. Here is the third it misses.

**First, contracts don't author themselves.** A typed-contract substrate presumes someone decomposed the goal into typed units. That decomposition *is* management work, and it is the expensive part. Saying 'build contracts, then render an org chart' quietly assumes the hardest function of a manager has already happened. A hierarchy is, among other things, a **standing answer to who decomposes** — and a standing answer is worth a great deal when the alternative is that nobody does and the work sits.

**Second, hierarchy is context-window economy, and that is a real agent problem, not a borrowed human one.** You have 12 GB of VRAM and a 32 K context cap. A supervisor holding a thin routing view and specialists holding deep task context uses strictly less context than one agent holding everything. The 'telephone-game degradation' objection is an argument against passing *chat history* down the chain — and the answer is exactly the typed task card, which is a hierarchy technique. Grok Bot's structure is not sentimental; a Chief-of-Staff agent above inbox, expenses and recruiting specialists is a compression scheme.

**Third, escalation requires an addressee, and 'the contract' is not an addressee.** This spec's own §4.4 concedes the point. The moment you require a named escalation target with a bounded hop count, you have drawn a tree. Call it a contract graph if you like; it has a root, and things flow up it.

**Fourth, a purely contractual system deadlocks on silence.** When a case falls outside every declared contract — and it will, on day one — a hierarchy has a default: it goes up. A flat contract mesh has no default; it has a gap, and gaps in this system present as the exact failure mode you already suffer, an agent quietly doing nothing. Hierarchy's much-mocked 'diffused responsibility' is the *pathology* of a mechanism whose *function* is having somewhere for the undefined case to go.

**Fifth, humans need one throat to choke.** Not for blame — for questions. When the owner asks 'what is happening with project X', the answer should come from one place with a coherent view, not from a query across seven receipt logs. That is a real requirement, and 'the UI aggregates it' is an answer that works until the aggregation is the thing that's wrong.

My concession: middle layers that only relay, and add latency without judgment, are pure loss, and agents make them cheap to create by accident. Cap depth. But do not mistake 'cap the depth' for 'there is no tree.'"

### P4 — The engineer who has to build the UI

"Look at the object in §4. That is roughly forty configurable fields per agent. If I render that as a form, nobody — including the owner — will ever create a second agent. Four things or this fails on contact.

**One: templates, not forms.** Creation starts from a role package (the crew spec already specifies `role-<name>/` with `ROLE.md`). The editor's default view shows about six fields — name, what it does, seat, what it may touch, what it may spend, who it escalates to. Everything else is behind 'advanced' with the inherited value shown, so the user sees *what it would be* without having to set it.

**Two: every field must be enforced, or it must not exist.** This repository has a document called `2026-09-04-five-dead-settings.md`. A configuration surface that presents controls which do nothing is worse than no surface, because it manufactures false confidence about a boundary. My hard requirement: **a field ships in the editor only in the same change that ships its enforcement, with a test that fails if the enforcement is removed.** If enforcement is deferred, the field is greyed out and labelled 'not yet enforced' — visibly, not in a tooltip.

**Three: the workspace's primary job is answering 'why is nothing happening?'** That is one view: every active agent, its state, and if waiting, *what it is waiting for* with a lease name and an estimate. Not a chat river. The chat river is where I would naturally put this and it is wrong — a stalled agent produces no messages, so a message-shaped UI renders a stall as an empty space.

**Four: the org chart must be a projection or it will rot.** If users can drag boxes, the drawing becomes state, the state disagrees with the bindings, and I get bugs I cannot reproduce. Chart is computed; editing happens on the binding.

Two smaller things. Contention needs a first-class view — a resource strip showing GPU, CPU, dollars, with who holds what and who is queued — because the alternative is that scarcity is invisible until it's a slowdown nobody can explain. And multi-project needs a project switcher with a *global* alarm rail, because the whole point of running four projects is not watching four dashboards."

### P5 — The maintainer's economics

"One machine. 370 MiB of VRAM headroom at optimum. One PyInstaller onefile, no second daemon, no Electron. Every design here must survive that. A coordination layer that costs a background service, a message broker or a resident model has already failed, whatever its merits. The arbiter must be a table and a tick. The workspace must be a view over stores that already exist. And the honest consequence of the hardware: **most 'multi-agent' concurrency on this box is sequential work with a scheduler**, and the coordination layer's real value is making that sequencing visible and deliberate rather than accidental."

### 8.1 Synthesis

P3 wins more than the opening position conceded, and it wins on two specific points, both of which are absorbed rather than argued with:

1. **Every project names a decomposer and an escalation addressee.** That is a tree, and pretending otherwise is how contract meshes deadlock. The synthesis is not "no hierarchy"; it is **hierarchy as per-project topology, not as global architecture, and never as a privilege tier**. An orchestrator decomposes and routes; it does not hold authority its members lack, cannot seat models, cannot approve, cannot waive. Rank is a job, not a permission.
2. **Depth is capped at 2** (orchestrator → member, plus one brokered sub-contract), and a relay-only layer — a node that neither transforms input nor adds a checkable decision — is a defect the arbiter can detect and report. This preserves P3's compression argument (thin routing context above deep task context) while foreclosing P3's own conceded pathology.

Where the opening position holds: the *substrate* is typed contracts, budgets, permissions and evaluation. The org chart is a projection over that substrate (P4's requirement), and it is a good projection precisely because a human reads it at a glance.

P1's amendments are adopted wholesale: backoff as a scope field, contracts-per-run cap, nonce-based idempotency at the receipt layer, and — the sharpest one — **success predicates for side-effecting work must be checkable, not model-scored.** Only style and quality axes go to `qa_gates`.

P2's amendments are adopted as laws, and one changes the build order: **enforcement ships before the editor.** The effective boundary is `min(agent, contract, global)`, computed at the tool call inside `_governance_check()`. Cross-agent memory reads inherit the untrusted-input discipline. Clone resets the boundary. Fail closed on a missing signature verifier. Exportable identity requires a per-export approval card.

P4's "no dead fields" rule and P5's "no second daemon" rule together set the phasing in §11: each phase ships an enforcement plus the minimum surface that exposes it, and nothing in the plan requires a resident process.

---

## 9. Buzz — adopt, fork, integrate, or learn from?

**REPORTED** (Block, 21 July 2026; not run or verified here): Buzz is an Apache-2.0, self-hostable collaboration workspace on the Nostr protocol — channels, threads, DMs, voice, media, code repositories, workflows. Agents "aren't assistants responding to commands. They have their own cryptographic identities, defined permissions, and the ability to participate in workflows." Every participant holds a Schnorr keypair that "belongs to them, not to the platform"; every message, patch and workflow step is a signed event in one auditable log. Model-agnostic and agent-agnostic (Claude Code, Codex, goose, or your own). Git hosting is present and described by Block as "still early."

**Verdict: learn from the primitive, plan a narrow integration, do not adopt and do not fork.**

**Why not adopt.** Buzz is a *workspace*, and Friday already is one. Adopting it means taking a relay and a second process as the substrate of a product that is one Flask process, one tray icon and a PyInstaller onefile, in a codebase whose own adoption review scores down every candidate needing a daemon *for that alone*. Worse, the two products' default postures on egress are opposed: Buzz's model is publish-signed-events-to-a-relay; Friday's is redact-by-default, fail-closed, with a gate on every outbound payload and a vault tier system that drops TIER_3 entirely. Reconciling those is not integration work, it is a rewrite of whichever one loses. And Friday's constitution — cLaws, honesty battery, approvals, completion receipts — is enforced in-process at the tool call. A workspace that hosts agents from any harness cannot enforce that, by design and on purpose; it is the right choice for Block and the wrong one for a product whose entire moat is that every action is attributable and gated.

**Why not fork.** Maintaining a Nostr workspace fork is not the moat, it is a second product. The crew spec's R5 kill criterion — unattributed crew action is a release blocker — is the thing worth defending. Nothing about it is easier inside a fork.

**What to steal, precisely.** The identity-plus-scope primitive is genuinely the right one, and it is close to what Friday already has: per-desk Ed25519 subkeys HKDF-derived from the install seed, with a revocation list. The three deltas worth adopting are:

- **P-1 — Identity is a first-class object with a declared permission scope attached to the key, not to a role table.** Friday's scope currently rides the manifest; binding it to the subkey means the scope travels with any signed act and can be verified by a third party. Cheap, and it is what makes the rest possible.
- **P-2 — Portability, gated.** The subkey should be *capable* of being exported so an agent could participate in a Nostr-compatible system with its identity, history and reputation intact. `identity.exportable` defaults false and requires a per-export approval card naming the destination (P2's amendment). Sovereignty means the owner's agents are theirs, including the right to take them elsewhere — but exportable-by-default would mean an identity that can act outside Friday's egress gate, which is exactly the wrong default.
- **P-3 — The receipt ledger becomes an append-only signed event log.** Friday has bylines and signed receipts already; making the ledger *structurally* an event log (one signed event per action, content-addressed, replayable) costs little now and turns a future Buzz integration into a transport change rather than a rewrite.

**The integration, when it comes.** One seam, later: a desk holds a Nostr keypair and appears as a participant in a Buzz channel, over `services/federation_transport.py`, with every outbound event passing `egress_gate.seal_outbound` like any other egress. That makes Buzz a *channel* — the same category as the Telegram and Discord bridges already in `services/channels/` — and Invariant 12 governs it: reach never widens authority. It should not be attempted before V6 P9, because a shared workspace is a multi-principal surface and Friday has no principals yet.

**The one thing Buzz gets philosophically right that Friday should copy today, free:** *no imposed hierarchy.* Agents are members with scopes, in shared space, and structure is a thing a team chooses rather than a thing the platform mandates. That is precisely the §6 position — orchestration as an assigned role — arrived at independently, and its convergence with a sovereignty-aligned Apache-2.0 product is corroboration worth noting.

**And Grok Bot** (SpaceXAI, 11 August 2026; **REPORTED**): persistent agents, each with its own cloud machine and long-term memory, direct agent-to-agent messaging, task handoff without the human as router, surfacing only for approval, with users placing a Chief-of-Staff agent above specialists in inbox, recruiting, expenses and operations. Two things to take: **handoff without the human as router** (Friday's contract, §6.1) and **per-agent isolated machine** (Friday's G3 second computer, already specified, and better — a Hyper-V guest on the maintainer's own hardware with zero durable secrets rather than a vendor's cloud VM). One thing to refuse: the leadership tier as a standing authority. Where Grok Bot's org chart is architecture, Friday's is a view.

---

## 10. Failure modes

| Goal | Failure mode | What catches it |
|---|---|---|
| G1 editor | A field is configurable but unenforced | P4's rule: no field ships without its enforcement plus a test that fails when enforcement is removed |
| G1 editor | Cloning an agent clones its irreversibility boundary onto a differently-scoped task | Clone resets boundary to the default (P2) |
| G1 editor | An agent widens its own scope via charter text | Charter is taste; scope is manifest; constitution is inherited and not editable (D7) |
| G2 workspace | A stalled agent renders as empty space in a message-shaped UI | Stall is a state with a heartbeat predicate, shown in a state view not a chat river |
| G2 workspace | Provenance shows the agent but not its config version | `config_hash` on every artifact |
| G3 arbiter | Friday kills or starves a human-started training run | Foreign holds are declared, never preempted (AR2) |
| G3 arbiter | An agent works without a lease and contention is invisible | AR1 plus AR5 divergence detection |
| G3 arbiter | Preemption loses work silently | AR4: receipt + escalation + resumable checkpoint, or it is not preemption |
| G3 orchestration | Orchestrator accrues privilege its members lack | Effective boundary is `min(agent, contract, global)`, computed at the tool call |
| G3 orchestration | Fan-out storm from a decomposer | Contracts-per-run cap in scope; exceeding it escalates |
| G4 chart | Chart becomes authored state and diverges from bindings | Chart is computed; boxes are not draggable |
| Evaluation | Learning loop runs without a harness and drifts | `learning.enabled` hard-gated on golden tasks + N scored runs |
| Evaluation | Side-effecting success judged by a model | Checkable predicates only for side-effecting work |
| Memory | Cross-agent read becomes an injection channel | Untrusted-input discipline applied to brokered reads; provenance and trust class on every record |
| Identity | An accidental second-user path appears | `memory.person_partition` always null pre-P9; no person-identifying scope in any agent partition |

---

## 11. Build order

Each phase ships an enforcement and the minimum surface that exposes it. No phase requires a background service.

**AE-0 — The arbiter of record (genuinely small, and useful alone).**
A leases table, a foreign-hold declaration, and a tick. No agent object, no editor, no projects. Deliverables: `services/arbiter.py` with `acquire/release/list`; a foreign hold the user can declare in one click ("I'm training — hands off the GPU and one core"); a resource strip in the UI showing holders and queue; and a refusal path — any Friday-initiated work that would contend with a declared hold is refused with options, in ≤5 s. **This alone prevents the incident that motivated this spec, and it is worth building even if nothing else here is.**

**AE-1 — Stall detection and the escalation contract.**
Open/close run records (FA13 already specifies them), a last-receipt timestamp, a liveness tick, and one escalation addressee per running task. Law: silence is never success. No new UI beyond a state badge.

**AE-2 — The agent object, read-only.**
Land the schema of §4. Migrate the existing crew `manifest.json` and `agent.toml` shapes onto it. Editor is a *viewer*: shows every field and where its value came from (default, role package, explicit). Nothing is editable yet. This is where dual-green-at-dispatch (defect X1) gets fixed, because the object makes the check's call site obvious.

**AE-3 — Enforcement, then editing.**
Budgets (four dimensions, `on_exhaustion`), the irreversibility boundary computed as `min(agent, contract, global)` inside `_governance_check()`, and termination predicates. Only fields with enforcement become editable. Approval cards pin a content hash; a source change voids the card (FA10).

**AE-4 — Evaluation, then learning.**
Golden tasks per agent, red/yellow axes, the scored success predicate, the trust table. `learning.enabled` becomes settable — and only for agents that pass the gate in §4.5.

**AE-5 — Projects, contracts, multi-project concurrency.**
The project object, orchestration binding, typed contracts with signed accept/complete/refuse receipts, project priority as an arbiter input, depth cap of 2. The workspace grows a project switcher and a global alarm rail.

**AE-6 — The org chart view.**
Computed projection. Last, because it is worthless until AE-5 gives it something true to draw.

**AE-7 — Portable identity and, optionally, Buzz federation.**
P-1/P-2/P-3 from §9. Not before V6 P9.

---

## 12. Acceptance tests (each can fail)

- **T1.** Declare a foreign hold on `gpu_exclusive`; ask Friday to start work that needs the GPU. It refuses within 5 s and offers CPU-only, queue, or cloud with a stated cost. *Catches: the scoring-job incident recurring.*
- **T2.** Start an agent task; kill its seat process. Within `heartbeat_s` + one tick, the agent enters `stalled` and its escalation addressee receives a payload naming the last receipt. *Catches: politely idling while the GPU sits cold.*
- **T3.** Give agent A `outward_message: approve`. Have an orchestrator issue a contract whose scope permits sending. A's send raises an approval card. *Catches: privilege laundering through a contract.*
- **T4.** Set an agent's token budget to 1,000 and give it a task needing more. It halts and escalates; it does not continue and does not silently truncate. *Catches: unbounded burn.*
- **T5.** Attempt to set `learning.enabled: true` on an agent with no golden tasks. Refused, with the reason named. *Catches: drift dressed as improvement.*
- **T6.** Clone an agent with a permissive boundary. The clone's boundary is the default, not the source's. *Catches: P2's clone trap.*
- **T7.** Produce an artifact, then change the agent's charter, then inspect the artifact. Its `config_hash` still resolves to the charter version that produced it. *Catches: provenance that names the agent but not its state.*
- **T8.** Remove the signature verifier (simulate PyNaCl absent). Contract verification fails closed. *Catches: `proof_of_integrity.py:284` recurring in a new place.*
- **T9.** Run two projects with different priorities against one llama seat. Both resolve to visible progress or a stated choice within 5 s; neither hangs. *Catches: MC3 not generalising from conversations to projects.*
- **T10.** Grep the editor's rendered fields against the enforcement test suite. Every field maps to a test. *Catches: a sixth dead setting.*
- **T11.** Have agent B read a `project:` memory record written by agent A from a web page. B treats it as untrusted content — informing, never instructing — and the receipt shows the provenance chain. *Catches: shared memory as an injection channel.*
- **T12.** Search the tree for any agent partition carrying person-identifying scope. Zero results. *Catches: an accidental multi-user path before Q1/Q2 are answered.*

---

## 13. Risk register with kill criteria

| # | Risk | Signal | Kill criterion |
|---|---|---|---|
| R1 | The agent object is too heavy; nobody creates a second agent | Two weeks after AE-3, agent count is 1 | Collapse to role packages only; no free-form creation |
| R2 | The arbiter becomes the bottleneck it was meant to remove | Median lease acquisition > 500 ms, or a lease deadlock observed | Arbiter reverts to advisory for all classes except `gpu_exclusive` |
| R3 | Multi-project is theatre on one 12 GB machine | Two projects never actually run concurrently in 30 days of use | Ship the arbiter and the workspace; drop projects to a label |
| R4 | Contracts add latency without adding judgment | Median contract hop adds > 20 s and no scored improvement vs. one agent doing the work | Cap depth at 1; orchestrator executes rather than delegates |
| R5 | Any unattributed agent action | One occurrence | Release blocker for that phase; no exceptions — this is the moat |
| R6 | Sampling for AR5 costs measurable load during training | Training throughput degrades > 2% with sampling on | Drop AR5; keep declared holds only |
| R7 | Editor ships a field ahead of its enforcement | One occurrence found in review | Revert the field; the rule is the rule |

---

## 14. Decision record

Answers below bind downstream work; downstream inherits from this file, not from chat history.

| # | Question | Decision | Binds |
|---|---|---|---|
| D1 | Is the org chart the architecture? | No. Typed contracts, budgets, permissions and evaluation are the substrate; the chart is a computed projection | §6.2, AE-6 |
| D2 | Does hierarchy survive at all? | Yes, as per-project topology with a named decomposer and escalation addressee, depth capped at 2, carrying no privilege | §8.1 |
| D3 | Is orchestration a property of an agent? | No — a role a project assigns; the same agent leads one project and takes direction in another | §6 |
| D4 | What is the coordination centre's first job? | Arbitrating scarce resources, not routing messages | §5, AE-0 |
| D5 | Can Friday preempt human-started work? | No. Foreign holds are declared and never preempted; Friday refuses to add contention | AR2 |
| D6 | Where is the irreversibility boundary enforced? | At the tool call, in `_governance_check()`, as `min(agent, contract, global)` | §4.2, §8.1 |
| D7 | May an agent's boundary be looser than the global policy? | Never. Only stricter | §4.2 |
| D8 | May learning loops run without an evaluation harness? | No — hard gate on golden tasks plus N scored runs | §4.5 |
| D9 | How is side-effecting success judged? | Checkable predicates only. Model scoring is for style and quality axes | §8.1 |
| D10 | Do agents get a new identity model? | No. Agent-scoped partitions only; `person_partition` reserved and null until V6 P9 | §7 |
| D11 | Buzz — adopt, fork, integrate, or learn from? | Learn from the primitive now (P-1/P-2/P-3); integrate as a channel after P9; do not adopt, do not fork | §9 |
| D12 | Is agent identity exportable? | Capable, but default false, per-export approval card naming the destination | §9, P-2 |
| D13 | May a field appear in the editor before its enforcement ships? | No | P4, R7 |

---

## 15. Open questions — the maintainer's product calls, not engineering defaults

- **Q1 — Who may create an agent?** Friday, acting on instruction, or the maintainer only? This is `friday-builds-agents.md`'s question in a new place, and the answer sets whether the editor is a tool Friday uses or a tool the owner uses.
- **Q2 — What is the default irreversibility boundary for a newly created agent?** Deny-all (safe, high friction, guarantees the first hour of every new agent is approval cards), or inherit-from-creator (fast, and a quiet privilege-propagation path)? Engineering can implement either; which one is correct is a values call about how much friction is worth.
- **Q3 — May agents read each other's histories by default, and may that cross project boundaries?** The spec defaults to brokered-and-receipted. A looser default would make crews smarter and make cross-contamination invisible.
- **Q4 — May a higher-priority project preempt a lower one mid-work, and who sets priority?** Preemption is specified as opt-in per project. Whether it should ever be automatic — and whether Friday may set priority or only the maintainer — is a product call.
- **Q5 — May a learning loop ever alter an agent's charter, or only its routines?** The spec defaults to routines only. Charter self-modification is the line between an agent that gets better at its job and an agent that changes what its job is.
- **Q6 — Should Friday's agents be exportable off this machine at all?** Sovereignty cuts both ways: the owner's agents are theirs, including the right to take them elsewhere; and an exportable identity can act outside the egress gate. D12 picks a cautious default; the principle is the owner's to state.
- **Q7 — When an agent needs an external account, is it a dedicated work identity or delegated session material?** The crew spec's D5 defaults to dedicated. Confirm, because it determines what an agent's compromise costs.
- **Q8 — Does the owner actually want the org metaphor in the interface?** The substrate argument holds either way. The chart is one rendering; a queue, a board, or a resource-first view are others. This is a taste call, and it should be made after AE-5 makes all three cheap to try.

---

*End of specification.*
