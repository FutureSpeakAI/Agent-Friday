# Owner rules in plain English, and anomaly detection for outward actions

> **Status:** proposed (spec only; nothing in this document is built)
> **Last verified:** 2026-09-26 against main `edca4b91`
> **Implementation:** none yet. Builds on `governance/action_gate.py`, `services/agent.py` (pre-tool hook chain), `services/tool_hooks.py`, `services/laya_backend.py`, `services/decisions.py`, `services/local_call.py`, `services/local_only_guard.py`, `services/approvals.py`, `services/approval_executor.py`, `services/taint.py`, `services/egress_gate.py`, `services/turn_budget.py`, `services/spend_guard.py`, `governance/proof_of_integrity.py`
> **Supersedes / superseded by:** neither. A companion to [`goals-and-delivery-receipts.md`](goals-and-delivery-receipts.md), whose run receipts carry the rule verdicts this spec produces
> **Written:** 2026-09-26

This adapts three ideas from Google's "Build zero-trust AI agents that judge
intent, not just syntax" (Google for Developers blog, Sept. 15, 2026, part 2 of
the "Zero-trust Agents" series):

- **Semantic Governance Policies.** Plain-text constraints, evaluated by an
  LLM-based policy engine against each proposed tool call, the user's prompt and
  the conversation history. Each evaluation is logged with a rationale.
- **Agent Anomaly Detection.** The signals are tool-call velocity, repeated writes
  against one entity, and cumulative parameter values. The article's premise is
  that single-turn guardrails "cannot see cumulative drainage or multi-turn
  velocity".
- **Closed-loop remediation.** A finding leads to a new natural-language policy.
  In the article an administrator authors it, or it is created automatically
  through an API.

Friday takes the ideas, not the product. The evaluator is local and private. The
owner writes every rule. Nothing is ever applied automatically, and the one place
Friday deliberately differs from the article is that a finding never becomes a
rule without the owner's approval.

Code citations are `path:line` on main `edca4b91`. Paths are relative to
`src/agent_friday/` unless they start with `index.html`, `docs/` or `tests/`.

---

## 0. Summary for the owner (one page)

**What changes for you.**

1. **You can give Friday your own rules, in your own words.** Some examples:
   - "Never email billing@example.com."
   - "Ask me twice before any purchase over $50."
   - "Nothing goes on my calendar on a weekend I've marked 'away'."

   Before a rule takes effect, Friday shows you how she understood it: which kinds
   of action it covers, and whether it blocks the action or asks you first. Rules
   are signed on this PC like Friday's built-in laws, so nothing can change them
   quietly.

   Every action Friday is about to take is checked against your rules first:
   - in chat and in background tasks;
   - by voice;
   - when an approved card is carried out.

   When a rule applies, the card says which rule and why, and so does the signed
   record of the action.
2. **Friday watches for patterns that each look fine on their own.** For example:
   - many messages to one person in a short time;
   - the same calendar event changed again and again;
   - many emails trashed at once;
   - spending that adds up;
   - a trickle over several days that no single action would reveal.

   These are compared with your own normal history, not with a number built into
   Friday.
3. **When something looks off, Friday stops and asks.** She pauses that kind of
   action, not everything, and tells you what she saw with the list of actions.
   She then suggests a rule that would have caught it, for example "Ask me before
   sending more than 5 messages to one person in a day".

   You approve it, edit it or decline it. She never adds a rule by herself.

**What it does not change.**
- Rules and pattern checks can only add caution: a question or a block. They can
  never remove a card Friday would otherwise ask you for.
- Your rules are limits you set, which is the only kind of limit Friday has.
- The pattern checks work like the existing "going in circles" check:
  - they react to a shape, not to an amount;
  - they pause and ask, and never stop work silently;
  - each one can be switched off in the Costs settings, next to that check.

**What it costs.**
- **Privacy.** Rules are checked only on this PC, never by a cloud model, because
  checking them means reading your conversation history.
  - A simple rule ("never email X") is a direct comparison, with no model at all.
  - A rule that needs judgement is answered first by Laya, Friday's small typed
  classifier on the processor (about 0.4 s).
  - Harder cases go to your local Bonsai2 model (a few seconds, only for outward
    actions a rule might cover).
  - If no local model can answer, Friday asks you instead of guessing.
- **Money.** Nothing. No cloud calls are added.
- **Speed.** Most actions are not touched. An outward action covered by a rule
  that needs judgement waits well under a second, or a few seconds when Bonsai2
  is asked.
- **Effort.** About six focused weeks, in five phases. The first phase, a private
  log of outward actions, also fixes today's blind spot: Friday keeps no record of
  who she messaged across days, except in the email log (§2.5).

**What to decide.**
1. Should the pattern checks be on by default, as the loop guard is? The
   recommendation is yes, because they only pause and ask.
2. How long to keep the private action log. The recommendation is 90 days, which
   you can change.

---

## 1. The source, and what Friday takes from it

**What the article describes.**
- **Model Armor.** An in-line filter on prompts and responses (for jailbreaks,
  malicious URLs and data leakage).
