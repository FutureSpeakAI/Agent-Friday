# A findings graph for Friday

**Status:** design only. Nothing here is built. Written 2026-08-23.
**Scope constraint, firm:** no graph database. Files, `jq`, and Git.
**Source:** *Graph Engineering: A Crash Course*, read in full 2026-08-23.

---

## 0. What I actually read and ran, and what does not exist

Establishing this first, because the rest of the document depends on it and
because one of the two things I was asked to do could not be done.

**The course: read in full.** All 16 concepts, Parts 1–7, the dogfooding
section, the eight projects and the sources. `web_fetch` timed out against that
host twice, so it was read through the browser instead.

**The lab: it is not published.** The course says, in a highlighted block:

> `git clone https://github.com/panaversity/agentfactory-labs.git`
> `cd agentfactory-labs/crash-course/graph-eng`
> `./verify.sh # runs all 17 demos and asserts each one`

That path does not exist. Cloned, checked, and confirmed four ways:

- `crash-course/` contains exactly one course directory, `loop-eng`, with five
  projects. There is no `graph-eng`.
- `crash-course/README.md` lists one course: Loop Engineering.
- `git ls-remote --heads origin` returns 11 branches; none carries a
  `graph-eng` tree.
- `grep -ril "graph-eng\|agent-forgets\|grounded-checker\|nodes-edges"` across
  the whole repository returns nothing, and there is no `verify.sh` anywhere.

`github.com/panaversity/agentfactory` — the likely docs source — is not
publicly readable.

So **no demo was run, because there is nothing to run.** Every concept in this
document was read, not executed. That is a weaker basis than the brief asked
for and it should be weighed as such. It is also, precisely, the failure this
whole document is about: a confident instruction whose referent does not exist,
restated downstream by a web search summary that told me the lab was there
because the course said so.

**Two things named in the brief I could not inspect.** There is no `lessons.md`
reachable from this machine, and no file matching *execution sheet* in the
repository. Both are described as existing spines to extend. I have designed
around the artefacts I could read; if either exists elsewhere, this design
should be revised against them rather than beside them.

---

## 1. The case against, answered from the record

The brief poses the honest question: a human relay got two things wrong, a
session that checked caught both, so the catching worked **with no formalism at
all**. Does a typed record catch a third class of error that neither the human
nor the checking session would have found?

**Yes, and the record contains a clean matched pair that proves it.**

### The pair

`KNOWN_ISSUES.md` §3 carries a full subsection on `function_manager`: a role in
the seat contract with a residency class, a context budget, a settings default
and a UI label, which **nothing in `src/` consults**. It was found, written up
carefully, and ratcheted.

`memory_manager` is the same defect exactly. Declared in `residency_policy.py`,
defaulted in `core/__init__.py`, mapped in `seat_binding.py`, labelled in
`routes/intelligence.py` — and consumed by nothing. Its actual job, embedding,
is done by `all-MiniLM-L6-v2` on the CPU, pinned by
`conversation_memory.py:44`, which refuses to write if the model changes.

**`memory_manager` appears nowhere in `KNOWN_ISSUES.md`.** Verified by grep:
zero occurrences.

That is the third class, and it is not hypothetical:

> A prose ratchet records **instances**. A typed record records **classes**.
> The instance was caught. Its identical twin, four files away, was not — and
> could not be, because prose has nowhere to put "this is one of a kind of
> thing, here is the predicate, show me the others."

Neither the human relay nor a checking session found `memory_manager`. I found
it only because a separate question sent me looking. A record with a
`consumed_by` field turns "did anyone notice?" into `jq`.

### It has happened before, which makes it a pattern rather than an anecdote

- **Stale inventory, twice.** `provisioning-report.md:79` documents an
  orchestrator inventory that listed models as absent while they were installed:
  *"was stale. Verified by fresh `ollama list`."* Today I found
  `llama-cpp-brain.provider.json` declaring one model nobody has
  (`qwen3.6-35b-a3b-iq4nl`) on port **8081**, which nothing is listening on,
  while the real seat serves `gemma4:12b` on **8090**. Same class, months apart,
  nobody connected them.
- **A rule applied in one place and not the others.** `model_plan.py:69` states
  it plainly: *"if a model is not consumed, it is not installed."* It is
  enforced for the installer (`VAULT_MODELS = ()`, with a test). It is not
  enforced for the seat contract, which is how two unconsumed roles kept their
  seats.
- **An artefact produced and never consumed.** `gguf_extract.extract` has copied
  the vision projector out beside the weights since the projector work landed,
  and returns it in the result as `projector`. `residency_arbiter._spawn` never
  passed it. The 160 MB file sat next to the weights while the seat answered
  *"image input is not supported"* to every picture.
