"""The local voice stack's downloadable artifacts, pinned (local voice spec §6, §11 D1).

One table, read by the installer (what to fetch and how to check it) and by
the Models screen (what the owner is shown before clicking Install: name,
size, licence, source). Nothing here downloads anything.

Every file is pinned twice: by the upstream REVISION its URL names (a commit
or a release tag, never a moving branch) and by its SHA-256. The installer
refuses an artifact whose pin is empty, and deletes a download whose hash
does not match: an unpinned or altered file is never installed. Python
packages are pinned to an exact version.

Licences were read from the upstream cards for the spec (§4.1) and are
re-checked when a pin is set: Nemotron (OpenMDW-1.1), Qwen3 (Apache-2.0),
Silero VAD (MIT), Ternary Bonsai (Apache-2.0, checked 2026-10-09), misaki (Apache-2.0 per its package metadata; the spec
said MIT), faster-whisper turbo (MIT), kokoro and Kokoro-82M (Apache-2.0),
Systran faster-whisper base/base.en/small (MIT, OpenAI Whisper weights).

The features that need these files, and what each says when one is missing,
are in services/feature_models.py.
"""
from __future__ import annotations

import os
from pathlib import Path

#: kind: "file" (one file to ``dest``), "archive" (a .tar.bz2 unpacked under
#: ``dest``), "pip" (``package`` pinned with ``==``, with optional ``extras``),
#: "hf_files" (named files of one Hugging Face repo at a pinned commit, each
#: with its own SHA-256, laid out as that repo's snapshot in a Hugging Face
#: cache, so the library that reads the model finds it with its own offline
#: lookup). ``dest`` is relative to the Friday runtime directory. ``revision``
#: and ``sha256`` are the pins.
ARTIFACTS = {
    "voice-ear-streaming": {
        "label": "Nemotron 3.5 streaming speech recognition (int8, CPU)",
        "kind": "archive",
        "source": "k2-fsa/sherpa-onnx release asr-models",
        "url": ("https://github.com/k2-fsa/sherpa-onnx/releases/download/{revision}/"
                "sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11.tar.bz2"),
        # The release tag is a moving name; the SHA-256 (GitHub's own asset
        # digest for this exact file) is what pins the bytes.
        "revision": "asr-models",
        "sha256": "c6bf5e0df765f9d5b43bc9e0536d4b4b3e7d40bdf5ecf13e45f134c51c05ae3a",
        "size_bytes": 475271763,
        "size_mb": 454,
        "licence": "OpenMDW-1.1",
        "dest": "voice/ear/nemotron-3.5-streaming-int8",
        "requires": ["sherpa-onnx"],
    },
    "sherpa-onnx": {
        "label": "sherpa-onnx runtime (streaming recognizer)",
        "kind": "pip",
        "package": "sherpa-onnx",
        # 1.13.8 has a Windows wheel for CPython 3.10 to 3.14 (PyPI).
        "version": "1.13.8",
        "sha256": None,
        "size_mb": 3,
        "licence": "Apache-2.0",
        "source": "PyPI",
    },
    # The package ships the v6 ONNX model itself (silero_vad/data), loaded by
    # local_voice.VADEndpointer with load_silero_vad(onnx=True); no separate
    # file is fetched.
    "voice-vad-v6": {
        "label": "Silero VAD v6 (voice activity, CPU)",
        "kind": "pip",
        "package": "silero-vad",
        "version": "6.2.1",
        "sha256": None,
        "size_mb": 2,
        "licence": "MIT",
        "source": "PyPI (snakers4/silero-vad)",
    },
    "voice-front-bonsai-1.7b": {
        "label": "Ternary Bonsai 1.7B PQ2_0 (voice front)",
        "kind": "file",
        # PrismML's ternary build of Qwen3-1.7B. It is served on the PrismML
        # llama.cpp fork (voice_front.required_engine), which the Bonsai brain
        # install already puts in place.
        "source": "prism-ml/Ternary-Bonsai-1.7B-gguf (Hugging Face)",
        "url": ("https://huggingface.co/prism-ml/Ternary-Bonsai-1.7B-gguf/resolve/"
                "{revision}/Ternary-Bonsai-1.7B-PQ2_0.gguf"),
        "revision": "983b5dec2ff16aab79990711ba0f828a499a7e6a",
        "sha256": "de68ba48a8dacb21979915991e7741b917869d71410a370df951c0c3a237ae50",
        "size_bytes": 463290464,
        "size_mb": 442,
        "licence": "Apache-2.0",
        "dest": "models/gguf/Ternary-Bonsai-1.7B-PQ2_0.gguf",
        # Served only on this runtime (voice_front.required_engine); the
        # Settings rows say so when it is missing, and voice_front.resolve
        # falls back to a front that runs.
        "runtime": "prism-fork",
    },
    "voice-front-4b": {
        "label": "Qwen3-4B-Instruct-2507 Q4_K_M (voice front)",
        "kind": "file",
        # Qwen publishes no GGUF of the 2507 Instruct model; unsloth's
        # Apache-2.0 quantisation of it carries the same file name.
        "source": "unsloth/Qwen3-4B-Instruct-2507-GGUF (Hugging Face)",
        "url": ("https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/"
                "{revision}/Qwen3-4B-Instruct-2507-Q4_K_M.gguf"),
        "revision": "a06e946bb6b655725eafa393f4a9745d460374c9",
        "sha256": "3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597",
        "size_bytes": 2497281120,
        "size_mb": 2382,
        "licence": "Apache-2.0",
        "dest": "models/gguf/Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
    },
    "voice-front-1.7b": {
        "label": "Qwen3-1.7B Q4_K_M (voice front beside the brain)",
        "kind": "file",
        # Qwen's own repo now holds only Q8_0; the llama.cpp maintainers'
        # (ggml-org) Apache-2.0 Q4_K_M of the same model is used instead.
        "source": "ggml-org/Qwen3-1.7B-GGUF (Hugging Face)",
        "url": ("https://huggingface.co/ggml-org/Qwen3-1.7B-GGUF/resolve/"
                "{revision}/Qwen3-1.7B-Q4_K_M.gguf"),
        "revision": "daeb8e2d528a760970442092f6bf1e55c3b659eb",
        "sha256": "d2387ca2dbfee2ffabce7120d3770dadca0b293052bc2f0e138fdc940d9bc7b5",
        "size_bytes": 1282439264,
        "size_mb": 1223,
        "licence": "Apache-2.0",
        "dest": "models/gguf/Qwen3-1.7B-Q4_K_M.gguf",
    },
    # Kokoro's Python package. 0.9.4 is the only release that installs beside
    # the bundled numpy 2.x: every 0.7/0.8 release pins numpy==1.26.4, which
    # pip resolves by DOWNGRADING the numpy everything else here was built
    # against. 0.9.4 declares plain ``numpy`` and ``misaki[en]>=0.9.4``, so
    # misaki is pinned to 0.9.4 below and installed first.
    "kokoro": {
        "label": "Kokoro voice engine (Python package)",
        "kind": "pip",
        "package": "kokoro",
        "version": "0.9.4",
        "sha256": None,
        "size_mb": 1,
        "licence": ("Apache-2.0 (its misaki[en] dependency also installs the "
                    "GPL-3.0 espeakng-loader and phonemizer-fork, which Friday "
                    "runs only as a separate helper program)"),
        "source": "PyPI (hexgrad/kokoro)",
        # Kokoro runs on PyTorch. pip would pull a multi-gigabyte torch for a
        # one-megabyte package if it is missing, so the installer refuses
        # instead and says so (voice_installer.start).
        "needs_modules": ["torch"],
        "requires": ["misaki"],
        # pip also installs what kokoro 0.9.4 declares and is not here yet
        # (see DEPENDENCY_ESTIMATES): shown in the size the owner confirms.
        "deps": [["transformers", 16, "transformers and tokenizers"],
                 ["loguru", 1, "loguru"]],
        # An earlier Kokoro install (0.7/0.8 beside numpy 1.26) still speaks:
        # it is reported as present with an upgrade available, not missing.
        "compatible_from": "0.7.0",
    },
    "misaki": {
        "label": "misaki G2P for Kokoro",
        "kind": "pip",
        "package": "misaki",
        # kokoro 0.9.4 requires misaki[en]>=0.9.4. 0.9.4's English extra
        # declares spacy, num2words, phonemizer-fork and espeakng-loader;
        # misaki.en itself imports none of the espeak pieces, and Kokoro's own
        # ``from misaki import espeak`` gets the pipe-backed stand-in
        # (services/g2p_fallback) first, so the GPL code never loads here.
        "version": "0.9.4",
        "extras": ["en"],
        "sha256": None,
        "size_mb": 4,
        # The package's own metadata (misaki 0.9.4) says Apache 2.0.
        "licence": "Apache-2.0",
        "source": "PyPI",
        # misaki's English G2P needs spaCy's small English model and, left to
        # itself, downloads it silently on first use. Installing misaki here
        # installs the model too: a declared, consented download, never at load.
        # espeakng-loader is part of misaki[en]; it is installed at its pin
        # first so pip does not choose a version.
        "requires": ["spacy-en-core-web-sm", "espeakng-loader"],
        "deps": [["spacy", 26, "spaCy and its helpers (thinc, blis, srsly and others)"],
                 ["num2words", 1, "num2words"],
                 ["phonemizer", 1, "phonemizer-fork"]],
        # misaki 0.7.4 (the earlier pin) still pronounces for an earlier
        # Kokoro: present, with an upgrade available.
        "compatible_from": "0.7.0",
    },
    "spacy-en-core-web-sm": {
        "label": "spaCy small English model (for Kokoro pronunciation)",
        "kind": "pip",
        "package": "en-core-web-sm",
        # The model line matches spaCy 3.8 (the model requires spacy>=3.8,<3.9).
        "version": "3.8.0",
        # Not on PyPI: explosion's release asset for exactly this version.
        "pip_url": ("https://github.com/explosion/spacy-models/releases/download/"
                    "en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"),
        # SHA-256 of that release asset (12,806,118 bytes), computed
        # 2026-10-15; pip checks it before installing.
        "sha256": "1932429db727d4bff3deed6b34cfc05df17794f4a52eeb26cf8928f7c1a0fb85",
        "size_mb": 13,
        "licence": "MIT",
        "source": "GitHub (explosion/spacy-models)",
    },
    # OPTIONAL, GPL-3.0: espeak-ng pronunciations for names misaki does not
    # know. Installed only by the owner's choice, and run as its own helper
    # program (agent_friday/voice/espeak_helper.py) that Friday talks to over
    # a pipe; it is never imported or loaded into Friday's process, and the
    # repository vendors none of it. Without it, unknown names are spelled
    # out (services/g2p_fallback).
    "espeak-ng-helper": {
        "label": "espeak-ng pronunciation helper for names (optional; runs as its "
                 "own program)",
        "kind": "pip",
        "package": "phonemizer-fork",
        "version": "3.3.2",
        "sha256": None,
        "size_mb": 25,
        "licence": "GPL-3.0-or-later (phonemizer-fork and espeak-ng)",
        "source": "PyPI (phonemizer-fork, espeakng-loader; espeak-ng)",
        "optional": True,
        "requires": ["espeakng-loader"],
    },
    "espeakng-loader": {
        "label": "espeak-ng library for the pronunciation helper",
        "kind": "pip",
        "package": "espeakng-loader",
        "version": "0.2.4",
        "sha256": None,
        "size_mb": 20,
        "licence": "GPL-3.0-or-later (bundles espeak-ng)",
        "source": "PyPI",
        # Not optional once Kokoro is wanted: misaki[en] 0.9.4 declares it.
        # It is installed, never imported into Friday's process (see above).
        "compatible_from": "0.1.0",
    },
    # The speech recognisers CTranslate2 (faster-whisper) loads. They land in
    # Friday's whisper cache (local_voice.WHISPER_DIR) as a Hugging Face
    # snapshot, which is where push-to-transcribe and the CPU ear already
    # look, and where media transcripts and the podcast listening check read
    # from (services/feature_models.whisper_model_dir). Nothing loads them
    # over the network.
    "whisper-base.en": {
        "label": "faster-whisper base.en (English speech recognition, CPU)",
        "kind": "hf_files",
        "repo": "Systran/faster-whisper-base.en",
        "revision": "3d3d5dee26484f91867d81cb899cfcf72b96be6c",
        "files": {
            "config.json": (2227, "f3bc3821e9fc76a27bae538e11ae5b677dcdd352b4600429ce7951d398569aeb"),
            "model.bin": (145216508, "2a166925539a16005f14ff328359f9b9adb9dc4fb631bb3b227526862e93e2ef"),
            "tokenizer.json": (2128466, "929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df"),
            "vocabulary.txt": (422309, "ff77588746d3a2595d32ab5b69ffd7b95ce2441ac57533cb66fc3eb575a115cf"),
        },
        "cache": "whisper",
        "licence": "MIT (OpenAI Whisper weights)",
        "source": "Systran/faster-whisper-base.en (Hugging Face)",
    },
    "whisper-base": {
        "label": "faster-whisper base (speech recognition for push-to-transcribe on the CPU)",
        "kind": "hf_files",
        "repo": "Systran/faster-whisper-base",
        "revision": "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66",
        "files": {
            "config.json": (2309, "56a6d8110d311f19c8f0471e562832c7527f146b567275bfca59fcf7c184da9a"),
            "model.bin": (145217532, "d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9"),
            "tokenizer.json": (2203239, "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab"),
            "vocabulary.txt": (459861, "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913"),
        },
        "cache": "whisper",
        "licence": "MIT (OpenAI Whisper weights)",
        "source": "Systran/faster-whisper-base (Hugging Face)",
    },
    "whisper-small": {
        "label": "faster-whisper small (more accurate push-to-transcribe on a GPU)",
        "kind": "hf_files",
        "repo": "Systran/faster-whisper-small",
        "revision": "536b0662742c02347bc0e980a01041f333bce120",
        "files": {
            "config.json": (2370, "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828"),
            "model.bin": (483546902, "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"),
            "tokenizer.json": (2203239, "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab"),
            "vocabulary.txt": (459861, "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913"),
        },
        "cache": "whisper",
        "licence": "MIT (OpenAI Whisper weights)",
        "source": "Systran/faster-whisper-small (Hugging Face)",
        "optional": True,
    },
    # Kokoro's weights, config and every published voice. They go to the
    # Hugging Face cache this process reads (huggingface_hub's HF_HUB_CACHE),
    # where kokoro_voice and podcast_render already look them up by path.
    "kokoro-82m": {
        "label": "Kokoro-82M voice model and its 54 voices",
        "kind": "hf_files",
        "repo": "hexgrad/Kokoro-82M",
        "revision": "f3ff3571791e39611d31c381e3a41a3af07b4987",
        "files": None,                      # filled from _KOKORO_FILES below
        "cache": "hf",
        "licence": "Apache-2.0",
        "source": "hexgrad/Kokoro-82M (Hugging Face)",
        "requires": ["kokoro"],
    },
    "voice-ear-turbo": {
        "label": "faster-whisper large-v3-turbo int8 (optional accuracy lane)",
        "kind": "archive",
        "source": "Hugging Face (CTranslate2 export)",
        "url": None,
        "revision": None,
        "sha256": None,
        "size_mb": 1500,
        "licence": "MIT",
        "dest": "voice/ear/whisper-large-v3-turbo-int8",
        "optional": True,
    },
}


