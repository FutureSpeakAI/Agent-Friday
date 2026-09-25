"""Headroom is reported truthfully and kept on this machine.

The shipped Windows wheel (headroom-ai 0.20.15) has no native core, and its
compress() quietly hands the input back. Friday counted that as a working
compressor saving 0%. These tests pin the honest readings and the settings
that keep Headroom's own storage and telemetry off the network and the disk.
"""
from __future__ import annotations

import sys
import types

import pytest

from agent_friday.pipeline import context_compressor as ccmod
from agent_friday.pipeline.context_compressor import ContextCompressor


def _fake_headroom(monkeypatch, *, with_core, compress=None):
    pkg = types.ModuleType("headroom")
    pkg.__version__ = "9.9.9"
    pkg.compress = compress or (lambda msgs, model=None: types.SimpleNamespace(
        messages=msgs, tokens_before=0, tokens_after=0, tokens_saved=0))
    monkeypatch.setitem(sys.modules, "headroom", pkg)
    if with_core:
        monkeypatch.setitem(sys.modules, "headroom._core", types.ModuleType("headroom._core"))
    else:
        monkeypatch.setitem(sys.modules, "headroom._core", None)   # import raises


def test_missing_native_core_is_reported_as_unavailable(monkeypatch):
    _fake_headroom(monkeypatch, with_core=False)
    cc = ContextCompressor()
    msgs = [{"role": "user", "content": "x" * 8000}]
    assert cc.compress(msgs) is msgs
    s = cc.get_stats()
    assert s["available"] is False
    assert "native core" in s["reason"] and s["version"] == "9.9.9"
    assert s["calls"] == 0


def test_a_passthrough_is_not_counted_as_a_compression():
    cc = ContextCompressor()
    cc._headroom = lambda msgs, model=None: types.SimpleNamespace(
        messages=msgs, tokens_before=0, tokens_after=0, tokens_saved=0)
    msgs = [{"role": "user", "content": "x" * 8000}]
    assert cc.compress(msgs) is msgs
    s = cc.get_stats()
    assert s["calls"] == 0 and s["passthrough"] == 1 and s["tokens_saved"] == 0


def test_headroom_environment_is_pinned_before_import(monkeypatch, tmp_path):
    monkeypatch.setenv("HEADROOM_TELEMETRY", "on")
    monkeypatch.setenv("HEADROOM_CCR_BACKEND", "sqlite")
    monkeypatch.delenv("HEADROOM_WORKSPACE_DIR", raising=False)
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    seen = {}

    def _compress(msgs, model=None):
        import os
        seen.update({k: os.environ.get(k) for k in
                     ("HEADROOM_TELEMETRY", "HEADROOM_CCR_BACKEND", "HEADROOM_WORKSPACE_DIR")})
        return types.SimpleNamespace(messages=[{"role": "user", "content": "short"}],
                                     tokens_before=2000, tokens_after=10, tokens_saved=1990)
    _fake_headroom(monkeypatch, with_core=True, compress=_compress)
    cc = ContextCompressor()
    cc.compress([{"role": "user", "content": "x" * 8000}], seat="local")
    assert seen["HEADROOM_TELEMETRY"] == "off"
    assert seen["HEADROOM_CCR_BACKEND"] == "memory"
    assert seen["HEADROOM_WORKSPACE_DIR"].startswith(str(tmp_path))
    s = cc.get_stats()
    assert s["available"] is True and s["calls"] == 1 and s["by_seat"] == {"local": 1}


def test_compress_new_touches_only_the_new_messages():
    seen = []

    def _compress(msgs, model=None):
        seen.append(list(msgs))
        return types.SimpleNamespace(messages=[{"role": "tool", "tool_call_id": "c2", "content": "dense"}],
                                     tokens_before=3000, tokens_after=5, tokens_saved=2995)
    cc = ContextCompressor(min_tokens_to_compress=100)
    cc._headroom = _compress
    old = [{"role": "user", "content": "q"},
           {"role": "tool", "tool_call_id": "c1", "content": "y" * 9000}]
    new = [{"role": "tool", "tool_call_id": "c2", "content": "z" * 9000}]
    out = cc.compress_new(old + new, start=2)
    assert out[:2] == old and out[2]["content"] == "dense"
    assert seen == [new]


