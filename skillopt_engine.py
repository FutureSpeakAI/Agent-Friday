"""
SkillOpt Engine — Self-improving skill optimization for Agent Friday
FutureSpeak.AI · Asimov's Mind

Inspired by Karpathy's SkillOpt: skills evolve through training epochs, validated
against regression gates, and refined by an auto-research loop that proposes
improvements when scores trend down.

Architecture:
    SkillOptEngine ── owns a fleet of skills, drives training loop
        │
        ├── SkillVersion        versioned snapshot with metrics
        ├── TrainingEpoch       batch eval + improvement cycle
        ├── ValidationGate      blocks regressions (within 5% of best)
        └── AutoResearchLoop    proposes edits when scores drop

Every execution is logged JSONL. Each skill has a best_skill.md artifact
tracking the current champion.

Scoring is composite, weighted across: accuracy, latency, cost, user_satisfaction,
completeness. Weights are configurable per skill.

Storage layout:
    ~/.friday/skillopt/
        <skill_name>/
            versions/v001.md, v002.md, ...
            metrics.jsonl              append-only execution log
            best_skill.md              current champion artifact
            config.json                weights + thresholds
            research_log.jsonl         autoresearch findings
"""
from __future__ import annotations

import dataclasses
import difflib
import hashlib
import json
import math
import os
import re
import statistics
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple


# ════════════════════════════════════════════════════════════════════════
#  Paths and constants
# ════════════════════════════════════════════════════════════════════════

FRIDAY_DIR = Path.home() / ".friday"
SKILLOPT_DIR = FRIDAY_DIR / "skillopt"

DEFAULT_WEIGHTS: Dict[str, float] = {
    "accuracy": 0.40,
    "latency": 0.15,
    "cost": 0.10,
    "user_satisfaction": 0.25,
    "completeness": 0.10,
}

# A new version must score within REGRESSION_TOLERANCE * best_score
# to be eligible for promotion. 0.95 = within 5%.
REGRESSION_TOLERANCE: float = 0.95

# Autoresearch fires when the 10-execution rolling mean drops by this
# fraction below the all-time best.
AUTORESEARCH_DROP_THRESHOLD: float = 0.10

# Composite score is clamped to [0.0, 1.0].
SCORE_MIN, SCORE_MAX = 0.0, 1.0

# Latency and cost are normalized against these soft targets so that
# "fast and cheap" maps to a high score. Tune per skill via config.
DEFAULT_LATENCY_TARGET_MS = 5000.0
DEFAULT_COST_TARGET_USD = 0.05


# ════════════════════════════════════════════════════════════════════════
#  Data classes
# ════════════════════════════════════════════════════════════════════════

@dataclass
class ExecutionRecord:
    """One execution of a skill — the atom of the metrics log."""
    skill_name: str
    version_id: str
    execution_id: str
    timestamp: str
    inputs: Dict[str, Any]
    outputs: Dict[str, Any]
    metrics: Dict[str, float]
    composite_score: float
    duration_ms: float
    cost_usd: float
    user_feedback: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, line: str) -> "ExecutionRecord":
        return cls(**json.loads(line))


@dataclass
class SkillVersion:
    """A versioned skill — content + provenance + rolled-up metrics."""
    skill_name: str
    version_id: str            # v001, v002, ...
    created_at: str
    content: str               # the SKILL.md body
    parent_version: Optional[str] = None
    edit_summary: str = ""
    edit_source: str = "manual"   # manual | autoresearch | imported
    metrics_summary: Dict[str, float] = field(default_factory=dict)
    execution_count: int = 0
    promoted: bool = False
    notes: str = ""

    @property
    def short_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()[:12]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["short_hash"] = self.short_hash
        return d

    def diff_against(self, other: "SkillVersion") -> str:
        """Unified diff against another version."""
        return "".join(difflib.unified_diff(
            other.content.splitlines(keepends=True),
            self.content.splitlines(keepends=True),
            fromfile=f"{self.skill_name}@{other.version_id}",
            tofile=f"{self.skill_name}@{self.version_id}",
            lineterm="",
        ))