#: hexgrad/Kokoro-82M at the pinned commit: ``path size sha256``. The voices'
#: hashes are the repo's Git LFS object ids (SHA-256 of the file); config.json
#: is a plain Git file, hashed from the pinned revision on 2026-10-15.
_KOKORO_FILES = """
config.json 2351 5abb01e2403b072bf03d04fde160443e209d7a0dad49a423be15196b9b43c17f
kokoro-v1_0.pth 327212226 496dba118d1a58f5f3db2efc88dbdc216e0483fc89fe6e47ee1f2c53f18ad1e4
voices/af_alloy.pt 523425 6d877149dd8b348fbad12e5845b7e43d975390e9f3b68a811d1d86168bef5aa3
voices/af_aoede.pt 523425 c03bd1a4c3716c2d8eaa3d50022f62d5c31cfbd6e15933a00b17fefe13841cc4
voices/af_bella.pt 523425 8cb64e02fcc8de0327a8e13817e49c76c945ecf0052ceac97d3081480e8e48d6
voices/af_heart.pt 523425 0ab5709b8ffab19bfd849cd11d98f75b60af7733253ad0d67b12382a102cb4ff
voices/af_jessica.pt 523435 cdfdccb8cc975aa34ee6b89642963b0064237675de0e41a30ae64cc958dd4e87
voices/af_kore.pt 523420 8bfbc512321c3db49dff984ac675fa5ac7eaed5a96cc31104d3a9080e179d69d
voices/af_nicole.pt 523430 c5561808bcf5250fe8c5f5de32caf2d94f27e57e95befdb098c5c85991d4c5da
voices/af_nova.pt 523420 e0233676ddc21908c37a1f102f6b88a59e4e5c1bd764983616eb9eda629dbcd2
voices/af_river.pt 523425 e149459bd9c084416b74756b9bd3418256a8b839088abb07d463730c369dab8f
voices/af_sarah.pt 523425 49bd364ea3be9eb3e9685e8f9a15448c4883112a7c0ff7ab139fa4088b08cef9
voices/af_sky.pt 523351 c799548aed06e0cb0d655a85a01b48e7f10484d71663f9a3045a5b9362e8512c
voices/am_adam.pt 523420 ced7e284aba12472891be1da3ab34db84cc05cc02b5889535796dbf2d8b0cb34
voices/am_echo.pt 523420 8bcfdc852bc985fb45c396c561e571ffb9183930071f962f1b50df5c97b161e8
voices/am_eric.pt 523420 ada66f0eefff34ec921b1d7474d7ac8bec00cd863c170f1c534916e9b8212aae
voices/am_fenrir.pt 523430 98e507eca1db08230ae3b6232d59c10aec9630022d19accac4f5d12fcec3c37a
voices/am_liam.pt 523420 c82550757ddb31308b97f30040dda8c2d609a9e2de6135848d0a948368138518
voices/am_michael.pt 523435 9a443b79a4b22489a5b0ab7c651a0bcd1a30bef675c28333f06971abbd47bd37
voices/am_onyx.pt 523420 e8452be16cd0f6da7b4579eaf7b1e4506e92524882053d86d72b96b9a7fed584
voices/am_puck.pt 523420 dd1d8973f4ce4b7d8ae407c77a435f485dabc052081b80ea75c4f30b84f36223
voices/am_santa.pt 523425 7f2f7582fa2b1f160e90aafe6d0b442a685e773608b6667e545d743b073e97a7
voices/bf_alice.pt 523425 d292651b6af6c0d81705c2580dcb4463fccc0ff7b8d618a471dbb4e45655b3f3
voices/bf_emma.pt 523420 d0a423deabf4a52b4f49318c51742c54e21bb89bbbe9a12141e7758ddb5da701
voices/bf_isabella.pt 523440 cdd4c37003805104d1d08fb1e05855c8fb2c68de24ca6e71f264a30aaa59eefd
voices/bf_lily.pt 523420 6e09c2e481e2d53004d7e5ae7d3a325369e130a6f45c35a6002de75084be9285
voices/bm_daniel.pt 523430 fc3fce4e9c12ed4dbc8fa9680cfe51ee190a96444ce7c3ad647549a30823fc5d
voices/bm_fable.pt 523425 d44935f3135257a9064df99f007fc1342ff1aa767552b4a4fa4c3b2e6e59079c
voices/bm_george.pt 523430 f1bc812213dc59774769e5c80004b13eeb79bd78130b11b2d7f934542dab811b
voices/bm_lewis.pt 523425 b5204750dcba01029d2ac9cec17aec3b20a6d64073c579d694a23cb40effbd0e
voices/ef_dora.pt 523420 d9d69b0f8a2b87a345f269d89639f89dfbd1a6c9da0c498ae36dd34afcf35530
voices/em_alex.pt 523420 5eac53f767c3f31a081918ba531969aea850bed18fe56419b804d642c6973431
voices/em_santa.pt 523430 aa8620cb96cec705823efca0d956a63e158e09ad41aca934d354b7f0778f63cb
voices/ff_siwis.pt 523425 8073bf2d2c4b9543a90f2f0fd2144de4ed157e2d4b79ddeb0d5123066171fbc9
voices/hf_alpha.pt 523425 06906fe05746d13a79c5c01e21fd7233b05027221a933c9ada650f5aafc8f044
voices/hf_beta.pt 523420 63c0a1a6272e98d43f4511bba40e30dd9c8ceaf5f39af869509b9f51a71c503e
voices/hm_omega.pt 523425 b55f02a8e8483fffe0afa566e7d22ed8013acf47ad4f6bbee2795a840155703e
voices/hm_psi.pt 523351 2f0f055cea4f1083f4ef127ece48d71606347f6557dbb961c0ca5740a2da485b
voices/if_sara.pt 523425 6c0b253b955fe32f1a1a86006aebe83d050ea95afd0e7be15182f087deedbf55
voices/im_nicola.pt 523341 234ed06648649f9bd874b37508ea17560b9c993ef85b4ddb3e3a71e062bd2c12
voices/jf_alpha.pt 523425 1bf4c9dc69e45ee46183b071f4db766349aac5592acbcfeaf051018048a5d787
voices/jf_gongitsune.pt 523351 1b171917f18f351e65f2bf9657700cd6bfec4e65589c297525b9cf3c20105770
voices/jf_nezumi.pt 523420 d83f007a7f01783b77014561a7d493d327a0210e143440e91c9b697590d27661
voices/jf_tebukuro.pt 523435 0d6917904438aec85f73a6fa1f7ac2be6481aae47c697834936930a91796c576
voices/jm_kumo.pt 523425 98340afd68b1cee84fe0cd95528cfa6d4b39e416aa75a9df64049d52c8b55896
voices/pf_dora.pt 523425 07e4ff987c5d5a8c3995efd15cc4f0db7c4c15e881b198d8ab7f67ecf51f5eb7
voices/pm_alex.pt 523425 cf0ba8c573c2480fc54123683a35cf1e2ae130428e441eb91f9149bdb188a526
voices/pm_santa.pt 523430 d42103169c5c872abbafb9129133af7e942bb9d272c3cc3b95c203e7d7198c29
voices/zf_xiaobei.pt 523435 9b76be63dab4f4f96962030acc0126a9aee9728608fbbe115e2b58a2bd504df6
voices/zf_xiaoni.pt 523430 95b49f169bf1640f4f43c25e13daa39f7b98d15d00823e83ef5c14b3ced59e49
voices/zf_xiaoxiao.pt 523440 cfaf6f2ded1ee56f1ff94fcd2b0e6cdf32e5b794bdc05b44e7439d44aef5887c
voices/zf_xiaoyi.pt 523430 b5235dbaeef85a4c613bf78af9a88ff63c25bac5f26ba77e36186d8b7ebf05e2
voices/zm_yunjian.pt 523435 76cbf8bad35901d011d9628a2fdceb7b4f1f127e7a3269cb393b3941eb7fc417
voices/zm_yunxi.pt 523425 dbe6e1ce7c3dbaf2f5667432947b638b1c6831ccbe154c4610dbcc44f431e27b
voices/zm_yunxia.pt 523430 bb2b03b08e84d64e1214440ce3b624987fac177f2eeb5bab8571799a3d980acd
voices/zm_yunyang.pt 523435 5238ac22e0c7f8b6cdd2eddd6e444b8a700b73c4674d9a047d59a94ff96379a2
"""
ARTIFACTS["kokoro-82m"]["files"] = {
    p: (int(n), h) for p, n, h in (ln.split() for ln in _KOKORO_FILES.strip().splitlines())}

