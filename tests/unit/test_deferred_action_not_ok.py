"""A deferred action is not a success, and a failure says why.

Two defects from the itinerary investigation, 2026-09-25:

**It reported success.** `write_file` was refused by the taint gate, which raised
an approval card; the ledger recorded `ok: true` twice, 824 ms and 151 ms, while
the owner's decision was still 446 s and 40 s away. The cause is one line:
`_TOOL_DENY_SENTINELS` did not list `[APPROVAL CARD RAISED]`, and
`_tool_call_status` classifies anything it does not recognise as `ok` -- the
exact trap the comment above that tuple warns about. So a call that never ran
was indistinguishable in the record from one that did.

**And a failure never said why.** Twenty-four calls failed in that conversation
and every one had an empty reason, because `tool_call` did not whitelist a field
a reason could go in. Diagnosing it needed the chat transcript; the machine's own
record could not explain itself.

The reason is a CLASSIFICATION, never the result text. The ledger is
metadata-only on purpose -- it is not encrypted and `/api/processes` is
world-readable on the box -- so a tool result that quotes a home address must not
end up in it.
"""

import io
import json

import pytest


# ── a deferred action is not ok ────────────────────────────────────────────

@pytest.mark.parametrize("prefix,expected", [
    ("[APPROVAL CARD RAISED] 'write_file' was NOT executed.", "pending"),
    ("[BLOCKED — FROM OUTSIDE CONTENT] 'x' was NOT executed.", "deny"),
    ("[GOVERNANCE HOLD] 'x' was NOT executed.", "deny"),
    ("[DECLINED] The user declined 'x'.", "deny"),
    ("[NOT RUN] 'x' uses details that came from outside content.", "deny"),
    ("[GOVERNANCE DENY] nope", "deny"),
    ("[CONFIRMATION REQUIRED] ask first", "deny"),
    ("Tool error (boom)", "error"),
    ("TOOL CALL FAILED", "error"),
    ("created: ok", "ok"),
])
def test_status_classification(prefix, expected):
    from agent_friday.services.agent import _tool_call_status
    assert _tool_call_status(prefix) == expected, prefix


def test_a_raised_card_is_never_recorded_as_ok(tmp_path, monkeypatch):
    """The reported defect, at the exact line that caused it."""
    from agent_friday.services import activity_ledger as al
    from agent_friday.services import agent as ag
    monkeypatch.setattr(al, "LEDGER_FILE", tmp_path / "ledger.jsonl")

    ag._ledger_tool_call(
        "write_file",
        "[APPROVAL CARD RAISED] 'write_file' was NOT executed. It needs the "
        "user's decision on an approval card.",
        824, None, None)

    rows = [json.loads(l) for l in
            io.open(tmp_path / "ledger.jsonl", encoding="utf-8") if l.strip()]
    assert rows, "nothing was recorded"
    r = rows[-1]
    assert r["ok"] is False, "a call that never ran was recorded as a success"
    assert r.get("status") == "pending", r
    assert r.get("reason"), "no reason recorded"


# ── every failure records a reason ─────────────────────────────────────────

@pytest.mark.parametrize("result", [
    "[APPROVAL CARD RAISED] x",
    "[GOVERNANCE DENY] x",
    "[VAULT-ZT DENY] x",
    "[SANDBOX DENY] x",
    "Tool error (boom)",
    "TOOL CALL FAILED",
])
def test_every_non_ok_call_records_a_reason(result, tmp_path, monkeypatch):
    from agent_friday.services import activity_ledger as al
    from agent_friday.services import agent as ag
    monkeypatch.setattr(al, "LEDGER_FILE", tmp_path / "ledger.jsonl")
    ag._ledger_tool_call("some_tool", result, 5, None, None)
    rows = [json.loads(l) for l in
            io.open(tmp_path / "ledger.jsonl", encoding="utf-8") if l.strip()]
    r = rows[-1]
    assert r["ok"] is False, result
    assert r.get("reason"), "ok=false with no reason: %r" % (r,)


def test_a_successful_call_needs_no_reason(tmp_path, monkeypatch):
    from agent_friday.services import activity_ledger as al
    from agent_friday.services import agent as ag
    monkeypatch.setattr(al, "LEDGER_FILE", tmp_path / "ledger.jsonl")
    ag._ledger_tool_call("some_tool", "created: ok", 5, None, None)
    rows = [json.loads(l) for l in
            io.open(tmp_path / "ledger.jsonl", encoding="utf-8") if l.strip()]
    r = rows[-1]
    assert r["ok"] is True
    assert r.get("status") in (None, "ok")


# ── the reason must not carry the content ──────────────────────────────────

def test_the_reason_never_quotes_the_result_body(tmp_path, monkeypatch):
    """The ledger is metadata-only and unencrypted. A reason that echoed the
    tool's output would put whatever the tool touched into a plaintext file."""
    from agent_friday.services import activity_ledger as al
    from agent_friday.services import agent as ag
    monkeypatch.setattr(al, "LEDGER_FILE", tmp_path / "ledger.jsonl")
    secret = "1847 Nonexistent Parkway, Apartment 4C"
    ag._ledger_tool_call(
        "create_calendar_event",
        "[VAULT-ZT DENY] refused: the location %s is TIER_2" % secret,
        7, None, None)
    text = io.open(tmp_path / "ledger.jsonl", encoding="utf-8").read()
    assert secret not in text, (
        "the tool result's content reached the plaintext ledger")
    assert "Nonexistent" not in text
    rows = [json.loads(l) for l in text.splitlines() if l.strip()]
    assert rows[-1].get("reason"), "a reason is still required"


def test_the_whitelist_actually_carries_the_new_fields():
    """A field the ledger does not whitelist is dropped silently -- which is
    why the reason was empty for all 24 failures in the first place."""
    from agent_friday.services.activity_ledger import _ALLOWED_FIELDS
    allowed = _ALLOWED_FIELDS["tool_call"]
    assert "reason" in allowed and "status" in allowed, allowed
