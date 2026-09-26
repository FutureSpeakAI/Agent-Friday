"""What Friday may open on the owner's machine without asking first.

Opening a file hands it to whatever program Windows associates with it. For a
document, a picture or a song that is reading. For an executable, a script, a
shortcut, an installer or a registry file it is running code, and the same
`os.startfile` call does both. So the rule is an ALLOW-LIST: the types below,
and existing folders, open without a decision. Everything else -- including a
file with no extension and every type nobody thought to list -- waits for the
owner (a yes in chat, or an approval card), and never runs silently.

The judgement is made on the REAL target: the path is resolved through
symlinks and junctions first, so `report.pdf` that links to `evil.exe` is an
executable. A Windows shortcut (`.lnk`, `.url`) is not on the list at all, so
what it points at never matters: it always asks.

Used by the governance checkpoint (`action_gate.classify` for `open_path`),
by the `open_path` handler itself as a second check, and by the
`/api/computer/open` route.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional, Tuple

#: File types that open as a document, picture or recording in their viewer.
#: Deliberately absent: .html/.htm/.svg/.xhtml/.mht (a browser runs the
#: script inside them), and every executable, installer, script, shortcut,
#: registry and archive type.
SAFE_OPEN_EXTENSIONS = frozenset({
    # documents
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".odt", ".ods", ".odp", ".rtf", ".csv", ".md", ".txt", ".log",
    ".json", ".xml",
    # images
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff",
    # audio
    ".mp3", ".wav", ".flac", ".ogg", ".m4a",
    # video
    ".mp4", ".mkv", ".mov", ".webm", ".avi",
})

#: A folder name ending in ".{CLSID}" is a shell namespace junction: Explorer
#: opens the object the CLSID names, not a folder.
_SHELL_JUNCTION = re.compile(r"\.\{[0-9a-fA-F-]{8,}\}$")


def real_target(path) -> Optional[Path]:
    """The path with `~`, `..`, symlinks and junctions resolved, or None."""
    try:
        s = os.fspath(path)
    except TypeError:
        return None
    if not s or "\x00" in s:
        return None
    try:
        return Path(os.path.realpath(os.path.expanduser(s)))
    except Exception:
        return None


def judge(path) -> Tuple[bool, str]:
    """(safe_to_open_without_asking, why) for a path that is about to be
    opened. Judged on the resolved target."""
    p = real_target(path)
    if p is None:
        return False, "the path could not be resolved"
    s = str(p)
    # An alternate data stream ("doc.pdf:payload.exe") or any other colon past
    # the drive letter is not an ordinary file.
    if ":" in (s[2:] if re.match(r"^[A-Za-z]:", s) else s):
        return False, "the path names an alternate data stream or device"
    try:
        is_dir = p.is_dir()
    except OSError:
        is_dir = False
    if is_dir:
        if _SHELL_JUNCTION.search(p.name):
            return False, "the folder is a shell namespace junction"
        return True, "it is a folder"
    ext = p.suffix.lower()
    if not ext:
        return False, "the file has no extension, so what it runs is unknown"
    if ext in SAFE_OPEN_EXTENSIONS:
        return True, f"a {ext} file opens in its viewer"
    return False, f"a {ext} file is not on the list of types that only open to view"


def classify_open(args) -> Tuple[str, str]:
    """(class, why) for an `open_path` call, for the governance checkpoint.

    Resolves the target exactly as the handler will (friendly folder names,
    bare file names found in the creations and user folders, explicit paths),
    then judges the real target. A known application from the handler's fixed
    list is internal: it is a named program with no arguments, not a file the
    model chose. A target that resolves to nothing opens nothing.
    """
    from agent_friday.governance.action_gate import INTERNAL, OUTWARD
    a = args or {}
    target = str(a.get("path") or a.get("target") or "").strip()
    if not target:
        return INTERNAL, "nothing to open"
    from agent_friday.services import agent as _agent
    key = re.sub(r"\s+", " ", target.lower())
    if key in _agent._OPEN_APPS or key in _agent._OPEN_SHELL_APPS:
        return INTERNAL, "a known application from Friday's fixed list"
    try:
        resolved = _agent._resolve_open_target(target)
    except Exception as e:
        return OUTWARD, f"the target could not be resolved ({e})"
    if not resolved:
        # The handler finds nothing and opens nothing. If a file appears there
        # before it runs, the handler's own check judges it then.
        return INTERNAL, "nothing matches, so nothing will be opened"
    safe, why = judge(resolved)
    return (INTERNAL, why) if safe else (OUTWARD, why)
