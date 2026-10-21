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
``ledger/segments/actions-NNNNNN.jsonl`` and the chain continues in a new live
file. Nothing is ever deleted (an append-only store, LESSONS L34).

``ledger/head.json`` holds the newest seq and hash, signed. A head ahead of the
file means lines were cut off the end. When an append finds that, it first
writes a ``truncation_detected`` entry, so the break stays on the record after
the head moves on.

What a break does
-----------------
``hold_reason()`` returns a sentence while the ledger has a break the owner has
not acknowledged. The action gate holds every outward action on it, with that
sentence. The owner reviews the break in Activity and acknowledges it with a
press on this PC; the acknowledgement is itself a signed entry naming the exact
line (segment, line number, SHA-256 of the line as found). A later change to
that line, or a new break, holds again.

Tests: with ``FRIDAY_TESTING=1`` the directory may be moved with
``FRIDAY_LEDGER_DIR`` (the root conftest gives every test its own). The real app
ignores that variable.
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
#: re-reads every line again (an edit that keeps size and mtime is caught then).
FULL_RECHECK_S = 600.0
#: Problems kept in one report.
MAX_PROBLEMS = 50

_KEY_INFO = b"agent-friday/action-ledger/v1"

_TLOCK = threading.RLock()
_STATE: dict[str, Any] = {
    "dir": None,            # the directory the caches below belong to
    "tip": None,            # (seq, hash, live_size, live_mtime_ns)
    "verified": None,       # cached report + the file stats it covered
    "full_at": 0.0,
    "migrated": False,
}


class LedgerError(RuntimeError):
    """The ledger could not be written (no key, no disk, lock timeout)."""


# ── Paths ───────────────────────────────────────────────────────────────────

def ledger_dir() -> Path:
    if os.environ.get("FRIDAY_TESTING") == "1":
        override = os.environ.get("FRIDAY_LEDGER_DIR", "").strip()
        if override:
            return Path(override)
    from agent_friday.paths import friday_home
    return Path(friday_home()) / "ledger"


def live_path() -> Path:
    return ledger_dir() / LIVE_NAME


def head_path() -> Path:
    return ledger_dir() / HEAD_NAME


def segments() -> list[Path]:
    """Sealed segments, oldest first, then the live file (when present)."""
    d = ledger_dir()
    out = sorted((d / SEGMENTS_DIR).glob("actions-*.jsonl")) if (d / SEGMENTS_DIR).is_dir() else []
    live = d / LIVE_NAME
    if live.exists():
        out.append(live)
    return out


def _seg_label(p: Path) -> str:
    return p.name if p.name == LIVE_NAME else f"{SEGMENTS_DIR}/{p.name}"


def _check_dir() -> Path:
    """Reset the in-process caches when the directory changed (tests, restore)."""
    d = ledger_dir()
    if _STATE["dir"] != str(d):
        _STATE.update(dir=str(d), tip=None, verified=None, full_at=0.0, migrated=False)
    return d


def reset_caches() -> None:
    with _TLOCK:
        _STATE.update(dir=None, tip=None, verified=None, full_at=0.0, migrated=False)


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


# ── Cross-process lock ──────────────────────────────────────────────────────

class _FileLock:
    """An exclusive lock on ledger/.lock shared by every Friday process."""

    def __init__(self, path: Path, timeout: float = 15.0):
        self.path, self.timeout, self.fh = path, timeout, None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+b")
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    self.fh.seek(0)
                    msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.monotonic() > deadline:
                    self.fh.close()
                    self.fh = None
                    raise LedgerError("the ledger is locked by another process")
                time.sleep(0.02)

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


def _read_tip_locked() -> tuple[int, str]:
    """The newest (seq, hash) on disk: the last line that reads as an entry.
    An unreadable or torn line after it is verify()'s to report; the chain
    goes on from the last good one."""
    for p in reversed(segments()):
        skip_first = not _ends_cleanly(p)
        for raw in _reverse_lines(p):
            if skip_first:
                skip_first = False
                continue
            e = _safe_json(raw)
            if isinstance(e, dict) and isinstance(e.get("seq"), int) and isinstance(e.get("hash"), str):
                return e["seq"], e["hash"]
    return 0, GENESIS


