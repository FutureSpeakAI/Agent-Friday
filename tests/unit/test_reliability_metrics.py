"""Connector and session reliability, measured on this PC (NS-33.6-1, BD-32).

Outcome receipts are real signed action-ledger entries in a scratch home;
the session log is the real file. Nothing is sent anywhere.
"""
from __future__ import annotations

import json
import sys
import time

import pytest

from agent_friday.governance import proof_of_integrity as poi
from agent_friday.services import action_ledger as L


@pytest.fixture(autouse=True)
def scratch_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(poi, "_GOV_KEY_FILE", tmp_path / "gk")
    monkeypatch.setitem(sys.modules, "keyring", None)
    L.reset_caches()
    from agent_friday.services import receipt_audit
    monkeypatch.setattr(receipt_audit, "audit", lambda **kw: {"open_decisions": []})
    try:
        from agent_friday.services import session_log
        monkeypatch.setattr(session_log, "path", lambda: tmp_path / "sessions.jsonl")
    except ImportError:
        pass
    yield
    L.reset_caches()


def _rm():
    from agent_friday.services import reliability_metrics
    return reliability_metrics


def _out(tool, status="ok", detail="", n=1):
    for _ in range(n):
        L.append("outcome", {"tool": tool, "status": status, "detail": detail,
                             "decision_seq": None, "decision_hash": None})


def _conn(m, cid):
    return next(r for r in m["connectors"] if r["id"] == cid)


class TestConnectorSuccess:
    def test_a_hundred_good_changes_meet_the_bar(self):
        _out("create_task", n=100)
        m = _rm().connector_success()
        r = _conn(m, "google:tasks")
        assert (r["attempts"], r["succeeded"], r["rate_pct"], r["state"]) == (100, 100, 100.0, "ok")
        assert m["state"] == "ok" and m["target_pct"] == 99.0

    def test_one_failure_in_a_hundred_is_still_the_bar(self):
        _out("create_task", n=99)
        _out("create_task", "error", "not done: refused")
        r = _conn(_rm().connector_success(), "google:tasks")
        assert r["rate_pct"] == 99.0 and r["state"] == "ok" and r["failed"] == 1

    def test_three_failures_in_a_hundred_need_attention(self):
        _out("create_calendar_event", n=97)
        _out("create_calendar_event", "error", "HttpError", n=3)
        r = _conn(_rm().connector_success(), "google:calendar")
        assert r["rate_pct"] == 97.0 and r["state"] == "attention"

    def test_too_few_changes_never_read_as_ok(self):
        _out("create_task", n=30)
        r = _conn(_rm().connector_success(), "google:tasks")
        assert r["state"] == "not_enough" and r["rate_pct"] == 100.0
        _out("save_google_contact", n=5)
        c = _conn(_rm().connector_success(), "google:contacts")
        assert c["state"] == "not_enough" and c["rate_pct"] is None

    def test_a_service_that_could_not_take_the_change_is_counted_apart(self):
        _out("create_task", n=100)
        _out("create_task", "error", "not done: unreachable", n=4)
        _out("create_task", "error", "not done: busy")
        _out("create_task", "error", "not done: revoked", n=2)
        r = _conn(_rm().connector_success(), "google:tasks")
        assert r["attempts"] == 100 and r["state"] == "ok"
        assert r["provider_unavailable"] == 5 and r["needs_owner"] == 2

    def test_held_and_refused_are_not_attempts(self):
        _out("create_task", "held", n=3)
        _out("create_task", "refused", n=2)
        r = _conn(_rm().connector_success(), "google:tasks")
        assert r["attempts"] == 0

    def test_a_change_that_never_reported_back_is_a_failure(self, monkeypatch):
        from agent_friday.services import receipt_audit
        monkeypatch.setattr(receipt_audit, "audit",
                            lambda **kw: {"open_decisions": [{"seq": 7, "tool": "create_task"}]})
        _out("create_task", n=99)
        r = _conn(_rm().connector_success(), "google:tasks")
        assert r["attempts"] == 100 and r["failed"] == 1 and r["never_reported"] == 1

    def test_reads_are_reported_apart(self):
        _out("list_tasks", n=4)
        _out("list_tasks", "error", "HttpError")
        r = _conn(_rm().connector_success(), "google:tasks")
        assert r["attempts"] == 0 and r["reads"] == 5 and r["reads_failed"] == 1

    def test_other_connected_tools_are_measured_not_held_to_the_bar(self):
        _out("mcp_github_create_issue", n=3)
        r = _conn(_rm().connector_success(), "mcp:github")
        assert r["supported"] is False and r["target_pct"] is None and r["state"] == "measured"

    def test_tools_that_are_not_connectors_are_not_counted(self):
        _out("write_file", n=10)
        assert _rm().connector_success()["connectors"] == []

    def test_an_untrusted_ledger_is_unknown_not_a_number(self, monkeypatch):
        _out("create_task", n=100)
        monkeypatch.setattr(L, "summary", lambda **kw: {"key_available": True, "intact": False, "ok": False})
        m = _rm().connector_success()
        assert m["state"] == "unknown" and m["connectors"] == [] and m["why"]

    def test_an_acknowledged_break_still_counts(self, monkeypatch):
        _out("create_task", n=100)
        monkeypatch.setattr(L, "summary", lambda **kw: {"key_available": True, "intact": False, "ok": True})
        assert _rm().connector_success()["state"] == "ok"

    def test_a_draft_that_may_send_is_measured_not_held_to_the_bar(self):
        _out("draft_email", n=3)
        r = _conn(_rm().connector_success(), "google:gmail:by-argument")
        assert r["supported"] is False and r["state"] == "measured"

    def test_the_report_never_names_another_connected_tool(self):
        _out("mcp_wilhelmina-private-notes_read", n=2)
        _out("create_task", n=3)
        rm = _rm()
        rep = rm.for_report(rm.metrics())
        blob = json.dumps(rep) + "\n".join(rm.render_lines(rep))
        assert "wilhelmina" not in blob
        assert any(r["id"] == "mcp" and r["attempts"] == 2 for r in rep["connectors"]["connectors"])

    def test_the_window_is_thirty_days(self):
        _out("create_task", n=100)
        later = time.time() + 31 * 86400.0
        m = _rm().connector_success(now=later)
        assert m["connectors"] == []


