"""User-granted cloud egress permissions for local files.

Grounding: "Just fair warning, I can't bring sensitive personal info from
that resume up to the cloud." The gate makes Friday least useful on exactly
the work the user cares most about — analyzing their own documents with a
frontier model — and the realistic
alternative to a grant is worse than a grant: pasting the same content into
the chat box by hand, which crosses the wire anyway with no registry, no
audit, no receipt. A grant inside the system with an audit trail beats a
manual bypass outside it.

THE MECHANISM: a second feeder of the span registry the news fix already
uses. No send-time exemption API exists here — nothing accepts a flag on a
call. `on_file_read()` is called by read_file at the moment a file is
actually read (NOT by search_files — a content-search snippet from a
granted file still gates normally; file_search.py's own `_search_content()`
discloses this as its "KNOWN GAP," failing toward gating rather than
leaking); if the resolved path carries a live grant, it
registers that read's exact paragraphs with `egress_gate.register_public_text
(text, origin="user-grant:<id>")`, exactly as news_engine registers a
fetched article. A prompt-injected model cannot register spans: the only way
content becomes sendable is that the real file at the granted path was
really read, here, just now.

WHO GRANTS. Nobody but the user, through an authenticated HTTP endpoint
driven by UI chrome (routes/control.py). There is no grant tool anywhere in
CLAUDE_TOOLS — no surface's model can call one. A spoken "yes" can never
create a grant; voice can only point at a pending chip. The two doors are the
Add button in Settings › Privacy › File access and the Approve button on a
file-access approval card (services/file_grant_requests.py). The model's
file_access tool can raise that card; approving it is a click on screen
(approvals.SCREEN_ONLY_KINDS).

GRANULARITY. File grants are content-pinned (SHA-256 at grant time); a later
read with a different hash is `stale` and gates normally. Folder/glob grants
cannot be content-pinned, so they REQUIRE an expiry, enforced here (not just
in a UI) at a hard 30-day maximum.

PRECEDENCE. A deny mark beats any grant at any specificity, no exceptions —
`check_grant()` checks denies before it ever looks at a grant.

DURABILITY. Append-only JSONL at ~/.friday/privacy/file_grants.jsonl,
deliberately separate from settings.json (so the BOM/factory-reset failure
mode that once nuked 83 settings keys cannot touch it). Each line carries an
HMAC over its event, keyed by the app's own secret_key. A line that fails to
parse or fails HMAC is dropped and counted. Dropped GRANT events fail safe
(fewer grants -> normal gating). Dropped DENY events are the dangerous
direction, so: any drop at all puts the whole ledger into SUSPENDERS MODE —
every grant treated as absent, every deny mark still enforced from whatever
folded cleanly, and one high-priority notification per distinct failed line
(not one per read). A corrupted ledger can only ever tighten.

RETIRED KEYS. A grant line that fails under the ledger's key but verifies
under the session secret the ledger used before it had its own key is
authentic but stale. It is moved aside to the quarantine file once, verbatim
(never re-signed, never deleted), and the user is asked once whether to
re-grant it; a re-grant is a fresh approval card. Only grant lines are moved
this way, because removing a grant can only take access away. A deny signed
with a retired key is honoured. A line no known key vouches for is never
trusted for what it says it is: it keeps grants suspended while it is in the
ledger, and still after it is set aside, because it may have been a deny.
"""
from __future__ import annotations

import fnmatch
import hashlib
import hmac
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from agent_friday.user_errors import UserFacingValueError

_MAX_EXPIRY_DAYS = 30
_APPEND_LOCK = threading.Lock()
_STATE_LOCK = threading.Lock()
_STATE_CACHE: dict = {"mtime": -2.0, "state": None}


def _ledger_path() -> Path:
    # Resolved lazily (not at import time) so tests' HOME redirection in
    # conftest.py is honored no matter when this module is first imported.
    from agent_friday.core import FRIDAY_DIR
    return Path(FRIDAY_DIR) / "privacy" / "file_grants.jsonl"


#: The ledger's OWN signing key, deliberately not the Flask session secret.
#:
#: Those two have opposite requirements and used to be the same value. A session
#: secret is allowed to rotate -- rotating it just logs everyone out. A LEDGER
#: signing key must never change, or the ledger's own history stops verifying.
#:
#: `core._load_or_create_secret()` prefers the FRIDAY_SECRET_KEY environment
#: variable over the persisted `~/.friday/secret_key`, so removing that variable
#: from a launcher silently swaps the session secret. A ledger signed with it
#: then stops verifying even though every line is intact and correctly signed
#: under the old key; Friday reports corruption and suspends every grant. That
#: is the right reaction to a moved key, so the key must not be able to move.
_SIGNING_KEY_CACHE: dict = {}


def _signing_key_path() -> Path:
    return _ledger_path().parent / "ledger_signing.key"


