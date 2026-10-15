"""One key for one model, however its id is spelled.

The same weights reach Friday under more than one name. The installer's
first-run request and the shortlist say ``bonsai2:27b`` (an Ollama-style
``name:tag``); a model registered by hand from its GGUF is often
``bonsai2-27b``; the Ollama daemon may report ``bonsai2:27b:latest`` or
``Bonsai2:27B``. Compared literally these are three different models, so a
hand-installed brain never satisfied the seat that asked for it and the
first-run card said "did not finish downloading" over a model that was
answering.

``canonical(model_id)`` maps every equivalent spelling to one comparison key.
It is for COMPARING ids only: display names, registry keys and settings keep
whatever they were written as, and a caller that needs the installed record
uses ``find_equivalent`` to get the real key back.

Rules, in order:
  * surrounding whitespace is dropped and case is folded;
  * a trailing ``:latest`` (Ollama's implicit tag) is dropped;
  * the ``:`` tag separator and ``_`` read as ``-``;
  * a known alias maps to its family's key (``KNOWN_ALIASES``).

A namespaced id (``hf:publisher-model``, ``voice-front:qwen3-1.7b``) keeps its
namespace and its first ``:`` in the key, so it can only ever equal another
id from the same namespace: ``hf:foo`` is not ``hf-foo``. Only the namespaces
in ``NAMESPACES`` are read this way; any other ``name:tag`` is a tag.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

#: Spellings of the same weights that the separator rules alone do not join,
#: already in key form on both sides.
KNOWN_ALIASES = {
    "bonsai-2-27b": "bonsai2-27b",
    "ternary-bonsai-2-27b": "bonsai2-27b",
    "ternary-bonsai2-27b": "bonsai2-27b",
}

#: Prefixes that name WHERE a model comes from, written ``<namespace>:<id>``:
#: ``hf:`` (a Hugging Face download, services/model_download), ``voice-front:``
#: (the voice front's Arbiter seat, services/voice_front), ``local:`` /
#: ``cloud:`` / ``openrouter:`` (author and route tags), and the local
#: provider names. Lower case, as compared.
NAMESPACES = frozenset({
    "hf", "voice-front", "local", "cloud", "openrouter",
    "ollama", "ollama-local", "arbiter-local", "llama-cpp-local",
})

_SEP = re.compile(r"[:_]+")
_DASHES = re.compile(r"-{2,}")


def _key(s: str) -> str:
    s = _DASHES.sub("-", _SEP.sub("-", s)).strip("-")
    return KNOWN_ALIASES.get(s, s)


def canonical(model_id) -> str:
    """The comparison key for ``model_id``; ``""`` for an empty id."""
    s = str(model_id or "").strip().lower()
    if not s:
        return ""
    while s.endswith(":latest"):
        s = s[: -len(":latest")]
    ns, sep, rest = s.partition(":")
    if sep and ns.strip() in NAMESPACES and rest.strip():
        inner = _key(rest.strip())
        return (ns.strip() + ":" + inner) if inner else ""
    return _key(s)


def same(a, b) -> bool:
    """True when ``a`` and ``b`` name the same model."""
    ka, kb = canonical(a), canonical(b)
    return bool(ka) and ka == kb


def find_equivalent(wanted, candidates: Iterable) -> Optional[str]:
    """The candidate that names the same model as ``wanted``, as the candidate
    spells it; an exact match wins over an equivalent one. None if absent."""
    if not wanted:
        return None
    pool = [str(c) for c in candidates if c]
    if str(wanted) in pool:
        return str(wanted)
    key = canonical(wanted)
    if not key:
        return None
    for c in pool:
        if canonical(c) == key:
            return c
    return None
