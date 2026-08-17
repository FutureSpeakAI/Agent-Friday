# Deep research — how Friday answers a hard question without making anything up

**Date:** 2026-08-17
**Branch:** `residency-policy` @ `33fa717`.
**Status:** design. **No implementation code exists for this document — it lands first, by instruction.**
**Method:** STORM — the pipeline this document specifies is STORM-shaped, and so is the document.
**Inherits:** [`residency-policy.md`](residency-policy.md) (R1–R10, the Arbiter, the seats),
[`symphony-of-intelligence.md`](symphony-of-intelligence.md) (S1–S4, the work queue, the
frontier-scopes/local-executes division), and the standing decisions in
[`decisions-2026-08.md`](../audits/decisions-2026-08.md).

**Evidence registers:**
- **VERIFIED** — the cited line, command output, or commit was read during the audit run for
  this document (2026-08-17).
- **INFERRED** — a conclusion from verified facts, reasoning shown.
- **UNKNOWN** — not determined; the check that would settle it is named.

---

## 0. What this layer is for, in one paragraph

Friday has no deep research capability. What she has is: two web tools
(`services/agent.py:418-487`) — one of which scrapes DuckDuckGo's HTML and returns eight
results whose URLs are display text, not links — a four-line "research discipline" prompt
buried in the background-task worker (`agent.py:1967-1970`), a contact-research route that
writes a placeholder file full of "pending" bullets and calls no tool at all
(`routes/contacts.py:256-288`), and an optional-skill YAML that is never loaded. This document
specifies the missing capability: a pipeline that takes a hard question, decomposes it from
multiple perspectives, grinds the searching and reading on local seats, synthesizes a cited
report under one heavy lease, verifies every citation deterministically before it renders, and
delivers the result into the conversation unprompted — with an honest account of what it could
not confirm. The organizing thesis is Stephen's: **frontier intelligence scopes, local
executes** — and this document builds on it, with one amendment the vault forces (§3.2).

---

## 1. What exists today — the audit

### 1.1 The retrieval substrate, and why it comes first

**`search_web`** (**VERIFIED** `services/agent.py:418-460`): scrapes
`html.duckduckgo.com/html/?q=`, parses `.result` blocks with BeautifulSoup, hard cap of
**8 results** (`:434`), no API key, no retry, no pagination, no cache. Two defects that are
fatal for research specifically:

- **The returned `url` is the `.result__url` display text** — a often-truncated domain string,
  not a fetchable href. `search_web` finds a page and `browse_web` frequently cannot fetch what
  it found. A research loop built on this substrate walks with a broken ankle.
- The endpoint is unauthenticated and rate-limits by IP; a layout change silently degrades to a
  raw-text dump (`:453`). **Zero results is therefore ambiguous between "nothing published" and
  "our scraper broke"** — an ambiguity §7 refuses to paper over.

**`browse_web`** (**VERIFIED** `agent.py:463-487`): fetches any `http(s)` URL, extracts text
via BeautifulSoup (strips script/style/nav/footer), truncates at 200,000 chars with the
truncation announced. No fetch cache, no ETag handling. **No SSRF guard** — `127.0.0.1`,
`169.254.169.254`, and Friday's own seat ports all pass; the host validator that exists
(`agent.py:1235-1262`) is wired to `open_url`, not `browse_web`.

Both are Ring 2, governed by the shared ring-2 token bucket at 60 calls/min
(`agent.py:4495-4515`) — the only rate limiting in the path. A second search backend exists for
news only: `_brave_results` (**VERIFIED** `services/news_engine.py:561-600`), Brave News API
behind `BRAVE_SEARCH_API_KEY`, used as RSS fallback.

**INFERRED:** the retrieval substrate must be repaired before any orchestration is worth
building on it. This is §8's P1–P3, and it is the reason this spec's build order starts below
the pipeline, not at it.

### 1.2 Everything already called "research" — the complete inventory

**VERIFIED**, so the pipeline replaces things by name rather than accreting beside them:

| Thing | Where | What it actually is |
|---|---|---|
| `spawn_task` | `agent.py:2353-2393` | The de-facto entry point: a background thread running one agentic turn, in-memory only, lost on restart |
| "research discipline" | `agent.py:1967-1970` | A prompt string: after round one, attack the weaker side of the evidence. The entire multi-hop story today |
| `researcher` subagent scope | `services/subagents.py:90-101` | An 11-tool allow-list, max_ring 2, max_steps 25, 900 s budget — a fence, not a method |
| `optional-skills/deep-research.yaml` | repo root | Declared tool chain + output template; not in `skills/`, never loaded |
| Contact research | `routes/contacts.py:256-288` | **Stubbed.** Writes "pending" bullets to a file; calls no tool. By the output-liveness rule this is a standing violation: it runs, exits clean, and produces nothing |
| Source Dossier | `routes/chat.py:1162-1256` | Post-hoc synthesis of sources already cited in a chat — with a fabrication check worth stealing (§3.5) |
| GraphRAG | `services/knowledge_graph/` | Real multi-hop retrieval — local/global/drift search — **over the local wiki only**. No web ingestion |
| Daily job-intelligence schedule | `services/scheduler.py:909-926` | An `agent_prompt` schedule that says "use web search," 07:30 daily |

