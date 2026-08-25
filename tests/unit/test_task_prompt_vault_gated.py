"""Unit tests for WO-2 item 2 (2026-08-25): background-task system prompts
must be gated for the provider the task actually lands on, not built ungated
and left to the egress gate's keyword classifier as the only defense.

SECURITY CONTEXT. `agent._task_worker` used to build its system prompt via
`_get_friday_system_prompt(prompt, workspace='task')` — no `provider`, no
`vault_control`. That combination is the function's own documented "legacy
ungated" default, so every TIER_2 vault/self-knowledge section rode into the
prompt in the clear regardless of destination. For a task that then routed to
a cloud model (the ordinary case for a tool-using turn — see `_route_basic`'s
TOOL_USE handling), the egress gate's field-wise keyword classifier was the
ONLY thing standing between raw personal context and Anthropic — the same
classifier already known to have a TIER_2 gap.

`routes/chat.py` and `routes/voice.py` never relied on the gate alone: they
pre-decide the provider and gate the prompt itself before it is ever built.
`_task_worker` now does the same, via `_predict_route_provider` (a second,
side-effect-free call into the same router `_generate_agent` consults moments
later) and `_gated_vault_control`.

INJECTION POINT: this uses the vault's "== ACTIVE TASKS ==" section
(`_build_context_prompt`, model_router.py — hard-tagged TIER_2, unconditional)
rather than SELF.md/self-knowledge. WO-1 (2026-08-25, same session) made
self-knowledge always gate-exempt by design — it is Friday's own
self-description, not Stephen's personal data — so it is no longer a valid
TIER_2 probe. Active tasks are genuinely Stephen's data and remain
tier-gated exactly as before.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

import agent_friday.services.agent as agent_mod
import agent_friday.services.model_router as mr

TIER2_SECRET = "renew the passport before the custody hearing"
TASK_PROMPT = "give me a status update on my pending tasks"  # triggers 'todos' needs


@pytest.fixture(autouse=True)
def _fake_active_todos(monkeypatch):
    """Stand in for the vault's active-todos list with one TIER_2 item, and
    make sure the standing 'is gating even active' setting defaults to on
    (its real default — see model_router._vault_local_only)."""
    monkeypatch.setattr(mr, "_load_vault_summary",
                        lambda: {"active_todos": [
                            {"status": "open", "task": TIER2_SECRET}]})
    monkeypatch.setattr(mr, "_vault_local_only", lambda: True)


def _run_task_worker_capturing_system(monkeypatch, *, routed_provider):
    """Run agent._task_worker with `_generate_agent` stubbed to capture the
    `system=` kwarg it was built with, and the provider prediction pinned to
    `routed_provider`. Returns the captured system prompt string."""
    captured = {}

    def _fake_generate_agent(messages, system=None, **kw):
        captured['system'] = system
        return "done", []

    monkeypatch.setattr(agent_mod, "_generate_agent", _fake_generate_agent)
    monkeypatch.setattr(agent_mod, "_predict_route_provider",
                        lambda **kw: routed_provider)

    task_id = "test-task-vault-gate"
    with agent_mod.TASKS_LOCK:
        agent_mod.TASKS[task_id] = {}
    try:
        agent_mod._task_worker(task_id, "Test Task", TASK_PROMPT)
    finally:
        with agent_mod.TASKS_LOCK:
            agent_mod.TASKS.pop(task_id, None)
    return captured.get('system') or ""


class TestTaskPromptVaultGated:
    def test_cloud_bound_task_prompt_omits_tier2_secret(self, monkeypatch):
        system = _run_task_worker_capturing_system(monkeypatch, routed_provider='cloud')
        assert TIER2_SECRET not in system, (
            "a task predicted to route to a CLOUD provider must not carry "
            "raw TIER_2 vault content (active tasks) in its system prompt — "
            "the egress gate's keyword classifier must not be the only "
            "defense"
        )

    def test_local_bound_task_prompt_keeps_tier2_secret(self, monkeypatch):
        # Over-redacting a task that is actually going to run fully on-device
        # is its own regression (it silently degrades local task quality) —
        # pin this so the fix does not swing the other way.
        system = _run_task_worker_capturing_system(monkeypatch, routed_provider='local')
        assert TIER2_SECRET in system, (
            "a task predicted to route LOCAL should still see its full "
            "vault context; local is exactly where vault-tier content is "
            "supposed to be usable"
        )


# ── Falsifiability ──────────────────────────────────────────────────────────
def test_ungated_prompt_would_leak_tier2(monkeypatch):
    """Reproduce the exact condition of the original bug — no vault_control
    passed to `_get_friday_system_prompt` at all — and confirm the TIER_2
    secret leaks into a cloud-bound task's prompt. If this stops leaking, the
    tests above are not actually exercising real gating; they would pass even
    if `_task_worker` silently stopped calling `_gated_vault_control`.
    """
    monkeypatch.setattr(agent_mod, "_gated_vault_control", lambda: None)
    system = _run_task_worker_capturing_system(monkeypatch, routed_provider='cloud')
    assert TIER2_SECRET in system, (
        "with vault_control forced back to None (the original bug's exact "
        "condition) the secret should leak; it did not, so the tests above "
        "are not actually exercising the gate"
    )
