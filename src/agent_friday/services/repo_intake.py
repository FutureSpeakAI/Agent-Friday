"""Copy bounded local source into a new salon without touching its checkout.

The intake is a text-source snapshot, not a clone or an execution step. Git
history, credentials, links, dependencies and generated output stay behind.
Only the newly allocated managed repository receives Git commands.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from agent_friday.paths import friday_home, user_home
from agent_friday.services import codebases as cb
from agent_friday.services import credential_paths, secret_patterns

MAX_FILES = 5_000
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_GRAPH_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 48 * 1024 * 1024
MAX_ENTRIES = 30_000
MAX_DEPTH = 48

_SKIP_DIRS = {
    ".git", ".friday", "node_modules", "vendor", ".venv", "venv", "env",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
    ".nox", ".cache", ".next", ".nuxt", ".output", ".svelte-kit",
    "dist", "build", "target", "coverage", ".parcel-cache", ".turbo",
    ".ssh", ".aws", ".azure", ".gnupg", ".kube", ".config", ".local",
    ".codex", ".claude", ".cursor", ".gemini", ".agents", ".npm", ".docker",
    "secrets", "credentials",
}
_SECRET_FILES = {"credentials.json", "credentials.yaml", "credentials.yml", "secrets.json",
                 "secrets.yaml", "secrets.yml", "tokens.json", "secret_key"}
_BINARY_SUFFIXES = {
    ".gif", ".png", ".jpeg", ".jpg", ".webp", ".ico", ".bmp", ".tif", ".tiff",
    ".avif", ".heic", ".heif", ".psd", ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".wasm", ".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a", ".aiff", ".opus",
    ".mp4", ".mov", ".avi", ".mkv", ".webm", ".wmv", ".m4v",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".zst", ".tgz",
    ".whl", ".jar", ".war", ".ear", ".nupkg", ".apk", ".dmg", ".iso",
    ".exe", ".dll", ".so", ".dylib", ".o", ".obj", ".a", ".lib", ".class",
    ".pyc", ".pyo", ".pyd", ".pdf", ".db", ".sqlite", ".sqlite3", ".mdb", ".accdb",
}
_GRAPH_DIRS = {".ua", ".understand-anything"}
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_LIMITATIONS = [
    "Editable text-source snapshot; binary and unsupported-encoding files are omitted.",
    "Git history, dependencies, generated output, credentials and links are omitted.",
    "This copy is not synchronized with its source; no source code was run during intake.",
]


def _linked(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE)


def _within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _local_drive(path: Path) -> bool:
    if os.name != "nt":
        return True
    import ctypes
    # Mapped shares retain a drive letter. GetDriveType does not enumerate or
    # open the selected repository, and unknown drives fail closed.
    try:
        return ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(path.anchor)) in {2, 3, 5, 6}
    except (AttributeError, OSError):
        return False


def _source_path(value: str) -> Path:
    text = str(value or "").strip()
    if not text or "\x00" in text or text.startswith(("\\", "//")) or "://" in text:
        raise ValueError("Choose a local repository folder, not a URL or network path.")
    if ":" in text[2:] or (len(text) > 1 and text[1] == ":" and
                            (os.name != "nt" or len(text) < 3 or text[2] not in "/\\")):
        raise ValueError("Choose a local repository folder with a normal filesystem path.")
    path = Path(os.path.abspath(os.path.expanduser(text)))
    if not _local_drive(path):
        raise ValueError("Choose a local repository folder, not a mapped network drive.")
    try:
        for part in (path, *path.parents):
            if _linked(part.lstat()):
                raise ValueError("Choose the original folder; linked folders cannot be imported.")
        root = path.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Choose an existing repository folder.")
        home = user_home().resolve()
        runtime = friday_home().resolve()
        managed = cb._root().resolve()
        if (root == Path(root.anchor) or _within(home, root) or
                _within(root, runtime) or _within(runtime, root) or
                _within(root, managed) or _within(managed, root)):
            raise ValueError("Choose a repository folder, not a home, runtime or managed salon folder.")
        if (any(part.casefold() in _SKIP_DIRS for part in root.parts) or
                root.suffix.casefold() in {".pem", ".key"} or credential_paths.check(root, sniff=False)):
            raise ValueError("That folder is reserved for credentials, dependencies or generated files.")
        return root
    except (OSError, RuntimeError) as exc:
        raise ValueError("The source folder cannot be read safely.") from exc


def _safe_name(name: str) -> bool:
    # The destination must retain one portable spelling for every source path.
    return (bool(name) and name not in {".", ".."} and
            not any(ch in name for ch in ("\\", ":", "\x00")) and
            not name.endswith((".", " ")) and not any(ord(ch) < 32 for ch in name) and
            not re.fullmatch(r"(?i)(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", name))


def _source_info(path: Path, root: Path) -> os.stat_result:
    """Recheck every ancestor immediately around reads; never follow reparse points."""
    if not _within(path, root):
        raise ValueError("A source entry leaves the selected folder.")
    for part in (path, *path.parents):
        info = part.lstat()
        if _linked(info):
            raise ValueError("The source changed while it was being copied; try again.")
        if part == root:
            break
    if path.resolve(strict=True) != path:
        raise ValueError("A source entry changed its resolved location.")
    return path.lstat()


def _read(path: Path, root: Path, maximum: int) -> bytes:
    before = _source_info(path, root)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError("The source changed while it was being copied; try again.")
    if before.st_size > maximum:
        raise ValueError("A source file exceeds the snapshot size limit; choose a smaller folder.")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_nlink) != (before.st_dev, before.st_ino, 1):
            raise ValueError("The source changed while it was being copied; try again.")
        data = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    current = _source_info(path, root)
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_nlink)
    if identity(before) != identity(after) or identity(before) != identity(current) or len(data) != before.st_size:
        raise ValueError("The source changed while it was being copied; try again.")
    if len(data) > maximum:
        raise ValueError("A source file exceeds the snapshot size limit; choose a smaller folder.")
    return data


def _decode(data: bytes) -> str | None:
    try:
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = data.decode("utf-16")
        else:
            text = data.decode("utf-8-sig")
    except UnicodeError:
        return None
    return None if "\x00" in text else text


def _collect(root: Path) -> tuple[dict[str, bytes], dict]:
    files: dict[str, bytes] = {}
    skipped: Counter = Counter()
    visited = 0
    total = 0
    bytes_read = 0
    seen: set[str] = set()
    digest = hashlib.sha256()

    def walk(folder: Path, depth: int) -> None:
        nonlocal visited, total, bytes_read
        if depth > MAX_DEPTH:
            raise ValueError("The source exceeds the snapshot depth limit; choose a smaller folder.")
        _source_info(folder, root)
        entries = []
        with os.scandir(folder) as scan:
            for entry in scan:
                visited += 1
                if visited > MAX_ENTRIES:
                    raise ValueError("The source exceeds the snapshot entry limit; choose a smaller folder.")
                entries.append(entry)
        for entry in sorted(entries, key=lambda e: e.name):
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            name = entry.name.casefold()
            # Windows directory enumeration omits file identity/link counts.
            # Read full metadata before deciding whether an entry is linked.
            info = path.lstat()
            if not _safe_name(entry.name):
                skipped["unsupported_names"] += 1
                continue
            if _linked(info) or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1):
                skipped["linked_entries"] += 1
                continue
            if name in _SKIP_DIRS:
                skipped["excluded_folders"] += 1
                continue
            # Known assets need no read to establish that a text snapshot omits
            # them. Unknown extensions still pass through the bounded reader.
            if stat.S_ISREG(info.st_mode) and path.suffix.casefold() in _BINARY_SUFFIXES:
                skipped["binary_or_encoding"] += 1
                continue
            # Content-gated credential paths can sniff even with sniff=False.
            # Omit these by name before any policy helper can open them.
            if (name in _SECRET_FILES or path.suffix.casefold() in {".pem", ".key"} or
                    name == ".env" or (name.startswith(".env.") and not name.endswith((".example", ".sample", ".template")))):
                skipped["credential_files"] += 1
                continue
            if credential_paths.check(path, sniff=False):
                skipped["credential_files"] += 1
                continue
            if any(part.casefold() in _GRAPH_DIRS for part in path.relative_to(root).parts[:-1]):
                if not (folder == root / folder.name and folder.name.casefold() in _GRAPH_DIRS and name == "knowledge-graph.json"):
                    skipped["analysis_scratch"] += 1
                    continue
            if stat.S_ISDIR(info.st_mode):
                walk(path, depth + 1)
                continue
            if not stat.S_ISREG(info.st_mode):
                skipped["special_entries"] += 1
                continue
            folded = relative.casefold()
            if folded in seen:
                raise ValueError("The source has filenames that collide on this filesystem.")
            seen.add(folded)
            is_graph = len(path.relative_to(root).parts) == 2 and folder.name.casefold() in _GRAPH_DIRS and name == "knowledge-graph.json"
            if bytes_read + info.st_size > MAX_TOTAL_BYTES:
                raise ValueError("The source exceeds the snapshot read size limit; choose a smaller folder.")
            data = _read(path, root, MAX_GRAPH_BYTES if is_graph else MAX_FILE_BYTES)
            bytes_read += len(data)
            text = _decode(data)
            if text is None:
                skipped["binary_or_encoding"] += 1
                continue
            if secret_patterns.contains_secret(text):
                skipped["secret_content"] += 1
                continue
            encoded = text.encode("utf-8")
            if len(files) >= MAX_FILES or total + len(encoded) > MAX_TOTAL_BYTES:
                raise ValueError("The source exceeds the snapshot file or total size limit; choose a smaller folder.")
            files[relative] = encoded
            total += len(encoded)
            for part in (relative.encode("utf-8"), encoded):
                digest.update(len(part).to_bytes(8, "big"))
                digest.update(part)

    try:
        walk(root, 0)
    except (OSError, RuntimeError) as exc:
        raise ValueError("The source could not be read completely; no snapshot was created.") from exc
    if not files:
        raise ValueError("That folder has no readable text source to copy.")
    return files, {
        "kind": "local-folder", "source_name": root.name,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "copied_files": len(files), "copied_bytes": total,
        "skipped_entries": sum(skipped.values()), "skipped_by_reason": dict(sorted(skipped.items())),
        "sha256": digest.hexdigest(), "limitations": list(_LIMITATIONS),
    }


def _git(repo: Path, *args: str) -> None:
    # Source-controlled attributes may name filters; no external configuration
    # or inherited GIT_* variable can supply a command for those names here.
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT="0", GIT_ATTR_NOSYSTEM="1")
    command = ["git", "-C", str(repo), "-c", "user.name=Friday", "-c", "user.email=friday@local",
               "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false",
               "-c", "core.hooksPath=" + str(repo / ".git" / "disabled-hooks"),
               "-c", "init.templateDir=", "-c", "core.attributesFile=" + os.devnull, *args]
    cp = subprocess.run(command, capture_output=True, timeout=60, env=env,
                        creationflags=cb._POPEN_FLAGS)
    if cp.returncode:
        raise ValueError("The managed snapshot could not be initialized.")


def create(title: str, source_path: str, conversation_id: str | None = None) -> dict:
    """Create a separately editable text snapshot, failing before any partial import."""
    source = _source_path(source_path)
    files, provenance = _collect(source)
    cid = cb.new_id()
    destination = cb._dir(cid)
    repo = destination / "repo"
    name = str(title or "").strip() or source.name
    created = False
    try:
        destination.mkdir(parents=True, exist_ok=False)
        created = True
        repo.mkdir()
        for relative, data in files.items():
            target = repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        _git(repo, "init", "-q", "-b", "main")
        # Ignore Friday's receipts without replacing the source's .gitignore.
        exclude = repo / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text(".friday/\n", encoding="utf-8")
        _git(repo, "add", "-f", "--all", "--", ".")
        _git(repo, "commit", "-q", "-m", "Start: " + name)
        (repo / ".friday" / "receipts").mkdir(parents=True)
        record = {
            "id": cid, "title": name, "slug": cb.slug_for(name), "template": None, "tier": "B0",
            "repo": str(repo), "branch": "main", "existing": False,
            "conversation_id": conversation_id, "created_at": provenance["created_at"],
            "seats": dict(cb.DEFAULT_SEATS), "key_profile": "mine", "source_snapshot": provenance,
        }
        cb._save(record)
        if conversation_id:
            cb.bind(cid, conversation_id)
        return record
    except Exception:
        # Only this call's newly created id is eligible for rollback.
        if created and destination.parent.resolve() == cb._root().resolve() and not _linked(destination.lstat()):
            shutil.rmtree(destination)
        raise
