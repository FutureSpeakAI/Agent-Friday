"""local_model_status answers "which local model is serving, how much can it
hold, is it busy" - read-only, ring 0, and outside the always-on catalogue."""
from __future__ import annotations

import json
import threading

import pytest


class _FakeArbiter:
    def __init__(self, seats, state="DEFAULT", lease=None):
        self.plan = {"seats": seats}
        self.state = state
        self.lease = lease


BRAIN = {"interactive_brain": {"model_id": "bonsai2:27b", "num_ctx": 8192,
                               "status": "resident", "device": "gpu"}}


@pytest.fixture
def arbiter(monkeypatch):
    from agent_friday.services import residency_arbiter as ra
    box = {"arb": _FakeArbiter(BRAIN)}
    monkeypatch.setattr(ra, "get_arbiter", lambda: box["arb"])
    return box


def _call():
    from agent_friday.services import agent
    return json.loads(agent.CLAUDE_TOOL_HANDLERS["local_model_status"]({}))


def test_it_names_the_seat_the_model_and_the_window(arbiter):
    out = _call()
    assert out["serving"] is True
    assert out["seat"] == "interactive_brain"
    assert out["model"] == "bonsai2:27b"
    assert out["context_tokens"] == 8192
    assert out["busy"] is False and out["busy_because"] == ""


def test_a_gpu_lease_reads_as_busy_with_the_reason(arbiter):
    arbiter["arb"] = _FakeArbiter(BRAIN, state="LEASED", lease={"kind": "image_job"})
    out = _call()
    assert out["busy"] is True
    assert "image job" in out["busy_because"]


def test_a_round_in_flight_to_the_local_seat_reads_as_busy(arbiter):
    from agent_friday.services import local_brain
    with local_brain.generating():
        out = _call()
    assert out["busy"] is True and out["rounds_in_flight"] == 1
    assert _call()["busy"] is False


def test_the_local_loop_counts_its_round_while_the_seat_generates(arbiter, monkeypatch):
    from agent_friday.services import agent as ag
    from agent_friday.services import local_brain
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {})
    monkeypatch.setattr(ag, "_get_vault_control", lambda *a, **k: None, raising=False)
    seen = []

    def send_fn(convo, tools, **over):
        seen.append(local_brain.rounds_in_flight())
        return {"choices": [{"message": {"content": "done."}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2}}

    text, _trace = ag._oai_agentic_loop([{"role": "user", "content": "hi"}], None,
                                        send_fn, provider="local", model="bonsai2:27b",
                                        session_ctx={})
    assert text == "done."
    assert seen == [1]
    assert local_brain.rounds_in_flight() == 0


def test_with_no_plan_it_says_it_cannot_confirm(monkeypatch):
    from agent_friday.services import residency_arbiter as ra
    monkeypatch.setattr(ra, "get_arbiter", lambda: None)
    out = _call()
    assert out["serving"] is False and out["model"] is None
    assert "cannot be confirmed" in out["note"]


def test_it_is_read_only_ring_zero():
    from agent_friday.services import agent
    assert agent.TOOL_RINGS["local_model_status"] == 0


def test_its_schema_is_not_in_the_always_on_catalogue_but_the_loader_has_it():
    from agent_friday.services import agent
    from agent_friday.services import tool_catalogue as tc
    assert "local_model_status" not in {t["name"] for t in agent.CLAUDE_TOOLS}
    assert "local_model_status" in {t["name"] for t in agent.WORKSPACE_TOOLS["settings"]}
    new, _msg = tc.expand(agent.CLAUDE_TOOLS, ["local_model_status"], [])
    assert [t["name"] for t in new] == ["local_model_status"]


def test_a_local_seat_is_told_the_tool_exists(arbiter):
    from agent_friday.services import local_brain
    assert "local_model_status" in local_brain.self_knowledge_line("local")


def test_the_counter_is_safe_across_threads():
    from agent_friday.services import local_brain

    def work():
        for _ in range(200):
            with local_brain.generating():
                pass

    ts = [threading.Thread(target=work) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert local_brain.rounds_in_flight() == 0
