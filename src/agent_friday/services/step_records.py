"""Step records (north star §30.9, NS-30.9-2; burn-down BD-12 slice G8).

A step is one thing a task did: a tool call, a subagent it started, one
attempt of a goal milestone. Each is written to the task's own journal
(services/task_journal, event kind ``step``), so it is stored, protected,
redacted, deleted and kept off the record exactly as the rest of that task's
record is. This module holds the rules; it does no I/O of its own except
fingerprinting a file a creating tool just wrote (``artifact_refs``).

A step record::

    step_id                 "stp_" + 16 hex
    kind                    one of KINDS
    state                   one of STATES
    name                    the tool, the subagent's scope, the milestone id
                            (an identifier, never text a model wrote)
    input_refs, output_refs ids only, "<type>:<id>" (REF_TYPES); never content
    distinct_progress_key   see below
    started_at, ended_at    epoch seconds; duration_ms

**Ids only.** A ref is a short typed identifier (``artifact:hmac-...``,
``receipt:...``, ``task:...``, ``args:<hash>``, ``result:<hash>``). Anything
that is not shaped like one (spaces, more than 128 characters, an unknown type)
is dropped, never stored: free text cannot get into a step record through a
ref. A ``result:``, ``args:``, ``delegation:``, ``progress:`` or ``artifact:``
ref is a 16-hex fingerprint of the value, not the value.

**Fingerprints are keyed.** ``fingerprint`` is an HMAC-SHA256 under a key
derived from the governance key for this purpose only (the way the artifact
manifest derives its own: ``artifact_manifest.derived_key``), so a short or
guessable value (a yes, an address, a query) cannot be recovered from its
fingerprint by hashing candidates. When no governance key is available there
is no fingerprint: those refs are left out (never a plain hash instead).

**distinct_progress_key: the rule.** Two steps that made the same progress
have the same key; a step that made new progress has a key no earlier step
had. The key is a hash of exactly:

1. the step's kind;
2. its name (the tool or action);
3. its state (done, failed, ...: an outcome);
4. its arguments' fingerprint (the ``args:`` input ref; KEY_INPUT_REFS), so
   three different searches that each come back "No results" are three
   different steps, while the same call repeated is the same step;
5. its *outcome* refs (OUTCOME_REFS: artifact fingerprints, receipt ids, state
   changes, the normalised result fingerprint, the delegated work, the keys of
   the steps a milestone attempt reached), sorted and de-duplicated.

Nothing else. Not the step id, the timestamps, the duration, the task id, a
message id or an action id: those change on every repeat and would make every
repeat look new. A result is normalised before it is fingerprinted
(``normalize_outcome``): case and white space are folded and clock times, ISO
dates, epoch numbers, UUIDs and long hex ids are blanked, so "fetched at
10:41:07" and "fetched at 10:41:09" are the same outcome.

No-progress detection over these keys lives with the other no-progress rules
in services/goal_state: across a goal's attempts only the attempt-level key
(the milestone_run step's) can stop a run (``stalled_attempts``); repeated
tool-call steps are reported (``stalled_steps``), never a reason to stop.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import re
import uuid
from typing import Any, Dict, Iterable, List, Optional

KINDS = ("model_turn", "tool_call", "subagent", "approval_wait", "verification", "milestone_run")
STATES = ("planned", "running", "waiting", "done", "failed", "cancelled", "unknown")

#: The kinds of id a ref may carry.
REF_TYPES = ("action", "approval", "args", "artifact", "delegation", "goal", "message",
             "milestone", "progress", "receipt", "result", "state", "task", "turn")
#: Refs that say what a step produced. These feed the progress key.
OUTCOME_REFS = frozenset({"artifact", "delegation", "progress", "receipt", "result", "state"})
#: Input refs that also feed the progress key: what the step was asked to do.
KEY_INPUT_REFS = frozenset({"args"})

MAX_REFS = 16
_REF_RE = re.compile(r"^([a-z]{2,16}):([A-Za-z0-9._:\-]{1,128})$")

#: Files larger than this are identified by path and size, not hashed, so a
#: big video never holds up the conversation (the manifest hashes it later).
ARTIFACT_HASH_MAX_BYTES = 1 * 1024 * 1024
_ARTIFACTS_PER_STEP = 5


_DOMAIN = b"agent-friday/step-records/v1"


def fp_key() -> Optional[bytes]:
    """The step-records fingerprint key (derived from the governance key for
    this purpose only), or None when the governance key is unavailable."""
    try:
        from agent_friday.services import artifact_manifest as _am
        return _am.derived_key(_DOMAIN)
    except Exception:
        return None


def fingerprint(value: Any, key: Optional[bytes] = None) -> Optional[str]:
    """16 hex characters of HMAC-SHA256 over the value as text, under `key`
    (or fp_key()). None when there is no key: no fingerprint, never a plain hash."""
    k = key or fp_key()
    if not k:
        return None
    s = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    return _hmac.new(k, s.encode("utf-8", errors="replace"), hashlib.sha256).hexdigest()[:16]


_VOLATILE = (
    re.compile(r"\b\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:z|[+-]\d{2}:?\d{2})?\b"),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?\b"),
    re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"),
    re.compile(r"\b[0-9a-f]{20,}\b"),
    re.compile(r"\b\d{10,}(?:\.\d+)?\b"),
)


def normalize_outcome(value: Any) -> str:
    """The result as text with what changes on every repeat blanked out."""
    s = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    s = " ".join(s.lower().split())
    for rx in _VOLATILE:
        s = rx.sub("#", s)
    return s


#: How much of a result is fingerprinted: the first 256 KB, plus the total
#: length when it is longer, so a huge result costs no more than this to
#: fingerprint and two long results that differ only in length still differ.
RESULT_FP_MAX_CHARS = 256 * 1024


def capped_outcome(value: Any) -> str:
    """The result as text, cut to RESULT_FP_MAX_CHARS, with its total length
    appended when it was cut."""
    s = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    if len(s) <= RESULT_FP_MAX_CHARS:
        return s
    return s[:RESULT_FP_MAX_CHARS] + "\n[total length %d]" % len(s)


def result_ref(value: Any, key: Optional[bytes] = None) -> Optional[str]:
    """``result:<fingerprint>`` of a normalised result (at most its first
    RESULT_FP_MAX_CHARS plus its length); None for no result or no
    fingerprint key."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return make_ref("result", fingerprint(normalize_outcome(capped_outcome(value)), key))