def _iter_lines(path: Path) -> Iterator[tuple[Optional[dict], bytes, int]]:
    """(entry or None when unreadable, raw bytes, 1-based line number)."""
    try:
        fh = open(path, "rb")
    except OSError:
        return
    with fh:
        n = 0
        for raw in fh:
            n += 1
            if not raw.endswith(b"\n"):
                yield None, raw, n            # a torn tail
                continue
            raw = raw[:-1]
            if raw.endswith(b"\r"):
                raw = raw[:-1]
            if not raw.strip():
                continue
            try:
                e = json.loads(raw)
                yield (e if isinstance(e, dict) else None), raw, n
            except ValueError:
                yield None, raw, n


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


def _read_head() -> Optional[dict]:
    try:
        h = json.loads(head_path().read_text(encoding="utf-8"))
        return h if isinstance(h, dict) else None
    except (OSError, ValueError):
        return None


def _write_head(key: bytes, seq: int, h: str) -> None:
    p = head_path()
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
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


def _append_locked(key: bytes, kind: str, rec: dict, durable: bool,
                   more: Optional[list] = None) -> dict:
    """Write `(kind, rec)` (and then each of `more`, a list of (kind, rec)) in
    one open of the live file. Caller holds both locks."""
    d = ledger_dir()
    d.mkdir(parents=True, exist_ok=True)
    live = d / LIVE_NAME
    # the tip: from the cache when the live file is exactly as this process
    # left it, otherwise from disk
    tip = _STATE.get("tip")
    try:
        st = live.stat()
        stat_now = (st.st_size, st.st_mtime_ns)
    except OSError:
        stat_now = (0, 0)
    if tip is None or (tip[2], tip[3]) != stat_now:
        torn = _repair_torn_tail_locked(live) if stat_now[0] else None
        seq, prev = _read_tip_locked()
        pending = []
        if torn:
            pending.append(("torn_tail", torn))
        head = _read_head()
        if head and isinstance(head.get("seq"), int) and head["seq"] > seq:
            pending.append(("truncation_detected",
                            {"head_seq": head["seq"], "head_hash": head.get("hash"),
                             "found_seq": seq,
                             "missing": head["seq"] - seq}))
    else:
        seq, prev, pending = tip[0], tip[1], []
    # seal the live file when it is full
    try:
        if live.exists() and live.stat().st_size >= SEGMENT_MAX:
            sd = d / SEGMENTS_DIR
            sd.mkdir(parents=True, exist_ok=True)
            n = len(list(sd.glob("actions-*.jsonl"))) + 1
            _replace(live, sd / f"actions-{n:06d}.jsonl")
    except OSError:
        pass                                   # sealed next time
    out = None
    pre_stats = _stats()
    written = 0
    with open(live, "ab") as f:
        for k, r in pending + [(kind, rec)] + list(more or []):
            written += 1
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
        f.flush()
        if durable:
            os.fsync(f.fileno())
    st = live.stat()
    _STATE["tip"] = (seq, prev, st.st_size, st.st_mtime_ns)
    _write_head(key, seq, prev)
    # Keep a verified result current across this process's own appends, so
    # the hold check does not re-read the whole ledger for each action. Only
    # when nothing else touched the files since that result, and only for
    # ordinary entries: a break flag or an acknowledgement changes the verdict.
    cached = _STATE.get("verified")
    if (cached and cached[0] == pre_stats and not pending
            and kind not in ("integrity_ack",)):
        rep = dict(cached[1])
        rep.update(records=rep.get("records", 0) + written, last_seq=seq, tip=prev)
        _STATE["verified"] = (_stats(), rep)
    else:
        _STATE["verified"] = None
    return out


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
        with _lock():
            return _append_locked(key, kind, rec, durable)


def try_append(kind: str, rec: dict, **kw) -> Optional[dict]:
    """append(), never raising: for records that must not break a turn."""
    try:
        return append(kind, rec, **kw)
    except Exception:
        return None


# ── Reading entries ─────────────────────────────────────────────────────────

def _reverse_lines(path: Path) -> Iterator[bytes]:
    try:
        size = path.stat().st_size
    except OSError:
        return
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