One inventory note that shapes §3: **STORM appears throughout `docs/` as the house
spec-authoring method, not as code.** There is no STORM runtime anywhere in the tree
(**VERIFIED by absence** — no planner, no sub-question object, no citation tracking across
rounds).

### 1.3 The seats and the measured economics

From the golden plan (**VERIFIED** `tests/golden/residency/P1.json`) and the catalog
(`services/residency_catalog.py:79-144`):

| Seat | Model | num_ctx | tok/s | Status | Cost to summon |
|---|---|---:|---:|---|---|
| `interactive_brain` | `gemma4:12b` | 131,072 | 49–54 | pinned | free (resident) |
| `sidekick` | `gemma4:e2b` | 32,768 | 166–178 | pinned, survives every lease (R10) | free |
| `sidekick_heavy` | `gemma4:e4b` | 65,536 | 99.9 | leased | 27.5 s cold |
| `heavy_hitter` | `gemma4:26b` | 32,768 | 22.4 | leased, `n_cpu_moe` 18 | **53.5 s cold** |
| `embedder` | `qwen3-embedding:0.6b` | 2,048 | — | CPU | — |

The numbers this design leans on, each **VERIFIED** at its source:

- **Local seats walk dependent tool chains: 15/15** across e2b/e4b/12b on a five-call chain;
  the e2b's median chain is **10.6 s** (`symphony-of-intelligence.md` §0.6). Grinding is proven
  local work.
- **Structured output is reliable: 3/3** strict-JSON conformance on all three small seats
  (`symphony` §0.5). The frontier→local task-spec handoff has a working mechanism.
  **UNKNOWN:** the same probe on the 26b — named in §11 of `symphony` and still unrun; the
  check is the same probe pointed at the heavy seat.
- **Fixed overhead of a full Friday turn is ~20,216 tokens** (~8,534 of tool schemas + ~11,681
  of system prompt; `services/context_budget.py:36-40`), measured live most recently at
  **20,778** (`docs/audits/handoff-2026-08-16.md:22-23`). §3.3 is designed around *not paying
  this* on every grind step.
- **The 26b's economics are batch economics**: 53.5 s to wake, then 22.4 tok/s
  (`services/pause_forecast.py:52-58`). The measured drain shows what amortization buys:
  first item 8.66 s, second item **0.9 s** on the warm model
  (`docs/audits/symphony-live-2026-08-15.md:349-361`).
- **The 12b at 131,072 has ~110k tokens of working room** — enough to hold every fetched page
  for a sub-question at once. That window cost 96 MiB (`residency_catalog.py:120-122`).

### 1.4 The boundary — what the gate actually does

**VERIFIED** against `services/egress_gate.py` and `services/sensitivity_classifier.py`:

- The hard boundary is **per-provider**: `is_local_provider()` (`egress_gate.py:42-82`)
  requires registry classification `local`, an adapter in `LOCAL_CAPABLE_ADAPTERS`, and a
  loopback/RFC1918 `base_url` — **re-checked at call time**. Everything else is cloud and gets
  sealed: TIER_3 spans are dropped (`""`), TIER_2 spans become placeholders
  (`egress_gate.py:345-350`). Local providers receive payloads untouched.
- **Vault-forced routes never fall back to cloud** (`services/agent.py:186-191`), and
  `_route_vault` forces local regardless of configured mode.
- The classifier's egress mode has a **weak-keyword rule directly relevant to research**:
  "legal", "court", "medical", "income" — one hit classifies PRIVATE, **two distinct hits
  SENSITIVE** (`sensitivity_classifier.py:179-184`). A research payload *about* a legal or
  medical topic — Stephen's ordinary beat — will trip this even when it contains nothing of
  his.
- The news exemption (`ca4ee8c`) is the template for handling that: text fetched from an
  external feed is registered **at ingest, by provenance**, exact-string, ≤2,000 chars,
  bounded at 20,000 entries, with **no send-time API** — nothing can claim "this is public" at
  the moment of sending (`egress_gate.py:212-227`). It currently covers **only** news_engine
  headlines (`news_engine.py:1516-1526`); the commit deliberately excluded "anything a tool
  returned that was not a registered feed fetch." Web pages fetched by `browse_web` are
  therefore **not** exempt today.
- Two defects in that mechanism, found by this audit and inherited by any extension:
  **(a)** the whole-field trusted check (`egress_gate.py:302-304`) consults `_TRUSTED_TEXTS`
  but not `_PUBLIC_PARAS`, so a registered public string sent as a single-paragraph field
  skips the span loop and is redacted whole; **(b)** `register_public_text` accepts an
  `origin` argument and **never stores it** — the egress log cannot attribute an exemption to
  a source.

### 1.5 The scheduling and report-back substrate

**VERIFIED** — what exists, and the four gaps a long-running capability falls into:

- `services/work_queue.py`: classes `reflex/interactive/heavy/image/background`, dispositions
  `when_away/now_local/now_cloud`, persistent queue, `drain()` holding one Arbiter lease
  across the whole batch and reporting `load_s_saved`. Enqueueing `touches_vault=True` with
  `now_cloud` **raises** (`work_queue.py:170-175`) — the label may not lie.