def make_ref(kind: str, value: Any) -> Optional[str]:
    """A well-formed ref, or None when the value is not an id (or is None:
    a fingerprint that could not be made)."""
    if value is None:
        return None
    r = f"{kind}:{value}"
    return r if valid_ref(r) else None


def valid_ref(ref: Any) -> bool:
    if not isinstance(ref, str):
        return False
    m = _REF_RE.match(ref)
    return bool(m) and m.group(1) in REF_TYPES


def clean_refs(refs: Optional[Iterable[Any]]) -> List[str]:
    """Only well-formed refs, de-duplicated, in order, at most MAX_REFS."""
    out: List[str] = []
    for r in refs or ():
        if valid_ref(r) and r not in out:
            out.append(r)
            if len(out) >= MAX_REFS:
                break
    return out


def progress_key(kind: str, name: Any, state: str, output_refs: Optional[Iterable[Any]],
                 input_refs: Optional[Iterable[Any]] = None) -> str:
    """The step's distinct_progress_key (the rule is in the module docstring)."""
    outcome = sorted({r for r in clean_refs(output_refs) if r.split(":", 1)[0] in OUTCOME_REFS})
    asked = sorted({r for r in clean_refs(input_refs) if r.split(":", 1)[0] in KEY_INPUT_REFS})
    payload = json.dumps([str(kind), str(name or ""), str(state), asked, outcome], separators=(",", ":"))
    return "dpk_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _clean_name(name: Any) -> str:
    s = str(name or "")
    return s if re.fullmatch(r"[A-Za-z0-9._:\-]{1,80}", s) else ((fingerprint(s) or "") if s else "")


