"""The morning receipt reads the signed decision log honestly.

Every checkpoint decision is a signed line in decision-bom.jsonl. The receipt
page lists them for a window of time with each signature checked: a line
that was edited after signing, or that carries no signature, is shown as
not verified -- never dropped, never shown as verified.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

from agent_friday.governance import action_gate
from agent_friday.services import morning_receipt

ROOT = Path(__file__).resolve().parents[2]
KEY = b"k" * 32


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(action_gate, "friday_home", lambda: str(tmp_path))
    monkeypatch.setattr(action_gate, "_governance_key", lambda: KEY)
    monkeypatch.setattr(morning_receipt, "_task_index", lambda: {
        "t-1": {"task_id": "t-1", "name": "Research: bridges", "status": "complete",
                "created": time.time() - 600, "ended": time.time() - 60},
        "t-old": {"task_id": "t-old", "name": "Old", "status": "complete",
                  "created": 1.0, "ended": 2.0},
    })
    return tmp_path


def _lines(home):
    return (home / "decision-bom.jsonl").read_text(encoding="utf-8").splitlines()


def test_authorize_signs_a_receipt_that_names_its_task(home):
    action_gate.authorize("search_web", {"query": "x"},
                          {"is_background_task": True, "task_id": "t-1"})
    entry = json.loads(_lines(home)[-1])
    assert entry["task_id"] == "t-1"
    assert action_gate.verify_receipt(entry, KEY)


def test_receipts_are_listed_verified_and_a_tampered_one_is_flagged(home):
    action_gate.authorize("search_web", {}, {"is_background_task": True, "task_id": "t-1"})
    action_gate.authorize("create_calendar_event", {}, {"is_background_task": True,
                                                        "task_id": "t-1"})
    action_gate.authorize("read_file", {}, {"session_id": "s"})
    lines = _lines(home)
    # Tamper with the calendar receipt: a "card" rewritten as "allow".
    tampered = json.loads(lines[1])
    assert tampered["decision"] == "card"
    tampered["decision"] = "allow"
    lines[1] = json.dumps(tampered)
    # And a line with no signature at all.
    lines.append(json.dumps({"tool": "forged", "decision": "allow",
                             "timestamp": json.loads(lines[0])["timestamp"]}))
    (home / "decision-bom.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    out = morning_receipt.build(time.time() - 3600)
    by_tool = {a["tool"]: a for a in out["actions"]}
    assert by_tool["search_web"]["verified"] is True
    assert by_tool["read_file"]["verified"] is True
    assert by_tool["create_calendar_event"]["verified"] is False
    assert by_tool["forged"]["verified"] is False
    assert out["verified"] == 2 and out["not_verified"] == 2
    assert by_tool["search_web"]["task_id"] == "t-1"
    assert by_tool["search_web"]["task_name"] == "Research: bridges"
    assert by_tool["search_web"]["surface"] == "background"
    assert [t["task_id"] for t in out["tasks"]] == ["t-1"], "only tasks that ended in the window"


def test_the_window_excludes_older_receipts(home):
    action_gate.authorize("search_web", {}, {"session_id": "s"})
    out = morning_receipt.build(time.time() + 60)
    assert out["actions"] == []


def test_without_the_signing_key_nothing_claims_to_be_verified(home, monkeypatch):
    action_gate.authorize("search_web", {}, {"session_id": "s"})

    def no_key():
        raise RuntimeError("governance key unavailable")
    monkeypatch.setattr(action_gate, "_governance_key", no_key)
    out = morning_receipt.build(time.time() - 3600)
    assert out["key_available"] is False
    assert out["verified"] == 0 and all(not a["verified"] for a in out["actions"])


@pytest.mark.parametrize("rel", ["index.html", "ui_parts/app.html"])
def test_the_receipt_card_is_in_the_system_workspace(rel):
    text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
    assert "function WhatFridayDidCard(" in text, f"{rel}: no receipt card"
    card = text.split("function WhatFridayDidCard(")[1].split("\nfunction ")[0]
    assert "/api/governance/receipts" in card
    assert "signature verified" in card and "not verified" in card
    assert "/api/tasks/" in card, f"{rel}: a receipt does not link to its task"
    assert "Since last visit" in card
    # Read-only: the card never POSTs, PUTs or DELETEs.
    assert not re.search(r"method\s*:\s*['\"](POST|PUT|DELETE)", card)
    system = text.split("function SystemWS(")[1][:40000]
    assert "WhatFridayDidCard" in system, f"{rel}: the card is not rendered in System"
