# Making the residency layer govern the running machine — 2026-08-15

**Branch:** `residency-policy`, unpushed.
**Predecessors:** [`residency-state-delta.md`](./residency-state-delta.md),
[`residency-implementation-report.md`](./residency-implementation-report.md),
[`../design/residency-policy.md`](../design/residency-policy.md).

**Registers:** **VERIFIED** (command output or file:line seen), **INFERRED**, **UNKNOWN**.

---

## 1. What was reported, and what was actually wrong

| # | Reported | Actually |
|---|---|---|
| 1 | Only Claude in the orchestrator/subagent pickers; creative only Google | **Two problems.** The pickers were a stale tab — the server was serving all four gemma models correctly (**VERIFIED** by querying it directly). The creative slot was real: no local image provider existed. |
| 2 | "Start my day" ran gemma, tray said *failed — waiting for activity*, reply claimed Sonnet 4.6 | `— waiting for activity —` is a UI **empty-log placeholder** (`app.html:6920`), not an error. The chain: 12b was not gated green → no usable green fallback → tools disabled → cloud → `start.bat:8` pins `claude-sonnet-4-6`. |
| 3 | Gating "just sat there" with no visibility | The gate was **broken six ways** and was condemning working models. |

---

## 2. The gate was condemning working models

Stephen gated four models at once. **VERIFIED** from the stored records:

```
gemma4:12b  structural 1/10   — 9 of 10 cases logged "timed out"
gemma4:26b  structural 1/10
gemma4:e2b  structural 4/10   — overwrote a standing GREEN
gemma4:e4b  structural 0/10
```

Re-run with a repaired harness — **VERIFIED**, same models, same machine, same day:

| model | before | after |
|---|---|---|
| `gemma4:12b` | 1/10 | **10/10** |
| `gemma4:26b` | 1/10 | **10/10** |
| `gemma4:e4b` | 0/10 | **10/10** |
| `gemma4:e2b` | 4/10 | 10/10, then 8/10, then 9/10 — see §3 |

**Six harness defects**, each sufficient alone:

1. **No `num_ctx`** — the gate inherited Ollama's default (262144 for gemma4, 79 % on CPU).
2. **A flat 120 s timeout** — the 12b run takes 151 s; a single 26b case took 102 s; a cold reload alone is 20–55 s.
3. **Concurrent gating** — four runs evicted each other, so every case paid a cold load, *manufacturing* the timeouts.
4. **The `/api/chat` fallback dropped `tools`** — a model given no tools cannot emit a tool call, so any retry was guaranteed to read as a tool-calling failure.
5. **`options.num_ctx` was silently discarded.** `options` is Ollama-native; the OpenAI-compatible endpoint accepts and ignores it. **VERIFIED:**
   ```
   /v1/chat/completions  options.num_ctx=8192 -> ollama ps says 131072
   /api/chat             options.num_ctx=8192 -> ollama ps says   8192
   ```
   So the "explicit context" fix reached nothing until callers were routed to the native endpoint.
6. **The context was set below the tool registry.** Measured: 52 tools = 34 138 chars ≈ **8534 tokens**, total prompt ≈ **8643** — above the 8192 I had chosen from the VRAM curve. A seat too small for its tools manufactures exactly the symptom the gate detects. Tool seats are now 32768, and `min_tool_context()` derives the floor from the live schema.

**The integrity fix that matters most.** A harness timeout was scored identically to model
misbehaviour, so an untested model earned a red — and that red **overwrote `gemma4:e2b`'s
standing green**. A run containing harness faults is now `inconclusive`: written beside the
authoritative record, never over it; `passed` is `None`; and it reads as **ungated**, not red.
Genuine failures are still red, and a test pins that.

---

## 3. The gate is not reproducible, and that is a finding

`gemma4:e2b` structural, four runs, same machine, same day: **10/10, 8/10, 8/10, 9/10**. The
"failing" cases pass every time when replayed in isolation. Honesty varied too (11/12 → 10/12)
despite that axis already running at temperature 0.0.

`honesty_battery.py:317` documents the same effect ("gemma4:latest at 0.2 swings between 9/10
and 7/10") and chose 0.0 for it. I set 0.0 on the structural axis to match — then **reverted
it**: at 0.0 the `email` case fails deterministically where it passed at 0.2, so it is not
simply a cleaner measurement, and changing what a gate *means* was not authorised. Raised as
**Q11** instead.

**Consequence:** an all-or-nothing 10/10 bar over a measurement that varies by two points makes
seating partly a matter of which run you took.

---

## 4. Gate state as it now stands

**VERIFIED** from the store, with timestamps:

| model | structural | honesty | dual-green |
|---|---|---|---|
| **`gemma4:12b`** | **10/10** (12:09) | **12/12** (12:31) | **✓ YES** |
| `gemma4:e4b` | 10/10 (12:11) | 11/12 (12:21) | no |
| `gemma4:e2b` | 9/10 (12:44) | 10/12 (12:45) | no |
| `gemma4:26b` | 10/10 (12:17) | **1/12 (11:02)** | no |

**The 26b's honesty red is not trustworthy and is flagged as such.** That record is from
Stephen's original broken run at 11:02, before any harness fix; it predates the `inconclusive`
marking, so it reads as a red rather than as "never measured". **UNKNOWN** what the 26b actually
scores on honesty — the re-run was interrupted twice and never completed. The check that would
settle it is a single serial run.

The `e4b` and `e2b` reds are genuine, measured post-fix, and each is one or two items short.

---

## 5. What now governs the running machine — verified live

The server was restarted so the work is actually live. **VERIFIED** after restart:

- **The Arbiter boots.** `gemma4:e2b` resident at **32768 context, 100 % GPU** — the tool-seat
  context, loaded by `_residency_boot`, not by a chat request.
- **`/api/models` serves the right catalog:** all four gemma models in `orchestrator`, and
  **`z-image-turbo-fp8`** in `creative` with `local=True`.
- **`/api/health` → `ollama-local: ok`,** `proved_inference: true`, 85.81 ms/token.
- **Seats bound from the plan** — `reasoning` and `orchestrator_model` → `gemma4:12b`.
- **`embedding` untouched** at `all-MiniLM-L6-v2` (D5), verified at every checkpoint.

### 5.1 Two bugs the restart itself exposed

**`/api/health` reported `down` on a healthy system.** **VERIFIED** live before the fix:
`anthropic: down — empty completion`, while Anthropic was serving chat. `_PROBE_MAX_TOKENS = 1`
can stop before any text block is emitted. Raised to 16; re-probed live → `status: ok,
proved_inference: true, generated in 1614ms`. This is the exact inverse of the failure D1 set
out to fix, and just as corrosive.

**A bound capability without its flat mirror is silently reverted.** **VERIFIED** in the live
settings after the first boot:

```
"creative_image": {"provider": "local-comfyui", "model": "gemini-nano-banana-2"}
```

A Google model on the on-device provider. `core._sync_capability_routing` derives
`capability_routing` *from* the flat `*_model` keys, so writing the capability without updating
`creative_model` let the sync restore the old model beside my new provider. Fixed, with a test
asserting every bound capability has a mirror. This is the two-surfaces problem appearing inside
the module written to end it.

---

## 6. Everything else fixed

- **The stale fallback seat.** `get_last_known_green` returned `qwen3.6-35b-a3b-iq4nl`,
  decommissioned the day before. **Correction to an earlier claim of mine:** this did *not*
  cause the "start my day" failure — `resolve_local_seat` already checked availability at
  `model_seat_gate.py:530-540` and correctly returned `tool_free`. The fix is defence in depth,
  moving the check into the function itself so it is honest standalone.
- **Gating streams progress** into the orb log, per case, with pass/fail/elapsed.
- **Gate runs are serialized** machine-wide.
- **A failed catalog fetch is reported**, not papered over with hardcoded Claude/Google lists.
- **`comfyui` is a local-capable adapter**, so on-device image prompts are not egress-gated as
  if leaving the machine — with a test that a *public* comfyui URL is still cloud.
- **`model_catalog` derives `local` from `classification_of`** rather than a hardcoded type list.

---

## 7. Definition of done

| Requirement | Status |
|---|---|
| Default suite green | **VERIFIED** — see §8 |
| Arbiter governs the running machine | **VERIFIED** — e2b resident at 32768 by boot, seats bound |
| Plan drives `capability_routing` | **VERIFIED**, with `embedding` excluded |
| Z-Image in the creative slot | **VERIFIED** — `local=True` in `/api/models` |
| Branch unpushed | **VERIFIED** |
| Stephen's end state fully live | **NO — see §9.** Only the 12b is seatable. |

---

## 8. Open decision questions

Still unanswered, and still Stephen's: **Q1** pinned pair on llama-server · **Q2**
`--n-cpu-moe 20` vs bending R3 · **Q3** `local_inference_slots` · **Q4** the 10 GB R8 floor ·
**Q6** the embedding model · **Q7** the 8 GB Windows OS reserve.

**Q10 — should the honesty axis be pass-at-12/12, or a threshold?** Every gemma4 model lands
11/12 or 10/12. One failing item is `date_weekday`, where the model *called `search_web`* and
the harness scored the first message — which has no content. Only `connection_state` and
`completion_honesty` get a second leg, so a model that defers to a tool on a date question fails
without ever being asked to conclude.

**Q11 — should the structural gate be made reproducible, and how?** It varies by two points run
to run (§3). Options: temperature 0.0 (tried, not obviously better), best-of-N, or a threshold
below 10/10. All three change what seating means, so none is mine to pick.

**Q12 — should the 26b's honesty be re-measured before its red is believed?** Its only record is
from the broken harness.

---

## 9. The honest bottom line

Stephen asked for 26b heavy, 12b orchestrator, e2b and e4b for small tasks, embedder resident,
image on Z-Image — **as the running config**.

What is live: **the 12b orchestrator seat and the Z-Image creative seat.** The embedder is
resident on CPU as designed. The plan computes all eight seats correctly and the Arbiter boots
to it.

What is **not** live: `heavy_hitter`, `local` and `subagent` are still refused, because
dual-green seating requires both gate axes and only the 12b has them. The refusals are correct
behaviour — binding an ungated model would trade a visible refusal now for an invisible one at
dispatch — but they mean the arrangement is **partially** realised, not fully. Whether it can be
fully realised depends on Q10, Q11 and Q12, which are decisions about the gate, not about the
residency layer.
