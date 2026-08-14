"""Unit tests for services/activity_ledger.py (B4 — global activity ledger).

Covers: record/read round-trip, newest-first ordering, per-field whitelisting
(metadata only, by construction), filters, rotation, and a thread-safety smoke.
"""

import json
import threading
import time
from pathlib import Path

import pytest

from agent_friday.services import activity_ledger as al


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch):
    """Point the module at a per-test ledger file (and restore rotation size)."""
    monkeypatch.setattr(al, "LEDGER_FILE", tmp_path / "activity_ledger.jsonl")
    monkeypatch.setattr(al, "_MAX_BYTES", 20 * 1024 * 1024)
    yield


def test_record_and_read_roundtrip():
    assert al.record("model_invocation", model="claude-sonnet-5",
                     provider="anthropic", seat="cloud", duration_ms=1234,
                     tokens_in=100, tokens_out=50, orb_id="agent-abc",
                     task_id="t-1", workspace="task")
    events = al.read()
    assert len(events) == 1
    ev = events[0]
    assert ev["kind"] == "model_invocation"
    assert ev["model"] == "claude-sonnet-5"
    assert ev["seat"] == "cloud"
    assert ev["tokens_in"] == 100
    assert ev["orb_id"] == "agent-abc"
    assert isinstance(ev["ts"], float)


def test_read_newest_first():
    for i in range(5):
        al.record("tool_call", tool=f"tool_{i}", ok=True, duration_ms=i)
    events = al.read()
    assert [e["tool"] for e in events] == ["tool_4", "tool_3", "tool_2", "tool_1", "tool_0"]


def test_unknown_kind_rejected():
    assert al.record("prompt_dump", text="secret") is False
    assert al.read() == []


def test_non_whitelisted_fields_dropped():
    """Metadata-only by construction: prompt/args/result fields can't enter."""
    al.record("tool_call", tool="search_web", ok=False, duration_ms=10,
              prompt="the user's secret prompt", args={"q": "secret"},
              result="secret result")
    ev = al.read()[0]
    assert "prompt" not in ev
    assert "args" not in ev
    assert "result" not in ev
    assert ev["tool"] == "search_web"
    assert ev["ok"] is False


def test_long_text_field_capped():
    al.record("subagent_spawn", task_id="t-9", description="x" * 5000, model="m")
    ev = al.read()[0]
    assert len(ev["description"]) == al._TEXT_CAP


def test_none_fields_omitted():
    al.record("tool_call", tool="wiki_read", ok=True, duration_ms=5,
              orb_id=None, task_id=None)
    ev = al.read()[0]
    assert "orb_id" not in ev
    assert "task_id" not in ev


def test_filters():
    t0 = time.time()
    al.record("model_invocation", model="gemma4:latest", provider="ollama",
              seat="local", duration_ms=1, task_id="t-A")
    al.record("model_invocation", model="claude-sonnet-5", provider="anthropic",
              seat="cloud", duration_ms=2, task_id="t-B")
    al.record("tool_call", tool="search_web", ok=True, duration_ms=3, task_id="t-B")

    assert [e["model"] for e in al.read(kind="model_invocation")] == \
        ["claude-sonnet-5", "gemma4:latest"]
    assert len(al.read(model="gemma4:latest")) == 1
    assert {e["kind"] for e in al.read(task_id="t-B")} == \
        {"model_invocation", "tool_call"}
    assert len(al.read(since=t0)) == 3
    assert al.read(since=time.time() + 60) == []
    assert al.read(until=t0 - 60) == []
    assert len(al.read(limit=2)) == 2


def test_rotation(monkeypatch):
    monkeypatch.setattr(al, "_MAX_BYTES", 300)
    for i in range(20):
        al.record("tool_call", tool=f"tool_{i}", ok=True, duration_ms=i)
    rotated = Path(str(al.LEDGER_FILE) + ".1")
    assert rotated.exists(), "ledger should have rotated to .1"
    # Single-generation rotation: only live + .1 are kept, so total retained =
    # lines(live) + lines(.1); read() must span BOTH, newest first.
    live_lines = al.LEDGER_FILE.read_text(encoding="utf-8").splitlines()
    rot_lines = rotated.read_text(encoding="utf-8").splitlines()
    events = al.read(limit=100)
    assert len(events) == len(live_lines) + len(rot_lines)
    assert 0 < len(events) < 20  # older generations were dropped by rotation
    assert events[0]["tool"] == "tool_19"  # newest first across files
    # Events are contiguous from the newest backwards (no gaps in retained set).
    idx = [int(e["tool"].split("_")[1]) for e in events]
    assert idx == list(range(19, 19 - len(events), -1))


def test_thread_safety_smoke():
    def _writer(n):
        for i in range(20):
            al.record("tool_call", tool=f"w{n}_i{i}", ok=True, duration_ms=1)

    threads = [threading.Thread(target=_writer, args=(n,)) for n in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = al.LEDGER_FILE.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 200
    # Every line is intact JSON (no interleaved partial writes).
    for line in lines:
        json.loads(line)
    assert len(al.read(limit=500)) == 200


def test_record_never_raises_on_bad_path(monkeypatch, tmp_path):
    # Point at an unwritable location — record must swallow and return False.
    monkeypatch.setattr(al, "LEDGER_FILE",
                        tmp_path / "no_dir_here" / "\0bad" / "x.jsonl")
    assert al.record("tool_call", tool="x", ok=True, duration_ms=1) is False