- `services/pause_forecast.py`: `before_drain()` exists precisely to warn before the machine
  goes quiet; `WORTH_WARNING_S = 3.0`.
- `notifications_engine.push(proactive_chat=True, ...)` is the working mechanism for an
  unprompted report into the conversation (`notifications_engine.py:92-147`), used by exactly
  three call sites today.
- **Gap 1:** `_task_worker` never pushes to chat — a finished background task sits in an
  in-memory dict until polled. **Gap 2:** nothing calls `Arbiter.expire_if_due()`
  (`residency_arbiter.py:646`) — a crashed lease holder strands the GPU. **Gap 3:** the
  away-drain has no scheduler; `drain()` fires only from an HTTP route the UI must poll.
  **Gap 4:** drain results are pull-only; no completion notification exists.

### 1.6 Findings that change the design

**(a) The substrate is the first deliverable.** A research pipeline on top of display-text
URLs and an SSRF-open fetcher would be orchestration of garbage. P1–P3 in §8 precede
everything.

**(b) Escalation is not free — the gate stands between local findings and Claude.** The
weak-keyword rule (§1.4) means "send the findings up for review" will shred payloads on
exactly the topics a journalist researches. Either fetched-page quotes gain provenance-keyed
exemption the way headlines did, or escalation must be designed to survive redaction, or it
converts to asking Stephen. §5 designs for all three; which is default is Stephen's call
(§11 Q3).

**(c) Vault-touching commissions invert the thesis.** If the *question itself* contains
TIER_2/3 spans, Claude cannot see the question, so Claude cannot scope it. The framing seat
for those commissions must be local — the most interesting constraint in the design, handled
in §3.2 as a testable fork, not a vibe.

**(d) The grind must not pay the 20k tax.** A research step is not a chat turn. Running each
fetch-and-extract through the full system prompt plus 52 tool schemas spends two-thirds of a
32k window on ceremony. The pipeline is a **code-driven harness that calls models at judgment
points** (§3.3), not a free agentic loop — which also makes budgets enforceable and progress
legible by construction.

**(e) The report-back seam does not exist and must.** By the output-liveness rule, a research
task that completes without the user hearing about it is a failure even when it exits zero.
The seam is one call — `notifications_engine.push(proactive_chat=True)` from the completion
path — and this spec makes it a hard requirement (RS9), not a nicety.

**(f) Research should compound.** GraphRAG already does multi-hop retrieval over the wiki.
A report delivered as a wiki page is indexed by the knowledge graph
(`wiki_write_text` calls `mark_wiki_dirty`, **VERIFIED** `services/wiki_engine.py:99-122`),
so the *next* commission's first stop can be the last commission's findings. The
retrieve-and-cite directive (`33fa717`) already points this way: look it up before asking —
and "look it up" should check what Friday already established before it touches the web.

---

## 2. STORM, and why it fits this machine

Stanford STORM (Shao et al., *Assisting in Writing Wikipedia-like Articles From Scratch with
Large Language Models*, NAACL 2024) writes a grounded, cited article by refusing to ask one
model to "research X." Instead: **(1)** discover the *perspectives* from which the topic is
seen; **(2)** for each perspective, run a simulated conversation in which a questioner with
that perspective interrogates an expert whose answers are grounded in retrieved sources —
questions compound, follow-ups chase gaps; **(3)** curate the accumulated Q&A into an
outline; **(4)** write section by section, every claim carrying its citation.

Stephen chose this method, and it happens to decompose *exactly* along the seat boundaries
this machine already has:

| STORM stage | Nature of the work | Seat | Why |
|---|---|---|---|
| Perspective discovery + question generation | Judgment; must be right first time | **Claude** (or 12b — §3.2) | This is the scoping the thesis pays frontier prices for |
| Simulated conversation: ask → search → read → note → follow-up | Bulk, well-specified, verifiable | **12b** (judgment), **e4b** (page extraction) | 15/15 chains; ~110k window holds a sub-question's whole corpus |
| Outline + section writing | Quality-critical bulk | **26b** under one `heavy_turn` lease | Its economics are batch economics; synthesis is the batch |
| Citation verification | Deterministic | **code**, e2b for fuzzy cases only | A receipt check is not a judgment call |
| Review of ambiguity/failure | Judgment | **Claude**, budget-capped | Only ambiguity and failure escalate; success does not |

The one point where this document amends the thesis rather than building on it: *frontier
scopes* is the default, not an invariant. The vault decides who frames (§3.2). Everything
else in `symphony` §2.2 — the task spec as machine-readable handoff, report-back as
structure, review only on failure — carries over unchanged.

---

## 3. The pipeline

### 3.0 The objects

Serialized as JSON under `FRIDAY_DIR/research/<commission_id>/`. Persistent from the first
byte — a commission that dies with the process is not a promise Friday can make (the
work-queue precedent, `work_queue.py:85-117`).

