# Friday Crew — Teach, Delegate, and the Second Computer

**Date:** 2026-08-14
**Author:** Fable 5 (STORM method — multi-perspective interrogation → synthesis), commissioned by Stephen
**Status:** SPEC ONLY — no implementation in this session. Uncommitted. **Decision questions answered by Stephen 2026-08-14 — see §12, now a decision record.** Sections affected by his answers (notably D7: desks have distinct voices and personalities) have been updated to match.
**Verified against:** working tree `phase-a-truth-flow` @ `d42361e` (all file:line citations below were read at this commit).
**Companions:** [`V6_WHOLENESS_SPEC.md`](../V6_WHOLENESS_SPEC.md), [`AUTONOMY_SPEC.md`](../AUTONOMY_SPEC.md), [`SEATS_AND_TRANSPARENCY_SPEC.md`](../SEATS_AND_TRANSPARENCY_SPEC.md), [`audits/decisions-2026-08.md`](../audits/decisions-2026-08.md), [`audits/phase-a-report.md`](../audits/phase-a-report.md).

---

## 1. Context and strategic frame

On 2026-08-11 xAI launched **Grok Bot**: per-bot cloud VMs signed into the user's tools, 24/7 operation, multi-bot threads with a coordinator, teach-by-demonstration saved as routines, persistent improving memory, approval queues, and role-templated bots.

The agreed strategic response is **not** to chase their demo. Three of their ideas are genuinely good and Friday should absorb them; everything else they shipped is a trade Friday must refuse. The three:

- **G1 — Teach-by-demonstration.** Show the agent a workflow once; it becomes a repeatable routine.
- **G2 — The crew.** Named, persistent role-agents that collaborate in one thread under a coordinator.
- **G3 — The second computer.** An isolated, always-on executor where unattended work runs while the real desktop stays yours.

And the three non-negotiables Friday must strengthen, never trade away:

1. **Sovereignty.** Grok Bot's VMs live on xAI's cloud, signed into *your* accounts. Friday's second computer lives on Stephen's hardware, holds no durable credentials, and its every network packet crosses a policy boundary Stephen owns.
2. **Receipts.** Grok Bot improves "persistently" — opaquely. Every Friday crew action carries a byline and a signed receipt in the activity ledger; a claim of completion without a matching tool receipt is fabrication and is stripped (the A7 completion-receipt law, `services/completion_receipts.py`).
3. **Dual-gate honesty.** No model holds a tool-using or conversational crew seat until it is **dual-green**: structural conformance (10/10 tool-call discipline, `services/model_seat_gate.py`) *and* the honesty battery (12/12, `services/honesty_battery.py`). Grok Bot has no equivalent concept; this is the moat.

**The result is parity plus proof:** everything their demo does, with a receipt for every step and every secret still at home.

| Capability | Grok Bot | Friday Crew |
|---|---|---|
| Always-on executor | Cloud VM at xAI, signed into user tools | Hyper-V guest on Stephen's machine, zero durable secrets, host-owned egress proxy |
| Teach by demonstration | Recorded, auto-activated routine | Recorded → distilled draft → dry-run verified → **human-gated** activation; drafts structurally cannot inject |
| Multi-bot threads | Coordinator + role bots | Friday as editor-in-chief; desks with bylines, signed handoffs, partitioned memory |
| Improving memory | Persistent, opaque | Partitioned per desk, quarantine-able per desk (`cognitive_memory.memory_quarantine(source_id=…)`), every change receipted |
| Role templates | Bot templates | Role packages that carry their own honesty-battery fixtures and scope; a role cannot be seated ungated |
| Approvals | Queue | Existing Q3 policy table (`services/approvals.py:102-108`), Law-1 `blocked` no human can wave through, expiry that never auto-approves |

### 1.1 Corrections to the working brief (truth-flow discipline)

Two things this spec was asked to build on do not exist, verified by exhaustive search at `d42361e`:

- **There is no Edition "beat" draft→approve lifecycle.** The Edition (`services/edition_engine.py`, merged via `656b70b`) has cards, receipts, and a charter — its lifecycle is compose→persist→read-only. The generalizable draft→approve pattern actually lives in **`services/goals.py:208-219`** (`proposed→approved→active…`, illegal transitions raise, `_hard_gate_approved` only on a literal human yes). This spec builds on goals' state machine and adopts the Edition's *receipt gate* and *constitutional split* instead.
- **There is no Household identity spec.** "Household" appears nowhere as a design document; the principal/discretion model is V6 Phase 9, unbuilt, blocked on values questions Q1/Q2. This spec reconciles against V6 P9 (§2) and deliberately does not preempt it.

---

## 2. Reconciliation with V6 Wholeness (and the Autonomy spec)

V6 is Stephen's current thinking; Crew converges with it rather than forking. Explicitly:

**Crew ADOPTS from V6/Autonomy, unchanged:**
- **P5/A3 durable goals** as the execution substrate — goal state machine, milestone verify→repair→escalate, signed receipts, `set_executor()` seam (`services/goals.py:794-812`), Q3 approval policy table. Built and in-tree; Crew adds callers, not forks.
- **A1 persona eval + A4 honesty battery** as the gate chassis. Crew extends the fixture corpus per role (§6.3, §8); it does not build a third eval harness.
- **A4 untrusted-input doctrine** (`AUTONOMY_SPEC.md` §6): content from mail, webhooks, files, screens — and now from the executor's browser — *informs, never instructs*. Crew's inbound gate (§7.4) is a consumer of that doctrine, not a reimplementation. Where A4 is not yet built, Crew builds the shared service first and A4 inherits it (same inversion the Autonomy spec already made for `screen_trust.py`).
- **Q10 (CDP browser lane), Q12 (cloud-VLM grounding default, local labeled best-effort), Q3 (autonomy ceiling), Q6 (dissent strength)** — decided in `AUTONOMY_SPEC.md` §4; Crew inherits them verbatim.
- **V6 §6 invariants 4, 5, 8, 9, 10, 11** and Autonomy invariant 12 ("reach never widens authority") bind every Crew phase.

**Crew EXTENDS:**
- **The seat concept.** Today a seat is a model binding watched by `services/seat_transparency.py:34-48` (four fixed keys). Crew generalizes to named seats: `crew.<name>.seat`, with the same diff→system-line→notification mechanic (`observe_seats`, `:97-153`) so a crew seat can never change silently — Incident 2's fix, inherited.
- **The dual gate.** From "gate on the local seat" to "gate on *every* crew seat, enforced per-dispatch" (§3, defect X1).
- **Federation trust dimensions.** The peers table (`services/federation.py:54-79`: `reliability / honesty / claws_adherence / competence`, 0.95^weeks decay) is instantiated *locally*, keyed by crew name — the inter-agent trust graph `source_trust_graph.py:5-7` already anticipates ("eventually peer agents").
- **The Edition's constitutional split**, promoted from one module's docstring to the crew-wide law (§2.1).

**Crew DIVERGES, respectfully, in two places:**

1. **"One soul, many relationships" (V6 invariant 1) vs. named agents — decided D7, 2026-08-14.** Crew members are **distinct characters on a shared constitution**. Stephen decided (D7, §12) that desks have their own voices and personalities — not byline badges on a single voice. The reconciliation with V6 invariant 1 is the accuracy-authority split itself: **persona is taste, and the user shapes taste absolutely.** Each desk carries its own persona contract (voice, tone, temperament — defined in its role package and editable like any charter). What no desk may diverge from is the **constitution**: cLaws, the honesty battery, receipts, egress tiers, the approvals policy. Concretely, the A1 persona eval splits into two rubric layers per desk seat: the **constitutional axes** (non-sycophancy, epistemic calibration, Law-1 refusal, no fabricated completions) are Friday-wide and identical for every desk; the **voice axes** (tone markers, style, forbidden phrases) come from the desk's own persona contract. A desk can sound nothing like Friday's default register and still be constitutionally Friday. Motto revised: **one constitution, many characters.** This is a real divergence from V6's "one soul" phrasing, affirmed explicitly by Stephen on 2026-08-14 — `SOUL.md` remains the root identity and the default (coordinator) voice; desk personas are taste overlays on it, versioned and receipted like charters, never forks of the value system.
2. **Per-desk memory partitions before per-person partitions.** V6 P9 (multi-user) is last and blocked on Q1/Q2. Crew needs partitioned memory *now*, for agents rather than people. Crew builds partitions on the primitives P9 will also need (`cognitive_memory` `source_id` + Chroma metadata filters, §6.4) and keeps them **orthogonal to principals**: a desk partition answers "which agent wrote this," never "which person owns this." Crew must not create an accidental multi-user path — same discipline as Autonomy S3. When P9 lands, desk partitions nest inside person partitions.

