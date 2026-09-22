# Why Friday was slow, and what changed

2026-09-18. Everything here was measured on this machine — an RTX 4070 with
12,282 MiB — and every number is from a log or a timed run, not an estimate.

The starting symptom: a chat turn either took forty seconds or never came back
at all, and the first message of a session was often answered by Sonnet 5
instead of the local model.

It was not one problem. It was nine, and several of them were mine.

---

## The state at the start of the day

- Bonsai 2 27B serving locally, apparently healthy.
- Turns taking 40s+, or hanging indefinitely.
- Cloud escalation happening without an explanation the user could see.
- 7.9 GB of disk free on a 930 GB drive.

---

## What was actually wrong

### 1. Friday was running twice

Two tray processes, both created at 07:56:24, each having started its own
server, each with its own scheduler and health probes — all landing on a
llama-server configured with a single slot. Every request queued behind
another Friday's request. The seat log showed four-token health probes taking
between seven and twenty seconds, which is what queueing looks like from the
inside.

The single-instance guard was a bare `socket.bind()`. That is not a reliable
mutex on Windows: without `SO_EXCLUSIVEADDRUSE` it can fail open, which is the
worst kind of guard because the source reads like protection while the
duplicate it was meant to stop runs anyway.

**Fixed** in `friday_tray.py`: a named kernel mutex (`Local\AgentFridayTray`),
with the socket kept as a cross-platform fallback and the exclusive-use option
set on it. Verified by starting a second tray on purpose and watching it
refuse.

### 2. The arbiter's context cap came from a different model

`LlamaServerBackend.MAX_SEAT_NUM_CTX = 131072` was measured on gemma4:12b.
Gemma 4 interleaves sliding-window layers with full-attention layers, so only
a third of them scale with context — which is why the curve looked flat enough
to justify that number. Bonsai 2 is a dense 27B where every layer scales.

So the arbiter kept killing a hand-configured 48K seat and respawning it at
131,072, which left 277 MiB free on the card and made every turn thrash. A
plain "say hello" sent into that state never returned.

**Fixed** in `residency_arbiter.py`: a model may declare `serve_num_ctx` and
`serve_args` in its own record, exactly as it already declares `engine`. The
class ceiling remains a ceiling — a declaration can only ever lower it — and
flags the arbiter needs to own (port, model path, alias) are refused if a
model tries to override them, so this cannot become a way to hide a seat from
the process responsible for it.

Bonsai now declares 32,768 with `-b 4096 -ub 2048 -np 1`.

### 3. A failed spawn was read as "this build rejects KV quantization"

Any `TransitionError` during spawn set `_kv_quant_unsupported = True` for the
whole process, permanently downgrading every seat to an f16 KV cache. f16 is
twice the size. So an out-of-memory failure was being answered by using more
memory, on the card that had just run out.

**Fixed**: the downgrade now requires the build to actually say so. The
markers llama.cpp really prints for a rejected flag are listed, as are the
ones it prints for an allocation failure, and a message containing both reads
as the allocation failure — because that is the one that explains the exit.
Unexplained failures get one identical retry instead, which today's evidence
says is usually enough.

### 4. Seats were not reaped before the next engine was tried

On failure the arbiter asked the running seat to stop and immediately started
the next engine on the next port, without waiting. A 27B takes seconds to
release ten gigabytes. Two bonsai2:27b servers were found alive on :8090 and
:8091, together holding 11,605 MiB of 12,282.

**Fixed**: wait for the exit, escalate to a kill if the request is ignored,
and sweep the port before the next attempt — touching only processes that
loaded from Friday's own model directory.

### 5. A seat was being spawned per *role*, not per model

`_pin` calls `load()` once per pinned role, and five roles on this machine all
named bonsai2:27b. Nothing checked whether the model was already served, so
each role spawned its own 27B. Worse, `self.procs` is keyed by model id, so
the second spawn overwrote the first's record — the first was not merely
redundant, it was orphaned, unevictable and invisible to the arbiter.