def record(kind: str, state: str, *, name: Any = None,
           input_refs: Optional[Iterable[Any]] = None, output_refs: Optional[Iterable[Any]] = None,
           started_at: Optional[float] = None, ended_at: Optional[float] = None,
           step_id: Optional[str] = None) -> Dict[str, Any]:
    """A step record as written to the journal (without seq/ts/task_id)."""
    if kind not in KINDS:
        raise ValueError("unknown step kind: %r" % kind)
    if state not in STATES:
        raise ValueError("unknown step state: %r" % state)
    outs = clean_refs(output_refs)
    ins = clean_refs(input_refs)
    nm = _clean_name(name)
    duration_ms = None
    if started_at is not None and ended_at is not None:
        duration_ms = max(0, int(round((float(ended_at) - float(started_at)) * 1000)))
    return {
        "step_id": step_id if (isinstance(step_id, str) and re.fullmatch(r"stp_[0-9a-f]{16}", step_id))
        else "stp_" + uuid.uuid4().hex[:16],
        "step_kind": kind,
        "state": state,
        "name": nm or None,
        "input_refs": ins,
        "output_refs": outs,
        "distinct_progress_key": progress_key(kind, nm, state, outs, ins),
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_ms": duration_ms,
    }


def public(ev: Dict[str, Any]) -> Dict[str, Any]:
    """A journal ``step`` event in the §30.9 shape the API serves."""
    return {
        "id": ev.get("step_id"), "task_id": ev.get("task_id"), "seq": ev.get("seq"),
        "kind": ev.get("step_kind"), "state": ev.get("state"), "name": ev.get("name"),
        "input_refs": list(ev.get("input_refs") or []),
        "output_refs": list(ev.get("output_refs") or []),
        "distinct_progress_key": ev.get("distinct_progress_key"),
        "started_at": ev.get("started_at"), "ended_at": ev.get("ended_at"),
        "duration_ms": ev.get("duration_ms"),
    }


def _kept_off_record(task_id: Optional[str]) -> bool:
    """True when this task's record is off the record (or that cannot be
    told): then nothing about its files is read at all."""
    try:
        from agent_friday.services import off_record
        if off_record.active() or off_record.skip("task_journal"):
            return True
        if task_id:
            from agent_friday.services import task_journal as _tj
            return bool(_tj._unwritten(task_id))
        return False
    except Exception:
        return True


def artifact_refs(name: str, args: Any, result: Any, *, task_id: Optional[str] = None,
                  key: Optional[bytes] = None, hashes: Optional[Dict[str, str]] = None) -> List[str]:
    """Refs to the files a creating tool call just wrote: the keyed
    fingerprint of the content's SHA-256 for a file up to
    ARTIFACT_HASH_MAX_BYTES, otherwise the keyed fingerprint of its path, and
    its size. Each SHA-256 taken is put in `hashes` ({resolved path: sha256})
    for the artifact manifest's line of the same call
    (artifact_manifest.submit(known_sha256=...)), so the file is hashed once.
    Off the record nothing is read and there are none; without a fingerprint
    key there are none. Never raises; never content."""
    out: List[str] = []
    try:
        if _kept_off_record(task_id):
            return out
        from agent_friday.services import artifact_manifest as _am
        if name not in _am.CREATING_TOOLS:
            return out
        k = key or fp_key()
        if not k:
            return out
        for p in _am.written_paths(name, args, result)[:_ARTIFACTS_PER_STEP]:
            try:
                if not p.is_file():
                    continue
                size = p.stat().st_size
                sha = _am.file_sha256(p, ARTIFACT_HASH_MAX_BYTES) if size <= ARTIFACT_HASH_MAX_BYTES else None
                if sha:
                    if hashes is not None:
                        hashes[str(p.resolve())] = sha
                    ref = make_ref("artifact", "hmac-%s" % fingerprint(sha, k))
                else:
                    ref = make_ref("artifact", "path-%s-%d" % (fingerprint(str(p.resolve()), k), size))
                if ref:
                    out.append(ref)
            except OSError:
                continue
    except Exception:
        return out
    return out