class TestCrashFree:
    def _start(self, previous, t):
        from agent_friday.services import session_log
        return session_log.note_start(previous, now=t)

    def test_the_first_start_on_a_pc_is_not_a_crash(self):
        row = self._start("unexpected", time.time())
        assert row["previous"] == "none"

    def test_closed_and_unexpected_ends_make_the_rate(self):
        now = time.time()
        self._start("unexpected", now - 100)            # first: none
        for i in range(8):
            self._start("closed", now - 90 + i)
        for i in range(2):
            self._start("unexpected", now - 50 + i)
        s = _rm().crash_free(now=now)
        assert (s["ended"], s["crashed"], s["rate_pct"], s["state"]) == (10, 2, 80.0, "measured")

    def test_too_few_ended_runs_say_so(self):
        now = time.time()
        self._start("closed", now - 10)
        self._start("closed", now - 5)
        s = _rm().crash_free(now=now)
        assert s["state"] == "not_enough" and s["rate_pct"] is None

    def test_the_journal_records_each_start(self, tmp_path, monkeypatch):
        from agent_friday.services import session_log, task_journal
        monkeypatch.setattr(task_journal, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
        task_journal.reconcile_on_boot()
        task_journal.mark_clean_exit("quit")
        task_journal.reconcile_on_boot()
        assert [r["previous"] for r in session_log.starts(0)] == ["none", "closed"]


def test_metrics_and_the_doctor_text():
    _out("create_task", n=100)
    rm = _rm()
    m = rm.metrics()
    assert m["connectors"]["state"] == "ok"
    text = "\n".join(rm.render_lines(m))
    assert "Google Tasks" in text and "100 of 100" in text


@pytest.fixture
def fixture_tool(monkeypatch):
    from agent_friday.governance import action_gate as gate
    from agent_friday.services import agent as ag
    box = {}
    monkeypatch.setitem(ag.CLAUDE_TOOL_HANDLERS, "fixture_change", lambda inp: box["fn"](inp))
    monkeypatch.setitem(ag.TOOL_RINGS, "fixture_change", 0)
    monkeypatch.setattr(gate, "INTERNAL_TOOLS", frozenset(gate.INTERNAL_TOOLS) | {"fixture_change"})
    monkeypatch.setattr(ag, "CLAUDE_TOOLS", list(ag.CLAUDE_TOOLS) + [{
        "name": "fixture_change", "description": "fixture",
        "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}}}])
    outcomes = []
    monkeypatch.setattr(gate, "record_outcome",
                        lambda tool, decision, status, **kw: outcomes.append((status, kw.get("detail"))))
    # every call is one whose outcome is recorded
    monkeypatch.setattr(ag, "_outcome_recorder", _always_recorder)

    def run(fn):
        from agent_friday.services import tool_receipts
        box["fn"] = fn
        outcomes.clear()
        tool_receipts.begin_turn()
        ag._execute_tool("fixture_change", {"q": "x"})
        return tool_receipts.receipts()[-1], list(outcomes)
    return run


def _always_recorder(name, decision, ctx):
    from agent_friday.governance import action_gate as gate
    done = []

    def _write(status, detail="", outcome=None):
        if not done:
            done.append(status)
            gate.record_outcome(name, decision, status, detail=detail, outcome=outcome)
    return _write


def test_an_error_result_is_not_recorded_as_ok(fixture_tool):
    """A handler that returns (rather than raises) an error result: its
    receipt and outcome say the call did not succeed."""
    receipt, outcomes = fixture_tool(lambda inp: "Tool error (fixture_change): the service said no")
    assert receipt["ok"] is False
    assert outcomes and outcomes[0][0] == "error"


def test_a_result_waiting_for_a_card_is_held_not_ok(fixture_tool):
    from agent_friday.services import agent as ag
    pending = ag._TOOL_PENDING_SENTINELS[0] + " the owner decides"
    _receipt, outcomes = fixture_tool(lambda inp: pending)
    assert outcomes and outcomes[0][0] == "held"


def test_a_result_the_handler_declined_is_refused_not_ok(fixture_tool):
    from agent_friday.services import agent as ag
    declined = ag._TOOL_DENY_SENTINELS[0] + " not now"
    _receipt, outcomes = fixture_tool(lambda inp: declined)
    assert outcomes and outcomes[0][0] == "refused"


def test_a_plain_result_is_still_ok(fixture_tool):
    receipt, outcomes = fixture_tool(lambda inp: '{"id": "t1"}')
    assert receipt["ok"] is True and outcomes and outcomes[0][0] == "ok"


# ── NS-35.11-6: the quantity of stored memory is never a goal ────────────────

def test_skill_scores_do_not_reward_memory():
    from agent_friday import skillopt_engine as se
    def _about_memory(key):
        words = key.lower().split("_")
        return any(w.startswith("memor") or w in ("fact", "facts", "entry", "entries", "stored")
                   for w in words)
    assert not [k for k in se.DEFAULT_WEIGHTS if _about_memory(k)]
    m = {"accuracy": 0.8, "latency_ms": 900, "cost_usd": 0.01,
         "user_satisfaction": 0.7, "completeness": 0.9}
    assert se.composite_score(m) == se.composite_score({**m, "memories_added": 10 ** 6,
                                                        "facts_stored": 10 ** 6})


def test_no_score_or_objective_reads_how_much_is_remembered():
    """No function that scores, ranks, rewards or promotes anything reads a
    count of stored memories. Display and cap readers are not scores."""
    import ast
    from pathlib import Path
    import agent_friday
    root = Path(agent_friday.__file__).parent
    readers = {"_count_memory_json", "_memory_entries_cached", "count_facts_by_categories",
               "total_memories", "memory_count", "memories_count", "fact_count"}
    goal = ("score", "reward", "objective", "fitness", "promote", "rank")
    hits = []
    for py in root.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except Exception:
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any(g in fn.name.lower() for g in goal):
                continue
            for node in ast.walk(fn):
                name = (node.attr if isinstance(node, ast.Attribute)
                        else node.id if isinstance(node, ast.Name) else None)
                if name in readers:
                    hits.append("%s:%s uses %s" % (py.relative_to(root), fn.name, name))
    assert not hits, hits


def test_reliability_metrics_set_no_memory_target():
    m = _rm().metrics()
    assert "memory" not in json.dumps(m).lower()
