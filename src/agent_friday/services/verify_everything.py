"""The two record checks the integrity view did not have yet (NS-18.9-2, A10).

  artifacts   every file the artifact manifest (services/artifact_manifest)
              recorded is hashed again and compared with the fingerprint its
              signed line kept: ok, changed (other bytes, or another size),
              missing, unreadable, not_hashed (too big to fingerprint when it
              was made). Only the newest line per file counts (a file Friday
              wrote again has a newer line). Manifest lines whose signature
              does not hold are `bad_lines`. Bounded: at most MAX_FILES files,
              MAX_BYTES hashed and MAX_SECONDS per check, newest first; what a
              cap stops is `unchecked` and the result is `partial`. The file
              and time caps end the check (time is also checked while one
              file is read); the byte cap skips only the file that would pass
              it, so older files are still checked, and a file bigger than
              the whole byte cap is `not_hashed`. A file is read to at most
              its stat size + 1 byte: one that grew since is `changed`. A file
              whose size and modification time have not moved is not hashed
              again for FILE_CACHE_S. Problems are named by the line's own
              reference (the start of its signature), the tool and the time:
              never the path or the content.

  unlinked    outward effects that services record themselves, matched to the
              gate's decision receipts in the action ledger:
                cards       an approved card an outward executor carried out
                            (a mail sent, a text or call placed, a site
                            published, a card run by the approval executor)
                            must have an outward decision naming that card
                batch_runs  an approved batch's `started` marker
                            (item_actions.RUN_KIND) names its decision's
                            action id, which must be a decision in the ledger
                sends       a send the egress ledger records (mail, texts,
                            calls, chat channels, posts, site publishes)
                            whose row names the action id of the decision
                            that authorised it must name a decision in the
                            ledger
              `no_decision` (the id names no decision) and `missing_id` (the
              record names none) are problems. A send whose row carries no
              action id (rows written before sends named one, and the paths
              that still have no decision to name) is counted per channel as
              `not_linkable`, never guessed at.

Read-only and fail-safe: each returns counts and short references, never a
record's content, and never raises from `integrity` (ledger_integrity catches
an error and says the part could not be read).
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import stat as _stat
import threading
import time
from typing import Any, Dict, Optional, Tuple

#: How many files one artifact check looks at (newest first).
MAX_FILES = 200
#: How many bytes one artifact check hashes at most.
MAX_BYTES = 256 * 1024 * 1024
#: How long one artifact check may take.
MAX_SECONDS = 5.0
#: How many manifest lines are read (newest first).
MAX_LINES = 5000
#: How long a file whose size and mtime have not moved keeps its last hash.
FILE_CACHE_S = 3600.0
#: How long a whole result stands while the inputs it read have not changed.
RESULT_TTL_S = 15.0
#: How many problems are listed (the counts are always whole).
MAX_ITEMS = 20

#: The window the outward match looks back over (as ledger_integrity).
WINDOW_S = 30 * 86400.0
#: Decisions are read from this much earlier than the window: an effect at the
#: window's start may rest on a decision written just before it.
DECISION_MARGIN_S = 86400.0
#: item_actions.RUN_KIND (mirrored, so this check never imports the batch engine).
RUN_KIND = "item_batch_run"
#: The card executors that carry out an outward action, and what in the card's
#: `used_detail` says it went out. Each writes an outward decision naming the
#: card (action_gate.record_external(approval_id=...)) before it acts.
CARD_EXECUTORS = {
    "gmail_send": lambda d: bool(d.get("message_id")),
    "phone_sms": lambda d: bool(d.get("sid")),
    "phone_call": lambda d: bool(d.get("sid")),
    "publish_web": lambda d: d.get("ok") is True,
    "approval_executor": lambda d: d.get("ok") is True,
}
#: How many cards are looked at (newest first).
MAX_CARDS = 2000
#: The egress ledger's channels that are outward effects (not reads: a fetch
#: or a search is not an action on someone else's behalf).
OUTWARD_CHANNELS = ("mail", "sms", "phone_call", "chat_channel", "publishing", "web_publish")
#: The egress results that mean something left.
_LEFT = ("sent", "sent_with_parts_withheld", "sent_unconfirmed")
#: How much of the egress ledger is read (its newest end).
MAX_EGRESS_BYTES = 16 * 1024 * 1024
MAX_EGRESS_ROWS = 50000

_LOCK = threading.Lock()
_FILE_CACHE: Dict[tuple, tuple] = {}     # (path, size, mtime_ns) -> (hashed at, sha256)
_RESULTS: Dict[str, tuple] = {}          # part -> (key, computed at, result)


def reset_cache() -> None:
    with _LOCK:
        _FILE_CACHE.clear()
        _RESULTS.clear()


def _stat_key(p) -> tuple:
    try:
        st = os.stat(p)
        return (str(p), st.st_size, st.st_mtime_ns)
    except (OSError, TypeError, ValueError):
        return (str(p), None, None)


def _cached(part: str, key: Optional[tuple]) -> Optional[dict]:
    if key is None:
        return None
    with _LOCK:
        hit = _RESULTS.get(part)
    if hit and hit[0] == key and time.time() - hit[1] < RESULT_TTL_S:
        return copy.deepcopy(hit[2])
    return None


def _keep(part: str, key: Optional[tuple], result: dict) -> None:
    if key is not None:
        with _LOCK:
            _RESULTS[part] = (key, time.time(), copy.deepcopy(result))


# ── artifacts ───────────────────────────────────────────────────────────────

_CHUNK = 1024 * 1024


def _hash_file(path: str, size: int, mtime_ns: int,
               deadline: Optional[float] = None) -> Tuple[Optional[str], int, bool]:
    """(sha256, bytes read, out of time). At most `size` + 1 bytes are read:
    a file that holds more than its stat said (it grew after the stat) gives
    no digest, so it is never read without bound and never matches. When
    `deadline` (time.monotonic) passes before the whole file is read, the
    digest is None and out of time is True. Only a whole, unchanged read is
    cached."""
    ck = (path, size, mtime_ns)
    with _LOCK:
        hit = _FILE_CACHE.get(ck)
    if hit and time.time() - hit[0] < FILE_CACHE_S:
        return hit[1], 0, False
    h = hashlib.sha256()
    read = 0
    with open(path, "rb") as f:
        while read <= size:
            chunk = f.read(min(_CHUNK, size + 1 - read))
            if not chunk:
                break
            read += len(chunk)
            if read > size:
                return None, read, False      # grew after the stat: not this file's bytes
            h.update(chunk)
            if read < size and deadline is not None and time.monotonic() > deadline:
                return None, read, True
    if read != size:
        return h.hexdigest(), read, False     # shrank after the stat: never matches, not cached
    digest = h.hexdigest()
    with _LOCK:
        if len(_FILE_CACHE) >= 4 * MAX_FILES:
            _FILE_CACHE.clear()
        _FILE_CACHE[ck] = (time.time(), digest)
    return digest, read, False


def _would_hash(path: str, size: int, mtime_ns: int) -> bool:
    with _LOCK:
        hit = _FILE_CACHE.get((path, size, mtime_ns))
    return not (hit and time.time() - hit[0] < FILE_CACHE_S)


def artifact_check(*, max_files: int = MAX_FILES, max_bytes: int = MAX_BYTES,
                   max_seconds: float = MAX_SECONDS) -> dict:
    """The manifest's files against their fingerprints. Counts and short
    references only. Raises only if the manifest module cannot be loaded."""
    from agent_friday.services import artifact_manifest as am
    defaults = (max_files, max_bytes, max_seconds) == (MAX_FILES, MAX_BYTES, MAX_SECONDS)
    key = _stat_key(am.manifest_path()) if defaults else None
    hit = _cached("artifacts", key)
    if hit is not None:
        return hit
    counts = {k: 0 for k in ("lines", "files", "ok", "changed", "missing", "unreadable",
                             "not_hashed", "bad_lines", "no_receipt", "unchecked")}
    items = []
    seen = set()
    spent = checked = 0
    partial = stopped = False
    started = time.monotonic()
    deadline = started + max_seconds
    lines = am.entries(limit=MAX_LINES)
    counts["lines"] = len(lines)
    for e in lines:
        if not e.get("verified"):
            counts["bad_lines"] += 1
            continue
        path = e.get("path")
        if not isinstance(path, str) or not path or path in seen:
            continue
        seen.add(path)
        counts["files"] += 1
        if not e.get("receipt"):
            counts["no_receipt"] += 1
        # The file-count and time caps stop the check; the byte cap only skips
        # the file that would pass it (below), and older files are still checked.
        if stopped or checked >= max_files or time.monotonic() > deadline:
            partial = stopped = True
            counts["unchecked"] += 1
            continue
        state = None
        try:
            st = os.stat(path)
        except FileNotFoundError:
            state = "missing"
        except OSError:
            state = "unreadable"
        if state is None:
            want = e.get("sha256")
            recorded = e.get("bytes")
            if not _stat.S_ISREG(st.st_mode):
                state = "unreadable"
            elif isinstance(recorded, int) and recorded != st.st_size:
                state = "changed"             # another size: no need to hash it
            elif not isinstance(want, str) or st.st_size > am.MAX_HASH_BYTES:
                state = "not_hashed"
            elif _would_hash(path, st.st_size, st.st_mtime_ns) and st.st_size > max_bytes:
                state = "not_hashed"          # alone more than one check may hash
            elif _would_hash(path, st.st_size, st.st_mtime_ns) and spent + st.st_size > max_bytes:
                partial = True                # this one is skipped; older files still are checked
                counts["unchecked"] += 1
                continue
            else:
                try:
                    digest, read, out_of_time = _hash_file(path, st.st_size, st.st_mtime_ns, deadline)
                except OSError:
                    state = "unreadable"
                else:
                    spent += read
                    if out_of_time:
                        partial = stopped = True
                        counts["unchecked"] += 1
                        continue
                    state = "ok" if digest is not None and digest == want else "changed"
        checked += 1
        counts[state] += 1
        if state not in ("ok", "not_hashed") and len(items) < MAX_ITEMS:
            items.append({"ref": str(e.get("hmac") or "")[:12], "tool": e.get("tool"),
                          "ts": e.get("ts"), "state": state})
    out = {"counts": counts, "checked": checked, "partial": partial, "items": items,
           "hashed_bytes": spent,
           "caps": {"files": max_files, "bytes": max_bytes, "seconds": max_seconds}}
    _keep("artifacts", key, out)
    return out


# ── outward effects without a decision ─────────────────────────────────────

def _egress_log():
    from agent_friday.services import egress_gate
    return egress_gate._DEFAULT_LOG


def _egress_rows(path, since: float):
    """The egress ledger's rows from `since` on, newest first, from at most
    MAX_EGRESS_BYTES of its end. (rows, partial)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - MAX_EGRESS_BYTES))
            data = f.read()
    except OSError:
        return [], False
    lines = data.split(b"\n")
    cut = size > MAX_EGRESS_BYTES
    if cut:
        lines = lines[1:]                 # the first may be half a line
    out = []
    for raw in reversed(lines):
        if not raw.strip():
            continue
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        ts = e.get("ts")
        if isinstance(ts, (int, float)) and ts < since:
            return out, False
        out.append(e)
        if len(out) >= MAX_EGRESS_ROWS:
            return out, True
    return out, cut


