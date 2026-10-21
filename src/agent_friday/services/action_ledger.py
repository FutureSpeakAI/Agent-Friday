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

ANCHOR_SERVICE = "agent-friday"
_KEY_INFO = b"agent-friday/action-ledger/v1"

_TLOCK = threading.RLock()
_CACHE_LOCK = threading.Lock()
_TEST_ANCHORS: dict[str, str] = {}
_STATE: dict[str, Any] = {}


def _fresh_state(d: Optional[str]) -> dict:
    return {"dir": d, "tip": None, "verified": None, "full_at": 0.0,
            "migrated": False, "seg_cache": {}, "max_seen": 0,
            "anchor_at": 0.0, "anchor_seq": None}


_STATE.update(_fresh_state(None))


class LedgerError(RuntimeError):
    """The ledger could not be written or read (no key, no disk, lock timeout)."""


# ── Paths ───────────────────────────────────────────────────────────────────

def _testing() -> bool:
    return os.environ.get("FRIDAY_TESTING") == "1"


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
    """Sealed segments, oldest first, then the live file (when present)."""
    d = ledger_dir()
    sd = d / SEGMENTS_DIR
    out = sorted(sd.glob("actions-*.jsonl"), key=_segment_index) if sd.is_dir() else []
    live = d / LIVE_NAME
    if live.exists():
        out.append(live)
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
    leaves the old anchor, which can only make a later check stricter."""
    cur = _STATE.get("anchor_seq")
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
    the restored ledger's newest entry."""
    with _TLOCK:
        _check_dir()
        with _lock():
            seq, h = _read_tip_locked()
            _move_anchor_locked(seq, h, force=True)
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
    for p in reversed(segments()):
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
    n = max([_segment_index(p) for p in sd.glob("actions-*.jsonl")] + [0]) + 1
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
        if live.exists() and live.stat().st_size >= SEGMENT_MAX:
            _seal_live_locked(d, live)
    except OSError:
        pass                                   # sealed next time
    pre_stats = _stats()
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
        if durable:
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
        if (cached and cached[0] == pre_stats and not pending and cached[1].get("ok")
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
            verified: bool = False, strict: bool = False) -> list[dict]:
    """Entries (whole lines), optionally filtered by kind and a predicate.

    `verified`: only lines whose hash and signature hold under this device's
    key (for readers that act on what they read, such as Undo).
    `strict`: raise LedgerError instead of returning what could be read (for
    readers that must fail closed, such as revoked grants). Otherwise never
    raises, and an unreadable line is skipped (verify() reports it)."""
    want = set(kinds) if kinds else None
    key = None
    if verified:
        try:
            key = _key()
        except Exception:
            if strict:
                raise LedgerError("the ledger key is not available")
            return []
    with _TLOCK:
        _check_dir()
        _ensure_migrated()
    for attempt in range(2):
        out: list[dict] = []
        try:
            segs = segments()
            order = list(reversed(segs)) if newest_first else segs
            for p in order:
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


def _walk(path: Path, key: Optional[bytes], is_live: bool) -> dict:
    """One file's lines: their internal links, hashes and signatures. The link
    from the file before is checked by the caller (first_seq, first_prev)."""
    kid = key_id(key) if key else None
    label = _seg_label(path)
    res = {"records": 0, "first_seq": None, "first_prev": None, "first_line": 1,
           "first_raw": b"", "last_seq": None, "last_hash": None,
           "problems": [], "acks": [], "flagged": [], "foreign": 0}
    last_seq = last_hash = None
    rows = list(_iter_lines(path))
    for idx, (e, raw, n, terminated) in enumerate(rows):
        if e is None:
            if (is_live and not terminated and idx == len(rows) - 1
                    and _someone_else_writing()):
                continue                         # another process is mid-line
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
    for p in segments():
        try:
            for e, _raw, _n, _t in _iter_lines(p):
                if isinstance(e, dict) and e.get("seq") == seq:
                    return e.get("hash")
        except OSError:
            continue
    return None


