# Open source, weighed against the six — what to adopt, what to defer, what to keep

> **Re-check 2026-08-29.** Written 2026-08-24 and verified against the tree
> as it stood that day; the repo has since shipped through **v5.7.0**. The
> adopt / defer / keep calls here are **judgement, not fact**, and are left
> exactly as the author argued them.
>
> No claim in this document was found to be falsified by the 5.6.5-5.7.0
> work, which touched the installer, the vault passphrase, onboarding and the
> knowledge graph rather than any of the six problems weighed here.
>
> **Caveat on citations:** the `file:line` references were spot-checked, not
> re-verified in full, and line numbers drift as files change. Treat a
> citation as naming the right file and the right claim — not necessarily
> the right line.

**Date:** 2026-08-24
**Branch:** `higgsfield-integration` (the working tree's active branch). This document
touches no code and no configuration; it is a position paper.
**Status:** design/position. **Read-only pass.** `app.html`, `index.html` and the mail
triage work belong to a concurrent Opus 5 session and were not modified.
**Question asked:** "should we search the open source marketplace and find popular
solutions for these?" — six named problems, scoped as *why is Friday hand-rolling
infrastructure that mature open source already solves?*
**Inherits:** [`workflow-run-forensics-2026-08-24.md`](../audits/workflow-run-forensics-2026-08-24.md)
(the incident that produced five of the six), [`switchyard-position.md`](switchyard-position.md)
(the last adopt/defer decision, and the format this follows),
[`residency-policy.md`](residency-policy.md) (seats, Arbiter, R1-R10).

**Evidence registers:**
- **VERIFIED** — read in the working tree or in the installed venv during this pass
  (2026-08-24), with the file, line, or byte count given.
- **MEASURED** — a number taken off this machine (`du`, `ls -la`, `wc -l`).
- **INFERRED** — a conclusion from verified facts, with the reasoning shown.
- **UNKNOWN** — not determined; the check that would settle it is named.

---

## 0. The position, in one paragraph

Five of the six problems are **not** cases of Friday hand-rolling something mature open
source already solves. Four of them are cases of Friday having *already built* the thing
and then not wiring it in, not installing it, or excluding it from the shipped binary: the
egress gate already integrates Microsoft Presidio and a sentence-transformers layer, and
ships with neither; the settings loader already does atomic writes, BOM-tolerant reads and
refuse-on-unparseable saves as of this morning; there are already three durable telemetry
stores on disk; and there is already a SQLite cost ledger with a budget alert path. What
is genuinely missing across all of them is smaller and more boring than a library:
**declared schemas, one source of truth for prices and context windows, and persistence
for one Python dict.** The two places where open source clearly beats what is here are
narrow and cheap — a glTF loader for a three.js copy that is *already vendored and already
loaded*, and LiteLLM's model price/context-window map used **as data, not as a
dependency**. On the router: adopting a mature router is not realistic and would not have
prevented today's incident. Three of the four router failures Stephen listed live in the
VRAM residency layer, which no general-purpose router models at all; the fourth — the
unannounced context-overflow escalation — is the one LiteLLM would genuinely have caught,
and that single behaviour can be borrowed in a day without adopting the router.

---

## 1. The stack, verified before anything is recommended

| Fact | Value | Register |
|---|---|---|
| Language / runtime | Python; `requires-python >=3.10`, venv runs **3.13.3** | VERIFIED `pyproject.toml`, `venv/Scripts/python.exe --version` |
| **Shell** | **Not Electron.** Flask server + `webbrowser.open(SERVER_URL)` into the user's default browser | VERIFIED `src/agent_friday/friday_tray.py:168`; `package.json` says outright "The application itself is Python; this file exists only to pin the test tooling" |
| UI | one 1,517,631-byte `index.html`, built from `ui_parts/` by `ui/build_ui.py`; React 18 + three.js served from `static/vendor/` | MEASURED |
| Packaging | PyInstaller **onefile**, `AgentFriday.spec`, `console=True` | VERIFIED |
| Node in the product | none. `package.json` pins `@playwright/test` only | VERIFIED |
| License | MIT (repo and `pyproject`) | VERIFIED |
| Dependency policy | explicit, commented, capability-grouped extras; every heavy dep is opt-in and every consumer "degrades gracefully if absent" | VERIFIED `requirements.txt`, `pyproject.toml` |
| GPU budget | RTX 4070, 12,282 MiB; two llama-server processes hold most of it | VERIFIED forensics §3.2 |
| Host RAM | 32,620 MiB (from the residency fingerprint) | VERIFIED |

**Three consequences that constrain every recommendation below, and that a
recommendation written without reading the tree would have got wrong:**

1. **There is no Electron main process and no pinned Chromium.** Anything in the UI runs
   in whatever browser the user has set as default. WebGPU cannot be assumed. WebGL2 can
   (Chrome/Edge/Firefox have shipped it for years), and the existing Galaxy view already
   proves WebGL works on this machine.
2. **The shipped binary is not the dev venv.** `AgentFriday.spec` excludes `torch`,
   `sentence_transformers`, `transformers` and `tokenizers`. Any recommendation that
   depends on those is a recommendation for *your* machine only, unless the spec changes —
   and changing it is not free (see §3.2).
3. **A background service is a much bigger ask here than in a server app.** There is one
   process, one tray icon, one browser tab, and a PyInstaller onefile. Every candidate
   below that needs a second daemon is scored down for that alone.

---

## 2. Problem 1 — the 3D viewer

### 2.1 What is actually true

`generate_3d` exists and is routed (`services/higgsfield_generate.py:40`), `.glb` is an
accepted creation type with a magic-byte check for `glTF` (`services/creative_store.py:63`),
and `MODALITIES` includes `"3d"` (`services/model_catalog.py:59`). **VERIFIED.**

`grep -oin '\.glb|model-viewer|gltf' index.html ui_parts/app.html` returns **nothing**.
**VERIFIED.** Stephen's description is exact: the file is written, catalogued and
validated, and no code path renders it.

What *is* already there: `static/vendor/three-r128.min.js`, **603,445 bytes**, MIT,
"Copyright 2010-2021 Three.js Authors" — plus six r128 post-processing addons, all seven
already `<script>`-loaded by `index.html` for the Galaxy view. **MEASURED / VERIFIED.**

### 2.2 Candidates

| Candidate | What it replaces | Maintenance | License | Weight added | Integration |
|---|---|---|---|---|---|
| **three.js r128 `GLTFLoader` + `OrbitControls` addons** | nothing — it *completes* the copy already shipped | pinned artifact; r128 is 2021, but glTF 2.0 core has not moved | MIT | **~75 KB** of plain JS into `static/vendor/`, zero new Python deps, zero new frameworks | ~4 h: one React component, one canvas, reusing existing renderer conventions |
| Google `<model-viewer>` | the loader *and* the camera/lighting/AR chrome | Apache-2.0, 8.2k stars, active | Apache-2.0 | ~350 KB min+gzip **and it bundles its own three.js** — a second copy beside the 603 KB already loaded | ~2 h, but see below |
| Babylon.js / PlayCanvas viewer | the whole renderer | active | MIT / MIT | 1-2 MB, a second engine | not justified |

### 2.3 Verdict — **ADOPT** the three.js addons; **reject** `<model-viewer>`

`<model-viewer>` is the better product and the wrong answer here. It is genuinely easier
if you have no 3D stack; Friday has one, loaded on every page. Adopting it means shipping
two copies of three.js inside a 1.5 MB single-file UI, in an app that vendors everything
offline by policy. The addons are the same upstream project, the same license, and 4.6× the
weight of nothing.

**Flagged, honestly:**
- r128's `GLTFLoader` handles glTF 2.0 core and the common KHR material extensions. It
  does **not** handle Draco or Meshopt compression without two further loader files.
  Whether Higgsfield's `generate_3d` emits compressed GLBs is **UNKNOWN** — the check is
  one `xxd | head` on the first output file, and it should be run before the component is
  written, not after.
- A WebGL canvas competes for the same GPU that two llama-servers are sitting on. One
  static model viewer is negligible; a continuously-animating one on the creations grid is
  not. Render on demand, pause when hidden — the same rule already established for the
  holo scene.

---

## 3. Problem 2 — the egress gate

### 3.1 The failing test does not mean what it looks like

`tests/unit/test_egress_gate.py:147::test_tool_definitions_scanned` asserts that a tool
named `vault_read` with the description "read SSN and financial records" comes back
redacted. It does not. **But the code is deliberate and the test is stale.**

`services/egress_gate.py:701-742` gates a tool description only when
`name.startswith("mcp_")`. The docstring, dated 2026-08-21, gives the reason in full:
first-party tool descriptions are static text compiled into this repository, they cannot
leak the vault because they were never in it, and classifying them meant any description
containing "contact", "family" or "calendar" was blanked — so the model was handed a list
of tools it could not read, on every cloud-fallback turn, for an unknown period. **VERIFIED.**

So: **this is test drift, not an exfiltration hole.** The premise "tool *descriptions*
containing PII reach the cloud unredacted" is true only of text this repository authored,
which contains no user PII by construction. The correct action is to **fix the test to
assert the current contract** (first-party passes, `mcp_`-prefixed gates) — not to buy a
PII library to make an obsolete assertion pass. If that contract is wrong, the argument to
make is that a first-party description could ever interpolate user data, and today none
does; a grep for f-strings in tool-definition construction would settle it and is
**UNKNOWN** at this writing.

### 3.2 The real gap, which is worse and cheaper to fix

Friday is *not* hand-rolling PII detection. `services/sensitivity_classifier.py` is a
four-layer design and **Layer 2 is already Microsoft Presidio** (`_load_presidio()`,
`AnalyzerEngine`, an entity-type map, score thresholds at 0.7/0.8). Layer 3 is
sentence-transformers embedding similarity against 22 curated exemplars. **VERIFIED.**

Then:

- `presidio-analyzer` is **not in `requirements.txt`, not in `pyproject.toml`, and not
  installed in the venv.** **VERIFIED.** Layer 2 has never run.
- `sentence_transformers` *is* installed here, but is in the PyInstaller `excludes`.
  **VERIFIED.** Layer 3 does not exist in the shipped `.exe`.
- Layer 4 posts to `gemma4:latest` on `localhost:11434` and is off by default
  (`use_llm=False`).

**INFERRED, and this is the finding of §3:** in the distributed build, the egress gate is
**Layer 1 only — four regexes and two keyword lists.** Every architectural claim in the
module docstring about defence in depth is, in the artifact users receive, a claim about
code that cannot load. The startup self-test (`egress_gate.startup_self_test`) will still
pass, because its probe is an SSN plus bank-account keywords — exactly what Layer 1
catches.

### 3.3 Candidates

| Candidate | What it replaces | Maintenance | License | Weight | Latency | Integration |
|---|---|---|---|---|---|---|
| **`presidio-analyzer`** | Layer 2, already coded | 10.6k stars, Microsoft, active | MIT | analyzer is small; the **spaCy model is the cost** — `en_core_web_sm` ~12 MB, `en_core_web_lg` ~560 MB; spaCy itself ~40 MB with compiled deps | vendor profiling cites **<10 ms per 1,000 tokens** with a spaCy backend; transformer backends are far slower | **~0 h of code.** A `pip install`, a model download, and a `pyproject` extra. The integration was written a year ago |
| **GLiNER-PII (ONNX, quantized)** | a better Layer 2, or a Presidio recognizer | active; `knowledgator/gliner-pii-{edge,small,base}` published | Apache-2.0 (**verify per checkpoint**) | model file only — **`onnxruntime` is already a hard dependency** for Piper TTS | BERT-base-class on CPU; edge/quantized variants target exactly this case | 1-2 d, or ~2 h via Presidio's built-in `GLiNERRecognizer` |
| `scrubadub` | Layers 1-2 | thin, slow-moving | MIT | light | fast | a downgrade on what is already coded |
| Commercial PII APIs | all layers | n/a | n/a | n/a | network | **disqualified on the spot** — sending content to a cloud service to decide whether it may go to a cloud service is the circularity the module docstring already forbids |

### 3.4 Verdict — **ADOPT** Presidio (as an extra); **ADOPT LATER** GLiNER; fix the test

Presidio is the rare case where the integration cost is genuinely near zero because the
integration already exists. Make it a `[privacy]` extra so the dependency policy holds,
and pick `en_core_web_sm` — the accuracy delta to `_lg` does not justify 560 MB in a
desktop installer.

**Be honest about the false-positive cost, because it is not hypothetical here.** This
codebase has already been burned three separate times by over-broad classification, and
each scar is in the source: "courtesy" matching "court" and "incoming" matching "income";
the product term "Sovereign Vault" nuking Friday's own system prompt to TIER_3; and
"family picture-book aesthetic" routing a storybook prompt to a local seat that could not
hold the payload, killing the turn. Presidio's `PERSON`, `LOCATION` and `DATE_TIME`
recognizers are precisely the ones that fire on ordinary prose — the published critique
that it cannot separate "Black Friday" from a sensitive date is the same class of error.
The existing code already anticipates this: those three types map to PRIVATE at a 0.8
threshold, and PRIVATE only escalates to SENSITIVE when a second independent layer agrees.
**Turn it on in shadow mode** — log what it *would* have redacted, on real traffic, before
it is allowed to redact anything. A gate in the hot path of every cloud call that starts
withholding Friday's system prompt is a worse outage than the hole it closes.

---

## 4. Problem 3 — settings persistence

### 4.1 What is actually true — this one is already fixed

Read `core/__init__.py:1877-2025`. As of this morning, `_load_settings_raw` reads
`utf-8-sig`, and on a parse failure logs `RUNNING ON FACTORY DEFAULTS` at ERROR instead of
returning silently. `_save_settings` reads `utf-8-sig`, **refuses the save entirely** if
the existing file will not parse ("A settings write that cannot see the current settings is
not a write, it is an erasure"), deep-merges `capability_routing` per capability,
invalidates the cache before *and* after, and writes atomically: temp file, `fsync`,
`Path.replace`. **VERIFIED.**

That is the whole list Stephen named — atomic writes, encoding handling — minus schema
validation and migrations. There is no library to buy for the part that is done.

### 4.2 The part that is not done, and has already cost something

`DEFAULT_SETTINGS` (`core/__init__.py:1321`) holds **79 top-level keys** and doubles as the
schema by acting as a read-side whitelist:

```python
merged.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
```

The comment directly above it, written by whoever debugged it, states the consequence: a
key not declared there "is written to settings.json successfully and then silently
discarded on every read. The write reports success; the setting does nothing. Both switches
below were added to settings.json and read back as False for exactly this reason, which
meant two kill switches existed that could never be turned on." **VERIFIED.**

Note the asymmetry, which the comment does not mention: `_save_settings` **preserves**
undeclared keys (`merged.update({k: v for k, v in existing.items()})`, unfiltered) while
`_load_settings_raw` **drops** them. So an undeclared key persists on disk forever,
invisible to every reader — the worst of both behaviours.

### 4.3 Candidates

| Candidate | What it replaces | Maintenance | License | Weight | Integration |
|---|---|---|---|---|---|
| **`pydantic.BaseModel`** (not `pydantic-settings`) | `DEFAULT_SETTINGS`-as-whitelist | pydantic 2.12.5 **already installed and already a hard dependency** (via `anthropic`) | MIT | **0 bytes added** | 2-3 d: 79 keys typed, defaults declared once, `model_validate` on load, unknown keys logged rather than swallowed |
| `pydantic-settings` | the loader | active; **already in the venv** | MIT | ~0 | **wrong tool.** Built for env-var/file *ingestion* into a typed object. Friday's settings.json is read-*write* from the UI; pydantic-settings has no opinion on writing, atomicity, or merge |
| `dynaconf` | the loader + layering | active | MIT | ~2 MB | multi-source layering Friday does not need; would replace working code |
| `confuse` | the loader | slow | MIT | small | YAML-centric; no gain |
| Migration frameworks | the missing migrations | — | — | — | **nothing standard exists** for versioned JSON app-config migration in Python. `alembic` is for databases. This is 40 lines of `if version < N` and always will be |

### 4.4 Verdict — **ADOPT** a pydantic schema; **keep** the file layer

Declare the 79 keys as a pydantic model, keep `_save_settings` exactly as it is, and add a
`schema_version` integer with a hand-written migration ladder. The dependency is already
shipped, so the cost is entirely in the typing work, and the payoff is that the
undeclared-key trap — which has already disabled two kill switches — becomes a validation
error instead of silence.

**Do not adopt a config framework.** Every one of them would replace a loader that was
hardened this morning, in a file whose comments are now the best documentation of exactly
how it fails. That is negative leverage.

---

## 5. Problem 4 — the ledger and observability

### 5.1 What is actually true — three durable stores already exist

| Store | Path | Backing | Size |
|---|---|---|---|
| `services/activity_ledger.py` | `~/.friday/activity_ledger.jsonl` | append-only JSONL, rotates at 20 MB | 131 lines |
| `services/work_log.py` | `~/.friday/work_log.db` | SQLite, indexed | 238 lines |
| `services/cost_meter.py` | `~/.friday/costs.db` | SQLite, buffered writes | 484 lines |

**VERIFIED / MEASURED.** The activity ledger is not a toy: it is a field-whitelisted,
metadata-only, privacy-by-construction record — and this morning's forensics document was
reconstructed almost entirely *from it*, minute by minute, across 3,236 lines. It worked.

### 5.2 The gap is one dict

`services/agent.py:1995` — `TASKS = {}`. Three server restarts between 11:46 and 11:49
destroyed every task record from the incident. **VERIFIED** (forensics §0.1, A3). The card
bodies, the per-step `verified` flags and the per-step artifacts are gone; the tool trace
behind each `task_id` survived, because that went to the ledger.

So the problem is not "Friday needs agent observability." It is "one in-memory registry
sits beside three durable stores and was never given a table."

### 5.3 Candidates

| Candidate | What it replaces | Maintenance | License | Weight / service | Integration |
|---|---|---|---|---|---|
| **A `tasks` table in `work_log.db`** | `TASKS = {}` | n/a — stdlib `sqlite3` | — | **0 bytes, 0 services** | ~1 d, and the schema, lock discipline and pruning pattern are already written twice in this repo to copy |
| **OTel `gen_ai.*` attribute names, as a naming convention** | ad-hoc field names | spec-driven | Apache-2.0 | **0** (adopt the vocabulary, not the SDK) | ~2 h of renaming, at the next ledger schema change |
| Arize Phoenix | the ledger + a UI | active, 20.3.0 (2026-08-17) | **Elastic License 2.0 — not OSI open source** | heavy Python stack (FastAPI/uvicorn/SQLAlchemy/alembic/strawberry/pandas) **plus a local web server on a port** | days — and see below |
| Langfuse | the ledger + a UI | MIT, active, acquired by ClickHouse Jan 2026 | MIT | **six services; 4+ cores and 16 GiB RAM minimum**, Postgres + ClickHouse | **disqualified** |
| OpenTelemetry SDK + OTLP | the ledger transport | 1.44.0 **already in the venv** (transitively, via `chromadb`) | Apache-2.0 | present already | OTLP needs a *collector or backend*. There is no SQLite exporter. Adopting the SDK without a backend buys nothing |
| OpenLLMetry / MLflow tracing | instrumentation | active | Apache-2.0 | MLflow is very heavy | both assume a server |

### 5.4 Verdict — **KEEP WHAT WE HAVE.** Persist the dict.

Langfuse asks for 16 GiB of RAM on a machine whose GPU is already ~95% committed and whose
host RAM is 32 GB. That is not a close call.

Phoenix is the serious candidate and still fails on two counts, one of which is legal:
**Elastic License 2.0 is not open source**, and its restriction on providing the software
as a hosted or managed service is a term this MIT-licensed, redistributed desktop
application should not take on casually — the same care the repo already applies to the
Stability AI licence and its "Powered by Stability AI" notice. The second count is that
Phoenix's value is its *UI over many traces from many runs*, and Friday has one user, one
machine, and a forensics workflow that has already demonstrated it can reconstruct a
27-minute incident from a JSONL file with `grep`.

**What the observability space is genuinely worth taking is its vocabulary, not its
infrastructure.** The OTel `gen_ai.*` conventions are still marked *Development*, not
Stable, as of mid-2026 — so do not bet a schema on them — but naming ledger fields
`gen_ai.request.model`, `gen_ai.usage.input_tokens` and so on costs nothing and means a
future export is a projection instead of a rewrite. Two things the space does that Friday
does not, and should copy by hand: **parent/child span ids** (a chain step's records
correlate only through `task_id` today), and **an explicit status on every span**, which is
precisely what forensics §6.1 says is missing — the card fires on any terminal status,
including failure.

---

## 6. Problem 5 — cost control

### 6.1 What is actually true

Both halves already exist, and neither is in the enforcement path.

- `services/cost_meter.py` — per-direction pricing, SQLite at `~/.friday/costs.db`,
  buffered off-hot-path writes, attribution by workspace/kind/schedule/run, and
  **configurable daily/monthly USD thresholds that push a notification at 80% and 100%**.
  Its own docstring: "Stdlib sqlite3 only — no new dependency." **VERIFIED.**
- `services/budget_enforcer.py` — 276 lines, `~/.friday/budgets.db`, atomic
  `reserve_budget` / `release_budget`, monthly and per-task caps, `enforce_hard_stop`.
  **VERIFIED.**
- Higgsfield credits **are** preflighted per generation via `get_cost` before every submit
  (`services/higgsfield_generate.py:133,265`), and the credit figure rides on the result.
  **VERIFIED.**

So "no budget cap" is not quite right: there is a cap engine, denominated in
milliPositrons for the orchestrator, while the cost meter is denominated in USD and the
creative path is denominated in credits. **Three currencies, three stores, no conversion.**
That is the actual defect.

The second defect is worse and is what produced the number in Stephen's brief: `PRICING` in
`cost_meter.py` is a **hand-maintained table of roughly twenty models**, and it carries no
context-window field. Nothing in the send path asks "will this fit?" before it asks "send
it." The 1,435,556-token call at 11:27:21 was not a budget failure. It was a
**context-budget** failure that a budget cap would only have noticed afterwards.

### 6.2 Candidates

| Candidate | What it replaces | Maintenance | License | Weight | Integration |
|---|---|---|---|---|---|
| **LiteLLM's `model_prices_and_context_window.json`, vendored as data** | the 20-row `PRICING` dict | tracks provider price changes continuously; the file is already on this disk at **1,313,681 bytes** | MIT | **1.3 MB of JSON. No import, no dependency, no litellm code** | ~1 d: a loader, a refresh script, a fallback to the existing table |
| `pydantic/genai-prices` | the pricing table | Pydantic org, MIT, 415 commits, recently updated | MIT | small; pure data + matching | ~1 d. Better *matching* logic (historic prices, tiered Gemini pricing, variable daily prices) than raw JSON. **Token-only — no image/video/audio pricing** |
| `tokencost` (AgentOps) | pricing + token counting | active, 400+ models | MIT | pulls `tiktoken` (2.3 MB); calls Anthropic's counting API for Claude 3+ | a network call for token counts is a poor fit for an offline-capable app |
| Helicone / OpenMeter / Portkey | metering | active | varies | **cloud proxy** — every call routed through a third party | disqualified: it inverts the egress model |
| Anything, for generative-media credits | — | — | — | — | **nothing exists.** No open-source library prices Higgsfield/Seedance/Veo credits. That table stays hand-written, and `get_cost` preflight is already the right mechanism |

### 6.3 Verdict — **ADOPT** the price map as data; **keep** the meters

Take LiteLLM's JSON, not LiteLLM. It is MIT, it is already sitting in
`venv/Lib/site-packages/litellm/`, it is the single most-maintained public dataset of
per-model input price, output price **and context window**, and using it as a vendored data
file with a refresh script adds no import, no transitive dependency, and nothing to the
PyInstaller bundle but 1.3 MB.

`genai-prices` is the better *library* and is worth revisiting if price-matching edge cases
(historic pricing, tiered context pricing) start mattering. Today they do not; the file
does the job.

Then do the two things the data unlocks, which are the actual reliability wins:

1. **A pre-call context check.** With a context window per model in hand, the ladder can
   refuse a leg that cannot hold the payload instead of discovering it as a 500. This is
   forensics A5, and it is the highest-value item in this document.
2. **One currency.** Convert credits and milliPositrons to USD at the ledger boundary so
   `budget_enforcer` can cap a Higgsfield generation and an Anthropic turn with the same
   number. Until then there are three budgets and no budget.

---

## 7. Problem 6 — the router. Argued from the code, as asked.

### 7.1 The size and shape of what exists

| File | Lines |
|---|---|
| `services/model_router.py` | 2,652 |
| `services/residency_arbiter.py` | 1,531 |
| `services/residency_policy.py` | 1,504 |
| `routing/model_router.py` | 978 |
| `services/residency_catalog.py` | 709 |
| `routing/provider_descriptors.py` | 681 |
| `services/provider_health.py` | 611 |
| `services/provider_registry.py` | 607 |
| `services/model_plan.py` | 544 |
| `services/model_seat_gate.py` | 479 |
| `routing/ollama_manager.py` | 437 |
| `services/local_seats.py` | 427 |
| `services/seat_binding.py` | 367 |
| `services/capability_router.py` | 158 |
| **Total** | **11,685** |

**MEASURED.** But `services/model_router.py` is not a router. Its 2,652 lines also build
the system prompt, load the vault summary, prune and compress context, index chat turns
into ChromaDB, generate session summaries, fact-check news citations and validate tool-call
integrity. **VERIFIED** from its own function list. There is no clean seam to swap out.

### 7.2 Take the four failures one at a time

| Failure Stephen named | Where it lives | Would a mature router have prevented it? |
|---|---|---|
| Routed to an uninstalled 26B | `local_seats.installed()` counts a GGUF on disk as available (`:118`); `residency_policy:723` picks `gen[0]` — largest wins; `:1011` exempts MoE from the fit rule; `routing/model_router.py:852` admits any model with **no VRAM measurement** | **No.** No general router models GGUF-on-disk vs served-on-a-port vs fits-in-VRAM. This is Friday's own domain, and the bug is `installed != servable` |
| Overwrote the cloud-model choice every boot | `seat_binding.propose()/apply()` never read `cloud_seats_from_settings`, which already exists and is already called by the arbiter at `residency_arbiter.py:951` | **No.** A missing argument in Friday's own seat binder |
| Silent failover to a frontier model on context overflow | `routes/chat.py:885-908` announces it and returns `local_fallback: {model, why}`; **`services/agent.py:230-243` — the background-task ladder — does not**, and that is the path that billed 1,435,556 tokens | **Yes — partly.** LiteLLM's `context_window_fallbacks` with `enable_pre_call_checks` catches the overflow *before* the send. The silence is Friday's own, on one of two ladders |
| Cannot reconcile a llama-server alias against `ollama list` | `local_seats` reads `~/.friday/runtime/models/models.json` and only asks the daemon for *additional* names; `endpoints.json` is not consulted at that level | **No.** This is the two-runtime problem Friday created by running llama.cpp *and* Ollama |

**Three of four are in the residency layer. One of four is a behaviour worth borrowing.**

### 7.3 Candidates

| Candidate | What it replaces | Maintenance | License | Weight | Integration |
|---|---|---|---|---|---|
| **LiteLLM (SDK, `litellm.Router`)** | provider dispatch, retries, cooldowns, fallbacks, `context_window_fallbacks`, cost accounting | very active | MIT | **78 MB installed (MEASURED)**; base deps pull `openai>=2.8` (16 MB), `tokenizers` (7.5 MB), `tiktoken` (2.3 MB), `aiohttp`, `jsonschema`, `jinja2`. **Already present transitively via `headroom-ai[all]`** | **weeks**, honestly. There is no seam — and the egress gate would have to move *inside* litellm's callback chain or be bypassed |
| **LiteLLM as one adapter behind the existing slot** | one transport among several | same | MIT | same, but opt-in | days. `ADAPTER_LITELLM = "litellm"  # optional escape hatch (spec §5.3) — not shipped yet` is **already in `routing/provider_descriptors.py:62`.** The slot was designed for exactly this |
| **llama-swap** | the Arbiter's process supervision | MIT, 5.5k stars, ~570 commits, active, Windows binaries + WinGet | MIT | single Go binary, no deps — **but it is a second daemon** | see §7.4 |
| RouteLLM | quality/cost routing between strong and weak models | research-derived | Apache-2.0 | needs a trained router model | solves a problem Friday does not have |
| OpenRouter / Portkey (hosted gateways) | provider abstraction | active | commercial | cloud | **disqualified** — routing every call through a third party inverts the entire sovereignty premise |

### 7.4 On llama-swap specifically, because it is the closest thing to a real competitor

llama-swap does what the Arbiter does at the process level: reads the `model` field, starts
the right llama.cpp backend if it is not running, routes the request, and unloads after a
TTL. MIT, one binary, Windows-supported, actively released.

What it does **not** do, per its own documentation: **no VRAM budget accounting and no
fit-checking.** It swaps on demand and evicts on a timer. Friday's `residency_policy` is a
pure deterministic function producing either a Placement *or* a Refusal citing a numbered
rule and showing the arithmetic — seven roles arranged across resident/leased/on-demand
classes so they fit 12,282 MiB. That is not what llama-swap is for, and **none of today's
four failures are things llama-swap would have caught.** It would also add a background
service and a second config file (YAML) to an app whose whole delivery story is one
PyInstaller executable.

There is a narrower reading worth naming: llama-swap could replace the *mechanical* half of
the Arbiter — spawn, health-check, terminate, port assignment — leaving `residency_policy`
to decide and llama-swap to execute. That is a real architecture. It is also a rewrite of
`residency_arbiter.py` (1,531 lines, serial transitions, timeouts derived from measured
load times, rollback) to gain a TTL evictor Friday does not want — because the whole
premise of rule R9 is that **a residency layer cannot delegate placement to a scheduler
that makes its own eviction decisions on different criteria**, which is exactly what Ollama
did on 2026-08-14 when it evicted the pinned 12b. Adopting llama-swap would be repeating
that experiment with a different daemon.

### 7.5 Verdict — **KEEP** the residency layer. **ADOPT LATER**, narrowly, one LiteLLM behaviour.

Friday's local-VRAM-residency logic is genuinely special and earns its keep: the
policy/arbiter split, the Placement-or-Refusal-with-arithmetic contract, the
resident/leased/on-demand classes, and R9's refusal to delegate eviction are all responses
to measured failures on this machine, and no general-purpose router models any of it.

The provider-abstraction half is a different story, and the honest verdict is
**"replaceable in principle, not worth it in practice — and not because of the code."** It
is worth it only if there is a seam, and there is not: the egress gate is architecturally
positioned *after* routing and *before* transport (`egress_gate.py` docstring: "The router
can be wrong or bypassed; the gate is the last line of defense"), and litellm owns the
transport. Putting litellm underneath means every cloud call's last line of defence becomes
a litellm callback. That is a security-boundary change, and it should not be made to save
router code.

**What to take now, in one day, without adopting anything:** implement
`context_window_fallbacks` semantics natively. With the price/context map from §6 already
loaded, a pre-call check ("does this payload fit this seat?") turns the 11:18:43 →
11:27:21 sequence from a 1.43M-token surprise into a refusal or a deliberate, announced
escalation. And make `services/agent.py:234` as loud as `routes/chat.py:897` already is.
That is forensics A5 + A6, it is the only router failure a mature router would have caught,
and copying the behaviour costs a fraction of adopting the library.

**The trigger that would change this answer:** if Friday ever needs more than three or four
cloud providers, hand-maintaining descriptors, auth shapes and wire dialects for each stops
being cheaper than litellm's 78 MB. Today there are two that matter.

---

## 8. Ranked by leverage — reliability per hour of work

| # | Action | Buys | New dependency | Est. |
|---|---|---|---|---|
| **1** | Vendor LiteLLM's price/context-window JSON as data; add a **pre-call context-fit check** to both attempt ladders; make the background-task fallback announce itself | Kills the class that produced a 1.43M-token bill and the "silent escalation" complaint in one change. Also replaces a 20-row hand-maintained price table | **none** (1.3 MB data file) | 1-2 d |
| **2** | Persist `TASKS` into a table beside `work_log.db` | The evidence base survives a restart. Everything in forensics §6 becomes verifiable after the fact instead of unfalsifiable | **none** (stdlib sqlite3) | 1 d |
| **3** | `presidio-analyzer` + `en_core_web_sm` as a `[privacy]` extra, in **shadow mode first**; either stop excluding the embedding layer from the frozen build, or state plainly in the docs that the shipped gate is Layer 1 | Turns a documented four-layer gate into an actual four-layer gate. The integration is already written | Presidio (MIT) + spaCy sm (~50 MB total) | 0.5 d + a shadow week |
| **4** | Declare `DEFAULT_SETTINGS` as a pydantic model with `schema_version`; log unknown keys instead of dropping them | Ends the trap that silently disabled two kill switches. Zero install cost — pydantic already ships | **none** (already a hard dep) | 2-3 d |
| **5** | Add r128 `GLTFLoader` + `OrbitControls` to `static/vendor/`; one React viewer component | Turns a shipped-but-dead feature on. Check a real output GLB for Draco/Meshopt first | **none** (~75 KB, same MIT project already loaded) | 0.5 d |
| **6** | Unify currencies — credits and milliPositrons to USD at the ledger boundary — so `budget_enforcer` can cap creative and LLM spend together | Makes the cap engine that already exists actually load-bearing | **none** | 2-3 d |
| **7** | Rename ledger fields to the OTel `gen_ai.*` vocabulary at the next schema change; add parent-span ids and an explicit per-span status | A future export becomes a projection, not a rewrite; chain steps become a tree | **none** | 2 h, opportunistic |

### Explicitly

**ADOPT (now):** three.js r128 glTF addons · LiteLLM's price/context JSON *as vendored
data* · `presidio-analyzer` + spaCy `sm` as an opt-in extra, shadow-mode first · pydantic
as the settings schema (already installed).

**ADOPT LATER (with a named trigger):**
- **GLiNER-PII ONNX** — when Presidio's spaCy NER proves insufficient in shadow mode.
  `onnxruntime` already ships for Piper, so the marginal cost is a model file.
- **`pydantic/genai-prices`** — when price-matching edge cases (historic pricing, tiered
  context pricing) start producing wrong numbers the flat JSON cannot express.
- **LiteLLM behind `ADAPTER_LITELLM`** — when the cloud provider count passes three or
  four. The descriptor slot already exists and is marked "not shipped yet".

**KEEP WHAT WE HAVE (and say why out loud):**
- **The residency policy and Arbiter.** Nothing in the open-source router space models VRAM
  residency, and three of today's four router failures were in code no router replaces. R9
  exists *because* delegating eviction to a daemon already failed once.
- **The three telemetry stores.** They reconstructed a 27-minute incident this morning.
  Langfuse wants 16 GiB and six services; Phoenix is Elastic License 2.0 and its value is a
  multi-run UI for a single-user app.
- **The settings file layer.** Atomic write, fsync, replace, BOM-tolerant read,
  refuse-on-unparseable save. Hardened today. Do not replace it with a config framework.
- **The egress gate's architecture.** Router decides where, gate decides what — gate last
  and unbypassable. This is the reason litellm cannot simply slide underneath, and it is
  worth more than the router code it costs.
- **Higgsfield credit preflight.** `get_cost` before every submit is already the right
  mechanism, and no open-source library prices generative-media credits.

**REJECT:** `<model-viewer>` (a second three.js) · Langfuse · Arize Phoenix (ELv2) ·
Dynaconf/confuse · hosted gateways and cloud PII APIs (they invert the egress model) ·
RouteLLM · llama-swap as an Arbiter replacement.

---

## 9. What this document did not settle

- **UNKNOWN:** whether Higgsfield's `generate_3d` emits Draco- or Meshopt-compressed GLBs.
  One `xxd` on a real output file decides whether §2 is 4 hours or 8.
- **UNKNOWN:** whether any first-party tool description interpolates user data at build
  time. A grep for f-strings in tool-definition construction settles whether §3.1's "static
  text by construction" is airtight.
- **UNKNOWN:** Presidio's real false-positive rate *on Friday's traffic*. No published
  benchmark answers this; the shadow week does.
- **UNKNOWN:** measured `_classify_cloud` latency per turn today. The gate sits in the hot
  path of every cloud call and §3 recommends adding a layer to it; the current baseline
  should be on the record before it changes.
- **Not attempted:** running the failing test, or any test. This was a read-only pass on a
  tree another session is actively editing.
