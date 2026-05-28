"""
Liquid UI — Friday's self-evolving interface system.
FutureSpeak.AI · Asimov's Mind

The UI literally reshapes itself around each user.

Pipeline:
    intent signal ──▶ FeatureSpecGenerator ──▶ tier classification
                          │
                          ▼
                  LiquidUIBuilder ──▶ React + backend artifacts
                          │
                          ▼
                  snapshot ──▶ hot reload ──▶ SkillOpt usage tracking
                          │
                          ▼
                  SuggestEngine watches behavior, proposes evolutions

Storage layout:
    ~/.friday/liquid_ui/
        requests.jsonl         append-only intent log
        features/<id>.json     spec + build artifact metadata
        snapshots/<n>/         rollback snapshots (every change → snapshot)
        usage.jsonl            per-feature usage events
        suggestions.jsonl      proactive suggestions surfaced

Complexity tiers — guide auto-vs-confirm behavior:
    trivial  (<1 min)         auto-approved, hot-reloaded
    simple   (1–5 min)        quick confirm modal
    medium   (5–30 min)       spec review with edits
    complex  (30–120 min)     detailed review, may spawn a task
    epic     (2+ hours)       full spec + roadmap, multi-step delivery

Every Liquid UI feature is also a SkillOpt skill — usage and "value score"
feed into the same versioning/promotion/deprecation loop.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
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
LIQUID_DIR = FRIDAY_DIR / "liquid_ui"
REQUESTS_PATH = LIQUID_DIR / "requests.jsonl"
USAGE_PATH = LIQUID_DIR / "usage.jsonl"
SUGGESTIONS_PATH = LIQUID_DIR / "suggestions.jsonl"
FEATURES_DIR = LIQUID_DIR / "features"
SNAPSHOTS_DIR = LIQUID_DIR / "snapshots"

# Tiers — keyed by complexity score [0, 1].
# Trivial under 0.10, simple 0.10–0.30, medium 0.30–0.55, complex 0.55–0.80, epic 0.80+.
TIER_THRESHOLDS = [
    (0.10, "trivial"),
    (0.30, "simple"),
    (0.55, "medium"),
    (0.80, "complex"),
    (1.01, "epic"),
]

TIER_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "trivial": {"max_seconds": 60,    "auto_approve": True,  "needs_review": False, "review_mode": "none"},
    "simple":  {"max_seconds": 300,   "auto_approve": False, "needs_review": True,  "review_mode": "quick_confirm"},
    "medium":  {"max_seconds": 1800,  "auto_approve": False, "needs_review": True,  "review_mode": "spec_review"},
    "complex": {"max_seconds": 7200,  "auto_approve": False, "needs_review": True,  "review_mode": "detailed_review"},
    "epic":    {"max_seconds": 14400, "auto_approve": False, "needs_review": True,  "review_mode": "full_spec_roadmap"},
}

# Rollback window — Ctrl+Z within this is one click, beyond is a Settings revert.
QUICK_REVERT_SECONDS = 30
SNAPSHOT_RETENTION_DAYS = 60

# Suggest engine — minimum signal strength to surface
SUGGEST_MIN_OCCURRENCES = 4
SUGGEST_LOOKBACK_EVENTS = 200


log = logging.getLogger("friday.liquid_ui")


# ════════════════════════════════════════════════════════════════════════
#  Data classes
# ════════════════════════════════════════════════════════════════════════

@dataclass
class LiquidUIRequest:
    """
    Captures intent. May be explicit ("I wish I could...") or behavioral
    (repeated context switching, error-recovery loops, dead clicks).
    """
    request_id: str = field(default_factory=lambda: f"req_{uuid.uuid4().hex[:10]}")
    created_at: str = field(default_factory=lambda: _now_iso())
    source: str = "explicit"          # explicit | behavioral | suggest_engine
    workspace: str = ""               # which workspace the user was in
    user_text: str = ""               # raw "I wish" / chat-derived text
    signals: Dict[str, Any] = field(default_factory=dict)  # behavioral evidence
    priority_hint: str = "normal"     # low | normal | high
    related_request_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FeatureSpec:
    """Structured spec — what to build, at what tier, with what shape."""
    feature_id: str = field(default_factory=lambda: f"feat_{uuid.uuid4().hex[:10]}")
    request_id: str = ""
    created_at: str = field(default_factory=lambda: _now_iso())
    title: str = ""
    description: str = ""
    complexity_tier: str = "medium"   # trivial | simple | medium | complex | epic
    complexity_score: float = 0.5     # raw [0,1]
    estimated_seconds: int = 600
    workspace_target: str = ""        # which workspace gets the feature
    data_model: Dict[str, Any] = field(default_factory=dict)
    ui_components: List[Dict[str, Any]] = field(default_factory=list)
    integrations: List[str] = field(default_factory=list)   # gmail, calendar, ...
    backend_routes: List[Dict[str, str]] = field(default_factory=list)
    state_flow: List[str] = field(default_factory=list)
    success_metrics: Dict[str, float] = field(default_factory=dict)
    open_questions: List[str] = field(default_factory=list)
    rollback_plan: str = "auto-snapshot + Ctrl+Z within 30s; Settings → revert beyond that"
    notes: str = ""

    @property
    def tier_policy(self) -> Dict[str, Any]:
        return TIER_DEFAULTS.get(self.complexity_tier, TIER_DEFAULTS["medium"])

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["tier_policy"] = self.tier_policy
        return d


@dataclass
class BuildArtifact:
    """Output of LiquidUIBuilder for a spec."""
    feature_id: str
    built_at: str = field(default_factory=lambda: _now_iso())
    react_component: str = ""           # JSX/TSX source
    css_overrides: str = ""
    backend_handlers: str = ""          # Python source (Flask routes)
    data_model_source: str = ""
    integration_glue: Dict[str, str] = field(default_factory=dict)
    files_written: List[str] = field(default_factory=list)
    snapshot_id: Optional[str] = None
    hot_reload_token: Optional[str] = None
    status: str = "built"               # built | live | shelved | reverted
    error: Optional[str] = None
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class UsageEvent:
    """One observed interaction with a Liquid UI feature."""
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    feature_id: str = ""
    workspace: str = ""
    kind: str = "view"                  # view | click | submit | abandon | error
    at: str = field(default_factory=lambda: _now_iso())
    payload: Dict[str, Any] = field(default_factory=dict)
    dwell_ms: Optional[int] = None
    user_satisfied: Optional[bool] = None


@dataclass
class Suggestion:
    """A proactive proposal from SuggestEngine."""
    suggestion_id: str = field(default_factory=lambda: f"sug_{uuid.uuid4().hex[:8]}")
    created_at: str = field(default_factory=lambda: _now_iso())
    workspace: str = ""
    title: str = ""
    rationale: str = ""
    signals: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    proposed_spec: Optional[Dict[str, Any]] = None
    status: str = "open"                # open | accepted | dismissed | snoozed
    decided_at: Optional[str] = None


# ════════════════════════════════════════════════════════════════════════
#  Storage helpers
# ════════════════════════════════════════════════════════════════════════

class LiquidStore:
    """File-backed store. Thread-safe via internal lock."""

    _lock = threading.Lock()

    def __init__(self, root: Path = LIQUID_DIR):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        FEATURES_DIR.mkdir(parents=True, exist_ok=True)
        SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    # ── requests ──
    def append_request(self, req: LiquidUIRequest):
        with self._lock:
            with REQUESTS_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(req.to_dict(), ensure_ascii=False) + "\n")

    def list_requests(self, limit: Optional[int] = None) -> List[LiquidUIRequest]:
        out: List[LiquidUIRequest] = []
        if not REQUESTS_PATH.exists():
            return out
        with REQUESTS_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(LiquidUIRequest(**json.loads(line)))
                except Exception:
                    continue
        if limit is not None:
            out = out[-limit:]
        return out

    # ── features ──
    def write_feature(self, spec: FeatureSpec, artifact: Optional[BuildArtifact] = None):
        path = FEATURES_DIR / f"{spec.feature_id}.json"
        data = {"spec": spec.to_dict()}
        if artifact:
            data["artifact"] = artifact.to_dict()
        # Atomic write
        tmp = tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent,
                                          suffix=".tmp", encoding="utf-8")
        try:
            json.dump(data, tmp, indent=2, ensure_ascii=False)
            tmp.flush(); os.fsync(tmp.fileno()); tmp.close()
            os.replace(tmp.name, path)
        except Exception:
            tmp.close()
            try: os.unlink(tmp.name)
            except OSError: pass
            raise

    def read_feature(self, feature_id: str) -> Optional[Dict[str, Any]]:
        path = FEATURES_DIR / f"{feature_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text("utf-8"))
        except Exception:
            return None

    def list_features(self, *, status: Optional[str] = None) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for p in sorted(FEATURES_DIR.glob("feat_*.json")):
            try:
                data = json.loads(p.read_text("utf-8"))
            except Exception:
                continue
            art = data.get("artifact") or {}
            if status and art.get("status") != status:
                continue
            out.append(data)
        out.sort(key=lambda d: d["spec"].get("created_at", ""), reverse=True)
        return out

    # ── usage ──
    def append_usage(self, event: UsageEvent):
        with self._lock:
            with USAGE_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")

    def read_usage(self, limit: int = SUGGEST_LOOKBACK_EVENTS) -> List[UsageEvent]:
        if not USAGE_PATH.exists():
            return []
        out: List[UsageEvent] = []
        with USAGE_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(UsageEvent(**json.loads(line)))
                except Exception:
                    continue
        return out[-limit:]

    # ── suggestions ──
    def append_suggestion(self, s: Suggestion):
        with self._lock:
            with SUGGESTIONS_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(s), ensure_ascii=False) + "\n")

    def list_suggestions(self, *, status: Optional[str] = None,
                         limit: Optional[int] = None) -> List[Suggestion]:
        if not SUGGESTIONS_PATH.exists():
            return []
        out: List[Suggestion] = []
        with SUGGESTIONS_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    s = Suggestion(**json.loads(line))
                except Exception:
                    continue
                if status and s.status != status:
                    continue
                out.append(s)
        if limit is not None:
            out = out[-limit:]
        return out


# ════════════════════════════════════════════════════════════════════════
#  Tier classification
# ════════════════════════════════════════════════════════════════════════

# Heuristic keyword weights — high score = more complex.
_COMPLEXITY_KEYWORDS = {
    "rename": 0.05, "color": 0.05, "label": 0.05, "tooltip": 0.05,
    "move": 0.08, "hide": 0.08, "show": 0.08,
    "add a button": 0.12, "add button": 0.12, "shortcut": 0.12,
    "filter": 0.18, "sort": 0.18, "search": 0.2, "tag": 0.2,
    "panel": 0.3, "widget": 0.3, "card": 0.25,
    "workspace": 0.5, "garden": 0.5, "view": 0.4,
    "integration": 0.55, "connect": 0.5, "sync": 0.55,
    "automation": 0.6, "auto": 0.45, "scheduler": 0.6,
    "graph": 0.45, "chart": 0.4, "dashboard": 0.45,
    "voice": 0.55, "vision": 0.55, "agent": 0.55,
    "platform": 0.8, "framework": 0.85, "engine": 0.7,
}


def classify_complexity(text: str, signals: Optional[Dict[str, Any]] = None) -> Tuple[str, float]:
    """
    Returns (tier_name, complexity_score in [0,1]).

    Heuristic — token-level scoring with simple boosters from behavioral
    signals. A real install can plug in an LLM-backed classifier by passing
    `complexity_fn=` into FeatureSpecGenerator.
    """
    lower = (text or "").lower()
    score = 0.0
    matched: List[str] = []
    for kw, w in _COMPLEXITY_KEYWORDS.items():
        if kw in lower:
            matched.append(kw)
            score = max(score, w)

    # Length-based booster (longer requests usually mean larger scope)
    words = max(len(lower.split()), 1)
    score += min(words / 200.0, 0.20)

    # Behavioral signals tilt heavier
    if signals:
        if signals.get("touches_multiple_workspaces"):
            score += 0.10
        if signals.get("requires_new_data_model"):
            score += 0.15
        if signals.get("requires_external_integration"):
            score += 0.10
        if signals.get("involves_voice_or_vision"):
            score += 0.10

    score = max(0.0, min(1.0, score))
    for thresh, name in TIER_THRESHOLDS:
        if score < thresh:
            return name, round(score, 3)
    return "epic", round(score, 3)


# ════════════════════════════════════════════════════════════════════════
#  Feature spec generator
# ════════════════════════════════════════════════════════════════════════

class FeatureSpecGenerator:
    """
    Turns intent → structured spec.

    Pluggable LLM hook: pass `synthesize=` to override the heuristic
    spec scaffold with a real model-generated one. Without it, a
    structurally-correct stub is returned that downstream stages can act on.
    """

    def __init__(self, synthesize: Optional[Callable[[LiquidUIRequest], Dict[str, Any]]] = None,
                 complexity_fn: Optional[Callable[[str, Dict[str, Any]], Tuple[str, float]]] = None):
        self.synthesize = synthesize
        self.complexity_fn = complexity_fn or classify_complexity

    def generate(self, req: LiquidUIRequest) -> FeatureSpec:
        signals = req.signals or {}
        tier, score = self.complexity_fn(req.user_text, signals)
        policy = TIER_DEFAULTS.get(tier, TIER_DEFAULTS["medium"])

        if self.synthesize:
            try:
                payload = self.synthesize(req) or {}
            except Exception as e:
                log.warning("synthesize failed: %s", e)
                payload = {}
        else:
            payload = self._heuristic_synth(req)

        spec = FeatureSpec(
            request_id=req.request_id,
            title=payload.get("title") or self._title_from_text(req.user_text),
            description=payload.get("description") or req.user_text.strip(),
            complexity_tier=payload.get("complexity_tier", tier),
            complexity_score=float(payload.get("complexity_score", score)),
            estimated_seconds=int(payload.get("estimated_seconds", policy["max_seconds"])),
            workspace_target=payload.get("workspace_target", req.workspace),
            data_model=payload.get("data_model", {}),
            ui_components=payload.get("ui_components", []),
            integrations=payload.get("integrations", []),
            backend_routes=payload.get("backend_routes", []),
            state_flow=payload.get("state_flow", []),
            success_metrics=payload.get("success_metrics", {
                "usage_per_week": 1.0,
                "dwell_seconds": 5.0,
                "user_satisfaction": 0.7,
            }),
            open_questions=payload.get("open_questions", []),
            notes=payload.get("notes", ""),
        )
        return spec

    @staticmethod
    def _title_from_text(text: str) -> str:
        text = (text or "").strip()
        # Strip "I wish I could / I want / make / add" prefixes for a cleaner title
        text = re.sub(r"^(i wish (i could )?|i want (to )?|please |can you |make |add )",
                      "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+", " ", text)
        if not text:
            return "Untitled feature"
        # First clause, capitalize
        first = re.split(r"[.,;:!?]", text, 1)[0].strip()
        if len(first) > 80:
            first = first[:77] + "…"
        return first[:1].upper() + first[1:]

    @staticmethod
    def _heuristic_synth(req: LiquidUIRequest) -> Dict[str, Any]:
        """Cheap structural fill when no LLM synthesizer is wired up."""
        text = (req.user_text or "").lower()
        ui_components: List[Dict[str, Any]] = []
        backend_routes: List[Dict[str, str]] = []
        integrations: List[str] = []

        if any(k in text for k in ["panel", "widget", "card", "dashboard"]):
            ui_components.append({
                "kind": "Card",
                "name": "FeatureCard",
                "props": {"title": "string", "items": "array", "actions": "array"},
            })
        if any(k in text for k in ["table", "list", "rows"]):
            ui_components.append({
                "kind": "Table",
                "name": "FeatureTable",
                "props": {"columns": "array", "rows": "array"},
            })
        if any(k in text for k in ["filter", "search", "find"]):
            ui_components.append({
                "kind": "SearchBar",
                "name": "FeatureSearch",
                "props": {"placeholder": "string", "onSearch": "function"},
            })
        if any(k in text for k in ["form", "submit", "send"]):
            ui_components.append({
                "kind": "Form",
                "name": "FeatureForm",
                "props": {"fields": "array", "onSubmit": "function"},
            })
            backend_routes.append({"method": "POST", "path": "/api/liquid/<feature_id>/submit"})

        if "gmail" in text or "email" in text:
            integrations.append("gmail")
        if "calendar" in text or "schedule" in text:
            integrations.append("calendar")
        if "slack" in text:
            integrations.append("slack")
        if "ofw" in text or "our family wizard" in text:
            integrations.append("ofw")

        # Always offer a default read route
        backend_routes.append({"method": "GET", "path": "/api/liquid/<feature_id>"})

        return {
            "ui_components": ui_components or [{
                "kind": "Panel",
                "name": "FeaturePanel",
                "props": {"title": "string"},
            }],
            "backend_routes": backend_routes,
            "integrations": integrations,
            "state_flow": [
                "initial → loading → populated",
                "interaction → optimistic update → persisted",
            ],
            "open_questions": _open_questions_for(req),
        }


def _open_questions_for(req: LiquidUIRequest) -> List[str]:
    q: List[str] = []
    if not req.workspace:
        q.append("Which workspace should this live in (Personal/Professional/Creative/Infra)?")
    if "every" in (req.user_text or "").lower() or "daily" in (req.user_text or "").lower():
        q.append("How often should this run? Any quiet hours?")
    if not req.signals.get("data_volume"):
        q.append("Roughly how many items will this hold (10s? 1000s?)")
    return q


# ════════════════════════════════════════════════════════════════════════
#  Snapshots — rollback core
# ════════════════════════════════════════════════════════════════════════

class SnapshotManager:
    """
    Every Liquid UI change creates a snapshot of touched files. Ctrl+Z within
    QUICK_REVERT_SECONDS triggers an immediate revert; beyond that, Settings
    exposes the full history with one-click revert.
    """

    def __init__(self, root: Path = SNAPSHOTS_DIR):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, feature_id: str, files: Iterable[Path],
               note: str = "") -> str:
        snap_id = f"snap_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
        snap_dir = self.root / snap_id
        snap_dir.mkdir(parents=True, exist_ok=True)
        manifest: Dict[str, Any] = {
            "snapshot_id": snap_id,
            "feature_id": feature_id,
            "created_at": _now_iso(),
            "note": note,
            "files": [],
        }
        for p in files:
            p = Path(p)
            if not p.exists():
                continue
            rel = _safe_name(str(p))
            dest = snap_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(p, dest)
                manifest["files"].append({
                    "path": str(p),
                    "stored_as": rel,
                    "size": p.stat().st_size,
                    "sha256": _sha256_of(p),
                })
            except Exception as e:
                log.warning("snapshot copy failed for %s: %s", p, e)
        (snap_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        self._gc()
        return snap_id

    def list(self, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for p in sorted(self.root.glob("snap_*/manifest.json"), reverse=True):
            try:
                out.append(json.loads(p.read_text("utf-8")))
            except Exception:
                continue
        if limit is not None:
            out = out[:limit]
        return out

    def revert(self, snapshot_id: str) -> Tuple[bool, str]:
        snap_dir = self.root / snapshot_id
        manifest_path = snap_dir / "manifest.json"
        if not manifest_path.exists():
            return False, "snapshot not found"
        try:
            manifest = json.loads(manifest_path.read_text("utf-8"))
        except Exception as e:
            return False, f"manifest unreadable: {e}"

        restored: List[str] = []
        for entry in manifest.get("files", []):
            stored = snap_dir / entry["stored_as"]
            target = Path(entry["path"])
            if not stored.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(stored, target)
                restored.append(entry["path"])
            except Exception as e:
                log.warning("revert failed for %s: %s", target, e)
        return True, f"restored {len(restored)} file(s)"

    def quick_revert_eligible(self, snapshot_id: str) -> bool:
        snap_dir = self.root / snapshot_id
        manifest_path = snap_dir / "manifest.json"
        if not manifest_path.exists():
            return False
        try:
            manifest = json.loads(manifest_path.read_text("utf-8"))
            created = datetime.fromisoformat(manifest["created_at"].rstrip("Z"))
            age = (datetime.utcnow() - created).total_seconds()
            return age <= QUICK_REVERT_SECONDS
        except Exception:
            return False

    def _gc(self):
        """Drop snapshots older than retention."""
        cutoff = datetime.utcnow() - timedelta(days=SNAPSHOT_RETENTION_DAYS)
        for p in self.root.glob("snap_*"):
            manifest = p / "manifest.json"
            if not manifest.exists():
                continue
            try:
                m = json.loads(manifest.read_text("utf-8"))
                created = datetime.fromisoformat(m["created_at"].rstrip("Z"))
                if created < cutoff:
                    shutil.rmtree(p, ignore_errors=True)
            except Exception:
                continue


# ════════════════════════════════════════════════════════════════════════
#  Builder — generates code + writes artifacts
# ════════════════════════════════════════════════════════════════════════

class LiquidUIBuilder:
    """
    Builds Liquid UI features from specs.

    Output is intentionally separated into a workspace dir
    (~/.friday/liquid_ui/features/<id>/) so server.py can hot-load
    without touching the source tree. The artifact JSON references the
    generated files via absolute paths.
    """

    def __init__(self, store: Optional[LiquidStore] = None,
                 snapshots: Optional[SnapshotManager] = None,
                 react_generator: Optional[Callable[[FeatureSpec], str]] = None,
                 backend_generator: Optional[Callable[[FeatureSpec], str]] = None):
        self.store = store or LiquidStore()
        self.snapshots = snapshots or SnapshotManager()
        self.react_generator = react_generator
        self.backend_generator = backend_generator

    def build(self, spec: FeatureSpec, *, dry_run: bool = False) -> BuildArtifact:
        feature_dir = LIQUID_DIR / "features" / spec.feature_id
        feature_dir.mkdir(parents=True, exist_ok=True)

        artifact = BuildArtifact(feature_id=spec.feature_id)

        # React component
        try:
            artifact.react_component = (self.react_generator or _default_react)(spec)
        except Exception as e:
            artifact.error = f"react gen: {e}"
            artifact.status = "shelved"
            self.store.write_feature(spec, artifact)
            return artifact

        # Backend handlers
        try:
            artifact.backend_handlers = (self.backend_generator or _default_backend)(spec)
        except Exception as e:
            artifact.error = f"backend gen: {e}"
            artifact.status = "shelved"
            self.store.write_feature(spec, artifact)
            return artifact

        # Data model — emit a JSON schema sidecar
        artifact.data_model_source = json.dumps(spec.data_model or {}, indent=2)

        # Write files (always to the per-feature workspace dir — keeps
        # the main source tree pristine). hot reload picks them up.
        if not dry_run:
            files: List[Path] = []
            (feature_dir / "component.jsx").write_text(artifact.react_component, encoding="utf-8")
            files.append(feature_dir / "component.jsx")
            (feature_dir / "handlers.py").write_text(artifact.backend_handlers, encoding="utf-8")
            files.append(feature_dir / "handlers.py")
            (feature_dir / "data_model.json").write_text(artifact.data_model_source, encoding="utf-8")
            files.append(feature_dir / "data_model.json")

            # Snapshot — captures current state BEFORE this build so we can revert
            snap_id = self.snapshots.create(
                feature_id=spec.feature_id,
                files=files,
                note=f"build {spec.feature_id} — {spec.title}",
            )
            artifact.snapshot_id = snap_id
            artifact.files_written = [str(p) for p in files]
            artifact.hot_reload_token = hashlib.sha1(
                f"{spec.feature_id}:{int(time.time())}".encode("utf-8")).hexdigest()[:12]

        artifact.status = "live" if spec.tier_policy["auto_approve"] else "built"
        self.store.write_feature(spec, artifact)
        # Push the spec into SkillOpt so usage is tracked
        _register_with_skillopt(spec)
        return artifact


def _default_react(spec: FeatureSpec) -> str:
    """Heuristic React stub. Real installs pass a model-backed generator."""
    safe_title = (spec.title or "Feature").replace('"', "'")
    components_block = ",\n      ".join(
        f'{{ kind: "{c.get("kind","Panel")}", name: "{c.get("name","Unnamed")}" }}'
        for c in (spec.ui_components or [])
    ) or '{ kind: "Panel", name: "FeaturePanel" }'
    routes_block = ",\n      ".join(
        f'"{r["method"]} {r["path"]}"'
        for r in (spec.backend_routes or [])
    ) or '"GET /api/liquid/feature"'

    return (
        f"// Liquid UI — {safe_title}\n"
        f"// feature_id: {spec.feature_id}  ·  tier: {spec.complexity_tier}\n"
        f"// auto-generated; persist edits via the Liquid UI panel.\n"
        f"\n"
        f"function {_to_pascal(spec.title) or 'LiquidFeature'}() {{\n"
        f"  const components = [\n      {components_block}\n  ];\n"
        f"  const routes = [\n      {routes_block}\n  ];\n"
        f"  return (\n"
        f"    <div className=\"liquid-feature glass p-4\">\n"
        f"      <div className=\"orbitron text-cyan-200 text-sm\">{safe_title}</div>\n"
        f"      <div className=\"text-muted text-xs mono mt-1\">tier · {spec.complexity_tier}</div>\n"
        f"      {{components.map(c => (\n"
        f"        <div key={{c.name}} className=\"mt-2 p-2 border border-cyan-500/20 rounded\">\n"
        f"          <span className=\"mono text-xs\">{{c.kind}} · {{c.name}}</span>\n"
        f"        </div>\n"
        f"      ))}}\n"
        f"    </div>\n"
        f"  );\n"
        f"}}\n"
    )


def _default_backend(spec: FeatureSpec) -> str:
    """Heuristic Flask blueprint stub."""
    routes = spec.backend_routes or [{"method": "GET", "path": f"/api/liquid/{spec.feature_id}"}]
    route_funcs: List[str] = []
    for i, r in enumerate(routes):
        path = r.get("path", "").replace("<feature_id>", spec.feature_id)
        method = r.get("method", "GET").upper()
        fn = (
            f"@bp.route({path!r}, methods=[{method!r}])\n"
            f"def _route_{i}():\n"
            f"    # Liquid UI — {spec.title}\n"
            f"    return {{'feature_id': {spec.feature_id!r}, 'status': 'ok'}}\n"
        )
        route_funcs.append(fn)
    return (
        f"# Liquid UI backend — {spec.title}\n"
        f"# feature_id: {spec.feature_id}\n"
        f"from flask import Blueprint\n\n"
        f"bp = Blueprint('liquid_{spec.feature_id}', __name__)\n\n"
        + "\n".join(route_funcs)
    )


# ════════════════════════════════════════════════════════════════════════
#  Suggest engine — proactive evolution prompts
# ════════════════════════════════════════════════════════════════════════

class SuggestEngine:
    """
    Watches usage events. When a behavioral pattern shows up enough times,
    surfaces a Suggestion.
    """

    def __init__(self, store: Optional[LiquidStore] = None,
                 detectors: Optional[List[Callable[[List[UsageEvent]], List[Suggestion]]]] = None):
        self.store = store or LiquidStore()
        self.detectors = detectors or [
            _detect_workspace_ping_pong,
            _detect_repeated_filter,
            _detect_error_loop,
            _detect_dwell_collapse,
        ]

    def scan(self) -> List[Suggestion]:
        events = self.store.read_usage(limit=SUGGEST_LOOKBACK_EVENTS)
        out: List[Suggestion] = []
        for det in self.detectors:
            try:
                for s in det(events) or []:
                    self.store.append_suggestion(s)
                    out.append(s)
            except Exception as e:
                log.warning("detector %s failed: %s", det.__name__, e)
        return out

    def context_suggestions(self, workspace: str, *,
                            recent_text: Optional[str] = None) -> List[Suggestion]:
        """
        Synchronous suggestion produced when the ✨ Suggest button is clicked.
        Combines workspace usage stats + optional recent context.
        """
        events = [e for e in self.store.read_usage(limit=200) if e.workspace == workspace]
        ideas: List[Suggestion] = []

        # Most-clicked target in this workspace → suggest a shortcut card
        clicks: Dict[str, int] = {}
        for e in events:
            if e.kind == "click":
                key = e.payload.get("target", "")
                if key:
                    clicks[key] = clicks.get(key, 0) + 1
        if clicks:
            top, n = max(clicks.items(), key=lambda kv: kv[1])
            if n >= 4:
                ideas.append(Suggestion(
                    workspace=workspace,
                    title=f"Pin {top} as a shortcut in {workspace}",
                    rationale=f"You've clicked '{top}' {n} times in recent activity.",
                    signals={"target": top, "clicks": n},
                    confidence=min(n / 10.0, 1.0),
                    proposed_spec={
                        "title": f"Shortcut · {top}",
                        "complexity_tier": "trivial",
                        "ui_components": [{"kind": "ShortcutCard", "name": top}],
                    },
                ))
        if recent_text:
            ideas.append(Suggestion(
                workspace=workspace,
                title=f"Build: {recent_text[:60]}",
                rationale="From the Suggest prompt.",
                signals={"source": "suggest_button"},
                confidence=0.5,
            ))
        return ideas


# ── Detectors ──────────────────────────────────────────────────────────

def _detect_workspace_ping_pong(events: List[UsageEvent]) -> List[Suggestion]:
    """Repeated A→B→A→B switches → suggest combining the views."""
    seq = [e.workspace for e in events if e.kind == "view"]
    pairs: Dict[Tuple[str, str], int] = {}
    for a, b in zip(seq, seq[1:]):
        if a and b and a != b:
            key = tuple(sorted([a, b]))
            pairs[key] = pairs.get(key, 0) + 1
    out: List[Suggestion] = []
    for (a, b), n in pairs.items():
        if n >= SUGGEST_MIN_OCCURRENCES:
            out.append(Suggestion(
                workspace=a,
                title=f"Combine {a} ↔ {b}?",
                rationale=f"You ping-ponged between {a} and {b} {n} times. "
                          f"Want a single split-view that shows both?",
                signals={"a": a, "b": b, "switches": n},
                confidence=min(n / 8.0, 1.0),
                proposed_spec={
                    "title": f"Split: {a} · {b}",
                    "complexity_tier": "medium",
                    "ui_components": [
                        {"kind": "SplitView", "name": f"{a}_vs_{b}",
                         "props": {"left": a, "right": b}}
                    ],
                },
            ))
    return out


def _detect_repeated_filter(events: List[UsageEvent]) -> List[Suggestion]:
    """User applies the same filter ≥ N times → suggest saving it as a view."""
    counts: Dict[Tuple[str, str], int] = {}
    for e in events:
        if e.kind == "submit" and "filter" in (e.payload.get("kind", "")):
            key = (e.workspace, json.dumps(e.payload.get("filter") or {}, sort_keys=True))
            counts[key] = counts.get(key, 0) + 1
    out: List[Suggestion] = []
    for (ws, fkey), n in counts.items():
        if n >= SUGGEST_MIN_OCCURRENCES:
            out.append(Suggestion(
                workspace=ws,
                title=f"Save filter as a view?",
                rationale=f"You re-applied the same filter {n} times. Save it as a one-click view?",
                signals={"filter": fkey, "uses": n},
                confidence=min(n / 6.0, 1.0),
                proposed_spec={
                    "title": "Saved view",
                    "complexity_tier": "simple",
                    "ui_components": [{"kind": "SavedView", "name": "SavedFilter"}],
                },
            ))
    return out


def _detect_error_loop(events: List[UsageEvent]) -> List[Suggestion]:
    """Error events in a feature → suggest revert + redo."""
    errs: Dict[str, int] = {}
    for e in events:
        if e.kind == "error" and e.feature_id:
            errs[e.feature_id] = errs.get(e.feature_id, 0) + 1
    out: List[Suggestion] = []
    for fid, n in errs.items():
        if n >= 3:
            out.append(Suggestion(
                workspace="",
                title=f"Revert and rebuild {fid}?",
                rationale=f"This feature errored {n} times. Roll back to last snapshot and try a different shape?",
                signals={"feature_id": fid, "errors": n},
                confidence=min(n / 5.0, 1.0),
                proposed_spec={
                    "title": "rebuild-with-error-feedback",
                    "complexity_tier": "medium",
                    "ui_components": [],
                    "notes": "Feed previous errors into the next spec.",
                },
            ))
    return out


def _detect_dwell_collapse(events: List[UsageEvent]) -> List[Suggestion]:
    """Feature dwell time collapsing → suggest deprecation."""
    dwell: Dict[str, List[int]] = {}
    for e in events:
        if e.dwell_ms and e.feature_id:
            dwell.setdefault(e.feature_id, []).append(e.dwell_ms)
    out: List[Suggestion] = []
    for fid, samples in dwell.items():
        if len(samples) < 6:
            continue
        # Halving comparison
        h = len(samples) // 2
        before = sum(samples[:h]) / h
        after = sum(samples[h:]) / (len(samples) - h)
        if after < before * 0.4 and before > 1000:
            out.append(Suggestion(
                workspace="",
                title=f"Deprecate {fid}?",
                rationale=f"Dwell time dropped from {before:.0f}ms → {after:.0f}ms. "
                          f"Auto-minimize or shelve?",
                signals={"feature_id": fid, "before": before, "after": after},
                confidence=0.6,
                proposed_spec={
                    "title": f"Shelve {fid}",
                    "complexity_tier": "trivial",
                },
            ))
    return out


# ════════════════════════════════════════════════════════════════════════
#  Engine — public API
# ════════════════════════════════════════════════════════════════════════

class LiquidUIEngine:
    """One-stop facade — server.py / UI panel call into this."""

    def __init__(self,
                 store: Optional[LiquidStore] = None,
                 generator: Optional[FeatureSpecGenerator] = None,
                 builder: Optional[LiquidUIBuilder] = None,
                 suggest: Optional[SuggestEngine] = None,
                 snapshots: Optional[SnapshotManager] = None):
        self.store = store or LiquidStore()
        self.snapshots = snapshots or SnapshotManager()
        self.generator = generator or FeatureSpecGenerator()
        self.builder = builder or LiquidUIBuilder(store=self.store, snapshots=self.snapshots)
        self.suggest = suggest or SuggestEngine(store=self.store)

    # ── ingest ──
    def submit_request(self, *, user_text: str, workspace: str = "",
                       source: str = "explicit",
                       signals: Optional[Dict[str, Any]] = None,
                       priority_hint: str = "normal") -> LiquidUIRequest:
        req = LiquidUIRequest(
            source=source, workspace=workspace,
            user_text=user_text, signals=signals or {},
            priority_hint=priority_hint,
        )
        self.store.append_request(req)
        return req

    # ── spec → build ──
    def plan(self, req: LiquidUIRequest) -> FeatureSpec:
        return self.generator.generate(req)

    def build(self, spec: FeatureSpec, *, dry_run: bool = False) -> BuildArtifact:
        return self.builder.build(spec, dry_run=dry_run)

    def submit_and_build(self, *, user_text: str, workspace: str = "",
                         dry_run: bool = False,
                         force_review: bool = False) -> Dict[str, Any]:
        req = self.submit_request(user_text=user_text, workspace=workspace)
        spec = self.plan(req)
        policy = spec.tier_policy
        if force_review or not policy["auto_approve"]:
            # Don't build automatically — surface for review
            self.store.write_feature(spec)
            return {
                "request_id": req.request_id,
                "feature_id": spec.feature_id,
                "spec": spec.to_dict(),
                "status": "awaiting_review",
                "review_mode": policy["review_mode"],
            }
        artifact = self.build(spec, dry_run=dry_run)
        return {
            "request_id": req.request_id,
            "feature_id": spec.feature_id,
            "spec": spec.to_dict(),
            "artifact": artifact.to_dict(),
            "status": artifact.status,
        }

    # ── feature lifecycle ──
    def shelve(self, feature_id: str, *, reason: str = "") -> bool:
        data = self.store.read_feature(feature_id)
        if not data:
            return False
        artifact = data.get("artifact") or {}
        artifact["status"] = "shelved"
        artifact["notes"] = (artifact.get("notes") or "") + f"\nShelved: {reason}"
        spec = FeatureSpec(**{k: v for k, v in data["spec"].items()
                              if k in FeatureSpec.__dataclass_fields__})
        art = BuildArtifact(**{k: v for k, v in artifact.items()
                               if k in BuildArtifact.__dataclass_fields__})
        self.store.write_feature(spec, art)
        return True

    def revert(self, snapshot_id: str) -> Tuple[bool, str]:
        return self.snapshots.revert(snapshot_id)

    def quick_revert_eligible(self, snapshot_id: str) -> bool:
        return self.snapshots.quick_revert_eligible(snapshot_id)

    # ── usage ──
    def record_usage(self, event: UsageEvent):
        self.store.append_usage(event)
        # Push to SkillOpt as a real-time signal
        _record_usage_to_skillopt(event)

    # ── suggestions ──
    def proactive_suggestions(self) -> List[Suggestion]:
        return self.suggest.scan()

    def suggest_for_workspace(self, workspace: str, *,
                              recent_text: Optional[str] = None) -> List[Suggestion]:
        return self.suggest.context_suggestions(workspace, recent_text=recent_text)

    # ── reporting ──
    def status_snapshot(self) -> Dict[str, Any]:
        features = self.store.list_features()
        suggestions = self.store.list_suggestions(status="open", limit=20)
        snaps = self.snapshots.list(limit=20)
        return {
            "generated_at": _now_iso(),
            "feature_count": len(features),
            "live_count": sum(1 for f in features
                              if (f.get("artifact") or {}).get("status") == "live"),
            "shelved_count": sum(1 for f in features
                                 if (f.get("artifact") or {}).get("status") == "shelved"),
            "open_suggestions": len(suggestions),
            "recent_features": features[:8],
            "recent_suggestions": [asdict(s) for s in suggestions],
            "recent_snapshots": snaps,
        }


# ════════════════════════════════════════════════════════════════════════
#  SkillOpt integration — every Liquid UI feature is also a tracked skill
# ════════════════════════════════════════════════════════════════════════

def _register_with_skillopt(spec: FeatureSpec):
    try:
        from skillopt_engine import get_engine
    except ImportError:
        return
    try:
        engine = get_engine()
        skill_name = f"liquid:{spec.feature_id}"
        content = (
            f"# Liquid UI feature — {spec.title}\n\n"
            f"- **Tier:** `{spec.complexity_tier}`\n"
            f"- **Workspace:** `{spec.workspace_target or '—'}`\n"
            f"- **Description:** {spec.description}\n"
        )
        engine.register_skill(
            skill_name=skill_name,
            content=content,
            weights={
                "user_satisfaction": 0.40,
                "accuracy": 0.30,
                "completeness": 0.15,
                "latency": 0.10,
                "cost": 0.05,
            },
            notes=f"liquid_ui spec {spec.feature_id}",
        )
    except Exception as e:
        log.warning("SkillOpt register failed: %s", e)


def _record_usage_to_skillopt(event: UsageEvent):
    try:
        from skillopt_engine import record_skill_run
    except ImportError:
        return
    if not event.feature_id:
        return
    satisfaction = 1.0 if event.user_satisfied else (0.0 if event.user_satisfied is False else 0.5)
    accuracy_map = {
        "view": 0.5, "click": 0.7, "submit": 1.0,
        "abandon": 0.1, "error": 0.0,
    }
    try:
        record_skill_run(
            skill_name=f"liquid:{event.feature_id}",
            inputs={"workspace": event.workspace, "kind": event.kind},
            outputs={"payload": event.payload},
            metrics={
                "accuracy": accuracy_map.get(event.kind, 0.5),
                "user_satisfaction": satisfaction,
                "completeness": 1.0 if event.kind != "abandon" else 0.3,
            },
            duration_ms=event.dwell_ms or 0,
            cost_usd=0.0,
            error=None if event.kind != "error" else "user-reported",
        )
    except Exception as e:
        log.warning("SkillOpt usage record failed: %s", e)


# ════════════════════════════════════════════════════════════════════════
#  Utilities
# ════════════════════════════════════════════════════════════════════════

def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _safe_name(path_str: str) -> str:
    s = path_str.replace(":", "_").replace("\\", "/").replace("/", "__")
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", s)[:180]


def _sha256_of(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _to_pascal(text: str) -> str:
    parts = re.split(r"[^a-zA-Z0-9]+", text or "")
    return "".join(w[:1].upper() + w[1:] for w in parts if w)


# ════════════════════════════════════════════════════════════════════════
#  Singleton + public API used by server.py / UI
# ════════════════════════════════════════════════════════════════════════

_engine_singleton: Optional[LiquidUIEngine] = None
_engine_lock = threading.Lock()


def get_engine() -> LiquidUIEngine:
    global _engine_singleton
    with _engine_lock:
        if _engine_singleton is None:
            _engine_singleton = LiquidUIEngine()
        return _engine_singleton


def submit_wish(user_text: str, workspace: str = "",
                force_review: bool = False) -> Dict[str, Any]:
    """Hook called from chat ('I wish I could ...')."""
    return get_engine().submit_and_build(
        user_text=user_text, workspace=workspace, force_review=force_review)


def export_panel_state() -> Dict[str, Any]:
    """Serialized state for the Liquid UI panel."""
    return get_engine().status_snapshot()


def record_event(*, feature_id: str = "", workspace: str = "", kind: str = "view",
                 payload: Optional[Dict[str, Any]] = None,
                 dwell_ms: Optional[int] = None,
                 user_satisfied: Optional[bool] = None) -> UsageEvent:
    ev = UsageEvent(feature_id=feature_id, workspace=workspace, kind=kind,
                    payload=payload or {}, dwell_ms=dwell_ms,
                    user_satisfied=user_satisfied)
    get_engine().record_usage(ev)
    return ev


# ════════════════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════════════════

def _cli_main():
    import argparse
    parser = argparse.ArgumentParser(prog="liquid_ui")
    sub = parser.add_subparsers(dest="cmd")

    p_wish = sub.add_parser("wish", help="Submit an 'I wish...' request")
    p_wish.add_argument("text", nargs="+")
    p_wish.add_argument("--workspace", default="")
    p_wish.add_argument("--review", action="store_true")
    p_wish.add_argument("--dry-run", action="store_true")

    p_status = sub.add_parser("status", help="Show fleet status")

    p_list = sub.add_parser("list", help="List features")
    p_list.add_argument("--status", default=None)

    p_show = sub.add_parser("show", help="Show one feature")
    p_show.add_argument("feature_id")

    p_revert = sub.add_parser("revert", help="Revert to a snapshot")
    p_revert.add_argument("snapshot_id")

    p_scan = sub.add_parser("scan", help="Run SuggestEngine scan")

    args = parser.parse_args()
    eng = get_engine()

    if args.cmd == "wish":
        text = " ".join(args.text)
        out = eng.submit_and_build(
            user_text=text, workspace=args.workspace,
            dry_run=args.dry_run, force_review=args.review,
        )
        print(json.dumps(out, indent=2))
    elif args.cmd == "status" or args.cmd is None:
        print(json.dumps(eng.status_snapshot(), indent=2))
    elif args.cmd == "list":
        for f in eng.store.list_features(status=args.status):
            spec = f.get("spec", {})
            art = f.get("artifact") or {}
            print(f"  {spec.get('feature_id'):<22} {spec.get('complexity_tier'):<8} "
                  f"{art.get('status','spec_only'):<10} {spec.get('title')}")
    elif args.cmd == "show":
        data = eng.store.read_feature(args.feature_id)
        print(json.dumps(data, indent=2) if data else "(not found)")
    elif args.cmd == "revert":
        ok, msg = eng.revert(args.snapshot_id)
        print(("✓ " if ok else "✗ ") + msg)
    elif args.cmd == "scan":
        out = eng.proactive_suggestions()
        for s in out:
            print(f"  · {s.title}  ({s.confidence:.2f})")
            print(f"    {s.rationale}")


if __name__ == "__main__":
    _cli_main()