# An "hf_files" artifact's size is the sum of its pinned files, so the size the
# owner is shown cannot drift from what is downloaded.
for _a in ARTIFACTS.values():
    if _a["kind"] == "hf_files":
        _a["size_bytes"] = sum(n for n, _h in _a["files"].values())
        _a["size_mb"] = max(1, round(_a["size_bytes"] / 1048576))


#: What each artifact is for, in plain words, for the Settings screen.
PURPOSE = {
    "kokoro": "Kokoro voice: the engine that speaks podcasts and the Kokoro local voice.",
    "kokoro-82m": "Kokoro voice: the voice model itself and every voice it can speak in.",
    "whisper-base.en": "Speech recognition: transcribes your audio and video, and checks a podcast says what its script says.",
    "whisper-base": "Speech recognition: push-to-transcribe (Alt+T) and the local ear on the processor.",
    "whisper-small": "Speech recognition (optional): a more accurate push-to-transcribe when a graphics card is free.",
    "voice-ear-streaming": "Ear: turns your speech into text as you talk, on this computer.",
    "sherpa-onnx": "Ear runtime: the small program library the ear model runs in.",
    "voice-vad-v6": "Ear helper: hears when you start and stop speaking.",
    "voice-front-bonsai-1.7b": "Fast reply model: answers voice questions and runs quick tools (the default; small enough to sit beside the main model).",
    "voice-front-4b": "Fast reply model: a larger Qwen3 alternative (pauses the main model while you talk).",
    "voice-front-1.7b": "Fast reply model: the Qwen3-1.7B alternative.",
    "misaki": "Voice helper: tells the Kokoro voice how to pronounce words.",
    "spacy-en-core-web-sm": "Voice helper: the small English language model the pronouncer reads sentences with.",
    "espeak-ng-helper": "Voice helper (optional): pronounces unusual names.",
    "espeakng-loader": "Voice helper: the espeak-ng library misaki installs with Kokoro; only the separate name pronouncer runs it.",
    "voice-ear-turbo": "Ear (optional): a larger, more accurate listener.",
}