def _secret_bytes() -> bytes:
    """The HMAC key for this ledger: persisted once, protected, never from env.

    Stored through `credential_store`, so it lands under the vault key or DPAPI
    when either is available rather than sitting in plaintext next to the file it
    protects. Minted on first use -- callers that short-circuit on a nonexistent
    ledger never create one.
    """
    cached = _SIGNING_KEY_CACHE.get("key")
    if cached:
        return cached
    from agent_friday.services import credential_store as _cs
    path = _signing_key_path()
    key = None
    if path.exists():
        try:
            key = _cs.read_secret(path)
        except Exception as e:
            # A key we cannot read is NOT a reason to mint a new one: that would
            # invalidate every existing line and look exactly like this
            # incident. Fail loudly instead.
            raise RuntimeError(
                "the file-grants signing key exists but could not be read (%s). "
                "Grants stay suspended rather than be re-signed under a new key."
                % e)
    if not key:
        import secrets as _secrets
        key = _secrets.token_bytes(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        _cs.write_secret(path, key)
        try:
            _cs.harden_permissions(path)
        except Exception:
            pass
    _SIGNING_KEY_CACHE["key"] = key
    return key


def _iso_now() -> str:
    """UTC ISO-8601, for the human-readable fields on quarantine records."""
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _hmac_hex(event: dict, key: bytes) -> str:
    canon = json.dumps(event, sort_keys=True, separators=(",", ":"), default=str)
    return hmac.new(key, canon.encode("utf-8"), hashlib.sha256).hexdigest()


# ── Ledger read: verify, fold, and fail toward tightening ─────────────────────

@dataclass
class LedgerState:
    grants: dict = field(default_factory=dict)   # id -> event dict
    denies: dict = field(default_factory=dict)    # id -> event dict
    suspended: bool = False
    dropped: int = 0
    #: Grants that folded cleanly while the ledger is suspended. Shown so the
    #: user can see and revoke them; never consulted by check_grant().
    paused_grants: dict = field(default_factory=dict)
    #: Unauthenticated lines moved to the quarantine file. Each still keeps
    #: grants suspended: what it said cannot be known, so it might have been a
    #: deny, and moving it aside must not be what lets content out.
    held: int = 0


#: Ledger events that can only ever ADD access. Removing one of these from the
#: ledger can only tighten what Friday may send, which is why a line of this
#: kind -- and only this kind -- may be moved aside without asking first.
_GRANT_EVENTS = ("grant_file", "grant_scope")


def _line_sha(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def _retired_keys() -> list[bytes]:
    """Keys this ledger was signed with before it had its own signing key.

    Before the ledger got `ledger_signing.key` it was signed with the session
    secret: FRIDAY_SECRET_KEY when set, else the persisted `secret_key` file.
    Both are READ here, never minted. A line that verifies under one of them is
    authentic but signed with a retired key. Nothing verified this way is ever
    honoured as a grant: such a line is only recognised so it can be moved aside
    and the user offered a fresh grant through the normal approval.
    """
    import os
    keys: list[bytes] = []
    env = os.environ.get("FRIDAY_SECRET_KEY")
    if env:
        keys.append(env.encode("utf-8"))
    try:
        from agent_friday.paths import friday_home
        p = friday_home() / "secret_key"
        if p.exists():
            v = p.read_text(encoding="utf-8").strip()
            if v:
                keys.append(v.encode("utf-8"))
    except Exception:
        pass
    current = _SIGNING_KEY_CACHE.get("key")
    return [k for k in dict.fromkeys(keys) if k and k != current]


def _ledger_lines(path: Path) -> list[tuple[str | None, bytes]]:
    """Every non-blank ledger line as (text, raw bytes), decoded ONE BY ONE.

    A stray undecodable byte spoils only its own line (text None), never the
    rest of the file: every line that verifies is still read, so a deny mark
    beside a damaged line is still enforced. The hash of a line is the hash of
    its raw bytes, which for a readable line equals _line_sha(text)."""
    out: list[tuple[str | None, bytes]] = []
    for raw in path.read_bytes().split(b"\n"):
        b = raw.strip()
        if not b:
            continue
        try:
            out.append((b.decode("utf-8"), b))
        except UnicodeDecodeError:
            out.append((None, b))
    return out


def _known_key_of(line: str) -> tuple[str | None, dict | None]:
    """Which known key signed this ledger line: ("current" | "retired" | None, event).

    The event is returned only when the line parses; it is trustworthy only
    when the first value is not None. A line that verifies under no known key
    is unauthenticated, and nothing in it -- not even what kind of event it
    claims to be -- may decide what happens to grants.
    """
    try:
        rec = json.loads(line)
        ev = rec["event"]
        sig = rec["hmac"]
        if not isinstance(ev, dict) or not isinstance(sig, str):
            raise ValueError("malformed record")
    except Exception:
        return None, None
    if hmac.compare_digest(sig, _hmac_hex(ev, _secret_bytes())):
        return "current", ev
    if any(hmac.compare_digest(sig, _hmac_hex(ev, k)) for k in _retired_keys()):
        return "retired", ev
    return None, ev


def _scan_ledger(path: Path) -> dict:
    """Parse and verify every line, and say which failures are which.

    Returns {"events": [folded events], "dropped": [line hashes that failed],
    "retired_grants": [(line hash, event)]}.

    A line signed with a retired key is authentic, so its kind is known:
      * a DENY is honoured (folded like a current line): a deny can only tighten;
      * a GRANT is never honoured; it is listed in `retired_grants` (and counts
        as dropped) so it can be moved aside once;
      * anything else (a revoke could lift a deny) counts as dropped and keeps
        grants suspended.
    """
    out: dict = {"events": [], "dropped": [], "retired_grants": []}
    if not path.exists():
        return out
    _secret_bytes()
    try:
        entries = _ledger_lines(path)
    except Exception:
        out["dropped"].append("unreadable-ledger")
        return out
    for line, b in entries:
        sha = hashlib.sha256(b).hexdigest()
        if line is None:
            out["dropped"].append(sha)
            continue
        known, ev = _known_key_of(line)
        if known == "current":
            out["events"].append(ev)
            continue
        if known == "retired" and ev.get("event") == "deny":
            out["events"].append(ev)
            continue
        out["dropped"].append(sha)
        if known == "retired" and ev.get("event") in _GRANT_EVENTS:
            out["retired_grants"].append((sha, ev))
    return out


def _read_verified_events(path: Path) -> tuple[list[dict], int]:
    """Parse and HMAC-verify every line. Returns (events, dropped_count).

    A nonexistent ledger (never created) is the honest "no grants yet" case:
    0 events, 0 dropped. A ledger that EXISTS but cannot even be decoded
    (the BOM/encoding corruption class) counts as 1 dropped line rather than
    silently returning empty — corruption must be visible, not indistinguishable
    from "nothing was ever granted".
    """
    scan = _scan_ledger(path)
    return scan["events"], len(scan["dropped"])


def _fold(events: list[dict]) -> tuple[dict, dict]:
    grants: dict = {}
    denies: dict = {}
    for ev in events:
        et = ev.get("event")
        eid = ev.get("id")
        if not eid:
            continue
        if et in ("grant_file", "grant_scope"):
            grants[eid] = ev
        elif et == "deny":
            denies[eid] = ev
        elif et == "revoke":
            tgt = ev.get("target_id")
            grants.pop(tgt, None)
            denies.pop(tgt, None)
    return grants, denies


def _push(**kwargs) -> bool:
    """One notification. True only when a NEW notification was created.

    The engine drops a push whose dedupe_key matches an undismissed entry and
    returns that older entry instead. A token in `meta` tells the two apart, so
    a failure is marked as told only when the user was really told about it.
    """
    try:
        from agent_friday.services.voice_engine import _notif_engine
    except Exception:
        return False
    if not _notif_engine:
        return False
    token = uuid.uuid4().hex
    meta = dict(kwargs.pop("meta", None) or {})
    meta["alarm_token"] = token
    try:
        entry = _notif_engine.push(meta=meta, **kwargs)
    except Exception:
        return False
    if not isinstance(entry, dict):
        return False
    return (entry.get("meta") or {}).get("alarm_token") == token


# ── Notices: one alarm per distinct failure ──────────────────────────────────
#
# The ledger is re-read whenever it changes, and a failed line fails on every
# read. An alarm per read would repeat the same warning until it means nothing,
# so each failure is keyed by the hash of the line that failed and alarms once.
# The record of what has alarmed, and of the open "re-grant it?" questions,
# lives beside the ledger. It decides only what is SAID: nothing in it can make
# a line verify or a grant exist.

_NOTICE_LOCK = threading.Lock()


def _notices_path() -> Path:
    return _ledger_path().parent / "file_grants.notices.json"


def _read_notices() -> dict:
    p = _notices_path()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            d.setdefault("alarmed", {})
            d.setdefault("notices", {})
            if isinstance(d["alarmed"], dict) and isinstance(d["notices"], dict):
                return d
    except Exception:
        pass
    return {"alarmed": {}, "notices": {}}


def _write_notices(d: dict) -> None:
    p = _notices_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, sort_keys=True, indent=1), encoding="utf-8", newline="\n")
    tmp.replace(p)