- **Semantic Governance Policies.** A "natural-language policy engine" at the
  agent's gateway.
  - It evaluates "the tool and the proposed parameters against the user prompt,
    the conversation history, and your policies".
  - It returns an enforcement verdict: its example policy blocks refunds over
    30 USD for digital goods.
  - It logs each evaluation with a rationale, the tool name and the verdict.
- **Agent Anomaly Detection.** Statistical and LLM analysis of session telemetry,
  keyed on:
  - tool-call velocity;
  - repeated writes against one entity;
  - cumulative parameter values (the demo compares cumulative refunds with the
    original order total).
- **Closed-loop remediation.** A security finding leads to a new natural-language
  policy, authored by an administrator or created programmatically, and enforced
  on the next tool call with no redeploy.

**What Friday changes.**

| Article | Friday | Why |
|---|---|---|
| A managed cloud policy engine | A local evaluator only: deterministic predicates, then Laya, then Bonsai2 | The history the evaluator reads is private. `local_only_guard` makes a cloud call impossible here (`services/local_only_guard.py:61`, `:113`) |
| BLOCK or ALLOW | `block`, `card` or `ask_twice`, **never allow**. A rule can add caution and never remove it | The union rule Friday already uses for Laya (`services/laya_backend.py:592`, `union_backend`, "can add approval cards, never remove one") |
| Policies authored by an admin or by an API | Authored by the owner only. Proposals from remediation are cards the owner approves, edits or declines | An automatic policy writer is a new, injectable way to change governance. The owner is the only author of rules |
| Fleet-wide anomaly baselines | One machine's own history, kept encrypted | Friday has one owner. Their normal is the baseline |
| Findings to a security console | A paused action class, a plain explanation, and a proposed rule | The owner is the security team |

---

## 2. What exists today (grounded)

### 2.1 The checkpoint every tool call passes

- **`_execute_tool`** (`services/agent.py:8486`) runs the pre-hook chain before
  any handler. `run_pre_hooks` (`services/tool_hooks.py:187`) returns the first
  DENY and treats a raising critical hook as a deny.
- **Registered hooks** (`services/agent.py:9183` onward):

  | Hook | Priority | Critical |
  |---|---|---|
  | `governance_rings` | 1 | yes |
  | `confirmation_gate` | 10 | yes |
  | `vault_zt` | 25 | yes |
  | `sandbox_policy` | 30 | no |
  | `rate_limiter` | 40 | no |

- **Every surface enters here.**
  - Chat and background tasks, through the agent loops.
  - Voice, through `_governed` (`services/voice_engine.py:960`).
  - The approval executor, through `_run_tool` (`services/approval_executor.py:75`).
- **`_hook_governance`** (`services/agent.py:8758`) does three things, in order:
  1. It lets an approved-card execution through first (`:8791`). This is the
     executor's path, and it comes before taint and `authorize`.
  2. It calls `taint.evaluate`.
  3. It calls `action_gate.authorize` (`governance/action_gate.py:646`), whose
     `_decide` (`:690`) returns allow, confirm, card or deny.
- **A card** is raised by `_taint_card` (`services/agent.py:8911`), as
  `kind="tainted_action"`, which the approval executor runs exactly once.
- **Every decision gets a signed receipt**, an HMAC line in
  `~/.friday/decision-bom.jsonl` (`governance/action_gate.py:581`). It stores
  `args_hash`, not the arguments.

**Consequence for placement.** A rule check inside `authorize()` would miss
executor runs, because the approved-card short-circuit at
`services/agent.py:8791` comes first. The rule check must be its own pre-hook
(§4.4).

### 2.2 cLaws: the pattern for signed owner governance

- `CLAWS_TEXT` is defined at `governance/proof_of_integrity.py:42`.
- It is pinned under the governance key (`governance/proof_of_integrity.py:405`,
  `get_governance_key`, in the OS keyring). `verify_claws` is at
  `governance/action_gate.py:77`.
- A mismatch holds every outward action.
- Re-pinning is owner-only, through a confirmed route that writes a receipt
  (`routes/owner_security.py:90`). It is shown in `ClawsRepinPanel`
  (`index.html:45193`), mounted in `SettingsTabPrivacy` (`index.html:45489`).
- Owner rules use exactly this pattern.

### 2.3 Local judgement that already exists

- **Laya.** A ModernBERT typed-decision encoder on CPU
  (`services/laya_backend.py:1-48`).
  - It answers choice questions with per-option probabilities, through
    `decisions.decide` (`services/decisions.py:327`).
  - It is measured at about 400 ms mean, 604 ms max, with a 46–70 s load
    (`docs/decisions/2026-09-22-laya-union-gate.md:52-56`).
  - It already runs as a union gate that can only escalate
    (`services/laya_backend.py:592`, modes at `:678`).
- **Bonsai2.**
  - Reached through `local_call.call` (`services/local_call.py:253`) on the seat
    `scheduler._resolve_local_seat` finds (`services/scheduler.py:107`).
  - `_evaluate_output` (`services/agent.py:3369`) is the existing local-only
    judgement call.
  - The 131K q4_0 seat measured 490–511 tok/s prefill
    (`docs/reference/long-running-tasks.md`).

