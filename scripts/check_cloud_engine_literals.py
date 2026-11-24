#!/usr/bin/env python3
"""A collection holding the voice engine name "gemini-live" may be spelled only in services/voice_engines.py.

Why: the cloud voice set was hard-coded as ("gemini", "gemini-live") in several places, so adding OpenAI
left some of them behind (found in #773 and again in #817). One module owns the set; everyone else imports it.

Fix a finding by importing CLOUD_ENGINES or is_cloud from agent_friday.services.voice_engines.

Warn-only by default (exit 0, findings on stdout). --enforce exits 1 on a finding.
Pure AST scan, stdlib only. Held by tests/unit/test_cloud_engine_literals_guard.py.
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

ENGINE_NAME = "gemini-live"
OWNER = "voice_engines.py"


def _holds_the_name(node: ast.AST) -> bool:
    if not isinstance(node, (ast.Tuple, ast.Set, ast.List)):
        return False
    return any(isinstance(e, ast.Constant) and e.value == ENGINE_NAME for e in node.elts)


def findings(src: Path) -> list[str]:
    out = []
    for path in sorted(src.rglob("*.py")):
        if path.name == OWNER:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if _holds_the_name(node):
                out.append("%s:%d: a collection spells the cloud voice engines; import CLOUD_ENGINES or is_cloud "
                           "from agent_friday.services.voice_engines" % (path.as_posix(), node.lineno))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(Path(__file__).resolve().parents[1] / "src"))
    ap.add_argument("--enforce", action="store_true")
    args = ap.parse_args(argv)
    found = findings(Path(args.src))
    for line in found:
        print(line)
    return 1 if (found and args.enforce) else 0


if __name__ == "__main__":
    sys.exit(main())
