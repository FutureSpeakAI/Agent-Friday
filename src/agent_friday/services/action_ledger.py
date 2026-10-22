"""The action ledger: one append-only, hash-chained, signed record of what
Friday did.

Before this module, each part of Friday kept its own receipt file: the action
gate's decision log (decision-bom.jsonl), the organize receipts with their undo
data (actions/receipts.jsonl), the activity feed (activity_ledger.jsonl), goal
receipts, dissent events, the pre-approved-send log, the website-trust history,
the inbox-cleanup receipts and the media-diet receipts. Some were signed line by
line, one was chained, most were neither, and none joined the others (north
star §18.9, §36.9-2, §37.2-5).

They are now kinds of entry in this one ledger. Each module keeps its own
public functions; what it writes goes here, and what it reads comes from here.

The file
--------
``<home>/ledger/actions.jsonl``, one JSON object per line::

    {"v":1, "seq":n, "ts":"...Z", "t":<unix>, "kind":"decision",
     "principal":"owner", "rec":{...}, "kid":"<key id>",
     "prev":"<hash of line n-1>", "hash":"<sha256 of this line's body>",
     "sig":"<HMAC-SHA256 of hash>"}

``hash`` is SHA-256 over the canonical JSON of every field except ``hash`` and
``sig``; ``prev`` is the previous line's ``hash`` (64 zeros for the first);
``sig`` is an HMAC of ``hash`` with a key derived from the governance key
(``HMAC(governance_key, "agent-friday/action-ledger/v1")``), so this ledger's
key signs nothing else. Changing any byte of a line, removing a line, or
reordering lines breaks the chain at that line; ``verify()`` names it.

When the live file passes ``SEGMENT_MAX`` bytes it is sealed into
``ledger/segments/actions-NNNNNN.jsonl`` (never over an existing one) and the
chain continues in a new live file. Nothing is ever deleted (an append-only
store, LESSONS L34).

Records cut off the end
-----------------------
A hash chain alone cannot see its own end being cut off. Three things do:

* ``ledger/head.json``, the newest seq and hash, signed. An append that finds
  the head ahead of the file first writes a ``truncation_detected`` entry.
* The anchor: the newest seq and hash of a durable entry (a decision, a
  revocation, an acknowledgement), kept OUTSIDE the data folder, in the OS
  keychain (service ``agent-friday``, one account per data folder). A ledger
  behind its anchor, or missing while an anchor exists, is a break; a copy of
  the folder from earlier cannot pass. Without a keychain backend the anchor
  falls back to the vault folder (weaker; ``summary()`` says so).
* What this process itself wrote: a ledger that goes below the highest seq
  this process saw is a break.

What a break does
-----------------
``hold_reason()`` returns a sentence while the ledger has a break the owner has
not acknowledged, while it cannot be checked (no key, no file access), or while
an old receipt file is still waiting to be brought in. The action gate holds
every outward action on it, with that sentence. The owner reviews the breaks in
Activity and acknowledges exactly the ones shown, with a press on this PC; the
acknowledgement is itself a signed entry naming each one (its seq, the SHA-256
of the line as found, and the reason). A later change to that line, or a new
break, holds again. A break found between the review and the press refuses the
acknowledgement ("review again").

The old receipt files
---------------------
Brought in once (``migrate_legacy``), then a signed ``migration_complete``
entry. After that, an old receipt file that appears again is NOT read: it is
moved whole to ``ledger/quarantine/`` and a ``legacy_reappeared`` entry is a
break the owner must look at. Planting an old-format file can never put
"history" into the ledger.

Two years live, then archived (amendment A22)
-------------------------------------------
Entries older than ``ledger_archive_after_days`` (Settings; 730 by default; 0
turns archiving off) leave the live files that Activity and the Ledger read day
to day. Whole sealed segments only: the live file is sealed when a new
calendar month begins, so a segment holds at most one month and an entry stays
live at most about a month past the period, never less. An archived segment is
encrypted (AES-256-GCM, a key derived from the ledger key) into
``ledger/archive/actions-NNNNNN.jsonl.enc`` and listed in a signed
``ledger/archive/manifest.json`` (first and last seq, the link into it, its last
hash, record count, SHA-256 of the plain and the encrypted file). Nothing is
deleted: every Friday Bundle and backup carries the archive. ``verify()``
starts the chain from the manifest's last hash, so a missing, edited or
substituted archive file, an edited manifest, or a live ledger cut at the
boundary is a break like any other; a forced check decrypts every archived
file and walks its chain. ``entries(archive=True)`` (and every strict reader,
such as revocations) reads the archive too.

Tests: with ``FRIDAY_TESTING=1`` the directory may be moved with
``FRIDAY_LEDGER_DIR`` (the root conftest gives every test its own) and the
anchor lives in this process's memory. The real app ignores both.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional

VERSION = 1
GENESIS = "0" * 64
SEGMENT_MAX = 32 * 1024 * 1024
LIVE_NAME = "actions.jsonl"
HEAD_NAME = "head.json"
SEGMENTS_DIR = "segments"
LOCK_NAME = ".lock"
PRINCIPAL = "owner"          # A11: one principal now; the field is here for later

#: How long a full re-verification result stays good before the hold check
#: re-reads every line again.
FULL_RECHECK_S = 600.0
#: How long an ordinary entry may go without moving the anchor. Durable
#: entries (decisions, revocations, acknowledgements) move it at once.
ANCHOR_EVERY_S = 30.0
#: Problems listed in one report (the count says how many there are).
MAX_PROBLEMS = 50
#: Entries that are themselves a break the owner must look at.
FLAG_KINDS = ("truncation_detected", "torn_tail", "legacy_reappeared")
#: The archive (amendment A22).
ARCHIVE_DIR = "archive"
ARCHIVE_MANIFEST = "manifest.json"
ARCHIVE_SETTING = "ledger_archive_after_days"
DEFAULT_ARCHIVE_AFTER_DAYS = 730
MIN_ARCHIVE_AFTER_DAYS = 30
_ARCHIVE_INFO = b"agent-friday/action-ledger/archive/v1"
_ARCHIVE_MAGIC = b"FRIDAYLEDGERARCHIVE\x01"

ANCHOR_SERVICE = "agent-friday"
_KEY_INFO = b"agent-friday/action-ledger/v1"

_TLOCK = threading.RLock()
_CACHE_LOCK = threading.Lock()
_TEST_ANCHORS: dict[str, str] = {}
_STATE: dict[str, Any] = {}


def _fresh_state(d: Optional[str]) -> dict:
    return {"dir": d, "tip": None, "verified": None, "full_at": 0.0,
            "migrated": False, "seg_cache": {}, "max_seen": 0,
            "anchor_at": 0.0, "anchor_seq": None, "live_first": None,
            "arch_rows": {}, "arch_cache": {}}


_STATE.update(_fresh_state(None))


class LedgerError(RuntimeError):
    """The ledger could not be written or read (no key, no disk, lock timeout)."""


# ── Paths ───────────────────────────────────────────────────────────────────

def _testing() -> bool:
    return os.environ.get("FRIDAY_TESTING") == "1"


def _fsync_durable() -> bool:
    """A durable append is flushed to the disk (fsync) outside the tests.
    Under FRIDAY_TESTING=1 it is not, unless FRIDAY_LEDGER_FSYNC=1: every gate
    decision is durable, and an fsync per decision doubled the Windows suite
    time on CI. The tests check order, chain and anchor, not power loss."""
    if not _testing():
        return True
    return os.environ.get("FRIDAY_LEDGER_FSYNC") == "1"


def ledger_dir() -> Path:
    if _testing():
        override = os.environ.get("FRIDAY_LEDGER_DIR", "").strip()
        if override:
            return Path(override)
    from agent_friday.paths import friday_home
    return Path(friday_home()) / "ledger"


def live_path() -> Path:
    return ledger_dir() / LIVE_NAME


def head_path() -> Path:
    return ledger_dir() / HEAD_NAME


def _segment_index(p: Path) -> int:
    try:
        return int(p.stem.split("-")[-1])
    except ValueError:
        return -1


def segments() -> list[Path]:
    """Sealed segments, oldest first, then the live file (when present).
    One directory read each (os.scandir), not a stat per name: this runs on
    every append and every hold check, and a stat is slow on Windows."""
    d = ledger_dir()
    out = []
    try:
        with os.scandir(d / SEGMENTS_DIR) as it:
            names = [e.name for e in it if e.name.startswith("actions-") and e.name.endswith(".jsonl")]
        out = sorted((d / SEGMENTS_DIR / n for n in names), key=_segment_index)
    except OSError:
        pass
    try:
        with os.scandir(d) as it:
            if any(e.name == LIVE_NAME for e in it):
                out.append(d / LIVE_NAME)
    except OSError:
        pass
    return out


def _seg_label(p: Path) -> str:
    return p.name if p.name == LIVE_NAME else f"{SEGMENTS_DIR}/{p.name}"


def _check_dir() -> Path:
    """Reset the in-process caches when the directory changed (tests, restore)."""
    d = ledger_dir()
    if _STATE.get("dir") != str(d):
        _STATE.clear()
        _STATE.update(_fresh_state(str(d)))
    return d


def reset_caches() -> None:
    with _TLOCK:
        _STATE.clear()
        _STATE.update(_fresh_state(None))


# ── Keys and hashing ────────────────────────────────────────────────────────

def _key() -> bytes:
    from agent_friday.governance.proof_of_integrity import get_governance_key
    gk = get_governance_key()
    if not gk:
        raise LedgerError("the governance key is not available")
    return _hmac.new(gk, _KEY_INFO, hashlib.sha256).digest()


def key_id(key: bytes) -> str:
    return hashlib.sha256(b"kid:" + key).hexdigest()[:16]


def canon(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("ascii")


def _normalise(rec: Any) -> Any:
    """The record as JSON will give it back, so the hash is over what is stored."""
    return json.loads(json.dumps(rec, default=str))


def body_hash(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k not in ("hash", "sig")}
    return hashlib.sha256(canon(body)).hexdigest()


def _sign(key: bytes, h: str) -> str:
    return _hmac.new(key, h.encode("ascii"), hashlib.sha256).hexdigest()


def _head_sig(key: bytes, seq: int, h: str) -> str:
    return _hmac.new(key, f"head:{seq}:{h}".encode("ascii"), hashlib.sha256).hexdigest()


def entry_ok(e: dict, key: Optional[bytes]) -> bool:
    """This one line is as the ledger wrote it: its hash matches its body and
    its signature matches this device's key. (Its place in the chain is
    verify()'s to judge.)"""
    if not isinstance(e, dict) or key is None:
        return False
    try:
        h = e.get("hash")
        return (isinstance(h, str) and body_hash(e) == h and e.get("kid") == key_id(key)
                and _hmac.compare_digest(_sign(key, h), str(e.get("sig"))))
    except Exception:
        return False


