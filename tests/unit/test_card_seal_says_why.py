"""A card whose seal cannot be checked is still refused, and the log now says why (F2/F9).

Two Windows CI flakes (test_reflex_chat and test_confirmation_resume) fail closed with only "not the card that was
raised": `approvals._holds` swallowed the exception that made the seal unverifiable, so nobody could tell a
transient key-read failure from a real forgery. The refusal is unchanged; the reason is logged, once per distinct
error so a missing key does not flood the log.
"""
import logging

import pytest

from agent_friday.services import approvals


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(approvals, "_SEAL_WARNED", set(), raising=False)


def _broken_key(monkeypatch, exc):
    def boom():
        raise exc
    monkeypatch.setattr(approvals, "_seal_key", boom)


def test_an_unreadable_key_still_refuses_the_card_and_says_why(monkeypatch, caplog):
    _broken_key(monkeypatch, PermissionError(13, "The process cannot access the file"))
    with caplog.at_level(logging.WARNING, logger="friday.approvals"):
        assert approvals.card_seal_holds({"card_seal": "abc", "kind": "tool_confirm"}) is False
    text = caplog.text
    assert "PermissionError" in text and "cannot access the file" in text


def test_the_same_failure_is_logged_once_not_every_time(monkeypatch, caplog):
    _broken_key(monkeypatch, PermissionError(13, "locked"))
    with caplog.at_level(logging.WARNING, logger="friday.approvals"):
        for _ in range(5):
            approvals.card_seal_holds({"card_seal": "abc"})
    assert caplog.text.count("PermissionError") == 1


def test_a_card_with_no_seal_is_refused_without_a_warning(monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger="friday.approvals"):
        assert approvals.card_seal_holds({"kind": "tool_confirm"}) is False
    assert caplog.text == ""
