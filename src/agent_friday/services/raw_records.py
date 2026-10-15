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
# Bank exports named for the account or the activity ("Chase1234_Activity_2026.CSV",
# "activity.csv", "Checking1.csv"): spreadsheets only, a PDF called "activity" is anything.
_EXPORT_NAME = re.compile(r"activity|checking|savings|credit[ _-]?card|ledger|register", re.I)
EXPORT_SUFFIXES = frozenset({".csv", ".xlsx", ".xls"})
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
    if suffix in EXPORT_SUFFIXES and _EXPORT_NAME.search(p.stem or ""):
        return "finance"
    # Only a file whose name says nothing is opened to look (a mailbox has no
    # extension; an export sometimes ends in .txt): never every file a check sees.
    if suffix in ("", ".txt"):
        return _sniff(p)
    return None


_QUOTED = re.compile(r"\"([^\"\r\n]{1,400})\"|'([^'\r\n]{1,400})'")
_TOKEN_EDGES = "()[]{},;`|<>&"


_ATTR = re.compile(r"^[A-Za-z_][\w]*(?:\.[A-Za-z_]\w*)+$")     # e.msg, self.msg, record.msg


def named_in(text: str) -> list:
    """[(token, kind)] for every path-like piece of a command or program that
    names a raw record: a quoted name, a path with a folder in it, a pattern
    ("*.mbox"), or the name of a file that exists. "e.msg" in code is an attribute, and a
    URL is a download, not a read of something on this PC. A file is opened
    to look only when the token is an existing file whose name says nothing."""
    t = str(text or "")
    quoted = [a or b for a, b in _QUOTED.findall(t)]
    tokens = quoted + [w.strip(_TOKEN_EDGES) for w in _QUOTED.sub(" ", t).split()]
    quoted = set(quoted)
    out, seen = [], set()
    for tok in tokens:
        tok = tok.strip()
        if not tok or tok in seen or len(tok) > 400:
            continue
        seen.add(tok)
        if "://" in tok or "(" in tok:
            continue
        try:
            p = Path(tok).expanduser()
            pathy = "/" in tok or "\\" in tok or tok.startswith("~")
            globby = "*" in tok or "?" in tok
            exists = p.is_file()
            if tok not in quoted:        # a string in quotes is a name, never an attribute
                if not (pathy or globby or exists):
                    continue             # a bare word or an attribute is not a path
                if _ATTR.match(tok) and not exists:
                    continue
            if "." not in p.name and not exists:
                continue                 # a name that says nothing is judged only by its content
            kind = kind_of(p)
        except (OSError, ValueError):
            continue
        if kind:
            out.append((tok, kind))
    return out


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


# What a mail archive or a statement looks like when a program prints it.
_MBOX_SEP = re.compile(r"^From \S+@\S+ |^From - \w{3} ", re.M)
_HEADER = re.compile(r"^(?:From|To|Cc|Subject|Date|Message-ID|Received|Reply-To):[ \t]", re.M | re.I)
_OFX = re.compile(r"OFXHEADER:|<STMTTRN>|<TRNAMT>|<BANKTRANLIST>", re.I)
_QIF = re.compile(r"^!Type:(?:Bank|CCard|Cash|Invst)", re.M | re.I)
# grep's "path:line" / "path-line": a line that starts with such a file's name.
_RAW_PATH_PREFIX = re.compile(r"^[^\n:]*\.(?:mbox|mbx|eml|emlx|msg|pst|ost|ofx|qfx|qif|qbo)[:\-]\d", re.I | re.M)


def looks_like(text: str) -> str | None:
    """"mail" / "finance" when program output reads like a mail archive or a
    statement (grep through an mbox, a cat of a mailbox, an OFX download),
    else None. Judged on shape only: separators, several message headers,
    statement tags, or lines prefixed by such a file's name."""
    t = str(text or "")
    if not t:
        return None
    if _OFX.search(t) or _QIF.search(t):
        return "finance"
    if _MBOX_SEP.search(t):
        return "mail"
    if len({m.group(0).split(":")[0].lower() for m in _HEADER.finditer(t)}) >= 3:
        return "mail"
    if _RAW_PATH_PREFIX.search(t):
        return "mail" if re.search(r"^[^\n:]*\.(?:mbox|mbx|eml|emlx|msg|pst|ost)[:\-]\d", t, re.I | re.M) else "finance"
    return None
