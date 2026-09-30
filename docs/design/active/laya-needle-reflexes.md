# Laya and Needle, Friday's reflexes

**Status:** draft specification; measurements partial (§6.1), build plan and
decisions pending on them. Not yet ready for the gauntlet. No product code changes in this commit.
**Written:** 2026-09-29, on this PC (i7-10700F, 8 cores, RTX 4070 12 GB).
**Question asked:** *"Let's figure out the best and most intelligent and deepest
and highest resolution manner to integrate both needle and laya within the Friday
desktop given all of the different ways you have seen me use it and all of our
different potential use cases. I want these things to act as broadly and as
quickly as possible."*
**Companion:** [laya-needle-reflexes-benchmarks.md](laya-needle-reflexes-benchmarks.md)
defines every measurement quoted here. Raw data stays under
`~/.friday/bench/reflexes/` and never enters the repository.
**Folds in, does not redesign:** the item-resolver phase 1 the Laya session is
building (Laya picks action and workspace, retrieval shortlists, Laya picks the
item from ten or fewer, the command is built from real IDs).
**Coordinates with:** `feat/injection-defense` (the governance checkpoint and
provenance), `feat/voice-first-parity` (the voice contract), the processing-states
chapter of `avatar-visual-genome.md` (§13), and the CLM research track.

---

## 0. Summary for the owner (one page)

Friday has one way of deciding anything today: ask the brain. A brain turn on
this PC costs **4.7 to 8 seconds at the median on the cloud and much more
locally**, and the mining in §2 shows that **about six in ten of your messages
are twelve words or shorter, and half of your short commands were answered
with one tool or none**. Those turns do not need a brain. They need a reflex.

The design has three layers, which you have already approved:

- **Laya is the judgment.** It answers a typed question about a piece of
  text in about a third of a second on your CPU: *is this a command or a
  conversation? does it touch private data? does it change anything outside
  this machine? which of these ten items do you mean?* It never writes a
  word, so it can never invent an action.
- **Needle is the reflex.** It turns words into one exact tool call with
  arguments, and it can say "no call". It is small enough to run beside Laya
  on the CPU.
- **The brain thinks.** Bonsai2 on the GPU, or a cloud model when you choose
  one. It wakes only when a turn needs thought, and it sees fewer tools when
  it does, because the reflexes have already narrowed the field.

Laya and Needle live on the CPU, so they never take VRAM from the brain, and
they work exactly the same whether you are in local-preferred or cloud-only
mode. Every reflex is bounded: if it is unsure, it asks you back, spoken and
on screen. If it is sure, it acts through the same gate as everything else,
and outward actions still get their card. Nothing about the gates or the
constitution loosens.

**What was measured (§6).** Needle 2 and Needle 3 were installed in an
isolated environment and scored on the 118-example set and on your own
phrasings, idle and with the local brain generating. Laya's shipping engine
was timed on each question shape this spec needs, including the ten-item
choice the resolver depends on. What has been measured so far is in §6.1; the
full tables are pending.

**The build (§9)** is ordered by seconds saved per week of work. The first
two phases are pure speed and ship nothing new to the gates: the reflex
questions run in shadow beside the brain, scored against your own labels,
and each one is promoted only when it beats what the brain did.

**Three decisions that are truly yours (§11):** whether Needle is allowed in
at all, given that it ships a closed engine binary and phones home by
default (we turn that off); whether a spoken "yes" to a card may be
recognised by a reflex rather than the model in the room-mode you have set;
and which of your labels you are willing to give an hour to, because the
shadow scoring needs an answer key and you are the only one who has it.

---

## 1. The three layers

```
   words (voice or text)
        │
        ▼
 ┌──────────────┐  0.2–0.4 s   typed questions, no generation
 │  Laya        │──────────▶  command? private? outward? which one of ten?
 │  judgment    │             addressed to Friday? needs memory?
 └──────┬───────┘
        │ "a command, local, not private"
        ▼
 ┌──────────────┐  0.2–0.6 s   words → one exact tool call, or "no call"
 │  Needle      │──────────▶  arguments filled from the words and real IDs
 │  reflex      │             never free text, never a new tool
 └──────┬───────┘
        │ a filled template, or "not a reflex"
        ▼
 ┌──────────────┐             the same governance checkpoint as today
 │  gate        │──────────▶  internal → act; outward → card; unsure → ask
 └──────┬───────┘
        │ "needs thought"
        ▼
 ┌──────────────┐  5–60 s     Bonsai2 on the GPU, or the chosen cloud model
 │  brain       │──────────▶  wakes only when needed; sees a shortlist of tools
 └──────────────┘
```

Two rules make the layers safe to stack:

1. **Laya never acts.** Its answers steer which path a turn takes and what
   the brain is shown. It never selects an action on its own for anything
   outward, and every gate it touches is add-only: it can only escalate
   (`laya_backend.union_backend`, `services/laya_backend.py:870`).
2. **Needle never invents.** It may only fill a template that names a real
   tool with real IDs from a shortlist Friday built. A call it proposes for a
   tool that is not in the offered set, or with an argument that is not
   grounded in the words or the shortlist, is discarded, not repaired.

The brain stays the only layer that generates language. That is why the
reflexes can be fast, and why they can be trusted to say "not mine".

---

## 2. What exists today, grounded

### 2.1 How the owner uses Friday (mined, read-only)

`mine_usage.py` read every user message it could find under `~/.friday`
(chat history, conversations, trajectories, memory), deduplicated across
stores, and classified each by coarse rules. Counts only; the labelled rows
stay private.

| | |
|---|---|
| User messages found | 1,939 (2026-06-08 to 2026-09-29) |
| By channel | text 1,370 · voice 569 (29%) |
| Median length | 10 words (voice 11); 90th percentile 39 |
| 12 words or fewer | 59% of all messages; 41% are 6 words or fewer |
| Shape | conversation 590 · question 530 · command 433 · statement 386 |

By intent (rule-assigned, first match wins):

| Intent | Count | Voice share | What a reflex could take |
|---|---|---|---|
| conversation | 539 | 38% | nothing: the point is to keep the brain out of it |
| open question | 285 | 44% | "does this need memory / the graph / a lookup" |
| approval reply (≤6 words) | 211 | 15% | the spoken or typed yes/no on a card |
| files and code | 139 | 14% | open, search, run-workflow templates |
| mail | 124 | 7% | search, open, reply-to, organise |
| creative media | 103 | 5% | generate-with-model templates |
| web research | 86 | 29% | search-web with the query lifted from the words |
| news and briefings | 86 | 57% | "open the front page", "the briefing", "that story" |
| settings and models | 71 | 32% | switch seat, mode, voice |
| workflow and task | 65 | 12% | run named routine, status |
| greeting and small talk | 51 | 33% | nothing, fast |
| wiki and knowledge | 43 | 23% | open page, search |
| calendar | 35 | 29% | today, tomorrow, this week |
| navigate and open | 25 | 20% | already a deterministic reflex |
| device control | 17 | 41% | play, pause, mute: no tool exists today |
| status check | 12 | 17% | "what are you doing", answered from live state |
| career | 9 | 56% | job-fit lookups |

Workspace, where the message carried one: 1,016 unlabelled, then the edition
(257), settings (240), studio (143), wiki (65), news (56), messages (42).

What the brain did with them (1,306 turns with a tool trace):

| | |
|---|---|
| Turns with no tool at all | 38% |
| Turns with exactly one distinct tool | 29% |
| Tool calls per turn, median / 90th | 1 / 10 |
| Short commands (≤12 words) answered with 0 or 1 tool | 174, **52% of all command-shaped turns** |
| Top first tools | run_command, read_file, search_files, search_web, search_email, search_wiki, read_wiki, write_file, browse_web, load_tools, query_calendar |

Two things stand out. First, 38% of turns used no tool and 29% used one:
two-thirds of the brain's work is a single decision followed by language.
Second, `load_tools` is the tenth most-called tool. That is the catalogue
asking the brain to spend a round finding the tool it wants
(`services/tool_catalogue.py:1-30`), which a reflex can hand it directly.

### 2.2 Where the time goes today

