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
said MIT), faster-whisper turbo (MIT).
"""
from __future__ import annotations

#: kind: "file" (one file to ``dest``), "archive" (a .tar.bz2 unpacked under
#: ``dest``), "pip" (``package`` pinned with ``==``). ``dest`` is relative to
#: the Friday runtime directory. ``revision`` and ``sha256`` are the pins.
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
    "misaki": {
        "label": "misaki G2P for Kokoro",
        "kind": "pip",
        "package": "misaki",
        # 0.7.4 is the version Kokoro runs with on this stack; 0.9.x moves
        # the English G2P to phonemizer-fork and is not verified here.
        "version": "0.7.4",
        "sha256": None,
        "size_mb": 4,
        # The package's own metadata (misaki 0.7.4) says Apache 2.0.
        "licence": "Apache-2.0",
        "source": "PyPI",
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
        "optional": True,
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


#: What each artifact is for, in plain words, for the Settings screen.
PURPOSE = {
    "voice-ear-streaming": "Ear: turns your speech into text as you talk, on this computer.",
    "sherpa-onnx": "Ear runtime: the small program library the ear model runs in.",
    "voice-vad-v6": "Ear helper: hears when you start and stop speaking.",
    "voice-front-bonsai-1.7b": "Fast reply model: answers voice questions and runs quick tools (the default; small enough to sit beside the main model).",
    "voice-front-4b": "Fast reply model: a larger Qwen3 alternative (pauses the main model while you talk).",
    "voice-front-1.7b": "Fast reply model: the Qwen3-1.7B alternative.",
    "misaki": "Voice helper: tells the Kokoro voice how to pronounce words.",
    "espeak-ng-helper": "Voice helper (optional): pronounces unusual names.",
    "espeakng-loader": "Voice helper (optional): the library the name pronouncer needs.",
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
    return "%s==%s" % (a["package"], a["version"])


def public_rows() -> list:
    """What the Models screen shows before an install: never a hash-less
    promise, always the size, licence and source."""
    out = []
    for aid, a in ARTIFACTS.items():
        ok, why = pinned(aid)
        out.append({"id": aid, "label": a["label"], "size_mb": a["size_mb"],
                    "size_bytes": size_bytes(aid), "purpose": PURPOSE.get(aid, ""),
                    "pinned": ok, "licence": a["licence"], "source": a.get("source"),
                    "kind": a["kind"],
                    "optional": bool(a.get("optional")),
                    "requires": list(a.get("requires") or []),
                    "installable": ok, "why_not": why})
    return out