### 2.4 Detection that already exists

- **The loop guard** (`services/turn_budget.py:260`) fires on shape, not amount:
  - identical calls, three by default (`:99`);
  - cycling patterns with a novelty gate.
- **Its limits.** It is per turn and in memory. It is instantiated inside each
  loop call, and nothing persists across turns or days. It is on unless the owner
  turns it off (`services/turn_budget.py:247`; switch at `index.html:49112`).
- **The spending cap** (`services/spend_guard.py:151`) covers model spend only.
  No dollar amount of an outward action is recorded anywhere.
- **The only rate caps on outward actions** limit texts to the owner's own phone
  (`phone/service.py:193`).
- **The only weekday concept** is the scheduler's suggestion filter,
  `working_days` (`services/scheduling.py:51`). `create_event`
  (`services/calendar_write.py:387`) has no such check.

### 2.5 Records an anomaly detector could read, and their gaps

| Record | Has entity / recipient? | Notes |
|---|---|---|
| Decision BOM (`governance/action_gate.py:581`) | No, only `args_hash`; external targets are a hashed recipient count | Signed, plaintext, no retention |
| Activity ledger | No arguments | Plaintext metadata |
| Task journal `tool_call` | Arguments, tier-redacted | Encrypted, but background tasks only; chat turns are not journaled |
| `sent_mail.jsonl` (`services/gmail_send.py:755`) | Full recipients, plaintext | Email only |
| Phone ledger (`phone/spool.py:66`) | Full E.164 number, plaintext | Phone only |
| Calendar writes | None | No log of their own |

There is no single record of "who and what Friday acted on, over days". Phase 0
adds one (§5.1).

### 2.6 Taint, egress and voice

- **Taint.** `taint.evaluate` (`services/taint.py:766`) labels each sensitive
  argument by origin (user, content, sender, own or model), under `POLICY`
  (`:437`), and a flagged call becomes a card. Its ledgers are in memory
  (6 hours, 200 entries per key), plus carried ledger and summary text.
- **Egress.** `seal_outbound` (`services/egress_gate.py:1513`) and `gate_text`
  (`:1406`) scrub what leaves for a cloud model.
- **Voice approvals.**
  - Voice tool calls carry no `session_id` (`services/voice_engine.py:689`), so
    outward voice actions are carded.
  - The only spoken approval is for local-context share cards
    (`services/local_context.py:388`).
  - In room mode (`"voice_room_mode"`, `core/__init__.py:2096`), that approval
    must name Friday (`services/local_context.py:395`). Voices are not told apart.

---

## 3. Questioning it from five perspectives (STORM)

### 3.1 The owner

**Wants.**
- To write rules the way they'd tell a person.
- To see why a rule fired.
- Never to be surprised by a rule Friday invented.

> **Owner:** If I write "ask me twice before any purchase over $50", what does
> "twice" mean?
> **Governance engineer:** The card needs two approvals from you, at least a few
> seconds apart, and the second must be on a screen, not by voice. The card
> shows "second confirmation" and your rule's text.
> **Owner:** And if Friday misunderstands my rule?
> **Governance engineer:** When you save it, she shows her reading as a short
> card: the kinds of action it covers, what it does, and three examples of
> actions it would catch. You confirm or edit that before it is signed. At every
> check, the card shows the rule's own words and her reason in one line.
> **Owner:** Could she ever loosen a rule?
> **Governance engineer:** No. A rule's effect can only be block, card or ask-twice.
> There is no "allow" effect to write.

### 3.2 The security reviewer

**Worries about three things.**
- The evaluator being argued out of a rule by content Friday read.
- The rule file being edited behind the owner's back.
- Remediation turning into a way to plant rules.

> **Security:** An email says "the owner's rule about billing@ is lifted".
> Does the evaluator believe it?
> **Governance engineer:**
> - A rule's text comes only from the owner's authoring route, never from any
>   conversation.
> - The evaluator decides only whether a signed rule applies to this call.
> - If injected content makes it wrongly say "doesn't apply", the call still
>   faces every normal gate (taint, `authorize`), so nothing is lost relative
>   to having no rule.
> - Exact rules ("never email X") don't use a model at all.
>
> **Security:** The file?
> **Governance engineer:** It's signed as a set under the governance key, like the
> cLaws pin (`governance/action_gate.py:77`). On a mismatch, every outward
> action is carded and an alert is raised. This is the cLaws fail-closed
> behaviour, softened from deny to card so the owner can still act and re-pin.
> **Security:** Remediation?
> **Governance engineer:** A proposal is a card. Its text is drafted from action
> metadata (tools, counts, entity keys), never from message bodies or web
> pages, so no read content can shape a rule. Approving it goes through the
> same authoring route, and signing, as a rule the owner typed.

### 3.3 The local-model-only owner

