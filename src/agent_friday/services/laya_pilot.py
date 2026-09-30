"""Optional, advisory source preparation with bounded in-memory measurements.

The pilot neither routes providers nor executes or authorizes tools. Control
turns do no prediction; assisted turns reuse a warm Laya and never wait for it.
No request text, provider text, identifiers, or error details enter metrics.
"""
from __future__ import annotations

from collections import Counter, deque
import math
import statistics
import threading
import time

MAX_REQUEST_CHARS = 1000
SCORE_DEADLINE_S = 2.5
MAX_RECORDS = 256
FAMILIES = {"A": "knowledge", "B": "web", "C": "files", "D": "apps",
            "E": "none", "F": "mixed"}
SOURCE_QUESTION = {"source": {
    "type": "choice",
    "instructions": "Which source of information is needed for the CURRENT user request? Respect explicit source restrictions.",
    "criteria": {
        "A": "The owner's personal wiki, private notes, archive or knowledge graph.",
        "B": "Public websites, online articles, current external facts or public research.",
        "C": "Local workspace files, source code, attached documents or local datasets.",
        "D": "The owner's connected email, calendar or task manager.",
        "E": "No lookup: answer from the supplied text or basic reasoning.",
        "F": "A combination of two or more of the above information sources.",
    },
}}
_LOCK = threading.Lock()
_NEXT = 0
_ROWS = deque(maxlen=MAX_RECORDS)
_COUNTERS = ("model_rounds", "loader_calls", "tool_calls", "added_tools", "context_blocks")
_MODES = {"local_only", "local_preferred", "cloud_only", "smart"}


def _settings():
    from agent_friday.core import _load_settings
    return _load_settings() or {}


def _off_record_now():
    try:
        from agent_friday.services.off_record import active
        return active()
    except Exception:
        return True


def _number(value):
    try:
        number = float(value)
        return max(0.0, min(number, 86400000.0)) if math.isfinite(number) else 0.0
    except (TypeError, ValueError):
        return 0.0


class Ticket:
    """Per-turn state. Peek during preparation, freeze at first dispatch."""
    def __init__(self, arm, mode):
        self.arm = arm
        self.routing_mode = mode
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._started = time.monotonic()
        self._family = None
        self._last_observed = None
        self._frozen = False
        self._finished = False
        self._status = "control" if arm == "control" else "pending"
        self._prepared_ms = None
        self._prediction_ms = None
        self._execution = set()
        self._counts = dict.fromkeys(_COUNTERS, 0)

    def _completed(self, result, elapsed_ms):
        family = None
        try:
            choice = result["answers"]["source"]["choice"]
            if isinstance(choice, str):
                family = FAMILIES.get(choice)
        except (KeyError, TypeError):
            pass
        with self._lock:
            if not self._frozen and not self._finished:
                self._family = family
                late = time.monotonic() - self._started > SCORE_DEADLINE_S
                self._status = "timeout" if late else ("ready" if family else ("error" if result is None else "invalid"))
                self._prediction_ms = _number(elapsed_ms)
        self._done.set()

    def _current_plan(self):
        if self._status == "pending" and time.monotonic() - self._started > SCORE_DEADLINE_S:
            self._status = "timeout"
            return None
        return self._family if self._status == "ready" else None

    def plan(self):
        """Return a validated advisory family immediately, without waiting."""
        with self._lock:
            if not self._frozen:
                self._last_observed = self._current_plan()
            return self._last_observed

    def mark_prepared(self):
        """Freeze the last plan the caller observed, not a later prediction."""
        with self._lock:
            if self._prepared_ms is None:
                self._prepared_ms = (time.monotonic() - self._started) * 1000
            if not self._frozen:
                self._current_plan()
                self._frozen = True
                if self._last_observed is None and self._status in {"pending", "ready"}:
                    self._status = "not_ready"

    prepared = mark_prepared

    def observe_execution(self, execution):
        if execution in {"local", "cloud"}:
            with self._lock:
                if not self._finished:
                    self._execution.add(execution)

    def increment(self, name, amount=1):
        if name not in _COUNTERS:
            return
        with self._lock:
            if not self._finished:
                self._counts[name] += int(_number(amount))

    def finish(self, *, total_ms=None, preparation_ms=None, outcome="ok", error=None,
               model_rounds=None, loader_calls=None, tool_calls=None, added_tools=None,
               context_blocks=None):
        """Record once. All measurements are numeric or predefined enums."""
        self.mark_prepared()
        with self._lock:
            if self._finished:
                return
            self._finished = True
            if _off_record_now():
                return
            counts = dict(self._counts)
            for name, value in (("model_rounds", model_rounds), ("loader_calls", loader_calls),
                                ("tool_calls", tool_calls), ("added_tools", added_tools),
                                ("context_blocks", context_blocks)):
                if value is not None:
                    counts[name] = int(_number(value))
            row = {
                "arm": self.arm, "routing_mode": self.routing_mode,
                "execution": (next(iter(self._execution)) if len(self._execution) == 1
                              else "mixed" if self._execution else "unknown"),
                "status": self._status, "plan_ready": self._last_observed is not None,
                "applied": bool(counts["added_tools"] or counts["context_blocks"]),
                "outcome": "error" if error else (outcome if outcome in {"ok", "error", "refused"} else "error"),
                "total_ms": round(_number(total_ms if total_ms is not None else
                                           (time.monotonic() - self._started) * 1000), 2),
                "preparation_ms": round(_number(preparation_ms if preparation_ms is not None else
                                                 self._prepared_ms), 2),
                "prediction_ms": self._prediction_ms,
                **counts,
            }
        with _LOCK:
            _ROWS.append(row)