- **Within one night.** `KNOWN_ISSUES.md` §1: seven instances of one comparison
  bug found in a single session, *"including in tooling written that same night
  to investigate the others."*

### Where the case against is right, and it is right about a lot

**The checking worked.** Both relayed errors were caught with no schema, no
graph, and no tooling — by one session reading the source. Any honest reading
has to concede that the cheapest intervention available is *"check the claim
before repeating it,"* and it already works.

**The course itself says stop.** Concept 15's test: skip the graph when tasks
are independent, answers come from one document at a time, relations are fixed
and simple, and provenance is not required. Three of those four are false here —
provenance is exactly what was missing, sessions do exchange facts, and
relations are evolving — so the test says build, but it says build **small**.

**A graph amplifies the builder's judgment, including the mistakes.** Concept 15
again. A findings file filled carelessly is worse than no findings file, because
it looks organised. `KNOWN_ISSUES.md` is currently good *because* a human
curates it slowly.

**Therefore the honest conclusion is narrow.** The value is not "sessions can
query a knowledge graph." It is one specific thing: *a claim carries whether
anyone checked it, and how.* Everything below is sized to that and nothing more.

---

## 2. What already exists, and what it cannot do

| Artefact | What it is | What it cannot do |
|---|---|---|
| `KNOWN_ISSUES.md` | The ratchet. Curated, honest, genuinely load-bearing. | Prose. Cannot be queried by class. Records instances, not predicates. Has no field for "verified how". |
| `docs/audits/*.md` | 18 files, ~450 KB, the real institutional memory. | Findings are buried in narrative. No two audits share a shape. Nothing links a claim to the command that proved it. |
| Git history | The commit DAG, free, already correct. | Says what changed, never why it was believed. |
| `tests/golden/residency/` | Frozen expectations with a regeneration procedure. | Covers placement only. Nothing else in the repo has this shape. |
| `evidence` in the audits | Ad-hoc: pasted `nvidia-smi` output, exit codes, line refs. | Sometimes present, sometimes not, never in a fixed place. |

The gap is one column wide. Every one of those artefacts records **what was
found**. None records **how strongly it is known** in a form a machine can sort.

---

## 3. The design

### 3.1 One file, one record type

`docs/findings/findings.jsonl` — one JSON object per line, append-only.

JSONL rather than the course's `claims.json` array for one reason: two sessions
appending to an array rewrite the whole file and one loses. Appending a line
does not. The course's own upgrade note says a single rewritten file "starts
hurting somewhere around ten thousand" claims; this repo will not see a hundred
this year, so the file is not the risk. Concurrent append is, and JSONL removes
it for free.

```json
{
  "id": "finding_2026-08-23_display-baseline-exceeds-card",
  "subject": "hardware_profile.live_display_mib",
  "predicate": "returns_value_exceeding_physical_vram",
  "object": "26463 MiB reported on a 12282 MiB card",
  "status": "verified",
  "provenance": {
    "kind": "measurement",
    "command": "Get-Counter '\\GPU Process Memory(*)\\Dedicated Usage'",
    "captured": "2026-08-23T15:37:09Z",
    "ref": "docs/audits/intelligence-warnings-inventory.md#the-finding"
  },
  "anchor": {
    "kind": "independent_instrument",
    "command": "nvidia-smi --query-gpu=memory.used,memory.free",
    "reading": "1347 MiB used, 10666 MiB free"
  },
  "produced_by": "session_2026-08-23_intelligence-warnings",
  "supersedes": null,
  "affects": ["residency_policy.gpu_budgets", "routes/intelligence"],
  "created": "2026-08-23"
}
```

### 3.2 `status` is the whole point

Four values, and the definitions are the deliverable:

| `status` | Means | Example from this week |
|---|---|---|
| `verified` | Executed and observed. Someone ran it and read the output. | gemma4:12b emits tool calls — 10/10 against the live registry. |
| `inferred` | Read from code, flags, or capability declarations. Not run. | "gemma4:12b has native tool calling" as `KNOWN_ISSUES.md:328` stated it *before* today. True, and untested when written. |
| `inconclusive` | Measured, and the measurement failed for harness reasons. **Never a refutation.** | TwiL-LM3 on CPU: no case completed in 12 min because the tool prompt is 8.6k tokens. Says nothing about the model. |
| `refuted` | A later finding supersedes it, with its own evidence. | "The seat can accept images" → refuted by llama.cpp's own HTTP 500. |