```
ResearchCommission
  id, created_at
  question          str            # verbatim as asked
  context           str|null       # conversation context attached at commission time
  vault_bound       bool           # §3.2 — computed, never asserted
  disposition       when_away | now_local | now_cloud_scoped
  budget            { sub_questions: 7, queries_per_sq: 4, fetches_per_sq: 6,
                      fetches_total: 40, followup_depth: 3, escalations: 2,
                      wall_clock_soft_s: 2700 }
  status            proposed | scoping | grinding | synthesizing | verifying |
                    delivered | failed
  scoped_by         model id       # the actual model, never a vendor
  report_path       str|null       # wiki-relative, once delivered

ResearchPlan                       # the scoper's output; strict JSON (proven 3/3 local)
  commission_id
  perspectives      [ {name, stance} ]                      # 3–5
  sub_questions     [ {id, text, perspective, done_when} ]  # done_when: a checkable
                                                            # sentence — what evidence
                                                            # would settle this
  deliverable       {kind: wiki_report, working_title}
  internal_first    [str]          # wiki pages / KG communities to consult before the web

SourceRecord                       # one per successful fetch; the cache IS the provenance
  id, url, final_url, title, fetched_at, http_status
  extracted_path    str            # full extracted text on disk, verbatim
  spans             [ {span_id, text ≤2000 chars, para_index} ]   # paragraph-sized,
                                                                  # registration-ready
  provenance        fetched-by-friday-research                    # set by the fetch path,
                                                                  # nothing else

Finding
  id, sub_question_id
  claim             str            # Friday's words
  quote             str            # the source's words, verbatim
  source_id, span_id               # -> SourceRecord; no receipt, no render (§3.5)
  confidence        confirmed | single_source | contested | unconfirmed
  contradicts       [finding_id]   # never silently resolved

ResearchReport
  commission_id, title
  answer            str            # the lede — what was found, first
  sections          [ {heading, body_with_citation_markers, finding_ids} ]
  unconfirmed       [ {claim, what_was_tried} ]   # mandatory section, may be empty,
                                                  # never absent
  sources           [ SourceRecord summary ]
  colophon          { scoped_by, ground_by[], synthesized_by, verified: {claims, killed},
                      fetches, escalations_used, wall_clock_s, vault_bound }
```

### 3.1 Stage A — commission

Research is heavy work, and S2/S3 settled who decides about heavy work: **Friday proposes,
Stephen disposes.** A research-shaped request produces a `WorkflowProposal`
(`services/workflow_plan.py`) carrying the question, the estimated cost in minutes, and the
disposition menu — with `now_cloud` shown blocked, and why, when the commission is
vault-bound. "Choose for me" applies its existing heuristic and shows its pick.

One carve-out to keep the tool honest at small scale: a **single-lookup** — one fact, one
sub-question, the retrieve-and-cite directive's territory (`33fa717`) — is not a commission
and does not warrant the proposal ceremony. The boundary is testable: if the scoper would
emit one sub-question, it was a lookup; run it inline on the brain. (Whether Friday may make
that judgment silently is Q7.)

### 3.2 Stage B — scoping, and the vault fork

The scoper turns the question into a `ResearchPlan`. **Who scopes is decided by the gate, not
by preference**, with a testable rule:

> **RS2.** Before scoping, run the commission text + attached context through the egress
> classifier in dry-run (`seal_outbound` against a synthetic payload). If the sealed text is
> byte-identical to the input, the commission is clean: **Claude scopes.** If any span would
> be redacted or dropped, the commission is vault-bound: `vault_bound=true`, **the 12b
> scopes**, and no stage of this commission may use a cloud provider. The fork is recorded in
> the commission and in the report's colophon.

This is the thesis's amendment (§1.6c). Its cost is stated, not hidden: 12b decomposition
will be weaker than Claude's. Mitigation: the STORM scoping prompt does the structural heavy
lifting (perspectives first, then questions per perspective, then `done_when` per question —
three narrow structured-output calls, each proven 3/3 territory, rather than one open-ended
"plan this"). **UNKNOWN:** the actual quality delta between Claude-scoped and 12b-scoped
plans — settled by running both scopers on five clean commissions and comparing plans
blind. Until measured, the delta is a named cost of privacy, which is the correct direction
for the default to err.

Scoping also honors retrieve-before-research: the plan's `internal_first` names the wiki
pages and GraphRAG communities to consult before the first web query — Friday's own prior
findings are sources too, and asking the web for what she already established is its own
small failure.

### 3.3 Stage C — the grind

Per sub-question, a **code-driven harness** — not an agentic tool loop — runs the simulated
conversation. The web is the expert; the corpus of fetched pages is its voice:

```
for sq in plan.sub_questions:                      # sequential; one sq owns the brain
    corpus = internal_first(sq) + []
    q = sq.text
    for depth in 0..budget.followup_depth:
        queries  = reformulate(q, corpus)          # e2b: 1 structured call
        results  = search(queries)                 # code: search tool, cached
        pages    = fetch(select(results))          # code: fetch tool, cached, budgeted
        for page in pages:
            corpus += extract(page, sq)            # e4b: spans relevant to sq, verbatim
                                                   # quotes only — no paraphrase at
                                                   # extraction time
        answer, findings, gaps = converse(sq, corpus)   # 12b: the expert answer,
                                                        # grounded ONLY in corpus,
                                                        # findings as claim+quote pairs
        record(findings)
        if sq.done_when satisfied (12b judges, against the checkable sentence): break
        q = gaps.best_followup                     # the conversation compounds
```