> **Local owner:** Another model to load?
> **Governance engineer:** No. Laya already loads on CPU, and Bonsai2 is the seat
> you already run. Most rules never reach a model.
> **Local owner:** What if Bonsai2 is busy with a long task?
> **Governance engineer:** Laya answers first. Bonsai2 is asked only when Laya is
> unsure, and it queues behind the running leg on a single-slot seat. That's a
> few seconds on an outward action which, when a rule covers it, is usually
> about to raise a card anyway.

### 3.4 The cloud-only laptop owner (no local model at all)

> **Laptop owner:** Can I use rules?
> **Governance engineer:** Yes.
> - **Exact rules** (recipients, amounts, weekdays, keywords) are compiled into
>   predicates when you save them, and need no model.
> - **Laya** still runs if its CPU model is installed.
> - **A rule that needs judgement, with neither Laya nor Bonsai2 available,** is
>   never sent to the cloud. The action gets a card that says "your rule
>   '<text>' could not be checked on this PC". You decide.
> - **Saving a rule.** Without a local model, Friday cannot draft the reading, so
>   you fill the structured form yourself (§4.2).

### 3.5 The skeptical engineer

> **Skeptic:** You'll add seconds to every tool call.
> **Synthesis:** No. The hook first matches the call's action class against each
> rule's compiled scope, which is a set lookup. Only outward calls that a rule
> covers reach a predicate, and only semantic rules reach Laya or Bonsai2.
> Results are cached per (rule, call fingerprint) for the turn.
> **Skeptic:** Anomaly baselines on one person's history are noise.
> **Synthesis:**
> - Two tiers. **Shape detectors** work from day one: the same entity written N
>   times in M minutes, or one call that trashes K items. They mirror the loop
>   guard's repeat rule.
> - **Baseline detectors** (velocity per recipient, cumulative amount, slow
>   drip) stay silent until there are 14 days of history. They use a robust
>   baseline (median plus MAD) and fire on a clear departure.
> - Every firing is a pause and a question, never a silent stop. The owner can
>   switch each detector off.
>
> **Skeptic:** Isn't a pause a built-in cap?
> **Synthesis:** It is a guard of the kind the no-caps policy keeps: it fires on
> a shape and is visible, like the loop guard (`services/turn_budget.py:260`).
> Owner rules are limits the owner set, which the policy explicitly allows
> (`services/turn_budget.py:49-64`).

---

## 4. Design 1: owner rules

### 4.1 A rule

```
{
  "rule_id": "r_<ulid>", "v": 1,
  "text": "Ask me twice before any purchase over $50.",     // the owner's words
  "effect": "block" | "card" | "ask_twice",                 // never "allow"
  "scope": {"classes": ["spend"], "tools": ["..."]},        // compiled, confirmed
  "predicate": {"all": [{"field": "amount_usd", "op": ">", "value": 50}]}
             | null,                                        // exact rules only
  "semantic": false | true,        // needs judgement beyond the predicate
  "examples": ["...", "...", "..."],                        // shown at authoring
  "status": "active" | "paused",
  "created": "...", "author": "owner", "source": "typed" | "remediation:<anomaly id>"
}
```

**The rule set on disk.** Rules are stored together in
`~/.friday/governance/owner_rules.json`. The set carries one `set_hmac`: an HMAC
under the governance key (`governance/proof_of_integrity.py:405`) over the
canonical JSON of all rules. This is how `claws_hmac` signs `CLAWS_TEXT`.

**Checking the signature.** It is verified on every evaluation, and the result
is cached against the file's mtime and size.
- On a mismatch, every outward action gets a card with "your rules could not be
  verified", and an alert is raised.
- Re-signing is owner-only, through the same route pattern as
  `/api/governance/claws/repin` (`routes/owner_security.py:90`), with a receipt.

**Classes.** These reuse the policy classes Friday already computes:
- `spend`, `irreversible`, `external_message` and `outward`
  (`services/approvals.py:140-152`);
- plus `calendar_write` and `delete`, from the tool sets in
  `governance/action_gate.py`.

### 4.2 Authoring (the owner confirms Friday's reading)

1. The owner types a rule, in the Rules panel or in chat (`/rule never email
   billing@example.com`).
2. Friday drafts the structured reading **locally**: Bonsai2 under `local_only`,
   with a short schema-bound prompt.
   - Exact phrasings are compiled by a deterministic parser first: an email
     address or name to recipients, `$<n>` to amount, weekday names, "every",
     "twice".
   - Without a local model, the owner fills the form: effect, classes and the
     optional predicate fields.
3. The panel shows the reading: effect, the kinds of action covered, and three
   example calls it would catch, drawn from the owner's own recent outward-action
   log (§5.1). The owner confirms or edits.
4. On confirmation, the set is re-signed and a governance receipt is written.

A rule proposed by remediation (§6) enters at step 3, as a card. No other path
can create or change a rule. The route refuses any request that is not an owner
UI session on this PC, the same `_refusal()` check the cLaws re-pin uses.

### 4.3 Evaluation

**Inputs**, for the calls whose class is in a rule's scope:
- the tool name and arguments;
- the owner's intent: the owner's latest words, which voice already carries as
  `owner_text` (`services/voice_engine.py:689`), or the chat turn's user message;
