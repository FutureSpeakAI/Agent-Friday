"""Consent to read a path into the Library: "add to the Library".

The record lives in the file-grants ledger (HMAC-signed, append-only, one
reader), so a tampered line suspends the Library exactly as it suspends cloud
grants, and a deny mark beats an add. An add lets Friday read and index a path
on this PC. It grants nothing toward a cloud model: that stays the separate
file grant, checked per document at send time.

Two doors create an add, both by the owner's own act: the Library workspace's
own folder picker (source "you"), and an approved card Friday raised (source
"card"). Nothing here is reachable by a model without that card.
"""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

from agent_friday.user_errors import UserFacingValueError

KIND = "library_add_request"
REMOVE_KIND = "library_remove_request"
FORGET_KIND = "library_forget_request"


def _fg():
    from agent_friday.services import file_grants
    return file_grants


def _norm(path) -> str:
    return os.path.normcase(str(path))


_ROOTS: dict[str, tuple[float, Path]] = {}
_ROOT_TTL_S = 5.0


def _resolved_root(root_text: str) -> Path:
    """A consent's root, resolved. Kept for a few seconds: a search asks about every document
    against every consent, and resolving the same root each time is a syscall per pair."""
    now = time.monotonic()
    hit = _ROOTS.get(root_text)
    if hit and now - hit[0] < _ROOT_TTL_S:
        return hit[1]
    try:
        r = Path(root_text).resolve()
    except OSError:
        r = Path(root_text)
    if len(_ROOTS) > 256:
        _ROOTS.clear()
    _ROOTS[root_text] = (now, r)
    return r


def _covers(add: dict, rp: Path) -> bool:
    """Does this consent cover `rp`, a path already resolved (symlinks followed)?"""
    root = Path(add["path"])
    if add.get("type") == "file":
        rr = _resolved_root(str(root))
        return _norm(rp) == _norm(rr) if root.exists() else _norm(rp) == _norm(root)
    try:
        rel = rp.relative_to(_resolved_root(str(root)))
    except (ValueError, OSError):
        return False
    if not add.get("recursive", True) and len(rel.parts) > 1:
        return False
    if add.get("glob"):
        import fnmatch
        if not fnmatch.fnmatch(rp.name.lower(), str(add["glob"]).lower()):
            return False
    return True


def describe(path_text: str) -> dict:
    """Check a path the way a card or the picker would, and say why not.
    Returns {ok, path, type} or {ok: False, error}."""
    fg = _fg()
    text = str(path_text or "").strip()
    why = fg.unsafe_path_reason(text)
    if why:
        return {"ok": False, "path": text, "error": why}
    try:
        p = Path(text).expanduser().resolve()
    except Exception:
        return {"ok": False, "path": text, "error": "that is not a path"}
    if not p.exists():
        return {"ok": False, "path": str(p), "error": "nothing exists at that path"}
    kind = "folder" if p.is_dir() else "file"
    if kind == "folder":
        broad = fg.too_broad_folder_reason(p)
        if broad:
            return {"ok": False, "path": str(p), "error": broad}
    from agent_friday.services.library import indexer
    if indexer._refused(p):
        return {"ok": False, "path": str(p), "error": "Friday doesn't read this location"}
    if fg.check_grant(p).state == "denied":
        return {"ok": False, "path": str(p), "error": "this path is on your never-send list"}
    return {"ok": True, "path": str(p), "type": kind}


def add_scope(principal: str, path_text: str, *, recursive: bool = True, glob: str | None = None,
              kinds: list[str] | None = None, source: str = "you") -> dict:
    """Record the consent. Raises UserFacingValueError for a path that cannot be added."""
    d = describe(path_text)
    if not d["ok"]:
        raise UserFacingValueError(d["error"])
    event = {"event": "library_add", "id": str(uuid.uuid4()), "principal": principal,
             "type": d["type"], "path": d["path"], "recursive": bool(recursive),
             "glob": glob or None, "kinds": list(kinds) if kinds else None,
             "source": source if source in ("you", "card") else "you", "created_ts": time.time()}
    return _fg()._append_event(event)


def remove_scope(principal: str, target_id: str) -> dict:
    event = {"event": "library_remove", "id": str(uuid.uuid4()), "principal": principal,
             "target_id": target_id, "created_ts": time.time()}
    return _fg()._append_event(event)


def set_shelf(principal: str, path: str, shelf: str) -> dict:
    if shelf not in ("open", "vault"):
        raise UserFacingValueError("shelf must be 'open' or 'vault'")
    event = {"event": "library_shelf", "id": str(uuid.uuid4()), "principal": principal,
             "path": str(Path(path).resolve()), "shelf": shelf, "created_ts": time.time()}
    return _fg()._append_event(event)


