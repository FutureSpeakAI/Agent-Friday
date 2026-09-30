# Model Soup: the advisor that fits a whole local stack to the machine it is on

> **Status:** active, specification
> **Written:** 2026-09-30
> **Implementation:** none. Prototype of the screen: [`prototypes/model-soup-advisor.html`](prototypes/model-soup-advisor.html). Tier inputs: [`bonsai2-floor.md`](bonsai2-floor.md) and [`bonsai2-tiers.json`](bonsai2-tiers.json).
> **Relationship to `model-soup.md`:** that document (2026-09-17) is the settings-surface and routing authority for the Intelligence tab, and code cites it by section; it stands. This document specifies the *advisor*: the tool and routine that profiles the machine, solves the fit, and installs the stack. The card `model-soup.md` §11.2 describes is where the advisor's result is shown.
> **Owner's direction (verbatim, 2026-09-29):** "enable them to run a routine that will recommend what models their computer can use, and I do mean scour the fucking internet and figure out what any user can run locally with enough headroom for their OS and for the Friday desktop and then stack them with whatever models we can, including system one, including needle, including Leia [Laya], including a deep Reasoner, including image and video and music models, whatever they can support. I want a tool that will supply them with a model soup custom tailored for their hardware to run within Friday, full stop. But we start with bonsai2 at whatever quantization the user's hardware can support."
> **Method:** STORM with the same six simulated experts as the floor document. **Provenance tags** as there: MEASURED, PUBLISHED (with source), TREE (code at `02035ba6`), ESTIMATE, UNMEASURED, plus **API** for a fact read from the public Hugging Face API on 2026-09-30.

---

## 0. The answer in one page

1. **The advisor is one routine with three doors:** the last step of onboarding, a button in Settings, and a voice tool ("Friday, what models can this computer run?"). All three run the same function and show the same result.

2. **It profiles locally and solves locally.** The hardware profile never leaves the machine. The only network traffic is a read of public catalogue metadata (Hugging Face's model API and the publishers' own pages) with nothing identifying in the request, the way `update_check` already does it with a test that proves it (**TREE**, `services/update_check.py`, `tests/unit/test_update_check.py::test_outbound_request_carries_nothing_identifying`). The fit is solved on the machine against the profile. This is amendment A3 applied to a recommender.

3. **The stack has twelve roles, and Bonsai 2 fills the first.** Brain, system one, Laya, Needle, deep reasoner, embeddings, speech in, speech out, vision, image, video, music. Each role has a ranked shortlist (§4) with its real size, licence and telemetry status, and the solver picks the best entry per role that fits the tier's budget, deciding which seats live together and which take turns through the residency arbiter (§5).

4. **Licences and telemetry are checked for every recommendation.** Non-commercial, revenue-capped and gated licences are flagged in the result, never hidden (§6.1). Any component that phones home is either silenced and blocked at the operating-system firewall for that process, with an egress test proving silence, or it is excluded. That is the proposed answer to the pending Needle decision (§6.2): **include only behind the block, and exclude if the block cannot be proven**.

