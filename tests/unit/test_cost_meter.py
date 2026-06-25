"""Unit tests for cost metering (Part D)."""
import pytest

from services import cost_meter as cm


@pytest.fixture(autouse=True)
def _fresh_db(friday_dir):
    cm.reset_for_tests()
    if cm.DB_PATH.exists():
        cm.DB_PATH.unlink()
    cm.reset_for_tests()
    yield
    cm.reset_for_tests()


def test_per_direction_pricing():
    # Opus: 0.015 in / 0.075 out per 1K. 1000 in + 1000 out = 0.015 + 0.075.
    assert cm.cost_for("claude-opus-4-8", 1000, 1000) == pytest.approx(0.09)
    # Output costs ~5× input — verify directions aren't blended.
    assert cm.cost_for("claude-opus-4-8", 2000, 0) == pytest.approx(0.03)
    assert cm.cost_for("claude-opus-4-8", 0, 2000) == pytest.approx(0.15)


def test_local_models_free():
    assert cm.cost_for("gemma4:latest", 100000, 100000) == 0.0
    assert cm.price_for("llama3.1:8b") == {"in": 0.0, "out": 0.0}


def test_record_and_summary():
    cm.record("anthropic", "claude-opus-4-8", 1000, 1000,
              workspace="research", kind="chat")
    cm.record("anthropic", "claude-sonnet-4-6", 1000, 1000,
              workspace="studio", kind="task")
    summ = cm.summary("today")
    assert summ["total_calls"] == 2
    assert summ["total_usd"] == pytest.approx(0.09 + 0.018)
    assert summ["by_workspace"]["research"]["calls"] == 1
    assert "anthropic" in summ["by_provider"]
    assert set(summ["by_kind"].keys()) == {"chat", "task"}


def test_meter_maps_openai_usage():
    cm.meter("openai", "gpt-4o", {"prompt_tokens": 1000, "completion_tokens": 500})
    summ = cm.summary("today")
    assert summ["input_tokens"] == 1000
    assert summ["output_tokens"] == 500


def test_meter_maps_anthropic_usage_object():
    class _Usage:
        input_tokens = 1000
        output_tokens = 200
    cm.meter("anthropic", "claude-opus-4-8", _Usage())
    summ = cm.summary("today")
    assert summ["input_tokens"] == 1000
    assert summ["output_tokens"] == 200


def test_task_attribution_for_scheduled():
    cm.register_task_attribution("task-123", {
        "kind": "scheduled", "schedule_id": "sch_jobintel", "workspace": "research"})
    cm.record("anthropic", "claude-opus-4-8", 1000, 0,
              session_ctx={"task_id": "task-123"})
    sched = cm.by_schedule("today")
    assert any(r["schedule_id"] == "sch_jobintel" for r in sched)


def test_thread_local_attribution():
    cm.push_attribution(kind="compaction", workspace="system")
    try:
        cm.record("anthropic", "claude-haiku-4-5-20251001", 500, 100)
    finally:
        cm.pop_attribution()
    summ = cm.summary("today")
    assert "compaction" in summ["by_kind"]


def test_budget_set_get():
    b = cm.set_budget({"daily": 10.0, "daily_enabled": True})
    assert b["daily"] == 10.0 and b["daily_enabled"] is True