From `costs.db` (`cost_calls`, 15,369 rows) and the task forensics:

| Path | n | p50 | p90 |
|---|---|---|---|
| Cloud chat turn, all models, all time | 3,650 | 6.4 s | 20.5 s |
| claude-sonnet-5 chat, last 30 days | 1,413 | 4.7 s | 19.4 s |
| claude-fable-5 chat, last 30 days | 608 | 8.0 s | 26.0 s |
| Cloud scheduled call | 2,961 | 3.8 s | 12.3 s |
| Background task (mostly Bonsai2 locally), elapsed | 4,710 | 72 s | 913 s |
| Scheduled job run | 442 | 5.4 s | |
| Approval card, created to approved | 84 | 10 s | 229 s |

The local brain's interactive latency is not in the cost ledger as a
duration (local rows carry none), so §6.4 measures it directly. What the
ledger does show: the median chat reply is 333 output tokens, and the
governance receipts (`decision-bom.jsonl`, 3,306 rows) say **91% of all
governed actions were plain "allow"**, 7% went to a chat confirmation and 2%
to a card. The gate is not where the seconds go. The brain is.

The reflexes' own budget today, from `decisions.jsonl` (856 rows, all the
`policy_class` question): keyword answers in 0.03 ms; the Laya union answers
in **337 ms at the median and 2,510 ms at the 95th**, which is the 2.5 s
timeout (`_SCORE_TIMEOUT_S`, `services/laya_backend.py:126`). Under
contention Laya misses its budget one time in twenty and the gate falls back
to the keyword half, as designed.

One finding for the Laya session: the `confidence` recorded on those 367
union rows has a median of **0.002**, and none reach 0.7. Whatever that field
holds, it is not the calibrated probability the model card describes, so no
reflex in this spec gates on the union's logged confidence until the field is
understood. §6.3 measures the confidence Laya returns directly.

### 2.3 The judgments Friday makes today, and by what

A survey of `src/agent_friday/services/` on main `b5389d94`. Every surface
this spec covers, with the mechanism it uses now.

| Surface | Decided by | Where | Runs |
|---|---|---|---|
| Command-or-conversation (chat) | two regex fast paths: navigate, open | `routes/chat.py:896, 926`; `services/agent.py:2590-2760` | every chat turn, before any model |
| The same for local voice | the same regexes, after the reply | `services/agent.py:2763 _voice_actions_for`; `routes/voice.py:3100` | every local-voice turn |
| Private-or-not, outward-or-not (voice) | not asked; the VOICE question set is defined and has **no caller** | `services/laya_questions.py:128`; `laya_runtime.ask` has no caller in `src/` | never |
| Room mode "addressed to Friday" | the spoken yes must name Friday; nothing else is checked | `services/local_context.decide_by_voice`; `voice_engine.py:924` | on a card decision |
| Item resolution | the brain, with `search_*` then `read_*`, two rounds | trajectories: `load_tools` is the 10th most-called tool | per turn that names a thing |
| Tool shortlisting | catalogue index + `load_tools`; hybrid MiniLM+lexical selector only when the set will not fit | `services/tool_catalogue.py`; `tool_selector.py:197-202`; `tool_budget.py:466` | per turn |
| Turn routing | keyword `classify_task` (VOICE, TOOL_USE, CODE, RESEARCH, SIMPLE under 200 chars) | `routing/model_router.py:204` | per turn |
| Knowledge graph / memory need | always-on structural query for the graph; keyword lists for wiki, memory, career, trust | `knowledge_graph/integration.py:27`; `model_router.py:3291` | per system prompt |
| Approval gate | explicit tool lists; keyword severity; Laya union for connector reads; unknown = outward | `governance/action_gate.py:497-600` | per tool call |
| Confirmation and no-pestering | exact-phrase affirmative; fingerprint per call; second ask becomes a card, never a third | `services/agent.py:8142, 8715, 8810` | per gated call |
| Injection on tool results | provenance (taint), not content: 5-word shingles, origin per argument | `services/taint.py:766`; `agent.py:8925` | per proposed call |
| Mail triage | weighted heuristic with per-sender learning; no urgency signal is ever produced | `services/message_triage.py:239, 344`; `routes/voice_context.py:110` | per card, cached 45 s |
| Goal done-check | LLM judge, threshold 0.7, "skipped" counts as pass; typed blockers **not built** | `services/qa_gates.py:140`; `goals.py:995` | per milestone, every 30 min |
| News relevance | weighted heuristic + profile regexes; editorial pick is an LLM call over the top 28 | `services/news_engine.py:1210, 1744` | per edition, 07:00 and 18:00 |
| News clustering | greedy Jaccard on title tokens, 0.4 | `news_engine.py:2985` | per archive pass |
| Podcasts (branch only) | keyword source scoring; chaptering is a local LLM call, 900 s timeout | `land/podcasts-1-engine` `podcast_sources.py`, `podcast_engine.py:315` | per episode |
| Job-posting fit | weighted heuristic (title, salary, remote, skills) | `seed/skills/job_scanner/scanner.py:145` | per listing |
| Doctor and logs | fixed boolean probes; rule verdicts; no classification of crashes | `health_check.py`, `liveness_audit.py:50`, `crash_forensics.py` | on demand |
| Scheduler "run now?" | due-time rules, idle window 09:00–23:00, 600 s user-activity gate, GPU lease hold | `services/scheduler.py:412, 510-564, 1625` | every 60 s |
| Hologram states | a polling loop reads mood signals; **no server event** drives the scene | `ui_parts/app.html:19858`; spec in `avatar-visual-genome.md` §13 | client poll |
| Salon voice commands | specified, no code | `vibe-coding-salon.md` §6.2 | – |

Read as a whole: Friday already has the seams. The gate has a decision
seam (`decisions.decide`), the voice has a question set, the scene has an
event allowlist on paper, the catalogue has a loader. What is missing is the
fast layer that answers before the brain is asked.

### 2.4 Laya today

- **Shipping:** ONNX fp32 encoder, decision head in torch, `laya_runtime`
  "auto" picks it (commit `efb7a539`; agreement with torch fp32 0 of 150
  answers differ; int8 rejected for changing 31–58 of 150).
- **Measured there (p50, CPU ~74% busy):** gate 1 question 371 ms at 4
  threads / 314 at 6; voice 1 question 299 / 227; voice 2 questions 730 /
  352; a ~350-token state 1,506 ms.
- **Question wording is measured, not written** (`tools/laya_question_eval.py`):
  `direct_command` 14/15 and `touches_private` 10/11 as statements;
  `changes_outside` 23/29; `leaves_machine` near chance and withdrawn.
- **Buckets:** `choice:11+` is clamped and uncalibrated on every build of
  this checkpoint (`laya_backend.py:150-165`). Every question in this spec is
  a statement, a two-option choice, or a choice of ten or fewer.
- **Live callers:** only the approval gate's union (`policy_class`) and the
  chat pilot's `source` question (advisory, alternating arms, warm-only,
  `laya_pilot.py`). `laya_runtime.ask()`, the budgeted batched fast path, is
  built, tested, and **called by nothing**.
- **Labels:** `~/.friday/laya_labels.jsonl` holds 10 owner labels, all
  `reaches_outside` on tools, written from the Settings label queue
  (`core_routes.py:319`). The answer key for everything below starts here.
- **Placement:** CPU only, one engine per process, ~1.7 GB resident,
  `default_threads()` leaves cores for the rest of Friday
  (`laya_runtime.py:120`).

### 2.5 Needle, examined

`cactus-needle` 3.0.6 was installed in an isolated venv under
`~/.friday/bench/reflexes/`. What it is, from the package and the model card:

| | Needle 2 | Needle 3 |
|---|---|---|
| Weights | `Cactus-Compute/needle2`, `needle2.cact` | `Cactus-Compute/needle3`, `needle3.cact`; 121M parameters, ~50M arithmetic, 2.125 bits/weight; 2–20 layers each deployable |
| Licence | Apache 2.0 | Apache 2.0 |
| Runtime | a **prebuilt native engine** (`libneedle.dll`, 14 MB) fetched from Hugging Face; ctypes calls `needle_init` / `needle_complete` | the same, a separate engine build (35 MB) |
| Cache on disk | 49 MB for both generations, engines and weights | |
| Input | a JSON list of `{name, description, parameters}` tools, plus the text | the same, plus `embed(text)` |
| Output | `{type, function_calls:[{name, arguments}], reasoning, confidence, prefill_tps, decode_tps, peak_ram_mb, validation}` | the same, with a confidence head |
| Grounding | the wrapper flags numbers and years in arguments that do not appear in the input (`_annotate_ungrounded`) | the same |
| Fine-tuning | LoRA or full, `needle.model.finetune` | the same; a fine-tune without a confidence head reports `confidence: None` |
| Telemetry | **on by default**: event name, versions, OS and a random install id, posted to a vendor endpoint on every `run`/`complete` (`needle/_telemetry.py`); off with `NEEDLE_TELEMETRY=0` or `DO_NOT_TRACK` | the same |

Three facts shape the design:

1. **The engine is a closed binary.** The Python is open; the thing that
   runs the model is a downloaded `.dll`. It is a ctypes library in Friday's
   process, so it inherits Friday's permissions. §3's security reviewer
   treats it as an untrusted native dependency: pinned by hash, loaded in a
   worker process, given no network.
2. **Telemetry is on by default.** Amendment A3 says nothing leaves the
   machine, ever, with no opt-in. Friday must set the environment before
   import and verify with a test that the telemetry module's `_enabled()`
   returns False, on every boot, not once.
3. **It has no "none of these" until you give it one.** In the smoke test
   both generations mapped "I think the weather is lovely today" to a calendar
   query, and Needle 3 mapped "tell me a joke" to an email search with
   confidence 0.17. Needle 2 returned an empty call list for the joke.
   Needle's confidence separates these cases (§6.2), and the spec never lets
   Needle see a turn Laya has not first called a command.

---

## 3. STORM: six perspectives

The method: each simulated expert questions the design from their own
practice, the answers are checked against the code and the numbers above,
and §3.7 keeps what survived.

### 3.1 The command-palette designer (Raycast, Superhuman)

*"A palette is fast because it never guesses. It matches, ranks, and shows
you the top result before you finish typing. The user's eye does the last
step."*

- **Q: Where is the palette in this design?** Friday already has one
  (`CommandPalette`, Ctrl+K, `index.html:9314`; GAP NS-21.2-4 shipped). It
  is keystroke-driven and knows nothing about the reflexes. The reflex arc
  should *be* the palette's engine: the same shortlist Laya picks from is
  the list the palette shows, so voice and keyboard resolve the same way.
- **Q: What happens at 0.6 confidence?** A palette shows the top three and
  waits. That is §4.4's ask-back: spoken as "the memo, the agenda, or the
  invoice?", shown as three rows with the first highlighted. Never a
  guess, never a wall of ten.
- **Q: What is the latency the user feels?** Not the model's. It is time to
  *first visible reaction*. North-star §33.4 asks for navigation under 200
  ms and approval-card open under 300 ms. A reflex that takes 400 ms must
  show something at 100: the listening ripple already exists for voice, and
  the palette can show "searching mail…" from Laya's first answer before
  the shortlist lands.
- **Q: Templates or free text?** Templates. Every command the palette can run
  is a named template with typed slots; the reflex fills slots. A palette
  with free-text commands is a chat window with worse manners.

**Kept:** the resolver feeds the palette; three-row ask-back; first reaction
under 200 ms; templates only.

### 3.2 The on-device ML engineer

*"Small models are wonderful until they are asked a question they were not
trained on. Then they answer anyway."*

- **Q: Why two models?** Because they are different shapes of computation.
  Laya is an encoder: one forward pass, a distribution over fixed options,
  a real calibrated probability per bucket. Needle is a decoder: it must
  generate the argument string, which is where value comes from and where
  hallucination lives. Use the encoder to decide *whether* and *which*;
  use the decoder only to *fill*, and only when the fill can be checked.
- **Q: Why not Needle alone? It can say no-call.** §6.2 measures its no-call
  behaviour. Even where it is good, a decoder's "no" is a generated token,
  not a probability over a defined alternative. Laya's `direct_command` is
  the latter, and it was 14/15 on clear-cut cases. Put the calibrated
  question first.
- **Q: Why not Laya alone?** Laya cannot fill an argument. It can pick from
  ten candidates Friday retrieved; it cannot produce "invoice" from "did
  anyone email me about the invoice". Needle can, and the answer can be
  checked: the argument must be a substring of the words or an ID from the
  shortlist.
- **Q: The `choice:11+` clamp.** Design around it: retrieval shortlists to
  ten or fewer *before* Laya sees the list, always. A store with 6,311 wiki
  pages and 6,293 graph entities (§2.1) is never a Laya question; it is a
  retrieval question followed by a Laya question.
- **Q: Fine-tuning.** Both models expect it. Laya's card says zero-shot is
  near chance on a new decision; Needle ships LoRA. The shadow logs are the
  training set, and the owner's labels are the test set, and they must
  never be the same rows. Until there is a fine-tune, use the published
  checkpoints with measured wordings and a high abstain bar.
- **Q: Contention.** Both run on the CPU beside a GPU brain. §6 measures
  idle and with the brain generating. The design gives each reflex a
  budget and a fallback, exactly as `laya_runtime.ask` already does; a
  missed budget is a "not a reflex", never a wait.
- **Q: One process or two?** Laya is 1.7 GB resident and torch-based; Needle
  is a native library that must never be given the main process. Needle runs
  in a worker process (the package already has `FineTuneWorker` for tuned
  weights; use the same shape for the base engine). Laya stays in-process as
  it is today.

**Kept:** encoder decides, decoder fills, fill is checked; ten or fewer;
budgets with fallbacks; Needle in a worker; shadow logs train, owner labels
test.

### 3.3 The voice-assistant latency engineer

*"Nobody measures what the user hears. They measure what the model
returns."*

- **Q: What is the budget from end-of-speech to first sound?** Gemini Live
  gives first audio in 0.9 s on the live model and 0.55 s resumed
  (`voice_engine.py:1410-1413`). Local voice has an 800 ms trailing-silence
  endpointer (`local_voice.py:225-245`) before Whisper even starts. A reflex
  that adds 300 ms on top of that is felt; one that runs *during* the 800 ms
  is free. §5.6 prefetch is the whole game for voice.
- **Q: Where does Laya run in a live voice turn?** On the running
  transcript. Gemini Live streams input transcription; local Whisper can
  run on partial audio. Ask `direct_command` and `touches_private` on the
  partial, re-ask on the final only if the text changed materially. The
  answers are ready when the utterance ends.
- **Q: What does the user hear while the reflex acts?** One short
  acknowledgement, under the voice contract's pacing, then the result:
  "Opening it." Not "I'll open that for you now". The voice contract's
  descriptions already say this for tools (`voice-tool-contract.md` §2).
- **Q: Barge-in and stop.** A reflex must be cancellable in under 250 ms
  (north-star §33.4). Laya and Needle calls are short enough to let finish;
  the *action* is what is cancelled, and a template action is a single
  call, so it is.
- **Q: Room mode.** "Is this addressed to Friday?" is a Laya statement
  over the transcript plus a name-mention feature. It is a calibrated
  probability, and in room mode the threshold is high and the miss is
  silent: Friday does nothing rather than acting on a conversation between
  two people. The card rule (a spoken yes must name Friday) stays as it is.

**Kept:** prefetch on the partial transcript; acknowledge-then-act wording
from the contract; cancellation at the action; room-mode threshold high and
misses silent.

### 3.4 The security reviewer (injection and exfiltration)

*"Every fast path is a path around something. Show me what it goes
around."*

- **Q: Does a reflex bypass the checkpoint?** No. A reflex produces a tool
  call; the call goes through `agent._execute_tool` and the governance
  hook chain exactly as a brain-proposed call does
  (`2026-09-24-injection-provenance-gate.md`). The discovery test
  `test_every_action_is_governed.py` will find a reflex that calls a
  handler directly and fail. That test is the fence.
