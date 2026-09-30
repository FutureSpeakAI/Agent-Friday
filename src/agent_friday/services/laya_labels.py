"""The owner's answer key for the approval gate, and the score against it.

One question, answered yes or no by the owner: does this reach outside my
machine? A label is per TOOL (argument-independent: "mcp_github_search_users
reaches outside") or per exact STATE (one described action, by its digest).
Labels live in ~/.friday/laya_labels.jsonl, append-only; the newest answer
for a subject wins, so a changed mind is a new line rather than an edit.

`evidence()` scores each scanner against those labels from the decision log:
real traffic and probes kept apart, because the week-one review's 125
disagreements were all Grants-screen probes, not actions. This is what a
promotion from shadow to "on" should be decided on, and it is served in
/api/decisions/gate_status beside the calibration report.

`queue()` is what the Settings panel asks the owner about next: unlabelled
states where the two scanners disagreed first, then the most recent real
actions.

Nothing here decides anything. It reads the log and the labels.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from typing import Dict, Optional

_log = logging.getLogger("friday.laya_labels")
_LOCK = threading.Lock()

LABELS_NAME = "laya_labels.jsonl"
QUESTION = "reaches_outside"
#: How much of each log is read back, newest last. Bounded so a year of
#: decisions does not make the Settings panel slow.
_MAX_ROWS = 20000
_TOOL_RE = re.compile(r"^\s*([a-z][a-z0-9_]{1,80})\s+[\[{]")


def labels_path():
    from agent_friday.core import FRIDAY_DIR
    return FRIDAY_DIR / LABELS_NAME


def _digest(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()[:16]


def label(*, reaches_outside: bool, tool: Optional[str] = None,
          state_sha256: Optional[str] = None, subject: Optional[str] = None,
          by: str = "owner", note: str = "") -> dict:
    """Record one answer. `subject` is "tool:<name>" or "state:<digest>"."""
    if subject is None:
        if tool:
            subject = "tool:" + str(tool)
        elif state_sha256:
            subject = "state:" + str(state_sha256)
    subject = str(subject or "")
    if not re.fullmatch(r"(tool:[A-Za-z0-9_.\-]{1,120}|state:[0-9a-f]{16})", subject):
        raise ValueError("a label needs a tool name or a 16-hex state digest")
    row = {"at": time.time(), "subject": subject, "question": QUESTION,
           "reaches_outside": bool(reaches_outside), "by": str(by)[:40],
           "note": str(note or "")[:200]}
    with _LOCK:
        p = labels_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def labels() -> Dict[str, dict]:
    """Latest label per subject."""
    out: Dict[str, dict] = {}
    try:
        p = labels_path()
        if not p.exists():
            return out
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except Exception:
                continue
            if row.get("question") == QUESTION and row.get("subject"):
                out[row["subject"]] = row
    except Exception as e:
        _log.warning("could not read laya labels: %s", e)
    return out


# ---------------------------------------------------------------------------
#  READING THE LOG AS EVIDENCE
# ---------------------------------------------------------------------------

def _outward(question: str, answer) -> Optional[bool]:
    """A scanner's answer as "reaches outside", or None if it is not one."""
    if answer is None:
        return None
    a = str(answer)
    if a in ("hard", "soft"):
        return a == "hard"
    if question == "policy_class":
        return a != "internal"
    return None


def _read(path) -> list:
    try:
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-_MAX_ROWS:]
    except Exception:
        return []
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def _states(rows: list) -> Dict[str, dict]:
    """One entry per state digest: what each scanner said, newest wins."""
    out: Dict[str, dict] = {}
    for r in rows:
        sha = r.get("state_sha256")
        if not sha or r.get("skipped") or r.get("event"):
            continue
        q = r.get("question")
        e = out.setdefault(sha, {"sha": sha, "question": q, "tool": None,
                                 "preview": "", "keyword": None, "laya": None,
                                 "last_at": 0.0})
        ctx = r.get("context") or {}
        state = str(r.get("state") or "")
        m = _TOOL_RE.match(state)
        e["tool"] = ctx.get("tool") or e["tool"] or (m.group(1) if m else None)
        e["preview"] = state[:140]
        e["last_at"] = max(e["last_at"], float(r.get("at") or 0))
        det = r.get("detail") or {}
        method = r.get("method")
        if r.get("shadow"):
            if method in ("laya", "laya-union"):
                e["laya"] = r.get("answer")
            continue
        if det.get("union") == "or":
            e["keyword"] = det.get("keyword")
            e["laya"] = det.get("laya")
        elif method == "keyword" or det.get("union") == "keyword-only":
            e["keyword"] = r.get("answer")
    return out


def _subject(e: dict) -> str:
    return "tool:" + e["tool"] if e.get("tool") else "state:" + e["sha"]


def _label_for(e: dict, lab: Dict[str, dict], tool_digests: Dict[str, str]) -> Optional[dict]:
    if e.get("tool") and ("tool:" + e["tool"]) in lab:
        return lab["tool:" + e["tool"]]
    if ("state:" + e["sha"]) in lab:
        return lab["state:" + e["sha"]]
    # A probe of "<tool> {}" whose name the log blanked: match by digest.
    t = tool_digests.get(e["sha"])
    return lab.get("tool:" + t) if t else None


def _score(entries: Dict[str, dict], lab: Dict[str, dict]) -> dict:
    tool_digests = {_digest(s[5:] + " {}"): s[5:] for s in lab if s.startswith("tool:")}
    res = {"labelled": 0, "keyword": {"right": 0, "wrong": 0},
           "laya": {"right": 0, "wrong": 0}}
    for e in entries.values():
        got = _label_for(e, lab, tool_digests)
        if got is None:
            continue
        truth = bool(got.get("reaches_outside"))
        res["labelled"] += 1
        for who in ("keyword", "laya"):
            said = _outward(e["question"], e[who])
            if said is None:
                continue
            res[who]["right" if said == truth else "wrong"] += 1
    return res


def evidence() -> dict:
    """Each scanner against the owner's labels: real traffic, then probes."""
    from agent_friday.services import decisions
    lab = labels()
    real = _score(_states(_read(decisions.log_path())), lab)
    probes = _score(_states(_read(decisions.probe_log_path())), lab)
    return dict(real, probes=probes, labels=len(lab),
                question="does this reach outside the machine?")


def queue(limit: int = 10) -> list:
    """Unlabelled states to ask the owner about: disagreements first."""
    from agent_friday.services import decisions
    lab = labels()
    tool_digests = {_digest(s[5:] + " {}"): s[5:] for s in lab if s.startswith("tool:")}
    items = []
    seen = set()
    for probe, path in ((False, decisions.log_path()), (True, decisions.probe_log_path())):
        for e in _states(_read(path)).values():
            subj = _subject(e)
            if subj in seen or _label_for(e, lab, tool_digests) is not None:
                continue
            seen.add(subj)
            k, la = _outward(e["question"], e["keyword"]), _outward(e["question"], e["laya"])
            items.append({"subject": subj, "preview": e["preview"],
                          "keyword": e["keyword"], "laya": e["laya"],
                          "keyword_says_outside": k, "laya_says_outside": la,
                          "disagree": k is not None and la is not None and k != la,
                          "probe": probe, "last_at": e["last_at"]})
    items.sort(key=lambda i: (not i["disagree"], i["probe"], -i["last_at"]))
    return items[:max(0, int(limit))]