def size_bytes(artifact_id: str) -> int:
    a = ARTIFACTS[artifact_id]
    return int(a.get("size_bytes") or a["size_mb"] * 1024 * 1024)


def pinned(artifact_id: str) -> tuple:
    """``(True, "")`` when the artifact can be installed, else ``(False, why)``."""
    a = ARTIFACTS.get(artifact_id)
    if a is None:
        return False, f"unknown voice artifact {artifact_id!r}"
    if a["kind"] == "pip":
        if not a.get("version"):
            return False, f"{a['label']} has no pinned version yet"
        return True, ""
    if a["kind"] == "hf_files":
        rev = str(a.get("revision") or "")
        if len(rev) != 40 or any(c not in "0123456789abcdef" for c in rev):
            return False, f"{a['label']} has no pinned source revision yet"
        if not a.get("files") or not all(
                isinstance(h, str) and len(h) == 64 for _n, h in a["files"].values()):
            return False, f"{a['label']} has no pinned SHA-256 yet"
        return True, ""
    if not a.get("url") or ("{revision}" in a["url"] and not a.get("revision")):
        return False, f"{a['label']} has no pinned source revision yet"
    if not a.get("sha256"):
        return False, f"{a['label']} has no pinned SHA-256 yet"
    return True, ""


def url_for(artifact_id: str) -> str:
    a = ARTIFACTS[artifact_id]
    return a["url"].format(revision=a.get("revision") or "")