- **Q: Can a reflex be injected?** Its input is the user's words and
  Friday's own shortlist. It never reads a tool result. A tool result can
  reach a reflex only as a *candidate item* in a shortlist, and the
  command built from it names the item by ID, not by content. Provenance
  (`taint.py`) still records that ID's origin, so a reply-to built from a
  recipient that came from read content still gets the card that names
  where it came from.
- **Q: What about the injection triage surface (§5.8)?** That is the one
  place a reflex *does* read tool results, and it is read-only: Laya
  scores "does this text contain instructions addressed to an assistant"
  and the answer can only *add* a warning or a card. It can never clear
  one. Add-only, as the union gate is.
- **Q: Needle's binary.** Pinned by SHA-256 of the engine and weights in a
  manifest Friday owns, checked before load (as `laya-onnx/manifest.json`
  already does for Laya). Loaded in a worker process with no network
  access and `NEEDLE_TELEMETRY=0`, and a boot test asserts the telemetry
  module reports disabled. If the hash does not match, Needle is off and
  Friday says so in Doctor; the reflex arc degrades to Laya-plus-templates,
  which needs no Needle.
- **Q: Confidence as a control.** A threshold is a control only if the
  number is calibrated. §2.2 found the union's logged confidence is not.
  Every threshold in this spec is set from shadow data on this PC, on the
  owner's labels, and recorded beside the decision so the Settings panel
  can show "acted at 0.83, threshold 0.80".
- **Q: Exfiltration by argument.** Needle fills a `query` for `search_web`.
  That query leaves the machine. Today the brain's queries pass the egress
  gate; a reflex's must too, unchanged (`egress_gate.seal_outbound`). A
  reflex is not a new egress path; it is a new proposer on the old one.

**Kept:** every reflex call goes through `_execute_tool`; reflexes read
user words and Friday's shortlists only; injection triage is add-only;
Needle pinned, sandboxed, telemetry asserted off; thresholds from labelled
shadow data; egress unchanged.

### 3.5 The journalist power user

*"I do not want a faster assistant. I want one that does not make me
repeat myself."*

- **Q: What does "act broadly" mean in a working day?** The mining says:
  mail (124), the edition (257 messages in that workspace), the wiki and
  archive, files, and the news front page, by voice for the briefings
  (57% of news requests were spoken). "Open the story about the zoning
  vote", "reply to the accountant and say yes", "file those two under
  finance", "what's in the afternoon briefing". Those are resolver turns:
  a verb, a workspace, and an item that has to be *found* before it can be
  acted on.
- **Q: What is worst today?** Two rounds to find a thing. The brain
  searches, reads, then acts, and each round is a brain turn (§2.2). The
  resolver collapses that to retrieval plus one Laya pick, and the brain
  never wakes for "open the second one".
- **Q: Where must it *not* be fast?** Sending. Publishing. Anything with a
  byline. The reflex may *prepare* a reply-to with the right thread ID and
  the right recipient, and it stops at the card. That is the same card as
  today. Nothing this spec does makes a send faster than the human
  reading the card.
- **Q: The News desk.** Story relevance for the front page is a heuristic
  today and the editorial pick is a 28-story LLM call. A Laya relevance
  question per story is a real improvement only if it beats the heuristic
  on the owner's own reads and skips, which Friday does not record. Log
  those first (§5.11).
- **Q: The archive and the graph.** 6,311 wiki pages and 6,293 entities.
  "Which of these ten" is the right question; "which of these six
  thousand" is retrieval, and retrieval is where the ranker seam (§4.7)
  belongs.

**Kept:** the resolver is the centre of the design; sends stop at the card;
News relevance needs the owner's reads logged before a model can be judged;
retrieval, then Laya.

### 3.6 The accessibility advocate

*"A reflex that only works by voice, or only on screen, is a new barrier."*

- **Q: Same reflex, every surface?** Yes: the arc takes text, whether it
  came from Whisper, Gemini's transcript, the composer, the palette, or the
  phone line. Amendment A4: no voice-only limits. The reverse holds too: no
  keyboard-only reflex.
- **Q: Ask-back.** Spoken *and* on screen, always both, with the same three
  rows. A screen reader gets the rows; a voice user hears them; a user who
  cannot speak picks with a key. The reduced-motion rule from
  `avatar-visual-genome.md` §13.7 applies to the reflex snap on the lattice.
- **Q: What if a reflex misfires?** Undo. The item-actions work already puts
  a receipt and an undo on every organise action (`feat/item-actions-and-branding`).
  A reflex may only act through templates whose actions have a receipt and,
  where the action is reversible, an undo. "Open" is trivially reversible;
  "move" has undo; "send" has a card.
- **Q: The status line.** Every lattice gesture has a status line and a
  spoken equivalent (§13.8 of the avatar spec). A reflex event says "Opened
  the budget memo (reflex)". The word "reflex" in the line is how the owner
  learns which path took the turn, and how a wrong reflex gets corrected
  in one click into the label queue.

**Kept:** one arc for every input surface; ask-back spoken and on screen;
templates with receipts and undo; the status line names the path.

### 3.7 Synthesis

What every perspective agreed on became the architecture of §4:

1. Laya first, on every turn, on the partial transcript when there is one.
2. Retrieval shortlists to ten or fewer; Laya picks; the command is a
   template filled from real IDs.
3. Needle fills only free-text slots (a query, a name) and only when the
   fill is checkable against the words; otherwise the slot is asked back.
4. Every reflex call is governed exactly as a brain call, receipted, and
   named as a reflex in the status line and the lattice.
5. Every reflex runs in shadow first against the owner's labels, and the
   threshold that promotes it is measured on this PC.
6. Budgets and fallbacks everywhere; a slow reflex is a missing reflex,
   never a wait.
7. Both paths: nothing here depends on which model the brain is.

What was rejected, with the reason:

- **Needle as the front door.** It over-calls on conversation (§2.5, §6.2);
  the calibrated question goes first.
- **An LLM picker for tools.** Bonsai 4B took 23.6 s on the CPU for that
  (`tool_selector.py:31-32`). The hybrid selector is 106–267 ms.
- **Free-text commands.** A palette with free text is a chat window.
- **Gating on the union's logged confidence.** The field is not what it
  says it is (§2.2).
- **A wake word.** Out of scope; GAP NS-22.1-4 is MAY and missing. Room
  mode's "addressed to Friday" question is the related thing and is
  specified.

---

## 4. Architecture: the reflex arc

### 4.1 The arc, per turn

Every turn, from any surface, runs these stages. Budgets are what the stage
may take *after* the previous one; §6.4 measures the whole.

| Stage | What | Budget (this PC) | On miss |
|---|---|---|---|
| A. Deterministic | the existing navigate/open regexes | 0 ms | – |
| B. Laya turn shape | `direct_command`, `touches_private`, and (voice, room) `addressed_to_friday`, one batched pass on the partial or final text | 350 ms idle; 700 ms busy | treat as "not a command"; the brain path |
| C. Route | reflex / sidekick / brain / cloud from B's answers, mode, and length (§5.4) | 0 ms | brain |
| D. Retrieval | per predicted workspace, the store's own search, top 10 (§5.2) | 300 ms local stores | shortlist empty → brain |
| E. Laya pick | action (≤5 options) and item (≤10) | 350 ms | ask back |
| F. Needle fill | free-text slots only, checked against the words | 400 ms (Needle 2) | ask back |
| G. Gate | `_execute_tool` → governance checkpoint | as today | as today |
| H. Act and say | the template's action; one spoken line; status line "(reflex)"; presence frame `reflex` | – | – |

Stages B and D run concurrently with speech where there is a partial
transcript (§5.6). A reflex turn is A→H with no brain; a brain turn is
A→C and then the agent loop, with the shortlist from D and the answers
from B handed to the brain as context, so even the brain turn is faster.

### 4.2 Templates and real IDs

A **template** is a named command with typed slots, registered in one
place beside the tool it calls:

```
open_item        (workspace, item_id)                → open_path / read_wiki / navigate_to …
search_items     (workspace, query)                  → search_email / search_wiki / search_files …
reply_to         (thread_id, stance: yes|no|later)   → draft_email  (card)
organise         (workspace, item_ids[], target)     → the item-actions batch (card per batch, undo)
run_routine      (routine_id)                        → run_workflow
calendar_window  (when: today|tomorrow|week)         → query_calendar
briefing         (which: morning|afternoon|latest)   → get_briefing
switch_seat      (seat_id)                           → switch_model
status           ()                                  → check_situation
```

Rules:

- **Slots of type `id` are filled only from a shortlist Friday retrieved
  this turn.** Never from generated text.
- **Slots of type `enum` are Laya choices** (≤5 options).
- **Slots of type `text`** (a query, a title) are the only ones Needle fills,
  and the fill is accepted only if every content word in it appears in the
  user's words. Otherwise the slot is asked back.
- **A template names one tool.** Multi-step work is a brain turn.
- **Every template is in `INTERNAL_TOOLS` or carries its own card.** A
  template for an outward tool exists to *prepare* the card with the right
  IDs, never to skip it.

### 4.3 The item resolver (phase 1, folded in)

The Laya session's phase 1 is the resolver's contract and this spec adopts
it as written:

1. Laya picks the **action** and the **workspace** (two small choices).
2. **Retrieval shortlists** from that workspace's own index: mail search,
   wiki search, the journalism archive, the News archive and front pages,
   Studio creations, the knowledge graph's structural query, the file
   roots, workflows and routines, podcasts. Ten or fewer, ranked by the
   store's own relevance plus recency.
3. Laya picks the **item** from the shortlist (a `choice:6-10` question,
   the items rendered as short titles, never bodies).
4. The **command is built deterministically** from the real ID, and the
   template's tool is called through the gate.

This spec adds:

- **Confidence bands.** The item pick's probability decides: ≥ threshold
  act; a middle band asks back with the top three; below it, the brain. The
  thresholds come from shadow (§4.5).
- **Pronouns and recency.** "That story", "the second one", "the one you
  just read" resolve against the **turn context** first: the last shortlist
  shown, the last item opened, the item under the cursor (from
  `check_situation`). Laya is asked only when context does not settle it.
- **Cross-workspace queries** ("the thing about the zoning vote") retrieve
  from every store in parallel and merge to ten before the pick. The
  workspace choice then has a "several" option.
- **The palette is the resolver's screen** (§3.1). Ctrl+K shows the same
  shortlist a voice turn would have picked from.

### 4.4 Confidence, abstain, ask-back

Every reflex decision carries a probability, and three outcomes:

| Band | Outcome | Wording (spoken and on screen) |
|---|---|---|
| ≥ act threshold | act | "Opening the budget memo." |
| between | ask back, top three, first highlighted | "The memo, the agenda, or the invoice?" |
| < abstain floor | hand to the brain, with the shortlist as context | the brain's normal turn |

Ask-back is a **question, not a card**: it needs no approval, it expires
with the next utterance, and answering it by number, by name, or by
pointing all work. A second ask-back on the same turn is not allowed; the
turn goes to the brain.

### 4.5 Shadow first, promoted on evidence

Every new Laya question and every Needle fill ships in shadow: the question
is asked, its answer and probability are logged in `decisions.jsonl` with
`context.purpose = "shadow:<surface>"`, and the turn proceeds exactly as
today. Promotion needs:

1. **An answer key.** The owner's labels, gathered through the existing
   label queue (`/api/decisions/label_queue`), extended with the new
   question ids. A reflex that wrongly acted, or wrongly abstained, is
   labelled from the status line in one click ("that was not a command",
   "you meant the other one").
2. **A measured threshold.** On the labelled shadow rows, the act threshold
   is set where precision among acted rows is ≥ 0.98 for internal actions
   and the abstain floor where recall of "brain needed" is ≥ 0.95. The
   numbers are recorded in the promotion commit.
3. **A latency check.** p95 within the stage budget on this PC, idle and
   with the brain generating.
4. **A rollback.** The setting `reflexes.<surface>` is `shadow` by default,
   `on` after promotion, `off` at any time; every reflex decision records
   which mode it ran in.

Shadow rows are the future fine-tuning set; labelled rows are the test set;
the two are never the same rows.

### 4.6 Both paths

Nothing in the arc depends on the brain's provider. In **cloud-only** mode,
stages A–F run identically on the CPU, and the difference is only that a
brain turn costs a cloud call: the reflexes save money there, not just
time. In **local-preferred**, they save the GPU. Amendment A6: no silent
substitution in either direction; a reflex is not a substitution, because
it either acts on a template the owner can see, or hands the turn to
whichever brain the owner chose.

### 4.7 The CLM seam

Two stages have a hole shaped like a ranker or verifier:

- **D, retrieval.** The stores' own search ranks the shortlist. A learned
  ranker over (query, candidate title, recency, workspace) would slot in
  between retrieval and Laya's pick, taking the same ten and returning the
  same ten in a better order. Its interface: `rank(query, candidates) ->
  candidates`. The CLM research track owns what fills it.
- **F, fill.** Needle's argument goes through a check ("every content word
  appears in the user's words"). A verifier model with the interface
  `verify(words, template, fill) -> probability` would replace the string
  check. Same seam, same fallback.

Both seams are functions with a default implementation and a settings key
naming the alternative. They are named here so the reflex arc never has to
be reopened to accept a ranker.

### 4.8 Runtime placement

- **Laya:** in-process, as today. One engine, ONNX fp32, `default_threads()`.
  The arc uses `laya_runtime.ask()` with the batched question sets from
  `laya_questions.py`, extended with the new ids (§5). The cache keyed on
  argument shape means repeated shapes cost nothing.
- **Needle:** a worker process owned by a small `services/needle_runtime.py`:
  pinned engine and weights, telemetry asserted off, no network, one
  generation (2 or 3 by measurement, §6.5), `stateless=True`, a
  `budget_ms`, and the same `{status: ok|missing}` contract as
  `laya_runtime.ask`. Restarted on crash; when down, stage F asks back.
- **Threads.** Laya and Needle never run at the same instant on the same
  turn: B then F. Across turns they can overlap with the brain's sampling
  thread; §6 shows the cost.
- **Memory.** Laya ~1.7 GB, Needle ~160 MB peak (`peak_ram_mb` in its
  envelope). Neither touches VRAM.
- **Boot.** Laya warms at boot as today (`server.py:282-300`); Needle warms
  lazily on the first command-shaped turn and stays warm.

### 4.9 Events to the lattice

The processing-states chapter defines the frame and the allowlist
(`avatar-visual-genome.md` §13.5). The arc raises:

| Event | Frame | When |
|---|---|---|
| Laya reflex or instant command | `{"type":"presence","state":"reflex","phase":"start"|"end"}` | stage H, one per reflex turn (a snap twist of one cube) |
| Retrieval count | `{"state":"retrieval","n":<shortlist size>}` | stage D end (one gathered cube per source, capped) |
| Waiting for the user's answer | reuses `card_pending`'s shape with `state:"ask"` | an ask-back is open; one stepped-out cube, no approval hue |
| Route | `{"state":"route","route":"local"|"cloud"}` | stage C, when the turn goes to a brain |

No text in any frame. Off the record writes nothing, and the frames are
never written to disk. A reflex that acts has a status line and a spoken
line; the gesture is the third copy of the same fact, never the only one.

---

## 5. Surfaces

Each surface: what decides today, the reflex question(s), the budget, the
fallback, what must not change, and the shadow key. Question wordings are
proposals to be **measured** by `tools/laya_question_eval.py` before use, as
every wording in `laya_questions.py` was.

### 5.1 Chat and voice turns: shape

**Today.** Two regexes; otherwise the brain. Voice: the same regexes after
the reply. The VOICE question set exists with no caller.

**Reflex.** One batched Laya pass (stage B):

| id | Type | Statement or choice | Bucket |
|---|---|---|---|
| `direct_command` | noul | *This is a short control command for a device or app.* (shipping wording, 14/15) | noul:2 |
| `touches_private` | noul | *Answering this needs the user's own personal records.* (10/11) | noul:2 |
| `changes_outside` | choice | *Does this action create, change, send, publish, pay for or delete anything outside this computer?* (23/29) | choice:2 |
| `addressed_to_friday` (room mode only) | noul | *These words are spoken to the assistant, not to another person in the room.* Features appended: whether the name was said; whether a reply is pending | noul:2 |
| `wants_action` | noul | *The user is asking for something to be done, not for a reply.* | noul:2 |

`direct_command` covers device-style commands; `wants_action` covers
"reply to the accountant and say yes". Both are needed because the first was
measured on device commands only.

**Budget.** 350 ms idle, 700 ms busy, on the partial transcript where one
exists.

**Fallback.** The brain path, exactly as today.

**Must not change.** In room mode the card rule (a spoken yes must name
Friday) stays; `addressed_to_friday` only adds a silent miss for
conversation between people. Private turns on cloud voice still go through
`ask_local_for_context` and the payload card.

**Shadow key.** `shadow:turn_shape`, labels "was a command", "was private",
"was to Friday".

### 5.2 The item resolver, every workspace

**Today.** The brain, two rounds.

**Reflex.** §4.3. Per workspace, the retrieval call and the ID the template
uses:

| Workspace | Retrieval (top 10) | ID | Templates |
|---|---|---|---|
| Mail | Gmail search via `search_email` with the words | thread/message id | open, reply_to (card), organise (batch card, undo), mark |
| Wiki | `search_wiki` | page path | open, read aloud, search |
| Journalism archive | the archive index | archive id | open, cite, search |
| News | front pages and `news/archive` titles | story id | open story, deep dive, source trust |
| Studio | `creations_meta` titles | creation id | open, regenerate (spend: as today) |
| Knowledge graph | `structural_query` top entities | entity id | open, related, explain |
| Files | `search_files` over the roots | path | open_path, read, organise |
| Workflows and routines | names | workflow id | run, status, stop |
| Podcasts (branch) | episode and source titles | episode id | play, open outline |

**Budget.** Retrieval 300 ms for local stores (Gmail is a network call and
gets 1.5 s, else the brain); Laya pick 350 ms.

**Fallback.** Ask back at the middle band; the brain below it, with the
shortlist as context so the brain does not repeat the search.

**Must not change.** Reply, send, publish, spend: the card. Organise: one
card per batch with undo, as built on `feat/item-actions-and-branding`.

**Shadow key.** `shadow:resolver`, labels "meant item N", "none of these".

### 5.3 Tool shortlisting

**Today.** The catalogue index plus `load_tools` (a brain round); the hybrid
selector only under budget pressure. `load_tools` is the tenth most-called
tool.

**Reflex.** When a turn goes to the brain, stage D's workspace answer and
the hybrid selector (`tool_selector.select`, 106–267 ms, already measured
correct on its probes) pick **at most 12 full schemas plus the core four**,
and the catalogue index stays as the long tail. Laya's `source` question
from the pilot (families A–F) is folded in as one more feature for the
selector, not a separate arm: the pilot's alternating-arm design ends
once the selector takes its answer.

**Budget.** 270 ms, overlapped with stage B.

**Fallback.** The catalogue as today.

**Must not change.** `load_tools` stays, so nothing is unreachable.

**Shadow key.** `shadow:shortlist`, label "the tool used was in the
shortlist" (automatic, from the trace, no owner time).

### 5.4 Turn routing

**Today.** `classify_task`: keywords and a 200-character rule; no reflex
tier; VOICE always cloud.

**Reflex.** A fourth tier, decided from §5.1's answers and the mode:

| Answers | Tier | Who |
|---|---|---|
| command or wants_action, a template matched, item resolved | **reflex** | no model |
| command or wants_action, no template, short, not private | **sidekick** | the small local seat, shortlist of tools |
| otherwise, local-preferred | **brain** | Bonsai2 |
| otherwise, cloud-only, or the owner's per-conversation binding | **cloud** | as today |

Routing writes the north-star §12.3 record (`routing_decision` with
`reason`, `alternatives_considered`, `expected_latency_ms`) for every turn,
including reflex turns, which closes GAP NS-12.3-1's missing fields with
numbers this spec measured.

**Budget.** 0 ms (a table over answers already computed).

**Must not change.** local_only never reaches cloud; vault turns stay local;
no silent substitution (A6).

### 5.5 Does this need the knowledge graph or memory?

**Today.** The graph is queried on every prompt (microseconds, structural);
wiki, memory, career and trust blocks are keyword-gated
(`model_router.py:3291`).

**Reflex.** One Laya choice added to stage B for brain turns only:
`needs_context` over {none, recent conversation, personal knowledge, live
state, several}. It replaces the keyword lists for the *memory* and *wiki*
blocks; the structural graph query stays always-on because it is free.
"Live state" routes to `live_state` probes rather than memory (GAP
NS-4.2-2).

**Budget.** Inside stage B's batch (one more question, ~+80 ms).

**Fallback.** The keyword lists.

**Shadow key.** `shadow:context_need`, automatic label: whether the brain's
reply cited a memory or wiki source it was given (from `sources_consulted`).

### 5.6 Prefetch while the user is still speaking

**Today.** Nothing runs until the utterance ends; local voice then waits
800 ms of silence, then Whisper.

**Reflex.** On every partial transcript (Gemini Live's input transcription;
Whisper on rolling audio for local voice):

1. Stage B on the partial when it has ≥ 3 words; re-run only if the final
   differs by more than a trailing word.
2. Stage D retrieval for the predicted workspace as soon as B says
   "command" with a workspace, so the shortlist is ready at end of speech.
3. For brain turns, the tool shortlist (§5.3) and context need (§5.5) are
   computed on the partial, so the brain's first token comes sooner.

Nothing is *acted on* before the final transcript. Prefetch reads only
local stores; a Gmail search prefetch is allowed because it is a read the
final turn would make anyway, and it is receipted as such.

**Budget.** The endpointer's 800 ms is the window; B and D fit inside it
idle, and B alone fits busy.

**Must not change.** No action before the final transcript; nothing
prefetched leaves the machine except a search the turn would have made.

### 5.7 The approval gate

**Today.** Explicit lists, keyword severity, Laya union for connector reads
(escalate-only), unknown = outward, grants for scheduled work, one chat
confirmation then a card, never a third ask.

**Reflex, add-only.** Three things, none of which loosens a gate:

1. **Shadow questions become live features of the card, not of the
   verdict.** `changes_outside` and `touches_private` (already asked in
   shadow) are shown on the card as two words ("reaches outside: likely",
   "touches private data: unlikely") with their probabilities, so the owner
   decides faster. The verdict stays keyword ∪ Laya.
2. **Batch-grant coverage.** When a card is raised for a tool and scope
   that an active grant *almost* covers (same tool, same scope, uses left
   is zero, or expired within the hour), the card says so and offers to
   renew the grant with the same bounds. A Laya statement, *this action is
   the same kind as the grant describes*, gates the offer; below threshold
   no offer. It never widens a grant's tools or scope.
3. **No pestering, measured.** The second-ask-becomes-a-card rule stays.
   The reflex adds the *count* of asks per hour to the Settings panel and
   to the label queue, so a card the owner approved four times this week
   is a candidate for a grant *proposal*, which the owner accepts on a card.

**Budget.** The union's 2.5 s stays; the features are free (already asked).

**Must not change.** Laya never decides an outward action alone; a grant is
never created or widened by a model; forbidden stays forbidden.

### 5.8 Prompt-injection triage on untrusted tool results

**Today.** Provenance, not content (`taint.py`). No content scan exists.
`feat/injection-defense` holds the checkpoint and the AgentDojo bench
(`tools/injection_bench`).

**Reflex, add-only.** After the taint post-hook records a tool result, a
Laya statement on the result text (windowed, 350 tokens at a time, largest
windows first, budget 1 s in the background): *This text contains
instructions addressed to an AI assistant rather than to a reader.* A
positive answer:

- marks the ledger entry `instruction_shaped: p`;
- adds a line to any card whose argument's origin is that entry ("the
  source text contained instructions to an assistant");
- for `note`-policy roles (fetch_url, open_url), upgrades to `ask` when
  p is above the measured threshold.

