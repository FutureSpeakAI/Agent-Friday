"""The one answer to "what version of Friday am I actually running?"

WHY THIS IS ITS OWN MODULE, AND WHY IT READS DISK.

`install-manifest.json` said 5.6.4 on machines whose files were 5.6.3. The
installer's `Invoke-Step` ran app.copy's -Verify BEFORE the action, and that
verify only checked that four files EXISTED — which they did, because the old
release had put them there. So 5.6.0 through 5.6.4 upgraded 0 of 489 files and
then wrote the new version number into the manifest. The log tell is
"app.copy : verify passed before action".

The consequence for anything that reports a version: the manifest is a record
of INTENT, not of fact. `app\\pyproject.toml` is the fact. It ships in every
payload, and it is exactly what the 5.6.5 installer's `Get-InstalledAppVersion`
reads back off disk before recording anything (packaging/windows/install.ps1).

An update checker built on the manifest would have told those 5.6.3 users they
were current. That is worse than shipping no checker at all, because it
manufactures confidence. So:

  * `running_version()` reads pyproject.toml. Always.
  * `version_truth()` reads both, and reports the DISAGREEMENT, because a
    disagreement is the fingerprint of a copy that did not take and the user
    needs to be told to re-run the installer.
  * Both `friday status` and the weekly update check consume this module.
    There is deliberately no second implementation to drift from this one.

`Get-InstalledAppVersion` returns '' for "cannot tell" and every caller treats
unknown as "not the version we expect". Same discipline here: `None`, never a
guess. A guessed version is what the bug WAS.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Optional

# src/agent_friday/services/app_version.py -> src/agent_friday -> src -> <app>
APP_ROOT = Path(__file__).resolve().parents[3]

_VERSION_RE = re.compile(r'(?m)^version\s*=\s*"([^"]+)"')


def _app_root(app_root: Optional[Path] = None) -> Path:
    return Path(app_root) if app_root is not None else APP_ROOT


def running_version(app_root: Optional[Path] = None) -> Optional[str]:
    """The version of the code ACTUALLY on disk, or None if it cannot be read.

    Same source and same regex as the installer's `Get-InstalledAppVersion`, so
    the two can never disagree about what "the version on disk" means.
    """
    try:
        pp = _app_root(app_root) / "pyproject.toml"
        if not pp.is_file():
            return None
        m = _VERSION_RE.search(pp.read_text(encoding="utf-8"))
        if m:
            return m.group(1).strip() or None
    except OSError:
        pass
    return None


def running_sequence(app_root: Optional[Path] = None) -> Optional[int]:
    """The release ordering number of the code ON DISK, or None.

    Releases are ordered by build sequence, never by version number: Beta 1.x
    (1.1.0b1) is numerically below the 5.x line it replaces. Derived from
    `running_version`, so it can only ever describe the files actually present;
    see `agent_friday.release` for the ordering.
    """
    from agent_friday.release import sequence_for_version
    return sequence_for_version(running_version(app_root))


def display_version(app_root: Optional[Path] = None) -> Optional[str]:
    """What the app shows a person as its version: "Agent Friday Beta 1.1"
    for the Beta build, the plain version for anything else, None if unknown."""
    from agent_friday.release import BUILD_SEQUENCE, RELEASE_NAME, sequence_for_version
    v = running_version(app_root)
    if not v:
        return None
    if sequence_for_version(v) == BUILD_SEQUENCE:
        return RELEASE_NAME
    return v


#: The files whose bytes ARE the page a browser tab runs: the served index and
#: every script, stylesheet and HTML fragment under static/.
_UI_SUFFIXES = (".js", ".css", ".html", ".mjs")
_build_ids: dict = {}
_build_lock = threading.Lock()


def _ui_fingerprint(ui_root: Path) -> str:
    """sha256 over index.html and the UI files under static/, by relative path
    and content, in a fixed order. Missing files simply do not contribute."""
    import hashlib
    h = hashlib.sha256()
    files = []
    idx = ui_root / "index.html"
    if idx.is_file():
        files.append(idx)
    static = ui_root / "static"
    if static.is_dir():
        files.extend(p for p in static.rglob("*")
                     if p.is_file() and p.suffix.lower() in _UI_SUFFIXES)
    for p in sorted(files, key=lambda q: q.relative_to(ui_root).as_posix()):
        try:
            data = p.read_bytes()
        except OSError:
            continue
        h.update(p.relative_to(ui_root).as_posix().encode("utf-8"))
        h.update(b"\0")
        h.update(data)
        h.update(b"\0")
    return h.hexdigest()


def build_id(ui_root: Optional[Path] = None, app_root: Optional[Path] = None) -> str:
    """Which build of the page this server process serves: "<version>+<hash>".

    The version alone cannot tell two builds of the same beta apart, and an
    update that ships a fix under the same version number is exactly the case
    where an open tab keeps running the old JavaScript. So the id also carries
    a short hash of the UI files themselves (index.html and static/).

    Computed ONCE per process and per root, on first use, then served from
    memory: /api/health is polled every few seconds and must stay cheap. A
    restart is what changes the files a tab would get on reload, and a restart
    starts a new process, so once per process is the honest granularity.

    `ui_root` is the directory the server serves index.html and static/ from
    (its working directory); it defaults to the app root.
    """
    root = Path(ui_root) if ui_root is not None else _app_root(app_root)
    try:
        key = str(root.resolve())
    except OSError:
        key = str(root)
    with _build_lock:
        cached = _build_ids.get(key)
        if cached is not None:
            return cached
        try:
            digest = _ui_fingerprint(root)[:12]
        except Exception:  # an unreadable tree still gets a stable, honest id
            digest = "unhashed"
        bid = f"{running_version(app_root) or 'unknown'}+{digest}"
        _build_ids[key] = bid
        return bid


def _reset_build_id_for_tests() -> None:
    with _build_lock:
        _build_ids.clear()


def installed_manifest(app_root: Optional[Path] = None) -> dict:
    """`install-manifest.json` for a packaged install, or {}.

    The installer lays the app out as <InstallRoot>\\app, so the manifest is the
    app dir's SIBLING. utf-8-sig because PowerShell's Out-File writes a BOM.
    """
    try:
        p = _app_root(app_root).parent / "install-manifest.json"
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8-sig"))
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        pass
    return {}


def version_truth(app_root: Optional[Path] = None) -> dict[str, Any]:
    """Everything anyone needs to know about which version this is.

    Returns::

        running       str|None  — read off disk. The only authoritative field.
        manifest      str|None  — what install-manifest.json records.
        installer     str|None  — the version the installer CARRIED.
        packaged      bool      — a manifest exists (installer install, not git).
        disagrees     bool
        disagreement  str|None  — plain-English, safe to show a user verbatim.
    """
    root = _app_root(app_root)
    on_disk = running_version(root)
    manifest = installed_manifest(root)
    m_version = (manifest.get("version") or None) if manifest else None
    m_installer = (manifest.get("installer_version") or None) if manifest else None

    disagreement = None

    # 1. The manifest names a version the disk does not corroborate. A pre-5.6.5
    #    manifest recording an upgrade that never copied anything looks exactly
    #    like this, and it is the case that must never be believed.
    if on_disk and m_version and m_version != on_disk:
        disagreement = (
            f"install-manifest.json records version {m_version}, but the files on "
            f"disk are {on_disk}. The install did not fully take. Re-run the "
            f"latest installer to repair it."
        )

    # 2. A 5.6.5+ manifest MEASURES the disk, so a failed copy shows up here
    #    instead — honestly recorded, still a broken install.
    elif on_disk and m_installer and m_installer != on_disk:
        disagreement = (
            f"the installer carried version {m_installer}, but the files on disk "
            f"are {on_disk}. The copy step did not replace them. Re-run the "
            f"latest installer to repair it."
        )

    # 3. We cannot read our own version, but something claims to have installed
    #    one. Report the gap rather than adopting the claim.
    elif on_disk is None and (m_version or m_installer):
        disagreement = (
            f"install-manifest.json records version "
            f"{m_version or m_installer}, but this install's pyproject.toml "
            f"could not be read, so the version on disk cannot be confirmed."
        )

    return {
        "running": on_disk,
        "manifest": m_version,
        "installer": m_installer,
        "packaged": bool(manifest),
        "disagrees": disagreement is not None,
        "disagreement": disagreement,
        "app_root": str(root),
    }
