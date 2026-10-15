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


#: How many files one pattern ("~/Downloads/*.csv") is expanded to, to judge it.
GLOB_LIMIT = 500
_PS_ENV = re.compile(r"\$env:([A-Za-z_][A-Za-z0-9_]{0,60})", re.I)


def expand(tok: str) -> str:
    """A path as the shell would see it: ~, %VAR%, $VAR and PowerShell's $env:VAR."""
    import os
    t = _PS_ENV.sub(lambda m: os.environ.get(m.group(1), m.group(0)), str(tok))
    return os.path.expanduser(os.path.expandvars(t))


def named_in(text: str, cwd=None) -> list:
    """[(label, kind, path or None)] for every path-like piece of a command or
    program that names a raw record: a quoted name, a path with a folder in
    it, the name of a file that exists, or a pattern ("*.mbox", or
    "~/Downloads/*.csv" when one of the files it matches is a statement).
    "e.msg" in code is an attribute, and a URL is a download, not a read of
    something on this PC. A relative name is judged in `cwd` (the folder the
    command runs in). A file is opened to look only when it exists and its
    name says nothing."""
    import glob as _glob
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
        if "://" in tok or "(" in tok or "\x00" in tok:
            continue
        if tok.replace("/", "\\").startswith("\\\\"):
            continue                     # a network or device path is never touched to look
        try:
            p = Path(expand(tok))
            if cwd and not p.is_absolute():
                p = Path(cwd) / p
            pathy = "/" in tok or "\\" in tok or tok.startswith(("~", "$", "%"))
            globby = "*" in tok or "?" in tok
            if globby and sum(1 for part in p.parts[:-1] if "*" in part or "?" in part) > 1:
                out.append((tok, "too_many", None))          # a pattern over many folders: not walked
                continue
            if globby:
                kind = kind_of(Path(tok).name.replace("*", "x").replace("?", "x")) \
                    if "." in Path(tok).name else None
                if kind:
                    out.append((tok, kind, None))
                    continue
                import itertools
                matches = list(itertools.islice(_glob.iglob(str(p)), GLOB_LIMIT + 1))
                if len(matches) > GLOB_LIMIT:
                    out.append((tok, "too_many", None))      # too many files to judge: not run
                    continue
                for m in matches:
                    k = kind_of(m)
                    if k:
                        out.append((m, k, Path(m)))
                continue
            exists = p.is_file()
            if tok not in quoted:        # a string in quotes is a name, never an attribute
                if not (pathy or exists):
                    continue             # a bare word or an attribute is not a path
                if _ATTR.match(tok) and not exists:
                    continue
            if "." not in p.name and not exists:
                continue                 # a name that says nothing is judged only by its content
            kind = kind_of(p)
        except (OSError, ValueError):
            continue
        if kind:
            out.append((tok, kind, p if p.is_file() else None))
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
# Anchored at the start of a line, so a diff ("+<TRNAMT>"), a grep of source
# code or a quoted string does not count.
_MBOX_SEP = re.compile(r"^From \S+@\S+ |^From - \w{3} ", re.M)
_ANY_HEADER = re.compile(r"^[A-Za-z][\w-]{0,40}:(?:[ \t]|$)")
_CORE_HEADERS = {"from", "to", "cc", "subject", "date", "message-id", "received", "reply-to"}
_OFX = re.compile(r"^(?:OFXHEADER:\d|<OFX>|<STMTTRN>|<BANKTRANLIST>)", re.M | re.I)
_QIF = re.compile(r"^!Type:(?:Bank|CCard|Cash|Invst)\s*$", re.M | re.I)
#: How many lines of a program's output are looked at, line by line.
OUTPUT_LINES = 20000


def _header_block(lines: list) -> bool:
    """A run of message headers (folded continuation lines and any other
    header inside it included) with From and at least three different
    message headers: a message, not a log line that happens to say "Date:"."""
    names: set = set()
    for line in lines:
        if line[:1] in (" ", "\t") and names:
            continue                     # a folded header line
        if _ANY_HEADER.match(line):
            names.add(line.split(":", 1)[0].lower())
            if "from" in names and len(names & _CORE_HEADERS) >= 3:
                return True
            continue
        names = set()
    return False


#: Mail clients' own names for a mailbox file with no extension.
MAILBOX_NAMES = frozenset({"inbox", "sent", "sent mail", "sent items", "drafts", "trash", "archive",
                           "archives", "all mail", "junk", "spam", "outbox", "deleted items"})
_LINE_NO = re.compile(r"\d{1,9}[:-]")


def _split_prefix(line: str):
    """(path, rest) for a line grep -r / findstr / rg printed as "path:line"
    or "path:number:line", else None. The path must look like one: no
    leading space, and a space only inside a path with a folder in it."""
    if not line or line[0] in " \t":
        return None
    start = 2 if line[1:3] in (":\\", ":/") else 0     # "C:\..." keeps its drive colon
    cut = line.find(":", start)
    if cut <= 0 or cut > 400:
        return None
    name, rest = line[:cut], line[cut + 1:]
    if " " in name and "/" not in name and "\\" not in name:
        return None                      # "Wrote statement.pdf: 3 pages" is a sentence
    return name, rest


def _prefixed_by_record(line: str) -> str | None:
    """The kind of the file a "path:line" output line says it came from,
    judged by its name ("e.msg:" in code is an attribute, not a message)."""
    sp = _split_prefix(line)
    if sp is None:
        return None
    name, rest = sp
    base = re.split(r"[\\/]", name)[-1]          # either separator, on any system
    if base.casefold() in MAILBOX_NAMES and ("/" in name or "\\" in name):
        # "Mail/Inbox:12:..." is a mailbox; "Archive:  foo.zip" (unzip) and
        # "Inbox: 3 unread" are not.
        return "mail"
    suffix = Path(name).suffix.lower()
    if suffix in ("", ".txt"):
        return None
    if suffix == ".msg" and _ATTR.match(name) and not _LINE_NO.match(rest):
        return None
    try:
        return kind_of(name)
    except (OSError, ValueError):
        return None


def looks_like(text: str) -> str | None:
    """"mail" / "finance" when program output reads like a mail archive or a
    statement (a recursive grep through a mail folder or statement exports,
    a cat of a mailbox or a message, an OFX download), else None. Judged on
    shape and on the names of the files lines say they came from."""
    t = str(text or "")
    if not t:
        return None
    if _OFX.search(t) or _QIF.search(t):
        return "finance"
    if _MBOX_SEP.search(t):
        return "mail"
    lines = t.splitlines()[:OUTPUT_LINES]
    if _header_block(lines):
        return "mail"
    # The same shapes inside grep's "path:" / "path:number:" prefix: a mailbox
    # whose name says nothing still prints its separators and headers.
    bare = []
    for line in lines:
        sp = _split_prefix(line)
        rest = sp[1] if sp else line
        m = _LINE_NO.match(rest)
        bare.append(rest[m.end():] if m else rest)
    if _MBOX_SEP.search("\n".join(bare)) or _header_block(bare):
        return "mail"
    for line in lines:
        k = _prefixed_by_record(line)
        if k:
            return k
    return None
