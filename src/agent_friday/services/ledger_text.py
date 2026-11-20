"""ledger_text: free text is kept in the action ledger as a keyed hash, never as the text.

Stephen's decision (c) on the evidence pack (#868): the ledger entry that is hashed, signed and chained must not hold an
owner's words, so an export of whole entries (the chain has to verify) never holds them either. A record's free-text
fields are named here (FREE_TEXT_KEYS); every string under one of them is replaced, when the entry is written, by
"ft1:" + 64 hex (a keyed digest the ledger computes). The value is still a string, so every reader's types hold. The
text itself stays on this PC in a local sidecar (action_ledger's texts.jsonl) that the ledger's own readers use to show
what Friday did, and that no export reads. Held by tests/unit/test_ledger_text.py and
tests/unit/test_action_ledger_free_text.py.

Known limits (named, not hidden): the key list is a deny list, so a NEW record key that carries text must be added here;
dict KEYS are not hidden (items[0]["path"] style keys are structure; a map keyed by host name keeps the host); the sidecar is
plaintext on this PC, as the live ledger was before; a signed receipt whose inner HMAC covers the text verifies only while
the sidecar can restore it.

This module is pure: it knows the keys and the shape of a hidden value, and nothing about keys, files or the network.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Callable

#: The fields of a ledger record that hold an owner's words, paths or titles. A new kind of record that carries text under
#: a new key adds the key here: the writer then hides it and the export refuses an older entry that kept it raw.
FREE_TEXT_KEYS = frozenset({"summary", "items", "what_ran", "critique", "artifacts", "note", "removed", "path", "paths",
                            "target", "detail", "item", "why"})
PREFIX = "ft1:"
_HIDDEN = re.compile(r"^ft1:[0-9a-f]{64}$")


def is_hidden(value: Any) -> bool:
    return isinstance(value, str) and bool(_HIDDEN.match(value))


def hide(rec: Any, digest: Callable[[str], str]) -> tuple[Any, dict]:
    """(the record with every string under a free-text key replaced by its hidden value, {digest: text} for the
    sidecar). `digest(text)` returns 64 hex characters. The input is not modified; a hidden value is not hidden again."""
    texts: dict[str, str] = {}

    def strings(node):
        if isinstance(node, str):
            if not node or is_hidden(node):
                return node
            d = digest(node)
            texts[d] = node
            return PREFIX + d
        if isinstance(node, Mapping):
            return {k: strings(v) for k, v in node.items()}
        if isinstance(node, (list, tuple)):
            return [strings(v) for v in node]
        return node

    def walk(node):
        if isinstance(node, Mapping):
            return {k: (strings(v) if k in FREE_TEXT_KEYS else walk(v)) for k, v in node.items()}
        if isinstance(node, (list, tuple)):
            return [walk(v) for v in node]
        return node

    return walk(rec), texts


def show(rec: Any, text_of: Callable[[str], str | None]) -> Any:
    """The record with every hidden value the sidecar knows turned back into its text (`text_of(digest)`); one it does not
    know stays as it is."""
    if is_hidden(rec):
        found = text_of(rec[len(PREFIX):])
        return found if found is not None else rec
    if isinstance(rec, Mapping):
        return {k: show(v, text_of) for k, v in rec.items()}
    if isinstance(rec, (list, tuple)):
        return [show(v, text_of) for v in rec]
    return rec


def raw_text_key(node: Any) -> str | None:
    """The first free-text key that still holds text that was never hidden (an entry written before hiding existed), or
    None."""
    def any_raw(value) -> bool:
        if isinstance(value, str):
            return bool(value) and not is_hidden(value)
        if isinstance(value, Mapping):
            return any(any_raw(v) for v in value.values())
        if isinstance(value, (list, tuple)):
            return any(any_raw(v) for v in value)
        return False

    if isinstance(node, Mapping):
        for key, value in node.items():
            if key in FREE_TEXT_KEYS:
                if any_raw(value):
                    return key
            else:
                nested = raw_text_key(value)
                if nested:
                    return nested
    elif isinstance(node, (list, tuple)):
        for item in node:
            nested = raw_text_key(item)
            if nested:
                return nested
    return None
