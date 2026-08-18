"""Edit index.html without ever serving a half-finished page.

The server reads index.html from disk on every request, so any partial edit
reaches Stephen's live app the moment it touches the file. He is using Friday
as his daily driver while sessions rebuild it underneath him; on 2026-08-18 he
opened Settings and got a spinner that never resolved, because a component was
live in his browser before the work behind it was.

So edits go to a staging copy, the staged file is checked for syntax, and only
then does it replace the served file in one atomic os.replace — which is
rename-on-write, so a request either gets the whole old page or the whole new
one and never a torn read.

Usage:

    from scripts.ui_stage import stage

    with stage() as path:          # path = the staging copy; edit that
        patch(path)
    # on a clean exit the staged file is verified and swapped in;
    # on an exception or a failed check, the served page is left untouched.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SERVED = REPO / "index.html"


def _app_script(text: str) -> str:
    """The last top-level <script> block: the application bundle."""
    lines = text.split("\n")
    start = None
    for i, ln in enumerate(lines):
        if ln.strip() == "<script>":
            start = i
    if start is None:
        raise ValueError("no <script> block found in index.html")
    end = len(lines) - 1
    for i in range(len(lines) - 1, start, -1):
        if lines[i].strip() == "</script>":
            end = i
            break
    return "\n".join(lines[start + 1:end])


def verify(path: Path) -> None:
    """Raise unless the file parses as JavaScript.

    A syntax error here is not a small thing: index.html renders through an
    in-browser transform, so a stray bracket produces a blank page with an
    empty console rather than an error anyone can read.
    """
    text = path.read_text(encoding="utf-8")
    for marker in ("<html", "</script>", 'id="ui-inner"', "createRoot"):
        if marker not in text:
            raise ValueError("staged page is missing %r — refusing to swap" % marker)
    script = _app_script(text)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(script)
        js = fh.name
    try:
        proc = subprocess.run(
            ["node", "--check", js], capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise ValueError("staged page does not parse:\n%s"
                             % (proc.stderr or proc.stdout)[:800])
    finally:
        os.unlink(js)


@contextlib.contextmanager
def stage(verify_staged: bool = True):
    """Yield a staging copy of index.html; swap it in atomically on success."""
    tmp = SERVED.with_suffix(".staging.html")
    shutil.copy2(SERVED, tmp)
    try:
        yield tmp
        if verify_staged:
            verify(tmp)
        # os.replace is atomic on Windows and POSIX: a concurrent request sees
        # either the whole previous page or the whole new one.
        os.replace(tmp, SERVED)
        print("  [ui_stage] verified and swapped in")
    except BaseException:
        if tmp.exists():
            tmp.unlink()
        print("  [ui_stage] left the served page untouched")
        raise
