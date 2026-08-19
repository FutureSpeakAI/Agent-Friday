# Contract: roles, model identity, and the budget preview

**Status:** live on branch `model-suite-determination`. **Audience:** anything that renders a model
picker, binds a model to a conversation, or shows the user what their machine can hold.

This exists because two sessions agreeing verbally is how a picker ends up rendering thirteen seats
as thirteen simultaneous models. If you are building against the residency layer, this file is the
agreement; the session report that produced it is not.

---

## 1. `ROLES` went from 8 entries to 13 — this is a breaking change

Anything that iterates `residency_policy.ROLES` and assumes the old set will now see five more.

```
interactive_brain  heavy_hitter  sidekick  sidekick_heavy  embedder  stt  tts  image
orchestrator  sidekick_fast  function_manager  memory_manager  researcher
```

The five new ones are the **working roles** Stephen named on 2026-08-18. Do not assume a role is a
model, and do not assume a role list is a count of resident processes. See §3.

---

## 2. Two alias tables. Resolve both, or you will render duplicates.

### Role aliases — `residency_policy.resolve_role(role)`

| you may receive | canonical |
|---|---|
| `embeddings_manager` | `embedder` |
| `embedding_manager` | `embedder` |
| `brain` | `interactive_brain` |
| `heavy` | `heavy_hitter` |

`embeddings_manager` is what the embedder seat is called when described by its job rather than its
mechanism. **It is the same seat.** Render it once. If you treat the two as separate rows, the user
assigns both, and the budget counts one model twice.

### Model aliases — `residency_catalog.canonical_model_id(model_id)`

| you may receive | canonical |
|---|---|
| `qwen3-embed:0.6b-q8` | `qwen3-embedding:0.6b` |
| `qwen3-embed:0.6b` | `qwen3-embedding:0.6b` |

The same weights arrived twice — once when Ollama pulled them, once when they were copied into
Friday's own store — and became two ids for one 0.6B embedder. `installed_entries()` deduplicates,
but the canonical id is what it emits, with the old name preserved in an `aliases` list on the
entry. **A persisted setting may still hold the legacy id**, so resolve before comparing.

Canonical form is the **upstream name** — what `ollama list` shows and what someone would search
for.

### Duplicates nobody has met yet

`residency_catalog.duplicate_candidates(entries)` is a standing check, not a merge. It reports a
suspected duplicate when two artifacts satisfy **both** conditions:

* sizes within **32 MB absolute** (container framing overhead is a fixed cost, not a proportion), and
* ids share a real word, after stripping registry prefixes and quantisation markers.

Both are required, and each one alone produced a false positive on the live inventory: a relative
tolerance merged `gemma4:12b` with a 12B finetune 134 MB away, and size alone merged two unrelated
600 MB embedders 17 MB apart. **Merging is worse than duplicating** — a duplicate is visible, a
wrongly merged model has silently vanished. Surface suspicions; never collapse on suspicion.

---

## 3. Residency classes — why 13 roles is not 13 models

`GET /api/residency/roles` returns, per role: `residency`, `cpu_capable`, `default_num_ctx`, plus
the alias table. **Read it. Do not hardcode this.**

| class | meaning | costs VRAM |
|---|---|---|
| `resident` | conversational path; must be warm | all day |
| `leased` | commissioned, exclusive; evicts resident seats except the retained sidekick | only while running |
| `on-demand` | scheduled or reactive (nightly consolidation, STT, TTS) | only while running |

Current assignment: `orchestrator`, `sidekick`, `sidekick_fast`, `function_manager`, `embedder`,
`interactive_brain` are **resident**. `heavy_hitter`, `researcher`, `sidekick_heavy`, `image` are
**leased**. `memory_manager`, `stt`, `tts` are **on-demand**.

`cpu_capable` roles (`embedder`, `memory_manager`, `stt`, `tts`) cost **no VRAM** when they hold a
model alone — but a CPU-capable role sharing a model with a GPU role is pulled onto the GPU, because
one process cannot sit on two devices.

If the picker presents thirteen roles without their residency class, a user reasonably concludes
they need thirteen models resident at once. On a 12 GB card that reads as impossible, and it isn't:
**seven roles fit in four models at a 7,046 MiB peak against 8,241 usable.**

