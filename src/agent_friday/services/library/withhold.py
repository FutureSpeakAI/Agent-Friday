"""Credentials never enter the Library's index.

A private key or a vendor token pasted inside an ordinary document (notes, a Word file,
a PDF) is document text, and the index quotes document text to a model. So extracted text
goes through `credential_paths.redact_secrets`, the call `read_file` uses, after
extraction and before anything is made from it: the sections, passages, vectors,
full-text rows, profiles and titles are all built from the withheld text, and none of
them holds more than the marker where the credential was.

The cutting is done on the whole document at once. A key's armour and its body are only
recognisable together, and a PDF or a Word file that holds a pasted key has one block per
line, so the blocks are joined, cut and split again. Each join carries the number of the
block that follows it, written in tabs and spaces (extraction never produces a tab, and
every pattern reads them as the whitespace they are). A join the cut swallows leaves the
text on both sides of the cut attached to the block it began in; a join that survives
names its block exactly.

Anything this module cannot cut cleanly is not kept: a check that fails fails the
document, and a document that holds nothing but credentials is not a document.
"""
from __future__ import annotations

import re

from agent_friday.services import credential_paths, secret_patterns

MARKER = secret_patterns.WITHHELD

_SEAM = "\n\t\t"


class Withheld(Exception):
    """The text holds a credential that cannot be cut out cleanly. `str(e)` is the reason shown."""


def text(value: str) -> str:
    """`value` with key blocks and vendor tokens replaced by the marker."""
    return credential_paths.redact_secrets(value) if value else value


def title(value: str) -> str:
    """A title with its credentials withheld. A check that cannot run leaves no title at all."""
    try:
        return text(value)
    except Exception:  # noqa: BLE001 - a title is never worth a leak
        return "Untitled"


def _seam(index: int, width: int) -> str:
    return _SEAM + format(index, "b").zfill(width).replace("0", " ").replace("1", "\t")


def blocks(items: list[dict]) -> list[dict]:
    """`items` with every credential in their text cut out, across block boundaries.

    Returns `items` itself when nothing was found. Otherwise new blocks, in order and
    renumbered, each keeping the metadata (page, box, time, kind) of the block its text
    began in; a block that was only a credential stays as the marker, so a footnote can
    still point at where it was. Raises `Withheld` when what is left is nothing but
    markers (a document made of credentials, or armour chunked to defeat the patterns,
    which the cutter withholds whole).
    """
    if not items:
        return items
    n = len(items)
    width = max(1, (n - 1).bit_length())
    parts = [(b.get("text") or "").replace("\t", " ") for b in items]
    joined = parts[0] + "".join(_seam(j, width) + parts[j] for j in range(1, n))
    cut = credential_paths.redact_secrets(joined)
    if cut == joined:
        return items
    pieces = re.split(r"\n\t\t([ \t]{%d})" % width, cut)
    heads = [0] + [int(code.replace(" ", "0").replace("\t", "1"), 2) for code in pieces[1::2]]
    if any(a >= b for a, b in zip(heads, heads[1:])) or heads[-1] >= n:
        raise Withheld("the check for keys lost its place in the document")
    out: list[dict] = []
    for head, body in zip(heads, pieces[0::2]):
        if "\t" in body:                                  # a seam the cut clipped: whitespace, never text
            body = re.sub(r"\n\t\t[ \t]*", "\n", body).replace("\t", " ")
        if not body.strip():
            continue
        block = dict(items[head])
        block["text"] = body
        if block.get("header"):
            block["header"] = text(block["header"])
        block["ord"] = len(out)
        out.append(block)
    if not any(re.sub(re.escape(MARKER), "", b["text"]).strip() for b in out):
        raise Withheld("holds a key or token that can't be cut out cleanly, so it isn't kept")
    return out
