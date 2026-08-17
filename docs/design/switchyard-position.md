# Switchyard — which parts, in what position, and what they cost

**Date:** 2026-08-17
**Branch:** landed on `deep-research-gate` (the working tree's active branch at commit
time); a position document touching no code — cherry-pick to `residency-policy` freely if
that is its proper home.
**Status:** design/position. **No implementation code exists for this document — it lands
first, by instruction.** The question it answers is not "should we use it" but which parts,
in what position, and at what price.
**Subject:** [NVIDIA-NeMo/Switchyard](https://github.com/NVIDIA-NeMo/Switchyard), evaluated
at v0.2.0, commit `f735d9dd`, cloned and read in full on 2026-08-17 — crates, docs, CI, and
changelog, not just the README.
**Inherits:** [`residency-policy.md`](residency-policy.md) (seats, Arbiter, R1–R10),
[`symphony-of-intelligence.md`](symphony-of-intelligence.md) (the scoping thesis, work
queue), [`deep-research.md`](deep-research.md) (the judgment gate, §5 there).

**Evidence registers:**
- **VERIFIED** — the cited line, command output, or doc was read during the audit runs for
  this document (2026-08-17). Switchyard cites are against `f735d9dd`; Friday cites against
  the working tree at `33fa717`+.
- **INFERRED** — a conclusion from verified facts, reasoning shown.
- **UNKNOWN** — not determined; the check that would settle it is named.

---

## 0. The position, in one paragraph

Switchyard is a well-built, honestly documented, pre-alpha Rust proxy whose two central
ideas — judge-the-completed-turn escalation routing, and typed protocol translation through
a neutral intermediate representation — are worth taking seriously. But the audit says the
right relationship today is **adopt the ideas, not the dependency**: the escalation router,
the one component whose pattern Stephen arrived at independently, is not reachable from
Python at all; the translation engine, the project's best half, has zero Python bindings and
covers the three cloud wire formats rather than the dialects that have actually burned
Friday (gemma's channel format, Ollama's arguments-as-dict, Gemini's SDK); and the metrics
story collapses on inspection because Friday already has three telemetry planes whose holes
are one-argument fixes, while Switchyard's own stats carry known attribution gaps. What
survives is real: a library path that provably never touches a model itself, a benchmarked
escalation prompt that is Apache-2.0 and directly reusable, a metric taxonomy worth copying,
and a coherent future position as a cloud-side normalizer **if** Friday ever multiplies
cloud providers. This document specifies the native adoptions to make now, the guard rules
any future integration must obey (the egress gate decides most of them), and the testable
triggers for revisiting.

---

## 1. What Switchyard actually is — verified against the crates

### 1.1 Three paths, and what each really offers

**The standalone proxy** (`switchyard-server`): an axum HTTP server, TOML-configured in
three layers (`llm_clients` → `targets` → `routes`; a route's `id` is the model name
clients send). Default bind is `0.0.0.0:4000` (**VERIFIED** `cli.rs:16-17`) — loopback is a
flag, not the default. TLS available (rustls), off by default. **No client-side
authentication of any kind** — any process that can reach the socket can spend the
configured provider keys. Upstream credentials: `api_key_env` read at startup, or
`forward_auth` passing the caller's own header through (mutually exclusive, enforced at
config load).

**The embedded server** (PyO3 `switchyard_rust.server.Server`): the same server, in-process
from Python, **loopback-forced with TLS hardcoded off** (**VERIFIED**
`server_bindings.rs:57,61`). This is what the `nemo-switchyard` pip launcher actually runs —
no binary download, no subprocess for the proxy; it then spawns a coding agent (Claude Code,
Codex, OpenClaw) pointed at the local URL.

**The library** (`libsy` via `switchyard_rust.libsy`): the claim "the algorithms hand every
model call back to you" is **accurate and architecturally enforced** — libsy's dependency
list contains no HTTP client, and its own source says so: *"libsy performs no I/O"*
(**VERIFIED** `core/algorithm.rs:228`). The mechanism is a coroutine-style step stream: the
algorithm emits `CallModel` steps carrying the request and a candidate list; the host serves
the call however it likes and posts the result back; `into_parts()` takes the routing
decision without serving anything (decision-only mode, first-class). Python receives
requests and responses as plain dicts.

**But the Python exposure is narrow in three load-bearing ways** (**VERIFIED**
`libsy_bindings.rs`):

1. **Exactly four algorithm factories are bound**: `noop`, `random`,
   `llm_task_classifier` — *capability mode hardcoded* — and `stage_router`.
   **The escalation router is not reachable from Python.** `LlmClassifierConfig` has
   `Capability | Escalation | Custom`; only the first is bound (`libsy_bindings.rs:438`).
2. **The translation engine has zero Python bindings.** OpenAI↔Anthropic↔Responses
   conversion is reachable only by running the server and speaking HTTP to it.
3. **Custom algorithms cannot be written in Python.** The `Algorithm` trait, the
   `FallThrough` composition framework, and the `Classifier`/`Processor` seams are
   Rust-only. You configure four presets; you do not extend them.

### 1.2 The routing algorithms

| Algorithm | Extra model call? | Mechanism | Python? |
|---|---|---|---|
| `passthrough` | no | one target, no decision | no |
| `random` | no | weighted split, seedable | yes |
| `llm_classifier` (capability) | 1 judge call | judge predicts difficulty pre-run; structured verdict; **fails to strong** | yes |
| `llm_classifier` (escalation) | 1 judge call/unlatched turn | see below | **no** |
| `stage_router` | usually none | scores tool-result history (error severity, spinning, progress); tanh-squashed; optional LLM fallback | yes |

**The escalation router, in detail, because it is the pattern Stephen arrived at
independently today** (**VERIFIED** `docs/routing_algorithms/escalation_router_routing.md`
and `llm_class.rs`): every unlatched turn calls the **weak** target and buffers its reply;
a judge model then rules on the *completed* turn — it rates work the weak model actually
did, not a prediction; consecutive escalate verdicts accumulate a streak (default
`confirmations = 2`); below the threshold the buffered weak reply is served; at the
threshold the weak reply is **discarded** and the strong target serves. A latched session
routes straight to strong with no further judge calls. A judge that times out or returns
garbage **fails open** — serves the buffered weak reply, holds the streak, and never
creates a latch. Cost: one weak + one judge call per unlatched turn; the escalating turn
pays weak + judge + strong. Session identity is mandatory above `confirmations = 1`
(in-memory, 1-hour TTL — nothing persists across restart). The packaged trajectory-judge
prompt is ~180 lines, benchmarked, Apache-2.0
(`crates/libsy/src/prompts/escalation/prompt.md`).

### 1.3 The translation engine — the project's best half

Hub-and-spoke through a neutral IR, not pairwise: three codecs (OpenAI Chat, Anthropic
Messages, OpenAI Responses), so all nine directions work and adding a format is one codec
(**VERIFIED** `switchyard-translation/src/engine.rs:58-64`). Streaming translation is real
and separate — the streaming IR carries `ReasoningDelta` and `ToolCallDelta` as first-class
chunks, so reasoning and tool calls survive SSE translation. Tool-call ID charset
normalization is handled explicitly. Same-format hops replay the original body verbatim.

Most notable for this house: **lossiness is typed and policied, not silent.** Every lossy
step emits a `TranslationDiagnostic`, and `LossyConversionPolicy::Reject` turns any loss
into a hard error (**VERIFIED** `policy.rs:18-92`). The shipped lossy edges are multimodal
content and JSON-Schema constraint richness; text, tool calls, and reasoning are the
well-covered core. This is the *disclosed, not substituted* rule, implemented in Rust by
people who never read Friday's house rules — and it is the strongest evidence of the
project's engineering culture.

**What it does not cover:** any non-standard tool-call dialect. There is no parser for
Hermes/XML/channel-style in-band tool calls (**VERIFIED by search** — the only "hermes"
hits are tool-name heuristics in the stage router's signal scorer, and a vLLM doc where the
*upstream server* normalizes the dialect). No Gemini `generate_content`. No Ollama-native
`/api/chat`.

### 1.4 Metrics

Prometheus at `/metrics` (no auth), JSON at `/v1/stats`. Per-model counters for
requests/errors/prompt/completion/cached/reasoning tokens; latency histograms per model;
`switchyard_routing_overhead_ms{algorithm}`;
`switchyard_classifier_fail_open_total{judge_model,reason}` with eight bounded reasons.
Explicit cardinality discipline: *"All labels are bounded enums. No per-request or per-user
values escape into label space"* (**VERIFIED** `docs/internal/metrics_reference.md`).
Ready-made scrape configs and alert rules ship in `examples/prometheus/`.

**But the observability story dents exactly where it matters** (**VERIFIED**
`docs/known_issues.md` 0.2.0): routing-tier attribution is *missing* from stats for
classifier judge failures, escalation decisions, and stage-router fallbacks (#2); session
IDs are not recorded in native session stats (#4); the retry-recovery counter is stuck at
zero (#3). The two algorithms you would adopt it to evaluate are the two its stats
currently cannot fully attribute.

### 1.5 Privacy of the proxy — checked, and clean

The routing log (`--routing-log-file`) records tokens, models, tiers, session IDs — **no
message content, no bodies** (**VERIFIED** `routing_log.rs:46-70`). A grep of every tracing
statement in the server found zero body/content/prompt fields. No response cache exists;
retry buffering is in-memory for the duration of the call; the theoretical-cache tracker is
opt-in via env var and stores only hash digests. One cost-relevant wart: buffered upstream
work continues after a client disconnect, so a cancelled request can still spend provider
money (known issue #1).

### 1.6 Maturity, priced

- The warning, verbatim (**VERIFIED** `README.md:27-30`): *"Switchyard is pre-alpha
  software that is evolving rapidly. The API and algorithms are expected to change
  significantly before we reach v1.0. … Experimental software. Not for production use."*
- **0.1.0 → 0.2.0 was a rewrite, not an increment**: the entire native path (server, all
  four library crates, all algorithms, the Python bindings) is new in 0.2.0; the previous
  Python serving stack is deprecated and already *removed* in unreleased main. Three
  further API breaks landed after the 0.2.0 tag. Expect the same churn into 0.3.0.
- **Test volume is genuinely heavy for a pre-alpha**: 495 Rust tests + 127 Python test
  functions, docs are CI-executed (the doc quotes above are tested claims). Distribution is
  telling: routing and translation cores are well covered; the PyO3 binding layer has zero
  Rust-side tests.
- **Windows: wheels ship (x86_64 + arm64, MSVC), smoke-tested at release** — but every CI
  job runs Ubuntu. **The 495-test suite has never run on Windows** (**VERIFIED**
  `.github/workflows/ci.yml`, `publish.yml:174-198`). Wheels install and import; behavior
  on Stephen's actual platform is unverified.
- Published for real: five crates on crates.io, `nemo-switchyard` on PyPI via trusted
  publishing. Active: last commit the day of this audit; 236 commits total.

---

## 2. What Friday actually has — the two guesses, tested

The commissioning hypothesis was that the least glamorous parts — protocol translation and
metrics — would be the most valuable. The audit says: **half right, and half right**, in an
instructive pattern: both categories are real needs, and in both, Friday's *specific* gaps
are ones Switchyard does not fill.

### 2.1 Telemetry: the guess fails, because Friday is not starting from zero

"No per-route latency/token/error telemetry" turned out to be **false**. Friday has three
planes (**VERIFIED**, all cites in the audit):

1. **`services/cost_meter.py`** — durable SQLite ledger (`~/.friday/costs.db`):
   per-call provider, model, tokens both shapes normalized, cost, workspace/kind/schedule
   attribution, **and a `duration_ms` column that is written but never read by any query**.
2. **`services/provider_health.py`** — in-memory ring (256 calls/provider, 15-min window):
   p50/p95 latency, error rate, availability, a circuit breaker that routing already
   consults, exposed at `/api/providers/health`.
3. **`services/activity_ledger.py`** — append-only JSONL: per-invocation
   model/provider/seat/duration/tokens for every seat including local.

What is actually missing is narrow, and each item is a small native fix at an existing
choke point — not a reason to import a proxy:

| Hole | Fix | Size |
|---|---|---|
| Every OpenAI-compatible + local call writes `duration_ms=0` | pass the argument at `services/agent.py:5490` | one argument |
| `cost_meter` never reads its own latency column | add `duration_ms` to `summary()` | one query |
| The **primary Anthropic chat path** (`_call_claude_agent`) never feeds `provider_health` — an Anthropic outage during chat cannot trip the breaker | add `record()` there | a few lines |
| Failed OpenAI/local calls leave no ledger row | move `_led_done()` into a `finally`; add `ok`/`error` to the whitelist | small |
| Health rings keyed on provider only — two models indistinguishable | key on `(provider, model)` | small |
| **Gemini bypasses everything** — router, meter, health, **and the egress gate**, across seven scattered `genai.Client` sites | bring Gemini inside the router, or accept and document it | real work, and Switchyard does not speak Gemini either |
| No TTFT | requires a streaming path that does not exist anywhere in Friday — new work, not instrumentation | out of scope here |

**What is worth taking from Switchyard here is the taxonomy, not the process**: bounded-enum
labels only; `routing_overhead_ms` as a first-class number (the judgment gate needs exactly
this — measured at ~12 s on the 12b, against the 2–4 s the deep-research spec inferred);
`fail_open_total{reason}` with an enumerated reason set. Friday's planes should adopt those
shapes natively.

### 2.2 Translation: the guess half-holds — right category, wrong dialects

Friday speaks six request dialects and normalizes three response shapes down to two, with
translation living in four places (**VERIFIED**, per the audit): Anthropic Messages native
(SDK, its own agentic loop), OpenAI Chat (`requests.post`, the shared `_oai_agentic_loop`
serving every OpenAI-compatible cloud **and** every Arbiter-owned local seat), Ollama native
(hand-normalized to OpenAI shape — with `finish_reason` hardcoded `"stop"`, so truncation is
invisible on that path), Gemini (seven scattered SDK sites, outside everything), and the
gemma e-series `<|channel|>` in-band tool-call dialect (`services/channel_toolcalls.py` — a
schema-aware parser that exists because the format is deliberately not JSON).

The wounds this surface has actually inflicted are on record: `json.loads()` on an
already-parsed dict silently dropped **every** local tool argument (0/5 → 15/15 when
fixed); `anthropic_to_openai_tools` failures are swallowed by a bare `except` into a
**tool-less turn** — the precise failure mode repeatedly misdiagnosed as "model too weak";
the `"openai"` vs `"openai-compatible"` adapter word (`7acf044`) classified Friday's own
loopback seat as cloud, **sealing vault-tier payloads on their way to the one model allowed
to see them**; and non-string message content is silently dropped on the OpenAI/Ollama
paths.

Now hold Switchyard against that list: its translation engine covers **none of it**. Not
the channel dialect (no non-standard dialect handling at all), not Gemini (no codec), not
Ollama-native (no codec), not the adapter-vocabulary trap (that is Friday's registry, not a
wire format). What it covers superbly — OpenAI↔Anthropic↔Responses with streaming and typed
loss — is the one seam where Friday's two native loops are *already working*, and it is
reachable only over HTTP through the server. **INFERRED:** the adoption-worthy translation
material is the *discipline*, not the code: typed lossiness diagnostics with a Reject
policy, and per-target capability declarations so degradation is deliberate. Friday's four
translation sites should grow toward that shape natively — starting with the two silent
`except` blocks and the hardcoded `finish_reason`.

### 2.3 The egress gate, and what a proxy does to it

The facts that decide this (**VERIFIED**, `egress_gate.py`, `provider_descriptors.py`):
sealing is **per-provider and pre-send** — `seal_outbound` runs at `_seal_or_block` before
the HTTP call, and what it does (drop TIER_3, redact TIER_2, pass local untouched) depends
entirely on *which provider the payload is about to visit*. Local classification is
conjunctive — registry says `local` AND adapter is local-capable AND the host resolves
private, re-checked at call time. A proxy on 127.0.0.1 forwarding to Anthropic does **not**
slip the gate by being loopback — classification must also claim local, and a descriptor
declared cloud stays sealed. The conjunction saved us once already (`7acf044` was the
inverse defect), but the audit also surfaced a live latent mismatch: name-keyed
`is_local_provider()` and dict-keyed `classification_of()` disagree about the Arbiter's
unregistered `arbiter-local` descriptor — harmless today only because the local path skips
sealing before the name lookup would happen. Two authorities over the same boundary is how
7acf044 happened; a proxy would add a third.

---

## 3. The three questions, answered honestly

### 3.1 The routing overlap: total, and that is the argument *against* adoption

Switchyard's escalation router and Friday's judgment-gate direction are the same insight
arrived at independently: **run cheap, judge the actual output, escalate on evidence.**
When two teams converge on a pattern, the pattern is probably right. But convergence on the
pattern is not a reason to import the implementation, and here the implementation argues
against itself three times over:

1. **The escalation router is not reachable from Friday's language.** Python binds
   capability mode only. Adopting Switchyard's escalation router today means adopting the
   *proxy*, with everything §3.2 says about that.
2. **Its judge is a routed model call configured in TOML, blind to Friday's law.**
   Friday's escalation decision must consult the judgment gate (is this payload allowed on
   the strong tier's provider at all?), the residency plan (is the strong seat worth a
   53.5 s wake for this?), and the vault rule. Switchyard's judge answers one question —
   "is the weak model stuck?" — and its router will happily latch a session to a strong
   target the egress gate would refuse. Routing between egress classes is Friday's
   decision, structurally, and cannot be delegated (§3.2).
3. **Friday's seat economics invert part of the design.** Switchyard buffers the weak
   reply and *discards* it on escalation — cheap when weak is a cloud mini-model; locally,
   the discarded work is free anyway but the strong call may cost a lease and a wake, which
   the judge should weigh and Switchyard's cannot.

**The verdict: adopt the design, natively.** The specific artifacts worth taking, with
attribution: the judge-the-completed-turn framing (superior to Friday's current
predict-difficulty framing for multi-turn work); the confirmation-streak latch with
fail-open-and-hold semantics (a judge failure never creates a latch — that is exactly the
fail-safe shape the judgment gate build needs, and it is subtler than it looks); and the
~180-line benchmarked trajectory-judge prompt, Apache-2.0, as a starting artifact for
Friday's own judge seat. Where it lands in Friday: e2b-answers/12b-judges for reflex-tier
work, 12b-answers with an e2b fast-judge for chat, and — the case that matches Stephen's
economics precisely — **cloud-weak/cloud-strong with a local judge**: pay Claude Sonnet
prices by default, escalate to Opus on a local model's verdict, pay the judge in local
seconds. Cost note the deep-research build already measured: judgment on the 12b runs
~12 s, not the 2–4 s inferred; a per-turn trajectory judge must target the e2b or it will
not be per-turn.

### 3.2 The egress problem: the choke point survives in exactly one path

The structural finding, stated as a rule:

> **SW2.** Friday seals a payload for the provider it is about to visit. Any router that
> can choose the destination *after* sealing must therefore only ever choose among
> destinations of the **same egress classification — judge targets included** — and the
> payload must be sealed for the strictest member of that set. A route mixing local and
> cloud targets behind one sealed request is structurally unsound, no matter whose router
> it is.

Apply that to the three integration paths:

- **Library path: the choke point survives completely.** libsy never makes a call; every
  `CallModel` step is served by Friday's own dispatch — which means every call passes
  `_seal_or_block` *per actual target, at call time*, exactly as today. Mixed-tier routing
  even becomes sound in this position, because sealing happens after the routing decision,
  per call. Decision-only mode (`into_parts()`) is safer still: Switchyard picks, Friday
  serves. **This is the only position in which Switchyard's routing and Friday's egress
  law compose without new rules.**
- **Embedded/standalone proxy: survives only under constraints.** The proxy must sit
  strictly *downstream* of `seal_outbound`; every route's full target set (weak, strong,
  judge, fallback) must be egress-homogeneous; the proxy's provider descriptor must
  declare `classification: cloud` even though its `base_url` is loopback; and because the
  system has already produced one adapter-word hole and carries a second latent mismatch
  (§2.3), the declaration cannot be trusted unverified — a startup probe must assert that
  a synthetic sensitive payload routed at the proxy provider arrives sealed (the existing
  egress self-test pattern, extended). Also: the proxy holds provider keys with **no
  client-side auth** — on a multi-process desktop, anything that can reach the port can
  spend the keys; loopback bind and key scoping are mandatory, not defaults.
- **Mixed-tier routing inside any proxy: never.** This is not a maturity issue that a 1.0
  release fixes; it is a structural conflict with per-provider span redaction.

### 3.3 The unglamorous-parts guess: tested in §2 — metrics native-fix (the guess fails
because the premise "no telemetry" was false), translation adopt-the-discipline (the guess
half-holds; the engine is excellent but covers the wrong dialects for Friday's scars and is
not Python-reachable).

---

## 4. Pricing the pre-alpha, per position

| Position | What breaks when Switchyard breaks | Blast radius |
|---|---|---|
| Patterns adopted natively (recommended now) | Nothing — no dependency exists | Zero |
| Library, decision-only, on a traffic slice | A routing *choice* degrades; Friday's fallback ladder still serves the call | One experiment; kill = remove the call |
| Library, serving mode | An in-process Rust panic can take the Python process with it; API churn (three breaks post-0.2.0 already) lands on Friday's floor each upgrade | The process — mitigated by pinning, but the suite has never run on Windows |
| Proxy in the cloud path | Every cloud call, mid-conversation; plus a keys-bearing unauthenticated local socket; plus known issue #1 (disconnects still spend money) | The daily driver — this is what "not for production" means, priced |

Process supervision itself is genuinely cheap here — the Arbiter already spawns,
health-checks, and restarts llama-server and ComfyUI processes, and a proxy binary is no
different in kind. The cost is not running it; the cost is **what stands in the path when
it misbehaves**, and pre-alpha software earns the small blast radii first.

---

## 5. Rules, as data

| id | Rule |
|---|---|
| **SW1** | Nothing pre-alpha sits in the path of every model call on the daily driver. A dependency that says "not for production" is taken at its word until it stops saying it |
| **SW2** | Any post-sealing router chooses only among egress-homogeneous destinations, judge targets included, sealed for the strictest member (§3.2). Mixed-tier in-proxy routing is refused permanently, at any maturity |
| **SW3** | A proxy provider descriptor declares `classification: cloud` regardless of loopback, and a startup probe asserts a synthetic sensitive payload reaches it sealed — the declaration is verified, not trusted |
| **SW4** | Any proxy holding provider keys binds loopback-only and is treated as an unauthenticated credential surface in the threat model |
| **SW5** | Patterns adopted from Switchyard are attributed in code comments and this document; the escalation prompt, if used, keeps its Apache-2.0 notice |
| **SW6** | Friday's escalation judge consults Friday's law — judgment gate, residency plan, vault rule — before any latch; a routing library that cannot is used decision-only or not at all |
| **SW7** | The library path, if trialed, serves every model call through Friday's existing dispatch (`_seal_or_block` per actual target); libsy's no-I/O property is asserted in the trial's tests, not assumed from its README |
| **SW8** | Version pins are exact; an upgrade is a change with a test run behind it, not a `pip install -U` |

---

## 6. Recommendation, phased

**Recommendation: do not adopt Switchyard as a dependency now. Adopt three of its ideas
natively now, run one cheap bounded experiment when convenient, and define the trigger that
would change the answer.** I am prepared to defend each leg: the component that overlaps
Friday's direction most (escalation) is unreachable from Python; the component that is best
(translation) solves dialects Friday is not wounded by and misses every one she is; the
component with the thinnest case (metrics) duplicates three existing planes whose repairs
are one-liners; and the maturity warning is corroborated by a rewrite-scale changelog, three
post-tag API breaks, and a test suite that has never run on Stephen's operating system.

### Phase 0 — native adoptions, no dependency (now)

1. **The escalation pattern, Friday-shaped** (§3.1): judge-the-completed-turn, streak
   latch, fail-open-and-hold, session-keyed; judge = e2b for per-turn speed (the 12b's
   measured ~12 s disqualifies it per-turn); the judge's verdict consults the judgment
   gate and lease economics before latching. First deployment: cloud-weak/cloud-strong
   (Sonnet default, Opus on latch) with the local judge. The Switchyard trajectory prompt
   is the starting artifact, attributed.
2. **The telemetry repairs** (§2.1's table): the `duration_ms` argument and query, health
   recording on the primary Anthropic path, ledger rows for failed calls,
   `(provider, model)` ring keys — plus the taxonomy adoptions: `routing_overhead_ms` as a
   first-class measure (the judgment gate's 12 s belongs in it), `fail_open_total{reason}`
   with bounded reasons. The Gemini bypass is bigger than this spec and is flagged to the
   sessions that own that surface rather than solved here.
3. **The translation discipline** (§2.2): typed lossiness at Friday's four translation
   sites — the two silent `except → None` blocks become logged diagnostics; the hardcoded
   `finish_reason="stop"` on the Ollama path is replaced with the real value; a
   Reject-equivalent mode for payloads where silent degradation is unacceptable.

### Phase 1 — one bounded experiment (optional, when idle)

Install `nemo-switchyard` pinned, in **decision-only mode** (`into_parts()`), on a slice of
clean cloud traffic: let the capability classifier or stage router *recommend* a tier while
Friday's own routing still decides and serves everything. Zero payload risk (SW7 — no
Switchyard code touches a request that hasn't already been sealed by Friday's dispatch, and
in decision-only mode it touches no wire at all). What it buys: first-party evidence on
whether the classifier's verdicts beat Friday's own judgment gate on tier prediction, and
validation that the wheels behave on Windows — the one platform the project never tests.
Kill switch: delete the call.

### Phase 2 — the proxy position (conditional, not scheduled)

The standalone server as a **cloud-side normalizer**: one wire format from Friday's side,
fan-out to multiple cloud providers, retry/fallback and Prometheus included, under SW2–SW4
and SW8. **Trigger, testable, both required:** (a) Friday genuinely multiplies cloud
providers — more than the Anthropic + Gemini of today — such that per-provider native
adapters become the maintenance burden; AND (b) Switchyard reaches beta or 1.0 with its
production warning removed and CI running on Windows. Until both hold, this phase does not
start. If instead the *library* grows Python bindings for escalation mode and translation,
Phase 2's case weakens further in favor of deeper library use — the better position
(§3.2) — and this document gets revised, not stretched.

**Explicit permanent non-adoptions:** mixed-tier in-proxy routing (SW2, structural);
Switchyard as a translation layer for local seats (the channel dialect it cannot parse is
precisely why Friday's own parser exists).

**Noted, not recommended:** the reverse position — Switchyard *in front of* Friday's
llama-server seats would give external Anthropic-format tools (Claude Code among them)
access to local models. Real, cheap, and out of scope unless Stephen wants it (Q2).

---

## 7. Open questions for Stephen

Each answerable in a sentence.

**Q1 — The multi-cloud trigger.** Phase 2's whole case rests on you actually wanting more
cloud providers than Anthropic and Gemini — is that a direction you foresee, or is the
cloud tier staying Claude-shaped?

**Q2 — Local seats for outside tools.** Do you want external tools (Claude Code, other
Anthropic/OpenAI-format clients) to be able to talk to Friday's local models — the one
position where Switchyard's proxy would face *inward* and touch nothing private going out?

**Q3 — The escalation build.** Approve Phase 0 item 1 — cloud-weak/cloud-strong with a
local judge, Sonnet by default and Opus on evidence — as a work item, and if so, does it
outrank or queue behind the deep-research pipeline build?

**Q4 — The telemetry repairs.** Fold Phase 0 item 2 into the current defect sessions'
queue, or hold it as its own pass?

**Q5 — The experiment.** Is the Phase 1 decision-only trial worth an idle afternoon when
one appears, or park Switchyard entirely until its trigger fires?

---

## 8. Sources

- [NVIDIA-NeMo/Switchyard](https://github.com/NVIDIA-NeMo/Switchyard) @ `f735d9dd`
  (v0.2.0, 2026-08-17) — full clone read: `crates/{libsy,libsy-llm-client,protocol,
  switchyard-py,switchyard-server,switchyard-translation,switchyard-skill-distillation}`,
  `docs/` (architecture, routing_algorithms, known_issues, internal/metrics_reference,
  reference), `CHANGELOG.md`, `.github/workflows/{ci,publish}.yml`, `pyproject.toml`.
  Apache-2.0.
- Friday-side audit cites: `services/{cost_meter,provider_health,activity_ledger,
  model_router,agent,channel_toolcalls,egress_gate,residency_arbiter}.py`,
  `routing/{model_router,provider_descriptors,ollama_manager}.py` — file:line references
  inline above, read 2026-08-17.
- [`docs/design/deep-research.md`](deep-research.md) §5 — the judgment gate this document's
  escalation adoption composes with; the ~12 s judgment measurement is from that build's
  live verification.
- Commit `7acf044` — the adapter-word incident cited throughout §2.3/§3.2.