**Fixed**: `load()` takes a lock, checks whether the model is already serving
(its own record first, then the ports, so a seat that outlived the arbiter is
adopted rather than duplicated), and only then spawns. Four concurrent roles
now collapse to one spawn. A seat running at a non-conforming context is still
reloaded rather than adopted.

### 6. Every chat turn blocked on an HTTP call to a daemon that wasn't there

Building the system prompt reaches `local_seats.installed()`, which asks the
Ollama daemon for its inventory with a four-second timeout. Ollama was
removed from this machine today, so that was four seconds of nothing on a path
every turn takes.

**Fixed**: a sub-millisecond connect check first. A closed loopback port
refuses immediately, so asking costs nothing and skips the timeout. The
negative is deliberately not remembered — a daemon someone starts later should
be found on the next refresh, not after a restart.

### 7. The first turn after every restart imported torch, inline

Caught with py-spy: a request thread sitting inside
`import sentence_transformers`, underneath the sensitivity classifier's
lazy `_load_embedder`. Every chat turn triggers it through the vault-access
check, so the first turn after a restart paid for the whole import — measured
at 103.5 seconds — on the request thread while the user watched an empty box.
That is the entire difference between the 104-to-166 second cold turns and the
46 second warm ones.

**Fixed**: `server.py` warms it on a daemon thread during boot, where it
overlaps everything else.

One caveat worth keeping: `/api/health` reports healthy about eighty seconds
before the embedder finishes. A first turn inside that window still waits.

### 8. A stale port number sent every session's first message to the cloud

`bonsai2-local.provider.json` carried `http://127.0.0.1:8099/v1` — the port
chosen by hand this morning. The arbiter had the seat on :8090 and published
that. Nothing reconciled them, so Friday dialled a dead port, failed, and
escalated to Sonnet with `Max retries exceeded ... port=8099` buried in a
fallback chain nobody reads.

**Fixed**: for local providers the published endpoint outranks the descriptor.
It is written by the process that started the seat, which makes it the only
account of where that seat is that cannot be stale by construction.

### 9. The prompt cache never hit — three separate reasons

This one took most of the day and needed instrumentation rather than
reasoning. The seat itself is blameless: driven by hand with a Friday-shaped
payload it reuses a prefix perfectly.

```
cold                     31.5s / 15,955 tokens
identical repeat          0.43s /      4 tokens     73x
extended conversation     0.69s /     27 tokens
with 40 tool schemas      0.48s /      4 tokens
```

Friday's own turns reprocessed the entire ~21,000-token prompt every time, at
about 500 tokens a second. Forty-three seconds a turn, rebuilding something
the seat already had.

Recording the actual outgoing payloads found three causes:

**The transcript was a sliding window.** `CHAT_HISTORY[-100:]`, sliding two
messages per turn, so every message changed position and nothing could match.
Fixed: `_history_start()` advances in steps of twenty. Over forty turns the
window moves four times instead of thirty-nine, and 90% of consecutive turns
share an identical transcript prefix.

**The tool list changed size.** Consecutive turns sent 62 tools and then 63 —
the budget subtracts the prompt from the window, the prompt breathes, the trim
count breathes with it. Tools render before everything else, so one tool
appearing invalidates the prompt from character zero. Fixed by quantising the
budget to 2,048-token steps, which makes the selection a step function with no
memory. Remembering the last decision was tried first and ratchets: whichever
direction it prefers becomes a drift (39 tools, then 37, then 36, measured).

**The `[SEAT]` note was appended once per call, not once per turn.** An
agentic turn passes through `_call_openai` repeatedly and each pass stapled
another nine-hundred-character paragraph onto the system prompt. Fixed: append
only if not already present.

After these, two consecutive calls within a turn came out byte-identical
across tools, system prompt and all ninety-seven messages — 100% reusable, the
first time all day.

---

## Where it ended up

With everything above in place, the arbiter log reads as it should:

```
[arbiter] adopted bonsai2:27b on :8090 (pid 28216)
Sensitivity embedder: ready (100.4s)
[ROUTER] chose local/bonsai2:27b | kept on a local seat
bonsai2:27b: core tools trimmed 75 -> 68 ... with a 10,276-token prompt
bonsai2:27b: core tools trimmed 75 -> 68 ... with a 10,236-token prompt
bonsai2:27b: core tools trimmed 75 -> 68 ... with a 10,209-token prompt
```

Three consecutive turns, the same 68 tools with the same seven dropped, and
those three turns returned in **3.8, 3.7 and 3.8 seconds**. The seat log shows
what that looks like from below — a 20,000-token eval followed by
`prompt eval time = 202.95 ms / 4 tokens`. The cache is reusing the prefix.

That is the good case. The bad case still happens, and the reason is the last
open item below.

## Still open

**The transcript's token count swings between about 10,000 and 20,000.** The
window holds 100 to 119 messages, and as short old messages age out and long
new ones arrive, the total moves by thousands of tokens. That crosses several
budget steps, which changes the tool list, which breaks the cache — and it
doubles the prompt, which is expensive on its own. A turn measured right after
the fast three took 50.4 seconds with a 20,092-token prompt.

Holding 100 messages of conversation in every prompt is the root of it. Thirty
would cut the prompt substantially and make it far steadier. That is a
decision about how much conversation Friday should carry in context rather
than retrieve, which is Stephen's to make, so it is recorded here rather than
changed.

**The "empty reply" was my own error and is withdrawn.** The reply text lives
in `friday_msg.text`; the `response` field is something else. Nothing was
empty. Recorded here because a false bug report costs someone a search.

### The cache misses have one more cause, and it is structural

After the tool list was stabilised and the transcript budgeted, three
consecutive turns at 21,447, 21,461 and 21,260 tokens — within 200 tokens of
each other, so the window had not moved — still reprocessed in full.

That rules out everything above as the remaining explanation, and leaves the
seat's slot geometry. The seat runs `-np 1`: **one slot, one cached prompt.**
Friday sends short health and capability probes to that same seat between
turns, and a probe landing in the only slot replaces what it was holding. By
the time the next chat turn arrives the slot's cache contains the probe's
four-token prompt, not the chat prefix.

This is why `-np 2` was the right instinct this morning. The mistake was
pairing it with `--kv-unified`, which shares one cache between the slots and
so cannot keep a per-slot prefix at all. Two slots WITHOUT a unified cache is
the configuration that works: llama.cpp assigns a request to the slot with the
best prefix match, so chat keeps its slot and probes land on the other.

The obstacle is arithmetic. `-c` is divided among slots, so two usable slots
need twice the window, and this card has no room for it — 65,536 measured at
11,518 MiB of 12,282, leaving less than the desktop itself needs.

So the options are:

1. **Shrink the prompt until two slots fit.** At ~12,000 tokens, `-c 32768
   -np 2` gives two 16K slots and fits the VRAM already proven. Getting there
   means carrying fewer than 61 tool schemas (currently ~8,200 tokens) and a
   smaller transcript. This is the only option that makes local turns fast.
2. **Give the probes their own seat.** A second, tiny model answering health
   checks, so nothing evicts the chat slot. Costs VRAM that isn't there.
3. **Accept ~45s local turns** and route interactive chat to the cloud, using
   the local seat for background work where latency does not matter.

Option 1 is a question about how many tools a local seat should carry, which
is a product decision rather than an engineering one.

### The probe was real, the fix shipped, and it was not the cause

`provider_health.inference_probe` sends a genuine completion — `_PROBE_PROMPT
= "hi"`, `_PROBE_MAX_TOKENS = 16` — to every configured provider, cached for
`_PROBE_TTL_S = 60`. Against a keyed cloud provider that is the only way to
tell "a key is present" from "a key still works". Against a llama-server this
machine started it proves nothing that `/health` does not, and it costs the
slot's cached prompt.

