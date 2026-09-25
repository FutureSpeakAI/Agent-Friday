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