- the recent conversation history (the last 8 turns, bounded);
- the taint flags for the arguments (`services/taint.py:766`);
- for rules that need them, the owner's own calendar or contacts, read through
  the existing read-only tools.

**Order**, stopping at the first definite answer:
1. **Predicate**, for rules with a `predicate`. The answer is exact: applies or
   doesn't.
   - A recipient compare normalises addresses: lowercased, display names
     stripped. It also resolves names through `people_graph`
     (`people_graph.py:58`) aliases and emails.
   - Amounts are parsed from `amount`, `price`, `total` and `usd` fields.
   - Weekdays are computed in the settings timezone, as the scheduler does.
2. **Laya**, for `semantic` rules. It is asked one choice question per rule:
   "Does this proposed action fall under the rule '<text>'?", answered
   yes / no / unsure, through `decisions.decide` with a new question id
   `owner_rule_applies`.
   - `yes` or `no` at confidence ≥ 0.8 is final.
   - Otherwise the question goes to step 3.
   - Laya's 2.5 s scoring timeout (`services/laya_backend.py:123`) applies.
3. **Bonsai2**, for what Laya left unsure. One `local_call.call`
   (`services/local_call.py:253`) under `local_only("your rules")`, with
   `json_mode`, returning `{"applies": "yes|no|unsure", "rationale": "<= 300 chars"}`.
   It has a 20 s timeout.
4. **Nothing could answer** (no Laya, no Bonsai2, Bonsai2 timed out, or the
   answer was `unsure`): the rule is treated as applying, with effect `card`
   whatever its effect, and the rationale "your rule could not be checked here:
   <why>". Unknown is never allow, and never a surprise block.

**Privacy invariant.** Every step runs on this PC. Steps 2 and 3 run inside
`local_only_guard.local_only`, so any cloud transport raises `CloudRefused`
(`services/local_only_guard.py:113`), including Ollama `-cloud` tags (`:102`).

**Verdict.**
```
{"rule_id": "...", "applies": true, "effect": "block|card|ask_twice",
 "via": "predicate|laya|bonsai|unavailable", "confidence": 0.93,
 "rationale": "billing@example.com is the recipient your rule names.",
 "elapsed_ms": 412}
```

### 4.4 Where it runs: a new pre-hook

- **The hook.** A new critical pre-hook, `owner_rules`, at priority 0: before
  `governance_rings` (1), and registered beside it (`services/agent.py:9183`).
- **What it decides.**
  - `block` → DENY, with `[BLOCKED BY YOUR RULE] '<rule text>': <rationale>`.
    This is a deny sentinel, so the one tool-result classifier (companion spec
    §5.4) records it as `deny`.
  - `card` → no deny. It sets `ctx.meta["owner_rule_card"] = verdicts`, and
    `_hook_governance` treats that exactly like a taint "ask": it forces `card`
    in `authorize` (`governance/action_gate.py:690`), and the one card raised by
    `_taint_card` (`services/agent.py:8911`) carries the rule verdicts. One card
    per action, however many reasons.
  - `ask_twice` → as `card`, and the card is created with
    `confirmations_required: 2` (§4.5).
- **Why before governance.** A block should never first raise a card the owner
  could approve pointlessly. Priority 0 guarantees the rule is seen first.
- **Approval-executor runs.** These arrive with
  `session_ctx["approved_card"]`, and the hook re-evaluates them.
  - A `block` rule written after the card was raised still blocks the run. The
    executor posts "blocked by your rule '<text>'" back to the conversation
    (`services/approval_executor.py:128`).
  - A `card` or `ask_twice` rule whose card is this approved card, with its
    confirmations met, passes.
- **Union.** The hook can only return DENY or add a card flag. It cannot turn a
  card or deny from a later hook into an allow. The hook registry's own contract
  already says added hooks can only tighten (`services/tool_hooks.py:26-32`).

### 4.5 "Ask twice"

- **The card field.** `approvals.create_approval` (`services/approvals.py:307`)
  gains `confirmations_required` (default 1).
- **Decisions.** `decide` (`:503`) records each approval in `confirmations[]`,
  with decider, surface and time.
  - The card stays `pending` until the count is met.
  - The second approval must come ≥ 5 s after the first, and from a screen
    surface (not voice).
  - A deny at any point is final.
- **Execution.** `claim_for_execution` (`:409`) is unchanged: it only ever sees a
  fully approved card, so exactly-once holds.

### 4.6 The rationale, on the card and in the receipt

- **On the card**, a "Why" section: "Your rule: '<text>' → <rationale>
  (<via>)".
- **In the decision BOM** (`governance/action_gate.py:581`), which is plaintext:
  only `rule_ids`, `effects` and a `rationale_sha256`. The rationale can quote
  private history, so it never goes there in plain text.
- **In the delivery receipt** (companion spec §5.2): the full rationale, under
  `actions[].rules`, in the encrypted body. Verdicts are therefore signed twice:
  once in the BOM line, and once in the receipt chain.

---

## 5. Design 2: anomaly detection across turns and days

