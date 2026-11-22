#!/usr/bin/env python3
"""List records in a Friday data root that look like test data. It lists; it never changes or deletes anything.

Test runs that reached a real data root left synthetic people and notes behind (a "John Doe" entry in a briefing supplement, an
index line "Meeting notes for test", a "Jane Smith" in the trust graph). Whether each one is test data is the owner's call, so
this prints every suspect with where it is and why it was flagged, and stops there.

    python scripts/list_suspected_test_records.py [--root DIR] [--json] [--out FILE]

* Reads only. The one file it can write is the report named by --out, which must be OUTSIDE the data root (refused otherwise);
  there is no delete, move or edit anywhere in this script (tests/unit/test_list_suspected_test_records.py parses it to prove it).
* It looks in: the people graph and its legacy mirror (names, keys, aliases and the text of saved intelligence), and every
  Markdown file under wiki/ (lines and index entries).
* Stdlib only; the application package is not imported (importing it can migrate files in the home directory).
* Exit status 0 whatever it finds. Nothing is "fixed" by running it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

#: (label, pattern, why). Synthetic names and phrases a test writes; a real person can share a name, which is why the owner
#: approves each one.
PATTERNS = (
    ("synthetic person", re.compile(r"\b(?:john|jane)[ _-](?:doe|smith)\b", re.I), "a stock placeholder name"),
    ("synthetic person", re.compile(r"\b(?:test|example|sample|dummy)[ _-](?:user|person|contact|name)\b", re.I),
     "a placeholder person name"),
    ("test intel", re.compile(r"\btest intel\b|\bintel about (?:john|jane)\b", re.I), "placeholder saved intelligence"),
    ("test note", re.compile(r"\bmeeting notes for test\b|\bnotes for (?:a )?test\b", re.I), "a note titled for a test"),
)
PEOPLE_FILES = ("people_graph.json", "trust_graph.json")
EXCERPT = 90


def default_root() -> Path:
    env = os.environ.get("FRIDAY_HOME")
    return Path(os.path.expanduser(env)) if env else Path.home() / ".friday"


def _hits(text: str):
    for label, pattern, why in PATTERNS:
        if pattern.search(text):
            yield label, why


def _record(store: str, where: str, locator: str, label: str, why: str, text: str) -> dict:
    return {"store": store, "file": where, "locator": locator, "flag": label, "why": why,
            "excerpt": " ".join(text.split())[:EXCERPT]}


def scan_people(root: Path) -> list[dict]:
    out = []
    for name in PEOPLE_FILES:
        path = root / name
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        people = data.get("people") if isinstance(data, dict) else None
        entries = people.items() if isinstance(people, dict) else enumerate(people or [])
        for key, person in entries:
            if not isinstance(person, dict):
                continue
            label_text = " | ".join([str(key), str(person.get("name", ""))] + [str(a) for a in person.get("aliases") or []])
            body = json.dumps({k: v for k, v in person.items() if k in ("intelligence", "notes", "evidence")}, default=str)
            for text, part in ((label_text, "name"), (body, "saved text")):
                for label, why in _hits(text):
                    out.append(_record("people", name, "entry %r (%s)" % (key, part), label, why, text))
    return out


def scan_wiki(root: Path) -> list[dict]:
    out = []
    wiki = root / "wiki"
    if not wiki.is_dir():
        return out
    for path in sorted(wiki.rglob("*.md")):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        for number, line in enumerate(lines, 1):
            for label, why in _hits(line):
                out.append(_record("wiki", rel, "line %d" % number, label, why, line))
    return out


def scan(root: Path) -> list[dict]:
    return scan_people(root) + scan_wiki(root)


def _write_report(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _anchored(p: Path):
    """`p` as (its nearest existing ancestor, the names below it), or (None, ()) when nothing on the way up exists."""
    for i, ancestor in enumerate((p, *p.parents)):
        try:
            if ancestor.exists():
                return ancestor, p.parts[len(p.parts) - i:] if i else ()
        except OSError:
            continue
    return None, ()


def _fold(names) -> tuple:
    return tuple(os.path.normcase(n) for n in names)


def _inside(path: Path, root: Path) -> bool:
    """True when `path` is `root` or under it. Decided by which folder things ARE, not by how the path is spelled: the
    root's nearest existing ancestor (the root itself once it exists) is found by identity (os.path.samefile) among the
    path's existing ancestors, and the names below it must continue as the root's do. So an extended-length or localhost-UNC
    spelling of the root is the root, whether or not the root exists yet. Held by
    test_out_under_another_spelling_of_the_root_is_refused."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        pass
    root_anchor, root_rest = _anchored(root)
    if root_anchor is None:
        return False
    for i, ancestor in enumerate((path, *path.parents)):
        try:
            if ancestor.exists() and os.path.samefile(ancestor, root_anchor):
                rest = path.parts[len(path.parts) - i:] if i else ()
                return _fold(rest[:len(root_rest)]) == _fold(root_rest)
        except OSError:
            continue
    return False


def _names_a_stream(p: Path) -> bool:
    """`folder:name` or `file:name` addresses an NTFS stream on that folder or file, not a file of its own."""
    return os.name == "nt" and any(":" in part for part in p.parts[1:])


def _name_windows_trims(p: Path) -> bool:
    """Windows drops a trailing dot or space when it opens a name, so ".friday." is ".friday": such a name is refused
    outright, since no comparison of the text can be trusted to agree with what gets opened."""
    return os.name == "nt" and any(part.rstrip(". ") != part for part in p.parts[1:] if part not in (".", ".."))


def _out_refusal(out: Path, root: Path) -> str:
    """Why the report may not be written at `out`, or "" when it may. Never inside the scanned root, never inside the owner's
    data root (FRIDAY_HOME or ~/.friday) whatever root is scanned, and never through a second name for an existing file (a
    hard link would write through to it) or a stream on an existing folder or file."""
    if _names_a_stream(out):
        return "names a stream on a folder or file (a ':' in a name); name a plain new file"
    if _name_windows_trims(out):
        return "has a name ending in a dot or space, which Windows trims to another name; name a plain new file"
    for guarded in (root, default_root()):
        if _inside(out, guarded):
            return "is inside the data root %s; this script never writes there" % guarded
    try:
        if out.exists() and (not out.is_file() or out.stat().st_nlink > 1):
            return "is not a plain file of its own (a link or a folder); name a new file"
    except OSError:
        return "cannot be checked"
    return ""


def render(records: list[dict], root: Path) -> str:
    if not records:
        return "No suspected test records under %s. Nothing was changed.\n" % root
    lines = ["%d suspected test record(s) under %s. Nothing was changed; each needs the owner's yes before it is removed.\n"
             % (len(records), root)]
    for n, r in enumerate(records, 1):
        lines.append("%2d. [%s] %s, %s\n    flagged as %s (%s)\n    %s\n" % (
            n, r["store"], r["file"], r["locator"], r["flag"], r["why"], r["excerpt"]))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=None, help="the data root (default: FRIDAY_HOME or ~/.friday)")
    ap.add_argument("--json", action="store_true", help="print the records as JSON")
    ap.add_argument("--out", type=Path, default=None, help="also write the report here (must be outside the data root)")
    args = ap.parse_args(argv)
    root = args.root or default_root()
    if args.out is not None:
        refusal = _out_refusal(args.out, root)
        if refusal:
            print("refused: --out %s %s" % (args.out, refusal), file=sys.stderr)
            return 2
    records = scan(root)
    text = json.dumps(records, indent=2) + "\n" if args.json else render(records, root)
    print(text, end="")
    if args.out is not None:
        _write_report(args.out, text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
