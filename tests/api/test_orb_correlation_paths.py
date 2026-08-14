"""2026-08-14 defect #5 — orb/trace correlation through NON-interactive
spawn paths.

Stephen saw gemma4:e4b process orbs whose thread views were empty. The B3
enrichment (task_id on orbs, lifecycle + tool log lines, /api/tasks join)
must hold on the scheduler spawn path and the seat-fallback path — not just
interactive chat spawns. These tests pin each hop of the chain.
"""
from __future__ import annotations

import agent_friday.core as core
import agent_friday.services.agent as agent_mod
import agent_friday.services.model_router as mr


def _fake_manager(monkeypatch, text="All quiet — heartbeat done."):
    class FakeMgr:
        base_url = "http://fake:11434"

        def is_available(self):
            return True

        def chat_completion(self, messages, model=None, tools=None, **kw):
            return {"choices": [{"message": {"role": "assistant",
                                             "content": text},
                                 "finish_reason": "stop"}],
                    "model": model,
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    import agent_friday.routing.ollama_manager as om
    monkeypatch.setattr(om, "get_manager", lambda url=None: FakeMgr())
    return FakeMgr


class TestSchedulerStyleLocalSpawn:
    def test_local_orb_carries_task_id_and_nonempty_log(self, client, monkeypatch):
        # The scheduler spawn path reaches _call_ollama with session_ctx
        # carrying the spawned task's id. The orb must join back to it AND
        # have a non-empty thread even for a text-only reply.
        _fake_manager(monkeypatch)
        # Green-light the seat so the gate doesn't reroute this test.
        from agent_friday.services import model_seat_gate as gate
        monkeypatch.setattr(gate, "resolve_local_seat",
                            lambda m, provider="local": {
                                "model": m, "seat_ok": True, "requested": m,
                                "reason": "gated green", "fallback": None})

        before = set(core.PROCESSES.keys())
        text, trace = mr._call_ollama(
            [{"role": "user", "content": "heartbeat"}],
            model="gemma4:e4b", tools=None,
            session_ctx={"authenticated": True, "is_background_task": True,
                         "task_id": "t-sched-123"},
        )
        assert "quiet" in text
        new = [pid for pid in core.PROCESSES if pid not in before
               and pid.startswith("local-")]
        assert new, "local dispatch must register a process orb"
        proc = core.PROCESSES[new[-1]]
        assert proc.get("task_id") == "t-sched-123", (
            "orb → task correlation must survive the scheduler spawn path")
        assert proc.get("log"), (
            "the orb thread must never be empty — lifecycle lines required")
        assert any("gemma4:e4b" in str(line) for line in proc["log"]), (
            "the thread must name the model that ran")

    def test_tasks_route_joins_local_orb_to_log_and_model(self, client):
        core.process_register("local-testorb1", name="Local Inference",
                              label="test", category="monitoring", icon="🏠",
                              model="gemma4:e4b", task_id="t-join-1")
        core.process_log("local-testorb1", "model: gemma4:e4b (local)")
        try:
            resp = client.get("/api/tasks/local-testorb1")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data.get("model") == "gemma4:e4b"
            assert data.get("log"), "join must expose the orb's log lines"
        finally:
            core.process_remove("local-testorb1")


class TestSeatFallbackSpawn:
    def test_fallback_leg_carries_session_ctx_task_id(self, client, monkeypatch):
        # Ladder falls local → cloud: the cloud leg must receive the SAME
        # session_ctx (task_id included) so its orb correlates too.
        seen = {}

        def fake_ollama(messages, **kwargs):
            raise RuntimeError("HTTP Error 404: Not Found")

        def fake_claude(messages, session_ctx=None, **kwargs):
            seen["session_ctx"] = session_ctx
            return "cloud fallback answered", []

        monkeypatch.setattr(agent_mod, "_call_ollama", fake_ollama)
        monkeypatch.setattr(agent_mod, "_call_claude_agent", fake_claude)
        monkeypatch.setattr(agent_mod, "get_anthropic_client", lambda: object())

        agent_mod._generate_agent(
            [{"role": "user", "content": "hb"}], model="gemma4:e4b",
            session_ctx={"authenticated": True, "task_id": "t-fall-9"},
        )
        assert (seen.get("session_ctx") or {}).get("task_id") == "t-fall-9", (
            "the fallback leg dropped the correlation id — its orb would be "
            "an orphan (the 2026-08-14 empty-thread class)")
