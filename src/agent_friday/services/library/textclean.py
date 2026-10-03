"""Text hygiene for document text that is shown or fed to a model."""
from __future__ import annotations

import re
import unicodedata

# Control characters except tab and newline, the bidirectional overrides and
# isolates (they reorder what a reader sees), and the zero-width characters.
_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f​-‏‪-‮⁠-⁩﻿]")
_WS = re.compile(r"[ \t ]+")


def clean_text(s: str) -> str:
    """Keep the words, drop what could spoof or hide them."""
    s = unicodedata.normalize("NFC", s)
    s = _BAD.sub("", s)
    s = _WS.sub(" ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def one_line(s: str, limit: int) -> str:
    """Whitespace collapsed to single spaces, cut at a word boundary."""
    s = _WS.sub(" ", _BAD.sub("", unicodedata.normalize("NFC", s)).replace("\n", " ")).strip()
    s = re.sub(r"\s+", " ", s)
    if len(s) <= limit:
        return s
    cut = s[:limit].rsplit(" ", 1)[0]
    return (cut or s[:limit]).rstrip(" ,;:-") + "…"


def safe_title(name: str) -> str:
    return one_line(name, 160) or "Untitled"