@dataclass
class TrainingEpoch:
    """One pass: evaluate a batch, decide whether to promote."""
    epoch_id: str
    skill_name: str
    candidate_version: str
    baseline_version: str
    batch_size: int
    started_at: str
    finished_at: Optional[str] = None
    candidate_score: float = 0.0
    baseline_score: float = 0.0
    decision: str = "pending"  # pending | promoted | rejected | inconclusive
    reason: str = ""

    @property
    def relative_improvement(self) -> float:
        if self.baseline_score <= 0:
            return 0.0
        return (self.candidate_score - self.baseline_score) / self.baseline_score


@dataclass
class ResearchFinding:
    """One autoresearch entry — what was tried, what was learned."""
    finding_id: str
    skill_name: str
    triggered_at: str
    trigger_reason: str          # "score_drop" | "manual" | "scheduled"
    rolling_mean: float
    best_score: float
    hypotheses: List[str]
    proposed_edits: List[Dict[str, str]]
    applied: bool = False
    applied_version: Optional[str] = None
    outcome: str = ""


# ════════════════════════════════════════════════════════════════════════
#  Scoring
# ════════════════════════════════════════════════════════════════════════

def normalize_latency(duration_ms: float, target_ms: float = DEFAULT_LATENCY_TARGET_MS) -> float:
    """Map latency to [0,1]. duration<=target → 1.0, decays exponentially after."""
    if duration_ms <= 0:
        return 1.0
    if duration_ms <= target_ms:
        return 1.0
    # Exponential decay beyond target — 2x target = ~0.5, 5x = ~0.1
    return math.exp(-(duration_ms - target_ms) / target_ms)


def normalize_cost(cost_usd: float, target_usd: float = DEFAULT_COST_TARGET_USD) -> float:
    if cost_usd <= 0:
        return 1.0
    if cost_usd <= target_usd:
        return 1.0
    return math.exp(-(cost_usd - target_usd) / max(target_usd, 1e-6))


def composite_score(metrics: Dict[str, float],
                    weights: Optional[Dict[str, float]] = None) -> float:
    """
    Weighted-sum composite score.

    Expected raw metrics (all in [0,1] or convertible):
        accuracy, user_satisfaction, completeness — pass through
        latency_ms — normalized via normalize_latency
        cost_usd  — normalized via normalize_cost
    """
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update(weights)
    # Normalize weights
    total = sum(w.values()) or 1.0
    w = {k: v / total for k, v in w.items()}

    parts: Dict[str, float] = {}
    parts["accuracy"] = float(metrics.get("accuracy", 0.0))
    parts["user_satisfaction"] = float(metrics.get("user_satisfaction", 0.0))
    parts["completeness"] = float(metrics.get("completeness", 0.0))
    parts["latency"] = normalize_latency(
        float(metrics.get("latency_ms", metrics.get("duration_ms", 0.0)))
    )
    parts["cost"] = normalize_cost(float(metrics.get("cost_usd", 0.0)))

    score = sum(w.get(k, 0.0) * parts.get(k, 0.0) for k in w)
    return max(SCORE_MIN, min(SCORE_MAX, score))


# ════════════════════════════════════════════════════════════════════════
#  Validation gate
# ════════════════════════════════════════════════════════════════════════

class ValidationGate:
    """
    Prevents regressions. A candidate is promoted only if it scores within
    REGRESSION_TOLERANCE * best, AND beats the immediate baseline.
    """

    def __init__(self, tolerance: float = REGRESSION_TOLERANCE):
        self.tolerance = tolerance

    def evaluate(self, candidate_score: float, baseline_score: float,
                 best_score: float) -> Tuple[bool, str]:
        if candidate_score < self.tolerance * best_score:
            return False, (
                f"regression — candidate {candidate_score:.3f} below "
                f"{self.tolerance:.0%} of best {best_score:.3f}"
            )
        if candidate_score < baseline_score:
            return False, (
                f"baseline beat — candidate {candidate_score:.3f} < "
                f"baseline {baseline_score:.3f}"
            )
        improvement = (candidate_score - baseline_score) / max(baseline_score, 1e-6)
        if improvement < 0.005:  # demand a real signal, not noise
            return True, (
                f"marginal pass — candidate {candidate_score:.3f} ~ "
                f"baseline {baseline_score:.3f} (within best tolerance)"
            )
        return True, (
            f"promoted — candidate {candidate_score:.3f} "
            f"({improvement:+.1%} vs baseline)"
        )


