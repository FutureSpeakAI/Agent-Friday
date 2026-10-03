#!/usr/bin/env python3
"""Static check: governance never reads trust.

Why this exists
----------------
Trust may add caution (a label, a check, a card line, an upgrade from
confirm to card). It can never clear an approval card, lower a tier, grant
anything or change a provenance class: an appeal that can clear a gate
weakens that gate. The simplest way to keep that true is that the code that
decides (the action gate, the taint ledger, approvals, the egress gate)
never imports the trust package or reads a trust file at all. A later phase
may add ONE seam (`governance/trust_hints.py`) that can only raise; until it
exists, nothing in these modules may touch trust.

What it checks
---------------
1. No module under `src/agent_friday/governance/`, nor `services/taint.py`,
   `services/approvals.py` or `services/egress_gate.py`, imports
   `agent_friday.trust`, `agent_friday.people_graph` or
   `agent_friday.source_trust_graph` (top level or inside a function: the
   whole AST is walked, because lazy imports are common here).
2. None of them names a trust file (`people_graph.json`, `trust_graph.json`,
   `source_trust.json`, `trust/log`, `trust/agents.json`) in a string literal.

Exit 0 and print "OK - ..." when clean; otherwise print each offender to
stderr and exit 1. Standard library only; no application import.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "agent_friday"

GUARDED = [
    *sorted((SRC / "governance").glob("*.py")),
    SRC / "services" / "taint.py",
    SRC / "services" / "approvals.py",
    SRC / "services" / "egress_gate.py",
]
#: The one future seam that may read trust, and may only raise a verdict.
ALLOWED_SEAM = SRC / "governance" / "trust_hints.py"

FORBIDDEN_MODULES = ("agent_friday.trust", "agent_friday.people_graph",
                     "agent_friday.source_trust_graph", "agent_friday.source_trust_federation")
FORBIDDEN_STRINGS = ("people_graph.json", "trust_graph.json", "source_trust.json",
                     "trust/log", "trust/agents.json", "trust\\log", "trust\\agents.json")


def _imports(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            yield node.lineno, mod
            for alias in node.names:
                yield node.lineno, f"{mod}.{alias.name}" if mod else alias.name


def _strings(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value


def check_file(path: Path) -> list:
    """Offending (line, reason) pairs for one file."""
    try:
        # utf-8-sig: several governance files carry a byte-order mark.
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    except SyntaxError as e:
        return [(e.lineno or 0, f"could not parse: {e}")]
    out = []
    for lineno, name in _imports(tree):
        if any(name == m or name.startswith(m + ".") for m in FORBIDDEN_MODULES):
            out.append((lineno, f"imports {name}"))
    for lineno, text in _strings(tree):
        low = text.lower()
        for needle in FORBIDDEN_STRINGS:
            if needle.lower() in low and len(text) < 200:
                out.append((lineno, f"names a trust file: {text[:60]!r}"))
                break
    return out


def main(paths=None) -> int:
    files = [Path(p) for p in paths] if paths else [p for p in GUARDED if p.exists()]
    bad = 0
    for path in files:
        if path.resolve() == ALLOWED_SEAM.resolve():
            continue
        for lineno, reason in check_file(path):
            rel = path.relative_to(ROOT) if ROOT in path.parents else path
            print(f"[trust-in-governance] {rel}:{lineno}: {reason}", file=sys.stderr)
            bad += 1
    if bad:
        print(f"[trust-in-governance] FAILED - {bad} place(s) where governance reads trust; "
              f"trust may add caution and can never clear a card", file=sys.stderr)
        return 1
    print(f"[trust-in-governance] OK - {len(files)} governance module(s) import no trust data")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
