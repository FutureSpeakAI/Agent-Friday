"""A stopped run says what it had already done (north star §32.22, NS-32.22-1).

Stopping halts new steps; it cannot take back the ones already taken. The
halt record and the notice name the effects already taken, read from the
durable record (the run's journal, its signed receipts, the files it wrote),
and say so plainly when that record could not be read.
"""
from __future__ import annotations

import pytest

from agent_friday.services import agent as ag
from agent_friday.services import task_journal as tj


def _ev(calls=(), receipts=(), files=(), errors=()):
    return {"calls": list(calls), "receipts": list(receipts), "files": list(files), "errors": list(errors)}


MADE_CALL = {"name": "create_calendar_event", "ok": True, "result_summary": "created event e1"}
MADE_RECEIPT = {"tool": "create_calendar_event", "decision": "allow", "class": "outward", "_verified": True}
CARD_RECEIPT = {"tool": "create_task", "decision": "card", "class": "outward", "_verified": True}
SENT_CALL = {"name": "draft_email", "ok": True, "result_summary": "sent to 1 recipient"}
FILE = {"path": "C:/notes/plan.md", "mtime": 1.0, "bytes": 10}


@pytest.fixture
def evidence(monkeypatch):
    from agent_friday.services import goal_evidence as ge
    box = {"ev": _ev()}
    monkeypatch.setattr(ge, "collect", lambda ids, since: box["ev"])
    return box


class TestEffectsTaken:
    def _ce(self):
        from agent_friday.services import cancel_effects
        return cancel_effects

    def test_actions_files_and_cards_are_listed(self, evidence):
        evidence["ev"] = _ev(calls=[MADE_CALL], receipts=[MADE_RECEIPT, CARD_RECEIPT], files=[FILE])
        eff = self._ce().effects_taken("t1")
        assert eff["outward"] == ["create_calendar_event"]
        assert eff["files_written"] == ["C:/notes/plan.md"]
        assert eff["cards"] == 1 and eff["unknown"] == [] and eff["may_have_acted"] == []
        text = self._ce().summary(eff)
        assert "1 action outside Friday (create_calendar_event)" in text
        assert "wrote 1 file" in text and "1 approval card" in text

    def test_a_tool_that_holds_its_own_card_and_sent_is_not_missed(self, evidence):
        """draft_email is self-gated: its send is signed outside the task, so
        no allowing receipt carries the task id. It still counts."""
        evidence["ev"] = _ev(calls=[SENT_CALL])
        eff = self._ce().effects_taken("t1")
        assert "draft_email" in eff["may_have_acted"]
        assert "Nothing" not in self._ce().summary(eff)

    def test_an_allowed_action_with_no_call_on_record_may_have_happened(self, evidence):
        evidence["ev"] = _ev(receipts=[MADE_RECEIPT])
        eff = self._ce().effects_taken("t1")
        assert eff["outward"] == [] and eff["may_have_acted"] == ["create_calendar_event"]
        assert "may have acted through create_calendar_event" in self._ce().summary(eff)

    def test_a_draft_queued_behind_a_card_did_not_send(self, evidence):
        queued = dict(SENT_CALL, result_summary='{"sent": false, "queued": true}')
        evidence["ev"] = _ev(calls=[queued])
        eff = self._ce().effects_taken("t1")
        assert eff["outward"] == [] and eff["may_have_acted"] == []

    def test_receipts_whose_signature_fails_make_it_unknown(self, evidence):
        evidence["ev"] = _ev(receipts=[dict(MADE_RECEIPT, _verified=False)])
        eff = self._ce().effects_taken("t1")
        assert eff["unknown"] and "could not be checked" in self._ce().summary(eff)

    def test_nothing_done_says_only_what_the_record_shows(self, evidence):
        text = self._ce().summary(self._ce().effects_taken("t1"))
        assert text.startswith("Nothing outside Friday is on record")

    def test_an_unreadable_record_is_never_nothing(self, evidence):
        evidence["ev"] = _ev(errors=["the action receipts (OSError)"])
        text = self._ce().summary(self._ce().effects_taken("t1"))
        assert "could not be checked" in text and "Nothing" not in text

    def test_a_collect_that_raises_is_unknown(self, monkeypatch):
        from agent_friday.services import goal_evidence as ge
        monkeypatch.setattr(ge, "collect", lambda ids, since: (_ for _ in ()).throw(RuntimeError("x")))
        eff = self._ce().effects_taken("t1")
        assert eff["unknown"] and "could not be checked" in self._ce().summary(eff)


@pytest.fixture
def journal(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    tj.reset_for_tests()
    with ag.TASKS_LOCK:
        saved = dict(ag.TASKS)
        ag.TASKS.clear()
    yield
    with ag.TASKS_LOCK:
        ag.TASKS.clear()
        ag.TASKS.update(saved)


def test_the_halt_of_a_stopped_run_carries_its_effects(journal, evidence):
    evidence["ev"] = _ev(calls=[MADE_CALL], receipts=[MADE_RECEIPT], files=[FILE])
    tid = "t-stop-1"
    with ag.TASKS_LOCK:
        ag.TASKS[tid] = {"task_id": tid, "name": "Send the notes", "status": "running",
                         "created": 1.0, "started": 1.0}
    ag._task_set(tid, status="cancelled", ended=2.0, result="[Stopped at your request.]")
    halts = [e for e in tj.read(tid) if e.get("kind") == "halt" and e.get("cause") == "cancelled"]
    assert halts and halts[-1]["effects"]["outward"] == ["create_calendar_event"]
    assert halts[-1]["effects"]["files_written"] == ["C:/notes/plan.md"]
    digest = tj.digest(tid)
    assert digest["halts"][-1]["effects"]["outward"] == ["create_calendar_event"]


def test_the_paths_a_stopped_run_wrote_pass_the_gate_before_an_observer(monkeypatch):
    """The file paths are sealed like every other free-text field served to
    a principal that is not the owner (the gate decides what of them goes)."""
    from agent_friday.services import egress_gate as eg
    monkeypatch.setattr(eg, "_gate_text", lambda text, *a, **k: "[sealed]")
    sealed, _redacted, _withheld = tj.seal_for_principal(
        {"halts": [{"effects": {"files_written": ["C:/Users/someone/medical-notes.md"]}}]}, "observer")
    assert "medical-notes" not in str(sealed)


def test_a_stopped_run_is_not_reported_as_finished(monkeypatch, evidence):
    evidence["ev"] = _ev(calls=[MADE_CALL], receipts=[MADE_RECEIPT])
    pushed = []
    import agent_friday.notifications_engine as ne
    from agent_friday.services import conversations, desktop_bus, voice_live_channel
    monkeypatch.setattr(ne, "push", lambda **kw: pushed.append(kw) or kw)
    monkeypatch.setattr(ag, "TASKS", {"t9": {"task_id": "t9", "started": 1.0}})
    monkeypatch.setattr(conversations, "resolve", lambda cid: cid)
    monkeypatch.setattr(voice_live_channel, "is_live", lambda cid: False)
    monkeypatch.setattr(desktop_bus, "connected", lambda *a, **k: False)
    ag._report_task_completion("t9", "Send the notes", "cancelled", "[Stopped at your request.]")
    assert pushed, "no notice"
    assert pushed[-1]["title"] == "Task stopped: Send the notes"
    assert "create_calendar_event" in pushed[-1]["body"]