def _alarm_new_failures(dropped_shas: list[str]) -> None:
    """Alarm once for failures not alarmed before; never for one already told."""
    if not dropped_shas:
        return
    with _NOTICE_LOCK:
        d = _read_notices()
        new = sorted(s for s in dropped_shas if s not in d["alarmed"])
        if not new:
            return
        # The key names the failed lines themselves, so a new failure is never
        # swallowed by an older, still-unread alarm about a different line.
        ident = new[0] if len(new) == 1 else hashlib.sha256(
            "".join(new).encode("ascii")).hexdigest()
        sent = _push(
            title="File-permission ledger corrupted — grants suspended",
            body=(f"{len(dropped_shas)} line(s) of the file-grants ledger failed "
                  f"to verify and were dropped. Every file grant is suspended "
                  f"until this is resolved; your never-send deny marks still "
                  f"apply. Nothing was silently un-denied. Settings › Privacy › "
                  f"File access shows each line and what you can do about it."),
            priority="high", source="file_grants", kind="warning",
            dedupe_key="file_grants_ledger_failure:%s" % ident,
        )
        if not sent:
            return   # not told yet, so not marked: the next read tries again
        now = _iso_now()
        for s in new:
            d["alarmed"][s] = now
        _write_notices(d)


def _date_label(ts) -> str:
    import datetime as _dt
    try:
        t = _dt.datetime.fromtimestamp(float(ts))
    except Exception:
        return "an earlier date"
    return f"{t:%b} {t.day}"


RETIRED_KEY_WHY = ("signed with a retired key; moved aside automatically, never "
                   "re-signed")


def _retired_message(ev: dict) -> str:
    return ("An old permission from %s was signed with a retired key; re-grant it?"
            % _date_label(ev.get("created_ts")))


def _open_retired_notice(sha: str, ev: dict) -> None:
    """Record the one question for a line just moved aside, and say it once."""
    with _NOTICE_LOCK:
        d = _read_notices()
        if sha in d["notices"]:
            return
        d["notices"][sha] = {
            "reason": "retired_key", "status": "open", "opened_at": _iso_now(),
            "path": ev.get("path"), "type": ev.get("type"),
            "created_ts": ev.get("created_ts"), "expires_ts": ev.get("expires_ts"),
        }
        d["alarmed"][sha] = _iso_now()
        _write_notices(d)
    _push(title="An old file permission needs you",
          body=_retired_message(ev) + " It grants nothing until you do.",
          priority="medium", source="file_grants", kind="info",
          target={"workspace": "settings", "tab": "privacy"},
          dedupe_key="file_grants_retired:%s" % sha)


def _sweep_retired_grants(found: list) -> int:
    """Move each retired-key grant line aside, once. Returns how many moved.

    Only grant lines, only ones that verify under a retired key: removing one
    can only take access away. Deny and revoke lines, and lines that verify
    under no key at all, stay where they are and keep grants suspended until
    the user decides (list_unverified / dismiss_unverified)."""
    moved = 0
    for sha, ev in found:
        if _quarantine_line(sha, RETIRED_KEY_WHY, "friday:retired-key-sweep"):
            moved += 1
            _open_retired_notice(sha, ev)
    return moved


def _load_state(force: bool = False) -> LedgerState:
    path = _ledger_path()

    def _mtime() -> float:
        try:
            return path.stat().st_mtime if path.exists() else -1.0
        except Exception:
            return -1.0

    mtime = _mtime()
    with _STATE_LOCK:
        cached = _STATE_CACHE.get("state")
        if not force and cached is not None and _STATE_CACHE.get("mtime") == mtime:
            return cached
    scan = _scan_ledger(path)
    if scan["retired_grants"]:
        # Outside _STATE_LOCK: moving a line aside invalidates the cache.
        try:
            if _sweep_retired_grants(scan["retired_grants"]):
                mtime = _mtime()
                scan = _scan_ledger(path)
        except Exception:
            pass   # the line stays in the ledger: still dropped, still suspending
    held = _held_by_quarantine()
    with _STATE_LOCK:
        grants, denies = _fold(scan["events"])
        dropped = len(scan["dropped"])
        suspended = dropped > 0 or held > 0
        paused: dict = {}
        if suspended:
            paused, grants = grants, {}   # suspenders mode: ALL grants suspended
        state = LedgerState(grants=grants, denies=denies, suspended=suspended,
                            dropped=dropped, paused_grants=paused, held=held)
        _STATE_CACHE["mtime"] = mtime
        _STATE_CACHE["state"] = state
    if suspended:
        _alarm_new_failures(scan["dropped"])
    return state