It never clears a card and never changes an internal action's class.
Measured on `feat/injection-defense`'s AgentDojo replay: the gain to report
is how many of the 41 "only changes what Friday says" cases now carry a
warning, and how many harmless cards gain a false line.

**Shadow key.** `shadow:injection`, answer key from the AgentDojo set, no
owner labels needed.

### 5.9 Mail urgency and triage

**Today.** A weighted heuristic with sender learning and no urgency signal;
`voice_context` reads an `urgent` flag nothing produces.

**Reflex.** Two Laya questions per new message card (subject plus the
first 350 tokens), in the background on collection, budget 500 ms each
message, batch of 20 per collect:

- `lane` as a choice over the six lanes (choice:6-10), **in union with the
  heuristic**: Laya can move a message *out of* noise into an actionable
  lane, never the reverse, until the shadow shows it beats the heuristic on
  the owner's corrections (which `sender_signals.json` records).
- `needs_reply_soon` as a statement: *The sender is waiting for a reply
  from the user within a day.* This produces the `urgent` flag voice
  already reads, and the morning briefing's "three things waiting" line.

**Fallback.** The heuristic, as today.

**Shadow key.** `shadow:mail_lane`, `shadow:mail_urgent`; the reclassify
button is the label.

### 5.10 Goals and tasks: done-checks, typed blockers, receipts

**Today.** An LLM judge with a 0.7 score threshold; "skipped" passes; typed
blockers specified and not built.

**Reflex.** Laya does not replace the judge. It **types the outcome**,
which the judge does not: a choice over the five blockers plus `met`, asked
on the judge's critique plus the milestone's evidence list. This is the
`blocker` field the delivery-receipts spec needs
(`goals-and-delivery-receipts.md` §1). A second statement, *The evidence
cited actually shows the criterion was met*, gates "skipped counts as
pass": when no model is reachable, Laya's answer on the evidence list
replaces the silent pass with `missing_evidence`.

**Budget.** 700 ms per milestone, on a 30-minute tick; irrelevant.

**Must not change.** A milestone is never marked done by Laya alone; the
escalation card stays.

**Shadow key.** `shadow:blocker`, the owner labels blockers from the goal
review.

### 5.11 News: relevance and clustering

**Today.** Heuristic score; editorial pick is an LLM over the top 28;
Jaccard clustering at 0.4.

**Reflex.** Two changes, and one precondition.

- **Precondition: log the reads.** Friday does not record which front-page
  stories the owner opened, listened to, or skipped. Without that there is
  no answer key. The front page gets a per-story `opened`/`skipped` receipt
  (local, numbers only).
- **Relevance:** a Laya statement per candidate story (title plus snippet),
  *This story matters to a journalist covering technology, politics, media
  and local news*, in shadow beside the heuristic score; promoted to a
  feature of the score (not a replacement) when it beats the heuristic on
  opened-vs-skipped. Budget: 300 ms × up to 100 stories per edition in the
  background before the editorial LLM call, which already takes minutes.
- **Clustering:** Needle 3's `embed()` (a real text embedding, §2.5)
  replaces title-token Jaccard for the "same story" test, thresholded on
  the archive's existing clusters as the answer key. Podcast source
  selection (§5.12) uses the same embedding.

**Shadow key.** `shadow:news_relevance`, label = opened/skipped.

### 5.12 Podcasts: source selection and chaptering

**Today (branch).** Keyword scoring over wiki pages; chaptering is the local
LLM with a 900 s timeout.

**Reflex.** Source selection becomes retrieval (the wiki index plus Needle
3 embeddings) to a shortlist of ten, then a Laya choice *which pages belong
in an episode about X*, asked once per page as a statement. Chaptering
stays with the brain: it is writing. Laya adds one check per chapter,
*This chapter's claims are supported by the cited sources*, in shadow,
feeding the `clean_lines` cite validator with a probability rather than
a regex.

**Budget.** Seconds, in a background job.

### 5.13 Job-posting fit

**Today.** A weighted heuristic in the job scanner; the career-ops tool's
score is external.

**Reflex.** A Laya choice per listing over {strong fit, possible, no},
asked on the title, the first 350 tokens of the posting, and the owner's
profile line, in union with the heuristic: Laya can *raise* a listing to
the priority notification, not lower it, until promoted. The answer key is
the owner's tracker column (applied / ignored).

**Budget.** 400 ms per listing on the daily job-intelligence run.

### 5.14 Doctor and log triage

**Today.** Boolean probes and rule verdicts; crashes are counted, not
classified.

**Reflex.** Laya classifies each crash signature and each failed scheduled
run's error line into {transient, configuration, resource, code, unknown},
so Doctor groups them and the weekly ask (A3) sends a grouped report rather
than raw lines. It is a report feature only; nothing acts on it.

**Budget.** Background, per new signature.

### 5.15 Scheduler: run now?

**Today.** Due-time rules, an idle window, a 600 s activity gate, a GPU
lease hold.

**Reflex.** The rules stay. Laya adds one statement to the *interactive*
side: when the owner says "run the morning routine" or "do the job scan
now", the reflex resolves the routine by ID (§5.2) and calls `run_now`,
which is already the manual path. No model decides on its own to run a
job; "should this run now" for unattended work stays deterministic, by
design (north-star NS-10.4-1: proactivity only on triggers).

### 5.16 Hologram processing events

§4.9. The reflex emits `reflex`, `retrieval` and `ask` frames; the route
frame comes from stage C. Every frame has a status line and a spoken
equivalent; the lattice never carries a fact the words do not.

### 5.17 The Vibe Coding Salon's voice commands

**Today.** Specified (§6.2 of the salon spec), no code.

**Reflex.** The salon's table is a template set: undo, go back N, show me,
what changed, read the diff, how much has this cost, use model X, are all
`open_item`/`status`-class templates over the **codebase in focus** as the
item, resolved by name against the open codebases (a shortlist far under
ten). Edit requests ("make the header bigger") are `wants_action` turns
that go to `delegate_to_friday` with the codebase in scope, as the salon
spec says. Outward salon actions (post, go live, trust this codebase) are
cards; the reflex prepares the card with the codebase ID and never skips
it.

### 5.18 What the usage added

- **Approval replies by voice and text** (211 messages). The exact-phrase
  `_is_affirmative` stays the *decision*; a Laya statement *The user is
  agreeing to the pending question* runs in shadow beside it to measure
  how often a real yes ("go for it, but the short version") is missed. If
  the miss rate is material, §11's second decision is whether the reflex
  may recognise those forms in one-person mode.
- **Device control** (17 messages; play, pause, mute). No tool exists. The
  reflex's job is to say so in one sentence without waking the brain:
  `direct_command` true and no template matched → "I can't control
  playback yet." That is a reflex turn that saves a brain turn.
- **Status checks** ("what are you doing"). A `status` template →
  `check_situation`, spoken from live state, no brain.
- **Greetings and thanks** (51). `direct_command` false, `wants_action`
  false, short: the sidekick answers, not the brain. Amendment A6 is kept
  because the sidekick is a local seat the owner configured.

---

## 6. Measurements

**Status of this section: partial.** The full runs defined in the companion
file were started but had not finished when this revision was committed. What
is below was measured; nothing is estimated. The per-set results land in
`~/.friday/bench/reflexes/results/` as each set completes, and the tables for
6.2 to 6.5 are filled from those files in the next revision.

### 6.1 Measured so far (2026-09-29/30, idle machine, CPU only)

**Needle, per-call cost against Friday's real schemas** (the 8 `t2` tools,
one stateless call each; two calls per setting, first and second):

| Generation | Description clip | Schema chars | Call 1 | Call 2 | Prefill tok/s | Decode tok/s |
|---|---|---|---|---|---|---|
| Needle 2 | 600 | 5,416 | 8,752 ms | 7,795 ms | 94 | 47 |
| Needle 2 | 200 | 4,200 | 4,889 ms | 4,421 ms | 201 | 92 |
| Needle 2 | 80 | 3,531 | 3,617 ms | 4,279 ms | 147 | 82 |
| Needle 2, stateful, clip 200 | | | first 4,455 ms | **second, no reset, 658 ms**; after reset 3,711 ms | | |
| Needle 3 | 600 | 5,416 | 2,465 ms | 2,836 ms | 42 | 25 |
| Needle 3 | 200 | 4,200 | 3,784 ms | 5,056 ms | 63 | 12 |
| Needle 3 | 80 | 3,531 | 3,111 ms | 4,736 ms | 91 | 18 |