Fixed: a local provider is now proven by `/health` plus `/v1/models` (the
second matters — "something is serving on this port" is exactly the claim that
once let a seat run for hours under the wrong alias). The probe generation
count in the seat log went from one a minute to one at boot.

**It did not restore the prompt cache.** Two consecutive local turns
afterwards, 21,736 and 21,668 tokens — sixty-eight tokens apart — both
reprocessed in full. So the eviction hypothesis was wrong, or at least not
sufficient, and the divergence is still somewhere at the very front of the
request.

The fix is kept regardless: a health check that sends a real generation to a
local model every sixty seconds is waste whether or not it was the cause here.

The next step is not another hypothesis. It is the payload recorder, pointed
at two **consecutive local turns** specifically — the earlier capture caught
two calls inside one turn and a pair that straddled a window move, neither of
which answers the question. Everything at the front of the request is now
either verified stable (tools, system prompt, the `[SEAT]` note) or budgeted
(the transcript), so whatever still moves will be visible immediately in a
byte diff of the right two payloads.

### An error of mine, worth recording

Updating `bonsai2-local.provider.json` from PowerShell wrote a UTF-8 BOM.
Python's `json.loads` rejects it, so Friday silently stopped being able to
parse its own provider descriptor and `bonsai2-local` vanished from the health
surface entirely. Local routing kept working only because
`model_router._call_openai` had just been changed to prefer the Arbiter's
published endpoint over the descriptor — one fix from earlier the same day
happened to mask the other.

`Set-Content -Encoding UTF8` in Windows PowerShell 5.1 emits a BOM. Config
files that Python reads must be written without one.



**The capability block flips mid-session.** The system prompt contains
"bonsai2:27b is installed but nothing is serving it right now", which becomes
"serving at 127.0.0.1:8090" once the seat is up. It sits at character 18,566
of a 21,686-character system prompt, so the damage is bounded, but it is a
divergence between turns.

**The transcript window still moves between some turns.** The step fix is
correct and tested; whether it is sufficient in practice needs a longer run
than I have measured.

**The standing prompt is ~21,000 tokens before you say a word** — roughly
9,000 of tool schemas, 5,000 of system prompt, the rest transcript. Even
perfectly cached, that is the ceiling on how fast Friday can feel. Reducing it
is a product decision about how many tools a local seat should carry.

**`/api/health` reports healthy before Friday can actually answer.** It should
reflect the embedder warmup.

**Five capability seats point at `openrouter`, which has no key configured** —
`orchestrator`, `sidekick_fast`, `function_manager`, `researcher` and
`embedding`. The last is plainly wrong on its face; an embedding capability
should be the local MiniLM. The other four are model assignments, which are
yours to make, so I have left them alone and am flagging them instead.

**743 references to Ollama across 76 source files.** The runtime dependency is
gone — the daemon served nothing, the one binary Friday actually needed (the
build that can read the Gemma-4 e-series, which upstream refuses and
FridayWeaver is built on) was copied into `runtime/llama.cpp-ollama` before
anything was deleted. The code-level cleanup is a refactor and wants its own
pass.

---

## Housekeeping

Disk went from **7.9 GB free to 64 GB**: temp files, the npm cache, crash
dumps, a superseded copy of the PrismML llama.cpp build that nothing
referenced, abandoned partial downloads, and 42.7 GB of Ollama model blobs.

The payload-dump instrumentation added during this investigation is off by
default and switched on by creating the directory
`~/.friday/runtime/diag/payload-dump`. It writes whole prompts in plain text —
memories and vault material included — so it should be turned off by deleting
that directory when you are done with it. It was deleted at the end of this
session.

---

## What shipped afterwards

The investigation above was the first half of the day. The second half was
committing a backlog and building the specs, in 27 commits from `18d3d91`.

**The repository.** 85 files were uncommitted, spanning 2026-09-08 to 09-18 —
33 of them dated the 9th alone. That is a fortnight of finished, tested work
living in a working tree where one bad command would have taken it. All of it
is now in git, grouped by theme, each group's tests run before its commit.

