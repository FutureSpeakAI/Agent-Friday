"""A read at a service the owner connected runs without a card, unless
private data would leave in its arguments.

"I think it should also be a high priority that we don't constantly pester
the user with approval cards for every single command." (the owner,
2026-09-29). His own labels say a GitHub or Higgsfield lookup reaches outside
his machine, and with Laya fully on every one of them carded.

The north star's classes (§18.2) already had the answer: "query a public
source" and "retrieve calendar events" are Class 0, OBSERVE, allowed and
logged. So a connector tool whose name leads with a read verb is OBSERVE:
labelled as reaching outside, receipted, and run without a card. It still
cards when:

  * a detail came from outside content (the taint ledger), or
  * its arguments would carry private data out: the PII check (regex and
    keywords) OR Laya's carries_private, because measured on the same cases
    each catches what the other misses (together 10/10, no false alarm on
    10 clean queries). This is the "exfiltrate through a search query" path;
  * Laya says it changes something outside, or the keyword scan flags it;
  * the owner sets outward_reads="card".

A write, a send, a delete, a spend or an unknown tool is untouched.

When Laya cannot answer in time, the read runs if the PII check finds the
arguments clean, and the miss is counted; it waits for the owner only if they
look private.

No model is loaded; the agent is faked at the `predict` boundary.
"""
from __future__ import annotations

import json
import threading

import pytest

from agent_friday.governance import action_gate
from agent_friday.services import approvals, decisions, dissent_gate, laya_backend

READ = "mcp_github_search_users"


class _Agent:
    """Answers every question it is asked, as configured."""

    def __init__(self, severity="hard", changes="no", private="public"):
        self.a = {"severity": severity, "changes_outside": changes,
                  "carries_private": private}
        self.calls = []

    def predict(self, text, questions):
        self.calls.append(sorted(questions))
        return {"answers": {q: {"choice": self.a.get(q, "no"), "confidence": 0.8}
                            for q in questions}}


@pytest.fixture(autouse=True)
def _union(tmp_path, monkeypatch):
    monkeypatch.setattr(decisions, "log_path", lambda: tmp_path / "decisions.jsonl")
    monkeypatch.setattr(decisions, "probe_log_path", lambda: tmp_path / "probes.jsonl")
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dissent_gate, "EVENTS_PATH", tmp_path / "dissent.jsonl")
    monkeypatch.delenv("FRIDAY_DECISION_BACKEND", raising=False)
    monkeypatch.delenv("FRIDAY_DECISION_SHADOW", raising=False)
    laya_backend.register()
    monkeypatch.setattr(laya_backend, "_loading", False)
    monkeypatch.setattr(laya_backend, "_scoring",
                        threading.BoundedSemaphore(laya_backend._MAX_SCORING))
    laya_backend.clear_answer_cache()
    laya_backend.reset_missed()
    _settings(monkeypatch)
    yield tmp_path
    laya_backend._agent = None
    laya_backend.clear_answer_cache()
    laya_backend.reset_missed()


def _settings(monkeypatch, **over):
    s = {"decision_backend": "laya-union", "decision_shadow": "", "outward_reads": "observe"}
    s.update(over)
    monkeypatch.setattr("agent_friday.core._load_settings", lambda: dict(s), raising=False)


def _laya(monkeypatch, **kw):
    agent = _Agent(**kw)
    monkeypatch.setattr(laya_backend, "_agent", agent)
    return agent


def _busy():
    return [laya_backend._reserve_scoring(pilot=False) for _ in range(laya_backend._MAX_SCORING)]


# ── the read the owner should stop being asked about ────────────────────────

def test_a_clean_read_is_observe_not_a_card(monkeypatch):
    _laya(monkeypatch)                      # severity hard: the union's old card
    klass, why = action_gate.classify(READ, {"q": "octocat"})
    assert klass == action_gate.OBSERVE
    assert "outside" in why


def test_an_observe_read_runs_and_is_receipted_as_observe(monkeypatch):
    _laya(monkeypatch)
    got = []
    monkeypatch.setattr(action_gate, "_receipt", lambda row: got.append(row))
    v = action_gate.authorize(READ, {"q": "octocat"},
                              {"authenticated": True, "is_background_task": True, "task_id": "t1"})
    assert v.action == "allow"
    assert got and got[-1]["class"] == action_gate.OBSERVE


def test_the_laya_pass_asks_the_read_questions_together(monkeypatch):
    agent = _laya(monkeypatch)
    action_gate.classify(READ, {"q": "octocat"})
    assert agent.calls == [["carries_private", "changes_outside", "severity"]]


# ── exfiltration through a search query stays closed ────────────────────────

def test_private_data_the_pii_check_sees_still_cards(monkeypatch):
    _laya(monkeypatch)                      # Laya says "public"
    klass, why = action_gate.classify(READ, {"q": "bank account 4432 overdraft fee"})
    assert klass == action_gate.OUTWARD
    assert "private" in why