# ── The anchor outside the data folder ──────────────────────────────────────

def _anchor_account() -> str:
    try:
        where = str(ledger_dir().resolve())
    except OSError:
        where = str(ledger_dir())
    return "action-ledger:" + hashlib.sha256(where.encode("utf-8")).hexdigest()[:16]


def _anchor_file() -> Path:
    from agent_friday.paths import friday_home
    return Path(friday_home()) / "vault" / ".action-ledger-anchor"


def anchor_get() -> tuple[Optional[tuple[int, str]], str]:
    """((seq, hash) or None, where it lives: keychain | file | memory | none).
    Raises when an anchor exists but cannot be read (the caller holds)."""
    acct = _anchor_account()
    if _testing() and os.environ.get("FRIDAY_LEDGER_ANCHOR") != "keyring":
        raw = _TEST_ANCHORS.get(acct)
        return (_parse_anchor(raw) if raw else None), "memory"
    try:
        import keyring
        from keyring.errors import NoKeyringError
    except ImportError:
        keyring, NoKeyringError = None, ()
    if keyring is not None:
        try:
            raw = keyring.get_password(ANCHOR_SERVICE, acct)
            return (_parse_anchor(raw) if raw else None), "keychain"
        except NoKeyringError:
            pass
    p = _anchor_file()
    if p.exists():
        from agent_friday.services import credential_store
        return _parse_anchor(credential_store.read_secret(p).decode("ascii")), "file"
    return None, "none"


def _parse_anchor(raw: str) -> tuple[int, str]:
    d = json.loads(raw)
    return int(d["seq"]), str(d["hash"])


def _anchor_set(seq: int, h: str) -> str:
    raw = json.dumps({"seq": int(seq), "hash": h})
    acct = _anchor_account()
    if _testing() and os.environ.get("FRIDAY_LEDGER_ANCHOR") != "keyring":
        _TEST_ANCHORS[acct] = raw
        return "memory"
    try:
        import keyring
        from keyring.errors import NoKeyringError
    except ImportError:
        keyring, NoKeyringError = None, ()
    if keyring is not None:
        try:
            keyring.set_password(ANCHOR_SERVICE, acct, raw)
            return "keychain"
        except NoKeyringError:
            pass
    from agent_friday.services import credential_store
    p = _anchor_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    credential_store.write_secret(p, raw.encode("ascii"))
    return "file"


def _move_anchor_locked(seq: int, h: str, *, force: bool = False) -> None:
    """Move the anchor forward to (seq, h). Never backwards unless `force`
    (the owner acknowledged the loss, or a restore). Best effort: a failure
    leaves the old anchor, which can only make a later check stricter.

    The first move in a process reads the stored anchor and moves it only when
    the chain on disk still holds the anchored record (same seq, same hash):
    a ledger deleted or put back from an earlier copy keeps the old anchor, so
    verify() reports the break instead of the first new entry covering it."""
    cur = _STATE.get("anchor_seq")
    if not force and cur is None:
        try:
            stored, _where = anchor_get()
        except Exception:
            return                              # unreadable: leave it; verify() holds
        if stored is not None:
            aseq, ahash = stored
            if aseq > 0 and (seq < aseq or _hash_at(aseq) != ahash):
                return                          # the chain lost the anchored record
            _STATE["anchor_seq"] = cur = aseq
    if not force and cur is not None and seq <= cur:
        return
    try:
        _anchor_set(seq, h)
        _STATE["anchor_seq"] = seq
        _STATE["anchor_at"] = time.time()
    except Exception:
        pass


def reset_anchor_to_tip() -> None:
    """After a restore (the restore is the owner's own act): the anchor names
    the restored ledger's newest entry (an empty one needs none: the first
    durable entry sets it)."""
    with _TLOCK:
        _check_dir()
        with _lock():
            seq, h = _read_tip_locked()
            if seq > 0:
                _move_anchor_locked(seq, h, force=True)
            else:
                try:
                    _anchor_set(0, GENESIS)
                    _STATE["anchor_seq"] = 0
                except Exception:
                    pass
            _STATE["max_seen"] = seq


# ── Cross-process lock ──────────────────────────────────────────────────────

class _FileLock:
    """An exclusive lock on ledger/.lock shared by every Friday process."""

    def __init__(self, path: Path, timeout: float = 15.0):
        self.path, self.timeout, self.fh = path, timeout, None

    def _try(self) -> bool:
        try:
            if os.name == "nt":
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+b")
        deadline = time.monotonic() + self.timeout
        while not self._try():
            if time.monotonic() > deadline:
                self.fh.close()
                self.fh = None
                raise LedgerError("the ledger is locked by another process")
            time.sleep(0.02)
        return self

    def __exit__(self, *exc):
        try:
            if os.name == "nt":
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            self.fh.close()
            self.fh = None


def _lock() -> _FileLock:
    return _FileLock(ledger_dir() / LOCK_NAME)


_HELD_HERE = threading.local()


def _someone_else_writing() -> bool:
    """True while another process holds the write lock (it may be mid-line)."""
    if getattr(_HELD_HERE, "n", 0):
        return False
    p = ledger_dir() / LOCK_NAME
    if not p.exists():
        return False
    lk = _FileLock(p, timeout=0)
    try:
        lk.fh = open(p, "a+b")
    except OSError:
        return False
    try:
        if lk._try():
            lk.__exit__()
            return False
        lk.fh.close()
        return True
    except Exception:
        try:
            lk.fh.close()
        except Exception:
            pass
        return False


# ── Reading lines ───────────────────────────────────────────────────────────

def _ends_cleanly(path: Path) -> bool:
    try:
        size = path.stat().st_size
        if size == 0:
            return True
        with open(path, "rb") as f:
            f.seek(size - 1)
            return f.read(1) == b"\n"
    except OSError:
        return True


def _iter_lines(path: Path) -> Iterator[tuple[Optional[dict], bytes, int, bool]]:
    """(entry or None when unreadable, raw bytes, 1-based line number,
    terminated). Raises OSError when the file cannot be opened."""
    with open(path, "rb") as fh:
        n = 0
        for raw in fh:
            n += 1
            terminated = raw.endswith(b"\n")
            if terminated:
                raw = raw[:-1]
            if raw.endswith(b"\r"):
                raw = raw[:-1]
            if not raw.strip():
                continue
            if not terminated:
                yield None, raw, n, False
                continue
            try:
                e = json.loads(raw)
                yield (e if isinstance(e, dict) else None), raw, n, True
            except ValueError:
                yield None, raw, n, True


def _reverse_lines(path: Path) -> Iterator[bytes]:
    size = path.stat().st_size
    with open(path, "rb") as f:
        pos, rest = size, b""
        while pos > 0:
            step = min(262144, pos)
            pos -= step
            f.seek(pos)
            buf = f.read(step) + rest
            parts = buf.split(b"\n")
            rest = parts[0]
            for line in reversed(parts[1:]):
                if line.strip():
                    yield line.rstrip(b"\r")
        if rest.strip():
            yield rest.rstrip(b"\r")


def _safe_json(raw: bytes):
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _read_tip_locked() -> tuple[int, str]:
    """The newest (seq, hash) on disk: the last line that reads as an entry."""
    for p in reversed(_reader_segments()):
        skip_first = not _ends_cleanly(p)
        try:
            for raw in _reverse_lines(p):
                if skip_first:
                    skip_first = False
                    continue
                e = _safe_json(raw)
                if isinstance(e, dict) and isinstance(e.get("seq"), int) and isinstance(e.get("hash"), str):
                    return e["seq"], e["hash"]
        except OSError:
            continue
    tail = _manifest_tail(_read_manifest())
    if tail:
        return tail
    return 0, GENESIS


# ── Writing ─────────────────────────────────────────────────────────────────

def _now() -> tuple[str, float]:
    t = time.time()
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), t


def _replace(src: Path, dst: Path) -> None:
    for i in range(20):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            time.sleep(0.01 * (i + 1))
    os.replace(src, dst)


def _move_no_overwrite(src: Path, dst: Path) -> None:
    """Rename src to dst, refusing (FileExistsError) when dst exists."""
    if dst.exists():
        raise FileExistsError(str(dst))
    if os.name == "nt":
        for i in range(20):
            try:
                os.rename(src, dst)          # never replaces on Windows
                return
            except PermissionError:
                time.sleep(0.01 * (i + 1))
        os.rename(src, dst)
    else:
        os.link(src, dst)                    # fails if dst exists
        os.unlink(src)


def _read_head() -> Optional[dict]:
    try:
        h = json.loads(head_path().read_text(encoding="utf-8"))
        return h if isinstance(h, dict) else None
    except (OSError, ValueError):
        return None


def _write_head(key: bytes, seq: int, h: str) -> None:
    p = head_path()
    tmp = p.with_name(p.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps({"seq": seq, "hash": h, "kid": key_id(key),
                                   "sig": _head_sig(key, seq, h)}), encoding="utf-8")
        _replace(tmp, p)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def _repair_torn_tail_locked(live: Path) -> Optional[dict]:
    """A last line without its newline is a write that never finished. Its
    bytes are kept under ledger/torn/ and the line is taken off the live file,
    so the next entry starts on a line of its own. Returns what was kept."""
    try:
        size = live.stat().st_size
    except OSError:
        return None
    if size == 0:
        return None
    with open(live, "r+b") as f:
        f.seek(size - 1)
        if f.read(1) == b"\n":
            return None
        pos, cut = size, 0
        while pos > 0:
            step = min(65536, pos)
            pos -= step
            f.seek(pos)
            chunk = f.read(step)
            i = chunk.rfind(b"\n")
            if i >= 0:
                cut = pos + i + 1
                break
        f.seek(cut)
        torn = f.read()
        tdir = ledger_dir() / "torn"
        tdir.mkdir(parents=True, exist_ok=True)
        name = f"torn-{int(time.time() * 1000)}-{os.getpid()}.bin"
        (tdir / name).write_bytes(torn)
        f.truncate(cut)
    return {"bytes": len(torn), "kept_as": f"torn/{name}",
            "sha256": hashlib.sha256(torn).hexdigest()}


def _seal_live_locked(d: Path, live: Path) -> None:
    sd = d / SEGMENTS_DIR
    sd.mkdir(parents=True, exist_ok=True)
    ad = d / ARCHIVE_DIR
    used = [_segment_index(p) for p in sd.glob("actions-*.jsonl")]
    if ad.is_dir():
        used += [_segment_index(Path(p.name[:-len(".enc")])) for p in ad.glob("actions-*.jsonl.enc")]
    used += [_segment_index(Path(n)) for n in _covered_names()]
    n = max(used + [0]) + 1
    for _ in range(1000):
        dst = sd / f"actions-{n:06d}.jsonl"
        try:
            _move_no_overwrite(live, dst)
            return
        except FileExistsError:
            n += 1


def _append_locked(key: bytes, items: list, durable: bool) -> dict:
    """Write each (kind, rec) of `items` in one open of the live file. The
    caller holds both locks."""
    d = ledger_dir()
    d.mkdir(parents=True, exist_ok=True)
    live = d / LIVE_NAME
    tip = _STATE.get("tip")
    try:
        st = live.stat()
        stat_now = (st.st_size, st.st_mtime_ns)
    except OSError:
        stat_now = (0, 0)
    pending: list = []
    if tip is None or (tip[2], tip[3]) != stat_now:
        torn = _repair_torn_tail_locked(live) if stat_now[0] else None
        seq, prev = _read_tip_locked()
        if torn:
            pending.append(("torn_tail", torn))
        head = _read_head()
        if head and isinstance(head.get("seq"), int) and head["seq"] > seq:
            pending.append(("truncation_detected",
                            {"head_seq": head["seq"], "head_hash": head.get("hash"),
                             "found_seq": seq, "missing": head["seq"] - seq}))
    else:
        seq, prev = tip[0], tip[1]
    try:
        if live.exists() and (live.stat().st_size >= SEGMENT_MAX or _new_month_locked(live)):
            _seal_live_locked(d, live)
            _STATE["live_first"] = None
    except OSError:
        pass                                   # sealed next time
    # the file stats a cached verify result covers: only worth reading when
    # there is such a result to keep current
    pre_stats = _stats() if _STATE.get("verified") else None
    out = None
    written = 0
    with open(live, "ab") as f:
        for k, r in pending + list(items):
            ts, t = _now()
            seq += 1
            entry = {"v": VERSION, "seq": seq, "ts": ts, "t": t, "kind": k,
                     "principal": PRINCIPAL, "rec": _normalise(r),
                     "kid": key_id(key), "prev": prev}
            entry["hash"] = body_hash(entry)
            entry["sig"] = _sign(key, entry["hash"])
            f.write(canon(entry) + b"\n")
            prev = entry["hash"]
            out = entry
            written += 1
        f.flush()
        if durable and _fsync_durable():
            os.fsync(f.fileno())
    st = live.stat()
    _STATE["tip"] = (seq, prev, st.st_size, st.st_mtime_ns)
    _STATE["max_seen"] = max(_STATE.get("max_seen") or 0, seq)
    _write_head(key, seq, prev)
    if durable or time.time() - (_STATE.get("anchor_at") or 0.0) > ANCHOR_EVERY_S:
        _move_anchor_locked(seq, prev)
    # Keep a verified result current across this process's own appends, so
    # the hold check does not re-read the whole ledger for each action. Only
    # when nothing else touched the files since that result, and only for
    # ordinary entries: a break flag or an acknowledgement changes the verdict.
    with _CACHE_LOCK:
        cached = _STATE.get("verified")
        kinds = {k for k, _r in items}
        if (cached and pre_stats is not None and cached[0] == pre_stats and not pending
                and cached[1].get("ok")
                and not kinds & ({"integrity_ack", "migration_complete"} | set(FLAG_KINDS))):
            rep = dict(cached[1])
            rep.update(records=rep.get("records", 0) + written, last_seq=seq, tip=prev)
            _STATE["verified"] = (_stats(), rep)
        else:
            _STATE["verified"] = None
    return out


def _guarded_append(key: bytes, items: list, durable: bool) -> dict:
    with _lock():
        _HELD_HERE.n = getattr(_HELD_HERE, "n", 0) + 1
        try:
            return _append_locked(key, items, durable)
        finally:
            _HELD_HERE.n -= 1


def append(kind: str, rec: dict, *, durable: bool = False) -> dict:
    """Append one entry and return it. Raises LedgerError (or OSError) when it
    cannot be written, so a caller that must not act without a receipt can
    hold. Callers whose record is best-effort catch the error."""
    if not kind or not isinstance(rec, dict):
        raise LedgerError("an entry needs a kind and a record")
    key = _key()
    with _TLOCK:
        _check_dir()
        _ensure_migrated()
        return _guarded_append(key, [(kind, rec)], durable)


def try_append(kind: str, rec: dict, **kw) -> Optional[dict]:
    """append(), never raising: for records that must not break a turn."""
    try:
        return append(kind, rec, **kw)
    except Exception:
        return None


# ── Reading entries ─────────────────────────────────────────────────────────

def entries(kinds: Optional[Iterable[str]] = None, *, newest_first: bool = True,
            limit: Optional[int] = None,
            where: Optional[Callable[[dict], bool]] = None,
            verified: bool = False, strict: bool = False,
            archive: Optional[bool] = None) -> list[dict]:
    """Entries (whole lines), optionally filtered by kind and a predicate.

    `verified`: only lines whose hash and signature hold under this device's
    key (for readers that act on what they read, such as Undo).
    `strict`: raise LedgerError instead of returning what could be read (for
    readers that must fail closed, such as revoked grants). Otherwise never
    raises, and an unreadable line is skipped (verify() reports it).
    `archive`: read the encrypted archive (A22) as well; by default only a
    strict reader does (a revocation two years old still counts)."""
    want = set(kinds) if kinds else None
    if archive is None:
        archive = strict
    key = None
    if verified or archive:
        try:
            key = _key()
        except Exception:
            if strict:
                raise LedgerError("the ledger key is not available")
            if verified:
                return []
            archive = False
    with _TLOCK:
        _check_dir()
        _ensure_migrated()
    for attempt in range(2):
        out: list[dict] = []
        try:
            segs = _reader_segments()
            sources: list = list(segs)
            if archive and key is not None:
                try:
                    with _TLOCK:
                        _check_dir()
                        arch = _archived_entries(key, want)
                except Exception as e:  # noqa: BLE001
                    if strict:
                        raise LedgerError("the action ledger's archive could not be read (%s)" % e)
                    arch = []
                sources = list(arch) + sources
            order = list(reversed(sources)) if newest_first else sources
            for p in order:
                if isinstance(p, list):
                    it = iter(reversed(p) if newest_first else p)
                else:
                    it = ((_safe_json(raw) for raw in _reverse_lines(p)) if newest_first
                          else (e for e, _r, _n, _t in _iter_lines(p)))
                for e in it:
                    if not isinstance(e, dict):
                        continue
                    if want is not None and e.get("kind") not in want:
                        continue
                    if verified and not entry_ok(e, key):
                        continue
                    if where is not None:
                        try:
                            if not where(e):
                                continue
                        except Exception:
                            continue
                    out.append(e)
                    if limit is not None and len(out) >= limit:
                        return out
            return out
        except FileNotFoundError:
            if attempt == 0:
                continue                      # sealed while reading: list again
            if strict:
                raise LedgerError("a ledger segment vanished while it was read")
            return out
        except Exception as e:  # noqa: BLE001
            if strict:
                raise LedgerError("the ledger could not be read (%s)" % type(e).__name__)
            return out
    return []


def records(kind: str, *, newest_first: bool = False, limit: Optional[int] = None) -> list[dict]:
    """The `rec` of every entry of one kind, each with `_seq` and `_ts` added."""
    out = []
    for e in entries([kind], newest_first=newest_first, limit=limit):
        r = e.get("rec")
        if isinstance(r, dict):
            out.append({**r, "_seq": e.get("seq"), "_ts": e.get("ts")})
    return out


# ── The archive (amendment A22) ─────────────────────────────────────────────

def archive_dir() -> Path:
    return ledger_dir() / ARCHIVE_DIR


def archive_after_days() -> int:
    """The retention period of the live record, in days (0: archiving off).
    Settings `ledger_archive_after_days`; anything unreadable is the default."""
    try:
        from agent_friday.core import _load_settings
        v = (_load_settings() or {}).get(ARCHIVE_SETTING, DEFAULT_ARCHIVE_AFTER_DAYS)
    except Exception:
        return DEFAULT_ARCHIVE_AFTER_DAYS
    return normalise_archive_days(v)


def normalise_archive_days(v: Any) -> int:
    """0 (off) or a whole number of days, at least MIN_ARCHIVE_AFTER_DAYS.
    Anything else is the default."""
    if isinstance(v, bool):
        return DEFAULT_ARCHIVE_AFTER_DAYS
    try:
        n = int(v)
    except (TypeError, ValueError):
        return DEFAULT_ARCHIVE_AFTER_DAYS
    if n == 0:
        return 0
    if n < MIN_ARCHIVE_AFTER_DAYS:
        return MIN_ARCHIVE_AFTER_DAYS
    return min(n, 36500)


def _archive_key(key: bytes) -> bytes:
    return _hmac.new(key, _ARCHIVE_INFO, hashlib.sha256).digest()


