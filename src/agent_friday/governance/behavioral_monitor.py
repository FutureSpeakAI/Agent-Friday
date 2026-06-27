"""
behavioral_monitor.py — Behavioral anomaly detection for Agent Friday.

Inspired by Adrian (secureagentics/adrian), but implemented as *internal
governance* rather than an external watcher. Adrian sits outside an agent and
observes it; this module lives inside Friday and scores her own tool-use loops
against the user's stated intent ("remit").

Lifecycle, per agent loop:

    mon = get_behavioral_monitor()
    sid = mon.begin_session(user_message)          # extract the remit
    ...                                             # agent runs
    mon.log_action(sid, tool_name, args, ring)      # once per tool call
    ...
    report = mon.evaluate(sid)                      # score + respond

`evaluate()` produces four sub-scores in [0, 1] (higher = riskier):

    scope_drift          did the agent stray outside the remit?
    privilege_escalation did it reach for higher rings than the task warranted?
    data_exfiltration    did it read sensitive data, then make an outbound call?
    repetition_anomaly   did it hammer one tool (loop / brute force)?

…a weighted `composite`, and a `cross_session` correlation pass over the last
20 traces (gradual escalation, repeated unsolicited access to the same data).

Response actions scale with the composite score:

    < 0.30   log only
    0.30-0.60 log + audit-trail note
    0.60-0.80 log + user notification ("Friday accessed X during your Y request")
    > 0.80   log + urgent notification + epistemic governance event

All state lives under ~/.friday/behavioral_monitor/ — never in the repo.
Argument summaries are PII-scrubbed before they are written anywhere.

This module is intentionally dependency-light: notifications_engine and
epistemic_engine are imported defensively so a missing module never breaks the
agent loop.
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from collections import Counter
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

HOME = Path(os.path.expanduser("~"))
BASE_DIR = HOME / ".friday" / "behavioral_monitor"
TRACES_PATH = BASE_DIR / "traces.jsonl"
AUDIT_PATH = BASE_DIR / "audit.jsonl"
LATEST_REPORT_PATH = BASE_DIR / "latest_report.json"

# Rolling buffer of session traces used for cross-session correlation.
MAX_TRACES = 20

# Default ring ceiling for a tool the host didn't classify (matches server.py's
# "unknown tools default to NETWORK ring" convention).
DEFAULT_RING = 2

# ── Tool taxonomy ────────────────────────────────────────────────────────
# These mirror server.py's TOOL_RINGS families but are kept local so the
# monitor degrades gracefully even if the host passes no ring information.

# Tools that, by themselves, read potentially-sensitive personal data.
SENSITIVE_READ_TOOLS = {
    "vault_read", "vault_export", "vault_list", "vault_append",
    "search_email", "read_email", "query_trust_graph",
    "get_career_pipeline", "read_clipboard",
}

# Tools that move data off the device or out to a third party.
OUTBOUND_TOOLS = {
    "draft_email", "send_email", "search_web", "browse_web", "open_url",
    "run_command", "install_package", "spawn_task", "peer_send",
    "peer_send_file", "post", "http_request", "webhook",
}

# Substrings in a file path / argument value that mark sensitive data.
SENSITIVE_PATH_HINTS = (
    "vault", "secret", "password", "credential", "private", "token",
    "api_key", "apikey", ".env", "ssh", "wallet", "seed",
    "intel", "medical", "ssn", "tax",
)

# Verb / phrase → ring-ceiling the request *reasonably* justifies.
# A read-only question shouldn't need OS control; sending email shouldn't
# need to install packages.
RING_INTENT_HINTS: List[Tuple[int, Tuple[str, ...]]] = [
    (3, ("install", "click", "type into", "press key", "open the app",
         "control my", "move the mouse", "automate", "screen", "screenshot")),
    (2, ("email", "send", "message", "search the web", "browse", "look up online",
         "post", "publish", "schedule", "run ", "download", "upload", "fetch")),
    (1, ("write", "save", "create a file", "edit", "update the", "note",
         "remember", "draft")),
    (0, ("what", "who", "when", "show", "read", "list", "summarize", "explain",
         "tell me", "how many", "find", "look at")),
]

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "for", "to", "of", "in", "on", "at",
    "is", "are", "was", "were", "be", "been", "with", "that", "this", "it",
    "you", "your", "my", "me", "i", "we", "can", "could", "would", "should",
    "please", "friday", "do", "does", "did", "have", "has", "had", "will",
    "about", "from", "into", "as", "if", "so", "then", "than", "what", "who",
    "when", "where", "why", "how", "get", "let", "want", "need", "make",
}

# Composite weighting — security-relevant dimensions (scope drift, exfiltration)
# carry the most weight.
COMPOSITE_WEIGHTS = {
    "scope_drift": 0.30,
    "privilege_escalation": 0.20,
    "data_exfiltration": 0.30,
    "repetition_anomaly": 0.20,
}


# ── PII-safe argument summarisation ──────────────────────────────────────

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_LONG_DIGITS_RE = re.compile(r"\b\d{6,}\b")
_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_\-]{24,}\b")
_USERPATH_RE = re.compile(r"[A-Za-z]:\\Users\\[^\\]+", re.IGNORECASE)
_WORD_RE = re.compile(r"\b\w{3,}\b")


def _scrub(value: str) -> str:
    """Strip obvious PII / secrets and Windows user paths from a value."""
    value = _USERPATH_RE.sub(r"<user>", value)
    value = _EMAIL_RE.sub("<email>", value)
    value = _TOKEN_RE.sub("<token>", value)
    value = _LONG_DIGITS_RE.sub("<num>", value)
    return value


def summarize_args(args: Any, *, max_len: int = 48) -> str:
    """Produce a short, PII-scrubbed one-line summary of tool arguments.

    Keys are preserved (they describe *what kind* of access happened); values
    are scrubbed, path-basenamed, and truncated.
    """
    if args is None:
        return ""
    if not isinstance(args, dict):
        return _scrub(str(args))[:max_len]
    parts: List[str] = []
    for k, v in args.items():
        sv = v if isinstance(v, str) else json.dumps(v, default=str)
        # Reduce filesystem paths to their basename so we keep the signal
        # ("which file family") without leaking the full path.
        if isinstance(v, str) and ("/" in v or "\\" in v):
            sv = re.split(r"[\\/]", v.rstrip("/\\"))[-1] or sv
        sv = _scrub(sv).replace("\n", " ").strip()
        if len(sv) > max_len:
            sv = sv[:max_len] + "…"
        parts.append(f"{k}={sv}")
    return "; ".join(parts)


def _tokens(text: str) -> set:
    return {
        w.lower() for w in _WORD_RE.findall(text or "")
        if w.lower() not in STOPWORDS
    }


# ── Data structures ──────────────────────────────────────────────────────

@dataclass
class Remit:
    """What the user is plausibly asking for, derived from their message."""
    raw: str
    tokens: List[str]
    referenced_paths: List[str]
    expected_max_ring: int
    mentions_sensitive: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Action:
    """A single logged tool call within a session."""
    tool_name: str
    arguments_summary: str
    timestamp: str
    ring_level: int
    result_type: str
    # Internal-only: raw arg tokens for scope matching (not persisted with PII).
    _arg_tokens: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "arguments_summary": self.arguments_summary,
            "timestamp": self.timestamp,
            "ring_level": self.ring_level,
            "result_type": self.result_type,
        }


@dataclass
class _Session:
    session_id: str
    remit: Remit
    actions: List[Action] = field(default_factory=list)
    started_at: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)


# ── The monitor ──────────────────────────────────────────────────────────

class BehavioralMonitor:
    """Internal-governance behavioral anomaly detector for Friday's agent loop."""

    def __init__(
        self,
        base_dir: Optional[Path] = None,
        notifier: Optional[Any] = None,
        epistemic: Optional[Callable[[], Any]] = None,
    ):
        self.base_dir = Path(base_dir) if base_dir else BASE_DIR
        self.traces_path = self.base_dir / "traces.jsonl"
        self.audit_path = self.base_dir / "audit.jsonl"
        self.latest_report_path = self.base_dir / "latest_report.json"
        self._lock = threading.RLock()
        self._sessions: Dict[str, _Session] = {}
        # Optional integrations, injected for testability; resolved lazily
        # otherwise.
        self._notifier = notifier
        self._epistemic_getter = epistemic
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    # ── Remit extraction ────────────────────────────────────────────────

    def extract_remit(self, user_message: str) -> Remit:
        """Derive a brief structured description of what the user is asking for."""
        msg = (user_message or "").strip()
        low = msg.lower()

        # File / path references the user explicitly named.
        referenced_paths = []
        for m in re.findall(r"[\w./\\-]+\.\w{1,5}\b", msg):
            referenced_paths.append(os.path.basename(m))
        referenced_paths = sorted(set(referenced_paths))

        # Highest ring the phrasing reasonably justifies. Default to NETWORK
        # only when the message clearly implies external action; otherwise the
        # baseline expectation is local read/write.
        expected = 0
        matched_any = False
        for ring, hints in RING_INTENT_HINTS:
            if any(h in low for h in hints):
                expected = max(expected, ring)
                matched_any = True
        if not matched_any:
            # No strong signal — allow local write as a benign default so we
            # don't false-positive on every ordinary request.
            expected = 1

        mentions_sensitive = any(h in low for h in SENSITIVE_PATH_HINTS)

        toks = sorted(_tokens(msg))
        return Remit(
            raw=msg[:500],
            tokens=toks,
            referenced_paths=referenced_paths,
            expected_max_ring=expected,
            mentions_sensitive=mentions_sensitive,
        )

    # ── Session lifecycle ───────────────────────────────────────────────

    def begin_session(
        self,
        user_message: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Open a monitoring session and extract its remit. Returns a session id."""
        sid = f"bmon-{uuid.uuid4().hex[:12]}"
        remit = self.extract_remit(user_message)
        sess = _Session(
            session_id=sid,
            remit=remit,
            started_at=_now_iso(),
            meta=meta or {},
        )
        with self._lock:
            self._sessions[sid] = sess
        return sid

    def log_action(
        self,
        session_id: str,
        tool_name: str,
        arguments: Any = None,
        ring_level: Optional[int] = None,
        result: Any = None,
    ) -> None:
        """Record one tool call. Cheap and exception-safe — never breaks the loop."""
        try:
            with self._lock:
                sess = self._sessions.get(session_id)
                if sess is None:
                    return
                summary = summarize_args(arguments)
                # Tokens for scope matching come from the raw (un-truncated)
                # arguments but are NOT persisted with the action.
                arg_text = ""
                if isinstance(arguments, dict):
                    arg_text = " ".join(str(v) for v in arguments.values())
                elif arguments is not None:
                    arg_text = str(arguments)
                action = Action(
                    tool_name=tool_name,
                    arguments_summary=summary,
                    timestamp=_now_iso(),
                    ring_level=int(ring_level) if ring_level is not None else DEFAULT_RING,
                    result_type=_classify_result(result),
                    _arg_tokens=sorted(_tokens(arg_text)),
                )
                sess.actions.append(action)
        except Exception:
            pass

    # ── Scoring ──────────────────────────────────────────────────────────

    def _is_sensitive_action(self, action: Action) -> bool:
        if action.tool_name in SENSITIVE_READ_TOOLS:
            return True
        blob = (action.arguments_summary or "").lower() + " " + " ".join(action._arg_tokens)
        return any(h in blob for h in SENSITIVE_PATH_HINTS)

    def _action_in_scope(self, action: Action, remit: Remit) -> bool:
        """Does this action plausibly serve the user's stated request?"""
        remit_tokens = set(remit.tokens)
        if not remit_tokens and not remit.referenced_paths:
            # No signal in the message — treat low-ring activity as in-scope so
            # we don't punish a terse "do it" follow-up.
            return action.ring_level <= max(remit.expected_max_ring, 1)
        # Token overlap between the action's args and the remit.
        if remit_tokens & set(action._arg_tokens):
            return True
        # The tool name itself echoing a remit token (e.g. "email" → draft_email).
        name_toks = set(re.split(r"[_\s]+", action.tool_name.lower()))
        if remit_tokens & name_toks:
            return True
        # Referenced file the user named explicitly.
        if remit.referenced_paths:
            summ = (action.arguments_summary or "").lower()
            if any(p.lower() in summ for p in remit.referenced_paths):
                return True
        return False

    def _score_scope_drift(self, actions: List[Action], remit: Remit) -> Tuple[float, List[str]]:
        if not actions:
            return 0.0, []
        findings: List[str] = []
        contribs: List[float] = []
        for a in actions:
            in_scope = self._action_in_scope(a, remit)
            sensitive = self._is_sensitive_action(a)
            if in_scope:
                contribs.append(0.0)
            elif sensitive:
                contribs.append(0.85)
                findings.append(f"accessed sensitive data via {a.tool_name} ({a.arguments_summary}) unrelated to the request")
            else:
                contribs.append(0.4)
        score = sum(contribs) / len(contribs)
        return _clamp(score), findings

    def _score_privilege_escalation(self, actions: List[Action], remit: Remit) -> Tuple[float, List[str]]:
        if not actions:
            return 0.0, []
        max_ring = max(a.ring_level for a in actions)
        expected = remit.expected_max_ring
        if max_ring <= expected:
            return 0.0, []
        over = [a for a in actions if a.ring_level > expected]
        # How far past the ceiling, normalised over the 0-3 ring range.
        depth = (max_ring - expected) / 3.0
        breadth = len(over) / len(actions)
        score = _clamp(0.6 * depth + 0.4 * breadth)
        findings = [
            f"used ring-{a.ring_level} tool {a.tool_name} (task warranted ≤ ring-{expected})"
            for a in over[:5]
        ]
        return score, findings

    def _score_data_exfiltration(self, actions: List[Action]) -> Tuple[float, List[str]]:
        seen_sensitive: List[Action] = []
        score = 0.0
        findings: List[str] = []
        hops = 0
        for a in actions:
            is_outbound = a.tool_name in OUTBOUND_TOOLS
            is_sensitive = self._is_sensitive_action(a)
            # Check the outbound branch FIRST: an outbound call that happens to
            # carry sensitive-looking arguments is the *worst* case (secrets
            # leaving the device), not a benign read — don't let the sensitivity
            # check shadow it.
            if is_outbound and seen_sensitive:
                base = 0.7 + 0.1 * hops  # escalate per outbound hop
                if is_sensitive:
                    base += 0.15         # the outbound payload itself looks sensitive
                hops += 1
                src = seen_sensitive[-1]
                findings.append(
                    f"read sensitive data ({src.tool_name}) then made outbound call "
                    f"{a.tool_name} ({a.arguments_summary})"
                )
                score = max(score, _clamp(base))
            # Any sensitive action (including an outbound carrier of secrets)
            # primes subsequent outbound calls as a potential exfil source.
            if is_sensitive:
                seen_sensitive.append(a)
        return score, findings

    def _score_repetition(self, actions: List[Action]) -> Tuple[float, List[str]]:
        if not actions:
            return 0.0, []
        by_tool = Counter(a.tool_name for a in actions)
        by_call = Counter((a.tool_name, a.arguments_summary) for a in actions)
        max_tool = max(by_tool.values())
        max_identical = max(by_call.values())
        # Identical repeated calls are a stronger loop/brute-force signal than
        # merely reusing the same tool with varied arguments.
        tool_score = _clamp((max_tool - 3) / 9.0)           # 3 free, 1.0 at 12
        identical_score = _clamp((max_identical - 2) / 5.0)  # 2 free, 1.0 at 7
        score = max(tool_score, identical_score)
        findings = []
        if score > 0:
            worst_tool, n = by_tool.most_common(1)[0]
            findings.append(f"called {worst_tool} {n}× in one loop")
            (wc_name, _), m = by_call.most_common(1)[0]
            if m >= 3:
                findings.append(f"made {m} near-identical {wc_name} calls")
        return score, findings

    # ── Cross-session correlation ────────────────────────────────────────

    def _cross_session(self, current_trace: Dict[str, Any]) -> Tuple[float, List[str]]:
        """Look across the last 20 traces for slow-burn patterns."""
        history = self._load_traces()
        findings: List[str] = []
        if len(history) < 3:
            return 0.0, []

        recent = history[-MAX_TRACES:]
        max_rings = [t.get("max_ring", 0) for t in recent]

        # 1. Gradual privilege escalation: is the recent ceiling creeping up?
        escalation = 0.0
        if len(max_rings) >= 6:
            half = len(max_rings) // 2
            early = sum(max_rings[:half]) / max(half, 1)
            late = sum(max_rings[half:]) / max(len(max_rings) - half, 1)
            if late > early:
                escalation = _clamp((late - early) / 3.0)
                if escalation >= 0.25:
                    findings.append(
                        f"max privilege ring is trending up across sessions "
                        f"({early:.1f} → {late:.1f})"
                    )

        # 2. Repeated unsolicited access to the same sensitive target.
        target_counter: Counter = Counter()
        for t in recent + [current_trace]:
            for tgt in t.get("sensitive_targets", []):
                target_counter[tgt] += 1
        repeat = 0.0
        for tgt, n in target_counter.items():
            if n >= 3:
                repeat = max(repeat, _clamp(0.5 + 0.15 * (n - 3)))
                findings.append(
                    f"sensitive target '{tgt}' accessed in {n} sessions without the user asking"
                )

        return max(escalation, repeat), findings

    # ── Evaluation ───────────────────────────────────────────────────────

    def evaluate(self, session_id: str) -> Dict[str, Any]:
        """Score a completed session, persist it, and fire response actions."""
        try:
            with self._lock:
                sess = self._sessions.pop(session_id, None)
        except Exception:
            sess = None
        if sess is None:
            return {}

        actions = sess.actions
        remit = sess.remit

        scope, scope_f = self._score_scope_drift(actions, remit)
        priv, priv_f = self._score_privilege_escalation(actions, remit)
        exfil, exfil_f = self._score_data_exfiltration(actions)
        rep, rep_f = self._score_repetition(actions)

        composite = (
            COMPOSITE_WEIGHTS["scope_drift"] * scope
            + COMPOSITE_WEIGHTS["privilege_escalation"] * priv
            + COMPOSITE_WEIGHTS["data_exfiltration"] * exfil
            + COMPOSITE_WEIGHTS["repetition_anomaly"] * rep
        )
        # Compounding: a weighted average alone lets one severe dimension be
        # diluted by calm ones (e.g. clear exfiltration averaged down to "high").
        # Independently-severe anomalies should multiply concern, not cancel —
        # so each dimension at/above 0.8 beyond the first lifts the composite.
        severe = sum(1 for s in (scope, priv, exfil, rep) if s >= 0.8)
        if severe >= 2:
            composite += 0.12 * (severe - 1)
        composite = _clamp(composite)

        # Sensitive targets touched out-of-scope (fuel for cross-session correlation).
        sensitive_targets = sorted({
            _target_key(a) for a in actions
            if self._is_sensitive_action(a) and not self._action_in_scope(a, remit)
        })

        max_ring = max((a.ring_level for a in actions), default=0)

        trace = {
            "session_id": sess.session_id,
            "timestamp": _now_iso(),
            "remit": remit.to_dict(),
            "action_count": len(actions),
            "actions": [a.to_dict() for a in actions],
            "max_ring": max_ring,
            "sensitive_targets": sensitive_targets,
            "scores": {
                "scope_drift": round(scope, 3),
                "privilege_escalation": round(priv, 3),
                "data_exfiltration": round(exfil, 3),
                "repetition_anomaly": round(rep, 3),
                "composite": round(composite, 3),
            },
            "meta": sess.meta,
        }

        cross_score, cross_f = self._cross_session(trace)
        # Fold a fraction of the cross-session signal into the final risk so a
        # slow-burn pattern can lift an otherwise-benign single session.
        final_composite = _clamp(composite + 0.15 * cross_score)

        findings = {
            "scope_drift": scope_f,
            "privilege_escalation": priv_f,
            "data_exfiltration": exfil_f,
            "repetition_anomaly": rep_f,
            "cross_session": cross_f,
        }

        report = {
            **trace,
            "scores": {**trace["scores"],
                       "cross_session": round(cross_score, 3),
                       "final_composite": round(final_composite, 3)},
            "risk_level": _risk_level(final_composite),
            "findings": findings,
        }

        # Persist and respond.
        self._append_trace(trace)
        self._write_latest(report)
        self._respond(report)
        return report

    # ── Response actions ─────────────────────────────────────────────────

    def _respond(self, report: Dict[str, Any]) -> None:
        score = report["scores"]["final_composite"]
        all_findings = [f for group in report["findings"].values() for f in group]
        summary = "; ".join(all_findings[:4]) or "no specific anomalies"
        remit_raw = (report.get("remit", {}).get("raw") or "your request").strip()
        remit_short = (remit_raw[:60] + "…") if len(remit_raw) > 60 else remit_raw

        # < 0.30 — log only (the trace write above is the log).
        if score < 0.30:
            return

        # 0.30-0.60 — add a note to the audit trail.
        self._audit_note(report, summary)
        if score < 0.60:
            return

        # 0.60-0.80 — notify the user.
        # > 0.80    — urgent notification + epistemic governance event.
        urgent = score >= 0.80
        primary = all_findings[0] if all_findings else "unexpected activity"
        title = ("⚠️ Friday flagged her own behaviour"
                 if not urgent else "🚨 Friday governance alert")
        body = (
            f"During your “{remit_short}” request, Friday {primary}. "
            f"Was this expected? (behavioral risk {score:.2f})"
        )
        self._notify(
            title=title,
            body=body,
            priority="critical" if urgent else "high",
            dedupe_key=f"bmon-{report['session_id']}",
            target={"workspace": "security", "tab": "behavioral",
                    "session_id": report["session_id"]},
            proactive_chat=urgent,
        )

        if urgent:
            self._epistemic_governance_event(score, summary)

    def _audit_note(self, report: Dict[str, Any], summary: str) -> None:
        entry = {
            "timestamp": _now_iso(),
            "session_id": report["session_id"],
            "risk_level": report["risk_level"],
            "final_composite": report["scores"]["final_composite"],
            "scores": report["scores"],
            "summary": summary,
        }
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _notify(self, **kwargs) -> None:
        notifier = self._notifier
        if notifier is None:
            try:
                import agent_friday.notifications_engine as notifier  # type: ignore
            except Exception:
                return
        try:
            notifier.push(source="behavioral_monitor", kind="security", **kwargs)
        except Exception:
            pass

    def _epistemic_governance_event(self, score: float, detail: str) -> None:
        """A governance violation should drag the epistemic score down."""
        getter = self._epistemic_getter
        if getter is None:
            try:
                from agent_friday.epistemic_engine import get_epistemic_engine as getter  # type: ignore
            except Exception:
                return
        try:
            engine = getter()
            fn = getattr(engine, "register_governance_event", None)
            if callable(fn):
                fn(severity=score, detail=detail)
        except Exception:
            pass

    # ── Persistence ──────────────────────────────────────────────────────

    def _load_traces(self) -> List[Dict[str, Any]]:
        if not self.traces_path.exists():
            return []
        out: List[Dict[str, Any]] = []
        try:
            with self.traces_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        continue
        except Exception:
            return []
        return out

    def _append_trace(self, trace: Dict[str, Any]) -> None:
        """Append a trace and truncate the file to the last MAX_TRACES entries."""
        with self._lock:
            history = self._load_traces()
            history.append(trace)
            history = history[-MAX_TRACES:]
            try:
                self.base_dir.mkdir(parents=True, exist_ok=True)
                tmp = self.traces_path.with_suffix(".jsonl.tmp")
                with tmp.open("w", encoding="utf-8") as f:
                    for t in history:
                        f.write(json.dumps(t, ensure_ascii=False) + "\n")
                tmp.replace(self.traces_path)
            except Exception:
                pass

    def _write_latest(self, report: Dict[str, Any]) -> None:
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            self.latest_report_path.write_text(
                json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass

    # ── Read accessors for the API endpoints ─────────────────────────────

    def get_latest_report(self) -> Dict[str, Any]:
        if self.latest_report_path.exists():
            try:
                return json.loads(self.latest_report_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "status": "no_data",
            "scores": {"final_composite": 0.0},
            "risk_level": "none",
            "findings": {},
        }

    def get_history_summary(self) -> Dict[str, Any]:
        history = self._load_traces()
        if not history:
            return {"count": 0, "sessions": [], "average_composite": 0.0,
                    "max_composite": 0.0}
        sessions = []
        composites = []
        for t in history:
            sc = t.get("scores", {})
            comp = sc.get("final_composite", sc.get("composite", 0.0))
            composites.append(comp)
            sessions.append({
                "session_id": t.get("session_id"),
                "timestamp": t.get("timestamp"),
                "action_count": t.get("action_count", 0),
                "max_ring": t.get("max_ring", 0),
                "composite": comp,
                "risk_level": _risk_level(comp),
                "remit": (t.get("remit", {}) or {}).get("raw", "")[:120],
            })
        return {
            "count": len(history),
            "window": MAX_TRACES,
            "average_composite": round(sum(composites) / len(composites), 3),
            "max_composite": round(max(composites), 3),
            "sessions": list(reversed(sessions)),  # newest first
        }

    def get_risk_score(self) -> Dict[str, Any]:
        latest = self.get_latest_report()
        score = (latest.get("scores", {}) or {}).get("final_composite", 0.0)
        history = self._load_traces()
        recent = history[-5:]
        recent_avg = 0.0
        if recent:
            vals = [(t.get("scores", {}) or {}).get(
                "final_composite", (t.get("scores", {}) or {}).get("composite", 0.0))
                for t in recent]
            recent_avg = round(sum(vals) / len(vals), 3)
        return {
            "composite": round(score, 3),
            "risk_level": _risk_level(score),
            "recent_average": recent_avg,
            "last_session": latest.get("session_id"),
            "last_updated": latest.get("timestamp"),
        }


# ── Module-level helpers ─────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _risk_level(score: float) -> str:
    if score >= 0.80:
        return "critical"
    if score >= 0.60:
        return "high"
    if score >= 0.30:
        return "elevated"
    return "normal"


def _classify_result(result: Any) -> str:
    """Coarse result type without storing the (possibly sensitive) payload."""
    if result is None:
        return "none"
    s = str(result)
    low = s.lower()
    if "[vault access denied]" in low or "vault-zt deny" in low:
        return "denied"
    if low.startswith("error") or "traceback" in low or "exception" in low[:80]:
        return "error"
    if "[screenshot" in low:
        return "image"
    return "text"


def _target_key(action: Action) -> str:
    """A coarse, stable identifier for a sensitive data target."""
    summ = action.arguments_summary or ""
    # Prefer a file basename or a sensitive hint word if present.
    for hint in SENSITIVE_PATH_HINTS:
        if hint in summ.lower():
            return f"{action.tool_name}:{hint}"
    m = re.search(r"=([\w.-]+)", summ)
    if m:
        return f"{action.tool_name}:{m.group(1)[:24]}"
    return action.tool_name


# ── Singleton ────────────────────────────────────────────────────────────

_monitor_singleton: Optional[BehavioralMonitor] = None
_monitor_lock = threading.Lock()


def get_behavioral_monitor() -> BehavioralMonitor:
    global _monitor_singleton
    with _monitor_lock:
        if _monitor_singleton is None:
            _monitor_singleton = BehavioralMonitor()
        return _monitor_singleton


__all__ = [
    "BehavioralMonitor",
    "get_behavioral_monitor",
    "Remit",
    "Action",
    "summarize_args",
]
