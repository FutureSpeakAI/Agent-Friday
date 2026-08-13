# Provisioning Report — reference-instance local AI stack

**Date:** 2026-08-13
**Host:** Windows 11 Pro 10.0.26200, RTX 4070 (12282 MiB VRAM), 32620 MiB RAM, PowerShell 5.1.26100.9168
**Runtime root:** `%USERPROFILE%\.friday\runtime`
**Repo state:** no existing repo file was edited; the only repo writes in this mission are `docs/audits/inference-discovery.md` and this file.

> **Path update, 2026-08-13 (Phase A item A8 / decision D7).** This stack was originally provisioned to
> `%USERPROFILE%\friday-local-stack`, an invented per-machine directory chosen because no convention was
> declared for it — recorded at the time as Phase 1 decision question Q7. Stephen settled Q7 as
> *"yes, relocate"*, so the whole tree was moved to `~/.friday/runtime`, the repo's own convention, and is now
> config-overridable via `FRIDAY_RUNTIME_DIR` or `settings.runtime_dir` (`core.runtime_dir()`).
> **Every path below has been updated to the new location.** The §1 paragraph about staying outside `~/.friday`
> records the reasoning that applied during Phase 2 and is left as written; it is superseded by D7.
> All five relocated components were re-smoke-tested after the move — see
> [`phase-a-report.md`](./phase-a-report.md) item A8.

Companion document: [`inference-discovery.md`](./inference-discovery.md) (Phase 1). That report was complete on disk before any action in this one.

---

## 1. The derived plan

This plan was derived from the **provisioning-gap** section of the Phase 1 report and written here **before execution began**.

Phase 1 established four facts that shaped it:

- **faster-whisper and Piper are already integrated and shipping** inside Friday (`services/local_voice.py:317-331` and `:348-463`), installing to `~/.friday/local_voice/`. Provisioning them again in an isolated venv is deliberate **duplication, not substitution** — the point is to prove the reference stack stands up standalone without mutating the running app's environment.
- **Kokoro and ComfyUI have no integration point whatsoever.** Image generation is cloud-Gemini-only; Phase 1 verified zero hits for any local image backend. These are greenfield.
- **The Ollama trio is wired** through `routing/ollama_manager.py` and `services/model_router.py`, so step 1 is verification, not installation.
- **`~/.friday/` is the repo's implied artifact convention**, but it is the live application's data home and out of scope for this mission's writes. Hence a new root outside both the repo and `~/.friday`. This deviation is deliberate and is Phase 1 decision question Q7.

**HARD GATE assessment: no conflict; proceed.** Two existing provisioning mechanisms touch APPENDIX components — `scripts/install.ps1:144-190` (installs Ollama, pulls `gemma3:4b`) and `services/voice_installer.py:109-131` (pip-installs the GPU voice stack from a UI action). Neither *contradicts* the APPENDIX; they overlap with it. Nothing in the APPENDIX countermands what those installers do, and no repo source file needs editing to execute any step. Stopping would have been an over-reading of "conflict."

**Planned sequence, each step gated on a smoke test that proves function:**

| # | Component | Plan | Proof required |
|---|---|---|---|
| 1 | Ollama trio | Verify only, never pull. Measure brain tok/s uncontended. | Real generation from each; embed call returns a vector |
| 2 | Kokoro TTS | Dedicated venv, `pip install kokoro` | wav with nonzero duration + synthesis time |
| 3 | faster-whisper | Same venv, `large-v3-turbo` int8 CPU | transcribe the Kokoro wav, keyword match, RTF |
| 4 | Piper | Same venv, `pip install piper-tts` | one synthesis |
| 5 | ComfyUI + Z-Image | Clone, dedicated venv, torch matching detected CUDA, one image model | 1024×1024 via headless API, GPU freed after |

**Ordering constraint applied:** before step 5, all Ollama models are unloaded to honour the never-co-resident rule. The live Friday app cold-reloads its model on next use; that latency blip is accepted.