def _held_by_quarantine() -> int:
    """How many quarantined lines still hold grants suspended."""
    q = _quarantine_path()
    if not q.exists():
        return 0
    try:
        raw = q.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return 1   # cannot tell, so assume the worst
    n = 0
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            if json.loads(line).get("holds_suspension"):
                n += 1
        except Exception:
            continue
    return n


def _invalidate_cache() -> None:
    with _STATE_LOCK:
        _STATE_CACHE["mtime"] = -2.0
        _STATE_CACHE["state"] = None


def _append_event(event: dict) -> dict:
    key = _secret_bytes()
    sig = _hmac_hex(event, key)
    rec = {"event": event, "hmac": sig}
    line = json.dumps(rec, sort_keys=True, separators=(",", ":"), default=str)
    path = _ledger_path()
    with _APPEND_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
    _invalidate_cache()
    return event


# ── Path matching ──────────────────────────────────────────────────────────────

def _same_path(a: str, b: Path) -> bool:
    try:
        return Path(a).resolve() == b.resolve()
    except Exception:
        return str(a) == str(b)


def _covers(rule: dict, path: Path) -> bool:
    t = rule.get("type")
    rp = rule.get("path", "")
    if t == "file":
        return _same_path(rp, path)
    if t == "folder":
        try:
            path.resolve().relative_to(Path(rp).resolve())
            return True
        except (ValueError, OSError):
            return False
    if t == "glob":
        return fnmatch.fnmatch(str(path).replace("\\", "/"), rp.replace("\\", "/"))
    return False


def _split_paragraphs(text: str) -> list[str]:
    """Mirror egress_gate._gate_text_span's own split exactly, so a span
    registered here is the same string the gate will look up."""
    sep = "\n\n" if "\n\n" in text else ("\n" if "\n" in text else None)
    paras = text.split(sep) if sep else [text]
    return [p.strip() for p in paras if p.strip()]


# ── Grant / deny creation ──────────────────────────────────────────────────────

def scan_path(path: Path) -> dict:
    """Classifier findings for a file, for the grant dialog. Generated from
    the system's OWN scan — never parametrized by model text, so a
    prompt-injected file cannot shape what the consent screen shows."""
    from agent_friday.services.file_extraction import extract_text
    from agent_friday.services.sensitivity_classifier import classify, Tier
    from agent_friday.services import judgment_gate as _jg

    result = extract_text(path)
    if result.text is None:
        return {
            "path": str(path), "extractable": False, "error": result.error,
            "tier_counts": {}, "never_send_matches": [], "paragraph_count": 0,
            "summary": result.error,
        }
    paras = _split_paragraphs(result.text)
    tier_counts = {"TIER_1": 0, "TIER_2": 0, "TIER_3": 0}
    never_matches: set = set()
    for p in paras:
        t = classify(p, default=Tier.PRIVATE, egress=True)
        tier_counts[Tier.NAMES.get(t, "TIER_1")] += 1
        never_matches.update(_jg.never_send_hits(p))
    summary = (f"{len(paras)} paragraph(s): {tier_counts['TIER_1']} public, "
               f"{tier_counts['TIER_2']} private, {tier_counts['TIER_3']} sensitive")
    if never_matches:
        summary += f"; {len(never_matches)} item(s) on your never-send list"
    return {
        "path": str(path), "extractable": True, "error": None,
        "tier_counts": tier_counts,
        "never_send_matches": sorted(never_matches),
        "paragraph_count": len(paras),
        "summary": summary,
    }


#: The largest file a grant reads to pin its content. Larger files are refused
#: with a plain message rather than read whole.
MAX_GRANT_BYTES = 50 * 1024 * 1024


def unsafe_path_reason(text: str) -> str | None:
    """Why a typed or requested path is refused, decided from the TEXT alone.

    Runs before anything touches the filesystem, so a path that would reach
    another machine (a UNC share), a raw device, a URL, or wherever the
    process happens to be running (a relative path) is never even opened.
    Returns None for a plain absolute local path."""
    t = str(text or "").strip()
    if not t:
        return "no path given"
    if "\x00" in t:
        return "that is not a path"
    norm = t.replace("/", "\\")
    if norm.startswith("\\\\"):
        if norm.startswith(("\\\\.\\", "\\\\?\\")):
            return "device paths are not allowed"
        return "network paths are not allowed; use a file on this computer"
    if "://" in t:
        return "web addresses are not allowed; use a file on this computer"
    import os
    expanded = os.path.expanduser(t)
    if not os.path.isabs(expanded):
        return "use the full path, starting from the drive or your home folder"
    drive = os.path.splitdrive(expanded)[0]
    if os.name == "nt" and len(drive) == 2 and drive[1] == ":":
        try:
            import ctypes
            DRIVE_REMOTE = 4
            if ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == DRIVE_REMOTE:
                return "network drives are not allowed; use a file on this computer"
        except Exception:
            pass
    return link_reason(expanded)


def _link_target(prefix: str) -> str | None:
    """Where a symlink or junction at `prefix` points, read WITHOUT following it
    (lstat and readlink look at the link itself, not at its target). None when
    `prefix` is not a link or cannot be read."""
    import os
    import stat as _stat
    try:
        st = os.lstat(prefix)
    except OSError:
        return None
    reparse = getattr(st, "st_file_attributes", 0) & 0x400   # FILE_ATTRIBUTE_REPARSE_POINT
    if not (_stat.S_ISLNK(st.st_mode) or reparse):
        return None
    try:
        return os.readlink(prefix)
    except (OSError, ValueError):
        return None


def _is_network_target(target: str) -> bool:
    t = str(target or "").replace("/", "\\")
    if t.upper().startswith("\\\\?\\UNC\\"):
        return True
    return t.startswith("\\\\") and not t.startswith(("\\\\?\\", "\\\\.\\"))


