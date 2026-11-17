"""A task's process row links the receipt its tool call wrote (BD-16 / NS-29.2-1 "receipt link").
Synthetic receipts and a synthetic process only; nothing runs."""
from __future__ import annotations

import pytest

from agent_friday import core
from agent_friday.services import action_journal as aj
from agent_friday.services import agent


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    monkeypatch.setattr(aj, "_MEM", {})
    monkeypatch.setattr(aj, "_LOADED", {"done": True})
    monkeypatch.setattr(aj, "_append", lambda rec: None)
    core.process_register("p-rcpt", name="Work")
    yield
    with core.PROCESSES_LOCK:
        core.PROCESSES.pop("p-rcpt", None)


def _row():
    with core.PROCESSES_LOCK:
        return dict(core.PROCESSES["p-rcpt"])


def _receipt():
    return aj.record("write_file", "write", "files", items=[], summary="wrote x", status="done")["receipt_id"]


def test_a_call_that_wrote_a_receipt_links_it_on_the_row():
    rid = _receipt()
    agent._orb_tool_trace("p-rcpt", "write_file", {"path": "x"}, "Wrote 1 chars (receipt_id %s)." % rid, 12)
    assert _row()["receipt_id"] == rid


def test_an_id_the_journal_never_wrote_is_not_linked():
    agent._orb_tool_trace("p-rcpt", "read_file", {"path": "x"}, "the page says rcpt_0123456789abcdef012", 12)
    assert _row()["receipt_id"] is None


def test_a_refused_call_links_nothing_even_if_it_quotes_a_real_receipt():
    rid = _receipt()
    agent._orb_tool_trace("p-rcpt", "write_file", {"path": "x"}, "[GOVERNANCE HOLD] not now (see %s)" % rid, 12)
    assert _row()["receipt_id"] is None


def test_the_latest_receipt_stays_when_a_later_call_names_none():
    rid = _receipt()
    agent._orb_tool_trace("p-rcpt", "write_file", {"path": "x"}, "Wrote (receipt_id %s)." % rid, 12)
    agent._orb_tool_trace("p-rcpt", "read_file", {"path": "x"}, "contents", 12)
    assert _row()["receipt_id"] == rid
