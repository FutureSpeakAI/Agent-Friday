# Bonsai 2 on every machine: the tier table and the application floor

> **Status:** active, specification with measurements
> **Written:** 2026-09-30
> **Implementation:** none. This document and [`bonsai2-tiers.json`](bonsai2-tiers.json) (version 2: download manifest with sha256, per-backend release assets, the structured pick, the detection commands) are inputs to the installer section of the first-run and onboarding master spec and to [`model-soup-advisor.md`](model-soup-advisor.md).
> **Owner's direction (verbatim, 2026-09-29):** "bonsai2 is our standard now, not Gemma. not Qwen..only bonsai2. [...] whatever the lowest range is, that is applications floor. All the other local models are out for suggested installation on first setup. We should support any models the user wishes."
> **Method:** STORM. Six simulated experts argued each section against the tree, the publisher's card and the September logs on the reference machine: an on-device inference engineer, a llama.cpp maintainer, an Apple Silicon ML engineer, a low-end-hardware user, a privacy reviewer and a generative-media engineer. Where they disagreed the disagreement is recorded, not smoothed.
> **Provenance tags.** **MEASURED-2026-09-18** and **MEASURED-2026-09-30** were measured on the reference machine (RTX 4070 12 GB, i7-10700F, 32 GB DDR4, Windows 11). **PUBLISHED** is from a cited public source. **TREE** was read from the code at `02035ba6`. **ESTIMATE** is arithmetic on tagged inputs with the formula shown. **UNMEASURED** means no evidence exists yet; the installer must not promise a number for an UNMEASURED row until it has benchmarked on that device (§7.4).

---

## 0. The answer in one page