Design properties, each earned by a §1 finding:

- **Models see purpose-built prompts, not Friday's chat turn.** The extraction call carries
  the sub-question and one page; the conversation call carries the sub-question and its
  corpus. No 52-tool registry, no 11,681-token persona. The 20k tax (§1.3) is paid zero
  times. Tools are invoked by the harness, so ring governance still applies at the tool
  layer, where it lives today.
- **The 12b's 131,072 window is what makes `converse()` honest** — the whole corpus is in
  context, so "grounded only in corpus" is enforceable by prompt and checkable by the
  verifier, instead of hoping a small window's truncation didn't silently remove the source.
- **Findings are claim+quote pairs from the first moment.** There is no later stage where
  citations get "added" — a claim is born with its receipt or it is born `unconfirmed`.
- Sub-questions run sequentially on the pinned seats — no lease needed
  (`CLASS_LEASE`: background/interactive need none), the machine stays fully interactive,
  and progress is inherently legible (§6).
- Cost, from measured rates: a sub-question ≈ 2–4 min (queries ≈ seconds; fetches are
  network-bound; extraction ≈ 12 s/page at ~100 tok/s; conversation ≈ 30–60 s at ~50 tok/s).
  A 6-sub-question commission grinds in ~15–25 min. **INFERRED** from §1.3 rates; the first
  live run measures it properly.

### 3.4 Stage D — synthesis

Outline and prose are the quality-critical bulk step, so they go to the seat whose economics
demand batching: the **26b, as a `heavy` work-queue item, drained under one `heavy_turn`
lease** with any other heavy work that has accumulated. The 53.5 s wake is paid once per
drain, not once per report (measured saving precedent: 8.66 s → 0.9 s, §1.3).