### 2.1 The constitutional split, crew-wide

`services/edition_engine.py:16-18` enforces, structurally: *"CHARTER GOVERNS TASTE, NEVER ACCURACY — the charter can change what's included and how much… It cannot waive the receipt gate or invent content."* Crew promotes this to law for every desk:

- **User-shaped (taste, absolute):** each desk's role charter — what it watches, what it prioritizes, its output style, its cadence, its budgets. Stephen edits these as freely as the Edition charter, versioned the same way (`write_charter` archive pattern, `edition_engine.py:106`).
- **Constitutional (truth, not editable):** the receipt gate, the honesty battery, the dual gate, the approvals policy for outward acts, egress tiers, the byline requirement, honest degradation (a desk with nothing real to report produces a gap note, never filler — `_gap_card` pattern).

No charter edit, no role template, and no crew member — including the coordinator — can waive a constitutional rule. Outbound email, money, and legal actions stay human-gated **regardless of which desk acts** (Q3 table: `external_message`, `spend`, `irreversible` are gated classes; `approvals.py:102-108`).

---

## 3. Grounded substrate — what exists, and four defects that are preconditions

Every Crew phase builds on verified in-tree code. *Harvest, don't rebuild* (V6 §2 contract). Line numbers as of `d42361e`.

| Chassis | Where | What Crew uses it for |
|---|---|---|
| Goal state machine, milestone verify/repair/escalate, signed receipts | `services/goals.py:208-222, 576-644, 864-1044` | Draft→approve lifecycle vocabulary (G1); execution substrate (G2/G3) |
| `set_executor()` seam | `services/goals.py:794-812` | The designed swap point for a remote executor (G3) |
| Approvals queue + Q3 policy table, Law-1 `blocked`, expiry-never-approves, decision hooks | `services/approvals.py:102-178, 285-462` | The single human gate for G1 activation, G2 outward acts, G3 unattended steps. **Do not build a second queue.** |
| Dual gate: structural axis + honesty battery, fail-closed store, `axis_status`/`is_seat_dual_green` | `services/model_seat_gate.py:170-193`, `services/honesty_battery.py` | Seat admission for every crew member (G2), executor models (G3) |
| Golden-eval chassis (corpus + deterministic scorers + injectable dispatchers + `setup_turns` replay) | `services/persona_eval.py`, `services/honesty_battery.py:62, 286-293, 387` | Role batteries (§6.3); routine dry-run harness (§5.5) |
| Tool-hook chain (pre/post, exception-isolated, priority-ordered) | `services/tool_hooks.py` + registrations `services/agent.py:4253-4266` | Demonstration capture is a post-hook — zero new instrumentation in the agent loop (§5.2) |
| Context log (full unredacted tool inputs, per-day JSONL) | `core/__init__.py:1006-1021` | The fidelity standard the demonstration store must match (§5.2) |
| Activity ledger — whitelist schema, metadata-only by construction, orb/task correlation | `services/activity_ledger.py:30-84` | Byline/receipt stream (G2); the telemetry contract for what an executor may report home (G3) |
| Process orbs + `_tier_safe_summary` redaction + click-through threads | `core/__init__.py:1136-1215`, `services/agent.py:4527-4658`, `routes/tasks.py:182-200` | Per-desk orbs, crew trace threads (§6.6) |
| Subagent scopes: allow/deny lists, ring ceiling, step/time budgets, fail-closed registration | `services/subagents.py:32-63, 209-229`; `services/agent.py:1902-1911` | Scope vocabulary for routines (G1), desks (G2), and the executor capability profile (G3) |
| Workflow chains: steps + context threading + `_advance_task_chain` | `services/agent.py:1955-2103` | The executor for distilled routines (§5.6) |
| Scheduler: `agent_prompt` jobs, retries, run history, failure-always-notifies | `services/scheduler.py:126-236, 455-599` | Recurring activation of approved routines; crew liveness ticks |
| Egress gate: tier lattice, `gate_worker_payload` destination-decides pattern, boot self-test | `services/egress_gate.py:42-82, 421-474, 523-585`; `services/sensitivity_classifier.py:32-36` | The pattern G3's boundary generalizes (§7.4) |
| Federation transport: X25519 ECDH + ChaCha20-Poly1305 + Ed25519 envelopes, job lifecycle, capability adverts | `services/federation_transport.py:1-26, 238-334`; `services/compute_client.py:157` | **The only in-tree machinery that crosses a machine boundary with authenticated, signed, encrypted control.** G3's control plane (§7.3) |
| MCP env sanitization (reduced environment for child processes) | `services/extension_security.py:21-43` | Credential-minimization pattern for the executor (§7.5) |
| Skill registry: canonical SKILL.md folders, import/export, `meta` absorbs unknown frontmatter | `skill_registry.py:71-98, 305-427` | Draft routine format (§5.4); role package format (§8) |
| Seat-change visibility (diff → system line → notification) | `services/seat_transparency.py:97-153` | Extended to crew seats — no silent reseating, ever |
| Trust graphs: people/source/**federation peers** (`reliability/honesty/claws_adherence/competence`) | `people_graph.py`, `source_trust_graph.py`, `services/federation.py:54-79, 403+` | Local inter-desk trust table (§6.5) |
| Auth hardening: `login_required` fails closed for non-loopback without `FRIDAY_REMOTE_KEY` | `core/__init__.py:252-256, 350-370, 434-447` | Phone surface (§7.7); why the executor must NOT talk to the loopback web API (§7.3) |
| Measured hardware constraint | `docs/audits/phase-a-report.md:267-269` | Brain at optimum holds ~11.6 GB of 12.28 GB VRAM — **370 MiB free**. Never-co-resident is measured, not conventional. Binds §7.6. |

### 3.1 Defects that are Crew preconditions (fix in Crew-0)

Found during the pre-spec audit; each is small, each is load-bearing, and Crew built on top of them un-fixed would inherit exactly the silent-failure class Phase A exists to kill.

- **X1 — Dual-green is not enforced at dispatch.** `routes/core_routes.py:618-708` requires both axes at settings-save, but the per-dispatch path (`services/model_router.py:396-420` → `model_seat_gate.resolve_local_seat:225-263`) checks **structural only**; `is_seat_dual_green` has no runtime call site. A direct settings.json edit — the exact Incident 2 vector — seats a structurally-green/honesty-red model (gemma4 today: structural 10/10, honesty 11/12) for tool-using turns. Fix: tool-using and conversational dispatch resolves through the dual-green check, same fail-closed ladder.
- **X2 — Skill activation has no gate.** `learn_skill` is Ring 1 "always allowed" (`services/agent.py:2854`, `_governance_check:3840`), not in `TOOL_REQUIRES_CONFIRMATION`, and its return text says "Active now." `match_skills` (`skill_registry.py:250`) injects anything with matching triggers. G1's whole premise — drafts never self-activate — is impossible until skills carry a `status` that the injection chokepoint honors. Fix: `status:` frontmatter (`draft|active|retired`, default `draft` for anything model-authored), `load_skills`/`match_skills` filter, `learn_skill` writes drafts only.
- **X3 — Workflow-chain routes are unauthenticated.** `routes/workflows.py:678-727` (`POST /api/workflows/chains`, `…/run`) carry no `@login_required`. Any local process can create and run a multi-step agent chain. Fix: `login_required` on all mutating chain routes.
- **X4 — Unverifiable signatures count as valid.** `governance/proof_of_integrity.py:284` computes `valid = all(v is True for v in checks.values() if v is not None)` — with PyNaCl absent, signatures are the literal `"ed25519_unavailable"`, checks are `None`, and an unsigned manifest verifies `valid: True`. Before any signed handoff (§6.5) or executor attestation (§7.3) relies on this, unverifiable must mean **not valid** (fail-closed, same as every gate in the tree).

Two adjacent facts, recorded but *not* Crew-0 scope: `learning_loop.promote()` is fully automatic (`services/learning_loop.py:276`) — acceptable for Ring-0 text heuristics, and Autonomy A7 already specs receipted promotions (Q10, §12); and the skill store is split (`_tool_learn_skill` writes legacy YAML while `save_skill` writes canonical folders — `skill_registry.py:305` vs `services/agent.py:474-521`); G1 unifies on canonical folders and the legacy path becomes read-only.

---

## 4. STORM interrogation — six perspectives before synthesis

**P1 — The Operator (Stephen, daily).** "I don't want to configure a crew; I want to *hire* one. Teaching should be: I do the thing once while talking, Friday hands me a draft, I read it, I approve it. The crew should feel like a newsroom I run from one thread — and from my phone when I'm out. If any of this adds friction to plain old chat, I'll stop using it." → **Requirements:** teaching is a single "watch this" gesture, not a recorder app; drafts are readable prose (SKILL.md, not JSON); crew threads live in the existing chat with bylines, not a new app; the phone surface is the approval queue and the kill switch before it is anything else; solo Friday remains the default — the crew appears when summoned or scheduled.

**P2 — The Safety Engineer.** "You are proposing an unattended actuator that browses attacker-controlled content while a distilled-from-demonstration script drives it. Enumerate what it can destroy: the host (VM escape, loopback trust), the accounts it touches (session abuse), the data it sees (exfiltration via its own browser — the exfil channel is the *task*), and the truth (fabricated completions no one watches happen)." → **Requirements:** the executor is a **federation peer, not a loopback client** — `FRIDAY_TRUST_LOOPBACK` must never see it (§7.3); its only internet path is a host-owned policy proxy with per-task domain allowlists (§7.4); it holds zero durable secrets (§7.5); everything returning from it is untrusted input; completion claims are checked against *two independent evidence streams* (its receipts and the proxy's logs); snapshot/rollback bounds compromise to one task; and the hypervisor pause is a kill switch that works even when the agent inside is wedged.

**P3 — The Newsroom (delegation, bylines, accountability).** "Multi-agent systems die of blame diffusion. Who wrote this claim? Which desk fetched that fact? If the answer is 'the crew,' you've built a committee, not a newsroom." → **Requirements:** every crew action carries its desk's byline into the ledger and the chat; only the coordinator merges into user-facing output, and its synthesis cites per-desk receipts the way Edition rationale cites clauses; a desk with nothing real produces a gap note (honest degradation); handoffs are signed artifacts, so "I never got that task" and "I never sent that result" are both checkable.

**P4 — The Economist.** "The card has 370 MiB of VRAM headroom with the brain resident. The brain does 25 tok/s; the sidekick 71. A crew of five sharing one brain is a queue, not a team. And an always-on executor that escalates to cloud at 3 a.m. is a bill." → **Requirements:** desks share seats through `capability_routing` — a desk is cheap because it is a *charter plus scope*, not a resident model; the executor is CPU-only and never loads models (its VLM/grounding calls route to the host router, which queues per the never-co-resident rule, or to cloud through the host egress gate per Q12); the daily autonomous-spend ceiling (Autonomy S5) applies crew-wide, hard-stop, receipted; per-desk budgets ride the existing goal `budget_cap_mψ` two-layer design (`goals.py:120-161`).

**P5 — The Skeptic (where multi-agent systems rot).** "Context drift: agents summarize each other into mush. Duplicated work: two desks fetch the same page. Silent failure: a desk stalls and nobody notices for a week. Coordination tax: the coordinator spends more tokens routing than the desks spend working. Show me why this beats one good Friday." → **Requirements:** handoffs carry task cards, not chat history — partitioned memory is a *feature* against drift; the coordinator dedups against the ledger before assigning (same correlation ids); desk liveness rides the scheduler tick and a stalled desk escalates like a failed milestone (`escalated` status, never silent); and Crew ships with a **falsifiable comparison**: golden tasks run solo-Friday vs. crew, scored — if the crew doesn't beat solo on quality or coverage within two weeks of Crew-2, it collapses back to solo (kill criterion R1, §11).

**P6 — Stephen's doctrines.** Loop engineering: every phase has verification gates that can fail, and nothing self-certifies — the demonstrator verifies the distiller, the dry-run verifies the routine, the battery verifies the seat, the proxy log verifies the executor. Vacuous tests are the enemy: every acceptance test below names the failure it would catch (the D9 lesson — a stubbed path ships defects green). Visible-if-wished: badges default-on, depth on demand ("Every model action, every subagent process, every reasoning thread needs to be visible if the user wishes to see it"). Accuracy-authority split: §2.1. Truth-flow: when this spec's own brief was wrong (no beats, no household spec), the spec says so (§1.1).

**Synthesis.** The gates exist; what's missing is *identity* (a desk), *artifacts* (a demonstration, a handoff), and a *boundary* (the executor). So the build order is: harden the gates that lie (Crew-0) → give demonstrations a store and drafts a lifecycle (Crew-1) → give desks names, memory, and bylines (Crew-2) → give the browser a lane (Crew-3) → put the executor behind a real boundary (Crew-4) → teach from the screen (Crew-5). Each phase is independently valuable; each later phase consumes the earlier ones' artifacts without modification.

---

## 5. G1 — Teach-by-demonstration

### 5.1 Design

Two modes, sequenced honestly:

- **Mode 1 (Crew-1): demonstrate *through* Friday.** Stephen says "Friday, watch this" (chat or UI toggle), then performs the workflow using Friday herself — asks her to search, fetch, write, file — narrating intent as he goes. Everything already flows through the tool loop, so capture is a post-hook, not a recorder. He says "done — make that a routine"; the distiller produces a draft; the draft goes to the approval queue.
- **Mode 2 (Crew-5): demonstrate on the screen.** Stephen drives apps directly while narrating; Friday observes via per-app **observe-tier** grants (V6 P6 permission tiers) — periodic screenshots plus locally-transcribed narration (faster-whisper, measured RTF 0.869, comfortably realtime; `docs/audits/provisioning-report.md:304`). Honesty requirement carried from V6 Q12: screen *understanding* needs a capable VLM; the cloud path goes through the egress gate, the local path is labeled best-effort. Mode 2 depends on the actuation phase's permission tiers and is not the first deliverable — Mode 1 is where the weekend lives.

**The one law that binds both: a draft NEVER self-activates.** Not by the model, not by the distiller, not by a trigger, not by `learning_loop`. Activation is exactly one path: a human decision on an approval card.

### 5.2 Capture (Mode 1)

New `services/demonstrations.py`:

- `begin_demo(narration_hint) -> demo_id` / `end_demo(demo_id)` — explicit session boundary (the thing `behavioral_monitor.begin_session` is *not*: its `traces.jsonl` truncates to 20 sessions and scrubs argument values to 48 chars — `governance/behavioral_monitor.py:62, 135-144` — unusable as a demonstration store, by design; leave it alone, it has a different job).
- A **post-hook** on the tool chain (priority ~100, registered like `services/agent.py:4253-4266`): while a demo session is open, append `{tool, input, result_preview, result_len, ring, workspace, run_id, task_id, orb_id, ts}` — full-fidelity inputs, same standard as the context log (`core/__init__.py:1006-1021`). Exception-isolated; cannot loosen any gate (user hooks can only tighten).
- Chat turns during the session are captured as narration alongside the tool events.
- Store: `~/.friday/demonstrations/<demo_id>/transcript.jsonl` + `manifest.json` `{demo_id, started, ended, narration_summary, observed_tools[], observed_rings[], max_ring, workspaces[], tier_max}`.

**Sensitivity:** the transcript inherits the highest tier its contents classify to (`sensitivity_classifier.classify`); a TIER_2/3 demonstration is stored under vault rules and its distillation runs on a local seat or fully sealed — a demonstration of "file my medical receipts" must not leak its examples to a cloud distiller.

### 5.3 Distillation

`services/routine_distiller.py`: one LLM pass on the **reasoning seat** (the brain by default) over `(transcript, narration)` producing a draft routine:

- Steps with names and prompts (mapping onto workflow-chain steps, `services/agent.py:1985` schema — `steps[{name, prompt, with_context}]`).
- **Parameter slots**: literal arguments the narration marks as variable ("this week's", "the new client") become `{{slot}}` with the demo value as the worked example.
- Dead-end pruning: exploratory calls that contributed nothing to the final artifact (heuristic: results never referenced downstream) are dropped, but recorded in `meta.pruned` so the review shows what was cut.
- The distiller **may not invent steps**: every step must cite the transcript event ids it derives from (`meta.derived_from`). A step with no citation fails distillation — the no-receipt-no-render rule applied to routine authorship.

### 5.4 Draft format and lifecycle

Drafts are canonical **SKILL.md folders** (`skill_registry.py` format; `meta` absorbs new keys without schema change — `:71-98`):

```yaml
name: file-weekly-receipts
description: …
status: draft            # draft | active | retired   (X2 makes this real)
triggers: [ "file my receipts" ]
version: 1
meta:
  kind: routine
  source_demo_id: demo_2026-08-14_ab12
  demonstrated_at: 2026-08-14T…
  observed_rings: [0, 1, 2]
  observed_tools: [search_email, read_file, write_file]
  slots: { period: "this week" }
  derived_from: { step1: [ev_003, ev_004], … }
  scope: { allowed_tools: [...], max_ring: 2, max_steps: 25, time_budget_s: 900 }
```

Lifecycle (vocabulary borrowed from `goals.py`, enforcement from `approvals.py`):

```
captured ──distill──▶ draft ──dry-run green──▶ pending_approval ──human──▶ active
                        │                          │                        │
                        └── edit/re-distill        └── denied → draft       └── retire / suspend
```

- `draft`: visible in a Routines panel, injectable **nowhere** (`match_skills` filters, X2), linkable to no schedule.
- `pending_approval`: `approvals.create_approval(kind="routine_activation", subject_type="routine", subject_id=slug, force_gate=True)` — always a human card, even though activation itself is "internal" by the Q3 table. The card renders the draft prose, the scope, the dry-run report, and the source demo link.
- `active`: injectable and schedulable. **Per-run gates still apply** — activation approves the routine, not its outward steps: any step classifying as `outward/spend/external_message/irreversible` hits the Q3 gate on every run (`gate_action` is idempotent per subject, so recurring runs create per-run cards). Teaching a workflow never becomes a standing authority to email people.
- Activation, edits, and retirement are receipted; edits to an `active` routine demote it to `draft` (re-approval required) — a modified routine is a new authority.

### 5.5 Dry-run verification

Before a draft may reach `pending_approval`, a replay harness proves it does what was demonstrated — the `honesty_battery` chassis reused (`HARNESS_TOOLS` fixed-tool-set pattern, `services/honesty_battery.py:62`; injectable dispatcher; deterministic scorers):

- Tools are stubbed with the demonstration's recorded results; the routine's steps run against them.
- Pass: the tool-call sequence matches the demonstrated one within slot substitution (order-tolerant where steps are independent), no un-demonstrated ring is touched, no un-demonstrated tool is called, and the completion-receipt law holds on every claimed step.
- The report `{passed, by_step, deviations[]}` attaches to the approval card. A red dry-run cannot be human-overridden into `active` — fix the draft instead (same posture as goals: never mark done on a failed check, `goals.py:108-118`).

### 5.6 Execution

An `active` routine runs as a **workflow chain** under a **derived scope**: `allowed_tools` = observed tools, `max_ring` = observed max (never higher; Ring 3 unreachable regardless — `_SUBAGENT_RING_CEILING`, `services/subagents.py:29`), step/time budgets from the manifest. Scope registration is synchronous-before-spawn and fail-closed (`services/agent.py:1902-1911`). Recurring activation is a scheduler `agent_prompt`/chain job — retries, backoff, history, failure-always-notifies all come free (`services/scheduler.py:581-599`). Every run: orb + ledger entries + a signed run receipt (goals receipt primitive, `goals.py:576-644`) carrying `routine_slug`, `source_demo_id`, and the chain's correlation ids.

### 5.7 UI surface

- "● Watching" indicator during capture (nothing silent — capture is as visible as recording should be).
- Routines panel: drafts with diff-style review (steps, scope, slots, what was pruned), the dry-run report, activate → approval card; run history per routine.
- Approval cards render in the existing queue; later, on the phone via the A4 channel (converge, don't duplicate).

### 5.8 Failure modes

| Failure | Mitigation |
|---|---|
| Over-generalization (wrong slot, wrong glob) | Slots require narration evidence; dry-run replays the demo case; first N live runs flagged "new routine" with notify-tier receipts |
| Demo captures secrets | Tier classification on the transcript; vault storage; local/sealed distillation (§5.2) |
| Tool surface drifts under a routine (renamed tool, moved file) | Dry-run re-runs on schedule-link and after upgrades; a failing step escalates (goals pattern), never silently skips |
| User demonstrates an outward act | Per-run Q3 gates survive activation (§5.4) |
| Distiller hallucinates steps | `derived_from` citation requirement; uncited step = failed distillation |
| Draft edited into something else, then approved on stale review | Approval card pins a content hash of the draft (Edition `_card_content_hash` pattern, `edition_engine.py:136-177`); hash mismatch at decision time voids the card |

### 5.9 Acceptance tests (each can fail)

- A demonstration produces a transcript whose every event carries full tool input and correlation ids; `behavioral_monitor` state is unchanged by it.
- A distilled draft with a fabricated (uncited) step is rejected by the distiller gate.
- A `draft` routine with matching triggers is **not** injected by `match_skills`; flipping the file's `status` to `active` by hand without an approval record is detected and the routine is demoted with a notification (the X2 regression test).
- The dry-run harness fails a draft whose steps deviate from the demonstration, and that draft cannot reach `pending_approval`.
- Activating a routine whose steps include `send_email` still produces a per-run approval card on every scheduled run.
- The F1-class test: a routine step's write tool returns failure; the run receipt records failure and no completion is claimed (completion-receipt law fires).
- A TIER_3 demonstration transcript never appears in any cloud-bound distiller payload (egress adversarial test).

---

## 6. G2 — The Crew

### 6.1 Design

A **crew member** ("desk") is a named, persistent role-agent: `Chief of Staff`, `Career Scout`, `Account Health`. Concretely it is five things bound together: a **role charter** (taste — user-editable), a **seat** (a dual-green model binding), a **scope** (capability envelope), a **memory partition** (its own working memory), and a **byline** (its accountable identity in every surface). Friday is the **editor-in-chief**: she routes work to desks, arbitrates conflicts, dedups, and is the only voice that merges desk output into user-facing prose — with citations to desk receipts.

Desks are summoned three ways: explicitly ("ask the Career Scout…"), by the coordinator during a task that matches a desk's charter, or by schedule/goal linkage (a desk can own goals — `goals.owner` becomes the desk name). Multi-desk threads render in the existing chat: desk messages carry byline badges (B1 model-badge pattern extended) **and each desk speaks in its own voice and personality (D7)** — the thread reads like a room of distinct, accountable colleagues, with Friday-the-coordinator as the voice that opens, arbitrates, and closes. Character is welcome; the accountability spine (byline + receipt per message, coordinator-only merge into conclusions) is what keeps a lively room from becoming a committee.

### 6.2 Data model

```
~/.friday/crew/<name>/
  manifest.json    { name, role_title, status: active|suspended|retired,
                     seat: { capability | provider+model },      # capability_routing key or explicit binding
                     scope: <subagents.SubagentScope fields>,
                     memory: { partition: "crew:<name>", shared_reads: [kg_core, ...] },
                     battery_profile: [core, <role categories>],
                     byline: { label, icon, color },
                     subkey_pub, created, charter_version }
  charter.md       # taste; versioned to charter_versions/ on every write (edition pattern)
  trust.json       # this desk's row in the local peer-trust table (§6.5)
```

Registered desks surface through a new `routes/crew.py` blueprint (`GET /api/crew`, `POST /api/crew/<name>/suspend|retire`, mutations `@login_required`). Seat changes to any desk flow through the generalized `seat_transparency` watcher — visible system line + notification, tested by direct settings edit (the Incident 2 acceptance test, inherited).

### 6.3 Gates: dual-green per seat, plus role batteries

- **Admission:** no model holds a desk seat unless `is_seat_dual_green(model, provider)` — and after X1, this is enforced **per-dispatch**, so a settings edit cannot seat an ungated model for a single turn. Nominating an ungated model for a desk triggers both axes with visible progress, fail-closed (A5 behavior, `SEATS_AND_TRANSPARENCY_SPEC.md`).
- **Role batteries:** each role package (§8) ships role-specific honesty fixtures as new categories in the battery's scorer registry (`_SCORERS`, `honesty_battery.py:286-293` — adding a category is one scorer + fixtures, the registry is designed for it). Examples: Chief of Staff — calendar-fact discipline (never assert an event without a fresh calendar receipt: the `connection_state` scorer generalized); Career Scout — source honesty (never present a listing without its origin URL receipt). A desk seat must be green on `core + role` categories. Threshold stays the battery's own: all-green, fail-closed.
- **Persona regression (two-layer, per D7):** the A1 persona eval runs per desk seat in fixture mode with a split rubric — the **constitutional axes** (sycophancy, hedging, corporate filler, Law-1 refusal, epistemic calibration) are identical for every desk and fail admission even if the desk is honest; the **voice axes** score adherence to the desk's *own* persona contract from its role package, so a Chief of Staff can be clipped and a Career Scout enthusiastic while both remain constitutionally Friday. A desk drifting out of its own declared character is a yellow flag (recheck); a desk failing a constitutional axis is a red gate. One constitution, many characters, provably.
- **Current gate reality, stated plainly:** no local model is dual-green today (gemma4: structural 10/10, honesty 11/12 — `tests/conformance/results/honesty__local__gemma4_latest.json`); claude-sonnet-5 is green (12/12). The sovereign candidate for desk seats is the llama-cpp brain (`qwen3.6-35b` at 25 tok/s), which has **no battery record yet** — running it is a Crew-2 precondition (decided D4: run it; it may hold desk seats if dual-green).

### 6.4 Memory partitions

Built on two existing primitives, no new store:

- **`cognitive_memory` `source_id`** — already first-class on every write, already indexable, already has bulk semantics: `memory_quarantine(source_id=…)` (`cognitive_memory.py:115-152`). Desk writes carry `source_id="crew:<name>"`. Retiring a desk = quarantine its partition (reversible). Per D11 — "we never delete anything" — Crew ships **no deletion path at all** for desk memory: quarantine is the only retirement verb, and the ledger keeps the retired desk's byline history readable forever.
- **Chroma metadata** — desk turns write `{agent: <name>}` metadata into the conversations collection; desk retrieval filters on it. The D5 dimension-stamp work (`conversation_memory.py:174-240`) means a desk could even hold a different embedder safely, though Crew-2 does not use that.

**Access rules (fail-closed):** a desk reads its own partition plus declared `shared_reads` (the knowledge-graph core, the wiki). Cross-desk reads are **brokered by the coordinator** as explicit handoff content — desk A never queries desk B's partition directly. On ambiguity, withhold — the same posture as the egress gate, and the same posture V6 P9's person gate will take; these partitions are the rehearsal, and they stay agent-scoped (never person-scoped) so P9's values questions stay open.

### 6.5 Delegation protocol: signed handoffs and desk trust

**Handoff artifact** — the missing piece the audit named ("nothing signs a task delegation today"):

```
Handoff { handoff_id, from_desk, to_desk, task_card{title, instructions, inputs[],
          success_criteria}, scope, budget_mψ, deadline, correlation{goal_id?, orb_id, task_id},
          nonce, ts, sig }
```

- Signed with the sending desk's **Ed25519 subkey**, derived via HKDF from the install seed (`~/.friday/vault/.attestation-key-ed25519`, `proof_of_integrity.py:134-156`) — per-desk identity without new key ceremonies; a revocation list lives beside the pubkeys; X4 makes verification fail-closed first.
- Receipt on accept / complete / **refuse** (a desk may refuse out-of-charter or out-of-scope work — refusal is honest degradation, and it is receipted, not silent).
- Correlation: `handoff_id` → orb → task → `work_log.goal_ancestry_json` (the field already exists and already flows — `services/work_log.py`). The ledger gains a `handoff` kind with whitelist fields only (`{from_desk, to_desk, handoff_id, ok, duration_ms}` — the `_ALLOWED_FIELDS` discipline, `activity_ledger.py:30-37`: metadata, never content).
- Task cards, not chat history: the handoff carries exactly the inputs named — the Skeptic's context-drift answer is structural, not aspirational.

**Desk trust table** — the federation peers schema (`reliability / honesty / claws_adherence / competence`, decayed weighted mean, `services/federation.py:54-79, 403+`) instantiated locally, keyed by desk name. Observations: verification pass/fail on handoff results, battery re-runs, refused-vs-botched ratio. The coordinator consults it: a low-trust desk's output gets a verification step appended (qa_gates), or the work is rerouted. Trust is visible in the crew panel — falsifiable, like everything else.

### 6.6 Coordinator: Friday as editor-in-chief

- **Routing:** match task → desk charter; consult the trust table; check the ledger for duplicate in-flight work (same correlation spine) before assigning.
- **Arbitration:** when desks disagree, the coordinator does not average — it surfaces the disagreement with both bylines and both receipts (the Edition's dissent posture, generalized).
- **Synthesis:** user-facing output is coordinator-voiced with inline desk citations backed by receipts — the rationale pattern (`edition_engine._build_rationale`).
- **Liveness:** a scheduler tick sweeps desk tasks; a stalled task escalates (`escalated`, notification) exactly like a failed milestone. Silence is never success.
- The coordinator holds **no elevated authority**: its outward acts hit the same Q3 gates; it cannot seat models, approve routines, or waive anything. Editor, not owner.

### 6.7 UI surface

Byline badges on desk messages (B1 pattern, persisted into history); per-desk orbs with click-through threads (existing orb/correlation machinery — desk orbs carry the desk name in `label` and the ledger joins on `orb_id`); ledger gains a desk filter; a Crew panel: roster, seats with gate chips (green/structural-only/red/ungated — A5 chips reused), charters (edit → version archive), trust sparklines, suspend/retire. Suspension takes effect next-dispatch (2 s settings TTL) and emits the seat-change system line.

### 6.8 Failure modes

| Failure | Mitigation |
|---|---|
| Context drift across desks | Task cards only; partitioned memory; coordinator synthesis from receipts, not from desk chat |
| Duplicated work | Ledger dedup before assignment (correlation ids are exact joins, not heuristics — B3 principle) |
| Blame diffusion | Byline + receipt on every action; single-writer rule for user-facing output; signed handoffs make "never got it / never sent it" checkable |
| Silent desk failure | Liveness tick → escalate; gap notes over filler |
| Coordination tax exceeds value | The falsifiable solo-vs-crew comparison (P5) with kill criterion R1 |
| Silent reseating of a desk's model | seat_transparency generalization + X1 per-dispatch dual-green |
| A desk "improves" itself past its constitution | Per-seat persona eval regression on the constitutional axes; persona contracts shape voice, never values (§2.1, D7) |

### 6.9 Acceptance tests (each can fail)

- An ungated model nominated for a desk seat is refused fail-closed with visible reason; a dual-green model passes; the gemma4 fixture (structural-green, honesty-red) is refused **at dispatch time** after a direct settings.json edit (X1 regression, Incident-2-shaped).
- A handoff with a bad signature, a replayed nonce, or an out-of-scope task card is refused and receipted; with PyNaCl absent, verification returns invalid, not valid (X4 regression).
- Desk A's partition content never surfaces in desk B's context (adversarial: seed a marker fact as A, query as B — must not leak; fail-closed on ambiguity).
- A crew thread renders two desks' contributions with correct bylines; every claim in the coordinator's synthesis resolves to a desk receipt in the ledger.
- A desk task stalled past its time budget escalates within one scheduler tick; no path marks it complete.
- An outward step initiated by any desk (email send) blocks on a Q3 approval card regardless of which desk asked.
- Retiring a desk quarantines its `source_id` partition; its history remains readable in the ledger with its byline.
- Solo-vs-crew golden-task comparison runs and produces a scored report (the R1 instrument exists and can show the crew losing).

---

## 7. G3 — The Second Computer

### 7.1 Design

An always-on **executor** where unattended computer-use and browser work runs gated-but-free, while the real desktop stays supervised-only. Three isolation tiers, used deliberately:

- **S1 — Reading room (Crew-3):** a dedicated browser profile on the host, driven through the Q10 CDP lane (loopback-only, token-gated, on-demand). Light isolation: separate profile, separate cookie jar, no access to Stephen's live browser. Supervised or short-leash unattended (Stephen present, kill hotkey live). This is the first shippable browser lane and it is valuable alone — and it is what Crew-5's Mode-2 teaching and most Career-Scout-style work actually need.
- **S2 — Disposable sandbox:** Windows Sandbox for one-shot untrusted tasks ("open this attachment and tell me what it is"). Ephemeral by construction — state evaporates on close. No always-on role.
- **S3 — The second computer proper (Crew-4):** a persistent Hyper-V guest (Windows 11 Pro host confirmed, virtualization available) with checkpoints, a golden image, and 24/7 residence. Unattended work runs here under the full boundary regime below.

**Ring model:** host Ring 3 stays exactly as it is — per-session grant, never persisted, unreachable from subagents (`services/agent.py:2612-2621`, `services/subagents.py:29`). The executor introduces **Ring 3S**: actuation *inside the executor*, reachable by autonomous paths only through the Q3 approval regime. This is a new privilege concept, not a relaxation — the audit's finding that "no unattended path can drive a mouse today" remains true *of the host* forever.

### 7.2 Why not Grok's way (and why not the cheap way)

Grok Bot's executor is a cloud VM holding the user's live sessions — sovereignty traded for convenience. The cheap local way is a VM bridged to the host that talks to Friday's web API — and the audit shows exactly why that's a hole: `FRIDAY_TRUST_LOOPBACK=1` auto-authenticates loopback callers (`routes/goals.py:28-30`), and `is_private_host` (`egress_gate.py:42-82`) would classify an RFC1918 guest as "local" and hand it the egress-gate bypass. **The executor gets neither loopback trust nor local classification.** It is a separate machine with a treaty, not a trusted extension.

### 7.3 Control plane: the executor is a federation peer

Reuse the one in-tree system built for exactly this shape (`services/federation_transport.py`: X25519 ECDH + ChaCha20-Poly1305 AEAD, Noise-XX-style handshake with forward secrecy, Ed25519 envelope signatures, per-peer rate limiting; `compute_provider`/`compute_client` job lifecycle with capability adverts and cost accounting — `compute_client.py:157` already seals outbound job payloads through the egress gate):

- The executor runs a slim **Friday Executor agent** (not the full server): it advertises capabilities (`browser.navigate`, `browser.act`, `computer.use`, `file.fetch`), accepts signed job envelopes, returns signed results. Its identity is an Ed25519 subkey minted by the host (HKDF, same scheme as desk subkeys) — revocable by deleting one pubkey.
- The host side plugs in at the **`set_executor()` seam** (`goals.py:794-812`): a `RemoteExecutor` satisfying the same `{ok, text, cost_mψ, error}` contract, so goals/milestones/routines run on the executor without the goal subsystem changing.
- The executor **cannot call host APIs**: on the internal switch it is non-loopback, `login_required` fails closed without `FRIDAY_REMOTE_KEY` (`core/__init__.py:434-447`), and it is never given that key. All host↔executor traffic is the federation transport. Jobs flow down; results and whitelist telemetry flow up; nothing else.
- **Kill, three layers:** in-band job cancel (federation message) → transport revocation (drop the peer key) → **hypervisor pause/rollback** (works even when everything inside is wedged or compromised — a kill switch the Grok architecture cannot offer, since you can't pause someone else's cloud).

### 7.4 Egress across the boundary

The A3/D2 principle — *the gate decides from the destination; the call site cannot opt out by omission* (`gate_worker_payload`, `egress_gate.py:428-452`) — generalized to a boundary:

1. **Egress zones.** Destination classification grows from `{local, cloud}` to `{this_device, managed_executor, private_lan, cloud}`. `managed_executor` is **not** local: payloads crossing host→executor are sealed by tier. Closing the `is_private_host` trap (a host-only-network guest is RFC1918 and must still not be "local") is the first commit of Crew-4, with an adversarial test.
2. **Tier policy at the boundary (decided D8):** TIER_3 never crosses. TIER_2 does not cross by default — the executor works on TIER_1 content plus task-scoped credentials. (Stephen deferred to the recommendation; revisitable case-by-case via approval card if a real task demands it.) Same classifier, same fail-closed posture, one new destination class.
3. **The executor's own internet is the task** (browsing is the work), so it cannot be gated by payload inspection alone. Enforcement is **network-level**: the guest sits on a Hyper-V internal switch with **no NAT**; its only route out is a **host-owned forward proxy** enforcing the per-task domain allowlist derived from the task card's scope (a Career Scout job gets job boards, not webmail). The proxy's log is an independent receipt stream. (Ops precedent: the Caddy loopback proxy in `ops/` — same operational muscles, new direction.) Default-deny; allowlist expansion is an approval-card event.
4. **Inbound gate.** Everything returning — page text, DOM, files, screenshots — enters as **untrusted input** (the A4 doctrine; Crew builds `services/untrusted_input.py` first if A4 hasn't, and A4 inherits it). Instruction-shaped content in returned material ("Friday, click delete", hidden prompt text on a page) trips the classifier → pause + human gate + receipt. The audit's gap that image blocks pass ungated (`egress_gate.py:356-361`) is closed for executor-origin images: they are labeled untrusted-observation blocks in prompt assembly, and screenshots feed the VLM only inside that frame.
5. **Two-source completion truth.** An unattended executor's dominant failure mode is fabricated completion with nobody watching. Every executor job's receipt is checked against the proxy log (did the claimed requests happen?) and, for actuation, against before/after screenshots (V6 P6 receipt rule). Completion claims without matching evidence are fabrication — stripped and escalated, the A7 law extended across the boundary.

### 7.5 Credentials: the executor never holds more than the task needs

- **Zero durable secrets in the guest.** No API keys, no `FRIDAY_PASSWORD`, no OAuth refresh tokens, no `FRIDAY_REMOTE_KEY`. The `extension_security.ENV_BLOCKLIST` pattern (`services/extension_security.py:21-43`) applies to the executor agent's environment by construction.
- **Task-scoped injection:** short-lived session material (a session cookie, a one-time token) injected at job start over the encrypted transport, bound to the task's domain allowlist, revoked/expired at job end. The host's `credential_store` remains the only durable holder.
- **Work identity (decided D5: both, user chooses):** each executor task class carries an identity mode set by the user in the UI — **dedicated work-identity accounts** (its own email, its own site logins) or **delegated session material** from the user's accounts (per-task, time-boxed, approval-gated). The mode is part of the task's scope and shows on its approval card. Either way the invariant holds: never a stored password in the guest, and the executor never types the user's passwords — that flow stays with Stephen. Default for a new desk: dedicated identity, switchable per desk/task in settings.
- **Guest OS: Windows 11 (decided D1).** Two verified reasons: `credential_store` falls to plaintext off-Windows (no Keychain/Secret Service — `decisions-2026-08.md:142`), and even ephemeral material deserves DPAPI; and tool parity with the host's actuation stack (pyautogui, window handles).

### 7.6 GPU arithmetic and residency

Measured, not asserted (`phase-a-report.md:267-269`): the brain at optimum holds ~11.6 GB of the 12.28 GB card — **370 MiB free**; brain and sidekick cannot be co-resident; the never-co-resident rule is a hard constraint.

- **The executor is CPU-only and loads no models. Ever.** It is hands, not brain.
- Its cognition — "what's on this page," "which button," "summarize this thread" — routes as requests to the **host** router: local models queue behind the host's residency reality (formal scheduler is Phase C per D8; until then the documented evict/reload discipline), or cloud via the host egress gate per Q12. GPU passthrough to the guest is a non-goal.
- Consequence stated plainly: unattended executor throughput is bounded by the host brain's 25 tok/s and by cloud spend gates. The Economist's ceiling (daily autonomous-spend hard-stop, crew-wide) is load-bearing here.

### 7.7 The pocket surface

Supervision from Stephen's phone — **decided D6: wait for the A4 channel.** No tunnel is stood up for Crew:

- The pocket surface is the Autonomy A4 channel (Telegram pairing, remote `approve <id>` / `STOP` with pre-parse kill precedence) when it lands; Crew consumes it rather than building a second channel layer or a parallel tunnel.
- Until A4 ships, unattended executor work is supervised through the **desktop approval queue only** — which means quiet-hours and approval-expiry behavior matter more, not less: gated actions queue with expiry paused when no one is at the desk (the Autonomy S5 rule), and the executor's leash is correspondingly shorter. This is an honest interim, not a gap: nothing outward proceeds unapproved just because Stephen is away.
- The auth plumbing that anticipates a tunnel (`FRIDAY_REMOTE_KEY`, `core/__init__.py:252-256`, fails closed at `:434-447`) stays as-is, unused — available if the decision is ever revisited.
- Autonomy invariant 12 binds whenever a remote surface exists: the phone can only *request* what a desktop-authenticated owner could request. Reach never widens authority.

### 7.8 Snapshot / rollback

- **Golden image** = patched guest + executor agent + empty work profile, checkpointed. New task classes and any injection trip → **auto-rollback to golden**; receipts record image checkpoint ids before/after so "what state did it run in" is answerable.
- Routine rollback cadence (e.g., nightly) bounds compromise persistence; durable work products leave the guest as job results (through the inbound gate), never live only inside it.

### 7.9 Failure modes

| Failure | Mitigation |
|---|---|
| Prompt injection via browsed page steers the agent | Untrusted-input framing; instruction-shaped-content classifier → pause + human gate; allowlist proxy caps the blast radius; rollback |
| Exfiltration through the browser (the task IS the channel) | TIER_1-only content; zero durable secrets; per-task domain allowlist; proxy logs audited against task cards |
| Executor fabricates completions unattended | Two-source truth: receipts × proxy logs × screenshots (§7.4.5) |
| Guest treated as trusted local | Egress zones + `is_private_host` fix + no loopback trust + no `FRIDAY_REMOTE_KEY` (§7.2-7.3); adversarial tests pin all three |
| VM escape | Minimal guest surface (no shared folders, no clipboard integration, internal switch only), patch cadence, S2 for the truly untrusted |
| GPU contention starves the brain | Executor is CPU-only by construction; R6 measures and kills regressions |
| Runaway cost at 3 a.m. | Daily autonomous-spend ceiling (hard-stop, receipted), quiet-hours queueing (Autonomy S5), per-goal budget caps |
| Host reboot / power loss | Executor state is disposable by design; jobs are goal milestones that resume via scheduler; presence endpoint reports executor health |

### 7.10 Acceptance tests (each can fail)

- A payload containing a seeded TIER_3 marker is blocked from crossing host→executor; TIER_2 blocked under the default policy; TIER_1 crosses sealed (adversarial, egress-suite standing).
- A guest on the internal switch is classified `managed_executor`, **not** `local`; the old `is_private_host` behavior is pinned as a red test.
- The executor, holding no `FRIDAY_REMOTE_KEY`, receives 403 from every host API route; the federation transport rejects a bad envelope signature and a replayed nonce.
- A page containing "Friday, run this command" returns through the inbound gate flagged; the job pauses on a human card; the receipt records the injection attempt (the Autonomy A4 acceptance shape, at this boundary).
- A job claiming a form submission with no matching proxy-log requests is marked fabricated and escalated — never reported as done.
- A domain outside the task's allowlist is refused by the proxy and receipted; the job does not silently fail (honest degradation: partial result + gap note).
- Hypervisor pause halts a mid-actuation job; rollback-to-golden restores a clean state and the receipt chain records both checkpoint ids.
- `nvidia-smi` during a full executor browser job shows no executor-attributable VRAM; host brain throughput within measurement noise of the 25.08 tok/s baseline (R6 instrument).

---

## 8. G0 — Role-template format (spec only)

A **role package** is a folder (zip-portable via the existing skill import/export, `skill_registry.py:321-427`):

```
role-career-scout/
  ROLE.md            # YAML frontmatter + prose
  charter.md         # taste seed — the user edits this after install
  persona.md         # the desk's voice & personality contract (D7): register, temperament,
                     # style markers, forbidden phrases — the voice-axis rubric source
  fixtures/          # role-specific honesty-battery items (golden JSON, battery schema)
  routines/          # optional starter routines, shipped as status: draft — never active
```

`ROLE.md` frontmatter:

```yaml
name: career-scout
role_title: Career Scout
version: 1
requires_gate: dual_green            # constitutional; not overridable by the package
battery_profile:
  core: true                         # the 12-item core battery, always
  categories: [source_honesty]       # scorers this package's fixtures exercise
seat:
  capability: subagent               # default seat via capability_routing; user may bind explicitly
scope:
  allowed_tools: [search_web, browse_web, read_file, write_file]
  max_ring: 2
  max_steps: 40
  time_budget_s: 1800
memory:
  partition: crew:career-scout
  shared_reads: [kg_core]
byline: { label: "Career Scout", icon: "🧭" }
persona: persona.md                  # voice-axis rubric source (D7); user-editable post-install
```

Rules: a package **cannot** grant itself Ring 3, name a seat model directly as trusted, ship `active` routines, or waive any constitutional rule — installation registers the desk `suspended` until its seat passes core + role batteries and Stephen activates it (one approval card). A package's `persona.md` shapes voice only; the constitutional persona axes are hardcoded in the eval, not read from the package. Unknown frontmatter rides in `meta` (registry convention), so the format versions forward. No marketplace, no federation distribution — out of scope (§10).

---

## 9. Phasing

Each phase independently shippable and valuable; each later phase consumes earlier artifacts unchanged. Names avoid collision with the audit's Phase A/B/C.

| Phase | Deliverable | Size (honest) | Depends on |
|---|---|---|---|
| **Crew-0 — Gates that don't lie** | X1 per-dispatch dual-green; X2 skill `status` + injection filter + `learn_skill` writes drafts; X3 auth on chain routes; X4 fail-closed verification. Plus: demonstration capture (`begin_demo`/post-hook/store) shipping a reviewable transcript. | **A weekend.** Four surgical fixes with red tests + one post-hook and a store. This is the honest weekend phase: alone, it closes Incident-2-class holes and makes "Friday, watch this" real. | — |
| **Crew-1 — Teach v1** | Distiller with citation gate; draft SKILL.md folders; dry-run harness; gated activation through approvals; scoped chain execution; run receipts; Routines panel. | ~2 build sessions | Crew-0 |
| **Crew-2 — The Crew v1** | Crew registry + 2 desks + coordinator; role charters; partitions (`source_id` + Chroma metadata); signed handoffs (subkeys, X4-hardened); desk trust table; bylines/orbs/ledger/Crew panel; brain battery run; solo-vs-crew comparison instrument. | ~3 build sessions | Crew-0 (X1, X4) |
| **Crew-3 — Reading room** | Dedicated browser profile + CDP lane (Q10) + session vault for the work identity; browser tasks become teachable (Mode 1) and delegable to desks; supervised/short-leash only. | ~2 build sessions | Crew-1 useful, not required |
| **Crew-4 — The second computer** | Hyper-V golden image + executor agent; federation-peer control plane at the `set_executor()` seam; egress zones + `is_private_host` fix; policy proxy; task-scoped credentials (identity mode per D5); inbound untrusted gate; snapshots/rollback; unattended under Q3 + the D12 user-set spend ceiling; supervision via the desktop approval queue until A4's channel lands (D6). | The big one — ~4 sessions, sequenced behind its own adversarial suite | Crew-2 (desks to delegate), Crew-3 (browser semantics), approvals/goals (built) |
| **Crew-5 — Screen teach + scale-out** | Mode-2 demonstration (observe-tier grants, local whisper narration, VLM-through-egress-gate distillation); role packages shipped for the standing desks; more desks by template. | after actuation permission tiers (Autonomy A5) exist | Crew-1, A5 tiers |

Persona-regression rule (V6 §5) binds every phase: A1 fixture eval green before a phase is accepted. Ordering with the Autonomy build: Crew-0/1/2 need nothing from A4/A5; Crew-4's pocket approvals converge with A4's channel work whenever it lands; Crew-5 waits for A5's permission tiers by design.

---

## 10. Non-goals

- **Multi-user / principals (V6 P9).** Desk partitions are agent-scoped only; Q1/Q2 stay Stephen's to decide. No accidental second-user path.
- **Cloud-hosted executors.** The Grok model — credentials on someone else's VM — is the thing this spec exists to refuse.
- **Marketplace / federation distribution of roles or routines.** Frozen with the rest of the federation-as-product surface (V6 §1.1). The role-package *format* is spec'd (§8); distribution is not.
- **GPU passthrough or in-guest models.** 370 MiB says no.
- **A second approval queue, a second eval harness, a second channel layer, a second trust store.** Every one of these exists; Crew extends, never duplicates.
- **Separate crew souls/values.** Desks have distinct voices and personalities (D7), but persona is taste: no desk gets its own values, its own cLaws, or an exemption from any constitutional rule. Characters, not forks.
- **Local-VLM grounding claims.** Q12's honesty stands: cloud-VLM default through the gate, local labeled best-effort.
- **Replacing solo Friday.** The crew is summoned or scheduled; plain chat stays plain.

---

## 11. Risk register with kill criteria

| # | Risk | Signal | Kill criterion |
|---|---|---|---|
| R1 | Crew is worse than solo Friday (coordination tax, drift) | Solo-vs-crew golden-task comparison (built in Crew-2) | Crew below solo on quality/coverage 2 weeks after Crew-2 ships → collapse to solo + single-desk delegation; revisit design before any Crew-4 spend |
| R2 | Injection-driven compromise of the executor | Inbound-gate trips; proxy anomalies; canary tokens in guest | Any confirmed exfiltration past the policy proxy → executor offline; Crew-4 halts until the boundary redesign passes its adversarial suite again |
| R3 | Credential material lands in the guest | Scheduled guest audit (image diff vs golden; secret scan) | Any durable secret found in-guest → halt unattended mode; rotate; redesign injection path before resume |
| R4 | Demonstration transcripts leak sensitive data to cloud | Egress adversarial tests on the distiller path | A TIER_2/3 demo fragment in any cloud payload → distiller forced local-only until re-verified |
| R5 | Receipts/bylines erode (actions without attribution) | Ledger audit: crew actions lacking byline+receipt | Any unattributed crew action = release blocker for that phase; no exceptions — this is the moat |
| R6 | Executor degrades the brain (GPU/RAM contention) | Throughput check vs 25.08 tok/s baseline during executor load | >10% sustained regression attributable to the executor → executor confined to hours the brain is idle until fixed |
| R7 | Always-on cost runaway | Daily spend ledger vs ceiling | Ceiling is a hard-stop by construction; two ceiling-hits in a week → unattended scope reduced, not ceiling raised |
| R8 | Complexity rot (features nobody can hold) | Each phase ships behind its own settings flag with a clean off-state | Any Crew feature whose off-switch no longer restores pre-Crew behavior = stop-ship for that phase |
| R9 | Teach produces confidently-wrong routines | Dry-run pass rate; first-run failure rate on activated routines | If >1 in 5 activated routines fails its first live run in a month, activation gains a mandatory supervised first run |
| R10 | The dual gate becomes a rubber stamp (batteries memorized/overfit) | Battery items are versioned; periodic novel-item refresh | A model passing the battery while failing a live honesty incident → battery gains the incident as a fixture (the F1 tradition) before that model is reseated |

---

## 12. Decision record — accepted by Stephen, 2026-08-14

Originally posed as questions; answered same day. Downstream work inherits these from this file, not from chat history (the `decisions-2026-08.md` convention).

| # | Question | Decision | Binds |
|---|---|---|---|
| **D1** | Executor guest OS | **Windows 11 guest.** DPAPI exists; tool parity with the host actuation stack. | §7.5, Crew-4 |
| **D2** | Isolation path | **Reading room (Crew-3) before the Hyper-V executor (Crew-4)**, as phased. | §9 |
| **D3** | First desks | **Chief of Staff + Career Scout** for Crew-2. | §6, §9 |
| **D4** | Brain seating | **Yes** — run the honesty battery on the llama-cpp brain (`qwen3.6-35b`); it may hold desk seats if dual-green. Crew-2 precondition. | §6.3 |
| **D5** | Work identity | **Both modes, user-chosen.** Per desk/task the user selects dedicated work-identity accounts *or* delegated per-task session material; the mode is part of the task scope and its approval card. Default for a new desk: dedicated. | §7.5 |
| **D6** | Pocket surface | **Wait for the A4 channel.** No tunnel for Crew; desktop approval queue (with expiry-paused queuing) is the interim supervision surface. | §7.7, Crew-4 |
| **D7** | Desk voice | **Distinct voices and personalities per desk.** Persona is taste (user-shaped, packaged as `persona.md`, editable); values/honesty/receipts are constitutional and identical for every desk. Persona eval splits into constitutional axes (Friday-wide, red-gate) and voice axes (per-desk contract). This affirms the §2 divergence from V6 invariant 1's phrasing: one constitution, many characters. | §2, §6.1, §6.3, §8 |
| **D8** | TIER_2 at the boundary | **Never crosses by default** (Stephen deferred to the recommendation). Case-by-case sealed crossing remains possible via explicit approval card; revisit if real tasks demand it. | §7.4 |
| **D9** | Mode-2 capture consent | **Approved:** screen observation only during an explicitly-opened demo session, per-app observe grants, indicator always visible. | §5.1, Crew-5 |
| **D10** | Learning-loop promotions | **Receipted with notifications** (the Autonomy A7 shape): promotions/retirements write signed receipts and surface as notify-tier cards; no longer fully silent. | §3.1 note, Crew-1 or A7 |
| **D11** | Retired desks | **"We never delete anything."** Quarantine is the only retirement verb; Crew ships no deletion path for desk memory; ledger history stays readable forever. | §6.4 |
| **D12** | Unattended spend ceiling | **User-set via a UI option** (settings, editable like any budget). Ships with a conservative default so the ceiling exists from first boot — an unset ceiling must never mean an unbounded one; the default value is picked at Crew-4 build time and named in its report. | §7.6, R7 |

---

*End of specification.*
