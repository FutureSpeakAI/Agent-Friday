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
_STATEMENT_NAME = re.compile(r"statement|transactions?|account[ _-]?history|bank", re.I)

KIND_WORDS = {"mail": "a mail archive", "finance": "a bank or card statement"}


def kind_of(path) -> str | None:
    """"mail", "finance" or None for `path`."""
    try:
        p = Path(path)
    except TypeError:
        return None
    suffix = p.suffix.lower()
    if suffix in MAIL_SUFFIXES:
        return "mail"
    if suffix in FINANCE_SUFFIXES:
        return "finance"
    if suffix in STATEMENT_SUFFIXES and _STATEMENT_NAME.search(p.stem or ""):
        return "finance"
    return None
