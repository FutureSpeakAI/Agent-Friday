"""Turning a connector's isolation off through its settings is written down (owner
ruling 4, 2026-10-28).

A save of the connector settings that takes a connector out of Windows' isolated mode
writes a signed connector_run_unisolated entry in the action ledger first, the same
entry the "run it un-isolated" card writes. If that entry cannot be written, nothing is
saved and the connector stays as it was. Before, only the card wrote it: setting
"isolation": "off" in settings ran the connector un-isolated with no record.

Values are synthetic.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def saved(tmp_path, monkeypatch):
    import agent_friday.core as core
    from agent_friday.services import agent
    from agent_friday.services import approvals as ap
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(ap, "FRIDAY_DIR", tmp_path)
    f = tmp_path / "mcp_servers.json"
    monkeypatch.setattr(agent, "MCP_SERVERS_FILE", f)
    monkeypatch.setattr(agent, "FRIDAY_DIR", tmp_path)
    f.write_text(json.dumps({"servers": {
        "notes": {"command": "node", "args": ["notes.js"], "isolation": "appcontainer"},
        "legacy": {"command": "node", "args": ["legacy.js"]},
        "docker": {"command": "docker", "args": ["run"], "isolation": "off"},
        "remote": {"url": "https://example.invalid/mcp"},
    }}), encoding="utf-8")
    return f


def _entries(name=None):
    from agent_friday.governance import action_gate as gate
    rows = [r for r in gate.receipts() if r.get("tool") == "connector_run_unisolated"]
    return [r for r in rows if name is None or r.get("target") == name]


def _save(f, change):
    from agent_friday.services import agent
    cfg = json.loads(f.read_text(encoding="utf-8"))
    change(cfg["servers"])
    agent._save_mcp_servers(cfg)


def test_turning_an_isolated_connector_off_in_settings_writes_the_entry(saved):
    from agent_friday.governance import action_gate as gate
    _save(saved, lambda s: s["notes"].update(isolation="off"))
    mine = _entries("notes")
    assert len(mine) == 1, "isolation was turned off in settings with no ledger entry"
    assert mine[0]["decision"] == "allow" and gate.verify_receipt(mine[0])
    assert mine[0].get("isolation_before") == "isolated"


def test_removing_the_isolation_key_counts_as_turning_it_off(saved):
    _save(saved, lambda s: s["notes"].pop("isolation"))
    assert len(_entries("notes")) == 1


def test_a_connector_from_before_a25_set_off_and_a_new_one_added_off_are_written(saved):
    def change(s):
        s["legacy"]["isolation"] = "false"
        s["fresh"] = {"command": "node", "args": ["fresh.js"], "isolation": "no"}
    _save(saved, change)
    assert len(_entries("legacy")) == 1 and len(_entries("fresh")) == 1


def test_saves_that_turn_nothing_off_write_nothing(saved):
    def change(s):
        s["docker"]["args"] = ["run", "--rm"]               # already off: not a change
        s["legacy"]["args"] = ["legacy.js", "--quiet"]      # before A25, left as it is
        s["notes"]["args"] = ["notes.js", "--v"]            # still isolated
        s["remote"]["url"] = "https://example.invalid/mcp2"
    _save(saved, change)
    assert _entries() == []


def test_no_entry_means_nothing_is_saved(saved, monkeypatch):
    from agent_friday.governance import action_gate as gate
    before = saved.read_text(encoding="utf-8")

    def broken(entry):
        raise OSError("ledger unavailable")
    monkeypatch.setattr(gate, "_receipt", broken)
    with pytest.raises(Exception):
        _save(saved, lambda s: s["notes"].update(isolation="off"))
    assert saved.read_text(encoding="utf-8") == before
    assert json.loads(before)["servers"]["notes"]["isolation"] == "appcontainer"