def pip_spec(artifact_id: str) -> str:
    a = ARTIFACTS[artifact_id]
    if a.get("pip_url"):
        # A package that is not on PyPI: a direct reference to the exact
        # release asset, with its hash when one is pinned.
        return "%s @ %s%s" % (a["package"], a["pip_url"],
                               ("#sha256=" + a["sha256"]) if a.get("sha256") else "")
    extras = "[%s]" % ",".join(a["extras"]) if a.get("extras") else ""
    return "%s%s==%s" % (a["package"], extras, a["version"])


# DEPENDENCY_ESTIMATES: a "pip" artifact's ``deps`` are the packages pip also
# installs for it when they are not here yet: ``[import name, MB, label]``.
# The MB are download sizes of the Windows CPython 3.11 wheels at the versions
# uv.lock resolves (read 2026-10-15): spaCy 3.8.16 15.6 MB + blis 5.9 + thinc
# 1.7 + srsly 0.6 + about a dozen small helpers ~0.6 = ~24.4 MB, rounded up
# to 26; transformers 11.5 + tokenizers 2.7 + safetensors/huggingface-hub 1.1
# = ~15.3 MB, rounded up to 16. They are approximate: Friday has no
# constraints file for these, so pip picks their versions, and the figure
# counts a group only when its import is not already on this computer.
def deps_missing(artifact_id: str) -> list:
    """``[(label, MB)]`` for the dependencies pip would add for this one
    artifact (not what it ``requires``: those are artifacts with their own
    rows and sizes), counting only those whose import is not here now. Looks
    the module up without importing it."""
    import importlib.util
    out = []
    for mod, mb, label in ARTIFACTS.get(artifact_id, {}).get("deps") or []:
        try:
            here = importlib.util.find_spec(mod) is not None
        except Exception:
            here = False
        if not here:
            out.append((label, int(mb)))
    return out


