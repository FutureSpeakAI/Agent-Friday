"""Input-validation security tests — SQL injection attempts against SQLite-backed
services, path traversal on file-scoped inputs, oversized payloads, and hostile
strings. The services use parameterized queries throughout, so injection strings
must be stored/handled as literal DATA, never executed.
"""
from __future__ import annotations

import pytest

from agent_friday.services import economy as econ
from agent_friday.services import user_model as um
from agent_friday.services import memory_dreaming as md
from agent_friday.services import soul

econ._ensure_schema()


_SQLI = [
    "'; DROP TABLE wallets;--",
    "1' OR '1'='1",
    "robert'); DROP TABLE facts;--",
    "\" OR 1=1 --",
    "'; DELETE FROM traits WHERE 1=1;--",
]


class TestSQLInjectionEconomy:
    @pytest.mark.parametrize("payload", _SQLI)
    def test_agent_id_injection_is_literal(self, payload):
        # The malicious string is treated as a literal agent_id — the wallets
        # table must still exist and the balance op succeeds against that key.
        econ.earn(payload, 1000, "test")
        wallet = econ.get_wallet(payload)
        assert wallet["agent_id"] == payload
        # Prove the table wasn't dropped: a normal agent still works.
        assert econ.get_wallet("sql-canary-agent")["agent_id"] == "sql-canary-agent"

    @pytest.mark.parametrize("payload", _SQLI)
    def test_reason_field_injection_literal(self, payload):
        tx = econ.earn("sqli-reason-agent", 500, payload)
        assert tx is not None
        assert tx["reason"] == payload


class TestSQLInjectionUserModel:
    @pytest.mark.parametrize("payload", _SQLI)
    def test_trait_key_injection_literal(self, payload):
        um.set_trait(payload, "value", confidence=0.5)
        # Table survives — a normal fact still stores.
        res = um.note_fact("preference", "canary fact after injection")
        assert res["ok"] is True

    @pytest.mark.parametrize("payload", _SQLI)
    def test_fact_text_injection_literal(self, payload):
        res = um.note_fact("preference", payload)
        assert res["ok"] is True
        facts = um._recent_facts(50)
        assert any(f["text"] == payload for f in facts)


class TestPathTraversalDreaming:
    @pytest.mark.parametrize("bad_day", [
        "../../etc/passwd",
        "..\\..\\windows\\system32",
        "2026-01-01/../../evil",
        "'; rm -rf /",
        "not-a-date",
        "2026-1-1",            # wrong zero-padding (regex requires 2 digits)
    ])
    def test_traversal_day_rejected(self, bad_day):
        res = md.dream(day=bad_day)
        # Must reject with an error, never write a file outside dreams/. The
        # module guards the FORMAT (strict YYYY-MM-DD) — that is what blocks
        # traversal. It intentionally does not validate calendar plausibility.
        assert res.get("ok") is False
        assert "invalid day" in res.get("error", "").lower()

    def test_valid_day_accepted(self):
        res = md.dream(day="2026-06-30", memory=None)
        # memory=None → empty envelope, but the date passed validation (ok True).
        assert res.get("ok") is True


class TestOversizedPayloads:
    def test_huge_fact_text_is_bounded(self):
        big = "x" * 1_000_000
        res = um.note_fact("preference", big)
        assert res["ok"] is True
        # Stored text is capped (500 chars) — no unbounded row growth.
        stored = [f for f in um._recent_facts(50) if f["text"].startswith("x")]
        assert stored and len(stored[0]["text"]) <= 500

    def test_huge_message_scan_bounded(self):
        # observe_message caps the scan window; a megabyte paste must not error.
        res = um.observe_message("please " * 200000)
        assert res.get("ok") is True

    def test_soul_oversized_rejected(self):
        res = soul.save_soul("#" * (33 * 1024))  # over the 32 KiB cap
        assert res["ok"] is False
        assert "too large" in res["error"].lower()


class TestHostileStrings:
    # Explicit short ids keep the pytest test-id small: the raw values would be
    # written to PYTEST_CURRENT_TEST (Windows env vars cap at 32767 chars).
    @pytest.mark.parametrize("s", [
        "\x00\x01\x02null bytes",
        "😀🔥 emoji flood " * 100,
        "‮RTL override text",
        "z̸̢͈a̷l̴g̶o̸ text",
        "%00%2e%2e%2f encoded traversal",
    ], ids=["nullbytes", "emoji", "rtl", "zalgo", "encoded"])
    def test_hostile_trait_values_stored_safely(self, s):
        res = um.set_trait("hostile.value", s)
        assert res["ok"] is True

    @pytest.mark.parametrize("s", ["\x00\x01", "🔥" * 50, "‮evil"],
                             ids=["nullbytes", "emoji", "rtl"])
    def test_hostile_economy_reason(self, s):
        tx = econ.spend("hostile-agent", 100, s)
        assert tx is not None
