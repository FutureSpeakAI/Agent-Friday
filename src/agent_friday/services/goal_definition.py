"""Defining a goal (north star §17.1, §17.2, §17.4, §17.5; BD-12 slice G4).

An aspiration becomes a precise goal through a short exchange: Friday asks
only the questions the goal still needs, then states the whole goal in plain
words, and creates it only when the owner confirms THAT summary.

    draft(spec)                   -> {draft, questions, summary, summary_id}
    create_from(spec, summary_id) -> the goal, or a refusal

`summary_id` is a fingerprint of the normalised goal: if anything changed
between the summary the owner read and the confirm (another tab, a script, a
model that edited the request), the ids differ and nothing is created.

The goal it creates carries the full schema (schema_version 2): principal,
title, intent, completion condition and who judges it, the evidence that
proves it, constraints, deadline, budget (mψ, money, runtime), autonomy per
category of action, notification policy, permitted tools, the people it
reaches, triggers and stop conditions; each milestone carries its outcome,
dependencies, evidence, decision points, risk, planned actions, estimated
cost and expected date.

Pure functions over dicts; no model is called. Nothing here runs a goal.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = 2

#: Kinds of action a goal's steps may take, and what Friday may do about each
#: on her own. "never" refuses the step outright.
AUTONOMY_CATEGORIES = ("read", "draft", "write", "send", "spend", "delete", "publish")
AUTONOMY_LEVELS = ("on_its_own", "ask_me", "never")
DEFAULT_AUTONOMY = {"read": "on_its_own", "draft": "on_its_own", "write": "ask_me", "send": "ask_me",
                    "spend": "ask_me", "delete": "never", "publish": "ask_me"}
CATEGORY_WORDS = {"read": "look things up and read", "draft": "draft", "write": "change files or settings",
                  "send": "send messages", "spend": "spend money", "delete": "delete things",
                  "publish": "publish"}
#: Categories whose reach goes beyond this PC: the goal must say who it reaches.
OUTWARD = ("send", "publish")
EVIDENCE_KINDS = ("file", "message_sent", "receipt", "link", "owner_confirms", "text_check")
EVALUATORS = ("friday_checks", "owner_confirms")
TRIGGER_KINDS = ("manual", "schedule", "event")
#: The moments a goal appears on Home (services/desktop_card_sources). The
#: Goals card in System always shows every goal.
NOTIFY_ON = ("needs_owner", "at_risk", "progress")
NOTIFY_WORDS = {"needs_owner": "needs you", "at_risk": "is at risk", "progress": "is being worked on"}
RISK_LEVELS = ("low", "medium", "high")
_ID = re.compile(r"[A-Za-z0-9_]{1,64}")

#: The questions, in the order Friday asks them. Each is asked only when the
#: goal needs it (see `questions`).
QUESTIONS = {
    "title": "What should I call this goal?",
    "completion_condition": "What would tell you this goal is done?",
    "evidence_required": "What should I show you as proof that it's done?",
    "stop_conditions": "When should I stop and come back to you instead of carrying on?",
    "people": "This goal sends or publishes something. Who will it reach?",
    "budget": "This goal may spend money. What's the most it may spend?",
}


class DefinitionRefused(ValueError):
    pass


def _text(v: Any, limit: int = 2000) -> str:
    return " ".join(str(v or "").split())[:limit]


def texts(v: Any, limit: int = 20) -> List[str]:
    if isinstance(v, str):
        v = [x for x in re.split(r"[\n;]+", v)]
    if not isinstance(v, list):
        return []
    return [t for t in (_text(x, 300) for x in v) if t][:limit]


def _int(v: Any) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 0
    return max(0, n)


def _evidence(v: Any) -> List[Dict[str, str]]:
    out = []
    items = v if isinstance(v, list) else ([v] if v else [])
    for it in items[:10]:
        if isinstance(it, str):
            it = {"kind": "text_check", "what": it}
        if not isinstance(it, dict):
            continue
        what = _text(it.get("what") or it.get("description"), 300)
        if not what:
            continue
        kind = it.get("kind") if it.get("kind") in EVIDENCE_KINDS else "text_check"
        out.append({"kind": kind, "what": what})
    return out


def autonomy(v: Any) -> Dict[str, str]:
    out = dict(DEFAULT_AUTONOMY)
    if isinstance(v, dict):
        for k, lvl in v.items():
            if k in AUTONOMY_CATEGORIES and lvl in AUTONOMY_LEVELS:
                out[k] = lvl
    if out["spend"] == "on_its_own":
        # Friday can't yet count money a step spends, so a money budget could
        # not be held: spending always asks.
        out["spend"] = "ask_me"
    return out


def _milestone(m: Any, i: int) -> Dict[str, Any]:
    if isinstance(m, str):
        m = {"name": m}
    m = m if isinstance(m, dict) else {}
    mid = m.get("milestone_id") if isinstance(m.get("milestone_id"), str) and _ID.fullmatch(m["milestone_id"]) else f"ms_{i + 1}"
    return {
        "milestone_id": mid,
        "name": _text(m.get("name") or m.get("outcome") or f"Step {i + 1}", 200),
        "outcome": _text(m.get("outcome"), 500),
        "depends_on": [d for d in (m.get("depends_on") or []) if isinstance(d, str) and _ID.fullmatch(d)][:10],
        "evidence": _evidence(m.get("evidence")),
        "decision_points": texts(m.get("decision_points"), 10),
        "risk": m.get("risk") if m.get("risk") in RISK_LEVELS else "low",
        "planned_actions": [a for a in (m.get("planned_actions") or []) if a in AUTONOMY_CATEGORIES][:7] or ["read", "draft"],
        "cost_estimate_mψ": _int(m.get("cost_estimate_mψ")),
        "due": m.get("due") or None,
        "success_criteria": _text(m.get("success_criteria") or m.get("outcome"), 1000),
    }


def normalize(spec: Dict[str, Any]) -> Dict[str, Any]:
    """The goal as Friday understood it: every field typed, unknown ones dropped."""
    spec = spec if isinstance(spec, dict) else {}
    budget = spec.get("budget") if isinstance(spec.get("budget"), dict) else {}
    triggers = []
    for t in (spec.get("triggers") or [])[:5]:
        if isinstance(t, dict) and t.get("kind") in TRIGGER_KINDS:
            triggers.append({"kind": t["kind"], "spec": _text(t.get("spec"), 200)})
    notify = spec.get("notification_policy") if isinstance(spec.get("notification_policy"), dict) else {}
    milestones = [_milestone(m, i) for i, m in enumerate((spec.get("milestones") or [])[:20])]
    return {
        "schema_version": SCHEMA_VERSION,
        "principal": _text(spec.get("principal") or "you", 80),
        "title": _text(spec.get("title"), 200),
        "intent": _text(spec.get("intent") or spec.get("aspiration"), 2000),
        "completion_condition": _text(spec.get("completion_condition") or spec.get("success_criteria"), 2000),
        "evaluator": spec.get("evaluator") if spec.get("evaluator") in EVALUATORS else "friday_checks",
        "evidence_required": _evidence(spec.get("evidence_required")),
        "constraints": texts(spec.get("constraints")),
        "deadline": spec.get("deadline") or None,
        "budget": {"mψ": _int(budget.get("mψ")), "money_cents": _int(budget.get("money_cents")),
                   "runtime_minutes": _int(budget.get("runtime_minutes"))},
        "autonomy": autonomy(spec.get("autonomy")),
        "notification_policy": {"on": [x for x in (notify["on"] if isinstance(notify.get("on"), list)
                                                   else list(NOTIFY_ON)) if x in NOTIFY_ON],
                                "where": "Home and the Goals card"},
        "permitted_tools": [t for t in (spec.get("permitted_tools") or []) if isinstance(t, str) and _ID.fullmatch(t)][:30],
        "people": texts(spec.get("people")),
        "triggers": triggers or [{"kind": "manual", "spec": ""}],
        "stop_conditions": texts(spec.get("stop_conditions")),
        "milestones": milestones,
    }


def autonomy_for(goal: Dict[str, Any], milestone: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """(categories that must ask, categories that are never allowed) for one
    step of a goal. A goal without a schema-2 autonomy map, or a step with no
    planned actions, asks for nothing new: the existing gate decides."""
    a = goal.get("autonomy")
    if not isinstance(a, dict):
        return [], []
    a = autonomy(a)
    cats = [c for c in (milestone.get("planned_actions") or []) if c in AUTONOMY_CATEGORIES]
    return [c for c in cats if a[c] == "ask_me"], [c for c in cats if a[c] == "never"]


def _uses(goal: Dict[str, Any], category: str) -> bool:
    return any(category in m["planned_actions"] for m in goal["milestones"]) \
        and goal["autonomy"].get(category) != "never"


def questions(goal: Dict[str, Any]) -> List[Dict[str, str]]:
    """Only what this goal still needs, in order."""
    need = []
    if not goal["title"]:
        need.append("title")
    if not goal["completion_condition"]:
        need.append("completion_condition")
    if not goal["evidence_required"] and goal["evaluator"] == "friday_checks":
        need.append("evidence_required")
    if not goal["stop_conditions"]:
        need.append("stop_conditions")
    if any(_uses(goal, c) for c in OUTWARD) and not goal["people"]:
        need.append("people")
    if _uses(goal, "spend") and not goal["budget"]["money_cents"]:
        need.append("budget")
    return [{"key": k, "ask": QUESTIONS[k]} for k in need]


def _money(cents: int) -> str:
    return "%d.%02d" % divmod(cents, 100)


def summary(goal: Dict[str, Any]) -> str:
    """The whole goal in plain words, as the owner confirms it."""
    a = goal["autonomy"]
    by = {lvl: [CATEGORY_WORDS[c] for c in AUTONOMY_CATEGORIES if a.get(c) == lvl] for lvl in AUTONOMY_LEVELS}
    lines = ["Goal: " + (goal["title"] or "(no name yet)") + "."]
    if goal["intent"]:
        lines.append("Why: " + goal["intent"])
    lines.append("Done when: " + (goal["completion_condition"].rstrip(".") or "(not said yet)") + "."
                 + (" I check it myself against the proof below." if goal["evaluator"] == "friday_checks"
                    else " You decide when it's met."))
    if goal["evidence_required"]:
        lines.append("Proof: " + "; ".join(e["what"] for e in goal["evidence_required"]) + ".")
    if goal["milestones"]:
        lines.append("Steps: " + "; ".join(f"{i + 1}. {m['name']}" for i, m in enumerate(goal["milestones"])) + ".")
    lines.append("On my own I may " + (", ".join(by["on_its_own"]) or "do nothing") + ".")
    if by["ask_me"]:
        lines.append("I ask you first before I " + ", ".join(by["ask_me"]) + ".")
    if by["never"]:
        lines.append("I never " + ", ".join(by["never"]) + " for this goal.")
    b = goal["budget"]
    limits = [f"{b['mψ']} mψ" if b["mψ"] else "", _money(b["money_cents"]) + " in money" if b["money_cents"] else "",
              f"{b['runtime_minutes']} minutes of work" if b["runtime_minutes"] else ""]
    limits = [x for x in limits if x]
    lines.append("Limits: " + (", ".join(limits) if limits else "no budget set") + "."
                 + (" Deadline: " + str(goal["deadline"])[:10] + "." if goal["deadline"] else " No deadline."))
    if goal["constraints"]:
        lines.append("Keep to: " + "; ".join(goal["constraints"]) + ".")
    if goal["people"]:
        lines.append("It reaches: " + ", ".join(goal["people"]) + ".")
    if goal["stop_conditions"]:
        lines.append("I stop and come back to you if: " + "; ".join(goal["stop_conditions"]) + ".")
    on = goal["notification_policy"]["on"]
    lines.append(("I put it on Home when it " + ", ".join(NOTIFY_WORDS[x] for x in on) + "."
                  if on else "I don't put it on Home; it's always in System > Goals.")
                 + (" It's always in System > Goals." if on else ""))
    return "\n".join(lines)


def summary_id(goal: Dict[str, Any]) -> str:
    raw = json.dumps(goal, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(b"goal-definition\x00" + raw).hexdigest()[:24]


def draft(spec: Dict[str, Any]) -> Dict[str, Any]:
    goal = normalize(spec)
    return {"draft": goal, "questions": questions(goal), "summary": summary(goal), "summary_id": summary_id(goal)}


def to_create_args(goal: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """(create_goal keyword args, the schema-2 fields stored beside them)."""
    args = {
        "title": goal["title"], "description": goal["intent"], "owner": goal["principal"],
        "deadline": goal["deadline"], "success_criteria": goal["completion_condition"],
        "budget_cap_mψ": goal["budget"]["mψ"],
        "approval_required": False,
        "verification_mode": "manual" if goal["evaluator"] == "owner_confirms" else "auto",
        "milestones": goal["milestones"],
    }
    extra = {k: goal[k] for k in ("schema_version", "principal", "intent", "completion_condition", "evaluator",
                                  "evidence_required", "constraints", "budget", "autonomy",
                                  "notification_policy", "permitted_tools", "people", "triggers",
                                  "stop_conditions")}
    return args, extra


def check_confirm(spec: Dict[str, Any], seen_summary_id: Optional[str]) -> Dict[str, Any]:
    """The goal to create, or DefinitionRefused: a question is still open, or
    the summary the owner confirmed is not this goal's."""
    goal = normalize(spec)
    open_q = questions(goal)
    if open_q:
        raise DefinitionRefused("Still to answer: " + " ".join(q["ask"] for q in open_q))
    if not seen_summary_id or seen_summary_id != summary_id(goal):
        raise DefinitionRefused("The goal changed after you read its summary. Read it again, then confirm.")
    return goal