# ════════════════════════════════════════════════════════════════════════
#  Storage
# ════════════════════════════════════════════════════════════════════════

class SkillStorage:
    """File-backed store for one skill's versions, metrics, and artifacts."""

    def __init__(self, skill_name: str, root: Path = SKILLOPT_DIR):
        self.skill_name = skill_name
        self.dir = root / _safe_slug(skill_name)
        self.versions_dir = self.dir / "versions"
        self.metrics_path = self.dir / "metrics.jsonl"
        self.best_path = self.dir / "best_skill.md"
        self.config_path = self.dir / "config.json"
        self.research_path = self.dir / "research_log.jsonl"
        self._lock = threading.Lock()
        self._ensure()

    def _ensure(self):
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        if not self.config_path.exists():
            self.config_path.write_text(json.dumps({
                "skill_name": self.skill_name,
                "weights": DEFAULT_WEIGHTS,
                "latency_target_ms": DEFAULT_LATENCY_TARGET_MS,
                "cost_target_usd": DEFAULT_COST_TARGET_USD,
                "created_at": _now_iso(),
            }, indent=2), encoding="utf-8")

    # ── config ──
    def load_config(self) -> Dict[str, Any]:
        try:
            return json.loads(self.config_path.read_text("utf-8"))
        except Exception:
            return {}

    def save_config(self, cfg: Dict[str, Any]):
        self.config_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

    # ── versions ──
    def next_version_id(self) -> str:
        existing = sorted(self.versions_dir.glob("v*.md"))
        if not existing:
            return "v001"
        last = existing[-1].stem
        try:
            n = int(last[1:]) + 1
        except ValueError:
            n = len(existing) + 1
        return f"v{n:03d}"

    def write_version(self, ver: SkillVersion):
        path = self.versions_dir / f"{ver.version_id}.md"
        header = (
            f"<!-- skillopt-meta\n"
            f"version: {ver.version_id}\n"
            f"parent: {ver.parent_version or 'none'}\n"
            f"source: {ver.edit_source}\n"
            f"created_at: {ver.created_at}\n"
            f"hash: {ver.short_hash}\n"
            f"summary: {ver.edit_summary}\n"
            f"-->\n\n"
        )
        path.write_text(header + ver.content, encoding="utf-8")
        # Sidecar with rolled-up metrics
        sidecar = path.with_suffix(".json")
        sidecar.write_text(json.dumps(ver.to_dict(), indent=2), encoding="utf-8")

    def read_version(self, version_id: str) -> Optional[SkillVersion]:
        sidecar = self.versions_dir / f"{version_id}.json"
        if not sidecar.exists():
            return None
        try:
            data = json.loads(sidecar.read_text("utf-8"))
            data.pop("short_hash", None)
            return SkillVersion(**data)
        except Exception:
            return None

    def list_versions(self) -> List[SkillVersion]:
        out: List[SkillVersion] = []
        for p in sorted(self.versions_dir.glob("v*.json")):
            try:
                data = json.loads(p.read_text("utf-8"))
                data.pop("short_hash", None)
                out.append(SkillVersion(**data))
            except Exception:
                continue
        return out

    def latest_version(self) -> Optional[SkillVersion]:
        versions = self.list_versions()
        return versions[-1] if versions else None

    def best_version(self) -> Optional[SkillVersion]:
        versions = [v for v in self.list_versions() if v.promoted]
        if not versions:
            return self.latest_version()
        return max(versions, key=lambda v: v.metrics_summary.get("composite", 0.0))

    # ── metrics ──
    def append_execution(self, rec: ExecutionRecord):
        with self._lock:
            with self.metrics_path.open("a", encoding="utf-8") as f:
                f.write(rec.to_json() + "\n")

    def read_executions(self, limit: Optional[int] = None,
                        version_id: Optional[str] = None) -> List[ExecutionRecord]:
        if not self.metrics_path.exists():
            return []
        out: List[ExecutionRecord] = []
        with self.metrics_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = ExecutionRecord.from_json(line)
                except Exception:
                    continue
                if version_id and rec.version_id != version_id:
                    continue
                out.append(rec)
        if limit is not None:
            out = out[-limit:]
        return out

    def rolling_score(self, window: int = 10,
                      version_id: Optional[str] = None) -> Optional[float]:
        recs = self.read_executions(limit=window, version_id=version_id)
        if not recs:
            return None
        return statistics.mean(r.composite_score for r in recs)

    # ── best artifact ──
    def write_best_artifact(self, ver: SkillVersion, score: float, executions: int):
        body = (
            f"# {self.skill_name} — best version\n\n"
            f"- **Version:** `{ver.version_id}`  (hash `{ver.short_hash}`)\n"
            f"- **Composite score:** `{score:.4f}`\n"
            f"- **Executions:** `{executions}`\n"
            f"- **Promoted at:** `{_now_iso()}`\n"
            f"- **Parent:** `{ver.parent_version or '—'}`\n"
            f"- **Edit source:** `{ver.edit_source}`\n"
            f"- **Summary:** {ver.edit_summary or '_(none)_'}\n\n"
            f"---\n\n"
            f"{ver.content}\n"
        )
        self.best_path.write_text(body, encoding="utf-8")

    # ── research ──
    def append_finding(self, finding: ResearchFinding):
        with self._lock:
            with self.research_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(finding), ensure_ascii=False) + "\n")

    def read_findings(self, limit: Optional[int] = None) -> List[ResearchFinding]:
        if not self.research_path.exists():
            return []
        out: List[ResearchFinding] = []
        with self.research_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(ResearchFinding(**json.loads(line)))
                except Exception:
                    continue
        if limit is not None:
            out = out[-limit:]
        return out