def entries(kinds: Optional[Iterable[str]] = None, *, newest_first: bool = True,
            limit: Optional[int] = None,
            where: Optional[Callable[[dict], bool]] = None) -> list[dict]:
    """Entries (whole lines), optionally filtered by kind and a predicate.
    Never raises; an unreadable line is skipped here (verify() reports it)."""
    want = set(kinds) if kinds else None
    out: list[dict] = []
    try:
        with _TLOCK:
            _check_dir()
            _ensure_migrated()
        segs = segments()
        order = reversed(segs) if newest_first else segs
        for p in order:
            if newest_first:
                it = (_safe_json(raw) for raw in _reverse_lines(p))
            else:
                it = (e for e, _r, _n in _iter_lines(p))
            for e in it:
                if not isinstance(e, dict):
                    continue
                if want is not None and e.get("kind") not in want:
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
    except Exception:
        return out
    return out


def records(kind: str, *, newest_first: bool = False, limit: Optional[int] = None) -> list[dict]:
    """The `rec` of every entry of one kind, each with `_seq` and `_ts` added."""
    out = []
    for e in entries([kind], newest_first=newest_first, limit=limit):
        r = e.get("rec")
        if isinstance(r, dict):
            out.append({**r, "_seq": e.get("seq"), "_ts": e.get("ts")})
    return out


def _safe_json(raw: bytes):
    try:
        return json.loads(raw)
    except ValueError:
        return None


# ── Verification ────────────────────────────────────────────────────────────

def _problem(seg: Path, n: int, raw: bytes, reason: str, seq=None) -> dict:
    return {"segment": _seg_label(seg), "line": n, "seq": seq, "reason": reason,
            "raw_sha256": hashlib.sha256(raw).hexdigest()}


def _verify_files(key: Optional[bytes]) -> dict:
    """Walk every line of every segment. The whole report, uncached."""
    kid = key_id(key) if key else None
    problems: list[dict] = []
    total, prev, last_seq = 0, GENESIS, 0
    acks: list[dict] = []
    flagged: list[dict] = []              # truncation_detected / torn_tail entries
    foreign = 0
    for p in segments():
        for e, raw, n in _iter_lines(p):
            if e is None:
                problems.append(_problem(p, n, raw, "unreadable line"))
                continue
            total += 1
            seq = e.get("seq")
            reasons = []
            if not isinstance(seq, int) or seq != last_seq + 1:
                reasons.append("out of order (expected record %d, found %s)" % (last_seq + 1, seq))
            if e.get("prev") != prev:
                reasons.append("does not follow the record before it")
            try:
                if body_hash(e) != e.get("hash"):
                    reasons.append("changed after it was written")
            except Exception:
                reasons.append("cannot be read as a record")
            if key is not None:
                if e.get("kid") != kid:
                    foreign += 1
                    reasons.append("signed with a key this device does not hold")
                elif not _hmac.compare_digest(_sign(key, str(e.get("hash"))), str(e.get("sig"))):
                    reasons.append("signature does not match")
            for r in reasons:
                problems.append(_problem(p, n, raw, r, seq if isinstance(seq, int) else None))
            if isinstance(seq, int):
                last_seq = seq
            prev = e.get("hash") if isinstance(e.get("hash"), str) else prev
            if not reasons and e.get("kind") == "integrity_ack":
                acks.append(e.get("rec") or {})
            if not reasons and e.get("kind") in ("truncation_detected", "torn_tail"):
                flagged.append(e)
    # a head ahead of the file: lines cut off the end since the last append
    head = _read_head()
    if head and isinstance(head.get("seq"), int):
        if key is not None and head.get("kid") == kid and not _hmac.compare_digest(
                _head_sig(key, head["seq"], str(head.get("hash"))), str(head.get("sig"))):
            problems.append({"segment": HEAD_NAME, "line": 0, "seq": head.get("seq"),
                             "reason": "the ledger head was changed",
                             "raw_sha256": hashlib.sha256(canon(head)).hexdigest()})
        elif head["seq"] > last_seq:
            problems.append({"segment": HEAD_NAME, "line": 0, "seq": head["seq"],
                             "reason": "%d record(s) missing from the end" % (head["seq"] - last_seq),
                             "raw_sha256": hashlib.sha256(canon(head)).hexdigest()})
    for e in flagged:
        r = e.get("rec") or {}
        reason = ("%s record(s) were missing from the end when Friday next wrote" % r.get("missing")
                  if e.get("kind") == "truncation_detected"
                  else "an unfinished write was set aside (%s bytes)" % r.get("bytes"))
        problems.append({"segment": "entry", "line": 0, "seq": e.get("seq"),
                         "reason": reason, "raw_sha256": e.get("hash")})
    acked = {(a.get("segment"), a.get("line"), a.get("raw_sha256"), a.get("reason"))
             for ack in acks for a in (ack.get("problems") or []) if isinstance(a, dict)}
    for pr in problems:
        pr["acknowledged"] = (pr["segment"], pr["line"], pr["raw_sha256"], pr["reason"]) in acked
    open_problems = [pr for pr in problems if not pr["acknowledged"]]
    return {
        "records": total, "last_seq": last_seq, "tip": prev,
        "key_available": key is not None,
        "intact": not problems,
        "ok": not open_problems,
        "problems": problems[:MAX_PROBLEMS],
        "problem_count": len(problems),
        "open_problem_count": len(open_problems),
        "foreign_records": foreign,
    }