# ── available means compressing ────────────────────────────────────────────

def _unchanged_with_counts(msgs, model=None):
    """A Headroom that reports token counts and hands back an equal copy."""
    return types.SimpleNamespace(messages=[dict(m) for m in msgs], tokens_before=2000,
                                 tokens_after=2000, tokens_saved=0)


def test_counts_without_a_saving_are_a_passthrough(monkeypatch):
    _fake_headroom(monkeypatch, with_core=True, compress=_unchanged_with_counts)
    cc = ContextCompressor()
    msgs = [{"role": "user", "content": "x" * 8000}]
    assert cc.compress(msgs) is msgs
    s = cc.get_stats()
    assert s["calls"] == 0 and s["passthrough"] == 1 and s["tokens_saved"] == 0


def test_a_headroom_that_never_compresses_stops_claiming_to_be_available(monkeypatch):
    _fake_headroom(monkeypatch, with_core=True, compress=_unchanged_with_counts)
    cc = ContextCompressor()
    msgs = [{"role": "user", "content": "x" * 8000}]
    for _ in range(ccmod._NOTHING_COMPRESSED_AFTER):
        cc.compress(msgs)
    s = cc.get_stats()
    assert s["available"] is False
    assert "compressed nothing" in s["reason"] and "9.9.9" in s["reason"]


def test_failures_count_toward_compressing_nothing(monkeypatch):
    def boom(msgs, model=None):
        raise RuntimeError("tokenizer vocabulary unreachable")
    _fake_headroom(monkeypatch, with_core=True, compress=boom)
    cc = ContextCompressor()
    for _ in range(ccmod._NOTHING_COMPRESSED_AFTER):
        cc.compress([{"role": "user", "content": "x" * 8000}])
    assert cc.get_stats()["available"] is False


def test_one_real_saving_keeps_it_available(monkeypatch):
    def half(msgs, model=None):
        out = [{"role": m["role"], "content": m["content"][: len(m["content"]) // 2]} for m in msgs]
        return types.SimpleNamespace(messages=out, tokens_before=2000, tokens_after=1000,
                                     tokens_saved=1000)
    calls = {"n": 0}

    def sometimes(msgs, model=None):
        calls["n"] += 1
        return half(msgs) if calls["n"] == 1 else _unchanged_with_counts(msgs)
    _fake_headroom(monkeypatch, with_core=True, compress=sometimes)
    cc = ContextCompressor()
    for _ in range(ccmod._NOTHING_COMPRESSED_AFTER + 2):
        cc.compress([{"role": "user", "content": "x" * 8000}])
    s = cc.get_stats()
    assert s["available"] is True and s["calls"] == 1


def test_the_installed_headroom_really_compresses(monkeypatch, tmp_path_factory):
    """The pinned library itself: installed means it loads its native core and
    makes a compressible tool result smaller, keeping the ids in it. A wheel
    without its core, or a release whose compress() is a no-op, fails here
    instead of shipping as a working 0%."""
    import importlib.util
    import json
    if importlib.util.find_spec("headroom") is None:
        pytest.skip("headroom-ai is not installed in this environment")
    # One tokenizer download per test session, not per test home.
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR",
                       str(tmp_path_factory.getbasetemp().parent / "tiktoken-cache"))
    rows = [{"id": "INC-%05d" % i, "status": "open" if i % 3 else "closed",
             "severity": "low", "owner": "ops", "tags": ["disk", "north"],
             "note": "routine check, nothing found"} for i in range(400)]
    msgs = [{"role": "user", "content": "list incidents"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "list", "input": {}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": json.dumps(rows)}]}]
    cc = ContextCompressor(min_tokens_to_compress=100)
    out = cc.compress(msgs, model="claude-opus-5-5", seat="local")
    s = cc.get_stats()
    assert s["available"] is True, s.get("reason")
    assert s["calls"] == 1 and s["tokens_saved"] > 0, s
    assert "INC-00399" in json.dumps(out)