1. **Bonsai 2 ships at one size.** Ternary Bonsai 2 is the 27B only: PrismML's ternary compression of Qwen3.8-27B, Apache-2.0, 5.95 GB in the `PTQ1_0` packing and 7.21 GB in `PQ2_0` (**PUBLISHED**, [model card](https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf); [publisher's download table](https://docs.prismml.com/download/models)). The smaller Bonsai files in the catalogue (1.7B, 4B, 8B) are the earlier *Bonsai* and *Ternary Bonsai* generations on Qwen3 bases. The 4B on this machine is one of those (**MEASURED-2026-09-30**, GGUF metadata: architecture `qwen3`, basename `Ternary-Bonsai`, 32K YaRN context). So "the smallest viable Bonsai 2" is the 27B, and the floor is the lowest machine that runs a 6 GB model well.

2. **The application floor is 16 GB of RAM with AVX2; a graphics card of 6 GB or more lifts a machine into a GPU tier.** On a 16 GB Windows machine with no GPU: 6,144 MiB OS reserve + about 1,500 MiB for Friday itself + 5,672 MiB of weights + 512 MiB of cache at 8K context + about 400 MiB compute buffer is 14,228 MiB of 16,384 (**ESTIMATE** from **TREE** reserves and the **MEASURED** file size). An 8 GB machine cannot hold the weights beside the operating system. A 12 GB machine holds them only with nothing else open, and one browser tab pushes the weights into the page file, where decode drops below one token a second. The floor is therefore 16 GB, which is what the README already publishes for RAM. What changes is the GPU line: "an NVIDIA graphics card" is no longer required, and the Gemma table is retired (§8).

3. **At the floor the binding constraint is prompt processing, not tokens per second.** The publisher's community kernels reach 3.9 to 8.3 tokens a second decoding on a laptop CPU, and about 16 tokens a second reading the prompt (**PUBLISHED**, [Bonsai-demo issue #196](https://github.com/PrismML-Eng/Bonsai-demo/issues/196)). Friday's full tool registry is about 8.6K tokens before the user says a word (**TREE**, `services/model_seat_gate.py` `GATE_NUM_CTX` comment). At 16 tokens a second that first turn takes nine minutes. The CPU profile in §5.3 fixes this by sending the twelve tools the turn needs (about 1,700 tokens, **TREE** `services/tool_selector.py`) and keeping the prompt cache warm, which brings the once-per-session cost to about two minutes and the per-turn cost to seconds. Without that profile the floor is not honest.

4. **The runtime is the PrismML fork of llama.cpp, and only that.** Stock llama.cpp refuses the two Bonsai 2 tensor types outright, Ollama's parser returns a type size of zero for them, and LM Studio rejects them (**PUBLISHED**, [llama.cpp issue #29058](https://github.com/ggml-org/llama.cpp/issues/29058); [atomic.chat guide](https://atomic.chat/blog/guides/how-to-run-bonsai-2-locally)). The fork publishes release binaries for Windows (CPU, CUDA, Vulkan, HIP), Linux (CPU, CUDA 12.4 and 12.8, Vulkan, ROCm), macOS (Apple Silicon and Intel) and iOS (**PUBLISHED**, [publisher's llama.cpp guide](https://docs.prismml.com/run/llamacpp)). The build installed here is the fork at build 10685 (**MEASURED-2026-09-30**, `llama-server.exe --version`). §4 has the backend matrix.

5. **On the reference machine the seat is measured, not estimated.** Bonsai 2 27B `PTQ1_0` fully offloaded on the RTX 4070 decodes at 30 to 36 tokens a second at 49K to 64K context with q8_0 cache, and reads short prompts at 95 to 100 tokens a second (**MEASURED-2026-09-18**, `run64k.log`, `seat.log`). Above 96K context the cache no longer fits, layers spill to the host and decode falls to 2 to 5 tokens a second, reaching 0.48 at 262K (**MEASURED-2026-09-18**, `try96k.log`, `try262k.log`). That cliff is the reason the served context is 49,152 and not the model's 262,144 (`start-bonsai.bat`).

6. **First setup suggests only Bonsai 2.** Gemma, Qwen and every other local family leave the first-setup suggestion list and stay fully supported by explicit choice. Nothing refuses a user's chosen model; the seat gate is a deliberate no-op by maintainer decision (**TREE**, `routes/core_routes.py` `_check_local_model_seat_gate`, `services/model_seat_gate.py` line 347). What the gate does now is inform the *recommendation*, never block the *choice* (§7.4).

7. **Three decisions are the owner's** (§10): what a machine below the floor is offered locally; whether the floor is published as 16 GB CPU-only or as "8 GB graphics card recommended, 16 GB CPU-only supported"; and whether FridayWeaver-2 training on Bonsai 2 starts now or after the advisor ships.

---

## 1. What Bonsai is, exactly

### 1.1 The publisher and the family

PrismML publishes under `prism-ml` on Hugging Face and `PrismML-Eng` on GitHub. The organisation lists 36 repositories (**PUBLISHED**, Hugging Face API, 2026-09-30). Three generations of language model and one of image model:

| Generation | Sizes | Bits per weight | Base | Repos | Downloads (largest) |
|---|---|---|---|---|---|
| **Ternary Bonsai 2** (2026-09-16) | **27B only** | 1.75 (`PTQ1_0`), 2.13 (`PQ2_0`), 2.25 (MLX) | Qwen3.8-27B | `Ternary-Bonsai-2-27B-gguf`, `-mlx-2bit`, `-gguf-dev` | 3.58 M |
| Ternary Bonsai (2026-04 to 07) | 1.7B, 4B, 8B, 27B | 1.58 nominal, 2.125 stored | Qwen3 family | `Ternary-Bonsai-{size}-gguf`, `-mlx-2bit`, `-unpacked`, `-AWQ-4bit` | 1.09 M (27B MLX) |
| Bonsai 1-bit (2026-03 to 07) | 1.7B, 4B, 8B, 27B | 1.0 (`Q1_0`) | Qwen3 family | `Bonsai-{size}-gguf`, `-mlx-1bit` | 1.10 M (27B MLX) |
| Bonsai Image (2026-05) | 4B | 1-bit and ternary | (diffusion) | `bonsai-image-{binary,ternary}-4B-{gemlite,mlx,unpacked}` | 1.1 K |

All of it is Apache-2.0 (**PUBLISHED**, model cards; **MEASURED-2026-09-30**, `general.license` in the 4B and mmproj GGUF headers).

*Low-end user:* "So there is no small Bonsai 2 for my laptop." *Inference engineer:* "Correct. There is a small Bonsai. It is a different, older, weaker model, and the doc must say so every time it is offered." That is decision D1.

### 1.2 The 27B, as the file on disk describes itself

**MEASURED-2026-09-30**, read from the GGUF header of `~/.friday/runtime/models/gguf/Ternary-Bonsai-2-27B-PTQ1_0.gguf` (5,672 MiB):

| Field | Value | What it means for Friday |
|---|---|---|
| `general.architecture` | `qwen35` | the hybrid architecture: 64 blocks, gated DeltaNet linear attention with full attention every 4th block (`qwen35.full_attention_interval = 4`) |
| `qwen35.context_length` | 262,144 | the training window; never the served window (§3.2) |
| `qwen35.attention.head_count_kv` | 4, key/value length 256 | why the KV cache is cheap: only 16 full-attention layers hold one |
| tensor types | 402 × `ptq1_0`, 96 × `bf16`, 353 × `f32` | the ternary matrices, the bf16 embeddings and the f32 norms; the bf16 embedding table is why the file is 5.95 GB and not 5.8 |
| `prism.hadamard.*` | version 1, block 1024, normalized Sylvester-Walsh-Hadamard, explicit signs on 401 weights | the activation-side transform a stock build cannot apply; the reason the fork is the engine |
| `general.sampling.*` | temp 1.0, top-p 0.95, top-k 20 | the publisher's thinking-mode defaults, carried in the file |
| chat template | Qwen3-VL style with image and video counters | native tool-call format; vision through the companion mmproj |

The companion `Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf` (600 MiB) is a 27-block SigLIP-class vision tower with the `qwen3vl_merger` projector, Apache-2.0 (**MEASURED-2026-09-30**). The publisher's card says it is loaded only for image turns and offloaded otherwise (**PUBLISHED**).

### 1.3 The packings

| Packing | GGML type | Bits/weight | Size | When it wins | Provenance |
|---|---|---|---|---|---|
| `PTQ1_0` "dense trits" | 143 | 1.75 | 5.95 GB | wherever memory binds; faster decode on Ada-class cards and the L4 because it moves 17% less data per step | PUBLISHED card |
| `PQ2_0` "2-bit slots" | 142 | 2.13 | 7.21 GB | Ampere, Hopper, Blackwell and Apple M5, where bandwidth is abundant and unpacking trits costs arithmetic | PUBLISHED card |
| `Q2_0_g64` | mainline `Q2_0` | 2.25 | ~7.6 GB | loads on **stock** llama.cpp; the escape hatch for a platform the fork has no binary for | PUBLISHED [formats page](https://docs.prismml.com/download/formats) |
| MLX 2-bit | (MLX) | 2.25 | 8.49 GB | Apple Silicon through `mlx-lm`; best raw decode on Apple | PUBLISHED [MLX guide](https://docs.prismml.com/run/mlx) |

The repository also carries the F16 reference weights (51,316 MiB) and a BF16 vision tower (888 MiB), neither of which is served (**API**, 2026-09-30). The download manifest with byte counts and sha256 for every file the installer may fetch is in the JSON under `model_family.*.manifest`; the `PTQ1_0` entry's byte count matches the file on this machine.

Two publisher warnings that the installer must encode: a `Q2_0` file on a stock build "loads silently and outputs gibberish" because the Hadamard transform is missing (**PUBLISHED**, llama.cpp guide); and `PQ2_0` on CPU has crashed in the repack step on some builds, exit code 139 in `ggml_backend_cpu_repack_buffer_set_tensor`, worked around with `--no-repack` or by using `PTQ1_0` (**PUBLISHED**, atomic.chat guide). Friday's CPU tiers use `PTQ1_0` for both reasons.

### 1.4 Quality, from the publisher

Thinking mode, 14 benchmarks, against the FP16 base at 86.32 (**PUBLISHED**, model card):

| Model | Footprint | Score | Retained |
|---|---|---|---|
| FP16 baseline | 54 GB | 86.32 | 100% |
| **Bonsai 2 27B `PTQ1_0`** | **5.95 GB** | **84.78** | **98.2%** |
| Unsloth `UD-Q4_K_XL` | 17.6 GB | 85.18 | 98.7% |
| `IQ2_XXS` | 7.27 GB | 72.59 | 84.1% |

By category: math 96.57 versus 97.06, coding 89.42 versus 89.07, agentic tool calling 74.92 versus 76.74. The tool-calling gap of 1.8 points is the one Friday cares about, and it is the axis the conformance gate measures on the device (§7.4).

*Privacy reviewer:* "These are the publisher's numbers on the publisher's benchmarks. Friday repeats none of them to the user as a promise; it shows what the gate measured here."

### 1.5 The runtime

The fork is `PrismML-Eng/llama.cpp`, branch `prism`, developed as `prism-v7`, tracking mainline; `prism-v6` is a stale mid-migration snapshot not to build from (**PUBLISHED**, [fork README mirror](https://github.com/snailium/bonsai2-mainline)). Releases from `prism-b10658` cover the platforms in §0.4; the latest is `prism-b10743-adfffbe` (2026-09-25) with one asset per platform and backend: Windows CPU x64/arm64, CUDA 12.4 and 13.3 (plus a `cudart` bundle), HIP Radeon, Vulkan; Linux CPU x64/arm64, CUDA 12.4/12.8/13.3, ROCm 7.2, Vulkan x64/arm64; macOS arm64 (Metal, plus a KleidiAI variant) and x64 (**API**, GitHub releases, 2026-09-30). The JSON's `runtime.release_assets` maps each (OS, backend) to its asset name and size so the installer downloads exactly one. The installed build is `0.2.0-dev (build 10685, commit 7dffb158d)`, MSVC, CUDA 12 DLLs (**MEASURED-2026-09-30**). The other two llama.cpp folders under `~/.friday/runtime/` are stock builds (`llama.cpp`, build 10415, Clang) and cannot serve these files.

Upstream status: mainline llama.cpp has merged the older `Q1_0` and `Q2_0_g64` formats on CPU, Metal, CUDA and Vulkan; the Bonsai 2 types are requested in issue #29058 and remain unsupported because the activation-side Walsh-Hadamard transform is not upstream (**PUBLISHED**, formats page and the issue). *llama.cpp maintainer:* "Plan on the fork for the life of this spec. If mainline lands the types, the installer's runtime check (§7.2) starts passing on stock builds and nothing else has to change."

---

## 2. Hardware on the reference machine, and what was measured

| | Value | Tag |
|---|---|---|
| CPU | Intel i7-10700F, 8 cores / 16 threads, AVX2 (no AVX-512) | MEASURED-2026-09-30 |
| RAM | 32,620 MiB DDR4, bandwidth class estimated 42.7 GB/s by the SMBIOS heuristic in `hardware_profile.detect_memory_bandwidth` | TREE + profile cache |
| GPU | RTX 4070, 12,282 MiB, `compute_class` `consumer-fp8`, driver 616.92 | MEASURED-2026-09-30 |
| Display reserve | Windows minimum 2,560 MiB (`MIN_DISPLAY_RESERVE_MIB`); the live counter read 114,708 MiB and was rejected as impossible, cached floor 1,183 MiB used | TREE, profile cache |
| Disk C: | **6 GB free** on 2026-09-30 (7.8 GB the day before); the residency floor is 10 GiB (`residency_policy.DISK_FLOOR_MIB`). No new weights can be downloaded here until something is deleted | MEASURED-2026-09-30 |
| Runtime | `~/.friday/runtime/llama.cpp-bonsai`, fork build 10685 | MEASURED-2026-09-30 |
| Serving recipe | `start-bonsai.bat`: `-ngl 99 -fa on -c 49152 -np 2 --kv-unified -b 4096 -ub 2048 --cache-type-k q8_0 --cache-type-v q8_0 --jinja --mmproj ...` on port 8099 | TREE (runtime folder) |

### 2.1 GPU, full offload (the tier-5 reference)

From the September 18 context sweep in `~/.friday/runtime/llama.cpp-bonsai/*.log` (**MEASURED-2026-09-18**):

| Log | Context | Slots / KV | Decode tok/s | Prompt tok/s | Note |
|---|---|---|---|---|---|
| `try64k.log` | 65,536 | 1, f16 | 25.9 to 34.0 | 101.9 (53 tok) | fits; first run after load |
| `run64k.log` | 65,536 | 1, f16 | 29.8 to 30.5 | 94.5 | one 2.88 tok/s outlier while the desktop was busy |
| `seat.log` | 49,152 | 2 unified, q8_0 | **35.9** | **97.8** | the shipped configuration |
| `start-bonsai.bat` comment | 16K prompt | | | 370 → **467** with `-ub 2048` | prompt processing on a real turn |
| `try96k.log` | 98,304 | 1 | **2.15 to 5.68** | 4.2 to 73 | spilled: "failed to fit params to free device memory" |
| `try131k.log` | 131,072 | 4 unified | prompt 1.7 to 2.4 | | unusable |
| `try262k.log` | 262,144 | 1 | **0.48 to 0.52** | 2.8 | the training window is not a serving window |
| `try_mmprojcpu.log` | 131,072, mmproj on CPU | 4 | 0.99 to 2.04 | 57 to 67 | vision tower on CPU does not rescue a spilled seat |

The `start-bonsai.bat` header records why 49K: at 64K the card had 495 MiB free "that Chrome, Claude, ChatGPT, Wallpaper Engine and the NVIDIA overlay also draw from", and at `-np 1` a 4-token background probe queued behind a chat turn for 7 to 20 s. Both are the desktop's problem, not the model's, and both are what the tier table's "headroom for the desktop" column pays for.

### 2.2 CPU-only: attempted and not obtained

On 2026-09-30 a CPU-only `llama-bench` of the Ternary Bonsai 4B (`-dev none -ngl 0`, GPU hidden with `CUDA_VISIBLE_DEVICES=""`) ran for more than ten minutes without producing a row, holding 3.6 GB of working set, while the machine had 2 to 5 GB of RAM available because other sessions' test processes held the rest. It was killed. The 27B was not attempted under those conditions: its 5.7 GB of weights would have paged, and a paged number is worse than no number. **Every CPU-only figure in §3 is therefore ESTIMATE or PUBLISHED, and the row says so.** The measurement recipe is in §9 so it can be run on a quiet machine, and the installer runs its own on every install (§7.4), which is the number that actually matters.

---

## 3. The tier table

### 3.1 How the numbers were made

Decode on a ternary model is memory-bandwidth-bound: each generated token streams every matrix once. `PTQ1_0` moves about 5.5 GB per token (the 5.95 GB file less the embedding table, of which a token reads one row). The planning estimate is

    decode tok/s ≈ 0.7 × bandwidth (GB/s) ÷ 5.5

where 0.7 is the fraction of theoretical bandwidth the publisher's community AVX2 kernels reached on a single-channel DDR5 laptop: 8.3 tok/s decode, up from 3.9 with the generic kernel, on an i7-13620H (**PUBLISHED**, issue #196). For partial offload (T4a) the estimate is `1 / (f_gpu / gpu_tok_s + f_cpu / cpu_tok_s)`, with `f_gpu` the fraction of weight bytes on the card, and the layer count is `floor(64 × (usable VRAM − KV − compute) ÷ 5,671)`, clamped so that fewer than 24 layers means the CPU tier instead. GPU tiers are interpolated by memory bandwidth between the published card (L4 32.1, RTX 4090 91.1, RTX 5090 120.5 tok/s for `PTQ1_0`) and the measured RTX 4070. Prompt processing on CPU is about twice decode (16 versus 8.3 in the same issue); on GPU it is the published PP512 figure. Seat memory is weights + KV cache at the served context (64 KiB per token at f16, half at q8_0; **PUBLISHED**, atomic.chat guide, consistent with the header in §1.2) + compute buffer (about 400 MiB at `-ub 512`, about 1,200 at `-ub 2048`) + 630 MiB mmproj only when vision is on.

The machine-readable form of this table, with every tag, is [`bonsai2-tiers.json`](bonsai2-tiers.json).

### 3.2 The table

| Tier | Hardware | Bonsai 2 configuration | Context | Decode tok/s | Prompt tok/s | Evidence | Verdict |
|---|---|---|---|---|---|---|---|
| **T0 below floor** | 8 GB RAM; or 12 GB with no GPU; or no AVX2; or 32-bit OS | does not fit (weights 5,672 + OS reserve 6,144 > 8,192 before Friday runs) | | | | ESTIMATE from TREE reserves | cloud path; D1 lesser local |
| **T1 FLOOR: CPU-only 16 GB** | 16 GB dual-channel DDR4 or single-channel DDR5, AVX2, 4 to 8 cores | `PTQ1_0`, `-ngl 0`, f16 KV, one slot, CPU profile (§5.3) | **8,192** | **3 to 6** | 8 to 16 | ESTIMATE 0.7×45/5.5 = 5.7; bracketed by PUBLISHED 3.9 to 8.3; **UNMEASURED here** | core loop viable with the CPU profile; long documents and deep-reasoner turns are cloud by choice |
| **T2 CPU-only 32 GB fast** | 32 GB DDR5 dual-channel (80 to 90 GB/s), AVX-512 or AVX-VNNI, 8+ cores; Strix Halo via Vulkan | `PTQ1_0`, `-ngl 0` or iGPU | 32,768 | 8 to 12 | 20 to 40 | ESTIMATE 0.7×85/5.5 = 10.8; UNMEASURED | comfortable; system-one seat co-resides in RAM |
| **T3a Apple 16 GB** | M1 to M4 base, 16 GB unified (12 GB usable) | `PTQ1_0` via fork Metal, or MLX 2-bit when memory allows | 16,384 | 10 to 16 | 150 to 300 | ESTIMATE from PUBLISHED M4 Pro 18.0 by bandwidth ratio; UNMEASURED | viable; one API on every platform argues for the fork over MLX |
| **T3b Apple Pro/Max 24 to 128 GB** | M4 Pro, M5 Pro, M5 Max, M3 Ultra | `PQ2_0` or MLX 2-bit | 65,536 to 262,144 | 18.0 (M4 Pro), 28.1 (M5 Pro), 47.0 (M5 Max) | 387 (M5 Pro), 765 (M5 Max) | PUBLISHED card | whole soup co-resident from 48 GB |
| **T4a 6 to 8 GB GPU on Windows, 6 GB on Linux: partial offload** | RTX 3050 6G, 4050 laptop 6G, 4060 8G, 5060 8G, RX 7600 8G; 16 GB RAM | `PTQ1_0`, q8_0 KV, `-ngl 33` (6 GB Windows: 3,584 usable) or `-ngl 56` (8 GB Windows: 5,632 usable; 6 GB Linux); the rest of the weights in RAM, so the T1 RAM floor still applies | 8,192 | 8 to 10 (33 layers), 14 to 20 (56 layers) | 100 to 300 | ESTIMATE by the partial-offload formula (§3.1); UNMEASURED | better than CPU-only; an 8 GB card on Windows cannot hold the 5,671 MiB of weights under the 2,560 MiB display reserve, which the earlier table missed |
| **T4 8 GB GPU on Linux, 10 GB on Windows: full offload** | RTX 4060 / 5060 on Linux (7,680 usable); RTX 3080 10G on Windows | `PTQ1_0`, `-ngl 99`, q8_0 KV, one slot, `-ub 512`; mmproj only when vision is turned on | 16,384 | 20 to 32 | 300 to 470 | ESTIMATE bracketed by PUBLISHED L4 32.1 / 467; UNMEASURED; AMD via Vulkan/HIP UNMEASURED | good core loop; vision on demand costs context |
| **T5 12 GB GPU (reference)** | RTX 4070 / 3060 12G / 5070 | `PTQ1_0`, `-ngl 99`, q8_0 KV, 2 unified slots, mmproj loaded, `-ub 2048` | **49,152** (64K fits with 495 MiB spare) | **30 to 36** | **95 to 100** short, **467** on a 16K prompt | **MEASURED-2026-09-18** | the reference; everything above is extrapolated from here and the card |
| **T6 16 GB GPU** | RTX 4070 Ti Super / 4080 / 5080, RX 7800 XT / 9070 XT | `PTQ1_0` (Ada) or `PQ2_0` (RDNA, Blackwell), q8_0 KV | 131,072 | 45 to 70 | 800 to 1,400 | ESTIMATE between T5 and 4090; UNMEASURED | brain + system one + embeddings + Kokoro co-resident |
| **T7 24 GB GPU** | RTX 3090 / 4090, RX 7900 XTX | `PTQ1_0` on 4090; `PQ2_0` on 3090 and RDNA | 196,608 | 91.1 (4090 `PTQ1_0`), 81.2 (`PQ2_0`) | 1,645 to 3,124 | PUBLISHED card | brain and deep reasoner take turns; image generation co-resides at 64K |
| **T8 32 GB+ / workstation** | RTX 5090, RTX 6000 Ada, dual-GPU | `PQ2_0`, f16 KV | 262,144 | 120.5 to 129.9 | 1,805 to 3,893 | PUBLISHED card (5090) | whole soup co-resident; video on the second card or by turns |

*Apple engineer, on T3a:* "The base M-series parts have 100 to 120 GB/s. Twelve usable gigabytes minus 5.7 of weights leaves room for 16K and the voice models, not more. Put the embeddings on CPU there." *Low-end user, on T1:* "Three tokens a second is slower than I read. Is that 'well enough'?" *Inference engineer:* "For a spoken answer of forty words, yes: fifteen seconds, streamed, and the voice starts on the first sentence. For a page of prose, no, and Friday should say 'this one is faster in the cloud' before it starts, not after."

### 3.3 Context: served versus training

`MAX_SEAT_NUM_CTX` is 131,072 (**TREE**, `residency_arbiter.py:1012`), and the arbiter serves `min(declared, MAX)`. Bonsai 2's declared window is 262,144, so without a per-model cap the arbiter would try 131,072, which on the reference card measured unusable (§2.1). The tier table's context column is the per-model cap the installer writes into the model record's `serve_args` (`_declared_serve_args`, **TREE** `residency_arbiter.py:1039`), so the arbiter never re-derives it. §7.3 has the rule.

---

## 4. Backends: which runtime build, on what

| Backend | Fork support | Friday's use | Evidence |
|---|---|---|---|
| CUDA (NVIDIA, Turing and newer) | release binaries, Windows and Linux; `PTQ1_0` and `PQ2_0` kernels | T4 to T8 | PUBLISHED release matrix; MEASURED T5 |
| Metal (Apple Silicon) | release binaries; native `PQ2_0` and `PTQ1_0` kernels | T3a, T3b | PUBLISHED card ("Metal: native; M4 Pro / M5 Pro / M5 Max") |
| Vulkan (AMD, Intel, NVIDIA without CUDA) | release binaries, Windows and Linux | T4 to T7 on AMD and Intel Arc; iGPUs | PUBLISHED llama.cpp guide; performance **UNMEASURED** |
| HIP / ROCm (AMD) | release binaries, Windows HIP and Linux ROCm; `PQ2_0` "preferred on HIP" | T6, T7 on RDNA 3 | PUBLISHED fork README; **UNMEASURED** |
| SYCL (Intel GPU) | listed in the fork README | not offered until measured | PUBLISHED; **UNMEASURED** |
| CPU x86-64 | AVX2 and AVX-512 paths; community AVX2/AVX-VNNI kernels for Bonsai 2 | T1, T2 | PUBLISHED issue #196; **UNMEASURED here** |
| CPU ARM NEON | macOS, Linux arm64, iOS builds | Apple fallback, Linux arm64 | PUBLISHED release matrix |
| Integrated GPUs (Intel Xe, AMD 780M, Strix Halo) | Vulkan or SYCL builds load; memory is shared with the OS | treated as T1/T2 with better prompt processing | reasoning only; **UNMEASURED** |

`hardware_profile.detect_gpus()` reads `nvidia-smi` only (**TREE**, `hardware_profile.py:196`), so today AMD, Intel and Apple GPUs are invisible to the profiler and their machines fall to the CPU tiers. Detection for those is build item 1 in §11.

---

## 5. The application floor

### 5.1 Definition

**Friday's minimum requirements, to be published:**

- **Memory:** 16 GB of RAM. (A graphics card with 6 GB or more, of any vendor the fork has a build for, lifts a 16 GB machine into a GPU tier: partial offload at 6 to 8 GB on Windows, full offload from 8 GB on Linux and 10 GB on Windows. It does not replace the RAM.)
- **Processor:** x86-64 with AVX2 and at least four physical cores (six recommended), or Apple Silicon.
- **Operating system:** Windows 10 or 11 64-bit; macOS 14 or newer on Apple Silicon; Linux x86-64 or arm64 with a supported GPU driver or none.
- **Disk:** 20 GB free at install (6 GB weights + 0.6 GB vision tower + the 10 GiB residency floor that keeps the machine from filling; voice models add 1.5 GB).
- **Network:** once, to download 6.6 GB. Nothing afterwards is required for the local path.

This is the T1 row. It runs Bonsai 2 27B `PTQ1_0` at 8K context on the CPU with the CPU profile.

### 5.2 Why this line and not another

*Low-end user:* "Why not 12 GB? Windows itself runs in 4." *Inference engineer:* "The reserve is 6,144 MiB because that is what a Windows desktop with a browser open actually holds (**TREE** `OS_RESERVE_MIB`, and the `residency_policy` R1 rule refuses plans under it). At 12,288 MiB total: 6,144 + 1,500 Friday + 5,672 weights is 13,316 before any cache. It pages. The first turn a user sees is one token every few seconds, and the honest thing is to not call that supported." *Privacy reviewer:* "And an unsupported machine gets the cloud path offered plainly, which is A6, not a silent downgrade."

*Apple engineer:* "8 GB Macs are T0 as well: 8 GB unified with 6 usable cannot hold 5.7 GB of weights beside the OS."

### 5.3 The CPU profile that makes the floor honest

A seat on T1 or T2 is served with a profile that the tier record carries, and the router reads:

1. **Trimmed tools.** `tool_selector.select(tools, query, k=12)` (**TREE**, `_DEFAULT_K = 12`) instead of the full registry. About 1,700 tokens instead of 12,800. This module exists and is measured; the profile makes it mandatory on CPU tiers rather than a fitting fallback.
2. **Prompt cache kept warm.** `llama-server` reuses the KV prefix across turns in a slot; the system prompt and tool block are the prefix. One slot, `--cache-reuse 256`, no `--kv-unified` (nothing else shares the seat on CPU).
3. **Thinking off by default** for the core loop: the publisher's `xhigh` reasoning effort is the default in the file (**PUBLISHED** card) and would spend minutes at 4 tok/s. The router sets `reasoning_effort: "medium"` or `enable_thinking: false` per the template; deep-reasoner turns route to the deep reasoner seat or the cloud (A6, user's choice).
4. **Streaming to voice.** The first sentence goes to TTS as soon as it closes; at 4 tok/s a ten-word sentence is 2.5 s to first audio.
5. **Session cost stated once.** "Getting ready takes about two minutes on this computer the first time each day" is the sentence, spoken and shown, with the measured number substituted.

### 5.4 What "well enough for the core loop" means, as a test

The floor is a claim, so it has a test the installer runs (§7.4): one spoken or typed turn that must call one tool and answer, measured after the session's first turn, must finish in under 60 seconds at the floor, with decode at or above 3 tok/s and the conformance gate at 8 of 10 or better. A machine that fails the test is told so in numbers and is offered the cloud path; the tier it was profiled into is corrected in the record.

---

## 6. Below the floor: what fails, and what Friday says

### 6.1 What fails, mechanically

| Shortfall | Mechanism | Symptom without a guard |
|---|---|---|
| RAM below weights + OS reserve | `mmap` pages the weights through the page file every token | decode under 1 tok/s, disk at 100%, the whole desktop stutters |
| No AVX2 | the fork's x86 kernels select the generic path | 5 to 10× slower; on a 2012 CPU under 1 tok/s |
| Stock llama.cpp | tensor types 142/143 rejected at load | "unknown type" refusal, or with a `Q2_0` file, gibberish output |
| GPU below display reserve | `gpu_headroom.check` refuses (**TREE**, `gpu_headroom.py:105`) | a monitor drops off Windows, which the check's own text says |
| Disk under the 10 GiB floor | `residency_policy` R8 refuses the plan | the download is refused before it starts, not after it fills the disk |

### 6.2 What Friday says, and offers

Per A6 ("both paths, and the user picks") and §6.4 of the north star ("no silent fallback"), the installer's message on a T0 machine is a statement of the number that failed and two doors, never a downgrade:

> **This computer has 8 GB of memory. Friday's own brain, Bonsai 2, needs 16.** Friday can still run everything here, with the brain in the cloud: your words go to a provider you choose, under the privacy rules you set, and you see every one of those trips. Or Friday can install a smaller local brain that stays private but is noticeably less capable. Which do you want?
>
> [ Use a cloud brain ] [ Install the smaller local brain ] [ Show me the numbers ]

"Show me the numbers" opens the hardware profile and the arithmetic in §5.2 for this machine. The second button exists only if decision D1 is yes. If D1 is no, the message has one door and says "Friday's local brain does not fit this computer; here is what it would take" with the floor.

The spoken form follows the voice contract: one sentence, then the question, then wait: "This computer doesn't have enough memory for my local brain. I can think in the cloud instead, with your permission each time, or install a smaller local one. Which do you prefer?"

*Privacy reviewer:* "The cloud door must lead to the existing consent flow (`privacy_consent.py`), not a new one, and the capability snapshot it records must say `capable: false` with the measured reason, which is the fix `model-soup.md` §0.1 asked for."

---

## 7. How the installer detects hardware and picks the variant

### 7.1 Profile

`hardware_profile.detect()` (**TREE**, `hardware_profile.py:651`) already returns OS family and version, CPU model with physical cores and threads, RAM total and available, memory bandwidth class from SMBIOS (DDR type, speed, channel count; `method` says heuristic), NVIDIA GPUs with total and live-used VRAM and a `compute_class`, display reserve, and disk free with an optional read-rate measurement. The profile is cached at `~/.friday/runtime/residency/hardware-profile.json` and refreshed on a 60 s memo.

The JSON's `detection.fields` lists every field with the command that reads it per OS, which pick input it feeds, and whether the tree has it today. The installer adds, in this order (build items in §11):

1. **CPU feature flags:** AVX2, AVX-512, AVX-VNNI, AMX on x86; NEON is implied on arm64. Source: `cpuid` via a 30-line ctypes probe on Windows/Linux, `sysctl machdep.cpu` on macOS.
2. **Non-NVIDIA GPUs:** `vulkaninfo --summary` where present, `rocm-smi`, Windows `Get-CimInstance Win32_VideoController` for name and dedicated memory, `system_profiler SPDisplaysDataType` on macOS. Each with `vendor` set and `vram_total_mib`, so the Vulkan and HIP tiers can be planned.
3. **Apple unified memory:** `memory_bandwidth.class == "unified"` is already set (**TREE**, `detect_memory_bandwidth`); the installer adds `usable_mib = 0.75 × total` and plans the seat from that, not from a VRAM figure.
4. **Friday's own footprint:** the server measures its RSS after boot and writes `friday_footprint_mib`, replacing the 1,500 MiB estimate.

### 7.2 Runtime check

Before any weight is downloaded: install the one release asset for the detected OS and backend (`runtime.release_assets` in the JSON), run `llama-server --version` and confirm the build is `prism-b10658` or newer; run `llama-bench --list-devices` and record the backend and device the build sees. A stock build, or a fork build that sees no device on a GPU tier, is a refusal with the sentence that explains it. This is where a future mainline merge would start passing silently, which is fine: the check is on capability, not on the fork's name.

### 7.3 Pick

The pick is a pure function of the profile, in `model_plan` (the pure half; `model_setup` executes and verifies, **TREE**), and it writes one model record with `serve_args`:

```
usable_vram = vram_total − max(display_reserve_min[os], measured_idle_used)
ram_budget  = ram_total − os_reserve[os] − friday_footprint
apple_budget = 0.75 × unified_total − friday_footprint

usable_vram ≥ 28,000            → T8  PQ2_0, -ngl 99, -c 262144, f16 KV
usable_vram ≥ 20,000            → T7  packing by rule, -c 196608, q8_0
usable_vram ≥ 13,000            → T6  packing by rule, -c 131072, q8_0
usable_vram ≥  9,000            → T5  PTQ1_0, -c 49152, -np 2 --kv-unified -b 4096 -ub 2048, q8_0, mmproj
usable_vram ≥  6,300            → T4  PTQ1_0, -ngl 99, -c 16384, q8_0, one slot        (8 GB Linux; 10 GB Windows)
usable_vram ≥  3,000 and RAM floor met → T4a PTQ1_0, -ngl floor(64×(usable−KV−compute)÷5,671), -c 8192, CPU profile
darwin, unified, apple_budget ≥ 16,000 → T3b  PQ2_0 on M5 else PTQ1_0, -c 65536..262144 by budget
darwin, unified, apple_budget ≥  7,000 → T3a  PTQ1_0, -c 16384
RAM ≥ 30,000, AVX2, bandwidth ≥ 70 GB/s → T2  PTQ1_0, -ngl 0, -c 32768, CPU profile
RAM ≥ 15,500, AVX2, cores ≥ 4         → T1  PTQ1_0, -ngl 0, -c 8192, CPU profile          (the floor)
otherwise                             → T0  refuse with numbers; cloud path; D1 lesser local
```

The same procedure, with every threshold, output and the headroom arithmetic per rule, is the `pick` object in the JSON; the installer reads that, and this listing is its commentary. The packing rule: `PQ2_0` on NVIDIA Ampere, Hopper and Blackwell, on AMD RDNA through HIP, and on Apple M5, when the budget holds it; `PTQ1_0` everywhere else and on every CPU tier. The disk rule: a download proceeds only if free space minus everything to be fetched, runtime asset included, stays above the 10 GiB floor (R8).

Every branch then runs `residency_policy.plan()` with the proposed seat so R1 (OS reserve), R2 (RAM ceiling), R3 (VRAM budget), R8 (disk) and R10 (sidekick) can refuse it with their own reasons before a byte downloads. The packing choice follows the publisher's rule: `PTQ1_0` where memory binds or the card is Ada-class or a CPU; `PQ2_0` on Ampere, Hopper, Blackwell, RDNA and Apple M5.

### 7.4 Verify on the device, then seat

After download and load, in order, each with its result written to the model record and shown to the user:

1. **Bench:** `llama-bench -p 512 -n 128 -r 2` at the served flags; the record's `decode_tok_s` and `prompt_tok_s` become MEASURED for this machine, and the tier's ESTIMATE is replaced. Under the floor test in §5.4 the seat is not suggested; it is still installed, and the user is told the number.
2. **Conformance gate:** `model_seat_gate.run_conformance_gate` (**TREE**), the ten real-tool prompts, at `min_tool_context()` or the served context, whichever is smaller. The result is *informational and shapes the recommendation*: 8 of 10 or better seats Bonsai 2 as the brain; below that Friday still installs and offers it, says the score, and suggests the cloud brain for tool-heavy work. **The gate never refuses the user's choice**: that is the maintainer's standing decision (**TREE**, `core_routes._check_local_model_seat_gate` docstring, `model_seat_gate.py:347`), and this spec keeps it. The brief's phrase "must pass the conformance gate and honesty battery before they're seated" is therefore implemented as: *must pass before being recommended or auto-seated; never a bar to a seat the user chose.*
3. **Honesty battery:** removed from the tree by maintainer decision (`routes/seat_gate.py` header). Not resurrected here. What remains of its intent is the tool-receipt honesty check on every turn (`tool_receipts.py`), which applies to every seat equally.
4. **Seat:** the arbiter pins the seat (`LlamaServerBackend.load` with `gguf_path`, `port`, `mmproj_path`; **TREE** `residency_arbiter.py:829`) under the alias `bonsai2:27b`, one process for every role that names it (the comment at that line records the two-process failure this prevents).

---

## 8. First-setup rule and what it retires

- **Suggested at first setup: Bonsai 2 27B, and nothing else.** The tier picks the packing and context.
- **Retired from suggestions:** the README's Gemma table (E2B at 4.3 GB, E4B at 5.5 GB, 12B), `RECOMMENDED_LOCAL_MODEL = "gemma4:latest"` (**TREE**, `model_seat_gate.py:45`), and every Qwen, Gemma or other local entry in `model_plan`'s candidate list. They are not deleted from the catalogue.
- **Still fully supported by choice:** any GGUF the fork or the stock build can load, any Ollama model, any provider descriptor. The Intelligence tab's picker keeps listing them; nothing refuses them.
- **The README's "For a local model: an NVIDIA graphics card" line is retired.** The floor is a CPU line; a GPU is a tier, not a requirement.

The first-run and onboarding master spec owns the screens; this document supplies the rule, the tier table and the floor text. The message to that session is in §12.

---

## 9. Measurement recipe (so the UNMEASURED rows get filled)

On a quiet machine (more than 8 GB of RAM available, no other model loaded):

```
# CPU-only, GPU hidden
set CUDA_VISIBLE_DEVICES=
llama-bench.exe -m Ternary-Bonsai-2-27B-PTQ1_0.gguf -dev none -ngl 0 -t <physical cores> -p 512 -n 128 -r 2 -fa on
llama-bench.exe -m Ternary-Bonsai-2-27B-PTQ1_0.gguf -dev none -ngl 0 -t 4 -p 512 -n 128 -r 2 -fa on      # 4-core laptop emulation
# GPU, at each candidate context, with the shipped flags
llama-bench.exe -m Ternary-Bonsai-2-27B-PTQ1_0.gguf -ngl 99 -fa on -ctk q8_0 -ctv q8_0 -p 512 -n 128 -d 0,8192,32768 -r 2
```

Record peak working set (the wrapper in the session scratchpad polled `psutil` every 0.5 s) and `nvidia-smi` peak. The 4B file uses `PQ2_0`; add `--no-repack` if the CPU run exits 139. Each row's provenance tag flips to MEASURED with the date when the number is in.

---

## 10. Decisions that are the owner's

- **D1. Below the floor, is a lesser local brain offered at all?** Yes means T0 machines are offered Ternary Bonsai 4B (1 GB) or 1.7B, labelled as the older, weaker generation, next to the cloud door. No means T0 gets the cloud door only, and "only Bonsai 2" is literal. Recommendation: **yes, labelled**, because a privacy-sensitive user on an 8 GB laptop (north star §5.2) otherwise has no local path at all, which A6 says should exist where feasible.
- **D2. How the floor is published.** Either "16 GB RAM, CPU-only supported" as the single line, or "16 GB RAM; an 8 GB graphics card recommended" with CPU-only as supported-but-slower. Recommendation: **the second**, because it sets expectations at 3 to 6 tokens a second before purchase rather than after install.
- **D3. When FridayWeaver-2 starts training on Bonsai 2.** The Friday-Models decisions log has no Bonsai entry yet; every FridayWeaver figure is from the Gemma E2B lineage. Training on the 27B needs the unpacked weights (`Ternary-Bonsai-2-27B` is not published unpacked as of 2026-09-30; the Ternary Bonsai 1 sizes are) or a LoRA over the ternary base with the fork's `llama-finetune`, which exists in the installed build (**MEASURED-2026-09-30**, `llama-finetune.exe` in `llama.cpp-bonsai`) but is unmeasured for this purpose. Recommendation: **after the advisor ships**, with a one-day feasibility run first.

---

## 11. Build plan and effort

Estimates are engineer-days for one person, tests included; each item ends with a test that fails before and passes after.

| # | Item | Grounding | Effort |
|---|---|---|---|
| 1 | CPU feature flags and non-NVIDIA GPU detection in `hardware_profile.py` (Vulkan, HIP, Win32_VideoController, macOS); `friday_footprint_mib` | §7.1 | 2 d |
| 2 | Runtime check: bundled fork version, `--list-devices`, refusal text | §7.2 | 1 d |
| 3 | `model_plan`: the tier pick as a pure function reading `bonsai2-tiers.json`; `serve_args` written to the record; `residency_policy.plan()` dry run | §7.3, §3.3 | 2 d |
| 4 | CPU profile: mandatory `tool_selector` on CPU tiers, thinking off, one slot, cache reuse; router reads the profile from the seat record | §5.3 | 2 d |
| 5 | On-device verify: bench, gate as recommendation input, floor test, numbers shown | §7.4, §5.4 | 2 d |
| 6 | Below-floor message and doors (typed and spoken), wired to `privacy_consent` | §6.2 | 1 d |
| 7 | First-setup suggestion list = Bonsai 2 only; README requirements rewrite; Gemma table retired | §8 | 1 d |
| 8 | Fill UNMEASURED rows: CPU-only on this box when quiet; one AMD/Vulkan and one Apple run from a volunteer machine | §9 | 1 d + hardware access |
| | **Total** | | **12 days** |

Items 1 to 3 are the installer's dependency and go first; the onboarding session's flow consumes them. Items 4 and 5 are what make the floor true. The whole plan goes through the program lead's gauntlet.

---

## 12. Coordination

- **First-run and onboarding master spec (session `local_f5c53673`):** consumes §5.1 (the floor text), §7.3 (the pick), §6.2 (the below-floor doors) and the JSON. The message sent is: the floor is 16 GB or an 8 GB card; the pick is a pure function of the profile; only Bonsai 2 is suggested; the below-floor screen has two doors pending D1.
- **Program lead (`local_6952d073`):** merges this and the advisor spec as docs only; the build items in §11 enter the gauntlet.
- **Friday-Models:** D3.

---

## 13. Synthesis against the north star

| North star | This document |
|---|---|
| §8.7 local model setup: recommend by measured device fit; show memory, first-token and tok/s, context, tool calling, vision, disk, and whether measured or estimated | §3 gives every field with a provenance tag; §7.4 makes ESTIMATE into MEASURED on the device |
| §12.1 models are seats | Bonsai 2 is the brain seat's default, one process for every role that names it |
| §12.4 local only / local preferred | T1 and up serve the brain locally; T0 is told so and chooses |
| §12.7 capability registry distinguishes measured from estimated | the JSON's provenance field is that distinction, machine-readable |
| §12.10 context-fit guarantee | §3.3: the served context is a per-model cap the arbiter reads, never the training window |
| §6.4 no silent fallback; A6 both paths | §6.2: numbers, then two doors |
| A3 no telemetry | nothing in this flow leaves the machine; the download is a GET of public weights |
| §6.9 the system can say "I do not know" | UNMEASURED rows stay UNMEASURED in the product until the device measures them |
| GAP-MATRIX NS-6.2-1 (local control plane functional offline, partial) | the floor is the first published hardware line under which that requirement is actually met |
