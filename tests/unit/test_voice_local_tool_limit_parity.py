"""A tool called by the LOCAL voice front keeps the cloud path's manners
(local voice spec P3): the owner's time limit with the same sentence when it
passes, a process orb as the execution receipt, and the late-result label
when the owner spoke while it ran. No egress gate: the result stays local.
"""
import time

import pytest

import agent_friday.routes.voice as rv


@pytest.fixture
def orbs(monkeypatch):
    seen = {"start": [], "finish": []}
    monkeypatch.setattr(rv, "_voice_orb_start", lambda f: seen["start"].append(f) or "orb1")
    monkeypatch.setattr(rv, "_voice_orb_finish",
                        lambda oid, f, a, r, ms: seen["finish"].append((f, r)))
    return seen


def test_a_slow_tool_hits_the_owners_limit_with_the_cloud_sentence(orbs, monkeypatch):
    monkeypatch.setattr(rv, "voice_tool_limit_s", lambda settings=None: 0.2)
    monkeypatch.setattr(rv, "_voice_tool_run",
                        lambda n, a, send, session=None: time.sleep(1.0) or "late")
    t0 = time.perf_counter()
    out = rv._local_voice_tool("search_web", {"query": "x"}, lambda o: True, {})
    assert time.perf_counter() - t0 < 0.8
    assert out == rv._tool_timeout_message("search_web", 0.2)
    assert orbs["start"] == ["search_web"] and orbs["finish"][0][0] == "search_web"


def test_a_result_after_the_owner_spoke_is_labelled_late(orbs, monkeypatch):
    monkeypatch.setattr(rv, "VOICE_TOOL_SLOW_S", 0.05)
    monkeypatch.setattr(rv, "voice_tool_limit_s", lambda settings=None: 5)
    session = {"user_spoke_at": 0.0}

    def runner(n, a, send, session=None):
        session["user_spoke_at"] = time.time() + 3.0      # he spoke while it ran
        time.sleep(0.1)
        return "3 stories"
    monkeypatch.setattr(rv, "_voice_tool_run", runner)
    out = rv._local_voice_tool("search_news", {}, lambda o: True, session)
    assert out.startswith("[LATE RESULT for search_news") and out.endswith("3 stories")


def test_no_egress_gate_on_the_local_path(orbs, monkeypatch):
    monkeypatch.setattr(rv, "voice_tool_limit_s", lambda settings=None: 5)
    monkeypatch.setattr(rv, "_voice_tool_run", lambda n, a, send, session=None: "TIER_3 note")
    monkeypatch.setattr(rv, "_gate_voice_tool_result",
                        lambda r, f: pytest.fail("the local front's result was gated"))
    assert rv._local_voice_tool("search_wiki", {"query": "q"}, lambda o: True, {}) == "TIER_3 note"


def test_the_front_runs_its_tools_through_the_parity_runner():
    import inspect
    src = inspect.getsource(rv)
    assert "run_tool=lambda n, a: _local_voice_tool(n, a, _send, _tool_session)" in src