def _stats() -> tuple:
    out = []
    for p in segments():
        try:
            st = p.stat()
            out.append((p.name, st.st_size, st.st_mtime_ns))
        except OSError:
            out.append((p.name, -1, -1))
    try:
        st = head_path().stat()
        out.append((HEAD_NAME, st.st_size, st.st_mtime_ns))
    except OSError:
        pass
    return tuple(out)


def verify(*, force: bool = True) -> dict:
    """Check every line: order, links, hashes, signatures, the head, and which
    breaks the owner has acknowledged. `force=False` reuses the last result
    while no ledger file changed and it is younger than FULL_RECHECK_S."""
    with _TLOCK:
        _check_dir()
        _ensure_migrated()
        stats = _stats()
        cached = _STATE.get("verified")
        fresh = time.time() - _STATE.get("full_at", 0.0) < FULL_RECHECK_S
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
        _STATE["verified"] = (stats, report)
        _STATE["full_at"] = time.time()
        return dict(report)


def hold_reason() -> Optional[str]:
    """A sentence when an outward action must wait for the owner to look at a
    break in the ledger; None while it is intact (or every break is
    acknowledged). A ledger that cannot be checked at all holds too."""
    try:
        r = verify(force=False)
    except Exception as e:  # noqa: BLE001 - any failure to check holds
        return "the action ledger could not be checked (%s)" % type(e).__name__
    if r.get("ok"):
        return None
    first = next((p for p in r.get("problems", []) if not p.get("acknowledged")), {})
    return ("the action ledger was changed on disk (%s, record %s: %s); "
            "open Activity to review it" % (first.get("segment"), first.get("seq"),
                                            first.get("reason")))


def acknowledge(by: str, note: str = "") -> dict:
    """Record that the owner has seen every open break. The caller has checked
    that this is the owner's press on this PC."""
    total = 0
    r = verify(force=True)
    # Twice at most: writing the acknowledgement can itself first record a
    # break found on the way in (records cut off the end), which the owner is
    # acknowledging by the same press.
    for _round in range(2):
        open_ = [{k: p.get(k) for k in ("segment", "line", "raw_sha256", "reason", "seq")}
                 for p in r.get("problems", []) if not p.get("acknowledged")]
        if not open_:
            break
        append("integrity_ack", {"problems": open_, "by": str(by)[:40],
                                 "note": str(note)[:200]}, durable=True)
        total += len(open_)
        r = verify(force=True)
    return {"acknowledged": total, "report": r}


def summary() -> dict:
    """What Activity and the Doctor show: counts and state, no content."""
    r = verify(force=False)
    return {k: r.get(k) for k in ("records", "last_seq", "intact", "ok", "key_available",
                                  "problem_count", "open_problem_count", "verify_ms",
                                  "checked_at", "foreign_records")}


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