---

## 4. One model, many roles — counted ONCE

A model held by three roles is one process holding one copy of one set of weights.

* **One charge.** `assignment_cost()` groups by model before costing anything.
* **One context** — the largest any sharing role needs, since they share a process.
* **One residency class** — the warmest. A model held by the orchestrator (resident) and the
  researcher (leased) is resident; a lease cannot evict what the conversation is using.

If you compute your own totals by summing per-role figures, you will overstate a shared lineup and
tell the user it does not fit when it does. **Use the preview.**

---

## 5. `POST /api/residency/preview` — warn at selection time

Request:

```json
{ "assignments": { "orchestrator": "gemma4:e4b", "sidekick": "gemma4:e2b" } }
```

Response is **always 200**, including when the selection does not fit. Notable fields:

| field | meaning |
|---|---|
| `fits` | false if it overflows, names an uninstalled model, or contains an unmeasured one |
| `advice` | one sentence written for a person; safe to render verbatim |
| `overflow_mib` | how far over |
| `would_evict` | which seats would have to give, largest first |
| `not_installed` | assigned models whose weights are absent |
| `unsized` | installed models never measured on this machine |
| `models[]` | per-model rows: `roles`, `residency`, `device`, `vram_mib`, `sized`, `num_ctx` |
| `resident_vram_mib` / `peak_lease_vram_mib` / `peak_vram_mib` / `peak_state` | the three states the card passes through, and the worst |

**This is advice, not a gate.** Stephen's standing rule all day: a model he selects wins. Show the
consequence, let him choose. Do not refuse, and do not silently substitute.

Two traps:

* **`vram_mib: null` means unknown, not free.** An unmeasured model was briefly coerced to 0, which
  made a lineup "fit" precisely because nobody knew what it cost. Render unknown as unknown.
* **The advisory is computed against a live display reserve.** `arbiter.preview()` re-samples what
  the compositor is holding before answering, because the card's usable budget moves with monitor
  count and what is open. Do not cache the result across a session.

---

## 6. Rule R11 — the working roles are *assigned*, not inferred

The policy picks `interactive_brain` and `sidekick` itself, because there is a right answer: the
biggest thing that fits, and the fastest thing left. **There is no such rule for an orchestrator or
a memory manager.** Which model should route your work, or read your day and decide what is worth
keeping, is a judgment about how you want to work, and guessing it would be the policy inventing a
preference and then hiding it.

So an unassigned working role is **refused by name** rather than left silently empty. In a plan it
appears as a `null` seat plus a refusal:

```json
{ "role": "orchestrator", "model": null, "rule_id": "R11",
  "explanation": "no model assigned; this seat is chosen by the user, not
                  inferred, and stays empty until one is named" }
```

**Render an R11 refusal as an empty seat awaiting a choice — not as an error and not as a failure.**
It is the normal state of a role nobody has assigned yet. Every other refusal rule (R1–R10) means
something could not be done; R11 means nothing has been asked for.

---

## 7. Where things live

| what | where |
|---|---|
| role list, aliases, residency classes, `assignment_cost`, `preview_assignment` | `services/residency_policy.py` |
| model identity, `canonical_model_id`, `duplicate_candidates`, `installed_entries` | `services/residency_catalog.py` |
| live-profile preview | `residency_arbiter.Arbiter.preview()` |
| endpoints | `routes/residency.py` |
| tests | `tests/unit/test_residency_roles.py` |

## Open, and worth knowing before you build on it

1. `model_store.register()` does not call `register_gguf()`, so a model downloaded directly into
   Friday's store is invisible to the arbiter until someone registers it by hand. Only the
   Ollama-extraction path registers.
2. `gemma4:26b` (17,391 MiB measured) no longer fits this card at all under an honest display
   reserve, and a resident 12B beside the sidekick (9,529 MiB against 8,241) does not either. Any
   default lineup a picker offers should not assume they do.
3. `sidekick_fast` exists in `ROLES` but has no seating logic of its own yet; it is a valid
   assignment target and behaves like `sidekick` for costing.