The `verified`/`inferred` split is the field the brief asked for. The
`inconclusive` value is the one I would fight for hardest: this repo has already
recorded a model as 1/10 when nine of ten cases timed out, and that record
overwrote a real result. `model_seat_gate.save_status` had to grow a special
case to stop it. A status enum makes that structural instead of local.

### 3.3 Anchors, and the strict definition

The course is exact and it matters more here than anything else:

> A run log is an anchor **only** where the cited lines are captured output from
> something outside the model. An agent's own prose inside a log file is model
> output wearing a filename.

Permitted `anchor.kind`:

- `independent_instrument` — a reading from a tool that was not part of the
  thing under test. The canonical example is this week's: the display-baseline
  bug was found by reading `nvidia-smi` **outside** the harness whose number was
  in doubt.
- `exit_code` — a process exited N.
- `committed_artifact` — a file at a git ref, cited by hash.
- `third_party_error` — an error string produced by software we did not write.
  llama.cpp's *"image input is not supported — hint: you may need to provide the
  mmproj"* is worth more than any amount of my reasoning about `--mmproj`.
- `human_observation` — Stephen saw it. Named as an anchor because it is one.

Explicitly **not** anchors: another session's summary, a previous finding, an
audit document, anything I wrote. A finding whose anchor cites another finding
is the circular graph in JSON, and the audit for it is Concept 13's: follow ten
findings to their leaves and count how many bottom out in something no model
produced.

### 3.4 Frozen nodes, stated as data

`docs/findings/FROZEN.md` — rules no session may change, each with the incident
that produced it. Not new rules; the ones already earned:

1. Nothing may claim success it has not verified. (`KNOWN_ISSUES.md` §1)
2. Many warnings at once is wallpaper. (§1, added today)
3. A harness failure is not a subject failure. (`model_seat_gate`)
4. An unmade choice is not a fault.
5. Golden files move only with a stated reason in the same commit.

Frozen means: a session proposing to change one must say so to the human first,
in those words. It is a social rule with a file behind it, which is what
`prepare.py` and `check.py` are in the course.

### 3.5 Seeding

Do **not** bulk-extract 450 KB of audits with a model. That is Concept 15's
warning — extraction errors would outweigh traversal value, and the
`inferred` findings would swamp the `verified` ones on day one.

Seed by hand with the findings from **this week only**, roughly 20, each already
carrying real evidence. That is enough to answer the question the file exists
for: *has anyone actually checked this?*

### 3.6 What reads it

A section in the repo's agent instructions: before asserting anything about the
seat contract, residency, egress or the model suite, grep
`docs/findings/findings.jsonl` for the subject. If a finding exists with
`status: verified`, cite its id. If one exists with `status: inferred`, treat it
as unchecked and say so.

No MCP server, no database, no framework. The course is explicit that MCP
becomes the answer only when the store leaves the folder.

---

## 4. What this deliberately does not do

- **No entity resolution.** The Cookbook's Concept 7 is the highest-risk stage
  in the course — a false merge is its catastrophic failure. Subjects here are
  code identifiers, which are already canonical. Not needed, so not built.
- **No model-driven extraction.** Findings are written by the session that
  established them, at the moment it established them.
- **No commit DAG layer.** Git already is one.
- **No governance graph.** There is one human and one agent at a time. Concept
  11's wiring is for many loops.
- **No database, now or at the sizes this repo will reach.**

---

## 5. Cost, and what would make this not worth doing

**Cost:** one file, one schema doc, one `FROZEN.md`, one paragraph of agent
instructions, ~20 seeded records. Perhaps two hours. Nothing ships to the user;
nothing enters the 959 MB installer; no runtime code changes.

**Kill it if:** after four weeks, no session has cited a finding id, or the
`inferred` count exceeds the `verified` count. The second is the real tell — it
would mean the file had become a place to record impressions with a schema
around them, which is worse than the prose it replaced.

**The strongest argument against remains unrebutted:** every error caught this
week was caught by a session that read the source rather than trusting a
summary. This file does not replace that. At best it tells the next session
which claims have already been through it.

---

## 6. Open questions for review

1. `lessons.md` and the execution sheets — I could not see either. Does this
   duplicate something that already works?
2. Is `status` enough, or does the `verified` case also need *what would falsify
   it*? I lean no: unfalsifiable-claim discipline is a reviewer's job, not a
   field.
3. Should `KNOWN_ISSUES.md` entries link to finding ids, or stay independent? I
   lean independent until the file has earned its place.
4. The lab does not exist. Worth telling the course's authors — the instruction
   block is confidently wrong, and it blocked two sessions before this one.