### 5.1 The outward-action log (Phase 0)

**What it is.** A new, local, encrypted append-only log,
`~/.friday/governance/outward_log.jsonl`. It is written by a post-hook on every
outward-class call, whether it was allowed, carded or denied, and by
`record_external` / `authorize_external` for executors outside tool calls
(`governance/action_gate.py:741`).

**Each line** (the body is encrypted with `credential_store.protect`, as the
task journal's is):
```
{"ts": ..., "tool": "...", "class": "external_message|spend|calendar_write|delete|...",
 "entity": "<HMAC(receipts key, normalised entity)>",   // recipient, event id, file path
 "entity_kind": "email|phone|event|file|account",
 "count": 1,            // recipients, items trashed, events touched
 "amount_usd": null,    // parsed when the call carries one
 "status": "ok|pending|deny|error", "approval_id": "...",
 "surface": "chat|task|voice|approval_card|schedule", "task_id": "...",
 "origin": "user|content|model"}   // taint origin of the entity, when known
```

- **Entities are keyed hashes.** Detectors compare equality and count; they
  never read a name.
- **Retention.** 90 days, which the owner can change. Pruning runs daily.
- **What it replaces.** Nothing. It is the one record that answers "how often,
  to whom, how much, across days" without plaintext recipients. The
  plaintext-recipient logs in §2.5 are left as they are.

### 5.2 Detectors

**Shape detectors, which run from day one and carry no amounts.**

| Detector | Fires when | Mirrors |
|---|---|---|
| `same_entity_writes` | The same entity is written ≥ 3 times in 10 minutes (update or overwrite; the same message recipient is excluded, since velocity covers it) | The loop guard's repeat rule (`services/turn_budget.py:99`) |
| `bulk_delete` | One call deletes or trashes ≥ 25 items, or ≥ 50 in an hour | A mailbox proposal can list up to 200 items (`services/mail_proposals.py:60`); today nothing counts them across calls |
| `new_entity_from_content` | A first-ever recipient whose taint origin is `content` | `taint.evaluate`'s recipient role, extended across days |

**Baseline detectors, silent until 14 days of history.**

| Detector | Signal | Fires when |
|---|---|---|
| `recipient_velocity` | Messages to one entity per hour and per day | Above median + 4·MAD of that entity's (or, if new, all entities') daily counts, with a minimum of 5 in a day |
| `cumulative_amount` | Sum of `amount_usd` per class per 24 h and per 7 days | Above median + 4·MAD of the owner's own 24 h and 7 d sums. With no amounts ever recorded, only owner rules apply |
| `slow_drip` | A one-sided CUSUM on daily counts per (class, entity) and per class | The cumulative excess over the 30-day median crosses 3× the typical day, over 2–7 days, while no single day tripped `recipient_velocity` |
| `class_velocity` | Outward calls per class per hour | As `recipient_velocity`, at class level |

**Every detector:**
- is per owner and per machine;
- is computed locally from §5.1;
- can be switched off in Settings → Costs, beside `LoopGuardSwitch`
  (`index.html:49112`), with the same reasoning as `loop_guard_enabled`
  (`services/turn_budget.py:247`);
- has thresholds that are shapes relative to the owner's own history. The owner
  can override any of them, which makes it an owner-set limit.

**When they run.**
- Shape and velocity detectors run in the `owner_rules` pre-hook, before the
  call, from the in-memory tail of the log.
- `slow_drip` and the 7-day sums also run in a daily sweep, on the internal
  scheduler.

### 5.3 What "LLM analysis" means here

- **The article** pairs statistics with LLM analysis. **Friday** uses Bonsai2
  (local only) for exactly one job: turning a firing into a plain explanation
  from the action metadata ("12 messages to the same address since Tuesday; your
  usual is 1 a day").
- **Firing decisions stay statistical**, so they are reproducible and testable.
- **Without Bonsai2**, a template writes the explanation.

---

## 6. Design 3: closed-loop remediation, and never automatic

When a detector fires, four things happen:

1. **Pause the action class.** The class's calls, from any surface, now get a
   card: "Paused: <detector explanation>".
   - This is a card, not a block, so the owner can still act.
   - The pause is stored in the rule set as a `system_pause` entry. It is signed,
     and visible in the Rules panel.
   - It lasts until the owner decides.
2. **Explain**, with evidence.
   - A notification, and a chat message in the conversation the last call came
     from.
   - The list of the actions involved: times, tools, counts and statuses. Entity
     names are resolved locally for display only.
   - In voice, one short sentence (§7.4).
3. **Propose a rule.**
   - Bonsai2 drafts one, or a template does: "Ask me before sending more than
     <n> messages to <entity> in a day"; "Block trashing more than <n> emails at
     once"; "Ask twice before spending more than $<n> in a week".
   - The text uses the detector's own numbers and names, and is drafted from
     metadata only (§3.2).
   - It arrives as an approval card of `kind="rule_proposal"`, with three
     actions:
     - **Approve**: it becomes a rule through §4.2 step 4.
     - **Edit**: it opens the Rules panel pre-filled.
     - **Decline**: recorded, and the same detector-and-entity pair is quiet for
       7 days.
4. **Lift the pause** when the owner approves, edits or declines the proposal,
   or presses "Resume <class>".

**Never automatic.** There is no code path that creates a rule without an owner
decision on this PC.
- The `rule_proposal` card kind is not registered with the approval executor.
- Approving it calls the authoring route, which re-checks `_refusal()`.
- A test asserts this (§10).

---

## 7. How it fits the existing defences

### 7.1 Taint and injection

- **Rules consume taint flags.** "Never email an address that came from an
  email" is a predicate on `origin == content`. So owner rules extend the taint
  defence with the owner's own policy.
- **Taint's memory-write rule stays** (`services/taint.py:90`,
  `MEMORY_WINDOW_SECONDS`). A rule cannot relax it (union).