def link_reason(text: str) -> str | None:
    """Refuse a local-looking path that a link in its chain sends to the network.

    Walks the path from its root one component at a time and reads each link it
    meets without following it; a local link target is walked in turn (a
    bounded number of hops), a network one refuses the path."""
    import os
    todo = [os.path.normpath(os.path.expanduser(str(text or "")))]
    hops = 0
    while todo:
        path = todo.pop()
        drive, rest = os.path.splitdrive(path)
        parts = [x for x in rest.replace("/", "\\").split("\\") if x]
        prefix = drive + "\\" if drive else os.sep
        for i, part in enumerate(parts):
            prefix = os.path.join(prefix, part)
            target = _link_target(prefix)
            if target is None:
                continue
            if _is_network_target(target):
                return "that path leads to a network location; use a file on this computer"
            hops += 1
            if hops > 16:
                return "that path has too many links to check"
            t = target[4:] if target.startswith("\\\\?\\") else target
            if not os.path.isabs(t):
                t = os.path.join(os.path.dirname(prefix), t)
            todo.append(os.path.normpath(os.path.join(t, *parts[i + 1:])))
            break
    return None


def too_broad_folder_reason(p: Path) -> str | None:
    """A folder grant on a whole drive or the home folder itself is refused."""
    try:
        rp = p.resolve()
    except Exception:
        return "that folder could not be resolved"
    if rp.parent == rp:
        return "a whole drive is too broad; choose a folder inside it"
    try:
        if rp == Path.home().resolve():
            return "your whole home folder is too broad; choose a folder inside it"
    except Exception:
        pass
    return None


