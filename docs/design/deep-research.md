# Deep research — how Friday answers a hard question without making anything up

**Date:** 2026-08-17
**Branch:** `residency-policy` @ `33fa717`.
**Status:** design. **No implementation code exists for this document — it lands first, by instruction.**
**Revised:** 2026-08-17, same day, after Stephen answered Q1–Q7 (§11). The two largest
consequences: keyword-based sensitivity is rejected outright in favor of a **judgment gate**
that classifies with a model and scrubs with receipts (§5 — a foundational component that
governs every cloud call, not a research detail), and the local-only vault fork in the first
draft is retired in favor of **scrub-then-escalate** (§3.2).
**Method:** STORM — the pipeline this document specifies is STORM-shaped, and so is the document.
**Inherits:** [`residency-policy.md`](residency-policy.md) (R1–R10, the Arbiter, the seats),
[`symphony-of-intelligence.md`](symphony-of-intelligence.md) (S1–S4, the work queue, the
frontier-scopes/local-executes division), and the standing decisions in
[`decisions-2026-08.md`](../audits/decisions-2026-08.md).

**Evidence registers:**
- **VERIFIED** — the cited line, command output, or commit was read during the audit runs for
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
report with clickable sources, verifies every citation deterministically before it renders,
and delivers the result into the conversation unprompted — with an honest account of what it
could not confirm. It also specifies the component the capability exposed a need for and which
outgrows it: **the judgment gate** (§5), which replaces keyword-triggered sensitivity with a
classify-scrub-verify path on every cloud call, so frontier intelligence can be used where it
is needed without Stephen's private material leaving the machine. Stephen's requirement,
verbatim: *"Friday must have web searching and scraping capabilities, and it must be able to
assemble large deep research reports with accurate sourcing and clickable links. If that
requires Claude Fable or Opus, so be it, but this must be a feature of our system."*

---

## 1. What exists today — the audit

### 1.1 The retrieval substrate, and why it comes first

**`search_web`** (**VERIFIED** `services/agent.py:418-460`): scrapes
`html.duckduckgo.com/html/?q=`, parses `.result` blocks with BeautifulSoup, hard cap of
**8 results** (`:434`), no API key, no retry, no pagination, no cache. Two defects that are
fatal for research specifically:

- **The returned `url` is the `.result__url` display text** — an often-truncated domain
  string, not a fetchable href. `search_web` finds a page and `browse_web` frequently cannot
  fetch what it found. A research loop built on this substrate walks with a broken ankle.
- The endpoint is unauthenticated and rate-limits by IP; a layout change silently degrades to
  a raw-text dump (`:453`). **Zero results is therefore ambiguous between "nothing published"
  and "our scraper broke"** — an ambiguity §7 refuses to paper over.

**`browse_web`** (**VERIFIED** `agent.py:463-487`): fetches any `http(s)` URL, extracts text
via BeautifulSoup (strips script/style/nav/footer), truncates at 200,000 chars with the
truncation announced. No fetch cache, no ETag handling. **No SSRF guard** — `127.0.0.1`,
`169.254.169.254`, and Friday's own seat ports all pass; the host validator that exists
(`agent.py:1235-1262`) is wired to `open_url`, not `browse_web`.

Both are Ring 2, governed by the shared ring-2 token bucket at 60 calls/min
(`agent.py:4495-4515`) — the only rate limiting in the path. A second search backend exists for
news only: `_brave_results` (**VERIFIED** `services/news_engine.py:561-600`), Brave News API
behind `BRAVE_SEARCH_API_KEY`, used as RSS fallback.

**Resolved by Q1:** web search and scraping are a *required* feature, not best-effort. A paid
general-search key is approved; §8 P1 specifies the repair.

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

### 1.4 The boundary — what the gate actually does, and what already scrubs

**VERIFIED** against `services/egress_gate.py`, `services/sensitivity_classifier.py`, and
`core/__init__.py`:

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
  his. The measured precedent: 9 of 120 public headlines classified TIER_3 on these rules
  (`ca4ee8c`). **This finding is what Stephen's Q3 answer overturns the architecture on — §5.**
- **A real PII scrubber already exists and runs.** `core._scrub_pii`
  (**VERIFIED** `core/__init__.py:935-989`) replaces SSNs, Luhn-validated card numbers, phone
  numbers, non-owner email addresses, US-style street addresses, and a user-controlled
  **privacy watchlist** (names, account numbers) with tagged placeholders
  (`[PII:kind:hash]`), returning a per-call in-memory lookup table for `_rehydrate_pii` on the
  response. It is wired cloud-only: `_finalize()` sets `scrub_pii=True` for cloud routes
  (`routing/model_router.py:257-265`), chat scrubs system prompt and messages before sending
  (`routes/chat.py:493-498`), and `_hook_pii_scrub` scrubs tool results as a post-hook at
  priority 95 (`agent.py:4602,4651`). **This is the scrubber §5 composes with — it exists; it
  is not being invented.**
- The news exemption (`ca4ee8c`) is the template for provenance handling: text fetched from an
  external feed is registered **at ingest, by provenance**, exact-string, ≤2,000 chars,
  bounded at 20,000 entries, with **no send-time API** (`egress_gate.py:212-227`). It covers
  only news_engine headlines today (`news_engine.py:1516-1526`). **Resolved by Q3:** the same
  mechanism extends to research fetches — §5.7.