- **Rule text and proposals never come from read content** (§3.2, §6).
- **The evaluator's history input** is fenced as untrusted, with the taint
  labels. The worst an injection can do is make a semantic rule miss, which
  leaves the default gates in force.

### 7.2 Egress scrub

- The evaluator is local, so no evaluation crosses the egress gate.
- The rationale can reach a cloud model in two ways:
  - read aloud in a cloud voice session;
  - shown in a chat that a cloud model later reads.

  Either way it is text like any other: `gate_text`
  (`services/egress_gate.py:1406`) and `seal_outbound` (`:1513`) apply to it.
- The plaintext BOM carries only a hash of it (§4.6).

### 7.3 The approval executor

- Rule-carded actions ride the existing `tainted_action` card and executor
  (`services/approval_executor.py:128`), so exactly-once is unchanged.
- Rules are re-checked at execution (§4.4). `ask_twice` needs its confirmations
  before `claim_for_execution` can succeed (§4.5).
- A rule that blocks at execution ends the run with the block sentinel, and the
  executor posts that back.

### 7.4 Room-mode voice

- **Cards raised by a rule or an anomaly pause cannot be approved by voice in
  room mode.** Voices are not told apart (`services/voice_engine.py:802`), and
  "Friday, yes" from anyone in the room is exactly the ambiguity these cards
  exist to remove.
- **In one-person mode,** a `card`-effect rule card may be approved by voice
  under the same owner-words check as local-context shares
  (`services/local_context.py:388`). An `ask_twice` second confirmation never
  can.
- **What voice says:** only that something is waiting and why, in one sentence,
  through the voice egress gate. Rule rationales that quote history are not read
  aloud in room mode. The spoken line is then "one of your rules needs you to
  approve something on screen".

### 7.5 The no-built-in-caps policy

- **Owner rules** are owner-set limits. The policy's own text allows exactly
  these (`services/turn_budget.py:49-64`).
- **Detectors** are guards that fire on a shape and only pause and ask. They
  follow the loop guard's precedent: on by default, switchable, visible
  (`services/turn_budget.py:247`, `:260`).
- **No detector has a built-in amount.** Amounts come from the owner's own
  history or from the owner's own override.

---

## 8. UI (existing elements)

- **The Rules panel** goes in `SettingsTabPrivacy` (`index.html:45283`), beside
  `ClawsRepinPanel` (`index.html:45193`, mounted at `:45489`). It shows:
  - the list of rules, each with its text, effect chip, scope and status
    (active / paused / pending proposal);
  - Add rule, which runs the §4.2 flow;
  - Edit, Pause and Delete (Delete asks once);
  - the signature status ("Your rules are signed on this PC").
- **Cards.** The existing approval card gains a "Why" section with the rule
  verdicts and anomaly explanation, and a "1 of 2 confirmations" line for
  `ask_twice`.
- **Costs tab.** The detector switches sit beside `LoopGuardSwitch`
  (`index.html:49112`) in `SettingsTabCosts` (`index.html:49143`).
- **Chat.** `/rule <text>` opens the authoring card inline. A pause message in
  the conversation has a "Resume" and a "See proposal" button.
- **Mirror.** `ui_parts/app.html` mirrors each changed component
  (`docs/development/ui-build.md`).

---

## 9. Costs and failure modes

**Costs.**
- **Money:** none. There are no cloud calls.
- **CPU.** A Laya question costs about 400 ms, and is asked only for semantic
  rules in scope.
- **Bonsai2.** A short judgement call is about 2K tokens in and 80 out, roughly
  4 s of prefill and 2 s of generation at the rates measured on the 131K seat.
  It runs only when Laya is unsure.
- **Disk.** The outward log is on the order of a few hundred bytes per outward
  action.

| Failure | Effect | Handling |
|---|---|---|
| Rule wrongly applied (false positive) | An extra card or block | The rationale is visible. The owner edits the rule. A block names the rule so it can be found |
| Rule wrongly not applied (false negative) | Default gates only | Exact rules use predicates. The union rule means no weaker than today |
| Evaluator unavailable | — | A card with "could not be checked" (§4.3 step 4) |
| Evaluator slow | Latency on an outward call | Laya 2.5 s timeout, Bonsai2 20 s timeout, then step 4 |
| Rule file tampered or unsigned | — | Every outward action is carded, and an alert is raised (§4.1) |
| Anomaly false positive | A paused class | One card per class. Decline quiets that detector-and-entity pair for 7 days. The detector can be switched off |
| Anomaly storm | Many firings | At most one active pause per class. Further firings are grouped into it |
| Injection in history | A semantic miss | Default gates stand (§7.1) |
| Clock or timezone skew | Wrong weekday | The settings timezone, the same as the scheduler |

