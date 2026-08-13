"""A4 — context budgets come from the catalog, not a flat constant (D3).

The audit found three truncation layers each using a hardcoded assumption while
model_discovery was already fetching and caching real per-model context windows
and showing them in the Model Browser. A small local model was budgeted exactly
like Claude Opus, so it would overflow before compaction ever fired.

These tests use a deliberately small window (4096) and assert the budget is
honoured end to end.
"""
from __future__ import annotations

import pytest

from agent_friday.services import compaction, model_catalog
from agent_friday.services import model_router


SMALL_MODEL = "tiny-local:4k"
BIG_MODEL = "claude-opus-4-8"


@pytest.fixture(autouse=True)
def _clear_ctx_cache():
    model_catalog.reset_context_window_cache()
    yield
    model_catalog.reset_context_window_cache()


@pytest.fixture
def catalog_windows(monkeypatch):
    """Pretend the catalog knows these windows."""
    table = {SMALL_MODEL: 4096, BIG_MODEL: 1_000_000}

    def _lookup(model_id):
        return table.get(model_id)

    monkeypatch.setattr(model_catalog, "context_window_for", _lookup)
    return table


def _messages(n=40, chars=1000):
    return [{"role": "user" if i % 2 == 0 else "assistant",
             "content": "x" * chars} for i in range(n)]


# ── compaction.py ────────────────────────────────────────────────────────────
def test_resolve_window_prefers_the_catalog(catalog_windows):
    assert compaction.resolve_context_window(SMALL_MODEL, {}) == 4096
    assert compaction.resolve_context_window(BIG_MODEL, {}) == 1_000_000


def test_resolve_window_falls_back_to_the_constant_when_catalog_is_silent(
        catalog_windows):
    """None from the catalog must mean 'use the documented default'."""
    assert compaction.resolve_context_window("unknown-model:xyz", {}) == 200000
    assert compaction.resolve_context_window(None, {}) == 200000


def test_configured_window_still_wins_over_the_default(catalog_windows):
    assert compaction.resolve_context_window(
        "unknown-model:xyz", {"context_window": 32768}) == 32768


def test_small_window_model_compacts_where_a_large_one_does_not(catalog_windows):
    """The end-to-end property: same transcript, different budgets.

    ~30K estimated tokens: over a 4096-window model's trigger, far under a
    1M-window model's. Before D3 both answered the same way.
    """
    cfg = {"enabled": True, "keep_head": 3, "keep_tail": 10,
           "trigger_ratio": 0.70}
    msgs = _messages(n=40, chars=3000)          # ~120K chars ≈ 30K tokens

    assert compaction.should_compact(msgs, model=SMALL_MODEL, cfg=cfg) is True
    assert compaction.should_compact(msgs, model=BIG_MODEL, cfg=cfg) is False


def test_maybe_compact_actually_shrinks_for_the_small_model(catalog_windows):
    cfg_msgs = _messages(n=40, chars=3000)
    out = compaction.maybe_compact(
        list(cfg_msgs), model=SMALL_MODEL,
        summarizer=lambda text, max_tokens=400: "condensed")

    assert len(out) < len(cfg_msgs), "small-window model was not compacted"
    assert any(compaction._SUMMARY_PREFIX in (m.get("content") or "")
               for m in out)


def test_maybe_compact_leaves_the_large_model_alone(catalog_windows):
    msgs = _messages(n=40, chars=3000)
    out = compaction.maybe_compact(
        list(msgs), model=BIG_MODEL,
        summarizer=lambda text, max_tokens=400: "condensed")
    assert len(out) == len(msgs)


# ── model_router trajectory compression ──────────────────────────────────────
def test_trajectory_limit_scales_with_the_window(catalog_windows):
    """2M chars is an Opus-sized threshold; a 4K model must get a small one."""
    small = model_router._traj_char_limit_for(SMALL_MODEL)
    big = model_router._traj_char_limit_for(BIG_MODEL)

    assert small < big
    assert small == max(4_000, int(4096 * 0.5 * 4))     # 8192 chars
    assert big == int(1_000_000 * 0.5 * 4)


def test_trajectory_limit_falls_back_to_the_documented_constant(catalog_windows):
    assert model_router._traj_char_limit_for(None) == model_router._TRAJ_CHAR_LIMIT
    assert (model_router._traj_char_limit_for("unknown-model:xyz")
            == model_router._TRAJ_CHAR_LIMIT)


def test_trajectory_compresses_for_small_window_but_not_by_default(
        catalog_windows, monkeypatch):
    monkeypatch.setattr(model_router, "_generate_text",
                        lambda *a, **k: "summary of the old turns")
    msgs = _messages(n=60, chars=1000)      # 60K chars: over 8192, under 2M

    compressed = model_router._compress_trajectory(list(msgs),
                                                   model=SMALL_MODEL)
    untouched = model_router._compress_trajectory(list(msgs))

    assert len(compressed) < len(msgs), "small-window trajectory not compressed"
    assert len(untouched) == len(msgs), "default behaviour changed"


# ── the real lookup ──────────────────────────────────────────────────────────
def test_context_window_for_returns_none_when_unknown():
    """None is a meaningful answer — callers key their fallback on it."""
    assert model_catalog.context_window_for("definitely-not-a-model") is None
    assert model_catalog.context_window_for("") is None
    assert model_catalog.context_window_for(None) is None


def test_local_models_get_their_window_from_the_daemon(monkeypatch):
    """Ollama models are the case D3 exists for and had NO other source.

    Descriptors declare no context_window and API discovery doesn't cover
    Ollama, so without asking the daemon every local model would fall back to
    the 200_000 constant — the exact mismatch D3 is meant to end.
    """
    class _Mgr:
        def list_models(self):
            return [{"name": "gemma4:e4b", "model": "gemma4:e4b"}]

        def context_length(self, model):
            assert model == "gemma4:e4b"
            return 131072

    monkeypatch.setattr("agent_friday.routing.ollama_manager.get_manager",
                        lambda *a, **k: _Mgr(), raising=False)

    assert model_catalog.context_window_for("gemma4:e4b") == 131072


def test_daemon_lookup_is_skipped_for_models_it_does_not_have(monkeypatch):
    class _Mgr:
        def list_models(self):
            return [{"name": "gemma4:e4b", "model": "gemma4:e4b"}]

        def context_length(self, model):
            raise AssertionError("should not be asked about a foreign model")

    monkeypatch.setattr("agent_friday.routing.ollama_manager.get_manager",
                        lambda *a, **k: _Mgr(), raising=False)

    assert model_catalog.context_window_for("gpt-4o-mini-not-local") is None


def test_ollama_context_length_parses_the_arch_prefixed_key(monkeypatch):
    """The GGUF key is `<arch>.context_length`; match on suffix, not guesswork."""
    from agent_friday.routing.ollama_manager import OllamaManager

    mgr = OllamaManager("http://localhost:11434")
    monkeypatch.setattr(mgr, "_post", lambda *a, **k: {
        "model_info": {"qwen3.embedding_length": 4096,
                       "qwen3.context_length": 32768}})
    assert mgr.context_length("qwen3.6:35b") == 32768

    monkeypatch.setattr(mgr, "_post", lambda *a, **k: {"model_info": {}})
    assert mgr.context_length("x") is None

    def _boom(*a, **k):
        raise ConnectionError("daemon down")

    monkeypatch.setattr(mgr, "_post", _boom)
    assert mgr.context_length("x") is None
