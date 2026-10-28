"""Chaos for the release gate (north star §34.16-1, §34.16-2 migration part):
a separate interpreter is killed with os._exit (no cleanup, no finally) in
the middle of an outward action, and this process starts up from the same
files the way Friday does after a crash. What may have happened is told to
the owner and never retried; what is unknown is never reported as done.

The model-call kill is tests/unit/test_task_journal.py
::test_a_killed_process_leaves_a_record_a_new_process_reads.
Synthetic data only; nothing reaches a network."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from agent_friday.services import approvals
from agent_friday.services import card_recovery
from agent_friday.services import task_journal as tj
from agent_friday.services import task_resume

SRC = Path(__file__).resolve().parents[2] / "src"


def _kill_after(script: str, tmp_path, timeout=120):
    """Run `script` in a fresh interpreter that shares this run's Friday home
    (same vault key, as after a real restart) and dies with os._exit(9)."""
    from agent_friday import core as _core
    head = textwrap.dedent(f"""
        import os, sys, time
        os.environ["FRIDAY_HOME"] = r"{_core.FRIDAY_DIR}"
        os.environ["FRIDAY_TESTING"] = "1"
        sys.path.insert(0, r"{SRC}")
        from agent_friday.services import task_journal as tj, task_resume, approvals
        tj.BASE_DIR_OVERRIDE = r"{tmp_path / 'tasks'}"
        approvals.APPROVALS_FILE = __import__("pathlib").Path(r"{tmp_path / 'approvals.json'}")
    """)
    proc = subprocess.run([sys.executable, "-c", head + textwrap.dedent(script) + "\nos._exit(9)\n"],
                          capture_output=True, text=True, timeout=timeout)
    assert proc.returncode == 9, f"child did not die as planned\nstdout={proc.stdout}\nstderr={proc.stderr[-2000:]}"
    return proc.stdout.strip().splitlines()


@pytest.fixture
def restart(tmp_path, monkeypatch):
    """This process, starting up from what the killed one left."""
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    tj.reset_for_tests()
    pushed = []
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: pushed.append(kw) or kw)
    return pushed


_START_TASK = """
    tid = "chaos-" + str(int(time.time() * 1000))
    now = time.time()
    tj.index_put(tid, "Send the invoice", "running", now)
    tj.write_state(tid, {"task_id": tid, "name": "Send the invoice", "status": "running", "created": now})
