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
  qa                        artifact_qa.check: ok, the findings, a summary line

and, for NS-24.1-2:

  principal                 whose work it is: "owner" or "crew:<id>", from
                            the task record (never from model text)
  goal_ancestry             {"goal_id", "milestone_id"} when the task serves
                            a goal (the task record's goal_ancestry), else null
  sensitivity               {"level", "tier", "basis"}: the sensitivity
                            classifier's level for the call and the file's
                            text, raised for a local-only task. A level only:
                            never the words that set it
  verification_state        "unverified" | "qa_passed" | "qa_failed" |
                            "owner_verified" (the last from an event line)
  delivery_receipt          null when made; the newest delivery once the file
                            was sent or published (an event line, below)
  version_history           the earlier versions of the same path, oldest
                            first: sha256, when, and the backup write_file
                            kept (or null), with its receipt id

What happens to a file later is an "artifact_event" line, signed the same
way and tied to the artifact line's HMAC: "delivered" (with the action
gate's receipt for the send, `record_delivery`) and "owner_verified"
(`mark_owner_verified`). A line is never rewritten.

The line is signed with a key derived from the governance key for this
purpose only, so a manifest line never verifies as a receipt or the other
way round. Nothing is written while off the record. The agent hands calls
to `submit`, which records them on a background thread: hashing a long
video or checking a big PDF never holds up the conversation.

`lookup(path)` gives the full record for a file whose bytes still match:
the latest verified line with its events applied. `receipt_of(line)` the
receipt it names (verified), `verify(line)` checks a line, `qa_failures`
the files a task made that failed their checks.
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
_RECEIPT_LOOKBACK = 50
_LOCK = threading.Lock()
_DOMAIN = b"agent-friday/artifact-manifest/v1"
#: The states a file can be in (NS-24.1-2).
VERIFICATION_STATES = ("unverified", "qa_passed", "qa_failed", "owner_verified")
#: The sensitivity classifier's tiers, by name.
SENSITIVITY_LEVELS = {1: "public", 2: "private", 3: "sensitive"}
#: How much of the call and of a text file the classifier reads.
SENSITIVITY_SAMPLE = 64 * 1024
_TEXT_SUFFIXES = frozenset((".md", ".txt", ".csv", ".tsv", ".html", ".htm", ".json", ".xml", ".svg",
                            ".yaml", ".yml", ".py", ".js", ".css"))
#: Earlier versions kept per path (the oldest drop off).
MAX_VERSIONS = 50
#: Findings kept on a line (errors first).
MAX_LINE_FINDINGS = 20
#: How long a finishing task waits for its files' checks before judging them.
QA_WAIT_S = 30.0


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
            # The tool reports the path it resolved ("Wrote 12 chars to C:\\..."),
            # then its receipt: " (new file; receipt_id ...)." or ". The earlier
            # version is kept (receipt_id ...)", which is not part of the path.
            first = result.split("\n", 1)[0]
            m = re.match(r"(?:Wrote|Appended) \d+ chars to (.+?)"
                         r"(?: \(new file; receipt_id [^)]*\)\.?|\. The earlier version is kept .*)?$", first)
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
    return _verify_kind(entry, "artifact", key)


def verify_event(entry: Any, key: Optional[bytes] = None) -> bool:
    """True when the line is an artifact event line whose HMAC matches."""
    return _verify_kind(entry, "artifact_event", key)


def _verify_kind(entry: Any, kind: str, key: Optional[bytes] = None) -> bool:
    if not isinstance(entry, dict) or entry.get("kind") != kind or not isinstance(entry.get("hmac"), str):
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
                  ahash: Optional[str], gov_key: Optional[bytes] = None) -> Optional[dict]:
    """The newest verified receipt for exactly this call: the same tool and
    arguments, in the same turn (or, outside a turn, the same task). With no
    arguments hash or no turn or task to tie it to, there is no receipt.
    Receipts are read from the action ledger, where decisions are kept."""
    if not ahash or not (turn_id or task_id):
        return None

    def this_call(e: dict) -> bool:
        r = e.get("rec") or {}
        if r.get("tool") != tool or r.get("decision") not in RAN_UNDER or r.get("args_hash") != ahash:
            return False
        return r.get("turn_id") == turn_id if turn_id else r.get("task_id") == task_id

    try:
        from agent_friday.governance import action_gate as ag
        for r in ag.receipts(newest_first=True, limit=_RECEIPT_LOOKBACK, where=this_call):
            if ag.verify_receipt(r, key=gov_key):
                return r
    except Exception:
        return None
    return None