def _unlinked_key(window: float) -> Optional[tuple]:
    try:
        from agent_friday.services import approvals, outward_audit
        head = outward_audit._head_key()
        if head is None:
            return None
        return head + _stat_key(approvals.APPROVALS_FILE) + _stat_key(_egress_log()) + (float(window),)
    except Exception:  # noqa: BLE001
        return None


def unlinked_outward(window: float = WINDOW_S, *, now: Optional[float] = None) -> dict:
    """Outward effects in the last `window` seconds whose record names no
    decision, or one the ledger does not hold (a send's row naming an action
    id is matched like a batch run's); and how many sends carry no id to
    match. Raises only if the ledger module cannot be loaded."""
    key = _unlinked_key(window) if now is None else None
    hit = _cached("unlinked", key)
    if hit is not None:
        return hit
    from agent_friday.governance import action_gate as G
    from agent_friday.services import action_ledger as L
    from agent_friday.services import outward_audit
    now = time.time() if now is None else float(now)
    since = now - float(window)
    floor = since - DECISION_MARGIN_S
    rows = L.entries(["decision", RUN_KIND], newest_first=False, verified=True,
                     where=lambda e: isinstance(e.get("t"), (int, float)) and e["t"] >= floor)
    action_ids, cards_decided, runs = set(), set(), []
    for e in rows:
        rec = e.get("rec") if isinstance(e.get("rec"), dict) else {}
        if e.get("kind") == "decision":
            if rec.get("class") != G.OUTWARD:
                continue
            aid = outward_audit._action_id(rec)
            if aid:
                action_ids.add(aid)
            if rec.get("approval"):
                cards_decided.add(str(rec["approval"]))
        elif rec.get("state") == "started" and e["t"] >= since:
            runs.append(rec)

    items = []
    partial = False

    def note(item):
        if len(items) < MAX_ITEMS:
            items.append(item)

    br = {"checked": 0, "linked": 0, "no_decision": 0, "missing_id": 0}
    for rec in runs:
        br["checked"] += 1
        ref = rec.get("decision_ref") if isinstance(rec.get("decision_ref"), dict) else {}
        aid = ref.get("action_id")
        if not aid:
            br["missing_id"] += 1
            note({"source": "batch_run", "ref": str(rec.get("run_id") or "")[:40],
                  "by": str(rec.get("domain") or "")[:20], "problem": "missing_id"})
        elif str(aid) not in action_ids:
            br["no_decision"] += 1
            note({"source": "batch_run", "ref": str(rec.get("run_id") or "")[:40],
                  "by": str(rec.get("domain") or "")[:20], "problem": "no_decision"})
        else:
            br["linked"] += 1

    cd = {"checked": 0, "linked": 0, "no_decision": 0}
    try:
        from agent_friday.services import approvals
        cards = approvals.list_approvals()
    except Exception:  # noqa: BLE001
        cards = None
    if cards is None:
        cd["error"] = "unreadable"
    else:
        def used_at_of(r):
            v = r.get("used_at") if isinstance(r, dict) else None
            return float(v) if isinstance(v, (int, float)) else 0.0

        looked = 0
        for c in sorted(cards, key=used_at_of, reverse=True):
            if used_at_of(c) < since:
                break
            looked += 1
            if looked > MAX_CARDS:
                partial = True
                break
            went = CARD_EXECUTORS.get(str(c.get("used_by") or ""))
            detail = c.get("used_detail") if isinstance(c.get("used_detail"), dict) else {}
            if went is None or not went(detail):
                continue
            cd["checked"] += 1
            if str(c.get("approval_id")) in cards_decided:
                cd["linked"] += 1
            else:
                cd["no_decision"] += 1
                note({"source": "card", "ref": str(c.get("approval_id") or "")[:40],
                      "by": str(c.get("used_by"))[:20], "problem": "no_decision"})

    by_channel: Dict[str, int] = {}
    sd = {"checked": 0, "linked": 0, "no_decision": 0}
    egress, cut = _egress_rows(_egress_log(), since)
    partial = partial or cut
    for e in egress:
        if e.get("kind") != "call" or not str(e.get("via") or "").startswith("service:"):
            continue
        ch = e.get("channel")
        if ch not in OUTWARD_CHANNELS or e.get("result") not in _LEFT:
            continue
        aid = e.get("action_id")
        if not aid:
            # an older row, or a path that still records no decision: never guessed at
            by_channel[ch] = by_channel.get(ch, 0) + 1
            continue
        sd["checked"] += 1
        if str(aid) in action_ids:
            sd["linked"] += 1
        else:
            sd["no_decision"] += 1
            note({"source": "send", "ref": str(e.get("call_id") or "")[:40],
                  "by": str(ch)[:20], "problem": "no_decision"})

    problems = br["no_decision"] + br["missing_id"] + cd["no_decision"] + sd["no_decision"]
    out = {"window_days": round(float(window) / 86400.0, 2),
           "problems": problems,
           "linked": br["linked"] + cd["linked"] + sd["linked"],
           "checked": br["checked"] + cd["checked"] + sd["checked"],
           "batch_runs": br, "cards": cd, "sends": sd,
           "not_linkable": {"total": sum(by_channel.values()), "by_channel": by_channel},
           "partial": partial, "items": items}
    _keep("unlinked", key, out)
    return out
