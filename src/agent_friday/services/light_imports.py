"""Imports that skip what a voice worker never uses.

``import faster_whisper`` pulls in ``ctranslate2``, whose package ``__init__``
imports ``ctranslate2.converters`` (the model converters: ``transformers``, ~6
s warm and 25 s on a cold disk) and whose ``specs.model_spec`` imports
``torch`` if it is there (~3 s). A whisper worker transcribes with
CTranslate2 alone: it converts nothing and needs no torch. Measured with
``-X importtime`` on this machine: 10.0 s warm (26 s cold) for the plain import
against 0.5 s with the converters stubbed and torch hidden, and 52 MiB resident
against 543.

Only for a single-threaded process that nothing else is importing into (a
worker loads before any other thread exists): ``torch`` is hidden for the
duration of one import and restored after, so a thread importing torch at the
same moment would be refused.
"""
from __future__ import annotations

import sys
import types


def faster_whisper_model():
    """``faster_whisper.WhisperModel`` without ctranslate2's converters or torch."""
    hidden = []
    if "ctranslate2.converters" not in sys.modules:
        sys.modules["ctranslate2.converters"] = types.ModuleType("ctranslate2.converters")
    if "torch" not in sys.modules:
        sys.modules["torch"] = None          # `import torch` raises ImportError
        hidden.append("torch")
    try:
        from faster_whisper import WhisperModel
    finally:
        for name in hidden:
            if sys.modules.get(name, 0) is None:
                del sys.modules[name]
    return WhisperModel