def _migrated_counts_locked() -> dict:
    counts: dict[str, int] = {}
    done: set = set()
    for p in segments():
        for e, _raw, _n in _iter_lines(p):
            if not isinstance(e, dict):
                continue
            r = e.get("rec") or {}
            if e.get("kind") == "migration":
                if r.get("file_sha256"):
                    done.add(r.get("file_sha256"))
                continue
            m = r.get("_migrated") if isinstance(r, dict) else None
            if isinstance(m, dict) and m.get("file_sha256"):
                counts[m["file_sha256"]] = counts.get(m["file_sha256"], 0) + 1
    return {"counts": counts, "done": done}


def _file_sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def migrate_legacy() -> dict:
    """Bring every old receipt file into the ledger, once. Each legacy line
    becomes an entry of its kind with `_migrated` (where it came from, its line
    number, whether its own signature held). The old file is then moved, whole
    and unchanged, to ledger/legacy/, and a `migration` entry records its
    SHA-256 so the original can still be checked against the chain.
    Resumable: a file half imported when Friday stopped continues from the
    first line not yet in the ledger. Idempotent: a file already done is
    recognised by its digest."""
    key = _key()
    moved = []
    with _TLOCK:
        _check_dir()
        with _lock():
            state = _migrated_counts_locked()
            legacy_dir = ledger_dir() / "legacy"
            for kind, paths in _legacy_stores():
                for p in paths:
                    if not p.is_file():
                        continue
                    digest = _file_sha256(p)
                    if digest in state["done"]:
                        _move_legacy(p, legacy_dir, digest)
                        continue
                    already = state["counts"].get(digest, 0)
                    n_lines = n_imported = n_unreadable = 0
                    batch: list = []
                    for _e, raw, n in _iter_lines(p):
                        n_lines += 1
                        try:
                            rec = json.loads(raw)
                        except ValueError:
                            rec = None
                        if not isinstance(rec, dict):
                            n_unreadable += 1
                            rec = {"unreadable": True,
                                   "raw_sha256": hashlib.sha256(raw).hexdigest()}
                        if n_lines <= already:
                            continue
                        rec = dict(rec)
                        rec["_migrated"] = {"from": _rel(p), "line": n,
                                            "file_sha256": digest,
                                            "legacy_verified": _legacy_check(kind, rec)}
                        batch.append((kind, rec))
                        n_imported += 1
                        if len(batch) >= 2000:
                            _append_locked(key, batch[0][0], batch[0][1], False, batch[1:])
                            batch = []
                    if batch:
                        _append_locked(key, batch[0][0], batch[0][1], False, batch[1:])
                    kept = _move_legacy(p, legacy_dir, digest)
                    _append_locked(key, "migration", {
                        "store": kind, "from": _rel(p), "file_sha256": digest,
                        "lines": n_lines, "imported": n_imported + already,
                        "unreadable": n_unreadable, "kept_as": kept}, durable=True)
                    moved.append({"store": kind, "from": _rel(p), "lines": n_lines})
            _STATE["migrated"] = True
    return {"migrated": moved}


def _rel(p: Path) -> str:
    try:
        from agent_friday.paths import friday_home
        return Path(p).resolve().relative_to(Path(friday_home()).resolve()).as_posix()
    except Exception:
        return Path(p).name


def _move_legacy(p: Path, legacy_dir: Path, digest: str) -> str:
    legacy_dir.mkdir(parents=True, exist_ok=True)
    dst = legacy_dir / f"{digest[:12]}-{p.name}"
    try:
        if dst.exists():
            # the same bytes are already kept; this copy is the same file
            if _file_sha256(dst) == digest:
                os.remove(p)
                return f"legacy/{dst.name}"
            dst = legacy_dir / f"{digest[:12]}-{int(time.time())}-{p.name}"
        _replace(p, dst)
    except OSError:
        return _rel(p)                      # left where it was; tried again next time
    return f"legacy/{dst.name}"


def _ensure_migrated() -> None:
    """Called under _TLOCK by every read and write: the first use in a process
    imports anything still in an old file."""
    if _STATE.get("migrated"):
        return
    _STATE["migrated"] = True               # set first: migrate_legacy appends
    try:
        if any(p.is_file() for _k, ps in _legacy_stores() for p in ps):
            migrate_legacy()
    except Exception:
        _STATE["migrated"] = False          # try again on the next use
