"""
Epistemic Engine — Real epistemic scoring for Agent Friday
FutureSpeak.AI · Asimov's Mind

Scores each Friday response on 4 dimensions:
  - Information gain: Did Friday provide new information?
  - Pushback rate: Did Friday disagree or correct the user?
  - Socratic ratio: Did Friday ask thought-provoking questions?
  - Independence fostering: Did Friday teach HOW vs just doing it?

Scores are written to ~/.friday/epistemic_scores.json after each turn.
Rolling averages tracked across last 10, last 50, and all-time.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_friday.paths import friday_home


FRIDAY_DIR = friday_home()
SCORES_PATH = FRIDAY_DIR / "epistemic_scores.json"
#: Governance events (the behavioural monitor's high-risk findings) keep
#: their own log. They used to be written as synthetic low-scoring turns,
#: which polluted the pushback average the sycophancy check reads.
GOVERNANCE_EVENTS_PATH = FRIDAY_DIR / "epistemic_governance_events.jsonl"

#: Friday's honesty rules, as fixed text that never carries a score. The
#: block it replaces told the model to "increase pushback" whenever a
#: keyword-scored average fell, which is the metric-chasing the north star
#: forbids. These rules change only through an owner edit with a receipt.
HONESTY_POLICY = (
    "Hold a stated position unless the user offers new evidence or a new argument; "
    "repetition, displeasure or authority alone do not move you. When you change "
    "your mind, say what changed it. State your confidence in words (high, moderate, "
    "low) when it is not high or when the stakes are high. Disagree once, clearly and "
    "with reasons, and do not re-argue a point the user has heard and decided. Say "
    "\"I was wrong\" plainly when you were. Separate what the evidence shows from "
    "what you or the user would prefer, and never stage a false balance when the "
    "evidence leans one way."
)

HISTORY_PATH = FRIDAY_DIR / "epistemic_history.jsonl"

PUSHBACK_PHRASES = [
    "actually", "i disagree", "that's not quite right", "consider instead",
    "not exactly", "i'd push back", "that's a common misconception",
    "worth reconsidering", "i wouldn't recommend", "careful with that",
    "that's not accurate", "let me correct", "to be precise",
    "i'd challenge that", "the evidence suggests otherwise",
    "that assumption", "not necessarily",
]

TEACHING_PATTERNS = [
    r"here'?s how",
    r"the way to",
    r"you can .+ by",
    r"the approach is",
    r"step \d",
    r"first,? .+\. then",
    r"the trick is",
    r"what you want to do is",
    r"the concept here is",
    r"this works because",
    r"under the hood",
    r"the reason .+ is",
]

EXECUTION_PATTERNS = [
    r"i'?ve? (?:done|completed|finished|sent|created|updated|deleted|scheduled)",
    r"done\.?\s*$",
    r"here'?s the result",
    r"task complete",
    r"all set",
    r"i (?:just )?(?:ran|executed|performed)",
]


@dataclass
class TurnScore:
    timestamp: str
    information_gain: float
    pushback_rate: float
    socratic_ratio: float
    independence_fostering: float
    composite: float
    user_message_length: int
    response_length: int


class EpistemicEngine:
    """Scores each Friday response on 4 epistemic dimensions."""

    def __init__(self):
        self._lock = threading.Lock()
        self._history: List[TurnScore] = []
        self._load_history()

    def _load_history(self):
        if HISTORY_PATH.exists():
            try:
                with HISTORY_PATH.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            self._history.append(TurnScore(**json.loads(line)))
                        except Exception:
                            continue
            except Exception:
                pass

    def score_turn(self, user_message: str, response: str) -> TurnScore:
        ig = self._score_information_gain(user_message, response)
        pb = self._score_pushback(response)
        sr = self._score_socratic_ratio(response)
        ind = self._score_independence(response)

        weights = {"information_gain": 0.3, "pushback_rate": 0.2,
                    "socratic_ratio": 0.25, "independence_fostering": 0.25}
        composite = (
            weights["information_gain"] * ig +
            weights["pushback_rate"] * pb +
            weights["socratic_ratio"] * sr +
            weights["independence_fostering"] * ind
        )
        composite = max(0.0, min(1.0, composite))

        turn = TurnScore(
            timestamp=datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
            information_gain=round(ig, 3),
            pushback_rate=round(pb, 3),
            socratic_ratio=round(sr, 3),
            independence_fostering=round(ind, 3),
            composite=round(composite, 3),
            user_message_length=len(user_message),
            response_length=len(response),
        )

        with self._lock:
            self._history.append(turn)
            self._append_history(turn)
            self._write_scores()

        return turn

    def _score_information_gain(self, user_msg: str, response: str) -> float:
        if not response:
            return 0.0
        ratio = len(response) / max(len(user_msg), 1)
        length_score = min(1.0, ratio / 5.0)

        response_lower = response.lower()
        user_words = set(re.findall(r'\b\w{4,}\b', user_msg.lower()))
        response_words = set(re.findall(r'\b\w{4,}\b', response_lower))
        new_words = response_words - user_words
        novelty_score = min(1.0, len(new_words) / max(len(response_words), 1))

        return (length_score * 0.4 + novelty_score * 0.6)

    def _score_pushback(self, response: str) -> float:
        if not response:
            return 0.0
        response_lower = response.lower()
        hits = sum(1 for phrase in PUSHBACK_PHRASES if phrase in response_lower)
        raw = min(1.0, hits / 2.0)
        # Pushback is valuable but shouldn't be artificially high.
        # A score of 0.3-0.5 is healthy; 0 means never disagrees.
        return min(1.0, raw * 1.5)

    def _score_socratic_ratio(self, response: str) -> float:
        if not response:
            return 0.0
        questions = response.count("?")
        sentences = max(1, len(re.findall(r'[.!?]+', response)))
        ratio = questions / sentences
        # Ideal: ~20-40% questions. Too many = annoying, too few = lecturing.
        if ratio < 0.05:
            return 0.1
        elif ratio < 0.15:
            return 0.4
        elif ratio < 0.35:
            return 0.8
        elif ratio < 0.5:
            return 1.0
        else:
            return 0.7  # too many questions

    def _score_independence(self, response: str) -> float:
        if not response:
            return 0.0
        response_lower = response.lower()
        teaching_hits = sum(
            1 for p in TEACHING_PATTERNS if re.search(p, response_lower)
        )
        execution_hits = sum(
            1 for p in EXECUTION_PATTERNS if re.search(p, response_lower)
        )
        total = teaching_hits + execution_hits
        if total == 0:
            return 0.5  # neutral
        return min(1.0, teaching_hits / max(total, 1))

    def register_governance_event(self, severity: float, detail: str = "") -> dict:
        """Record a governance violation in its own log.

        Called by the behavioral monitor when an agent loop trips a high
        composite-risk threshold. It is NOT a turn: writing it as a synthetic
        low-scoring turn polluted the pushback average the weekly sycophancy
        check reads, so a governance finding looked like Friday agreeing too
        much. The event is kept beside the history, never inside it.
        """
        severity = max(0.0, min(1.0, float(severity)))
        event = {
            "timestamp": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
            "severity": round(severity, 3),
            "detail": str(detail or "")[:300],
        }
        try:
            FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
            with GOVERNANCE_EVENTS_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception:
            pass
        return event

    def governance_events(self, limit: int = 100) -> list:
        """The most recent governance events, for the weekly self-review."""
        try:
            lines = GOVERNANCE_EVENTS_PATH.read_text(encoding="utf-8").splitlines()
        except Exception:
            return []
        out = []
        for ln in lines[-max(1, int(limit)):]:
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
        return out

    def _append_history(self, turn: TurnScore):
        try:
            FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
            with HISTORY_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(turn), ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _write_scores(self):
        try:
            FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
            recent_10 = self._history[-10:] if len(self._history) >= 10 else self._history
            recent_50 = self._history[-50:] if len(self._history) >= 50 else self._history
            all_time = self._history

            def avg(turns: List[TurnScore], field: str) -> float:
                if not turns:
                    return 0.0
                return round(sum(getattr(t, field) for t in turns) / len(turns), 3)

            data = {
                "overall": avg(all_time, "composite"),
                "overall_score": avg(all_time, "composite"),
                "total_turns_scored": len(all_time),
                "dimensions": {
                    "information_gain": avg(all_time, "information_gain"),
                    "pushback_rate": avg(all_time, "pushback_rate"),
                    "socratic_ratio": avg(all_time, "socratic_ratio"),
                    "independence_fostering": avg(all_time, "independence_fostering"),
                },
                "rolling_10": {
                    "composite": avg(recent_10, "composite"),
                    "information_gain": avg(recent_10, "information_gain"),
                    "pushback_rate": avg(recent_10, "pushback_rate"),
                    "socratic_ratio": avg(recent_10, "socratic_ratio"),
                    "independence_fostering": avg(recent_10, "independence_fostering"),
                    "count": len(recent_10),
                },
                "rolling_50": {
                    "composite": avg(recent_50, "composite"),
                    "information_gain": avg(recent_50, "information_gain"),
                    "pushback_rate": avg(recent_50, "pushback_rate"),
                    "socratic_ratio": avg(recent_50, "socratic_ratio"),
                    "independence_fostering": avg(recent_50, "independence_fostering"),
                    "count": len(recent_50),
                },
                "last_updated": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
            }

            if self._history:
                last = self._history[-1]
                data["last_turn"] = asdict(last)

            SCORES_PATH.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    def get_scores(self) -> Dict[str, Any]:
        if SCORES_PATH.exists():
            try:
                return json.loads(SCORES_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "overall": 0.0,
            "overall_score": 0.0,
            "total_turns_scored": 0,
            "dimensions": {
                "information_gain": 0.0,
                "pushback_rate": 0.0,
                "socratic_ratio": 0.0,
                "independence_fostering": 0.0,
            },
        }

    def get_prompt_injection(self) -> str:
        """The honesty rules, as fixed text. The score never enters the prompt:
        a model told to "increase pushback" to lift a keyword average is
        chasing the metric, not holding a position."""
        return HONESTY_POLICY


_engine_singleton: Optional[EpistemicEngine] = None
_engine_lock = threading.Lock()


def get_epistemic_engine() -> EpistemicEngine:
    global _engine_singleton
    with _engine_lock:
        if _engine_singleton is None:
            _engine_singleton = EpistemicEngine()
        return _engine_singleton