5. **Installation is one click, and nothing is reported done that was not verified.** Each model is downloaded, loaded, benchmarked on the device and, for tool-using seats, run through the conformance gate; its record turns from ESTIMATE to MEASURED. The gate shapes what is *recommended*; it never refuses what the user *chose* (the maintainer's standing decision, **TREE**). "Any model you want" is always a door on the screen.

6. **The stack stays current** by re-running the catalogue read on a schedule the user sets and proposing, never applying, an upgrade.

7. **Three decisions are the owner's** (§10): the Needle rule; whether flagged-licence models are recommended by default or only on request; and whether the advisor may pre-download the brain during onboarding before the user has seen the plan.

---

## 1. Where it runs and how it is reached

| Door | Trigger | What the user sees |
|---|---|---|
| **End of onboarding** | after the brain is installed (the floor document's pick) | "Want me to see what else this computer can run?" One button. The result screen (§7). |
| **Settings > Intelligence** | the Model Soup card's "Re-check this computer" button | same screen, with the current stack shown beside the proposal |
| **Voice** | `model_soup_advise` voice tool (§8) | one spoken sentence per role that changes, then "Want me to install that?" |
| **Schedule** | a refresh interval the user sets (default: monthly, off on metered connections) | a proposal in the tray, never an install |

The routine is idempotent and cheap to re-run: the profile takes about a second (`hardware_profile.get`, memoised 60 s, **TREE**), the catalogue read is a few small JSON GETs cached for a day, and the solver is arithmetic.

---

## 2. Profile: what is measured, and where it stays

`hardware_profile.detect()` (**TREE**) plus the additions in the floor document §7.1. The advisor reads:

| Field | Source | Used for |
|---|---|---|
| GPUs: vendor, name, total VRAM, live used, compute class, driver | `nvidia-smi` today; Vulkan, HIP, `Win32_VideoController`, `system_profiler` after floor item 1 | the tier, packing, KV type, `-ngl` |
| Display reserve | `MIN_DISPLAY_RESERVE_MIB` and the live counter with its impossible-value rejection (**TREE**, `hardware_profile.py:249-338`) | VRAM budget |
| RAM total and available; bandwidth class and GB/s estimate | `psutil`, SMBIOS | CPU tiers, co-residency in RAM |
| CPU model, physical cores, threads, feature flags | `platform`, `cpuid` probe | threads, kernel path, the floor |
| Disk free and read rate | `detect_disk` | whether a download is allowed (R8), load times |
| OS family and version | `detect_os` | reserves, runtime build |
| Friday's own footprint | server RSS at boot | the "Friday desktop" headroom the owner asked for |
| Apple unified memory | `memory_bandwidth.class == "unified"` | usable = 0.75 × total |

The profile is written to `~/.friday/runtime/residency/hardware-profile.json` and nowhere else. The advisor's result screen shows it in full under "Show me the numbers", because a recommendation the user cannot audit is a claim (north star §6.8).

*Privacy reviewer:* "The profile is a fingerprint. GPU name plus driver plus CPU plus RAM plus disk is close to unique. It must not be in any request, log line that leaves, or Doctor report that is uploaded, and the Doctor report is never uploaded (A3)." The egress test in §6.3 covers the advisor's own requests.

---

## 3. Catalogue: what is read from the internet, and how

### 3.1 Sources

| Source | What | Request | Cache |
|---|---|---|---|
| Hugging Face model API: `GET /api/models/{id}?expand[]=cardData&expand[]=gated&expand[]=siblings&expand[]=usedStorage` | licence, gating, file list with sizes, tags, last modified | anonymous, no token, no query parameter beyond the expand list | 24 h |
| Hugging Face author listing: `GET /api/models?author=prism-ml` | new Bonsai releases | same | 24 h |
| Publisher docs (PrismML formats and models pages) | packing sizes and runtime notes | plain GET | 7 d |
| The fork's GitHub releases: `GET /repos/PrismML-Eng/llama.cpp/releases/latest` | runtime build per platform | same shape as `update_check` | 24 h |
| Friday's own shortlist file `resources/model_soup_shortlist.json` | the ranked candidates per role (§4), shipped with the release and updated by release | none | |

**MEASURED-2026-09-30:** the model API returns `cardData.license`, `gated` and `usedStorage` for every candidate in §4 in one anonymous GET each; the whole shortlist took 28 requests and under a minute. That is the entire network cost of a refresh.

### 3.2 What is never in a request

No hardware field, no install id, no version string, no `User-Agent` beyond the HTTP library's default, no cookies, no token. The same test that guards `update_check` is applied to the advisor's fetch seam (§6.3). Search is not performed on the user's behalf: the shortlist is curated in the release, and the catalogue read only *refreshes* the entries' metadata. "Scour the internet" happens in the release process and in this document, not on the user's machine, because a live web search from a private machine is itself a trace.

### 3.3 Shortlist maintenance

The shortlist is a JSON file in the repository with one entry per candidate: id, role, publisher, licence as read, licence class (§6.1), telemetry class (§6.2), runtime, files with sizes and packings, minimum tier, published quality figures with their source, and a `last_reviewed` date. It is reviewed at each release against the API and the sources cited in §4; an entry whose licence or gating changed is flagged in the release notes.

---

## 4. Roles and candidates

For each role: the recommendation per tier, then the shortlist with real numbers. Licence and gating are **API** unless noted; sizes are from the publishers' file lists or the files on this machine (**MEASURED**). "Tier" refers to the floor document's table.

### 4.1 Brain: Bonsai 2 27B, always

| Candidate | Licence | Size | Runtime | Tier | Note |
|---|---|---|---|---|---|
| **Ternary Bonsai 2 27B `PTQ1_0`** | Apache-2.0 | 5.95 GB + 0.63 GB mmproj | PrismML fork | T1+ | the standard; packing and context by tier |
| Ternary Bonsai 2 27B `PQ2_0` | Apache-2.0 | 7.21 GB | fork | T6+ (Ampere, RDNA, Blackwell, Apple M5) | faster where bandwidth is abundant |
| Ternary Bonsai 2 27B MLX 2-bit | Apache-2.0 | 8.49 GB | mlx-lm | T3b | best Apple decode; separate API, so second choice |

Nothing else is recommended for this seat. Gemma 4, Qwen 3.6 and every other family stay installable by choice from the picker.

### 4.2 System one: the fast responder

The seat that answers "what time is it", acknowledges a command, and drafts the first sentence while the brain thinks. Latency under 300 ms to first token on the tier's hardware is the requirement; quality is second.

| Candidate | Licence | Size | Runtime | Tier | Note |
|---|---|---|---|---|---|
| **Ternary Bonsai 1.7B** (`Ternary-Bonsai-1.7B-gguf`) | Apache-2.0 | ~0.5 GB | fork | T1+ | same runtime, same publisher; co-resides in RAM on every tier |
| Ternary Bonsai 4B `PQ2_0` (on disk, 1,025 MiB) | Apache-2.0 | 1.0 GB | fork | T2+ | the one measured here; `--no-repack` on CPU |
| Gemma 4 E2B Q4_K_M | Gemma terms (flag: restricted) | 1.6 GB | stock or fork | T4+ | by choice; has native audio input |

On T0 with D1 = yes, the 4B or 1.7B *is* the brain, labelled as such.

### 4.3 Laya: the typed-decision classifier

`convaiinnovations/laya`, ModernBERT-large, 421M parameters, Apache-2.0 (**API**), about 2 GB on disk in fp32 ONNX (**TREE**, `laya_backend.py`; the consolidated build runs shadow mode with ONNX fp32 after int8 changed answers). CPU-only by design, about 300 ms per decision, 42 s cold load on a warm-up thread. The advisor always includes it on T1 and above as a CPU seat; it costs no VRAM and about 1.7 GB of RAM while loaded. Its own document (`laya-across-the-harness.md`) says it cannot take decisions until it is fine-tuned on the owner's recorded decisions; the advisor installs it for shadow mode and says so.

### 4.4 Needle: on-device tool routing

Cactus Compute's Needle 3: a 121M-parameter automation model shipped as 8 to 29 MB weight files (`needle3.cact`) and a **closed prebuilt engine under 1 MB per platform**; the repository is Apache-2.0, the weights' licence is not stated in the card (**API**: no `cardData.license` on `cactus-compute/needle3`), and "by default, telemetry is turned on in the binary", opt-out by `NEEDLE_TELEMETRY=0` and `DO_NOT_TRACK=1`; what is collected and where it goes is not documented (**PUBLISHED**, [repository](https://github.com/cactus-compute/needle), [PyPI](https://pypi.org/project/cactus-needle/)). It would take the "pick the tool and fill its arguments" step for simple spoken commands at a few milliseconds on any CPU, ahead of the brain.

The recommendation is in §6.2: **included only behind a proven block; otherwise excluded.** Until D-Needle is decided, the advisor lists Needle as "available, pending the owner's telemetry rule" and does not install it.

### 4.5 Deep reasoner

For the turn the brain should not take: long, judgement-dense, many-step. Cloud by choice is always the other door (A6).

| Candidate | Licence | Size | Runtime | Tier | Note |
|---|---|---|---|---|---|
| **gpt-oss-20b** (MXFP4) | Apache-2.0 | ~12 GB | stock llama.cpp | T6+ (16 GB), T5 by turns | native reasoning effort levels; 21B MoE with 3.6B active |
| Qwen3.6-35B-A3B IQ4_NL | Apache-2.0 | ~20 GB | stock llama.cpp with `--n-cpu-moe` | T5 by turns with 32 GB RAM (measured here at 21.6 tok/s with 20 experts on CPU, **TREE** `start-brain.ps1`), T7 resident | the box's previous brain |
| Bonsai 2 27B in `xhigh` thinking | Apache-2.0 | 0 extra | fork | every tier | the same seat, more thinking budget; the default deep reasoner on T1 to T4 where nothing else fits |

On T5 the reasoner *takes turns* with the brain (lease, §5). On T7 and up it is resident.

### 4.6 Embeddings

| Candidate | Licence | Size | Runtime | Tier | Note |
|---|---|---|---|---|---|
| **Qwen3-Embedding-0.6B** (Q8 GGUF) | Apache-2.0 | 0.64 GB | stock llama.cpp `--embeddings` | T1+ (CPU) | 64.3 MTEB multilingual, 32K context (**PUBLISHED**) |
| nomic-embed-text-v1.5 | Apache-2.0 | 0.27 GB | stock | T1+ | the lightest good one; long documents |
| bge-m3 | MIT | 1.2 GB (fp16) | stock | T2+ | dense + sparse + multi-vector |
| embeddinggemma-300m (in use today) | **Gemma terms, gated (flag)** | 0.3 GB | Ollama/stock | | stays by choice; not recommended because the licence is restricted and the repo is gated |

Changing the embedder re-indexes the knowledge graph; the advisor says how long that took last time before offering it.

### 4.7 Speech in

| Candidate | Licence | Size | Runtime | Tier | Note |
|---|---|---|---|---|---|
| **Parakeet-TDT 0.6B v3** | CC-BY-4.0 (attribution required; flag as "attribution") | 2.4 GB fp16, 0.6 GB int8 | NeMo or ONNX | T2+, T4+ | 6.34% WER English, fastest on CPU (**PUBLISHED**, Open ASR leaderboard via northflank) |
| whisper-large-v3-turbo (in use today, faster-whisper) | MIT | 1.6 GB | CTranslate2 | T2+, T4+ | 99 languages, 7.75% WER |
| Moonshine base | not stated (**API**: no licence field; flag "check") | 0.06 GB | ONNX | T1 | streaming on tiny CPUs |
| whisper small int8 | MIT | 0.25 GB | CTranslate2 | T1 | the floor's default |

Cloud voice (Gemini Live) remains the other door and is the default where the user chose it (`voice_model` setting, **TREE**).

### 4.8 Speech out

| Candidate | Licence | Size | Runtime | Tier | Note |
|---|---|---|---|---|---|
| **Kokoro 82M** (in use today, ONNX q8f16) | Apache-2.0 | 0.33 GB | ONNX, CPU or GPU | T1+ | 54 voices; the time-and-length guard against GPU contention is in the tree |
| Piper lessac medium (in use today) | MIT | 0.06 GB | ONNX CPU | T1 | the fallback that always speaks |
| Chatterbox-Turbo | MIT | ~1.5 GB, ~6 GB VRAM | PyTorch | T6+ | voice cloning from 7 s; heavier; by choice |

### 4.9 Vision

Bonsai 2's own mmproj (0.63 GB, Apache-2.0) loaded on demand (**PUBLISHED**; **MEASURED** file). No separate vision model is recommended on any tier; on T4 the vision turn costs context and the advisor says so.

### 4.10 Image generation

| Candidate | Licence | Size | Runtime | Tier | Note |
|---|---|---|---|---|---|
| **Z-Image-Turbo** fp8 (on disk, 5.7 GB) | Apache-2.0 | 5.7 GB + text encoder | ComfyUI | T7 resident; T5 by lease (measured render peak 10,453 MiB, **TREE** `local_image.py`) | fast, permissive |
| bonsai-image-ternary-4B | Apache-2.0 | ~1.5 GB | MLX (Apple) / gemlite (CUDA) | T3a+, T4+ | PrismML's own; small enough to co-reside; quality UNMEASURED |
| Qwen-Image Q3_K_S (on disk, 8.3 GB) | Apache-2.0 | 8.3 GB + 8.7 GB encoder | ComfyUI | T7+ | best text rendering; heavy |
| SDXL base (on disk, 6.5 GB) | OpenRAIL++-M (use restrictions; flag) | 6.5 GB | ComfyUI | T5 by lease (8,192 MiB peak) | the only one that fit T5 in the earlier study |
| FLUX.1-dev fp8 (on disk, 11 GB) | **non-commercial (flag: excluded from recommendations)** | 11 GB | ComfyUI | T7+ | by choice only |
| SD3.5 medium (on disk, 10.8 GB) | **Stability community licence, revenue cap, gated (flag)** | 10.8 GB | ComfyUI | T7+ | by choice only |

### 4.11 Video generation

| Candidate | Licence | Size | Runtime | Tier | Note |
|---|---|---|---|---|---|
| **Wan 2.2 TI2V-5B** Q8 (on disk, 5.0 GB) | Apache-2.0 | 5.0 GB + 6.3 GB umt5 encoder + 1.3 GB VAE | ComfyUI | T7+ resident, T5 by exclusive lease (UNMEASURED here; the VAE decode hang in `local_video.py:44-48` is unresolved, **TREE**) | the permissive one |
| Wan 2.2 T2V-A14B two-expert Q3 (on disk, 2 × 6.7 GB) | Apache-2.0 | 13.4 GB | ComfyUI | T8 | quality; heavy |
| LTX-2 / 2.5 | **revenue-capped commercial licence (flag)** | 16 GB VRAM minimum | ComfyUI | T7+ | native audio-video; by choice |

### 4.12 Music generation

| Candidate | Licence | Size | Runtime | Tier | Note |
|---|---|---|---|---|---|
| **ACE-Step 1.5** | MIT | ~9 GB; XL needs 12 GB VRAM with offload | PyTorch | T5 by exclusive lease, T7 resident | the most capable permissive one (**PUBLISHED**) |
| DiffRhythm 2 | Apache-2.0 | ~4 GB | PyTorch | T5 by lease | full-length songs, lighter |
| HeartMuLa-oss-3B | Apache-2.0 | ~14 GB | PyTorch | T7+ | lyrics control |
| Stable Audio Open 1.0 | **Stability community licence, gated (flag)** | 14 GB | PyTorch | | by choice |
| MusicGen medium | **CC-BY-NC (non-commercial; excluded)** | 33 GB repo | PyTorch | | by choice |

There is no local music backend in the tree today (`model-soup.md` §1); the advisor lists the role as "not yet wired" until one exists, and never claims it is.

*Generative-media engineer:* "Every one of these takes the whole card on T5. The advisor must plan them as exclusive leases that evict the brain, say so, and give the user the turn-taking cost in seconds: the brain's cold reload (about 20 s here, **TREE** arbiter comments) plus the model's own load."

---

## 5. The solver: fit, co-residency, turns

### 5.1 Budgets

From the profile and the floor document's reserves:

    vram_budget = vram_total − display_reserve − foreign_occupancy     (residency_policy R3, TREE)
    ram_budget  = ram_total − os_reserve − friday_footprint             (R1, R2)
    disk_budget = disk_free − DISK_FLOOR_MIB                            (R8)

Apple: `vram_budget = 0.75 × unified − friday_footprint`, `ram_budget` is the remainder.

### 5.2 Placement

Seats are placed in priority order, each as *pinned* (resident) or *leased* (takes turns), the two states `residency_policy.plan()` already models (**TREE**, `LEASED`, `_place(..., "pinned"|"leased")`):

1. **Brain** pinned, at the tier's context. Non-negotiable; if it does not fit, the machine is T0 and the advisor stops with the floor document's message.
2. **System one** pinned if the remaining budget holds it beside the brain (on CPU tiers in RAM; on GPU tiers in VRAM); otherwise on CPU RAM.
3. **Speech out, speech in, embeddings, Laya** pinned on CPU RAM by default (they are small and latency-bound, and a CPU seat survives every GPU lease, which is R10's intent, **TREE** `residency_policy.py:249`). On T6 and up speech and embeddings move to VRAM if the budget holds them after 1 and 2.
4. **Deep reasoner** pinned on T7+, leased on T5 and T6 (evicts the brain, restores it after), Bonsai 2 in `xhigh` on T1 to T4.
5. **Image, video, music** exclusive leases on T5 to T7 (the `exclusive: True` shape the policy already emits for image, **TREE** `:945`), pinned only on T8 or on a second GPU.
6. **Needle** pending D-Needle.

Every placement is then handed to `residency_policy.plan(profile, entries)` as a dry run; a refusal carries the rule id and the numbers and is shown on the screen, not swallowed.

### 5.3 The turn-taking cost, stated

For every leased seat the screen shows "takes turns with the brain: about *N* seconds to switch", where *N* is the measured cold-load of the evicted seat plus the leased one (from the records once benchmarked; ESTIMATE from file size ÷ measured disk read rate before that).

---

## 6. Licences, telemetry, and the egress test

### 6.1 Licence classes

Read from `cardData.license` and `gated` (**API**), then classified:

| Class | Examples | In recommendations? |
|---|---|---|
| **permissive** | Apache-2.0, MIT, BSD | yes |
| **attribution** | CC-BY-4.0 (Parakeet) | yes, with the attribution line shown |
| **restricted** | Gemma terms, OpenRAIL++-M, Stability community (revenue cap), LTX (revenue cap), any `gated: manual` or `auto` | **D2**: by request only (recommended default) or with a visible flag |
| **non-commercial** | CC-BY-NC (MusicGen), FLUX.1-dev | never recommended; installable by choice with the flag |
| **unknown** | no licence field (Needle weights, Moonshine) | never recommended until reviewed |

The class is shown on every row as a word, not an icon, and "Why?" opens the licence text's URL.

### 6.2 Telemetry rule, and the Needle answer

The owner's rule is zero telemetry (A3), for Friday and for anything Friday installs. Every candidate is classed: **none** (an inert weight file loaded by Friday's own runtime, which is every GGUF, ONNX and safetensors entry above), **opt-out** (a binary with a documented switch, Needle), **opt-in**, or **unknown**. Only **none** is installable without further work.

**Proposed answer to the pending Needle decision (D-Needle):** Needle is admitted to the stack only if all of the following hold, each with a test:

1. `NEEDLE_TELEMETRY=0` and `DO_NOT_TRACK=1` are set in the process environment by Friday, never inherited.
2. The engine process is blocked at the operating-system firewall: on Windows an outbound `netsh advfirewall firewall add rule ... program=<engine path> dir=out action=block`; on macOS a `pf` anchor or the application firewall's block; on Linux an `nftables` rule keyed on the engine's uid or cgroup. Friday writes the rule at install, verifies it exists at every start, and refuses to start the engine if it does not.
3. An **egress test** runs at install and at every Friday update: the engine is started in a monitored session (ETW `Microsoft-Windows-TCPIP` on Windows, `nettop` on macOS, a network namespace on Linux) with a real tool call, and any packet from its process to any destination fails the test. A failed test removes Needle from the seat and tells the user why.
4. The weights' licence is confirmed in writing (the card has none).

If 2 or 3 cannot be implemented on a platform, Needle is **excluded** on that platform. The runtime's 14 MB and its speed do not buy an exception; the value it adds is a few hundred milliseconds ahead of the brain, and the brain can do the job.

*Privacy reviewer:* "A closed binary with telemetry on by default is a phone-home you cannot read. Blocking it is the floor; proving the block is the bar." *Inference engineer:* "Agreed, and note the alternative: Ternary Bonsai 1.7B as system one does the same routing job in the open, at about 100 ms on a CPU. Needle's case is the microcontroller, not the desktop."

### 6.3 The egress test for the advisor itself

Reuse of `tests/unit/test_update_check.py::test_outbound_request_carries_nothing_identifying` against the advisor's single fetch seam: the reconstructed request is scanned for every field of the hardware profile, the install id, the version, and any header beyond the library default. Plus the recorder in `services/egress_gate.py`'s test fixtures for a live run with the profile loaded: zero bytes of profile in any outbound payload.

---

## 7. The screen

Brand: the Settings token set in `index.html` (`--st-accent #00d4ff`, `--st-ok #00ff80`, `--st-warn #f59e0b`, `--st-danger #ff6b8a`, Inter and JetBrains Mono on black) and amendment A5's one-brand rule. Prototype: [`prototypes/model-soup-advisor.html`](prototypes/model-soup-advisor.html), clickable, screenshots reviewed on 2026-09-30 at desktop and phone widths.

Layout, top to bottom:

1. **This computer.** One line: "RTX 4070, 12 GB · 32 GB RAM · i7-10700F · 6 GB free on C:" and the tier name in words ("a 12 GB graphics card: the reference tier"). "Show me the numbers" expands the profile and every budget.
2. **The soup.** One row per role: role name in words ("Brain", "Fast responder", "Decision classifier"...), the pick, size, **measured or estimated** speed with the word, licence class, telemetry class, and placement ("stays loaded" / "takes turns, about 25 s to switch" / "on the processor"). Rows that are pending a decision (Needle) or unwired (music) say so in the same place a number would be.
3. **Warnings, in words.** Below the floor, a restricted licence, a lease that evicts the brain, an unmeasured row: each a sentence, none an icon.
4. **Two buttons and a door.** "Install this soup" (one click; downloads in the background; Friday stays usable, north star §8.7). "Change a pick" opens the per-role list with every candidate, including the flagged ones and "Any model: paste a Hugging Face id or a file path". "Not now."
5. **Progress and receipts.** Each model: downloading (with size and rate), verifying (sha256), loading, benchmarking (the number appears), gate (score of 10), seated. Every step's result is a receipt in the run log the user can open. Nothing says "done" before the daemon lists the model and the bench has a number (`model_setup`'s honesty rule, **TREE**).

Phone width: the table becomes cards, one per role; the two buttons pin to the bottom.

---

## 8. Voice

Per `docs/reference/voice-tool-contract.md` (**TREE**): one tool, declared in `_VOICE_LIVE_TOOLS`, routed through `_governed`, ring 1 in `governance/action_gate.py` because it reaches nothing outside this machine (the catalogue read is anonymous and cached; the tool itself never triggers a download).

```python
("model_soup_advise",
 "Profile this computer and say which local models it can run and how. "
 "Speak one short sentence per role that would change, with the speed as a "
 "plain number, and end by asking whether to install. Do NOT install; "
 "installing needs the user's yes, which raises the usual card. If a row is "
 "estimated rather than measured, say 'about'.",
 {}, [])
```

The install itself raises an approval card (it changes the machine and downloads 6 to 30 GB), decided by voice through the existing spoken-decision path, never a second way to say yes. Below the floor the spoken message is the one in the floor document §6.2.

---

## 9. Staying current

- **Refresh:** the catalogue read on the user's schedule; a changed licence, a new Bonsai release, a new fork build, or a candidate that now fits a freed budget produces a *proposal* in the tray. Nothing installs itself (§6.4 of the north star, and A7's guarded-deploy spirit applied to models).
- **Re-benchmark:** after a driver update or a hardware change (profile id changes, **TREE** `_profile_id`), the MEASURED numbers are marked stale and re-run on the next idle window with the user's consent.
- **Any model you want:** the per-role list always ends with a free entry. A user-supplied model is registered through `model_store.register` (**TREE**), benchmarked and gated the same way, and never refused for its score.

---

## 10. Decisions that are the owner's

- **D-Needle.** Adopt §6.2 (include only behind a proven firewall block and egress test; exclude where that cannot be proven), or exclude Needle outright. Recommendation: **§6.2**, because the block is testable and the fallback (Bonsai 1.7B as system one) exists.
- **D2. Restricted licences.** Recommend flagged-licence models by default with the flag, or only when the user opens "Change a pick". Recommendation: **only on request**; the default soup is all permissive or attribution.
- **D3. Pre-download during onboarding.** May the installer start the 6 GB brain download while the user is still reading the plan, to save minutes? Recommendation: **yes, but only after the user has seen the size and the one line "downloading Bonsai 2, 6 GB, from Hugging Face"**, cancellable, with nothing else downloaded until they click.

---

## 11. Build plan and effort

Engineer-days for one person, tests included. Items 1 to 3 of the floor document's plan are prerequisites.

| # | Item | Effort |
|---|---|---|
| 1 | Shortlist file `resources/model_soup_shortlist.json` from §4, with the review script that refreshes licence/gating/size from the API and diffs | 2 d |
| 2 | Catalogue fetch seam with the nothing-identifying test (§3, §6.3) and the 24 h cache | 1 d |
| 3 | Solver: budgets, placement, dry-run through `residency_policy.plan`, turn-taking cost (§5) | 3 d |
| 4 | Result screen in `index.html` and `ui_parts/app.html` from the prototype; "Show me the numbers"; per-role change list; any-model entry (§7) | 3 d |
| 5 | One-click install pipeline: download with sha256, register, load, bench, gate-as-recommendation, seat; receipts (§7.5) | 3 d |
| 6 | Voice tool and its parity tests (§8) | 1 d |
| 7 | Refresh schedule, stale-benchmark marking, tray proposal (§9) | 1 d |
| 8 | Needle admission: env, firewall rule per OS, egress test harness; or the exclusion path (§6.2, after D-Needle) | 3 d |
| 9 | Music backend wiring (ACE-Step or DiffRhythm 2 through a `local_music.py` matching `local_image.py`) | 3 d, separate piece |
| | **Total** | **17 days + 3 for music** |

Phasing: 1 to 3 (the advisor answers, no install) ship first and can be reviewed against this document alone; 4 to 6 are the product; 7 to 9 follow. All of it goes through the program lead's gauntlet.

---

## 12. Synthesis against the north star

| North star | This document |
|---|---|
| §8.7: recommend by measured device fit; show memory, speed, context, tool calling, vision, disk, measured-versus-estimated; downloads in the background with Friday usable | §7 shows every field with the word "measured" or "about"; §7.4 installs in the background |
| §12.1 seats not identities | twelve roles, each a seat; the user sees the role in words and may inspect the model |
| §12.7 capability registry: licence, privacy characteristics, evaluation date, measured versus estimated | the shortlist entry and the model record carry all four |
| §12.4 and A6: both paths, user picks | every role's screen has the cloud door where one exists; T0 gets the floor message |
| §6.4 no silent fallback; §6.5 done is verified | §7.5 receipts; `model_setup`'s rule |
| A3 no telemetry | §2, §3.2, §6.2, §6.3: profile never leaves; anonymous catalogue read with a test; phone-home components blocked and proven or excluded |
| A4 voice-first parity | §8, one tool, same governance path |
| A5 brand integrity | §7, the shared tokens |
| §6.8 the interface must prove | "Show me the numbers" and the receipts |
| GAP-MATRIX NS-26.23-1 (no telemetry, shipped) | extended from Friday's own traffic to everything Friday installs |