def shelf_override(principal: str, path: Path) -> str | None:
    st = _fg()._load_state()
    try:
        key = (principal, _norm(path.resolve()))
    except OSError:
        return None
    return st.library_shelves.get(key)


#: The one consent that covers everything Friday already tracks (below). It is
#: an ordinary library_add in the ledger, so removing it, revoking it, a deny
#: and a tampered ledger all treat it exactly as they treat any other add.
TRACKED = "tracked"


def add_tracked(principal: str, *, source: str = "you", said: str = "") -> dict:
    """Record the one bulk consent: the Library covers every file Friday
    already tracks. `said` keeps the owner's own words when the consent was
    given in conversation rather than with the switch."""
    for a in active_scopes(principal, expand=False):
        if a.get("type") == TRACKED:
            return a                                  # already on: one consent, never a second
    event = {"event": "library_add", "id": str(uuid.uuid4()), "principal": principal,
             "type": TRACKED, "path": "", "recursive": True, "glob": None, "kinds": None,
             "source": source if source in ("you", "card") else "you",
             "said": str(said or "")[:500] or None, "created_ts": time.time()}
    return _fg()._append_event(event)


def tracked_consent(principal: str) -> dict | None:
    for a in active_scopes(principal, expand=False):
        if a.get("type") == TRACKED:
            return a
    return None


_TRACKED_CACHE: dict = {}


def tracked_roots() -> list[dict]:
    """Kept for _ROOT_TTL_S: a purge asks about every document, and working the
    roots out is a ledger read plus a check of each root."""
    now = time.monotonic()
    hit = _TRACKED_CACHE.get("roots")
    if hit and now - hit[0] < _ROOT_TTL_S:
        return [dict(r) for r in hit[1]]
    roots = _tracked_roots()
    _TRACKED_CACHE["roots"] = (now, roots)
    return [dict(r) for r in roots]


def _tracked_roots() -> list[dict]:
    """What "everything Friday tracks" is, worked out on every read (never a
    copy that goes stale): the files and folders the owner granted, and the
    folders Media is built from. A place the Library never reads (Friday's own
    home, the vault, credential files) is left out here and refused again by
    the indexer; a deny beats it in allowed()."""
    from agent_friday.services.library import indexer
    out, seen = [], set()

    def keep(key, path, kind):
        try:
            p = Path(path).expanduser().resolve()
        except (OSError, ValueError):
            return
        if not p.exists() or _norm(p) in seen or indexer._refused(p):
            return
        seen.add(_norm(p))
        out.append({"key": key, "path": str(p), "type": "folder" if p.is_dir() else "file"})
    now = time.time()
    for g in _fg().list_grants():
        if g.get("type") not in ("file", "folder"):
            continue                                  # a glob has no folder to walk
        if g.get("expires_ts") and float(g["expires_ts"]) <= now:
            continue
        keep("grant:" + str(g.get("id")), g.get("path") or "", g["type"])
    try:
        from agent_friday.services import media_index
        for r in media_index._roots():
            keep("media:" + Path(r).name, r, "folder")
    except Exception:
        pass
    return out


def active_scopes(principal: str, *, expand: bool = True) -> list[dict]:
    """The principal's live consents. The tracked consent is expanded into one
    scope per tracked root (ids "<consent id>:<root key>") unless `expand` is
    False; it covers nothing by itself."""
    st = _fg()._load_state()
    own = [dict(a) for a in st.library.values() if (a.get("principal") or "owner") == principal]
    if not expand:
        return own
    out = []
    for a in own:
        if a.get("type") != TRACKED:
            out.append(a)
            continue
        for r in tracked_roots():
            out.append({"event": "library_add", "id": "%s:%s" % (a["id"], r["key"]), "parent": a["id"],
                        "principal": principal, "type": r["type"], "path": r["path"], "recursive": True,
                        "glob": None, "kinds": None, "source": TRACKED, "created_ts": a.get("created_ts")})
    return out


def suspended() -> bool:
    return bool(_fg()._load_state().suspended)


def allowed(principal: str, path: Path) -> bool:
    """May this path be read into this principal's Library right now?

    False while the ledger is suspended (no add is trusted), for a denied path,
    and for a path no active add of the principal covers. Read on every call,
    so a removal takes effect before the next purge pass."""
    path = Path(path)
    fg = _fg()
    if fg.check_grant(path).state == "denied":
        return False
    try:
        rp = path.resolve()
    except OSError:
        return False
    from agent_friday.services.library import indexer
    if indexer._refused(rp):
        return False                                  # a credential or private place, whatever covers it
    return any(_covers(a, rp) for a in active_scopes(principal))