def receipt_of(entry: dict) -> Optional[dict]:
    """The verified receipt a manifest line names, or None."""
    want = ((entry or {}).get("receipt") or {}).get("hmac")
    if not want:
        return None
    try:
        from agent_friday.governance import action_gate as ag
        for r in ag.receipts(newest_first=True, limit=1,
                             where=lambda e: (e.get("rec") or {}).get("hmac") == want):
            if ag.verify_receipt(r):
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


def _task_context(task_id: Optional[str]) -> dict:
    """Whose work the call is and which goal it serves, from the task record
    (services/agent TASKS, or the task journal's saved state): never from
    anything a model wrote. A call outside a task is the owner's own turn."""
    out = {"principal": "owner", "goal_ancestry": None, "local_only": False}
    if not task_id:
        return out
    rec: dict = {}
    try:
        import sys
        ag = sys.modules.get("agent_friday.services.agent")
        if ag is not None:
            rec = dict(ag.TASKS.get(task_id) or {})
    except Exception:
        rec = {}
    if not rec:
        try:
            from agent_friday.services import task_journal
            rec = dict(task_journal.read_state(task_id) or {})
        except Exception:
            rec = {}
    p = rec.get("principal")
    if isinstance(p, str) and (p == "owner" or p.startswith("crew:")):
        out["principal"] = p[:80]
    ga = rec.get("goal_ancestry")
    if isinstance(ga, dict) and ga.get("goal_id"):
        out["goal_ancestry"] = {"goal_id": str(ga.get("goal_id"))[:120],
                                "milestone_id": (str(ga["milestone_id"])[:120] if ga.get("milestone_id") else None)}
    out["local_only"] = bool(rec.get("local_only") or rec.get("local_only_result"))
    return out


def _sensitivity(args: Any, p: Path, local_only: bool = False) -> dict:
    """The sensitivity classifier's level for the call's arguments and, for a
    text file, its first bytes. Only the level is kept, never the text."""
    parts = []
    try:
        parts.append(json.dumps(args or {}, default=str)[:SENSITIVITY_SAMPLE])
    except Exception:
        pass
    if p.suffix.lower() in _TEXT_SUFFIXES:
        try:
            with open(p, "rb") as f:
                parts.append(f.read(SENSITIVITY_SAMPLE).decode("utf-8", errors="replace"))
        except OSError:
            pass
    try:
        from agent_friday.services import sensitivity_classifier as sc
        tier = int(sc.classify("\n".join(parts), default=1, use_presidio=False, use_embeddings=False))
    except Exception:
        return {"level": "unknown", "tier": None, "basis": "unavailable"}
    tier = min(3, max(1, tier))
    basis = "classifier"
    if local_only and tier < 2:
        tier, basis = 2, "classifier+local_only_task"
    return {"level": SENSITIVITY_LEVELS[tier], "tier": tier, "basis": basis}


def _write_receipt(name: str, result: Any) -> Optional[dict]:
    """For write_file: the receipt it wrote, with the backup it kept of the
    earlier version (services/file_write_receipts), or None."""
    if name != "write_file" or not isinstance(result, str):
        return None
    m = re.search(r"receipt_id (rcpt_[0-9a-f]+)", result[:4000])
    if not m:
        return None
    try:
        from agent_friday.services import action_journal as journal
        rec = journal.get(m.group(1)) or {}
    except Exception:
        return None
    item = dict(((rec.get("items") or [None])[0]) or {})
    backup = item.get("backup")
    if not isinstance(backup, str) or backup.startswith("memory:"):
        backup = None
    return {"receipt_id": m.group(1), "existed": bool(item.get("existed")),
            "before_sha256": item.get("before_sha256"), "backup": backup}


def _version_history(prior: Optional[dict], sha: Optional[str], wr: Optional[dict]) -> List[dict]:
    """The earlier versions of a path: the previous line's history, plus the
    previous version itself when the bytes changed."""
    hist = list((prior or {}).get("version_history") or [])
    if prior and prior.get("sha256") and prior.get("sha256") != sha:
        entry = {"sha256": prior["sha256"], "ts": prior.get("ts"), "backup": None,
                 "write_receipt": None, "by_friday": True}
        if wr and wr.get("before_sha256") == prior["sha256"]:
            entry["backup"], entry["write_receipt"] = wr.get("backup"), wr.get("receipt_id")
        hist.append(entry)
    elif not prior and wr and wr.get("existed") and wr.get("before_sha256") and wr["before_sha256"] != sha:
        # The file was there before Friday's first recorded version of it.
        hist.append({"sha256": wr["before_sha256"], "ts": None, "backup": wr.get("backup"),
                     "write_receipt": wr.get("receipt_id"), "by_friday": False})
    return hist[-MAX_VERSIONS:]


def _state_of(qa: dict) -> str:
    if not qa.get("checked"):
        return "unverified"
    return "qa_passed" if qa.get("ok") else "qa_failed"


def record_call(name: str, args: Any, result: Any, *, task_id: Optional[str] = None,
                started_at: Optional[float] = None, ok: bool = True,
                turn_id: Any = "current", where: Optional[dict] = None,
                paths: Optional[List[Path]] = None, task_context: Optional[dict] = None) -> List[dict]:
    """Record the files a finished tool call made. Returns the lines written.
    Only a call that went through (`ok`) counts, and only files changed since
    the call started (a path the tool names but did not touch is not its
    artifact). `task_context` (principal, goal ancestry) is read from the
    task record when not given. Never raises."""
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
        ctx = task_context if isinstance(task_context, dict) else _task_context(task_id)
        receipt = _find_receipt(name, task_id, turn_id, ahash, where.get("gov_key"))
        appended = name == "write_file" and isinstance(result, str) and result.startswith("Appended ")
        wr = _write_receipt(name, result)
        mp = where.get("manifest") or manifest_path()
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
                prior = _latest_line(mp, str(p.resolve()), where.get("key"))
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
                    "qa": {"checked": qa["checked"], "ok": qa["ok"],
                           "findings": qa["findings"][:MAX_LINE_FINDINGS],
                           "summary": artifact_qa.summary(qa)},
                    "principal": ctx.get("principal"),
                    "goal_ancestry": ctx.get("goal_ancestry"),
                    "sensitivity": _sensitivity(args, p, bool(ctx.get("local_only"))),
                    # An append keeps the state the file had when it was checked.
                    "verification_state": ((prior or {}).get("verification_state") or "unverified")
                                          if appended else _state_of(qa),
                    "delivery_receipt": None,
                    "write_receipt": (wr or {}).get("receipt_id"),
                    "version_history": _version_history(prior, sha, wr),
                }, where.get("key")))
            except Exception:
                continue
        if lines and not _off_record():
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
        where = {"manifest": manifest_path(),
                 "key": _key(), "gov_key": ag._governance_key()}
    except Exception:
        return False
    paths = written_paths(name, args, result)          # resolved here, not later elsewhere
    if not paths:
        return False
    job = ((name, args, result), {"task_id": task_id, "started_at": started_at, "ok": ok,
                                  "turn_id": _current_turn(), "where": where, "paths": paths,
                                  "task_context": _task_context(task_id)})
    with _WORKER_LOCK:
        if _WORKER is None or not _WORKER.is_alive():
            _WORKER = threading.Thread(target=_work, name="artifact-manifest", daemon=True)
            _WORKER.start()
    try:
        _QUEUE.put_nowait(job)
        return True
    except queue.Full:
        # Dropped, not guessed: a file with no line claims nothing (lookup
        # returns None), but the gap is logged.
        import logging
        logging.getLogger(__name__).warning("artifact manifest queue full; %s call not recorded", name)
        return False


def drain(timeout: float = 10.0) -> bool:
    """Wait until every submitted call is recorded (tests, shutdown)."""
    end = time.time() + timeout
    while _QUEUE.unfinished_tasks and time.time() < end:
        time.sleep(0.02)
    return not _QUEUE.unfinished_tasks


# ── reading ────────────────────────────────────────────────────────────────

def _read(mp: Path, key: Optional[bytes] = None, limit: int = 500) -> List[dict]:
    out = []
    for line in reversed(_tail_lines(mp, 4 * 1024 * 1024)):
        try:
            e = json.loads(line)
        except Exception:
            continue
        if isinstance(e, dict):
            out.append(dict(e, verified=verify(e, key) or verify_event(e, key)))
        if len(out) >= limit:
            break
    return out


def entries(limit: int = 500) -> List[dict]:
    """The newest lines first (artifact and event lines), each with "verified"."""
    return _read(manifest_path(), None, limit)


def _latest_line(mp: Path, path_str: str, key: Optional[bytes] = None) -> Optional[dict]:
    """The newest verified artifact line for a path, whatever its bytes."""
    for e in _read(mp, key, 5000):
        if e.get("kind") == "artifact" and e.get("verified") and e.get("path") == path_str:
            return e
    return None


