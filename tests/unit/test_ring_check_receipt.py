"""The privilege-ring check writes through the governance checkpoint's receipt.

There is one signed receipt file, ~/.friday/decision-bom.jsonl, written by
governance.action_gate._receipt, which signs every entry or raises. The ring
check used to keep its own file and append an entry unsigned when signing
failed; it now goes through _receipt, and when the receipt cannot be written
a ring-2+ call it would have allowed is held instead.
"""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest

import agent_friday.services.agent as agent
from agent_friday.governance import action_gate

KEY = b"k" * 32


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(action_gate, "friday_home", lambda: str(tmp_path))
    monkeypatch.setattr(action_gate, "_governance_key", lambda: KEY)
    return tmp_path


def _rows(home):
    p = home / "decision-bom.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def test_ring_check_entry_is_signed_in_the_one_receipt_file(home):
    allowed, _ = agent._governance_check("search_news", {"q": "x"}, {"authenticated": True})
    assert allowed
    rows = [r for r in _rows(home) if r.get("kind") == "ring_check"]
    assert len(rows) == 1
    row = dict(rows[0])
    mac = row.pop("hmac")
    canonical = json.dumps(row, sort_keys=True, default=str).encode("utf-8")
    assert hmac.compare_digest(mac, hmac.new(KEY, canonical, hashlib.sha256).hexdigest())
    assert row["tool"] == "search_news" and row["ring"] == 2 and row["decision"] == "allow"


def test_a_receipt_failure_holds_a_ring2_call_and_writes_nothing_unsigned(home, monkeypatch):
    def boom(entry):
        raise RuntimeError("governance key unavailable")
    monkeypatch.setattr(action_gate, "_receipt", boom)
    legacy = agent.core.FRIDAY_DIR / "vault" / "decision-bom.jsonl"
    before = legacy.read_text(encoding="utf-8") if legacy.exists() else ""

    allowed, reason = agent._governance_check("search_news", {}, {"authenticated": True})

    assert not allowed and "receipt" in reason
    assert _rows(home) == []
    after = legacy.read_text(encoding="utf-8") if legacy.exists() else ""
    assert after == before, "no unsigned entry is written anywhere"


def test_a_receipt_failure_does_not_stop_a_read(home, monkeypatch):
    monkeypatch.setattr(action_gate, "_receipt",
                        lambda e: (_ for _ in ()).throw(OSError("disk full")))
    allowed, _ = agent._governance_check("read_file", {"path": "x"}, {})
    assert allowed