- Two defects in that mechanism, found by this audit and inherited by any extension:
  **(a)** the whole-field trusted check (`egress_gate.py:302-304`) consults `_TRUSTED_TEXTS`
  but not `_PUBLIC_PARAS`, so a registered public string sent as a single-paragraph field
  skips the span loop and is redacted whole; **(b)** `register_public_text` accepts an
  `origin` argument and **never stores it** — the egress log cannot attribute an exemption to
  a source.
- The gate has a **startup self-test** (`egress_gate.py:580-632`): it seals an SSN+bank probe
  and asserts the material does not survive, plus a false-positive leg. This is the precedent
  §5.6's probe battery extends.

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

**(b) Keywords cannot tell Stephen's affairs from the world's.** The weak-keyword rule (§1.4)
shreds payloads on exactly the topics a journalist researches, and the headline incident
proved it on live data. The first draft of this document designed *around* that limitation;
Stephen rejected the limitation itself: *"keywording is insufficient; we need a judgement call
and a classification system to protect sensitive materials, and it should work with the PII
scrubber so we don't lock cloud models out completely."* §5 is the resulting component.

**(c) Protection, not location, is the constraint on framing.** The first draft forked
vault-touching commissions to local-only scoping. Under the judgment gate the fork is
narrower: Claude frames whenever a protected version of the question exists; local frames only
when it does not (§3.2).

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
| Perspective discovery + question generation | Judgment; must be right first time | **Claude** on the protected question (or 12b — §3.2) | This is the scoping the thesis pays frontier prices for |
| Simulated conversation: ask → search → read → note → follow-up | Bulk, well-specified, verifiable | **12b** (judgment), **e4b** (page extraction) | 15/15 chains; ~110k window holds a sub-question's whole corpus |
| Outline + section writing | Quality-critical bulk | **26b** under one `heavy_turn` lease; **Claude** when local is inferior (§3.4) | Batch economics locally; quality bar outranks localism when they conflict |
| Citation verification | Deterministic | **code**, e2b for fuzzy cases only | A receipt check is not a judgment call |
| Review of ambiguity/failure | Judgment | **Claude**, budget-capped | Only ambiguity and failure escalate; success does not |

The thesis, restated after Stephen's answers: **local by default, because the marginal token
is free; frontier wherever local would produce an inferior result and the judgment gate can
protect him.** *"If that requires Claude Fable or Opus, so be it"* — the quality of the
report outranks where it was made. What does not bend: raw private material never travels
(§5.3), and every crossing is judged, scrubbed, verified, and logged.

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
  protection        ProtectionPlan # §3.2 — computed by the judgment gate, never asserted
  disposition       when_away | now_local | now_cloud
  budget            { sub_questions: 10, queries_per_sq: 5, fetches_per_sq: 8,
                      fetches_total: 80, followup_depth: 3, escalations: 2,
                      wall_clock_soft_s: 2700 }        # sized to Q6 — see §3.3
  status            proposed | scoping | grinding | synthesizing | verifying |
                    delivered | failed
  scoped_by         model id       # the actual model, never a vendor
  report_path       str|null       # wiki-relative, once delivered

ProtectionPlan                     # the judgment gate's verdict on this commission
  cloud_allowed     bool           # false only when never-send material is load-bearing
  question_sent     str|null       # the scrubbed question Claude would actually see
  scrub_tags        [kind]         # what kinds were replaced (names, addr, ...) — kinds,
                                   # never values
  reason            str            # the judgment sentence, shown in the proposal

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
  sources           [ {url, title, fetched_at} ]  # every url a real, clickable href (Q1)
  colophon          { scoped_by, ground_by[], synthesized_by, verified: {claims, killed},
                      fetches, escalations_used, wall_clock_s, protection: ProtectionPlan }
