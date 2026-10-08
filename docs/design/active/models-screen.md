# Settings → Models: pick, fit, download and keep any local model

> **Status:** active, build plan with the spec it serves
> **Written:** 2026-10-02
> **Serves:** [`model-soup-advisor.md`](model-soup-advisor.md) (the advisor's roles, licence and telemetry classes, solver), [`bonsai2-floor.md`](bonsai2-floor.md) and [`bonsai2-tiers.json`](../../../src/agent_friday/resources/bonsai2-tiers.json) (the manifest, the runtime assets, the pick), the owner's direction of 2026-10-02 ("let users select and download their own models as they please ... maybe someone else can run something bigger, or multiples"), and the llmfit evaluation of 2026-10-02 (borrow its ideas, not its engine: real file bytes, the display reserve, per-card budgets, fit at the served context, resume plus checksum).
> **Owner rules carried:** zero telemetry (the hardware profile never leaves the machine; catalogue reads are plain public GETs with nothing identifying); no downloads during development without the owner's yes; Bonsai 2 is "Friday's standard", pre-selected only at first setup and never after; the user may pick anything, bigger, or several.

---

## 0. What is wrong today, in one paragraph

The Models tab can fetch only Ollama Gemma tags: its text rows come from a hard-coded table (`services/model_plan.py` `_BRAINS`), its one fetch path is `POST /api/ollama/pull` (`routes/skills.py`), and the pre-fetch card refuses any id outside that table. Ollama cannot load Bonsai 2's tensor types, and no code in the tree downloads a GGUF by URL. `model_store.register`, `verify` and `forget` exist with sha256 support and have no callers. The arbiter reads an `engine` field from the store record to pick the PrismML fork, and nothing writes it. So the standard brain cannot be acquired from the screen, which blocks the first-install priority.

## 1. The screen

Five regions, top to bottom, inside the existing `intelligence` tab (label "Models"). The Model Soup card and the "Model for each job" rows stay; the "Local models" section and the "Fetch" flow are replaced.

1. **This computer.** One plain line (card, usable VRAM, RAM for models, free disk, tier in words), "Show me the numbers" (every budget with its arithmetic), "Pretend I have…" (a what-if VRAM and RAM figure; everything recomputes under a SIMULATED badge; local arithmetic only), and the line "stays on this PC" with the profile path.
2. **Your stack.** One bar per memory pool (each GPU, then processor RAM): solid blocks for seats that stay loaded, dashed blocks for seats that take turns with the swap time, a labelled reserve block. The sentence under it is the planner's verdict ("these fit together, 0.7 GB spare" / "these take turns: image evicts the brain for about 25 s" / "Y needs 3.1 GB more, even taking turns"). Any combination of installed and catalogue models can be ticked and previewed before a byte downloads.
3. **Find a model.** Rows from the curated shortlist, the installed store, and "any model" (a Hugging Face id or a file path). Each model expands to one row per file (quantisation) with real bytes, the verdict in words (runs well / tight / won't fit / partly on the processor), the expected speed with its basis (measured here / published / about, calibrated), the context it can hold, the licence word and the telemetry word, and a "Why?" that opens the arithmetic. Filters: role, fit, licence, runtime, installed. Sort includes "biggest that runs well". Bonsai 2 carries "Friday's standard" and is never ticked by this screen.
4. **Get it.** The pre-download card: bytes, disk left afterwards against the 10 GiB floor, time from the last measured download rate (or "depends on your connection"), licence with its source, telemetry class, and what changes in the stack. One click starts a background download with byte-range resume, sha256 verification, atomic rename, registration (with the engine for Bonsai), the runtime asset when one is needed, then the on-device benchmark. Progress shows in the row and in the process orb; pause and cancel are one click.
5. **Keep it honest.** After install the bench replaces the estimate (labelled "measured") and calibrates the other rows' "about" speeds. Remove frees the file after the arbiter releases its seat; "go back to the previous version" restores the previous file and role binding from the previous-version slot.

Voice: "What's the biggest model I can run?", "Could I run X?", "What would I need for X?", "Pretend I had 24 GB", "What's using my graphics memory?", through one tool on the same solver; install and remove raise the usual card.

## 2. The fit arithmetic (the "Why?")

For a file `f` on a profile `p` at a served context `c` (the context Friday will actually use, from the policy's context ladder, never a flat 8K):

```
seat_mib(f, c) = file_bytes/2^20
               + kv_mib(c)                         # from the GGUF header: full-attention layers × kv_heads × (key+value dims) × c × bytes(kv type) / 2^20
               + compute_mib                       # 400 at -ub 512, 1200 at -ub 2048
               + mmproj_mib if vision is on
usable_vram   = vram_total − max(display reserve, measured idle)       # per card, never summed
ram_budget    = ram_total − os_reserve − friday_footprint
```

The verdict is the residency planner's dry run (`residency_policy.preview_assignment`, then `plan()` for refusals with rule ids), with the candidate placed beside the user's current stack: **runs well** when it is pinned inside budget with the room target, **tight** when it fits only at a smaller context or inside the hard ceiling, **partly on the processor** when the layer split puts fewer than all layers on the card (speed by the partial-offload formula in the tier table), **takes turns** when the planner makes it a lease, **won't fit** when the planner refuses (the rule id and the shortfall are shown). Speed: measured here if a measurement row exists for this fingerprint; else the publisher's figure if the shortlist has one; else `about` from the bandwidth formula, multiplied by the machine's calibration factor (measured ÷ estimated on the models that have both).

A ternary or any unknown packing is sized from bytes only; there is no bytes-per-parameter table. MoE weights all count; active parameters drive speed.

## 3. Build plan

Engineer-days are for one person with tests. Each milestone ends with a ledger line and a test file that fails on the previous code.

| # | Milestone | What lands | Verify | Days |
|---|---|---|---|---|
| M1 | **Bonsai 2 fetchable** | `resources/model_shortlist.json` (Bonsai 2 manifest + runtime assets + the advisor's first shortlist); `services/model_download.py` (queue, byte-range resume into `.part`, sha256, atomic rename, `model_store.register(... engine=...)`, runtime asset fetch and unpack, measured rate, progress to the process orb); `routes/models_screen.py` (`GET /api/models/shortlist`, `POST /api/models/download`, `GET /api/models/downloads`, `POST /api/models/downloads/<id>/{pause,resume,cancel}`); `model_store.register` grows `engine`; the tab's Fetch button becomes Get for GGUF rows | `tests/unit/test_model_download.py`: resume from a `.part` against a local range-capable server, sha mismatch rejects and keeps nothing, atomic rename, engine recorded, disk-floor refusal, nothing-identifying request (reuses the update_check scanner), the shortlist's Bonsai entry equals the tier manifest | 3 |
| M2 | **Fit engine** | `services/model_fit.py`: GGUF header read for the KV layout (local file, or the first 2 MiB of a remote file by range), `seat_mib`, `verdict`, `speed`, `why`, `what_if(profile override)`, `what_would_i_need`, calibration factor | `tests/unit/test_model_fit.py` on the P1 fixture and a pretend 24 GB card: Bonsai 2 PTQ1_0 at 49K = runs well, PQ2_0 at 131K = tight, a 20 GB file = won't fit with the shortfall, partial offload on an 8 GB Windows card, calibration applied | 2.5 |
| M3 | **Catalogue and Why** | `GET /api/models/catalog` (shortlist + installed + any-model rows with verdicts), `POST /api/models/check` (any model: HF id or path, one anonymous metadata read), the Find-a-model region and the Why panel in `index.html` | route tests with mocked fetches; rendered frames | 2.5 |
| M4 | **Stack bar and preview** | `POST /api/models/stack/preview` (ticked set → bars, swap times from measured cold loads, planner sentence); the Your-stack region and Pretend-I-have | tests on preview shapes; frames | 2 |
| M5 | **Measured beats estimated** | `services/model_bench.py`: after install, load through the arbiter's engine for the record, `llama-bench -p 512 -n 128`, peak VRAM delta, cold load; writes a measurement row (`residency_catalog.record_measurement`) and the calibration file; conformance gate for tool-using roles; receipts | tests with a fake bench runner: the row lands, the label flips, calibration recomputes | 1.5 |
| M6 | **Remove and roll back** | `DELETE /api/models/<id>` (release seat, `model_store.forget(delete_file=True)`, frees N GB), previous-version slot on replace, `POST /api/models/<id>/rollback`, role undo | tests on the slot and the undo | 1.5 |
| M7 | **Voice** | `local_models_advise` in `_VOICE_LIVE_TOOLS` and the agent tool, the five phrasings, parity tests | `tests/unit/test_voice_parity.py` additions | 1 |
| M8 | **Frames and hand-off** | scratch-server captures at desktop and phone widths, the brand check, the ledger line, hand to the lead's daytime lane | frames looked at | 0.5 |

Total about 14.5 days. M1 first because it unblocks the first install; M2 to M4 are the screen; M5 to M7 complete the owner's six asks.

## 3.1 What has landed (2026-10-02)

- [x] M1 `resources/model_shortlist.json`, `services/model_shortlist.py`, `services/model_download.py`, `routes/models_screen.py` (shortlist, download, downloads, pause/resume/cancel), `model_store.register(engine, serve_args, serve_num_ctx)`, shortlist rows in `local_models_catalog`, the Get button. `tests/unit/test_model_download.py`.
- [x] M2 `services/model_fit.py`. `tests/unit/test_model_fit.py`. Finding: by the planner's budgets (1 GiB beyond the display reserve) Bonsai 2 reads **tight** on a 12 GB card with the full context overhead; the engine tries the small compute buffer before saying so, and the screen says "tight", never "runs well", when that is the arithmetic.
- [x] M3 `services/model_catalog_rows.py`, `GET /api/models/catalog`, `POST /api/models/whatif`, `POST /api/models/check`, `ModelCatalogSection` (search, fit and licence filters, per-file rows, Why, Pretend I have, any model). `tests/unit/test_model_catalog_rows.py`.
- [x] M4 `services/model_stack.py`, `POST /api/models/stack/preview`, `StackSection` (bars, legend, swap time, sentence; Add to stack on rows). `tests/unit/test_model_stack.py`.
- [x] M5 `services/model_bench.py` (llama-bench beside the engine, through the arbiter's `bench_job` lease; measurement row; calibration; receipt), bench route, after-install hook, Measure button. `tests/unit/test_model_bench.py`.
- [x] M6 `services/model_remove.py` (remove with the replacement question, previous-version slot kept by a replacing download, rollback, role undo snapshot), routes, Remove and Go back buttons. `tests/unit/test_model_remove.py`.
- [x] M7 `services/local_models_tools.py` (`local_models_advise`, ring 0, in the governance internal list, declared in `_VOICE_LIVE_TOOLS`, routed through `_governed`). `tests/unit/test_local_models_tools.py`.
- [x] M8 frames captured from the worktree page on a scratch static server with fixture payloads, desktop and phone, looked at. Not done here: the conformance gate as an automatic post-install step for tool-using roles (it needs the seat loaded; the existing seat-gate route runs it on demand), and the Settings window's own narrow phone layout, which squeezes every tab.

## 4. Decisions made here and owned by the build

- **No new settings key.** Queue, previous-version slot and calibration live under `runtime/models/` and `runtime/residency/` as JSON; the settings checker is untouched. Role binding stays `POST /api/settings` with `capability_routing`.
- **`index.html` only.** `ui_parts/app.html` says nothing builds from it and it never carried this tab; the brand check and the settings checker are satisfied without touching it.
- **One downloader for every modality.** The GGUF path is first; ComfyUI files use the same code with a different destination folder.
- **Verification is the file on disk plus the engine loading it**, never the HTTP exit code: a download is "installed" only after sha256 matches and the bench has a number.
- **Ternary packings size from bytes.** Bonsai's `PTQ1_0` and `PQ2_0` are not in any bytes-per-parameter table; the manifest's byte counts are the truth.
- **Runtime asset per (OS, backend)** from the tier table's release list; stock llama.cpp refuses Bonsai 2, so the record's `engine` points at the fork.

## 5. Zero-telemetry contract

Outbound requests from this screen are exactly: Hugging Face `GET /api/models/<id>?blobs=true` (metadata), `GET .../resolve/main/<file>` (the weights, with a `Range` header), and the GitHub releases API for the fork's asset list. Each carries no token, no query parameter beyond the expand list, no cookie, and the HTTP library's default headers. `tests/unit/test_model_download.py::test_outbound_requests_carry_nothing_identifying` reconstructs every request the downloader makes and scans it for every field of the hardware profile, the install id and the version, the way `test_update_check` already does.
