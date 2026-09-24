"""GET /api/governance/receipts: the morning receipt page's data.

Read-only. It lists every checkpoint decision in a window with its signature
checked, and flags a receipt that was changed after it was signed.
"""
from __future__ import annotations

import json
import time

import pytest

from agent_friday.governance import action_gate

KEY = b"r" * 32


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(action_gate, "friday_home", lambda: str(tmp_path))
    monkeypatch.setattr(action_gate, "_governance_key", lambda: KEY)
    return tmp_path


def test_receipts_route_returns_verified_entries_and_flags_a_tampered_one(client, home):
    action_gate.authorize("search_web", {}, {"is_background_task": True, "task_id": "t-9"})
    action_gate.authorize("run_command", {"command": "Remove-Item x"},
                          {"is_background_task": True, "task_id": "t-9"})
    path = home / "decision-bom.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    bad = json.loads(lines[1])
    bad["decision"] = "allow"                     # a held action rewritten as allowed
    lines[1] = json.dumps(bad)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    r = client.get(f"/api/governance/receipts?since={time.time() - 3600}")
    assert r.status_code == 200, r.get_data(as_text=True)
    d = r.get_json()
    by_tool = {a["tool"]: a for a in d["actions"]}
    assert by_tool["search_web"]["verified"] is True
    assert by_tool["search_web"]["task_id"] == "t-9"
    assert by_tool["run_command"]["verified"] is False
    assert d["verified"] == 1 and d["not_verified"] == 1


def test_receipts_route_defaults_to_the_last_day_and_rejects_bad_input(client, home):
    d = client.get("/api/governance/receipts").get_json()
    assert abs((d["until"] - d["since"]) - 86400) < 5
    assert client.get("/api/governance/receipts?since=nope").status_code == 400
    assert client.get("/api/governance/receipts?date=2026-13-40").status_code == 400
    day = client.get("/api/governance/receipts?date=2026-09-24&tz_offset_min=0").get_json()
    assert day["until"] - day["since"] == 86400


def test_receipts_route_is_read_only(client, home):
    assert client.post("/api/governance/receipts").status_code == 405
