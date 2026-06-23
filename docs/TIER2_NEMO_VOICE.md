# Tier-2 Local Voice — NVIDIA NeMo (GPU)

The premium on-device voice tier for RTX-class machines: GPU-accelerated
streaming ASR + higher-fidelity TTS. It runs **behind the same backend interface
and the same `/ws/voice-local` WebSocket contract** as the Tier-1 CPU path, so
the browser audio plumbing, the `friday-pcm-player` worklet, and the holographic
signals are unchanged. Tier-1 (faster-whisper + Piper, CPU) remains the default
and the universal fallback — both tiers coexist.

| | Tier-1 (default) | Tier-2 (premium) |
|---|---|---|
| ASR | faster-whisper (CTranslate2, CPU INT8) | `nvidia/nemotron-3.5-asr-streaming-0.6b` (GPU) |
| TTS | Piper (VITS→ONNX, CPU) | NeMo FastPitch + HiFi-GAN (GPU) |
| Deps | onnxruntime (~150–300 MB) | torch-CUDA + NeMo (~3–6 GB) |
| Hardware | any CPU | NVIDIA GPU, ≥4 GB free VRAM |
| Install | `.[voice-local-lite]` (in `[all]`) | `.[voice-local-gpu]` (opt-in, **not** in `[all]`) |

## Requirements

- **GPU:** NVIDIA, Turing → Blackwell (RTX 2050/3050 laptop and up). Nemotron
  streaming is **GPU-only** — it does not run on CPU.
- **VRAM:** ≥4 GB free (gate: `services.nemo_voice.MIN_VRAM_GB`). The 0.6B RNN-T
  in fp16 is ~2–3 GB single-stream; 4 GB leaves headroom for TTS sharing the card.
- **CUDA:** a torch build matching your CUDA runtime. The installers default to
  the CUDA 12.4 wheel index (`https://download.pytorch.org/whl/cu124`). Pick the
  index URL that matches your driver from the PyTorch site if 12.4 isn't right.
- **NeMo:** `nemo_toolkit[asr,tts]>=2.6` (pulled by `.[voice-local-gpu]`).

## Install

**Linux / macOS-NVIDIA:** `install.sh` detects `nvidia-smi` and offers the NeMo
install. To do it by hand:

```bash
venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cu124
venv/bin/pip install -e .[voice-local-gpu]
```

**Windows:** `install.ps1` offers it when an NVIDIA GPU is present:

```powershell
venv\Scripts\pip.exe install torch --index-url https://download.pytorch.org/whl/cu124
venv\Scripts\pip.exe install -e .[voice-local-gpu]
```

> **Windows is best-effort.** NeMo is Linux-first; on Windows+RTX it usually
> works with a recent torch-CUDA wheel, but the dependency stack can be fragile.
> If a clean install proves painful, use **WSL2** (Ubuntu + the CUDA wheel) or a
> conda env, or simply stay on Tier-1 — the engine falls back to CPU voice
> automatically, so voice never breaks. (See VOICE_INTEGRATION_SPEC §14 R9.)

Models download lazily on first GPU voice activation into `~/.friday/models/nemo/`
(~1.5 GB), with a one-time "Downloading NeMo voice models…" progress orb. Nothing
is vendored in the repo.

## Selecting the tier

- Settings → **Audio & Voice → Voice Engine**: `Local (CPU)` · `Local GPU (NeMo)`
  · `Cloud (Gemini)` · `Auto`. (`Auto` picks GPU when ready, else CPU, then cloud.)
- Persisted as `voice_engine` in `settings.json`
  (`local` | `local-gpu` | `gemini` | `auto`).
- The switch is a **hot-swap**: the next voice session rebuilds the backends — no
  server restart. A GPU pick that can't actually run (no CUDA / NeMo missing /
  load error) **degrades gracefully to Tier-1 CPU**, surfaced via the status orb.

## Health & performance

- `GET /api/health/full` → `local_voice` block carries `active_tier`, last ASR/TTS
  latencies under `perf`, and a Tier-2 `gpu` sub-block (CUDA/VRAM/model readiness).
- `GET /api/health` (providers) → the `nvidia-nemo` provider reports
  `ok` / `needs_download` / `down` / `missing` from `services.nemo_voice.nemo_health`.
- `python friday_cli.py health` → prints both tiers + the active-tier latencies.

## License

- **Nemotron-3.5 ASR** — **OpenMDW-1.1** (permissive open model weights license).
  Models are downloaded at runtime, not redistributed in this repo.
- **NeMo FastPitch / HiFi-GAN** — NVIDIA NeMo model terms; also fetched at runtime.
- We deliberately **avoid CC-BY-NC** models (e.g. Canary) for the public repo.

## Manual GPU test procedure (run on an RTX box)

CI has no GPU, so the NeMo inference path is validated manually. The CPU/wiring
path is covered by `tests/unit/test_nemo_voice.py` and
`tests/unit/test_local_voice.py`.

1. **Install:** `.[voice-local-gpu]` + torch-CUDA (above).
2. **Detect:** `python friday_cli.py health` → expect
   `Local voice (Tier-2 · NeMo GPU): needs_download` (deps + GPU detected, models
   not yet fetched) and a `Hardware: GPU=… VRAM=…GB` line.
3. **Select:** Settings → Audio & Voice → Voice Engine → **Local GPU (NeMo)**.
4. **First activation:** click the mic. Expect the one-time download orb, then
   `live (GPU/NeMo)`. Models land in `~/.friday/models/nemo/`.
5. **Round-trip:** speak a sentence → live transcript → Friday replies in NeMo
   voice. Confirm the holographic cube animates on speech identically to Gemini
   (it reads the shared playback analyser).
6. **Latency:** `GET /api/health/full` → `local_voice.perf` shows `asr_ms` /
   `tts_ms` for the GPU tier; compare against the CPU tier.
7. **Fallback:** uninstall NeMo (or set `MIN_VRAM_GB` high) and confirm a
   `local-gpu` setting still yields working CPU voice with a "falling back to CPU
   voice" status message — never a dead socket.
