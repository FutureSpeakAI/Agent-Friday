"""A14: the privacy gate runs under unrestricted cloud.

The owner's ruling (docs/design/north-star/AMENDMENTS.md, A14): under unrestricted cloud
consent the egress gate still runs, still records every call in the egress log, and still
applies the never-send floor. The consent changes what the gate permits (tier withholding
and the identifier scrub are widened), never whether it runs.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import egress_gate as eg
from agent_friday.services import judgment_gate as jg

SECRET = "the never-send marker is right here"


def _unrestricted(monkeypatch, on=True):
    monkeypatch.setattr(eg, "is_unrestricted_cloud", lambda: on)
    monkeypatch.setattr(jg, "never_send_hits", lambda text: ["marker"] if "marker" in str(text) else [])
    monkeypatch.setattr(eg, "_never_send_covered_by_override", lambda text: False)


def _entries(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.exists() else []


def test_the_never_send_floor_stops_text_under_unrestricted_cloud(monkeypatch, tmp_path):
    _unrestricted(monkeypatch)
    with pytest.raises(eg.NeverSendBlocked):
        eg._gate_text_span(SECRET, "anthropic", "prompt", tmp_path / "log.jsonl")
    assert _entries(tmp_path / "log.jsonl")[-1]["action"] == "block"


def test_the_never_send_floor_stops_tool_prose_under_unrestricted_cloud(monkeypatch, tmp_path):
    _unrestricted(monkeypatch)
    with pytest.raises(eg.NeverSendBlocked):
        eg._gate_tool_prose("a tool description containing the marker", "anthropic", "tool.description",
                            tmp_path / "log.jsonl")


def test_seal_outbound_runs_the_gate_under_unrestricted_cloud(monkeypatch, tmp_path):
    _unrestricted(monkeypatch)
    log = tmp_path / "log.jsonl"
    with pytest.raises(eg.NeverSendBlocked):
        eg.seal_outbound({"messages": [{"role": "user", "content": SECRET}]}, "anthropic", log_path=log)
    assert any(e["action"] == "block" for e in _entries(log))


def test_a_file_grant_override_still_lets_covered_never_send_text_through(monkeypatch, tmp_path):
    _unrestricted(monkeypatch)
    monkeypatch.setattr(eg, "_never_send_covered_by_override", lambda text: True)
    assert eg._gate_text_span(SECRET, "anthropic", "prompt", tmp_path / "log.jsonl") == SECRET


def test_unrestricted_cloud_still_widens_the_policy(monkeypatch, tmp_path):
    """Tier withholding and the identifier scrub are what the consent lifts."""
    _unrestricted(monkeypatch)
    monkeypatch.setattr(eg, "_classify_cloud", lambda text: eg.Tier.SENSITIVE)
    log = tmp_path / "log.jsonl"
    text = "custody hearing on the 14th, account 9876543210"
    assert eg._gate_text_span(text, "anthropic", "prompt", log) == text
    out = eg.seal_outbound({"system": text, "messages": [{"role": "user", "content": text}]}, "anthropic",
                           log_path=log)
    assert out["system"] == text and out["messages"][0]["content"] == text and "[PII:" not in out["system"]


def test_every_call_is_recorded_under_unrestricted_cloud(monkeypatch, tmp_path):
    _unrestricted(monkeypatch)
    log = tmp_path / "log.jsonl"
    eg.seal_outbound({"system": "be brief", "messages": [{"role": "user", "content": "hello there"}],
                      "tools": [{"name": "t", "description": "a tool"}]}, "anthropic", log_path=log)
    fields = {e["field"] for e in _entries(log)}
    assert "*" in fields and "system" in fields, fields
    assert any("unrestricted" in e["reason"] for e in _entries(log))


def test_restricted_mode_is_unchanged(monkeypatch, tmp_path):
    _unrestricted(monkeypatch, on=False)
    with pytest.raises(eg.NeverSendBlocked):
        eg._gate_text_span(SECRET, "anthropic", "prompt", tmp_path / "log.jsonl")


def test_the_startup_self_test_exercises_the_gate_under_unrestricted_cloud(monkeypatch):
    _unrestricted(monkeypatch)
    monkeypatch.setattr(eg, "_SELF_TEST_RESULT", None)
    res = eg.startup_self_test()
    assert res["ok"] is True and res.get("unrestricted_cloud") is True
    assert res.get("gate_ran") is True and res.get("floor_ran") is True, res
    assert "not run" not in res.get("note", "")
    # a gate that cannot apply the floor is a broken gate even under consent
    monkeypatch.setattr(eg, "_SELF_TEST_RESULT", None)
    monkeypatch.setattr(jg, "never_send_hits", lambda text: (_ for _ in ()).throw(RuntimeError("down")))
    res = eg.startup_self_test()
    assert res["ok"] is False
