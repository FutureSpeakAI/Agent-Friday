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

import collections
import concurrent.futures
import fnmatch
import os
import threading
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


def scope_covered(principal: str, scope) -> bool:
    """Is this folder (or file) still one the owner's consent covers? Read live. A grant with a glob such as
    `*.md` covers its folder: the glob judges each FILE (`allowed`), never the folder's own name. False while
    the ledger is suspended, for a never-send path and for a credential place."""
    if suspended():
        return False
    fg = _fg()
    p = Path(scope)
    if fg.check_grant(p).state == "denied":
        return False
    try:
        rp = p.resolve()
    except OSError:
        return False
    from agent_friday.services.library import indexer
    if indexer._refused(rp):
        return False
    for a in active_scopes(principal):
        try:
            root = _resolved_root(str(Path(a["path"])))
        except OSError:
            continue
        if _norm(rp) == _norm(root):
            return True
        if a.get("type") != "file":
            try:
                rel = rp.relative_to(root)
            except ValueError:
                continue
            if a.get("recursive", True) or len(rel.parts) <= 1:
                return True
    return False


def suspended() -> bool:
    return bool(_fg()._load_state().suspended)


def _allowed_uncached(principal: str, path: Path) -> bool:
    """The whole consent check, worked out from scratch. `allowed` must always agree with it."""
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


# -- the kept refusals ----------------------------------------------------------
#
# A search asks about every indexed document, and the part of the answer that costs is the
# refusal: the never-send list, the credential places and names, each worked out on the
# document's resolved path. That part is kept, per document, and nothing else is:
#
#   * a kept "refused / not refused" is read back only for the same spelling, the same RESOLVED
#     path (resolved afresh on every ask, so a retargeted link or junction is a different key),
#     the same never-send list and credential rules as when it was kept. A name whose verdict
#     depends on what the file holds (*.key, credential_paths._CONTENT_GATED) is never kept;
#   * the owner's consents (adds, removals, expiry) are NEVER kept. They are read from the ledger
#     as it stands (re-read at least every _RECHECK_S inside a batch), and a deny mark or a rule
#     change drops every kept refusal at once. The tracked-roots lists (_TRACKED_CACHE, _ROOTS)
#     keep their own _ROOT_TTL_S cache, in the uncached check as well, so a grant behind the
#     tracked consent can go on covering for up to that long;
#   * anything this module cannot vouch for (a ledger without a revision, a path spelled with a
#     variable or a ~, any error while working out the rules) goes the long way, uncached.
#
# Bounded: _KEPT_MAX entries, oldest out first, and none older than _KEPT_TTL_S.

_KEPT_MAX = 16384
_KEPT_TTL_S = 120.0
_RECHECK_S = 0.025          # how often a batch looks at the ledger again between documents
_PRIME_MIN = 32             # fewer paths than this are resolved one at a time
_PRIME_THREADS = 8
_PRIME_GOOD_S = 2.0         # a path resolved ahead of its turn is used only within this long
_KEPT_LOCK = threading.Lock()
_KEPT: "collections.OrderedDict[tuple, tuple[float, bool]]" = collections.OrderedDict()
_KEPT_RULES: dict = {"fp": None, "serial": 0}


def _rules_fingerprint():
    """Everything the refusal check reads besides the path and the never-send list."""
    from agent_friday.services import credential_paths as cp
    from agent_friday.services import studio_files as sf
    from agent_friday.services.library import indexer
    dirs, files, _lookups = cp._norms()
    return (str(_fg()._ledger_path()), str(cp._home()), str(cp._friday()), str(cp._app_dir()),
            dirs, files, tuple(sf._excluded()), indexer.SECRET_NAME.pattern,
            tuple(indexer.HOME_CONTENT_ALLOW), cp._NAME_GLOBS, cp._CONTENT_GATED)


def _never_send_fingerprint(state):
    """The never-send list as it stands, each file or folder mark with where it points now."""
    out = []
    for d in state.denies.values():
        rp = None
        if d.get("type") in ("file", "folder"):
            try:
                rp = str(Path(d.get("path") or "").resolve())
            except (OSError, ValueError):
                rp = None
        out.append((str(d.get("id")), str(d.get("type")), str(d.get("path")), rp))
    return tuple(sorted(out))


def _kept_serial(fp) -> int:
    """The generation of kept refusals that belongs to `fp`; a different fp starts a new one."""
    with _KEPT_LOCK:
        if _KEPT_RULES["fp"] != fp:
            _KEPT_RULES["fp"] = fp
            _KEPT_RULES["serial"] += 1
            _KEPT.clear()
        return _KEPT_RULES["serial"]


def forget_kept() -> None:
    with _KEPT_LOCK:
        _KEPT.clear()
        _KEPT_RULES["fp"] = None
        _KEPT_RULES["serial"] += 1


def _content_gated(*names: str) -> bool:
    """Does any of these names get judged by what the file holds?"""
    from agent_friday.services import credential_paths as cp
    for n in names:
        plain = cp._plain_name(n)
        if any(fnmatch.fnmatch(plain, pat) for pat, _why in cp._CONTENT_GATED):
            return True
    return False


def _refused_uncached(path: Path, rp: Path) -> bool:
    from agent_friday.services.library import indexer
    return _fg().check_grant(path).state == "denied" or bool(indexer._refused(rp))


class Allower:
    """`allowed` for many paths of one principal (a search, a purge): the same answer, the
    expensive refusal check kept as above, the consents read live."""

    def __init__(self, principal: str):
        self.principal = principal
        self._primed: dict[str, Path] = {}
        self._primed_at = 0.0
        self._build()

    def _build(self) -> None:
        self.ok = False
        self.scopes: list[dict] = []
        self.rev = None
        self.next_expiry = None
        self.serial = -1
        self._checked = time.monotonic()
        try:
            fg = _fg()
            for _attempt in range(4):
                # One ledger read: the scopes, the fingerprint and the revision must all come from
                # the same state, so a mark landing mid-build is seen by the next attempt.
                state = fg._load_state()
                rev = getattr(state, "rev", None)
                if not isinstance(rev, int):
                    return                            # a ledger that cannot name its revision keeps nothing
                scopes = active_scopes(self.principal)
                now = time.time()
                ends = [float(e["expires_ts"]) for part in (state.grants, state.denies, state.library)
                        for e in part.values() if e.get("expires_ts") and float(e["expires_ts"]) > now]
                fp = (_rules_fingerprint(), _never_send_fingerprint(state))
                if getattr(fg._load_state(), "rev", None) == rev:
                    self.scopes = scopes
                    self.next_expiry = min(ends) if ends else None
                    self.serial = _kept_serial(fp)
                    self.rev = rev
                    self.ok = True
                    return
            # the ledger kept moving: keep nothing, ask the long way
        except Exception:                             # noqa: BLE001 - what cannot be vouched for is not kept
            self.ok = False
            self.scopes = []

    def _fresh(self) -> bool:
        """Still the ledger this was built from? A change or an expiry rebuilds it."""
        if self.next_expiry is not None and time.time() >= self.next_expiry:
            self._build()
        elif time.monotonic() - self._checked >= _RECHECK_S:
            self._checked = time.monotonic()
            try:
                changed = getattr(_fg()._load_state(), "rev", None) != self.rev
            except Exception:                         # noqa: BLE001
                changed = True
            if changed:
                self._build()
        return self.ok

    def _eligible(self, path: Path, text: str) -> bool:
        return self.ok and path.is_absolute() and not any(c in text for c in "%$~")

    def _refusal(self, path: Path, rp: Path, text: str) -> bool:
        """Is `path` (resolving to `rp`) refused by the never-send list or the credential rules?
        Kept when asked the same way again; worked out when not."""
        if _content_gated(path.name, rp.name):
            return _refused_uncached(path, rp)        # judged by what the file holds: never kept
        key = (self.serial, text, str(rp))
        now = time.monotonic()
        with _KEPT_LOCK:
            hit = _KEPT.get(key)
            if hit is not None and now - hit[0] < _KEPT_TTL_S:
                _KEPT.move_to_end(key)
                return hit[1]
        refused = _refused_uncached(path, rp)
        with _KEPT_LOCK:
            if self.serial == _KEPT_RULES["serial"]:
                _KEPT[key] = (now, refused)
                _KEPT.move_to_end(key)
                while len(_KEPT) > _KEPT_MAX:
                    _KEPT.popitem(last=False)
        return refused

    def prime(self, paths) -> None:
        """Resolve many paths at once, just before they are asked about. Resolving is a call into the
        operating system that waits on the disk; many at a time cost little more than one. The result
        is single use and good for a moment: each path is judged again, on its own, when asked about."""
        paths = [Path(p) for p in paths]
        if len(paths) < _PRIME_MIN or not self._fresh():
            return

        def one(p: Path):
            try:
                return str(p), p.resolve()
            except (OSError, ValueError):
                return str(p), None
        with concurrent.futures.ThreadPoolExecutor(max_workers=_PRIME_THREADS) as pool:
            self._primed = dict(pool.map(one, paths))
        self._primed_at = time.monotonic()

    def _resolve(self, path: Path, text: str) -> Path:
        if self._primed:
            if time.monotonic() - self._primed_at < _PRIME_GOOD_S:
                rp = self._primed.pop(text, None)
                if rp is not None:
                    return rp
            else:
                self._primed = {}
        return path.resolve()

    def __call__(self, path) -> bool:
        path = Path(path)
        text = str(path)
        if not self._fresh() or not self._eligible(path, text):
            return _allowed_uncached(self.principal, path)
        try:
            rp = self._resolve(path, text)
        except (OSError, ValueError):
            return _allowed_uncached(self.principal, path)
        try:
            refused = self._refusal(path, rp, text)
        except Exception:                             # noqa: BLE001 - any doubt goes the long way
            return _allowed_uncached(self.principal, path)
        if refused:
            return False
        return any(_covers(a, rp) for a in self.scopes)


def allower(principal: str) -> Allower:
    return Allower(principal)


def allowed(principal: str, path: Path) -> bool:
    """May this path be read into this principal's Library right now?

    False while the ledger is suspended (no add is trusted), for a denied path,
    and for a path no active add of the principal covers. Read on every call,
    so a removal takes effect before the next purge pass."""
    return Allower(principal)(path)