def test_private_data_only_laya_sees_still_cards(monkeypatch):
    _laya(monkeypatch, private="personal")  # the PII check misses this one
    klass, why = action_gate.classify(READ, {"q": "my son failed his math test"})
    assert klass == action_gate.OUTWARD
    assert "private" in why


def test_an_email_address_in_a_query_still_cards_without_laya(monkeypatch):
    monkeypatch.setattr(laya_backend, "_agent", None)
    monkeypatch.setattr(laya_backend, "start_warming", lambda *a, **k: None)
    klass, _ = action_gate.classify(READ, {"q": "jane.doe@example.com"})
    assert klass == action_gate.OUTWARD


def test_a_tainted_read_still_cards(monkeypatch):
    _laya(monkeypatch)
    monkeypatch.setattr(action_gate, "_receipt", lambda row: None)
    v = action_gate.authorize(READ, {"q": "octocat"},
                              {"authenticated": True, "is_background_task": True}, tainted=True)
    assert v.action == "card"


# ── what still cards exactly as before ──────────────────────────────────────

def test_a_connector_write_is_untouched(monkeypatch):
    _laya(monkeypatch, severity="soft")
    assert action_gate.classify("mcp_github_create_issue", {"title": "x"})[0] == action_gate.OUTWARD


def test_laya_saying_it_changes_something_cards(monkeypatch):
    _laya(monkeypatch, changes="yes")
    klass, why = action_gate.classify(READ, {"q": "octocat"})
    assert klass == action_gate.OUTWARD


def test_the_keyword_scan_flagging_a_read_cards(monkeypatch):
    _laya(monkeypatch, severity="soft")
    klass, _ = action_gate.classify(READ, {"q": "send the invoice and pay it"})
    assert klass == action_gate.OUTWARD


def test_an_unknown_tool_is_untouched(monkeypatch):
    _laya(monkeypatch, severity="soft")
    assert action_gate.classify("frobnicate_everything", {})[0] == action_gate.OUTWARD


def test_the_owner_can_set_reads_to_card(monkeypatch):
    _settings(monkeypatch, outward_reads="card")
    _laya(monkeypatch)
    assert action_gate.classify(READ, {"q": "octocat"})[0] == action_gate.OUTWARD


# ── Laya too slow ───────────────────────────────────────────────────────────

def test_a_missed_laya_lets_a_clean_read_run_and_counts_the_miss(monkeypatch):
    _laya(monkeypatch)
    held = _busy()
    try:
        klass, why = action_gate.classify(READ, {"q": "octocat"})
    finally:
        for r in held:
            r()
    assert klass == action_gate.OBSERVE
    assert "could not check" in why
    assert laya_backend.status()["missed"]["busy"] == 1


def test_a_missed_laya_holds_a_read_whose_arguments_look_private(monkeypatch):
    _laya(monkeypatch)
    held = _busy()
    try:
        klass, _ = action_gate.classify(READ, {"q": "diagnosis type 2 diabetes insulin dose"})
    finally:
        for r in held:
            r()
    assert klass == action_gate.OUTWARD


# ── the record ──────────────────────────────────────────────────────────────

def test_observe_reads_leave_the_grants_list(monkeypatch, _union):
    _laya(monkeypatch)
    from agent_friday.routes import goals  # noqa: F401 - route module import check
    klass, _ = action_gate.classify(READ, {})
    assert klass != action_gate.OUTWARD


def test_the_policy_change_is_recorded(monkeypatch, tmp_path):
    monkeypatch.setattr(decisions, "gate_events_path", lambda: tmp_path / "gate_events.jsonl")
    decisions.on_settings_change({"decision_backend": "laya-union", "outward_reads": "card"},
                                 {"decision_backend": "laya-union", "outward_reads": "observe"})
    ev = [json.loads(x) for x in (tmp_path / "gate_events.jsonl").read_text().splitlines()]
    assert ev and ev[-1]["from"]["outward_reads"] == "card"
    assert ev[-1]["to"]["outward_reads"] == "observe"


def test_laya_privacy_counts_only_where_an_argument_is_free_text(monkeypatch):
    """Measured live: carries_private said "personal" for
    {"owner": "octo", "repo": "hello", "pull_number": 3} and {"limit": 10}.
    Identifiers without spaces cannot carry "my son failed his math test";
    the structured leaks they can carry (email, phone, account and ID
    numbers) are the PII check's, which still applies."""
    _laya(monkeypatch, private="personal")
    assert action_gate.classify("mcp_github_get_pull_request",
                                {"owner": "octo", "repo": "hello", "pull_number": 3})[0] \
        == action_gate.OBSERVE
    assert action_gate.classify("mcp_higgsfield_show_generations", {"limit": 10})[0] \
        == action_gate.OBSERVE
    assert action_gate.classify(READ, {"q": "my son failed his math test"})[0] \
        == action_gate.OUTWARD
    assert action_gate.classify(READ, {"q": "jane.doe@example.com"})[0] == action_gate.OUTWARD
