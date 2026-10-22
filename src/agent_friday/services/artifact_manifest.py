"""The artifact manifest (north star §34.12-2): which task made which file,
under which receipt, and whether it checked out.

Every file a creating tool makes through the agent (a document, a deck, a
page, a filled form, a picture, a saved file) gets one signed line in
~/.friday/artifact-manifest.jsonl:

  kind "artifact"           so a line is never mistaken for anything else
  path, sha256, bytes       the file as it was when the tool finished
  tool, task_id, turn_id    what made it, for which task and turn
  args_hash                 the call's arguments, hashed as the gate hashes them
  receipt                   the action-gate receipt for exactly this call
                            (same tool, same arguments, same turn or task) by
                            its HMAC, or null when none matches: never a guess
  provenance                True when the file's own signed provenance
                            sidecar exists (services/provenance.py)
  qa                        artifact_qa.check: ok and the findings

The line is signed with a key derived from the governance key for this
purpose only, so a manifest line never verifies as a receipt or the other
way round. Nothing is written while off the record. The agent hands calls
to `submit`, which records them on a background thread: hashing a long
video or checking a big PDF never holds up the conversation.

`lookup(path)` gives the latest verified line for a file whose bytes still
match, `receipt_of(line)` the receipt it names (verified), `verify(line)`
checks a line.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import os
import queue
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

#: The tools that make a file the owner keeps, and how to find its path.
CREATING_TOOLS = frozenset({
    "write_file", "office", "create_presentation", "create_website", "fill_pdf_form",
    "generate_image", "generate_video", "generate_music", "compose_timeline", "save_output",
    "speak_text",
})
#: The gate decisions under which a call can have run: allowed outright, or
#: put to the owner (a chat confirmation or a card) and then carried out.
RAN_UNDER = ("allow", "confirm", "card")
#: Files bigger than this are listed without a hash (a long video).
MAX_HASH_BYTES = 512 * 1024 * 1024
#: How far back in the receipts file to look for the call's receipt.
_RECEIPT_TAIL_BYTES = 1024 * 1024
_LOCK = threading.Lock()
_DOMAIN = b"agent-friday/artifact-manifest/v1"


def manifest_path() -> Path:
    from agent_friday.paths import friday_home
    return Path(friday_home()) / "artifact-manifest.jsonl"


# ── which files a call made ────────────────────────────────────────────────

def _json(result: str) -> Optional[dict]:
    try:
        v = json.loads(result)
        return v if isinstance(v, dict) else None
    except Exception:
        return None


def written_paths(name: str, args: Any, result: Any) -> List[Path]:
    """The files a successful call of a creating tool says it wrote. Only
    what the tool itself reports (or, for write_file and office, the path the
    call named, resolved the way the tool resolves it)."""
    if name not in CREATING_TOOLS or not isinstance(result, str):
        return []
    args = args if isinstance(args, dict) else {}
    out: List[str] = []
    if name == "write_file":
        if result.startswith(("Wrote ", "Appended ")):
            # The tool reports the path it resolved ("Wrote 12 chars to C:\\...").
            first = result.split("\n", 1)[0]
            m = re.match(r"(?:Wrote|Appended) \d+ chars to (.+)$", first)
            if m:
                out.append(m.group(1).strip())
            elif args.get("path"):
                out.append(str(args["path"]))
    elif name == "office":
        if result.startswith(("office failed", "office refused")):
            return []
        try:
            from agent_friday.services import office_engine as oe
            argv = oe.split_command(args.get("command"))
            if oe.verb_of(argv) in oe.WRITE_VERBS:
                out += [str(oe.resolve_in_workspace(a)) for a in oe.file_args(argv)]
        except Exception:
            return []
    elif name == "save_output":
        m = re.fullmatch(r"saved: (.+) \([\d.]+ KB\)", result.split("\n", 1)[0])
        if m:
            out.append(m.group(1))
    elif name == "speak_text":
        m = re.search(r" -> (.+) \([\d.]+ KB, ", result.split("\n", 1)[0][:2000])
        if m:
            out.append(m.group(1))
    else:
        data = _json(result)
        if data and str(data.get("status", "ok")) in ("ok", "demo"):
            for f in data.get("files") or []:
                if isinstance(f, dict) and f.get("path"):
                    out.append(str(f["path"]))
            if name == "fill_pdf_form" and data.get("output"):
                out.append(str(data["output"]))
    seen, paths = set(), []
    for s in out:
        try:
            p = Path(s).expanduser().resolve()
        except Exception:
            continue
        if str(p) not in seen:
            seen.add(str(p))
            paths.append(p)
    return paths


# ── signing ────────────────────────────────────────────────────────────────

def _key() -> bytes:
    """A key for this file only, derived from the governance key, so a
    manifest line and a receipt can never stand in for each other."""
    from agent_friday.governance.action_gate import _governance_key
    return _hmac.new(_governance_key(), _DOMAIN, hashlib.sha256).digest()


def _canonical(entry: dict) -> bytes:
    body = {k: v for k, v in entry.items() if k not in ("hmac", "verified")}
    return json.dumps(body, sort_keys=True, default=str).encode("utf-8")


def _sign(entry: dict, key: Optional[bytes] = None) -> dict:
    body = {k: v for k, v in entry.items() if k != "hmac"}
    return dict(body, hmac=_hmac.new(key or _key(), _canonical(body), hashlib.sha256).hexdigest())


def verify(entry: Any, key: Optional[bytes] = None) -> bool:
    """True when the line is a manifest line whose HMAC matches its content."""
    if not isinstance(entry, dict) or entry.get("kind") != "artifact" or not isinstance(entry.get("hmac"), str):
        return False
    try:
        want = _hmac.new(key or _key(), _canonical(entry), hashlib.sha256).hexdigest()
    except Exception:
        return False
    return _hmac.compare_digest(want, entry["hmac"])


# ── the receipt behind the call ────────────────────────────────────────────

def args_hash(args: Any) -> Optional[str]:
    """The arguments hashed exactly as action_gate.authorize hashes them."""
    try:
        return hashlib.sha256(json.dumps(args or {}, sort_keys=True, default=str).encode()).hexdigest()
    except Exception:
        return None


def _tail_lines(path: Path, limit: int) -> List[str]:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - limit))
            data = f.read()
    except OSError:
        return []
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[1:] if size > limit else lines


def _find_receipt(tool: str, task_id: Optional[str], turn_id: Optional[str],
                  ahash: Optional[str], receipts: Optional[Path] = None,
                  gov_key: Optional[bytes] = None) -> Optional[dict]:
    """The newest verified receipt for exactly this call: the same tool and
    arguments, in the same turn (or, outside a turn, the same task). With no
    arguments hash or no turn or task to tie it to, there is no receipt."""
    if not ahash or not (turn_id or task_id):
        return None
    try:
        from agent_friday.governance import action_gate as ag
        lines = _tail_lines(receipts or ag.receipts_path(), _RECEIPT_TAIL_BYTES)
    except Exception:
        return None
    for line in reversed(lines):
        if ahash not in line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if not isinstance(r, dict) or r.get("tool") != tool or r.get("decision") not in RAN_UNDER:
            continue
        if r.get("args_hash") != ahash:
            continue
        if (turn_id and r.get("turn_id") != turn_id) or (not turn_id and r.get("task_id") != task_id):
            continue
        if ag.verify_receipt(r, key=gov_key):
            return r
    return None


def receipt_of(entry: dict) -> Optional[dict]:
    """The verified receipt a manifest line names, or None."""
    want = ((entry or {}).get("receipt") or {}).get("hmac")
    if not want:
        return None
    try:
        from agent_friday.governance import action_gate as ag
        with open(ag.receipts_path(), encoding="utf-8") as f:
            for line in f:
                if want in line:
                    r = json.loads(line)
                    if r.get("hmac") == want and ag.verify_receipt(r):
                        return r
    except Exception:
        return None
    return None


# ── recording ──────────────────────────────────────────────────────────────

def _sha256(p: Path) -> Optional[str]:
    if p.stat().st_size > MAX_HASH_BYTES:
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _has_provenance(sha: Optional[str]) -> bool:
    if not sha:
        return False
    try:
        from agent_friday.services import provenance
        return provenance._sidecar_path("sha256:" + sha).exists() or provenance._sidecar_path(sha).exists()
    except Exception:
        return False


def _off_record() -> bool:
    try:
        from agent_friday.services import off_record
        return bool(off_record.active())
    except Exception:
        return True                 # cannot tell: write nothing


def _current_turn() -> Optional[str]:
    try:
        from agent_friday.governance import action_gate as ag
        return ag._turn_id()
    except Exception:
        return None


def record_call(name: str, args: Any, result: Any, *, task_id: Optional[str] = None,
                started_at: Optional[float] = None, ok: bool = True,
                turn_id: Any = "current", where: Optional[dict] = None,
                paths: Optional[List[Path]] = None) -> List[dict]:
    """Record the files a finished tool call made. Returns the lines written.
    Only a call that went through (`ok`) counts, and only files changed since
    the call started (a path the tool names but did not touch is not its
    artifact). Never raises."""
    try:
        if not ok or name not in CREATING_TOOLS or _off_record():
            return []
        paths = written_paths(name, args, result) if paths is None else paths
        if not paths:
            return []
        if turn_id == "current":
            turn_id = _current_turn()
        ahash = args_hash(args)
        where = where or {}
        receipt = _find_receipt(name, task_id, turn_id, ahash, where.get("receipts"), where.get("gov_key"))
        appended = name == "write_file" and isinstance(result, str) and result.startswith("Appended ")
        from agent_friday.services import artifact_qa
        lines = []
        since = (started_at or time.time()) - 2.0
        for p in paths:
            try:
                if not p.is_file() or p.stat().st_mtime < since:
                    continue
                sha = _sha256(p)
                # An append adds to a file that was checked when it was made.
                qa = ({"checked": False, "ok": True, "findings": []} if appended
                      else artifact_qa.check_isolated(p))
                lines.append(_sign({
                    "kind": "artifact",
                    "ts": datetime.utcnow().isoformat() + "Z",
                    "path": str(p.resolve()),
                    "sha256": sha,
                    "bytes": p.stat().st_size,
                    "tool": name,
                    "task_id": task_id,
                    "turn_id": turn_id,
                    "args_hash": ahash,
                    "receipt": ({"hmac": receipt.get("hmac"), "decision": receipt.get("decision"),
                                 "class": receipt.get("class"), "timestamp": receipt.get("timestamp")}
                                if receipt else None),
                    "provenance": _has_provenance(sha),
                    "qa": {"checked": qa["checked"], "ok": qa["ok"], "findings": qa["findings"][:12]},
                }, where.get("key")))
            except Exception:
                continue
        if lines and not _off_record():
            mp = where.get("manifest") or manifest_path()
            mp.parent.mkdir(parents=True, exist_ok=True)
            with _LOCK, open(mp, "a", encoding="utf-8") as f:
                for e in lines:
                    f.write(json.dumps(e, default=str) + "\n")
        return lines if lines else []
    except Exception:
        return []


# ── off the conversation's path ────────────────────────────────────────────

_QUEUE: "queue.Queue" = queue.Queue(maxsize=256)
_WORKER: Optional[threading.Thread] = None
_WORKER_LOCK = threading.Lock()


def _work() -> None:
    while True:
        job = _QUEUE.get()
        try:
            record_call(*job[0], **job[1])
        except Exception:
            pass
        finally:
            _QUEUE.task_done()


def submit(name: str, args: Any, result: Any, *, task_id: Optional[str] = None,
           started_at: Optional[float] = None, ok: bool = True) -> bool:
    """Record a finished call on the background thread. The turn is read now,
    on the caller's thread, where it is known. A creating call only; returns
    False (nothing queued) for anything else or when the queue is full."""
    global _WORKER
    if not ok or name not in CREATING_TOOLS or _off_record():
        return False
    # Everything that depends on where and for whom we are running is read
    # now, on the caller's thread: the turn, the files and the keys.
    try:
        from agent_friday.governance import action_gate as ag
        where = {"manifest": manifest_path(), "receipts": ag.receipts_path(),
                 "key": _key(), "gov_key": ag._governance_key()}
    except Exception:
        return False
    paths = written_paths(name, args, result)          # resolved here, not later elsewhere
    if not paths:
        return False
    job = ((name, args, result), {"task_id": task_id, "started_at": started_at, "ok": ok,
                                  "turn_id": _current_turn(), "where": where, "paths": paths})
    with _WORKER_LOCK:
        if _WORKER is None or not _WORKER.is_alive():
            _WORKER = threading.Thread(target=_work, name="artifact-manifest", daemon=True)
            _WORKER.start()
    try:
        _QUEUE.put_nowait(job)
        return True
    except queue.Full:
        return False


def drain(timeout: float = 10.0) -> bool:
    """Wait until every submitted call is recorded (tests, shutdown)."""
    end = time.time() + timeout
    while _QUEUE.unfinished_tasks and time.time() < end:
        time.sleep(0.02)
    return not _QUEUE.unfinished_tasks


# ── reading ────────────────────────────────────────────────────────────────

def entries(limit: int = 500) -> List[dict]:
    """The newest lines first, each with "verified"."""
    out = []
    for line in reversed(_tail_lines(manifest_path(), 4 * 1024 * 1024)):
        try:
            e = json.loads(line)
        except Exception:
            continue
        if isinstance(e, dict):
            out.append(dict(e, verified=verify(e)))
        if len(out) >= limit:
            break
    return out


def lookup(path) -> Optional[dict]:
    """The newest verified line for a file whose bytes still match, or None."""
    try:
        want = str(Path(path).resolve())
        sha = _sha256(Path(want)) if Path(want).is_file() else None
    except Exception:
        return None
    for e in entries(limit=5000):
        if e.get("path") == want and e.get("verified") and (sha is None or e.get("sha256") == sha):
            return e
    return None