# ════════════════════════════════════════════════════════════════════════
#  Auto-research loop
# ════════════════════════════════════════════════════════════════════════

class AutoResearchLoop:
    """
    Karpathy-style self-improvement:
      1. Watch the rolling mean of composite scores
      2. When it drops by AUTORESEARCH_DROP_THRESHOLD vs best, trigger
      3. Generate hypotheses (LLM-backed if researcher callable provided)
      4. Propose edits to the SKILL.md content
      5. Hand candidates to TrainingEpoch — validation gate decides
    """

    def __init__(self, engine: "SkillOptEngine",
                 researcher: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None):
        self.engine = engine
        self.researcher = researcher  # signature: (skill_name, context) -> {hypotheses, edits}

    def maybe_trigger(self, skill_name: str, reason: str = "score_drop") -> Optional[ResearchFinding]:
        storage = self.engine.storage(skill_name)
        best = storage.best_version()
        if not best:
            return None
        best_score = best.metrics_summary.get("composite", 0.0)
        rolling = storage.rolling_score(window=10) or 0.0

        if reason == "score_drop":
            drop = (best_score - rolling) / max(best_score, 1e-6)
            if drop < AUTORESEARCH_DROP_THRESHOLD:
                return None

        context = {
            "current_content": best.content,
            "rolling_mean": rolling,
            "best_score": best_score,
            "recent_executions": [
                asdict(r) for r in storage.read_executions(limit=10)
            ],
        }

        if self.researcher:
            try:
                research = self.researcher(skill_name, context) or {}
            except Exception as e:
                research = {"error": str(e)}
        else:
            research = self._heuristic_research(skill_name, context)

        finding = ResearchFinding(
            finding_id=str(uuid.uuid4())[:8],
            skill_name=skill_name,
            triggered_at=_now_iso(),
            trigger_reason=reason,
            rolling_mean=rolling,
            best_score=best_score,
            hypotheses=list(research.get("hypotheses", [])),
            proposed_edits=list(research.get("edits", [])),
        )
        storage.append_finding(finding)
        return finding

    def apply_finding(self, finding: ResearchFinding,
                      edit_index: int = 0) -> Optional[SkillVersion]:
        """Apply a proposed edit, creating a new candidate version."""
        if edit_index >= len(finding.proposed_edits):
            return None
        edit = finding.proposed_edits[edit_index]
        storage = self.engine.storage(finding.skill_name)
        best = storage.best_version()
        if not best:
            return None

        new_content = best.content
        op = edit.get("op", "replace")
        if op == "replace":
            new_content = edit.get("content", new_content)
        elif op == "patch":
            old, new = edit.get("from", ""), edit.get("to", "")
            if old and old in new_content:
                new_content = new_content.replace(old, new, 1)
        elif op == "append":
            new_content = new_content + "\n\n" + edit.get("content", "")

        if new_content == best.content:
            return None  # no-op

        version = self.engine.register_version(
            skill_name=finding.skill_name,
            content=new_content,
            parent_version=best.version_id,
            edit_summary=edit.get("summary", f"autoresearch {finding.finding_id}"),
            edit_source="autoresearch",
        )
        finding.applied = True
        finding.applied_version = version.version_id
        storage.append_finding(finding)  # second entry: applied trace
        return version

    @staticmethod
    def _heuristic_research(skill_name: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Cheap fallback when no LLM researcher is wired up."""
        recent = context.get("recent_executions", [])
        errors = [r for r in recent if r.get("error")]
        slow = [r for r in recent if r.get("duration_ms", 0) > DEFAULT_LATENCY_TARGET_MS * 2]
        hypotheses: List[str] = []
        edits: List[Dict[str, str]] = []
        if errors:
            hypotheses.append(
                f"{len(errors)}/{len(recent)} executions errored — add a guard clause"
            )
            edits.append({
                "op": "append",
                "summary": "add error-handling guard",
                "content": (
                    "\n## Error handling\n\n"
                    "Before invoking external services, validate inputs and fall back "
                    "to a clear error message rather than crashing.\n"
                ),
            })
        if slow:
            hypotheses.append(
                f"{len(slow)}/{len(recent)} executions exceeded 2x latency target — "
                "consider trimming prompt or caching"
            )
            edits.append({
                "op": "append",
                "summary": "trim prompt and add cache hint",
                "content": (
                    "\n## Performance\n\n"
                    "Keep the system prompt under 2KB. Reuse cached responses for "
                    "identical inputs within a 5-minute window.\n"
                ),
            })
        if not hypotheses:
            hypotheses.append("Quality drift with no obvious root cause — try clarifying "
                              "the success criteria.")
            edits.append({
                "op": "append",
                "summary": "sharpen success criteria",
                "content": (
                    "\n## Success criteria\n\n"
                    "The output must be self-contained, citable, and answer the "
                    "user's exact question in 3 paragraphs or less.\n"
                ),
            })
        return {"hypotheses": hypotheses, "edits": edits}


# ════════════════════════════════════════════════════════════════════════
#  Engine
# ════════════════════════════════════════════════════════════════════════

class SkillOptEngine:
    """
    Main orchestrator. Owns the lifecycle:
        register_skill → record_execution → maybe_train → promote_best
    """

    def __init__(self, root: Path = SKILLOPT_DIR,
                 researcher: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,
                 evaluator: Optional[Callable[[str, str, Dict[str, Any]], Dict[str, float]]] = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.gate = ValidationGate()
        self.research = AutoResearchLoop(self, researcher=researcher)
        self.evaluator = evaluator   # signature: (skill_name, version_content, eval_case) -> metrics
        self._storages: Dict[str, SkillStorage] = {}
        self._lock = threading.Lock()

    # ── storage handles ──
    def storage(self, skill_name: str) -> SkillStorage:
        with self._lock:
            store = self._storages.get(skill_name)
            if store is None:
                store = SkillStorage(skill_name, root=self.root)
                self._storages[skill_name] = store
            return store

    def list_skills(self) -> List[str]:
        if not self.root.exists():
            return []
        return sorted([p.name for p in self.root.iterdir() if p.is_dir()])

    # ── registration ──
    def register_skill(self, skill_name: str, content: str,
                       weights: Optional[Dict[str, float]] = None,
                       latency_target_ms: float = DEFAULT_LATENCY_TARGET_MS,
                       cost_target_usd: float = DEFAULT_COST_TARGET_USD,
                       notes: str = "") -> SkillVersion:
        storage = self.storage(skill_name)
        cfg = storage.load_config()
        if weights:
            cfg["weights"] = weights
        cfg["latency_target_ms"] = latency_target_ms
        cfg["cost_target_usd"] = cost_target_usd
        storage.save_config(cfg)

        latest = storage.latest_version()
        if latest and latest.content.strip() == content.strip():
            return latest  # idempotent

        return self.register_version(
            skill_name=skill_name,
            content=content,
            parent_version=latest.version_id if latest else None,
            edit_summary="initial registration" if not latest else "manual update",
            edit_source="manual",
            notes=notes,
        )

    def register_version(self, skill_name: str, content: str,
                         parent_version: Optional[str],
                         edit_summary: str = "",
                         edit_source: str = "manual",
                         notes: str = "") -> SkillVersion:
        storage = self.storage(skill_name)
        version_id = storage.next_version_id()
        ver = SkillVersion(
            skill_name=skill_name,
            version_id=version_id,
            created_at=_now_iso(),
            content=content,
            parent_version=parent_version,
            edit_summary=edit_summary,
            edit_source=edit_source,
            notes=notes,
        )
        # First-ever version is auto-promoted so we have a baseline.
        if parent_version is None:
            ver.promoted = True
        storage.write_version(ver)
        if ver.promoted:
            storage.write_best_artifact(ver, score=0.0, executions=0)
        return ver

    # ── execution recording ──
    def record_execution(self, skill_name: str, version_id: str,
                         inputs: Dict[str, Any], outputs: Dict[str, Any],
                         metrics: Dict[str, float],
                         duration_ms: float, cost_usd: float = 0.0,
                         user_feedback: Optional[Dict[str, Any]] = None,
                         error: Optional[str] = None) -> ExecutionRecord:
        storage = self.storage(skill_name)
        cfg = storage.load_config()
        weights = cfg.get("weights", DEFAULT_WEIGHTS)

        merged = dict(metrics)
        merged.setdefault("latency_ms", duration_ms)
        merged.setdefault("cost_usd", cost_usd)
        score = composite_score(merged, weights=weights)

        rec = ExecutionRecord(
            skill_name=skill_name,
            version_id=version_id,
            execution_id=str(uuid.uuid4())[:8],
            timestamp=_now_iso(),
            inputs=_safe_jsonify(inputs),
            outputs=_safe_jsonify(outputs),
            metrics=merged,
            composite_score=score,
            duration_ms=duration_ms,
            cost_usd=cost_usd,
            user_feedback=user_feedback,
            error=error,
        )
        storage.append_execution(rec)
        self._refresh_version_summary(skill_name, version_id)
        return rec

    def _refresh_version_summary(self, skill_name: str, version_id: str):
        storage = self.storage(skill_name)
        ver = storage.read_version(version_id)
        if not ver:
            return
        execs = storage.read_executions(version_id=version_id)
        if not execs:
            return
        ver.execution_count = len(execs)
        ver.metrics_summary = {
            "composite": statistics.mean(e.composite_score for e in execs),
            "accuracy": statistics.mean(e.metrics.get("accuracy", 0.0) for e in execs),
            "user_satisfaction": statistics.mean(
                e.metrics.get("user_satisfaction", 0.0) for e in execs
            ),
            "completeness": statistics.mean(
                e.metrics.get("completeness", 0.0) for e in execs
            ),
            "latency_ms_p50": _percentile([e.duration_ms for e in execs], 50),
            "latency_ms_p95": _percentile([e.duration_ms for e in execs], 95),
            "cost_usd_mean": statistics.mean(e.cost_usd for e in execs),
            "error_rate": sum(1 for e in execs if e.error) / len(execs),
        }
        storage.write_version(ver)

    # ── training ──
    def run_epoch(self, skill_name: str, candidate_version: str,
                  eval_batch: List[Dict[str, Any]]) -> TrainingEpoch:
        """
        Score candidate over a batch of eval cases.

        eval_batch entries should look like:
            {"inputs": {...}, "expected": {...}, "rubric": {...}}

        Requires self.evaluator to be wired up — otherwise scores stay at 0.
        """
        storage = self.storage(skill_name)
        candidate = storage.read_version(candidate_version)
        baseline = storage.best_version()
        if not candidate or not baseline:
            raise ValueError(f"missing candidate or baseline for {skill_name}")

        epoch = TrainingEpoch(
            epoch_id=str(uuid.uuid4())[:8],
            skill_name=skill_name,
            candidate_version=candidate.version_id,
            baseline_version=baseline.version_id,
            batch_size=len(eval_batch),
            started_at=_now_iso(),
        )

        cand_scores: List[float] = []
        base_scores: List[float] = []

        for case in eval_batch:
            if self.evaluator:
                try:
                    cand_metrics = self.evaluator(skill_name, candidate.content, case)
                    base_metrics = self.evaluator(skill_name, baseline.content, case)
                except Exception as e:
                    cand_metrics = {"accuracy": 0.0}
                    base_metrics = {"accuracy": 0.0}
                    epoch.reason = f"evaluator error: {e}"
            else:
                # Without an evaluator we can only rely on previously logged data.
                cand_metrics = {"accuracy": candidate.metrics_summary.get("accuracy", 0.0)}
                base_metrics = {"accuracy": baseline.metrics_summary.get("accuracy", 0.0)}
            cfg = storage.load_config()
            weights = cfg.get("weights", DEFAULT_WEIGHTS)
            cand_scores.append(composite_score(cand_metrics, weights))
            base_scores.append(composite_score(base_metrics, weights))

        epoch.candidate_score = statistics.mean(cand_scores) if cand_scores else 0.0
        epoch.baseline_score = statistics.mean(base_scores) if base_scores else 0.0
        epoch.finished_at = _now_iso()

        best_score = baseline.metrics_summary.get("composite", epoch.baseline_score)
        ok, reason = self.gate.evaluate(
            candidate_score=epoch.candidate_score,
            baseline_score=epoch.baseline_score,
            best_score=best_score,
        )
        epoch.decision = "promoted" if ok else "rejected"
        epoch.reason = reason

        if ok:
            self._promote(skill_name, candidate.version_id, epoch.candidate_score)

        return epoch

    def _promote(self, skill_name: str, version_id: str, score: float):
        storage = self.storage(skill_name)
        # Demote any previously-promoted versions
        for v in storage.list_versions():
            if v.promoted and v.version_id != version_id:
                v.promoted = False
                storage.write_version(v)
        ver = storage.read_version(version_id)
        if not ver:
            return
        ver.promoted = True
        ver.metrics_summary["composite"] = score
        storage.write_version(ver)
        storage.write_best_artifact(ver, score=score,
                                    executions=ver.execution_count)

    # ── high-level conveniences ──
    def status_snapshot(self, skill_name: str) -> Dict[str, Any]:
        storage = self.storage(skill_name)
        best = storage.best_version()
        latest = storage.latest_version()
        recent = storage.read_executions(limit=50)
        rolling = storage.rolling_score(window=10) or 0.0
        return {
            "skill_name": skill_name,
            "best_version": best.version_id if best else None,
            "best_score": best.metrics_summary.get("composite", 0.0) if best else 0.0,
            "latest_version": latest.version_id if latest else None,
            "version_count": len(storage.list_versions()),
            "execution_count": len(recent),
            "rolling_mean": rolling,
            "last_execution": recent[-1].timestamp if recent else None,
            "open_findings": len([f for f in storage.read_findings() if not f.applied]),
        }

    def fleet_status(self) -> List[Dict[str, Any]]:
        return [self.status_snapshot(s) for s in self.list_skills()]

    def trend_series(self, skill_name: str, window: int = 100) -> List[Tuple[str, float]]:
        """Return (timestamp, score) pairs for plotting."""
        recs = self.storage(skill_name).read_executions(limit=window)
        return [(r.timestamp, r.composite_score) for r in recs]


# ════════════════════════════════════════════════════════════════════════
#  Utilities
# ════════════════════════════════════════════════════════════════════════

def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


_SLUG_RE = re.compile(r"[^a-z0-9_\-]+")


def _safe_slug(name: str) -> str:
    s = name.strip().lower().replace(" ", "_")
    s = _SLUG_RE.sub("", s)
    return s or "unnamed_skill"


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (pct / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return s[int(k)]
    return s[f] + (s[c] - s[f]) * (k - f)


def _safe_jsonify(obj: Any, depth: int = 0) -> Any:
    """Best-effort conversion to JSON-safe primitives."""
    if depth > 6:
        return repr(obj)[:200]
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _safe_jsonify(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_safe_jsonify(v, depth + 1) for v in obj]
    if dataclasses.is_dataclass(obj):
        return _safe_jsonify(asdict(obj), depth + 1)
    return repr(obj)[:200]


# ════════════════════════════════════════════════════════════════════════
#  Public API helpers — what server.py and the UI use
# ════════════════════════════════════════════════════════════════════════

_engine_singleton: Optional[SkillOptEngine] = None
_engine_lock = threading.Lock()


def get_engine() -> SkillOptEngine:
    """Lazy singleton — server.py and the observatory talk to one engine."""
    global _engine_singleton
    with _engine_lock:
        if _engine_singleton is None:
            _engine_singleton = SkillOptEngine()
        return _engine_singleton


def export_fleet_state() -> Dict[str, Any]:
    """JSON-safe snapshot for the Observatory UI."""
    engine = get_engine()
    skills = engine.list_skills()
    fleet: List[Dict[str, Any]] = []
    for name in skills:
        snap = engine.status_snapshot(name)
        storage = engine.storage(name)
        versions = storage.list_versions()
        findings = storage.read_findings(limit=10)
        trend = engine.trend_series(name, window=100)
        fleet.append({
            **snap,
            "versions": [v.to_dict() for v in versions],
            "trend": [{"t": t, "score": s} for t, s in trend],
            "research": [asdict(f) for f in findings],
        })
    return {
        "generated_at": _now_iso(),
        "skill_count": len(skills),
        "skills": fleet,
    }


def record_skill_run(skill_name: str, inputs: Dict[str, Any],
                     outputs: Dict[str, Any], metrics: Dict[str, float],
                     duration_ms: float, cost_usd: float = 0.0,
                     user_feedback: Optional[Dict[str, Any]] = None,
                     error: Optional[str] = None) -> ExecutionRecord:
    """Convenience hook called from inside server.py tool executors."""
    engine = get_engine()
    storage = engine.storage(skill_name)
    best = storage.best_version() or storage.latest_version()
    if not best:
        # Auto-register an empty version so we can record anyway
        best = engine.register_skill(
            skill_name=skill_name,
            content=f"# {skill_name}\n\n_(auto-registered on first run)_\n",
        )
    return engine.record_execution(
        skill_name=skill_name,
        version_id=best.version_id,
        inputs=inputs,
        outputs=outputs,
        metrics=metrics,
        duration_ms=duration_ms,
        cost_usd=cost_usd,
        user_feedback=user_feedback,
        error=error,
    )


def maybe_autoresearch(skill_name: str) -> Optional[Dict[str, Any]]:
    """Called periodically (e.g. nightly) — triggers research if scores dropped."""
    finding = get_engine().research.maybe_trigger(skill_name, reason="score_drop")
    return asdict(finding) if finding else None


# ════════════════════════════════════════════════════════════════════════
#  CLI for quick inspection: python skillopt_engine.py status
# ════════════════════════════════════════════════════════════════════════

def _cli_main():
    import argparse
    parser = argparse.ArgumentParser(prog="skillopt_engine")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("status", help="Show fleet status")

    p_show = sub.add_parser("show", help="Show a skill in detail")
    p_show.add_argument("skill_name")

    p_versions = sub.add_parser("versions", help="List versions of a skill")
    p_versions.add_argument("skill_name")

    p_export = sub.add_parser("export", help="Dump fleet state as JSON")

    p_register = sub.add_parser("register", help="Register a skill from a file")
    p_register.add_argument("skill_name")
    p_register.add_argument("path")

    args = parser.parse_args()
    engine = get_engine()

    if args.cmd == "status" or args.cmd is None:
        skills = engine.list_skills()
        if not skills:
            print("(no skills registered yet)")
            return
        for s in skills:
            snap = engine.status_snapshot(s)
            print(f"  {s:30s}  best={snap['best_version']:>5}  "
                  f"score={snap['best_score']:.3f}  "
                  f"runs={snap['execution_count']:>4}  "
                  f"rolling={snap['rolling_mean']:.3f}")
    elif args.cmd == "show":
        snap = engine.status_snapshot(args.skill_name)
        print(json.dumps(snap, indent=2))
    elif args.cmd == "versions":
        for v in engine.storage(args.skill_name).list_versions():
            print(f"  {v.version_id:>5s}  promoted={'Y' if v.promoted else 'n'}  "
                  f"runs={v.execution_count:>4}  "
                  f"score={v.metrics_summary.get('composite', 0.0):.3f}  "
                  f"{v.edit_summary}")
    elif args.cmd == "export":
        print(json.dumps(export_fleet_state(), indent=2))
    elif args.cmd == "register":
        content = Path(args.path).read_text(encoding="utf-8")
        ver = engine.register_skill(args.skill_name, content)
        print(f"registered {args.skill_name} @ {ver.version_id} ({ver.short_hash})")


if __name__ == "__main__":
    _cli_main()