def create_file_grant(path: str, never_send_override: bool = False,
                       ack_never_send_matches: list | None = None) -> dict:
    """Create a content-pinned grant for one file.

    `never_send_override` may be True ONLY when the caller has already shown
    the user the specific never-send matches and recorded their acknowledgment
    in `ack_never_send_matches` — the endpoint's job, not this function's; this
    function just persists what was acknowledged so the ledger carries the
    consent record, and refuses to record an override with nothing behind it.
    """
    why = unsafe_path_reason(path)
    if why:
        raise UserFacingValueError(why)
    p = Path(path).expanduser().resolve()
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"{p} does not exist or is not a file")
    if p.stat().st_size > MAX_GRANT_BYTES:
        raise UserFacingValueError("that file is over %d MB, too large to allow"
                                   % (MAX_GRANT_BYTES // (1024 * 1024)))
    data = p.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    findings = scan_path(p)
    matches = findings.get("never_send_matches") or []
    override = bool(never_send_override) and bool(matches)
    event = {
        "event": "grant_file",
        "id": str(uuid.uuid4()),
        "type": "file",
        "path": str(p),
        "sha256": sha,
        "created_ts": time.time(),
        "never_send_override": override,
        "ack_never_send_matches": list(ack_never_send_matches or []) if override else [],
        "findings_summary": findings.get("summary"),
    }
    return _append_event(event)


def create_scope_grant(path_or_pattern: str, kind: str, expiry_days: float) -> dict:
    """Create a folder or glob grant. Expiry is REQUIRED and capped at 30 days
    — enforced here at the API, not merely suggested by the UI, because a
    folder/glob grant cannot be content-pinned and a permanent one is exactly
    the quiet gate-bypass this feature must not become."""
    if kind not in ("folder", "glob"):
        raise UserFacingValueError("kind must be 'folder' or 'glob'")
    if not expiry_days or expiry_days <= 0 or expiry_days > _MAX_EXPIRY_DAYS:
        raise UserFacingValueError(f"expiry_days is required and must be in (0, {_MAX_EXPIRY_DAYS}]")
    why = unsafe_path_reason(path_or_pattern)
    if why:
        raise UserFacingValueError(why)
    if kind == "folder":
        broad = too_broad_folder_reason(Path(path_or_pattern).expanduser())
        if broad:
            raise UserFacingValueError(broad)
        p = str(Path(path_or_pattern).expanduser().resolve())
    else:
        p = str(Path(path_or_pattern).expanduser())
    event = {
        "event": "grant_scope",
        "id": str(uuid.uuid4()),
        "type": kind,
        "path": p,
        "created_ts": time.time(),
        "expires_ts": time.time() + expiry_days * 86400.0,
        "never_send_override": False,   # override is file-grant only
    }
    return _append_event(event)


def create_deny_mark(path_or_pattern: str, kind: str) -> dict:
    if kind not in ("file", "folder", "glob"):
        raise UserFacingValueError("kind must be 'file', 'folder', or 'glob'")
    if kind == "glob":
        p = str(Path(path_or_pattern).expanduser())
    else:
        p = str(Path(path_or_pattern).expanduser().resolve())
    event = {"event": "deny", "id": str(uuid.uuid4()), "type": kind,
              "path": p, "created_ts": time.time()}
    return _append_event(event)


def revoke(target_id: str) -> dict:
    """Revoke a grant OR a deny mark by id — one action, either registry."""
    event = {"event": "revoke", "id": str(uuid.uuid4()),
              "target_id": target_id, "created_ts": time.time()}
    return _append_event(event)


# ── Read-time check + feeder ────────────────────────────────────────────────────

@dataclass
class GrantCheck:
    state: str                    # 'active' | 'stale' | 'denied' | 'none'
    grant_id: str | None = None
    deny_id: str | None = None
    never_send_override: bool = False


def check_grant(path: Path, sha256_hex: str | None = None) -> GrantCheck:
    """Deny beats any grant at any specificity, no exceptions — checked first,
    unconditionally, before any grant (file or scope) is even examined."""
    state = _load_state()
    for d in state.denies.values():
        if _covers(d, path):
            return GrantCheck(state="denied", deny_id=d["id"])
    for g in state.grants.values():
        if g.get("event") == "grant_file" and _same_path(g.get("path", ""), path):
            if sha256_hex is not None and g.get("sha256") != sha256_hex:
                return GrantCheck(state="stale", grant_id=g["id"])
            return GrantCheck(state="active", grant_id=g["id"],
                               never_send_override=bool(g.get("never_send_override")))
    now = time.time()
    for g in state.grants.values():
        if g.get("event") == "grant_scope" and _covers(g, path):
            expires = g.get("expires_ts")
            if expires and now > expires:
                continue
            return GrantCheck(state="active", grant_id=g["id"], never_send_override=False)
    return GrantCheck(state="none")


# A granted paragraph is page-sized prose (extract_text joins pages on
# "\n\n"), not a headline — register_public_text's 2000-char default is a
# NEWS constraint that would silently drop 3 of 4 pages of a real CV: the
# grant looks live (ledger entry, check_grant='active') while most of the
# document still gates normally.
_GRANT_SPAN_MAX_LEN = 50_000


def _register_grant_spans(text: str, grant_id: str, never_send_override: bool) -> None:
    from agent_friday.services import egress_gate as _eg
    origin = f"user-grant:{grant_id}"
    for p in _split_paragraphs(text):
        _eg.register_public_text(p, origin=origin, max_len=_GRANT_SPAN_MAX_LEN)
        if never_send_override:
            _eg.register_override_text(p, origin=origin, max_len=_GRANT_SPAN_MAX_LEN)


def _register_deny_spans(text: str) -> None:
    from agent_friday.services import judgment_gate as _jg
    for p in _split_paragraphs(text):
        _jg.register_deny_span(p)


def on_file_read(path: Path, text: str) -> GrantCheck:
    """The read-time feeder (the central move). Call this — and only this
    — after a file's content is actually extracted, before returning it to a
    tool caller. There is no other path into the grant span registries: a
    caller cannot hand the gate a flag, and a model cannot register spans by
    describing a file it never read.
    """
    try:
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        sha = None
    result = check_grant(path, sha256_hex=sha)
    if result.state == "denied":
        _register_deny_spans(text)
    elif result.state == "active":
        _register_grant_spans(text, result.grant_id, result.never_send_override)
    return result


# ── Listing / audit ─────────────────────────────────────────────────────────────

def list_grants() -> list[dict]:
    return list(_load_state().grants.values())


def list_denies() -> list[dict]:
    return list(_load_state().denies.values())


def status() -> dict:
    s = _load_state()
    return {"suspended": s.suspended, "dropped_lines": s.dropped,
            "held_lines": s.held,
            "grant_count": len(s.grants), "deny_count": len(s.denies)}


def list_pending_reapproval() -> list[dict]:
    """File grants whose content no longer matches the pinned hash — the
    chip re-raise case. Derived live from the actual files, not from a
    hand-maintained 'is this stale' flag."""
    state = _load_state()
    pending: list[dict] = []
    for g in state.grants.values():
        if g.get("event") != "grant_file":
            continue
        p = Path(g.get("path", ""))
        if not p.exists():
            pending.append({**g, "reason": "file_missing"})
            continue
        try:
            cur = hashlib.sha256(p.read_bytes()).hexdigest()
        except Exception:
            continue
        if cur != g.get("sha256"):
            pending.append({**g, "reason": "content_changed",
                             "current_sha256": cur, "fresh_findings": scan_path(p)})
    return pending

# ── Re-attestation after a key change ───────────────────────────────────────
#
# A line that fails to verify is never re-signed automatically. Doing so would
# turn a tampered line into a valid one, which defeats the reason for signing.
# Instead its CONTENT is listed for review and re-signed only when the user
# confirms it, as a NEW event that records the hash of what it replaced.

def list_unverified() -> list[dict]:
    """Every ledger line that does not verify, with its content, for review.

    `verified: False` on every row, so no caller can mistake one of these for a
    live grant. The `line_sha256` is the handle `reattest()` takes -- content
    addressed, so confirming one line can never re-sign a different one.
    """
    path = _ledger_path()
    if not path.exists():
        return []
    try:
        entries = _ledger_lines(path)
    except Exception:
        return [{"event": None, "verified": False, "line_sha256": "",
                 "signed_with": None, "why": "the ledger could not be read at all"}]
    _secret_bytes()
    out: list[dict] = []
    for line, b in entries:
        sha = hashlib.sha256(b).hexdigest()
        if line is None:
            out.append({"event": None, "verified": False, "line_sha256": sha,
                        "signed_with": None,
                        "why": "the line is not readable text"})
            continue
        try:
            rec = json.loads(line)
            ev = rec["event"]
            sig = rec["hmac"]
            if not isinstance(ev, dict) or not isinstance(sig, str):
                raise ValueError("malformed record")
        except Exception as e:
            # Said in words, not the parser's own text: this list is shown in
            # the browser for review.
            why = ("the line is not valid JSON" if isinstance(e, json.JSONDecodeError)
                   else "the line is missing its event or signature")
            out.append({"event": None, "verified": False, "line_sha256": sha,
                        "signed_with": None,
                        "why": "the line could not be parsed (%s)" % why})
            continue
        known, _ev = _known_key_of(line)
        if known == "current" or (known == "retired" and ev.get("event") == "deny"):
            continue
        out.append({
            "event": ev, "verified": False, "line_sha256": sha,
            "signed_with": known,
            "why": ("signed with a retired key" if known == "retired" else
                    "the signature matches no key this ledger has used -- the "
                    "line was altered, or signed with a key that is gone"),
        })
    return out


def _quarantine_path() -> Path:
    return _ledger_path().parent / "file_grants.quarantine.jsonl"


def _quarantine_line(line_sha256: str, why: str, who: str) -> bool:
    """Move one unverifiable line OUT of the active ledger, verbatim.

    Not deleted -- appended to `file_grants.quarantine.jsonl` exactly as it was,
    with a header recording who moved it and why. That keeps the evidence (a
    tampered line stays readable for inspection) while letting the active ledger
    verify cleanly again.

    Moving a line aside lifts suspenders mode ONLY when a known key (current or
    retired) vouches that the line is a grant: losing a grant only takes access
    away. Any other line -- unauthenticated, or an authentic revoke -- is
    recorded with `holds_suspension`, and grants stay suspended while that
    record exists. An unauthenticated line could have been a deny; what it
    claims to be is not evidence.
    """
    path = _ledger_path()
    # The read, the quarantine write and the rewrite all happen under the
    # append lock, so a grant appended meanwhile is never lost by the rewrite.
    with _APPEND_LOCK:
        if not path.exists():
            return False
        try:
            entries = _ledger_lines(path)
        except Exception:
            return False
        keep: list[bytes] = []
        moved, moved_b = None, None
        for line, b in entries:
            if hashlib.sha256(b).hexdigest() == line_sha256:
                moved, moved_b = line, b
                continue
            keep.append(b)
        if moved_b is None:
            return False
        known, ev = (_known_key_of(moved) if moved is not None else (None, None))
        vouched_grant = known is not None and (ev or {}).get("event") in _GRANT_EVENTS
        q = _quarantine_path()
        q.parent.mkdir(parents=True, exist_ok=True)
        with open(q, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({
                "quarantined_at": _iso_now(),
                "quarantined_by": who,
                "line_sha256": line_sha256,
                "why": why,
                "signed_with": known,
                "holds_suspension": not vouched_grant,
                "line": moved,
                "line_b64": (None if moved is not None else
                             __import__("base64").b64encode(moved_b).decode("ascii")),
            }, sort_keys=True, separators=(",", ":")) + "\n")
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_bytes((b"\n".join(keep) + b"\n") if keep else b"")
        tmp.replace(path)
    _invalidate_cache()
    return True


def reattest(line_sha256: str, *, confirmed_by: str) -> dict:
    """Re-sign one reviewed line, on an explicit confirmation.

    Two things happen under the one confirmation, in this order:
      1. a NEW event is written under the current key, carrying
         `reattested_from` (the hash of the line it replaces) and
         `reattested_by`;
      2. the original, unverifiable line is QUARANTINED -- moved verbatim to
         file_grants.quarantine.jsonl, never deleted and never re-signed.

    The original is never rewritten in place. Re-signing whatever failed would
    turn a tampered line into a valid one; quarantining it keeps the evidence
    and lets the active ledger verify again so grants can come back.
    """
    who = (confirmed_by or "").strip()
    if not who:
        return {"ok": False, "error": "a re-attestation needs an explicit "
                                      "confirmation from the user"}
    target = None
    for row in list_unverified():
        if row.get("line_sha256") == line_sha256 and isinstance(row.get("event"), dict):
            target = row
            break
    if target is None:
        return {"ok": False, "error": "no unverified line with that hash"}
    if target.get("signed_with") != "retired":
        # Re-signing an unauthenticated line would turn whatever was written
        # into it into a valid grant. Only a line a retired key vouches for is
        # known to say what it says.
        return {"ok": False,
                "error": "that line matches no key this ledger has used, so what it "
                         "says cannot be trusted and it will not be re-signed"}

    ev = dict(target["event"])
    et = ev.get("event")
    if et not in ("grant_file", "grant_scope", "deny"):
        return {"ok": False,
                "error": "only a grant or deny line can be re-attested (got %r)" % et}
    ev["id"] = "reattest-%s" % uuid.uuid4().hex[:12]
    ev["reattested_from"] = line_sha256
    ev["reattested_by"] = who
    ev["reattested_at"] = _iso_now()
    _append_event(ev)
    _quarantine_line(line_sha256, "re-attested by the user under the current "
                                  "signing key", who)
    _invalidate_cache()
    return {"ok": True, "id": ev["id"], "event": et,
            "quarantined": line_sha256}


def dismiss_unverified(line_sha256: str, *, confirmed_by: str) -> dict:
    """Quarantine an unverifiable line WITHOUT re-signing it.

    For a line the user does not recognise or does not want back. The grant it
    described simply does not return; the line is preserved for inspection.
    """
    who = (confirmed_by or "").strip()
    if not who:
        return {"ok": False, "error": "a dismissal needs an explicit "
                                      "confirmation from the user"}
    known = {r.get("line_sha256") for r in list_unverified()}
    if line_sha256 not in known:
        return {"ok": False, "error": "no unverified line with that hash"}
    ok = _quarantine_line(line_sha256, "dismissed by the user; not re-attested", who)
    return {"ok": bool(ok), "quarantined": line_sha256 if ok else None}


# ── Starting fresh ───────────────────────────────────────────────────────────
#
# The one way out of a suspension that an unauthenticated line holds. The whole
# ledger is set aside verbatim (copied and checked byte for byte; never
# deleted, never re-signed), the quarantine file with it, and a new ledger
# starts with no grants. Lines are decoded one by one, so every never-send mark
# that still verifies is carried over as its own signed line, unchanged, even
# beside a line that is not readable text: starting fresh must not un-deny
# anything. Called
# only from the approval hook of a "file_access_reset" card that the owner
# approved on screen (file_grant_requests.apply_reset).

FRESH_START_TEXT = ("The old permissions file, which has a line no current key "
                    "signed, is set aside unchanged; you start with no file "
                    "permissions and grant again. Your never-send marks are kept.")


def start_fresh(*, confirmed_by: str) -> dict:
    """Set the whole ledger aside and start a new one. See the section note.

    Order, so that a crash at any point leaves a ledger in place and nothing
    looser than before: the new ledger is written to a temp file, the old one is
    COPIED aside and checked byte for byte, then the temp file replaces the
    ledger in one step. The quarantine file is renamed aside last; a crash before
    that leaves its holds in force, which only keeps grants suspended."""
    import os
    import shutil
    if confirmed_by != "owner:ui":
        return {"ok": False, "error": "starting fresh is approved only on screen"}
    path = _ledger_path()
    q = _quarantine_path()
    stamp = "%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:6])
    aside = path.parent / ("file_grants.set-aside-%s.jsonl" % stamp)
    q_aside = path.parent / ("file_grants.quarantine.set-aside-%s.jsonl" % stamp)
    kept: list[bytes] = []
    with _APPEND_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        had_ledger = path.exists()
        if had_ledger:
            try:
                entries = _ledger_lines(path)
            except Exception:
                return {"ok": False, "error": "the permissions file could not be read, "
                                              "so nothing was changed"}
            lines, events = [], []
            for line, b in entries:
                if line is None:
                    continue   # unreadable: not carried, kept in the file set aside
                known, ev = _known_key_of(line)
                if known == "current" or (known == "retired" and (ev or {}).get("event") == "deny"):
                    lines.append((b, ev))
                    events.append(ev)
            _grants, denies = _fold(events)
            kept = [b for b, ev in lines
                    if ev.get("event") == "deny" and ev.get("id") in denies]
        tmp = path.with_suffix(".jsonl.fresh.tmp")
        tmp.write_bytes((b"\n".join(kept) + b"\n") if kept else b"")
        try:
            if had_ledger:
                original = path.read_bytes()
                shutil.copyfile(path, aside)
                if aside.read_bytes() != original:
                    tmp.unlink()
                    return {"ok": False, "error": "the old permissions file could not "
                                                  "be set aside intact, so nothing was "
                                                  "changed"}
            os.replace(tmp, path)
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
        if q.exists():
            q.replace(q_aside)
    _invalidate_cache()
    return {"ok": True, "set_aside": aside.name if had_ledger else None,
            "kept_denies": len(kept)}


# ── What the File access panel shows ─────────────────────────────────────────
#
# Rows for every grant the panel can act on, each with a plain status, and the
# open questions about lines that failed to verify. Everything read from the
# quarantine file or from an unverified line is DISPLAY ONLY: none of it is
# ever folded into a grant.

def list_quarantined() -> list[dict]:
    """Lines moved out of the ledger, newest last, as the panel shows them."""
    q = _quarantine_path()
    if not q.exists():
        return []
    out: list[dict] = []
    try:
        raw = q.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    for raw_line in raw.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            ev = (json.loads(rec.get("line") or "") or {}).get("event") or {}
        except Exception:
            continue
        if not isinstance(ev, dict):
            ev = {}
        out.append({
            "id": rec.get("line_sha256"), "status": "quarantined",
            "event": ev.get("event"), "type": ev.get("type"), "path": ev.get("path"),
            "created_ts": ev.get("created_ts"), "expires_ts": ev.get("expires_ts"),
            "why": rec.get("why"), "quarantined_at": rec.get("quarantined_at"),
        })
    return out


def notices() -> list[dict]:
    """The open questions about lines that failed to verify.

    reason "retired_key": a grant line a retired key vouched for, already moved
    aside (in_ledger False). Its path is known, so it can be re-granted through
    a new approval card.

    reason "unverified": a line no known key vouches for (in_ledger True). It
    keeps every file permission paused. Its content is not trusted -- not its
    path, not even what kind of line it says it is -- so it offers no re-grant,
    and hiding the notice changes nothing else: the pause stays.
    """
    d = _read_notices()["notices"]
    out: list[dict] = []
    for sha, n in d.items():
        if n.get("status") != "open":
            continue
        ev = {"created_ts": n.get("created_ts")}
        out.append({"id": sha, "reason": n.get("reason") or "retired_key",
                    "in_ledger": False, "path": n.get("path"), "type": n.get("type"),
                    "created_ts": n.get("created_ts"),
                    "expires_ts": n.get("expires_ts"),
                    "message": _retired_message(ev),
                    "resolves": ("Re-grant puts a card on screen for this path; "
                                 "approving it there makes a fresh permission. "
                                 "Leave it off keeps it set aside.")})
    for row in list_unverified():
        sha = row.get("line_sha256")
        if sha in d:
            continue
        out.append({"id": sha, "reason": "unverified", "in_ledger": True,
                    "path": None, "type": None, "created_ts": None,
                    "message": ("A line in the file-permissions record could not be "
                                "verified. Every file permission is paused while it is "
                                "there, because it may have been a never-send mark."),
                    "resolves": ("Hiding this notice changes nothing else: the line "
                                 "stays and file permissions stay paused.")})
    return out


def find_notice(notice_id: str) -> dict | None:
    for n in notices():
        if n.get("id") == notice_id:
            return n
    return None


def resolve_notice(notice_id: str, outcome: str, *, confirmed_by: str) -> dict:
    """Close one notice: 'regranted' or 'dismissed'. Never re-signs anything,
    never moves a line, and never lifts a suspension.

    Only a retired-key notice (its line already set aside, its path vouched for)
    can be 'regranted'. Dismissing an unverified notice only hides it."""
    if outcome not in ("regranted", "dismissed"):
        return {"ok": False, "error": "outcome must be regranted or dismissed"}
    n = find_notice(notice_id)
    if n is None:
        return {"ok": False, "error": "no open notice with that id"}
    if outcome == "regranted" and n.get("reason") != "retired_key":
        return {"ok": False, "error": "only a permission a known key vouches for "
                                      "can be re-granted"}
    with _NOTICE_LOCK:
        d = _read_notices()
        rec = d["notices"].get(notice_id) or {
            "reason": n.get("reason"), "path": n.get("path"), "type": n.get("type"),
            "created_ts": n.get("created_ts"), "expires_ts": n.get("expires_ts")}
        rec.update(status=outcome, closed_at=_iso_now(), closed_by=confirmed_by)
        d["notices"][notice_id] = rec
        d["alarmed"].setdefault(notice_id, _iso_now())
        _write_notices(d)
    _invalidate_cache()
    return {"ok": True, "id": notice_id, "status": outcome}


def access_rows() -> list[dict]:
    """Every row the File access panel lists, each with a plain status.

    valid: a grant in force. changed / missing: a file grant whose file no
    longer matches what was granted (it gates normally). expired: a folder or
    pattern grant past its expiry. paused: grants exist but the ledger is
    suspended. quarantined / unverified: a line that grants nothing.
    """
    state = _load_state()
    stale = {g.get("id"): g.get("reason") for g in list_pending_reapproval()}
    now = time.time()
    rows: list[dict] = []
    for g in list(state.grants.values()) + list(state.paused_grants.values()):
        status = "valid" if not state.suspended else "paused"
        if g.get("id") in stale:
            status = "missing" if stale[g["id"]] == "file_missing" else "changed"
        elif g.get("expires_ts") and now > g["expires_ts"]:
            status = "expired"
        rows.append({"id": g.get("id"), "type": g.get("type"), "path": g.get("path"),
                     "status": status, "created_ts": g.get("created_ts"),
                     "expires_ts": g.get("expires_ts"),
                     "summary": g.get("findings_summary")})
    if state.suspended:
        for row in list_unverified():
            ev = row.get("event") if isinstance(row.get("event"), dict) else {}
            rows.append({"id": row.get("line_sha256"), "type": ev.get("type"),
                         "path": ev.get("path"), "status": "unverified",
                         "created_ts": ev.get("created_ts"),
                         "expires_ts": ev.get("expires_ts"), "why": row.get("why")})
    rows.extend(list_quarantined())
    return rows
