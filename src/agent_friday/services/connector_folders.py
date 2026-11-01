"""The folders an isolated connector server may reach (BD-26, NS-36.8-3 / NS-36.8-10).

A connector server configured ``"isolation": "appcontainer"`` runs under its own
Windows identity with no network and, before this, only its own working folder.
Now it reaches exactly what it declares in its config::

    "folders": ["C:/Users/me/Documents/Notes",
                {"path": "C:/Users/me/Projects/site", "write": true}]

Each declared folder is judged by the same rule as Friday's file tools
(services/path_policy): never Friday's state folder, application data or a store
of sign-ins; outside the owner's folders only with the owner's folder grant. It
must be an existing folder, not the whole home folder or a parent of it, and must
not CONTAIN any of those zones either. A write folder also must not contain a file
run at start-up, nor a folder whose contents run as the owner later (a ``.git`` with
its hooks, editor or dev-container settings): Windows does not apply a deny entry to
an isolated app's identity, so such a folder cannot be held read-only inside a
write folder, and the write folder is refused instead. A folder that fails any of
these means the server does not start, and the reason is logged; nothing is
quietly dropped.

The server also needs to read its program. Program Files and Windows are readable to
isolated apps already. Under the home, only one program's own folder in the usual
per-user program places is opened by itself, read-only: ``%LOCALAPPDATA%\\Programs\\<app>``
and ``%APPDATA%\\npm\\node_modules\\<package>`` (not a linked package, which points
at the owner's own folder), found from the command or the script a known runtime
is given (``node .../server.js``); no other argument opens anything. A program kept
anywhere else is declared under "folders" like any other folder.

Access goes to the server's own identity (services/appcontainer), recorded, and
taken back when the server's folders change or the server is removed.

Known limit: inside a write folder the server may create a ``.git``, ``.vscode`` or
similar folder where none existed. Friday finds it at the server's next start and
refuses the folder from then on, but what was written stays until the owner looks.
A folder that will be opened in an editor or used with git is declared read-only.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

#: Folders inside a write folder whose contents act as the owner later.
PROTECTED_NAMES = (".git", ".githooks", ".husky", ".vscode", ".idea", ".devcontainer", ".vs",
                   ".claude", ".cursor", ".venv", "venv", ".envrc")
_WALK_DEPTH = 8
_WALK_MAX = 20000


def _norm(p) -> str:
    s = os.path.normpath(os.path.abspath(str(p))).replace("\\", "/")
    return s.lower() if os.name == "nt" else s


def _within(p, root) -> bool:
    a, b = _norm(p), _norm(root)
    return a == b or a.startswith(b.rstrip("/") + "/")


def _too_broad(p: Path) -> Optional[str]:
    from agent_friday.services import path_policy as pp
    if _within(pp._home(), p):
        return "it is the home folder or a folder above it"
    if p.parent == p:
        return "it is a whole drive"
    return None


def _contains_zone(p: Path, op: str) -> Optional[str]:
    """A refused zone that lies INSIDE `p` (judge looks only at `p` itself). For a
    write folder a zone counts even when it does not exist yet: the server could
    create it (Documents\\WindowsPowerShell\\profile.ps1 runs as the owner)."""
    import agent_friday.core as core
    from agent_friday.services import path_policy as pp
    zones = list(pp._off_limits(op)) + [(Path(core.FRIDAY_DIR), "Friday's own state folder")]
    for zone, label in zones:
        try:
            if _within(zone, p) and _norm(zone) != _norm(p) and (op == "write" or Path(zone).exists()):
                return "it contains %s" % label
        except OSError:
            continue
    return None


#: Heavy folders of fetched code the walk does not enter (their contents are not the
#: owner's settings).
_SKIP = {"node_modules", "site-packages", "__pycache__", ".tox"}


def protect(p: Path) -> Tuple[List[Path], Optional[str]]:
    """Folders under a write folder that stay read-only for the server, and a reason
    when the folder cannot be judged (too large or deep to check, or a .git FILE that
    points git somewhere else). Links are not followed."""
    out: List[Path] = []
    seen = 0
    stack = [(Path(p), 0)]
    while stack:
        d, depth = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    seen += 1
                    if seen > _WALK_MAX:
                        return out, "it is too large to check for files that run as you"
                    try:
                        if e.name.lower() == ".git" and e.is_file(follow_symlinks=False):
                            return out, ("it holds a .git file (a worktree or submodule) that "
                                         "could point git at hooks the server writes")
                        if (e.name.lower() in PROTECTED_NAMES or e.name.lower() in ("pyvenv.cfg", ".hg")
                                or e.name.lower().endswith(".code-workspace")):
                            # a folder or a file (.envrc; pyvenv.cfg marks a Python
                            # environment of any name, whose .pth files run with it)
                            out.append(Path(e.path))
                            continue
                        if not e.is_dir(follow_symlinks=False) or e.is_symlink():
                            continue
                        if os.name == "nt" and getattr(e.stat(follow_symlinks=False),
                                                       "st_file_attributes", 0) & 0x400:
                            continue        # a junction or other reparse point
                    except OSError:
                        return out, "part of it cannot be read, so it cannot be checked"
                    if e.name.lower() in _SKIP:
                        continue
                    elif depth + 1 < _WALK_DEPTH:
                        stack.append((Path(e.path), depth + 1))
                    else:
                        return out, "it is too deep to check for files that run as you"
        except OSError:
            return out, "part of it cannot be read, so it cannot be checked"
    return out, None


def _declared(raw) -> List[Tuple[str, bool]]:
    out = []
    for item in raw or ():
        if isinstance(item, str):
            out.append((item, False))
        elif isinstance(item, dict) and isinstance(item.get("path"), str):
            out.append((item["path"], bool(item.get("write"))))
        else:
            out.append(("", False))
    return out


def _program_roots() -> List[Path]:
    roots = []
    la, ad = os.environ.get("LOCALAPPDATA"), os.environ.get("APPDATA")
    if la:
        roots.append(Path(la).resolve(strict=False) / "Programs")
    if ad:
        roots.append(Path(ad).resolve(strict=False) / "npm" / "node_modules")
    return roots


def _program_folder(f: Path) -> Optional[Path]:
    """The folder to open read-only for a program or script file, or None."""
    from agent_friday.services import path_policy as pp
    try:
        # Resolved: one spelling for comparing (a short 8.3 name is the long name), and
        # a file reached through a link is judged where it really is.
        f = Path(str(f)).resolve(strict=False)
        folder = f.parent
    except Exception:
        return None
    if not _within(folder, pp._home()):
        # Program Files and Windows are readable by isolated apps already, and only an
        # administrator may change who reads them: nothing to grant there.
        return None
    for root in _program_roots():
        if _within(folder, root) and _norm(folder) != _norm(root):
            rel = Path(os.path.relpath(str(folder), str(root))).parts
            if rel[0].startswith("@"):
                if len(rel) < 2:
                    return None             # a bare npm scope is many packages
                pkg = root / rel[0] / rel[1]
            else:
                pkg = root / rel[0]
            if _is_link(pkg) or _too_broad(pkg) or _contains_zone(pkg, "read"):
                return None                 # an `npm link`ed package points at the owner's folder
            return pkg
    # Anywhere else under the home nothing is opened by itself: guessing a program's
    # folder from where its files sit opened too much (a package.json in a broad
    # folder, a script the server wrote itself). The owner declares it under "folders".
    return None


def _is_link(p: Path) -> bool:
    try:
        if p.is_symlink():
            return True
        if os.name == "nt":
            return bool(getattr(os.lstat(p), "st_file_attributes", 0) & 0x400)
    except OSError:
        return True
    return False


_RUNTIMES = {"node", "nodejs", "python", "python3", "pythonw", "py", "deno", "bun", "tsx", "ts-node"}
_SCRIPT_EXT = {".js", ".mjs", ".cjs", ".ts", ".mts", ".py", ".pyw"}


def _script_arg(command: str, args: List[str]) -> Optional[Path]:
    """The script a known runtime is given (node server.js, python server.py): the first
    argument that is not an option, when it is a script file. Any other argument (a
    database, a document) opens nothing."""
    exe = Path(str(command or "")).name.lower()
    stem = exe[:-4] if exe.endswith(".exe") else exe
    if not (stem in _RUNTIMES or re.fullmatch(r"python3(\.\d+)?", stem)):
        return None
    for a in args:
        if str(a).startswith("-"):
            continue
        try:
            q = Path(str(a))
            if q.is_absolute() and q.is_file() and q.suffix.lower() in _SCRIPT_EXT:
                return q
        except (OSError, ValueError):
            pass
        return None
    return None


def plan(folders, command: str, args: Iterable[str]) -> Tuple[List[Path], List[Path], List[Path], List[str]]:
    """(read folders, write folders, protected folders, refusals). Any refusal: the
    server does not start."""
    from agent_friday.services import path_policy as pp
    read: List[Path] = []
    write: List[Path] = []
    keep: List[Path] = []
    refusals: List[str] = []
    for raw, w in _declared(folders):
        if not raw:
            refusals.append("a 'folders' entry is not a path")
            continue
        op = "write" if w else "read"
        p, why = pp.judge(raw, op)
        if p is None:
            refusals.append("%s: %s" % (raw, why))
            continue
        why = _too_broad(p) or (None if p.is_dir() else "it is not an existing folder") \
            or _contains_zone(p, op)
        if why:
            refusals.append("%s: %s" % (raw, why))
            continue
        if w:
            own = [part for part in Path(p).parts if part.lower() in PROTECTED_NAMES]
            if own:
                refusals.append("%s: it is inside %s, whose contents run as you" % (raw, own[0]))
                continue
            inner, too_big = protect(p)
            if too_big:
                refusals.append("%s: %s" % (raw, too_big))
                continue
            if inner:
                # Windows does not apply a deny entry to an isolated app's identity
                # (checked on Windows 11: a denied .git stayed writable), so such a
                # folder cannot be kept read-only inside a write folder: refused.
                refusals.append("%s: it holds %s, whose contents run as you; declare it "
                                "read-only, or a folder without one"
                                % (raw, ", ".join(sorted({q.name for q in inner}))))
                continue
            write.append(p)
        else:
            read.append(p)
    files = []
    try:
        c = Path(str(command))
        if c.is_absolute() and c.is_file():
            files.append(c)
    except (OSError, ValueError):
        pass
    s = _script_arg(command, [str(a) for a in (args or ())])
    if s is not None:
        files.append(s)
    for f in files:
        folder = _program_folder(f)
        if folder is not None and all(_norm(folder) != _norm(x) for x in read + write):
            read.append(folder)
    return read, write, keep, refusals


__all__ = ["plan", "protect", "PROTECTED_NAMES"]