**Deviation taken during execution (recorded, not silent):** the Z-Image FP8 repo ships **only** the 6.15 GB diffusion transformer. Z-Image cannot run without its text encoder and VAE. I downloaded `qwen_3_4b.safetensors` and `ae.safetensors` from `Comfy-Org/z_image_turbo` as **required companions of the one authorised image model**, not as a second model. Exactly one image model was downloaded, as instructed.

---

## 2. Preflight — all mandatory checks

| Check | Requirement | Measured | Result |
|---|---|---|---|
| Free disk | ≥ 30 GB after estimating downloads | **53.1 GB** free; ~20 GB estimated | **PASS** |
| Python | 3.10+ available | 3.14, 3.13, **3.11.15**, 3.10 all present | **PASS** |
| `nvidia-smi` | must succeed; report driver + CUDA | RTX 4070, **driver 610.88**, **CUDA UMD 13.3**, 12282 MiB total / 10521 MiB free | **PASS** |
| Shell | expect PowerShell | PowerShell **5.1.26100.9168** Desktop | **PASS** |

Interpreter chosen for all venvs: `%USERPROFILE%\AppData\Roaming\uv\python\cpython-3.11.15-windows-x86_64-none\python.exe` — a real base interpreter (never the system Python, never the repo's `venv`), picked for the broadest wheel availability. `python` on PATH resolves to the unrelated hermes venv and was deliberately not used.

---

## 3. Per-component status

| # | Component | Status |
|---|---|---|
| 1a | `qwen3.6:35b` (brain) | **VERIFIED WORKING** |
| 1b | `gemma4:e4b` (sidekick) | **VERIFIED WORKING** |
| 1c | `qwen3-embedding:0.6b` | **VERIFIED WORKING** |
| 2 | Kokoro TTS | **VERIFIED WORKING** |
| 3 | faster-whisper STT | **VERIFIED WORKING** |
| 4 | Piper TTS | **VERIFIED WORKING** |
| 5 | ComfyUI + Z-Image Turbo FP8 | **VERIFIED WORKING** |
| — | llama.cpp | **SKIPPED** — correctly. Brief: install only as brain contingency; the brain did not fail. GGUF pointer recorded below as instructed. |

Zero components FAILED. No component required a second attempt except the warm-timing reruns noted below, which were measurement refinements rather than failure retries.

**Note on the brief's model inventory:** all three named tags — `qwen3.6:35b`, `gemma4:e4b`, `qwen3-embedding:0.6b` — **were present**. The orchestrator's inventory (which listed `gemma3:4b`, `gemma4:latest`, `gemma4:12b`, `qwen3:8b`, `qwen3.6:27b`, `phi4` and flagged the named tags as absent) was stale. Verified by fresh `ollama list`; nothing was pulled.

### Step 1 — Ollama trio (verify, never pull)

`ollama --version` → **0.32.9**. Fresh `ollama list`:

```
NAME                    ID              SIZE      MODIFIED
gemma4:e2b              7fbdbf8f5e45    7.2 GB    13 minutes ago
gemma4:e4b              c6eb396dbd59    9.6 GB    14 minutes ago
qwen3-embedding:0.6b    ac6da0dfba84    639 MB    15 minutes ago
qwen3.6:35b             07d35212591f    23 GB     21 minutes ago
```

**GPU coordination honoured.** `ollama ps` initially showed `gemma4:e2b` resident. The baseline was deferred until `ollama ps` returned empty, then taken uncontended. No waiting beyond that was required — the model's own TTL expired on its own.

**Brain — `qwen3.6:35b`.** First call returned empty text with all 64 tokens consumed by reasoning (it is a thinking model). Re-run with `think:false` produced real output, confirming genuine generation rather than a silent empty success:

```
RESPONSE: [Hello there, how are you today?]
```

Baseline over a longer fixed prompt ("Write one paragraph about the ocean.", `num_predict=200`, `think:false`): **121 tokens in 8.402 s = 14.4 tok/s**, 655 characters of prose returned.

Placement during that run — the load-bearing number for any future routing work:

```
NAME           SIZE     PROCESSOR          CONTEXT
qwen3.6:35b    29 GB    66%/34% CPU/GPU    262144
```

**The 23 GB brain does not fit the 12 GB card.** It expands to 29 GB resident and runs two-thirds on CPU. 14.4 tok/s is therefore an honest *system* baseline, not a GPU baseline. Cold load cost 83–100 s.

**Sidekick — `gemma4:e4b`.** `[Greetings to you with good cheer today.]` — **71.65 tok/s**, cold load 32.09 s, resident **3.4 GB at 100% GPU**. The contrast is the headline hardware finding: the sidekick is ~5× faster than the brain purely because it fits.

**Embedding — `qwen3-embedding:0.6b`.** `/api/embed` returned a vector of **1024 dimensions**, L2 norm exactly 1.0, in 18.87 s cold.

> **Cross-reference to Phase 1 §6.** Friday's embeddings are hardcoded to `all-MiniLM-L6-v2` (**384-dim**) in three separate modules, and `capability_routing.embedding.model` is a dead setting with zero readers. Adopting this 1024-dim model is therefore **not a config change** — it is a dimension change that would silently break every existing Chroma collection, and Phase 1 verified the failure would be *silent* (broad `except Exception: print(...)`), presenting as permanent memory loss with no user-visible error. This measured number is the concrete evidence behind Phase 1 decision question Q5.

**GGUF pointer (recorded, not installed — brain succeeded, so llama.cpp was correctly skipped):**

- Repo: **`unsloth/Qwen3.6-35B-A3B-GGUF`**
- Filename: **`Qwen3.6-35B-A3B-UD-IQ4_NL.gguf`** — 18 GB (IQ4_NL-class, official Unsloth)
- Larger variant: `Qwen3.6-35B-A3B-UD-IQ4_NL_XL.gguf` — 19.5 GB
- MTP variant repo: `unsloth/Qwen3.6-35B-A3B-MTP-GGUF`
- Contingency runtime (**not installed**): `https://github.com/ggml-org/llama.cpp`

At 18 GB this quant still exceeds 12 GB VRAM, but would spill materially less than the current 29 GB resident footprint — the obvious next experiment if brain throughput matters.

### Step 2 — Kokoro TTS — VERIFIED WORKING

Weights `hexgrad/Kokoro-82M`, code `hexgrad/kokoro`, voice `af_heart`, 24 kHz. Fixed sentence: *"The quick brown fox jumps over the lazy dog near the riverbank at sunrise."*

```
pipeline_load_s : 78.84   (first run: weight download + spacy en_core_web_sm)
synthesis_s     : 2.97
audio_seconds   : 5.08
rtf_synth       : 0.585
NONZERO_DURATION: PASS
```

**Synthesis time 2.97 s for 5.08 s of audio — RTF 0.585, comfortably faster than realtime on CPU.**

### Step 3 — faster-whisper STT — VERIFIED WORKING (with an important caveat)

`large-v3-turbo`, `device="cpu"`, `compute_type="int8"`, `beam_size=5`, transcribing the Kokoro wav from step 2.

```
TRANSCRIPT     : The quick brown fox jumps over the lazy dog  near the riverbank at sunrise.
keywords_found : 7/7 -> [quick, brown, fox, lazy, dog, riverbank, sunrise]
detected_lang  : en (p=1.00)
KEYWORD_CHECK  : PASS
```

**The full local voice loop is closed:** Kokoro synthesised → faster-whisper transcribed the sentence verbatim, 7/7 keywords.

Real-time factor required three measurements to report honestly:

| Condition | Audio | Best transcribe | RTF |
|---|---|---|---|
| Cold, machine under load | 5.08 s | 23.15 s | 4.562 |
| Warm, machine under load | 5.08 s | 16.31 s | 3.214 |
| Warm, machine quiet | 5.08 s | 18.83 s | 3.711 |
| **Warm, quiet, 31.3 s utterance** | **31.30 s** | **27.21 s** | **0.869** |

The quiet run was *slower* than the loaded one, which rules out contention as the cause. The real explanation is that Whisper pads every clip to a 30 s encoder window, so a 5 s clip pays nearly the full cost of a 30 s one. **The representative figure is RTF 0.869** on a realistic utterance.

**This matters architecturally:** at 0.869 the configuration is *marginally* viable for batch transcription and **not viable for low-latency streaming STT** on this CPU — a 5-second utterance still costs ~19 s to transcribe. Streaming would need GPU execution or a smaller model. Recorded because the stated direction includes "streaming STT."

### Step 4 — Piper TTS fallback — VERIFIED WORKING

Voice `en_US-lessac-medium`, 22.05 kHz, same sentence.

```
download_s    : 2.11
voice_load_s  : 2.05
synthesis_s   : 2.05
audio_seconds : 4.03
rtf_synth     : 0.508
NONZERO_DURATION: PASS
```

Marginally faster than Kokoro (RTF 0.508 vs 0.585) at lower sample rate — a sound fallback.

### Step 5 — ComfyUI + Z-Image Turbo FP8 — VERIFIED WORKING

**Never-co-resident rule honoured.** All four Ollama models were stopped before launching ComfyUI; `ollama ps` confirmed empty and `nvidia-smi` reported **10998 MiB free** before the run.

ComfyUI **v0.33.0**, commit `2f35f4a08176d993cded35dac3332be4f7287f41` (dated 2026-08-13). Boot log confirms:

```
Total VRAM 12282 MB, total RAM 32620 MB
pytorch version: 2.13.0+cu130
Device: cuda:0 NVIDIA GeForce RTX 4070 : cudaMallocAsync
```

Torch was matched to the **detected CUDA 13.3 driver** by selecting the `cu130` wheel index after verifying cp311/win_amd64 wheels exist there. `torch.cuda.is_available()` → `True`, device `NVIDIA GeForce RTX 4070`.

Model choice: the **FP8 build** (`drbaph/Z-Image-Turbo-FP8`), per the brief's preference order. SDXL was **not** downloaded — the condition for it (Z-Image failing both attempts) never arose. The GGUF path and `ComfyUI-GGUF` nodes were correspondingly not needed.

Workflow: `UNETLoader(fp8_e4m3fn)` → `CLIPLoader` → `VAELoader` → `CLIPTextEncode` ×2 → `EmptySD3LatentImage(1024×1024)` → `KSampler(euler/simple, 8 steps, cfg 1.0)` → `VAEDecode` → `SaveImage`, submitted over the headless HTTP API on **port 8188** (port 3000 was never bound).

> The correct CLIP loader type was resolved by reading the cloned source rather than guessing: `comfy/sd.py:1841-1848` routes a detected `TEModel.QWEN3_4B` encoder to `comfy.text_encoders.z_image` whenever `clip_type` is not FLUX/FLUX2, so the default `stable_diffusion` type is correct. There is no `z_image` entry in the `CLIPLoader` type list.

| Run | Seed | Time |
|---|---|---|
| Cold (includes loading 14.5 GB of weights) | 20260813 | **180.49 s** |
| **Warm (generation only)** | 987654321 | **28.10 s** |

Output verified as a genuine image, not a blank canvas:

```
format PNG   size (1024, 1024)   mode RGB
mean_pixel 162.66   std_pixel 60.03
NOT_BLANK: PASS
```

**Shutdown verified.** ComfyUI stopped, no leftover process, **port 8188 closed**, GPU returned to **11032 MiB free / 981 MiB used** (desktop only), `ollama ps` empty. The live Friday server on **:3000 confirmed still listening and untouched**.

---

## 4. Exact versions installed

**`venv-voice`** — Python 3.11.15, at `%USERPROFILE%\.friday\runtime\venv-voice`

| Package | Version |
|---|---|
| kokoro | 0.9.4 |
| misaki | 0.9.4 |
| espeakng-loader | 0.2.4 |
| faster-whisper | 1.2.1 |
| ctranslate2 | 4.8.1 |
| piper-tts | 1.6.1 |
| onnxruntime | 1.28.0 |
| torch | 2.13.0 (CPU) |
| soundfile | 0.14.0 |
| numpy | 2.4.6 |
| transformers | 5.15.0 |
| spacy / en_core_web_sm | 3.8.15 / 3.8.0 |

**`venv-comfy`** — Python 3.11.15, at `%USERPROFILE%\.friday\runtime\venv-comfy`

| Package | Version |
|---|---|
| torch | **2.13.0+cu130** |
| torchvision | 0.28.0+cu130 |
| torchaudio | 2.11.0+cu130 |
| numpy | 2.4.4 |
| safetensors | 0.8.0 |
| transformers | 5.15.0 |
| kornia | 0.8.3 |
| spandrel | 0.4.2 |
| comfyui-frontend-package | 1.48.7 |

**Outside the venvs:** Ollama 0.32.9 (pre-existing, untouched), ComfyUI v0.33.0 @ `2f35f4a`.

---

## 5. Absolute paths

| What | Path |
|---|---|
| Runtime root | `%USERPROFILE%\.friday\runtime` |
| Voice venv | `%USERPROFILE%\.friday\runtime\venv-voice` |
| ComfyUI venv | `%USERPROFILE%\.friday\runtime\venv-comfy` |
| ComfyUI checkout | `%USERPROFILE%\.friday\runtime\ComfyUI` |
| Z-Image diffusion model | `...\ComfyUI\models\diffusion_models\z_image_turbo_fp8_e4m3fn.safetensors` (6,154,958,896 B) |
| Z-Image text encoder | `...\ComfyUI\models\text_encoders\qwen_3_4b.safetensors` (8,044,982,048 B) |
| Z-Image VAE | `...\ComfyUI\models\vae\ae.safetensors` (335,304,388 B) |
| Whisper models | `%USERPROFILE%\.friday\runtime\whisper-models` |
| Piper voices | `%USERPROFILE%\.friday\runtime\piper-voices` |
| Kokoro weights | `%USERPROFILE%\.cache\huggingface\hub\models--hexgrad--Kokoro-82M` |
| Smoke-test artifacts | `%USERPROFILE%\.friday\runtime\artifacts` |
| Logs | `%USERPROFILE%\.friday\runtime\logs` |
| Smoke-test scripts | `%USERPROFILE%\.friday\runtime\smoke_*.py`, `run_zimage*.py`, `zimage_workflow*.json` |

**Artifacts produced:** `kokoro_test.wav` (5.08 s), `kokoro_long.wav` (31.30 s), `piper_test.wav` (4.03 s), `comfy-output\friday_zimage_smoke_00001_.png` and `_00002_.png` (1024×1024, 1.63 MB each). All inside the runtime root; none in the repo.

---

## 6. Disk consumed

| Directory | GB |
|---|---|
| ComfyUI (incl. 14.5 GB weights) | 13.59 |
| venv-comfy | 4.07 |
| whisper-models | 1.51 |
| venv-voice | 1.25 |
| piper-voices | 0.06 |
| artifacts | 0.01 |
| **Runtime root total** | **20.48** |
| Kokoro weights (HF cache, outside root) | 0.69 |
| **Grand total** | **≈ 21.2** |

Free disk: **53.1 GB → 28.5 GB**. The 30 GB requirement was a **preflight gate** (satisfied: 53.1 GB before starting), not a standing invariant. Flagging plainly that the machine now sits below that figure — the largest reclaimable item is the 8.04 GB `qwen_3_4b.safetensors` text encoder if the image path is abandoned.

---

## 7. Measured numbers

| Metric | Value | Conditions |
|---|---|---|
| **Brain tok/s** (`qwen3.6:35b`) | **14.4 tok/s** | uncontended, 121 tok / 8.402 s, 66% CPU / 34% GPU, 29 GB resident |
| Sidekick tok/s (`gemma4:e4b`) | **71.65 tok/s** | 100% GPU, 3.4 GB resident |
| Embedding dimensions | **1024**, L2-normalised | `qwen3-embedding:0.6b` |
| **Whisper RTF** | **0.869** | warm, quiet, 31.3 s utterance — the representative figure |
| Whisper RTF (short clip) | 3.214 – 4.562 | 5.08 s clip; encoder-padding artefact, not throughput |
| **Kokoro synthesis time** | **2.97 s** for 5.08 s audio (RTF 0.585) | CPU |
| **Piper synthesis time** | **2.05 s** for 4.03 s audio (RTF 0.508) | CPU |
| **Image generation time** | **28.10 s** warm / 180.49 s cold | 1024×1024, 8 steps, FP8, RTX 4070 |
| Brain cold load | 83 – 100 s | 23 GB model, 12 GB card |
| Sidekick cold load | 32.09 s | |

---

## 8. Anything left undone

1. **llama.cpp not installed — correct per the brief.** The brain succeeded, so only the GGUF pointer was recorded. Not a gap.
2. **`ComfyUI-GGUF` nodes and the GGUF quants not installed.** The FP8 build worked first time, so the GGUF fallback branch was never entered. Not a gap.
3. **SDXL not downloaded — correct.** Its precondition (Z-Image failing both attempts) never occurred.
4. **Nothing was wired into Friday.** This mission provisioned and proved an environment; it did not touch routing, `capability_routing`, or any repo source. Every integration point remains as Phase 1 described it — in particular ComfyUI and Kokoro still have **no** integration point in the codebase at all.
5. **No checksum verification was performed** on any downloaded artifact, because none of the tooling offers it and Phase 1 §11 verified the codebase has no checksum mechanism either. The Piper voice and Z-Image weights were fetched over HTTPS with no hash check. This is Phase 1 risk **R9**, now reproduced in the reference stack.
6. **`gemma4:e2b` was left installed and untouched.** It is not an APPENDIX component; it was present at audit start and was only stopped, never removed.
7. **Multi-GPU and non-Windows paths untested** — single-GPU Windows host only.
8. **The 8.04 GB text encoder is a companion download**, disclosed in §1 rather than silently included.

---

## 9. Definition of done — verification

| Requirement | Status |
|---|---|
| Both reports exist on disk | `docs/audits/inference-discovery.md`, `docs/audits/provisioning-report.md` |
| Every APPENDIX component VERIFIED WORKING or FAILED with logs | 7/7 VERIFIED WORKING; 0 FAILED; llama.cpp correctly SKIPPED |
| `git status` shows exactly two new files, nothing else | verified below |
| GPU left free | **Yes, by this mission.** At handoff of step 5: 11032 MiB free / 981 MiB used (desktop only), `ollama ps` empty, port 8188 closed. See note below. |
| Nothing committed | no `git add`, `commit`, or `push` was run at any point |
| Live Friday server unharmed | :3000 confirmed still listening; 2 `friday-desktop` python processes alive; no python/pythonw process was broad-killed |

`git status --porcelain --untracked-files=all` at completion:

```
?? docs/audits/inference-discovery.md
?? docs/audits/provisioning-report.md
```

Branch `fix/toolcall-integrity-v5` still at `656b70b`; the unpushed local WIP commit `fb4daee` was not touched or pushed.

**Precision on "GPU left free":** every component this mission started was shut down and its VRAM released — ComfyUI terminated, port 8188 closed, all Ollama models explicitly stopped. A later check showed 4661 MiB in use with `gemma4:e2b` resident at 100% GPU. **That is the live Friday application reloading its own sidekick model during normal operation, not a leak from this mission** — exactly the accepted cold-reload behaviour anticipated when the never-co-resident rule required unloading it. Nothing from this stack is resident.
