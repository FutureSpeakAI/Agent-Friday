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
Silero VAD (MIT), misaki (Apache-2.0 per its package metadata; the spec
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
        "revision": "asr-models",
        "sha256": None,
        "size_mb": 682,
        "licence": "OpenMDW-1.1",
        "dest": "voice/ear/nemotron-3.5-streaming-int8",
        "requires": ["sherpa-onnx"],
    },
    "sherpa-onnx": {
        "label": "sherpa-onnx runtime (streaming recognizer)",
        "kind": "pip",
        "package": "sherpa-onnx",
        "version": None,
        "sha256": None,
        "size_mb": 30,
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
    "voice-front-4b": {
        "label": "Qwen3-4B-Instruct-2507 Q4_K_M (voice front)",
        "kind": "file",
        "source": "Qwen/Qwen3-4B-Instruct-2507-GGUF (Hugging Face)",
        "url": ("https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507-GGUF/resolve/"
                "{revision}/Qwen3-4B-Instruct-2507-Q4_K_M.gguf"),
        "revision": None,
        "sha256": None,
        "size_mb": 2500,
        "licence": "Apache-2.0",
        "dest": "models/gguf/Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
    },
    "voice-front-1.7b": {
        "label": "Qwen3-1.7B Q4_K_M (voice front beside the brain)",
        "kind": "file",
        "source": "Qwen/Qwen3-1.7B-GGUF (Hugging Face)",
        "url": ("https://huggingface.co/Qwen/Qwen3-1.7B-GGUF/resolve/"
                "{revision}/Qwen3-1.7B-Q4_K_M.gguf"),
        "revision": None,
        "sha256": None,
        "size_mb": 1100,
        "licence": "Apache-2.0",
        "dest": "models/gguf/Qwen3-1.7B-Q4_K_M.gguf",
    },
    "misaki": {
        "label": "misaki G2P for Kokoro",
        "kind": "pip",
        "package": "misaki",
        "version": None,
        "sha256": None,
        "size_mb": 5,
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
                    "licence": a["licence"], "source": a.get("source"),
                    "optional": bool(a.get("optional")),
                    "installable": ok, "why_not": why})
    return out
