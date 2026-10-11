"""Start with your knowledge: the native folder picker and the suggestions.

Two things live here, both for the "Your knowledge" panel (the setup chat stage
and Settings > Privacy & Data) and for the voice tool that opens it:

* `pick_folders()` opens the Windows folder dialog (IFileOpenDialog with
  FOS_PICKFOLDERS | FOS_ALLOWMULTISELECT) through a short PowerShell/COM
  helper, launched the way studio_files.reveal launches Explorer. Every path it
  returns passes `file_grants.unsafe_path_reason` and `too_broad_folder_reason`
  before the caller sees it.
* `suggestions()` is a bounded, read-only look for places that usually hold a
  person's notes: Documents, Obsidian and Logseq vaults, a Notion export, Evernote
  .enex files, a Zotero library and git project roots. Depth is at most 3 and
  each detector gets 2 seconds. Each suggestion carries a size and file-count
  estimate. Nothing is preselected.

Nothing here grants anything. Reading a folder into the Library is
`library.grants.add_scope` and a cloud model reading it is a separate
`file_grants` entry; both are the owner's own press in the panel. This module
only finds and describes folders.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

#: Deepest folder level a detector looks at, counted from the place it starts.
MAX_DEPTH = 3
#: Each detector, and each size estimate, gets this long.
BOX_S = 2.0
#: A size estimate stops here even inside its time box, and says it is approximate.
MAX_ESTIMATE_FILES = 200_000
MAX_PER_KIND = 5
MAX_SUGGESTIONS = 14
PICK_TIMEOUT_S = 600.0

#: "Recommended" goes on Documents only when it holds real material.
RECOMMEND_MIN_FILES = 25
RECOMMEND_MIN_BYTES = 1024 * 1024

#: Never walked into: they hold other people's code, caches or Friday's own state.
_SKIP_DIRS = frozenset({
    "node_modules", "__pycache__", "venv", ".venv", "site-packages", "appdata",
    "$recycle.bin", "system volume information", "windows", "program files",
    "program files (x86)", "programdata", ".git", ".hg", ".svn",
})

#: Folders whose name says they are personal. The cloud reading choice is never offered for them.
_PRIVATE_NAME = re.compile(
    r"private|personal|journal|diary|vault|secret|confidential|medical|health|therap|"
    r"\btax(es)?\b|financ|\bbank|password|legal|\bid\b|passport", re.IGNORECASE)
_NOTION_NAME = re.compile(r" [0-9a-f]{32}$", re.IGNORECASE)
_NOTION_MEMBER = re.compile(r" [0-9a-f]{32}(\.[A-Za-z0-9]+)?$", re.IGNORECASE)

#: Kinds whose files are the owner's private writing or library. No cloud choice is offered.
_PRIVATE_KINDS = frozenset({"obsidian", "logseq", "notion", "evernote", "zotero"})

LABELS = {
    "documents": "Your Documents folder",
    "obsidian": "Obsidian vault",
    "logseq": "Logseq graph",
    "notion": "Notion export",
    "evernote": "Evernote export",
    "zotero": "Zotero library",
    "project": "Project folder",
    "folder": "Folder",
}
WHY = {
    "documents": "The folder Windows keeps your documents in.",
    "obsidian": "It has an .obsidian folder, which is how Obsidian marks a vault.",
    "logseq": "It has logseq/config.edn, which is how Logseq marks a graph.",
    "notion": "Its file names end in the long ids Notion adds when you export.",
    "evernote": "It holds .enex files, which is what Evernote exports.",
    "zotero": "It sits next to zotero.sqlite, which is how Zotero keeps its files.",
    "project": "It has a .git folder, which marks the top of a project.",
    "folder": "You picked it.",
}

#: The panel's words, in one place. "{agent}" is replaced with her name by the UI.
#: Agent Friday brand voice: second person, plain, short, a little dry.
COPY = {
    "title": "Your knowledge",
    "intro": ("Point {agent} at the notes and documents you already have, and she can find things in "
              "them for you. You choose every folder. Nothing is chosen for you."),
    "local_title": "Let {agent} read and index it on this PC.",
    "local_body": ("Your files stay here. {agent} can search them and answer from them with "
                   "citations. Nothing leaves this PC."),
    "cloud_title": "Also let cloud models read it.",
    "cloud_body": ("Only if you turn this on, folder by folder, and it ends after 30 days. It is a "
                   "separate permission from reading it here. It is off unless you turn it on, and "
                   "{agent} never suggests it for a vault or a private folder."),
    "recommended": "Recommended",
    "detected": "Found on this PC",
    "none_found": "Nothing obvious turned up. Choose a folder yourself.",
    "pick": "Choose folders\u2026",
    "pick_busy": "The folder window is open on your screen.",
    "typed": "Or type a folder path",
    "typed_add": "Check it",
    "save": "Save my choices",
    "skip": "Not now",
    "later": "You can come back to this in Settings > Privacy & Data > Your knowledge.",
    "added": "Already in your Library",
    "unzip": "This is a zip. Unzip it first, then choose the folder.",
    "nothing_picked": "Pick at least one folder, or choose Not now.",
}


# ── sizes ────────────────────────────────────────────────────────────────────

def human_bytes(n: int) -> str:
    n = max(0, int(n or 0))
    if n < 1024:
        return "%d B" % n
    for unit in ("KB", "MB", "GB", "TB"):
        n /= 1024.0
        if n < 1024 or unit == "TB":
            return ("%.0f %s" if n >= 100 else "%.1f %s") % (n, unit)
    return "%d B" % n


def _is_link(entry: "os.DirEntry") -> bool:
    """A symlink or junction. Never followed: it could lead off this PC."""
    try:
        if entry.is_symlink():
            return True
        st = entry.stat(follow_symlinks=False)
        return bool(getattr(st, "st_file_attributes", 0) & 0x400)   # FILE_ATTRIBUTE_REPARSE_POINT
    except OSError:
        return True


def _walk(root: Path, max_depth: int, deadline: float):
    """Breadth-first (dir_path, depth, dir_entries, file_entries), read-only, links not followed,
    skipped names not entered. Stops quietly at `deadline`."""
    todo = [(root, 0)]
    while todo:
        if time.monotonic() > deadline:
            return
        d, depth = todo.pop(0)
        try:
            with os.scandir(d) as it:
                entries = list(it)
        except OSError:
            continue
        dirs, files = [], []
        for e in entries:
            try:
                if _is_link(e):
                    continue
                (dirs if e.is_dir(follow_symlinks=False) else files).append(e)
            except OSError:
                continue
        yield d, depth, dirs, files
        if depth < max_depth:
            for e in dirs:
                if e.name.lower() in _SKIP_DIRS or e.name.startswith("."):
                    continue
                todo.append((Path(e.path), depth + 1))


def estimate(path, *, box_s: float = BOX_S, max_files: int = MAX_ESTIMATE_FILES) -> dict:
    """{size_bytes, file_count, approximate}: a read-only count that stops at its time box.
    Dot-folders (.git, .obsidian) and package caches are not counted."""
    p = Path(path)
    deadline = time.monotonic() + box_s
    size = count = 0
    cut = False
    if p.is_file():
        try:
            return {"size_bytes": p.stat().st_size, "file_count": 1, "approximate": False}
        except OSError:
            return {"size_bytes": 0, "file_count": 0, "approximate": False}
    for _d, _depth, _dirs, files in _walk(p, 64, deadline):
        for e in files:
            try:
                size += e.stat(follow_symlinks=False).st_size
            except OSError:
                continue
            count += 1
            if count >= max_files:
                cut = True
                break
        if cut:
            break
    return {"size_bytes": size, "file_count": count,
            "approximate": cut or time.monotonic() > deadline}


# ── what a folder is ─────────────────────────────────────────────────────────

def _is_windows() -> bool:
    return sys.platform == "win32"


def documents_dir() -> Path:
    """Windows' own Documents folder (it may be redirected into OneDrive), else ~/Documents."""
    if _is_windows():
        try:
            import ctypes
            from ctypes import wintypes

            class _GUID(ctypes.Structure):
                _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD),
                            ("d", ctypes.c_ubyte * 8)]
            g = _GUID(0xFDD39AD0, 0x238F, 0x46AF, (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
            out = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) == 0 and out.value:
                try:
                    return Path(out.value)
                finally:
                    ctypes.windll.ole32.CoTaskMemFree(out)
        except Exception:  # noqa: BLE001 - fall back to the plain path
            pass
    return Path.home() / "Documents"


def _norm(p) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


def _looks_private(p: Path) -> bool:
    """A folder name that says it is personal, anywhere below the home folder (or in its last
    three parts when the folder is elsewhere)."""
    try:
        parts = p.resolve().relative_to(Path.home().resolve()).parts
    except (ValueError, OSError):
        parts = p.parts[-3:]
    return any(_PRIVATE_NAME.search(part) for part in parts)


def classify_folder(path) -> str:
    """What kind of place this folder is, from what is directly inside it. Read-only."""
    p = Path(path)
    try:
        if _norm(p) == _norm(documents_dir()):
            return "documents"
        if (p / ".obsidian").is_dir():
            return "obsidian"
        if (p / "logseq" / "config.edn").is_file():
            return "logseq"
        if (p / "zotero.sqlite").is_file() or (p.name.lower() == "storage" and (p.parent / "zotero.sqlite").is_file()):
            return "zotero"
        if (p / ".git").exists():
            return "project"
        names = []
        with os.scandir(p) as it:
            for e in it:
                names.append(e.name)
                if len(names) > 400:
                    break
        if any(n.lower().endswith(".enex") for n in names):
            return "evernote"
        if sum(1 for n in names if _NOTION_NAME.search(os.path.splitext(n)[0])) >= 3:
            return "notion"
    except OSError:
        pass
    return "folder"


def _covered_by_scope(path, scopes: list) -> bool:
    n = _norm(path)
    for s in scopes or []:
        root = s.get("path")
        if not root:
            continue
        r = _norm(root)
        if n == r or (s.get("type") != "file" and n.startswith(r.rstrip("\\/") + os.sep)):
            return True
    return False


def _row(kind: str, path, *, scopes: list | None = None, box_s: float = BOX_S,
         can_add: bool = True, note: str = "") -> dict:
    p = Path(path)
    est = estimate(p, box_s=box_s)
    private = kind in _PRIVATE_KINDS or _looks_private(p)
    recommended = (kind == "documents" and est["file_count"] >= RECOMMEND_MIN_FILES
                   and est["size_bytes"] >= RECOMMEND_MIN_BYTES)
    return {
        "id": hashlib.sha1(_norm(p).encode("utf-8", "replace")).hexdigest()[:10],
        "kind": kind,
        "label": LABELS.get(kind, "Folder"),
        "name": p.name or str(p),
        "path": str(p),
        "why": WHY.get(kind, ""),
        "size_bytes": est["size_bytes"],
        "size_label": human_bytes(est["size_bytes"]),
        "file_count": est["file_count"],
        "approximate": est["approximate"],
        "recommended": bool(recommended),
        "preselected": False,                      # nothing is ever chosen for the owner
        "private": bool(private),
        "cloud_offer": not private,                # never offered for a vault or a private folder
        "can_add": bool(can_add),
        "added": _covered_by_scope(p, scopes or []),
        "note": note,
    }


def check_path(text: str) -> tuple[Path | None, str | None]:
    """The gate every folder passes before the panel shows it or anything records it:
    unsafe_path_reason (no UNC, devices, URLs, relative paths, links to the network),
    then it must be a folder, then too_broad_folder_reason (no drive root, not the whole
    home folder). Returns (resolved path, None) or (None, why)."""
    from agent_friday.services import file_grants as fg
    why = fg.unsafe_path_reason(text)
    if why:
        return None, why
    try:
        p = Path(text).expanduser().resolve()
    except Exception:  # noqa: BLE001
        return None, "that is not a path"
    if not p.is_dir():
        return None, "that is not a folder on this computer"
    broad = fg.too_broad_folder_reason(p)
    if broad:
        return None, broad
    return p, None


def describe_folder(text: str, *, scopes: list | None = None) -> dict:
    """One folder, checked and described: {ok, ...row} or {ok: False, path, error}."""
    p, why = check_path(text)
    if why:
        return {"ok": False, "path": str(text or ""), "error": why}
    from agent_friday.services.library import grants as lg
    d = lg.describe(str(p))                      # also the Library's own refusals and the never-send list
    if not d.get("ok"):
        return {"ok": False, "path": str(p), "error": d.get("error") or "that folder can't be added"}
    return {"ok": True, **_row(classify_folder(p), p, scopes=scopes)}


# ── the suggestions ──────────────────────────────────────────────────────────

def _roots() -> list[Path]:
    """Where detectors look. Never the home folder itself (AppData lives there)."""
    home = Path.home()
    out = [documents_dir(), home / "Desktop"]
    try:
        out += sorted(p for p in home.iterdir() if p.is_dir() and p.name.lower().startswith(("onedrive", "dropbox")))
    except OSError:
        pass
    out += [home / n for n in ("Projects", "source", "repos", "dev", "code", "src", "git", "workspace")]
    seen, res = set(), []
    for r in out:
        k = _norm(r)
        if k not in seen and r.is_dir():
            seen.add(k)
            res.append(r)
    return res


def _find_marked(roots: list[Path], marker, box_s: float) -> list[Path]:
    """Folders (depth <= MAX_DEPTH under any root) for which marker(dir, dirs, files) is true.
    A marked folder is not searched inside."""
    found, seen = [], set()
    deadline = time.monotonic() + box_s
    for root in roots:
        pruned: list[str] = []
        for d, depth, dirs, files in _walk(root, MAX_DEPTH, deadline):
            if any(_norm(d).startswith(x) for x in pruned):
                continue
            if marker(d, dirs, files):
                k = _norm(d)
                if k not in seen:
                    seen.add(k)
                    found.append(d)
                pruned.append(k + os.sep)
        if time.monotonic() > deadline:
            break
    return found


def _names(entries) -> set:
    return {e.name.lower() for e in entries}


def _detect_obsidian(roots, box_s):
    return [("obsidian", p) for p in _find_marked_hidden(roots, ".obsidian", box_s)]


def _find_marked_hidden(roots: list[Path], hidden: str, box_s: float) -> list[Path]:
    """Folders directly holding a dot-folder named `hidden`. The walk skips dot-folders, so look
    for them in each listing's sibling check."""
    found, seen = [], set()
    deadline = time.monotonic() + box_s
    for root in roots:
        pruned: list[str] = []
        for d, depth, dirs, files in _walk(root, MAX_DEPTH, deadline):
            if any(_norm(d).startswith(x) for x in pruned):
                continue
            if hidden in _names(dirs):
                k = _norm(d)
                if k not in seen:
                    seen.add(k)
                    found.append(d)
                pruned.append(k + os.sep)
        if time.monotonic() > deadline:
            break
    return found


def _detect_logseq(roots, box_s):
    def mark(d, dirs, files):
        return "logseq" in _names(dirs) and (d / "logseq" / "config.edn").is_file()
    return [("logseq", p) for p in _find_marked(roots, mark, box_s)]


def _detect_git(roots, box_s):
    found = _find_marked_hidden(roots, ".git", box_s)
    found = [p for p in found if _norm(p) != _norm(Path.home())]

    def stamp(p):
        try:
            return (p / ".git").stat().st_mtime
        except OSError:
            return 0
    found.sort(key=stamp, reverse=True)                  # the projects touched lately first
    return [("project", p) for p in found]


def _detect_evernote(roots, box_s):
    parents: dict[str, list[Path]] = {}
    deadline = time.monotonic() + box_s
    for root in roots:
        for d, _depth, _dirs, files in _walk(root, MAX_DEPTH, deadline):
            hit = [Path(e.path) for e in files if e.name.lower().endswith(".enex")]
            if hit:
                parents.setdefault(str(d), []).extend(hit)
    out = []
    root_keys = {_norm(r) for r in roots}
    for parent, files in parents.items():
        if _norm(parent) in root_keys:                   # loose in Documents: offer the files, not the whole folder
            out += [("evernote", f) for f in files[:MAX_PER_KIND]]
        else:
            out.append(("evernote", Path(parent)))
    return out


def _detect_notion(roots, box_s):
    out = []
    deadline = time.monotonic() + box_s
    downloads = Path.home() / "Downloads"
    scan_roots = list(roots) + ([downloads] if downloads.is_dir() else [])
    seen = set()
    for root in scan_roots:
        pruned: list[str] = []
        for d, _depth, dirs, files in _walk(root, MAX_DEPTH, deadline):
            if any(_norm(d).startswith(x) for x in pruned):
                continue
            stems = [os.path.splitext(e.name)[0] for e in files if e.name.lower().endswith((".md", ".csv", ".html"))]
            if sum(1 for s in stems if _NOTION_NAME.search(s)) >= 3 and _norm(d) not in seen:
                seen.add(_norm(d))
                out.append(("notion", d))
                pruned.append(_norm(d) + os.sep)
                continue
            for e in files:                              # an export zip: read its listing only
                if e.name.lower().endswith(".zip") and _zip_is_notion(Path(e.path)):
                    out.append(("notion_zip", Path(e.path)))
    return out


def _zip_is_notion(path: Path) -> bool:
    import zipfile
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()[:300]
    except Exception:  # noqa: BLE001 - not a zip, or unreadable: not a suggestion
        return False
    return sum(1 for n in names if _NOTION_MEMBER.search(os.path.splitext(n)[0] + os.path.splitext(n)[1])
               or _NOTION_NAME.search(os.path.splitext(os.path.basename(n))[0])) >= 3


def _detect_zotero(roots, box_s):
    z = Path.home() / "Zotero"
    if (z / "zotero.sqlite").is_file() and (z / "storage").is_dir():
        return [("zotero", z / "storage")]
    return []


def _detect_documents(roots, box_s):
    d = documents_dir()
    return [("documents", d)] if d.is_dir() else []


def suggestions(principal: str | None = None, *, box_s: float = BOX_S) -> list[dict]:
    """Places worth offering, each with a size and file-count estimate. Bounded and read-only;
    a slow disk gives fewer suggestions, never a hang. Nothing is preselected."""
    scopes = []
    if principal:
        try:
            from agent_friday.services.library import grants as lg
            scopes = lg.active_scopes(principal)
        except Exception:  # noqa: BLE001 - suggestions work without the ledger
            scopes = []
    roots = _roots()
    detectors = (_detect_documents, _detect_obsidian, _detect_logseq, _detect_notion,
                 _detect_evernote, _detect_zotero, _detect_git)
    found: list[tuple[str, Path]] = []
    with ThreadPoolExecutor(max_workers=len(detectors)) as pool:
        futs = [pool.submit(_safe_detect, fn, roots, box_s) for fn in detectors]
        for f in futs:
            found += f.result()
    picked: list[tuple[str, Path]] = []
    per_kind: dict[str, int] = {}
    seen = set()
    for kind, p in found:
        k = (_norm(p))
        if k in seen or per_kind.get(kind, 0) >= MAX_PER_KIND:
            continue
        seen.add(k)
        per_kind[kind] = per_kind.get(kind, 0) + 1
        picked.append((kind, p))
    picked = picked[:MAX_SUGGESTIONS]

    def build(item):
        kind, p = item
        if kind == "notion_zip":
            return _row("notion", p, scopes=scopes, box_s=box_s, can_add=False, note=COPY["unzip"])
        if p.is_dir():
            ok = describe_folder(str(p), scopes=scopes)
        else:                                            # a loose file (an .enex): the Library's own checks
            from agent_friday.services.library import grants as lg
            d = lg.describe(str(p))
            ok = {"ok": True, **_row(kind, p, scopes=scopes, box_s=box_s)} if d.get("ok") else {"ok": False}
        if not ok.get("ok"):
            return None
        ok.pop("ok", None)
        ok["kind"] = kind                                # the detector's reason wins over a re-sniff
        ok["label"] = LABELS.get(kind, ok["label"])
        ok["why"] = WHY.get(kind, ok["why"])
        if kind in _PRIVATE_KINDS:
            ok["private"], ok["cloud_offer"] = True, False
        return ok
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = [r for r in pool.map(build, picked) if r]
    order = {k: i for i, k in enumerate(("documents", "obsidian", "logseq", "notion", "evernote", "zotero", "project"))}
    rows.sort(key=lambda r: order.get(r["kind"], 99))
    return rows


def _safe_detect(fn, roots, box_s):
    try:
        return fn(roots, box_s) or []
    except Exception:  # noqa: BLE001 - one detector failing hides only its own suggestions
        return []


# ── the native picker ────────────────────────────────────────────────────────

_PICKER_CS = r"""
using System;
using System.Runtime.InteropServices;
[ComImport, Guid("DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7")] class FileOpenDialogCoClass {}
[ComImport, Guid("43826d1e-e718-42ee-bc55-a1e261c37bfe"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IShellItem {
  void BindToHandler(IntPtr pbc, ref Guid bhid, ref Guid riid, out IntPtr ppv);
  void GetParent(out IShellItem ppsi);
  void GetDisplayName(uint sigdnName, [MarshalAs(UnmanagedType.LPWStr)] out string ppszName);
  void GetAttributes(uint sfgaoMask, out uint psfgaoAttribs);
  void Compare(IShellItem psi, uint hint, out int piOrder);
}
[ComImport, Guid("b63ea76d-1f85-456f-a19c-48159efa858b"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IShellItemArray {
  void BindToHandler(IntPtr pbc, ref Guid rbhid, ref Guid riid, out IntPtr ppvOut);
  void GetPropertyStore(int flags, ref Guid riid, out IntPtr ppv);
  void GetPropertyDescriptionList(IntPtr keyType, ref Guid riid, out IntPtr ppv);
  void GetAttributes(int attribFlags, uint sfgaoMask, out uint psfgaoAttribs);
  void GetCount(out uint pdwNumItems);
  void GetItemAt(uint dwIndex, out IShellItem ppsi);
  void EnumItems(out IntPtr ppenumShellItems);
}
[ComImport, Guid("d57c7288-d4ad-4768-be02-9d969532d960"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IFileOpenDialog {
  [PreserveSig] int Show(IntPtr parent);
  void SetFileTypes(uint cFileTypes, IntPtr rgFilterSpec);
  void SetFileTypeIndex(uint iFileType);
  void GetFileTypeIndex(out uint piFileType);
  void Advise(IntPtr pfde, out uint pdwCookie);
  void Unadvise(uint dwCookie);
  void SetOptions(uint fos);
  void GetOptions(out uint pfos);
  void SetDefaultFolder(IShellItem psi);
  void SetFolder(IShellItem psi);
  void GetFolder(out IShellItem ppsi);
  void GetCurrentSelection(out IShellItem ppsi);
  void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string pszName);
  void GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string pszName);
  void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string pszTitle);
  void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string pszText);
  void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string pszLabel);
  void GetResult(out IShellItem ppsi);
  void AddPlace(IShellItem psi, int fdap);
  void SetDefaultExtension([MarshalAs(UnmanagedType.LPWStr)] string pszDefaultExtension);
  void Close(int hr);
  void SetClientGuid(ref Guid guid);
  void ClearClientData();
  void SetFilter(IntPtr pFilter);
  void GetResults(out IShellItemArray ppenum);
  void GetSelectedItems(out IShellItemArray ppsai);
}
public static class FridayFolderPicker {
  public static string[] Pick(string title) {
    var dlg = (IFileOpenDialog)new FileOpenDialogCoClass();
    uint opts; dlg.GetOptions(out opts);
    dlg.SetOptions(opts | 0x20u | 0x200u | 0x40u);   // FOS_PICKFOLDERS | FOS_ALLOWMULTISELECT | FOS_FORCEFILESYSTEM
    dlg.SetTitle(title);
    if (dlg.Show(IntPtr.Zero) != 0) return new string[0];   // cancelled (HRESULT_FROM_WIN32(ERROR_CANCELLED))
    IShellItemArray arr; dlg.GetResults(out arr);
    uint n; arr.GetCount(out n);
    var res = new string[n];
    for (uint i = 0; i < n; i++) {
      IShellItem it; arr.GetItemAt(i, out it);
      string p; it.GetDisplayName(0x80058000u, out p);      // SIGDN_FILESYSPATH
      res[i] = p;
    }
    return res;
  }
}
"""

PICKER_TITLE = "Choose the folders to start with"

_PS_TEMPLATE = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -TypeDefinition @'
__CS__
'@
$paths = [FridayFolderPicker]::Pick('__TITLE__')
if ($paths.Count -eq 0) { Write-Output 'CANCELLED' } else { foreach ($p in $paths) { Write-Output ('PATH:' + $p) } }
"""


def picker_script() -> str:
    """The PowerShell/COM helper, as text."""
    return _PS_TEMPLATE.replace("__CS__", _PICKER_CS.strip("\n")).replace("__TITLE__", PICKER_TITLE)


def _powershell() -> str:
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    full = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    return full if os.path.exists(full) else "powershell"


_PICK_LOCK = threading.Lock()


def _parse_picker_output(text: str) -> tuple[list[str], bool]:
    paths, cancelled = [], False
    for line in (text or "").splitlines():
        line = line.strip("\r\n").lstrip("\ufeff")
        if line == "CANCELLED":
            cancelled = True
        elif line.startswith("PATH:") and len(line) > 5:
            paths.append(line[5:])
    return paths, cancelled


def pick_folders(*, timeout: float = PICK_TIMEOUT_S, principal: str | None = None) -> dict:
    """Open the native folder dialog and return the folders chosen.

    {"ok": True, "cancelled": bool, "folders": [row...], "refused": [{path, error}]}, or
    {"ok": False, "error": plain words, "fallback": "type"} when the dialog cannot open here.
    The typed path in the panel stays as the fallback. Never reads anything inside a folder
    beyond the size estimate."""
    if not _is_windows():
        return {"ok": False, "fallback": "type",
                "error": "The folder window only opens on Windows. Type the folder's path instead."}
    if not _PICK_LOCK.acquire(blocking=False):
        return {"ok": False, "fallback": "wait", "error": "The folder window is already open."}
    try:
        enc = base64.b64encode(picker_script().encode("utf-16-le")).decode("ascii")
        argv = [_powershell(), "-NoProfile", "-NonInteractive", "-STA", "-ExecutionPolicy", "Bypass",
                "-EncodedCommand", enc]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
            try:
                out, _err = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                return {"ok": False, "fallback": "type",
                        "error": "The folder window timed out. Try again, or type the folder's path."}
        except OSError:
            return {"ok": False, "fallback": "type",
                    "error": "The folder window wouldn't open. Type the folder's path instead."}
        text = out.decode("utf-8", "replace") if isinstance(out, (bytes, bytearray)) else str(out or "")
        paths, cancelled = _parse_picker_output(text)
        if proc.returncode not in (0, None) and not paths and not cancelled:
            return {"ok": False, "fallback": "type",
                    "error": "The folder window wouldn't open. Type the folder's path instead."}
    finally:
        _PICK_LOCK.release()
    scopes = []
    if principal:
        try:
            from agent_friday.services.library import grants as lg
            scopes = lg.active_scopes(principal)
        except Exception:  # noqa: BLE001
            scopes = []
    folders, refused, seen = [], [], set()
    for text_path in paths[:25]:
        d = describe_folder(text_path, scopes=scopes)
        if not d.get("ok"):
            refused.append({"path": d.get("path"), "error": d.get("error")})
            continue
        d.pop("ok", None)
        if d["path"] in seen:
            continue
        seen.add(d["path"])
        folders.append(d)
    return {"ok": True, "cancelled": bool(cancelled and not paths), "folders": folders, "refused": refused}


# ── by voice ─────────────────────────────────────────────────────────────────

def _quantity(row: dict) -> str:
    n = row["file_count"]
    more = "about " if row["approximate"] else ""
    return "%s%s in %s%s file%s" % (more, row["size_label"], more, format(n, ","), "" if n == 1 else "s")


def spoken_suggestions(rows: list[dict], *, local_model: bool) -> str:
    """The suggestions in a few plain sentences, for a spoken answer. Folder names are said only
    when the voice model is on this PC; a cloud voice model hears the kind and the size."""
    if not rows:
        return "I didn't find anything obvious on this PC."
    seen: dict[str, int] = {}
    total: dict[str, int] = {}
    for r in rows:
        total[r["kind"]] = total.get(r["kind"], 0) + 1
    parts = []
    for r in rows[:6]:
        seen[r["kind"]] = seen.get(r["kind"], 0) + 1
        label = r["label"] if r["kind"] != "documents" else "your Documents folder"
        if total[r["kind"]] > 1:
            label += " number %d" % seen[r["kind"]]
        if local_model and r["kind"] not in ("documents",) and r.get("name"):
            label += ", called %s" % r["name"]
        tag = ", the one I'd suggest" if r.get("recommended") else ""
        parts.append("%s, %s%s%s" % (label, _quantity(r), tag, ", already in your Library" if r.get("added") else ""))
    more = len(rows) - 6
    return "I found " + "; ".join(parts) + ("; and %d more on screen" % more if more > 0 else "") + "."


_LAST_PICK: dict = {}


def start_voice_pick(principal: str) -> dict:
    """Open the folder window without holding the conversation, and when the owner has
    chosen, raise ONE Library card for the folders. The card is decided on screen; a spoken
    yes never adds anything."""
    if _PICK_LOCK.locked():
        return {"started": False, "reason": "already open"}

    def work():
        res = pick_folders(principal=principal)
        _LAST_PICK.clear()
        _LAST_PICK.update({"ok": res.get("ok"), "at": time.time(),
                           "count": len(res.get("folders") or []), "cancelled": res.get("cancelled")})
        if res.get("ok") and res.get("folders"):
            try:
                from agent_friday.services.library import cards
                cards.request_add([{"path": f["path"]} for f in res["folders"] if f.get("can_add", True)],
                                  reason="You chose these folders in the folder window.",
                                  requested_by="friday")
            except Exception:  # noqa: BLE001 - the panel is still there to do it by hand
                _LAST_PICK["card_failed"] = True
    threading.Thread(target=work, name="friday-knowledge-pick", daemon=True).start()
    return {"started": True}
