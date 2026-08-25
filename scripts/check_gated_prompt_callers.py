#!/usr/bin/env python3
"""Static check: every call to _get_friday_system_prompt() must decide
provider/vault_control explicitly. Catches a missing-gate bug BEFORE the rare
code path that would have exercised it ever runs.

Why this exists
----------------
2026-08-25: 22 call sites across the codebase built Friday's system prompt
via _get_friday_system_prompt(keywords=..., workspace=...) with no `provider`
or `vault_control` — the function's own former defaults ('cloud', None),
which its docstring already called "legacy ungated". Two of the twenty-two
were background jobs (a daily unattended briefing, a session-summary
distiller) that would not have been re-exercised, and therefore would not
have raised, until their own next scheduled run — hours or a day after the
bug was introduced. A `TypeError` for a missing required argument is only
loud if something actually CALLS the function; making the argument required
converts a silent leak into a loud failure, but only a STATIC scan makes
that failure loud immediately, at commit time, regardless of which code path
would eventually have hit it.

This does not replace the runtime TypeError (which now fires for the two
required params `_get_friday_system_prompt` accepts as keyword-only with no
default) — it exists because that TypeError alone would not have been loud
in time.

Approach
--------
AST-parse (not import — this must not require the app's dependencies) every
.py file under src/, find every Call node targeting a bare-name
`_get_friday_system_prompt(...)`, and confirm both `provider=` and
`vault_control=` appear as explicit keyword arguments. Since the function's
signature makes them keyword-only (no positional slot exists for them at
all), a keyword-argument scan is a complete check — there is no positional
form to also account for.

A call passing `**something` is given the benefit of the doubt (an AST scan
cannot see what a dict-splat contains); no real caller does this today.

Runs in under a second. Exit 0 = every call site decides gating explicitly.
Exit 1 = at least one does not; details on stderr.
"""
from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
TARGET_FUNC = "_get_friday_system_prompt"
REQUIRED_KWARGS = ("provider", "vault_control")
TAG = "[gated-prompt-check]"


def _err(msg: str = "") -> None:
    print(f"{TAG} {msg}".rstrip(), file=sys.stderr)


def _rel(path: pathlib.Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except (ValueError, OSError):
        return str(path)


def _is_target_call(node: ast.Call) -> bool:
    """True if this Call node invokes the bare name _get_friday_system_prompt.

    Deliberately narrow: every real caller today imports the name directly
    (`from ...model_router import _get_friday_system_prompt`) and calls it
    bare. An attribute-form call (`mr._get_friday_system_prompt(...)`) would
    be missed — none exist today; broaden this if one is ever added.
    """
    return isinstance(node.func, ast.Name) and node.func.id == TARGET_FUNC


def _missing_kwargs(node: ast.Call) -> list[str] | None:
    """Which of REQUIRED_KWARGS are absent from this call's keywords.

    Returns None (not a list) if the call has a **-splat, since an AST scan
    cannot see what a dict-splat contributes — that call is given the
    benefit of the doubt rather than flagged on a guess.
    """
    present = set()
    for kw in node.keywords:
        if kw.arg is None:  # **something
            return None
        present.add(kw.arg)
    missing = [name for name in REQUIRED_KWARGS if name not in present]
    return missing


def _check_file(path: pathlib.Path) -> list[tuple[int, list[str]]]:
    try:
        source = path.read_text(encoding="utf-8-sig", errors="replace")
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        # Not this checker's job to catch syntax errors — check_imports.py
        # (and the real import) already will. Skip rather than duplicate.
        _err(f"skipping {_rel(path)}: {exc}")
        return []

    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_target_call(node):
            missing = _missing_kwargs(node)
            if missing:
                violations.append((node.lineno, missing))
    return violations


def main() -> int:
    if not SRC.is_dir():
        _err(f"no src/ directory at {SRC} — nothing to check")
        return 0

    total_violations = 0
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for lineno, missing in _check_file(path):
            total_violations += 1
            _err(f"{_rel(path)}:{lineno} calls {TARGET_FUNC}() without "
                 f"{', '.join(f'{m}=' for m in missing)}")

    if total_violations:
        _err("")
        _err(f"{total_violations} call site(s) build Friday's system prompt "
             "without deciding gating explicitly.")
        _err("Pass both provider= and vault_control= — even a deliberate "
             "vault_control=None reads as a decision, not an oversight. See "
             "_get_friday_system_prompt's docstring in "
             "src/agent_friday/services/model_router.py.")
        return 1

    _err("OK - every _get_friday_system_prompt() call site is gated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
