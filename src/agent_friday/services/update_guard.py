"""
Agent Friday — "an update will replace files you changed", said BEFORE it does.

An upgrade deletes the install folder and copies the new release over it
(packaging/windows/install.ps1, step app.copy). Anything a person or a helper
changed in there — a patched module, an edited tray script — is gone, and
until now nothing said so. This module is the Python half of the guard:

  * the build writes `app-files.sha256` into the payload: one
    `<sha256>  <path>` line per shipped file (packaging/windows/
    build-installer.ps1, lib/AppFiles.ps1 `Write-AppFilesManifest`);
  * `scan()` compares the installed folder against it and lists files that
    were CHANGED or ADDED since;
  * the installer runs the same comparison (lib/AppFiles.ps1, which mirrors
    this module rule for rule), copies those files to
    `~/.friday/overrides/replaced-<version>/` with a REPLACED.txt list, shows
    the list on its Ready page, and only then replaces the folder;
  * `summary()` feeds the in-app update check (services/update_check.status
    and its notification), so Friday can say it before anyone clicks update.

An install from a release that shipped no manifest is compared by TIME
instead: a file written after install-manifest.json (the installer's last act)
was changed after install. That basis is weaker and is reported as such.

Read-only except `backup()`, which only ever writes under the overrides folder.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import stat
import threading
import time
from pathlib import Path
from typing import Iterable, Optional

_log = logging.getLogger("friday.update_guard")

MANIFEST_NAME = "app-files.sha256"
INSTALL_MANIFEST = "install-manifest.json"

#: THE EXCLUSION LIST, mirrored rule for rule from packaging/windows/lib/
#: AppFiles.ps1 (whose header documents why each entry is there: everything
#: that lands in the app folder after the payload copy and is not the
#: person's work, so a clean install followed by an upgrade reports 0).
#: tests/unit/test_user_overrides.py fails if the two lists drift apart.
#: Every comparison is case-insensitive, as on Windows.
#:
#: Any folder with this name, anywhere (bytecode, tool caches, VCS/venv).
SKIP_DIRS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache",
                       ".ruff_cache", "node_modules", ".git", "venv", ".venv"})
#: Any folder ending so (pip metadata).
SKIP_DIR_SUFFIXES = (".egg-info", ".dist-info")
#: Any file ending so (bytecode).
SKIP_SUFFIXES = (".pyc", ".pyo")
#: Any file with this name, anywhere (what Explorer/Finder drop in a folder).
SKIP_NAMES = frozenset({"thumbs.db", "desktop.ini", ".ds_store"})
#: Any '/'-relative path starting so (model_seat_gate.save_evidence's folder;
#: the payload ships no tests/ at all).
SKIP_PREFIXES = ("tests/conformance/results/",)
#: install.ps1's $script:AppUserFiles, at the top of the app folder only:
#: written by the setup wizard after the copy, preserved across it by the
#: installer itself, and they hold the vault passphrase and keys in plain
#: text, so they are never listed or copied here.
USER_FILES = frozenset({"start.bat", "launch_now.bat", "friday_startup.bat",
                        "friday_startup.vbs", ".env", "secrets.yaml",
                        "config.yaml"})
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
#: A file written within this many seconds after the install manifest counts
#: as part of the install (the time basis only).
INSTALL_SLACK_S = 120

_MEMO: dict = {}
_MEMO_TTL_S = 600.0
_MEMO_LOCK = threading.Lock()


def app_root() -> Path:
    """The folder the installer replaces: `<InstallRoot>/app`, which holds
    `src/agent_friday`. In a source checkout this is the repository root,
    which has no manifest, so the guard reports basis "none"."""
    return Path(__file__).resolve().parents[3]


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _skipped_dir(name: str) -> bool:
    n = name.lower()
    return n in SKIP_DIRS or n.endswith(SKIP_DIR_SUFFIXES)


def skipped_file(rel: str) -> bool:
    """AppFiles.ps1's Test-AppFilesSkippedFile: `rel` is '/'-relative."""
    r = rel.lower()
    name = r.rsplit("/", 1)[-1]
    if name.endswith(SKIP_SUFFIXES) or name in SKIP_NAMES:
        return True
    if r.startswith(SKIP_PREFIXES) or r == MANIFEST_NAME:
        return True
    return "/" not in r and name in USER_FILES


