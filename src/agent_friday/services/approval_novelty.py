"""Shadow record of how familiar each approval decision was (Compressor eval K4, slice B1).

Every decided card gets one label: ``never_seen``, ``new_target``, ``new_sequence`` or ``seen``.
The label is written to its own file and read by nothing that decides. Nothing here can change a
card, raise one, quiet one or fail a decision: ``note_decision`` never raises.

The counts are keyed by what a card already holds (its kind, its target class (the tool, for a tool card), who raised it, where
it came from, and the kind decided just before it in the same task). They hold labels only; no card
text, subject id or payload is kept.

A label is the first rule that holds, in this order (``classify``; tests/unit/test_approval_novelty.py
walks every small case):

    never_seen    the kind has not been decided before
    new_target    the kind has, but not with this target class
    new_sequence  kind and target class have, but never right after this prior kind
    seen          all three have

The report after 14 days of data (slice B2) reads ``approval_novelty.jsonl`` and says how many cards
would have been added by escalating on ``never_seen`` and ``new_sequence``, and how many of today's
cards were for ``seen`` actions. Stephen reads it before anything changes.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from enum import Enum
from typing import Any, Dict, NamedTuple, Optional

from agent_friday.paths import friday_home
from agent_friday.services import action_pattern

_log = logging.getLogger("friday.approval_novelty")

SHADOW_DAYS = 14

_DIR = friday_home()
COUNTS_FILE = _DIR / "approval_novelty_counts.json"
LABELS_FILE = _DIR / "approval_novelty.jsonl"
_LOCK = threading.Lock()
_TASKS_REMEMBERED = 500


class Novelty(str, Enum):
    NEVER_SEEN = "never_seen"
    NEW_TARGET = "new_target"
    NEW_SEQUENCE = "new_sequence"
    SEEN = "seen"


class Shape(NamedTuple):
    kind: str
    target_class: str
    principal: str
    origin: str
    prior_kind: str


def shape_of(card: Dict[str, Any], prior_kind: str = "") -> Shape:
    payload = card.get("payload") if isinstance(card.get("payload"), dict) else {}
    return Shape(
        kind=str(card.get("kind") or "")[:60],
        target_class=str(payload.get("tool") or card.get("subject_type") or "")[:60],
        principal=str(card.get("principal") or "owner")[:60],
        origin=str(payload.get("origin") or card.get("requested_by") or "")[:60],
        prior_kind=prior_kind[:60],
    )


def classify(shape: Shape, counts: Dict[str, Dict[str, int]]) -> Novelty:
    if not counts["kind"].get(shape.kind):
        return Novelty.NEVER_SEEN
    if not counts["target"].get(_target_key(shape)):
        return Novelty.NEW_TARGET
    if not counts["sequence"].get(_sequence_key(shape)):
        return Novelty.NEW_SEQUENCE
    return Novelty.SEEN


def _target_key(shape: Shape) -> str:
    return f"{shape.kind}|{shape.target_class}"


def _sequence_key(shape: Shape) -> str:
    return f"{shape.kind}|{shape.target_class}|{shape.prior_kind}"


def count(shape: Shape, counts: Dict[str, Dict[str, int]]) -> None:
    for family, key in (("kind", shape.kind), ("target", _target_key(shape)),
                        ("sequence", _sequence_key(shape))):
        counts[family][key] = counts[family].get(key, 0) + 1


def _empty_state() -> Dict[str, Any]:
    return {"since": time.time(), "kind": {}, "target": {}, "sequence": {}, "last_kind_by_task": {}}


def _load_state() -> Dict[str, Any]:
    try:
        state = json.loads(COUNTS_FILE.read_text(encoding="utf-8"))
        if isinstance(state, dict) and all(isinstance(state.get(k), dict) for k in
                                           ("kind", "target", "sequence", "last_kind_by_task")):
            return state
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001
        _log.warning("approval novelty counts unreadable, starting again: %s", exc)
    return _empty_state()


def _save_state(state: Dict[str, Any]) -> None:
    _DIR.mkdir(parents=True, exist_ok=True)
    tmp = COUNTS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(COUNTS_FILE)


def _task_of(card: Dict[str, Any]) -> str:
    payload = card.get("payload") if isinstance(card.get("payload"), dict) else {}
    return str(payload.get("task_id") or "")[:80]


def peek(card: Dict[str, Any]) -> Novelty:
    """The label this card would get now, without counting it."""
    with _LOCK:
        state = _load_state()
    task = _task_of(card)
    return classify(shape_of(card, state["last_kind_by_task"].get(task, "") if task else ""), state)


def note_decision(card: Dict[str, Any]) -> Optional[Novelty]:
    """Label and count one decided card. Returns the label, or None when nothing was recorded
    (an off-the-record card, or a failure; a failure is logged and never raised)."""
    try:
        if card.get("off_record"):
            return None
        with _LOCK:
            state = _load_state()
            task = _task_of(card)
            shape = shape_of(card, state["last_kind_by_task"].get(task, "") if task else "")
            label = classify(shape, state)
            count(shape, state)
            if task:
                tasks = state["last_kind_by_task"]
                tasks.pop(task, None)
                tasks[task] = shape.kind
                while len(tasks) > _TASKS_REMEMBERED:
                    tasks.pop(next(iter(tasks)))
            _save_state(state)
            pattern = action_pattern.from_card(card)
            line = {"t": time.time(), "pattern": pattern.fingerprint() if pattern else "",
                    "approval_id": str(card.get("approval_id") or ""),
                    "label": label.value, "decision": str(card.get("status") or ""),
                    "kind": shape.kind, "target_class": shape.target_class,
                    "principal": shape.principal, "origin": shape.origin,
                    "prior_kind": shape.prior_kind}
            with LABELS_FILE.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(line) + "\n")
        return label
    except Exception as exc:  # noqa: BLE001
        _log.warning("approval novelty not recorded: %s", exc)
        return None


def decided_rows() -> list:
    """Every label row written so far, oldest first. Unreadable lines are skipped."""
    try:
        lines = LABELS_FILE.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows
