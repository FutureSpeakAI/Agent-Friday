"""
Research objects — commission, plan, findings, report. All on disk.

Persistent from the first byte (deep-research.md §3.0). A commission that dies
with the process is not a promise Friday can make, and the work-queue precedent
already settled that argument. The directory on disk IS the state: a restart
resumes from the last recorded finding rather than silently starting over, and
§6's progress endpoint reads the same structure the orb reads, so the UI can
never invent progress it does not have.

One deliberate shape choice: findings are append-only JSONL rather than a
rewritten JSON array. A crash mid-write then costs the last line, not the
ledger.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Status values a commission moves through. `delivered` and `failed` are the
# only terminal ones — RS9: no report and no failure account is `failed`, never
# `complete`.
PROPOSED = "proposed"
SCOPING = "scoping"
GRINDING = "grinding"
SYNTHESIZING = "synthesizing"
VERIFYING = "verifying"
DELIVERED = "delivered"
FAILED = "failed"

# Confidence on a finding.
CONFIRMED = "confirmed"
SINGLE_SOURCE = "single_source"
CONTESTED = "contested"
UNCONFIRMED = "unconfirmed"


def research_root() -> Path:
    from agent_friday.core import FRIDAY_DIR
    return Path(FRIDAY_DIR) / "research"


def commission_dir(commission_id: str) -> Path:
    return research_root() / commission_id


DEFAULT_BUDGET = {
    # Sized to Q6 — match what Claude.ai's Research delivers (§3.3).
    "sub_questions": 10,
    "queries_per_sq": 5,
    "fetches_per_sq": 8,
    "fetches_total": 80,
    "followup_depth": 3,
    "escalations": 2,
    "wall_clock_soft_s": 2700,      # 45 minutes
}


@dataclass
class ProtectionPlan:
    """The judgment gate's verdict on this commission (§3.2 / RS2).

    Computed, never asserted. `scrub_tags` carries KINDS ONLY — the whole point
    is that a protection plan can be shown to the user without the plan itself
    becoming a place private values are written down.
    """
    cloud_allowed: bool = True
    question_sent: str | None = None
    scrub_tags: list[str] = field(default_factory=list)
    scrub_count: int = 0
    reason: str = ""

    def sentence(self) -> str:
        """The line shown in the proposal, up front (Q5)."""
        if not self.cloud_allowed:
            return ("Claude will never see this question — it cannot be asked "
                    f"without material that stays here. {self.reason} "
                    "A local model will frame it, which may give a weaker plan.")
        if self.scrub_count:
            kinds = ", ".join(sorted(set(self.scrub_tags))) or "identifying details"
            return (f"Claude will see a protected version of this question — "
                    f"{kinds} scrubbed ({self.scrub_count} span"
                    f"{'s' if self.scrub_count != 1 else ''}).")
        return "Claude will see this question as written; nothing needed scrubbing."


@dataclass
class SubQuestion:
    """One separately-answerable question — with its OWN privacy verdict.

    The fork used to be per-COMMISSION: one verdict for the whole job. Stephen
    found the flaw by using it — a report on himself and his company mixes
    "what has been published about FutureSpeak.AI" (public, deserves the
    strongest model and the live web) with "what does my vault say about my
    positioning" (his, stays here). One verdict for both is wrong in whichever
    direction it lands.
    """
    id: str
    text: str
    perspective: str = ""
    done_when: str = ""
    cloud_allowed: bool = False       # computed per sub-question
    protection_reason: str = ""
    seat: str = ""                    # the model that actually ground it
    source: str = "web"               # "web" | "vault" — a SOURCE split


@dataclass
class ResearchPlan:
    commission_id: str
    perspectives: list[dict] = field(default_factory=list)
    sub_questions: list[SubQuestion] = field(default_factory=list)
    working_title: str = ""
    internal_first: list[str] = field(default_factory=list)
    scoped_by: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["sub_questions"] = [asdict(s) if not isinstance(s, dict) else s
                              for s in self.sub_questions]
        return d


@dataclass
class Finding:
    """A claim is born with its receipt or it is born unconfirmed (RS4).

    There is no later stage that "adds citations" — that stage is exactly where
    fabricated sourcing gets introduced, so it does not exist.
    """
    id: str
    sub_question_id: str
    claim: str
    quote: str
    source_id: str = ""
    span_id: str = ""
    url: str = ""
    confidence: str = SINGLE_SOURCE
    contradicts: list[str] = field(default_factory=list)
    struck_reason: str = ""          # set by verification when a receipt fails


class Commission:
    """A research commission and its directory on disk."""

    def __init__(self, question: str, *, context: str | None = None,
                 disposition: str = "now_local", budget: dict | None = None,
                 commission_id: str | None = None,
                 instruction: dict | None = None):
        self.id = commission_id or uuid.uuid4().hex[:12]
        self.created_at = time.time()
        self.question = question
        self.context = context
        self.disposition = disposition
        self.budget = {**DEFAULT_BUDGET, **(budget or {})}
        # An EXPLICIT instruction from Stephen. "the system should follow my
        # instructions" — naming a model or asking for web research on himself
        # is an instruction, not a preference, and it outranks the classifier's
        # verdict. The gate advises; he decides.
        #   model       str|None  — the model he named, used verbatim
        #   allow_cloud bool|None — True forces cloud even when the fork objects
        self.instruction = dict(instruction or {})
        self.status = PROPOSED
        self.protection = ProtectionPlan()
        self.plan: ResearchPlan | None = None
        self.report_path: str | None = None
        self.styled_path: str | None = None
        self.colophon: dict = {}
        self.failure: str | None = None
        # Live counters the progress endpoint reads (§6).
        self.progress: dict = {"stage": PROPOSED, "sub_question": 0,
                               "sub_questions_total": 0, "fetches": 0,
                               "findings": 0, "note": ""}
        self.dir.mkdir(parents=True, exist_ok=True)

    # ── paths ──
    @property
    def dir(self) -> Path:
        return commission_dir(self.id)

    @property
    def findings_path(self) -> Path:
        return self.dir / "findings.jsonl"

    @property
    def log_path(self) -> Path:
        return self.dir / "log.jsonl"

    @property
    def meta_path(self) -> Path:
        return self.dir / "commission.json"

    # ── persistence ──
    def save(self) -> None:
        data = {
            "id": self.id, "created_at": self.created_at,
            "question": self.question, "context": self.context,
            "disposition": self.disposition, "budget": self.budget,
            "status": self.status,
            "protection": asdict(self.protection),
            "instruction": self.instruction,
            "plan": self.plan.to_dict() if self.plan else None,
            "report_path": self.report_path, "styled_path": self.styled_path,
            "colophon": self.colophon, "failure": self.failure,
            "progress": self.progress,
        }
        tmp = self.meta_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.meta_path)      # atomic: never a half-written state

    @classmethod
    def load(cls, commission_id: str) -> "Commission | None":
        p = commission_dir(commission_id) / "commission.json"
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
        c = cls(d["question"], context=d.get("context"),
                disposition=d.get("disposition", "now_local"),
                budget=d.get("budget"), commission_id=d["id"],
                instruction=d.get("instruction"))
        c.created_at = d.get("created_at", c.created_at)
        c.status = d.get("status", PROPOSED)
        c.protection = ProtectionPlan(**(d.get("protection") or {}))
        c.report_path = d.get("report_path")
        c.styled_path = d.get("styled_path")
        c.colophon = d.get("colophon") or {}
        c.failure = d.get("failure")
        c.progress = d.get("progress") or c.progress
        pd = d.get("plan")
        if pd:
            c.plan = ResearchPlan(
                commission_id=pd.get("commission_id", c.id),
                perspectives=pd.get("perspectives") or [],
                sub_questions=[SubQuestion(**s) for s in pd.get("sub_questions") or []],
                working_title=pd.get("working_title", ""),
                internal_first=pd.get("internal_first") or [],
                scoped_by=pd.get("scoped_by", ""))
        return c

    # ── progress, written AT execution time ──
    def log(self, message: str, **fields) -> None:
        """Emit a progress line as the work happens, not after it.

        The `dcf8caf` lesson: a log written at the end is a transcript, not
        progress. This is what makes a long commission legible while it runs.
        """
        entry = {"ts": time.time(), "msg": message, **fields}
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass

    def set_stage(self, stage: str, **progress) -> None:
        self.status = stage
        self.progress["stage"] = stage
        self.progress.update(progress)
        self.save()
        self.log(f"stage: {stage}", **progress)

    def read_log(self, limit: int = 500) -> list[dict]:
        try:
            lines = self.log_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return []
        out = []
        for ln in lines[-limit:]:
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
        return out

    # ── findings: append-only ──
    def add_finding(self, f: Finding) -> None:
        try:
            with open(self.findings_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(f)) + "\n")
        except Exception:
            pass
        self.progress["findings"] = self.progress.get("findings", 0) + 1

    def findings(self) -> list[Finding]:
        try:
            lines = self.findings_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return []
        out = []
        for ln in lines:
            try:
                out.append(Finding(**json.loads(ln)))
            except Exception:
                continue
        return out

    # ── status, for the endpoint and the orb ──
    def status_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "question": self.question, "status": self.status,
            "progress": self.progress,
            "protection": {**asdict(self.protection), "sentence":
                           self.protection.sentence()},
            "sub_questions": [asdict(s) for s in (self.plan.sub_questions
                                                  if self.plan else [])],
            "findings": self.progress.get("findings", 0),
            "report_path": self.report_path, "styled_path": self.styled_path,
            "colophon": self.colophon, "failure": self.failure,
            "elapsed_s": round(time.time() - self.created_at, 1),
        }


def list_commissions(limit: int = 50) -> list[dict]:
    root = research_root()
    out = []
    try:
        dirs = [d for d in root.iterdir() if d.is_dir() and d.name != "sources"]
    except Exception:
        return []
    for d in sorted(dirs, key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        c = Commission.load(d.name)
        if c:
            out.append({"id": c.id, "question": c.question, "status": c.status,
                        "created_at": c.created_at,
                        "report_path": c.report_path})
    return out
