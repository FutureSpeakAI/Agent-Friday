"""The reason a pre-approval does not cover a send never quotes the message (follow-up to the #868 critic).

use_refusal() fails closed: when the sensitivity check raises, the reason names that it could not run. The reason is
written to the ledger and shown to the owner, so it must say what kind of error it was (the exception's type), never the
exception's text: that text can quote the subject or body being screened. What each check bites on:
  1. an exception whose message quotes the body: the reason holds none of it, names the type, and still refuses;
  2. the check can fail: a reason built from the exception's text would hold the canary (the old format, below).
"""
from agent_friday.governance import send_grants

CANARY = "CANARY-5e1b the quarterly numbers are attached"


def _refusal(monkeypatch, exc):
    def boom(args):
        raise exc
    monkeypatch.setattr(send_grants, "content_tier", boom)
    return send_grants.use_refusal("gmail:send", {"subject": "s", "body": CANARY}, ctx={})


def test_a_failed_sensitivity_check_refuses_without_quoting_the_message(monkeypatch):
    why = _refusal(monkeypatch, RuntimeError("could not classify: " + CANARY))
    assert why and "CANARY-5e1b" not in why and "quarterly" not in why
    assert "RuntimeError" in why and "does not cover it" in why, "it still refuses, and says what kind of error"


def test_the_check_can_fail_the_old_format_quoted_the_exception_text():
    exc = RuntimeError("could not classify: " + CANARY)
    assert CANARY in ("the sensitivity check could not run (%s)" % exc)
