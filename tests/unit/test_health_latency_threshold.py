"""The RAM-paging detector: a model that is merely alive is not healthy.

Phase A (D1/A2) made /api/health able to fail — it does a real one-token
generation instead of checking that a key string is non-empty. But it decided
status purely on "did any text come back", computed the latency, and spent it
only on the human-readable detail string. A model paging against RAM returns
text, slowly, and reported green. That is the exact failure this whole
residency layer exists to prevent, invisible to the only sensor that would see
it.

Two false-negative defects found on the live machine are pinned here too. Both
made health report failure while inference was fine, which is the inverse
error and just as damaging to trust.
"""
from __future__ import annotations

import pytest

from agent_friday.services import provider_health as ph

BASELINE = 20.26          # gemma4:12b, measured 2026-08-14


@pytest.fixture(autouse=True)
def catalog_baseline(monkeypatch):
    """Pin the baseline lookup; the rule is what is under test, not the store."""
    monkeypatch.setattr(ph, "_latency_verdict",
                        ph._latency_verdict, raising=False)
    import agent_friday.services.residency_catalog as rc
    import agent_friday.services.hardware_profile as hwp
    monkeypatch.setattr(hwp, "get", lambda *a, **k: {
        "gpus": [{"name": "NVIDIA GeForce RTX 4070",
                  "vram_total_mib": 12282}],
        "ram": {"total_mib": 32620}})
    monkeypatch.setattr(rc, "baseline_ms_per_token",
                        lambda m, fp: BASELINE if m == "gemma4:12b" else None)
    monkeypatch.setattr(rc, "baseline_probe_ms_per_token",
                        lambda m, fp: None)


def test_probe_baseline_is_preferred_over_the_sustained_one(monkeypatch):
    """Compare like with like.

    A 10-token probe pays fixed per-request overhead a 200-token run amortises
    away: measured, a healthy e2b probes at 8.68 ms/token against a 6.02
    sustained baseline. Comparing probe-to-sustained spends ~1.4x of the 5x
    margin before anything is actually wrong.
    """
    import agent_friday.services.residency_catalog as rc
    monkeypatch.setattr(rc, "baseline_probe_ms_per_token", lambda m, fp: 8.68)
    monkeypatch.setattr(rc, "baseline_ms_per_token", lambda m, fp: 6.02)
    # 40 ms/token: 4.6x the probe baseline (passes), 6.6x the sustained (fails)
    v = ph._latency_verdict("gemma4:e2b", 40.0)
    assert v is None, "the probe baseline is the correct comparator"
    v2 = ph._latency_verdict("gemma4:e2b", 50.0)
    assert v2 is not None and "probe" in v2


def test_falls_back_to_the_sustained_baseline_and_says_so(monkeypatch):
    import agent_friday.services.residency_catalog as rc
    monkeypatch.setattr(rc, "baseline_probe_ms_per_token", lambda m, fp: None)
    monkeypatch.setattr(rc, "baseline_ms_per_token", lambda m, fp: 20.26)
    v = ph._latency_verdict("gemma4:12b", 20.26 * 8)
    assert v is not None and "sustained" in v


# ── the threshold itself ─────────────────────────────────────────────────────

def test_healthy_speed_passes():
    assert ph._latency_verdict("gemma4:12b", BASELINE) is None


def test_moderately_slow_still_passes():
    """3x is bad but is not the collapse; 5x sits past the worst honest case."""
    assert ph._latency_verdict("gemma4:12b", BASELINE * 3) is None


def test_paging_collapse_is_caught():
    v = ph._latency_verdict("gemma4:12b", BASELINE * 8)
    assert v is not None
    assert "paging" in v


def test_the_verdict_states_both_numbers():
    v = ph._latency_verdict("gemma4:12b", BASELINE * 6)
    assert "%.2f" % (BASELINE * 6) in v
    assert "%.2f" % BASELINE in v


def test_the_boundary_is_five_times():
    assert ph.LATENCY_UNHEALTHY_MULTIPLE == 5.0
    assert ph._latency_verdict("gemma4:12b", BASELINE * 4.9) is None
    assert ph._latency_verdict("gemma4:12b", BASELINE * 5.1) is not None


# ── it must not fire on things that are not collapse ─────────────────────────

def test_an_unmeasured_model_is_never_failed_on_a_guess():
    """No baseline means liveness only, not a verdict from a global constant."""
    assert ph._latency_verdict("something:new", 9999.0) is None


def test_no_timing_yields_no_verdict():
    assert ph._latency_verdict("gemma4:12b", None) is None