---

## 10. Test plan (fail-first; each written to fail on main `edca4b91` before its change)

**Phase 0: outward log and hook placement**
- `test_every_surface_passes_the_owner_rules_hook`: chat, task, voice
  `_governed` and approval-executor calls each reach a priority-0 pre-hook.
  Fails today: no such hook.
- `test_outward_log_records_entity_hash_count_and_status_for_carded_and_denied_calls`
- `test_outward_log_body_is_encrypted_and_carries_no_plaintext_recipient`

**Phase 1: owner rules (exact)**
- `test_never_email_rule_blocks_on_chat_task_voice_and_executor`
- `test_rule_added_after_card_blocks_the_approved_execution`
- `test_a_rule_cannot_allow`: authoring an "allow" effect is refused.
  Hook-level: an ALLOW from the hook never overrides a later card.
- `test_ask_twice_needs_two_screen_approvals_five_seconds_apart`
- `test_rules_set_hmac_mismatch_cards_every_outward_action_and_alerts`
- `test_rule_routes_refuse_anything_but_an_owner_session_on_this_pc`
- `test_rule_rationale_is_on_the_card_and_only_hashed_in_the_plaintext_bom`

**Phase 2: semantic evaluation**
- `test_semantic_rule_uses_laya_then_bonsai_and_never_a_cloud_transport`: a
  cloud transport stub raises. The test asserts that it was never called.
- `test_unevaluable_semantic_rule_raises_a_card_with_the_reason`
- `test_injected_history_cannot_create_or_disable_a_rule`
- `test_weekday_predicate_uses_the_settings_timezone`

**Phase 3: detectors**
- `test_same_entity_three_writes_in_ten_minutes_pauses_the_class`
- `test_bulk_trash_of_25_items_pauses_deletes`
- `test_recipient_velocity_fires_only_after_14_days_of_history`
- `test_slow_drip_over_five_days_fires_while_no_single_day_does`
- `test_cumulative_amount_uses_the_owners_baseline_not_a_constant`
- `test_each_detector_can_be_switched_off_by_the_owner`

**Phase 4: remediation and voice**
- `test_anomaly_pauses_explains_and_proposes_a_rule_card`
- `test_no_code_path_applies_a_rule_without_an_owner_decision`: a static check
  that `rule_proposal` is not registered with the executor, plus behavioural
  tests of approve, edit and decline.
- `test_decline_quiets_the_detector_entity_pair_for_seven_days`
- `test_room_mode_voice_cannot_approve_a_rule_or_pause_card`
- `test_room_mode_voice_does_not_read_a_rationale_aloud`

---

## 11. Phased delivery (rough sizes)

| Phase | Content | Size |
|---|---|---|
| **0** | Outward-action log (encrypted, entity hashes, retention). The `owner_rules` pre-hook skeleton at priority 0, seen by every surface | 1 week |
| **1** | Rules: schema, signed set, owner-only routes, deterministic compiler and predicates (recipient, amount, weekday, keyword, origin), block / card / ask-twice, card "Why" section, Rules panel, BOM fields | 1.5 weeks |
| **2** | Semantic rules: the Laya `owner_rule_applies` question, the Bonsai2 fallback under `local_only`, the "could not be checked" card, authoring drafts on Bonsai2 | 1 week |
| **3** | Detectors: shape detectors, then baselines after 14 days, the daily sweep, Costs-tab switches | 1.5 weeks |
| **4** | Remediation: class pauses, explanations, `rule_proposal` cards, voice and room-mode behaviour | 1 week |

The total is about 6 weeks. Phase 0 is useful on its own: it creates the first
cross-day record of outward actions with no plaintext recipients. Phase 1 alone
already delivers "never email X" and "ask twice over $50" on every surface.

**Order with the companion spec.** The receipts phase of
`goals-and-delivery-receipts.md` should land before Phase 1 here, so that rule
verdicts have a signed receipt to live in. Its Phase 0 (one tool-result
classifier) is a prerequisite for the block sentinel in §4.4.

---

## 12. Open questions

1. **Detectors on by default** (recommended, as the loop guard is), or off until
   the owner turns them on?
2. **Outward-log retention.** 90 days is proposed. The slow-drip detector needs
   30.
3. **Purchases.** Friday has no purchase tool today. Browser payment fields are
   already carded (`services/browser_session.py`), and amounts are parsed only
   when a tool carries one. Should `cumulative_amount` wait for a real purchase
   tool, or also count Twilio's own per-message cost (`phone/spool.py:66`)?
4. **The second confirmation for `ask_twice`.** Should it require a different
   surface (for example the phone app) rather than just a delay?