def start(message, settings=None):
    """Start alternating control/assisted preparation; disabled by default."""
    global _NEXT
    try:
        cfg = _settings() if settings is None else settings
        if not isinstance(cfg, dict) or cfg.get("laya_pilot_enabled") is not True or cfg.get("off_record"):
            return None
        if _off_record_now():
            return None
        text = message.strip()[:MAX_REQUEST_CHARS] if isinstance(message, str) else ""
        if not text:
            return None
        mode = (cfg.get("model_routing") or {}).get("mode")
        mode = mode if mode in _MODES else "unknown"
        with _LOCK:
            arm = "control" if _NEXT % 2 == 0 else "assisted"
            _NEXT += 1
        ticket = Ticket(arm, mode)
        if arm == "assisted":
            from agent_friday.services import laya_backend
            status = laya_backend.try_start_pilot_score(text, SOURCE_QUESTION, ticket._completed)
            if status != "started":
                with ticket._lock:
                    ticket._status = status if status in {"cold", "busy", "error"} else "error"
                ticket._done.set()
        return ticket
    except Exception:
        return None


def _percentile(values, fraction):
    if not values:
        return None
    return round(sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)], 2)


def snapshot():
    """Bounded measurements since restart; no content or per-user identifiers."""
    with _LOCK:
        rows = [dict(row) for row in _ROWS]
    groups = []
    for arm, mode, execution in sorted({(r["arm"], r["routing_mode"], r["execution"]) for r in rows}):
        selected = [r for r in rows if (r["arm"], r["routing_mode"], r["execution"]) == (arm, mode, execution)]
        eligible = [r for r in selected if r["model_rounds"] > 0 and r["outcome"] == "ok"]
        totals = [r["total_ms"] for r in eligible]
        prep = [r["preparation_ms"] for r in eligible]
        applied = sum(r["applied"] for r in selected)
        groups.append({
            "arm": arm, "routing_mode": mode, "execution": execution,
            "count": len(selected), "eligible_count": len(eligible),
            "prepared": sum(r["plan_ready"] for r in selected),
            "applied": applied, "skipped": len(selected) - applied if arm == "assisted" else 0,
            "statuses": dict(Counter(r["status"] for r in selected)),
            "outcomes": dict(Counter(r["outcome"] for r in selected)),
            "median_total_ms": round(statistics.median(totals), 2) if totals else None,
            "p95_total_ms": _percentile(totals, .95),
            "median_preparation_ms": round(statistics.median(prep), 2) if prep else None,
            "p95_preparation_ms": _percentile(prep, .95),
            **{name: sum(r[name] for r in selected) for name in _COUNTERS},
        })
    from agent_friday.services import laya_backend
    try:
        status = laya_backend.status()
    except Exception:
        status = {}
    return {"scope": "since restart; latest completed turns", "capacity": MAX_RECORDS,
            "count": len(rows), "groups": groups, "rows": rows,
            "backend": {"ready": bool(status.get("ready")), "loading": bool(status.get("loading")),
                        "device": "cpu"}}


def _reset_for_tests():
    global _NEXT
    with _LOCK:
        _NEXT = 0
        _ROWS.clear()
