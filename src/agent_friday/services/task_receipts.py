"""Per-step task receipts for judging chain-step honesty (RSI Phase 1, Defect B).

Nothing previously recorded per-step receipts on task records, so a chain
step reported "complete" could not be judged shipped vs hollow after the
fact — the harness had to take "complete" at face value. This module gives
callers a place to record what a step actually did (files written, tests
run, commands executed) and a function to judge the step from those
receipts rather than from its self-reported status alone.

See tests/unit/test_workflow_step_receipts.py for the pinned API contract.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

# Tools that only observe the world and never change it. A step whose
# receipts are drawn exclusively from this set did no side-effecting work,
# so it cannot be judged "shipped" even if its status says "complete".
_READ_ONLY_TOOLS = frozenset(
    {
        "read_file",
        "read_wiki",
        "read_doc",
        "search_web",
        "browse_web",
        "search_wiki",
        "search_files",
        "search_email",
        "search_contacts",
        "search_drive",
        "search_news",
        "query_calendar",
        "query_trust_graph",
        "find_calendar_events",
        "find_free_slots",
        "get_career_pipeline",
        "get_briefing",
        "list_tasks",
        "list_voices",
        "list_workspace_history",
        "list_sending_accounts",
        "knowledge_query",
        "knowledge_related",
        "knowledge_communities",
        "screenshot",
        "epistemic_score",
        "personality_show",
        "personality_check_sycophancy",
        "workflow_status",
    }
)


@dataclass(frozen=True)
class TaskReceipt:
    """One recorded unit of work performed for a task/step."""

    tool: str
    detail: str
    ok: bool


_LOCK = threading.Lock()
_RECEIPTS: dict[str, list[TaskReceipt]] = {}


def record_task_receipt(
    task_id: str, *, tool: str, detail: str, ok: bool = True
) -> None:
    """Record that `tool` ran against `detail` for `task_id`.

    Receipts accumulate per task_id for the life of the process. They are
    the evidence step_verdict() reads back to decide whether a step that
    reports "complete" actually shipped anything.
    """
    receipt = TaskReceipt(tool=tool, detail=detail, ok=ok)
    with _LOCK:
        _RECEIPTS.setdefault(task_id, []).append(receipt)


def step_verdict(task_id: str, *, status: str) -> str:
    """Judge a workflow step: "shipped", "hollow", or "failed".

    - status == "failed" is always "failed"; receipts don't matter.
    - status == "complete" is "shipped" only if at least one successful
      receipt used a side-effecting tool (not in _READ_ONLY_TOOLS).
    - Any other "complete" case (no receipts at all, or only read-only /
      failed receipts) is "hollow" — reported done, did nothing.
    """
    if status == "failed":
        return "failed"

    with _LOCK:
        receipts = list(_RECEIPTS.get(task_id, ()))

    for receipt in receipts:
        if receipt.ok and receipt.tool not in _READ_ONLY_TOOLS:
            return "shipped"
    return "hollow"