def _flag_reason(f: dict) -> str:
    r = f.get("rec") or {}
    if f.get("kind") == "truncation_detected":
        return "%s record(s) were missing from the end when Friday next wrote" % r.get("missing")
    if f.get("kind") == "legacy_reappeared":
        return "an old receipt file appeared after the switch (%s); it was set aside, not read" % r.get("from")
    return "an unfinished write was set aside (%s bytes)" % r.get("bytes")


def _verify_files(key: Optional[bytes]) -> dict:
    """Every line of every segment, the head, the anchor and what this process
    wrote. The whole report."""
    problems: list[dict] = []
    total, last_seq, last_hash = 0, 0, GENESIS
    acks: list[dict] = []
    flagged: list[dict] = []
    foreign = 0
    segs = segments()
    cache = _STATE.setdefault("seg_cache", {})
    for i, p in enumerate(segs):
        is_live = p.name == LIVE_NAME
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
    if anchor:
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
    out = []
    for p in segments():
        try:
            st = p.stat()
            out.append((p.name, st.st_size, st.st_mtime_ns,
                        _tail_digest(p) if p.name == LIVE_NAME else ""))
        except OSError:
            out.append((p.name, -1, -1, ""))
    try:
        st = head_path().stat()
        out.append((HEAD_NAME, st.st_size, st.st_mtime_ns, ""))
    except OSError:
        pass
    return tuple(out)


def stats_token() -> tuple:
    """Changes whenever any ledger file changes: a cache key for readers."""
    with _TLOCK:
        _check_dir()
    return _stats()


def verify(*, force: bool = True) -> dict:
    """Check every line: order, links, hashes, signatures, the head, the anchor,
    and which breaks the owner has acknowledged. `force=False` reuses the last
    result while no ledger file changed and it is younger than FULL_RECHECK_S.
    The files are read without holding the write lock."""
    with _TLOCK:
        _check_dir()
        _ensure_migrated()
        stats = _stats()
        with _CACHE_LOCK:
            cached = _STATE.get("verified")
            fresh = time.time() - (_STATE.get("full_at") or 0.0) < FULL_RECHECK_S
        if not force and cached and cached[0] == stats and fresh:
            return dict(cached[1])
    try:
        key = _key()
    except Exception:
        key = None
    t0 = time.perf_counter()
    report = _verify_files(key)
    report["verify_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    report["checked_at"] = _now()[0]
    if key is not None:                          # a result without the key is never kept
        with _CACHE_LOCK:
            if _stats() == stats:
                _STATE["verified"] = (stats, report)
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
        with _lock():
            seq, h = _read_tip_locked()
        # the owner accepted what is gone: the anchor and this process's own
        # high-water mark move to what is here now
        with _lock():
            _move_anchor_locked(seq, h, force=True)
        _STATE["max_seen"] = seq
    del out
    return {"acknowledged": len(items), "report": verify(force=True)}


def summary() -> dict:
    """What Activity and the Doctor show: counts and state, no content."""
    r = verify(force=False)
    return {k: r.get(k) for k in ("records", "last_seq", "intact", "ok", "key_available",
                                  "anchor", "problem_count", "open_problem_count",
                                  "verify_ms", "checked_at", "foreign_records",
                                  "segments", "bytes")}


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
    counts: dict[str, int] = {}
    done: set = set()
    complete = False
    for p in segments():
        for e, _raw, _n, _t in _iter_lines(p):
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
                empty = not any(p.exists() and p.stat().st_size for p in segments())
                if not present and not empty:
                    return {"migrated": moved}
                state = _migration_state_locked() if not empty else {"counts": {}, "done": set(),
                                                                     "complete": False}
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
                                _append_locked(key, batch, False)
                                batch = []
                        if batch:
                            _append_locked(key, batch, False)
                        kept = _set_aside(p, d / "legacy", digest)
                        _append_locked(key, [("migration", {
                            "store": kind, "from": _rel(p), "file_sha256": digest,
                            "lines": n_lines, "imported": n_imported + already,
                            "unreadable": n_unreadable, "kept_as": kept})], True)
                        moved.append({"store": kind, "from": _rel(p), "lines": n_lines})
                if not state["complete"] and not _legacy_present():
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