def _seal_blob(key: bytes, name: str, plain: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    return _ARCHIVE_MAGIC + nonce + AESGCM(_archive_key(key)).encrypt(
        nonce, plain, name.encode("ascii"))


def _open_blob(key: bytes, name: str, blob: bytes) -> bytes:
    """The archived file's bytes; raises LedgerError when it does not open
    under this key (changed, substituted, or another device's key)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not blob.startswith(_ARCHIVE_MAGIC) or len(blob) < len(_ARCHIVE_MAGIC) + 28:
        raise LedgerError("not an archived ledger file")
    nonce = blob[len(_ARCHIVE_MAGIC):len(_ARCHIVE_MAGIC) + 12]
    try:
        return AESGCM(_archive_key(key)).decrypt(nonce, blob[len(_ARCHIVE_MAGIC) + 12:],
                                                 name.encode("ascii"))
    except Exception:
        raise LedgerError("the archived file does not open under this device's key")


def _manifest_sig(key: bytes, body: dict) -> str:
    return _hmac.new(key, b"archive:" + canon(body), hashlib.sha256).hexdigest()


def _read_manifest() -> Optional[dict]:
    """The archive list as stored (signature not checked here), or None."""
    try:
        m = json.loads((archive_dir() / ARCHIVE_MANIFEST).read_text(encoding="utf-8"))
        return m if isinstance(m, dict) else {"ranges": "unreadable"}
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"ranges": "unreadable"}


def _manifest_body(m: dict) -> dict:
    return {k: v for k, v in m.items() if k != "sig"}


def _manifest_tail(m: Optional[dict]) -> Optional[tuple[int, str]]:
    rs = (m or {}).get("ranges")
    if isinstance(rs, list) and rs and isinstance(rs[-1], dict):
        try:
            return int(rs[-1]["last_seq"]), str(rs[-1]["last_hash"])
        except (KeyError, TypeError, ValueError):
            return None
    return None


def _write_manifest(key: bytes, ranges: list) -> None:
    body = {"v": 1, "ranges": ranges, "kid": key_id(key)}
    m = dict(body, sig=_manifest_sig(key, body))
    ad = archive_dir()
    ad.mkdir(parents=True, exist_ok=True)
    p = ad / ARCHIVE_MANIFEST
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    with open(tmp, "wb") as f:
        f.write(json.dumps(m, indent=1).encode("utf-8"))
        f.flush()
        if _fsync_durable():
            os.fsync(f.fileno())
    _replace(tmp, p)


def _check_archive(key: Optional[bytes], deep: bool) -> dict:
    """The archive's part of verify(): the signed list, every file named in
    it (present, its SHA-256), and with `deep` each file opened and its chain
    walked. Returns where the live chain must continue from."""
    out = {"last_seq": 0, "last_hash": GENESIS, "records": 0, "problems": [],
           "files": 0, "bytes": 0, "through_ts": None, "first_ts": None, "covered": {}}
    m = _read_manifest()
    ad = archive_dir()
    stray = sorted(p.name for p in ad.glob("actions-*.jsonl.enc")) if ad.is_dir() else []
    if m is None:
        if [x for x in stray if not _pending_move(key, x)]:
            out["problems"].append({"segment": "archive", "line": 0, "seq": None,
                                    "reason": "archived files are present but their signed list is missing",
                                    "raw_sha256": ""})
        return out
    raw_m = canon(m) if isinstance(m, dict) else b""
    rs = m.get("ranges")
    if not isinstance(rs, list):
        out["problems"].append(_problem("archive/" + ARCHIVE_MANIFEST, 0, raw_m,
                                        "the archive list cannot be read"))
        return out
    if key is not None and (m.get("kid") != key_id(key) or not _hmac.compare_digest(
            _manifest_sig(key, _manifest_body(m)), str(m.get("sig")))):
        out["problems"].append(_problem("archive/" + ARCHIVE_MANIFEST, 0, raw_m,
                                        "the archive list was changed"))
        return out          # the live chain is then checked from the start: a break
    last_seq, last_hash = 0, GENESIS
    cache = _STATE.setdefault("arch_cache", {})
    for r in rs:
        name = str((r or {}).get("name") or "")
        label = f"{ARCHIVE_DIR}/{name}.enc"
        try:
            first_seq, lseq = int(r["first_seq"]), int(r["last_seq"])
            first_prev, lhash = str(r["first_prev"]), str(r["last_hash"])
        except (KeyError, TypeError, ValueError):
            out["problems"].append(_problem(label, 0, canon(r), "an archive entry cannot be read"))
            return out
        if first_seq != last_seq + 1 or first_prev != last_hash:
            out["problems"].append(_problem(label, 0, canon(r),
                                            "the archive does not follow the record before it",
                                            first_seq))
        f = ad / (name + ".enc")
        try:
            st = f.stat()
            sig = (st.st_size, st.st_mtime_ns)
        except OSError:
            out["problems"].append(_problem(label, 0, canon(r), "an archived file is missing",
                                            first_seq))
            last_seq, last_hash = lseq, lhash
            continue
        hit = cache.get(name)
        if hit and hit[0] == sig and not deep and hit[1] is not None:
            reason = hit[1]
        else:
            reason = ""
            try:
                blob = f.read_bytes()
                if hashlib.sha256(blob).hexdigest() != r.get("sha256_enc"):
                    reason = "an archived file was changed or replaced"
                elif deep and key is not None:
                    plain = _open_blob(key, name, blob)
                    if hashlib.sha256(plain).hexdigest() != r.get("sha256_plain"):
                        reason = "an archived file was changed or replaced"
                    else:
                        reason = _walk_archived(plain, key, r)
            except LedgerError as e:
                reason = str(e)
            except OSError:
                reason = "an archived file cannot be read"
            cache[name] = (sig, reason)
        if reason:
            out["problems"].append(_problem(label, 0, canon(r), reason, first_seq))
        out["covered"][name] = str(r.get("sha256_plain"))
        out["files"] += 1
        out["bytes"] += st.st_size
        out["records"] += int(r.get("records") or 0)
        out["first_ts"] = out["first_ts"] or r.get("first_ts")
        out["through_ts"] = r.get("last_ts")
        last_seq, last_hash = lseq, lhash
    listed = {str((r or {}).get("name")) + ".enc" for r in rs}
    for n in stray:
        if n not in listed and not _pending_move(key, n):
            out["problems"].append({"segment": "archive", "line": 0, "seq": None,
                                    "reason": "an archived file is not in the signed list (%s)" % n,
                                    "raw_sha256": ""})
    out["last_seq"], out["last_hash"] = last_seq, last_hash
    return out


def _pending_move(key: Optional[bytes], enc_name: str) -> bool:
    """An encrypted copy written by a move that stopped before the list was:
    its sealed segment is still in place and the copy opens to its bytes."""
    if key is None:
        return False
    seg = ledger_dir() / SEGMENTS_DIR / enc_name[:-len(".enc")]
    try:
        return _enc_holds(key, seg.name, hashlib.sha256(seg.read_bytes()).hexdigest())
    except OSError:
        return False


def _walk_archived(plain: bytes, key: bytes, r: dict) -> str:
    """'' when the archived file's own chain is whole and matches its entry."""
    w = _walk_bytes(plain, key, str(r.get("name") or "archived"))
    if w["problems"]:
        return "an archived file's own chain does not hold"
    if (w["first_seq"] != r.get("first_seq") or w["first_prev"] != r.get("first_prev")
            or w["last_seq"] != r.get("last_seq") or w["last_hash"] != r.get("last_hash")
            or w["records"] != r.get("records")):
        return "an archived file does not match its entry in the list"
    return ""


def _strict_anchor_check(m: Optional[dict]) -> None:
    """A strict reader refuses a ledger that does not hold its anchor (the
    record kept outside the data folder): cut, rolled back, or replaced."""
    try:
        anchor = anchor_get()[0]
    except Exception:
        raise LedgerError("the ledger's anchor could not be read")
    if not anchor or anchor[0] <= 0:
        return
    aseq, ahash = anchor
    for p in reversed(_reader_segments()):
        try:
            for raw in _reverse_lines(p):
                e = _safe_json(raw)
                if not isinstance(e, dict) or not isinstance(e.get("seq"), int):
                    continue
                if e["seq"] == aseq:
                    if e.get("hash") != ahash or body_hash(e) != ahash:
                        raise LedgerError("the record kept outside the data folder was replaced")
                    return
                if e["seq"] < aseq:
                    break
        except OSError:
            raise LedgerError("the ledger could not be read")
    tail = _manifest_tail(m) if isinstance(m, dict) else None
    if tail and aseq <= tail[0]:
        if aseq == tail[0] and tail[1] != ahash:
            raise LedgerError("the record kept outside the data folder was replaced")
        return                           # inside the archive (its signed list holds it)
    raise LedgerError("entries are missing: the ledger is behind the record kept outside the data folder")


def _archived_entries(key: bytes, kinds: Optional[Iterable[str]] = None) -> list[list[dict]]:
    """Every archived file's entries (of `kinds`, when given), oldest file
    first. Rows of a kind filter are kept in memory per file (a strict reader
    such as revocations asks again often); a read of everything is not kept.
    Raises when the ledger does not hold its anchor."""
    m = _read_manifest()
    _strict_anchor_check(m)
    if m is None:
        if _archive_gone(key):
            raise LedgerError("the action ledger's archive is missing")
        return []
    rs = m.get("ranges")
    if not isinstance(rs, list):
        raise LedgerError("the archive list cannot be read")
    if not rs:
        return []
    if m.get("kid") != key_id(key) or not _hmac.compare_digest(
            _manifest_sig(key, _manifest_body(m)), str(m.get("sig"))):
        raise LedgerError("the archive list was changed")
    # the live record must continue from the list's end, and no archived file
    # may lie outside it: a list put back from an earlier copy is refused
    tail = _manifest_tail(m)
    listed = {str((r or {}).get("name")) + ".enc" for r in rs}
    for f in archive_dir().glob("actions-*.jsonl.enc"):
        if f.name not in listed and not _pending_move(key, f.name):
            raise LedgerError("an archived file is not in the signed list")
    cov = {str((r or {}).get("name")) for r in rs}
    first = None
    for p in segments():
        if p.name != LIVE_NAME and p.name in cov:
            continue
        try:
            first = next((e for e, _r, _n, _t in _iter_lines(p) if isinstance(e, dict)), None)
        except OSError:
            first = None
        if first is not None:
            break
    if first is not None and tail and (first.get("seq") != tail[0] + 1 or first.get("prev") != tail[1]):
        raise LedgerError("the live record does not continue from the archive")
    want = frozenset(kinds) if kinds else None
    rows_cache = _STATE.setdefault("arch_rows", {})
    out = []
    for r in rs:
        name = str(r.get("name"))
        sha = str(r.get("sha256_enc"))
        ck = (name, sha, want)
        if want is not None and ck in rows_cache:
            out.append(rows_cache[ck])
            continue
        blob = (archive_dir() / (name + ".enc")).read_bytes()
        if hashlib.sha256(blob).hexdigest() != sha:
            raise LedgerError("an archived file was changed or replaced")
        plain = _open_blob(key, name, blob)
        rows = [e for e in (_safe_json(x) for x in plain.split(b"\n") if x.strip())
                if isinstance(e, dict) and (want is None or e.get("kind") in want)]
        if want is not None:
            if len(rows_cache) >= 4096:
                rows_cache.pop(next(iter(rows_cache)))
            rows_cache[ck] = rows
        out.append(rows)
    return out


def _covered_names() -> dict:
    """{segment name: sha256 of its plain bytes} for every archived segment,
    from the list only when its signature holds (a forged list hides nothing)."""
    m = _read_manifest()
    rs = (m or {}).get("ranges")
    if not isinstance(rs, list) or not rs:
        return {}
    try:
        key = _key()
    except Exception:
        return {}
    if m.get("kid") != key_id(key) or not _hmac.compare_digest(
            _manifest_sig(key, _manifest_body(m)), str(m.get("sig"))):
        return {}
    return {str(r.get("name")): str(r.get("sha256_plain")) for r in rs if isinstance(r, dict)}


def _reader_segments() -> list[Path]:
    """segments() less any sealed file under an archived name: the archive
    holds those entries, and a file put back under that name is never read."""
    cov = _covered_names()
    if not cov:
        return segments()
    return [p for p in segments() if p.name == LIVE_NAME or p.name not in cov]


def _archive_gone(key: Optional[bytes] = None) -> bool:
    """No archive list, yet the live record does not start at the first entry
    (or archived files lie about without their list): the archive was removed.
    A copy written by a move that stopped before its list is not that."""
    ad = archive_dir()
    if ad.is_dir() and any(not _pending_move(key, p.name) for p in ad.glob("actions-*.jsonl.enc")):
        return True
    for p in segments():
        try:
            for e, _raw, _n, _t in _iter_lines(p):
                if isinstance(e, dict):
                    return e.get("seq") != 1 or e.get("prev") != GENESIS
        except OSError:
            return True
    return False


def _new_month_locked(live: Path) -> bool:
    """The live file's first entry is from an earlier calendar month (UTC):
    it is sealed, so a sealed segment holds at most one month (A22)."""
    try:
        st = live.stat()
    except OSError:
        return False
    if st.st_size == 0:
        return False
    ident = (st.st_ino, st.st_dev)
    lf = _STATE.get("live_first")
    if not lf or lf[0] != ident:
        t = None
        try:
            with open(live, "rb") as f:
                e = _safe_json(f.readline())
                if isinstance(e, dict) and isinstance(e.get("t"), (int, float)):
                    t = float(e["t"])
        except OSError:
            return False
        lf = (ident, t)
        _STATE["live_first"] = lf
    if lf[1] is None:
        return False
    a, b = time.gmtime(lf[1]), time.gmtime(time.time())
    return (a.tm_year, a.tm_mon) < (b.tm_year, b.tm_mon)


def _unlink_retry(p: Path) -> bool:
    for i in range(10):
        try:
            os.unlink(p)
            return True
        except FileNotFoundError:
            return True
        except PermissionError:
            time.sleep(0.02 * (i + 1))
        except OSError:
            return False
    return False


def _fsync_dir(d: Path) -> None:
    if os.name == "nt" or not _fsync_durable():
        return
    try:
        fd = os.open(str(d), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _enc_holds(key: bytes, name: str, sha_plain: str, sha_enc: Optional[str] = None) -> bool:
    """The archived copy of `name` is on disk and opens to exactly those bytes."""
    try:
        blob = (archive_dir() / (name + ".enc")).read_bytes()
        if sha_enc is not None and hashlib.sha256(blob).hexdigest() != sha_enc:
            return False
        return hashlib.sha256(_open_blob(key, name, blob)).hexdigest() == sha_plain
    except (OSError, LedgerError):
        return False


def _walk_bytes(plain: bytes, key: bytes, name: str = "archived") -> dict:
    return _walk(Path(SEGMENTS_DIR) / name, key, False, data=plain)


def archive_old(*, now: Optional[float] = None, days: Optional[int] = None) -> dict:
    """Move sealed segments whose newest entry is older than the period into
    the encrypted archive (A22). Only while the ledger has no open break, only
    whole segments, oldest first and contiguous, and never one with a break in
    it (a flagged entry only once the owner acknowledged it). One segment per
    hold of the locks, so writers wait for one file at most. Nothing is
    deleted: the plain segment is removed only after its encrypted copy
    (checked to open to the same bytes) and the signed list are on disk."""
    days = archive_after_days() if days is None else normalise_archive_days(days)
    if days == 0:
        return {"archived": 0, "why": "archiving is off"}
    now = time.time() if now is None else float(now)
    cutoff = now - days * 86400.0
    key = _key()
    moved: list[dict] = []
    with _TLOCK:
        _check_dir()
        _ensure_migrated()
    if _legacy_present():
        return {"archived": 0, "why": "older receipt files are still being brought in"}
    # the deep check (every archived file opened and walked) runs without the
    # locks; under them only what changed since is checked again
    if not verify(force=True).get("ok"):
        return {"archived": 0, "why": "the ledger has a break; nothing is archived until it is reviewed"}
    why = None
    for _ in range(100000):
        with _TLOCK:
            _check_dir()
            with _lock():
                _HELD_HERE.n = getattr(_HELD_HERE, "n", 0) + 1
                try:
                    step = _archive_one_locked(key, cutoff, days)
                finally:
                    _HELD_HERE.n -= 1
        if step.get("why"):
            why = step["why"]
        if step.get("moved"):
            moved.append(step["moved"])
        if not step.get("more"):
            break
    out = {"archived": len(moved), "records": sum(x["records"] for x in moved),
           "through": moved[-1]["last_ts"] if moved else None, "after_days": days}
    if why and not moved:
        out["why"] = why
    return out


def _acked_flags(report: dict) -> set:
    return set(_STATE.get("acked_flag_keys") or ())


def _next_first(after: Path) -> Optional[dict]:
    """The first entry of the file after `after` (the next sealed one or the live file)."""
    segs = segments()
    try:
        i = [x.name for x in segs].index(after.name)
    except ValueError:
        return None
    for nxt in segs[i + 1:]:
        try:
            e = next((e for e, _r, _n, _t in _iter_lines(nxt) if isinstance(e, dict)), None)
        except OSError:
            return None
        if e is not None:
            return e
    return None


def _archive_one_locked(key: bytes, cutoff: float, days: int) -> dict:
    """One step of archive_old; the caller holds both locks. Returns
    {"moved": entry} or {"why": ...}, and "more" when another step may follow."""
    # checked again under the write lock, so nothing changes between the
    # check and the move (the cached deep result when no file changed)
    r = verify(force=False, migrate=False)
    if not r.get("ok"):
        return {"why": "the ledger has a break; nothing is archived until it is reviewed"}
    acked = _acked_flags(r)
    m = _read_manifest()
    ranges = list((m or {}).get("ranges") or [])
    tail = _manifest_tail(m) or (0, GENESIS)
    ad = archive_dir()
    for left in (list(ad.glob("*.tmp")) if ad.is_dir() else []):
        try:
            left.unlink()                        # a crash's leftovers (writers hold this lock)
        except OSError:
            pass
    for p in [x for x in segments() if x.name != LIVE_NAME]:
        plain = p.read_bytes()
        name = p.name
        sha_plain = hashlib.sha256(plain).hexdigest()
        done = next((x for x in ranges if x.get("name") == name), None)
        if done is not None:
            # a move cut short after the list was written: finish it,
            # only when the archived copy opens to these very bytes
            if (done.get("sha256_plain") == sha_plain
                    and _enc_holds(key, name, sha_plain, done.get("sha256_enc"))):
                if _unlink_retry(p):
                    _append_locked(key, [("archived", {"segments": [], "finished": [name],
                                                       "after_days": days})], True)
                    return {"more": True}
                continue
            return {"why": "a file under an archived name holds other bytes"}
        w = _walk_bytes(plain, key, name)
        unacked = [f for f in w["flagged"]
                   if ("seq", f.get("seq"), f.get("hash"), _flag_reason(f)) not in acked]
        if (w["problems"] or unacked or not w["records"]
                or w["first_seq"] != tail[0] + 1 or w["first_prev"] != tail[1]):
            return {"why": "a segment with a break or an unreviewed flag stays live"}
        nxt = _next_first(p)
        if nxt is not None and (nxt.get("seq") != w["last_seq"] + 1 or nxt.get("prev") != w["last_hash"]):
            return {"why": "the next file does not follow this one; nothing is archived"}
        ts = [e["t"] for e, _r, _n, _t in _lines_of(plain)
              if isinstance(e, dict) and isinstance(e.get("t"), (int, float))]
        if not ts or max(ts) >= cutoff:
            return {}
        ad.mkdir(parents=True, exist_ok=True)
        dst = ad / (name + ".enc")
        if dst.exists():
            # an earlier run stopped after writing the copy: adopt it
            # when it opens to the same bytes, otherwise stop here
            if not _enc_holds(key, name, sha_plain):
                return {"why": "an archived copy under this name holds other bytes"}
            blob = dst.read_bytes()
        else:
            blob = _seal_blob(key, name, plain)
            tmp = ad / (name + f".{os.getpid()}.tmp")
            try:
                with open(tmp, "wb") as f:
                    f.write(blob)
                    f.flush()
                    if _fsync_durable():
                        os.fsync(f.fileno())
                _move_no_overwrite(tmp, dst)
            finally:
                try:
                    tmp.unlink()
                except OSError:
                    pass
            if not _enc_holds(key, name, sha_plain):
                return {"why": "the archived copy did not read back"}
        entry = {"name": name, "first_seq": w["first_seq"], "last_seq": w["last_seq"],
                 "first_prev": w["first_prev"], "last_hash": w["last_hash"],
                 "records": w["records"],
                 "first_ts": datetime.fromtimestamp(min(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "last_ts": datetime.fromtimestamp(max(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "sha256_plain": sha_plain,
                 "sha256_enc": hashlib.sha256(blob).hexdigest(),
                 "archived_at": _now()[0]}
        ranges.append(entry)
        _write_manifest(key, ranges)
        _fsync_dir(ad)
        _unlink_retry(p)        # still open in a reader on Windows: removed next time
        _STATE["seg_cache"] = {}
        with _CACHE_LOCK:
            _STATE["verified"] = None
        _append_locked(key, [("archived", {"segments": [name], "finished": [],
                                           "first_seq": entry["first_seq"], "last_seq": entry["last_seq"],
                                           "last_hash": entry["last_hash"], "records": entry["records"],
                                           "through": entry["last_ts"], "after_days": days})], True)
        return {"moved": entry, "more": True}
    return {}


def archive_summary() -> dict:
    """What the Ledger page says about the archive: counts and dates, no content."""
    with _TLOCK:
        _check_dir()
    m = _read_manifest()
    rs = (m or {}).get("ranges") if isinstance(m, dict) else None
    rs = rs if isinstance(rs, list) else []
    return {"after_days": archive_after_days(), "files": len(rs),
            "records": sum(int((r or {}).get("records") or 0) for r in rs),
            "first_ts": rs[0].get("first_ts") if rs else None,
            "through_ts": rs[-1].get("last_ts") if rs else None,
            "last_seq": (_manifest_tail(m) or (0, ""))[0]}


# ── Verification ────────────────────────────────────────────────────────────

def _problem(label: str, n: int, raw: bytes, reason: str, seq=None) -> dict:
    return {"segment": label, "line": n, "seq": seq, "reason": reason,
            "raw_sha256": hashlib.sha256(raw).hexdigest()}


def _ack_key(p: dict) -> tuple:
    """What an acknowledgement names: the record and the line as found (not
    the file it sits in, which changes when a segment is sealed)."""
    if p.get("seq") is not None:
        return ("seq", p.get("seq"), p.get("raw_sha256"), p.get("reason"))
    return ("line", p.get("segment"), p.get("line"), p.get("raw_sha256"), p.get("reason"))


def _lines_of(data: bytes) -> Iterator[tuple[Optional[dict], bytes, int, bool]]:
    """_iter_lines over bytes already in memory (archived files are never
    written out in plain)."""
    n = 0
    for raw in data.splitlines(keepends=True):
        n += 1
        terminated = raw.endswith(b"\n")
        if terminated:
            raw = raw[:-1]
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        if not raw.strip():
            continue
        if not terminated:
            yield None, raw, n, False
            continue
        try:
            e = json.loads(raw)
            yield (e if isinstance(e, dict) else None), raw, n, True
        except ValueError:
            yield None, raw, n, True


def _walk(path: Path, key: Optional[bytes], is_live: bool, data: Optional[bytes] = None) -> dict:
    """One file's lines: their internal links, hashes and signatures. The link
    from the file before is checked by the caller (first_seq, first_prev).
    `data`: the file's bytes, already read (nothing is written to disk)."""
    kid = key_id(key) if key else None
    label = _seg_label(path)
    res = {"records": 0, "first_seq": None, "first_prev": None, "first_line": 1,
           "first_raw": b"", "last_seq": None, "last_hash": None,
           "problems": [], "acks": [], "flagged": [], "foreign": 0}
    last_seq = last_hash = None
    rows = list(_lines_of(data) if data is not None else _iter_lines(path))
    for idx, (e, raw, n, terminated) in enumerate(rows):
        if e is None:
            if (is_live and not terminated and idx == len(rows) - 1
                    and _someone_else_writing()):
                digest = hashlib.sha256(raw).hexdigest()
                if _STATE.get("torn_seen") != digest:
                    _STATE["torn_seen"] = digest
                    continue                     # another process is mid-line (once)
            res["problems"].append(_problem(label, n, raw,
                                            "unreadable line" if terminated
                                            else "an unfinished write"))
            continue
        res["records"] += 1
        seq = e.get("seq")
        reasons = []
        if res["first_seq"] is None:
            res["first_seq"], res["first_prev"] = seq, e.get("prev")
            res["first_line"], res["first_raw"] = n, raw
        else:
            if not isinstance(seq, int) or last_seq is None or seq != last_seq + 1:
                reasons.append("out of order (expected record %s, found %s)"
                               % ((last_seq or 0) + 1, seq))
            if e.get("prev") != last_hash:
                reasons.append("does not follow the record before it")
        try:
            if body_hash(e) != e.get("hash"):
                reasons.append("changed after it was written")
        except Exception:
            reasons.append("cannot be read as a record")
        if key is not None:
            if e.get("kid") != kid:
                res["foreign"] += 1
                reasons.append("signed with a key this device does not hold")
            elif not _hmac.compare_digest(_sign(key, str(e.get("hash"))), str(e.get("sig"))):
                reasons.append("signature does not match")
        for r in reasons:
            res["problems"].append(_problem(label, n, raw, r, seq if isinstance(seq, int) else None))
        if isinstance(seq, int):
            last_seq = seq
        if isinstance(e.get("hash"), str):
            last_hash = e["hash"]
        if not reasons and e.get("kind") == "integrity_ack":
            res["acks"].append(e.get("rec") or {})
        if not reasons and e.get("kind") in FLAG_KINDS:
            res["flagged"].append({"seq": seq, "kind": e.get("kind"), "rec": e.get("rec") or {},
                                   "hash": e.get("hash")})
    res["last_seq"], res["last_hash"] = last_seq, last_hash
    return res


def _hash_at(seq: int) -> Optional[str]:
    """The stored hash of record `seq`, read from the newest end (the anchor
    is usually near the tip)."""
    for p in reversed(_reader_segments()):
        try:
            for raw in _reverse_lines(p):
                e = _safe_json(raw)
                if not isinstance(e, dict) or not isinstance(e.get("seq"), int):
                    continue
                if e["seq"] == seq:
                    return e.get("hash")
                if e["seq"] < seq:
                    return None
        except OSError:
            continue
    m = _read_manifest()
    tail = _manifest_tail(m)
    if tail and seq <= tail[0]:
        if seq == tail[0]:
            return tail[1]
        try:
            for rows in _archived_entries(_key()):
                for e in rows:
                    if e.get("seq") == seq:
                        return e.get("hash")
        except Exception:
            return None
    return None


def _flag_reason(f: dict) -> str:
    r = f.get("rec") or {}
    if f.get("kind") == "truncation_detected":
        return "%s record(s) were missing from the end when Friday next wrote" % r.get("missing")
    if f.get("kind") == "legacy_reappeared":
        return "an old receipt file appeared after the switch (%s); it was set aside, not read" % r.get("from")
    return "an unfinished write was set aside (%s bytes)" % r.get("bytes")


def _verify_files(key: Optional[bytes], deep: bool = False) -> dict:
    """Every line of every segment, the archive it continues from (A22), the
    head, the anchor and what this process wrote. The whole report. `deep`
    also opens every archived file and walks its chain."""
    problems: list[dict] = []
    acks: list[dict] = []
    flagged: list[dict] = []
    foreign = 0
    arch = _check_archive(key, deep)
    problems.extend(arch["problems"])
    total, last_seq, last_hash = 0, arch["last_seq"], arch["last_hash"]
    segs = segments()
    cache = _STATE.setdefault("seg_cache", {})
    covered = arch.get("covered") or {}
    for i, p in enumerate(segs):
        is_live = p.name == LIVE_NAME
        if not is_live and p.name in covered:
            # a move into the archive that stopped before the plain file was
            # removed: the same bytes are already in the chain (archive_old
            # finishes it); different bytes under an archived name are a break
            try:
                got = hashlib.sha256(p.read_bytes()).hexdigest()
            except OSError:
                got = "unreadable"
            if got != covered[p.name]:
                problems.append({"segment": _seg_label(p), "line": 0, "seq": None,
                                 "reason": "a sealed file has the name of an archived one but other content",
                                 "raw_sha256": got})
            continue
        try:
            st = p.stat()
            sig = (p.name, st.st_size, st.st_mtime_ns, _tail_digest(p))
        except OSError:
            sig = None
        hit = cache.get(str(p)) if (sig and not is_live) else None
        if hit and hit[0] == sig and hit[1] == (key_id(key) if key else None):
            r = hit[2]
        else:
            r = _walk(p, key, is_live)
            if sig and not is_live:
                cache[str(p)] = (sig, key_id(key) if key else None, r)
        if r["records"]:
            if r["first_seq"] != last_seq + 1:
                problems.append(_problem(_seg_label(p), r["first_line"], r["first_raw"],
                                         "out of order (expected record %d, found %s)"
                                         % (last_seq + 1, r["first_seq"]),
                                         r["first_seq"] if isinstance(r["first_seq"], int) else None))
            if r["first_prev"] != last_hash:
                problems.append(_problem(_seg_label(p), r["first_line"], r["first_raw"],
                                         "does not follow the record before it",
                                         r["first_seq"] if isinstance(r["first_seq"], int) else None))
            if isinstance(r["last_seq"], int):
                last_seq = r["last_seq"]
            if r["last_hash"]:
                last_hash = r["last_hash"]
        total += r["records"]
        problems.extend(r["problems"])
        acks.extend(r["acks"])
        flagged.extend(r["flagged"])
        foreign += r["foreign"]
    if key is None:
        problems.append({"segment": "key", "line": 0, "seq": None,
                         "reason": "signatures cannot be checked: the governance key is unavailable",
                         "raw_sha256": ""})
    # the head: lines cut off the end since the last append
    head = _read_head()
    if head and isinstance(head.get("seq"), int):
        hraw = canon(head)
        if key is not None and head.get("kid") == key_id(key) and not _hmac.compare_digest(
                _head_sig(key, head["seq"], str(head.get("hash"))), str(head.get("sig"))):
            problems.append(_problem(HEAD_NAME, 0, hraw, "the ledger head was changed", head.get("seq")))
        elif head["seq"] > last_seq:
            problems.append(_problem(HEAD_NAME, 0, hraw, "%d record(s) missing from the end"
                                     % (head["seq"] - last_seq), head["seq"]))
    # the anchor outside the data folder
    anchor_where = "none"
    try:
        anchor, anchor_where = anchor_get()
    except Exception as e:  # noqa: BLE001
        anchor = None
        anchor_where = "unreadable"
        problems.append({"segment": "anchor", "line": 0, "seq": None,
                         "reason": "the ledger's anchor could not be read (%s)" % type(e).__name__,
                         "raw_sha256": ""})
    if anchor and anchor[0] > 0:
        aseq, ahash = anchor
        if last_seq < aseq:
            problems.append({"segment": "anchor", "line": 0, "seq": aseq,
                             "reason": "%d record(s) missing: the ledger is behind the record "
                                       "kept outside the data folder" % (aseq - last_seq),
                             "raw_sha256": ahash})
        elif _hash_at(aseq) != ahash:
            problems.append({"segment": "anchor", "line": 0, "seq": aseq,
                             "reason": "the record kept outside the data folder was replaced",
                             "raw_sha256": ahash})
    seen = _STATE.get("max_seen") or 0
    if last_seq < seen:
        problems.append({"segment": "process", "line": 0, "seq": seen,
                         "reason": "%d record(s) this session wrote are gone" % (seen - last_seq),
                         "raw_sha256": ""})
    for f in flagged:
        problems.append({"segment": "entry", "line": 0, "seq": f.get("seq"),
                         "reason": _flag_reason(f), "raw_sha256": f.get("hash")})
    acked = {tuple(a.get("key")) for ack in acks for a in (ack.get("problems") or [])
             if isinstance(a, dict) and isinstance(a.get("key"), list)}
    for pr in problems:
        pr["acknowledged"] = list(_ack_key(pr)) in [list(k) for k in acked]
    _STATE["acked_flag_keys"] = {tuple(_ack_key(pr)) for pr in problems
                                 if pr["acknowledged"] and pr.get("segment") == "entry"}
    open_problems = [pr for pr in problems if not pr["acknowledged"]]
    return {
        "records": total, "last_seq": last_seq, "tip": last_hash,
        "key_available": key is not None,
        "anchor": anchor_where,
        "intact": not problems,
        "ok": not open_problems,
        "problems": open_problems[:MAX_PROBLEMS] + [p for p in problems if p["acknowledged"]][:MAX_PROBLEMS],
        "problem_count": len(problems),
        "open_problem_count": len(open_problems),
        "foreign_records": foreign,
        "segments": len(segs),
        "bytes": sum((p.stat().st_size for p in segs if p.exists()), 0),
        "archived_records": arch["records"], "archive_files": arch["files"],
        "archive_bytes": arch["bytes"], "archived_through": arch["through_ts"],
    }


def _tail_digest(p: Path) -> str:
    try:
        size = p.stat().st_size
        with open(p, "rb") as f:
            f.seek(max(0, size - 65536))
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except OSError:
        return ""


def _stats() -> tuple:
    """(name, size, mtime_ns) of every ledger file and the head: a cache key
    that changes whenever any of them changes. From directory reads
    (os.scandir carries the sizes and times on Windows without opening each
    file). An edit that puts back a file's size and mtime is caught by the
    full re-read at least every FULL_RECHECK_S and at every start."""
    out = []
    d = ledger_dir()
    for where, keep in ((d / SEGMENTS_DIR, lambda n: n.startswith("actions-") and n.endswith(".jsonl")),
                        (d, lambda n: n in (LIVE_NAME, HEAD_NAME)),
                        (d / ARCHIVE_DIR, lambda n: n == ARCHIVE_MANIFEST or n.endswith(".enc"))):
        try:
            with os.scandir(where) as it:
                for e in it:
                    if keep(e.name):
                        tag = where.name + "/" + e.name
                        try:
                            st = e.stat()
                            out.append((tag, st.st_size, st.st_mtime_ns))
                        except OSError:
                            out.append((tag, -1, -1))
        except OSError:
            out.append((where.name + "/", -2, -2))
    return tuple(sorted(out))


def stats_token() -> tuple:
    """Changes whenever any ledger file changes: a cache key for readers."""
    with _TLOCK:
        _check_dir()
    return _stats()


def verify(*, force: bool = True, migrate: bool = True) -> dict:
    """Check every line: order, links, hashes, signatures, the head, the anchor,
    and which breaks the owner has acknowledged. `force=False` reuses the last
    result while no ledger file changed and it is younger than FULL_RECHECK_S.
    The files are read without holding the write lock."""
    with _TLOCK:
        _check_dir()
        if migrate:
            _ensure_migrated()
        stats = _stats()
        with _CACHE_LOCK:
            cached = _STATE.get("verified")
            fresh = time.time() - (_STATE.get("full_at") or 0.0) < FULL_RECHECK_S
        if not force and cached and cached[0] == stats and fresh:
            return dict(cached[1])
        full = force or not fresh
        # a forced or periodic full check re-reads sealed segments too: an
        # edit that put back a segment's size and mtime is caught here
        if force or not fresh:
            _STATE["seg_cache"] = {}
            _STATE["arch_cache"] = {}
            _STATE["arch_rows"] = {}
    try:
        key = _key()
    except Exception:
        key = None
    t0 = time.perf_counter()
    report = _verify_files(key, deep=force)
    report["verify_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    report["checked_at"] = _now()[0]
    if key is not None:                          # a result without the key is never kept
        with _CACHE_LOCK:
            if _stats() == stats:
                _STATE["verified"] = (stats, report)
                if full:
                    # only a check that re-read every file restarts the clock
                    _STATE["full_at"] = time.time()
    return dict(report)


def _legacy_present() -> list[Path]:
    try:
        return [p for _k, ps in _legacy_stores() for p in ps if p.is_file()]
    except Exception:
        return []


def hold_reason() -> Optional[str]:
    """A sentence when an outward action must wait for the owner to look at
    the ledger; None while it is intact (or every break is acknowledged). A
    ledger that cannot be checked holds too, and so does an old receipt file
    not yet brought in."""
    try:
        r = verify(force=False)
    except Exception as e:  # noqa: BLE001 - any failure to check holds
        return "the action ledger could not be checked (%s)" % type(e).__name__
    if not r.get("ok"):
        first = next((p for p in r.get("problems", []) if not p.get("acknowledged")), {})
        if first.get("segment") == "key":
            return "the action ledger cannot be checked: the governance key is unavailable"
        return ("the action ledger was changed on disk (%s, record %s: %s); "
                "open Activity to review it" % (first.get("segment"), first.get("seq"),
                                                first.get("reason")))
    if _legacy_present():
        return "older receipt files are still being brought into the action ledger"
    return None


class StaleReview(LedgerError):
    """The breaks open now are not the ones the owner reviewed."""


def acknowledge(by: str, note: str = "", *, reviewed: Optional[list] = None) -> dict:
    """Record that the owner has looked at the breaks they reviewed. The caller
    has checked that this is the owner's press on this PC. `reviewed` is the
    list of problems the page showed (each with seq/segment/line, raw_sha256
    and reason); when the breaks open now differ, nothing is recorded and
    StaleReview is raised. Only breaks shown are acknowledged."""
    r = verify(force=True)
    if r.get("problems") and any(p.get("segment") == "key" for p in r["problems"]):
        raise LedgerError("the ledger cannot be checked without the governance key")
    open_now = [p for p in r.get("problems", []) if not p.get("acknowledged")]
    shown = {tuple(_ack_key(p)) for p in (reviewed or []) if isinstance(p, dict)}
    now = {tuple(_ack_key(p)) for p in open_now}
    if reviewed is None or not now or now != shown:
        if not now:
            return {"acknowledged": 0, "report": r}
        raise StaleReview("the ledger changed since it was reviewed; review it again")
    items = [{"key": list(_ack_key(p)), "seq": p.get("seq"), "reason": p.get("reason")}
             for p in open_now]
    key = _key()
    with _TLOCK:
        _check_dir()
        out = _guarded_append(key, [("integrity_ack", {"problems": items, "by": str(by)[:40],
                                                         "note": str(note)[:200]})], True)
        # the owner accepted what is gone: the anchor and this process's own
        # high-water mark move to the acknowledgement itself (never to a fresh
        # read of the tip, which could take in a change made since the review)
        with _lock():
            _move_anchor_locked(out["seq"], out["hash"], force=True)
        _STATE["max_seen"] = out["seq"]
    return {"acknowledged": len(items), "report": verify(force=True)}


def summary(*, migrate: bool = True) -> dict:
    """What Activity and the Doctor show: counts and state, no content.
    `migrate=False` (the Doctor) only reads."""
    r = verify(force=False, migrate=migrate)
    return {k: r.get(k) for k in ("records", "last_seq", "intact", "ok", "key_available",
                                  "anchor", "problem_count", "open_problem_count",
                                  "verify_ms", "checked_at", "foreign_records",
                                  "segments", "bytes", "archived_records",
                                  "archive_files", "archived_through")}


# ── The stores this ledger replaced ─────────────────────────────────────────

def _legacy_stores() -> list[tuple[str, list[Path]]]:
    """(kind, legacy files) for every store whose writes now come here. Read
    at call time, because each module's own path names it."""
    from agent_friday.paths import friday_home
    home = Path(friday_home())
    stores: list[tuple[str, list[Path]]] = []

    def add(kind, getter):
        try:
            paths = [Path(p) for p in getter() if p]
        except Exception:
            paths = []
        stores.append((kind, paths))

    def _bom():
        from agent_friday.governance import action_gate as ag
        return [home / "vault" / "decision-bom.jsonl", ag.receipts_path()]

    def _journal():
        from agent_friday.services import action_journal as aj
        return [aj._path()]

    def _activity():
        from agent_friday.services import activity_ledger as al
        return [Path(str(al.LEDGER_FILE) + ".1"), al.LEDGER_FILE]

    def _goals():
        from agent_friday.services import goals
        return [goals.RECEIPTS_LOG]

    def _dissent():
        from agent_friday.services import dissent_gate
        return [dissent_gate.EVENTS_PATH]

    def _send_grants():
        from agent_friday.governance import send_grants
        return [send_grants._log_path()]

    def _web_sites():
        from agent_friday.governance import web_sites
        return [web_sites._history_path()]

    def _inbox():
        from agent_friday.services import inbox_cleanup
        return [inbox_cleanup.receipts_path()]

    def _diet():
        from agent_friday.services import media_diet
        return [media_diet.receipts_path()]

    add("decision", _bom)
    add("action", _journal)
    add("activity", _activity)
    add("goal", _goals)
    add("dissent", _dissent)
    add("send_grant", _send_grants)
    add("web_site", _web_sites)
    add("inbox_cleanup", _inbox)
    add("media_diet", _diet)
    return stores


def _legacy_check(kind: str, rec: dict) -> Optional[bool]:
    """Did a legacy line's own signature hold? None when it never had one."""
    try:
        if kind == "decision" and "hmac" in rec:
            from agent_friday.governance import action_gate
            return bool(action_gate.verify_receipt(rec))
        if kind in ("goal", "dissent", "web_site") and isinstance(rec.get("hmac"), str):
            if rec["hmac"].startswith("unsigned:"):
                return False
            from agent_friday.governance.proof_of_integrity import get_governance_key
            from agent_friday.privacy import vault_crypto
            return bool(vault_crypto.verify_entry(rec, get_governance_key()))
    except Exception:
        return False
    return None


def _migration_state_locked() -> dict:
    """What the switch from the old receipt files has done so far: archived
    entries count too (A22), and an archive that cannot be read counts as a
    finished switch, so an old file planted later is quarantined, never read."""
    counts: dict[str, int] = {}
    done: set = set()
    complete = False
    sources: list = []
    if _archive_trace() or _chain_start_missing() or not _chain_is_genuine():
        # an archive exists only once old files were dealt with (archive_old
        # waits for them), and a chain whose start is gone was not started by
        # this switch: either way the switch is over, and an old file that
        # appears now is quarantined, never read
        complete = True
        try:
            sources = list(_archived_entries(_key()))
        except Exception:
            return {"counts": counts, "done": done, "complete": True}
    for src in sources + list(segments()):
        rows = ((e, b"", 0, True) for e in src) if isinstance(src, list) else _iter_lines(src)
        for e, _raw, _n, _t in rows:
            if not isinstance(e, dict):
                continue
            r = e.get("rec") or {}
            k = e.get("kind")
            if k == "migration_complete":
                complete = True
                continue
            if k == "migration":
                if r.get("file_sha256"):
                    done.add(r.get("file_sha256"))
                continue
            m = r.get("_migrated") if isinstance(r, dict) else None
            if isinstance(m, dict) and m.get("file_sha256"):
                counts[m["file_sha256"]] = counts.get(m["file_sha256"], 0) + 1
    return {"counts": counts, "done": done, "complete": complete}


def _archive_trace() -> bool:
    """Any sign an archive was made: its list (readable or not) or a file."""
    ad = archive_dir()
    try:
        return ad.is_dir() and ((ad / ARCHIVE_MANIFEST).exists()
                                or any(ad.glob("actions-*.jsonl.enc")))
    except OSError:
        return True


def _first_on_disk() -> Optional[dict]:
    for p in segments():
        try:
            for e, _raw, _n, _t in _iter_lines(p):
                if isinstance(e, dict):
                    return e
        except OSError:
            continue
    return None


def _chain_is_genuine() -> bool:
    """The chain on disk is this device's own, whole and anchored: its first
    entry is signed, seq 1 from the genesis link, and a full check finds no
    open break but an unfinished last write. Only then may an import that
    stopped halfway continue; anything else ends the switch (quarantine)."""
    try:
        key = _key()
    except Exception:
        return False
    first = _first_on_disk()
    if first is None:
        try:
            a = anchor_get()[0]
        except Exception:
            return False
        return a is None or a[0] == 0
    if not (entry_ok(first, key) and first.get("seq") == 1 and first.get("prev") == GENESIS):
        return False
    rep = _verify_files(key)
    for p in rep.get("problems", []):
        if p.get("acknowledged"):
            continue
        if p.get("reason") == "an unfinished write" and p.get("segment") == LIVE_NAME:
            continue
        if p.get("segment") == "entry" and "unfinished write was set aside" in str(p.get("reason")):
            continue
        return False
    return True


def _chain_start_missing() -> bool:
    e = _first_on_disk()
    return e is not None and (e.get("seq") != 1 or e.get("prev") != GENESIS)


def _file_sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _rel(p: Path) -> str:
    try:
        from agent_friday.paths import friday_home
        return Path(p).resolve().relative_to(Path(friday_home()).resolve()).as_posix()
    except Exception:
        return Path(p).name


def _set_aside(p: Path, where: Path, digest: str) -> str:
    """Move a file whole and unchanged under `where`; never over another."""
    where.mkdir(parents=True, exist_ok=True)
    dst = where / f"{digest[:12]}-{p.name}"
    try:
        if dst.exists():
            if _file_sha256(dst) == digest:
                os.remove(p)                  # the same bytes are already kept
                return f"{where.name}/{dst.name}"
            dst = where / f"{digest[:12]}-{int(time.time() * 1000)}-{p.name}"
        _move_no_overwrite(p, dst)
    except OSError:
        return _rel(p)                        # left where it was; tried again next time
    return f"{where.name}/{dst.name}"


def migrate_legacy() -> dict:
    """Bring every old receipt file into the ledger, once.

    Each legacy line becomes an entry of its kind with `_migrated` (where it
    came from, its line number, whether its own signature held). The old file
    is then moved, whole and unchanged, to ledger/legacy/, and a `migration`
    entry records its SHA-256 so the original can still be checked. When every
    file is in, a `migration_complete` entry closes the door: an old file that
    appears after it is moved to ledger/quarantine/ unread, and a
    `legacy_reappeared` entry is a break for the owner to look at.

    Resumable: a file half imported when Friday stopped continues from the
    first line not yet in the ledger. Idempotent: a file already done is
    recognised by its digest. Called at every start; on a fresh ledger with no
    old files it only writes `migration_complete`."""
    key = _key()
    moved = []
    with _TLOCK:
        _check_dir()
        _STATE["migrated"] = True
        with _lock():
            _HELD_HERE.n = getattr(_HELD_HERE, "n", 0) + 1
            try:
                present = _legacy_present()
                # empty: no entry anywhere (bytes that are not an entry count
                # as nothing) and no sign of an archive
                empty = _first_on_disk() is None and not _archive_trace()
                if not present and not empty:
                    return {"migrated": moved}
                try:
                    anchored = (anchor_get()[0] or (0, ""))[0] > 0
                except Exception:
                    anchored = True
                # an empty ledger that was anchored was removed: an old file
                # found now is quarantined, never read into a fresh chain
                state = (_migration_state_locked() if not empty
                         else {"counts": {}, "done": set(), "complete": anchored})
                d = ledger_dir()
                for kind, paths in _legacy_stores():
                    for p in paths:
                        if not p.is_file():
                            continue
                        digest = _file_sha256(p)
                        if state["complete"]:
                            kept = _set_aside(p, d / "quarantine", digest)
                            _append_locked(key, [("legacy_reappeared", {
                                "store": kind, "from": _rel(p), "file_sha256": digest,
                                "kept_as": kept})], True)
                            moved.append({"store": kind, "from": _rel(p), "quarantined": True})
                            continue
                        if digest in state["done"]:
                            _set_aside(p, d / "legacy", digest)
                            continue
                        already = state["counts"].get(digest, 0)
                        n_lines = n_imported = n_unreadable = 0
                        batch: list = []
                        for _e, raw, n, _t in _iter_lines(p):
                            n_lines += 1
                            rec = _safe_json(raw)
                            if not isinstance(rec, dict):
                                n_unreadable += 1
                                rec = {"unreadable": True,
                                       "raw_sha256": hashlib.sha256(raw).hexdigest()}
                            if n_lines <= already:
                                continue
                            rec = dict(rec)
                            rec["_migrated"] = {"from": _rel(p), "line": n, "file_sha256": digest,
                                                "legacy_verified": _legacy_check(kind, rec)}
                            batch.append((kind, rec))
                            n_imported += 1
                            if len(batch) >= 2000:
                                _append_locked(key, batch, True)
                                batch = []
                        if batch:
                            _append_locked(key, batch, True)
                        kept = _set_aside(p, d / "legacy", digest)
                        _append_locked(key, [("migration", {
                            "store": kind, "from": _rel(p), "file_sha256": digest,
                            "lines": n_lines, "imported": n_imported + already,
                            "unreadable": n_unreadable, "kept_as": kept})], True)
                        moved.append({"store": kind, "from": _rel(p), "lines": n_lines})
                if empty and anchored:
                    pass        # an anchored ledger that is now empty was removed: verify() says so
                elif not state["complete"] and not _legacy_present():
                    _append_locked(key, [("migration_complete",
                                          {"files": len([m for m in moved if not m.get("quarantined")])})],
                                   True)
            finally:
                _HELD_HERE.n -= 1
    return {"migrated": moved}


def _ensure_migrated() -> None:
    """Called under _TLOCK by every read and write: the first use in a process
    deals with anything still in an old file."""
    if _STATE.get("migrated"):
        return
    _STATE["migrated"] = True               # set first: migrate_legacy appends
    try:
        if _legacy_present():
            migrate_legacy()
    except Exception:
        _STATE["migrated"] = False          # try again on the next use
