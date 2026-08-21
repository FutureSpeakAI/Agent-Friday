#!/usr/bin/env python3
"""Import smoke test - catches module-level breakage before it costs an outage.

Why this exists
---------------
On 2026-08-19 a registration block in services/agent.py was moved ABOVE the dict
it mutates, creating a module-level use-before-definition. The server could not
import. It died ~2s into every start, before its own file logging existed, and
the tray discarded the child's stderr - so seven consecutive failures produced
no traceback anywhere and the cause took a full forensic pass to find.
See docs/audits/server-death-forensics.md.

The same dict had been spliced apart once before (commit e8c6140, "rejoin the
dict d207fec spliced apart in agent.py"). Twice is a pattern, not an accident.

Ruff does not catch this. F821 flags names undefined *anywhere*; these names ARE
defined, just later in the file. Only executing the import proves that
module-level statement order is actually sound.

Runs in ~4s. Exit 0 = all modules imported. Exit 1 = failure, details on stderr.

Override the module list for testing:  FRIDAY_IMPORT_CHECK_MODULES="a.b,c.d"
"""
from __future__ import annotations

import importlib
import os
import pathlib
import re
import sys
import time
import traceback

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
TAG = "[import-check]"

# Modules whose import must succeed for the server to start at all. server.py
# transitively imports services.agent; both are listed so the narrowest failing
# module is named rather than only the outermost one.
DEFAULT_MODULES = [
    "agent_friday.services.agent",
    "agent_friday.server",
]


def _err(msg: str = "") -> None:
    print(f"{TAG} {msg}".rstrip(), file=sys.stderr)


def _rel(path: pathlib.Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except (ValueError, OSError):
        return str(path)


def _definition_line(path: pathlib.Path, symbol: str) -> int | None:
    """If `symbol` is assigned at module level (col 0), return its 1-based line."""
    try:
        pattern = re.compile(rf"^{re.escape(symbol)}\s*(?::[^=]+)?=")
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            for num, line in enumerate(fh, 1):
                if pattern.match(line):
                    return num
    except OSError:
        pass
    return None


def _source_line(path: pathlib.Path, lineno: int) -> str | None:
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            for num, line in enumerate(fh, 1):
                if num == lineno:
                    return line.rstrip()
    except OSError:
        pass
    return None


def _deepest_repo_frame(exc: BaseException):
    """Last traceback frame inside this repo - where the damage actually is.

    The outermost frame is always the importing shim, which is never the bug.
    """
    chosen = None
    for frame in traceback.extract_tb(exc.__traceback__):
        try:
            resolved = pathlib.Path(frame.filename).resolve()
        except (OSError, ValueError):
            continue
        if resolved == ROOT or ROOT in resolved.parents:
            chosen = frame
    return chosen


def _report(module: str, exc: BaseException) -> None:
    _err(f"FAILED importing {module}")
    _err(f"{type(exc).__name__}: {exc}")

    # SyntaxError carries its own precise location; the traceback does not.
    if isinstance(exc, SyntaxError) and exc.filename:
        _err(f"  at {_rel(pathlib.Path(exc.filename))}:{exc.lineno}")
        if exc.text:
            _err(f"  {exc.lineno} | {exc.text.rstrip()}")
        return

    frame = _deepest_repo_frame(exc)
    if frame is None:
        _err("  (no frame inside the repo - probably a missing dependency)")
        return

    path = pathlib.Path(frame.filename)
    _err(f"  at {_rel(path)}:{frame.lineno}")
    source = frame.line or _source_line(path, frame.lineno)
    if source:
        _err(f"  {frame.lineno} | {source.strip()}")

    symbol = getattr(exc, "name", None)
    if isinstance(exc, NameError) and symbol:
        defined_at = _definition_line(path, symbol)
        if defined_at is not None and defined_at > frame.lineno:
            _err("")
            _err(f"  '{symbol}' IS defined in this file, at line {defined_at} -")
            _err(f"  which is AFTER line {frame.lineno} that uses it.")
            _err("  This is a module-level use-before-definition: move the block")
            _err(f"  at line {frame.lineno} to below line {defined_at}.")
        elif defined_at is None:
            _err("")
            _err(f"  '{symbol}' is never assigned at module level in this file.")


def main() -> int:
    # Mirror production sys.path. `python server.py` runs the repo-root
    # shim, so Python puts the REPO ROOT on sys.path automatically - which
    # is how top-level `data` and `skills` resolve. Running this checker as
    # `python scripts/check_imports.py` puts scripts/ there instead, so
    # without this the checker reports failures the real server never has
    # (and logs them into friday.log, which is worse than useless).
    for _entry in (SRC, ROOT):
        if str(_entry) not in sys.path:
            sys.path.insert(0, str(_entry))

    # Prove the module IMPORTS; do not run the application. Importing
    # agent_friday.server executes module-level code that starts the
    # scheduler, notification loop, news archiver, network monitor,
    # connector monitor, skill watcher, MCP clients and a global
    # Ctrl+Shift+Q hotkey - in a process that exits seconds later. On every
    # commit and every boot, that is a landmine.
    #
    # FRIDAY_TESTING=1 is this codebase's established import-safe switch
    # (server.py:219 gates every background daemon on it, and dozens of
    # modules document being import-safe under it). cli.py does exactly this
    # for the same reason. setdefault, so an explicit outer value still wins.
    os.environ.setdefault("FRIDAY_TESTING", "1")
    override = os.environ.get("FRIDAY_IMPORT_CHECK_MODULES", "").strip()
    modules = [m.strip() for m in override.split(",") if m.strip()] or DEFAULT_MODULES

    started = time.time()
    for module in modules:
        try:
            importlib.import_module(module)
        except BaseException as exc:  # noqa: BLE001 - report anything, mask nothing
            _report(module, exc)
            _err("")
            _err("server startup WILL fail with this error. Fix before launching.")
            return 1

    _err(f"OK - {len(modules)} module(s) imported in {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