def deps_mb(artifact_id: str) -> int:
    return sum(mb for _l, mb in deps_missing(artifact_id))


def deps_note(artifact_id: str) -> str:
    """"plus its Python dependencies (spaCy and its helpers ..., about 27 MB;
    approximate, pip picks their versions)", or "" when none are missing."""
    miss = deps_missing(artifact_id)
    if not miss:
        return ""
    return ("plus its Python dependencies (%s, about %d MB; approximate, pip "
            "picks their versions)" % (", ".join(l for l, _mb in miss),
                                        sum(mb for _l, mb in miss)))


def _version_tuple(v: str) -> tuple:
    out = []
    for part in str(v or "").split("."):
        digits = "".join(c for c in part if c.isdigit())
        if not digits:
            break
        out.append(int(digits))
    return tuple(out)


def pip_state(artifact_id: str):
    """For a "pip" artifact: ``"pinned"`` (the pinned version is installed),
    ``"older"`` (an older version the runtime still works with is installed:
    an upgrade is available), ``"other"`` (a newer compatible version), or
    None (not installed, or too old to use)."""
    a = ARTIFACTS[artifact_id]
    if a.get("kind") != "pip":
        return None
    try:
        from importlib import metadata
        have = metadata.version(a["package"])
    except Exception:
        return None
    if have == a.get("version"):
        return "pinned"
    floor = a.get("compatible_from")
    if not floor or _version_tuple(have) < _version_tuple(floor):
        return None
    return "older" if _version_tuple(have) < _version_tuple(a["version"]) else "other"