The smoke test with three one-line tools ran 170 to 560 ms on Needle 2 and
1.5 to 2.3 s on Needle 3. The difference is the tool prompt: a stateless
call re-reads every schema, and Friday's descriptions run to a thousand
characters. **A reflex that hands Needle more than a handful of short tools
is not a reflex on this CPU.** The design already assumed a shortlist; the
number says the shortlist for Needle is three or four tools with one-line
descriptions, and that the tool prefix must be kept warm between calls.
On this probe Needle 3 returned no call at confidence 0.04 to 0.05 on a
read-file request that Needle 2 answered correctly at 0.99.

**Laya** on this PC, from commit `efb7a539` (ONNX fp32, p50, CPU ~74% busy):
gate 1 question 371 ms at 4 threads and 314 ms at 6; voice 1 question 299
and 227; voice 2 questions 730 and 352; a ~350-token state 1,506 ms. The
`choice:6-10` shape the resolver needs, and the busy-versus-idle comparison,
are in the pending runs.

**The brain**, from the cost ledger (§2.2): cloud chat p50 4.7 to 8.0 s
depending on the model, p90 19 to 26 s; local interactive latency is not
recorded as a duration and is in the pending runs.

### 6.2 to 6.5 Pending

Needle 2 vs 3 on the 118-example set and on the owner's phrasings (name and
argument accuracy, no-call correctness, p50/p95, idle and with the brain
generating), Laya per shape including the ten-item choice, and the
end-to-end reflex-versus-brain budget. Not yet measured; not estimated.

---

## 7. The rules kept

| Rule | How this spec keeps it |
|---|---|
| Laya never acts alone on anything outward; add-only on safety | §1, §4.2 (outward templates prepare cards), §5.7 (features and offers, never verdicts), §5.8 (add-only), §5.9/5.13 (escalate-only unions) |
| No weakening of gates or cLaws | every reflex call goes through `_execute_tool` (§3.4); the discovery test is the fence; `verify_claws` unchanged |
| Every new question ships in shadow; owner labels are the key; promotion on evidence | §4.5, and a shadow key on every surface in §5 |
| Commands from real IDs and templates, never free generation | §4.2; Needle fills text slots only, checked against the words |
| Low confidence asks back, spoken and on screen | §4.4, §3.6 |
| Everything local, no telemetry | §2.5, §3.4: `NEEDLE_TELEMETRY=0` asserted at boot; decisions and shadow rows stay in `~/.friday`; presence frames carry no text and are never written |
| Voice-first parity | §3.6, §5.1, §5.6: one arc for every input; the voice contract's wording and card rules unchanged |
| Brand integrity | one status-line form "(reflex)"; the lattice gestures from the approved vocabulary; the approval hue reserved (§4.9) |
| Both paths | §4.6 |
| A clean seam for a CLM ranker or verifier | §4.7 |

---

## 8. Benchmark definitions and data

Definitions are in [laya-needle-reflexes-benchmarks.md](laya-needle-reflexes-benchmarks.md).
Raw data, the owner's phrasings, per-row results and the isolated Needle venv
are under `~/.friday/bench/reflexes/` and stay there. Nothing else under
`~/.friday` was written by this work, and the live server and live checkout
were not touched.

---

## 9. Phased build plan

**Pending.** The ordering by seconds saved per week of effort depends on
§6's pending numbers (whether Needle is worth a worker at all, and Laya's
ten-item cost). The order this spec expects, to be confirmed against §6:
(1) turn shape in shadow, §5.1, with prefetch on the partial transcript,
§5.6; (2) the resolver, §5.2, Laya-only, templates with real IDs; (3) tool
shortlist from the resolver's answers, §5.3; (4) the gate's card features
and batch-grant offers, §5.7; (5) mail urgency, §5.9; (6) injection triage,
§5.8; (7) News relevance after the read-log precondition, §5.11; then the
rest of §5 as background jobs. Effort, gates and budgets per phase are
written when §6 is complete.

---

## 10. North-star mapping

| Requirement | Today (gap matrix) | This spec |
|---|---|---|
| NS-12.2-1 router considers latency target, health, scheduled vs interactive | partial | §5.4 adds a reflex tier and a measured expected latency per tier |
| NS-12.3-1 structured routing record with alternatives and expected latency | partial (no alternatives, no latency) | §5.4 writes the record for every turn, reflex turns included |
| NS-12.4-1 / A6 local-only, no silent substitution | shipped | §4.6 unchanged; a reflex is not a substitution |
| NS-14.1-1 one context compiler | partial | §5.3/§5.5 feed the existing assembler; no new assembler |
| NS-14.9-1 instruction-shaped untrusted text isolated and labelled | partial | §5.8 labels it with a probability, add-only |
| NS-18.3-1 unknown tools outward | shipped | §4.2: a template names one known tool; Needle may not name another |
| NS-19.5-4 untrusted content never modifies a plan | partial | §3.4: reflexes read user words and Friday's shortlists only |
| NS-21.2-4 shell search and command entry | shipped | §4.3: the palette becomes the resolver's screen |
| NS-21.22-2 complete flat/voice/keyboard path (A1) | partial | §3.6: one arc, ask-back on both, keyboard pick |
| NS-22.2-5 UI shows voice latency | partial | §6.4's budgets are the numbers to show; the status line names the path |
| NS-22.3-1 voice choreography: acknowledge, ambiguity, act | partial | §3.3, §4.4 |
| NS-22.4-7 global stop phrase outside the model | missing | not built here; noted as the natural next `direct_command` template |
| NS-4.2-2 live-state questions query the source | partial | §5.5 routes "live state" to probes |
| NS-6.8-1 / A1 visualisations correspond to real events | partial | §4.9: reflex, retrieval and ask frames from real events only |
| NS-29.3-1 progress only when measurable | shipped | ask-back is a state, not a progress bar |
| NS-33.4 performance targets | – | §6.4 measures against them: navigation < 200 ms, card open < 300 ms, local search < 1 s |
| NS-10.4-1 proactivity only on triggers | partial | §5.15: no model decides to run a job |
| A3 no telemetry | in force | §2.5, §3.4 |
| A4 voice-first parity | in force | §5.1, §5.6, §3.6 |
| A5 brand integrity | in force | §4.9, §7 |

---

## 11. Decisions for the owner

**Pending, with the three questions named.** Each is written up with the
options and a recommendation once §6 is complete.

1. **Is Needle allowed at all?** It ships a closed engine binary and its
   telemetry is on by default. The owner's rule is zero telemetry, not
   "we turn it off". If the answer is no: Laya plus templates alone (the
   resolver needs no Needle), or an open decoder fine-tuned on the shadow
   logs, or a search for an open equivalent.
2. **May a reflex recognise a spoken "yes" to a card in room mode?** Today
   the exact-phrase rule decides and the spoken yes must name Friday.
3. **The labelling hour.** Which shadow questions get an hour of the
   owner's labels first, because every promotion in §4.5 needs his key.

---

## Appendix A. Provenance

Every number in this document was produced on this machine on 2026-09-29
and can be re-run from the companion definitions:

- Usage taxonomy and latency-today figures: `~/.friday/bench/reflexes/mine_usage.py`
  over `~/.friday`, output `usage_taxonomy.json`.
- Needle: `needle_bench.py`, results `results/needle_idle.json` and
  `results/needle_busy.json`.
- Laya shapes: `laya_resolver_bench.py`, results `results/laya_idle.json`
  and `results/laya_busy.json`; the reference numbers from commit `efb7a539`
  and `tools/laya_bench.py`.
- Code citations: main `b5389d94` unless a branch is named.
- Surveys of the judgment sites and the tool, gate and router code: two
  read-only sweeps of `src/agent_friday/` whose findings are the rows of
  §2.3 and the citations in §2.4, §5.