def test_cold_load_cannot_trip_the_rule():
    """Ollama reports load_duration separately from eval_duration.

    A 55s cold load (measured for the 26b) contributes nothing to ms/token, so
    the detector cannot mistake loading for paging — otherwise it would fire
    RED on every cold start and be turned off within a week.
    """
    import agent_friday.routing.ollama_manager as om

    mgr = om.OllamaManager.__new__(om.OllamaManager)
    mgr._post = lambda path, body, timeout=30: {
        "response": "Hello!",
        "eval_count": 100,
        "eval_duration": 2_026_000_000,      # 20.26 ms/token — healthy
        "load_duration": 55_000_000_000,     # 55s cold load
    }
    out = mgr.probe_generate("gemma4:12b")
    assert out["ms_per_token"] == pytest.approx(20.26, abs=0.01)
    assert out["load_s"] == pytest.approx(55.0, abs=0.1)
    assert ph._latency_verdict("gemma4:12b", out["ms_per_token"]) is None


# ── false negative #1: thinking models ───────────────────────────────────────

def test_a_thinking_model_gets_thinking_disabled_for_the_probe():
    """Measured: gemma4:12b at num_predict=10 returns response='' with
    done_reason='length'; with think:false it returns 'Hello!'."""
    import agent_friday.routing.ollama_manager as om

    sent = {}
    mgr = om.OllamaManager.__new__(om.OllamaManager)

    def _post(path, body, timeout=30):
        sent.update(body)
        return {"response": "" if body.get("think") is not False else "Hello!",
                "eval_count": 3, "eval_duration": 60_000_000,
                "done_reason": "length"}

    mgr._post = _post
    assert mgr.probe_generate("gemma4:12b", disable_thinking=False)["ok"] is False
    assert mgr.probe_generate("gemma4:12b", disable_thinking=True)["ok"] is True
    assert sent["think"] is False


# ── false negative #2: probing an embedding model ────────────────────────────

def test_an_embedding_model_is_never_chosen_for_a_generation_probe(monkeypatch):
    """The live defect: local_model pointed at an uninstalled gemma3:4b, the
    fallback picked the SMALLEST installed model — qwen3-embedding:0.6b — and
    sent it to /api/generate, so the whole local provider read as down.
    """
    import agent_friday.services.residency_catalog as rc
    monkeypatch.setattr(rc, "can_generate",
                        lambda m: "embedding" not in m)
    monkeypatch.setattr(ph, "_load_settings",
                        lambda: {"model_routing": {"local_model": "gone:4b"}},
                        raising=False)

    class _Mgr:
        def list_models(self):
            return [{"name": "qwen3-embedding:0.6b", "size_gb": 0.6},
                    {"name": "gemma4:e2b", "size_gb": 7.2},
                    {"name": "gemma4:12b", "size_gb": 7.6}]

    monkeypatch.setattr(
        "agent_friday.routing.ollama_manager.get_manager",
        lambda *a, **k: _Mgr())

    chosen = ph.resident_model_for({"type": "ollama"})
    assert chosen == "gemma4:e2b", \
        "smallest GENERATION-capable model, not the smallest model"


def test_a_configured_embedding_model_is_also_rejected(monkeypatch):
    import agent_friday.services.residency_catalog as rc
    monkeypatch.setattr(rc, "can_generate", lambda m: "embedding" not in m)
    monkeypatch.setattr(
        ph, "_load_settings",
        lambda: {"model_routing": {"local_model": "qwen3-embedding:0.6b"}},
        raising=False)

    class _Mgr:
        def list_models(self):
            return [{"name": "qwen3-embedding:0.6b", "size_gb": 0.6},
                    {"name": "gemma4:12b", "size_gb": 7.6}]

    monkeypatch.setattr(
        "agent_friday.routing.ollama_manager.get_manager",
        lambda *a, **k: _Mgr())
    assert ph.resident_model_for({"type": "ollama"}) == "gemma4:12b"


def test_no_generation_model_installed_reports_none(monkeypatch):
    """Honest: an embedder-only install has nothing to prove generation with."""
    import agent_friday.services.residency_catalog as rc
    monkeypatch.setattr(rc, "can_generate", lambda m: "embedding" not in m)
    monkeypatch.setattr(ph, "_load_settings", lambda: {}, raising=False)

    class _Mgr:
        def list_models(self):
            return [{"name": "qwen3-embedding:0.6b", "size_gb": 0.6}]

    monkeypatch.setattr(
        "agent_friday.routing.ollama_manager.get_manager",
        lambda *a, **k: _Mgr())
    assert ph.resident_model_for({"type": "ollama"}) is None
