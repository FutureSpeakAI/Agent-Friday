"""Work that stopped has to say it stopped.

docs/design/conversations-and-concurrency.md §2.5 / build step 8.

Stephen's Q6: do background tasks survive a restart? Tonight the answer was
"at storage, yes; at execution, no" — a commission sat frozen at `grinding`
while the app that started it had restarted around it, neither finished nor
failed nor running. These tests pin the two halves of the fix:

  * structured, stage-checkpointed work RESUMES at its recorded stage
  * free-form work cannot resume and is REPORTED interrupted, into the
    conversation that asked for it
"""
import importlib

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path, raising=False)
    conv = importlib.import_module("agent_friday.services.conversations")
    importlib.reload(conv)
    monkeypatch.setattr(conv, "FRIDAY_DIR", tmp_path, raising=False)
    rec = importlib.import_module("agent_friday.services.reconcile")
    importlib.reload(rec)
    return rec, conv


# ── delivery lands without a browser ────────────────────────────────────────

def test_a_finished_task_reports_into_its_own_conversation(env):
    """The old path needed a live browser to witness a transition.

    A task that completed while nothing was watching reported to nobody. This
    writes into the store, so the message is there whenever he opens that chat.
    """
    rec, conv = env
    a = conv.create(title="asked here")
    b = conv.create(title="somewhere else")

    rec.report_task_result(a["id"], "Research finished", "Here are the findings.",
                           task_id="task-1")

    texts_a = [m["text"] for m in conv.messages(a["id"])]
    texts_b = [m["text"] for m in conv.messages(b["id"])]
    assert any("Research finished" in t for t in texts_a)
    assert texts_b == [], "the report leaked into a conversation that did not ask"


def test_a_report_with_no_owner_lands_in_main(env):
    """Voice, channels and the scheduler carry no conversation id.

    Their output still has to go somewhere real — output with nowhere to go is
    how completions went missing.
    """
    rec, conv = env
    rec.report_task_result(None, "Heartbeat", "All quiet.")
    assert any("Heartbeat" in m["text"] for m in conv.messages(conv.MAIN_ID))


def test_the_report_is_a_system_report_not_a_model_turn(env):
    """It must never be replayed to a model as if Friday had said it."""
    rec, conv = env
    c = conv.create()
    rec.report_task_result(c["id"], "Done", "body")
    roles = {m["role"] for m in conv.messages(c["id"])}
    assert roles == {"system_report"}


# ── free-form work is admitted, not resumed ─────────────────────────────────

def test_an_interrupted_task_is_marked_and_announced(env, monkeypatch):
    rec, conv = env
    owner = conv.create(title="owner")
    import agent_friday.core as core
    tid = "task-abc"
    monkeypatch.setattr(core, "TASKS", {
        tid: {"status": "running", "name": "Draft the memo",
              "conversation_id": owner["id"], "log": []}}, raising=False)
    import threading
    monkeypatch.setattr(core, "TASKS_LOCK", threading.Lock(), raising=False)

    out = rec.reconcile_tasks()

    assert out["interrupted"] == [tid]
    assert core.TASKS[tid]["status"] == "interrupted"
    said = " ".join(m["text"] for m in conv.messages(owner["id"]))
    assert "Draft the memo" in said
    assert "interrupted" in said.lower()
    assert "start it again" in said.lower(), (
        "an interruption notice has to offer a way forward, not just report a "
        "death")


def test_a_task_that_was_not_running_is_left_alone(env, monkeypatch):
    rec, conv = env
    import agent_friday.core as core
    import threading
    monkeypatch.setattr(core, "TASKS", {
        "done": {"status": "complete", "name": "finished thing", "log": []}},
        raising=False)
    monkeypatch.setattr(core, "TASKS_LOCK", threading.Lock(), raising=False)
    assert rec.reconcile_tasks()["interrupted"] == []
    assert core.TASKS["done"]["status"] == "complete"


# ── structured work resumes ─────────────────────────────────────────────────

class _FrozenCommission:
    def __init__(self, cid, stage, owner=None):
        self.id = cid
        self.progress = {"stage": stage}
        self.status = stage
        self.conversation_id = owner


def test_a_frozen_commission_is_resumed_from_its_recorded_stage(env, monkeypatch):
    rec, conv = env
    owner = conv.create(title="research chat")
    frozen = _FrozenCommission("7a2fdcb4f468", "grinding", owner["id"])
    monkeypatch.setattr(rec, "_commissions", lambda: [frozen])

    started = []
    class _T:
        def __init__(self, target=None, **kw): self.t = target
        def start(self): started.append(True)
    monkeypatch.setattr(rec.threading, "Thread", _T)

    out = rec.reconcile_research(resume=True)
    assert out["resumed"] == ["7a2fdcb4f468"]
    assert started, "the resume was never actually started"


def test_a_finished_commission_is_not_resumed(env, monkeypatch):
    rec, conv = env
    monkeypatch.setattr(rec, "_commissions",
                        lambda: [_FrozenCommission("done-1", "delivered")])
    out = rec.reconcile_research(resume=True)
    assert out["resumed"] == []


def test_without_resume_a_frozen_commission_is_reported_not_silently_left(env, monkeypatch):
    rec, conv = env
    owner = conv.create(title="research chat")
    monkeypatch.setattr(rec, "_commissions",
                        lambda: [_FrozenCommission("c-9", "grinding", owner["id"])])
    out = rec.reconcile_research(resume=False)
    assert out["reported"] == ["c-9"]
    said = " ".join(m["text"] for m in conv.messages(owner["id"]))
    assert "interrupted" in said.lower() and "resume" in said.lower()


def test_boot_reconciliation_never_raises(env, monkeypatch):
    """A failure here must not stop the server from starting."""
    rec, _ = env
    monkeypatch.setattr(rec, "_commissions",
                        lambda: (_ for _ in ()).throw(RuntimeError("disk gone")))
    out = rec.run_at_boot()
    assert "research" in out
