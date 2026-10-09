# Local voice models and the GPU option

Agent Friday™ can listen and speak entirely on your PC. This page covers the
downloadable pieces of local voice and the optional, experimental GPU engine
for NVIDIA graphics cards. The everyday voice features are in
[Voice](voice.md).

## Local voice models

**Settings > Voice > Local voice models** lists every downloadable piece of
local voice. The list appears in every voice mode, including cloud, so you can
prepare local voice before you need it.

Each row shows what the piece does, its download size, and whether it is
installed, downloading, stopped or failed. Nothing downloads until you press
**Download** and confirm. The confirmation names the size, the publisher and
the licence.

| Piece | What it does | Size |
|---|---|---|
| Nemotron 3.5 streaming speech recognition (int8, CPU) | The ear. Turns your speech into text as you talk. | about 454 MB |
| sherpa-onnx runtime | The program library the ear runs in. | about 3 MB |
| Silero VAD v6 | Hears when you start and stop speaking. | about 2 MB |
| Qwen3-4B-Instruct-2507 Q4_K_M | Fast reply model for short voice questions (best quality). | about 2.3 GB |
| Qwen3-1.7B Q4_K_M | A smaller fast reply model that fits beside the main model. | about 1.2 GB |
| misaki | Tells the Kokoro voice how to pronounce words. | about 4 MB |
| espeak-ng pronunciation helper and its library (optional) | Pronounces unusual names. Runs as its own program. | about 45 MB together |

A piece that needs another piece downloads it too, and the confirmation adds
the sizes together. Optional pieces are marked "(optional)". A piece that is
not yet available says why.

How downloads are handled:

- **Pinned.** Files come from a fixed release or revision, never from a moving
  branch. Software packages are pinned to an exact version.
- **Verified.** Friday checks each downloaded file against a fixed SHA-256
  checksum. A file that does not match is deleted and never used.
- **Resumable.** If a download stops, **Continue download** picks up where it
  stopped. **Stop** cancels a running download.

## The GPU option (experimental)

The GPU engine is an alternative to the default local engine. It runs
NVIDIA's Nemotron streaming speech recognition and a FastPitch plus HiFi-GAN
voice on the graphics card. It is labelled **Local GPU (NeMo, experimental)**
in the voice mode picker. The default local engine needs no graphics card.

### Requirements

- An NVIDIA graphics card with CUDA. Streaming recognition on this engine does
  not run on a processor.
- At least 4 GB of free graphics memory.
- The CUDA build of PyTorch and NVIDIA NeMo, which are large downloads.

If the engine cannot run on your PC, its button in the picker is greyed out
and the reason is shown.

### Installing

1. Open **Settings > Voice** and press **Voice setup**.
2. In the steps, find **GPU voice tier (NVIDIA NeMo), optional** and press
   **Install GPU tier**. The button shows the approximate download size.
3. Wait for the install to finish. Friday reports success only after the
   installed packages import correctly.

The speech and voice model files (about 1.5 GB) download the first time you
use the engine, with a progress message. They are stored in
`%USERPROFILE%\.friday\models\nemo\`.

### Choosing it

Open **Settings > Voice > Voice engine** and choose **Local GPU (NeMo,
experimental)**. The change applies to the next voice session without a
restart. The setting is `voice_engine` with the value `local-gpu`.

If the GPU engine cannot run (no CUDA, NeMo missing, not enough free graphics
memory, or a load error), Friday uses the default local voice instead and
shows a status message. Voice does not stop working.

### Checking health

- **Settings > Voice > Voice readiness** shows the ear, the reasoning and the
  mouth. A stage turns green only after it has actually heard, thought or
  spoken in the last 15 minutes.
- `GET /api/health/full` returns a `local_voice` block with the active tier,
  recent latencies and the GPU readiness.

## Licences

- Nemotron 3.5 speech recognition: OpenMDW-1.1.
- Qwen3 models: Apache-2.0.
- Silero VAD: MIT.
- NeMo FastPitch and HiFi-GAN: NVIDIA's NeMo model terms.

Model files are downloaded to your PC at your request. Friday does not
redistribute them.