def kokoro_pip_command() -> str:
    """The one pip line that installs Kokoro at the pins above, for messages
    that tell someone how to repair an install by hand."""
    return "pip install " + " ".join(
        pip_spec(aid) for aid in ("kokoro", "misaki", "espeakng-loader"))


# ── Hugging Face snapshots ("hf_files") ─────────────────────────────────────

def hf_file_url(artifact_id: str, path: str) -> str:
    a = ARTIFACTS[artifact_id]
    return "https://huggingface.co/%s/resolve/%s/%s" % (a["repo"], a["revision"], path)


def hf_hub_cache() -> Path:
    """The Hugging Face cache this process reads (huggingface_hub's own
    HF_HUB_CACHE when the library is importable, else the same rule it uses)."""
    try:
        from huggingface_hub import constants
        return Path(constants.HF_HUB_CACHE)
    except Exception:
        env = os.environ.get("HF_HUB_CACHE") or os.environ.get("HUGGINGFACE_HUB_CACHE")
        if env:
            return Path(env)
        home = os.environ.get("HF_HOME") or os.path.join(
            os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache"),
            "huggingface")
        return Path(home) / "hub"


def cache_root(artifact_id: str) -> Path:
    """Where an "hf_files" artifact's snapshot lives: Friday's whisper cache
    (the one push-to-transcribe already reads) or the Hugging Face cache."""
    if ARTIFACTS[artifact_id].get("cache") == "whisper":
        from agent_friday.services.local_voice import WHISPER_DIR
        return Path(WHISPER_DIR)
    return hf_hub_cache()


