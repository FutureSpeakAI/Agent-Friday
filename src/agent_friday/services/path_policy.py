"""Where Friday's file tools may read and write: one canonical path policy (BD-26).

Before this, `read_file` took any absolute path (anything on the disk the owner's
account could read: other apps' data under AppData, Friday's own vault and keys,
system folders) and `write_file` was held only by a hook that settings could switch
off. A file op now resolves its path once, here, and is allowed only inside:

  * the owner's home folder (the sandbox root, FRIDAY_SANDBOX_ROOT), except the
    places no assistant needs to reach: application data (AppData) and Friday's own
    state folder (~/.friday), whose output folders are the only parts allowed;
  * Friday's output folders (creations, documents);
  * folders the owner added to the Library (reads only), and file-search roots;
  * folders the owner granted on screen (Settings > Privacy > Folders Friday may
    use): a root, the ops (read, write), an expiry and a purpose, signed with the
    governance key so a model or a program cannot add one by editing a file.

Text is judged before the disk is touched: network shares, device paths, URLs,
relative paths and alternate data streams are refused. The path is then resolved
(links, junctions, `..`, trailing dots, short names) and containment is judged on the
resolved, case-folded form, so no spelling walks around it. Credential files keep
their own refusal (credential_paths); this decides reach, not content.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

OPS = ("read", "write")
MAX_GRANT_DAYS = 90
DEFAULT_GRANT_DAYS = 30
REFUSAL_PREFIX = "[SANDBOX DENY]"
WHERE_TO_ALLOW = "Settings > Privacy > Folder grants"

_LOCK = threading.Lock()


# ── text and resolution ───────────────────────────────────────────────────────

def _text_reason(raw: str) -> Optional[str]:
    from agent_friday.services import file_grants
    why = file_grants.unsafe_path_reason(raw)
    if why:
        return why
    t = os.path.expanduser(str(raw))
    drive, rest = os.path.splitdrive(t)
    # An alternate data stream (name:stream) is another file behind the same name.
    if ":" in rest:
        return "a name with ':' (an alternate data stream) is not allowed"
    # Windows drops a trailing dot or space from a name, so 'AppData.' would reach
    # AppData while not looking like it to a comparison of text.
    for part in rest.replace("/", "\\").split("\\"):
        if part not in ("", ".", "..") and part != part.rstrip(". "):
            return "a name ending in a dot or a space is not allowed"
    return None


def _norm(p: Path) -> str:
    return os.path.normcase(str(p))


def _within(p: Path, root: Path) -> bool:
    a, b = _norm(p), _norm(root)
    if a == b:
        return True
    return a.startswith(b.rstrip("\\/") + os.sep)


def _within_folded(p: Path, root: Path) -> bool:
    """Containment with case folded on every platform: used for refusals, where
    matching too much only refuses more."""
    a, b = str(p).casefold(), str(root).casefold()
    if a == b:
        return True
    return a.startswith(b.rstrip("\\/") + os.sep)


def resolve(raw: str) -> Tuple[Optional[Path], Optional[str]]:
    """(resolved path, None) or (None, why it is refused). Judges the text first, so a
    refused spelling never touches the disk or the network."""
    raw = str(raw or "").strip()
    why = _text_reason(raw)
    if why:
        return None, why
    try:
        p = Path(os.path.expanduser(raw)).resolve(strict=False)
    except Exception:  # noqa: BLE001
        return None, "the path could not be resolved"
    return p, None


# ── where ─────────────────────────────────────────────────────────────────────

def _profile() -> Path:
    return Path(os.path.expanduser("~")).resolve(strict=False)


def _home() -> Path:
    """The sandbox root. A root set above the owner's profile (a whole drive, the
    Users folder) is clamped to the profile: it can narrow, never widen."""
    import agent_friday.core as core
    root = os.environ.get("FRIDAY_SANDBOX_ROOT", "") or str(core.HOME)
    r = Path(os.path.expanduser(root)).resolve(strict=False)
    prof = _profile()
    if r != prof and _within(prof, r):
        return prof
    return r


def output_dirs() -> List[Path]:
    import agent_friday.core as core
    out = []
    for d in (getattr(core, "CREATIONS_DIR", None), getattr(core, "DAILY_CREATIONS_DIR", None)):
        if d:
            out.append(Path(d).resolve(strict=False))
    try:
        from agent_friday.services import office_engine
        out.append(Path(office_engine.DOCUMENTS_DIR).resolve(strict=False))
    except Exception:
        pass
    return out


#: Under the home folder, never reached by a file tool unless the owner granted it:
#: other programs' data and settings, and stores that hold sign-ins and history.
_APP_DATA = ("AppData", "Library/Application Support", "Library/Keychains", "Library/Cookies",
             ".config", ".local/share", ".local/state", ".mozilla", ".thunderbird")
_SIGN_IN_STORES = (".ssh", ".aws", ".azure", ".gnupg", ".kube", ".docker", ".claude", ".codex",
                   ".npmrc", ".pypirc", ".netrc", ".git-credentials", ".cargo/credentials",
                   ".cargo/credentials.toml", ".m2", ".terraform.d", ".gem/credentials",
                   ".bash_history", ".zsh_history", ".python_history", ".node_repl_history",
                   ".psql_history", ".mysql_history", ".lesshst", ".viminfo")
#: Never WRITTEN by a file tool (reading is fine): files a shell, git or PowerShell
#: runs or obeys at start-up, so a write there is code that runs later.
_AUTORUN = (".bashrc", ".bash_profile", ".profile", ".zshrc", ".zprofile", ".zshenv", ".gitconfig",
            ".gitattributes", ".inputrc", ".wgetrc", ".curlrc", ".npmrc", ".pip", "pip",
            "Documents/WindowsPowerShell", "Documents/PowerShell", ".vscode", ".ipython",
            ".jupyter", "Start Menu", ".config/autostart")


def _off_limits(op: str) -> List[Tuple[Path, str]]:
    import agent_friday.core as core
    homes = {_home(), _profile()}
    # The same folders as spelled (not resolved): a resolved home is the long name,
    # a command's path is judged by its text, which may carry the 8.3 short name
    # (C:\Users\RUNNER~1). Matching too much only refuses more.
    for raw in (os.path.expanduser("~"), str(core.HOME), os.environ.get("FRIDAY_SANDBOX_ROOT", "")):
        if raw:
            homes.add(Path(os.path.normpath(os.path.abspath(raw))))
    out: List[Tuple[Path, str]] = [(Path(core.FRIDAY_DIR).resolve(strict=False), "Friday's own state folder")]
    for var in ("APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        v = os.environ.get(var)
        if v:
            zone = Path(v).resolve(strict=False)
            # A zone is carved OUT of the home. One that contains the sandbox root
            # (a root the owner set inside %TEMP%, which lives under LOCALAPPDATA)
            # would refuse the whole root, so it does not apply there. On a normal
            # install the root is the profile and LOCALAPPDATA sits inside it.
            if _within_folded(_home(), zone):
                continue
            out.append((zone, "application data"))
    for h in homes:
        out += [(h / rel, "application data") for rel in _APP_DATA]
        out += [(h / rel, "a store of sign-ins or history") for rel in _SIGN_IN_STORES]
        if op == "write":
            out += [(h / rel, "a file run or obeyed at start-up") for rel in _AUTORUN]
    # The same rule for the profile's own zones (profile\AppData holding a sandbox
    # root under %TEMP%). Friday's state folder always stays refused.
    root = _home()
    return [out[0]] + [(z, label) for z, label in out[1:] if not _within_folded(root, z)]


def _search_roots() -> List[Path]:
    try:
        from agent_friday.services import file_search
        return [Path(r).resolve(strict=False) for r in file_search._configured_roots().values()]
    except Exception:
        return []


def _library_allows(p: Path) -> bool:
    try:
        from agent_friday.services.library import grants as lg, principal as lp
        who = lp.current() or "owner"
        return bool(lg.allowed(who, p))
    except Exception:
        return False


def off_limits_reason(p: Path, op: str = "read") -> Optional[str]:
    """Why a resolved path is in a refused zone (ignoring grants), or None. Cheap: for
    a walk that judges every file it opens."""
    for root, label in _off_limits(op):
        if _within_folded(p, root):
            return f"it is inside {label}"
    return None


def judge(raw: str, op: str) -> Tuple[Optional[Path], Optional[str]]:
    """May a file tool `op` ("read" or "write") at `raw`? (path, None) when allowed;
    (None, reason) when not. Fails closed."""
    if op not in OPS:
        return None, "unknown file operation"
    p, why = resolve(raw)
    if p is None:
        return None, why
    try:
        if any(_within(p, d) for d in output_dirs()):
            return p, None
        zone = off_limits_reason(p, op)
        if zone:
            return (p, None) if _grant_for(p, op) is not None else (None, zone)
        if _within(p, _home()):
            return p, None
        if _grant_for(p, op) is not None:
            return p, None
        if op == "read" and (any(_within(p, r) for r in _search_roots()) or _library_allows(p)):
            return p, None
        return None, "it is outside the folders Friday may use"
    except Exception:  # noqa: BLE001
        return None, "the folder check could not run"


def refusal(raw: str, why: str, op: str) -> str:
    return (f"{REFUSAL_PREFIX} Nothing was {'read' if op == 'read' else 'written'}: that path is "
            f"not a place Friday's file tools may {op} ({why}). Tell the owner; they can allow a "
            f"folder on Friday's own screen ({WHERE_TO_ALLOW}). Do not try another spelling of "
            f"the same path.")


# ── owner folder grants ───────────────────────────────────────────────────────

def _store() -> Path:
    import agent_friday.core as core
    return Path(core.FRIDAY_DIR) / "governance" / "folder_grants.json"


def _key() -> bytes:
    from agent_friday.governance import action_gate
    return action_gate._governance_key()


def _sign(g: Dict[str, Any]) -> str:
    body = json.dumps({k: g[k] for k in sorted(g) if k != "sig"}, sort_keys=True).encode("utf-8")
    return hmac.new(_key(), body, hashlib.sha256).hexdigest()


def _read() -> List[Dict[str, Any]]:
    p = _store()
    if not p.exists():
        return []
    try:
        rows = json.loads(p.read_text(encoding="utf-8") or "[]")
    except Exception:
        return []
    out = []
    for g in rows if isinstance(rows, list) else []:
        try:
            if isinstance(g, dict) and hmac.compare_digest(str(g.get("sig", "")), _sign(g)):
                out.append(g)
        except Exception:
            continue
    return out


def _write(rows: List[Dict[str, Any]]) -> None:
    p = _store()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def grants(*, include_expired: bool = False) -> List[Dict[str, Any]]:
    now = time.time()
    rows = _read()
    return rows if include_expired else [g for g in rows if float(g.get("expires_at") or 0) > now]


def _grant_for(p: Path, op: str) -> Optional[Dict[str, Any]]:
    for g in grants():
        if op in (g.get("ops") or []) and _within(p, Path(g["root"])):
            return g
    return None


def add_grant(root_text: str, ops: List[str], *, days: int = DEFAULT_GRANT_DAYS,
              purpose: str = "", by: str = "owner:ui") -> Tuple[Optional[Dict[str, Any]], str]:
    """Record an owner's folder grant: (grant, "") or (None, why not). The caller has
    checked the press."""
    from agent_friday.services import file_grants
    ops = sorted({str(o).lower() for o in (ops or []) if str(o).lower() in OPS})
    if not ops:
        return None, "choose read, write or both"
    p, why = resolve(root_text)
    if p is None:
        return None, "that folder cannot be allowed: " + (_text_reason(root_text) or "it could not be resolved")
    if not p.is_dir():
        return None, "that is not a folder on this computer"
    if file_grants.too_broad_folder_reason(p) or _within(_home(), p) or _within(_profile(), p):
        return None, "that folder is too broad; choose a folder inside it"
    for zone, label in _off_limits("write"):
        if _within_folded(zone, p) and not _within_folded(p, zone):
            return None, f"that folder holds {label}; choose a folder inside it"
    import agent_friday.core as core
    fd = Path(core.FRIDAY_DIR).resolve(strict=False)
    if _within(p, fd) or _within(fd, p):
        return None, "Friday's own state folder cannot be granted"
    try:
        days = max(1, min(int(days or DEFAULT_GRANT_DAYS), MAX_GRANT_DAYS))
    except (TypeError, ValueError):
        days = DEFAULT_GRANT_DAYS
    now = time.time()
    g = {"id": "fg_" + hashlib.sha256(f"{p}{now}".encode()).hexdigest()[:12], "root": str(p),
         "ops": ops, "purpose": str(purpose or "")[:200], "granted_at": now,
         "expires_at": now + days * 86400, "by": by}
    g["sig"] = _sign(g)
    with _LOCK:
        rows = [r for r in _read() if float(r.get("expires_at") or 0) > now]
        rows.append(g)
        _write(rows)
    return g, ""


def revoke(grant_id: str) -> bool:
    with _LOCK:
        rows = _read()
        keep = [r for r in rows if r.get("id") != grant_id]
        if len(keep) == len(rows):
            return False
        _write(keep)
    return True
