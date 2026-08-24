"""forensics-snapshot.py — capture Friday's volatile state to disk. One shot.

WHY THIS EXISTS
---------------
Two of Friday's registries are plain in-memory dicts:

    PROCESSES  core/__init__.py:1209     the orbs, with their tool traces
    TASKS      services/agent.py:1995    background tasks

A restart erases both. On 2026-08-24 three restarts inside twenty minutes
destroyed the entire record of a six-task workflow run while it was being
investigated (docs/audits/workflow-run-forensics-2026-08-24.md). The ledger
survived because it is append-only on disk; everything about WHAT each agent
actually did did not.

This captures the volatile registries, and copies the durable-but-rotatable
files (friday.log rotates at 10 MB; settings.json has been factory-reset once
already) into ~/.friday/forensics/ where nothing overwrites them.

DESIGN CONSTRAINTS, and how each is met
---------------------------------------
* READ-ONLY against Friday. Two HTTP GETs on 127.0.0.1 and file reads. No
  writes anywhere under ~/.friday except inside forensics/. No locks are held
  on Friday's files: copies are read-and-close, never opened for write.
* SAFE ALONGSIDE A LIVE FRIDAY. Nothing here restarts, signals, or evicts
  anything. If the server is down the HTTP half is skipped and the file half
  still runs, so a snapshot during downtime is partial rather than failed.
* ONE SHOT, NOT A LOOP. The scheduler owns the cadence. A crashed run is
  replaced by the next tick instead of leaving a dead daemon behind.
* IDEMPOTENT. A PID lock file with a staleness timeout means overlapping runs
  cannot interleave writes; the loser exits 0 silently. Appends are deduped by
  content hash, so re-running over the same state adds nothing.
* BOUNDED. JSONL files rotate at MAX_JSONL_BYTES; file snapshots keep the most
  recent KEEP_PER_SOURCE; the dedupe index is capped and pruned by last-seen.

Usage:
    python ops/forensics-snapshot.py            # capture once
    python ops/forensics-snapshot.py --status   # what has been captured
    python ops/forensics-snapshot.py --verbose  # say what it did

Registered on a schedule by ops/forensics-install.ps1; removed by
ops/forensics-down.ps1.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:3000"
FRIDAY = Path(os.environ.get("USERPROFILE") or Path.home()) / ".friday"
OUT = FRIDAY / "forensics"
STATE = OUT / ".state.json"
LOCK = OUT / ".lock"
TOKEN_CACHE = OUT / ".token-cache"
LOG = OUT / "snapshot.log"

# --- bounds -----------------------------------------------------------------
#
# WORST CASE ON DISK, stated rather than hand-waved: four append streams
# (orbs, tasks, friday.log.tail, ledger.tail) at MAX_JSONL_BYTES each plus one
# retained rotation = 8 x 32 MB = 256 MB, plus the dated copies, which are the
# small rewritten-wholesale files: chat_history (~0.3 MB) x KEEP_PER_SOURCE,
# settings and its six backups (~7 KB each), seat_state, and the workflows
# directory (~30 KB). Call it 260 MB absolute ceiling.
#
# Realistically far less: friday.log grows ~6 MB/day and the ledger ~0.5 MB/day
# on this machine, so the streams take weeks to reach a rotation.
MAX_JSONL_BYTES = 32 * 1024 * 1024   # per stream; rotates to .1, older dropped
KEEP_PER_SOURCE = 12                 # dated copies retained per source file
MAX_INDEX_ENTRIES = 5000             # dedupe index cap, pruned by last-seen
LOCK_STALE_S = 900                   # a lock older than this is a dead run
HTTP_TIMEOUT_S = 30

# APPEND-ONLY sources are captured as a DELTA, not as a copy.
#
# friday.log is ~6 MB and grows on essentially every run. Copying it whole
# every tick would write ~180 MB/hour to disk to preserve a few kilobytes of
# new lines — pointless churn, and it would blow the retention budget on one
# file. Instead we append only the bytes past the last offset into a single
# growing tail file, which rotates like the JSONL streams.
#
# Rotation and truncation in the SOURCE are detected by re-reading the first
# HEAD_PROBE_BYTES: friday.log rotates to friday.log.1 at 10 MB, and a naive
# offset would then silently skip everything after the rollover.
APPEND_FILES = [
    ("friday.log", "friday.log.tail"),
    ("activity_ledger.jsonl", "ledger.tail"),
]
HEAD_PROBE_BYTES = 4096

# Rewritten-wholesale files, small enough that a dated copy is the honest
# thing to keep. Copied only when the content hash changes, so a quiet hour
# costs nothing.
FILES = [
    ("chat_history.json", "chat_history"),
    ("settings.json", "settings"),
    ("seat_state.json", "seat_state"),
]
# Every settings backup, by glob. settings.json.bak-preheal is the only
# surviving copy of the configuration the 13:08:58 factory reset destroyed.
FILE_GLOBS = [("settings.json.bak*", "settings-bak")]
DIRS = [("workflows", "workflows")]

VERBOSE = "--verbose" in sys.argv


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.0f%s" % (n, unit) if unit == "B" else "%.1f%s" % (n, unit)
        n /= 1024.0
    return str(n)


def log(msg: str) -> None:
    line = "%s %s" % (time.strftime("%Y-%m-%dT%H:%M:%S"), msg)
    if VERBOSE:
        print(line)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ── lock ────────────────────────────────────────────────────────────────────
def acquire_lock() -> bool:
    """True if we own the run. Stale locks (dead scheduler tick) are reclaimed."""
    try:
        if LOCK.exists():
            age = time.time() - LOCK.stat().st_mtime
            if age < LOCK_STALE_S:
                return False
            log("reclaiming stale lock (%.0fs old)" % age)
        LOCK.write_text(str(os.getpid()), encoding="utf-8")
        return True
    except Exception as e:
        log("lock error: %s" % e)
        return False


def release_lock() -> None:
    try:
        LOCK.unlink()
    except Exception:
        pass


# ── state ───────────────────────────────────────────────────────────────────
def load_state() -> dict:
    try:
        # utf-8-sig: this project has been bitten once by a BOM on a JSON file
        # it wrote itself. Costs nothing to be tolerant here.
        return json.loads(STATE.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def save_state(st: dict) -> None:
    for section in ("orbs", "tasks"):
        idx = st.get(section) or {}
        if len(idx) > MAX_INDEX_ENTRIES:
            # Keep the most recently seen. The index is a dedupe aid, not a
            # record; dropping an old id costs at most one duplicate append.
            keep = sorted(idx.items(), key=lambda kv: kv[1].get("seen", 0),
                          reverse=True)[:MAX_INDEX_ENTRIES]
            st[section] = dict(keep)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st), encoding="utf-8")
    tmp.replace(STATE)


# ── http ────────────────────────────────────────────────────────────────────
def token(force: bool = False) -> str | None:
    """The API session token, cached. Scraped from the served HTML.

    Cached because the page is ~1.5 MB and this runs on a short interval;
    re-scraped on the first 401/403. The token is loopback-only, rotates every
    24 h and on every restart, and the cache lives beside the capture in the
    user's own profile.
    """
    if not force:
        try:
            v = TOKEN_CACHE.read_text(encoding="utf-8").strip()
            if v:
                return v
        except Exception:
            pass
    try:
        html = urllib.request.urlopen(
            BASE + "/", timeout=HTTP_TIMEOUT_S).read().decode("utf-8", "replace")
    except Exception as e:
        log("server unreachable while minting a token: %s" % str(e)[:120])
        return None
    m = re.search(r'__FRIDAY_API_TOKEN\s*=\s*[\x27"]([0-9a-fA-F]{16,})[\x27"]',
                  html)
    if not m:
        log("no token in served HTML")
        return None
    try:
        TOKEN_CACHE.write_text(m.group(1), encoding="utf-8")
    except Exception:
        pass
    return m.group(1)


def get_json(ep: str):
    """GET an endpoint, re-minting the token once on an auth failure."""
    for attempt in (0, 1):
        tok = token(force=bool(attempt))
        if not tok:
            return None
        try:
            rq = urllib.request.Request(BASE + ep, headers={"X-Friday-Token": tok})
            return json.loads(urllib.request.urlopen(
                rq, timeout=HTTP_TIMEOUT_S).read())
        except urllib.error.HTTPError as e:
            if e.code in (401, 403) and attempt == 0:
                continue            # token rotated or the server restarted
            log("%s HTTP %s" % (ep, e.code))
            return None
        except Exception as e:
            log("%s unreachable: %s" % (ep, str(e)[:120]))
            return None
    return None


def rows_of(payload, *keys) -> list:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in keys:
            if isinstance(payload.get(k), list):
                return payload[k]
    return []


# ── capture: the volatile registries ────────────────────────────────────────
def rotate(path: Path) -> None:
    try:
        if path.exists() and path.stat().st_size > MAX_JSONL_BYTES:
            old = path.with_suffix(path.suffix + ".1")
            if old.exists():
                old.unlink()        # only one generation kept, by design
            path.replace(old)
            log("rotated %s" % path.name)
    except Exception as e:
        log("rotate %s failed: %s" % (path.name, e))


def capture_registry(st: dict, ep: str, name: str, idkey: str) -> int:
    payload = get_json(ep)
    if payload is None:
        return 0
    idx = st.setdefault(name, {})
    path = OUT / ("%s.jsonl" % name)
    rotate(path)
    now = time.time()
    written = 0
    lines = []
    for r in rows_of(payload, name, "processes", "tasks"):
        rid = str(r.get(idkey) or r.get("id") or "")
        blob = json.dumps(r, sort_keys=True, default=str)
        h = hashlib.sha1(blob.encode("utf-8")).hexdigest()
        prev = idx.get(rid)
        if prev and prev.get("h") == h:
            idx[rid]["seen"] = now          # unchanged; just touch last-seen
            continue
        idx[rid] = {"h": h, "seen": now}
        lines.append(json.dumps({"captured_at": now, "record": r},
                                default=str))
        written += 1
    if lines:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    return written


# ── capture: the durable-but-rotatable files ────────────────────────────────
def file_hash(p: Path) -> str | None:
    """SHA-1 of a file, read in chunks and closed immediately.

    Read-only and non-exclusive on purpose: Friday may be appending to
    friday.log or replacing settings.json while this runs, and a partial read
    of a growing log is an acceptable snapshot. Holding a lock would not be.
    """
    try:
        h = hashlib.sha1()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def glob_label(base: str, src: Path) -> str:
    """The dated-copy label for a globbed source. One definition, two callers.

    `settings.json.bak-preheal` -> `settings-bak-preheal`; the bare
    `settings.json.bak` -> `settings-bak-plain`, because an empty suffix would
    collide with the glob's own prefix and silently prune the others.
    """
    tail = src.name.split("bak", 1)[-1].lstrip("-.")
    return "%s-%s" % (base, tail or "plain")


def dated_labels() -> list:
    """Every label this script writes dated copies under."""
    labels = [lbl for _, lbl in FILES] + [lbl for _, lbl in DIRS]
    for pattern, base in FILE_GLOBS:
        labels += [glob_label(base, s) for s in sorted(FRIDAY.glob(pattern))]
    return sorted(set(labels))


def stream_names() -> set:
    """Every append-stream file, including its one retained rotation."""
    out = {"orbs.jsonl", "tasks.jsonl", "snapshot.log"}
    out |= {lbl for _, lbl in APPEND_FILES}
    return out | {n + ".1" for n in out}


def stray_files() -> list:
    """Files sitting in the capture directory that this script did not write.

    Worth naming rather than hiding: someone (including a past me) dropping
    ad-hoc copies here is fine, but a status report that silently folds them
    into its own totals is how a capture starts lying about its coverage.
    """
    labels = dated_labels()
    streams = stream_names()
    out = []
    for p in OUT.glob("*"):
        if p.name.startswith((".", "_")):
            continue
        if p.name in streams:
            continue
        if any(p.name.startswith(l + ".") for l in labels):
            continue
        out.append(p.name)
    return out


def prune(prefix: str) -> None:
    keep = sorted(OUT.glob(prefix + ".*"), key=lambda p: p.name, reverse=True)
    for p in keep[KEEP_PER_SOURCE:]:
        try:
            shutil.rmtree(p) if p.is_dir() else p.unlink()
        except Exception:
            pass


def capture_file(st: dict, src: Path, label: str, stamp: str) -> bool:
    if not src.exists():
        return False
    h = file_hash(src)
    if h is None:
        return False
    idx = st.setdefault("files", {})
    if idx.get(label) == h:
        return False                        # unchanged since the last capture
    # Keep the real extension so the copy still opens in the right tool, but
    # only when it IS one: `settings.json.bak-preheal` has a Path.suffix of
    # ".bak-preheal", and echoing that produced
    # `settings-bak-preheal.<stamp>.bak-preheal`.
    ext = src.suffix if re.fullmatch(r"\.[A-Za-z0-9]{1,5}", src.suffix or "") else ""
    dest = OUT / ("%s.%s%s" % (label, stamp, ext))
    try:
        shutil.copy2(src, dest)
    except Exception as e:
        log("copy %s failed: %s" % (src.name, e))
        return False
    idx[label] = h
    prune(label)
    return True


def capture_append(st: dict, src: Path, label: str) -> int:
    """Append only the bytes added since last time. Returns bytes captured.

    Reads with a plain open-and-close: no lock, no exclusive mode, so Friday
    can keep writing to the source throughout. A torn final line is possible
    and acceptable — the next run resumes from wherever this one stopped, so
    nothing is lost, at worst one line is split across two captures.
    """
    if not src.exists():
        return 0
    idx = st.setdefault("append", {})
    prev = idx.get(label) or {}
    try:
        size = src.stat().st_size
        with open(src, "rb") as f:
            head = hashlib.sha1(f.read(HEAD_PROBE_BYTES)).hexdigest()
            # Source rotated (friday.log -> friday.log.1) or was truncated:
            # the head changed, or it shrank. Either way the old offset is
            # meaningless and starting over is the only correct move.
            start = prev.get("offset", 0)
            if head != prev.get("head") or size < start:
                if prev:
                    log("%s: source rotated or truncated, restarting from 0"
                        % src.name)
                start = 0
            if size <= start:
                idx[label] = {"offset": size, "head": head}
                return 0
            f.seek(start)
            chunk = f.read(size - start)
    except Exception as e:
        log("append %s failed: %s" % (src.name, e))
        return 0
    out = OUT / label
    rotate(out)
    try:
        with open(out, "ab") as f:
            f.write(chunk)
    except Exception as e:
        log("write %s failed: %s" % (out.name, e))
        return 0
    idx[label] = {"offset": size, "head": head}
    return len(chunk)


def capture_dir(st: dict, src: Path, label: str, stamp: str) -> bool:
    if not src.is_dir():
        return False
    parts = []
    for f in sorted(src.rglob("*")):
        if f.is_file():
            parts.append("%s:%s" % (f.name, file_hash(f)))
    h = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    idx = st.setdefault("files", {})
    if idx.get(label) == h:
        return False
    dest = OUT / ("%s.%s" % (label, stamp))
    try:
        shutil.copytree(src, dest)
    except Exception as e:
        log("copytree %s failed: %s" % (src, e))
        return False
    idx[label] = h
    prune(label)
    return True


# ── status ──────────────────────────────────────────────────────────────────
def status() -> int:
    if not OUT.exists():
        print("no capture yet at %s" % OUT)
        return 1
    total = 0
    for p in sorted(OUT.rglob("*")):
        if p.is_file():
            total += p.stat().st_size
    print("forensics capture: %s" % OUT)
    print("  total size      : %.1f MB" % (total / 1048576))
    for name in ("orbs", "tasks"):
        p = OUT / ("%s.jsonl" % name)
        if p.exists():
            n = sum(1 for _ in open(p, encoding="utf-8", errors="replace"))
            print("  %-16s: %d records, %.1f MB" % (
                name, n, p.stat().st_size / 1048576))
    for _, label in APPEND_FILES:
        p = OUT / label
        if p.exists():
            print("  %-16s: %s captured" % (label, _human(p.stat().st_size)))
    # Count by the labels this script actually writes. Deriving them here by
    # splitting filenames produced two wrong answers at once — it called
    # `friday.log.<stamp>` a source named "friday", and it counted files
    # dropped in this directory by hand as sources of their own. A status line
    # that invents categories is worse than no status line, so both status and
    # main() now ask `dated_labels()`, and there is one definition to be wrong.
    for label in dated_labels():
        n = len(list(OUT.glob(label + ".*")))
        if n:
            print("  %-16s: %d dated copies" % (label, n))
    print("  (ceiling %d MB: %d streams x %d MB x 2 generations + dated copies)"
          % (((2 + len(APPEND_FILES)) * MAX_JSONL_BYTES * 2) // 1048576 + 4,
             2 + len(APPEND_FILES), MAX_JSONL_BYTES // 1048576))
    for name in sorted(stray_files()):
        print("  not ours       : %s" % name)
    try:
        st = load_state()
        print("  last run        : %s" % time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(st.get("last_run", 0))))
    except Exception:
        pass
    return 0


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    if "--status" in sys.argv:
        return status()
    if not acquire_lock():
        log("another run holds the lock; skipping")
        return 0
    try:
        st = load_state()
        stamp = time.strftime("%Y%m%dT%H%M%S")
        n_orbs = capture_registry(st, "/api/processes", "orbs", "id")
        n_tasks = capture_registry(st, "/api/tasks", "tasks", "task_id")
        appended = 0
        for fname, label in APPEND_FILES:
            appended += capture_append(st, FRIDAY / fname, label)
        copied = []
        for fname, label in FILES:
            if capture_file(st, FRIDAY / fname, label, stamp):
                copied.append(label)
        for pattern, label_base in FILE_GLOBS:
            for src in sorted(FRIDAY.glob(pattern)):
                label = glob_label(label_base, src)
                if capture_file(st, src, label, stamp):
                    copied.append(label)
        for dname, label in DIRS:
            if capture_dir(st, FRIDAY / dname, label, stamp):
                copied.append(label)
        st["last_run"] = time.time()
        save_state(st)
        log("orbs +%d, tasks +%d, tail +%s, files [%s]"
            % (n_orbs, n_tasks, _human(appended),
               ", ".join(copied) or "none changed"))
        return 0
    finally:
        release_lock()


if __name__ == "__main__":
    raise SystemExit(main())