Three things the repo's own pre-commit guard caught, all of which would
otherwise have shipped: the maintainer's real email address hardcoded in three
test files and one history record (eight occurrences, replaced with
`primary@example.com`); a private Windows path in a design document; and one
false positive — a `restore_token` in the arbiter tests whose value is a
model-and-port identifier rather than a credential, which got the documented
allowlist pragma rather than a rename to dodge the check.

(The guard then caught this very paragraph, because the sentence describing
that false positive contained the assignment it was describing. It was right
both times, which is the correct number.)

`ui_parts/app.html` had been rewritten CRLF at some point, so its diff was
24,064 lines hiding 184 real ones. Normalised back to LF before committing:
same content, reviewable diff.

**The specs.** Eight of ten items:

- **1 — multiple chat windows and undock.** The UI had been calling six
  `/api/conversations` endpoints that did not exist, with a complete service
  layer underneath and 125 conversations sitting unreachable on disk. Wrote
  the routes; the switcher, the per-chat model picker and the transcript
  loader all work. Added the undock button and a chrome-less window mode.
  A second conversation binding a different local model is refused with a
  reason naming what is in the way.
- **2.4 — latency budget.** Three declared ceilings with tests, including two
  that guard the guard: one proves the measurement is not returning zero, the
  other proves the ceiling is between 1x and 2x the measured value, because a
  budget at reality always fires and one far above it never does.
- **2.5 — receipts over regex.** A claimed action is now checked against
  receipts for THAT action rather than only when nothing ran at all.
- **3.1 — the tier vocabulary.** `spawn_task` takes `small_local`,
  `large_local` or `cloud_frontier`. A tier that cannot be served is refused
  with a reason; nothing is quietly relocated across the local/cloud line.
- **3.5 — the honesty suite.** Twelve golden fixtures had been sitting with no
  runner for weeks. Split into a deterministic validator that always runs and
  a behavioural runner that is opt-in (`--run-honesty`), verified end to end
  against the live seat. Sycophancy is reported for human review rather than
  scored, because no string distinguishes agreement from
  agreement-because-pressed and a scorer that guessed would be inventing a
  verdict.
- Plus the heartbeat on one click — it existed, four clicks deep in Settings,
  which is the same as not existing.

**2.2 is mostly already done**, which is worth recording rather than building
something to cover. The capability-state block puts live account status in the
system prompt every turn, and the UI already colours `needs_reauth` distinctly
with the nine-day incident recorded in a source comment. What remains is a
scheduled sweep and a lead-with-the-expiry rule.

**3.3 was deliberately not started.** 3.1 unblocks it; it is a design piece
rather than a repair, and it will be better done fresh.

**Found while committing, unresolved:** the GitHub connector is in `error`
with `spawn failed: GCM auth tag mismatch — tampered ciphertext or wrong key`.
That is the encrypted credential store failing to decrypt — either the token
was written under a different key or the vault key changed beneath it. Left
for the maintainer.

**Not verified:** the undock button and the heartbeat button parse and are
served, but no human has seen them render. The browser pane was unavailable.

**Pre-existing test failures, not regressions:** `tests/unit/
test_residency_arbiter.py` and `test_run_chain.py` fail on this tree and
failed identically at `18d3d91`, checked in a worktree at that commit —
twelve failures there, nine here. Their count also shifts with which files are
run together, so there is shared state between them.

## The pattern worth remembering

Six of these nine faults share one shape: **a permanent conclusion drawn from
a single ambiguous observation.**

The context cap measured on one model, applied to all models. The KV flag
declared unsupported from any error. The seat assumed dead the moment it was
asked to stop. The port written down once and trusted forever. A tool budget
recomputed from a number that moves. And my own two: reverting to a unified KV
cache to fix probe contention, and reading a 300-millisecond idle penalty as
the twenty-second stall I was hunting.

The instrumentation added today — payload recording and the seat's own timing
log — is what finally distinguished the real cause from six plausible ones. It
took hours of reasoning from the outside to get nowhere, and one recorded turn
to settle it.