def repo_folder(repo: str) -> str:
    """``org/name`` as the Hugging Face cache names its folder."""
    return "models--" + repo.replace("/", "--")


def snapshot_dir(artifact_id: str) -> Path:
    a = ARTIFACTS[artifact_id]
    return cache_root(artifact_id) / repo_folder(a["repo"]) / "snapshots" / a["revision"]


def public_rows() -> list:
    """What the Models screen shows before an install: never a hash-less
    promise, always the size, licence and source."""
    out = []
    for aid, a in ARTIFACTS.items():
        ok, why = pinned(aid)
        out.append({"id": aid, "label": a["label"], "size_mb": a["size_mb"],
                    "size_bytes": size_bytes(aid), "purpose": PURPOSE.get(aid, ""),
                    # What pip adds for it, shown in the size confirmed.
                    "deps_mb": deps_mb(aid), "deps_note": deps_note(aid),
                    "pinned": ok, "licence": a["licence"], "source": a.get("source"),
                    "kind": a["kind"],
                    "optional": bool(a.get("optional")),
                    "requires": list(a.get("requires") or []),
                    "installable": ok, "why_not": why,
                    "runtime": a.get("runtime"),
                    "runtime_ready": _runtime_ready(a.get("runtime"))})
    return out


def _runtime_ready(name):
    """None when the artifact needs no runtime; else whether it is installed."""
    if not name:
        return None
    try:
        from agent_friday.services import model_download as _md
        return _md.runtime_binary(name) is not None
    except Exception:
        return False
