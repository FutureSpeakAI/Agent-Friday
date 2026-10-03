"""A local seat is told what it is: the model on this PC, its window, its
limits, and that the conversation stays on this machine. A cloud seat is not
told any of that, because none of it would be true."""
from __future__ import annotations

import types

import pytest


class _FakeArbiter:
    def __init__(self, seats):
        self.plan = {"seats": seats}
        self.state = "DEFAULT"
        self.lease = None


@pytest.fixture
def bonsai(monkeypatch):
    from agent_friday.services import residency_arbiter as ra
    arb = _FakeArbiter({"interactive_brain": {
        "model_id": "bonsai2:27b", "num_ctx": 8192, "status": "resident",
        "device": "gpu", "backend": "llama-server"}})
    monkeypatch.setattr(ra, "get_arbiter", lambda: arb)
    return arb


def test_the_line_names_the_serving_model_and_its_window(bonsai):
    from agent_friday.services import local_brain
    line = local_brain.self_knowledge_line("local")
    assert "bonsai2:27b" in line
    assert "8,192-token" in line
    assert "this PC" in line and "stays on this machine" in line
    assert "\n" not in line, "the self-knowledge is one line"


def test_a_cloud_provider_gets_no_line(bonsai):
    from agent_friday.services import local_brain
    assert local_brain.self_knowledge_line("cloud") == ""
    assert local_brain.self_knowledge_line("anthropic") == ""
    assert local_brain.self_knowledge_line("") == ""


def test_with_no_plan_it_claims_no_model_name(monkeypatch):
    from agent_friday.services import residency_arbiter as ra
    from agent_friday.services import local_brain
    monkeypatch.setattr(ra, "get_arbiter", lambda: None)
    line = local_brain.self_knowledge_line("local")
    assert line.startswith("You are the local model, running on this PC")
    assert "token working window" not in line


def test_the_local_system_prompt_carries_the_line_and_the_cloud_one_does_not(bonsai):
    from agent_friday.services.agent import _get_friday_system_prompt
    local = _get_friday_system_prompt(provider="local", vault_control=None)
    cloud = _get_friday_system_prompt(provider="cloud", vault_control=None)
    assert "== THIS SEAT ==" in local and "bonsai2:27b, running on this PC" in local
    assert "== THIS SEAT ==" not in cloud
    assert "running on this PC as Friday's brain seat" not in cloud


def test_the_brain_alias_is_read_when_the_canonical_role_is_absent(monkeypatch):
    from agent_friday.services import residency_arbiter as ra
    from agent_friday.services import local_brain
    arb = types.SimpleNamespace(plan={"seats": {"brain": {"model_id": "gemma4:12b"}}},
                                state="DEFAULT", lease=None)
    monkeypatch.setattr(ra, "get_arbiter", lambda: arb)
    assert local_brain.brain_seat()["model"] == "gemma4:12b"
