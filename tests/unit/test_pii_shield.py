"""PII Privacy Shield — scrub/redact correctness.

Pins the strengthened behavior:
  * credit cards must pass Luhn (random 13-19 digit runs are NOT cards)
  * international +country numbers are caught, version strings are not
  * watchlist tokens match on word boundaries (no SmithKline corruption)
  * scrub -> rehydrate round-trips
"""
from __future__ import annotations

import json

import agent_friday.core as core


def _reset_watchlist_cache():
    core._PII_WATCHLIST_CACHE["mtime"] = 0.0
    core._PII_WATCHLIST_CACHE["items"] = []


def _write_watchlist(tokens):
    path = core.FRIDAY_DIR / "privacy_shield.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"watchlist": tokens}), encoding="utf-8")
    _reset_watchlist_cache()


# ── credit cards: Luhn-validated ─────────────────────────────────────────────

def test_valid_card_is_scrubbed():
    text = "charge it to 4111 1111 1111 1111 please"   # Visa test number, Luhn-valid
    out, lookup = core._scrub_pii(text)
    assert "4111" not in out
    assert any(t.startswith("[PII:cc:") for t in lookup)


def test_luhn_invalid_digit_run_is_left_alone():
    text = "tracking number 1234 5678 9012 3456 arrived"  # fails Luhn
    out, lookup = core._scrub_pii(text)
    assert "1234 5678 9012 3456" in out
    assert not any(t.startswith("[PII:cc:") for t in lookup)


def test_pii_redact_applies_luhn_too():
    assert "[REDACTED-CC]" in core._pii_redact("card 4111-1111-1111-1111 ok")
    assert "[REDACTED-CC]" not in core._pii_redact("id 1234 5678 9012 3456 ok")


# ── phones: NANP + international ─────────────────────────────────────────────

def test_us_phone_still_scrubbed():
    out, lookup = core._scrub_pii("call me at (512) 555-0182 tonight")
    assert "555-0182" not in out
    assert any(t.startswith("[PII:phone:") for t in lookup)


def test_international_phone_scrubbed():
    for num in ("+44 20 7946 0958", "+91 98765 43210", "+33 1 42 68 53 00"):
        out, lookup = core._scrub_pii(f"reach him on {num} after lunch")
        assert num not in out, f"intl number leaked: {num}"
        assert any(t.startswith("[PII:phone:") for t in lookup)


def test_version_strings_are_not_phones():
    for s in ("upgrade to v+2.10.3 now", "C++17 and +5 boost", "+1 vote"):
        out, _ = core._scrub_pii(s)
        assert out == s, f"false positive on: {s}"


# ── watchlist: word-boundary matching ────────────────────────────────────────

def test_watchlist_word_boundary(tmp_path):
    _write_watchlist(["Smith"])
    try:
        out, lookup = core._scrub_pii("Mr. Smith called about SmithKline stock")
        assert "SmithKline" in out, "substring corruption — boundary not respected"
        assert "Mr. [PII:name:" in out or "Smith called" not in out
        assert any(t.startswith("[PII:name:") for t in lookup)

        red = core._pii_redact("Mr. Smith called about SmithKline stock")
        assert "SmithKline" in red
        assert "Smith called" not in red
    finally:
        (core.FRIDAY_DIR / "privacy_shield.json").unlink(missing_ok=True)
        _reset_watchlist_cache()


def test_watchlist_non_word_token_still_matches(tmp_path):
    _write_watchlist(["ACCT-99-1234"])
    try:
        out, lookup = core._scrub_pii("wire from ACCT-99-1234 cleared")
        assert "ACCT-99-1234" not in out
        assert lookup
    finally:
        (core.FRIDAY_DIR / "privacy_shield.json").unlink(missing_ok=True)
        _reset_watchlist_cache()


# ── round-trip ───────────────────────────────────────────────────────────────

def test_scrub_rehydrate_round_trip():
    text = ("SSN 123-45-6789, card 4111 1111 1111 1111, call +44 20 7946 0958 "  # pragma: allowlist secret
            "or (512) 555-0182, mail kim@example.org")
    scrubbed, lookup = core._scrub_pii(text)
    assert "123-45-6789" not in scrubbed  # pragma: allowlist secret
    assert "4111" not in scrubbed
    assert "kim@example.org" not in scrubbed
    assert core._rehydrate_pii(scrubbed, lookup) == text