def _with_events(base: dict, lines: List[dict]) -> dict:
    """An artifact line with what happened to it since (its event lines)."""
    rec = dict(base)
    rec.setdefault("principal", None)
    rec.setdefault("goal_ancestry", None)
    rec.setdefault("sensitivity", None)
    rec.setdefault("version_history", [])
    rec.setdefault("delivery_receipt", None)
    if not rec.get("verification_state"):
        rec["verification_state"] = _state_of(rec.get("qa") or {})
    events = [e for e in lines if e.get("kind") == "artifact_event" and e.get("verified")
              and e.get("artifact") == base.get("hmac")]                       # newest first
    deliveries = [e.get("delivery_receipt") for e in events
                  if e.get("event") == "delivered" and isinstance(e.get("delivery_receipt"), dict)]
    rec["deliveries"] = deliveries
    if deliveries:
        rec["delivery_receipt"] = deliveries[0]
    owner = next((e for e in events if e.get("event") == "owner_verified"), None)
    if owner:
        rec["verification_state"] = "owner_verified"
        rec["owner_verified_at"] = owner.get("ts")
    return rec


def lookup(path) -> Optional[dict]:
    """The full record (NS-24.1-2) for a file whose bytes still match: the
    newest verified line with its events applied, or None."""
    try:
        want = str(Path(path).resolve())
        sha = _sha256(Path(want)) if Path(want).is_file() else None
    except Exception:
        return None
    lines = entries(limit=5000)
    for e in lines:
        if e.get("kind") == "artifact" and e.get("path") == want and e.get("verified") \
                and (sha is None or e.get("sha256") == sha):
            return _with_events(e, lines)
    return None


def _append_events(events: List[dict]) -> None:
    mp = manifest_path()
    mp.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, open(mp, "a", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, default=str) + "\n")


def _event(base: dict, event: str, **fields) -> dict:
    return _sign({"kind": "artifact_event", "event": event,
                  "ts": datetime.utcnow().isoformat() + "Z",
                  "path": base.get("path"), "sha256": base.get("sha256"),
                  "artifact": base.get("hmac"), **fields})


def record_delivery(receipt: Any, *, channel: str, sha256: Optional[str] = None, path=None,
                    ref: Optional[str] = None, approval_id: Optional[str] = None) -> List[dict]:
    """A file Friday made was sent, attached or published: tie it to the
    action gate's receipt for that send. The file is found by its bytes
    (`sha256`, as an attachment carries them) or by `path` (its bytes now).
    Only a verified receipt is recorded, and each artifact line gets one
    event per receipt. Returns the event lines written. Never raises."""
    try:
        if _off_record():
            return []
        from agent_friday.governance import action_gate as ag
        if not isinstance(receipt, dict) or not ag.verify_receipt(receipt):
            return []
        if sha256 is None and path is not None:
            p = Path(path).resolve()
            sha256 = _sha256(p) if p.is_file() else None
        if not sha256 or not re.fullmatch(r"[0-9a-f]{64}", str(sha256)):
            return []
        dr = {"id": receipt.get("hmac"), "channel": str(channel)[:40],
              "ref": (str(ref)[:120] if ref else None),
              "approval_id": (str(approval_id)[:120] if approval_id else receipt.get("approval")),
              "at": receipt.get("timestamp")}
        lines = entries(limit=5000)
        done = {(e.get("artifact"), (e.get("delivery_receipt") or {}).get("id"))
                for e in lines if e.get("kind") == "artifact_event" and e.get("verified")}
        seen, events = set(), []
        for e in lines:
            if e.get("kind") != "artifact" or not e.get("verified") or e.get("sha256") != sha256:
                continue
            if e.get("path") in seen:
                continue
            seen.add(e.get("path"))
            if (e.get("hmac"), dr["id"]) in done:
                continue
            events.append(_event(e, "delivered", delivery_receipt=dr))
        if events:
            _append_events(events)
        return events
    except Exception:
        return []


def mark_owner_verified(path) -> Optional[dict]:
    """The owner checked the file and says it is right: an "owner_verified"
    event on its current line. None when there is no line for these bytes."""
    try:
        if _off_record():
            return None
        rec = lookup(path)
        if not rec:
            return None
        ev = _event(rec, "owner_verified")
        _append_events([ev])
        return ev
    except Exception:
        return None


def qa_failures(task_id: Optional[str], *, wait: float = 0.0) -> List[dict]:
    """The files a task made whose newest line failed its checks:
    [{"path", "errors"}]. `wait` first lets queued checks finish (a task
    finishing on a background thread)."""
    if not task_id:
        return []
    if wait:
        drain(wait)
    out, seen = [], set()
    for e in entries(limit=5000):
        if e.get("kind") != "artifact" or not e.get("verified") or e.get("task_id") != task_id:
            continue
        if e.get("path") in seen:
            continue
        seen.add(e.get("path"))
        qa = e.get("qa") or {}
        if e.get("verification_state") == "qa_failed" or (qa.get("checked") and qa.get("ok") is False):
            out.append({"path": e.get("path"),
                        "errors": [f.get("message") for f in qa.get("findings") or []
                                   if isinstance(f, dict) and f.get("severity") == "error"][:5]})
    return out