```

### 3.1 Stage A — commission

Research is heavy work, and S2/S3 settled who decides about heavy work: **Friday proposes,
Stephen disposes.** A research-shaped request produces a `WorkflowProposal`
(`services/workflow_plan.py`) carrying the question, the estimated cost in minutes, the
disposition menu — and, per Q5, **the protection plan up front**: either "Claude will see a
protected version of this question — names and addresses scrubbed (3 spans)" or "Claude will
never see this question; a local model frames it, which may give a weaker plan." The
information he needs to veto arrives before the work starts, not in the credits.

The single-lookup carve-out is **resolved by Q7**: one fact, one search — the retrieve-and-cite
directive's territory (`33fa717`) — runs silently on the brain, no proposal, *"especially if
it is necessary to complete a task I have assigned."* The boundary stays testable: if the
scoper would emit one sub-question, it was a lookup.

### 3.2 Stage B — scoping: scrub, then escalate

> **Rewritten 2026-08-17.** The first draft forked vault-touching commissions to local-only
> framing. Stephen replaced that: *"Claude should see it if the PII scrubber can protect me
> and if the local models may give an inferior answer."* Location is no longer the rule;
> protection is.

The scoper turns the question into a `ResearchPlan`. Who scopes is decided by the **judgment
gate** (§5), with a testable rule:

> **RS2.** Before scoping, the commission text + attached context goes through the judgment
> gate in dry-run. If the gate produces a protected version — every private span judged and
> scrubbed, nothing on the never-send list load-bearing — **Claude scopes, on the protected
> text**, and the proposal shows what was scrubbed (kinds and counts, never values). If
> never-send material is essential to the question itself — the question cannot be asked
> without it — `cloud_allowed=false`: **the 12b scopes**, the proposal says "Claude will
> never see this question," and no stage of this commission uses a cloud provider. The
> verdict, either way, is recorded in the `ProtectionPlan` and the colophon.

The local-scoping path is now the exception, not the default for an entire category — and its
cost stays stated: 12b decomposition will be weaker than Claude's. Mitigation unchanged: the
STORM scoping prompt does the structural heavy lifting (perspectives first, then questions per
perspective, then `done_when` per question — three narrow structured-output calls, each proven
3/3 territory). **UNKNOWN:** the actual quality delta between Claude-scoped and 12b-scoped
plans — settled by running both scopers on five commissions and comparing plans blind.

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

**Default size (Q6): matched to what Claude.ai's Research feature delivers.** Anthropic's
advanced Research runs **up to 45 minutes** and reports consulting **"hundreds of internal
and external sources,"** delivering a multi-section cited report (per Anthropic's
announcement coverage; §12). The distinction that makes a local match feasible: *consulted*
counts every search result weighed, not every page read in full. Defaults sized to that
target: ~10 sub-questions, ~50 search queries yielding a few hundred candidate results
consulted, **80 full-page fetches**, soft wall clock 45 minutes. Feasibility from measured
rates (**INFERRED** from §1.3): fetch+extract ≈ 8–15 s/page on the e4b ≈ 15–25 min of
extraction, conversations ≈ 30–60 s each on the 12b — a full-size commission lands inside the
45-minute target. The first live run measures it properly and the budget defaults get
corrected from truth, per house habit.

### 3.4 Stage D — synthesis

Outline and prose are the quality-critical bulk step. The default seat is the **26b, as a
`heavy` work-queue item, drained under one `heavy_turn` lease** with any other heavy work that
has accumulated — the 53.5 s wake paid once per drain, not once per report (measured saving
precedent: 8.66 s → 0.9 s, §1.3).

Inside the lease, synthesis is map-reduce sized to the seat: the heavy seat runs at 32,768
(**VERIFIED** golden plan) — ample for a purpose-built prompt, not for the full findings
ledger of a large commission. So: outline first (findings index in, outline out), then one
call per section (that section's findings in, cited prose out), then a stitch pass. Each call
is structured output. **UNKNOWN:** the 26b's structured-output conformance (§1.3) — if the
probe fails, synthesis falls back to the 12b and the colophon says so; the report does not
silently change author.

**Claude may synthesize — this is Stephen's "so be it."** When the commission's
`ProtectionPlan` allows cloud and any of the following holds, synthesis goes to Claude
instead: (a) the disposition is `now_cloud`; (b) verification (§3.5) struck >20% of a local
draft's claims and one local retry did not cure it (the old E4, now a seat decision rather
than only an escalation); (c) the commission's size exceeds what map-reduce on a 32k seat can
hold coherently (more than ~40 receipted findings feeding one section). What Claude receives:
the findings ledger — quotes provenance-registered (§5.7), Friday's claim text
judgment-gated and scrubbed — never the raw conversation, never anything the gate would
refuse. The colophon names the seat that actually wrote it.

**The express lane.** For a clean or fully-protectable commission with `now_cloud` chosen,
the entire commission — scoping, search, reading, synthesis — may run frontier-side in one
piece, using the Claude API's server-side web-search tool. Same objects, same receipt
verification on the way in (§3.5 runs regardless of who wrote the draft), same delivery
(§3.7), same colophon honesty. This is the fastest and most expensive shape of the feature;
it exists because the feature's quality bar is set by what frontier models can do, and some
questions will be worth it.

The synthesis prompt's standing rules, any seat: write only from findings; every claim
carries a finding id inline; contested findings are presented as contested with both quotes;
absence of evidence is written as absence, in the `unconfirmed` section — never smoothed into
prose.

### 3.5 Stage E — verification: no receipt, no render

Deterministic code, not a model, because a receipt check is not a judgment call:

1. Every citation marker in the draft resolves to a `Finding`; every finding's `quote` is
   located **verbatim** (whitespace-normalized) in its `SourceRecord`'s cached extraction.
   Found → the citation renders, as a clickable link to the source URL. Not found → e2b gets
   one fuzzy-match attempt against the cached page; still not found → **the claim is struck
   from the body and moved to `unconfirmed` with `what_was_tried`**, and the kill is counted
   in the colophon.
2. The pseudo-toolcall integrity check the Source Dossier already runs
   (`routes/chat.py:1250-1256`) runs against the draft: prose that *narrates* tool calls that
   never happened discards the draft.
3. A report whose body survives with zero receipted claims is not a report — it is delivered
   as a finding-of-absence (§7.1), never as prose that sounds researched.

This stage runs identically whether the draft came from the 26b, the 12b, or Claude — the
receipts do not care who wrote the prose. It is the mechanical enforcement of the house's
oldest rule: never claim an action not taken — extended to its research form, *never render a
claim not sourced*.

### 3.6 Stage F — escalation, as testable conditions

Escalation exists for ambiguity and failure; success never escalates. Every escalation and
its trigger is recorded in the colophon.

**The budget, in one plain sentence (Q4 asked this badly the first time):** this number
controls how many times one research job may go back to Claude for help before it must finish
with what it has — set too high, the symptom is surprise API spend; set too low, reports give
up early and say "couldn't resolve." Default: **the scoping call plus two escalations** per
commission, revisited when live runs produce evidence.

| id | Condition (testable) | Payload up | Expected down |
|---|---|---|---|
| **E1** | A sub-question has 0 usable SourceRecords after its full query budget | The sub-question, queries tried, result counts | Reformulated queries or "retire this sub-question" |
| **E2** | Two findings for one sub-question carry `contradicts` links and both are load-bearing (cited by the draft) | Both quotes verbatim + both URLs | Adjudication, or "present both" |
| **E3** | A sub-question's `done_when` is unsatisfied after budget exhaustion | The `done_when`, findings so far | Narrowed done-criteria or acceptance of partial |
| **E5** | The grind surfaces a sub-question the plan lacks | One sentence + the finding that surfaced it | Plan amendment yes/no |

(The first draft's E4 — repeated verification failure — is now a synthesis-seat decision,
§3.4(b), rather than an escalation.)

> **RS6 (rewritten).** Every escalation payload goes through the judgment gate before it is
> sent: quotes travel under their provenance registration (§5.7), Friday's own framing is
> judged and scrubbed (§5). A payload the gate cannot protect — never-send material
> load-bearing in the framing itself — is not sent degraded and not sent silently: the
> escalation converts to a question to Stephen, carrying what-was-found-so-far, per
> `33fa717`'s ordering — retrieve, then say what couldn't be confirmed, then ask. A
> commission with `cloud_allowed=false` never escalates cloudward at all; its E-conditions
> route straight to the ask-Stephen form.

### 3.7 Stage G — delivery: land, style, surface

Three steps, per Q2's answer, in order:

1. **Land.** The report writes **directly** to `Research/<slug>.md` through the wiki engine —
   never `write_file`, which bypasses the Drive mirror, the encryption check, and the
   knowledge-graph dirty-marking (**VERIFIED** `services/wiki_engine.py:99-168`). No approval
   queue. Landing in the wiki means GraphRAG indexes it and the next commission's
   `internal_first` can cite this one — research compounds (§1.6f). Every citation in the
   landed page is a real, clickable link to its source.
2. **Style.** The landed markdown is rendered into **Friday's page style** — a formatted HTML
   report page, built by the same deterministic template discipline the showcase engine
   already uses for decks and sites (LLM output → fixed template → styled page; the
   `create_presentation`/`create_website` precedent). The styled page cites the wiki page as
   its source of truth; the wiki page remains canonical.
3. **Surface.** The styled report **opens in a new tab** on completion, and the report is
   **visible in the workspace** — a tile/entry in the relevant workspace listing recent
   research, alongside the task-tray orb it grew from.

Simultaneously, the completion path calls
`notifications_engine.push(proactive_chat=True, chat_message=…, target={…})` — the unprompted
report into the conversation. The chat message is the lede, not the report: what was asked,
what was found (or not), how many claims were confirmed, how many struck, where the full
report lives, and the colophon line. Example shape:

> Research finished: *"What happened to the Austin housing-bond audit?"* — answered, 14
> claims confirmed across 9 sources, 2 struck in verification, 1 thing I couldn't confirm
> (flagged in the report). Full report: Research/austin-housing-bond-audit (opened in a new
> tab). *Scoped by claude-sonnet-5 on a protected question (2 names scrubbed) · ground by
> gemma4:12b + gemma4:e4b · synthesized by gemma4:26b · 74 fetches · 1 escalation · 38 min.*

The colophon names actual models that served each stage — never a vendor, and never a model
that didn't serve.

---

## 4. Rules as data

Stable ids so a refusal, a log line, or a bug report can cite one. RS2, RS3, and RS6 were
rewritten in the 2026-08-17 revision; their first-draft forms are superseded.

| id | Rule |
|---|---|
| **RS1** | Research is proposed, not assumed: a commission goes through the `WorkflowProposal` gate (S2/S3). Single-lookups (one sub-question) are exempt and may run silently, especially in service of an assigned task (Q7) |
| **RS2** | The scoper is chosen by the judgment gate: a protectable question → Claude scopes on the protected text; never-send material load-bearing → 12b scopes, `cloud_allowed=false`, stated in the proposal up front. Recorded in ProtectionPlan and colophon |
| **RS3** | The disposition menu reflects the ProtectionPlan: `cloud_allowed=false` removes every cloud option with the reason shown; `work_queue.enqueue` keeps its right to raise on a contradictory label |
| **RS4** | Every finding is born with its receipt: claim + verbatim quote + source id, or it is `unconfirmed`. There is no later citation-adding stage |
| **RS5** | No receipt, no render: verification is deterministic, runs before delivery regardless of which seat wrote the draft, strikes unreceipted claims into `unconfirmed`, and counts its kills in the colophon |
| **RS6** | Every cloudward payload — escalation, Claude-synthesis, express lane — goes through the judgment gate; what the gate cannot protect converts to ask-Stephen, never sends degraded, never sends silently |
| **RS7** | Escalations are budgeted (default: scoping + 2); exhaustion is a reported condition (E3 path), not a silent stall. What the number controls, in plain terms: how many times one job may go back to Claude for help before finishing with what it has |
| **RS8** | Grind steps run on purpose-built prompts through the harness; no research stage pays the full-turn overhead, and no research stage carries the 52-tool registry |
| **RS9** | A completed commission **must** push a proactive chat message and land its report; a commission that produces no report and no failure account is `failed`, never `complete` — a subsystem that runs and produces nothing is a failure even when it exits zero |
| **RS10** | The report names the actual model that served each stage. A stage that fell back or was promoted (26b → 12b, 26b → Claude) says so |
| **RS11** | Zero search results and search-infrastructure failure are distinguished (§7.2) and reported as different facts |
| **RS12** | The fetch cache is append-only for the life of a commission: verification runs against the bytes the finding was born from, not a re-fetch that may have changed |

---

## 5. The judgment gate — a foundational component, not a research detail

> **New in the 2026-08-17 revision.** Stephen, verbatim: *"keywording is insufficient; we
> need a judgement call and a classification system to protect sensitive materials, and it
> should work with the PII scrubber so we don't lock cloud models out completely (we'll need
> frontier intelligence sometimes)."* This section specifies that system. It governs **every
> cloud call Friday makes** — chat, escalations, synthesis, workers — not just research;
> research is merely its first demanding customer. The existing span-level redaction, the PII
> scrubber, and the news provenance registry are the substrate it composes with, **not things
> it replaces.**

### 5.1 Why keywords failed, in one measured paragraph

The current gate classifies by pattern: regex for hard identifiers, keyword lists, NER, and
embedding similarity (`sensitivity_classifier.py`, four layers, max wins). The layers are
good at *what* a span looks like and structurally blind to *whose* it is. Measured
consequence: 9 of 120 public headlines classified TIER_3 because they contained "court" and
"raised a Series B" (`ca4ee8c`); the weak-keyword rule will do the same to any research
payload on a legal, medical, or financial topic. The rules exist to keep *Stephen's* legal
and financial affairs on the machine; they cannot tell his affairs from a story about someone
else's. That distinction **is a judgment call, so a model must make it.**

### 5.2 The three verdicts

The judgment gate classifies each flagged span into one of three verdicts, which map to
mechanical treatments:

| Verdict | Meaning | Treatment |
|---|---|---|
| **ABOUT_THE_WORLD** | Third-party material: published facts, other people's public actions, quotes from public sources | Sends, after the deterministic identifier sweep (§5.5) — judgment never exempts a span from the scrubber |
| **STEPHEN_SUBSTANCE** | His material, where the *substance* matters and the *identity* can be separated: "my client in the housing case" | Scrubbed — identifying spans replaced by the existing tagged placeholders — then re-verified, then sent; rehydrated on the response |
| **NEVER_SEND** | Material where identity and substance cannot be separated, plus everything on the never-list (§5.3) | Redacted or dropped exactly as today. The gate's floor does not move |

### 5.3 The never-list — what no verdict can override

Mechanical, short, and not subject to judgment:

- Hard identifiers: SSNs, Luhn-valid card numbers, bank/routing numbers, government IDs,
  credentials, API keys — the regex tier's territory, scrubbed unconditionally and **blocked
  entirely if a scrub somehow leaves one intact** (§5.5 step 4).
- **Raw vault documents.** The contents of `vault/{legal,finances,family}`, `finance/`, and
  `health/` never travel as documents, under any verdict. Their substance may reach a cloud
  model only as judged, scrubbed, derived text — a summary or a question — never as the file.
  The existing vault-forced local routing (`_route_vault`, `agent.py:186-191`) stays: *work
  on* vault material runs on local seats; the judgment gate governs only what derived text
  may leave afterward.
- Anything on Stephen's **never-send watchlist** — an extension of the existing privacy
  watchlist (`core/__init__.py:986-989`) with a stronger meaning: not "scrub this" but
  "block any payload containing this." The dial is his; Friday builds the dial and never
  repoints it.

### 5.4 Who judges

- **The seat:** the `interactive_brain` (12b today) with a purpose-built prompt — the same
  discipline as §3.3: no tool registry, no persona, one structured call per payload with all
  flagged spans batched in and verdicts out. The core question, stated in the prompt: *"Is
  this span Stephen's private material, or material about the world? When uncertain, say
  STEPHEN_SUBSTANCE."* Each verdict returns with a one-sentence reason; the sentence goes to
  the ledger (§5.8), because a judgment that cannot explain itself cannot be audited.
- **When it runs:** the judgment gate is an appeals court, not a first instance. Payloads
  whose deterministic classification is PUBLIC everywhere skip it entirely — nothing to
  judge, no latency added. It is consulted exactly where today's gate would redact or drop —
  the set of cases where keywords currently destroy value. Cost estimate (**INFERRED** from
  §1.3 rates): a 10-span judgment ≈ 2–4 s on the 12b, paid only on cloud calls that today
  lose content silently.
- **When it cannot run** (seat unreachable, timeout, malformed verdict): **the deterministic
  gate governs that payload unchanged** — fail toward redaction, never toward open — and the
  ledger records `judged: no (fallback: <reason>)`. A payload is never held hostage waiting
  for judgment; it is sent the way today's gate would send it.

### 5.5 Composition — the order of operations on every cloud-bound payload

The enforcement point does not move: `seal_outbound` (`model_router.py:88-118`) remains the
single choke point with its existing fail-closed contract (gate raises → send blocked). What
changes is what happens inside:

```
1. Deterministic identifier scrub          core._scrub_pii — existing, unconditional.
                                           Tagged placeholders + rehydration table.
