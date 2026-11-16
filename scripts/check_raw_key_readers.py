#!/usr/bin/env python3
"""Static check: no NEW module reads a raw provider key.

Why this exists
----------------
"No agent holds a key" is a written rule. Today about thirty modules read a key from the environment, the
settings, or the credential store's getter (Compressor eval, finding K2). Each is a place a key can be copied,
logged or handed to a child process. The long-term fix is the call-time injection proxy (salon_proxy.py,
widened in docs/PROXY_WIDENING_PLAN.md); until it lands this check stops the list growing.

What it checks
---------------
A "raw read" in a file under src/agent_friday is any of:
  * ``os.environ.get / os.getenv / os.environ[...]`` of a literal name ending in API_KEY, APIKEY, _TOKEN,
    _SECRET, PASSPHRASE or ACCESS_KEY;
  * a call to ``get_provider_key(...)`` (the credential store's getter);
  * a read of an attribute or name that ends in API_KEY (for example ``core.ANTHROPIC_API_KEY``).
Every file with a raw read must be listed in scripts/raw_key_readers.allow.json with a reason, an expiry date and
who approved it (CODE_QUALITY Q3, Q5). The list can only shrink:
  1. a file with a raw read that is not listed fails;
  2. a listed file that no longer has a raw read fails (delete its entry);
  3. a listed entry past its expiry, or without a reason, an approver or a date, fails.
Guarded by tests/unit/test_check_raw_key_readers.py. Standard library only; no application import.
Exit 0 and print "OK - ..." when clean; otherwise each problem goes to stderr and the exit code is 1.
"""
from __future__ import annotations

import ast
import datetime
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "agent_friday"
ALLOWLIST = Path(__file__).resolve().parent / "raw_key_readers.allow.json"

_ENV_NAME = re.compile(r"(API_?KEY|_TOKEN|_SECRET|PASSPHRASE|ACCESS_KEY)$", re.I)
_KEY_ATTRIBUTE = re.compile(r"^[A-Z0-9_]*API_?KEY$")
_ENV_CALLS = ("os.environ.get", "os.getenv", "environ.get", "os.environ.pop", "os.environ.setdefault")
_ENV_MAPS = ("os.environ", "environ")
_STORE_GETTER = "get_provider_key"


def _literal(node: ast.AST):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def raw_reads(tree: ast.AST) -> list:
    """(line, what) pairs for every raw key read in one parsed module."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            callee = ast.unparse(node.func)
            name = _literal(node.args[0]) if node.args else None
            if callee in _ENV_CALLS and name and _ENV_NAME.search(name):
                found.append((node.lineno, f"environment read of {name}"))
            elif callee.split(".")[-1] == _STORE_GETTER:
                found.append((node.lineno, f"call to {_STORE_GETTER}"))
        elif isinstance(node, ast.Subscript) and ast.unparse(node.value) in _ENV_MAPS:
            name = _literal(node.slice)
            if name and _ENV_NAME.search(name) and isinstance(node.ctx, ast.Load):
                found.append((node.lineno, f"environment read of {name}"))
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load) and _KEY_ATTRIBUTE.match(node.attr):
            found.append((node.lineno, f"read of {node.attr}"))
    return found


def readers_in(src: Path) -> dict:
    """{posix path relative to src.parent.parent: [(line, what), ...]} for every file with a raw read."""
    base = src.parent.parent
    out = {}
    for path in sorted(src.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except SyntaxError:
            continue
        reads = raw_reads(tree)
        if reads:
            out[path.relative_to(base).as_posix()] = reads
    return out


def problems(readers: dict, allow: dict, today: datetime.date) -> list:
    """Every way the tree and the allow-list disagree, as readable lines."""
    out = []
    for path, reads in sorted(readers.items()):
        if path not in allow:
            line, what = reads[0]
            out.append(f"{path}:{line}: new raw key reader ({what}); route the call through the proxy instead, "
                       f"or ask the release lead to list it with a reason and an expiry")
    for path, entry in sorted(allow.items()):
        if path not in readers:
            out.append(f"{path}: listed but no longer reads a raw key; delete its entry (the list only shrinks)")
            continue
        missing = [field for field in ("reason", "expires", "approved_by") if not str(entry.get(field, "")).strip()]
        if missing:
            out.append(f"{path}: allow-list entry lacks {', '.join(missing)}")
            continue
        try:
            expires = datetime.date.fromisoformat(entry["expires"])
        except ValueError:
            out.append(f"{path}: expires is not a date (YYYY-MM-DD)")
            continue
        if expires < today:
            out.append(f"{path}: allow-list entry expired {expires}; move the reader to the proxy or renew with approval")
    return out


def main() -> int:
    allow = json.loads(ALLOWLIST.read_text(encoding="utf-8"))["readers"]
    found = problems(readers_in(SRC), allow, datetime.date.today())
    if found:
        print("\n".join(found), file=sys.stderr)
        return 1
    print(f"[raw-key-readers] OK - {len(allow)} listed reader(s), none new")
    return 0


if __name__ == "__main__":
    sys.exit(main())