Inside the lease, synthesis is map-reduce sized to the seat: the heavy seat runs at 32,768
(**VERIFIED** golden plan) — ample for a purpose-built prompt, not for the full findings
ledger of a large commission. So: outline first (findings index in, outline out), then one
call per section (that section's findings in, cited prose out), then a stitch pass. Each call
is structured output. **UNKNOWN:** the 26b's structured-output conformance (§1.3) — if the
probe fails, synthesis falls back to the 12b and the colophon says so; the report does not
silently change author.

The synthesis prompt's standing rules: write only from findings; every claim carries a
finding id inline; contested findings are presented as contested with both quotes; absence of
evidence is written as absence, in the `unconfirmed` section — never smoothed into prose.

### 3.5 Stage E — verification: no receipt, no render

Deterministic code, not a model, because a receipt check is not a judgment call:

1. Every citation marker in the draft resolves to a `Finding`; every finding's `quote` is
   located **verbatim** (whitespace-normalized) in its `SourceRecord`'s cached extraction.
   Found → the citation renders. Not found → e2b gets one fuzzy-match attempt against the
   cached page; still not found → **the claim is struck from the body and moved to
   `unconfirmed` with `what_was_tried`**, and the kill is counted in the colophon.
2. The pseudo-toolcall integrity check the Source Dossier already runs
   (`routes/chat.py:1250-1256`) runs against the draft: prose that *narrates* tool calls that
   never happened discards the draft.
3. A report whose body survives with zero receipted claims is not a report — it is delivered
   as a finding-of-absence (§7.1), never as prose that sounds researched.

This stage is the mechanical enforcement of the house's oldest rule: never claim an action
not taken — extended to its research form, *never render a claim not sourced*.

### 3.6 Stage F — escalation, as testable conditions

Escalation exists for ambiguity and failure; success never escalates. Budget: the scoping
call plus **at most `budget.escalations` (default 2)** per commission. Every escalation and
its trigger is recorded in the colophon.

| id | Condition (testable) | Payload up | Expected down |
|---|---|---|---|
| **E1** | A sub-question has 0 usable SourceRecords after its full query budget | The sub-question, queries tried, result counts | Reformulated queries or "retire this sub-question" |
| **E2** | Two findings for one sub-question carry `contradicts` links and both are load-bearing (cited by the draft) | Both quotes verbatim + both URLs | Adjudication, or "present both" |
| **E3** | A sub-question's `done_when` is unsatisfied after budget exhaustion | The `done_when`, findings so far | Narrowed done-criteria or acceptance of partial |
| **E4** | Verification struck >20% of the draft's claims, and one local re-synthesis did not cure it | The outline + surviving findings | A rewritten outline |
| **E5** | The grind surfaces a sub-question the plan lacks | One sentence + the finding that surfaced it | Plan amendment yes/no |

**The gate governs every one of them:**

> **RS6.** Before any escalation is sent, its payload is sealed in dry-run. If the sealed
> payload is byte-identical, send. If anything would be redacted, the escalation is
> **converted, never degraded silently**: for a vault-bound commission it becomes a question
> to Stephen (the ask-the-user path, with what-was-found-so-far attached, per `33fa717`'s
> ordering: retrieve, then say what couldn't be confirmed, then ask); for a clean commission
> it is retried with quotes only (quotes from registered public spans survive the gate —
> §5), and if that still redacts, it converts to asking Stephen. A commission with
> `vault_bound=true` never sends any escalation payload cloudward at all — E-conditions
> route straight to the ask-Stephen form.

### 3.7 Stage G — delivery

The deliverable is a **wiki page**, written through the wiki engine (never `write_file`,
which bypasses the mirror, the encryption check, and the knowledge-graph dirty-marking —
**VERIFIED** §1.2 of the wiki audit): proposed path `Research/<slug>.md` (Q2 decides whether
that lands direct or through the pending-approval queue). Landing in the wiki means GraphRAG
indexes it, and the next commission's `internal_first` can cite this one — research
compounds (§1.6f).

Simultaneously, the completion path calls
`notifications_engine.push(proactive_chat=True, chat_message=…, target={workspace:"wiki",…})`
— the unprompted report into the conversation. The chat message is the lede, not the report:
what was asked, what was found (or not), how many claims were confirmed, how many struck,
where the full report lives, and the colophon line. Example shape:

> Research finished: *"What happened to the Austin housing-bond audit?"* — answered, 14
> claims confirmed across 9 sources, 2 struck in verification, 1 thing I couldn't confirm
> (flagged in the report). Full report: Research/austin-housing-bond-audit.
> *Scoped by claude-sonnet-5 · ground by gemma4:12b + gemma4:e4b · synthesized by gemma4:26b
> · 31 fetches · 1 escalation · 27 min.*

The colophon names actual models that served each stage — never a vendor, and never a model
that didn't serve.

---

## 4. Rules as data

Stable ids so a refusal, a log line, or a bug report can cite one.

| id | Rule |
|---|---|
| **RS1** | Research is proposed, not assumed: a commission goes through the `WorkflowProposal` gate (S2/S3). Single-lookups (one sub-question) are exempt — they are the retrieve-and-cite directive, not research |
| **RS2** | The scoper is chosen by the gate: seal-dry-run clean → Claude; any redaction → 12b, `vault_bound=true`, no cloud at any stage. Recorded in commission and colophon |
| **RS3** | A `vault_bound` commission's disposition menu never contains a cloud option, and `work_queue.enqueue` keeps its existing right to raise on the contradiction |
| **RS4** | Every finding is born with its receipt: claim + verbatim quote + source id, or it is `unconfirmed`. There is no later citation-adding stage |
| **RS5** | No receipt, no render: verification is deterministic, runs before delivery, strikes unreceipted claims into `unconfirmed`, and counts its kills in the colophon |
| **RS6** | Every escalation payload is sealed in dry-run first; redaction converts the escalation (to quotes-only, then to ask-Stephen) — it never sends a degraded payload silently, and vault-bound commissions never escalate cloudward at all |
| **RS7** | Escalations are budgeted (default 2 + scoping); exhaustion is a reported condition (E3 path), not a silent stall |
| **RS8** | Grind steps run on purpose-built prompts through the harness; no research stage pays the full-turn overhead, and no research stage carries the 52-tool registry |
| **RS9** | A completed commission **must** push a proactive chat message and land its report; a commission that produces no report and no failure account is `failed`, never `complete` — a subsystem that runs and produces nothing is a failure even when it exits zero |
| **RS10** | The report names the actual model that served each stage. A stage that fell back (26b → 12b synthesis) says so |
| **RS11** | Zero search results and search-infrastructure failure are distinguished (§7.2) and reported as different facts |
| **RS12** | The fetch cache is append-only for the life of a commission: verification runs against the bytes the finding was born from, not a re-fetch that may have changed |

---

## 5. The boundary, concretely

What crosses, and what cannot, for each stage — with the mechanism named:

| Stage | Crosses to cloud? | Mechanism |
|---|---|---|
| Commission text | Only if seal-dry-run is lossless (RS2) | `seal_outbound` dry-run |
| Grind (search/fetch/extract/converse) | Never — all local seats | Harness dispatches only to local providers; `is_local_provider` re-checked at call time |
| Fetched page content | Outbound to the web it came *from* is moot; toward cloud LLMs only as registered spans (below) | Provenance registry |
| Findings/escalations | Per RS6 | Seal dry-run + conversion ladder |
| Synthesis | Never — 26b (or 12b fallback) | Local lease |
| Report | Lands in the wiki (local; Drive mirror per existing wiki rules) | Wiki engine |

**The provenance extension** — the design, if Q3 approves it: web content fetched by the
research harness is third-party published material, the same category `ca4ee8c` carved out
for headlines. The extension registers `SourceRecord.spans` (paragraph-sized, ≤2,000 chars —
the object is shaped to the registry's existing bound on purpose) at **fetch time, from the
fetch path only**, preserving every constraint the news commit established: ingest-side only,
no send-time API, exact-string, bounded. Consequence: a verbatim quote survives the gate on
its way into an E2 adjudication or a clean commission's review, while **Friday's own
analysis around the quote still classifies normally** — synthesis may weave private context
and gets no exemption, exactly the line `ca4ee8c` drew.

Two gate defects must be fixed as part of the extension (or explicitly inherited): the
whole-field asymmetry and the unrecorded `origin` (§1.4). The extension's registration
records `origin=url` **and stores it**, so the egress log can attribute every exemption to
the page it came from — an audit trail the news registry currently lacks.

If Q3 declines the extension: escalation payloads carry Friday-authored summaries, which the
weak-keyword rule will redact on legal/medical/financial topics, so the conversion ladder in
RS6 lands on ask-Stephen more often. That is a legitimate configuration — more privacy, more
interruptions — and the spec supports it; it is slower, not broken.

**What never crosses, under either answer:** vault content in any form; TIER_2/3 spans of
the commission or conversation; any payload for a `vault_bound` commission; anything the
seal dry-run would touch.

---

## 6. Time, the GPU, and what Stephen sees

**Placement over time.** The grind runs on the pinned seats — no lease, machine fully
interactive, e2b answering chat throughout (R10). Synthesis enqueues as a `heavy`
work-queue item; disposition follows the commission: `when_away` waits for the away-drain,
`now_local` drains immediately. The heavy lease evicts the 12b brain for its duration, so
chat degrades to the e2b while the 26b writes — **and that is announced, not felt**:
`pause_forecast.before_drain()` fires the warning ("synthesis starting, the brain stands
down ~N min — the sidekick has the chat") before the card changes hands. Warn-before-silence
is already the house behavior for unattended work (`b10841c`); research inherits it.

**Progress.** The commission is a task-tray orb from Stage A. The harness emits log lines at
execution time (the `dcf8caf` fix — during, not after): which sub-question (k of n), which
source is being read, fetches used against budget, current stage. A
`GET /api/research/<id>` status endpoint serves the same structure the orb reads, so the UI
never invents progress. The commission directory on disk *is* the state — a restart resumes
from the last recorded finding rather than silently starting over (RS12 makes the cache the
stable ground for that).

**Three prerequisite gaps in the substrate (from §1.5) become real work here:** the
away-drain needs a scheduler tick (it currently fires only from an HTTP route); something
must call `Arbiter.expire_if_due()` so a crashed synthesis cannot strand the GPU; and drain
completion needs its notification seam. All three are named in §8 — this spec depends on
them and will not pretend they exist.

---

## 7. Failure modes, and how each one says so

**7.1 Nothing found.** A commission whose grind produces zero confirmed findings still
delivers: a finding-of-absence report — the question, every query tried per sub-question,
every source fetched and why it didn't answer, and the sentence "I could not answer this."
It reads as a search trail, not as prose. This is a *successful* commission (`delivered`);
absence honestly established is an answer.

**7.2 The tools failed — which is not the same fact.** Zero results everywhere is ambiguous
(§1.1). The harness disambiguates: search calls that error, parse to the raw-text fallback,
or return zero results across *all* sub-questions trigger a canary — one query with a known
stable answer. Canary fails → the commission is **`failed` with "my search tool is broken",
never "there is nothing published."** Inventing "no information exists" when the scraper
broke would be inventing a technical constraint's evil twin: a fabricated empirical result.

**7.3 Contradiction.** `contradicts` links survive to the report. E2 may escalate for
adjudication within budget; unadjudicated contradictions render as "sources disagree," both
quotes, both citations. The report never picks a winner silently.

**7.4 Fabricated citations.** Struck deterministically by RS5, counted in the colophon. A
nonzero kill count is Stephen's signal about the synthesis seat's honesty under pressure —
data he has asked this system to surface before, not hide.

**7.5 Death mid-run.** State is on disk per stage; the task-tray orb goes stale → the
liveness audit's territory. On restart, an interrupted commission is either resumed from its
last finding or reported as interrupted — the one forbidden outcome is silent disappearance
(RS9).

**7.6 A capability the tools can't express.** A commission needing what the substrate cannot
do (paywalled archives, PDFs — `browse_web` refuses non-text content today, a JS-rendered
site) **discloses the limit in the report** ("this source exists but I cannot read it: PDF")
rather than substituting a secondary source silently. Disclosed substitution is fine;
silent substitution is the defect.

---

## 8. Prerequisites — the substrate debts this design stands on

Ordered; each is small, none is optional. P1–P4 block Stage C; P5–P7 block Stage D/G.

| id | Debt | The fix |
|---|---|---|
| **P1** | `search_web` returns display-text URLs, 8 results, no key, no cache | Parse the real href; add Brave web search behind the existing key pattern (Q1) with DDG as fallback; return structured JSON with fetchable URLs |
| **P2** | `browse_web` has no SSRF guard | Wire the existing `open_url` validator (`agent.py:1235-1262`) to the fetcher; loopback/RFC1918/link-local refused |
| **P3** | No fetch cache | The `SourceRecord` store *is* the cache: keyed by URL, verbatim extraction on disk, hit before fetch |
| **P4** | Task completion is invisible | The `notifications_engine.push(proactive_chat=True)` seam from the completion path — one call, currently missing from `_task_worker` |
| **P5** | Away-drain has no scheduler | A scheduler tick calling `batch_ready`/`drain` (the existing 60 s scheduler is the natural home) |
| **P6** | Nothing calls `expire_if_due()` | The same tick calls it; a crashed lease holder no longer strands the GPU |
| **P7** | Gate defects §1.4(a)(b) | Whole-field check consults `_PUBLIC_PARAS`; registration stores `origin` |

`optional-skills/deep-research.yaml` and the stubbed `POST /api/contacts/research` are
superseded by this design and should be retired or rebuilt on it when it lands — named here
so neither survives as a second, quieter research path with different rules.

---

## 9. The house rules, and where each one is enforced

Each of these was earned in a debugging session; the design treats them as requirements with
mechanisms, not as tone.

| Rule | Mechanism |
|---|---|
| Never claim an action not taken | RS5's deterministic receipt check; the dossier's pseudo-toolcall integrity check on the draft; the colophon reports counts the code measured, not counts a model asserted |
| Never invent a technical constraint | §7.2's canary — "tool broken" and "nothing exists" are distinguished facts; §7.6's disclosure of real limits, verbatim, instead of invented ones |
| Name the actual model that served | `scoped_by`/`ground_by`/`synthesized_by` in the colophon, filled by the dispatch path (the `on_route` precedent from `dcf8caf`), including fallbacks |
| A capability the tools can't express is disclosed, not substituted | §7.6; RS10's fallback disclosure |
| Retrieve and cite before asking | `internal_first` in every plan; the single-lookup carve-out (RS1); RS6's ask-Stephen form always carries what-was-found-first |
| A subsystem that runs and produces nothing is a failure | RS9; the evidence-gate precedent (`completed_unverified`) extended: no report + no failure account = `failed` |

---

## 10. Build order

Shape on record before code; one commit each, testable in isolation.

1. **P1–P3** — the retrieval substrate (search fix, SSRF guard, fetch cache). Test: a query
   whose results are fetched end-to-end from the returned URLs; an SSRF probe refused.
2. **P4–P6** — the seams (proactive push on completion, scheduler tick for drain + lease
   expiry). Test: a spawned task's completion appears in chat unprompted; a deliberately
   abandoned lease is reclaimed.
3. **Objects + harness skeleton** — commission/plan/finding/report on disk; the grind loop
   with budgets, against a stub search backend. Test: golden commission replay is
   deterministic.
4. **Scoping with the vault fork** — RS2's dry-run fork, both scopers behind one interface.
   Test: a commission with a planted TIER_2 span scopes locally; a clean one scopes cloud;
   both recorded.
5. **Grind live** — Stage C against the real substrate. Test: a real question produces
   findings with receipts that verify.
6. **Synthesis under lease** — Stage D through the work queue, warn-before-silence firing.
   Test: the drain report shows the amortized load; chat stays answerable on the e2b.
7. **Verification + delivery** — RS5 and Stage G. Test: a planted unreceipted claim is
   struck and reported; the wiki page lands; the proactive message arrives.
8. **Escalation ladder** — E1–E5 with the seal dry-run conversion. Test: a planted redaction
   converts the escalation to ask-Stephen; the budget exhausts loudly.
9. **P7 + the provenance extension** — per Q3's answer.

---

## 11. Open questions for Stephen

Each answerable in a sentence.

**Q1 — Search backend.** The Brave key you have is News-only; general web search is a
separate (paid) key. Get one, or stay on DuckDuckGo scraping with its rate limits and
fragility as a named risk?

**Q2 — Where reports land.** Direct write to a new `Research/` wiki section (immediately
indexed, no click required), or through the existing pending-approval queue like other
Friday-proposed wiki edits?

**Q3 — The provenance extension.** May quotes from pages fetched during research be
registered gate-exempt the way news headlines are (§5) — meaning verbatim public-web quotes
can reach Claude during review — or should escalation stay quote-free and fall back to
asking you more often?

**Q4 — Escalation budget.** Is scoping-plus-two-escalations per commission the right
default ceiling?

**Q5 — The vault fork's visibility.** When a commission is vault-bound and the 12b scopes,
is the colophon line enough, or do you want Friday to say so up front in the proposal
("Claude will never see this question")?

**Q6 — Grind budget defaults.** Are ~7 sub-questions / ~40 fetches / ~45 minutes the right
default size for a commission, with bigger available on ask?

**Q7 — The single-lookup carve-out.** May Friday decide silently that a request is a lookup
(one sub-question, run inline, no proposal), or should anything that touches the web beyond
one search be proposed?

---

## 12. Sources

- Shao, Jiang, Kanell, Xu, Khattab, Lam — *Assisting in Writing Wikipedia-like Articles From
  Scratch with Large Language Models* (NAACL 2024) — the STORM method.
  https://arxiv.org/abs/2402.14207
- [`docs/design/residency-policy.md`](residency-policy.md) — seats, budgets, the Arbiter,
  R1–R10.
- [`docs/design/symphony-of-intelligence.md`](symphony-of-intelligence.md) — S1–S4, the work
  queue, measured chain/structured-output/overhead evidence.
- Commits read for this document: `ca4ee8c` (news provenance exemption), `33fa717`
  (retrieve-cite-then-ask), `dcf8caf` (unattended work local, live tool lines, `on_route`),
  `b10841c` (warn-before-silence), `218fca5` (batch under one lease).
