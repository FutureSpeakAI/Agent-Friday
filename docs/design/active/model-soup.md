# Model Soup: how Friday chooses a model today, and the local lineup that gets the owner mostly off the cloud

> **Status:** active
> **Written:** 2026-09-17
> **Implementation:** 2026-09-17, first pass (uncommitted in the working tree). Landed: step 1 (UNC guard, `services/path_probe.py`, plus a fourth stall site the spec missed, `model_catalog._friday_store_entries`, which alone cost 25.4 s of a 27.2 s `/api/intelligence`; warm route now 0.14 s with the share wedged), the registry half of step 2 (`models.json` records the LoRA and local-disk preferences, `gemma4:e2b-friday-v1` retired, stale `residency/gguf_models.json` set aside with a timestamp; no weights copied), the code half of step 3 (`--lora`/`--mmproj` spawn, `/lora-adapters` check, honest absent seat instead of a daemon 404, `_ours_resident_mib`, `utf-8-sig`), `local_seats.serving()` from step 4 (the indexer, router candidates and manifest do not read it yet), step 6 (the card, three modes, seven dead pickers gone from the payload, two dead tabs deleted) and step 7 (Privacy and KG copy, `wiki_encrypted_sections` checklist with its missing `DEFAULT_SETTINGS` key). Not done: §7.1 yielding sidekick, §11.3 turn-level button, the `DEFAULT_SETTINGS`/`ASSIGNED_ROLES` removal of the seven seats, §8 speculation, steps 8 to 11 (the owner's). §12 remains the build order for the rest.
> **Authority:** this document has full authority over every settings surface in `index.html` that touches models, routing, residency, voice engines, image models, knowledge-graph indexing or egress (§11). `ui_parts/app.html` is a hand-maintained mirror nothing builds from; it is not edited.
> **Method:** STORM. Four perspectives were argued against the tree and the live machine (a systems engineer counting MiB, a privacy engineer counting bytes that leave, a designer asking whether each control tells the truth, and a user who wants voice to answer). The synthesis below cites file paths and line numbers as they stand at `9eab2e2`.
> **Provenance tags.** **MEASURED-2026-09-17** was measured on the target machine (RTX 4070, 12 GB). **BRIEF** is a number handed in with the brief and not re-derived. **TREE** was read from the code. **RECORD** is from `Friday-Models/docs/DECISIONS.md`, `~/.friday/friday.log` or `~/.friday/runtime/*.json`. **LEDGER** is from `~/.friday/costs.db`. **ESTIMATE** is arithmetic on measured inputs and is labelled as such. **UNCHECKED** is a claim not verified when this was written.

---

## 0. What is true right now, in one page

1. **Every turn the owner has taken since 2026-09-06 has gone to the cloud, and nothing in the UI said so.** `model_routing.mode` is `cloud_only`, `cloud_consent.choice` is `cloud_unrestricted`, `vault_local_only` is `false`, and `knowledge_graph.indexing_mode` is `cloud` (**RECORD**, `~/.friday/settings.json`). Under that combination the egress gate returns every payload untouched (`services/egress_gate.py:1544-1554`), vault-tier prompt content is not stripped, and the knowledge graph's nightly pass sends wiki chunks to the cloud extractor. The consent that switched all of this off was recorded against a capability snapshot that said local was `capable: false` for one reason only, that nothing had been *measured* (`"why": "no RAM or throughput figure recorded for gemma4:e2b on this machine"`, **RECORD**). The consent answered a question the machine had asked wrongly.

2. **There is no local seat Friday can start.** The seat the owner's settings name, `gemma4:e2b-fridayweaver-1.0`, is a Q8_0 base plus a LoRA plus an mmproj that must be passed to `llama-server` as `--lora` and `--mmproj`; `residency_arbiter.py` has no LoRA wiring anywhere (`grep -rn lora src/` returns only image-model comments, **TREE**), so the Arbiter can only ever spawn the base model under the FridayWeaver name, which is the silent-wrong-model failure `DECISIONS.md` refused on 2026-09-09. The seat has only ever run by hand. Its three files live on `\\wsl.localhost\Ubuntu-24.04\root\friday-models-storage\gguf-intermediate\`, and today that share does not answer (`wsl --list --verbose` itself hung past 25 s, **MEASURED-2026-09-17**). Ollama has zero models (**MEASURED-2026-09-17**, `ollama list`). `~/.friday/runtime/residency/gguf_models.json` names seven GGUFs under `runtime/models/gguf/`, a directory that no longer exists (**MEASURED-2026-09-17**), so the Arbiter's `gguf_paths` is empty and every pinned load would fall to the Ollama daemon and 404 (`residency_arbiter.py:1935-1964`).

3. **The wedged WSL share is stalling Friday itself.** `model_store.available()` calls `Path(path).exists()` on every registered model (`services/model_store.py:203-204`), and one of those paths is the UNC share. That function sits under `residency_catalog.installed_entries()` (`:966`), which sits under `Arbiter.plan_fresh()` (`residency_arbiter.py:1221`), which `/api/residency/status` and `/api/intelligence` call. `GET /api/residency/status` on the live server timed out at 25 s and `/api/health` took 10.8 s (**MEASURED-2026-09-17**, server pid 25556 on `127.0.0.1:3000`). The same `exists()` is in `local_seats._friday_store()` (`services/local_seats.py:94`), which is on the chat path (`routes/chat.py:676`). A dead 9p share is a slow Friday, in every tab.

4. **The one model Friday would fall back to is the wrong one.** With the reasoning seat bound to `claude-fable-5-1` and nothing serving locally, `local_seats.resolve("brain")` picks the smallest store entry, `gemma4:e2b-friday-v1` (`local_seats.py:368-371`). That is the 2026-09-02 merged checkpoint, which `DECISIONS.md` flags as possibly numerically identical to the base model because of the `merge.py` adapter-load bug found on 2026-09-09, and it has no mmproj (`models.json`, `"mmproj": null`). `friday.log` shows exactly this substitution at 2026-09-17 07:40:47 (**RECORD**). The voice manifest's mind proof then refuses because nothing serves that name either (`services/voice_manifest.py:177-182`), so cloud voice's `ask_friday` relay has nothing to relay to (`routes/voice.py:912-936`).

5. **The build is fine for audio.** llama.cpp issue #21868 (HTTP 500 on `input_audio`) does **not** affect the installed build. `llama-server.exe` b10415-1d2869c6e served a Gemma 4 E4B with its mmproj on a throwaway port, accepted an `input_audio` block on `/v1/chat/completions`, returned HTTP 200 and transcribed `resources/voice_proof.wav` exactly as "Friday, what time is it right now?" in 0.27 s (**MEASURED-2026-09-17**, §14.1). Native audio input, the reason E2B was chosen, works on this binary today.

6. **Where the money goes.** In the last 30 days the ledger holds roughly 7,200 model calls: about 6,300 cloud (Anthropic 5,613 calls / $1,159; OpenRouter 568 / $88; Gemini voice 119 / $2) and about 916 local, all at $0 (**LEDGER**, §7.4). By kind: 3,354 single-shot `text` calls ($85), 1,846 cloud `chat` calls ($659), 981 cloud `scheduled` calls ($504). The scheduled and single-shot work is the part that moves cleanly.

**Headline recommendation.** Move the FridayWeaver files onto C:, teach the Arbiter `--lora` and `--mmproj` so it can own that seat, pin it resident at 131,072 context (measured 4,291 MiB), and add Gemma 4 12B QAT Q4_K_M as an on-demand leased seat that displaces E2B for the turns that need it. Flip routing to `local_preferred` with the reasoning seat bound to FridayWeaver, set the knowledge graph back to local, and re-ask the cloud consent question once the local seat is proven rather than assumed absent. Then rebuild the Intelligence tab around one question, "what is about to answer me, where, and at what cost", and delete the controls that lie. Estimated effect (§7.4): 70 to 80 percent of calls and 55 to 70 percent of spend off the cloud. Image generation stays cloud except SDXL, video stays cloud, and frontier-class reasoning stays cloud by choice.

---

## 1. What cannot go local on this hardware

Stated early because a plan that hides this is worse than one that draws the line.

| Capability | Verdict | Why (with numbers) |
|---|---|---|
| Image generation with the models the owner has been using | **No, except SDXL, and only by evicting the language seat** | Ceiling after the 2,560 MiB display reserve is 9,722 MiB. Measured render peaks: `z-image-turbo-fp8` 10,453, `sd3.5-medium-fp8` 10,621, `flux1-dev-fp8` 11,878, `sdxl-base-1.0` 8,192 (`services/local_image.py:272-281`, **BRIEF**). SDXL fits only with the whole card. The retained E2B seat (4,291 at 131k, 3,607 at 32k) plus SDXL is 11,799 to 12,483 MiB, over the ceiling either way. So R10 ("the sidekick survives every lease", `residency_policy.py:364`) cannot hold for an SDXL lease on this card. The configured image seat, `z-image-turbo-fp8`, cannot render here at all; every request to it today ends in the structural refusal at `local_image.py:796-814`. |
| Video generation | **No** | `services/local_video.py` exists and is wired to ComfyUI, and Wan 2.2 weights (5B Q8 5.03 GB, 14B two-expert Q3 13.4 GB) are on disk under `runtime/ComfyUI/models/diffusion_models` (**MEASURED-2026-09-17**). Nothing has ever been measured on this card, the 5B's VAE decode hangs (`local_video.py:44-48`), and `residency_policy.plan()` refuses the video seat unconditionally with a comment that says no backend exists (`:959-969`), which is stale. The setting already points at `gemini-omni-flash`. Leave it there. |
| E2B and 12B resident at the same time | **No** | 12B Q4_K_M at 131,072 is 7,814 MiB under Ollama's `-b 512` (`measurements.json`, **RECORD**) and 7,813 under the Arbiter's caps (**BRIEF**). Add the E2B seat (4,291) and the total is 12,105 MiB against 9,722 usable. Even the smallest honest E2B (Q4, 32k, no mmproj, 1,811) beside the 12B at 32k (7,718) is 9,529, inside 9,722 by 193 MiB and outside the planner's 8,698 MiB budget (§3.2). The 12B is a lease that evicts E2B. |
| Speculative decoding with a draft model on the 12B | **No** | 12B Q4 weights (6.6 to 6.9 GB) plus an E2B Q4 draft (about 1.5 GB) is 8.1 GB before either KV cache or compute buffer, leaving about 1.6 GB of 9.7 for both (**BRIEF** arithmetic). Ngram speculation costs no VRAM and is the only kind that fits (§8). |
| Frontier-general reasoning | **No, by definition** | `frontier-on-12gb-deepseek-derived.md` §"The bound" settles this. Frontier-on-this-distribution is what FridayWeaver is for. Long, hard, judgement-dense work stays a deliberate cloud choice, made per turn and shown per turn (§11). |
| 12B fine-tuning | **Not now** | `train-2` has transformers 5.5.4; `gemma4_unified` needs ≥ 5.10.1 (**BRIEF**). The 12B ships as stock QAT weights until that changes. |
| Local video generation, local music (Lyria) | **No** | Music has no local backend in the tree. |

Everything else in this document is about the rest.

---

## 2. Hardware, as measured

| | Value | Tag |
|---|---|---|
| GPU | RTX 4070, 12,282 MiB. 9,722 MiB usable after the 2,560 MiB Windows display reserve (`hardware_profile.MIN_DISPLAY_RESERVE_MIB["windows"]`, `services/hardware_profile.py:249`) | BRIEF, TREE |
| Idle card | 1,541 MiB used before the probe, 1,682 after it (**MEASURED-2026-09-17**); the profile's cached idle floor is 1,161 (`hardware-profile.json`) | MEASURED, RECORD |
| RAM | 32,620 MiB; `os_reserve_mib` 6,144 | RECORD |
| Disk C: | **19.8 GB free** (**MEASURED-2026-09-17**, `Get-PSDrive`). The brief said ~24; the profile's last sample said 19,852 MiB. The residency floor is 10 GiB (`residency_policy.DISK_FLOOR_MIB`). Usable headroom for new weights is therefore about 9 GB, before anything is deleted | MEASURED, TREE |
| llama-server | `~/.friday/runtime/llama.cpp/llama-server.exe`, build **b10415-1d2869c6e**, files dated 2026-08-13 (`system_fingerprint` in the probe response) | MEASURED |
| Ollama | daemon running (pid 28440), **zero models** | MEASURED |
| Friday server | pid 25556 on `127.0.0.1:3000`, up since 2026-09-16 13:56 | MEASURED |
| WSL | `vmmemWSL` resident (430 MB) but `wsl.exe --list --verbose` and any `\\wsl.localhost` access hang past 25 s | MEASURED |

### 2.1 Seat footprints

| Seat | Config | MiB | Tag |
|---|---|---|---|
| FridayWeaver E2B (Q8_0 base + LoRA + 557 MB mmproj) | `-c 131072 -b 512 -ub 512`, KV q8_0 | **4,291** seat / 6,082 total on card | BRIEF (2026-09-12) |
| Same seat | `-c 32768` | 3,607 | BRIEF |
| Same seat | `-c 65536` | 3,831 | BRIEF |
| Gemma 4 E4B Q4_K_M + 920 MB mmproj (HauhauCS file on disk) | `-c 4096 -b 512 -ub 512`, f16 KV, 4 slots | **≈4,400 to 4,530** (6,074 used minus 1,541 idle) | MEASURED-2026-09-17 |
| Gemma 4 12B Q4_K_M | 131,072 under Ollama (`-b 512`) | 7,814; cold load 21.2 s | RECORD (`measurements.json`) |
| Gemma 4 12B Q4_K_M | 4,096 under Ollama | 7,689; 53.2 tok/s; cold 26.0 s | RECORD |
| gemma4:e2b Q4_K_M (text only, Ollama) | 32,768 | 1,811; cold 8.7 s | RECORD |
| gemma4:e2b Q4_K_M | 4,096 | 1,629; 169 tok/s | RECORD |
| 12B at 131,072 *without* `-b 512 -ub 512` | | 11,351 (the compute buffer, not the model) | BRIEF |

### 2.2 What is on disk that matters

| File | Size | Where | Note |
|---|---|---|---|
| `base-e2b-q8_0.gguf` | 4.95 GB | WSL share | the FridayWeaver base; unreachable today |
| `fridayweaver-lora.gguf` | 12.5 MB | WSL share | the adapter; 140 tensors (`gate_proj`/`up_proj`, 35 layers) |
| `mmproj.gguf` | 557 MB | WSL share | vision + audio tower, valid for any E2B checkpoint |
| `gemma-4-E2B-it-friday-v1.Q4_K_M.gguf` | 3.19 GB | `Friday-Models/weights/gguf/` | older merged checkpoint, possibly a no-op merge, no mmproj. **Do not use as the base under the LoRA** (`DECISIONS.md` 2026-09-09) |
| `Gemma4-12B-QAT-Uncensored-HauhauCS-Balanced-Q4_K_M.gguf` + `.mmproj.gguf` | 6.87 GB + 0.16 GB | `~/.ollama/models/.studio_links/` | a real, fully allocated GGUF (magic verified, **MEASURED-2026-09-17**). A community abliterated QAT quant, not stock Google weights |
| `Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf` + mmproj | 4.97 + 0.92 GB | same | used for the audio probe (§14.1) |
| `qwen3.5-9b.gguf`, `qwen3-vl-8b.gguf`, `qwen3-8b.gguf`, `SmolLM3-3B` | 6.1, 5.7, 4.9, 1.8 GB | same | product decision excludes Qwen from the ladder (`model_plan.py:139-149`) |
| ComfyUI image weights that can never render here | `flux_dev_fp8` 11.08 GB, `sd3.5_medium` 10.84 GB, `z_image_turbo` 5.73 GB, plus `t5xxl_fp8` 4.56 GB and `qwen_2.5_vl_7b_fp8` 8.74 GB text encoders | `runtime/ComfyUI/models/` | about 27.6 GB of diffusion weights whose measured peaks exceed 9,722 MiB (§1). This is where disk comes from (§9.3) |

---

## 3. The budget arithmetic every decision below obeys

### 3.1 Three ceilings, and which one applies where

| Ceiling | MiB | Who uses it |
|---|---|---|
| Card total | 12,282 | nobody, directly |
| Usable after display reserve | **9,722** | `Arbiter.grant()` R-DISPLAY-RESERVE (`residency_arbiter.py:1463-1480`), `local_image.render_ceiling_mib()` (`local_image.py:284-307`), voice worker admission |
| Planner budget (`available_mib`) | **8,698** = 12,282 − 1,024 (`VRAM_RESERVE_MIB`, R3 slack, `residency_policy.py:144`) − 2,560 (display reserve via `effective_baseline_mib`, `hardware_profile.py:332-360`) | `residency_policy.plan()` seat placement, `_pick_local_model` VRAM fit (`routing/model_router.py:1056-1086`) |

The planner is 1,024 MiB more conservative than the grant path. That is by design (R3 slack against the 354 MiB thrash margin, `residency_policy.py:136-143`), and it is why a lineup can pass `nvidia-smi` and still be refused. Every number in §6 is checked against **8,698** for resident seats and **9,722** for leases.

### 3.2 The lineup, costed

| State of the card | Resident | MiB | Fits 8,698? | Fits 9,722? |
|---|---|---|---|---|
| Normal | FridayWeaver E2B @131k | 4,291 | yes (4,407 spare) | yes (5,431 spare) |
| Normal + voice armed | E2B + whisper-small CUDA (~600 to 900) + Kokoro CUDA (~400 to 600) | ≈5,300 to 5,800 | yes | yes |
| 12B lease | 12B Q4_K_M @65,536 (7,750 measured under Ollama at 65k; 7,814 at 131k) | 7,750 to 7,814 | yes, alone (884 to 948 spare) | yes |
| 12B lease + E2B retained | | 12,041 to 12,105 | **no** | **no** |
| SDXL lease | sdxl-base 8,192 peak | 8,192 | n/a (lease) | yes, alone |
| SDXL + E2B retained | | 12,483 | | **no** |

Consequence: on this card, **a lease of either the 12B or SDXL evicts the E2B seat**, and the E2B is reloaded after (cold load 8.7 to 20.5 s depending on quant and context, **RECORD**). R10 becomes "the sidekick survives every lease *that leaves room for it*", which is what §7 changes.

### 3.3 Context sizing

The seat's window is 131,072 by measurement (`residency_arbiter.py:686-710`). At that window the tool budget trims nothing (75 tools, 12,438 tokens measured through `/tokenize`, `tool_budget.py:40-47`), and `knowledge_query` stays in the request. At 32,768 it trimmed 75 to 40 and dropped `knowledge_query` (`tool_budget.py:331-337`). The window is the difference between a Friday that can look things up and one that narrates. Nothing below lowers it.

---

## 4. The routing map

Every path by which a turn reaches a model, with what decides local versus cloud, which setting governs it, whether sensitivity gating actually applies, and what happens when the local option is absent. **Today** means under the owner's settings as of 2026-09-17 (`cloud_only`, `cloud_unrestricted`, `vault_local_only=false`, KG `cloud`, no local seat serving).

| # | Path | Entry point | What decides local vs cloud | Governing settings | Does sensitivity gating apply? | When the local option is absent | Today |
|---|---|---|---|---|---|---|---|
| 1 | **Text chat** (interactive, tools) | `routes/chat.py:709` → `agent._generate_agent` (`services/agent.py:84`) → `routing/model_router.ModelRouter.route()` (`:554`) | (a) `needs_vault_access` runs first (`:574`), and force-routes local **only if** `vault_local_only` is true (`_route_vault`, `:517`); (b) `mode`: `cloud_only` returns cloud at `:764-808`; `local_only` returns local or refuses at `:810-892`; (c) `smart`/`local_preferred`: the bound reasoning seat wins for non-tool classes (`:899-906`), and **every tool-carrying turn goes local if any local candidate exists** (`:924-967`) regardless of the seat binding; then the ladder in `agent.py:305-320` retries cloud after a local failure unless `vault_access` | `model_routing.mode`, `capability_routing.reasoning` (via `_chosen_seat`, `:289-345`; the factory value is treated as no binding, `:341`), per-conversation seat, `task_overrides`, `vault_local_only`, `fallback_to_cloud` | Only through the egress gate (`_seal_or_block`, `services/model_router.py:89-154`), which is bypassed whole under `cloud_unrestricted` (`egress_gate.py:1544`). Vault-tier prompt stripping is off when `vault_local_only=false` (`privacy/vault_policy.py`) | `cloud_only`: n/a. `local_preferred`/`smart`: a failed local leg falls to cloud with only `attribution.note_fallback` as the trace (`agent.py:367-371`). `local_only`: refuses and offers a cloud switch (`routing/model_router.py:881-892`) | **cloud**, `claude-fable-5-1`, ungated |
| 2 | **Single-shot text** (briefings, digests, editorial, KG summaries, calendar/message drafting, wiki bootstrap) | `services/model_router._generate_text` (`:373`) with `has_tools: False` | Same router; SIMPLE/CODE/RESEARCH classes go to the bound seat; in `smart` with no binding they go to `_pick_local_model` if Ollama lists models (`:991-1037`, **Ollama-only**, so today always cloud) | as above | as above | The ladder `local → cloud → openai` (`:501-509`) unless `vault_access` | **cloud**; 3,354 calls / $85 in 30 days (**LEDGER**) |
| 3 | **Scheduled and background agent turns** (heartbeat, routines, subagents via `spawn_task`) | `agent.py:2861-2874` uses `subagent_model` (settings flat key) → `_generate_agent` | `subagent_model` is `anthropic/claude-sonnet-5` on OpenRouter; the router's TOOL_USE branch would prefer local in `smart`/`local_preferred` | `subagent_model`, `capability_routing.subagent`, `mode` | as row 1 | as row 1 | **cloud**; 981 calls / $504 in 30 days |
| 4 | **Local voice** (`/ws/voice`) | `routes/voice.py:1608` `ws_voice_local` → `VoiceSession` → `_generate_agent(..., is_voice=True)` (`:1791-1799`) | The Voice Manifest's mind proof: `local_seats.resolve("brain")` then `tool_budget._seat_base(seat)` must answer (`services/voice_manifest.py:163-211`). `TaskType.VOICE` in the router returns cloud (`routing/model_router.py:916-922`) but the local voice path never asks the router for the provider; it dispatches to the seat | `voice_engine` (`local` \| `gemini` \| provider name), `capability_routing.reasoning` (through `resolve("brain")`), `voice_ear_gpu`, `voice_mouth_gpu`, `voice_tools` | Local; no egress | Mind refused (`local_voice_brain_absent`); the session refuses rather than falls to cloud (settled 2026-09-09, clean-sheet §1.3) | **refused**: `resolve("brain")` returns `gemma4:e2b-friday-v1`, nothing serves it |
| 5 | **Cloud voice** (Gemini Live, `/ws/live`) | `routes/voice.py` → `services/voice_engine.py` | `voice_engine=gemini`; mic audio goes to Google. `ask_friday` relays context questions to the local agent **only when** the manifest's mind is proven (`routes/voice.py:866-936`) | `voice_engine`, `voice_model` (`gemini-2.5-flash-native-audio-latest`), `voice_tools` | The `ask_friday` answer is sealed by `_gate_voice_tool_result` (`:584`); the seal is a no-op under `cloud_unrestricted` | Relay exists but has nothing to relay to; HUD says "your notes, memory and knowledge graph are out of reach" (`:928-936`) | **cloud, no reach into the owner's context** |
| 6 | **Knowledge graph Tier B** (entity extraction, community reports) | `knowledge_graph/indexer.py::_llm` (`:355-379`) via `run_nightly_reindex` (`integration.py:163`) or the panel | `knowledge_graph.indexing_mode`: `local` calls `_call_ollama` directly with `_available_local_model()`; `cloud` calls `_generate_text` with `model=None`. **`_resolve_model` never pins sensitive chunks local under `cloud`** (`:313-352`) | `knowledge_graph.indexing_mode`, `nightly_reindex` (default true), `index_sources` | Only the egress gate; bypassed today | `_available_local_model()` intersects **Ollama's** `list_models()` with `TOOL_CAPABLE_IDS` = {`gemma4:e2b`, `e4b`, `12b`, `26b`} (`indexer.py:275-310`, `model_plan.py:297`). It never looks at Friday's own store or a llama-server seat, and the FridayWeaver id is not in the set. **Local Tier B cannot run on this machine under any setting today** | **cloud**, nightly, 226 new wiki pages included |
| 7 | **Knowledge graph ambient context** (every system prompt) | `integration.knowledge_context_block` (`:27-78`) | Structural, no LLM. Injects page excerpts into *every* provider's prompt with no per-provider tier gating (its own docstring, `:30-36`). The only filter is `wiki_encrypted_sections`, which is **absent** from settings | `wiki_encrypted_sections` | No | n/a | plaintext excerpts of any wiki page can ride any cloud prompt |
| 8 | **Embeddings** (conversation memory, KG entity vectors) | `conversation_memory.EMBED_MODEL` = `all-MiniLM-L6-v2`, in-process CPU (`role_consumers.py:151-160`) | Constant. No setting reads | none (the `capability_routing.embedding` picker is inert) | Local always | n/a | **local** |
| 9 | **Image** | `creative_engine.generate_image` (`:655`) | If the configured model is a `local_image.MODELS` entry and installed, `local_image.generate` runs under an `image_job` lease (`local_image.py:982`); otherwise Higgsfield, kie, then Gemini. A structural refusal returns `{status: refused, options:[smaller_model, cloud]}` (`:796-820`) and does **not** auto-escalate | `capability_routing.creative_image` (`z-image-turbo-fp8`), `creative_model` mirror | Local; no egress | The local path is only tried when `is_installed()`; a too-big model is refused every time with the same explanation | **refused every time**: z-image needs 10,453 of 9,722 |
| 10 | **Video** | `creative_engine` → `local_video.generate` if a local id is configured, else Gemini | `capability_routing.creative_video` = `gemini-omni-flash` | | Local video never measured; policy refuses the seat (`residency_policy.py:965`) | **cloud** |
| 11 | **Music** | `music_engine.resolve_music_model` | `capability_routing.creative_music` = `lyria-pro` | | no local backend | **cloud** |
| 12 | **News engine** (editorial, front page, factcheck) | `news_engine.py:1503, 1974, 2119, 2693` → `_generate_text` | row 2 | row 2 | row 2 | **cloud** |
| 13 | **Memory dreaming** | `services/memory_dreaming.py` | "No cloud call, no LLM. Ring-0" (`:13`): six regexes | none | n/a | n/a | **local (no model)** |
| 14 | **Memory proposals** (`memory_manager` seat) | `services/memory_proposals.py:116-185` | Refuses unless the seat's provider is local; then `_call_ollama` with no fallback | `capability_routing.memory_manager` (today `openrouter`/sonnet-5, so it refuses) | Local only | `SeatUnavailable`; nothing stored | **refuses** (manual-only path anyway) |
| 15 | **Learning loop** | `services/learning_loop.py` | No model call in the module (sqlite + regex) | none | n/a | n/a | **local (no model)** |
| 16 | **Judgment gate** (egress rescue of borderline spans) | `services/judgment_gate.py:374-391` → `local_seats.resolve("judge", DEFAULT_JUDGE_MODEL="gemma4:e2b")` | Local only, by construction | `judgment_gate.enabled` (**false** today), `judgment_gate.model` | It *is* gating | Deterministic verdict stands | **off** |
| 17 | **Vision on a local seat** | `--mmproj` passed at spawn when `gguf_extract.projector_path` exists (`residency_arbiter.py:842-847`) | | | | | n/a, no seat |
| 18 | **Native audio input** | `services/native_audio.py`, opt-in `understand` mode | seat must carry the audio tower | | | | works on this build (§14.1); no seat |

### 4.1 Where the UI and the code disagree

Each row is a control or a sentence on screen whose behaviour differs from the code it claims to describe. §11 says what happens to each.

| # | Surface | Says | Code does | Verdict |
|---|---|---|---|---|
| U1 | Knowledge Graph tab (`index.html:37533`) | "Local: nothing ever leaves this machine." | Local Tier B cannot run at all on this machine (row 6); choosing "Local" produces `TIER B CANNOT RUN` in the log (`indexer.py:459-466`) and the panel keeps saying "Active now: LOCAL" | true sentence, impossible mode; the panel must say *cannot run* |
| U2 | Privacy tab, "Unrestricted Cloud" toggle (`index.html:35348-35360`) | flipping it changes the posture | it writes `model_routing.unrestricted_cloud`, which `cloud_consent.resolve()` reads **only when no consent is recorded** (`privacy/cloud_consent.py:140-155`). Consent is recorded, so the toggle is inert. Off would still be unrestricted | **lying toggle; delete, replace with the consent record and a re-ask** |
| U3 | Privacy tab, "Local-Only Mode" (`:35327-35340`) | "Force all AI processing to local models. Cloud providers receive no vault data." | it is `vault_local_only`: vault-tier prompt stripping plus force-routing of vault-touching turns only (`vault_policy.py`). Ordinary turns are unaffected. It also shares its name with `model_routing.mode = local_only` on the Intelligence tab, a different setting | rename to "Keep vault content off the cloud" and say what it covers |
| U4 | Intelligence tab, mode help "Smart: local for routine work, cloud when it will clearly be better" (`routes/intelligence.py:1119-1122`) | a quality-based split | `smart` sends **every tool-carrying turn** (all chat) local when any local candidate exists and the bound seat is cloud (`routing/model_router.py:899-967`); non-tool text goes to the bound seat. The split is by tool presence, then keyword class | rewrite or remove `smart` (§11.3 removes it) |
| U5 | `seat_transparency._MODE_MEANING["smart"]` (`:49`) | "vault-touching turns go local" | only when `vault_local_only` is true, which it is not | stale copy in a chat system line |
| U6 | Intelligence tab, 16 role rows (`routes/intelligence.py:37-92`) | each picker chooses a model for a job | `orchestrator`, `sidekick_fast`, `function_manager`, `researcher`, `asr`, `tts` are read by nothing; `embedding` is read only for a badge (`services/role_consumers.py:118-230`). Seven of sixteen pickers do nothing | delete the seven |
| U7 | Intelligence tab, Images row shows `z-image-turbo-fp8` as the seat | a working choice | the model cannot render on this card, ever (§1). The picker's `machine`/`fits` data is per language seat, not per image model | picker must grey image models with `fits_this_card` verdicts |
| U8 | `SettingsTabModels` (`index.html:34305`, ~650 lines) and `SettingsTabOrchestrator` (`:35518`, ~1,380 lines) | | defined, never rendered by `SettingsWS` (`:38169-38340`) | dead code; delete |
| U9 | Top-bar `QuickSwitch` pill (`:33332-33394`) | "Answering you now: X · local/cloud" | reads `/api/intelligence`, which blocks behind the WSL `exists()` today; the pill degrades to "bound" with no indication that the server is stalled | keep the pill; fix the stall (§9) and add a stale state |
| U10 | `setup.bundled_model = "gemma3:4b"` in settings | | the H3 defect value, a model that cannot call tools, still on disk in `settings.json` | migrate to `FLOOR_MODEL` on next save |
| U11 | Voice tab (`:34956`) still shows "TTS Voice (Gemini)" when `voice_engine === 'auto'` | `auto` is a mode | `auto` was removed from the picker on 2026-09-16 and reads as `local` | dead branch; remove |
| U12 | Intelligence tab "THE MACHINE" bar reads `vram_headroom` live; "Loaded now" reads `Arbiter.status()` | | `_ours_resident_mib()` reads `proc.get("vram_mib")` on `llama.procs` values that are `(Popen, port)` tuples (`residency_arbiter.py:2088-2092`), so llama-server seats are never counted; the display-reserve sampler then double-counts them as compositor draw | fix the accessor (§7.2) |
| U13 | Chat refusal card for `local_only` ("Install or start Ollama") | Ollama is the local runtime | Friday's seats are llama-server processes; Ollama has nothing to do with them | copy fix |
| U14 | `models.json` label "FridayWeaver-1.0 (MANUAL seat -- won't survive a Friday restart yet)" is what the picker renders | | true, and the reason this document exists | resolved by §7 |

---

## 5. The expert conversation, condensed

**Systems engineer.** "Two seats cannot share this card. Pick one to be resident and make the other a lease that admits it will evict. Move the weights to a local NTFS path or every liveness check in the tree turns into a 25-second hang. And teach the Arbiter about `--lora`, because until then the seat you are planning around does not exist as far as the Arbiter is concerned."

**Privacy engineer.** "The consent flag is doing all the work and it was answered on a false premise. Under `cloud_unrestricted` the gate is bypassed whole, not redacted, and the knowledge graph sends family, financial, legal and health pages to the cloud extractor nightly. The `embedding` seat pointing at OpenRouter is meaningless but the `indexing_mode: cloud` one is not. Fix the KG local path first, because it is the one that runs overnight."

**Designer.** "The Intelligence tab has sixteen rows and seven of them do nothing. The Privacy tab has a toggle that cannot change anything. Two different tabs each have a control called Local Only. A person cannot tell from any surface which model is about to answer or whether it is on this machine. One card at the top that answers that, and a ledger that shows what the last hour cost, does more than every picker combined."

**User.** "I want to press the mic and have Friday answer from what she knows about me. Right now the manifest says the mind is refused, and the cloud voice says my notes are out of reach. I do not care about tokens per second. I care that the seat is up when I sit down and that nothing quietly sends my father's estate paperwork to a data centre."

The synthesis that follows is the plan all four could sign.

---

## 6. The target local configuration

### 6.1 The lineup

| Seat | Model | Quant | Files (final location) | Residency | Window | Flags beyond the Arbiter's standard set | VRAM (MiB) | Cold load |
|---|---|---|---|---|---|---|---|---|
| **A. Resident brain** | FridayWeaver-1.0 = Gemma 4 E2B base + LoRA + mmproj | base **Q8_0** (keep; 4.95 GB) | `~/.friday/runtime/models/gguf/base-e2b-q8_0.gguf`, `fridayweaver-lora.gguf`, `mmproj-e2b.gguf` | **resident**, pinned, Arbiter-owned | **131,072** | `--lora <path> --mmproj <path> --spec-type ngram-cache` (§8) | **4,291** measured; plan against 4,400 | ≈9 to 20 s (measure after the move, §13) |
| **B. On-demand depth** | Gemma 4 12B instruct, **QAT** | **Q4_K_M** (6.6 to 6.9 GB; QAT keeps 4-bit close to BF16, **BRIEF**) | `~/.friday/runtime/models/gguf/gemma4-12b-qat-q4_k_m.gguf` (+ its mmproj) | **leased** (`heavy_turn`), displaces A | **65,536** (7,750 measured; 131,072 costs 64 MiB more and is allowed if measured under the caps) | `--spec-type ngram-cache`; `--mmproj` for vision | **7,750 to 7,814**; plan against 7,900 | 21 s measured (Ollama); re-measure |
| C. Voice ear | faster-whisper small, CTranslate2 | int8_float16 CUDA when leased, CPU int8 beam 1 otherwise | present | leased voice worker | | | ≈600 to 900 (VENDOR class figure; the worker records the real number) | 0.9 to 1.6 s |
| D. Voice mouth | Kokoro-82M torch CUDA when leased, Piper CPU floor | | present | leased voice worker | | | ≈400 to 600 | Kokoro CUDA 39 to 56 s (shown as progress) |
| E. Image | `sdxl-base-1.0` | fp16 | present (`sd_xl_base_1.0.safetensors`, 6.46 GB) | leased `image_job`, **displaces A** | | | 8,192 peak | 20 to 25 s ComfyUI start |
| Embeddings | all-MiniLM-L6-v2 | | present | in-process CPU | | | 0 | |

Everything not in this table is cloud, by name, in the UI (§11).

Which file for seat B: the recommended artifact is the stock Google QAT release quantized to Q4_K_M (`google/gemma-4-12b-it-qat-q4_0-gguf` or the unsloth `Q4_K_M` of the QAT checkpoint; the executor picks whichever carries the audio and vision towers in its mmproj and records the choice). The `HauhauCS ... Uncensored ... Balanced` file already on disk is a real QAT Q4_K_M with a mmproj and would serve immediately, but it is an abliterated community finetune with different refusal behaviour and no provenance chain to Google. **It is offered as the interim only if the owner says so**; the spec does not choose it for them. Until either lands, seat B is absent and the UI says so.

### 6.2 Why Q8_0 for the resident seat and not Q4

A Q4_K_M E2B would save about 1.7 GB of VRAM and 1.7 GB of disk. Nothing needs that VRAM: with A at 4,291 the card has 5,431 MiB usable free, enough for both voice workers (≈1.5 GB) with 3.9 GB to spare, and seat B evicts A regardless of A's size (§3.2). The LoRA was trained against a BF16 base, and the tool-calling numbers on record (98.1 percent lenient, 70.3 percent strict) were scored on the training checkpoint, never on any GGUF (`frontier-on-12gb-deepseek-derived.md` §"The dependency"). Q8_0 is the closest servable artifact to what was scored. Requantizing to Q4 is a measured decision for later, not a default.

### 6.3 Eviction thresholds, in MiB

| Event | Rule | Numbers |
|---|---|---|
| Admit a lease (B or E) | `grant()`'s live check: free ≥ display reserve after the load, else refuse with R-DISPLAY-RESERVE | free must be ≥ 2,560 after the seat lands. For B: 12,282 − 7,900 = 4,382 ≥ 2,560 only with A evicted. For E: 12,282 − 8,192 = 4,090, A evicted |
| Evict A for a lease | when `lease_budget < lease` | `_lease_budget` (`residency_policy.py:377-381`) is the planner's 8,698 minus the retained seat; with A retained it is 4,407, below B's 7,750 and SDXL's 8,192. A is evicted (§7.1) |
| Restore A after a lease | on `release()`, always, before the lease returns | `_restore_pinned` (`residency_arbiter.py:2024-2037`) already does this for displaced roles |
| Voice workers | admitted only while free ≥ 2,560 + worker's declared MiB; evicted first when B or E needs the card (clean-sheet §5.4) | ear ≈900 max, mouth ≈600 max |
| Display breach at rest | existing `_on_display_breach` (`:1817-1856`): release leases, then evict A only if still breached | unchanged |
| Idle unload of B | B's lease TTL, then release | 300 s default (`grant(ttl_s=300)`), configurable in the Model Soup card (§11.2) as "keep the deep model warm for N minutes" |

### 6.4 The routing settings that go with the lineup

| Setting | Value | Why |
|---|---|---|
| `model_routing.mode` | **`local_preferred`** | Local first, cloud when local fails or when the owner picks cloud for a turn. `local_only` is available as a one-click posture in the card. `smart` is removed (§11.3) |
| `model_routing.local_model` | `gemma4:e2b-fridayweaver-1.0` | unchanged |
| `capability_routing.reasoning` | `gemma4:e2b-fridayweaver-1.0`, provider `arbiter-local` | the explicit binding the router honours (`_route_chosen_seat`, `routing/model_router.py:378-384`) |
| `capability_routing.heavy_hitter` | `gemma4:12b` (seat B), provider `arbiter-local` | the "Think harder" button (§11.2) routes a turn here |
| `capability_routing.subagent` + `subagent_model` | `gemma4:e2b-fridayweaver-1.0` | scheduled and background work is where the tool-calling fine-tune earns its keep; 981 cloud calls / $504 a month move here |
| `capability_routing.local` | `gemma4:e2b-fridayweaver-1.0` | read by `work_plan._seat_for` for the reflex class |
| `capability_routing.memory_manager` | `gemma4:e2b-fridayweaver-1.0` | `memory_proposals` refuses non-local seats anyway |
| `capability_routing.creative_image` | **`sdxl-base-1.0`** for local, or a cloud image model; never z-image | the only image model that fits |
| `knowledge_graph.indexing_mode` | **`local`** once §7.3 lands | Tier B on seat A; wiki pages stop leaving |
| `judgment_gate.enabled` / `.model` | `true` / `gemma4:e2b-fridayweaver-1.0` | the gate becomes useful once a local judge exists |
| `model_routing.vault_local_only` | **`true`** | restores vault-tier stripping and force-local for vault turns, which is now satisfiable |
| `model_routing.cloud_consent` | **re-asked** (§10.2) after seat A is proven | the current answer was given against a snapshot that said local was incapable because nothing was measured |
| `voice_engine` | The owner's pick; the local mind is proven either way | `ask_friday` gains reach the moment A is up |

None of these are written by the executor. §12 stages them behind the owner's confirmations where they change posture, and the UI (§11) is where the owner sets them.

---

## 7. Tiered residency: what changes in the Arbiter and the policy

### 7.1 `residency_policy.py`

1. **A retained seat yields when the lease cannot fit beside it.** `RETAINED_THROUGH_LEASE` (`:364`) stays, and `_lease_budget()` (`:377`) gains a second return: `(budget_with_retained, budget_without)`. `_heavy()` (`:1021`) and the image seat (`:939-952`) try the first and fall to the second, recording `seat["displaces"] = "sidekick (does not fit beside it: X + Y > Z MiB)"` with the arithmetic. R10's text becomes: "The sidekick survives every lease that leaves it room. When a lease and the sidekick cannot share the card, the sidekick stands down and is reloaded on release, and the plan says so." A golden fixture for this card pins the numbers.
2. **The video seat refusal** (`:959-969`) changes its reason from "no local video backend exists" to "local video is unmeasured on this profile and its 5B decode hangs (`local_video.py:44-48`); routes to cloud". The old sentence is false in the tree.
3. `ROLE_RESIDENCY["heavy_hitter"]` stays `LEASED`. `context_for("heavy_hitter", ...)` will size seat B at the largest measured rung; the executor records the 65,536 and 131,072 rows in `measurements.json` under the seat's id after the first load, so `basis` reads `measured` rather than `extrapolated`.
4. `residency_policy.plan()` takes a new optional `lora_paths`-free signature: nothing. The policy stays pure; the file layout is the catalog's business (§7.2).

### 7.2 `residency_arbiter.py`

1. **LoRA and projector are first-class seat files.** `Arbiter.gguf_paths` (`:1168`) changes from `{model_id: path}` to `{model_id: {"gguf": path, "lora": path|None, "mmproj": path|None}}`, built by `residency_catalog.gguf_models()` from **`models.json`** (`model_store`), not from the stale `residency/gguf_models.json`. `model_store.register()` gains a `lora` field beside `mmproj`. `_spawn_once` (`:786`) appends `--lora <path>` when present and prefers the store's `mmproj` over `gguf_extract.projector_path` (`:842-847`). A seat whose record carries a `lora` and whose spawn cannot pass it is a `TransitionError`, never a base-model seat under the fine-tune's name. `_ENGINE_MEMO` and the Ollama-engine fallback are unchanged.
2. **A missing mapping never falls to the daemon on this machine.** `_load_pinned` (`:1932-1971`) currently calls `self.ollama.load()` when no GGUF is mapped. With zero Ollama models that produces a 404 and a DEGRADED boot. Change: when `gguf` is None and the daemon does not list the model, raise `TransitionError("no GGUF mapped and the daemon does not have it")` and record the seat as `pin_unenforced` with that reason, so boot continues with an honest empty seat rather than a degraded state.
3. **`_ours_resident_mib()`** (`:2071-2094`) reads `vram_mib` from the plan seat for each model in `self.llama.procs` instead of from the tuple. This is what makes the display-reserve sampler stop double-counting Friday's own seats.
4. **`_llama_server_pids()`** (`:482-507`) matches command lines against `~/.friday/runtime/models` only. After §9 that is correct. Until then a seat served from any other path is invisible to adoption, which is the 2026-09-09 kill in `DECISIONS.md`. §9 removes the case rather than widening the filter.
5. **`_read_published()`** (`:135-143`) reads with `encoding="utf-8-sig"`, per the BOM finding in `DECISIONS.md` 2026-09-09.
6. **Lease kinds.** `grant()` keeps `heavy_turn` and `image_job`. `heavy_turn` now: `displaced = self._evict_pinned(for_lease=seat)` evicts the sidekick only when `rp.needs_sidekick_evicted(plan, seat)` says the arithmetic requires it, and `release()` restores exactly the displaced list (already the case). The transition audit row records `displaced_sidekick: true|false` and the MiB that forced it.
7. **Warm-B window.** A new `Arbiter.keep_warm_s` (from `model_routing.deep_seat_keep_warm_s`, default 300) is the `heavy_turn` TTL; `expire_if_due()` already releases on expiry.

### 7.3 Everything that asks "is there a local model" asks one place

Five sites answer this question five ways today: `local_seats.installed()` (store + daemon + liveness), `routing/model_router._local_candidates()` (store + daemon), `indexer._available_local_model()` (daemon ∩ `TOOL_CAPABLE_IDS`), `voice_manifest._run_mind` (resolve + live endpoint), `residency_catalog.installed_entries()` (store + daemon + stale registry). They disagree, and one of them makes local Tier B impossible.

Change: `local_seats.serving()` becomes the single answer: `{model_id: base_url}` for every seat with a live `/health`, from `residency_arbiter.owned_endpoint` and the published file, then the daemon's `/api/tags` for daemon-served names. `installed()` keeps its meaning (can be served) and `serving()` means is being served. `indexer._available_local_model()` prefers `serving()` and drops the `TOOL_CAPABLE_IDS` intersection in favour of the store record's `template_supports_tools` (`models.json` already carries it). `_local_candidates()` uses `installed()`. The manifest's mind proof uses `serving()`. That is the whole of the KG fix: with seat A up, `indexing_mode: local` works, and "Local: nothing ever leaves this machine" becomes a true sentence about a mode that runs.

### 7.4 What share of turns this moves, honestly

From the 30-day ledger (**LEDGER**, §0 item 6), by kind, with a judgement about what the lineup can absorb:

| Kind | Cloud calls / $ | Movable to A or B | Basis for the share |
|---|---|---|---|
| `text` (single-shot: briefings, KG, summaries, drafts) | 3,354 / $85 | **≈90 %** | short prompts, no tool loop; E2B handles extraction and summaries, B handles editorial prose. The remaining 10 % is the owner choosing cloud for a briefing they want in frontier register |
| `scheduled` (heartbeat, routines, subagents) | 981 / $504 | **≈80 %** | tool calling is the fine-tune's measured strength; judgement-heavy routines go to B; the rest stay cloud by `task_overrides` |
| `chat` (interactive) | 1,846 / $659 | **≈50 to 70 %** | depends entirely on how often the owner presses "Think in the cloud" (§11.2). E2B for reflex and lookups, B for depth |
| `voice` | 119 / $2 | 100 % when `voice_engine=local`; 0 % when Gemini Live is chosen | the owner's choice per session |
| `creative` | 3 / $0.45 | image only via SDXL | |
| **Total** | ≈6,300 / $1,250 | **≈70 to 80 % of calls, ≈55 to 70 % of spend** | **ESTIMATE**; measured after rollout by re-running the §0 ledger query, which the Model Soup card shows live (§11.2) |

The last 7 days hold only 233 calls at $3.60, so the 30-day figures describe the month before, not this week. The share is measured, not promised: the card's "last 24 h / 30 d" ledger row is the acceptance test.

---

## 8. Speculative decoding

Tokens per second has never been the complaint. The receipts show the bill is cold load (8.7 to 26 s), seat death (the WSL share, the reap-on-boot, the Ollama fallback), and tool trimming at 32k. None of those change with a faster decode. This section is therefore small and every flag in it is revertible by restarting the seat.

| Seat | `--spec-type` | Why | What to measure before keeping it |
|---|---|---|---|
| A (E2B) | **`ngram-cache`** | there is nothing smaller in the family to draft with; ngram variants cost no VRAM and no second model (**BRIEF**, `llama-server --help`). Friday's turns repeat long tool schemas and prior tool results verbatim, which is the pattern n-gram lookup accepts | acceptance rate and `predicted_per_second` from the response `timings` over 50 real turns, against the same 50 with `--spec-type none`. Keep it only if median decode improves ≥ 20 % with no change in tool-call strict accuracy on the t2-single harness. Otherwise `none` |
| B (12B) | **`ngram-cache`**, same test | a draft model does not fit beside it (§1) | same |
| Both | never `draft-*` | VRAM | |

Flag names to try in order if `ngram-cache` is rejected by the binary at spawn: `ngram-simple`, then `none`. The spawn already retries once without the KV flag when a flag is rejected (`residency_arbiter.py:753-784`); the same retry covers `--spec-type`. Do not oversell this in the UI: it is a line in the seat's detail drawer ("speculation: ngram-cache, 31 % accepted"), not a feature.

---

## 9. Storage: get the weights off the WSL share

### 9.1 Why this is a root cause, not an incident

`llama.cpp` memory-maps the GGUF. A process reading over `\\wsl.localhost` survives the share disappearing and dies minutes later on the next page fault with a storage error in `ggml-base.dll` (**RECORD**, `DECISIONS.md` 2026-09-10 04:01). Separately, every `Path.exists()` on that UNC path blocks for the SMB timeout, and the tree performs that check on the chat path, the residency plan, and the intelligence payload (§0 item 3). Today's symptoms (a 25 s `Test-Path`, `/api/residency/status` timing out, no seat, cloud without a word) are one cause.

### 9.2 Where the weights go

`~/.friday/runtime/models/gguf/` on C:. This is the path `_llama_server_pids()` recognises as Friday's own (`residency_arbiter.py:502`), the path `gguf_models.json` already assumed, and the path the installer's model store uses. Files:

| File | From | Size |
|---|---|---|
| `base-e2b-q8_0.gguf` | WSL `gguf-intermediate/` | 4.95 GB |
| `fridayweaver-lora.gguf` | WSL `gguf-intermediate/` | 12.5 MB |
| `mmproj-e2b.gguf` | WSL `gguf-intermediate/mmproj.gguf` | 557 MB |
| `gemma4-12b-qat-q4_k_m.gguf` + mmproj | download (§6.1) | 6.6 to 6.9 GB + 0.16 GB |

Total ≈12.3 GB. C: has 19.8 GB free with a 10 GiB floor, so **seat A fits today (5.5 GB, leaving 14.3 GB) and seat B does not** until about 7 GB is freed.

### 9.3 Freeing the disk (the owner's decision; irreversible)

The image weights that can never render on this card (§1) are the obvious candidates: `flux_dev_fp8_scaled_diffusion_model.safetensors` (11.08 GB), `sd3.5_medium_incl_clips_t5xxlfp8scaled.safetensors` (10.84 GB), `z_image_turbo_fp8_e4m3fn.safetensors` (5.73 GB). Removing any one of the first two makes room for seat B. The executor does not delete them; it lists them in the card's disk row with their `fits_this_card` verdict and a "Move to…" action that copies to a path the owner picks and then removes the original, with the size shown before the click.

The `.ollama/models/.studio_links` directory holds another ≈47 GB of GGUFs, most excluded from the ladder by product decision. Same treatment: listed, verdicted, never deleted silently.

### 9.4 The copy itself

The WSL share is unresponsive as this is written and **must not be restarted while any seat is mmapped from it** (no seat is, today). The copy is done from *inside* WSL to `/mnt/c/Users/<you>/.friday/runtime/models/gguf/` once WSL answers again (`cp` with a checksum), or with `robocopy` from Windows if the 9p share comes back first. SHA-256 of each file is recorded in `models.json` (`model_store.register(..., sha256=)`). The WSL originals are left in place until the Arbiter has served the seat from C: for one full session; then they are the owner's to delete.

### 9.5 Code changes tied to storage

1. `model_store.available()` and `local_seats._friday_store()` **never call `exists()` on a UNC path on the request path.** A path that starts with `\\` is checked by a background thread with a 2 s budget and cached; the request path reads the cache. Until §9.2 lands this is what stops a wedged share from stalling chat.
2. `residency/gguf_models.json` is deleted after `residency_catalog.gguf_models()` is repointed at `models.json` (§7.2 item 1). Stale registries are how a plan believes in seven files that are not there.
3. `models.json` entry `gemma4:e2b-friday-v1` is **retired** (moved to `missing()` with a note), so `resolve()` can never fall back to it. The file stays on disk in `Friday-Models` (do-not-modify).

---

## 10. Knowledge graph and privacy posture

### 10.1 What is leaving today, stated plainly

With `indexing_mode: cloud`, `nightly_reindex: true`, `cloud_unrestricted`, `vault_local_only: false` and no `wiki_encrypted_sections`, the nightly Tier B pass sends every stale wiki chunk, cognitive-memory fact and conversation turn to the cloud extractor with no redaction, up to `MAX_CLOUD_EXTRACT_CALLS` = 200 per pass (`indexer.py:71`). The 226 new pages of family, financial, legal, health and ancestry material are in that corpus. `knowledge_context_block` separately injects plaintext page excerpts into every cloud system prompt (`integration.py:27-78`). This is what the recorded consent permits. It is also more than the consent's own snapshot understood, because that snapshot believed local was impossible.

### 10.2 The order of repair

1. Seat A up and proven (§12 steps 1 to 4). Nothing about posture changes before a local model exists, because a `local` KG mode that cannot run is the U1 lie.
2. `wiki_encrypted_sections` gets a UI (§11.4): a checklist of top-level wiki sections; checked sections never enter `knowledge_context_block` or the ambient prompt for a cloud provider. Default suggestion, shown and not pre-applied: the sections whose names match the sensitivity classifier's TIER_3 vocabulary.
3. `indexing_mode` back to `local`, with the panel showing the seat it will run on and refusing to save `local` while `serving()` is empty.
4. `vault_local_only: true`.
5. **Re-ask the consent.** `cloud_consent.record_consent()` is the only writer (`core/__init__.py:1946-1958`). The card (§11.2) offers "Re-answer the cloud question" which runs `assess_local_capability()` against the now-measured seat and shows the two choices again with the new snapshot. The owner may answer `cloud_unrestricted` again; the difference is that the answer will then be true to the machine.

### 10.3 Tier B on seat A

`_llm()` already calls `_call_ollama` directly for pinned chunks (`indexer.py:372-375`), and `_call_ollama` dispatches to `owned_provider(model)` first (`services/model_router.py:664-686`), so with seat A owned by the Arbiter the local path exists end to end once §7.3 lets the indexer find the seat. Cost: `MAX_REPORT_COMMUNITIES` = 24 and one extraction call per stale chunk at up to 4,096 output tokens; on the E2B at 80 to 170 tok/s a 226-page delta is on the order of an hour, scheduled at 03:00 beside memory dreaming. The pass holds the seat busy; the card shows "indexing" on the seat row for its duration.

---

## 11. Settings UI

### 11.1 Principles the executor builds to

1. **One question at the top of Intelligence:** what is about to answer me, where does it run, what will it cost. Answered by a card, not inferred from sixteen rows.
2. **Selected → effective → proven**, the Voice Stack card's own grammar (`index.html:34838`), applied to every seat.
3. **A control ships with the code that reads it** (`docs/decisions/2026-09-04-five-dead-settings.md`). Every control removed below is removed because `role_consumers.py` or this audit found no reader.
4. **Nothing silent.** A fallback, a refusal, an eviction, a stale value each render as a state with a reason and an action.
5. **Cost on the same screen as the choice.** The ledger row lives beside the seat rows.

### 11.2 Intelligence tab, rebuilt

Replace the four sections in `SettingsTabIntelligence` (`index.html:33942-34305`) with these, in order.

**A. The Model Soup card** (new `ModelSoupCard`, data from `/api/intelligence` extended with a `soup` object):

```
 NOW ANSWERING   FridayWeaver-1.0 · this machine · :8090 · 131k    ● proven 09:12 · 41 tok/s · $0
 DEEP MODEL      Gemma 4 12B QAT · this machine · on demand        ○ cold · loads in ~21 s, replaces FridayWeaver while it runs
 CLOUD           claude-fable-5-1 · Anthropic                      ● key present · $0.03 / turn typical (30-day median)
 POSTURE         Local preferred · vault kept local · KG indexes locally · cloud consent: unrestricted (answered 09-06, before local was measured)  [Re-answer]
 LAST 24 H       61 turns · 55 local · 6 cloud · $0.41           LAST 30 D  7,219 · 916 local · $1,250   (24 h figures illustrative)
 [Local only]  [Local preferred]  [Cloud only]                    [Think in the cloud for this chat ▾]
```

Row states use the manifest vocabulary: **● proven** (green, with time and measured tok/s), **◐ loading** (with the cold-load estimate and elapsed), **○ cold** (grey), **✕ refused** (red, reason, action). The NOW ANSWERING row is `serving` from the ledger plus the live seat; when they disagree (bound to X, last served by Y) the row shows both and says which will take the next turn. The `[Re-answer]` control runs §10.2 step 5. The ledger numbers are the §0 query, live.

**B. Where work runs.** Three mode buttons (`local_only`, `local_preferred`, `cloud_only`). `smart` is removed from the picker and from `routing_modes` in `routes/intelligence.py:1100-1128`; an existing `smart` value is read as `local_preferred` and the router's `smart` branches are left in place for one release, then deleted. Help copy per button, exact:

- **Local only.** "Every turn runs on this machine. If the local model cannot answer, Friday says so and offers the cloud for that turn. Nothing leaves without that click."
- **Local preferred.** "Local first. When the local model fails or is busy, the turn goes to the cloud model below and the reply is marked cloud. Vault content still stays local."
- **Cloud only.** "Every chat, voice and background turn goes to the cloud model. Embeddings still run here. This is what has been running since 6 September."

**C. Seats.** Seven rows, grouped as today by residency, each row `label · selected model · effective (where it is running, or why not) · proven`. The rows are the seats with a reader (`role_consumers.py`):

| Row | Capability key | Reader |
|---|---|---|
| Everyday conversation | `reasoning` | `local_seats._configured` / router `_chosen_seat` |
| Deep thinking (on demand) | `heavy_hitter` | `local_seats._configured` / lease |
| Background & research | `subagent` (+ `subagent_model` mirror) | `agent.py:2861` |
| Quick reflexes | `local` | `work_plan._seat_for` |
| Memory keeper | `memory_manager` | `memory_proposals.seat` |
| Images | `creative_image` | `creative_engine._configured_image_model` |
| Video | `creative_video` | `creative_engine` |
| Music | `creative_music` | `music_engine._seat_model` |
| Voice | `voice` (mirror `voice_model`) | `voice_engine._get_live_model`; rendered as a link to the Voice tab, not a picker |

**Removed** from `ROLE_SPEC` (`routes/intelligence.py:37-92`), `_RESIDENCY_FOR_ROLE`, `seat_binding`, and `DEFAULT_SETTINGS["capability_routing"]`: `orchestrator`, `sidekick_fast`, `function_manager`, `researcher`, `asr`, `tts`, `embedding`. `test_role_consumers::test_every_declared_seat_is_mapped` is updated to the seven-plus-voice set. The `embedding` row is replaced by one non-interactive line under the card: "Memory embeddings: all-MiniLM-L6-v2 on this CPU, always." The residency plan's `ASSIGNED_ROLES` (`residency_policy.py:131`) loses the four orphaned roles and their R11 refusals stop appearing.

Image models in the Images picker carry `fits_this_card` verdicts from `local_image.py:310`: a too-big model is greyed with "needs 10,453 MiB; this card can free 9,722". Cloud image models show their per-image price from the catalogue.

**D. The machine.** Keep the VRAM/RAM/disk bars. Add a **Disk** subsection listing model files over 4 GB with their fit verdict and a "Move to…" action (§9.3). Add a **Weights** line per local seat: path, size, SHA-256 short, and a red state when the path is a UNC share ("served over a network share; move it").

**E. Providers.** Unchanged.

Delete `SettingsTabModels` (`:34305-34955`) and `SettingsTabOrchestrator` (`:35518-36894`). Neither is rendered.

### 11.3 The turn-level control

In the chat header, beside the existing `ConversationSeatPicker` (`:33403`), one button: **Think in the cloud** (when the conversation is on a local seat) or **Answer locally** (when on cloud). It sets a per-conversation `route_mode` for the *next* turn only, shows the cost estimate for the cloud model before the click, and the reply badge says which served. The `QuickSwitch` pill (`:33332`) keeps its role as the global default and gains a third state, **stalled**, when `/api/intelligence` has not answered in 10 s, with the text "Friday's model status is not answering; the last known seat was X".

### 11.4 Privacy & Security tab

- Delete the **Unrestricted Cloud** toggle (`:35348-35360`, U2). In its place, a read-only **Cloud consent** row showing the recorded choice, the date, and the snapshot's one-line reason, with **Re-answer** (same control as the card).
- Rename **Local-Only Mode** (`:35327-35340`, U3) to **Keep vault content off the cloud**, help: "Vault-tier notes (finance, health, legal, private) are stripped from cloud prompts, and questions that touch them run on the local model. Everything else follows the routing mode on the Intelligence tab." Greyed with reason when no local seat is proven ("no local model is serving; turning this on would refuse vault questions").
- New **Wiki sections kept off the cloud** checklist (§10.2 step 2) writing `wiki_encrypted_sections`.
- The **Egress** line: `egress_mode` (`audit`) rendered with its meaning and a link to the ledger; unchanged behaviour.

### 11.5 Knowledge Graph tab

`Semantic indexing` select keeps `local` / `cloud`. Copy becomes:

- **Local.** "Runs on {seat name} on this machine. Nothing leaves." Disabled with reason when `serving()` is empty: "No local model is serving, so local indexing cannot run. Load the model on the Intelligence tab first."
- **Cloud.** "Sends wiki, memory and conversation text to {cloud model}. Under your current cloud consent that text is {unredacted | redacted by tier}." The clause is computed from `cloud_consent`, not from `unrestricted_cloud`.

"Active now" shows what the *last* pass actually used (`_TIER_B_STATE["last"]["mode"]` and model), not the setting. Nightly reindex keeps its toggle and gains the next-run time.

### 11.6 Voice tab

Keep the 2026-09-16 Mode picker and Voice Stack card as built. Three changes: the MIND row links to the Model Soup card and shows the same seat id and port; the dead `auto` branches in `SettingsTabVoice` (`:34956` onward, U11) are removed; the cloud-mode disclosure adds one line computed from the manifest, "Questions about your own notes {will | will not} reach Friday's local model right now."

### 11.7 Copy fixes elsewhere

- `seat_transparency._MODE_MEANING` (`services/seat_transparency.py:45-52`): drop `smart`; `local_preferred` reads "local first; cloud when local fails, and the reply says so".
- The `local_only` refusal (`routing/model_router.py:887-890`): "Local-only mode is on, but no local model is serving right now. Load one on the Intelligence tab, or answer this turn in the cloud." (Ollama is not mentioned.)
- `settings.setup.bundled_model` is migrated to `FLOOR_MODEL` on next save (U10).

### 11.8 What the owner can now tell at a glance

Which model will answer the next turn, whether it is on this machine, whether it is proven up or merely configured, what the last turn cost and what the month cost, what the cloud consent says and when it was given, and one button to change the answer for this chat. Every control on the tab has a reader in the tree.

---

## 12. Rollout order

Each step is a commit that leaves the tree working, is independently revertible, and names its revert. Python changes need a Friday restart; the executor coordinates rather than bouncing the production server (port 3000). Never broad-kill `python.exe`/`pythonw.exe`; stop seats by PID. Do not restart WSL while any llama-server is mmapped from it (none is, today). Nothing under `Friday-Models` is modified.

| Step | Change | Revert | Gate |
|---|---|---|---|
| 1 | **UNC guard.** `model_store.available()` / `local_seats._friday_store()` stop calling `exists()` on `\\` paths inline (§9.5 item 1). `_read_published()` reads `utf-8-sig`. | git revert | `/api/residency/status` answers in < 2 s with the share wedged (**it took > 25 s today**). Unit test with a fake UNC path and a monkeypatched `exists` that sleeps |
| 2 | **Weights onto C:.** Copy the three FridayWeaver files (§9.4) with SHA-256; register `lora` in `models.json` via `model_store.register`; retire `gemma4:e2b-friday-v1`; delete `residency/gguf_models.json` after §7.2 item 1. | restore the JSON backups (timestamped, as on 09-09) | `sha256` matches; `local_seats.installed()` lists exactly one E2B |
| 3 | **Arbiter owns the LoRA seat.** §7.2 items 1, 2, 3, 5. Boot spawns A at 131,072 with `--lora --mmproj`; `GET /lora-adapters` on the seat shows scale 1.0. | git revert; the seat is stopped by PID | the manifest's mind proof passes; `friday.log` shows `[seats] brain: using 'gemma4:e2b-fridayweaver-1.0'` with no substitution line; one `input_audio` request returns 200 (§14.1's probe against the real seat) |
| 4 | **One resolver** (§7.3): `local_seats.serving()`; indexer, router candidates and manifest read it. | git revert | with A up: `indexing_mode=local` runs a Tier B delta and the log shows the seat id; `_local_candidates()` lists A; `resolve("brain")` never returns a name nothing serves |
| 5 | **Policy: yielding sidekick** (§7.1 items 1 to 3); Arbiter §7.2 items 6 and 7; golden fixture for this card. | git revert | `rp.plan()` on the profile places A pinned and B leased with `displaces: sidekick (…)` and the arithmetic in the string |
| 6 | **Intelligence tab rebuild** (§11.2, §11.3), `/api/intelligence` `soup` object, deletion of the seven dead seats and the two dead tabs. Dead-settings tests for every surviving control. | git revert (one commit, UI only) | the card renders the seat from step 3 as proven; the removed keys no longer appear in `DEFAULT_SETTINGS`; `test_role_consumers` passes on the new set |
| 7 | **Privacy and KG tabs** (§11.4, §11.5), `wiki_encrypted_sections` UI. | git revert | toggling "Keep vault content off the cloud" changes `vault_policy.status()`; the deleted Unrestricted toggle has no reader left |
| 8 | **Posture flip, by the owner, in the UI:** `local_preferred`, reasoning/subagent/local/memory_manager bound to A, KG `local`, `vault_local_only` true, judgment gate on. | each is one setting; the card's mode buttons revert in one click | the LAST 24 H row shows local turns climbing; the nightly KG pass logs the local seat |
| 9 | **Seat B.** Disk freed per §9.3 (the owner's yes), stock 12B QAT Q4_K_M downloaded through the allowlisted fetch with size shown, registered with mmproj, measured at 65,536 and 131,072 under the caps, bound to `heavy_hitter`. | remove the file and the binding | a "Think harder" turn grants `heavy_turn`, evicts A, answers, releases, and A is back within its measured cold load; the card shows all of it |
| 10 | **Speculation flags** (§8) on A, then B, each behind the measurement. | drop the flag; restart the seat | median decode ≥ 20 % better and no strict-accuracy loss, or the flag goes |
| 11 | **Re-answer consent** (§10.2 step 5), offered by the card once A has been proven for a day. | the record is the owner's | `cloud_consent.at` newer than 2026-09-06 with a snapshot that says `capable: true` |

Steps 1 to 4 are the repair. Steps 5 to 7 are the shape. Steps 8 to 11 are the owner's calls, made from a UI that tells the truth.

---

## 13. Measurements the executor records (and where)

| What | How | Where it goes |
|---|---|---|
| Seat A cold load and VRAM from C: at 131,072 with `--lora --mmproj` | Arbiter transition audit + `nvidia-smi` before/after | `measurements.json` under `gemma4:e2b-fridayweaver-1.0`, rows for 131,072 (and 32,768 for the policy's floor rung) |
| Seat A `input_audio` on the real seat | the §14.1 request against `owned_endpoint` | `friday.log` receipt; the manifest's mind proof gains an `audio: proven` field |
| Seat B VRAM at 65,536 and 131,072 under `-b 512 -ub 512`, KV q8_0, cold load | same | `measurements.json` under the 12B id |
| Time from "Think harder" click to first token, including A's eviction and B's load | receipt | the card's DEEP MODEL row shows the last measured figure |
| A's restore time after a B or SDXL lease | transition audit | same row |
| Ngram acceptance and decode rate, 50 turns each way | `timings` in responses | seat detail drawer |
| Local share of calls and spend, 24 h and 30 d | the §0 ledger query | the card, live |
| Tool-call strict/lenient on the served GGUF+LoRA | needs the endpoint scoring mode `frontier-on-12gb` §"The dependency" describes, inside `Friday-Models` (**the owner's go required**) | `Friday-Models/docs/reports` |

---

## 14. What was verified, and what could not be

### 14.1 Verified: native audio input on this build

Procedure (**MEASURED-2026-09-17**, all on loopback, no seat was live, the probe was stopped by PID and VRAM returned to 1,682 MiB):

```
llama-server.exe -m …\Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf
                 --mmproj …\Gemma-4-E4B-…-Q4_K_M.mmproj.gguf
                 --alias probe-e4b --host 127.0.0.1 --port 8199 -ngl 99 --flash-attn on
                 -c 4096 -b 512 -ub 512 --jinja --no-webui
```

The log shows `init_audio: audio input is in experimental stage` and `loaded multimodal model`. A `POST /v1/chat/completions` with `{"type":"input_audio","input_audio":{"data":<base64 of resources/voice_proof.wav>,"format":"wav"}}` returned **HTTP 200**. With thinking off (`chat_template_kwargs.enable_thinking=false`) the reply was exactly `Friday, what time is it right now?` in 0.27 s wall (92 prompt tokens, 10 predicted, 80 tok/s decode). The response's `system_fingerprint` is `b10415-1d2869c6e`. Issue #21868 does not reproduce on this binary. The same request against seat A itself is step 3's gate; it could not be run today because A's mmproj is on the unreachable share.

### 14.2 Verified: the stall

`GET http://127.0.0.1:3000/api/residency/status` timed out at 25 s; `/api/health` took 10.8 s; `wsl.exe --list --verbose` did not return within the tool's 25 s budget; `Test-Path` on the share hung earlier in the day (**BRIEF**). The code path is cited in §0 item 3.

### 14.3 Not verified (UNCHECKED)

- **Seat A's actual VRAM and cold load from C:.** The 4,291 MiB figure is from 2026-09-12 with the files on the WSL share; mmap over 9p versus NTFS should not change VRAM but may change load time materially. Step 2 measures it.
- **That the 2026-09-09 audio verification on the FridayWeaver seat used this same binary.** `DECISIONS.md` says it did ("Friday's real binary"); it was not re-run on that seat.
- **The stock 12B QAT Q4_K_M's exact size and whether its published mmproj carries the audio tower.** The 6.6 GB figure is from the brief; the on-disk HauhauCS mmproj is 160 MB, which is consistent with vision-only. The executor checks the mmproj's tensor names for an audio encoder before promising audio on seat B; if absent, seat B is text+vision and the card says so.
- **Whether the E2B LoRA behaves identically when the base is served through `--lora` at Q8_0 versus the training checkpoint.** Nobody has scored the served artifact (`frontier-on-12gb` §"The dependency"). Step 13's last row is the measurement, and it needs the owner's permission inside `Friday-Models`.
- **Ngram speculation's effect on this workload.** Unmeasured anywhere; §8 keeps it behind a measurement.
- **What respawned the manual seat on 2026-09-10 with `--lora` intact.** `DECISIONS.md` records it and no scheduled task or script that does it was found (`Get-ScheduledTask` lists only the forensics snapshot and the morning briefing; no `.ps1`/`.bat` under `~/.friday` mentions `lora`). Unknown. After step 3 it does not matter.
- **The wiki's 226 new pages' actual sensitivity distribution.** Not read. §10 treats the brief's description as the fact.

---

## 15. Sources

**Repository, read 2026-09-17 at `9eab2e2`:** `services/residency_arbiter.py`, `services/residency_policy.py`, `services/local_seats.py`, `services/model_store.py`, `services/residency_catalog.py`, `services/model_router.py`, `routing/model_router.py`, `services/agent.py` (`_generate_agent`, `spawn_task`), `services/model_plan.py`, `services/tool_budget.py`, `services/knowledge_graph/indexer.py`, `services/knowledge_graph/integration.py`, `services/knowledge_graph/__init__.py`, `services/egress_gate.py`, `privacy/vault_policy.py`, `privacy/cloud_consent.py`, `services/local_image.py`, `services/local_video.py`, `services/creative_engine.py`, `services/voice_manifest.py`, `routes/voice.py`, `services/voice_engine.py`, `services/memory_dreaming.py`, `services/memory_proposals.py`, `services/learning_loop.py`, `services/judgment_gate.py`, `services/news_engine.py`, `services/role_consumers.py`, `services/seat_transparency.py`, `services/seat_binding.py`, `services/hardware_profile.py`, `routes/intelligence.py`, `routes/chat.py`, `routes/privacy_consent.py`, `core/__init__.py` (`DEFAULT_SETTINGS["model_routing"]`), `index.html` (`SettingsWS`, `SettingsTabIntelligence`, `SettingsTabModels`, `SettingsTabOrchestrator`, `SettingsTabVoice`, `VoiceStackCard`, `SettingsTabPrivacy`, `SettingsTabKnowledge`, `QuickSwitch`, `ConversationSeatPicker`, `CloudConsentGate`).

**Design record:** `docs/design/active/voice-system-clean-sheet.md` (incl. §15 to §16 appended by the 2026-09-16 build), `voice-mode-diagnosis-and-repair.md`, `frontier-on-12gb-deepseek-derived.md`, `local-voice-repair-and-native-audio.md`, `docs/decisions/2026-09-04-five-dead-settings.md`.

**Machine records:** `~/.friday/settings.json` (routing, capability, KG, voice keys; secrets not read), `~/.friday/runtime/models/models.json`, `~/.friday/runtime/residency/{endpoints,gguf_models,hardware-profile,measurements}.json`, `~/.friday/seat_state.json`, `~/.friday/startup-report.json`, `~/.friday/friday.log` (tail), `~/.friday/costs.db` (read-only query), `~/Projects/Friday-Models/docs/DECISIONS.md` (2026-09-09 and 2026-09-10 entries, read only).

**Live probes:** `nvidia-smi`, `Get-Process`, `Get-NetTCPConnection`, `Get-ScheduledTask`, `ollama list`, `fsutil sparse queryrange`, the llama-server audio probe (§14.1), `GET /api/health` and `GET /api/residency/status` on `127.0.0.1:3000`.
