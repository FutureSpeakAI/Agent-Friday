"""Raw personal records: whole mail archives and bank statements.

Owner decision O7 (2026-10-15), first-run spec E-03: even under "cloud for
everything", a person's raw records go to a cloud model only with a yes for
that one source. A folder's cloud permission does not cover them, and the
posture does not either; the approval card names the file, what it is, and
which provider would read it.

Recognised by what the file is, never by what a model says it is: the mail
archive formats by extension, and statements by extension plus a name that
says so (a PDF called "statement" or "transactions"). A file this misses
gates exactly as it did before; a file this catches only asks first.
"""
from __future__ import annotations

import re
from pathlib import Path

MAIL_SUFFIXES = frozenset({".mbox", ".mbx", ".eml", ".emlx", ".msg", ".pst", ".ost"})
FINANCE_SUFFIXES = frozenset({".ofx", ".qfx", ".qif", ".qbo"})
STATEMENT_SUFFIXES = frozenset({".pdf", ".csv", ".xlsx", ".xls"})
_STATEMENT_NAME = re.compile(r"statement|stmt|transactions?|account[ _-]?history|bank", re.I)
#: How much of a file without a telling extension is read to recognise it.
SNIFF_BYTES = 512

KIND_WORDS = {"mail": "a mail archive", "finance": "a bank or card statement"}


def kind_of(path) -> str | None:
    """"mail", "finance" or None for `path`."""
    try:
        p = Path(path)
    except TypeError:
        return None
    suffixes = {x.lower() for x in p.suffixes}
    suffix = p.suffix.lower()
    if suffix in MAIL_SUFFIXES or suffixes & MAIL_SUFFIXES:          # "x.mbox.txt" too
        return "mail"
    if suffix in FINANCE_SUFFIXES or suffixes & FINANCE_SUFFIXES:
        return "finance"
    if suffix in STATEMENT_SUFFIXES and _STATEMENT_NAME.search(p.stem or ""):
        return "finance"
    # Only a file whose name says nothing is opened to look (a mailbox has no
    # extension; an export sometimes ends in .txt): never every file a check sees.
    if suffix in ("", ".txt"):
        return _sniff(p)
    return None


def _sniff(p: Path) -> str | None:
    """A file whose name says nothing: an mbox starts with a "From " line
    (Thunderbird's Inbox, the mbox inside an Apple Mail folder), an OFX
    statement with its header."""
    try:
        if not p.is_file():
            return None
        with open(p, "rb") as f:
            head = f.read(SNIFF_BYTES)
    except OSError:
        return None
    head = head.lstrip(b"\xef\xbb\xbf").lstrip()
    first = head.split(b"\n", 1)[0]
    # mbox separators: "From addr date", "From - date" (Thunderbird), "From MAILER-DAEMON ..."
    if first.startswith(b"From ") and (b"@" in first or first.startswith((b"From - ", b"From MAILER-DAEMON"))):
        return "mail"
    if b"OFXHEADER" in head[:100] or head.lstrip().upper().startswith(b"<OFX>"):
        return "finance"
    return None