2. Deterministic classification            existing four layers, span-wise — existing.
   → all PUBLIC?                           send. Judgment never consulted.
3. Judgment                                12b verdict per flagged span (§5.4).
   ABOUT_THE_WORLD                         span passes (already identifier-scrubbed).
   STEPHEN_SUBSTANCE                       span re-scrubbed with watchlist + NER aids,
                                           placeholder map extended.
   NEVER_SEND                              span redacted/dropped as today.
4. Verification of the scrub               deterministic, and this is the step that makes
                                           a wrong judgment survivable:
                                           (a) re-run the identifier regexes + NER over the
                                               outgoing text — any hard identifier or
                                               watchlist token surviving → BLOCK the send;
                                           (b) re-classify the scrubbed text — a span still
                                               classifying SENSITIVE after scrub → treated
                                               as NEVER_SEND.
5. Send, log (§5.8), rehydrate the         existing _rehydrate_pii mechanism; the lookup
   response                                table never leaves memory.
```

**INFERRED design choice, stated:** the scrubber is deterministic and the judge is not, and
they are composed so that the model can only ever *narrow* what the deterministic layers
would have withheld after the scrub has already run — it can rescue over-redaction; it cannot
authorize an unscrubbed send. The one degree of freedom judgment adds (letting a scrubbed
span through that keywords would have dropped) is exactly the one bounded by step 4.

### 5.6 Being wrong, detected — not assumed impossible

A classifier that makes judgments is a model that can be wrong, and a scrubber that silently
succeeds is the failure mode this codebase specialises in. Four mechanisms, none optional:

1. **The probe battery.** The gate's existing startup self-test (`egress_gate.py:580-632`)
   extends to the judgment layer: a fixture set of known-private payloads (synthetic
   identifiers, planted watchlist names, first-person legal/medical text) that must **never**
   survive, and known-public payloads (headline-shaped text, third-party facts) that must
   survive. Runs at startup and after any change to the judgment prompt. **Any private-fixture
   failure disables the judgment layer** — the deterministic gate resumes alone — with a loud
   notification, because losing capability honestly beats keeping it dishonestly.
2. **The overturn ledger.** Every time judgment lets a span through that the deterministic
   layers would have withheld, that is an *overturn*: logged with span hash, verdict reason,
   destination provider, and timestamp. Overturns are the entire risk surface of this design,
   so they are first-class data, not log noise.
3. **Sampled review.** The weekly self-improvement loop surfaces a digest to Stephen: how
   many overturns, to which providers, with N sampled reasons shown. He reads five sentences
   a week and knows exactly what class of thing his gate is letting through. A judgment
   pattern he dislikes becomes a watchlist entry or a prompt correction — the dial again.
4. **The kill switch.** `settings.judgment_gate.enabled = false` reverts the entire system to
   today's deterministic behavior in one setting. The new layer is strictly additive and
   strictly removable.

### 5.7 Provenance, extended (Q3: approved)

Web content fetched by the research harness is third-party published material — the same
category `ca4ee8c` carved out for headlines. `SourceRecord.spans` (paragraph-sized, ≤2,000
chars — shaped to the registry's existing bound on purpose) are registered at **fetch time,
from the fetch path only**, preserving every constraint the news commit established:
ingest-side only, no send-time API, exact-string, bounded. A verbatim quote therefore
survives the gate on its way to Claude — Stephen's answer, verbatim: *"Quotes are fine to
reach Claude."* Friday's own analysis around the quote still classifies normally and goes
through §5.5 — exactly the line `ca4ee8c` drew.

Two inherited defects are fixed as part of this extension: the whole-field check consults
`_PUBLIC_PARAS` (closing §1.4's asymmetry), and registration **stores `origin`** so the
ledger can attribute every exemption to the page it came from.

### 5.8 The audit surface — what left the machine

The egress log (`~/.friday/vault/egress-log.jsonl`) already records allow/redact/drop per
field. It gains the judgment fields — verdict, reason sentence, scrub-tag kinds and counts
(kinds, never values), overturn flag, provenance origin for exempted quotes — and gets a
human surface: a **"What left the machine"** panel. One row per cloud call: when, to which
provider and model, what verdicts were applied, what kinds were scrubbed, whether judgment
overturned the keyword layer, and the reason sentence. Stephen can open any week and read
exactly what traveled and why — which is what makes §5.6's sampled review a two-minute habit
instead of a forensic project.

### 5.9 What this does not change

The per-provider boundary (local models see everything, re-checked at call time); the
fail-closed contract; vault-forced local routing for work on vault material; the news
registry's semantics; span-level redaction as the mechanical substrate; the classifier
consulted directly by other subsystems (`ca4ee8c` kept the classifier untouched for the same
reason — the judgment gate lives at the gate).

---

## 6. Time, the GPU, and what Stephen sees

**Placement over time.** The grind runs on the pinned seats — no lease, machine fully
interactive, e2b answering chat throughout (R10). Synthesis enqueues as a `heavy`
work-queue item; disposition follows the commission: `when_away` waits for the away-drain,
`now_local` drains immediately, `now_cloud` skips the lease entirely (Claude synthesizes, or
the express lane runs — §3.4). The heavy lease evicts the 12b brain for its duration, so
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
stable ground for that). On completion, the styled report opens in a new tab and appears in
the workspace (§3.7) — surfacing is part of delivery, not a courtesy.

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

**7.7 The judgment gate is wrong.** Not a research failure mode but the system's most
consequential one, so it is cross-referenced here: a wrong SEND is bounded by the
deterministic post-scrub verification (§5.5 step 4), surfaced by the overturn ledger and
weekly digest (§5.6), and recoverable by the kill switch. A wrong NEVER_SEND costs capability,
not privacy, and shows up as the ask-Stephen conversions it causes.

---

## 8. Prerequisites — the substrate debts this design stands on

Ordered; each is small, none is optional. P1–P3 block Stage C; P4–P6 block Stage D/G; P7 is
part of the judgment-gate build (§5.7).

| id | Debt | The fix |
|---|---|---|
| **P1** | `search_web` returns display-text URLs, 8 results, no key, no cache — and Q1 makes search-and-scrape a *required* feature | A paid general web-search key (Brave web search, same key pattern as the existing news key) as primary backend; DDG scrape demoted to fallback; parse real hrefs; return structured JSON with fetchable URLs; results feed clickable citations end-to-end |
| **P2** | `browse_web` has no SSRF guard | Wire the existing `open_url` validator (`agent.py:1235-1262`) to the fetcher; loopback/RFC1918/link-local refused |
| **P3** | No fetch cache | The `SourceRecord` store *is* the cache: keyed by URL, verbatim extraction on disk, hit before fetch |
| **P4** | Task completion is invisible | The `notifications_engine.push(proactive_chat=True)` seam from the completion path — one call, currently missing from `_task_worker` |
| **P5** | Away-drain has no scheduler | A scheduler tick calling `batch_ready`/`drain` (the existing 60 s scheduler is the natural home) |
| **P6** | Nothing calls `expire_if_due()` | The same tick calls it; a crashed lease holder no longer strands the GPU |
| **P7** | Gate defects §1.4(a)(b) | Whole-field check consults `_PUBLIC_PARAS`; registration stores `origin` — folded into the §5.7 build |

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
| Name the actual model that served | `scoped_by`/`ground_by`/`synthesized_by` in the colophon, filled by the dispatch path (the `on_route` precedent from `dcf8caf`), including fallbacks and promotions |
| A capability the tools can't express is disclosed, not substituted | §7.6; RS10's fallback disclosure |
| Retrieve and cite before asking | `internal_first` in every plan; the single-lookup carve-out (RS1/Q7); RS6's ask-Stephen form always carries what-was-found-first |
| A subsystem that runs and produces nothing is a failure | RS9; the evidence-gate precedent (`completed_unverified`) extended: no report + no failure account = `failed` |
| A protection layer that silently succeeds is not trusted | §5.5 step 4 verifies every scrub deterministically; §5.6's probe battery, overturn ledger, and sampled review make wrong judgments visible, not assumed impossible |

---

## 10. Build order

Shape on record before code; one commit each, testable in isolation. Revised: the judgment
gate is foundational and its floor-preserving pieces come early.

1. **P1–P3** — the retrieval substrate (search key + href fix, SSRF guard, fetch cache).
   Test: a query whose results are fetched end-to-end from the returned URLs; an SSRF probe
   refused.
2. **P4–P6** — the seams (proactive push on completion, scheduler tick for drain + lease
   expiry). Test: a spawned task's completion appears in chat unprompted; a deliberately
   abandoned lease is reclaimed.
3. **Judgment gate, floor first** — §5.5 steps 1–2 and 4 (composition + deterministic
   verification) with the judgment slot stubbed to "deterministic only"; the probe battery;
   the extended ledger fields. Test: the battery passes with judgment disabled; behavior is
   byte-identical to today's gate.
4. **Judgment live** — §5.4's verdict call, the overturn ledger, the kill switch, P7.
   Test: the headline fixture that today classifies TIER_3 survives as ABOUT_THE_WORLD; a
   planted first-person legal paragraph is scrubbed then sent; a planted watchlist token
   blocks the send; disabling the setting restores today's behavior exactly.
5. **Objects + harness skeleton** — commission/plan/finding/report on disk; the grind loop
   with budgets, against a stub search backend. Test: golden commission replay is
   deterministic.
6. **Scoping with the protection fork** — RS2 through the judgment gate, both scopers behind
   one interface, the proposal showing the protection plan. Test: a commission with planted
   never-send material scopes locally and says so; a protectable one scopes on Claude with
   scrub kinds shown.
7. **Grind live** — Stage C against the real substrate. Test: a real question produces
   findings with receipts that verify.
8. **Synthesis under lease + Claude synthesis** — Stage D through the work queue,
   warn-before-silence firing, the promotion conditions. Test: the drain report shows the
   amortized load; a draft with a planted kill-rate promotes to Claude and the colophon says
   so.
9. **Verification + delivery** — RS5 and Stage G's three steps. Test: a planted unreceipted
   claim is struck and reported; the wiki page lands with clickable links; the styled page
   opens in a new tab; the proactive message arrives.
10. **Escalation ladder + audit surface** — E-conditions with judgment-gated payloads; the
    "What left the machine" panel and weekly digest. Test: a planted unprotectable escalation
    converts to ask-Stephen; the panel shows the overturn a fixture run produced.

---

## 11. The seven questions, answered

Stephen answered on 2026-08-17. Resolutions first, with his words where they carry the
reasoning; the questions are kept verbatim below so the resolutions have their questions
attached rather than arriving as bare assertions.

| # | Answer | Consequence |
|---|---|---|
| **Q1** | **Yes — buy the general web-search key.** *"Friday must have web searching and scraping capabilities… this must be a feature of our system."* | P1 upgraded from repair to requirement; DDG demoted to fallback; clickable-link citations become an end-to-end requirement |
| **Q2** | **Direct write — then styled, then surfaced.** *"Reports should land direct and then get formatted into Friday's page style, then opened in a new tab + visible in the workspace."* | §3.7 is now three steps: land, style, surface. The approval queue is not in the path |
| **Q3** | **Yes — quotes may reach Claude, keyed on provenance.** *"Quotes are fine to reach Claude."* And the larger verdict: *"keywording is insufficient; we need a judgement call and a classification system… it should work with the PII scrubber so we don't lock cloud models out completely."* | §5.7 extends the news registry to research fetches; §5 exists — the judgment gate replaces keyword-only sensitivity for every cloud call in the system |
| **Q4** | **Asked badly; not re-asked.** | Default stands (scoping + 2). RS7 now carries the plain-language sentence about what the number controls and what he'd notice if it were wrong |
| **Q5** | **Claude frames when the scrubber can protect him and local would be inferior.** *"Claude should see it if the PII scrubber can protect me and if the local models may give an inferior answer."* | RS2 rewritten: the local-only vault fork is retired; scrub-then-escalate is the rule; the proposal states the protection plan up front |
| **Q6** | **Match Claude.ai's Research feature.** *"Default size for deep research should be about the same as what Anthropic will give me if I ran a report on Claude.AI."* | Established (§12): advanced Research runs up to 45 minutes and consults hundreds of sources. Defaults sized to that: ~10 sub-questions, ~80 full fetches, hundreds of results consulted, 45-minute soft wall clock (§3.3) |
| **Q7** | **Yes — silent single lookups.** *"Friday may silently treat that one search and just do it, especially if it is necessary to complete a task I have assigned."* | RS1 carries the carve-out; the boundary stays testable (one sub-question = a lookup) |

On everything not explicitly asked: *"go with your reads."* The reads taken under that
license, so they are visible rather than smuggled: the never-list contents (§5.3), the
judgment prompt's err-private default (§5.4), the appeals-court placement (judgment consulted
only where keywords would withhold, §5.4), the overturn-ledger-plus-weekly-digest audit shape
(§5.6), Claude-synthesis promotion conditions (§3.4), and the express lane (§3.4).

---

**The questions, as originally asked (first draft, 2026-08-17):**

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
- Anthropic — *Introducing Research* (claude.com/blog/research): agentic multi-search with
  "easy-to-check citations"; no quantitative figures in the announcement itself.
- Coverage of the advanced Research upgrade (runtime and scale figures used for Q6): *"Claude's
  AI research mode now runs for up to 45 minutes before delivering reports"*, reporting
  investigation across "hundreds of internal and external sources"
  (https://tagteam.harvard.edu/hub_feeds/3382/feed_items/13720721/content; corroborated by
  https://unmarkdown.com/blog/claude-research-explained). **INFERRED** working figures — the
  primary announcement gives none; if live use of Claude.ai Research shows different scale,
  the defaults follow the observation.
- [`docs/design/residency-policy.md`](residency-policy.md) — seats, budgets, the Arbiter,
  R1–R10.
- [`docs/design/symphony-of-intelligence.md`](symphony-of-intelligence.md) — S1–S4, the work
  queue, measured chain/structured-output/overhead evidence.
- Commits read for this document: `ca4ee8c` (news provenance exemption), `33fa717`
  (retrieve-cite-then-ask), `dcf8caf` (unattended work local, live tool lines, `on_route`),
  `b10841c` (warn-before-silence), `218fca5` (batch under one lease).