def is_reparse_point(path) -> bool:
    """A symlink or a junction (any reparse point): never followed."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & _REPARSE)


def iter_files(root, skipped: Optional[list] = None) -> Iterable[str]:
    """Every file under `root` the guard cares about, as a '/'-relative path,
    in sorted order. Junctions and symlinks are never followed (a loop would
    never end; a link's target is not part of the install): each one passed
    over is appended to `skipped` as ("reparse", rel), and a folder that
    cannot be listed as ("unlisted", rel, why)."""
    root = Path(root)
    out = []
    stack = [root]
    while stack:
        d = stack.pop()
        rel_dir = d.relative_to(root).as_posix()
        prefix = "" if rel_dir == "." else rel_dir + "/"
        try:
            entries = list(os.scandir(d))
        except OSError as e:
            if skipped is not None:
                skipped.append(("unlisted", prefix.rstrip("/"), str(e)))
            continue
        for ent in entries:
            rel = prefix + ent.name
            if is_reparse_point(ent.path):
                if not (ent.is_dir(follow_symlinks=True) and _skipped_dir(ent.name)) \
                        and not skipped_file(rel) and skipped is not None:
                    skipped.append(("reparse", rel))
                continue
            if ent.is_dir(follow_symlinks=False):
                if not _skipped_dir(ent.name):
                    stack.append(Path(ent.path))
                continue
            if not skipped_file(rel):
                out.append(rel)
    return sorted(out)


def write_manifest(root, out_path=None) -> Path:
    """Write `app-files.sha256` for a staged payload (the build does this in
    PowerShell; this is the same format, for tests and source builds)."""
    root = Path(root)
    out_path = Path(out_path) if out_path else root / MANIFEST_NAME
    lines = ["%s  %s" % (sha256_file(root / rel), rel) for rel in iter_files(root)]
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return out_path


_LINE = re.compile(r"^([0-9a-fA-F]{64})\s+\*?(.+?)\s*$")


def read_manifest(root) -> Optional[dict]:
    """`{relpath: sha256}` from the installed manifest, or None when absent."""
    p = Path(root) / MANIFEST_NAME
    try:
        text = p.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    out = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _LINE.match(line)
        if m:
            out[m.group(2).replace("\\", "/")] = m.group(1).lower()
    return out


def scan(root=None) -> dict:
    """What an upgrade would replace that is not what was installed.

    `{"basis": "manifest"|"install-time"|"none", "root", "modified": [...],
    "added": [...], "count": n}`. `modified` are shipped files whose bytes
    differ; `added` are files the release did not ship (a new module a helper
    wrote, a tray script). A shipped file that was DELETED is not listed: the
    update puts it back, which is not a loss.
    """
    root = Path(root) if root else app_root()
    res = {"basis": "none", "root": str(root), "modified": [], "added": [],
           "count": 0, "reparse": [], "unlisted": []}
    manifest = read_manifest(root)
    skipped: list = []
    if manifest is not None:
        res["basis"] = "manifest"
        for rel in iter_files(root, skipped):
            want = manifest.get(rel)
            if want is None:
                res["added"].append(rel)
                continue
            try:
                if sha256_file(root / rel) != want:
                    res["modified"].append(rel)
            except OSError as e:
                _log.info("update guard: could not read %s (%s)", rel, e)
                res["modified"].append(rel)
    else:
        marker = root.parent / INSTALL_MANIFEST
        try:
            installed_at = marker.stat().st_mtime
        except OSError:
            return res
        res["basis"] = "install-time"
        for rel in iter_files(root, skipped):
            try:
                if (root / rel).stat().st_mtime > installed_at + INSTALL_SLACK_S:
                    res["modified"].append(rel)
            except OSError:
                res["modified"].append(rel)
    res["count"] = len(res["modified"]) + len(res["added"])
    res["reparse"] = [s[1] for s in skipped if s[0] == "reparse"]
    res["unlisted"] = ["%s|%s" % (s[1], s[2]) for s in skipped if s[0] == "unlisted"]
    for r in res["reparse"]:
        _log.info("update guard: not followed (junction or symlink): %s", r)
    for u in res["unlisted"]:
        _log.warning("update guard: folder could not be read: %s", u)
    return res


def _long(path) -> str:
    """`path` in the \\\\?\\ form on Windows, so a backup deeper than 260
    characters still copies (AppFiles.ps1 uses the same Win32 form)."""
    p = os.path.abspath(str(path))
    if os.name != "nt" or p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def _safe_label(version: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", str(version or "").strip()).strip("-.")
    return s or "unknown-version"


def backup(root=None, version: str = "", dest_root=None, found: Optional[dict] = None) -> dict:
    """Copy every changed/added file to `<overrides>/replaced-<version>/`.

    Never overwrites an earlier backup (a second run gets `-2`, `-3`...).
    Writes REPLACED.txt listing each file and why. Returns
    `{"dest": str|None, "files": [...], "failed": [{rel, why}], "basis": ...}`;
    `failed` is what AppFiles.ps1 would stop the update on (it throws; this
    reports, since nothing here deletes anything).
    """
    root = Path(root) if root else app_root()
    found = found if found is not None else scan(root)
    files = [("modified", r) for r in found["modified"]] + \
            [("added", r) for r in found["added"]]
    out = {"dest": None, "files": [], "failed": [], "basis": found["basis"]}
    for u in found.get("unlisted") or []:
        rel, _, why = u.partition("|")
        out["failed"].append({"rel": rel + "/", "why": "the folder could not be read: " + why})
    if not files:
        return out
    if dest_root is None:
        from agent_friday.services import user_overrides as _uo
        dest_root = _uo.ensure_dir() or _uo.overrides_dir()
    base = Path(dest_root) / ("replaced-" + _safe_label(version))
    dest, n = base, 1
    while dest.exists():
        n += 1
        dest = base.with_name("%s-%d" % (base.name, n))
    try:
        os.makedirs(_long(dest))
    except OSError as e:
        why = "the backup folder could not be created: %s" % e
        out["failed"] += [{"rel": rel, "why": why} for _k, rel in files]
        out["dest"] = str(dest)
        _log.warning("update guard: %s (%s)", why, dest)
        return out
    listing =["Files an update of Agent Friday %s was about to replace, copied here first."
               % (version or "(unknown version)"),
               "Compared by: %s. Original folder: %s" % (found["basis"], root), ""]
    for kind, rel in files:
        to = dest / rel
        try:
            os.makedirs(_long(to.parent), exist_ok=True)
            shutil.copy2(_long(root / rel), _long(to))
            out["files"].append(rel)
            listing.append("%s  %s" % (kind, rel))
        except OSError as e:
            listing.append("%s  %s  (NOT COPIED: %s)" % (kind, rel, e))
            out["failed"].append({"rel": rel, "why": str(e)})
            _log.warning("update guard: could not back up %s (%s)", rel, e)
    try:
        with open(_long(dest / "REPLACED.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(listing) + "\n")
    except OSError as e:
        out["failed"].append({"rel": "REPLACED.txt", "why": str(e)})
    out["dest"] = str(dest)
    _log.info("update guard: backed up %d changed file(s) to %s", len(out["files"]), dest)
    return out


def summary(root=None, *, max_list: int = 20, force: bool = False) -> dict:
    """The short form the update check and Settings show. Memoised for ten
    minutes: hashing the install folder is not free and Settings polls."""
    key = str(root or app_root())
    with _MEMO_LOCK:
        hit = _MEMO.get(key)
        if hit and not force and (time.time() - hit[0]) < _MEMO_TTL_S:
            return hit[1]
    try:
        found = scan(root)
    except Exception as e:                                   # noqa: BLE001
        _log.info("update guard: scan failed (%s: %s)", type(e).__name__, e)
        found = {"basis": "none", "modified": [], "added": [], "count": 0}
    files = found["modified"] + found["added"]
    try:
        from agent_friday.paths import overrides_dir
        backup_dir = str(overrides_dir())
    except Exception:
        backup_dir = ""
    out = {
        "basis": found["basis"],
        "count": found["count"],
        "files": files[:max_list],
        "more": max(0, len(files) - max_list),
        "backup_dir": backup_dir,
        "message": message(found["count"], files[:max_list], backup_dir),
    }
    with _MEMO_LOCK:
        _MEMO[key] = (time.time(), out)
    return out


def message(count: int, files: list, backup_dir: str) -> str:
    """The sentence, identical in spirit to the installer's Ready page."""
    if not count:
        return ""
    shown = ", ".join(files[:8]) + (" and more" if count > len(files[:8]) else "")
    return ("%d file%s you or a helper changed in the installed copy of Friday will be "
            "replaced by an update: %s. They are backed up to %s first. Supported "
            "customisations belong in that overrides folder, which updates never "
            "touch." % (count, "" if count == 1 else "s", shown,
                        os.path.join(backup_dir or "~/.friday/overrides",
                                     "replaced-<version>")))


def reset_cache() -> None:
    with _MEMO_LOCK:
        _MEMO.clear()
