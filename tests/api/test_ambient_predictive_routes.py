"""API coverage for predictive workspaces, ambient awareness, and task-chaining
workflows. All offline: usage/ambient/chain state lands under the isolated temp
home, and no LLM is touched (chains only *register*; we don't spawn real agents)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


# ── Ambient awareness ─────────────────────────────────────────────────────────
class TestAmbientState:
    def test_state_shape(self, client):
        resp = client.get("/api/ambient/state")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        st = data["state"]
        for k in ("energy_level", "focus_quality", "stress_indicator", "creative_flow"):
            assert 0.0 <= st[k] <= 1.0
        assert st["label"] in ("creative_flow", "stressed", "low_energy", "focused", "steady")
        assert "hints" in st and "response_length" in st["hints"]
        assert st["scene_mood"]  # non-empty mood for the holographic scene


# ── Predictive workspaces ─────────────────────────────────────────────────────
class TestPredictiveWorkspaces:
    def test_visit_then_predict(self, client):
        # Record several opens; the heavily-used one should surface in predictions.
        for _ in range(5):
            r = client.post("/api/workspace/visit", json={"workspace": "news"})
            assert r.status_code == 200
        client.post("/api/workspace/visit", json={"workspace": "code"})

        preds = client.get("/api/workspace/predictions").get_json()
        assert preds["status"] == "ok"
        wss = [p["workspace"] for p in preds["predictions"]]
        assert "news" in wss
        # Each prediction carries a 0..1 score and a human reason.
        for p in preds["predictions"]:
            assert 0.0 <= p["score"] <= 1.0
            assert p["reason"]

    def test_visit_requires_workspace(self, client):
        assert client.post("/api/workspace/visit", json={}).status_code == 400

    def test_home_and_system_ignored(self, client):
        # 'home'/'system' are navigation chrome, not learnable destinations.
        client.post("/api/workspace/visit", json={"workspace": "home"})
        preds = client.get("/api/workspace/predictions").get_json()["predictions"]
        assert "home" not in [p["workspace"] for p in preds]

    def test_prewarm_is_safe(self, client):
        resp = client.post("/api/workspace/prewarm")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"


# ── Task-chaining workflows ───────────────────────────────────────────────────
class TestWorkflowChains:
    def test_create_list_get_run_delete(self, client, server_module):
        defn = {
            "name": "Research → Draft",
            "description": "Two-step chain",
            "steps": [
                {"name": "Research", "prompt": "Research the topic."},
                {"name": "Draft", "prompt": "Draft a brief.", "with_context": True},
            ],
        }
        created = client.post("/api/workflows/chains", json=defn).get_json()
        assert created["status"] == "ok"
        slug = created["chain"]["slug"]
        assert created["chain"]["steps"] and len(created["chain"]["steps"]) == 2

        listed = client.get("/api/workflows/chains").get_json()
        assert slug in [c["slug"] for c in listed["chains"]]

        got = client.get(f"/api/workflows/chains/{slug}").get_json()
        assert got["chain"]["name"] == "Research → Draft"

        # Run spawns the first task (background thread); we just confirm wiring.
        run = client.post(f"/api/workflows/chains/{slug}/run").get_json()
        assert run["status"] == "ok"
        assert run["task_id"]
        snap = server_module._task_snapshot(run["task_id"])
        assert snap is not None and snap.get("chain") == slug

        assert client.delete(f"/api/workflows/chains/{slug}").status_code == 200
        assert client.get(f"/api/workflows/chains/{slug}").status_code == 404

    def test_create_rejects_empty_steps(self, client):
        resp = client.post("/api/workflows/chains", json={"name": "Bad", "steps": []})
        assert resp.status_code == 400

    def test_step_without_prompt_rejected(self, client):
        resp = client.post("/api/workflows/chains",
                           json={"name": "Bad2", "steps": [{"name": "x"}]})
        assert resp.status_code == 400

    def test_run_unknown_chain_404(self, client):
        assert client.post("/api/workflows/chains/does-not-exist/run").status_code == 404

    def test_advance_chain_spawns_next_step(self, client, server_module, monkeypatch):
        """_advance_task_chain should spawn the next step, threading the prior
        result into its prompt when with_context is set. Patch _spawn_task on the
        module that DEFINES _advance_task_chain (it resolves the name in its own
        namespace, not server's re-export)."""
        import services.agent as agent_mod
        spawned = {}

        def fake_spawn(name, prompt, description='', on_complete=None, chain=None, chain_step=0):
            spawned.update(name=name, prompt=prompt, chain=chain, chain_step=chain_step)
            return "next-task-id"

        client.post("/api/workflows/chains", json={
            "name": "Chain Adv",
            "steps": [
                {"name": "S1", "prompt": "first"},
                {"name": "S2", "prompt": "second", "with_context": True},
            ],
        })
        monkeypatch.setattr(agent_mod, "_spawn_task", fake_spawn, raising=False)

        # Seed a finished step-0 task in the registry, then advance it.
        with agent_mod.TASKS_LOCK:
            agent_mod.TASKS["t0"] = {
                "task_id": "t0", "name": "S1", "chain": "chain-adv", "chain_step": 0,
                "on_complete": None, "log": [],
            }
        agent_mod._advance_task_chain("t0", "RESULT-FROM-S1")

        assert spawned.get("chain_step") == 1
        assert spawned.get("name") == "S2"
        assert "RESULT-FROM-S1" in spawned.get("prompt", "")