"""


def test_a_kill_during_a_tasks_first_send_is_told_not_retried(tmp_path, restart):
    _kill_after(_START_TASK + """
    task_resume.mark_tool_pending(tid, "send_email", "tu_1")
    print(tid, flush=True)                  # the send is in flight here
    """, tmp_path)
    tj.reconcile_on_boot()
    tid = next(iter(tj.index_read()))
    verdict = task_resume.resumability(tid)
    assert verdict["resumable"] is False and verdict["may_have_acted"] is True
    assert "send_email" in verdict["reason"] and "not known" in verdict["reason"]
    acted = task_resume.may_have_acted_after_boot()
    assert [r["task_id"] for r in acted] == [tid]
    assert task_resume.resumable_after_boot() == []          # nothing to pick up, nothing re-run
    task_resume.announce_may_have_acted(acted)
    assert restart and restart[-1]["kind"] == "tasks_may_have_acted"
    assert "will not repeat it" in restart[-1]["body"]


def test_a_first_step_that_only_reads_is_not_a_worry(tmp_path, restart):
    _kill_after(_START_TASK + """
    task_resume.mark_tool_pending(tid, "read_file", "tu_1")
    """, tmp_path)
    tj.reconcile_on_boot()
    assert task_resume.may_have_acted_after_boot() == []


def test_a_kill_during_a_later_send_asks_before_resuming(tmp_path, restart):
    _kill_after(_START_TASK + """
    convo = [{"role": "user", "content": "send the invoice"},
             {"role": "assistant", "content": [{"type": "text", "text": "Reading it first."}]}]
    assert task_resume.checkpoint(tid, convo=convo, iteration=1, model="m")
    task_resume.mark_tool_pending(tid, "send_email", "tu_2")
    """, tmp_path)
    tj.reconcile_on_boot()
    found = task_resume.resumable_after_boot()
    assert len(found) == 1 and found[0]["needs_confirmation"] is True
    assert "take effect twice" in found[0]["reason"]


_N = [0]


def _card(status="approved", **extra):
    _N[0] += 1
    rec = approvals.create_approval(kind="tool_confirm", subject_type="tool", subject_id="send_email-%d" % _N[0],
                                    title="Send the invoice to the client", description="synthetic",
                                    payload={"tool": "send_email", "input": {"to": "a@example.invalid"}})
    return approvals._patch(rec["approval_id"], status=status, decided_at=time.time() - 5, **extra)


def test_an_approved_action_killed_mid_run_is_marked_unknown_and_never_run_again(tmp_path, restart):
    lines = _kill_after("""
    rec = approvals.create_approval(kind="tool_confirm", subject_type="tool", subject_id="send_email",
                                    title="Send the invoice to the client", description="synthetic",
                                    payload={"tool": "send_email", "input": {"to": "a@example.invalid"}})
    approvals._patch(rec["approval_id"], status="approved", decided_at=time.time())
    assert approvals.claim_for_execution(rec["approval_id"], by="approval_executor")
    print(rec["approval_id"], flush=True)   # the action is running here
    """, tmp_path)
    aid = lines[-1]
    done = _card()                                             # one that finished, in this process's past
    approvals._patch(done["approval_id"], executing_at=time.time() - 3, used_at=time.time() - 2,
                     consumed=True, executing_by="approval_executor")
    ids = card_recovery.reconcile_at_boot(started_at=time.time() + 5, since=0)
    assert ids == [aid]
    rec = approvals.get_approval(aid)
    assert rec["outcome"] == "unknown" and rec["consumed"] is True and not rec.get("used_at")
    assert approvals.claim_for_execution(aid) is False, "an action that may have happened ran again"
    assert restart[-1]["kind"] == "approval_outcome_unknown" and "an email to send" in restart[-1]["body"]
    assert "Send the invoice" not in restart[-1]["body"], "the notice carried the card's title (ruling 5)"
    assert card_recovery.reconcile_at_boot(started_at=time.time() + 5, since=0) == []      # told once


def test_a_mail_send_killed_after_the_card_was_burned_is_unknown_but_a_failed_one_is_not(tmp_path, restart,
                                                                                         monkeypatch):
    from agent_friday.services import gmail_send
    burned = approvals._patch(_card()["approval_id"], subject_type=gmail_send.SUBJECT_TYPE, consumed=True,
                              consumed_at=time.time() - 30)
    failed = approvals._patch(_card()["approval_id"], subject_type=gmail_send.SUBJECT_TYPE, consumed=True,
                              consumed_at=time.time() - 30)
    # Burned by THIS process (a held message sent at start-up while the check runs): not judged.
    approvals._patch(_card()["approval_id"], subject_type=gmail_send.SUBJECT_TYPE, consumed=True,
                     consumed_at=time.time() + 60)
    # Burned before this check existed (no stamp): not judged.
    approvals._patch(_card()["approval_id"], subject_type=gmail_send.SUBJECT_TYPE, consumed=True)
    monkeypatch.setattr(gmail_send, "_outbox_path", lambda: tmp_path / "no-outbox.jsonl")
    monkeypatch.setattr(gmail_send, "outbox", lambda limit=50: [{"approval_id": failed["approval_id"],
                                                                 "ok": False}])
    assert card_recovery.reconcile_at_boot(started_at=time.time() + 5, since=0) == [burned["approval_id"]]


def test_a_card_claimed_in_this_process_is_left_alone(tmp_path, restart):
    rec = _card()
    assert approvals.claim_for_execution(rec["approval_id"], by="approval_executor")
    assert card_recovery.reconcile_at_boot(started_at=time.time() - 60, since=0) == []


def test_only_claims_that_always_record_their_end_are_judged(tmp_path, restart):
    """A site build or a media tidy claims a card and ends its own way (no
    used_at): it must never be reported as unknown. Nor a card off the record."""
    site = _card()
    assert approvals.claim_for_execution(site["approval_id"])                  # no claimer named
    approvals._patch(site["approval_id"], executing_at=time.time() - 30)
    private = _card(off_record=True, executing_by="approval_executor", executing_at=time.time() - 30)
    assert private["off_record"]
    assert card_recovery.reconcile_at_boot(started_at=time.time() + 5, since=0) == []


def test_the_first_start_after_the_upgrade_judges_nothing_older(tmp_path, restart, monkeypatch):
    import agent_friday.paths as paths
    monkeypatch.setattr(paths, "friday_home", lambda: tmp_path)
    old = _card(executing_by="approval_executor", executing_at=time.time() - 3600)
    assert card_recovery.reconcile_at_boot(started_at=time.time()) == []         # records "since"
    assert (tmp_path / "card-recovery.json").exists()
    assert card_recovery.reconcile_at_boot(started_at=time.time()) == []         # still older than since
    assert not approvals.get_approval(old["approval_id"]).get("outcome")


def test_without_a_process_start_time_nothing_is_judged(tmp_path, restart, monkeypatch):
    _card(executing_by="approval_executor", executing_at=time.time() - 30)
    monkeypatch.setattr(card_recovery, "process_started", lambda: None)
    assert card_recovery.reconcile_at_boot(since=0) == []


def test_a_stopped_task_is_announced_once_across_restarts(tmp_path, restart):
    _kill_after(_START_TASK + """
    task_resume.mark_tool_pending(tid, "send_email", "tu_1")
    """, tmp_path)
    tj.reconcile_on_boot()
    first = task_resume.may_have_acted_after_boot()
    task_resume.announce_may_have_acted(first)
    assert len(first) == 1
    assert task_resume.may_have_acted_after_boot() == []                        # the next start


def test_a_kill_between_two_schema_changes_is_finished_on_the_next_start(tmp_path):
    from agent_friday.services import db_util
    db = tmp_path / "store.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE items(a TEXT)")
    con.execute("INSERT INTO items(a) VALUES ('kept')")
    con.commit()
    con.close()
    proc = subprocess.run([sys.executable, "-c", textwrap.dedent(f"""
        import os, sqlite3
        con = sqlite3.connect(r"{db}")
        con.execute("ALTER TABLE items ADD COLUMN b INTEGER DEFAULT 1")
        con.commit()
        os._exit(9)                          # before the second change
    """)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 9
    con = sqlite3.connect(db)
    added = db_util.ensure_columns(con, "items", [("b", "INTEGER DEFAULT 1"), ("c", "TEXT DEFAULT 'x'")])
    assert added == ["c"]
    assert con.execute("SELECT a, b, c FROM items").fetchall() == [("kept", 1, "x")]
    con.close()


def test_the_boot_path_runs_both_reconciliations(monkeypatch):
    """The start-up restore calls them (a wiring check, beside the kills above)."""
    import inspect
    from agent_friday.services import agent
    src = inspect.getsource(agent._restore_tasks_from_journal)
    assert "may_have_acted_after_boot" in src and "card_recovery" in src and "reconcile_at_boot" in src
